import math


def scale_size_log(val: float, max_auth: float, px_min: float = 5, px_max: float = 60) -> float:
    if val <= 0 or max_auth <= 0:
        return px_min
    return px_min + (math.log1p(val) / math.log1p(max_auth)) * (px_max - px_min)


def make_node_label(m: dict) -> str:
    parts = [m[k] for k in ("fac", "inst", "grp") if m.get(k)]
    return " | ".join(parts)


def render_year_comparison(all_years_data: dict, series: list, title: str, **kwargs):
    """TODO (fase 2): port fra _render_year_comparison, gl. linje 4053-4147.

    Bruges af flere faner til at vise udvikling over år for en given
    serie af nøgletal. Tjek hvilke faner der kalder den, før du flytter den,
    så signaturen matcher alle kald-steder.
    """
    raise NotImplementedError("Flyt fra sampubliceringsapp.py linje 4053-4147")


def render_org_bar(edges_keep: list, node_meta: dict, org_key: str, title_label: str, **kwargs):
    """TODO (fase 2): port fra _render_org_bar, gl. linje 4147-4270."""
    raise NotImplementedError("Flyt fra sampubliceringsapp.py linje 4147-4270")