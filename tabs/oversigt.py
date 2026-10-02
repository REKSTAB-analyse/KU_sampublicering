import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import plotly.graph_objects as go

from config import dims_for_mode, base_mode, metric_count_sql, CPR
from data.loader import get_cursor
from components.colors import build_faculty_colors, hls_gradient, ku_color_sequence
from components.charts import PLOTLY_CONFIG

_KOEN_LABEL_TO_KODE = {v: k.upper() for k, v in CPR.items()}

_EXPLICIT_FLAG = {
    "Koen": "køn_explicit", "Statsbg": "statsborgerskab_explicit",
}

_DIM_TO_EDGE_TYPE_COL = {
    "Fak": "Edge_type_fak", "Inst": "Edge_type_inst",
    "Stil": "Edge_type_stil", "Koen": "Edge_type_koen",
    "Statsbg": "Edge_type_statsbg",
}

def _pairs_where_and_params(filters, require_year=True):
    """WHERE-klausul + params til `pairs` (forfatterpar-niveau) - samme
    opskrift som data/network.py::load_edges(), duplikeret lokalt her
    (samme konvention som KU_publikationer/tabs/oversigt.py's egen
    _base_where_and_params()), så Oversigt-fanens tal matcher, hvad
    netværksfanerne viser for samme filtre. Kræver BEGGE sider af parret
    inden for de valgte fakulteter/institutter/stillingsgrupper/køn/
    statsborgerskab, præcis som load_edges()."""
    where = []
    params = []

    if require_year:
        where.append("Year BETWEEN ? AND ?")
        params += [filters["aar_fra"], filters["aar_til"]]

    def both_sides_in(col, values):
        #if not filters.get(_EXPLICIT_FLAG[col], False) or not values:
        if col in _EXPLICIT_FLAG and not filters.get(_EXPLICIT_FLAG[col], False): 
            return 
        if not values: 
            return
        ph = ", ".join("?" for _ in values)
        where.append(f"{col}_1 IN ({ph}) AND {col}_2 IN ({ph})")
        params.extend(values)
        params.extend(values)
    
    ph_org = lambda lst: ", ".join("?" for _ in lst)
    _fak, _inst, _stil = filters["fakultet"], filters["institutter"], filters["stillingsgrupper"]
    where.append(
        f"((Fak_1 IN ({ph_org(_fak)}) AND Inst_1 IN ({ph_org(_inst)}) AND Stil_1 IN ({ph_org(_stil)})) "
        f"OR (Fak_2 IN ({ph_org(_fak)}) AND Inst_2 IN ({ph_org(_inst)}) AND Stil_2 IN ({ph_org(_stil)})))"
    )
    params += _fak + _inst + _stil + _fak + _inst + _stil

    koen_koder = [_KOEN_LABEL_TO_KODE.get(v, v) for v in filters.get("køn", [])]
    both_sides_in("Koen", koen_koder)
    both_sides_in("Statsbg", filters.get("statsborgerskab"))

    def in_filter(col, values):
        if not values:
            return 
        ph = ", ".join("?" for _ in values)
        where.append(f"{col} IN ({ph})")
        params.extend(values)

    in_filter("Type", filters.get("typer"))
    in_filter("Indholdstype", filters.get("indholdstyper"))
    in_filter("Sprog", filters.get("sprog"))

    if filters.get("peer"):
        ph = ", ".join("?" for _ in filters["peer"])
        where.append(f"COALESCE(NULLIF(Peer_review, ''), 'Ukendt') IN ({ph})")
        params.extend(filters["peer"])
    
    if filters.get("open_access"):
        ph = ", ".join("?" for _ in filters["open_access"])
        where.append(f"COALESCE(Open_Access, 'Unknown') IN ({ph})")
        params.extend(filters["open_access"])

    har_doi = filters.get("har_doi") or ["Ja", "Nej"]
    if set(har_doi) == {"Ja"}:
        where.append("DOI IS NOT NULL AND DOI != ''")
    elif set(har_doi) == {"Nej"}:
        where.append("(DOI IS NULL OR DOI = '')")
    
    if (filters.get("min_forfattere") is not None
            and filters.get("max_forfattere") is not None):
        where.append("Antal_forfattere BETWEEN ? AND ?")
        params += [filters["min_forfattere"], filters["max_forfattere"]]
    
    edge_type_filters = filters.get("edge_type_filters", {})
    for dim, col in _DIM_TO_EDGE_TYPE_COL.items():
        allowed = edge_type_filters.get(dim)
        if allowed and set(allowed) != {"intra", "inter"}:
            ph = ", ".join("?" for _ in allowed)
            where.append(f"{col} IN ({ph})")
            params.extend(allowed)
    
    return (" AND ".join(where) if where else "1=1"), params

def _pub_where_and_params(filters, require_intern=True):
    """WHERE-klausul + params til `pubs` (én række pr. forfatter pr.
    publikation) - samme filtre som _pairs_where_and_params(), men uden
    _1/_2-duplikering. Bruges til nøgletal, der IKKE er forfatterpar-
    baserede (fx samlet antal publikationer/forfattere), så de kan sættes
    i forhold til sampubliceringstallene fra `pairs`."""
    where = []
    params = []

    if require_intern:
        where.append("Intern = 'Intern'")
        where.append("HR_status IN ('match', 'match_fallback')")

    where.append("Year BETWEEN ? AND ?")
    params += [filters["aar_fra"], filters["aar_til"]]

    def in_filter(col, values):
        #if explicit_key and not filters.get(explicit_key, False):
            #return
        if not values:
            return 
        ph = ", ".join("?" for _ in values)
        where.append(f"{col} IN ({ph})")
        params.extend(values)
    
    in_filter("Fak", filters.get("fakultet"))
    in_filter("Inst", filters.get("institutter"))
    in_filter("Stil", filters.get("stillingsgrupper"))

    in_filter("Type", filters.get("typer"))
    in_filter("Indholdstype", filters.get("indholdstyper"))
    in_filter("Sprog", filters.get("sprog"))

    if filters.get("peer"):
        ph = ", ".join("?" for _ in filters["peer"])
        where.append(f"COALESCE(NULLIF(Peer_review, ''), 'Ukendt') IN ({ph})")
        params.extend(filters["peer"])

    if filters.get("open_access"):
        ph = ", ".join("?" for _ in filters["open_access"])
        where.append(f"COALESCE(Open_Access, 'Unknown') IN ({ph})")
        params.extend(filters["open_access"])

    


    har_doi = filters.get("har_doi") or ["Ja", "Nej"]
    if set(har_doi) == {"Ja"}:
        where.append("DOI IS NOT NULL AND DOI != ''")
    elif set(har_doi) == {"Nej"}:
        where.append("(DOI IS NULL OR DOI = '')")

    if (filters.get("min_forfattere") is not None
            and filters.get("max_forfattere") is not None):
        where.append("Antal_forfattere BETWEEN ? AND ?")
        params += [filters["min_forfattere"], filters["max_forfattere"]]

    return " WHERE " + " AND ".join(where), params

@st.cache_data(show_spinner="Henter data...")
def _query_kpis(filters):
    """Nøgletal for det aktuelt filtrerede udsnit. 'Forfatterpar' og
    'Publikationer med internt samarbejde' hentes BEGGE fra `pairs`,
    uafhængigt af sidepanelets globale metrik-valg (filters['metric']) -
    formålet med denne fane er netop at vise begge tal på én gang, så de
    kan tjekkes op mod hinanden og mod KU_publikationer.

    NB om 'Andel af KU's publikationer': tælleren (publikationer med
    internt samarbejde) kræver, at BEGGE forfattere i mindst ét
    forfatterpar er inden for det valgte organisatoriske filter (samme
    logik som netværksfanerne); nævneren (KU's publikationer i alt)
    kræver kun, at MINDST ÉN intern forfatter er inden for filtret. Ved
    et snævert fakultets-/institutfilter kan andelen derfor godt
    undervurderes en smule - se Datagrundlag for uddybning."""
    pairs_where, pairs_params = _pairs_where_and_params(filters)

    n_pairs = get_cursor().execute(
        f"SELECT COUNT(*) FROM pairs WHERE {pairs_where}", pairs_params
    ).fetchone()[0] or 0

    n_pubs_samarbejde = get_cursor().execute(
        f"SELECT COUNT(DISTINCT PURE_ID) FROM pairs WHERE {pairs_where}",
        pairs_params,
    ).fetchone()[0] or 0

    pub_where, pub_params = _pub_where_and_params(filters)
    n_pubs_total = get_cursor().execute(
        f"SELECT COUNT(DISTINCT PURE_ID) FROM pubs{pub_where}", pub_params
    ).fetchone()[0] or 0
    n_authors = get_cursor().execute(
        f"SELECT COUNT(DISTINCT ext_id) FROM pubs{pub_where}", pub_params
    ).fetchone()[0] or 0

    andel_samarbejde = (
        round(100 * n_pubs_samarbejde / n_pubs_total, 1) if n_pubs_total else 0
    )
    par_pr_pub = (
        round(n_pairs / n_pubs_samarbejde, 2) if n_pubs_samarbejde else 0
    )

    return {
        "n_pairs": n_pairs,
        "n_pubs_samarbejde": n_pubs_samarbejde,
        "n_pubs_total": n_pubs_total,
        "andel_samarbejde": andel_samarbejde,
        "n_authors": n_authors,
        "par_pr_pub": par_pr_pub,
     }

@st.cache_data(show_spinner="Henter data...")
def _query_pairs_trend(filters, metric):
    """Forfatterpar/publikationer år for år, brudt ned på organisatorisk
    niveau (Fak/Inst/Stil - Køn og Statsborgerskab fragmenterer bevidst
    IKKE denne graf). Et forfatterpar tælles under en given enhed, hvis
    BEGGE forfattere sidder i den (fx begge på SCIENCE); 'KU samlet'
    dækker derimod ALLE forfatterpar, også dem på tværs af enheder -
    forskellen mellem 'KU samlet' og summen af enkeltenhederne er derfor
    et udtryk for det tværorganisatoriske samarbejde, ikke en fejl. Dette
    er én mulig fortolkning af 'nedbrydning pr. enhed' for et parret
    datasæt (alternativet - at tælle et tværgående par under BEGGE
    enheder - ville lade summen af enhederne overstige KU-tallet). Sig
    til, hvis I hellere vil have den anden fortolkning.

    Ignorerer bevidst sidepanelets årsinterval, så grafen altid dækker
    hele den tilgængelige periode - øvrige filtre respekteres stadig,
    samme princip som KU_publikationer/tabs/oversigt.py's trend-grafer."""
    dims = dims_for_mode(base_mode(filters.get("mode", "F")))
    where_sql, params = _pairs_where_and_params(filters, require_year=False)
    count_expr = metric_count_sql(metric)

    ku_sql = f"SELECT Year, {count_expr} AS n FROM pairs WHERE {where_sql} GROUP BY 1"
    ku_rows = get_cursor().execute(ku_sql, params).fetchall()
    result = {year: {"KU samlet": n} for year, n in ku_rows}

    if not dims:
        return result
    
    n_dims = len(dims)
    intra_clause = " AND ".join(f"{d}_1 = {d}_2" for d in dims)
    dim_select = ", ".join(f"{d}_1 AS dim_{i}" for i, d in enumerate(dims))
    sql = f"""
        SELECT {dim_select}, Year, {count_expr} AS n
        FROM pairs
        WHERE {where_sql} AND {intra_clause}
        GROUP BY {", ".join(str(i) for i in range(1, n_dims + 2))}
    """
    rows = get_cursor().execute(sql, params).fetchall()
    for row in rows:
        dim_values = row[:n_dims]
        year = row[n_dims]
        n = row[n_dims + 1]
        unit_label = " | ".join(str(v) for v in reversed(dim_values))
        result.setdefault(year, {})[unit_label] = n

    return result

def _unit_colors(units, totals):
    """Fælles farvelogik for org-nedbrudte trendgrafer - KU-grå for 'KU
    samlet', faste fakultetsfarver, knækkede nuancer pr. institut/enhed
    under et fakultet. Udtrukket til egen funktion, da både
    _render_org_trend() og _render_ratio_trend() skal bruge den."""
    faculty_colors = build_faculty_colors()
    colors = {}
    for u in units:
        if u == "KU samlet":
            colors[u] = "#666666"
        elif u in faculty_colors:
            colors[u] = faculty_colors[u]

    inst_units = [u for u in units if u not in colors]
    by_faculty = {}
    for u in inst_units:
        parts = u.split(" | ")
        parent_fak = parts[-1] if len(parts) > 1 else None
        by_faculty.setdefault(parent_fak, []).append(u)
    for parent_fak, insts in by_faculty.items():
        insts_sorted = sorted(insts, key=lambda u: -totals.get(u, 0))
        base = faculty_colors.get(parent_fak)
        if base:
            shades = hls_gradient(base, len(insts_sorted))
            for i, u in enumerate(insts_sorted):
                colors[u] = shades[i]
        else:
            fallback = ku_color_sequence(len(insts_sorted))
            for i, u in enumerate(insts_sorted):
                colors[u] = fallback[i]
    return colors

def _render_org_trend(trend_data, title, key_suffix, yaxis_title="Antal"):
    """Linjegraf, én linje pr. organisatorisk enhed (eller 'KU samlet') -
    samme princip som KU_publikationer/tabs/oversigt.py::_render_org_trend()."""
    if not trend_data:
        st.error("Ingen data matcher de valgte filtre.")
        return
    years_sorted = sorted(trend_data.keys())
    units = sorted(
        {u for cats in trend_data.values() for u in cats},
        key=lambda u: (u != "KU samlet", u),
    )

    totals = {}
    for cats in trend_data.values():
        for u, n in cats.items():
            totals[u] = totals.get(u, 0) + n
    colors = _unit_colors(units, totals)

    fig = go.Figure()
    for unit in units:
        y_vals = [trend_data.get(year, {}).get(unit, 0) for year in years_sorted]
        fig.add_trace(go.Scatter(
            x=years_sorted, y=y_vals, mode="lines+markers", name=unit,
            line=dict(
                color=colors.get(unit, "#666666"),
                width=3 if unit == "KU samlet" else 2,
            ),
            marker=dict(size=6),
            hovertemplate=f"<b>{unit}</b><br>%{{x}}<br>%{{y:,}}<extra></extra>",
        ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=14)),
        xaxis=dict(title="Udgivelsesår", dtick=1),
        yaxis=dict(title=yaxis_title),
        plot_bgcolor="white", height=420,
        showlegend=True,
        legend=dict(orientation="v", yanchor="top", y=1.0, xanchor="left", x=1.02),
        margin=dict(t=50, b=10, l=10, r=150),
    )
    st.plotly_chart(
        fig, width="stretch", config=PLOTLY_CONFIG, key=f"trend_chart_{key_suffix}"
    )
    # XX todo: eksport af denne tabel til Excel, hvis/når components/export.py
    # porteres fra KU_publikationer (findes ikke i dette repo endnu).

def _render_ratio_trend(pair_trend, pub_trend, title, key_suffix):
    """Linjegraf: forfatterpar pr. samarbejds-publikation, år for år, pr.
    organisatorisk enhed - samme farvelogik som _render_org_trend(), men
    viser forholdet mellem to allerede hentede trend-datasæt."""
    years_sorted = sorted(set(pair_trend.keys()) | set(pub_trend.keys()))
    units = sorted(
        {u for cats in pair_trend.values() for u in cats}
        | {u for cats in pub_trend.values() for u in cats},
        key=lambda u: (u != "KU samlet", u),
    )
    if not units:
        st.error("Ingen data matcher de valgte filtre.")
        return

    totals = {}
    for cats in pair_trend.values():
        for u, n in cats.items():
            totals[u] = totals.get(u, 0) + n
    colors = _unit_colors(units, totals)

    fig = go.Figure()
    for unit in units:
        y_vals = []
        for year in years_sorted:
            n_pair = pair_trend.get(year, {}).get(unit, 0)
            n_pub = pub_trend.get(year, {}).get(unit, 0)
            ratio = round(n_pair / n_pub, 2) if n_pub else None
            y_vals.append(ratio)
        fig.add_trace(go.Scatter(
            x=years_sorted, y=y_vals, mode="lines+markers", name=unit,
            line=dict(
                color=colors.get(unit, "#666666"),
                width=3 if unit == "KU samlet" else 2,
            ),
            marker=dict(size=6),
            hovertemplate=(
                f"<b>{unit}</b><br>%{{x}}<br>"
                "%{y:.2f} forfatterpar pr. publikation<extra></extra>"
            ),
        ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=14)),
        xaxis=dict(title="Udgivelsesår", dtick=1),
        yaxis=dict(title="Forfatterpar pr. publikation"),
        plot_bgcolor="white", height=420,
        showlegend=True,
        legend=dict(orientation="v", yanchor="top", y=1.0, xanchor="left", x=1.02),
        margin=dict(t=50, b=10, l=10, r=150),
    )
    st.plotly_chart(
        fig, width="stretch", config=PLOTLY_CONFIG, key=f"trend_chart_{key_suffix}"
    )

def render(filters):
    st.markdown(
"""
### Oversigt over KU's sampublicering

Fanen giver et samlet overblik over det interne samarbejde bag KU's publikationer - altså, 
hvor meget KU's forskere sampublicerer med hinanden, på tværs af fakulteter, institutter og 
stillingsgrupper. 

Et **forfatterpar** er to interne KU-forfattere, der begge står som medforfattere på samme 
publikation. Én publikation med flere interne medforfattere bidrager derfor med flere
forfatterpar - se Datagrundlag-fanen for den fulde forklaring og for forskellen mellem 
'Forfatterpar' og 'Publikationer' som optællingsmetrik. 
"""
    )

    kpis = _query_kpis(filters)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c4.metric(
        "Forfatterpar", f"{kpis['n_pairs']:,}",
        help="Antal interne forfatterpar i det valgte udsnit.",
    )
    c2.metric(
        "Publikationer med samarbejde", f"{kpis['n_pubs_samarbejde']:,}",
        help="Antal publikationer med mindst ét internt forfatterpar.",
    )
    c5.metric(
        "Andel af KU's publikationer", f"{kpis['andel_samarbejde']:.1f}%",
        help=(
            "Andelen af KU's publikationer (mindst én intern forfatter), "
            "der har mindst ét internt forfatterpar."
        ),
    )
    c3.metric(
        "Interne forfattere", f"{kpis['n_authors']:,}",
        help="Antallet af unikke, udgivne interne KU-forfattere.",
    )
    c6.metric(
        "Forfatterpar pr. samarbejds-publikation", f"{kpis['par_pr_pub']:.2f}",
        help=(
            "Gennemsnitligt antal forfatterpar pr. publikation blandt de "
            "publikationer, der HAR internt samarbejde."
        ),
    )
    c1.metric(
        "Publikationer i alt", f"{kpis['n_pubs_total']:,}",
        help=(
            "Alle KU's publikationer i det valgte udsnit (mindst én intern "
            "forfatter), uanset internt samarbejde - samme optælling som "
            "'Publikationer' i KU_publikationer/tabs/oversigt.py, så du kan "
            "tjekke tallet direkte op mod den fane."
        ),
    )






    

