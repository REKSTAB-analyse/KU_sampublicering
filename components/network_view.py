import tempfile
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network
 
from data.network import compute_layout_for_edges, load_inst_fac_map, load_inst_kort_map
from components.colors import node_colors_for_mode, add_alpha
from config import base_mode, METRIC_LABELS, make_abbr

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

_PNG_BUTTON_HTML = """
<style>
  #dl-png {
    position: fixed; top: 8px; right: 8px; z-index: 1000;
    width: 30px; height: 30px; padding: 4px;
    background: transparent; border: none; outline: none;
    border-radius: 4px; cursor: pointer;
    opacity: 0; transition: opacity .15s;
  }
  body:hover #dl-png { opacity: 1; }
  #dl-png svg { fill: rgba(68, 68, 68, 0.3); transition: fill .15s; }
  #dl-png:hover svg { fill: rgba(68, 68, 68, 0.7); }
</style>
<button id="dl-png" type="button" title="Download som PNG">
  <svg viewBox="0 0 24 24" width="20" height="20">
    <circle cx="12" cy="12" r="3.2"/>
    <path d="M9 2 7.17 4H4c-1.1 0-2 .9-2 2v12c0 1.1.9 2 2 2h16c1.1 0
      2-.9 2-2V6c0-1.1-.9-2-2-2h-3.17L15 2H9zm3 15c-2.76 0-5-2.24-5-5s2.24
      -5 5-5 5 2.24 5 5-2.24 5-5 5z"/>
  </svg>
</button>
<script>
document.getElementById("dl-png").addEventListener("click", function () {
  var PNG_SCALE = __PNG_SCALE__;      // 1 = skærmopløsning, 3 = 3x skarpere
  var MAX_PIXELS = 36000000;          // loft, så browseren ikke løber tør
  var cv = document.querySelector("#mynetwork canvas");
  if (!cv) { return; }

  var dprDesc = Object.getOwnPropertyDescriptor(window, "devicePixelRatio");
  var realDpr = window.devicePixelRatio || 1;
  var cssW = cv.clientWidth, cssH = cv.clientHeight;
  var scale = PNG_SCALE;
  while (scale > 1 && cssW * cssH * Math.pow(realDpr * scale, 2) > MAX_PIXELS) {
    scale -= 1;
  }
  var optW = network.canvas.options.width;
  var optH = network.canvas.options.height;

  // vis-network aflæser window.devicePixelRatio ved hver setSize() - sæt den
  // midlertidigt op, tegn om, kopiér resultatet og gendan derefter.
  Object.defineProperty(window, "devicePixelRatio",
                        { value: realDpr * scale, configurable: true });
  var out = document.createElement("canvas");
  try {
    network.setSize(optW, optH);
    network.redraw();
    var src = document.querySelector("#mynetwork canvas");
    out.width = src.width;
    out.height = src.height;
    var ctx = out.getContext("2d");
    ctx.fillStyle = "#ffffff";        // canvas er transparent som standard
    ctx.fillRect(0, 0, out.width, out.height);
    ctx.drawImage(src, 0, 0);
  } finally {
    if (dprDesc) { Object.defineProperty(window, "devicePixelRatio", dprDesc); }
    else { delete window.devicePixelRatio; }
    network.setSize(optW, optH);
    network.redraw();
  }

  out.toBlob(function (blob) {
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "__PNG_FILENAME__";
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }, "image/png");
});
</script>
"""

def _scale_node_size(val: float, max_val: float, mode: str) -> float:
    px_min, px_max = _SIZE_RANGE_BY_BASE_MODE.get(base_mode(mode), _DEFAULT_SIZE_RANGE)
    power = _SIZE_POWER_BY_BASE_MODE.get(base_mode(mode), _DEFAULT_SIZE_POWER)
    if max_val <= 0 or val <= 0:
        return px_min
    ratio = (val / max_val) ** power
    return px_min + ratio * (px_max - px_min)


def render_pyvis_network(edges: list, dims: list, mode: str, node_sizes: dict = None,
                          network_scale: int = 1200, edge_scale: float = 6.0,
                          metric: str = "forfatterpar", height: int = 700,
                          png_filename: str = "sampubliceringsnetværk.png",
                          png_scale: int = 3) -> None:
 
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

    # Forkort institutnavne i selve labelen (fuldt navn bevares i title/
    # tooltip) - lange, fulde institutnavne gjorde skriften ulæselig ved
    # normal zoom. existing-sættet sikrer unikke forkortelser, selv hvis to
    # institutter ville forkorte til samme bogstaver.
    dim_index = {d: i for i, d in enumerate(dims)}
    inst_abbrs = {}
    if "Inst" in dim_index:
        inst_kort_map = load_inst_kort_map()
        inst_names = sorted({
            node_key.split(" | ")[dim_index["Inst"]]
            for node_key in positions.keys()
        })
        seen = set()
        for name in inst_names:
            abbr = inst_kort_map.get(name)
            if not abbr:
                abbr = make_abbr(name, existing=seen)
            seen.add(abbr)
            inst_abbrs[name] = abbr

    def _display_label(node_key: str) -> str:
        if "Inst" not in dim_index:
            return node_key
        parts = node_key.split(" | ")
        parts[dim_index["Inst"]] = inst_abbrs.get(
            parts[dim_index["Inst"]], parts[dim_index["Inst"]]
        )
        return " | ".join(parts)

    net = Network(height=f"{height}px", width="100%", bgcolor="#ffffff", font_color="#222222", directed=False)
    net.toggle_physics(False)  # AFGØRENDE: se modulets docstring
 
    max_size = max((node_sizes or {}).values(), default=1) or 1
    inst_fac_map = load_inst_fac_map() if "Inst" in dim_index else None
    colors = node_colors_for_mode(positions.keys(), dims, mode, inst_fac_map=inst_fac_map)
    px_min_default, _ = _SIZE_RANGE_BY_BASE_MODE.get(base_mode(mode), _DEFAULT_SIZE_RANGE)

    for node_key, (x, y) in positions.items():
        size = px_min_default
        if node_sizes and node_key in node_sizes:
            size = _scale_node_size(node_sizes[node_key], max_size, mode)
        net.add_node(
            node_key,
            label=_display_label(node_key),
            x=x, y=y,
            physics=False,
            size=size,
            color=colors.get(node_key, "#888888"),
            font={
                "size": 46,
                "face": "arial",
                "color": "#1a1a1a",
                "strokeWidth": 3,
                "strokeColor": "#ffffff",
            },
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

    button_html = (_PNG_BUTTON_HTML
                   .replace("__PNG_FILENAME__", png_filename)
                   .replace("__PNG_SCALE__", str(int(png_scale))))
    html = html.replace("</body>", button_html + "</body>", 1)

    components.html(html, height=height + 50, scrolling=False)
