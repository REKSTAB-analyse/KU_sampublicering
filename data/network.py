import streamlit as st
from math import pi, cos, sin
 
from config import dims_for_mode, metric_count_sql, CPR, HIERARKI, base_mode, sex_in_mode, nat_in_mode, grp_in_mode, inst_in_mode
from data.loader import get_pairs_cursor, get_pubs_cursor
 
# UI-labels ("Mænd"/"Kvinder") -> koder i data ("M"/"K"). CPR = {"m": "Mænd", "k": "Kvinder"}.
_KOEN_LABEL_TO_KODE = {v: k.upper() for k, v in CPR.items()}

_DIM_TO_EDGE_TYPE_COL = {
    "Fak": "Edge_type_fak", "Inst": "Edge_type_inst", "Stil": "Edge_type_stil",
    "Koen": "Edge_type_koen", "Statsbg": "Edge_type_statsbg",
}
 
 
# ---------------------------------------------------------------------------
# KANTER (NYT - erstatter merge_grp_to_facgrp...merge_fac_by_nat,
# _split_nodes_sex, apply_mode_merge, merge_grp_variants, gl. linje 528-1010)
# ---------------------------------------------------------------------------
 
@st.cache_data
def load_edges(filters: dict, mode: str) -> list[dict]:

    dims = dims_for_mode(mode)
    if not dims:
        return []
 
    cols_1 = ", ".join(f"{d}_1" for d in dims)
    cols_2 = ", ".join(f"{d}_2" for d in dims)
    count_expr = metric_count_sql(filters.get("metric", "forfatterpar"))
 
    where_clauses = ["Year BETWEEN ? AND ?"]
    params = [filters["aar_fra"], filters["aar_til"]]
 
    def both_sides_in(col: str, values: list):
        if not values:
            return
        ph = ", ".join("?" for _ in values)
        where_clauses.append(f"{col}_1 IN ({ph}) AND {col}_2 IN ({ph})")
        params.extend(values)
        params.extend(values)
 
    both_sides_in("Fak", filters.get("fakultet"))
    both_sides_in("Inst", filters.get("institutter"))
    both_sides_in("Stil", filters.get("stillingsgrupper"))
 
    koen_koder = [_KOEN_LABEL_TO_KODE.get(v, v) for v in filters.get("køn", [])]
    both_sides_in("Koen", koen_koder)
    both_sides_in("Statsbg", filters.get("statsborgerskab"))
 
    def in_filter(col: str, values: list):
        if not values:
            return
        ph = ", ".join("?" for _ in values)
        where_clauses.append(f"{col} IN ({ph})")
        params.extend(values)
 
    in_filter("Type", filters.get("typer"))
    in_filter("Indholdstype", filters.get("indholdstyper"))
    in_filter("Sprog", filters.get("sprog"))
    in_filter("Peer_review", filters.get("peer"))
    in_filter("Open_Access", filters.get("open_access"))
 
    har_doi = filters.get("har_doi") or ["Ja", "Nej"]
    if set(har_doi) == {"Ja"}:
        where_clauses.append("DOI IS NOT NULL AND DOI != ''")
    elif set(har_doi) == {"Nej"}:
        where_clauses.append("(DOI IS NULL OR DOI = '')")
 
    if filters.get("min_forfattere") is not None and filters.get("max_forfattere") is not None:
        where_clauses.append("Antal_forfattere BETWEEN ? AND ?")
        params += [filters["min_forfattere"], filters["max_forfattere"]]

    edge_type_filters = filters.get("edge_type_filters", {})
    for dim in dims:
        allowed = edge_type_filters.get(dim)
        if allowed and set(allowed) != {"intra", "inter"}:
            col = _DIM_TO_EDGE_TYPE_COL.get(dim)
            if col:
                ph = ", ".join("?" for _ in allowed)
                where_clauses.append(f"{col} IN ({ph})")
                params.extend(allowed)

    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT {cols_1}, {cols_2}, {count_expr} AS weight
        FROM pairs
        WHERE {where_sql}
        GROUP BY {cols_1}, {cols_2}
    """
    rows = get_pairs_cursor().execute(sql, params).fetchall()
 
    n = len(dims)
    result = []
    for row in rows:
        vals_1, vals_2, weight = row[:n], row[n:2 * n], row[-1]
        rec = {f"{d}_1": v for d, v in zip(dims, vals_1)}
        rec.update({f"{d}_2": v for d, v in zip(dims, vals_2)})
        rec["weight"] = weight
        result.append(rec)
    return result
 
 
@st.cache_data
def load_node_totals(filters: dict, mode: str) -> dict:
    """Antal UNIKKE forfattere pr. organisatorisk enhed, respekterer nu ALLE
    sidepanel-filtre (samme mønster som load_edges()) - ikke kun Year."""
    dims = dims_for_mode(mode)
    if not dims:
        return {}
    select_dims = ", ".join(dims)

    where_clauses = ["Intern = 'Intern'", "Year BETWEEN ? AND ?"]
    params = [filters["aar_fra"], filters["aar_til"]]

    def in_filter(col: str, values: list):
        if not values:
            return
        ph = ", ".join("?" for _ in values)
        where_clauses.append(f"{col} IN ({ph})")
        params.extend(values)

    in_filter("Fak", filters.get("fakultet"))
    in_filter("Inst", filters.get("institutter"))
    in_filter("Stil", filters.get("stillingsgrupper"))

    koen_koder = [_KOEN_LABEL_TO_KODE.get(v, v) for v in filters.get("køn", [])]
    in_filter("Koen", koen_koder)
    in_filter("Statsbg", filters.get("statsborgerskab"))

    in_filter("Type", filters.get("typer"))
    in_filter("Indholdstype", filters.get("indholdstyper"))
    in_filter("Sprog", filters.get("sprog"))
    in_filter("Peer_review", filters.get("peer"))
    in_filter("Open_Access", filters.get("open_access"))

    har_doi = filters.get("har_doi") or ["Ja", "Nej"]
    if set(har_doi) == {"Ja"}:
        where_clauses.append("DOI IS NOT NULL AND DOI != ''")
    elif set(har_doi) == {"Nej"}:
        where_clauses.append("(DOI IS NULL OR DOI = '')")

    if filters.get("min_forfattere") is not None and filters.get("max_forfattere") is not None:
        where_clauses.append("Antal_forfattere BETWEEN ? AND ?")
        params += [filters["min_forfattere"], filters["max_forfattere"]]

    if filters.get("min_forfattere") is not None and filters.get("max_forfattere") is not None:
        where_clauses.append("Antal_forfattere BETWEEN ? AND ?")
        params += [filters["min_forfattere"], filters["max_forfattere"]]

    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT {select_dims}, COUNT(DISTINCT ext_id) AS n
        FROM pubs
        WHERE {where_sql}
        GROUP BY {select_dims}
    """

    where_sql = " AND ".join(where_clauses)
    sql = f"""
        SELECT {select_dims}, COUNT(DISTINCT ext_id) AS n
        FROM pubs
        WHERE {where_sql}
        GROUP BY {select_dims}
    """
    rows = get_pubs_cursor().execute(sql, params).fetchall()

    result = {}
    for row in rows:
        dim_values, n = row[:-1], row[-1]
        dim_label = " | ".join(str(v) for v in dim_values)
        result[dim_label] = n
    return result
 
 
# ---------------------------------------------------------------------------
# LAYOUT (gl. linje 1153-1590 - filens største enkeltfunktion i originalen)
# ---------------------------------------------------------------------------
 
def _node_type_for_mode(mode: str) -> str:
    """Svarer til node_meta[nid]['type'] i den gamle app - men der var det
    sat direkte på hver node ved konstruktionstid; her er den entydigt
    bestemt af mode (samme type for alle noder i én analyse), siden en node
    her IKKE er andet end dens kombination af dims_for_mode(mode)-værdier.
    Dybeste aktive organisatoriske niveau (grp > inst > fac), plus _sex/_nat
    -suffiks. S og N kan aldrig begge være aktive (se sidepanelets radio),
    så suffikset er entydigt."""
    if grp_in_mode(mode):
        node_base = "grp"
    elif inst_in_mode(mode):
        node_base = "inst"
    else:
        node_base = "fac"
    if sex_in_mode(mode):
        return f"{node_base}_sex"
    if nat_in_mode(mode):
        return f"{node_base}_nat"
    return node_base
 
 
_DIM_TO_META_KEY = {"Fak": "fac", "Inst": "inst", "Stil": "grp", "Koen": "sex", "Statsbg": "statsborgerskab"}
 
 
def _build_node_meta(nodes: set, dims: list, mode: str) -> dict:
    """Genopbygger den gamle node_meta-struktur (nid -> {fac, inst, grp,
    sex, statsborgerskab, type}) ud fra jeres nye node-nøgler (" | ".join af
    dims_for_mode(mode)-værdier, samme konvention som load_node_totals() og
    render_pyvis_network()). Nødvendig for at kunne genbruge compute_layout()
    stort set uændret."""
    node_type = _node_type_for_mode(mode)
    node_meta = {}
    for key in nodes:
        vals = key.split(" | ")
        rec = {"type": node_type}
        for dim, val in zip(dims, vals):
            meta_key = _DIM_TO_META_KEY.get(dim)
            if meta_key == "sex":
                rec["sex"] = val.lower() if val else val  # "M"/"K" -> "m"/"k"
            elif meta_key:
                rec[meta_key] = val
        node_meta[key] = rec
    return node_meta
 
 
def compute_layout(nodes_keep: set, node_meta: dict, mode: str, network_scale: int = 1200,
                    n_selected_nats: int = None) -> dict:
    """Porteret NÆSTEN ORDRET fra sampubliceringsapp.py, linje 1153-1588 -
    kun importerne er justeret (pi/cos/sin, HIERARKI, base_mode,
    sex_in_mode, nat_in_mode importeres nu fra toppen af denne fil/config.py
    i stedet for at være moduludeblet globalt). Selve geometrien er urørt.
 
    Deterministisk, hierarkisk radial-positionering - IKKE fysik-baseret,
    bevidst, så noder (og dermed kanter) ikke bevæger sig ved re-rendering.
    Se compute_layout_for_edges() nedenfor for den nye indgang, der bygger
    nodes_keep/node_meta ud fra load_edges()'s output.
    """
 
    def _r(n: int, min_dist: float = 250, floor: float = 1200) -> float:
        """Radius så n noder har mindst min_dist pixels mellem sig."""
        if n <= 1:
            return 0
        return max(min_dist * n / (2 * pi), floor)
 
    # Forstærk network_scale eksponentielt så slideren har større effekt
    network_scale = int(network_scale ** 1.5 / 35)
 
    R_G = 100
 
    pos = {}
 
    # --- Faculty centres ---
    fac_centers = {}
    faculties = sorted({m["fac"] for m in node_meta.values() if "fac" in m})
    faculties = ["HUM", "SCIENCE", "SAMF", "JUR", "SUND", "TEO"]
    k = max(1, len(faculties))
 
    if base_mode(mode) == "FIG":
        _insts_per_fac = {}
        for m in node_meta.values():
            if m.get("type") in ("grp", "grp_sex", "grp_nat") and m.get("fac"):
                _insts_per_fac.setdefault(m["fac"], set()).add(m.get("inst", ""))
        _max_insts = max((len(v) for v in _insts_per_fac.values()), default=1)
        _n_nats = max(1, len({m.get("statsborgerskab","") for m in node_meta.values() if m.get("statsborgerskab")})) if nat_in_mode(mode) else 1
        _R_NAT_est = max(50, _n_nats * network_scale // 30)
        if nat_in_mode(mode):
            R_FAC = _r(k, network_scale + _max_insts * (network_scale // 10 + _R_NAT_est * 3 // 2) + 100, floor=network_scale * 2)
        else:
            R_FAC = _r(k, network_scale + _max_insts * (network_scale // 20 + _R_NAT_est * 1) + 100, floor=network_scale * 3 // 2)
 
    elif base_mode(mode) == "IG":     
        _insts_per_fac = {}
        for m in node_meta.values():
            if m.get("fac") and m.get("inst"):
                _insts_per_fac.setdefault(m["fac"], set()).add(m["inst"])
        _max_insts = max((len(v) for v in _insts_per_fac.values()), default=1)
        _n_nats = max(1, len({m.get("statsborgerskab","") for m in node_meta.values() if m.get("statsborgerskab")})) if nat_in_mode(mode) else 1
        _R_NAT_est = max(50, _n_nats * network_scale // 30)
        R_FAC = _r(k, network_scale + _max_insts * (network_scale // 10 + _R_NAT_est * 1) + 100, floor=network_scale * 2)
 
    elif base_mode(mode) == "I":         
        _insts_per_fac = {}
        for m in node_meta.values():
            if m.get("fac") and m.get("inst"):
                _insts_per_fac.setdefault(m["fac"], set()).add(m["inst"])
        _max_insts = max((len(v) for v in _insts_per_fac.values()), default=1)
        _n_nats = max(1, len({m.get("statsborgerskab","") for m in node_meta.values() if m.get("statsborgerskab")})) if nat_in_mode(mode) else 1
        _R_NAT_est = max(50, _n_nats * network_scale // 30)
        R_FAC = _r(k, network_scale + _max_insts * (network_scale // 10 + _R_NAT_est * 1) + 100, floor=network_scale * 2)
 
    elif nat_in_mode(mode) and base_mode(mode) == "F":
        # NF-mode: fakulteter har nationalitets-clustre rundt om sig
        _n_nats = n_selected_nats if n_selected_nats is not None else max(1, len({m.get("statsborgerskab","") for m in node_meta.values() if m.get("statsborgerskab")}))
        _R_NAT_est = max(30, _n_nats * network_scale // 80)
        R_FAC = _r(k, network_scale + _R_NAT_est * 1, floor=network_scale + _R_NAT_est // 2)
 
    elif nat_in_mode(mode) and base_mode(mode) == "FI":
        # NFI-mode: hvert institut har en nationalitets-cluster
        _max_insts = 1
        _insts_per_fac = {}
        for m in node_meta.values():
            if m.get("fac") and m.get("inst"):
                _insts_per_fac.setdefault(m["fac"], set()).add(m["inst"])
        _max_insts = max((len(v) for v in _insts_per_fac.values()), default=1)
        _n_nats = max(1, len({m.get("statsborgerskab","") for m in node_meta.values() if m.get("statsborgerskab")}))
        _R_NAT_est = max(50, _n_nats * network_scale // 30)
        R_FAC = _r(k, network_scale + _max_insts * (network_scale // 20 + _R_NAT_est * 1 // 2) + 100, floor=network_scale * 3 // 2)

    elif base_mode(mode) == "F" and not nat_in_mode(mode):
        # NYT: ren F-mode (uden nationalitet) - markant mindre radius, så de
        # 6 fakultets-noder ligger tæt sammen i stedet for spredt ud over et
        # kæmpe, tomt lærred. Juster divisoren (// 3) for mere/mindre afstand.
        R_FAC = _r(k, network_scale // 3, floor=network_scale // 3)

    else:
        R_FAC = _r(k, network_scale, floor=network_scale)
 
    for i, fac in enumerate(faculties):
        theta = 2 * pi * i / k
        fac_centers[fac] = (R_FAC * cos(theta), R_FAC * sin(theta))
  
    # --- Institute centres ---
    inst_centers = {}
 
    if base_mode(mode) in ("FI", "IS"):
        inst_by_fac = {}
        for m in node_meta.values():
            if m.get("type") in ("inst", "inst_sex", "inst_nat"):
                inst_by_fac.setdefault(m["fac"], []).append(m["inst"])
        _n_nats = max(1, len({m.get("statsborgerskab","") for m in node_meta.values() if m.get("statsborgerskab")})) if nat_in_mode(mode) else 1
        _R_NAT_est = max(50, _n_nats * network_scale // 30)
        for fac, insts in inst_by_fac.items():
            cx, cy = fac_centers.get(fac, (0, 0))
            unique_insts = sorted(set(insts))
            k = max(1, len(unique_insts))
            if nat_in_mode(mode):
                R_INST = _r(k, network_scale // 3 + _R_NAT_est * 2, floor=network_scale // 3 + _R_NAT_est * 2)
            else:
                R_INST = _r(k, 200, floor = network_scale // 3)
            if fac in ("TEO", "JUR"):
                R_INST *= 0.3  # færre institutter - mindre cirkel
            for j, inst in enumerate(unique_insts):
                theta = 2 * pi * j / k
                inst_centers[(fac, inst)] = (cx + R_INST * cos(theta), cy + R_INST * sin(theta))
 
    elif base_mode(mode) == "I" and not sex_in_mode(mode) and not nat_in_mode(mode):
        # Ren I-mode: flad ring, uafhængigt af fakultet
        unique_insts = sorted({
            node_meta[nid2].get("inst", "")
            for nid2 in nodes_keep if node_meta[nid2].get("inst")
        })
        n_insts = max(1, len(unique_insts))
        R_INST_FLAT = _r(n_insts, network_scale // 2, floor=network_scale)
        for j, inst in enumerate(unique_insts):
            theta = 2 * pi * j / n_insts
            cx_i, cy_i = R_INST_FLAT * cos(theta), R_INST_FLAT * sin(theta)
            for nid2 in nodes_keep:
                if node_meta[nid2].get("inst") == inst:
                    pos[nid2] = (cx_i, cy_i)
        return pos

    elif base_mode(mode) == "I":
        inst_by_fac = {}
        for m in node_meta.values():
            if m.get("fac") and m.get("inst"):
                inst_by_fac.setdefault(m["fac"], set()).add(m["inst"])
        _n_nats = max(1, len({m.get("statsborgerskab","") for m in node_meta.values() if m.get("statsborgerskab")})) if nat_in_mode(mode) else 1
        _R_NAT_est = max(50, _n_nats * network_scale // 30)
        for fac, insts in inst_by_fac.items():
            cx, cy = fac_centers.get(fac, (0, 0))
            unique_insts = sorted(insts)
            k = max(1, len(unique_insts))
            R_INST = _r(k, network_scale // 3 + _R_NAT_est * 2, floor=network_scale // 3 + _R_NAT_est * 2)
            for j, inst in enumerate(unique_insts):
                theta = 2 * pi * j / k
                inst_centers[(fac, inst)] = (cx + R_INST * cos(theta), cy + R_INST * sin(theta))
 
    elif base_mode(mode) == "FIG":
        insts_by_fac = {}
        for nid, m in node_meta.items():
            if m.get("type") in ("grp", "grp_sex", "grp_nat"):
                insts_by_fac.setdefault(m["fac"], []).append(m["inst"])
        _n_nats = max(1, len({m.get("statsborgerskab","") for m in node_meta.values() if m.get("statsborgerskab")})) if nat_in_mode(mode) else 1
        _R_NAT_est = max(50, _n_nats * network_scale // 30)
        for fac, insts in insts_by_fac.items():
            cx, cy = fac_centers.get(fac, (0, 0))
            insts = sorted(set(insts))
            k = max(1, len(insts))
            if nat_in_mode(mode):
                R_INST = _r(k, network_scale // 2 + _R_NAT_est * 2, floor=network_scale // 2 + _R_NAT_est * 2)
            elif sex_in_mode(mode):
                R_INST = _r(k, network_scale // 2, floor=network_scale // 3)
            else:
                R_INST = _r(k, network_scale // 3, floor=network_scale // 4)
            for j, inst in enumerate(insts):
                theta = 2 * pi * j / k
                inst_centers[(fac, inst)] = (cx + R_INST * cos(theta), cy + R_INST * sin(theta))
 
    elif base_mode(mode) == "IG":
        # IG har intet 'fac' i node_meta (dims_for_mode("IG") = ["Inst","Stil"])
        # - flad ring af institutter, samme princip som ren I-mode.
        # inst_centers nøgles her KUN på "inst" (streng), ikke på (fac, inst).
        unique_insts = sorted({m.get("inst", "") for m in node_meta.values() if m.get("inst")})
        k = max(1, len(unique_insts))
        R_INST = _r(k, network_scale // 2, floor=network_scale)
        for j, inst in enumerate(unique_insts):
            theta = 2 * pi * j / k
            inst_centers[inst] = (R_INST * cos(theta), R_INST * sin(theta))

    elif base_mode(mode) == "G" and not nat_in_mode(mode):
        if sex_in_mode(mode):
            # SG-mode: én ring, stillingsgrupper sorteret efter hierarki,
            # k til venstre og m til højre inden for hver gruppe
            unique_grps = sorted(
                {node_meta[nid2].get("grp", "") for nid2 in nodes_keep if node_meta[nid2].get("grp")},
                key=lambda g: HIERARKI.get(g, 999)
            )
            n_grps = max(1, len(unique_grps))
            R_G = _r(n_grps, network_scale // 4, floor=network_scale // 2)
            pair_offset = network_scale // 8
            for j, grp in enumerate(unique_grps):
                theta = 2 * pi * j / n_grps
                cx_grp = R_G * cos(theta)
                cy_grp = R_G * sin(theta)
                pair = sorted(
                    [nid2 for nid2 in nodes_keep if node_meta[nid2].get("grp") == grp],
                    key=lambda n: node_meta[n].get("sex", "m")  # k før m (k < m alfabetisk)
                )
                n_pair = len(pair)
                angle_gap = 0.38  # radianer mellem k og m - juster efter smag
                for p_idx, nid2 in enumerate(pair):
                    offset_angle = angle_gap * (p_idx - (n_pair - 1) / 2)
                    t = theta + offset_angle
                    pos[nid2] = (R_G * cos(t), R_G * sin(t))
        else:
            # G-mode: fakultetsklynger
            grps_by_fac = {}
            for nid2 in nodes_keep:
                fac = node_meta[nid2].get("fac", "")
                grps_by_fac.setdefault(fac, []).append(nid2)
            for fac, fac_nodes in grps_by_fac.items():
                cx, cy = fac_centers.get(fac, (0, 0))
                fac_nodes_sorted = sorted(fac_nodes, key=lambda n: HIERARKI.get(node_meta[n].get("grp", ""), 999))
                k = max(1, len(fac_nodes_sorted))
                R_G = _r(k, network_scale // 6, floor=network_scale // 5)
                for j, nid2 in enumerate(fac_nodes_sorted):
                    theta = 2 * pi * j / k
                    pos[nid2] = (cx + R_G * cos(theta), cy + R_G * sin(theta))
        return pos
 
    # --- Place every node ---
    for nid in nodes_keep:
        if nid in pos:
            continue
        m = node_meta[nid]
        t = m.get("type")
 
        if t == "fac_sex":
            cx, cy = fac_centers.get(m["fac"], (0, 0))
            R_SEX = network_scale // 3
            pos[nid] = (cx + (R_SEX if m.get("sex") == "m" else -R_SEX), cy)
 
        elif t == "fac_nat":
            fac = m.get("fac", "")
            nat = m.get("statsborgerskab", "")
            cx, cy = fac_centers.get(fac, (0, 0))
            nats_for_fac = sorted({
                _m.get("statsborgerskab", "")
                for _m in node_meta.values()
                if _m.get("fac") == fac and _m.get("statsborgerskab")
            })
            k   = max(1, len(nats_for_fac))
            j   = nats_for_fac.index(nat) if nat in nats_for_fac else 0
            R_NAT = _r(k, network_scale // 10, floor=max(80, k * network_scale // 60))
            theta = 2 * pi * j / k
            pos[nid] = (cx + R_NAT * cos(theta), cy + R_NAT * sin(theta))
        
        elif t == "inst_nat":
            fac  = m.get("fac", "")
            inst = m.get("inst", "")
            nat  = m.get("statsborgerskab", "")

            if fac:
                cx, cy = inst_centers.get((fac, inst), fac_centers.get(fac, (0, 0)))
            else:
                alle_insts = sorted({
                    _m.get("inst", "") for _m in node_meta.values() if _m.get("inst")
                })
                n_insts_flat = max(1, len(alle_insts))
                i_idx = alle_insts.index(inst) if inst in alle_insts else 0
                R_INST_FLAT = _r(n_insts_flat, network_scale // 2, floor=network_scale)
                theta_inst = 2 * pi * i_idx / n_insts_flat
                cx, cy = (R_INST_FLAT * cos(theta_inst), R_INST_FLAT * sin(theta_inst))

            nats_for_inst = sorted({
                _m.get("statsborgerskab", "")
                for _m in node_meta.values()
                if _m.get("inst") == inst and _m.get("statsborgerskab")
            })
            k     = max(1, len(nats_for_inst))
            j     = nats_for_inst.index(nat) if nat in nats_for_inst else 0
            R_NAT = _r(k, network_scale // 10, floor=max(80, k * network_scale // 60))
            theta = 2 * pi * j / k
            pos[nid] = (cx + R_NAT * cos(theta), cy + R_NAT * sin(theta))
 
        elif t == "grp_nat":
            fac  = m.get("fac", "")
            inst = m.get("inst", "")
            nat  = m.get("statsborgerskab", "")
            grp  = m.get("grp", "")
            _bm  = base_mode(mode)
            
            # Vælg cluster-center afhængig af mode
            if _bm in ("FIG", "IG", "FI", "I"):
                # Brug institut-center når institut er aktivt niveau
                cx, cy = inst_centers.get((fac, inst), fac_centers.get(fac, (0, 0)))
                # Stillingsgrupper for dette institut
                grps_for_unit = sorted(
                    {_m.get("grp","") for _m in node_meta.values() 
                    if _m.get("grp") and _m.get("fac") == fac and _m.get("inst") == inst},
                    key=lambda g: HIERARKI.get(g, 999)
                )
            elif _bm in ("FG", "F"):
                # Brug fakultets-center når kun fakultet er aktivt
                cx, cy = fac_centers.get(fac, (0, 0))
                grps_for_unit = sorted(
                    {_m.get("grp","") for _m in node_meta.values() 
                    if _m.get("grp") and _m.get("fac") == fac},
                    key=lambda g: HIERARKI.get(g, 999)
                )
            else:
                # NG-mode: ring af alle stillingsgrupper på KU-niveau (uændret)
                grps_for_unit = sorted({_m.get("grp","") for _m in node_meta.values() if _m.get("grp")}, 
                                    key=lambda g: HIERARKI.get(g, 999))
                k_grp = max(1, len(grps_for_unit))
                R_GRP = _r(k_grp, network_scale // 2, floor=network_scale)
                g_idx = grps_for_unit.index(grp) if grp in grps_for_unit else 0
                theta_grp = 2 * pi * g_idx / k_grp
                cx = R_GRP * cos(theta_grp)
                cy = R_GRP * sin(theta_grp)
            
            # For institut/fakultet-modes: placér stillingsgruppe-cluster rundt om enheds-center
            if _bm in ("FIG", "IG", "FI", "I", "FG", "F"):
                n_grps = max(1, len(grps_for_unit))
                if _bm == "FIG":
                    R_GRP_local = _r(n_grps, network_scale // 4, floor=network_scale // 3)
                else:
                    R_GRP_local = _r(n_grps, network_scale // 6, floor=network_scale // 5)
                g_idx = grps_for_unit.index(grp) if grp in grps_for_unit else 0
                theta_grp = 2 * pi * g_idx / n_grps
                cx_grp = cx + R_GRP_local * cos(theta_grp)
                cy_grp = cy + R_GRP_local * sin(theta_grp)
            else:
                # NG-mode: cx/cy er allerede stillingsgruppe-positionen
                cx_grp = cx
                cy_grp = cy
            
            # Nationaliteter for denne stillingsgruppe i den specifikke enhed
            nats_for_grp = sorted({
                _m.get("statsborgerskab","")
                for _m in node_meta.values()
                if _m.get("grp") == grp and _m.get("statsborgerskab")
                and (not fac or _m.get("fac") == fac)
                and (not inst or _bm not in ("FIG", "IG", "FI", "I") or _m.get("inst") == inst)
            })
            k_nat     = max(1, len(nats_for_grp))
            R_NAT     = max(50, k_nat * network_scale // 40)
            n_idx     = nats_for_grp.index(nat) if nat in nats_for_grp else 0
            theta_nat = 2 * pi * n_idx / k_nat
            pos[nid]  = (cx_grp + R_NAT * cos(theta_nat), cy_grp + R_NAT * sin(theta_nat))
 
        elif t == "inst_sex":
            fac = m.get("fac")
            sex = m.get("sex", "m")
            inst = m.get("inst")

            if fac:
                cx, cy = fac_centers.get(fac, (0, 0))
                fac_insts = sorted({
                    node_meta[nid2].get("inst")
                    for nid2 in nodes_keep
                    if node_meta[nid2].get("type") == "inst_sex"
                    and node_meta[nid2].get("fac") == fac
                    and node_meta[nid2].get("inst")
                })
            else:
                cx, cy = (0, 0)
                fac_insts = sorted({
                    node_meta[nid2].get("inst")
                    for nid2 in nodes_keep
                    if node_meta[nid2].get("type") == "inst_sex"
                    and node_meta[nid2].get("inst")
                })
            n_insts = max(1, len(fac_insts))
            inst_idx = fac_insts.index(inst) if inst in fac_insts else 0
 
            # Køn bestemmer halvkreds: k = venstre (pi/2 → 3pi/2), m = højre (3pi/2 → 5pi/2)
            if n_insts == 1:
                # Kun ét institut: placer k og m fast til venstre/højre
                R_INST = network_scale // 3
                pos[nid] = (cx + (R_INST if sex == "m" else -R_INST), cy)
            else:
                if sex == "k":
                    theta = pi / 2 + inst_idx * pi / n_insts
                else:
                    theta = 3 * pi / 2 + inst_idx * pi / n_insts
                R_INST = _r(n_insts, network_scale // 3, floor=network_scale // 3)
                pos[nid] = (cx + R_INST * cos(theta), cy + R_INST * sin(theta))
 
        elif t == "fac":
            pos[nid] = fac_centers.get(m["fac"], (0, 0))
 
        elif t == "inst":
            center = inst_centers.get((m["fac"], m["inst"]))
            if center is None:
                continue
            pos[nid] = center
 
        elif t in ("grp", "grp_sex"):
            fac  = m.get("fac")
            inst = m.get("inst")
            sex  = m.get("sex", "")
            _bm  = base_mode(mode)
 
            # SF/SFI-modes uden stillingsgrupper: placér grp_sex som "institut-stand-in"
            # k til venstre, m til højre omkring fakultets-center
            if sex_in_mode(mode) and sex and "F" in _bm and "G" not in _bm:
                cx, cy = fac_centers.get(fac, (0, 0))
                fac_insts = sorted({
                    node_meta[nid2].get("inst", "")
                    for nid2 in nodes_keep
                    if node_meta[nid2].get("fac") == fac
                    and node_meta[nid2].get("inst")
                })
                n_insts = max(1, len(fac_insts))
                inst_idx = fac_insts.index(inst) if inst in fac_insts else 0
                R_INST = _r(n_insts, network_scale // 3, floor=network_scale // 3)
                if n_insts == 1:
                    pos[nid] = (cx + (R_INST if sex == "m" else -R_INST), cy)
                else:
                    if sex == "k":
                        theta = pi / 2 + inst_idx * pi / n_insts
                    else:
                        theta = 3 * pi / 2 + inst_idx * pi / n_insts
                    pos[nid] = (cx + R_INST * cos(theta), cy + R_INST * sin(theta))
 
            elif _bm == "FIG":
                # SFIG: institut-center, cluster består af alle grp/grp_sex for det institut
                center = inst_centers.get((fac, inst))
                if center is None:
                    continue
                cx, cy = center
                cluster = sorted(
                    nid2 for nid2 in nodes_keep
                    if node_meta[nid2].get("type") in ("grp", "grp_sex")
                    and node_meta[nid2].get("fac") == fac
                    and node_meta[nid2].get("inst") == inst
                )
            elif _bm == "FG":
                # SFG: fakultets-center, cluster består af alle grp/grp_sex for det fakultet
                cx, cy = fac_centers.get(fac, (0, 0))
                cluster = sorted(
                    nid2 for nid2 in nodes_keep
                    if node_meta[nid2].get("type") in ("grp", "grp_sex")
                    and node_meta[nid2].get("fac") == fac
                )
            elif _bm in ("FI", "IG", "I"):
                center = inst_centers.get(inst) if _bm == "IG" else inst_centers.get((fac, inst))
                if center is None:
                    continue
                cx, cy = center
                cluster = sorted(
                    nid2 for nid2 in nodes_keep
                    if node_meta[nid2].get("type") in ("grp", "grp_sex")
                    and node_meta[nid2].get("fac") == fac
                    and node_meta[nid2].get("inst") == inst
                )
            else:
                cx, cy = fac_centers.get(fac, (0, 0))
                cluster = [nid]
 
            if nid in pos:
                continue
 
            grp_pairs = {}
            for nid2 in cluster:
                g = node_meta[nid2].get("grp", nid2)
                grp_pairs.setdefault(g, []).append(nid2)
            unique_grps = sorted(grp_pairs.keys(), key=lambda g: HIERARKI.get(g, 999))
            n_grps = max(1, len(unique_grps))
            if base_mode(mode) == "FIG":
                R_GRP      = _r(n_grps, network_scale // 10, floor=network_scale // 8)
                pair_offset = network_scale // 4
            else:
                R_GRP      = _r(n_grps, network_scale // 6, floor=network_scale // 5)
                pair_offset = network_scale // 2
 
            grp_idx = unique_grps.index(node_meta[nid].get("grp", nid)) if node_meta[nid].get("grp") in unique_grps else 0
            theta = 2 * pi * grp_idx / n_grps
            cx_grp = cx + R_GRP * cos(theta)
            cy_grp = cy + R_GRP * sin(theta)
 
            # Placer k og m ved siden af hinanden
            pair = grp_pairs.get(node_meta[nid].get("grp", nid), [nid])
            p_idx = pair.index(nid) if nid in pair else 0
            n_pair = len(pair)
            offset_x = pair_offset * (p_idx - (n_pair - 1) / 2)
            pos[nid] = (cx_grp + offset_x, cy_grp)
 
    return pos
 
 
def compute_layout_for_edges(edges: list, dims: list, mode: str,
                              network_scale: int = 1200, n_selected_nats: int = None) -> dict:
    """Ny indgang til compute_layout(): udleder nodes_keep + node_meta fra
    load_edges()'s output (i stedet for de gamle raw_nodes/raw_edges-dicts),
    og kalder den ellers uændrede compute_layout() ovenfor. Returnerer
    {node_nøgle: (x, y)}, hvor node_nøgle matcher samme " | "-konvention
    som load_node_totals().
    """
    nodes = set()
    for e in edges:
        nodes.add(" | ".join(str(e[f"{d}_1"]) for d in dims))
        nodes.add(" | ".join(str(e[f"{d}_2"]) for d in dims))
    node_meta = _build_node_meta(nodes, dims, mode)
    return compute_layout(nodes, node_meta, mode, network_scale, n_selected_nats)
 
 
# ---------------------------------------------------------------------------
# CENTRALITET, GRUPPETABELLER (gl. linje 1594-1734)
# ---------------------------------------------------------------------------
 
def aggregate_centrality_by(meta_key: str, edge_rows: list, *args, **kwargs):
    """TODO (fase 2): port fra gl. linje 1594-1610. Se note om ændret input
    i compute_layout() ovenfor - samme gælder her."""
    raise NotImplementedError
 
 
def build_grp_table_by_mode(weighted_deg, bet_cent, mode: str):
    """TODO (fase 2): port fra gl. linje 1610-1666."""
    raise NotImplementedError
 
 
def intra_inter_labels(mode: str) -> tuple:
    """TODO (fase 2): port fra gl. linje 1666-1674 - ren tekstfunktion, ingen ændringer nødvendige."""
    raise NotImplementedError
 
 
def filter_status_caption(mode: str, show_intra: bool, show_inter: bool, *args, **kwargs):
    """TODO (fase 2): port fra gl. linje 1674-1712."""
    raise NotImplementedError
 
 
def compute_modularity_pre_for_key(edge_rows: list, comm_key: str) -> float:
    """TODO (fase 2): port fra gl. linje 7838-7869 (lå langt fra resten i
    originalen, lige før render_tab_netvaerksstruktur - hører naturligt til
    her sammen med de øvrige metrik-funktioner)."""
    raise NotImplementedError
