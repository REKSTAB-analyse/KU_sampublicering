import tempfile
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network
 
from data.network import compute_layout_for_edges
from components.colors import node_colors_for_mode, add_alpha
from config import base_mode, METRIC_LABELS

_SIZE_RANGE_BY_BASE_MODE = {
    "F":  (20, 100),
    "FI": (18, 90),
    "I":  (18, 90),
    "G":  (12, 55),
    "FG": (10, 90),
}
_DEFAULT_SIZE_RANGE = (8, 40)

_SIZE_POWER_BY_BASE_MODE = {
    "FG": 1.0,  # tættere på lineær - undgår at ét stort outlier-institut klemmer resten sammen
    "F":  0.6,  # under 1 = mindre forskel mellem store og små fakulteter
}
_DEFAULT_SIZE_POWER = 2.2

def _scale_node_size(val: float, max_val: float, mode: str) -> float:
    px_min, px_max = _SIZE_RANGE_BY_BASE_MODE.get(base_mode(mode), _DEFAULT_SIZE_RANGE)
    power = _SIZE_POWER_BY_BASE_MODE.get(base_mode(mode), _DEFAULT_SIZE_POWER)
    if max_val <= 0 or val <= 0:
        return px_min
    ratio = (val / max_val) ** power
    return px_min + ratio * (px_max - px_min)


def render_pyvis_network(edges: list, dims: list, mode: str, node_sizes: dict = None,
                          network_scale: int = 1200, edge_scale: float = 6.0,
                          metric: str = "forfatterpar", height: int = 700) -> None:
 
    if not edges:
        st.info("Ingen kanter matcher de valgte filtre.")
        return
 
    positions = compute_layout_for_edges(edges, dims, mode, network_scale=network_scale)
    if not positions:
        st.info("Kunne ikke beregne et layout for de valgte noder.")
        return
 
    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    x_span = max(xs) - min(xs) or 1
    y_span = max(ys) - min(ys) or 1
 
    net = Network(height=f"{height}px", width="100%", bgcolor="#ffffff", font_color="#222222", directed=False)
    net.toggle_physics(False)  # AFGØRENDE: se modulets docstring
 
    max_size = max((node_sizes or {}).values(), default=1) or 1
    colors = node_colors_for_mode(positions.keys(), dims, mode)
    px_min_default, _ = _SIZE_RANGE_BY_BASE_MODE.get(base_mode(mode), _DEFAULT_SIZE_RANGE)

    for node_key, (x, y) in positions.items():
        size = px_min_default
        if node_sizes and node_key in node_sizes:
            size = _scale_node_size(node_sizes[node_key], max_size, mode)
        net.add_node(
            node_key,
            label=node_key,
            x=x, y=y,
            physics=False,
            size=size,
            color=colors.get(node_key, "#888888"),
            title=f"{node_key}" + (f" ({node_sizes.get(node_key)} forfattere)" if node_sizes else ""),
        )
 
    max_weight = max((e["weight"] for e in edges), default=1) or 1
    for e in edges:
        key_1 = " | ".join(str(e[f"{d}_1"]) for d in dims)
        key_2 = " | ".join(str(e[f"{d}_2"]) for d in dims)
        if key_1 == key_2:
            continue  # intra-node "selv-kant" giver ikke mening at tegne
        ratio = (e["weight"] / max_weight) ** 0.5  # <1 = flere kanter fremstår tykke, ikke kun den kraftigste
        width = max(1.5, 6 * edge_scale * ratio)   # bundgrænse, så selv de svageste kanter er synlige
        metric_label = METRIC_LABELS.get(metric, metric).lower()
        net.add_edge(
            key_1, key_2, width=width,
            color={"color": add_alpha("#888888", 0.35), "highlight": add_alpha("#888888", 0.9), "hover": "#888888"},
            title=f"{int(e['weight'])} {metric_label}",
        )
 
    with tempfile.NamedTemporaryFile(delete=False, suffix=".html") as f:
        net.save_graph(f.name)
        html_path = f.name
 
    with open(html_path, "r", encoding="utf-8") as f:
        html = f.read()
    components.html(html, height=height + 50, scrolling=False)
