from matplotlib import colors as mcolors
import colorsys

_KU_PALETTE_RAW = [
    # Mørke - høj kontrast, bruges først
    "#122947",  # Blå mørk
    "#901a1E",  # Rød mørk
    "#39641c",  # Grøn mørk
    "#0a5963",  # Petroleum mørk
    "#3d3d3d",  # Grå mørk
    "#7d5402",  # Brun mørk (JUR)
    # Mellem - god læsbarhed
    "#ffbd38",  # Gul (adskiller)
    "#4b8325",  # Grøn mellem
    "#c73028",  # Rød mellem
    "#197f8e",  # Petroleum mellem
    "#425570",  # Blå mellem
    "#666666",  # Grå mellem
    # Lyse - bruges sidst, kun ved mange kategorier
    "#bac7d9",  # Blå lys
    "#dB3B0A",  # Rød-orange lys
    "#becaa8",  # Grøn lys
    "#b7d7de",  # Petroleum lys
    "#e1dfdf",  # Grå lys
]


def ku_color_sequence(n: int, seed: int = 26) -> list[str]:
    if n <= len(_KU_PALETTE_RAW):
        return _KU_PALETTE_RAW[:n]
    # Fallback ved flere kategorier end paletten rækker
    plotly_defaults = [
        "#3A1A5F",  # Lilla mørk (TEO)
        "#7d5402",  # Brun mellem
        "#c45c5f",  # Rosa-rød
        "#5C1012",  # Rød meget mørk
        "#6B84A0",  # Blå-grå
        "#fefaf2",  # Champagne
        "#7A131A",  # Bordeaux
        "#aaaaaa",  # Grå neutral
        "#ffbd38",  # Gul (gentaget)
        "#becaa8",  # Grøn lys (gentaget)
    ]
    extras = plotly_defaults * ((n - len(_KU_PALETTE_RAW)) // len(plotly_defaults) + 1)
    return _KU_PALETTE_RAW + extras[:n - len(_KU_PALETTE_RAW)]


def build_faculty_colors() -> dict:
    return {
        "TEO": "#3A1A5F",
        "JUR": "#7d5402",
        "HUM": "#122947",      # Blå mørk
        "SCIENCE": "#39641c",  # Grøn mørk
        "SAMF": "#0a5963",     # Petroleum mørk
        "SUND": "#7A131A",
    }


def stillingsgruppe_colors() -> dict:
    return {
        "Særlig stilling": "#D4D4D4",
        "Øvrige VIP (DVIP)": "#BAC7D9",
        "Ph.d.": "#6B84A0",
        "Stillinger u. adjunktniveau": "#425570",
        "Postdoc": "#AAAAAA",
        "Adjunkt": "#C45C5F",
        "Lektor": "#901A1E",
        "Professor": "#5C1012",
    }


def adjust_color(hex_color: str, lightness_factor: float = 1.0, saturation_factor: float = 1.0) -> str:
    r, g, b = mcolors.to_rgb(hex_color)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l = max(0.0, min(1.0, l * lightness_factor))
    s = max(0.0, min(1.0, s * saturation_factor))
    r2, g2, b2 = colorsys.hls_to_rgb(h, l, s)
    return mcolors.to_hex((r2, g2, b2))

def hls_gradient(base_hex: str, n: int, spread: float = 0.15) -> list[str]:
    """KU-tro farvegradient: n nuancer af base_hex, varierer KUN lyshed
    (mætning uændret) - porteret fra KU_publikationer/components/charts.py
    ::_hls_gradient() (der privat; gjort offentlig her, da flere moduler
    skal bruge den)."""
    r, g, b = mcolors.to_rgb(base_hex)
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    l_min = max(0.12, l - spread)
    l_max = min(0.82, l + spread)
    colors = []
    for i in range(n):
        t = i / (n - 1) if n > 1 else 0.5
        l_new = l_min + t * (l_max - l_min)
        r2, g2, b2 = colorsys.hls_to_rgb(h, l_new, s)
        colors.append(mcolors.to_hex((r2, g2, b2)))
    return colors


def node_colors_for_mode(node_keys, dims: list, mode: str) -> dict:
    """Farve pr. netværksnode, ud fra hvilke dimensioner der er aktive:
    - Fak + Inst begge til stede (fx FI, FIG): institut = "knækket" nuance
      af moderfakultetets farve (hls_gradient).
    - Kun Fak til stede (F, FG): fakultetets egen basisfarve, ingen nuancering.
    - Kun Stil til stede, hverken Fak eller Inst (G): stillingsgruppe_colors().
    - Andre kombinationer (fx ren I/IG, uden Fak): ingen naturlig basisfarve
      at nuancere fra - falder tilbage til ku_color_sequence().

    Dækker de tre tilfælde, du bad om (F/FIG/G) + rimelige fallbacks for
    resten. Sig til, hvis fx FG eller IG skal farves anderledes.
    """
    dim_index = {d: i for i, d in enumerate(dims)}
    has_fak = "Fak" in dim_index
    has_inst = "Inst" in dim_index
    has_stil = "Stil" in dim_index
    keys = sorted(set(node_keys))

    if has_fak and has_inst:
        fac_colors = build_faculty_colors()
        by_fac = {}
        for key in keys:
            fac = key.split(" | ")[dim_index["Fak"]]
            by_fac.setdefault(fac, []).append(key)
        result = {}
        for fac, fac_keys in by_fac.items():
            fac_keys_sorted = sorted(fac_keys, key=lambda k: k.split(" | ")[dim_index["Inst"]])
            base = fac_colors.get(fac, "#122947")
            for key, shade in zip(fac_keys_sorted, hls_gradient(base, len(fac_keys_sorted))):
                result[key] = shade
        return result

    if has_fak:
        fac_colors = build_faculty_colors()
        return {key: fac_colors.get(key.split(" | ")[dim_index["Fak"]], "#122947") for key in keys}

    if has_stil and not has_inst:
        grp_colors = stillingsgruppe_colors()
        return {key: grp_colors.get(key.split(" | ")[dim_index["Stil"]], "#888888") for key in keys}

    fallback = ku_color_sequence(len(keys))
    return dict(zip(keys, fallback))

def add_alpha(hex_color: str, alpha: float) -> str:
    r, g, b = mcolors.to_rgb(hex_color)
    return f"rgba({int(r*255)}, {int(g*255)}, {int(b*255)}, {alpha})"

