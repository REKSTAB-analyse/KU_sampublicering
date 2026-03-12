import streamlit as st
import json
import os
import tempfile
import networkx as nx
from pyvis.network import Network
from matplotlib import colors as mcolors
from math import cos, sin, pi
import numpy as np
import colorsys
import pyarrow as pa
import io, csv
from datetime import datetime
from collections import defaultdict, OrderedDict
import plotly.graph_objects as go
from networkx.algorithms.community.quality import modularity as modq
from networkx.algorithms.community import greedy_modularity_communities
import ast
import math
import re
from pathlib import Path
import hashlib
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side, PatternFill
from openpyxl.utils import get_column_letter
import random
import paramiko
import base64

# ---------------------------------------------------------------------------
# CONFIG - all paths in one place
# ---------------------------------------------------------------------------

_ERDA = st.secrets["erda"]
DATA_PATH = _ERDA["data_path"]

@st.cache_resource
def get_sftp():
    transport = paramiko.Transport((_ERDA["host"], 22))
    transport.connect(username=_ERDA["username"], password=_ERDA["password"])
    return paramiko.SFTPClient.from_transport(transport)

def read_file(filename: str) -> bytes:
    sftp = get_sftp()
    with sftp.open(f"{DATA_PATH}/{filename}", "rb") as f:
        return f.read()

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

HIERARKI = {
    "Særlig stilling": -1,
    "Øvrige VIP (DVIP)": 0,
    "Ph.d.": 1,
    "Stillinger u. adjunktniveau": 2,
    "Postdoc": 3,
    "Adjunkt": 4,
    "Lektor": 5,
    "Professor": 6,
}

CPR = {
    "m": "Mænd",
    "k": "Kvinder"
}

GROUP_ORDER = sorted(HIERARKI.keys(), key=lambda g: HIERARKI[g])
LVL_MIN, LVL_MAX = min(HIERARKI.values()), max(HIERARKI.values())

FAC_ORDER = ["SAMF", "JUR", "TEO", "SUND", "HUM", "SCIENCE"]
FAC_ABBRS = {
    "Det Teologiske Fakultet": "TEO",
    "Det Juridiske Fakultet": "JUR",
    "Det Humanistiske Fakultet": "HUM",
    "Det Natur- og Biovidenskabelige Fakultet": "SCIENCE",
    "Det Samfundsvidenskabelige Fakultet": "SAMF",
    "Det Sundhedsvidenskabelige Fakultet": "SUND",
}

def make_abbr(name: str, existing: set = None) -> str:
    """Lav forkortelse af et navn ved at tage forbogstaver af ord, minus stopord."""
    _stop = {"for", "og", "i", "af", "til", "det", "den", "de", "en", "et",
             "med", "på", "ved", "om", "fra", "under", "over"}
    words = [w for w in name.split() if w.lower() not in _stop]
    abbr = "".join(w[0].upper() for w in words if w)
    if not abbr:
        abbr = name[:4].upper()
    # Hvis forkortelsen kolliderer, tilføj ekstra bogstav fra første ord
    if existing is not None:
        original = abbr
        i = 1
        while abbr in existing:
            abbr = original + (words[0][i].upper() if i < len(words[0]) else str(i))
            i += 1
    return abbr


# ---------------------------------------------------------------------------
# DATA LOADING
# ---------------------------------------------------------------------------
@st.cache_data
def load_network_data() -> dict:
    raw = json.loads(read_file("vip_transformed.json"))
    return {int(k): v for k, v in raw.items()}

@st.cache_data
def load_stilling_map() -> dict:
    result = {}
    reader = csv.DictReader(
        read_file("KU_stillingstyper.csv").decode("utf-8").splitlines(),
        delimiter=";"
    )
    for row in reader:
        raw = row["Stillingstype"].strip()
        med = row["medtages?"].strip()
        grp = row["Stillingsgruppe"].strip()
        if med == "1" and grp:
            result[raw] = grp
    return result

@st.cache_data
def load_inst_filter() -> tuple[set, dict]:
    inst_ok = set()
    inst_to_fac = {}
    reader = csv.DictReader(
        read_file("Fakulteter_institutter.csv").decode("utf-8-sig").splitlines(),
        delimiter=";"
    )
    for row in reader:
        if row["medtages?"].strip() == "1":
            fac  = row["Fakultet"].strip()
            inst = row["Institut"].strip()
            alt  = row["Alternativ"].strip()
            if inst:
                inst_ok.add(inst)
                inst_to_fac[inst] = fac
            if alt:
                inst_ok.add(alt)
                inst_to_fac[alt] = fac
    return inst_ok, inst_to_fac

@st.cache_data
def load_pubtype_map() -> dict:
    mapping = OrderedDict()
    lines = read_file("Publikationstyper_mod.csv").decode("utf-8").splitlines()
    reader = csv.DictReader(lines, delimiter=";")
    field_map = {h.strip(): h for h in reader.fieldnames or []}
    col_raw = next((field_map[k] for k in field_map if k.lower() == "publikationstype"), None)
    col_collapsed = next((field_map[k] for k in field_map if k.lower() == "kollapset"), None)
    if not col_raw or not col_collapsed:
        raise ValueError()
    for row in reader:
        raw = (row.get(col_raw) or "").strip()
        col = (row.get(col_collapsed) or "").strip()
        if not raw:
            continue
        mapping[raw] = col or raw
    return dict(mapping)

@st.cache_data
def load_forfatterpositioner() -> dict:
    return json.loads(read_file("forfatterpositioner.json"))

@st.cache_data
def load_ku_totals() -> dict:
    raw = json.loads(read_file("ku_totals.json"))
    return {int(k): v for k, v in raw.items()}

@st.cache_data
def load_ku_colors() -> dict:
    return json.loads(read_file("ku-farver02.json"))

@st.cache_data  
def load_logo() -> bytes:
    return read_file("KU-logo.png")

@st.cache_data
def load_forfatterantal(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(read_file("forfatterantal.json"))


# ---------------------------------------------------------------------------
# COLOR HELPERS
# ---------------------------------------------------------------------------
_KU_PALETTE_RAW = [
    # Mørke — høj kontrast, bruges først
    "#122947",  # Blå mørk
    "#901a1E",  # Rød mørk
    "#39641c",  # Grøn mørk
    "#0a5963",  # Petroleum mørk
    "#3d3d3d",  # Grå mørk
    "#7d5402",  # Brun mørk (JUR)
    # Mellem — god læsbarhed
    "#ffbd38",  # Gul (adskiller)
    "#4b8325",  # Grøn mellem
    "#c73028",  # Rød mellem
    "#197f8e",  # Petroleum mellem
    "#425570",  # Blå mellem
    "#666666",  # Grå mellem
    # Lyse — bruges sidst, kun ved mange kategorier
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

def build_faculty_colors(ku_farver: dict) -> dict:
    return {
        "TEO": "#3A1A5F",
        "JUR": "#7d5402",
        "HUM": ku_farver["Blaa"]["Moerk"],
        "SCIENCE": ku_farver["Groen"]["Moerk"],
        "SAMF": ku_farver["Petroleum"]["Moerk"],
        "SUND": "#7A131A",
    }

def stillingsgruppe_colors(ku_farver: dict) -> dict:
    return {
        "Særlig stilling": "#D4D4D4",
        "Øvrige VIP (DVIP)": "#BAC7D9",
        "Ph.d.": "#6B84A0",
        "Stillinger u. adjunktniveau":"#425570",
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

def add_alpha(hex_color: str, alpha: float) -> str:
    r, g, b = mcolors.to_rgb(hex_color)
    return f"rgba({int(r*255)}, {int(g*255)}, {int(b*255)}, {alpha})"


# ---------------------------------------------------------------------------
# SIZE SCALING
# ---------------------------------------------------------------------------

def scale_size_log(val: float, max_auth: float, px_min: float = 5, px_max: float = 60) -> float:
    if val <= 0 or max_auth <= 0:
        return px_min
    return px_min + (math.log1p(val) / math.log1p(max_auth)) * (px_max - px_min)


# ---------------------------------------------------------------------------
# PYARROW HELPERS
# ---------------------------------------------------------------------------

def build_table(rows: list, schema_fields: list) -> pa.Table:
    cols = {name: [r.get(name) for r in rows] for name, _ in schema_fields}
    arrays = [pa.array(cols[name], type=dtype) for name, dtype in schema_fields]
    return pa.Table.from_arrays(arrays, names=[name for name, _ in schema_fields])

def rows_to_csv_bytes(rows: list, field_order: list) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=field_order, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k) for k in field_order})
    return buf.getvalue().encode("utf-8")

def rows_to_excel_bytes(rows: list, field_order:list, sheet_name: str = "Data") -> bytes:
    """Return a styled .xlsx file as bytes with KU red header styling."""

    KU_RED   = "901a1E"   # KU primary red (no #)

    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31]

    header_font    = Font(name="Arial", bold=False, color="FFFFFF", size=11)
    header_fill    = PatternFill("solid", start_color=KU_RED, end_color=KU_RED)
    header_align   = Alignment(horizontal="left", vertical="center", wrap_text=False)
    thin_side      = Side(style="thin", color="DDDDDD")
    cell_border    = Border(bottom=thin_side)
    data_font      = Font(name="Arial", size=10)
    data_align     = Alignment(horizontal="left", vertical="center")

    # Header row
    for col_idx, col_name in enumerate(field_order, start = 1):
        cell = ws.cell(row=1, column=col_idx, value=col_name)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = header_align
    
    # Data rows
    for row_idx, row in enumerate(rows, start = 2):
        for col_idx, col_name in enumerate(field_order, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row.get(col_name))
            cell.font = data_font
            cell.alignment = data_align
            cell.border = cell_border

    for col_idx, col_name in enumerate(field_order, start=1):
        max_len = max(
            len(str(col_name)),
            *(len(str(row.get(col_name, "") or "")) for row in rows),
        ) if rows else len(col_name)
        ws.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 3, 50)
    
    ws.freeze_panes = "A2"

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()

# ---------------------------------------------------------------------------
# NODE-LABEL HELPER
# ---------------------------------------------------------------------------

def make_node_label(m: dict) -> str:
    parts = [m[k] for k in ("fac", "inst", "grp") if m.get(k)]
    return " | ".join(parts)

# ---------------------------------------------------------------------------
# GRAPH MERGE FUNCTIONS
# ---------------------------------------------------------------------------

def merge_grp_to_facgrp(nodes: dict, edges: list):
    merged_meta = {}
    old_to_new = {}
    size_acc = {}

    for nid, m in nodes.items():
        if m.get("type") != "grp":
            continue
        fac, grp = m["fac"], m["grp"]
        new_id = f"{fac}|{grp}"
        old_to_new[nid] = new_id
        if new_id not in merged_meta:
            merged_meta[new_id] = {"type": "grp", "fac": fac, "inst": "", "grp": grp, "size": 0, "children": []}
            size_acc[(fac, grp)] = 0
        size_acc[(fac, grp)] += m.get("size", 0)
        merged_meta[new_id]["children"].append(nid)

    for nid, m in nodes.items():
        if m.get("type") == "fac":
            merged_meta[nid] = m

    for (fac, grp), s in size_acc.items():
        merged_meta[f"{fac}|{grp}"]["size"] = s

    edge_acc = {}
    for edge in edges:
        u, v, w = edge[0], edge[1], edge[2]
        if u not in old_to_new or v not in old_to_new:
            continue
        u2, v2 = old_to_new[u], old_to_new[v]
        if u2 == v2:
            continue
        key = tuple(sorted((u2, v2)))
        edge_acc[key] = edge_acc.get(key, 0) + w

    return merged_meta, [(u, v, w) for (u, v), w in edge_acc.items()]

def merge_grp_to_inst(nodes: dict, edges: list):
    merged_meta = {}
    old_to_new = {}

    for nid, m in nodes.items():
        if m.get("type") != "grp":
            continue
        fac, inst = m["fac"], m["inst"]
        new_id = f"INST:{fac}|{inst}"
        old_to_new[nid] = new_id
        if new_id not in merged_meta:
            merged_meta[new_id] = {"type": "inst", "fac": fac, "inst": inst, "grp": "", "size": 0, "children": []}
        merged_meta[new_id]["size"] += int(m.get("size", 0))
        merged_meta[new_id]["children"].append(nid)

    for nid, m in nodes.items():
        t = m.get("type")
        if t == "inst":
            if nid not in merged_meta:
                merged_meta[nid] = dict(m)
                merged_meta[nid].setdefault("children", [])
            old_to_new[nid] = nid
        elif t == "fac":
            merged_meta[nid] = m
            old_to_new[nid] = nid

    edge_acc = {}
    for edge in edges:
        u, v, w = edge[0], edge[1], edge[2]
        if u in old_to_new and v in old_to_new:
            u2, v2 = old_to_new[u], old_to_new[v]
            if u2 != v2:
                key = tuple(sorted((u2, v2)))
                edge_acc[key] = edge_acc.get(key, 0) + w

    return merged_meta, [(u, v, w) for (u, v), w in edge_acc.items()]

def merge_all_to_fac(nodes: dict, edges: list):
    merged_meta = {}
    old_to_new = {}

    for nid, m in nodes.items():
        fac = m["fac"]
        new_id = f"FAC:{fac}"
        old_to_new[nid] = new_id
        if new_id not in merged_meta:
            merged_meta[new_id] = {"type": "fac", "fac": fac, "inst": "", "grp": "", "size": 0, "children": []}
        merged_meta[new_id]["size"] += m.get("size", 0)
        merged_meta[new_id]["children"].append(nid)

    edge_acc = {}
    for edge in edges:
        u, v, w = edge[0], edge[1], edge[2]
        if u not in old_to_new or v not in old_to_new:
            continue
        u2, v2 = old_to_new[u], old_to_new[v]
        if u2 == v2:
            continue
        key = tuple(sorted((u2, v2)))
        edge_acc[key] = edge_acc.get(key, 0) + w

    return merged_meta, [(u, v, w) for (u, v), w in edge_acc.items()]

def merge_fac_by_sex(nodes: dict, edges: list):
    """Split each faculty into two nodes - one per sex - giving mode FS."""
    merged_meta = {}
    old_to_new  = {}

    for nid, m in nodes.items():
        if m.get("type") != "grp":
            continue
        fac = m["fac"]
        sex = m.get("sex", "m")
        new_id = f"FAC:{fac}|{sex}"
        old_to_new[nid] = new_id

        if new_id not in merged_meta:
            merged_meta[new_id] = {
                "type":     "fac_sex",
                "fac":      fac,
                "sex":      sex,
                "inst":     "",
                "grp":      "",
                "size":     0,
                "children": [],
            }
        merged_meta[new_id]["size"]     += m.get("size", 0)
        merged_meta[new_id]["children"].append(nid)

    edge_acc = {}
    for edge in edges:
        u_raw, v_raw, w = edge[0], edge[1], edge[2]
        sex_combo = edge[3] if len(edge) > 3 else None

        if u_raw not in old_to_new or v_raw not in old_to_new:
            continue
        u2, v2 = old_to_new[u_raw], old_to_new[v_raw]
        if u2 == v2:
            continue

        key = tuple(sorted((u2, v2)))
        if key not in edge_acc:
            edge_acc[key] = {"weight": 0, "sex_combo": sex_combo}
        edge_acc[key]["weight"] += w

    merged_edges = [
        (u, v, acc["weight"], acc["sex_combo"])
        for (u, v), acc in edge_acc.items()
    ]
    return merged_meta, merged_edges

def merge_inst_by_sex(nodes: dict, edges: list):
    """Split each institute into two nodes - one per sex."""
    merged_meta = {}
    old_to_new  = {}

    for nid, m in nodes.items():
        if m.get("type") != "grp":
            continue
        fac  = m["fac"]
        inst = m["inst"]
        sex  = m.get("sex", "m")
        new_id = f"INST:{fac}|{inst}|{sex}"
        old_to_new[nid] = new_id

        if new_id not in merged_meta:
            merged_meta[new_id] = {
                "type":     "inst_sex",
                "fac":      fac,
                "inst":     inst,
                "sex":      sex,
                "grp":      "",
                "size":     0,
                "children": [],
            }
        merged_meta[new_id]["size"]     += m.get("size", 0)
        merged_meta[new_id]["children"].append(nid)

    edge_acc = {}
    for edge in edges:
        u_raw, v_raw, w = edge[0], edge[1], edge[2]
        sex_combo = edge[3] if len(edge) > 3 else None

        if u_raw not in old_to_new or v_raw not in old_to_new:
            continue
        u2, v2 = old_to_new[u_raw], old_to_new[v_raw]
        if u2 == v2:
            continue

        key = tuple(sorted((u2, v2)))
        if key not in edge_acc:
            edge_acc[key] = {"weight": 0, "sex_combo": sex_combo}
        edge_acc[key]["weight"] += w

    merged_edges = [
        (u, v, acc["weight"], acc["sex_combo"])
        for (u, v), acc in edge_acc.items()
    ]
    return merged_meta, merged_edges

def merge_grp_by_sex(nodes: dict, edges: list):
    """Split each position group into two nodes - one per sex."""
    merged_meta = {}
    old_to_new  = {}

    for nid, m in nodes.items():
        if m.get("type") != "grp":
            continue
        grp = m["grp"]
        sex = m.get("sex", "m")
        new_id = f"GRP:{grp}|{sex}"
        old_to_new[nid] = new_id

        if new_id not in merged_meta:
            merged_meta[new_id] = {
                "type":     "grp_sex",
                "fac":      m.get("fac", ""),
                "inst":     m.get("inst", ""),
                "grp":      grp,
                "sex":      sex,
                "size":     0,
                "children": [],
            }
        merged_meta[new_id]["size"]     += m.get("size", 0)
        merged_meta[new_id]["children"].append(nid)

    edge_acc = {}
    for edge in edges:
        u_raw, v_raw, w = edge[0], edge[1], edge[2]
        sex_combo = edge[3] if len(edge) > 3 else None

        if u_raw not in old_to_new or v_raw not in old_to_new:
            continue
        u2, v2 = old_to_new[u_raw], old_to_new[v_raw]
        if u2 == v2:
            continue

        key = tuple(sorted((u2, v2)))
        if key not in edge_acc:
            edge_acc[key] = {"weight": 0, "sex_combo": sex_combo}
        edge_acc[key]["weight"] += w

    merged_edges = [
        (u, v, acc["weight"], acc["sex_combo"])
        for (u, v), acc in edge_acc.items()
    ]
    return merged_meta, merged_edges

# ---------------------------------------------------------------------------
# APPLY MODE MERGE
# ---------------------------------------------------------------------------

def apply_mode_merge(mode: str, raw_nodes: dict, raw_edges: list):
    """Return (node_meta, edge_source) for the current display mode."""

    sex_active = "S" in mode

    # Sex split at faculty level
    if mode == "FS":
        return merge_fac_by_sex(raw_nodes, raw_edges)

    # Sex split at institute level
    elif mode == "IS":
        return merge_inst_by_sex(raw_nodes, raw_edges)

    # Sex split at group level
    elif mode == "GS":
        return merge_grp_by_sex(raw_nodes, raw_edges)

    elif mode == "F":
        return merge_all_to_fac(raw_nodes, raw_edges)

    elif mode == "FG":
        return merge_grp_to_facgrp(raw_nodes, raw_edges)

    elif mode == "FI":
        return merge_grp_to_inst(raw_nodes, raw_edges)

    elif mode == "FIG":
        nm, es = merge_grp_variants(raw_nodes, raw_edges)
        nm = {nid: m for nid, m in nm.items() if m.get("type") != "inst"}
        return nm, es

    elif mode == "IG":
        nm, merged_edges = merge_grp_variants(raw_nodes, raw_edges)
        node_meta = {}
        for nid, m in nm.items():
            if m.get("type") == "grp":
                node_meta[nid] = m
                fac, inst = m.get("fac", ""), m.get("inst", "")
                inst_id = f"INST:{fac}|{inst}"
                if inst_id not in node_meta:
                    node_meta[inst_id] = {"type": "inst", "fac": fac, "inst": inst, "grp": "", "size": 0}
        return node_meta, merged_edges

    elif mode == "I":
        raw_grp = {nid: m for nid, m in raw_nodes.items() if m.get("type") == "grp"}
        nm, es = merge_grp_to_inst(raw_grp, raw_edges)
        node_meta = {nid: m for nid, m in nm.items() if m.get("type") == "inst"}
        return node_meta, es

    elif mode == "G":
        merged = {}
        sizes = {}
        for nid, m in raw_nodes.items():
            if m["type"] != "grp":
                continue
            grp = m["grp"]
            if grp not in merged:
                merged[grp] = {"type": "grp", "grp": grp, "size": 0}
                sizes[grp] = 0
            sizes[grp] += m["size"]
        for g, s in sizes.items():
            merged[g]["size"] = s

        edge_dict = {}
        for edge in raw_edges:
            u_raw, v_raw, w = edge[0], edge[1], edge[2]
            if u_raw not in raw_nodes or v_raw not in raw_nodes:
                continue
            grp_u = raw_nodes[u_raw]["grp"]
            grp_v = raw_nodes[v_raw]["grp"]
            if grp_u == grp_v:
                continue
            key = tuple(sorted((grp_u, grp_v)))
            edge_dict[key] = edge_dict.get(key, 0) + w

        return merged, [(u, v, w) for (u, v), w in edge_dict.items()]

    return raw_nodes, raw_edges

def merge_grp_variants(nodes: dict, edges: list):
    """Collapse grp nodes that differ only in sex/citizenship into one node."""
    old_to_new = {}
    merged = {}

    for nid, m in nodes.items():
        if m.get("type") != "grp":
            merged[nid] = m
            continue
        new_id = f"{m['fac']}|{m['inst']}|{m['grp']}"
        old_to_new[nid] = new_id
        if new_id not in merged:
            merged[new_id] = {"type": "grp", "fac": m["fac"], "inst": m["inst"],
                               "grp": m["grp"], "size": 0}
        merged[new_id]["size"] += m.get("size", 0)

    edge_acc = {}
    for edge in edges:
        u, v, w = edge[0], edge[1], edge[2]
        sex_combo = edge[3] if len(edge) > 3 else None
        u2 = old_to_new.get(u, u)
        v2 = old_to_new.get(v, v)
        if u2 == v2:
            continue
        key = tuple(sorted((u2, v2)))
        edge_acc[key] = edge_acc.get(key, 0) + w

    return merged, [(u, v, w) for (u, v), w in edge_acc.items()]

# ---------------------------------------------------------------------------
# NODE / EDGE FILTER HELPERS
# ---------------------------------------------------------------------------

def passes_category_filters(m: dict, mode: str, show_fac: bool, show_inst: bool,
                              show_grp: bool, selected_facs: list,
                              selected_insts: list, selected_grps: list) -> bool:
    fac  = m.get("fac")
    inst = m.get("inst")
    grp  = m.get("grp")
    t    = m.get("type")

    if show_fac and selected_facs and fac not in selected_facs:
        return False
    if show_inst and selected_insts and inst not in selected_insts:
        return False
    if mode in ("FI", "IG", "I", "IS"):
        return True
    if t in ("grp", "grp_sex") and show_grp and selected_grps and grp not in selected_grps:
        return False
    return True

def size_relevant_in_mode(m: dict, mode: str) -> bool:
    t = m.get("type", "grp")
    if t == "fac_sex":
        return mode == "FS"
    if t == "fac":
        return mode == "F"
    if t == "inst_sex":
        return mode == "IS"
    if t in ("inst",):
        return mode in ("FI", "IG", "I")
    if t == "grp_sex":
        return mode == "GS"
    if t == "grp":
        return mode in ("G", "FG", "FIG")
    return False

def node_passes_size(m: dict, mode: str, dyn_author_min: int, dyn_author_max: int) -> bool:
    t = m.get("type")
    # Nodes whose size is tracked by the slider - apply the filter
    if size_relevant_in_mode(m, mode):
        return dyn_author_min <= m.get("size", 0) <= dyn_author_max
    # All other node types (e.g. inst placeholders in FI mode, fac nodes
    # that aren't the primary level) pass through unconditionally
    return True

def node_passes_filters(nid: str, m: dict, mode: str, show_fac: bool, show_inst: bool,
                         show_grp: bool, selected_facs: list,
                         selected_insts: list, selected_grps: list) -> bool:
    fac = m.get("fac")
    inst = m.get("inst")
    grp = m.get("grp")
    t   = m.get("type")

    if mode == "IG":
        return True
    if show_fac and selected_facs and fac not in selected_facs:
        return False
    if show_inst and selected_insts and inst not in selected_insts:
        return False
    if t in ("grp", "grp_sex") and show_grp and selected_grps and grp not in selected_grps:
        return False
    if t == "inst" and show_grp and selected_grps and mode not in ("FI", "IG", "IS"):
        if grp and grp not in selected_grps:
            return False
    return True

def pre_nodes_for_mode(node_meta: dict, mode: str) -> set:
    if mode == "FIG":
        return {nid for nid, m in node_meta.items() if m.get("type") != "inst"}
    elif mode == "IG":
        return {nid for nid, m in node_meta.items() if m.get("type") == "grp"}
    elif mode == "FS":
        return {nid for nid, m in node_meta.items() if m.get("type") == "fac_sex"}
    elif mode == "IS":
        return {nid for nid, m in node_meta.items() if m.get("type") == "inst_sex"}
    elif mode == "GS":
        return {nid for nid, m in node_meta.items() if m.get("type") == "grp_sex"}
    return set(node_meta.keys())

def edge_type(u: str, v: str, node_meta: dict, mode: str) -> str:
    """Return 'intra', 'inter', or 'group' based on mode's natural grouping level."""
    if mode in ("G", "GS"):
        return "group"
    if mode in ("I", "IS", "IG"):
        gu = node_meta[u].get("inst", "")
        gv = node_meta[v].get("inst", "")
    else:
        gu = node_meta[u].get("fac", "")
        gv = node_meta[v].get("fac", "")
    return "intra" if (gu and gu == gv) else "inter"

def edge_type_grp(u: str, v: str, node_meta: dict) -> str:
    """Position-group-level intra/inter classification."""
    gu = node_meta[u].get("grp", "")
    gv = node_meta[v].get("grp", "")
    return "intra" if (gu and gu == gv) else "inter"

def edge_type_fac(u: str, v: str, node_meta: dict) -> str:
    """Faculty-level intra/inter classification (always faculty, regardless of mode)."""
    fu = node_meta[u].get("fac", "")
    fv = node_meta[v].get("fac", "")
    return "intra" if (fu and fu == fv) else "inter"

def edge_type_inst(u: str, v: str, node_meta: dict) -> str:
    """Institute-level intra/inter classification."""
    iu = node_meta[u].get("inst", "")
    iv = node_meta[v].get("inst", "")
    return "intra" if (iu and iu == iv) else "inter"

def author_passes_filters(fac_list, inst_list, grp, selected_facs, selected_insts, selected_grps) -> bool:
    if selected_facs and not any(f in selected_facs for f in fac_list):
        return False
    if selected_insts and not any(i in selected_insts for i in inst_list):
        return False
    if selected_grps and grp not in selected_grps:
        return False
    return True

# ---------------------------------------------------------------------------
# LAYOUT
# ---------------------------------------------------------------------------

def compute_layout(nodes_keep: set, node_meta: dict, mode: str) -> dict:
    R_FAC  = 1200
    R_INST = 450
    R_GRP  = 150
    R_G    = 600

    pos = {}

    # --- Faculty centres ---
    fac_centers = {}
    if mode in ("F", "FS", "FI", "IS", "FG", "FIG"):
        faculties = sorted({m["fac"] for m in node_meta.values() if "fac" in m})
        k = max(1, len(faculties))
        for i, fac in enumerate(faculties):
            theta = 2 * pi * i / k
            fac_centers[fac] = (R_FAC * cos(theta), R_FAC * sin(theta))

    # --- Institute centres ---
    inst_centers = {}

    if mode in ("FI", "IS"):
        inst_by_fac = {}
        for m in node_meta.values():
            if m.get("type") in ("inst", "inst_sex"):
                inst_by_fac.setdefault(m["fac"], []).append(m["inst"])
        for fac, insts in inst_by_fac.items():
            cx, cy = fac_centers.get(fac, (0, 0))
            unique_insts = sorted(set(insts))
            k = max(1, len(unique_insts))
            for j, inst in enumerate(unique_insts):
                theta = 2 * pi * j / k
                inst_centers[(fac, inst)] = (cx + R_INST * cos(theta), cy + R_INST * sin(theta))

    elif mode == "I":
        insts = sorted({(m["fac"], m["inst"]) for m in node_meta.values() if m.get("type") == "inst"})
        k = max(1, len(insts))
        for i, (fac, inst) in enumerate(insts):
            theta = 2 * pi * i / k
            inst_centers[(fac, inst)] = (R_FAC * cos(theta), R_FAC * sin(theta))

    elif mode == "FIG":
        insts_by_fac = {}
        for nid, m in node_meta.items():
            if m.get("type") == "grp":
                insts_by_fac.setdefault(m["fac"], []).append(m["inst"])
        for fac, insts in insts_by_fac.items():
            cx, cy = fac_centers.get(fac, (0, 0))
            insts = sorted(set(insts))
            k = max(1, len(insts))
            for j, inst in enumerate(insts):
                theta = 2 * pi * j / k
                inst_centers[(fac, inst)] = (cx + R_INST * cos(theta), cy + R_INST * sin(theta))

    elif mode in ("IG", "I"):
        insts = sorted({(m["fac"], m["inst"]) for m in node_meta.values() if m.get("type") == "inst"})
        k = max(1, len(insts))
        for i, (fac, inst) in enumerate(insts):
            theta = 2 * pi * i / k
            inst_centers[(fac, inst)] = (R_FAC * cos(theta), R_FAC * sin(theta))

    elif mode in ("G", "GS"):
        cluster = sorted(nodes_keep)
        k = max(1, len(cluster))
        for j, nid2 in enumerate(cluster):
            theta = 2 * pi * j / k
            pos[nid2] = (R_G * cos(theta), R_G * sin(theta))
        return pos

    # --- Place every node ---
    for nid in nodes_keep:
        if nid in pos:
            continue
        m = node_meta[nid]
        t = m.get("type")

        if t == "fac_sex":
            cx, cy = fac_centers.get(m["fac"], (0, 0))
            offset = 150 if m.get("sex") == "m" else -150
            pos[nid] = (cx, cy + offset)

        elif t == "inst_sex":
            fac  = m.get("fac")
            inst = m.get("inst")
            sex  = m.get("sex", "m")
            center = inst_centers.get((fac, inst))
            if center is None:
                continue
            cx, cy = center
            # Collect both sex variants for this institute and orbit them
            siblings = sorted(
                nid2 for nid2 in nodes_keep
                if node_meta[nid2].get("type") == "inst_sex"
                and node_meta[nid2].get("fac") == fac
                and node_meta[nid2].get("inst") == inst
            )
            n_sib = max(1, len(siblings))
            j = siblings.index(nid) if nid in siblings else 0
            R_SEX = 120
            theta_sex = pi / 2 + 2 * pi * j / n_sib   # start at top
            pos[nid] = (cx + R_SEX * cos(theta_sex), cy + R_SEX * sin(theta_sex))

        elif t == "grp_sex":
            grp = m.get("grp")
            sex = m.get("sex", "m")
            cluster = sorted(
                nid2 for nid2 in nodes_keep
                if node_meta[nid2].get("type") == "grp_sex"
                and node_meta[nid2].get("grp") == grp
            )
            k = max(1, len(cluster))
            j = cluster.index(nid) if nid in cluster else 0
            theta = 2 * pi * j / k
            pos[nid] = (R_G * cos(theta), R_G * sin(theta))

        elif t == "fac":
            pos[nid] = fac_centers.get(m["fac"], (0, 0))

        elif t == "inst":
            center = inst_centers.get((m["fac"], m["inst"]))
            if center is None:
                continue
            pos[nid] = center

        elif t == "grp":
            fac  = m.get("fac")
            inst = m.get("inst")

            if mode == "FIG":
                center = inst_centers.get((fac, inst))
                if center is None:
                    continue
                cx, cy = center
                cluster = sorted(
                    nid2 for nid2 in nodes_keep
                    if node_meta[nid2].get("type") == "grp"
                    and node_meta[nid2].get("fac") == fac
                    and node_meta[nid2].get("inst") == inst
                )
            elif mode == "FG":
                cx, cy = fac_centers.get(fac, (0, 0))
                cluster = sorted(
                    nid2 for nid2 in nodes_keep
                    if node_meta[nid2].get("type") == "grp"
                    and node_meta[nid2].get("fac") == fac
                )
            elif mode in ("FI", "IG", "I"):
                center = inst_centers.get((fac, inst))
                if center is None:
                    continue
                cx, cy = center
                cluster = sorted(
                    nid2 for nid2 in nodes_keep
                    if node_meta[nid2].get("type") == "grp"
                    and node_meta[nid2].get("fac") == fac
                    and node_meta[nid2].get("inst") == inst
                )
            else:
                cx, cy = (0, 0)
                cluster = [nid]

            k = max(1, len(cluster))
            j = cluster.index(nid) if nid in cluster else 0
            theta = 2 * pi * j / k
            pos[nid] = (cx + R_GRP * cos(theta), cy + R_GRP * sin(theta))

    return pos

# ---------------------------------------------------------------------------
# CENTRALITY HELPERS
# ---------------------------------------------------------------------------

def aggregate_centrality_by(meta_key: str, nodes_list: list, node_meta: dict,
                             weighted_deg: dict, bet_cent: dict):
    acc_wd, acc_bc = {}, {}
    for n in nodes_list:
        meta = node_meta.get(n, {})
        key = meta.get(meta_key, "")
        if not key:
            continue
        acc_wd[key] = acc_wd.get(key, 0.0) + float(weighted_deg.get(n, 0.0))
        acc_bc[key] = acc_bc.get(key, 0.0) + float(bet_cent.get(n, 0.0))
    return (
        sorted(acc_wd.items(), key=lambda x: -x[1]),
        sorted(acc_bc.items(), key=lambda x: -x[1]),
    )

def build_grp_table_by_mode(weighted_deg, bet_cent, node_meta: dict, mode: str):
    wd_map = {grp: float(val) for grp, val in weighted_deg} if weighted_deg else {}
    bc_map = {grp: float(val) for grp, val in bet_cent}     if bet_cent     else {}

    rows = []
    base_fields = None

    for nid, meta in node_meta.items():
        if meta.get("type") not in ("grp", "grp_sex"):
            continue
        grp  = meta.get("grp", "")
        fac  = meta.get("fac", "")
        inst = meta.get("inst", "")
        wd   = wd_map.get(grp, 0.0)
        bc   = bc_map.get(grp, 0.0)

        if mode in ("FIG", "GS"):
            row = {"Stillingsgruppe": grp, "Fakultet": fac, "Institut": inst,
                   "Weighted degree": wd, "Betweenness": bc, "mode": mode}
            base_fields = ["Stillingsgruppe", "Fakultet", "Institut",
                           "Weighted degree", "Betweenness", "mode"]
        elif mode == "IG":
            row = {"Stillingsgruppe": grp, "Institut": inst,
                   "Weighted degree": wd, "Betweenness": bc, "mode": mode}
            base_fields = ["Stillingsgruppe", "Institut",
                           "Weighted degree", "Betweenness", "mode"]
        elif mode == "FG":
            row = {"Stillingsgruppe": grp, "Fakultet": fac,
                   "Weighted degree": wd, "Betweenness": bc, "mode": mode}
            base_fields = ["Stillingsgruppe", "Fakultet",
                           "Weighted degree", "Betweenness", "mode"]
        elif mode in ("G", "GS"):
            row = {"Stillingsgruppe": grp,
                   "Weighted degree": wd, "Betweenness": bc, "mode": mode}
            base_fields = ["Stillingsgruppe", "Weighted degree", "Betweenness", "mode"]
        else:
            continue

        rows.append(row)

    if base_fields is None:
        return [], []

    type_map = {
        "Stillingsgruppe": pa.string(), "Fakultet": pa.string(),
        "Institut": pa.string(), "Weighted degree": pa.float64(),
        "Betweenness": pa.float64(), "mode": pa.string(),
    }
    schema = [(key, type_map[key]) for key in base_fields]
    return rows, schema


# ===========================================================================
# MAIN APP
# ===========================================================================

def intra_inter_labels(mode: str) -> tuple:
    """Return (intra_label, inter_label) suited to the current mode's grouping level."""
    if mode in ("I", "IS", "IG"):
        return "intra-institut", "inter-institut"
    if mode in ("G", "GS"):
        return "intra-gruppe", "inter-gruppe"
    return "intra-fakultet", "inter-fakultet"

def _compute_mod_pre(G2, nodes_keep, node_meta, comm_key):
    try:
        _connected = {n for n in G2.nodes() if G2.degree(n) > 0}
        G_conn = G2.subgraph(_connected).copy()
        comms = {}
        for nid in nodes_keep:
            gl = node_meta.get(nid, {}).get(comm_key, "")
            if gl:
                comms.setdefault(gl, []).append(nid)
        filtered = [[n for n in c if n in _connected] for c in comms.values()]
        filtered = [c for c in filtered if c]
        if not filtered or G_conn.number_of_edges() == 0:
            return float("nan")
        if any(len(c) == 1 for c in filtered):
            return float("nan")
        val = modq(G_conn, filtered, weight="weight")
        return val if val >= 0 else float("nan")
    except Exception:
        return float("nan")

def compute_year_snapshot(content: dict, mode: str,
                           year: int, 
                           raw_nodes_pre: dict,
                           selected_genders: list,
                           selected_gender_edges: list,
                           selected_citizenships: list,
                           selected_facs: list,
                           selected_insts: list,
                           selected_grps: list,
                           show_fac: bool, show_inst: bool, show_grp: bool,
                           sampub_count_raw: int = 0,
                           ku_totals: dict = None) -> dict:
    """Recompute aggregated stats for one year, same filters as main view."""
    raw_nodes = {}
    for nid, meta in content["nodes"]:
        m = dict(meta)
        m.setdefault("type", "grp")
        parts = nid.split("|")
        if "fac" not in m and len(parts) >= 1:
            m["fac"] = parts[0]
        if "inst" not in m and len(parts) >= 2: 
            m["inst"] = parts[1]
        if "grp" not in m and len(parts) >= 3: 
            m["grp"] = parts[2]
        if "sex" not in m and len(parts) >= 4: 
            m["sex"] = parts[3]
        if "statsborgerskab" not in m and len(parts) >= 5:
            m["statsborgerskab"] = parts[4]
        m.setdefault("size", 0)
        raw_nodes[nid] = m

    if selected_citizenships:
        raw_nodes = {
            nid: m for nid, m in raw_nodes.items()
            if m.get("type") != "grp"
            or m.get("statsborgerskab", "") in selected_citizenships
        }

    for nid, m in list(raw_nodes.items()):
        fac  = m.get("fac", "")
        inst = m.get("inst", "")
        if not fac or not inst:
            continue
        inst_id = f"INST:{fac}|{inst}"
        if inst_id not in raw_nodes:
            raw_nodes[inst_id] = {"type": "inst", "fac": fac, "inst": inst, "grp": "", "size": 0}

    raw_edges = [
        (e[0], e[1], e[2], e[3] if len(e) > 3 else None)
        for e in content["edges"]
        if e[0] in raw_nodes and e[1] in raw_nodes
    ]

    node_meta, edge_source = apply_mode_merge(mode, raw_nodes, raw_edges)

    node_meta = {
        nid: m for nid, m in node_meta.items()
        if passes_category_filters(m, mode, show_fac, show_inst, show_grp,
                                   selected_facs, selected_insts, selected_grps)
    }
    if selected_genders and mode in ("FS", "IS", "GS"):
        node_meta = {nid: m for nid, m in node_meta.items() if m.get("sex") in selected_genders}

    edge_source = [e for e in edge_source if e[0] in node_meta and e[1] in node_meta]
    nodes_keep  = pre_nodes_for_mode(node_meta, mode)
    total_authors = sum(node_meta[nid].get("size", 0) for nid in nodes_keep)

    edges_keep = []
    for edge in edge_source:
        u, v, w = edge[0], edge[1], edge[2]
        sex_combo = edge[3] if len(edge) > 3 else None
        if u not in nodes_keep or v not in nodes_keep:
            continue
        if selected_gender_edges and sex_combo and sex_combo not in selected_gender_edges:
            continue
        edges_keep.append((u, v, w, sex_combo))

    if mode not in ("FS", "IS", "GS"):
        connected  = {n for u, v, *_ in edges_keep for n in (u, v)}
        nodes_keep = nodes_keep & connected
    #isolated_nodes = all_nodes_pre_isolation - nodes_keep
    total_authors = sum(node_meta[nid].get("size", 0) for nid in nodes_keep)

    total_pubs = sum(w for _, _, w, *_ in edges_keep)
    intra_pubs = sum(w for u, v, w, *_ in edges_keep
                     if mode not in ("G", "GS") and edge_type(u, v, node_meta, mode) == "intra")
    inter_pubs = sum(w for u, v, w, *_ in edges_keep
                     if mode not in ("G", "GS") and edge_type(u, v, node_meta, mode) == "inter")
    intra_inst_pubs = sum(w for u, v, w, *_ in edges_keep
                          if edge_type_inst(u, v, node_meta) == "intra")
    inter_inst_pubs = sum(w for u, v, w, *_ in edges_keep
                          if edge_type_inst(u, v, node_meta) == "inter")
    intra_grp_pubs = sum(w for u, v, w, *_ in edges_keep
                         if edge_type_grp(u, v, node_meta) == "intra")
    inter_grp_pubs = sum(w for u, v, w, *_ in edges_keep
                         if edge_type_grp(u, v, node_meta) == "inter")

    fac_tot, inst_tot, grp_tot = {}, {}, {}
    for nid in nodes_keep:
        m    = node_meta.get(nid, {})
        size = m.get("size", 0)
        if m.get("fac"):  fac_tot[m["fac"]]  = fac_tot.get(m["fac"],  0) + size
        if m.get("inst"): inst_tot[m["inst"]] = inst_tot.get(m["inst"], 0) + size
        if m.get("grp"):  grp_tot[m["grp"]]  = grp_tot.get(m["grp"],  0) + size
    

    G2 = nx.Graph()
    for u in pre_nodes_for_mode(node_meta, mode): G2.add_node(u)
    for u, v, w, *_ in edges_keep: G2.add_edge(u, v, weight=w)
    density = nx.density(G2)

    if mode in ("F", "I", "G"):
        modularity_pre_snap    = float("nan")
        modularity_greedy_snap = float("nan")
    else:
        #comm_key_snap = "fac" if mode in ("F","FS","FI","FG","FIG") else ("inst" if mode in ("I","IG","IS") else "grp")
        comm_key_snap = (
            "fac"  if mode in ("F", "FS", "FI", "FG", "FIG") else
            "inst" if mode in ("I", "IG", "IS") else
            "grp"
        )
        
        if comm_key_snap is None:
            modularity_pre_snap = float("nan")
            modularity_greedy_snap = float("nan")
        else:
            communities_snap, seen_snap = [], set()
            for nid in nodes_keep:
                gl = node_meta.get(nid, {}).get(comm_key_snap, "")
                if not gl or gl in seen_snap: continue
                members = [n for n in nodes_keep if node_meta.get(n, {}).get(comm_key_snap) == gl]
                if members:
                    communities_snap.append(members)
                    seen_snap.add(gl)
            try:
                _connected_snap = {n for n in G2.nodes() if G2.degree(n) > 0}
                _G2_conn_snap = G2.subgraph(_connected_snap).copy()
                _communities_snap_filtered = [
                    [n for n in c if n in _connected_snap] for c in communities_snap
                ]
                _communities_snap_filtered = [c for c in _communities_snap_filtered if c]
                has_singleton_pre = any(len(c) == 1 for c in _communities_snap_filtered)
                if not has_singleton_pre and _communities_snap_filtered and _G2_conn_snap.number_of_edges() > 0:
                    _mod_val = modq(_G2_conn_snap, _communities_snap_filtered, weight="weight")
                    modularity_pre_snap = _mod_val if _mod_val >= 0 else float("nan")
                else:
                    modularity_pre_snap = float("nan")
            except Exception:
                modularity_pre_snap = float("nan")
            try:
                if G2.number_of_edges() > 0 and G2.number_of_nodes() > 1:
                    _gc_full = list(greedy_modularity_communities(G2, weight="weight"))
                    has_singleton = any(len(c) == 1 for c in _gc_full)
                    modularity_greedy_snap = float("nan") if has_singleton else modq(G2, _gc_full, weight="weight")
                else:
                    modularity_greedy_snap = float("nan")
            except Exception:
                modularity_greedy_snap = float("nan")

    fac_ew, inst_ew, grp_ew = {}, {}, {}
    for u, v, w, *_ in edges_keep:
        for n in (u, v):
            m = node_meta.get(n, {})
            half = w / 2
            if m.get("fac"):  fac_ew[m["fac"]]  = fac_ew.get(m["fac"],  0.0) + half
            if m.get("inst"): inst_ew[m["inst"]] = inst_ew.get(m["inst"], 0.0) + half
            if m.get("grp"):  grp_ew[m["grp"]]  = grp_ew.get(m["grp"],  0.0) + half
    
    # Sex breakdowns
    sex_bidrag: dict[str, int] = {}
    for nid in nodes_keep:
        m = node_meta.get(nid, {})
        sex = m.get("sex", "")
        if sex:
            sex_bidrag[sex] = sex_bidrag.get(sex, 0) + m.get("size", 0)
    
    # Nationality breakdown
    nat_bidrag: dict[str, int] = {}
    for nid, m in raw_nodes.items():
        if m.get("type") != "grp":
            continue
        cs = m.get("statsborgerskab", "")
        if cs:
            nat_bidrag[cs] = nat_bidrag.get(cs, 0) + m.get("size", 0)
    
    combo_pubs: dict[str, int] = {}
    for u, v, w, sex_combo in edges_keep:
        if sex_combo:
            combo_pubs[sex_combo] = combo_pubs.get(sex_combo, 0) + int(round(w))

    # Top co-publication pairs
    top_pairs: dict[str, float] = {}
    for u, v, w, *_ in edges_keep:
        mu = node_meta.get(u, {})
        mv = node_meta.get(v, {})
        label_u = " | ".join(p for p in (mu.get("fac",""), mu.get("inst",""), mu.get("grp","")) if p)
        label_v = " | ".join(p for p in (mv.get("fac",""), mv.get("inst",""), mv.get("grp","")) if p)
        pair_key = " ↔ ".join(sorted([label_u, label_v]))
        top_pairs[pair_key] = top_pairs.get(pair_key, 0.0) + w

    # Isolated units (nodes with no edges)
    all_unit_nodes = pre_nodes_for_mode(node_meta, mode)
    connected_nodes = {n for u, v, *_ in edges_keep for n in (u, v)}
    isolated_units: list[str] = []
    for nid in all_unit_nodes - connected_nodes:
        m = node_meta.get(nid, {})
        parts = [p for p in (m.get("fac",""), m.get("inst",""), m.get("grp","")) if p]
        if parts:
            isolated_units.append(" | ".join(parts))

    _ku = (ku_totals or {}).get(year, {})
    if mode == "F" and selected_facs:
        ku_pubs    = sum(_ku.get("by_fac", {}).get(f, {}).get("pubs",    0) for f in selected_facs)
        ku_authors = sum(_ku.get("by_fac", {}).get(f, {}).get("authors", 0) for f in selected_facs)
    elif mode == "I" and selected_insts:
        ku_pubs    = sum(_ku.get("by_inst", {}).get(i, {}).get("pubs",    0) for i in selected_insts)
        ku_authors = sum(_ku.get("by_inst", {}).get(i, {}).get("authors", 0) for i in selected_insts)
    elif mode in ("G", "IG") and selected_grps:
        ku_pubs    = sum(_ku.get("by_grp", {}).get(g, {}).get("pubs",    0) for g in selected_grps)
        ku_authors = sum(_ku.get("by_grp", {}).get(g, {}).get("authors", 0) for g in selected_grps)
    else:  # S, N, eller ingen filter valgt
        ku_pubs    = _ku.get("total_pubs",    0)
        ku_authors = _ku.get("total_authors", 0)
    
    # Per-enhed intra/inter (Mulighed A: kanter hvor mindst én node tilhører enheden)
    fac_intra_ew: dict[str, float] = {}
    fac_inter_ew: dict[str, float] = {}
    for u, v, w, *_ in edges_keep:
        fu = node_meta.get(u, {}).get("fac", "")
        fv = node_meta.get(v, {}).get("fac", "")
        et = "intra" if (fu and fu == fv) else "inter"
        for f in {fu, fv}:
            if not f: continue
            if et == "intra":
                fac_intra_ew[f] = fac_intra_ew.get(f, 0.0) + w / 2
            else:
                fac_inter_ew[f] = fac_inter_ew.get(f, 0.0) + w / 2

    inst_intra_ew: dict[str, float] = {}
    inst_inter_ew: dict[str, float] = {}
    for u, v, w, *_ in edges_keep:
        iu = node_meta.get(u, {}).get("inst", "")
        iv = node_meta.get(v, {}).get("inst", "")
        et = "intra" if (iu and iu == iv) else "inter"
        for i in {iu, iv}:
            if not i: continue
            if et == "intra":
                inst_intra_ew[i] = inst_intra_ew.get(i, 0.0) + w / 2
            else:
                inst_inter_ew[i] = inst_inter_ew.get(i, 0.0) + w / 2

    grp_intra_ew: dict[str, float] = {}
    grp_inter_ew: dict[str, float] = {}
    # Krydstabuleret: fac → inst → vægt
    fac_inst_intra_ew: dict[str, dict[str, float]] = {}
    fac_inst_inter_ew: dict[str, dict[str, float]] = {}
    # Krydstabuleret: fac → grp → vægt, inst → grp → vægt
    fac_grp_intra_ew: dict[str, dict[str, float]] = {}
    # Krydstabuleret: fac → grp → vægt, inst → grp → vægt
    fac_grp_intra_ew: dict[str, dict[str, float]] = {}
    fac_grp_inter_ew: dict[str, dict[str, float]] = {}
    inst_grp_intra_ew: dict[str, dict[str, float]] = {}
    inst_grp_inter_ew: dict[str, dict[str, float]] = {}
    for u, v, w, *_ in edges_keep:
        gu = node_meta.get(u, {}).get("grp", "")
        gv = node_meta.get(v, {}).get("grp", "")
        fu = node_meta.get(u, {}).get("fac", "")
        fv = node_meta.get(v, {}).get("fac", "")
        iu = node_meta.get(u, {}).get("inst", "")
        iv = node_meta.get(v, {}).get("inst", "")
        # fac → inst krydstabulering
        et_inst = "intra" if (iu and iu == iv) else "inter"
        for inst, fac in [(iu, fu), (iv, fv)]:
            if inst and fac:
                d_fi = fac_inst_intra_ew if et_inst == "intra" else fac_inst_inter_ew
                d_fi.setdefault(fac, {})[inst] = d_fi.setdefault(fac, {}).get(inst, 0.0) + w / 2
        et_grp = "intra" if (gu and gu == gv) else "inter"
        for g, f, i in [(gu, fu, iu), (gv, fv, iv)]:
            if not g: continue
            d_grp = grp_intra_ew if et_grp == "intra" else grp_inter_ew
            d_grp[g] = d_grp.get(g, 0.0) + w / 2
            if f:
                d_fg = fac_grp_intra_ew if et_grp == "intra" else fac_grp_inter_ew
                d_fg.setdefault(f, {})[g] = d_fg.setdefault(f, {}).get(g, 0.0) + w / 2
            if i:
                d_ig = inst_grp_intra_ew if et_grp == "intra" else inst_grp_inter_ew
                d_ig.setdefault(i, {})[g] = d_ig.setdefault(i, {}).get(g, 0.0) + w / 2

    return {
        "total_pubs":        total_pubs,
        "total_authors":     total_authors,
        "intra_pubs":        intra_pubs,
        "inter_pubs":        inter_pubs,
        "intra_inst_pubs":   intra_inst_pubs,
        "inter_inst_pubs":   inter_inst_pubs,
        "intra_grp_pubs":    intra_grp_pubs,
        "inter_grp_pubs":    inter_grp_pubs,
        "fac_tot":           fac_tot,
        "inst_tot":          inst_tot,
        "grp_tot":           grp_tot,
        "fac_ew":            fac_ew,
        "inst_ew":           inst_ew,
        "grp_ew":            grp_ew,
        "density":           density,
        "modularity_pre":    modularity_pre_snap,
        "modularity_pre_fac":  _compute_mod_pre(G2, nodes_keep, node_meta, "fac"),
        "modularity_pre_inst": _compute_mod_pre(G2, nodes_keep, node_meta, "inst"),
        "modularity_pre_grp":  _compute_mod_pre(G2, nodes_keep, node_meta, "grp"),
        "modularity_greedy": modularity_greedy_snap,
        "sex_bidrag":        sex_bidrag,
        "combo_pubs":        combo_pubs,
        "top_pairs":         top_pairs,
        "isolated_units":    isolated_units,
        "ku_total_pubs":    ku_pubs,
        "ku_total_authors": ku_authors,
        "nat_bidrag": nat_bidrag,
        "sampub_count_raw": sampub_count_raw,
        "sampub_rate": round(total_pubs / total_authors, 4) if total_authors else 0.0,
        "fac_intra_ew":      fac_intra_ew,
        "fac_inter_ew":      fac_inter_ew,
        "inst_intra_ew":     inst_intra_ew,
        "inst_inter_ew":     inst_inter_ew,
        "grp_intra_ew":      grp_intra_ew,
        "grp_inter_ew":      grp_inter_ew,
        "fac_inst_intra_ew": fac_inst_intra_ew,
        "fac_inst_inter_ew": fac_inst_inter_ew,
        "fac_grp_intra_ew":  fac_grp_intra_ew,
        "fac_grp_inter_ew":  fac_grp_inter_ew,
        "inst_grp_intra_ew": inst_grp_intra_ew,
        "inst_grp_inter_ew": inst_grp_inter_ew,
    }



def main():
    # -----------------------------------------------------------------------
    # Load data 
    # -----------------------------------------------------------------------
    data_by_year_CURIS = load_network_data(str(JSON_PATH))

    data_by_year_OpenAlex = {
        2021: {
            "nodes": [
                # --- TEO ---
                ("TEO|Bibel|Professor|m|DK",      {"fac": "TEO",     "inst": "Bibel",     "grp": "Professor", "size": 55, "sex": "m", "statsborgerskab": "DK"}),
                ("TEO|Bibel|Professor|k|DK",      {"fac": "TEO",     "inst": "Bibel",     "grp": "Professor", "size": 63, "sex": "k", "statsborgerskab": "DK"}),
                ("TEO|Bibel|Lektor|k|DK",         {"fac": "TEO",     "inst": "Bibel",     "grp": "Lektor",    "size": 30, "sex": "k", "statsborgerskab": "DK"}),
                ("TEO|Bibel|Adjunkt|k|D",         {"fac": "TEO",     "inst": "Bibel",     "grp": "Adjunkt",   "size": 20, "sex": "k", "statsborgerskab": "D"}),
                ("TEO|Bibel|Ph.d.|m|DK",          {"fac": "TEO",     "inst": "Bibel",     "grp": "Ph.d.",     "size": 15, "sex": "m", "statsborgerskab": "DK"}),
                ("TEO|Oldgræsk|Professor|m|DK",   {"fac": "TEO",     "inst": "Oldgræsk",  "grp": "Professor", "size": 38, "sex": "m", "statsborgerskab": "DK"}),
                ("TEO|Oldgræsk|Lektor|k|UK",      {"fac": "TEO",     "inst": "Oldgræsk",  "grp": "Lektor",    "size": 29, "sex": "k", "statsborgerskab": "UK"}),
                ("TEO|Oldgræsk|Adjunkt|m|DK",     {"fac": "TEO",     "inst": "Oldgræsk",  "grp": "Adjunkt",   "size": 24, "sex": "m", "statsborgerskab": "DK"}),
                ("TEO|Oldgræsk|Ph.d.|k|D",        {"fac": "TEO",     "inst": "Oldgræsk",  "grp": "Ph.d.",     "size": 16, "sex": "k", "statsborgerskab": "D"}),
                # --- HUM ---
                ("HUM|Engelsk|Professor|k|UK",    {"fac": "HUM",     "inst": "Engelsk",   "grp": "Professor", "size": 44, "sex": "k", "statsborgerskab": "UK"}),
                ("HUM|Engelsk|Lektor|m|DK",       {"fac": "HUM",     "inst": "Engelsk",   "grp": "Lektor",    "size": 25, "sex": "m", "statsborgerskab": "DK"}),
                ("HUM|Engelsk|Ph.d.|k|DK",        {"fac": "HUM",     "inst": "Engelsk",   "grp": "Ph.d.",     "size": 19, "sex": "k", "statsborgerskab": "DK"}),
                ("HUM|Engelsk|Adjunkt|m|UK",      {"fac": "HUM",     "inst": "Engelsk",   "grp": "Adjunkt",   "size": 13, "sex": "m", "statsborgerskab": "UK"}),
                ("HUM|Historie|Professor|m|DK",   {"fac": "HUM",     "inst": "Historie",  "grp": "Professor", "size": 50, "sex": "m", "statsborgerskab": "DK"}),
                ("HUM|Historie|Lektor|k|DK",      {"fac": "HUM",     "inst": "Historie",  "grp": "Lektor",    "size": 27, "sex": "k", "statsborgerskab": "DK"}),
                ("HUM|Historie|Ph.d.|m|D",        {"fac": "HUM",     "inst": "Historie",  "grp": "Ph.d.",     "size": 17, "sex": "m", "statsborgerskab": "D"}),
                ("HUM|Historie|Adjunkt|k|DK",     {"fac": "HUM",     "inst": "Historie",  "grp": "Adjunkt",   "size": 14, "sex": "k", "statsborgerskab": "DK"}),
                # --- SCIENCE ---
                ("SCIENCE|Matematik|Professor|m|DK", {"fac": "SCIENCE", "inst": "Matematik", "grp": "Professor", "size": 60, "sex": "m", "statsborgerskab": "DK"}),
                ("SCIENCE|Matematik|Lektor|m|DK",    {"fac": "SCIENCE", "inst": "Matematik", "grp": "Lektor",    "size": 45, "sex": "m", "statsborgerskab": "DK"}),
                ("SCIENCE|Matematik|Postdoc|k|D",    {"fac": "SCIENCE", "inst": "Matematik", "grp": "Postdoc",   "size": 28, "sex": "k", "statsborgerskab": "D"}),
                ("SCIENCE|Matematik|Ph.d.|k|UK",     {"fac": "SCIENCE", "inst": "Matematik", "grp": "Ph.d.",     "size": 22, "sex": "k", "statsborgerskab": "UK"}),
                ("SCIENCE|Kemi|Professor|k|DK",      {"fac": "SCIENCE", "inst": "Kemi",      "grp": "Professor", "size": 67, "sex": "k", "statsborgerskab": "DK"}),
                ("SCIENCE|Kemi|Lektor|m|D",          {"fac": "SCIENCE", "inst": "Kemi",      "grp": "Lektor",    "size": 42, "sex": "m", "statsborgerskab": "D"}),
                ("SCIENCE|Kemi|Postdoc|k|DK",        {"fac": "SCIENCE", "inst": "Kemi",      "grp": "Postdoc",   "size": 24, "sex": "k", "statsborgerskab": "DK"}),
                ("SCIENCE|Kemi|Ph.d.|m|UK",          {"fac": "SCIENCE", "inst": "Kemi",      "grp": "Ph.d.",     "size": 20, "sex": "m", "statsborgerskab": "UK"}),
            ],
            "edges": [
                ("TEO|Oldgræsk|Lektor|k|UK",         "TEO|Bibel|Adjunkt|k|D",             3, "k-k"),
                ("TEO|Oldgræsk|Professor|m|DK",      "TEO|Oldgræsk|Lektor|k|UK",          5, "k-m"),
                ("TEO|Oldgræsk|Lektor|k|UK",         "TEO|Oldgræsk|Ph.d.|k|D",            2, "k-k"),
                ("HUM|Engelsk|Professor|k|UK",       "HUM|Engelsk|Lektor|m|DK",           5, "k-m"),
                ("HUM|Engelsk|Lektor|m|DK",          "HUM|Engelsk|Ph.d.|k|DK",            2, "k-m"),
                ("HUM|Engelsk|Professor|k|UK",       "HUM|Historie|Lektor|k|DK",          3, "k-k"),
                ("HUM|Historie|Ph.d.|m|D",           "HUM|Historie|Adjunkt|k|DK",         3, "k-m"),
                ("SCIENCE|Matematik|Professor|m|DK", "SCIENCE|Matematik|Lektor|m|DK",     7, "m-m"),
                ("SCIENCE|Matematik|Ph.d.|k|UK",     "SCIENCE|Matematik|Postdoc|k|D",     3, "k-k"),
                ("SCIENCE|Matematik|Professor|m|DK", "SCIENCE|Kemi|Lektor|m|D",           6, "m-m"),
                ("SCIENCE|Kemi|Postdoc|k|DK",        "SCIENCE|Kemi|Ph.d.|m|UK",           3, "k-m"),
                ("TEO|Bibel|Professor|k|DK",         "HUM|Engelsk|Ph.d.|k|DK",            1, "k-k"),
                ("TEO|Oldgræsk|Adjunkt|m|DK",        "HUM|Historie|Ph.d.|m|D",            2, "m-m"),
                ("HUM|Engelsk|Lektor|m|DK",          "SCIENCE|Matematik|Postdoc|k|D",     3, "k-m"),
                ("HUM|Historie|Professor|m|DK",      "SCIENCE|Kemi|Professor|k|DK",       5, "k-m"),
                ("TEO|Oldgræsk|Professor|m|DK",      "SCIENCE|Matematik|Professor|m|DK",  2, "m-m"),
            ],
        },
        2022: {
            "nodes": [
                ("TEO|Bibel|Professor|m|DK",         {"fac": "TEO",     "inst": "Bibel",     "grp": "Professor", "size": 50, "sex": "m", "statsborgerskab": "DK", "fwci": 1.3}),
                ("TEO|Bibel|Adjunkt|k|D",            {"fac": "TEO",     "inst": "Bibel",     "grp": "Adjunkt",   "size": 19, "sex": "k", "statsborgerskab": "D",  "fwci": 1.0}),
                ("TEO|Oldgræsk|Lektor|k|UK",         {"fac": "TEO",     "inst": "Oldgræsk",  "grp": "Lektor",    "size": 30, "sex": "k", "statsborgerskab": "UK", "fwci": 1.8}),
                ("HUM|Engelsk|Ph.d.|k|DK",           {"fac": "HUM",     "inst": "Engelsk",   "grp": "Ph.d.",     "size": 31, "sex": "k", "statsborgerskab": "DK", "fwci": 1.0}),
                ("HUM|Historie|Lektor|k|DK",         {"fac": "HUM",     "inst": "Historie",  "grp": "Lektor",    "size": 28, "sex": "k", "statsborgerskab": "DK", "fwci": 1.2}),
                ("SCIENCE|Matematik|Professor|m|DK", {"fac": "SCIENCE", "inst": "Matematik", "grp": "Professor", "size": 65, "sex": "m", "statsborgerskab": "DK", "fwci": 2.6}),
                ("SCIENCE|Kemi|Postdoc|k|DK",        {"fac": "SCIENCE", "inst": "Kemi",      "grp": "Postdoc",   "size": 36, "sex": "k", "statsborgerskab": "DK", "fwci": 2.7}),
            ],
            "edges": [
                ("TEO|Bibel|Professor|m|DK",         "HUM|Engelsk|Ph.d.|k|DK",           2, "k-m"),
                ("TEO|Oldgræsk|Lektor|k|UK",         "SCIENCE|Matematik|Professor|m|DK", 2, "k-m"),
                ("HUM|Historie|Lektor|k|DK",         "SCIENCE|Kemi|Postdoc|k|DK",        3, "k-k"),
                ("TEO|Bibel|Adjunkt|k|D",            "SCIENCE|Kemi|Postdoc|k|DK",        2, "k-k"),
                ("HUM|Engelsk|Ph.d.|k|DK",           "HUM|Historie|Lektor|k|DK",         2, "k-k"),
                ("SCIENCE|Matematik|Professor|m|DK", "SCIENCE|Kemi|Postdoc|k|DK",        4, "k-m"),
            ],
        },
    }

    def get_data_by_year(source: str) -> dict:

        if source == "CURIS":
            return data_by_year_CURIS

        if source == "OpenAlex":
            return data_by_year_OpenAlex

        # --- Merge both sources ---
        merged = {}
        all_years = sorted(set(data_by_year_CURIS) | set(data_by_year_OpenAlex))

        for year in all_years:
            curis  = data_by_year_CURIS.get(year,    {"nodes": [], "edges": []})
            openalex = data_by_year_OpenAlex.get(year, {"nodes": [], "edges": []})

            # Nodes: sum sizes for matching IDs; keep all unique IDs
            node_map = {}
            for nid, meta in curis["nodes"]:
                node_map[nid] = dict(meta)
                node_map[nid]["_sources"] = {"CURIS"}

            for nid, meta in openalex["nodes"]:
                if nid in node_map:
                    node_map[nid]["size"] += meta.get("size", 0)
                    node_map[nid]["_sources"].add("OpenAlex")
                else:
                    node_map[nid] = dict(meta)
                    node_map[nid]["_sources"] = {"OpenAlex"}

            # Edges: sum weights for matching pairs
            edge_map = {}
            for u, v, w, *rest in curis["edges"]:
                key = tuple(sorted((u, v)))
                sex_combo = rest[0] if rest else None
                edge_map[key] = {"w": w, "sex_combo": sex_combo}

            for u, v, w, *rest in openalex["edges"]:
                key = tuple(sorted((u, v)))
                if key in edge_map:
                    edge_map[key]["w"] += w
                else:
                    sex_combo = rest[0] if rest else None
                    edge_map[key] = {"w": w, "sex_combo": sex_combo}

            merged[year] = {
                "nodes": [(nid, meta) for nid, meta in node_map.items()],
                "edges": [(u, v, d["w"], d["sex_combo"]) for (u, v), d in edge_map.items()],
                }

        return merged
    
    ku_farver    = load_ku_colors(str(COLORS_PATH))
    faculty_base_colors = build_faculty_colors(ku_farver)
    grp_colors = stillingsgruppe_colors(ku_farver)

    forfatterpositioner = load_forfatterpositioner(str(FORFATTERPOSITIONER_PATH))
    ku_totals = load_ku_totals(str(KU_TOTALS_PATH))
    _, inst_to_fac = load_inst_filter(str(INST_FILTER_PATH))
    forfatterantal_data    = load_forfatterantal(str(FORFATTERANTAL_JSON))
    publikationstyper_data = load_publikationstyper(str(PUBLIKATIONSTYPER_JSON))

    # -----------------------------------------------------------------------
    # Page config & header
    # -----------------------------------------------------------------------

    st.set_page_config(
        page_title="REKSTAB Analyse",
        page_icon=LOGO_PATH,
        layout="wide",
    )

    col_logo, col_title = st.columns([1, 4])
    with col_logo:
        st.image(LOGO_PATH)
    with col_title:
        st.title("Sampublicering på Københavns Universitet")

    # -----------------------------------------------------------------------
    # Sidebar filters
    # -----------------------------------------------------------------------
    with st.sidebar:
        st.sidebar.header("Hvad skal vises i netværket?")
        
        with st.expander("**Datakilde**"):
            data_source = st.radio(
                "Vælg datakilde",
                options = ["CURIS", "OpenAlex", "Begge"],
                index = 0,
                key = "data_source_radio",
                help = "Vælg datakilden, som skal ligge til grund for analysen"
            )
        
        data_by_year = get_data_by_year(data_source)

    institut_fakultets_map = {}
    for year_content in data_by_year.values():
        for nid, meta in year_content["nodes"]:
            fac  = meta.get("fac")
            inst = meta.get("inst")
            if fac and inst:
                institut_fakultets_map[inst] = fac

    years = list(data_by_year.keys())

    all_faculties = sorted({
        meta["fac"]
        for content in data_by_year.values()
        for _, meta in content["nodes"]
    })
    all_groups = sorted({
        meta["grp"]
        for content in data_by_year.values()
        for _, meta in content["nodes"]
    }, key=lambda g: HIERARKI.get(g, 999))

    # All citizenship codes present across all years - DK sorted first.
    _all_cs_raw = sorted({
        meta.get("statsborgerskab", "")
        for content in data_by_year.values()
        for _, meta in content["nodes"]
        if meta.get("statsborgerskab")
    })
    all_citizenships = (
        (["DK"] if "DK" in _all_cs_raw else []) +
        [c for c in _all_cs_raw if c != "DK"]
    )

    global_sizes      = [meta["size"] for yc in data_by_year.values() for _, meta in yc["nodes"]]
    global_min_auth   = min(global_sizes) if global_sizes else 0
    global_max_auth   = max(global_sizes) if global_sizes else 0

    all_edge_counts   = [int(round(e[2])) for yc in data_by_year.values() for e in yc["edges"]]
    global_min_w      = min(all_edge_counts) if all_edge_counts else 0
    global_max_w      = max(all_edge_counts) if all_edge_counts else 1

    with st.sidebar:
        # --- DIVERSITET expander (no organisation info here) ---
        with st.expander("**Diversitet**"):
            analyse_køn = st.checkbox(
                "**Køn**",
                value = False, 
                key = "cb_køn"
                )

            if analyse_køn:
                selected_genders = st.multiselect(
                    "Vælg køn",
                    options = ["k", "m"],
                    default = ["k", "m"],
                    format_func = lambda x: {"k": "Kvinder", "m": "Mænd"}.get(x, x)
                )

                selected_gender_edges = st.multiselect(
                    "Kanter efter kønskombination",
                    options = ["k-k", "k-m", "m-m"],
                    default = [],
                    format_func=lambda x: {"k-k": "Kvinde–Kvinde", "k-m": "Kvinde–Mand", "m-m": "Mand–Mand"}.get(x, x),
                )
            else:
                selected_genders = []
                selected_gender_edges = []
            
            analyse_nat = st.checkbox(
                "**Statsborgerskab**",
                value = False,
                key = "cb_nat")

            if analyse_nat:
                selected_citizenships = st.multiselect(
                    "Vælg statsborgerskab (tom = alle)",
                    options = all_citizenships,
                    default = []
                )
            else:
                selected_citizenships = []

        sex_active = bool(selected_genders or selected_gender_edges)
        diversity_active = sex_active or bool(selected_citizenships)

        # --- ORGANISATION expander ---
        _org_opts = ["Fakulteter", "Institutter", "Stillingsgrupper"]

        with st.expander("**Organisation**"):
            if analyse_køn or analyse_nat:
                # When any diversity filter is on, only one org level is meaningful
                # so we enforce single-selection with a radio.
                st.caption("Diversitetsfilter aktivt: vælg ét organisationsniveau.")

                # Initialise radio from checkbox state so entering diversity mode
                # reflects what was already checked (e.g. G-only → radio on Stillingsgrupper)
                if "org_radio_index" not in st.session_state:
                    _fac_on  = st.session_state.get("cb_fac",  True)
                    _inst_on = st.session_state.get("cb_inst", False)
                    _grp_on  = st.session_state.get("cb_grp",  True)
                    if _grp_on and not _fac_on and not _inst_on:
                        st.session_state["org_radio_index"] = 2   # Stillingsgrupper
                    elif _inst_on and not _fac_on and not _grp_on:
                        st.session_state["org_radio_index"] = 1   # Institutter
                    else:
                        st.session_state["org_radio_index"] = 0   # Fakulteter (default)

                org_radio = st.radio(
                    "Organisationsniveau",
                    options=_org_opts,
                    index=st.session_state["org_radio_index"],
                    key="org_radio",
                )
                st.session_state["org_radio_index"] = _org_opts.index(org_radio)

                # Write radio selection back into checkbox state so that when
                # diversity is deactivated the checkboxes restore the right values.
                st.session_state["cb_fac"]  = (org_radio == "Fakulteter")
                st.session_state["cb_inst"] = (org_radio == "Institutter")
                st.session_state["cb_grp"]  = (org_radio == "Stillingsgrupper")

                show_fac  = org_radio == "Fakulteter"
                show_inst = org_radio == "Institutter"
                show_grp  = org_radio == "Stillingsgrupper"
            else:
                # Clear the radio index so next time diversity is activated it
                # re-derives from the current checkbox state rather than a stale value.
                if "org_radio_index" in st.session_state:
                    del st.session_state["org_radio_index"]

                # Normal multi-level checkboxes - value= reads from session_state
                # automatically because the keys match (cb_fac / cb_inst / cb_grp).
                show_fac  = st.checkbox("**Fakulteter**",       value=st.session_state.get("cb_fac",  True),  key="cb_fac")
                show_inst = st.checkbox("**Institutter**",      value=st.session_state.get("cb_inst", False), key="cb_inst")
                show_grp  = st.checkbox("**Stillingsgrupper**", value=st.session_state.get("cb_grp",  True),  key="cb_grp")

            _all_insts_by_fac: dict[str, list] = {}
            for yc in data_by_year.values():
                for _, meta in yc["nodes"]:
                    f, i = meta.get("fac", ""), meta.get("inst", "")
                    if f and i and i not in _all_insts_by_fac.setdefault(f, []):
                        _all_insts_by_fac[f].append(i)

            selected_facs = (
                st.multiselect("Vælg fakulteter (tom = alle)", all_faculties, default=[])
                if show_fac else []
            )

            if show_inst:
                _inst_opts_sidebar = sorted({
                    i
                    for f, insts in _all_insts_by_fac.items()
                    for i in insts
                    if not selected_facs or f in selected_facs
                })
                selected_insts = st.multiselect(
                    "Vælg institutter (tom = alle)",
                    options=_inst_opts_sidebar,
                    default=[v for v in st.session_state.get("selected_insts_prev", []) if v in _inst_opts_sidebar],
                    key="selected_insts_ms",
                )
                st.session_state["selected_insts_prev"] = selected_insts
            else:
                selected_insts = []

            selected_grps = (
                st.multiselect("Vælg stillingsgrupper (tom = alle)", all_groups, default=[])
                if show_grp else []
            )

# -----------------------------------------------------------------------
# Main controls
# -----------------------------------------------------------------------
# Derive a preliminary mode just for checkbox labels (full mode comes after)
    _org_levels_pre = [l for l, a in [("F", show_fac), ("I", show_inst), ("G", show_grp)] if a]
    _sex_active_pre = bool(selected_genders or selected_gender_edges)
    if _sex_active_pre and len(_org_levels_pre) == 1:
        _mode_pre = _org_levels_pre[0] + "S"
    else:
        _mode_pre = ("F" if show_fac else "") + ("I" if show_inst else "") + ("G" if show_grp else "")

    _inst_mode = show_fac and show_inst   # modes where both fac + inst are visible (FI, FIG)

    cols = st.columns(2)
    with cols[0]:
        year = st.selectbox("Vælg år: ", list(data_by_year.keys()), index=0)
        _intra_label, _inter_label = intra_inter_labels(_mode_pre)
        show_intra     = st.checkbox(f"Vis {_intra_label}kanter", True, key="chk_intra")
        show_inter     = st.checkbox(f"Vis {_inter_label}kanter", True, key="chk_inter")
        # Institut-level edge filters - only relevant when both F and I are active
        if _inst_mode:
            show_intra_inst = st.checkbox("Vis intra-instituttets kanter", True, key="chk_intra_inst")
            show_inter_inst = st.checkbox("Vis inter-instituttets kanter", True, key="chk_inter_inst")
        else:
            show_intra_inst = True
            show_inter_inst = True
        # Stillingsgruppe-level edge filters
        _grp_mode = show_grp
        if _grp_mode:
            show_intra_grp = st.checkbox("Vis intra-stillingsgruppekanter", True, key="chk_intra_grp")
            show_inter_grp = st.checkbox("Vis inter-stillingsgruppekanter", True, key="chk_inter_grp")
        else:
            show_intra_grp = True
            show_inter_grp = True

    if not show_fac and not show_inst and not show_grp:
        st.error("Vælg mindst ét filter (fakultet, institut eller stillingsgruppe).")
        st.stop()

    # -----------------------------------------------------------------------
    # Determine mode
    # -----------------------------------------------------------------------
    # Sex can now be combined with any single organisation level.
    # Priority when multiple org levels are checked: F > I > G
    # Sex suffix "S" is appended when sex filter is active AND exactly one
    # org level is selected (so the split is meaningful).

    org_levels = [l for l, active in [("F", show_fac), ("I", show_inst), ("G", show_grp)] if active]

    if sex_active and len(org_levels) == 1:
        # Sex split at the single chosen org level
        mode = org_levels[0] + "S"          # FS, IS, or GS
    else:
        # Build composite mode string the original way (F/I/G combinations)
        mode = (
            ("F" if show_fac  else "") +
            ("I" if show_inst else "") +
            ("G" if show_grp  else "")
        )

    _mode_labels = {
        "F":   "fakultetsniveau",
        "FI":  "fakultets- og institutsniveau",
        "FIG": "fakultets-, instituts- og stillingsgruppesniveau",
        "FG":  "fakultets- og stillingsgruppesniveau",
        "I":   "institutsniveau",
        "IG":  "instituts- og stillingsgruppesniveau",
        "G":   "stillingsgruppesniveau",
        "FS":  "fakultetsniveau opdelt på køn",
        "IS":  "institutsniveau opdelt på køn",
        "GS":  "stillingsgruppesniveau opdelt på køn",
    }
    _current_mode_label = _mode_labels.get(mode, mode)

    # -----------------------------------------------------------------------
    # Build raw node metadata for selected year
    # -----------------------------------------------------------------------

    content = data_by_year[year]

    raw_nodes = {}
    for nid, meta in content["nodes"]:
        m = dict(meta)
        m.setdefault("type", "grp")
        parts = nid.split("|")
        if "fac"  not in m and len(parts) >= 1: 
            m["fac"]  = parts[0]
        if "inst" not in m and len(parts) >= 2: 
            m["inst"] = parts[1]
        if "grp"  not in m and len(parts) >= 3: 
            m["grp"]  = parts[2]
        if "sex"  not in m and len(parts) >= 4: 
            m["sex"]  = parts[3]
        if "statsborgerskab" not in m and len(parts) >= 5: 
            m["statsborgerskab"] = parts[4]
        m.setdefault("size", 0)
        raw_nodes[nid] = m

    # ---------------------------------------------------------------------------
    # Citizenship filter: applied at raw grp-node level BEFORE any merge.
    # Empty selection = no filter (show all). OR logic for multiple codes.
    # ---------------------------------------------------------------------------
    if selected_citizenships:
        raw_nodes = {
            nid: m for nid, m in raw_nodes.items()
            if m.get("type") != "grp"
            or m.get("statsborgerskab", "") in selected_citizenships
        }
        # Remove edges that reference nodes now absent
        raw_edges_prefilter = [
            e for e in content["edges"]
            if e[0] in raw_nodes and e[1] in raw_nodes
        ]
    else:
        raw_edges_prefilter = list(content["edges"])


    # Add institute placeholder nodes
    for nid, m in list(raw_nodes.items()):
        fac  = m.get("fac", "")
        inst = m.get("inst", "")
        if not fac or not inst:
            continue
        inst_id = f"INST:{fac}|{inst}"
        if inst_id not in raw_nodes:
            raw_nodes[inst_id] = {"type": "inst", "fac": fac, "inst": inst, "grp": "", "size": 0}

    # Normalise edges to 4-tuples
    raw_edges = [
        (e[0], e[1], e[2], e[3] if len(e) > 3 else None)
        for e in raw_edges_prefilter
    ]
    raw_nodes_unfiltered = dict(raw_nodes)   # snapshot after inst placeholders, before mode merge
    raw_edges_unfiltered = list(raw_edges)

    # -----------------------------------------------------------------------
    # Apply mode merge
    # -----------------------------------------------------------------------

    node_meta, edge_source = apply_mode_merge(mode, raw_nodes, raw_edges)

    all_inst = sorted({m.get("inst", "UKENDT") for m in node_meta.values()})

    # -----------------------------------------------------------------------
    # Size slider
    # -----------------------------------------------------------------------

    pre_size_nodes = {
        nid: m for nid, m in node_meta.items()
        if passes_category_filters(m, mode, show_fac, show_inst, show_grp,
                                   selected_facs, selected_insts, selected_grps)
    }

    if not pre_size_nodes:
        st.error("Ingen noder matcher valgte filtre.")
        st.stop()

    candidate_items  = [(nid, int(m.get("size", 0))) for nid, m in pre_size_nodes.items() if size_relevant_in_mode(m, mode)]
    candidate_sizes  = [s for _, s in candidate_items]
    vis_min          = min(candidate_sizes) if candidate_sizes else 0
    vis_max          = max(candidate_sizes) if candidate_sizes else 1
    if vis_min == vis_max:
        vis_max = vis_min + 1 if vis_min > 0 else 1

    author_ver = (mode, tuple(sorted(candidate_items)))
    if st.session_state.get("author_version") != author_ver:
        st.session_state["author_range"]   = (vis_min, vis_max)
        st.session_state["author_version"] = author_ver

    # -----------------------------------------------------------------------
    # Edge slider
    # -----------------------------------------------------------------------

    pre_nodes_keep = pre_nodes_for_mode(node_meta, mode)
    edge_candidates = [
        (e[0], e[1], int(round(e[2])))
        for e in edge_source
        if e[0] in node_meta and e[1] in node_meta and e[0] in pre_nodes_keep and e[1] in pre_nodes_keep
    ]
    cur_min_w = min(w for _, _, w, *_ in edge_candidates) if edge_candidates else 0
    cur_max_w = max(w for _, _, w, *_ in edge_candidates) if edge_candidates else 1

    edge_ver = (mode, year, len(edge_candidates), cur_min_w, cur_max_w)
    if st.session_state.get("edge_version") != edge_ver:
        st.session_state["edge_range"]   = (cur_min_w, cur_max_w)
        st.session_state["edge_version"] = edge_ver

    with cols[1]:
        author_min, author_max = st.slider(
            "Filter: antal unikke forfattere (dynamisk)",
            min_value=vis_min, max_value=vis_max,
            value=st.session_state.get("author_range", (vis_min, vis_max)),
            key="author_minmax_slider",
            help="Noder med færre forfatterbidrag end minimumsværdien skjules. Nyttig til at fjerne meget små enheder fra netværket.",
        )
        st.session_state["author_range"] = (author_min, author_max)

        edge_min, edge_max = st.slider(
            "Filter: antal publikationer (kanter)",
            min_value=int(cur_min_w), max_value=int(cur_max_w),
            value=st.session_state.get("edge_range", (int(cur_min_w), int(cur_max_w))),
            step=1,
            key=f"edge_slider_{year}_{mode}",
            help="Kanter med færre sampubliceringer end minimumsværdien skjules. Sæt minimumsværdien højt for kun at se de stærkeste samarbejder.",
        )
        edge_scale = st.slider("Kantvægt", min_value=1.0, max_value=50.0, value=6.0, step=0.1, key="edge_scale_slider")

    st.session_state["edge_range"] = (edge_min, edge_max)

    # -----------------------------------------------------------------------
    # Apply remaining node filters
    # -----------------------------------------------------------------------

    node_meta = {
        nid: m for nid, m in pre_size_nodes.items()
        if node_passes_size(m, mode, author_min, author_max)
    }
    if not node_meta:
        st.error("Ingen noder matcher de valgte filtre (kategori + størrelse).")
        st.stop()

    node_meta = {
        nid: m for nid, m in node_meta.items()
        if node_passes_filters(nid, m, mode, show_fac, show_inst, show_grp,
                               selected_facs, selected_insts, selected_grps)
    }
    if not node_meta:
        st.error("Ingen noder matcher de valgte filtre.")
        st.stop()

    # Sex filter on nodes - active in FS, IS, GS modes
    if selected_genders and mode in ("FS", "IS", "GS"):
        node_meta = {
            nid: m for nid, m in node_meta.items()
            if m.get("sex") in selected_genders
        }
        if not node_meta:
            st.error("Ingen noder matcher det valgte kønsfilter.")
            st.stop()

    # -----------------------------------------------------------------------
    # Build edges_keep
    # -----------------------------------------------------------------------

    edge_source = [e for e in edge_source if e[0] in node_meta and e[1] in node_meta]

    nodes_keep = pre_nodes_for_mode(node_meta, mode)
    total_authors = sum(node_meta[nid].get("size", 0) for nid in nodes_keep)

    edges_keep = []
    for edge in edge_source:
        u, v, w = edge[0], edge[1], edge[2]
        sex_combo = edge[3] if len(edge) > 3 else None
        if u not in node_meta or v not in node_meta:
            continue
        if u not in nodes_keep or v not in nodes_keep:
            continue
        et = edge_type(u, v, node_meta, mode)
        if et == "intra" and not show_intra:
            continue
        if et == "inter" and not show_inter:
            continue
        # Institute-level filter (only active in FI / FIG modes)
        if _inst_mode:
            et_inst = edge_type_inst(u, v, node_meta)
            if et_inst == "intra" and not show_intra_inst:
                continue
            if et_inst == "inter" and not show_inter_inst:
                continue
        # Stillingsgruppe-level filter
        if _grp_mode:
            et_grp = edge_type_grp(u, v, node_meta)
            if et_grp == "intra" and not show_intra_grp:
                continue
            if et_grp == "inter" and not show_inter_grp:
                continue
        if not (edge_min <= int(round(w)) <= edge_max):
            continue
        # Filter by sex_combo if edges are filtered
        if selected_gender_edges and sex_combo and sex_combo not in selected_gender_edges:
            continue
        edges_keep.append((u, v, w, sex_combo))

    # Remove isolated nodes (skip in sex-split modes so all nodes stay visible)
    all_nodes_pre_isolation = set(nodes_keep)
    connected = {n for u, v, *_ in edges_keep for n in (u, v)}
    isolated_nodes = all_nodes_pre_isolation - connected   # ← altid beregnet
    if mode not in ("FS", "IS", "GS"):
        nodes_keep = nodes_keep & connected   



    # -----------------------------------------------------------------------
    # Layout
    # -----------------------------------------------------------------------

    pos = compute_layout(nodes_keep | isolated_nodes, node_meta, mode)

    # -----------------------------------------------------------------------
    # Build PyVis network
    # -----------------------------------------------------------------------

    net = Network(height="700px", width="100%", directed=False)
    net.toggle_physics(False)

    _node_display: dict = {}

    for nid in nodes_keep:
        meta   = node_meta[nid]
        fac    = meta.get("fac", "")
        inst   = meta.get("inst", "")
        grp    = meta.get("grp", "")
        sex    = meta.get("sex", "")
        size   = meta.get("size", "NA")
        t      = meta.get("type", "grp")

        parts = []
        if mode in ("F", "FS", "FI", "FG", "FIG", "IG", "I") and fac:
            parts.append(fac)
        if mode in ("FI", "FIG", "IG", "I", "IS") and inst:
            parts.append(inst)
        if mode in ("G", "GS", "FG", "FIG", "IG") and grp:
            parts.append(grp)
        if sex and mode in ("FS", "IS", "GS"):
            parts.append(f"({sex})")
        title_text = " | ".join(parts) + f"\n Unikke forfattere: {size}"

        if mode in ("G", "GS"):
            color = grp_colors.get(grp, "#888888")
            label = grp + (f"\n({sex})" if mode == "GS" and sex else "")
        elif mode == "IS":
            label = inst + f"\n({sex})"
            insts_for_fac = sorted({m["inst"] for m in node_meta.values() if m.get("fac") == fac})
            rank   = insts_for_fac.index(inst) if inst in insts_for_fac else 0
            k      = len(insts_for_fac)
            factor = 0.9 + 0.3 * (rank / max(1, k - 1))
            base   = adjust_color(faculty_base_colors.get(fac, "black"), factor)
            color  = adjust_color(base, 1.3, 0.7) if sex == "m" else adjust_color(base, 0.75, 1.1)
        elif mode in ("I", "FI"):
            label = inst
            insts_for_fac = sorted({m["inst"] for m in node_meta.values() if m["fac"] == fac})
            rank   = insts_for_fac.index(inst) if inst in insts_for_fac else 0
            k      = len(insts_for_fac)
            factor = 0.9 + 0.3 * (rank / max(1, k - 1))
            color  = adjust_color(faculty_base_colors.get(fac, "black"), factor)
        elif mode == "FS":
            base  = faculty_base_colors.get(fac, "black")
            color = adjust_color(base, 1.3, 0.7) if sex == "m" else adjust_color(base, 0.75, 1.1)
            label = f"{fac}\n({'m' if sex == 'm' else 'k'})"
        elif mode == "F":
            label = fac
            color = faculty_base_colors.get(fac, "black")
        else:
            base  = faculty_base_colors.get(fac, "black")
            lvl   = HIERARKI.get(grp, LVL_MIN)
            lf    = 1.2 + 1.2 * (1 - (lvl / max(HIERARKI.values())))
            sf    = 0.6 + 0.4 * (1 - (lvl / max(HIERARKI.values())))
            color = adjust_color(base, lf, sf)
            label = grp

        if nid not in pos:
            continue

        x, y    = pos[nid]
        size_px = scale_size_log(meta.get("size", 1), global_max_auth)
        net.add_node(nid, label=label, x=x, y=y, size=size_px,
                     color=color, title=title_text, physics=False, font="30px")
        _node_display[nid] = {"label": label, "color": color, "size_px": size_px, "title": title_text}
    
    for nid in isolated_nodes:
        if nid not in node_meta or nid not in pos:
            continue
        meta  = node_meta[nid]
        fac   = meta.get("fac", "")
        inst  = meta.get("inst", "")
        grp   = meta.get("grp", "")
        sex   = meta.get("sex", "")
        size  = meta.get("size", "NA")

        # Recompute label + color the same way as the main loop
        if mode in ("G", "GS"):
            color = "#888888"
            label = grp + (f"\n({sex})" if mode == "GS" and sex else "")
        elif mode == "IS":
            label = inst + f"\n({sex})"
            insts_for_fac = sorted({m["inst"] for m in node_meta.values() if m.get("fac") == fac})
            rank   = insts_for_fac.index(inst) if inst in insts_for_fac else 0
            k      = len(insts_for_fac)
            factor = 0.9 + 0.3 * (rank / max(1, k - 1))
            base   = adjust_color(faculty_base_colors.get(fac, "black"), factor)
            color  = adjust_color(base, 1.3, 0.7) if sex == "m" else adjust_color(base, 0.75, 1.1)
        elif mode in ("I", "FI"):
            label = inst
            insts_for_fac = sorted({m["inst"] for m in node_meta.values() if m["fac"] == fac})
            rank   = insts_for_fac.index(inst) if inst in insts_for_fac else 0
            k      = len(insts_for_fac)
            factor = 0.9 + 0.3 * (rank / max(1, k - 1))
            color  = adjust_color(faculty_base_colors.get(fac, "black"), factor)
        elif mode == "FS":
            base  = faculty_base_colors.get(fac, "black")
            color = adjust_color(base, 1.3, 0.7) if sex == "m" else adjust_color(base, 0.75, 1.1)
            label = f"{fac}\n({'m' if sex == 'm' else 'k'})"
        elif mode == "F":
            label = fac
            color = faculty_base_colors.get(fac, "black")
        else:
            base  = faculty_base_colors.get(fac, "black")
            lvl   = HIERARKI.get(grp, LVL_MIN)
            lf    = 1.2 + 1.2 * (1 - (lvl / max(HIERARKI.values())))
            sf    = 0.6 + 0.4 * (1 - (lvl / max(HIERARKI.values())))
            color = adjust_color(base, lf, sf)
            label = grp

        parts = []
        if mode in ("F", "FS", "FI", "FG", "FIG", "IG", "I") and fac:
            parts.append(fac)
        if mode in ("FI", "FIG", "IG", "I", "IS") and inst:
            parts.append(inst)
        if mode in ("G", "GS", "FG", "FIG", "IG") and grp:
            parts.append(grp)
        if sex and mode in ("FS", "IS", "GS"):
            parts.append(f"({sex})")
        title_text = " | ".join(parts) + f"\n Unikke forfattere: {size}\n⚠ Ingen kanter i udsnittet"

        x, y    = pos[nid]
        size_px = scale_size_log(meta.get("size", 1), global_max_auth)
        net.add_node(
            nid,
            label=label,
            x=x, y=y,
            size=size_px * 0.7,
            color={
                "background": add_alpha(color, 0.3),
            },
            borderWidth=0,
            borderWidthSelected=3,
            title=title_text,
            physics=False,
            font="24px",
        )

    # Store per-node display info for reuse in ego-netværk tab
    #_node_display: dict = {}   # {nid: {"label": ..., "color": ..., "size_px": ...}}

    # -----------------------------------------------------------------------
    # Add edges to PyVis
    # -----------------------------------------------------------------------

    max_w_cur = max((w for _, _, w, *_ in edges_keep), default=1.0)

    for u, v, w, *_sc in edges_keep:
        width = 6 * edge_scale * (w / max_w_cur)
        if mode in ("G", "GS"):
            col = "gray"
            et  = "group"
        else:
            fu  = node_meta[u].get("fac", "")
            fv  = node_meta[v].get("fac", "")
            et  = "intra" if fu == fv else "inter"
            col = "black" if et == "inter" else adjust_color(faculty_base_colors.get(fu, "#888888"), 0.25)

        net.add_edge(
            u, v, width=width, 
            color={"color": add_alpha(col, 0.25), "highlight": add_alpha(col, 0.85), "hover": col}, 
            title=f"Publikationer: {int(w)} ({et})")

    # -----------------------------------------------------------------------
    # NetworkX graph for centrality
    # -----------------------------------------------------------------------

    G = nx.Graph()
    for nid in nodes_keep:
        G.add_node(nid)
    for u, v, w, *_sc in edges_keep:
        G.add_edge(u, v, weight=w)

    weighted_deg = dict(G.degree(weight="weight"))
    bet_cent     = nx.betweenness_centrality(G, weight="weight", normalized=True)

    grp_nodes  = [n for n, m in node_meta.items() if m.get("type") in ("grp", "grp_sex")]
    inst_nodes = [n for n, m in node_meta.items() if m.get("type") in ("inst", "inst_sex")]
    fac_nodes  = [n for n, m in node_meta.items() if m.get("type") in ("fac", "fac_sex")]

    source_for_fac = fac_nodes or inst_nodes or grp_nodes
    faculty_wd_sorted, faculty_bs_sorted = aggregate_centrality_by(
        "fac", source_for_fac, node_meta, weighted_deg, bet_cent)

    nodes_for_inst = grp_nodes if mode in ("IG", "FIG") else (inst_nodes or grp_nodes)
    inst_wd_sorted, inst_bs_sorted = aggregate_centrality_by(
        "inst", nodes_for_inst, node_meta, weighted_deg, bet_cent)

    grp_wd_sorted, grp_bs_sorted = aggregate_centrality_by(
        "grp", grp_nodes, node_meta, weighted_deg, bet_cent)

    # Node-level compound labels for multi-level modes (FIG, FI, FG, IG …).
    # Label: "Stillingsgruppe @ Institut @ Fakultet" (parts omitted if empty).
    def _compound_label(m: dict) -> str:
        parts = [p for p in (m.get("grp",""), m.get("inst",""), m.get("fac","")) if p]
        return " | ".join(parts) if parts else "ukendt"

    if mode not in ("G", "GS") and grp_nodes:
        _nlabels  = {n: _compound_label(node_meta[n]) for n in grp_nodes}
        _wd_nodes = sorted(
            ((lbl, float(weighted_deg.get(n, 0))) for n, lbl in _nlabels.items()),
            key=lambda x: -x[1],
        )
        _bs_nodes = sorted(
            ((lbl, float(bet_cent.get(n, 0))) for n, lbl in _nlabels.items()),
            key=lambda x: -x[1],
        )
        grp_node_wd_sorted = _wd_nodes
        grp_node_bs_sorted = _bs_nodes
    else:
        grp_node_wd_sorted = []
        grp_node_bs_sorted = []

    fac_strength = {}
    fac_edge_acc = {}

    for u, v, w, *_sc in edges_keep:
        fu = node_meta[u].get("fac")
        fv = node_meta[v].get("fac")
        if not fu or not fv:
            continue
        tu = node_meta[u].get("type")
        tv = node_meta[v].get("type")

        include = (
            (mode == "F" and tu == "fac" and tv == "fac") or
            (mode == "FS" and tu == "fac_sex" and tv == "fac_sex") or
            (mode == "IS" and tu == "inst_sex" and tv == "inst_sex") or
            (mode == "FI" and tu in ("fac", "inst") and tv in ("fac", "inst")) or
            (mode == "FG" and tu == "grp" and tv == "grp") or
            mode == "FIG"
        )
        if include:
            fac_strength[fu] = fac_strength.get(fu, 0.0) + w
            fac_strength[fv] = fac_strength.get(fv, 0.0) + w
            key = tuple(sorted((fu, fv)))
            fac_edge_acc[key] = fac_edge_acc.get(key, 0.0) + w

    faculty_wd_sorted = sorted(fac_strength.items(), key=lambda x: -x[1])

    for (fu, fv), w_sum in fac_edge_acc.items():
        G.add_edge(fu, fv, weight=w_sum)

    for n, m in node_meta.items():
        if m.get("type") in ("fac", "inst", "grp") and m.get("fac"):
            G.add_node(m["fac"])

    fac_bet = nx.betweenness_centrality(G, weight="weight", normalized=True) if G.number_of_nodes() >= 1 else {}
    fac_bs_sorted = sorted(fac_bet.items(), key=lambda x: -x[1])

    # -----------------------------------------------------------------------
    # Size aggregates for tabs
    # -----------------------------------------------------------------------

    fac_sizes, inst_sizes, grp_sizes = {}, {}, {}
    for nid in nodes_keep:
        m    = node_meta[nid]
        size = m.get("size", 0)
        fac  = m.get("fac")
        inst = m.get("inst")
        grp  = m.get("grp")
        t    = m.get("type")
        if t in ("grp", "grp_sex"):
            grp_sizes.setdefault(grp, []).append(size)
        if inst:
            inst_sizes.setdefault(inst, []).append(size)
        if fac:
            fac_sizes.setdefault(fac, []).append(size)

    fac_avg_size  = {f: sum(v) / len(v) for f, v in fac_sizes.items()}
    fac_tot_size  = {f: sum(v)           for f, v in fac_sizes.items()}
    inst_avg_size = {i: sum(v) / len(v) for i, v in inst_sizes.items()}
    inst_tot_size = {i: sum(v)           for i, v in inst_sizes.items()}
    grp_avg_size  = {g: sum(v) / len(v) for g, v in grp_sizes.items()}
    grp_tot_size  = {g: sum(v)           for g, v in grp_sizes.items()}

    # Edge-weight share per org-unit (each edge split evenly between endpoints)
    fac_ew: dict[str, float] = {}
    inst_ew: dict[str, float] = {}
    grp_ew: dict[str, float] = {}
    for u, v, w, *_ in edges_keep:
        for n in (u, v):
            m = node_meta.get(n, {})
            half = w / 2
            if m.get("fac"):  fac_ew[m["fac"]]  = fac_ew.get(m["fac"],  0.0) + half
            if m.get("inst"): inst_ew[m["inst"]] = inst_ew.get(m["inst"], 0.0) + half
            if m.get("grp"):  grp_ew[m["grp"]]  = grp_ew.get(m["grp"],  0.0) + half

    # -----------------------------------------------------------------------
    # Render PyVis
    # -----------------------------------------------------------------------
    def build_click_panel_html(nodes_keep: set, node_meta: dict, edges_keep: list) -> str:
        """Load the panel template and inject node data."""
        _node_info = {}
        for nid in nodes_keep:
            m = node_meta.get(nid, {})
            neighbours_data = []
            for u, v, w, *_ in edges_keep:
                if u == nid or v == nid:
                    partner = v if u == nid else u
                    pm = node_meta.get(partner, {})
                    et = "Intra" if m.get("fac") == pm.get("fac") else "Inter"
                    parts = [p for p in (pm.get("fac",""), pm.get("inst",""), pm.get("grp","")) if p]
                    neighbours_data.append({
                        "label": " | ".join(parts) if parts else partner,
                        "pubs":  int(w),
                        "type":  et,
                    })
            neighbours_data.sort(key=lambda x: -x["pubs"])
            parts = [p for p in (m.get("fac",""), m.get("inst",""), m.get("grp","")) if p]
            _node_info[nid] = {
                "label":      " | ".join(parts) if parts else nid,
                "size":       m.get("size", 0),
                "neighbours": neighbours_data,
            }

        panel_path = Path(__file__).parent / "network_panel.html"
        template   = panel_path.read_text(encoding="utf-8")
        return template.replace(
            "__NODE_INFO_JSON__",
            json.dumps(_node_info, ensure_ascii=False),
        )

    temp_path = os.path.join(tempfile.gettempdir(), f"pyvis_{year}.html")
    net.write_html(temp_path)
    with open(temp_path, "r", encoding="utf-8") as f:
        html = f.read()

    click_panel = build_click_panel_html(nodes_keep, node_meta, edges_keep)
    html = html.replace("</body>", click_panel + "\n</body>")

    with st.expander("Sådan læser du netværket", expanded = False):
        st.markdown(
f"""
**Hvordan læser du diagrammet?** 

Hver cirkel (node) repræsenterer en organisatorisk enhed - på valgte niveau: *{_current_mode_label}*. 
Cirklens størrelse afspejler antallet af unikke forfatterbidrag: jo større cirkel, jo flere bidrag. Farverne 
følger fakultetstilhørsforholdet.

**Hvad viser linjerne?**

En linje (kant) mellem to boder betyder, at de har sampubliceret. Linjetykkelsen afspejler antallet af
sampubliceringer: en tykkere linje er lig med flere fælles publikationer.

**Hvad er intra- og interkanter?**

Mørke linjer er *inter*-{intra_inter_labels(mode)[0].split("-")[1]}kanter; samarbejde på tværs af enheder. 
Lyse linjer er *intra*-kanter; samarbejde inden for samme enhed - det værende samme fakultet, 
institut eller stillingsgruppe, afhængig af valgte filtre.

**Tip:** Klik på en node for at se dens detaljer og nærmeste samarbedspartnere i panelet i højre hjørne.

""")

    st.components.v1.html(html, height=800, scrolling=True)

    # Show active-filter badges so the user knows the view is subsetted
    _cpr = {"m": "Mænd", "k": "Kvinder"}
    _combo_display = {"k-k": "Kvinde–Kvinde", "k-m": "Kvinde–Mand", "m-m": "Mand–Mand"}

    active_filters = []
    if selected_citizenships:
        active_filters.append(f"Statsborgerskab: {', '.join(selected_citizenships)}")
    if selected_genders:
        active_filters.append(f"Køn: {', '.join(_cpr.get(g, g) for g in selected_genders)}")
    if selected_gender_edges:
        active_filters.append(f"Kønskombination: {', '.join(_combo_display.get(c, c) for c in selected_gender_edges)}")



    # -----------------------------------------------------------------------
    # Summary stats
    # -----------------------------------------------------------------------

    total_pubs = sum(w for _, _, w, *_ in edges_keep)
    intra_pubs = sum(w for u, v, w, *_ in edges_keep if mode not in ("G", "GS") and edge_type(u, v, node_meta, mode) == "intra")
    inter_pubs = sum(w for u, v, w, *_ in edges_keep if mode not in ("G", "GS") and edge_type(u, v, node_meta, mode) == "inter")
    intra_grp_pubs = sum(w for u, v, w, *_ in edges_keep if edge_type_grp(u, v, node_meta) == "intra")
    inter_grp_pubs = sum(w for u, v, w, *_ in edges_keep if edge_type_grp(u, v, node_meta) == "inter")

    # -----------------------------------------------------------------------
    # Modularity
    # -----------------------------------------------------------------------

    comm_key = "fac" if mode in ("F", "FS", "FI", "FG", "FIG") else ("inst" if mode in ("I", "IG", "IS") else "grp")
    communities = []
    communities_dict = {}
    for nid in nodes_keep:
        group_label = node_meta[nid].get(comm_key, "")
        if not group_label:
            continue
        if group_label not in communities_dict:
            communities_dict[group_label] = []
        communities_dict[group_label].append(nid)

    communities = list(communities_dict.values())
    seen_groups = set(communities_dict.keys())

    G2 = nx.Graph()
    all_unit_nodes = pre_nodes_for_mode(node_meta, mode)
    for u in all_unit_nodes:
        G2.add_node(u)
    for u, v, w, *_sc in edges_keep:
        G2.add_edge(u, v, weight=w)

    density = nx.density(G2)

    covered = {n for comm in communities for n in comm}
    for nid in G2.nodes():
        if nid not in covered:
            communities.append([nid])
    _connected = {n for n in G2.nodes() if G2.degree(n) > 0}
    G2_connected = G2.subgraph(_connected).copy()
    communities_filtered = [
        [n for n in c if n in _connected] for c in communities
    ]
    communities_filtered = [c for c in communities_filtered if c]
    has_singleton_pre = any(len(c) == 1 for c in communities_filtered)
    modularity_pre = float("nan") if has_singleton_pre else (
        modq(G2_connected, communities_filtered, weight="weight")
        if communities_filtered and G2_connected.number_of_edges() > 0 else float("nan")
    )
    if G2.number_of_edges() > 0 and G2.number_of_nodes() > 1:
        greedy_comms    = list(greedy_modularity_communities(G2, weight="weight"))
        has_singleton = any(len(c) == 1 for c in greedy_comms)
        modularity_greedy = float("nan") if has_singleton else modq(G2, greedy_comms, weight="weight")
        n_comms         = len(greedy_comms)

        greedy_comms_labeled = [
            sorted([
                " | ".join(p for p in (node_meta.get(nid, {}).get("fac", ""),
                                        node_meta.get(nid, {}).get("inst", ""),
                                        node_meta.get(nid, {}).get("grp", "")) if p)
                for nid in comm        
            ])
            for comm in greedy_comms
        ]
    else:
        greedy_comms, modularity_greedy, n_comms = [], float("nan"), 0



    # -----------------------------------------------------------------------
    # Year comparison snapshots (lightweight - no network rendering)
    # -----------------------------------------------------------------------

    all_years_data = {}
    for _yr, _content in data_by_year.items():
        try:
            all_years_data[_yr] = compute_year_snapshot(
                _content, mode, _yr, raw_nodes,
                selected_genders, selected_gender_edges, selected_citizenships,
                selected_facs, selected_insts, selected_grps,
                show_fac, show_inst, show_grp,
                sampub_count_raw=_content.get("sampub_count", 0),
                ku_totals=ku_totals,
            )
        except Exception as e:
            st.warning(f"compute_year_snapshot fejlede for år {_yr}: {e}")
            
    # -----------------------------------------------------------------------
    # TABS
    # -----------------------------------------------------------------------

    # Build tab list dynamically so diversity tabs appear whenever active,
    # regardless of the current org mode.
    _base_tabs = {
        "FS":  ["Oversigt", "Fakulteter", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
        "IS":  ["Oversigt", "Institutter", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
        "GS":  ["Oversigt", "Stillingsgrupper", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
        "F":   ["Oversigt", "Fakulteter", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
        "FI":  ["Oversigt", "Fakulteter", "Institutter", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
        "FIG": ["Oversigt", "Fakulteter", "Institutter", "Stillingsgrupper", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
        "FG":  ["Oversigt", "Fakulteter", "Stillingsgrupper", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
        "IG":  ["Oversigt", "Institutter", "Stillingsgrupper", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
        "I":   ["Oversigt", "Institutter", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
        "G":   ["Oversigt", "Stillingsgrupper", "Nøgleaktører", "Samarbejdsmønstre", "Datagrundlag"],
    }
    tabs_by_mode = {}
    for _m, _tl in _base_tabs.items():
        _extra = []
        if analyse_køn:
            _extra.append("Køn")
        if analyse_nat:
            _extra.append("Nationaliteter")
        # Insert diversity tabs just before Centralitet
        _ins = _tl.index("Nøgleaktører")
        tabs_by_mode[_m] = _tl[:_ins] + _extra + _tl[_ins:]
    tabs_to_show = tabs_by_mode.get(mode, ["Basisstatistik"])
    tabs         = st.tabs(tabs_to_show)
    tabs_dict    = {name: tab for name, tab in zip(tabs_to_show, tabs)}

    with tabs_dict["Oversigt"]:
        render_tab_oversigt(year, mode, edges_keep, total_pubs, intra_pubs, inter_pubs,
                    node_meta, *intra_inter_labels(mode),
                    all_years_data=all_years_data, isolated_nodes=isolated_nodes,
                    total_authors=total_authors,
                    sampub_count=content.get("sampub_count", 0),
                    intra_grp_pubs=intra_grp_pubs, inter_grp_pubs=inter_grp_pubs
                    )

    if "Fakulteter" in tabs_dict:
        with tabs_dict["Fakulteter"]:
            render_tab_fakulteter(year, mode, fac_tot_size, fac_avg_size, edges_keep, node_meta, all_years_data=all_years_data, fac_ew=fac_ew, faculty_base_colors=faculty_base_colors)

    if "Institutter" in tabs_dict:
        with tabs_dict["Institutter"]:
            render_tab_institutter(year, mode, inst_tot_size, inst_avg_size, institut_fakultets_map, edges_keep, node_meta, all_years_data=all_years_data, inst_ew=inst_ew, faculty_base_colors=faculty_base_colors)

    if "Stillingsgrupper" in tabs_dict:
        with tabs_dict["Stillingsgrupper"]:
            render_tab_stillingsgrupper(year, mode, all_groups, grp_tot_size, grp_avg_size,
                            selected_facs, selected_insts, selected_grps, 
                            selected_genders, selected_citizenships,
                            all_years_data=all_years_data, grp_ew=grp_ew,
                            edges_keep=edges_keep, node_meta=node_meta, 
                            forfatterpositioner=forfatterpositioner,
                            inst_to_fac=inst_to_fac)

    if "Nøgleaktører" in tabs_dict:
        with tabs_dict["Nøgleaktører"]:
            render_tab_centralitet(year, mode, faculty_wd_sorted, faculty_bs_sorted,
                                   inst_wd_sorted, inst_bs_sorted,
                                   grp_wd_sorted, grp_bs_sorted, node_meta,
                                   grp_node_wd_sorted, grp_node_bs_sorted,
                                   faculty_base_colors=faculty_base_colors,
                                   grp_colors=grp_colors)

    if "Samarbejdsmønstre" in tabs_dict:
        with tabs_dict["Samarbejdsmønstre"]:
            render_tab_netvaerksstruktur(year, mode, density, modularity_pre, modularity_greedy,
                             n_comms, communities_dict, greedy_comms, comm_key,
                             edges_keep, node_meta, all_years_data=all_years_data)

    if "Køn" in tabs_dict:
        with tabs_dict["Køn"]:
            render_tab_køn(year, mode, raw_nodes, content["edges"], node_meta,
                           selected_facs, selected_insts, selected_grps, all_years_data = all_years_data,
                           edges_keep=edges_keep, forfatterpositioner=forfatterpositioner)

    if "Nationaliteter" in tabs_dict:
        with tabs_dict["Nationaliteter"]:
            render_tab_nationaliteter(year, mode, raw_nodes, raw_edges, node_meta,
                                      selected_facs, selected_insts, selected_grps,
                                      raw_nodes_unfiltered, raw_edges_unfiltered,
                                      all_years_data)

    if "Datagrundlag" in tabs_dict:
        with tabs_dict["Datagrundlag"]:
            render_tab_datagrundlag(year, mode, all_groups, selected_facs, selected_insts, selected_grps,
                                    forfatterantal=forfatterantal_data,
                                    publikationstyper=publikationstyper_data,
                                    faculty_base_colors=faculty_base_colors,
                                    years_sorted=sorted(all_years_data.keys()),
                                    pubtype_map=load_pubtype_map(str(PUBTYPE_CSV_PATH)))

    st.markdown("""
<hr style="margin-top: 50px;">
<div style="text-align:center; color:#666; font-size: 0.9em;">
  REKSTAB Analyse · Amanda Schramm Petersen · <a href="mailto:ascp@adm.ku.dk">ascp@adm.ku.dk</a>
</div>
""", unsafe_allow_html=True)




def _render_share_comparison(org_tot: dict, org_ew: dict, org_label: str, key: str = ""):
    """Scatter + diverging bar: share of forfatterbidrag vs share of edge weight.

    org_tot : {unit: forfatterbidrag_sum}
    org_ew  : {unit: edge_weight_sum}   (already split 50/50 per endpoint)
    """
    units = sorted(set(org_tot) | set(org_ew))
    if not units:
        return

    grand_tot = sum(org_tot.values()) or 1
    grand_ew  = sum(org_ew.values())  or 1

    rows = []
    for u in units:
        pct_tot = 100 * org_tot.get(u, 0) / grand_tot
        pct_ew  = 100 * org_ew.get(u, 0)  / grand_ew
        rows.append({
            "unit":    u,
            "pct_tot": round(pct_tot, 1),
            "pct_ew":  round(pct_ew, 1),
            "diff":    round(pct_ew,1) - round(pct_tot, 1),
        })

    # ── Scatter: forfatterbidrag% (x) vs kant% (y) ───────────────────────────
    #st.subheader(f"{org_label}: andel af forfatterbidrag vs andel af sampubliceringer")
    st.subheader("Andel af forfatterbidrag vs. andel af sampubliceringer")
    st.markdown(
        """
        Diagrammet viser forholdet mellem en enheds andel af alle forfatterbidrag (x-aksen) og enhedens
        andel af den samlede vægt af sampubliceringer (y-aksen). Punkter over diagonalen indikerer enheder, 
        som er relativt mere integrerede i sampubliceringsnetværket end deres størrelse tilsiger. Punkter
        under diagonalen indikerer det modsatte.
        """
    )

    _fig_sc = go.Figure()
    # Diagonal reference line
    _max_axis = max(max(r["pct_tot"] for r in rows), max(r["pct_ew"] for r in rows)) * 1.1
    _fig_sc.add_trace(go.Scatter(
        x=[0, _max_axis], y=[0, _max_axis],
        mode="lines",
        line=dict(color="#aaaaaa", dash="dash", width=1),
        showlegend=False,
        hoverinfo="skip",
    ))
    _fig_sc.add_trace(go.Scatter(
        x=[r["pct_tot"] for r in rows],
        y=[r["pct_ew"]  for r in rows],
        mode="markers+text",
        text=[r["unit"] for r in rows],
        textposition="top center",
        marker=dict(
            size=12,
            color="#122947",
            cmin=-max(abs(r["diff"]) for r in rows),
            cmax= max(abs(r["diff"]) for r in rows),
        ),
        hovertemplate=(
            "<b>%{text}</b><br>"
            "Forfatterbidrag: %{x:.1f}%<br>"
            "Sampubliceringer: %{y:.1f}%<br>"
            "<extra></extra>"
        ),
    ))
    _fig_sc.update_layout(
        xaxis_title=f"Andel af forfatterbidrag (%)",
        yaxis_title=f"Andel af sampubliceringer (%)",
        height=420,
        margin=dict(t=20),
        showlegend=False,
    )
    st.plotly_chart(_fig_sc, width='stretch', key=f"fig_sc_{org_label}_{key}")

    # ── Diverging bar: difference (kant% − bidrag%) ──────────────────────────
    rows_sorted = sorted(rows, key=lambda r: r["diff"])
    _colors = ["#bac7d9" if r["diff"] >= 0 else "#122947" for r in rows_sorted]
    _fig_div = go.Figure(go.Bar(
        y=[r["unit"] for r in rows_sorted],
        x=[r["diff"] for r in rows_sorted],
        orientation="h",
        marker_color=_colors,
        text=[f"{r['diff']:+.1f}pp" for r in rows_sorted],
        textposition="inside",
        hovertemplate="<b>%{y}</b><br>Forskel: %{x:+.1f} pp<extra></extra>",
    ))
    _fig_div.add_vline(x=0, line_width=1, line_color="#666666")
    _fig_div.update_layout(
        xaxis_title="Kant-andel minus bidrag-andel (procentpoint)",
        yaxis=dict(autorange="reversed"),
        height=max(300, 35 * len(rows_sorted)),
        margin=dict(l=140, r=80, t=10),
    )
    st.plotly_chart(_fig_div, width='stretch', key=f"fig_div_{org_label}_{key}")

    # Table
    _tbl_rows = [
        {org_label: r["unit"],
         "Bidrag-andel (%)": r["pct_tot"],
         "Kant-andel (%)":   r["pct_ew"],
         "Forskel (pp)":     r["diff"]}
        for r in sorted(rows, key=lambda r: -abs(r["diff"]))
    ]
    _tbl_schema = [
        (org_label,           pa.string()),
        ("Bidrag-andel (%)",  pa.float64()),
        ("Kant-andel (%)",    pa.float64()),
        ("Forskel (pp)",      pa.float64()),
    ]
    with st.expander("Se tabel"):
        st.dataframe(build_table(_tbl_rows, _tbl_schema), hide_index=True, width="stretch")
        st.download_button(
            "Download (.xlsx)",
            data=rows_to_excel_bytes(_tbl_rows, [n for n, _ in _tbl_schema]),
            file_name=f"andelsfordeling_{org_label.lower()}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_share_comparison_{org_label}_{key}"
            )

def _render_year_comparison(all_years_data: dict, series: list, title: str,
                             yaxis_label: str = "Forfatterbidrag",
                             key_suffix: str = "",
                             colors = None,
                             description: str = None):
    """Render a year-comparison line chart inside a collapsible expander.

    Parameters
    ----------
    all_years_data : dict  {year: snapshot_dict}
    series : list of (label, data_key, sub_key_or_None)
        Each entry describes one line:
        - label: legend name
        - data_key: top-level key in snapshot ("fac_tot", "grp_tot", etc.)
        - sub_key: the specific org-unit name (str) or None for scalar keys
                   like "total_pubs"
    title : str  - expander title
    """
    if not all_years_data or len(all_years_data) < 2:
        return

    years_sorted = sorted(all_years_data.keys())
    st.markdown(f"##### {title}")
    if description:
        st.markdown(f"""{description}""")
    
    ku_colors = ku_color_sequence(len(series))

    fig = go.Figure()
    for i, (label, data_key, sub_key) in enumerate(series):
        y_vals = []
        for yr in years_sorted:
            snap = all_years_data.get(yr, {})
            if sub_key is None:
                y_vals.append(snap.get(data_key, 0))
            else:
                y_vals.append(snap.get(data_key, {}).get(sub_key, 0))
        _color = (colors.get(label) if colors else None) or ku_colors[i]

        fig.add_trace(go.Scatter(
            x=years_sorted, y=y_vals, name=label,
            mode="lines+markers",
            line=dict(width=2, **({"color": _color} if _color else {})),
            marker=dict(size=7, **( {"color": _color} if _color else {})),
        ))
    fig.update_layout(
        yaxis_title=yaxis_label,
        xaxis=dict(tickmode="array", tickvals=years_sorted, dtick=1),
        height=380,
        legend_title="",
        margin=dict(t=20),
    )
    st.plotly_chart(fig, width='stretch')

    # ── Downloadable table ────────────────────────────────────────────────────
    _tbl_rows = []
    for yr in years_sorted:
        snap = all_years_data.get(yr, {})
        row = {"År": yr}
        for label, data_key, sub_key in series:
            val = snap.get(data_key, {}).get(sub_key, 0) if sub_key else snap.get(data_key, 0)
            row[label] = round(val, 1)
        _tbl_rows.append(row)

    _tbl_schema = (
        [("År", pa.int64())] +
        [(label, pa.float64()) for label, _, _ in series]
    )
    with st.expander("Se tabel"):
        st.dataframe(build_table(_tbl_rows, _tbl_schema), hide_index=True, width="stretch")
        _key = hashlib.md5(f"{title}{key_suffix}{','.join(str(s) for s in series)}".encode()).hexdigest()[:10]
        st.download_button(
            "Download (.xlsx)",
            data=rows_to_excel_bytes(_tbl_rows, [n for n, _ in _tbl_schema]),
            file_name=f"year_comparison_{title[:30].replace(' ', '_')}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_yrcmp_{_key}"
        )

# ===========================================================================
# TAB RENDERERS  (unchanged except minor mode checks for new sex modes)
# ===========================================================================

def _render_org_bar(edges_keep: list, node_meta: dict, org_key: str, title_label: str,
                    color_map: dict = None, size_map: dict = None):

    _totals: dict = {}
    for u, v, w, *_ in edges_keep:
        for n in (u, v):
            k = node_meta.get(n, {}).get(org_key, "")
            if k:
                _totals[k] = _totals.get(k, 0) + w / 2

    if not _totals:
        return

    _sorted = sorted(_totals.items(), key=lambda x: -x[1])
    _grand  = sum(v for _, v in _sorted) or 1
    _colors = [color_map.get(k, "#122947") if color_map else "#122947" for k, _ in _sorted]

    _tab_abs, _tab_ratio = st.tabs(["Kantvægt", "Kantvægt per forfatterbidrag"])

    with _tab_abs:
        _fig = go.Figure(go.Bar(
            y=[x[0] for x in _sorted],
            x=[x[1] for x in _sorted],
            orientation="h",
            marker_color=_colors,
            text=[f"{x[1]:,.1f}  ({100*x[1]/_grand:.1f}%)" for x in _sorted],
            textposition="inside",
        ))
        _fig.update_layout(
            xaxis_title="Kantvægt (fordelt)",
            yaxis_title=title_label,
            height=max(350, 35 * len(_sorted)),
            margin=dict(l=160, t=20, r=80),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(_fig, width='stretch')

    with _tab_ratio:
        if not size_map:
            st.caption("Forfatterbidrag ikke tilgængeligt for dette niveau.")
        else:
            _ratio_sorted = sorted(
                [(k, v / size_map[k]) for k, v in _totals.items() if size_map.get(k)],
                key=lambda x: -x[1],
            )
            if not _ratio_sorted:
                st.caption("Ingen data.")
            else:
                _ratio_colors = [color_map.get(k, "#122947") if color_map else "#122947" for k, _ in _ratio_sorted]
                _fig_r = go.Figure(go.Bar(
                    y=[x[0] for x in _ratio_sorted],
                    x=[x[1] for x in _ratio_sorted],
                    orientation="h",
                    marker_color=_ratio_colors,
                    text=[f"{x[1]:.3f}" for x in _ratio_sorted],
                    textposition="inside",
                ))
                _fig_r.update_layout(
                    xaxis_title="Kantvægt per forfatterbidrag",
                    yaxis_title=title_label,
                    height=max(350, 35 * len(_ratio_sorted)),
                    margin=dict(l=160, t=20, r=80),
                    yaxis=dict(autorange="reversed"),
                )
                st.plotly_chart(_fig_r, width='stretch')

    # ── Combined table for both tabs ──────────────────────────────────────────
    _ratio_map = (
        {k: v / size_map[k] for k, v in _totals.items() if size_map and size_map.get(k)}
        if size_map else {}
    )
    _combined_rows = [
        {
            title_label:                       k,
            "Kantvægt (fordelt)":              round(v, 1),
            "Andel (%)":                       round(100 * v / _grand, 1),
            "Forfatterbidrag":                 size_map.get(k, 0) if size_map else None,
            "Kantvægt per forfatterbidrag":    round(_ratio_map[k], 4) if k in _ratio_map else None,
        }
        for k, v in _sorted
    ]
    _combined_schema = [
        (title_label,                       pa.string()),
        ("Kantvægt (fordelt)",              pa.float64()),
        ("Andel (%)",                       pa.float64()),
    ]
    if size_map:
        _combined_schema += [
            ("Forfatterbidrag",                 pa.int64()),
            ("Kantvægt per forfatterbidrag",    pa.float64()),
        ]
    else:
        # Drop the None columns if no size_map
        _combined_rows = [{k: v for k, v in r.items()
                           if k not in ("Forfatterbidrag", "Kantvægt per forfatterbidrag")}
                          for r in _combined_rows]

    _key = hashlib.md5(f"{title_label}_{org_key}".encode()).hexdigest()[:10]
    with st.expander("Se tabel"):
        st.dataframe(build_table(_combined_rows, _combined_schema), hide_index=True, width="stretch")
        st.download_button(
            "Download (.xlsx)",
            data=rows_to_excel_bytes(_combined_rows, [n for n, _ in _combined_schema]),
            file_name=f"kantvaegt_{org_key}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        

def render_tab_oversigt(year, mode, edges_keep, total_pubs, intra_pubs, inter_pubs,
                        node_meta, intra_label="intra-fakultet", inter_label="inter-fakultet",
                        all_years_data=None, isolated_nodes=None, total_authors=0,
                        sampub_count=0, intra_grp_pubs=0, inter_grp_pubs=0):
    _mode_labels = {
        "F":   "fakulteter",
        "FI":  "fakulteter og institutter",
        "FIG": "fakulteter, institutter og stillingsgrupper",
        "FG":  "fakulteter og stillingsgrupper",
        "I":   "institutter",
        "IG":  "institutter og stillingsgrupper",
        "G":   "stillingsgrupper",
        "FS":  "fakultetter opdelt på køn",
        "IS":  "institutter opdelt på køn",
        "GS":  "stillingsgrupper opdelt på køn",
    }

    _inst_sentence = ""
    if mode in ("FI", "FIG"):
        _intra_inst_pubs = sum(w for u, v, w, *_ in edges_keep
                               if edge_type_inst(u, v, node_meta) == "intra")
        _inter_inst_pubs = sum(w for u, v, w, *_ in edges_keep
                               if edge_type_inst(u, v, node_meta) == "inter")
        _inst_sentence = (
f""" På **institut-niveau** er der **{int(_intra_inst_pubs)} intra-institut**- og 
**{int(_inter_inst_pubs)} inter-institut forfatterpar** - svarende til en intra-andel på 
**{round(100*_intra_inst_pubs/total_pubs,1) if total_pubs else 0}%** af alle forfatterpar.
""")

    st.markdown(
        f"""
Dette afsnit giver et samlet overblik over publikationsmængden, antal forfatterpar og de 
overordnede
sampubliceringsmønstre i det valgte udsnit for **{year}**. Tallene afspejler alene summen af
publikationer mellem de noder, der er inkluderede på baggrund af de valgte filtre 
(*{_mode_labels.get(mode, mode)}*).
"""
    )


    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Sampublikationer (ufiltreret)", int(sampub_count),
                  help="Antal publikationer med mindst to VIP-forfattere")
    with col2:
        st.metric("Unikke forfatterbidrag", int(total_authors),
              help="Summen af forfatterbidrag for alle noder i det filtrerede netværk")
    
    with col4:
        _rate = round(total_pubs / total_authors, 2) if total_authors else 0.0
        st.metric("Sampubliceringsrate (forfatterpar / forfatterbidrag)", _rate,
                  help="Antal forfatterpar divideret med antal forfatterbidrag — viser hvor mange sampubliceringsrelationer en forsker i gennemsnit indgår i.")

    if mode in ("FI", "FIG"):
        _intra_inst_m = sum(w for u, v, w, *_ in edges_keep
                            if edge_type_inst(u, v, node_meta) == "intra")
        _inter_inst_m = sum(w for u, v, w, *_ in edges_keep
                            if edge_type_inst(u, v, node_meta) == "inter")
        _pct_inst = round(100 * _intra_inst_m / total_pubs, 1) if total_pubs else 0.0

    # Isolated nodes metric
    if isolated_nodes is not None:
        n_iso = len(isolated_nodes)
        with col3:
            st.metric(
                "Isolerede noder",
                n_iso,
                help="Noder, der ikke har nogen kanter inden for det valgte udsnit og filtre",
            )
        if n_iso > 0:
            with st.expander(f"Se isolerede noder"):
                _iso_rows = []
                for nid in sorted(isolated_nodes):
                    m = node_meta.get(nid, {})
                    _iso_rows.append({
                        "Fakultet":        m.get("fac", ""),
                        "Institut":        m.get("inst", ""),
                        "Stillingsgruppe": m.get("grp", ""),
                        "Forfatterbidrag": m.get("size", 0),
                    })
                _iso_rows.sort(key=lambda r: (-r["Forfatterbidrag"], r["Fakultet"]))
                _iso_schema = [
                    ("Fakultet",        pa.string()),
                    ("Institut",        pa.string()),
                    ("Stillingsgruppe", pa.string()),
                    ("Forfatterbidrag", pa.int64()),
                ]
                st.dataframe(build_table(_iso_rows, _iso_schema), hide_index=True, width="stretch")
                st.download_button(
                    "Download (.xlsx)",
                    data=rows_to_excel_bytes(_iso_rows, [n for n, _ in _iso_schema]),
                    file_name=f"isolerede_noder_{year}_{mode}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

    
    _pub_series = [
        ("Samlet forfatterpar", "total_pubs", None),
        ("Intra-forfatterpar",  "intra_pubs", None),
        ("Inter-forfatterpar",  "inter_pubs", None),
    ]
    if "I" in mode:
        _pub_series += [
            ("Intra-institut",  "intra_inst_pubs", None),
            ("Inter-institut",  "inter_inst_pubs", None),
        ]

    _render_year_comparison(
        all_years_data,
        series=[
            ("Forfatterpar (filtreret)",                          "total_pubs",        None),
            ("Unikke forfatterbidrag i sampublikationer (filtreret)", "total_authors",      None),
            ("Sampublikationer (ufiltreret, alle noder)",             "sampub_count_raw",   None),
            ("Alle KU-publikationer",                                 "ku_total_pubs",      None),
            ("Alle unikke KU-forfattere",                             "ku_total_authors",   None),
            ("Sampubliceringsrate (forfatterpar / forfatterbidrag)",  "sampub_rate",        None),
        ],
        title="Sammenlign år - publikationsantal, forfatterbidrag og forfatterpar",
        description= "**Tip:** For at aflæse sampubliceringsratenraten i figuren, kan der til højre klikkes på de andre grafer, så de fjernes fra figuren. Det kan også bruges til at gøre forskellene mellem *Unikke forfatterbidrag i sampublikationer (filtreret)* og *Alle unikke KU-forfattere* læsbar.",
        yaxis_label="Antal",
    )

    #st.markdown("##### Top sampubliceringer i udsnittet")

    #if not edges_keep:
        #st.error("Ingen sampubliceringer matcher det valgte udsnit.")
        #return

    #max_n     = len(edges_keep)
    #default_n = min(4, max_n)
    #top_n = st.number_input("**Hvor mange sampubliceringer skal vises?**",
                             #min_value=1, max_value=max_n, value=default_n, step=1,
                             #key=f"top_edges_n_{year}_{mode}")

    #top_edges = sorted(edges_keep, key=lambda x: -x[2])[:top_n]
    #top_edge_rows = []
    #for u, v, w, *_ in top_edges:
        #mu, mv = node_meta.get(u, {}), node_meta.get(v, {})
        #et_type = f"{intra_label.capitalize()}" if mu.get("fac") == mv.get("fac") else f"{inter_label.capitalize()}"
        #top_edge_rows.append({
            #"Den ene node": make_node_label(mu),
            #"Den anden node": make_node_label(mv),
            #"Antal forfatterpar": int(w),
            #"Type": et_type,
            #"mode": mode,
        #})

    #schema = [
        #("Den ene node", pa.string()), 
        #("Den anden node", pa.string()),
        #("Antal forfatterpar", pa.int64()), 
        #("Type", pa.string()), 
        #("mode", pa.string())]
    #st.dataframe(build_table(top_edge_rows, schema), width="stretch", hide_index=True)

    #st.download_button(
            #"Download (.xlsx)",
            #data=rows_to_excel_bytes(top_edge_rows, [n for n, _ in schema]),
            #file_name=f"Top_{top_n}_edges_{year}_{mode}.xlsx",
            #mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            #)

    
    # ── 2. Top-10 mest sampublicerende par over tid ───────────────────────────
    if all_years_data and len(all_years_data) >= 2:
        st.markdown("##### Hvilke samarbejder vokser?")
        st.markdown(
            """Nedenstående figur viser de mest sampublicerende par på tværs af alle år. 
            Linjer, der stiger, indikerer voksende samarbejde; faldende linjer indikerer
             aftagende samarbejder.""" 
        )
        years_sorted = sorted(all_years_data.keys())

        # Aggregate total weight per pair across all years to find top-N
        _all_pair_totals: dict[str, float] = {}
        for snap in all_years_data.values():
            for pair, w in snap.get("top_pairs", {}).items():
                _all_pair_totals[pair] = _all_pair_totals.get(pair, 0.0) + w

        top_n_pairs = st.number_input(
            "**Antal par at vise**",
            min_value=1, max_value=min(20, max(len(_all_pair_totals), 1)),
            value=min(4, len(_all_pair_totals)),
            step=1,
            key=f"top_pairs_n_{mode}",
        )
        _top_pairs = sorted(_all_pair_totals.items(), key=lambda x: -x[1])[:top_n_pairs]
        _top_pair_labels = [p for p, _ in _top_pairs]

        colors = ku_color_sequence(len(_top_pair_labels))
        _fig_pairs = go.Figure()
        for i, pair in enumerate(_top_pair_labels):
            _y = [all_years_data[yr].get("top_pairs", {}).get(pair, 0) for yr in years_sorted]
            _fig_pairs.add_trace(go.Scatter(
                x=years_sorted, y=_y,
                name=pair,
                mode="lines+markers",
                line=dict(width=2),
                marker=dict(size=7),
                marker_color = colors[i]
            ))
        _fig_pairs.update_layout(
            yaxis_title="Antal forfatterpar",
            xaxis=dict(tickmode="array", tickvals=years_sorted, dtick=1),
            height=420,
            legend=dict(orientation="h", yanchor="top", y=-0.25, xanchor="left", x=0),
            margin=dict(t=20, b=120),
        )
        st.plotly_chart(_fig_pairs, width='stretch')

        _pair_tbl_rows = [
            {"Par": pair,
             **{str(yr): round(all_years_data[yr].get("top_pairs", {}).get(pair, 0), 1)
                for yr in years_sorted},
             "Total": round(_all_pair_totals.get(pair, 0), 1)}
            for pair in _top_pair_labels
        ]
        _pair_tbl_schema = (
            [("Par", pa.string())] +
            [(str(yr), pa.float64()) for yr in years_sorted] +
            [("Total", pa.float64())]
        )
        with st.expander("Se tabel"):
            st.dataframe(build_table(_pair_tbl_rows, _pair_tbl_schema), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)",
                data=rows_to_excel_bytes(_pair_tbl_rows, [n for n, _ in _pair_tbl_schema]),
                file_name=f"top_par_over_tid_{mode}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


    # ── 1. Intra-andel over tid ───────────────────────────────────────────────
    _pct_intra = round(100 * intra_pubs / total_pubs, 1) if total_pubs else 0.0
    _pct_inter = round(100 * inter_pubs / total_pubs, 1) if total_pubs else 0.0
    _pct_intra_grp = round(100 * intra_grp_pubs / total_pubs, 1) if total_pubs else 0.0

    _grp_modes = {"FG", "FIG", "IG", "G", "GS"}
    _grp_sentence = ""
    if mode in _grp_modes and total_pubs > 0:
        _grp_sentence = (
            f"\n\nPå **stillingsgruppe-niveau** er der **{int(intra_grp_pubs)} intra-stillingsgruppe**- "
            f"og **{int(inter_grp_pubs)} inter-stillingsgruppe forfatterpar** - "
            f"svarende til en intra-andel på **{_pct_intra_grp}%** af alle forfatterpar."
        )

    # Donut: intra vs inter split
    st.markdown(
f"""#### Intra-/interpublikationer
 Intra-andelen viser, hvor stor en del af sampubliceringerne der foregår inden for samme enhed.
 **{intra_label.capitalize()}-publikationer** dækker over samarbejde inden for samme
{intra_label.split('-')[1]} og udgør **{int(intra_pubs)}** forfatterpar
(**{_pct_intra}%** af totalen), mens **{inter_label} forfatterpar** - samarbejde på
tværs af {intra_label.split('-')[1]}er - udgør **{int(inter_pubs)}**
(**{_pct_inter}%**).{_inst_sentence}{_grp_sentence}
""")

    _filter_key = f"{int(intra_pubs)}_{int(inter_pubs)}_{int(total_pubs)}"
    _has_grp_donut = mode in _grp_modes and total_pubs > 0

    if total_pubs > 0 and mode not in ("G", "GS"):
        _show_inst_donut = mode == "FIG"
        _show_grp_donut  = _has_grp_donut

        _n_donuts = 1 + int(_show_inst_donut) + int(_show_grp_donut)

        if _n_donuts == 3:
            _col_donut, _col_donut2, _col_donut_grp = st.columns(3)
        elif _n_donuts == 2:
            _col_donut, _col_donut_grp = st.columns(2)
            _col_donut2 = None
        else:
            _col_donut     = st.columns([1])[0]
            _col_donut2    = None
            _col_donut_grp = None

        _fig_donut = go.Figure(go.Pie(
            labels=[intra_label.capitalize(), inter_label.capitalize()],
            values=[intra_pubs, inter_pubs],
            hole=0.55,
            marker_colors=["#122947", "#bac7d9"],
            textinfo="percent",
            rotation=45,
            sort=False,
            hoverinfo="label+value+percent",
        ))
        _fig_donut.update_layout(
            height=360,
            margin=dict(t=60, b=60, l=60, r=60),
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
        )
        with _col_donut:
            st.plotly_chart(_fig_donut, width='stretch', key=f"donut_{year}_{mode}_{_filter_key}")

        if _show_inst_donut and _col_donut2 is not None:
            _intra_inst = sum(w for u, v, w, *_ in edges_keep
                              if edge_type_inst(u, v, node_meta) == "intra")
            _inter_inst = sum(w for u, v, w, *_ in edges_keep
                              if edge_type_inst(u, v, node_meta) == "inter")
            _fig_donut2 = go.Figure(go.Pie(
                labels=["Intra-institut", "Inter-institut"],
                values=[_intra_inst, _inter_inst],
                hole=0.55,
                marker_colors=["#122947", "#bac7d9"],
                textinfo="percent",
                rotation=45,
                sort=False,
                hoverinfo="label+value+percent",
            ))
            _fig_donut2.update_layout(
                height=360,
                margin=dict(t=60, b=60, l=60, r=60),
                showlegend=True,
                legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
            )
            with _col_donut2:
                st.plotly_chart(_fig_donut2, width='stretch', key=f"donut2_{year}_{mode}_{_filter_key}")

        if _show_grp_donut and _col_donut_grp is not None:
            _fig_donut_grp = go.Figure(go.Pie(
                labels=["Intra-stillingsgruppe", "Inter-stillingsgruppe"],
                values=[intra_grp_pubs, inter_grp_pubs],
                hole=0.55,
                marker_colors=["#39641c", "#becaa8"],
                textinfo="percent",
                rotation=45,
                sort=False,
                hoverinfo="label+value+percent",
            ))
            _fig_donut_grp.update_layout(
                height=360,
                margin=dict(t=60, b=60, l=60, r=60),
                showlegend=True,
                legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
            )
            with _col_donut_grp:
                st.plotly_chart(_fig_donut_grp, width='stretch', key=f"donut_grp_{year}_{mode}_{_filter_key}")

    elif _has_grp_donut and mode in ("G", "GS"):
        _col_donut_g = st.columns([1])[0]
        _fig_donut_grp = go.Figure(go.Pie(
            labels=["Intra-stillingsgruppe", "Inter-stillingsgruppe"],
            values=[intra_grp_pubs, inter_grp_pubs],
            hole=0.55,
            marker_colors=["#39641c", "#becaa8"],
            textinfo="percent",
            rotation=45,
            sort=False,
            hoverinfo="label+value+percent",
        ))
        _fig_donut_grp.update_layout(
            height=360,
            margin=dict(t=60, b=60, l=60, r=60),
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
        )
        with _col_donut_g:
            st.plotly_chart(_fig_donut_grp, width='stretch', key=f"donut_grp_{year}_{mode}_{_filter_key}")
    elif _has_grp_donut and mode in ("G", "GS"):
        _col_donut_g = st.columns([1])[0]
        _fig_donut_grp = go.Figure(go.Pie(
            labels=["Intra-stillingsgruppe", "Inter-stillingsgruppe"],
            values=[intra_grp_pubs, inter_grp_pubs],
            hole=0.55,
            marker_colors=["#39641c", "#becaa8"],
            textinfo="percent",
            rotation=45,
            sort=False,
            hoverinfo="label+value+percent",
        ))
        _fig_donut_grp.update_layout(
            height=360,
            margin=dict(t=60, b=60, l=60, r=60),
            showlegend=True,
            legend=dict(orientation="h", yanchor="bottom", y=-0.2, xanchor="center", x=0.5),
        )
        with _col_donut_g:
            st.plotly_chart(_fig_donut_grp, width='stretch', key=f"donut_grp_{year}_{mode}_{_filter_key}")

    if all_years_data and len(all_years_data) >= 2:
        years_sorted = sorted(all_years_data.keys())

        def _pct(snap, num_key, denom_key="total_pubs"):
            tot = snap.get(denom_key, 0)
            return round(100 * snap.get(num_key, 0) / tot, 1) if tot else 0.0

        if mode in ("FIG", "FG", "FI", "IG", "G", "GS"):
            years_sorted = sorted(all_years_data.keys())

            _intra_fak_pcts  = [_pct(all_years_data[yr], "intra_pubs")      for yr in years_sorted]
            _inter_fak_pcts  = [_pct(all_years_data[yr], "inter_pubs")      for yr in years_sorted]
            _intra_inst_pcts = [_pct(all_years_data[yr], "intra_inst_pubs") for yr in years_sorted]
            _inter_inst_pcts = [_pct(all_years_data[yr], "inter_inst_pubs") for yr in years_sorted]
            _intra_grp_pcts  = [_pct(all_years_data[yr], "intra_grp_pubs")  for yr in years_sorted]
            _inter_grp_pcts  = [_pct(all_years_data[yr], "inter_grp_pubs")  for yr in years_sorted]

            # Dynamic analytisk tekst
            _first_yr, _last_yr   = years_sorted[0], years_sorted[-1]
            _first_pct, _last_pct = _intra_fak_pcts[0], _intra_fak_pcts[-1]
            _delta     = round(_last_pct - _first_pct, 1)
            _delta_str = f"+{_delta}" if _delta > 0 else str(_delta)
            _trend = (
                "en svagt stigende tendens, hvilket indikerer øget siloering"
                if _delta > 2 else
                "en svagt faldende tendens, hvilket indikerer mere tværgående samarbejde"
                if _delta < -2 else
                "et bemærkelsesværdigt stabilt forhold uden entydige tegn på hverken øget integration eller siloering"
            )

            _inst_trend_str = ""
            if "I" in mode:
                _first_inst, _last_inst = _intra_inst_pcts[0], _intra_inst_pcts[-1]
                _delta_inst     = round(_last_inst - _first_inst, 1)
                _delta_inst_str = f"+{_delta_inst}" if _delta_inst > 0 else str(_delta_inst)
                _inst_trend = (
                    "er intra-institutandelen ligeledes steget"
                    if _delta_inst > 2 else
                    "er intra-institutandelen faldet"
                    if _delta_inst < -2 else
                    "er intra-institutandelen ligeledes forblevet stabil"
                )
                _inst_trend_str = (
                    f"""På **institutniveau** {_inst_trend} fra **{_first_inst}%** til 
                    **{_last_inst}%** ({_delta_inst_str} pp) i samme periode."""
                )

            st.markdown(
f"""
##### Bliver KU mere eller mindre siloopdelt?
En **stigende** intra-andel tyder på øget siloering; en **faldende** på mere tværgående
samarbejde på tværs af fakulteter.

Fra **{_first_yr}** til **{_last_yr}** har intra-fakultetandelen bevæget sig fra
**{_first_pct}%** til **{_last_pct}%** ({_delta_str} procentpoint), hvilket peger på
{_trend}.

{_inst_trend_str}
""")
            # Hvilket niveau vises som tabs i denne mode?
            _tab_level = (
                "inst" if mode in ("IG", "I", "IS") else
                "grp"  if mode in ("G", "GS")       else
                "fac"                                      # F, FS, FI, FG, FIG
            )
            _tab_intra_key, _tab_inter_key, _tab_label = {
                "fac":  ("fac_intra_ew",  "fac_inter_ew",  "Fakultet"),
                "inst": ("inst_intra_ew", "inst_inter_ew", "Institut"),
                "grp":  ("grp_intra_ew",  "grp_inter_ew",  "Stillingsgruppe"),
            }[_tab_level]

            # Alle enheder på tab-niveauet på tværs af år
            _tab_units = sorted({
                u
                for yr in years_sorted
                for u in all_years_data[yr].get(_tab_intra_key, {})
            })

            # Hvilke kurver skal med i hvert plot?
            _plot_levels = []
            if mode not in ("G", "GS"):
                _plot_levels.append(("fac",  "fac_intra_ew",  "fac_inter_ew",
                                     "intra_pubs",      "inter_pubs",      "#122947",
                                     None, None))
            if "I" in mode:
                _plot_levels.append(("inst", "inst_intra_ew", "inst_inter_ew",
                                     "intra_inst_pubs", "inter_inst_pubs", "#4a7ca8",
                                     "fac_inst_intra_ew", "fac_inst_inter_ew"))
            if mode in _grp_modes:
                _tab_cross_intra = (
                    "fac_grp_intra_ew"  if _tab_level == "fac"  else
                    "inst_grp_intra_ew" if _tab_level == "inst" else None
                )
                _tab_cross_inter = (
                    "fac_grp_inter_ew"  if _tab_level == "fac"  else
                    "inst_grp_inter_ew" if _tab_level == "inst" else None
                )
                _plot_levels.append(("grp",  "grp_intra_ew",  "grp_inter_ew",
                                     "intra_grp_pubs",  "inter_grp_pubs",  "#39641c",
                                     _tab_cross_intra, _tab_cross_inter))

            def _render_silo_unit(filter_unit=None):
                """Render plot + tabel. filter_unit=None → samlet."""
                _fig = go.Figure()
                _tbl_rows = {yr: {"År": yr} for yr in years_sorted}
                _tbl_cols = ["År"]

                for unit_key, intra_key, inter_key, intra_abs_key, inter_abs_key, color, cross_intra_key, cross_inter_key in _plot_levels:
                    if filter_unit is None:
                        _intra_abs = [int(all_years_data[yr].get(intra_abs_key, 0)) for yr in years_sorted]
                        _inter_abs = [int(all_years_data[yr].get(inter_abs_key, 0)) for yr in years_sorted]
                    elif unit_key == _tab_level:
                        _intra_abs = [int(all_years_data[yr].get(intra_key, {}).get(filter_unit, 0)) for yr in years_sorted]
                        _inter_abs = [int(all_years_data[yr].get(inter_key, {}).get(filter_unit, 0)) for yr in years_sorted]
                    elif cross_intra_key and cross_inter_key:
                        # fx grp-niveau når tab-niveau er fac: slå op i fac_grp_intra_ew[filter_unit]
                        _intra_abs = [int(all_years_data[yr].get(cross_intra_key, {}).get(filter_unit, {}).get("_sum", 0)
                                    or sum(all_years_data[yr].get(cross_intra_key, {}).get(filter_unit, {}).values()))
                                    for yr in years_sorted]
                        _inter_abs = [int(all_years_data[yr].get(cross_inter_key, {}).get(filter_unit, {}).get("_sum", 0)
                                    or sum(all_years_data[yr].get(cross_inter_key, {}).get(filter_unit, {}).values()))
                                    for yr in years_sorted]
                    else:
                        _intra_abs = [int(all_years_data[yr].get(intra_abs_key, 0)) for yr in years_sorted]
                        _inter_abs = [int(all_years_data[yr].get(inter_abs_key, 0)) for yr in years_sorted]

                    _totals  = [i + x or 1 for i, x in zip(_intra_abs, _inter_abs)]
                    _y_intra = [round(100 * i / t, 1) for i, t in zip(_intra_abs, _totals)]
                    _y_inter = [round(100 - v, 1)     for v in _y_intra]

                    _level_label = {"fac": "fakultet", "inst": "institut", "grp": "stillingsgruppe"}[unit_key]
                    _color = color

                    _fig.add_trace(go.Scatter(
                        x=years_sorted, y=_y_intra,
                        mode="lines+markers+text",
                        text=[f"{v}%" for v in _y_intra],
                        textposition="top center",
                        line=dict(color=_color, width=2),
                        marker=dict(size=9),
                        name=f"Intra-{_level_label} (%)",
                    ))
                    _fig.add_trace(go.Scatter(
                        x=years_sorted, y=_y_inter,
                        mode="lines+markers+text",
                        text=[f"{v}%" for v in _y_inter],
                        textposition="bottom center",
                        line=dict(color=_color, width=2, dash="dot"),
                        marker=dict(size=9),
                        name=f"Inter-{_level_label} (%)",
                    ))

                    _intra_col = f"Intra-{_level_label} (forfatterpar)"
                    _inter_col = f"Inter-{_level_label} (forfatterpar)"
                    _pct_col   = f"Intra-{_level_label} (%)"
                    _tbl_cols += [_intra_col, _inter_col, _pct_col]
                    for i, yr in enumerate(years_sorted):
                        _tbl_rows[yr][_intra_col] = _intra_abs[i]
                        _tbl_rows[yr][_inter_col] = _inter_abs[i]
                        _tbl_rows[yr][_pct_col]   = _y_intra[i]

                _fig.update_layout(
                    yaxis_title="Andel af forfatterpar (%)",
                    yaxis=dict(range=[0, 100]),
                    xaxis=dict(tickmode="array", tickvals=years_sorted, dtick=1),
                    legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
                    height=400,
                    margin=dict(t=20, b=100),
                )
                _fu = filter_unit or "samlet"
                st.plotly_chart(_fig, width="stretch",
                    key=f"silo_unit_{_fu}_{year}_{mode}")

                _tbl_schema = (
                    [("År", pa.int64())] +
                    [(c, pa.int64() if "forfatterpar" in c else pa.float64())
                     for c in _tbl_cols[1:]]
                )
                _rows_list = [_tbl_rows[yr] for yr in years_sorted]
                with st.expander("Se tabel"):
                    st.dataframe(build_table(_rows_list, _tbl_schema),
                                 hide_index=True, width="stretch")
                    st.download_button(
                        "Download (.xlsx)",
                        data=rows_to_excel_bytes(_rows_list, [n for n, _ in _tbl_schema]),
                        file_name=f"intra_over_tid_{_fu}_{year}_{mode}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_silo_unit_{_fu}_{year}_{mode}",
                    )

            _tab_samlet, *_tabs_units = st.tabs(
                ["Samlet KU"] + _tab_units
            )
            with _tab_samlet:
                _render_silo_unit(filter_unit=None)
            for unit, _tab in zip(_tab_units, _tabs_units):
                with _tab:
                    _render_silo_unit(filter_unit=unit)
            
            # ── Samlet tabel på tværs af alle enheder og år ──────────────
            _all_rows = []
            for yr in years_sorted:
                for unit in _tab_units:
                    _row = {"År": yr, _tab_label: unit}
                    for unit_key, intra_key, inter_key, intra_abs_key, inter_abs_key, color, cross_intra_key, cross_inter_key in _plot_levels:
                        if unit_key == _tab_level:
                            _i = int(all_years_data[yr].get(intra_key, {}).get(unit, 0))
                            _x = int(all_years_data[yr].get(inter_key, {}).get(unit, 0))
                        else:
                            _i = int(all_years_data[yr].get(intra_abs_key, 0))
                            _x = int(all_years_data[yr].get(inter_abs_key, 0))
                        _tot = _i + _x or 1
                        _level_label = {"fac": "fakultet", "inst": "institut", "grp": "stillingsgruppe"}[unit_key]
                        _row[f"Intra-{_level_label} (forfatterpar)"] = _i
                        _row[f"Inter-{_level_label} (forfatterpar)"] = _x
                        _row[f"Intra-{_level_label} (%)"]            = round(100 * _i / _tot, 1)
                    _all_rows.append(_row)

            _all_cols = [("År", pa.int64()), (_tab_label, pa.string())] + [
                (c, pa.int64() if "forfatterpar" in c else pa.float64())
                for c in _all_rows[0] if c not in ("År", _tab_label)
            ] if _all_rows else []

            if _all_rows:
                with st.expander("Se samlet tabel"):
                    st.dataframe(build_table(_all_rows, _all_cols), hide_index=True, width="stretch")
                    st.download_button(
                        "Download (.xlsx)",
                        data=rows_to_excel_bytes(_all_rows, [n for n, _ in _all_cols]),
                        file_name=f"intra_over_tid_samlet_{year}_{mode}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        key=f"dl_silo_samlet_{year}_{mode}",
                    )

def render_tab_fakulteter(year, mode, fac_tot_size, fac_avg_size, edges_keep, node_meta, all_years_data=None, fac_ew=None, faculty_base_colors=None, size_map=None):
    if mode not in ("FIG", "FI", "FG", "F", "FS"):
        return
    st.subheader("Fakulteters forfatterbidrag")

    _sorted_tot  = [r for r in [{"Fakultet": fac, "val": fac_tot_size[fac]} for fac in FAC_ORDER if fac in fac_tot_size]]
    _grand_tot   = sum(fac_tot_size.values()) or 1
    _top_fac, _top_val = max(fac_tot_size.items(), key=lambda x: x[1])
    _bot_fac, _bot_val = min(fac_tot_size.items(), key=lambda x: x[1])
    _top_share   = 100 * _top_val / _grand_tot
    _avg_top, _avg_top_val = max(fac_avg_size.items(), key=lambda x: x[1])
    _avg_bot, _avg_bot_val = min(fac_avg_size.items(), key=lambda x: x[1])


    st.markdown(
f"""
Den her sektion forsøger at kortlægge fakulteternes sampubliceringsmønster ved at undersøge
forfatterbidrag, kantvægte samt forholdet mellem forfatterbidrag og sampubliceringer.

##### Fakulteternes forfatterbidrag i {year}

I det valgte udsnit har **{_top_fac}** flest forfatterbidrag med **{int(_top_val):,}** 
({_top_share:.1f}% af samtlige bidrag), mens **{_bot_fac}** har færrest med
 **{int(_bot_val):,}**. Målt på gennemsnitligt forfatterbidrag per node topper
  **{_avg_top}** (**{_avg_top_val:,.1f}**), mens **{_avg_bot}** ligger lavest
(**{_avg_bot_val:,.1f}**).
    """)

    if size_map is None:
        size_map = fac_tot_size

    tab_ft, tab_fa = st.tabs(["Samlet forfatterbidrag", "Gennemsnitligt forfatterbidrag"])
    
    with tab_ft:
        st.markdown("**Samlet forfatterbidrag**")
        fac_total_rows = [{"Fakultet": fac, "Samlet forfatterbidrag": int(fac_tot_size[fac]), "mode": mode}
                        for fac in FAC_ORDER if fac in fac_tot_size]
        _facs_ord  = [r["Fakultet"] for r in fac_total_rows]
        _tots_ord  = [r["Samlet forfatterbidrag"] for r in fac_total_rows]
        _fac_colors = [faculty_base_colors.get(f, "#122947") if faculty_base_colors else "#122947" for f in _facs_ord]
        _tot_grand = sum(_tots_ord) or 1
        _fig_ft = go.Figure(go.Bar(
            y=_facs_ord,
            x=_tots_ord,
            orientation="h",
            marker_color=_fac_colors,
            text=[f"{v:,}<br>({100*v/_tot_grand:.1f}%)" for v in _tots_ord],
            textposition="inside",
        ))
        _fig_ft.update_layout(
            xaxis_title="Forfatterbidrag", height=max(300, 50 * len(_facs_ord)),
            margin=dict(l=80, t=20, r=80),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(_fig_ft, width='stretch')

    with tab_fa:
        st.markdown("**Gennemsnitligt forfatterbidrag per node**")
        fac_avg_rows = [{"Fakultet": fac, "Gennemsnitlige forfatterbidrag": int(fac_avg_size[fac]), "mode": mode}
                        for fac in FAC_ORDER if fac in fac_avg_size]
        _fac_avg_colors = [faculty_base_colors.get(r["Fakultet"], "#4a7ca8") if faculty_base_colors else "#4a7ca8" for r in fac_avg_rows]
        _avgs_ord = [r["Gennemsnitlige forfatterbidrag"] for r in fac_avg_rows]
        _fig_fa = go.Figure(go.Bar(
            y=[r["Fakultet"] for r in fac_avg_rows],
            x=_avgs_ord,
            orientation="h",
            marker_color=_fac_avg_colors,
            text=[f"{v:,}" for v in _avgs_ord], textposition="inside",
        ))
        _fig_fa.update_layout(
            xaxis_title="Gns. forfatterbidrag", height=max(300, 50 * len(fac_avg_rows)),
            margin=dict(l=80, t=20, r=80),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(_fig_fa, width='stretch')

        _fac_summary_rows = [
            {"Fakultet": fac,
             "Samlet forfatterbidrag": int(fac_tot_size[fac]),
             "Andel (%)": round(100 * fac_tot_size[fac] / _tot_grand, 1),
             "Gns. forfatterbidrag": round(fac_avg_size.get(fac, 0), 1),
             "mode": mode}
            for fac in FAC_ORDER if fac in fac_tot_size
        ]
        _fac_summary_schema = [
            ("Fakultet", pa.string()),
            ("Samlet forfatterbidrag", pa.int64()),
            ("Andel (%)", pa.float64()),
            ("Gns. forfatterbidrag", pa.float64()),
            ("mode", pa.string()),
        ]
    with st.expander("Se tabel"):
        st.dataframe(build_table(_fac_summary_rows, _fac_summary_schema), width="stretch", hide_index=True)
        st.download_button(
            "Download (.xlsx)",
            data=rows_to_excel_bytes(_fac_summary_rows, [n for n, _ in _fac_summary_schema]),
            file_name=f"forfatterbidrag_fakulteter_total_{year}_{mode}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)

    _facs_in_data = sorted({f for s in (all_years_data or {}).values() for f in s.get("fac_tot", {})})
    _render_year_comparison(
        all_years_data,
        series=[(fac, "fac_tot", fac) for fac in _facs_in_data],
        title="Sammenlign år - forfatterbidrag per fakultet",
        colors = faculty_base_colors
    )
    
    st.subheader("Kantvægte fordelt på fakultet")

    if fac_ew:
        _grand_ew    = sum(fac_ew.values()) or 1
        _ew_top, _ew_top_val = max(fac_ew.items(), key=lambda x: x[1])
        _ew_share    = 100 * _ew_top_val / _grand_ew
        _ratio_sorted = sorted(
            [(k, fac_ew[k] / fac_tot_size[k]) for k in fac_tot_size if fac_tot_size.get(k) and fac_ew.get(k)],
            key=lambda x: -x[1],
        )
        _rat_top, _rat_top_val = _ratio_sorted[0]
        _rat_bot, _rat_bot_val = _ratio_sorted[-1]
        st.markdown(
            f"Hver kant er fordelt ligeligt mellem dens to endepunkter. "
            f"**{_ew_top}** modtager den største andel af sampubliceringerne "
            f"({_ew_share:.1f}% af den samlede kantvægt). "
            f"Relativt til størrelse - kantvægt per forfatterbidrag - er **{_rat_top}** "
            f"mest sampublicerende ({_rat_top_val:.3f}), mens **{_rat_bot}** er mindst ({_rat_bot_val:.3f})."
        )
    else:
        st.markdown("Hver kant har to noder - hver publikation (kantvægten) er fordelt ligeligt mellem de tilhørende fakultetsnoder.")
    _render_org_bar(edges_keep, node_meta, "fac", "Fakultet", color_map=faculty_base_colors, size_map=fac_tot_size)

    if fac_ew is not None:
        _render_share_comparison(fac_tot_size, fac_ew, "Fakultet")



def render_tab_institutter(year, mode, inst_tot_size, inst_avg_size, institut_fakultets_map, edges_keep, node_meta, all_years_data=None, inst_ew=None, faculty_base_colors=None, size_map = None):
    
    if size_map is None:
        size_map = inst_tot_size
    
    st.subheader("Institutter")

    _sorted_tot  = sorted(inst_tot_size.items(), key=lambda x: -x[1])
    _grand_tot   = sum(inst_tot_size.values()) or 1
    _top_inst, _top_val   = _sorted_tot[0]
    _bot_inst, _bot_val   = _sorted_tot[-1]
    _top_share   = 100 * _top_val / _grand_tot
    _sorted_avg  = sorted(inst_avg_size.items(), key=lambda x: -x[1])
    _avg_top, _avg_top_val = _sorted_avg[0]
    _avg_bot, _avg_bot_val = _sorted_avg[-1]

    st.markdown(f"""
Denne fane viser, hvor meget hvert institut bidrager til KU's sampubliceringsaktivitet — 
både i absolutte tal og relativt til instituttets størrelse.

##### Institutternes forfatterbidrag i {year}

**{_top_inst}** tegner sig for flest forfatterbidrag i det valgte udsnit med **{int(_top_val):,}** 
({_top_share:.1f}% af samtlige bidrag), mens **{_bot_inst}** bidrager mindst med **{int(_bot_val):,}**.
Ser man på forfatterbidrag per forsker, er **{_avg_top}** mest aktiv (**{_avg_top_val:,.1f}**),
mens **{_avg_bot}** ligger lavest (**{_avg_bot_val:,.1f}**).
    """)

    _inst_color_map = {}
    if faculty_base_colors:
        _insts_by_fac: dict[str, list] = {}
        for m in node_meta.values():
            if m.get("inst") and m.get("fac"):
                _insts_by_fac.setdefault(m["fac"], [])
                if m["inst"] not in _insts_by_fac[m["fac"]]:
                    _insts_by_fac[m["fac"]].append(m["inst"])
        for fac, insts in _insts_by_fac.items():
            insts_sorted = sorted(insts)
            k = max(1, len(insts_sorted))
            base = faculty_base_colors.get(fac, "#122947")
            for rank, inst in enumerate(insts_sorted):
                t = rank / max(1, k - 1)  # 0.0 → 1.0
                lf = 0.5 + 1.8 * t        # lyshed: 0.6 (mørk) → 1.4 (lys)
                sf = 1.3 - 0.7 * t        # mætning: 1.2 (mættet) → 0.8 (dæmpet)
                _inst_color_map[inst] = adjust_color(base, lf, sf)

    # ── Lokalt fakultetsfilter ────────────────────────────────────────────
    _all_facs_in_tab = sorted({
        institut_fakultets_map.get(inst, "")
        for inst in inst_tot_size
        if institut_fakultets_map.get(inst, "")
    })
    _fac_filter = st.multiselect(
        "Filtrer på fakultet",
        options=_all_facs_in_tab,
        default=[],
        key=f"inst_tab_fac_filter_{year}_{mode}",
        placeholder="Alle fakulteter",
    )
    def _fac_ok(inst):
        return not _fac_filter or institut_fakultets_map.get(inst, "") in _fac_filter

    tab_it, tab_ia = st.tabs(["Samlet forfatterbidrag", "Gennemsnitligt forfatterbidrag"])
    with tab_it:
        # ── Samlet forfatterbidrag - sorted descending ────────────────────────────
        st.markdown("**Samlet forfatterbidrag**")
        inst_total_rows = [
            {"Fakultet": institut_fakultets_map.get(inst, ""), "Institut": inst,
            "Samlet forfatterbidrag": int(inst_tot_size[inst]), "mode": mode}
            for inst in sorted(inst_tot_size, key=lambda i: -inst_tot_size[i]) if _fac_ok(inst)
        ]
        _insts   = [r["Institut"] for r in inst_total_rows]
        _tots    = [r["Samlet forfatterbidrag"] for r in inst_total_rows]
        _tot_g   = sum(_tots) or 1

        _inst_colors = [_inst_color_map.get(i, "#122947") for i in _insts]

        _fig_it  = go.Figure(go.Bar(
            y=_insts, x=_tots, orientation="h",
            marker_color=_inst_colors,
            text=[f"{v:,}  ({100*v/_tot_g:.1f}%)" for v in _tots],
            textposition="inside",
        ))
        _fig_it.update_layout(
            xaxis_title="Forfatterbidrag", height=max(350, 35 * len(_insts)),
            margin=dict(l=160, t=20, r=80),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(_fig_it, width='stretch')

        _inst_summary_rows = [
            {"Fakultet": institut_fakultets_map.get(inst, ""),
             "Institut": inst,
             "Samlet forfatterbidrag": int(inst_tot_size[inst]),
             "Andel (%)": round(100 * inst_tot_size[inst] / _tot_g, 1),
             "Gns. forfatterbidrag": round(inst_avg_size.get(inst, 0), 1),
             "mode": mode}
            for inst in sorted(inst_avg_size, key=lambda i: -inst_avg_size[i]) if _fac_ok(inst)
        ]
        _inst_summary_schema = [
            ("Fakultet", pa.string()),
            ("Institut", pa.string()),
            ("Samlet forfatterbidrag", pa.int64()),
            ("Andel (%)", pa.float64()),
            ("Gns. forfatterbidrag", pa.float64()),
            ("mode", pa.string()),
        ]

    with tab_ia:
        # ── Gennemsnitligt forfatterbidrag ────────────────────────────────────────
        st.markdown("**Gennemsnitligt forfatterbidrag per node**")
        inst_avg_rows = [
            {"Fakultet": institut_fakultets_map.get(inst, ""), "Institut": inst,
            "Gennemsnitligt forfatterbidrag": round(inst_avg_size[inst], 1), "mode": mode}
            for inst in sorted(inst_avg_size, key=lambda i: -inst_avg_size[i]) if _fac_ok(inst)
        ]
        _avgs   = [r["Gennemsnitligt forfatterbidrag"] for r in inst_avg_rows]
        _inst_avg_colors = [_inst_color_map.get(r["Institut"], "#122947") for r in inst_avg_rows]
        _fig_ia = go.Figure(go.Bar(
            y=[r["Institut"] for r in inst_avg_rows], x=_avgs, orientation="h",
            marker_color=_inst_avg_colors,
            text=[f"{v:,}" for v in _avgs], textposition="inside",
        ))
        _fig_ia.update_layout(
            xaxis_title="Gns. forfatterbidrag", height=max(350, 35 * len(_avgs)),
            margin=dict(l=160, t=20, r=80),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(_fig_ia, width='stretch')

    with st.expander("Se tabel"):
        st.dataframe(build_table(_inst_summary_rows, _inst_summary_schema), width="stretch", hide_index=True)
        st.download_button(
            "Download (.xlsx)", 
            data=rows_to_excel_bytes(_inst_summary_rows, [n for n, _ in _inst_summary_schema]),
            file_name=f"forfatterbidrag_institutter_total_{year}_{mode}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)

    _insts_in_data = sorted({i for s in (all_years_data or {}).values() for i in s.get("inst_tot", {}) if _fac_ok(i)})
    _render_year_comparison(
        all_years_data,
        series=[(inst, "inst_tot", inst) for inst in _insts_in_data],
        title="Sammenlign år - forfatterbidrag per institut",
        colors = _inst_color_map
    )

    st.subheader("Sampubliceringsaktivitet per institut")
    _inst_ew_filtered  = {k: v for k, v in (inst_ew or {}).items() if _fac_ok(k)}
    _inst_tot_filtered = {k: v for k, v in inst_tot_size.items() if _fac_ok(k)}
    _edges_filtered    = [(u, v, w, *r) for u, v, w, *r in edges_keep
                          if _fac_ok(node_meta.get(u, {}).get("inst", ""))
                          or _fac_ok(node_meta.get(v, {}).get("inst", ""))]

    if _inst_ew_filtered:
        _grand_ew   = sum(_inst_ew_filtered.values()) or 1
        _ew_sorted  = sorted(_inst_ew_filtered.items(), key=lambda x: -x[1])
        _ew_top, _ew_top_val = _ew_sorted[0]
        _ew_share   = 100 * _ew_top_val / _grand_ew
        _ew_top_val_fmt = f"{_ew_top_val:,.1f}"
        _ratio_sorted = sorted(
            [(k, _inst_ew_filtered[k] / _inst_tot_filtered[k]) for k in _inst_tot_filtered if _inst_tot_filtered.get(k) and _inst_ew_filtered.get(k)],
            key=lambda x: -x[1],
        )
        _rat_top, _rat_top_val = _ratio_sorted[0]
        _rat_bot, _rat_bot_val = _ratio_sorted[-1]
        st.markdown(
f"""Hver kant er fordelt ligeligt mellem dens to endepunkter. **{_ew_top}** modtager den 
største andel af sampubliceringerne ({_ew_top_val_fmt} forfatterpar, {_ew_share:.1f}% 
af den samlede kantvægt). Relativt til størrelse - kantvægt per forfatterbidrag - 
er **{_rat_top}** mest sampublicerende ({_rat_top_val:.3f}), mens **{_rat_bot}** er 
mindst ({_rat_bot_val:.3f}).

Alle samarbejder, hvor mindst ét institut er fra det valgte fakultet, vises.
"""
)
    else:
        st.markdown("Hver kant har to noder - hver publikation (kantvægten) er fordelt ligeligt mellem de tilhørende noder.")

    _render_org_bar(_edges_filtered, node_meta, "inst", "Institut", color_map=_inst_color_map, size_map=_inst_tot_filtered)

    if inst_ew is not None:
        _render_share_comparison(_inst_tot_filtered, _inst_ew_filtered, "Institut")

    if edges_keep and mode in ("FI", "FIG", "IG"):
        st.subheader("Institutternes inter-fakultet samarbejde")
        
        st.markdown(
            """Figuren viser, hvor stor en andel af hvert instituts sampubliceringer der involverer 
            et institut fra et andet fakultet. Institutter med en høj andel samarbejder i særlig 
            grad på tværs af fakultetsgrænser.
            
            Vær opmærksom på, hvilke filtre der er valgte i sidepanelet."""
        )

        _inst_intra_ew: dict[str, float] = {}
        _inst_inter_ew: dict[str, float] = {}

        for u, v, w, *_ in edges_keep:
            mu = node_meta.get(u, {})
            mv = node_meta.get(v, {})
            fac_u, fac_v   = mu.get("fac", ""), mv.get("fac", "")
            inst_u, inst_v = mu.get("inst", ""), mv.get("inst", "")
            cross = fac_u != fac_v
            for inst in [inst_u, inst_v]:
                if not inst:
                    continue
                if cross:
                    _inst_inter_ew[inst] = _inst_inter_ew.get(inst, 0.0) + w / 2
                else:
                    _inst_intra_ew[inst] = _inst_intra_ew.get(inst, 0.0) + w / 2

        _all_insts = sorted(set(_inst_intra_ew) | set(_inst_inter_ew))
        if _all_insts:
            _inter_rows = []
            for inst in _all_insts:
                intra = _inst_intra_ew.get(inst, 0.0)
                inter = _inst_inter_ew.get(inst, 0.0)
                total = intra + inter
                _inter_rows.append({
                    "Institut":              inst,
                    "Fakultet":              institut_fakultets_map.get(inst, ""),
                    "Inter-fakultet (%)":    round(100 * inter / total, 1) if total else 0.0,
                    "Inter-fakultet (vægt)": round(inter, 1),
                    "Total kantvægt":        round(total, 1),
                })

            if _fac_filter:
                _inter_rows = [r for r in _inter_rows if r["Fakultet"] in _fac_filter]

            _inter_rows.sort(key=lambda r: -r["Inter-fakultet (%)"])

            _fig_inter = go.Figure(go.Bar(
                y=[r["Institut"] for r in _inter_rows],
                x=[r["Inter-fakultet (%)"] for r in _inter_rows],
                orientation="h",
                marker_color=[_inst_color_map.get(r["Institut"], "#122947") for r in _inter_rows],
                text=[f"{r['Inter-fakultet (%)']:.1f}%  ({r['Inter-fakultet (vægt)']:.1f} fp)" for r in _inter_rows],
                textposition="inside",
            ))
            _fig_inter.update_layout(
                xaxis_title="Andel inter-fakultet sampubliceringer (%)",
                xaxis_range=[0, 100],
                height=max(400, 28 * len(_inter_rows)),
                margin=dict(l=200, r=80, t=10),
                yaxis=dict(autorange="reversed"),
            )
            st.plotly_chart(_fig_inter, width="stretch")

            _inter_schema = [
                ("Institut",              pa.string()),
                ("Fakultet",              pa.string()),
                ("Inter-fakultet (%)",    pa.float64()),
                ("Inter-fakultet (vægt)", pa.float64()),
                ("Total kantvægt",        pa.float64()),
            ]
            with st.expander("Se tabel"):
                st.dataframe(build_table(_inter_rows, _inter_schema), hide_index=True, width="stretch")
                st.download_button(
                    "Download (.xlsx)",
                    data=rows_to_excel_bytes(_inter_rows, [n for n, _ in _inter_schema]),
                    file_name=f"inter_fakultet_institutter_{year}_{mode}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_inter_fak_inst_{year}_{mode}",
                )




def render_pos_chart(pos_counts: dict, tab_key: str, year: int, mode: str):
    stillinger_sorted = sorted(pos_counts.keys(), key=lambda g: HIERARKI.get(g, 999))
    if not stillinger_sorted:
        st.error("Ingen data i det valgte udsnit.")
        return
    _pos_colors = ku_color_sequence(3)
    fig = go.Figure()
    for (pos_name, label), color in zip(
        (("first", "Førsteforfatter"), ("middle", "Mellemforfatter"), ("last", "Sidsteforfatter")),
        _pos_colors,
    ):
        fig.add_trace(go.Bar(
            name=label,
            y=stillinger_sorted,
            x=[pos_counts.get(g, {}).get(pos_name, 0) for g in stillinger_sorted],
            orientation="h",
            marker_color=color,
            text=[pos_counts.get(g, {}).get(pos_name, 0) for g in stillinger_sorted],
            textposition="inside",
        ))
    fig.update_layout(
        barmode="stack",
        xaxis_title="Antal publikationer",
        height=450,
        legend_title="Forfatterposition",
    )
    st.plotly_chart(fig, width="content", key=f"pos_fig_{tab_key}_{year}_{mode}")

    rows = [
        {
            "Stillingsgruppe":     grp,
            "Førsteforfatter":     pos_counts.get(grp, {}).get("first",  0),
            "Førsteforfatter (%)": round(100 * pos_counts.get(grp, {}).get("first",  0) / pos_counts.get(grp, {}).get("total", 1), 1) if pos_counts.get(grp, {}).get("total") else 0.0,
            "Mellemforfatter":     pos_counts.get(grp, {}).get("middle", 0),
            "Mellemforfatter (%)": round(100 * pos_counts.get(grp, {}).get("middle", 0) / pos_counts.get(grp, {}).get("total", 1), 1) if pos_counts.get(grp, {}).get("total") else 0.0,
            "Sidsteforfatter":     pos_counts.get(grp, {}).get("last",   0),
            "Sidsteforfatter (%)": round(100 * pos_counts.get(grp, {}).get("last",   0) / pos_counts.get(grp, {}).get("total", 1), 1) if pos_counts.get(grp, {}).get("total") else 0.0,
            "Total deltagelse":    pos_counts.get(grp, {}).get("total",  0),
        }
        for grp in stillinger_sorted
    ]
    schema = [
        ("Stillingsgruppe",    pa.string()),
        ("Førsteforfatter",    pa.int64()),  ("Førsteforfatter (%)",  pa.float64()),
        ("Mellemforfatter",    pa.int64()),  ("Mellemforfatter (%)",  pa.float64()),
        ("Sidsteforfatter",    pa.int64()),  ("Sidsteforfatter (%)",  pa.float64()),
        ("Total deltagelse",   pa.int64()),
    ]
    with st.expander("Se tabel"):
        st.dataframe(build_table(rows, schema), width="stretch", hide_index=True)



def render_tab_stillingsgrupper(year, mode, all_groups, grp_tot_size, grp_avg_size,
                                 selected_facs, selected_insts, selected_grps, 
                                 selected_genders, selected_citizenships,
                                 all_years_data=None, grp_ew=None, edges_keep=None, 
                                 node_meta=None, forfatterpositioner=None,
                                 inst_to_fac = None):
    st.subheader("Stillingsgruppernes forfatterbidrag")
    st.markdown(
f""" Den her sektion fokuserer på, hvordan stillingsgrupperne publicerer i det valgte udsnit.
Først kortlægges stillinggruppernes forfatterbidrag og publikationer for til sidst at 
zoome ind på, hvordan de forskellige stillingsgrupper placerer sig i publikationernes 
forfatterrækker."""
)

     # ── Lokale filtre ─────────────────────────────────────────────────────
    _fac_to_insts: dict[str, list] = {}
    _facs_only: list = []
    if node_meta:
        for m in node_meta.values():
            f, i = m.get("fac", ""), m.get("inst", "")
            if f and i:
                if i not in _fac_to_insts.setdefault(f, []):
                    _fac_to_insts[f].append(i)
            elif f:
                if f not in _facs_only:
                    _facs_only.append(f)
    _all_facs_grp = sorted(set(list(_fac_to_insts.keys()) + _facs_only))
    _show_local_filter = bool(_all_facs_grp)
    _show_inst_filter  = bool(_fac_to_insts)

    _grp_fac_filter = st.multiselect(
        "Filtrer på fakultet",
        options=_all_facs_grp if _show_local_filter else [],
        default=[],
        key=f"grp_tab_fac_filter_{year}_{mode}_v2",
        placeholder="Alle fakulteter" if _show_local_filter else "Ikke tilgængeligt i denne mode",
        disabled=not _show_local_filter,
    )
    if _show_inst_filter:
        _inst_opts = sorted({
            i
            for f, insts in _fac_to_insts.items()
            for i in insts
            if not _grp_fac_filter or f in _grp_fac_filter
        })
        _grp_inst_filter = st.multiselect(
            "Filtrer på institut",
            options=_inst_opts,
            default=[],
            key=f"grp_tab_inst_filter_{year}_{mode}_v2",
            placeholder="Alle institutter",
        )
    else:
        _grp_inst_filter = []

    # Institutter afhænger af valgte fakulteter
    _inst_opts = sorted({
        i
        for f, insts in _fac_to_insts.items()
        for i in insts
        if not _grp_fac_filter or f in _grp_fac_filter
    })

    def _grp_node_ok(nid):
        m = (node_meta or {}).get(nid, {})
        if _grp_fac_filter and m.get("fac", "") not in _grp_fac_filter:
            return False
        if _grp_inst_filter and m.get("inst", "") not in _grp_inst_filter:
            return False
        return True

    # Filtrerede edges og størrelser
    _edges_grp = (
        [(u, v, w, *r) for u, v, w, *r in (edges_keep or [])
         if _grp_node_ok(u) or _grp_node_ok(v)]
        if (_grp_fac_filter or _grp_inst_filter) else (edges_keep or [])
    )
    _nodes_grp = {
        nid for edge in _edges_grp for nid in (edge[0], edge[1])
    }
    _grp_tot_filtered: dict[str, float] = {}
    _grp_avg_filtered: dict[str, float] = {}
    if _grp_fac_filter or _grp_inst_filter:
        _grp_counts: dict[str, list] = {}
        for nid in _nodes_grp:
            m = (node_meta or {}).get(nid, {})
            g = m.get("grp", "")
            if not g: continue
            if not _grp_node_ok(nid): continue
            _grp_counts.setdefault(g, []).append(m.get("size", 0))
        for g, sizes in _grp_counts.items():
            _grp_tot_filtered[g] = sum(sizes)
            _grp_avg_filtered[g] = sum(sizes) / len(sizes) if sizes else 0
        _grp_ew_filtered: dict[str, float] = {}
        for u, v, w, *_ in _edges_grp:
            for n in (u, v):
                if not _grp_node_ok(n): continue
                g = (node_meta or {}).get(n, {}).get("grp", "")
                if g:
                    _grp_ew_filtered[g] = _grp_ew_filtered.get(g, 0.0) + w / 2
    else:
        _grp_tot_filtered = grp_tot_size
        _grp_avg_filtered = grp_avg_size
        _grp_ew_filtered  = grp_ew or {}
    
    grp_tot_size = _grp_tot_filtered
    grp_avg_size = _grp_avg_filtered
    grp_ew       = _grp_ew_filtered

    if grp_tot_size:
        _grand_tot   = sum(grp_tot_size.values()) or 1
        _top_grp, _top_val = max(grp_tot_size.items(), key=lambda x: x[1])
        _bot_grp, _bot_val = min(grp_tot_size.items(), key=lambda x: x[1])
        _top_share   = 100 * _top_val / _grand_tot
        _avg_top, _avg_top_val = max(grp_avg_size.items(), key=lambda x: x[1])
        _avg_bot, _avg_bot_val = min(grp_avg_size.items(), key=lambda x: x[1])
        

        st.markdown(
f"""
##### Stillinggruppernes forfatterbidrag i {year}

Generelt udgør **{str(_top_grp).lower()}er** flest forfatterbidrag med **{int(_top_val):,}** 
({_top_share:.1f}% af samtlige bidrag i udsnittet), mens **{_bot_grp}** har færrest med 
**{int(_bot_val):,}** forfatterbidrag. Det samlede tal afspejler gruppens størrelse — en stor 
gruppe vil naturligt dominere. Målt på gennemsnitligt forfatterbidrag per node — som 
normaliserer for gruppestørrelse og dermed viser hvor produktiv den typiske node er — topper 
**{str(_avg_top).lower()}er** (**{_avg_top_val:,.1f}**), mens **{str(_avg_bot)}er** ligger 
lavest (**{_avg_bot_val:,.1f}**).
""")

    # ── Forfatterbidrag - sorted by hierarchy ────────────────────────────────
    _grps_hier = sorted(grp_tot_size.keys(), key=lambda g: HIERARKI.get(g, 999))
    _tots_hier = [int(grp_tot_size[g]) for g in _grps_hier]
    _avgs_hier = [round(grp_avg_size.get(g, 0), 1) for g in _grps_hier]
    _tot_grand = sum(_tots_hier) or 1

    grp_colors = stillingsgruppe_colors({})

    # Samlet (with % labels)
    tab_gt, tab_ga = st.tabs(["Samlet forfatterbidrag", "Gennemsnitligt forfatterbidrag"])
    with tab_gt:
        _fig_tot = go.Figure(go.Bar(
            y=_grps_hier, x=_tots_hier, orientation="h",
            marker_color=[grp_colors.get(g, "#122947") for g in _grps_hier],
            text=[f"{v:,}  ({100*v/_tot_grand:.1f}%)" for v in _tots_hier],
            textposition="inside",
        ))
        _fig_tot.update_layout(
            xaxis_title="Forfatterbidrag",
            height=max(350, 40 * len(_grps_hier)),
            margin=dict(l=160, t=20, r=80),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(_fig_tot, width='stretch')

    with tab_ga:
        _fig_avg = go.Figure(go.Bar(
            y=_grps_hier, x=_avgs_hier, orientation="h",
            marker_color=[grp_colors.get(g, "#122947") for g in _grps_hier],
            text=[f"{v:.1f}" for v in _avgs_hier],
            textposition="inside",
        ))
        _fig_avg.update_layout(
            xaxis_title="Gns. forfatterbidrag",
            height=max(350, 40 * len(_grps_hier)),
            margin=dict(l=160, t=20, r=80),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(_fig_avg, width='stretch')

    # Combined summary table
    _grp_summary_rows = [
        {"Stillingsgruppe": g,
         "Samlet forfatterbidrag": int(grp_tot_size[g]),
         "Andel (%)": round(100 * grp_tot_size[g] / _tot_grand, 1),
         "Gns. forfatterbidrag": round(grp_avg_size.get(g, 0), 1),
         "mode": mode}
        for g in _grps_hier
    ]
    _grp_schema = [
        ("Stillingsgruppe", pa.string()),
        ("Samlet forfatterbidrag", pa.int64()),
        ("Andel (%)", pa.float64()),
        ("Gns. forfatterbidrag", pa.float64()),
        ("mode", pa.string()),
    ]

    with st.expander("Se tabel"):
        st.dataframe(build_table(_grp_summary_rows, _grp_schema), hide_index=True, width="stretch")
        st.download_button("Download (.xlsx)",
                           data=rows_to_excel_bytes(_grp_summary_rows, [n for n, _ in _grp_schema]),
                           file_name=f"forfatterbidrag_stillingsgrupper_{year}_{mode}.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)
    
    _grps_in_data = sorted(
        {g for s in (all_years_data or {}).values() for g in s.get("grp_tot", {})},
        key=lambda g: HIERARKI.get(g, 999),
    )
    _render_year_comparison(
    all_years_data,
    series=[(grp, "grp_tot", grp) for grp in _grps_in_data],
    title="Sammenlign år - forfatterbidrag per stillingsgruppe",
    colors=grp_colors,
    )

    st.subheader("Kantvægt fordelt på stillingsgruppe")

    if grp_ew:
        _grand_ew    = sum(grp_ew.values()) or 1
        _ew_top, _ew_top_val = max(grp_ew.items(), key=lambda x: x[1])
        _ew_share    = 100 * _ew_top_val / _grand_ew
        _ratio_sorted = sorted(
            [(k, grp_ew[k] / grp_tot_size[k]) for k in grp_tot_size if grp_tot_size.get(k) and grp_ew.get(k)],
            key=lambda x: -x[1],
        )
        _rat_top, _rat_top_val = _ratio_sorted[0]
        _rat_bot, _rat_bot_val = _ratio_sorted[-1]
        st.markdown(
            f"Hver kant er fordelt ligeligt mellem dens to endepunkter. "
            f"**{_ew_top}** modtager den største andel af sampubliceringerne "
            f"({_ew_share:.1f}% af den samlede kantvægt). "
            f"Relativt til størrelse - kantvægt per forfatterbidrag - er **{_rat_top}** "
            f"mest sampublicerende ({_rat_top_val:.3f}), mens **{_rat_bot}** er mindst ({_rat_bot_val:.3f})."
        )
    _render_org_bar(edges_keep, node_meta, "grp", "Stillingsgruppe", size_map=grp_tot_size, color_map=grp_colors) if edges_keep else None

    if grp_ew is not None:
        _render_share_comparison(grp_tot_size, grp_ew, "Stillingsgruppe")

    st.subheader("Stillingsgruppers placering i forfatterrækkefølger")
    st.markdown(
        "Nedenstående viser i hvilke forfatterpositioner - første, mellem eller sidst - "
        "de forskellige stillingsgrupper optræder i publikationerne for det valgte år. "
        "Kun publikationer med mindst to forfattere er medtaget."
    )

    yr_data = (forfatterpositioner or {}).get(str(year), {})

    def _merge_pos(level_dict: dict, keys: list) -> dict:
        counts = defaultdict(lambda: defaultdict(int))
        for key in keys:
            for grp, c in level_dict.get(key, {}).items():
                for k, v in c.items():
                    counts[grp][k] += v
        return {grp: dict(c) for grp, c in counts.items()}
        
    active_facs  = (
        _grp_fac_filter if _grp_fac_filter
        else list(selected_facs) if selected_facs
        else [f for f in FAC_ORDER if f in yr_data.get("fac", {})]
    )
    active_insts = (
        _grp_inst_filter if _grp_inst_filter
        else list(selected_insts) if selected_insts
        else sorted(yr_data.get("inst", {}).keys())
    )
    # Filtrer institutter til valgte fakulteter
    if mode in ("FI", "FIG") and inst_to_fac:
        fac_set = set(active_facs)
        active_insts = [i for i in active_insts if inst_to_fac.get(i) in fac_set]
        pos_data = _merge_pos(yr_data.get("inst", {}), active_insts)
    elif mode in ("F", "FS", "FG"):
        pos_data = _merge_pos(yr_data.get("fac", {}), active_facs)
    else:
        pos_data = _merge_pos(yr_data.get("inst", {}), active_insts)


    render_pos_chart(pos_data, "ku", year, mode)
    



def render_tab_centralitet(year, mode, faculty_wd_sorted, faculty_bs_sorted,
                            inst_wd_sorted, inst_bs_sorted,
                            grp_wd_sorted, grp_bs_sorted, node_meta,
                            grp_node_wd_sorted=None, grp_node_bs_sorted=None,
                            faculty_base_colors=None, grp_colors=None):
    st.subheader("Nøgleaktører i sampubliceringsnetværket")
    
    st.markdown(
f""" 
Denne fane viser, hvilke enheder der spiller den største rolle i sampubliceringsnetværket - både
hvem der samarbejder mest, og hvem der binder forskellige enheder sammen. 

**Samlet samarbejdsomfang** (centralitet) opgør, hvor mange forfatterpar en enhed samlet set 
indgår i. Det er et mål for aktivitet - ikke nødvendigvis strategisk position. Bemærk, at dette
ikke er det samme som, hvor meget to enheder samarbejder med hinanden - det vises under fanen 
*Samarbejdsmønstre*.

**Brobyggerrolle** (*betweenness* centralitet) viser, hvilke enheder der fungerer som forbindelsesled mellem grupper, der 
ellers ikke samarbejder direkte. En enhed men en høj brobyggerrolle er strukturel vigtig, da
den binder netværket sammen og skaber forbindelser på tværs af grupper, der ellers ville være
adskilte
""")

    if SVG_CENTRALITET:
        st.markdown(f'<div style="max-width:800px;">{SVG_CENTRALITET}</div>', unsafe_allow_html=True)
        st.caption("Illustration af de to centralitetsmål. En mørkerød node markerer enheden med den højeste værdi i hvert mål.")

    st.markdown(
"""
Måske lidt tekst her, der fortæller, at man for overskuelighedens skyld kan vælge de nøgleaktører, som man vil fokusere på,
nedenfor. XX
""")


    def _cent_charts(label_key, wd_sorted, bs_sorted, extra_col=None, extra_map=None, color_map=None):
        """Render bar charts + table for one centralitet level."""
        if not wd_sorted:
            return
        wd_map = dict(wd_sorted)
        bs_map = dict(bs_sorted) if bs_sorted else {}
        keys   = [k for k, _ in wd_sorted]

        # Weighted degree bar
        st.subheader(f"{label_key}")

        #tab_weg, tab_bet = st.tabs(["Weighted degree", "Betweenness centralitet"])
        tab_weg, tab_bet = st.tabs(["Samlet samarbejdsomfang", "Brobyggerrolle"])
        with tab_weg:
            _fig_wd = go.Figure(go.Bar(
                y=keys, x=[wd_map[k] for k in keys], orientation="h",
                marker_color=[color_map.get(k, "#122947") for k in keys] if color_map else "#122947",
                text=[f"{wd_map[k]:,.1f}" for k in keys], textposition="inside",
            ))
            _fig_wd.update_layout(
                xaxis_title="Weighted degree", yaxis=dict(autorange="reversed"),
                height=max(300, 32 * len(keys)), margin=dict(l=160, r=80, t=10),
            )
            st.plotly_chart(_fig_wd, width='stretch')

        with tab_bet:
            # Betweenness bar
            if bs_map:
                #st.markdown(f"**Betweenness centralitet - {label_key}**")
                _bs_sorted_keys = sorted(keys, key=lambda k: -bs_map.get(k, 0))
                _fig_bs = go.Figure(go.Bar(
                    y=_bs_sorted_keys,
                    x=[bs_map.get(k, 0) for k in _bs_sorted_keys],
                    orientation="h",
                    marker_color=[color_map.get(k, "#122947") for k in _bs_sorted_keys] if color_map else "#122947",
                    text=[f"{bs_map.get(k,0):.4f}" for k in _bs_sorted_keys],
                    textposition="inside",
                ))
                _fig_bs.update_layout(
                    xaxis_title="Betweenness (normaliseret)",
                    yaxis=dict(autorange="reversed"),
                    height=max(300, 32 * len(keys)), margin=dict(l=160, r=80, t=10),
                )
                st.plotly_chart(_fig_bs, width='stretch')

        if bs_map and len(keys) >= 4:
            _n = max(len(keys) - 1, 1)
            _wd_max = max((v for _, v in wd_sorted), default=1) or 1
            _bs_max = max((v for _, v in bs_map.items()), default=1) or 1
            _wd_norm = {k: v / _wd_max for k, v in wd_sorted}
            _bs_norm = {k: v / _bs_max for k, v in bs_map.items()}
            _rank_diff = {k: _bs_norm[k] - _wd_norm[k] for k in keys}

            _biggest_broker = max(keys, key=lambda k: _rank_diff[k])
            _biggest_active = min(keys, key=lambda k: _rank_diff[k])

            if abs(_rank_diff[_biggest_broker]) >= 0.05 or abs(_rank_diff[_biggest_active]) >= 0.05:
                if _rank_diff[_biggest_broker] >= 0.05:
                    st.markdown(
f"""
##### Størst forskel mellem samarbejdsomfang og brobyggerrolle (normaliseret)
**{_biggest_broker}** har en relativt høj brobyggerrolle sammenlignet med sit samlede samarbejdsomfang — 
enheden fungerer som forbindelsesled på tværs af grupper, uden nødvendigvis at være den mest aktive samarbejdspartner."""
                        )
                if _rank_diff[_biggest_active] <= -0.05:
                    st.markdown(
f"""**{_biggest_active}** har et højt samlet samarbejdsomfang, med en relativt lav brobyggerrolle — 
enheden samarbejder meget, men primært inden for sin egen gruppe."""
                        )
            else:
                st.markdown(
"""De to mål følger hinanden tæt — der er ingen enheder, der skiller sig markant ud ved at 
have en væsentligt højere brobyggerrolle end samarbejdsomfang eller omvendt.""")

        # Full table
        _rows = []
        _has_norm = bs_map and len(keys) >= 4
        for k in keys:
            row = {label_key: k, "Samlet samarbejdsomfang": float(wd_map.get(k, 0)),
                   "Brobyggerrolle": float(bs_map.get(k, 0))}
            if extra_col and extra_map:
                row[extra_col] = extra_map.get(k, "")
            if _has_norm:
                row["Samarbejdsomfang (normaliseret)"] = round(_wd_norm.get(k, 0), 3)
                row["Brobyggerrolle (normaliseret)"]   = round(_bs_norm.get(k, 0), 3)
                row["Brobygger vs. aktivitet (+ = mere brobygger)"] = round(_rank_diff.get(k, 0), 3)
            _rows.append(row)
        _schema = ([(label_key, pa.string())] +
                   ([(extra_col, pa.string())] if extra_col else []) +
                   [("Samlet samarbejdsomfang", pa.float64()), ("Brobyggerrolle", pa.float64())] +
                   ([("Samarbejdsomfang (normaliseret)", pa.float64()),
                     ("Brobyggerrolle (normaliseret)",   pa.float64()),
                     ("Brobygger vs. aktivitet (+ = mere brobygger)", pa.float64())] if _has_norm else []))
        with st.expander(f"Se tabel"):
            st.dataframe(build_table(_rows, _schema), width="stretch", hide_index=True)
            st.download_button("Download (.xlsx)",
                               data=rows_to_excel_bytes(_rows, [n for n, _ in _schema]),
                               file_name=f"centralitet_{label_key.lower()}_{year}_{mode}.xlsx",
                               mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)

    _available = []
    if faculty_wd_sorted:
        _available.append("Fakulteter")
    if inst_wd_sorted:
        _available.append("Institutter")
    if grp_wd_sorted:
        _available.append("Stillingsgrupper (aggregeret)")
    if grp_node_wd_sorted:
        _available.append("Stillingsgrupper (node-niveau)")

    if not _available:
        st.error("Ingen centralitetsdata tilgængelig for det valgte udsnit.")
        return

    _state_key = f"cent_level_filter_{mode}"
    if _state_key not in st.session_state:
        st.session_state[_state_key] = _available
    # Behold kun valg der stadig er tilgængelige i denne mode
    _default = [v for v in st.session_state[_state_key] if v in _available] or _available
    _selected = st.multiselect(
        "Vis nøgleaktører for:",
        options=_available,
        default=_default,
        key=f"cent_level_filter_{year}_{mode}",
        on_change=lambda: st.session_state.update(
            {_state_key: st.session_state[f"cent_level_filter_{year}_{mode}"]}
        ),
    )

    if not _selected:
        st.error("Vælg mindst ét niveau ovenfor.")
        return

    _inst_to_fac = {m.get("inst"): m.get("fac") for m in node_meta.values() if m.get("inst")}

    # Byg institut-farvekort samme metode som render_tab_institutter
    _inst_color_map = {}
    if faculty_base_colors:
        _insts_by_fac: dict[str, list] = {}
        for m in node_meta.values():
            if m.get("inst") and m.get("fac"):
                _insts_by_fac.setdefault(m["fac"], [])
                if m["inst"] not in _insts_by_fac[m["fac"]]:
                    _insts_by_fac[m["fac"]].append(m["inst"])
        for fac, insts in _insts_by_fac.items():
            insts_sorted = sorted(insts)
            k = max(1, len(insts_sorted))
            base = faculty_base_colors.get(fac, "#122947")
            for rank, inst in enumerate(insts_sorted):
                t = rank / max(1, k - 1)
                lf = 0.5 + 1.8 * t
                sf = 1.3 - 0.7 * t
                _inst_color_map[inst] = adjust_color(base, lf, sf)

    if "Fakulteter" in _selected:
        _cent_charts("Fakulteter", faculty_wd_sorted, faculty_bs_sorted,
                     color_map=faculty_base_colors)

    if "Institutter" in _selected:
        _cent_charts("Institutter", inst_wd_sorted, inst_bs_sorted,
                     extra_col="Fakultet", extra_map=_inst_to_fac,
                     color_map=_inst_color_map)

    if "Stillingsgrupper (aggregeret)" in _selected:
        _cent_charts("Stillingsgrupper", grp_wd_sorted, grp_bs_sorted,
                     color_map=grp_colors)

    if "Stillingsgrupper (node-niveau)" in _selected:
        _cent_charts("Stillingsgrupper (node-niveau)",
                     grp_node_wd_sorted, grp_node_bs_sorted or [],
                     color_map={k: "#901a1e" for k, _ in (grp_node_wd_sorted or [])})

def compute_modularity_pre_for_key(snap: dict, comm_key: str) -> float:
    """Genberegn foruddefineret modularitet for et snapshot med en given comm_key."""
    import networkx as nx
    nodes_snap = snap.get("nodes_keep", [])
    edges_snap = snap.get("edges_keep", [])
    node_meta_snap = snap.get("node_meta", {})
    if not nodes_snap or not edges_snap:
        return float("nan")
    G = nx.Graph()
    for u in nodes_snap: G.add_node(u)
    for u, v, w, *_ in edges_snap: G.add_edge(u, v, weight=w)
    _connected = {n for n in G.nodes() if G.degree(n) > 0}
    G_conn = G.subgraph(_connected).copy()
    comms = {}
    for nid in nodes_snap:
        gl = node_meta_snap.get(nid, {}).get(comm_key, "")
        if gl:
            comms.setdefault(gl, []).append(nid)
    filtered = [[n for n in c if n in _connected] for c in comms.values()]
    filtered = [c for c in filtered if c]
    if not filtered or G_conn.number_of_edges() == 0:
        return float("nan")
    if any(len(c) == 1 for c in filtered):
        return float("nan")
    try:
        val = modq(G_conn, filtered, weight="weight")
        return val if val >= 0 else float("nan")
    except Exception:
        return float("nan")

def render_tab_netvaerksstruktur(year, mode, density, modularity_pre, modularity_greedy,
                                  n_comms, communities_dict, greedy_comms, comm_key,
                                  edges_keep=None, node_meta=None, all_years_data=None):
    abbrs = {"fac": "fakulteter", "inst": "institutter", "grp": "stillingsgrupper"}
    abbrs_singular = {"fac": "fakultet", "inst": "institut", "grp": "stillingsgruppe"}

     # Klyngeniveau-vælger
    _available_keys = [
        k for k, active in [
            ("fac",  "F" in mode),
            ("inst", "I" in mode),
            ("grp",  "G" in mode),
        ] if active
    ]

    st.subheader("Samarbejdsmønstre")

    st.markdown(
f"""
**Netværkstæthed** angiver andelen af mulige forbindelser, der faktisk
forekommer i netværket - inkl. isolerede noder (enheder uden sampubliceringer). 
En høj tæthed betyder, at mange grupper samarbejder med hinanden,
men en lav tæthed afspejler et mere spredt og opdelt samarbejdsmønster. 

Vær opmærksom på valg af filtre og af intra-/interkanter.
""")

    st.metric("Netværkstæthed", f"{density:.3f}")

    st.markdown(
f"""
**Modularitet** beskriver, i hvilken grad et netværk er opdelt i adskilte klynger 
sammenlignet med, hvad man vil forvente tilfældigt. Modulariteten beregnes kun på baggrund af 
enheder, der faktisk sampublicerer - isolerede noder indgår ikke.

- Et netværk med høj modularitet (typisk over 0,3) er karakteriseret ved tydeligt
adskilte grupper (klynger).
- Lav modularitet (under 0,3) indikerer et mere sammenhængede netværk.
""")

    if len(_available_keys) > 1:
        _key_labels = {"fac": "Fakultet", "inst": "Institut", "grp": "Stillingsgruppe"}
        comm_key = st.radio(
            "Foruddefinerede klynger baseres på:",
            options=_available_keys,
            format_func=lambda k: _key_labels[k],
            horizontal=True,
            index=_available_keys.index(comm_key) if comm_key in _available_keys else 0,
            key=f"comm_key_radio_{year}_{mode}",
        )
        # Genberegn communities_dict med det valgte niveau
        communities_dict = {}
        for nid, m in (node_meta or {}).items():
            gl = m.get(comm_key, "")
            if gl:
                communities_dict.setdefault(gl, []).append(nid)
        # Genberegn modularity_pre med valgt comm_key
        _G2_recompute = nx.Graph()
        for nid in (node_meta or {}):
            _G2_recompute.add_node(nid)
        for u, v, w, *_ in (edges_keep or []):
            _G2_recompute.add_edge(u, v, weight=w)
        modularity_pre = _compute_mod_pre(
            _G2_recompute,
            list(_G2_recompute.nodes()),
            node_meta or {},
            comm_key,
        )

    colA, colB, colC = st.columns(3)
    with colA:
        if mode in ("I", "F", "G"):
            st.metric("Modularitet (foruddefinerede klynger)", "n/a",
            help = "Modularitet er kun meningsfuldt, når der er flere noder per klynge")
        else:
            st.metric("Modularitet (foruddefinerede klynger)",
                    f"{modularity_pre:.3f}" if not np.isnan(modularity_pre) else "n/a",
                    help=f"Klyngeniveau: {len(communities_dict)} {abbrs.get(comm_key)}")
    with colB:
        if mode in ("I", "F", "G"):
            st.metric("Modularitet (greedy)", "n/a",
            help = "Modularitet er kun meningsfuldt, når der er flere noder per klynge")
        else:
            st.metric("Modularitet (greedy)",
                    f"{modularity_greedy:.3f}" if not np.isnan(modularity_greedy) else "n/a")
    with colC:
        if mode in ("I", "F", "G"):
            st.metric("Antal greedy-klynger", "n/a",
            help = "Modularitet er kun meningsfuldt, når der er flere noder per klynge")
        else:
            st.metric("Antal greedy-klynger", n_comms)


    greedy_comms_labeled = [
            sorted([
                " | ".join(p for p in (node_meta.get(nid, {}).get("fac", ""),
                                        node_meta.get(nid, {}).get("inst", ""),
                                        node_meta.get(nid, {}).get("grp", "")) if p)
                for nid in comm        
            ])
            for comm in greedy_comms
        ]

    greedy_rows = [{
        "Klynge (Greedy)": f"Greedy {i}", 
        "Antal noder": len(c),
        "Noder": ", ".join(c), 
        "Type": "greedy"}
                   for i, c in enumerate(greedy_comms_labeled, 1)]
    
    schema = [
        ("Klynge (Greedy)", pa.string()),
        ("Antal noder", pa.int64()),
        ("Noder", pa.string()),
        ("Type", pa.string())
    ]

    # Node-count bar per cluster
    st.markdown(
"""
##### Antal noder per klynge

For at beregne modulariteten opdeles noderne på to forskellige måder: 

- **Foruddefinerede klynger** kan enten være fakulteter, institutter eller stillingsgrupper - 
baseret på valgte enheder i sidepanelet. Hvis både fakulteter og institutter er
valgte, består fakultets-klyngerne af deres respektive institutter. 

- **Greedy-klynger** er baseret på Greedy-algoritmen, som automatisk finder de grupper 
af forfattere, der hænger stærkest sammen i netværket. Algoritmen samler først
noderne i små klynger og slår dem gradvist sammen, så den endelige struktur giver den 
højest mulige modularitet ([Newman 2004](https://journals.aps.org/pre/abstract/10.1103/PhysRevE.70.066111)). 

""")
    _tab_pre, _tab_greedy = st.tabs(["Foruddefinerede klynger", "Greedy-klynger"])

    with _tab_pre:
        _comm_sizes = {g: len(m) for g, m in sorted(communities_dict.items(), key=lambda x: x[0])}
        _fig_ns = go.Figure(go.Bar(
            x=list(_comm_sizes.keys()), y=list(_comm_sizes.values()),
            marker_color="#122947",
            text=list(_comm_sizes.values()), textposition="inside",
        ))
        _fig_ns.update_layout(
            xaxis_title="Klynger", yaxis_title="Antal noder",
            height=320, margin=dict(t=30),
        )
        st.plotly_chart(_fig_ns, width='stretch')

        with st.expander("Se klyngetabel med prædefinerede klynger"):
            schema = [("Klynge (foruddefineret)", pa.string()), ("Antal noder", pa.int64()), ("Type", pa.string())]
            table_rows = [
                {"Klynge (foruddefineret)": g, "Antal noder": len(m), "Type": comm_key}
                for g, m in sorted(communities_dict.items(), key=lambda x: x[0])
                ]

            st.dataframe(build_table(table_rows, schema), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)", 
                data=rows_to_excel_bytes(table_rows, [n for n, _ in schema]),
                file_name=f"klynger_{mode}_{year}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)
    
    with _tab_greedy:
        if greedy_comms_labeled:
            _greedy_sizes = {f"Greedy {i}": len(c) for i, c in enumerate(greedy_comms_labeled, 1)}
            _fig_gs = go.Figure(go.Bar(
                x=list(_greedy_sizes.keys()), y=list(_greedy_sizes.values()),
                marker_color="#122947",
                text=list(_greedy_sizes.values()), textposition="inside",
            ))
            _fig_gs.update_layout(
                xaxis_title="Klynger", yaxis_title="Antal noder",
                height=320, margin=dict(t=30),
            )
            st.plotly_chart(_fig_gs, width='stretch')
        else:
            st.error("Ingen greedy-klynger tilgængelige.")
        
        with st.expander("Se klyngetabel med greedy algoritme"):
            st.dataframe(build_table(greedy_rows, schema), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)", 
                data=rows_to_excel_bytes(greedy_rows, [n for n, _ in schema]),
                file_name=f"greedy_klynger_{mode}_{year}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",)

    # ── Modularitet og tæthed over tid ───────────────────────────────────────
    if all_years_data and len(all_years_data) >= 2:
        st.markdown(
"""##### Sammenlign år - modularitet og netværkstæthed
I figuren nedenfor vises netværkstætheden samt modulariteterne. År, hvor 
modulariteten er angivet som n/a, er udeladt fra visualiseringen.

Vær opmærksom på, hvilke filtre der er valgt i sidepanelet, da de både påvirker 
beregninger og visning.
""")

        years_sorted = sorted(all_years_data.keys())

        _density_vals  = [all_years_data[y].get("density",           float("nan")) for y in years_sorted]
        _mod_pre_key = f"modularity_pre_{comm_key}"
        _mod_pre_vals = [all_years_data[y].get(_mod_pre_key, float("nan")) for y in years_sorted]
        _mod_gr_vals   = [all_years_data[y].get("modularity_greedy", float("nan")) for y in years_sorted]

        def _clean(lst):
            return [None if (v is None or (isinstance(v, float) and math.isnan(v))) else v for v in lst]

        # Filtrér år væk hvor alle tre værdier er None/nan
        _yr_plot = years_sorted

        def _filter(lst):
            return _clean(lst)

        _fig_mt = go.Figure()
        _fig_mt.add_trace(go.Scatter(
            x=_yr_plot, y=_filter(_density_vals),
            name="Netværkstæthed",
            mode="lines+markers+text",
            text=[f"{v:.3f}" if v is not None else "" for v in _filter(_density_vals)],
            textposition="top center",
            line=dict(color="#122947", width=2),
            marker=dict(size=8),
        ))
        _fig_mt.add_trace(go.Scatter(
            x=_yr_plot, y=_filter(_mod_pre_vals),
            name="Modularitet (foruddefinerede)",
            mode="lines+markers+text",
            text=[f"{v:.3f}" if v is not None else "" for v in _filter(_mod_pre_vals)],
            textposition="top center",
            line=dict(color="#4a7ca8", width=2, dash="dash"),
            marker=dict(size=8),
        ))
        _fig_mt.add_trace(go.Scatter(
            x=_yr_plot, y=_filter(_mod_gr_vals),
            name="Modularitet (greedy)",
            mode="lines+markers+text",
            text=[f"{v:.3f}" if v is not None else "" for v in _filter(_mod_gr_vals)],
            textposition="bottom center",
            line=dict(color="#39641c", width=2, dash="dot"),
            marker=dict(size=8),
        ))
        _fig_mt.update_layout(
            xaxis=dict(tickmode="array", tickvals=_yr_plot, dtick=1),
            yaxis=dict(title="Værdi (0–1)", rangemode="tozero"),
            legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="left", x=0),
            height=420,
            margin=dict(t=20, b=80),
        )
        st.plotly_chart(_fig_mt, width='stretch')

        with st.expander("Se tabel", expanded = False):
            # Download table
            _yr_rows = [
                {
                    "År": y,
                    "Netværkstæthed": round(all_years_data[y].get("density", float("nan")), 4),
                    "Modularitet (foruddefinerede)": round(all_years_data[y].get("modularity_pre", float("nan")), 4),
                    "Modularitet (greedy)": round(all_years_data[y].get("modularity_greedy", float("nan")), 4),
                }
                for y in years_sorted
            ]
            _yr_schema = [
                ("År", pa.int64()),
                ("Netværkstæthed",                pa.float64()),
                ("Modularitet (foruddefinerede)",  pa.float64()),
                ("Modularitet (greedy)",           pa.float64()),
            ]
            st.dataframe(build_table(_yr_rows, _yr_schema), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)",
                data=rows_to_excel_bytes(_yr_rows, [n for n, _ in _yr_schema]),
                file_name=f"netvaerksmetrik_over_tid_{mode}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    HEATMAP_MAX = 10

    # ── Heatmap: kantvægt mellem klynger ────────────────────────────────────
    st.markdown("#### Sampubliceringsstyrke mellem klyngerne")
    st.markdown(
f"""
Heatmappet viser det samlede antal forfatterpar mellem {abbrs.get(comm_key, comm_key)}.
Diagonalen angiver interne forfatterpar inden for samme {abbrs_singular.get(comm_key, comm_key)}.
""")
    _units = sorted(communities_dict.keys())
    if len(_units) >= 2 and edges_keep and node_meta:
        # Map each node → its cluster label via comm_key
        _node_to_unit = {
            nid: m.get(comm_key, "")
            for nid, m in node_meta.items()
            if m.get(comm_key, "") in communities_dict.keys()
        }
        # Accumulate edge weight into unit×unit matrix (symmetric)
        _mat = {(a, b): 0.0 for a in _units for b in _units}
        for u, v, w, *_ in edges_keep:
            gu = _node_to_unit.get(u, "")
            gv = _node_to_unit.get(v, "")
            if gu and gv:
                _mat[(gu, gv)] = _mat.get((gu, gv), 0.0) + w
                if gu != gv:
                    _mat[(gv, gu)] = _mat.get((gv, gu), 0.0) + w

        _z = [[_mat.get((a, b), 0.0) for b in _units] for a in _units]

        if len(_units) > HEATMAP_MAX:
            _unit_totals = sorted(
                _units,
                key=lambda a: sum(_mat.get((a, b), 0) for b in _units),
                reverse=True
            )
            _default_units = _unit_totals[:HEATMAP_MAX]
            _selected_units = st.multiselect(
                f"For mange enheder til heatmap ({len(_units)}). Vælg hvilke der vises (maks {HEATMAP_MAX}):",
                options=_units,
                default=_default_units,
                key=f"heatmap_filter_{year}_{mode}",
            )
            _units_plot = _selected_units if _selected_units else _default_units
        else:
            _units_plot = _units

        if _units_plot:
            _z_plot = [[_mat.get((a, b), 0.0) for b in _units_plot] for a in _units_plot]
            _fig_heat = go.Figure(go.Heatmap(
                z=_z_plot, x=_units_plot, y=_units_plot,
                colorscale=[[0, "#f0f4f8"], [1, "#122947"]],
                text=[[f"{_mat.get((a,b),0):.0f}" for b in _units_plot] for a in _units_plot],
                texttemplate="%{text}",
                hovertemplate="%{y} → %{x}: %{z:.0f}<extra></extra>",
                showscale=True,
                colorbar=dict(title="Antal forfatterpar"),
            ))
            _fig_heat.update_layout(
                xaxis_title=abbrs.get(comm_key, comm_key).capitalize(),
                yaxis_title=abbrs.get(comm_key, comm_key).capitalize(),
                yaxis=dict(showgrid=False),
                height=max(380, 60 * len(_units_plot)),
                margin=dict(l=140, b=140, t=10),
            )
            
            st.plotly_chart(_fig_heat, width='stretch')

        _heat_rows = [
                {"Fra": a, "Til": b, "Antal forfatterpar": int(_mat.get((a, b), 0))}
                for a in _units for b in _units
                if a != b and _mat.get((a, b), 0) > 0
            ]
        _heat_schema = [("Fra", pa.string()), ("Til", pa.string()), ("Antal forfatterpar", pa.int64())]
        
        with st.expander("Se tabel"):
            st.dataframe(build_table(_heat_rows, _heat_schema), hide_index=True, width="stretch")
            st.download_button(
                    "Download (.xlsx)",
                    data=rows_to_excel_bytes(_heat_rows, ["Fra", "Til", "Antal forfatterpar"]),
                    file_name=f"forfatterpar_matrix_{mode}_{year}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

    # ── 3. Isolerede enheder over tid ─────────────────────────────────────────
    if all_years_data and len(all_years_data) >= 2:
        st.markdown("#### Er de samme enheder altid isolerede?")
        st.markdown(
f"""
Enheder, der konsekvent er isolerede (ingen sampubliceringer i det valgte udsnit) 
kan pege på strukturelle barrierer. Enheder, der kun lejlighedsvist er isolerede kan 
afspejle dataudfald eller midlertidige forhold - og derfor viser figuren kun ender, der har været
isolerede i **mindst to år**.
""")
        years_sorted = sorted(all_years_data.keys())

        # Collect all units ever isolated
        _all_isolated: dict[str, list] = {}
        for yr in years_sorted:
            for unit in all_years_data[yr].get("isolated_units", []):
                _all_isolated.setdefault(unit, []).append(yr)

        _persistent = {unit: yrs for unit, yrs in _all_isolated.items() if len(yrs) >= 2}

        if not _persistent:
            st.error("Ingen enheder har været isolerede i to eller flere år.")
        else:
            # Sort: most-frequently isolated first
            _iso_sorted = sorted(_persistent.items(), key=lambda x: -len(x[1]))

            _iso_tbl_rows = [
            {
                "Enhed":               unit,
                "Antal isolerede år":  int(len(yrs)),
                "Isoleret i år":       ", ".join(str(y) for y in sorted(yrs)),
                "Konsekvent isoleret": "Ja" if len(yrs) == len(years_sorted) else "Nej",
            }
            for unit, yrs in _iso_sorted
            ]
            _iso_schema = [
                ("Enhed",                pa.string()),
                ("Antal isolerede år",   pa.int64()),
                ("Isoleret i år",        pa.string()),
                ("Konsekvent isoleret",  pa.string()),
            ]

            # Presence/absence heatmap
            _iso_units = [r["Enhed"] for r in _iso_tbl_rows]
            _iso_z = [
                [1 if yr in _persistent[unit] else 0 for yr in years_sorted]
                for unit in _iso_units
            ]
            _fig_iso = go.Figure(go.Heatmap(
                z=_iso_z,
                x=[str(yr) for yr in years_sorted],
                y=_iso_units,
                colorscale=[[0, "#f0f4f8"], [1, "#7A131A"]],
                zmin=0, zmax=1,
                text=[["Isoleret" if v else "" for v in row] for row in _iso_z],
                texttemplate="%{text}",
                showscale=False,
                hovertemplate="%{y} - %{x}: %{text}<extra></extra>",
            ))
            _fig_iso.update_layout(
                xaxis_title="År",
                height=max(300, 28 * len(_iso_units)),
                margin=dict(l=220, t=20, r=20),
            )
            st.plotly_chart(_fig_iso, width='stretch')

            with st.expander("Se tabel"):
                st.dataframe(build_table(_iso_tbl_rows, _iso_schema), hide_index=True, width="stretch")
                st.download_button(
                    "Download (.xlsx)",
                    data=rows_to_excel_bytes(_iso_tbl_rows, [n for n, _ in _iso_schema]),
                    file_name=f"isolerede_enheder_over_tid_{mode}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
    
    
    _units = sorted(communities_dict.keys())
    if len(_units) >= 2 and edges_keep and node_meta:

        # ── Reciprocitetsgrad ─────────────────────────────────────────────
        st.markdown("#### Samarbejdssymmetri mellem klyngerne")
        st.markdown(
"""
Indikatoren viser graden af *gensidighed i samarbejdet* mellem to fakulteter. For hvert 
fakultetspar opgøres, hvor stor en andel af fakultet X's samlede samarbejde på tværs
af fakulteter, der retter sig mod fakultet Y - og omvendt. 
- En værdi tæt på 1 indikerer et gensidigt og symmetrisk samarbejde. 
- Værdier tæt på 0 viser et skævt forhold, hvor det ene fakultet dominerer samarbejdet. 
""")
        # For each unit: total edge weight going TO other units (inter-unit only)
        _inter_out: dict[str, float] = {u: 0.0 for u in _units}
        _pair_w: dict[tuple, float] = {}   # {(A,B): raw weight}  A < B always
        for eu, ev, ew, *_ in edges_keep:
            gu = _node_to_unit.get(eu, "")
            gv = _node_to_unit.get(ev, "")
            if not gu or not gv or gu == gv:
                continue
            _inter_out[gu] = _inter_out.get(gu, 0.0) + ew
            _inter_out[gv] = _inter_out.get(gv, 0.0) + ew
            key = (min(gu, gv), max(gu, gv))
            _pair_w[key] = _pair_w.get(key, 0.0) + ew

        # Compute symmetry per pair:
        # share_AB = w(A,B) / total_inter(A)
        # share_BA = w(A,B) / total_inter(B)   (symmetric graph → same raw weight)
        # symmetry = min(share_AB, share_BA) / max(share_AB, share_BA)
        _sym_rows = []
        for (a, b), w_ab in sorted(_pair_w.items(), key=lambda x: -x[1]):
            tot_a = _inter_out.get(a, 0.0)
            tot_b = _inter_out.get(b, 0.0)
            if tot_a == 0 or tot_b == 0:
                continue
            share_a = w_ab / tot_a   # fraction of A's inter-weight with B
            share_b = w_ab / tot_b   # fraction of B's inter-weight with A
            sym = min(share_a, share_b) / max(share_a, share_b)
            _sym_rows.append({
                "Par": f"{a} ↔ {b}",
                "A": a, "B": b,
                "Kantvægt": round(w_ab, 1),
                "A's andel til B (%)": round(100 * share_a, 1),
                "B's andel til A (%)": round(100 * share_b, 1),
                "Symmetriscore": round(sym, 3),
            })

        if _sym_rows:
            _sym_sorted_asc  = sorted(_sym_rows, key=lambda r:  r["Symmetriscore"])
            _sym_sorted_desc = sorted(_sym_rows, key=lambda r: -r["Symmetriscore"])
            _most_asym  = _sym_sorted_asc[0]
            _most_sym   = _sym_sorted_desc[0]

            # Find den enhed der bidrager mest asymmetrisk (laveste gennemsnit på tværs af par)
            _avg_sym_per_unit: dict[str, list] = {}
            for r in _sym_rows:
                for side in (r["A"], r["B"]):
                    _avg_sym_per_unit.setdefault(side, []).append(r["Symmetriscore"])
            _unit_avg = {u: round(sum(v) / len(v), 3) for u, v in _avg_sym_per_unit.items()}
            _least_sym_unit = min(_unit_avg, key=_unit_avg.get)
            _most_sym_unit  = max(_unit_avg, key=_unit_avg.get)

            _andel_a = _most_asym["A's andel til B (%)"]
            _andel_b = _most_asym["B's andel til A (%)"]

            #st.markdown(
#f"""
#Det **mest gensidige** samarbejdspar er **{_most_sym['A']} ↔ {_most_sym['B']}** med en symmetriscore 
#på **{_most_sym['Symmetriscore']:.2f}** (kantvægt: {int(_most_sym['Kantvægt'])}). 
#Det **mest asymmetriske** par er **{_most_asym['A']} ↔ {_most_asym['B']}** 
#(score: **{_most_asym['Symmetriscore']:.2f}**), hvor **{_most_asym['A']}** retter **{_andel_a:.0f}%** 
#af sit inter-samarbejde mod **{_most_asym['B']}**, mens **{_most_asym['B']}** kun retter 
#**{_andel_b:.0f}%** mod **{_most_asym['A']}**. På tværs af alle par er **{_most_sym_unit}** 
#gennemsnitligt den mest gensidige enhed (gns. score: {_unit_avg[_most_sym_unit]:.2f}), 
#mens **{_least_sym_unit}** indgår i de mest skæve samarbejder (gns. score: {_unit_avg[_least_sym_unit]:.2f}).
#""")

            if len(_units) > HEATMAP_MAX:
                _unit_totals_sym = sorted(
                    _units,
                    key=lambda a: sum(_pair_w.get((min(a,b), max(a,b)), 0) for b in _units),
                    reverse=True
                )
                _default_units_sym = _unit_totals_sym[:HEATMAP_MAX]
                _selected_units_sym = st.multiselect(
                    f"For mange enheder til heatmap ({len(_units)}). Vælg hvilke der vises (maks {HEATMAP_MAX}):",
                    options=_units,
                    default=_default_units_sym,
                    key=f"heatmap_sym_filter_{year}_{mode}",
                )
                _units_sym = _selected_units_sym if _selected_units_sym else _default_units_sym
            else:
                _units_sym = _units

            if _units_sym:
                # Symmetry heatmap
                _sym_mat = {(a, b): 0.0 for a in _units_sym for b in _units_sym}
                for r in _sym_rows:
                    if r["A"] in _units_sym and r["B"] in _units_sym:
                        _sym_mat[(r["A"], r["B"])] = r["Symmetriscore"]
                        _sym_mat[(r["B"], r["A"])] = r["Symmetriscore"]
                _sz = [[_sym_mat.get((a, b), float("nan")) for b in _units_sym] for a in _units_sym]
                for i in range(len(_units_sym)):
                    _sz[i][i] = float("nan")

                _fig_sym = go.Figure(go.Heatmap(
                    z=_sz, x=_units_sym, y=_units_sym,
                    colorscale=[[0, "#f0f4f8"], [1, "#122947"]],
                    zmin=0, zmax=1,
                    text=[[f"{_sz[i][j]:.2f}" if not (isinstance(_sz[i][j], float) and math.isnan(_sz[i][j])) else ""
                        for j in range(len(_units_sym))] for i in range(len(_units_sym))],
                    texttemplate="%{text}",
                    hovertemplate="%{y} ↔ %{x}: symmetri %{z:.3f}<extra></extra>",
                    colorbar=dict(title="Symmetri (0–1)"),
                ))
                _fig_sym.update_layout(
                    xaxis_title=abbrs.get(comm_key, comm_key).capitalize(),
                yaxis_title=abbrs.get(comm_key, comm_key).capitalize(),
                    yaxis=dict(showgrid=False),
                    height=max(380, 60 * len(_units_sym)),
                    margin=dict(l=140, b=140, t=10),
                )
                st.plotly_chart(_fig_sym, width='stretch')
            
            _sym_schema = [
                    ("Par (A B)",              pa.string()),
                    ("Kantvægt",               pa.float64()),
                    ("A's andel til B (%)",    pa.float64()),
                    ("B's andel til A (%)",    pa.float64()),
                    ("Symmetriscore",          pa.float64()),
            ]
            with st.expander("Se tabel"):
                st.dataframe(
                    build_table(
                            [{"Par (A B)": r["Par"], "Kantvægt": r["Kantvægt"],
                            "A's andel til B (%)": r["A's andel til B (%)"],
                            "B's andel til A (%)": r["B's andel til A (%)"],
                            "Symmetriscore": r["Symmetriscore"]}
                            for r in sorted(_sym_rows, key=lambda x: -x["Symmetriscore"])],
                            _sym_schema,
                        ),
                        hide_index=True, width="stretch",
                    )
                st.download_button(
                    "Download (.xlsx)",
                    data=rows_to_excel_bytes(
                            [{"Par": r["Par"], "Kantvægt": r["Kantvægt"],
                            "A's andel til B (%)": r["A's andel til B (%)"],
                            "B's andel til A (%)": r["B's andel til A (%)"],
                            "Symmetriscore": r["Symmetriscore"]}
                            for r in _sym_rows],
                            [n for n, _ in _sym_schema],
                        ),
                        file_name=f"reciprocitet_{mode}_{year}.xlsx",
                        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )

    elif len(_units) < 2:
        st.error("For få klynger til at vise heatmap.")

    


def render_tab_datagrundlag(year, mode, all_groups, selected_facs, selected_insts, selected_grps,
                             forfatterantal=None, publikationstyper=None,
                             faculty_base_colors=None, years_sorted=None, pubtype_map=None):
    st.subheader("Datagrundlag")

    st.markdown(
f"""Netværk og de tilhørende opgørelser bygger på følgende datagrundlag:
- CURIS-publikationer for 2021–2025
    - Særligt 2025-data kan være ufuldstændige, da registrering 
    kan ske med forsinkelse
- VIP-forfattere matchet via HR-data

Analysen omfatter kun publikationer med mindst én KU-associeret forfatter. Instituttilknytning
er fastlagt ved hjælp af en struktureret liste over KU-institutter. Publikationer med forskellige
organisationstilhørsforhold tælles ligeligt med for alle relevante enheder. Generelt indgår
følgende publikationer ikke i opgørelserne: 

- Publikationer uden entydig organisationstilknytning
- Publikationer, der er registrerede uden forfattere
- Publikationer, hvor der kun er registreret én KU-forfatter

Publikationstyper er grupperet i overordnede kategorier, så beslægtede formater samles 
og kan sammenlignes på tværs af fakulteter.
""")

    yr_str   = str(year)
    fac_order = [f for f in FAC_ORDER if f in (forfatterantal or {}).get(yr_str, {})]
    _fa       = (forfatterantal or {}).get(yr_str, {})
    _fac_colors = [faculty_base_colors.get(f, "#122947") if faculty_base_colors else "#122947" for f in fac_order]

    # ── Forfatterantal ────────────────────────────────────────────────
    one_counts  = [_fa.get(f, {}).get("1",   0) for f in fac_order]
    many_counts = [_fa.get(f, {}).get("gt1", 0) for f in fac_order]

    ratio_per_fac = {f: one / (one + many) * 100
                     for f, one, many in zip(fac_order, one_counts, many_counts)
                     if (one + many) > 0}

    if ratio_per_fac:
        min_fac = min(ratio_per_fac, key=ratio_per_fac.get)
        max_fac = max(ratio_per_fac, key=ratio_per_fac.get)
        min_val, max_val = ratio_per_fac[min_fac], ratio_per_fac[max_fac]
        abs_one = _fa.get(max_fac, {}).get("1", 0)
    else:
        min_fac = max_fac = None
        min_val = max_val = abs_one = 0


    st.markdown(
f"""
##### Forfatterantal per publikation

Publikationspraksis varierer betydeligt på tværs af fakulteterne, bl.a.
i forhold til omfanget af co-forfatterskaber og udbredelsen af soloartikler. Det påvirker
både antallet af KU-forfattere per publikation og fordelingen mellem én og flere
KU-forfattere per publikation. 

Andelen af publikationer med én KU-forfatter varierer fra **{min_val:.1f}%** ({min_fac}) til
 **{max_val:.1f}%** ({max_fac}), svarende til **{abs_one}** frasorterede publikationer.
""")
    
    _tab_abs_fa, _tab_pct_fa, _tab_tid_fa = st.tabs(["Antal", "Andel (%)", "Udvikling over tid"])

    with _tab_abs_fa:
        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=fac_order, y=one_counts, name="1 KU-forfatter",
            marker_color=[adjust_color(c, 2.2, 0.3) for c in _fac_colors],
            text=[str(v) for v in one_counts], textposition="inside", textfont_color="white",
        ))
        fig.add_trace(go.Bar(
            x=fac_order, y=many_counts, name=">1 KU-forfatter",
            marker_color=_fac_colors,
            text=[str(v) for v in many_counts], textposition="inside", textfont_color="white",
        ))
        fig.update_layout(barmode="stack", yaxis_title="Antal publikationer",
                          legend_title="KU-forfattere", height=380, margin=dict(t=20))
        st.plotly_chart(fig, width="stretch", key=f"dg_forfatter_abs_{year}_{mode}")

    with _tab_pct_fa:
        _pct_one  = [ratio_per_fac.get(f, 0) for f in fac_order]
        _pct_many = [round(100 - v, 1) for v in _pct_one]
        fig_pct = go.Figure()
        fig_pct.add_trace(go.Bar(
            x=fac_order, y=_pct_one, name="1 KU-forfatter",
            marker_color=[adjust_color(c, 2.2, 0.3) for c in _fac_colors],
            text=[f"{v:.1f}%" for v in _pct_one], textposition="inside", textfont_color="white",
        ))
        fig_pct.add_trace(go.Bar(
            x=fac_order, y=_pct_many, name=">1 KU-forfatter",
            marker_color=_fac_colors,
            text=[f"{v:.1f}%" for v in _pct_many], textposition="inside", textfont_color="white",
        ))
        fig_pct.update_layout(barmode="stack", yaxis=dict(title="Andel (%)", range=[0, 100]),
                               legend_title="KU-forfattere", height=380, margin=dict(t=20))
        st.plotly_chart(fig_pct, width="stretch", key=f"dg_forfatter_pct_{year}_{mode}")

    with _tab_tid_fa:
        if forfatterantal and years_sorted:
            fig_tid = go.Figure()
            for f, col in zip(fac_order, _fac_colors):
                _y_vals = []
                for yr in years_sorted:
                    _d = forfatterantal.get(str(yr), {}).get(f, {})
                    _one, _many = _d.get("1", 0), _d.get("gt1", 0)
                    _tot = _one + _many
                    _y_vals.append(round(_one / _tot * 100, 1) if _tot else 0)
                fig_tid.add_trace(go.Scatter(
                    x=years_sorted, y=_y_vals, name=f,
                    mode="lines+markers+text",
                    text=[f"{v:.1f}%" for v in _y_vals],
                    textposition="top center",
                    line=dict(color=col, width=2), marker=dict(size=7),
                ))
            fig_tid.update_layout(
                yaxis=dict(title="Andel med 1 KU-forfatter (%)", range=[0, 100]),
                xaxis=dict(tickmode="array", tickvals=years_sorted, dtick=1),
                height=420, margin=dict(t=20, b=120),
                legend_title="Fakulteter"
            )
            st.plotly_chart(fig_tid, width="stretch", key=f"dg_forfatter_tid_{year}_{mode}")
        else:
            st.error("Tidsdata ikke tilgængelig.")
    
    # ── Publikationstyper ─────────────────────────────────────────────
    _pt          = (publikationstyper or {}).get(yr_str, {})
    fac_order_pt  = [f for f in FAC_ORDER if f in _pt]
    all_pub_types = sorted(
        {t for fd in _pt.values() for t in fd},
        key=lambda t: -sum(_pt.get(f, {}).get(t, 0) for f in fac_order_pt)
    )
    _pt_colors    = ku_color_sequence(len(all_pub_types))

    st.markdown(
f"""##### Publikationstyper per fakultet
Publikationstyperne viser tilsvarende forskelle mellem fakulteterne. For at få et mere
sammenligneligt billede er CURIS-publikationsformaterne lagt sammen i kollapsede kategorier, 
f.eks. samles konferenceformater i én fælles kategori. Tallene angiver antallet af
publikationer, ikke vægtede mål. 

""")
    # Kategoriinddeling
    _tab_abs_pt, _tab_pct_pt = st.tabs(["Antal", "Andel (%)"])

    with _tab_abs_pt:
        fig2 = go.Figure()
        for i, t in enumerate(all_pub_types):
            _vals = [_pt.get(f, {}).get(t, 0) for f in fac_order_pt]
            fig2.add_trace(go.Bar(
                x=fac_order_pt, y=_vals, name=t,
                text=[str(v) if v > 0 else "" for v in _vals],
                textposition="inside",
                marker_color=_pt_colors[-(i+1)],
            ))
        fig2.update_layout(barmode="stack", yaxis_title="Antal publikationer",
                           legend_title="Kategori", height=500, margin=dict(t=20),
                           legend=dict(traceorder="reversed"))
        st.plotly_chart(fig2, width="stretch", key=f"dg_pubtype_abs_{year}_{mode}")

    with _tab_pct_pt:
        fig3 = go.Figure()
        for i, t in enumerate(all_pub_types):
            _vals_pct = []
            for f in fac_order_pt:
                _tot = sum(_pt.get(f, {}).values()) or 1
                _vals_pct.append(round(_pt.get(f, {}).get(t, 0) / _tot * 100, 1))
            fig3.add_trace(go.Bar(
                x=fac_order_pt, y=_vals_pct, name=t,
                text=[f"{v:.1f}%" if v > 0 else "" for v in _vals_pct],
                textposition="inside",
                marker_color=_pt_colors[-(i+1)],
            ))
        fig3.update_layout(barmode="stack",
                           yaxis=dict(title="Andel (%)", range=[0, 100]),
                           legend_title="Type", height=500, margin=dict(t=20),
                           legend=dict(traceorder="reversed"))
        st.plotly_chart(fig3, width="stretch", key=f"dg_pubtype_pct_{year}_{mode}")

    if pubtype_map:
        _map_rows = sorted(set((raw, mapped) for raw, mapped in pubtype_map.items()))
        with st.expander("Se kategoriinddeling"):
            _map_schema = [("CURIS-type", pa.string()), ("Kategori", pa.string())]
            _map_data   = [{"CURIS-type": r, "Kategori": m} for r, m in _map_rows]
            st.dataframe(build_table(_map_data, _map_schema), hide_index=True, width="stretch")

# ---------------------------------------------------------------------------


# ===========================================================================
# KØN TAB
# ===========================================================================

def render_tab_køn(year, mode, raw_nodes, raw_edges, node_meta,
                   selected_facs, selected_insts, selected_grps,
                   all_years_data = None, edges_keep=None,
                   forfatterpositioner = None
                   ):
    st.subheader("Kønsfordeling på KU")
    st.markdown(
f"""
Fanen viser kønsfordelingen i forfatterbidragene og sampubliceringer. Opgørelserne bygger på HR-datagrundlaget, hvor CPR-nummerets
slutciffer bestemmer kategorien (lige = kvinde, ulige = mand). Visualiseringerne nedenfor givet et overblik over fordeling
på tværs af enheder samt kønskombinationer i publikationssamarbejder.

Opgørelserne nedenfor bygger på **alle {len(raw_edges)} sampubliceringer** - 
og er dermed uafhængig af den aktuelle netværksvisning, som kun viser kanter *mellem* 
de valgte organisatoriske enheder. 
""")

    # Determine grouping key based on mode
    if mode in ("FS", "F", "FI", "FG", "FIG"):
        group_key = "fac"
        group_label = "Fakultet"
    elif mode in ("IS", "I", "IG"):
        group_key = "inst"
        group_label = "Institut"
    else:
        group_key = "grp"
        group_label = "Stillingsgruppe"

    # ── Forfatterbidrag fordelt på køn per org-enhed ──────────────────────────
    st.markdown(
f"""##### Forfatterbidrag fordelt på køn i {year}

""")

    sex_size: dict[str, dict[str, int]] = {}   # {group: {sex: size}}
    for nid, m in raw_nodes.items():
        if m.get("type") != "grp":
            continue
        grp_val = m.get(group_key, "")
        sex     = m.get("sex", "ukendt")
        size    = m.get("size", 0)
        if not grp_val:
            continue
        # Filtrer på valgte organisatoriske enheder
        if selected_facs and m.get("fac") not in selected_facs:
            continue
        if selected_insts and m.get("inst") not in selected_insts:
            continue
        if selected_grps and m.get("grp") not in selected_grps:
            continue
        sex_size.setdefault(grp_val, {})
        sex_size[grp_val][sex] = sex_size[grp_val].get(sex, 0) + size

    groups_sorted = sorted(sex_size.keys())
    sexes         = sorted({s for d in sex_size.values() for s in d})

    sex_display = {"m": "Mænd", "k": "Kvinder"}
    sex_colors  = {"m": "#425570", "k": "#901a1E"}

    # Tabs for absolute vs normalised view
    _tab_abs, _tab_pct = st.tabs(["Antal", "Andel (%)"])

    with _tab_abs:
        fig1 = go.Figure()
        for sex in sexes:
            fig1.add_trace(go.Bar(
                name=sex_display.get(sex, sex),
                y=groups_sorted,
                x=[sex_size[g].get(sex, 0) for g in groups_sorted],
                orientation="h",
                marker_color=sex_colors.get(sex, "#aaaaaa"),
                text=[sex_size[g].get(sex, 0) for g in groups_sorted],
                textposition="inside",
            ))
        fig1.update_layout(
            barmode="stack",
            yaxis=dict(title=group_label, autorange="reversed"),
            xaxis_title="Forfatterbidrag",
            legend_title="Køn",
            height=max(300, 35 * len(groups_sorted)),
            bargap=0.15,
            bargroupgap=0.05,
            margin=dict(l=200, r=40, t=20, b=20),
        )
        st.plotly_chart(fig1, width='stretch')

    with _tab_pct:
        fig1p = go.Figure()
        for sex in sexes:
            _pct_vals = [
                round(100 * sex_size[g].get(sex, 0) / (sum(sex_size[g].values()) or 1), 1)
                for g in groups_sorted
            ]
            fig1p.add_trace(go.Bar(
                name=sex_display.get(sex, sex),
                y=groups_sorted,
                x=_pct_vals,
                orientation="h",
                marker_color=sex_colors.get(sex, "#aaaaaa"),
                text=[f"{v}%" for v in _pct_vals],
                textposition="inside",
            ))
        fig1p.update_layout(
            barmode="stack",
            xaxis_title="Andel (%)",
            xaxis_range=[0, 100],
            legend_title="Køn",
            height=max(300, 40 * len(groups_sorted)),
        )
        st.plotly_chart(fig1p, width='stretch')

    # Table
    rows = []
    for g in groups_sorted:
        row = {group_label: g}
        total = sum(sex_size[g].values())
        for sex in sexes:
            n = sex_size[g].get(sex, 0)
            row[sex_display.get(sex, sex)] = n
            row[f"{sex_display.get(sex, sex)} (%)"] = round(100 * n / total, 1) if total else 0.0
        row["Total"] = total
        rows.append(row)

    if rows:
        schema_fields = (
            [(group_label, pa.string())] +
            [(sex_display.get(s, s), pa.int64()) for s in sexes] +
            [(f"{sex_display.get(s, s)} (%)", pa.float64()) for s in sexes] +
            [("Total", pa.int64())]
        )
        with st.expander("Se tabel"):
            st.dataframe(build_table(rows, schema_fields), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)",
                data=rows_to_excel_bytes(rows, [n for n, _ in schema_fields]),
                file_name=f"kønsfordeling_{year}_{mode}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    if forfatterpositioner:
        st.markdown("#### Forfatterpositioner fordelt på køn")
        yr_data = forfatterpositioner.get(str(year), {})

        # Farver: mørk = kvinder, lys = mænd
        pos_labels = [("first", "Førsteforfatter"), ("middle", "Mellemforfatter"), ("last", "Sidsteforfatter")]
        sex_keys   = [("K", "Kvinder"), ("M", "Mænd")]
        # Tre nuancer per køn: mørk → mellem → lys
        pos_sex_colors = {
            ("first",  "K"): "#901a1E", ("middle", "K"): "#c45c61", ("last", "K"): "#e8a8aa",
            ("first",  "M"): "#122947", ("middle", "M"): "#425570", ("last", "M"): "#7a9bbf",
        }

        all_grps = sorted(
            {grp for sk, _ in sex_keys
                 for grp in yr_data.get("sex", {}).get(sk, {})},
            key=lambda g: HIERARKI.get(g, 999),
        )

        if all_grps:
            _tab_pos_abs, _tab_pos_pct = st.tabs(["Antal", "Andel (%)"])

            def _make_pos_fig(use_pct: bool) -> go.Figure:
                fig = go.Figure()
                for sex_key, sex_label in sex_keys:
                    sex_data = yr_data.get("sex", {}).get(sex_key, {})
                    for pos_name, pos_label in pos_labels:
                        if use_pct:
                            vals, texts = [], []
                            for g in all_grps:
                                gd = sex_data.get(g, {})
                                tot = sum(gd.get(p, 0) for p, _ in pos_labels) or 1
                                pct = round(100 * gd.get(pos_name, 0) / tot, 1)
                                vals.append(pct)
                                texts.append(f"{pct:.1f}%")
                        else:
                            vals  = [sex_data.get(g, {}).get(pos_name, 0) for g in all_grps]
                            texts = [str(v) for v in vals]
                        fig.add_trace(go.Bar(
                            name=pos_label,
                            y=all_grps,
                            x=vals,
                            orientation="h",
                            marker_color=pos_sex_colors[(pos_name, sex_key)],
                            legendgroup=sex_key,
                            legendgrouptitle_text=sex_label if pos_name == "first" else None,
                            text=texts,
                            textposition="inside",
                            offsetgroup=sex_key,
                            hovertemplate=f"<b>%{{y}}</b> – {sex_label}<br>{pos_label}: %{{x}}" +
                                          ("%" if use_pct else "") + "<extra></extra>",
                        ))
                fig.update_layout(
                    barmode="stack",
                    xaxis_title="Andel af forfatterbidrag (%)" if use_pct else "Antal forfatterbidrag",
                    xaxis=dict(range=[0, 100]) if use_pct else {},
                    height=max(350, 70 * len(all_grps)),
                    margin=dict(l=160, t=20, r=20),
                    bargap=0.1,
                    bargroupgap=0.1,
                    yaxis=dict(autorange="reversed"),
                )
                return fig

            with _tab_pos_abs:
                st.plotly_chart(_make_pos_fig(False), width="stretch", key=f"pos_sex_abs_{year}_{mode}")

            with _tab_pos_pct:
                st.markdown("Andelen viser hvor stor en del af hver køns forfatterbidrag, der falder i hver forfatterposition - f.eks. hvor mange procent af kvindelige lektorers bidrag der er som førsteforfatter.")
                st.plotly_chart(_make_pos_fig(True), width="stretch", key=f"pos_sex_pct_{year}_{mode}")

            _pos_rows = []
            for g in all_grps:
                row = {"Stillingsgruppe": g}
                for sex_key, sex_label in sex_keys:
                    sex_data = yr_data.get("sex", {}).get(sex_key, {})
                    gd = sex_data.get(g, {})
                    tot = sum(gd.get(p, 0) for p, _ in pos_labels) or 1
                    for pos_name, pos_label in pos_labels:
                        v = gd.get(pos_name, 0)
                        row[f"{sex_label} – {pos_label} (n)"]  = v
                        row[f"{sex_label} – {pos_label} (%)"] = round(100 * v / tot, 1)
                _pos_rows.append(row)

            _pos_col_names = ["Stillingsgruppe"] + [
                f"{sex_label} – {pos_label} {suffix}"
                for sex_key, sex_label in sex_keys
                for pos_name, pos_label in pos_labels
                for suffix in ["(n)", "(%)"]
            ]
            _pos_schema = [("Stillingsgruppe", pa.string())] + [
                (c, pa.float64() if "(%)" in c else pa.int64())
                for c in _pos_col_names[1:]
            ]
            with st.expander("Se tabel"):
                st.dataframe(build_table(_pos_rows, _pos_schema), hide_index=True, width="stretch")
                st.download_button(
                    "Download (.xlsx)",
                    data=rows_to_excel_bytes(_pos_rows, _pos_col_names),
                    file_name=f"forfatterpositioner_køn_{year}_{mode}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_pos_sex_{year}_{mode}",
                )

    # ── Køn × sampubliceringsaktivitet per org-enhed ─────────────────────
    if mode in ("FS", "F", "FI", "FG", "FIG"):
        _sx_org_key   = "fac"
        _sx_org_label = "Fakultet"
        _sx_heading   = "Sampubliceringsaktivitet per køn og fakultet"
    elif mode in ("IS", "I", "IG"):
        _sx_org_key   = "inst"
        _sx_org_label = "Institut"
        _sx_heading   = "Sampubliceringsaktivitet per køn og institut"
    else:
        _sx_org_key   = "grp"
        _sx_org_label = "Stillingsgruppe"
        _sx_heading   = "Sampubliceringsaktivitet per køn og stillingsgruppe"

    st.markdown(f"#### {_sx_heading}")
    st.markdown(f"""
Figuren viser den samlede sampubliceringsaktivitet for kvindelige og mandlige forskere, **målt 
som forfatterpar**. Et forfatterpar opstår når to forfattere sampublicerer — en publikation med 
fire forfattere giver således seks forfatterpar. Hver forfatter
tildeles halvdelen af kantvægten per par, så tallene kan summeres på tværs af enheder.
Tallene er dermed ikke direkte sammenlignelige med antallet af publikationer.
""")

    _grp_sex_ew: dict[str, dict[str, float]] = {}
    _grp_sex_size: dict[str, dict[str, int]] = {}

    for nid, m in node_meta.items():
        if m.get("type") != "grp":
            continue
        org = m.get(_sx_org_key, "")
        sex = m.get("sex", "")
        if not org or not sex:
            continue
        _grp_sex_size.setdefault(org, {})
        _grp_sex_size[org][sex] = _grp_sex_size[org].get(sex, 0) + m.get("size", 0)

    for u, v, w, *_ in edges_keep:
        mu = node_meta.get(u, {})
        mv = node_meta.get(v, {})
        for m in [mu, mv]:
            org = m.get(_sx_org_key, "")
            sex = m.get("sex", "")
            if org and sex:
                _grp_sex_ew.setdefault(org, {})
                _grp_sex_ew[org][sex] = _grp_sex_ew[org].get(sex, 0.0) + w / 2

    _grps_sx = sorted(
        set(_grp_sex_ew) | set(_grp_sex_size),
        key=lambda g: HIERARKI.get(g, 999) if _sx_org_key == "grp" else g
    )

    _tab_sx_abs, _tab_sx_pct = st.tabs(["Antal", "Andel (%)"])

    with _tab_sx_abs:
        _fig_sx = go.Figure()
        for sex, label, color in [("k", "Kvinder", "#901a1E"), ("m", "Mænd", "#425570")]:
            _fig_sx.add_trace(go.Bar(
                name=label,
                y=_grps_sx,
                x=[_grp_sex_ew.get(g, {}).get(sex, 0) for g in _grps_sx],
                orientation="h",
                marker_color=color,
                text=[f"{_grp_sex_ew.get(g, {}).get(sex, 0):.1f}" for g in _grps_sx],
                textposition="inside",
                hovertemplate="<b>%{y}</b><br>%{x:.1f} forfatterpar<extra></extra>",
            ))
        _fig_sx.update_layout(
            barmode="stack",
            xaxis_title="Forfatterpar",
            height=max(300, 35 * len(_grps_sx)),
            bargap=0.15,
            margin=dict(l=200, r=40, t=20, b=20),
            yaxis=dict(autorange="reversed"),
            legend_title="Køn",
        )
        st.plotly_chart(_fig_sx, width="stretch", key=f"sx_abs_{year}_{mode}")

    with _tab_sx_pct:
        _fig_sx_pct = go.Figure()
        for sex, label, color in [("k", "Kvinder", "#901a1E"), ("m", "Mænd", "#425570")]:
            _pct_vals = []
            for g in _grps_sx:
                _tot = sum(_grp_sex_ew.get(g, {}).values()) or 1
                _pct_vals.append(round(100 * _grp_sex_ew.get(g, {}).get(sex, 0) / _tot, 1))
            _fig_sx_pct.add_trace(go.Bar(
                name=label,
                y=_grps_sx,
                x=_pct_vals,
                orientation="h",
                marker_color=color,
                text=[f"{v:.1f}%" for v in _pct_vals],
                textposition="inside",
                hovertemplate="<b>%{y}</b><br>%{x:.1f}%<extra></extra>",
            ))
        _fig_sx_pct.update_layout(
            barmode="stack",
            xaxis=dict(title="Andel (%)", range=[0, 100]),
            height=max(300, 35 * len(_grps_sx)),
            bargap=0.15,
            margin=dict(l=200, r=40, t=20, b=20),
            yaxis=dict(autorange="reversed"),
            legend_title="Køn",
        )
        st.plotly_chart(_fig_sx_pct, width="stretch", key=f"sx_pct_{year}_{mode}")

    _sx_rows = []
    for g in _grps_sx:
        row = {_sx_org_label: g}
        total_ew = sum(_grp_sex_ew.get(g, {}).values()) or 1
        for sex, label in [("k", "Kvinder"), ("m", "Mænd")]:
            ew = _grp_sex_ew.get(g, {}).get(sex, 0.0)
            row[f"{label} (forfatterpar)"] = round(ew, 1)
            row[f"{label} (%)"] = round(100 * ew / total_ew, 1)
        _sx_rows.append(row)

    _sx_schema = [
        (_sx_org_label,            pa.string()),
        ("Kvinder (forfatterpar)", pa.float64()),
        ("Kvinder (%)",            pa.float64()),
        ("Mænd (forfatterpar)",    pa.float64()),
        ("Mænd (%)",               pa.float64()),
    ]
    with st.expander("Se tabel"):
        st.dataframe(build_table(_sx_rows, _sx_schema), hide_index=True, width="stretch")
        st.download_button(
            "Download (.xlsx)",
            data=rows_to_excel_bytes(_sx_rows, [n for n, _ in _sx_schema]),
            file_name=f"køn_{_sx_org_label.lower()}_{year}_{mode}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_køn_grp_{year}_{mode}",
        )


    # ── Kanter per kønskombination ────────────────────────────────────────────
    # Beregn på raw_edges
    total_kk_raw = 0
    total_km_raw = 0
    total_mm_raw = 0
    for edge in raw_edges:
        combo = edge[3] if len(edge) > 3 and edge[3] else "ukendt"
        w = int(round(edge[2]))
        if combo == "k-k":
            total_kk_raw += w
        elif combo == "k-m":
            total_km_raw += w
        elif combo == "m-m":
            total_mm_raw += w

    kvinder_total = (2 * total_kk_raw + total_km_raw) or 1
    maend_total   = (2 * total_mm_raw + total_km_raw) or 1

    pct_k_med_k = round(100 * 2 * total_kk_raw / kvinder_total, 1)
    pct_k_med_m = round(100 * total_km_raw     / kvinder_total, 1)
    pct_m_med_k = round(100 * total_km_raw     / maend_total,   1)
    pct_m_med_m = round(100 * 2 * total_mm_raw / maend_total,   1)

    st.markdown(
f"""#### Sampubliceringer per kønskombination
En publikation med flere forfattere indgår typisk med fleren kanter - én per forfatterpar - 
og kan defor optræde under flere kønskombinationer samtidig. Sampubliceringsopgørelserne
tæller således **forfatterpar** - og ikke unikke publikationer. 

Generelt for forfatterpar på KU gælder det, at **kvindelige forfattere** indgår i 
**{pct_k_med_k}%** forfatterpar med **kvindelige medforfattere** - og de resterende 
**{pct_k_med_m}%** par er med **mandlige medforfattere**. 

Mens for de **mandlige forfattere** gælder det, at de **{pct_m_med_m}%** af forfatterparene
er indgået med **mandlige medforfattere** - og de resterende 
**{pct_m_med_k}%** forfatterpar er med **kvindelige medforfattere**.
""")

    tab_act, tab_pct = st.tabs(["Antal", "Andel (%)"])
    combo_counts: dict[str, int] = {}
    for edge in edges_keep:
        combo = edge[3] if len(edge) > 3 and edge[3] else "ukendt"
        combo_counts[combo] = combo_counts.get(combo, 0) + int(round(edge[2]))

    combos  = sorted(combo_counts.keys())
    combo_display = {"k-k": "Kvinde–Kvinde", "k-m": "Kvinde–Mand", "m-m": "Mand–Mand"}
    combo_colors  = {"k-k": "#901a1E", "k-m": "#bac7d9", "m-m": "#425570"}

    org_combo: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for u, v, w, *rest in (edges_keep or raw_edges):
        combo = rest[0] if rest and rest[0] else "ukendt"
        w = int(round(w))
        org_u = node_meta.get(u, {}).get(group_key, "ukendt")
        org_v = node_meta.get(v, {}).get(group_key, "ukendt")
        for org in ({org_u, org_v} if org_u != org_v else {org_u}):
            org_combo[org][combo] += w
    
    orgs_sorted = sorted(org_combo.keys())
    combos = sorted({c for d in org_combo.values() for c in d})

    with tab_act:
        fig2 = go.Figure()
        for combo in combos:
            fig2.add_trace(go.Bar(
                name=combo_display.get(combo, combo),
                y=orgs_sorted,
                x=[org_combo[org].get(combo, 0) for org in orgs_sorted],
                orientation="h",
                marker_color=combo_colors.get(combo, "#aaaaaa"),
                text=[org_combo[org].get(combo, 0) for org in orgs_sorted],
                textposition="inside",
            ))
        fig2.update_layout(
            barmode="stack", yaxis_title=group_label,
            xaxis_title="Antal forfatterpar", legend_title="Kønskombination",
            height=max(300, 60 * len(orgs_sorted)),
            margin=dict(l=160, t=20, r=20),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig2, width="stretch")

        combo_rows = []
        for org in orgs_sorted:
            row = {group_label: org}
            total = sum(org_combo[org].values())
            for combo in combos:
                n = org_combo[org].get(combo, 0)
                row[combo_display.get(combo, combo)] = n
            row["Total"] = total
            combo_rows.append(row)

        combo_schema = (
            [(group_label, pa.string())] +
            [(combo_display.get(c, c), pa.int64()) for c in combos] +
            [("Total", pa.int64())]
        )

        with st.expander("Se tabel"):
            st.dataframe(build_table(combo_rows, combo_schema), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)",
                data=rows_to_excel_bytes(combo_rows, [n for n, _ in combo_schema]),
                file_name=f"kønskombinationer_{year}_{mode}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

    with tab_pct:
        fig2p = go.Figure()
        for combo in combos:
            _pct_vals = [
                round(100 * org_combo[org].get(combo, 0) / (sum(org_combo[org].values()) or 1), 1)
                for org in orgs_sorted
            ]
            fig2p.add_trace(go.Bar(
                name=combo_display.get(combo, combo),
                y=orgs_sorted, x=_pct_vals,
                orientation="h",
                marker_color=combo_colors.get(combo, "#aaaaaa"),
                text=[f"{v}%" for v in _pct_vals],
                textposition="inside",
            ))
        fig2p.update_layout(
            barmode="stack", yaxis_title=group_label,
            xaxis=dict(title="Andel (%)", range=[0, 100]),
            legend_title="Kønskombination",
            height=max(300, 60 * len(orgs_sorted)),
            margin=dict(l=160, t=20, r=20),
            yaxis=dict(autorange="reversed"),
        )
        st.plotly_chart(fig2p, width="stretch")

        combo_rows_pct = []
        for org in orgs_sorted:
            row = {group_label: org}
            total = sum(org_combo[org].values())
            for combo in combos:
                n = org_combo[org].get(combo, 0)
                row[combo_display.get(combo, combo)] = n
                row[f"{combo_display.get(combo, combo)} (%)"] = round(100 * n / total, 1) if total else 0.0
            row["Total"] = total
            combo_rows_pct.append(row)

        combo_schema_pct = (
            [(group_label, pa.string())] +
            [(combo_display.get(c, c), pa.int64()) for c in combos] +
            [(f"{combo_display.get(c, c)} (%)", pa.float64()) for c in combos] +
            [("Total", pa.int64())]
        )
        with st.expander("Se tabel"):
            st.dataframe(build_table(combo_rows_pct, combo_schema_pct), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)",
                data=rows_to_excel_bytes(combo_rows_pct, [n for n, _ in combo_schema_pct]),
                file_name=f"kønskombinationer_pct_{year}_{mode}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    
    if all_years_data and len(all_years_data) >= 2:
        st.subheader("Udvikling over tid")
        years_sorted = sorted(all_years_data.keys())

        _tab_bidrag, _tab_combo = st.tabs(["Forfatterbidrag per køn", "Forfatterpar per kønskombination"])

        with _tab_bidrag:
            _all_sexes = sorted({
                s for snap in all_years_data.values()
                for s in snap.get("sex_bidrag", {})
            })

            fig_sb = go.Figure()
            for sex in _all_sexes:
                fig_sb.add_trace(go.Scatter(
                    x=years_sorted,
                    y=[all_years_data[y].get("sex_bidrag", {}).get(sex, 0) for y in years_sorted],
                    name = sex_display.get(sex, sex),
                    mode = "lines+markers",
                    line=dict(color=sex_colors.get(sex, "#3d3d3d"), width=2),
                    marker=dict(size=8)
                ))
            
            fig_sb.add_trace(go.Scatter(
                x=years_sorted,
                y=[sum(all_years_data[y].get("sex_bidrag", {}).get(sex, 0) for sex in _all_sexes) for y in years_sorted],
                name="Total",
                mode="lines+markers",
                line=dict(color="#3d3d3d", width=2, dash="dot"),
                marker=dict(size=8),
                visible=True,
            ))
            
            fig_sb.update_layout(
                xaxis=dict(tickmode="array", tickvals = years_sorted, dtick=1),
                yaxis_title="Forfatterbidrag",
                legend_title = "Køn",
                height = 500,
                margin=dict(t=20)
            )
            st.plotly_chart(fig_sb, width = "stretch")

        with _tab_combo:
            st.markdown("Antal forfatterpar per kønskombination per år. En publikation med fire forfattere giver seks forfatterpar.")
            _all_combos = sorted({
                c for snap in all_years_data.values()
                for c in snap.get("combo_pubs", {})
            })
            fig_cp = go.Figure()
            for combo in _all_combos:
                fig_cp.add_trace(go.Scatter(
                    x=years_sorted,
                    y=[all_years_data[y].get("combo_pubs", {}).get(combo, 0) for y in years_sorted],
                    name=combo_display.get(combo, combo),
                    mode="lines+markers",
                    line=dict(color=combo_colors.get(combo, "#3d3d3d"), width=2),
                    marker=dict(size=8)
                ))

            fig_cp.add_trace(go.Scatter(
                x=years_sorted,
                y=[sum(all_years_data[y].get("combo_pubs", {}).get(c, 0) for c in _all_combos) for y in years_sorted],
                name="Total",
                mode="lines+markers",
                line=dict(color="#3d3d3d", width=2, dash="dot"),
                marker=dict(size=8),
            ))

            fig_cp.update_layout(
                xaxis=dict(tickmode="array", tickvals=years_sorted, dtick=1),
                yaxis_title="Antal forfatterbidrag",
                legend_title="Kønskombination",
                height=380,
                margin=dict(t=2)
            )
            st.plotly_chart(fig_cp, width = "stretch")
        
        _all_sexes_o  = sorted({s for snap in all_years_data.values() for s in snap.get("sex_bidrag", {})})
        _all_combos_o = sorted({c for snap in all_years_data.values() for c in snap.get("combo_pubs", {})})
        
        _o_rows = []
        for y in years_sorted:
            sb = all_years_data[y].get("sex_bidrag", {})
            cp = all_years_data[y].get("combo_pubs", {})
            row = {"År": y}
            for sex in _all_sexes_o:
                row[f"Bidrag – {sex_display.get(sex, sex)}"] = sb.get(sex, 0)
            row["Bidrag – Total"] = sum(sb.get(sex, 0) for sex in _all_sexes_o)
            for combo in _all_combos_o:
                row[f"Par – {combo_display.get(combo, combo)}"] = cp.get(combo, 0)
            row["Par – Total"] = sum(cp.get(combo, 0) for combo in _all_combos_o)
            _o_rows.append(row)

        _o_col_names = (
            ["År"]
            + [f"Bidrag – {sex_display.get(s, s)}" for s in _all_sexes_o]
            + ["Bidrag – Total"]
            + [f"Par – {combo_display.get(c, c)}" for c in _all_combos_o]
            + ["Par – Total"]
        )
        _o_schema = [("År", pa.int64())] + [(c, pa.int64()) for c in _o_col_names[1:]]

        with st.expander("Se tabel"):
            st.dataframe(build_table(_o_rows, _o_schema), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)",
                data=rows_to_excel_bytes(_o_rows, _o_col_names),
                file_name=f"køn_overblik_{mode}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_køn_overblik_{year}_{mode}",
            )



    # ── Andel af forfatterbidrag vs. andel af sampubliceringer per køn ────────
    # Total forfatterbidrag per sex
    def _org_from_node_id(nid: str) -> str:
        """Udled organisatorisk enhed direkte fra node-ID."""
        parts = nid.split("|")
        if group_key == "fac":
            return parts[0] if len(parts) > 0 else "ukendt"
        elif group_key == "inst":
            return parts[1] if len(parts) > 1 else "ukendt"
        elif group_key == "grp":
            return parts[2] if len(parts) > 2 else "ukendt"
        return "ukendt"

    # Forudberegn kant-vægt per køn per org fra raw_edges
    org_sex_ew2: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for edge in raw_edges:
        u, v, w = edge[0], edge[1], edge[2]
        combo = edge[3] if len(edge) > 3 and edge[3] else None
        if not combo or "-" not in combo:
            continue
        sex_u, sex_v = combo.split("-", 1)
        org_u = _org_from_node_id(u)
        org_v = _org_from_node_id(v)
        # Filtrer: kun org-enheder der er i sex_size (dvs. de valgte)
        for endpoint_org, sex in [(org_u, sex_u), (org_v, sex_v)]:
            if endpoint_org in sex_size:  # <-- kun valgte enheder
                org_sex_ew2[endpoint_org][sex] += w / 2

    sex_tot: dict[str, int] = {}
    for nid, m in raw_nodes.items():
        if m.get("type") != "grp":
            continue
        sex  = m.get("sex", "ukendt")
        size = m.get("size", 0)
        sex_tot[sex] = sex_tot.get(sex, 0) + size

    orgs_for_comparison = sorted(sex_size.keys())

    def _sex_tot_for_org(org):
        """Forfatterbidrag per køn for én org-enhed."""
        return {
            sex_display.get(s, s): v
            for s, v in sex_size.get(org, {}).items()
        }

    def _sex_ew_for_org(org):
        """Kant-vægt per køn for én org-enhed (rå nøgler k/m)."""
        return dict(org_sex_ew2.get(org, {}))

    _tab_overall, *_tabs_orgs = st.tabs(["Samlet KU"] + orgs_for_comparison)

    # Samlet KU-tab
    with _tab_overall:
        sex_tot_all: dict[str, int] = {}
        for nid, m in raw_nodes.items():
            if m.get("type") != "grp":
                continue
            sex  = m.get("sex", "ukendt")
            size = m.get("size", 0)
            sex_tot_all[sex] = sex_tot_all.get(sex, 0) + size

        sex_ew_all: dict[str, float] = {}
        for edge in raw_edges:
            u, v, w = edge[0], edge[1], edge[2]
            combo = edge[3] if len(edge) > 3 and edge[3] else None
            if not combo or "-" not in combo:
                continue
            sex_u, sex_v = combo.split("-", 1)
            for sex in (sex_u, sex_v):
                sex_ew_all[sex] = sex_ew_all.get(sex, 0.0) + w / 2

        _render_share_comparison(
            {sex_display.get(k, k): v for k, v in sex_tot_all.items()},
            {sex_display.get(k, k): v for k, v in sex_ew_all.items()},
            "Køn",
            key="samlet",
        )

    # Én tab per organisatorisk enhed
    for org, _tab in zip(orgs_for_comparison, _tabs_orgs):
        with _tab:
            _render_share_comparison(
                {sex_display.get(k, k): v for k, v in sex_size.get(org, {}).items()},
                {sex_display.get(k, k): v for k, v in org_sex_ew2.get(org, {}).items()},
                "Køn",
                key=org,
            )

    # ── Samlet tabel på tværs af alle org-enheder (udenfor tabs) ─────────────
    share_rows = []
    for org in orgs_for_comparison:
        fb     = sex_size.get(org, {})
        ew     = org_sex_ew2.get(org, {})
        fb_tot = sum(fb.values()) or 1
        ew_tot = sum(ew.values()) or 1
        for sex in ["k", "m"]:
            label  = sex_display.get(sex, sex)
            pct_fb = round(100 * fb.get(sex, 0)   / fb_tot, 1)
            pct_ew = round(100 * ew.get(sex, 0.0) / ew_tot, 1)
            share_rows.append({
                group_label:                  org,
                "Køn":                        label,
                "Andel forfatterbidrag (%)":  pct_fb,
                "Andel sampubliceringer (%)": pct_ew,
                "Forskel (pp)":               round(pct_ew - pct_fb, 1),
            })

    share_schema = [
        (group_label,                  pa.string()),
        ("Køn",                        pa.string()),
        ("Andel forfatterbidrag (%)",   pa.float64()),
        ("Andel sampubliceringer (%)",  pa.float64()),
        ("Forskel (pp)",               pa.float64()),
    ]

    with st.expander("Se samlet tabel"):
        st.dataframe(build_table(share_rows, share_schema), hide_index=True, width="stretch")
        st.download_button(
            "Download (.xlsx)",
            data=rows_to_excel_bytes(share_rows, [n for n, _ in share_schema]),
            file_name=f"kønsandel_{year}_{mode}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_share_samlet_{year}_{mode}",
        )
    
    



# ===========================================================================
# NATIONALITETER TAB
# ===========================================================================
def render_tab_nationaliteter(year, mode, raw_nodes, raw_edges, node_meta,
                               selected_facs, selected_insts, selected_grps,
                               raw_nodes_unfiltered=None, raw_edges_unfiltered=None,
                               all_years_data=None):

    st.subheader("Nationalitetsfordeling")
    st.markdown(
f"""
Fanen viser fordelingen af stataborgerskab blandt forfatterbidragene i sampubliceringer.
Opgørelserne nedenfor bygger på **alle {len(raw_edges)} sampubliceringer** - og er 
dermed uafhængige af den aktuelle netværksvisning.
""")

    # Determine grouping key based on mode
    if mode in ("FS", "F", "FI", "FG", "FIG"):
        group_key   = "fac"
        group_label = "Fakultet"
    elif mode in ("IS", "I", "IG"):
        group_key   = "inst"
        group_label = "Institut"
    else:
        group_key   = "grp"
        group_label = "Stillingsgruppe"

    # Aggregate forfatterbidrag by (group, citizenship)
    cs_size: dict[str, dict[str, int]] = {}
    cs_total: dict[str, int] = {}

    for nid, m in raw_nodes.items():
        if m.get("type") != "grp":
            continue
        grp_val = m.get(group_key, "")
        cs      = m.get("statsborgerskab", "Ukendt") or "Ukendt"
        size    = m.get("size", 0)
        if not grp_val:
            continue
        # Filtrer på valgte org-enheder
        if selected_facs and m.get("fac") not in selected_facs:
            continue
        if selected_insts and m.get("inst") not in selected_insts:
            continue
        if selected_grps and m.get("grp") not in selected_grps:
            continue
        cs_size.setdefault(grp_val, {})
        cs_size[grp_val][cs] = cs_size[grp_val].get(cs, 0) + size
        cs_total[cs]         = cs_total.get(cs, 0) + size

    groups_sorted = sorted(cs_size.keys())

    # ── Top-N nationaliteter (overall) ───────────────────────────────────────
    st.markdown("#### Mest repræsenterede nationaliteter")

    all_cs_sorted = sorted(cs_total.items(), key=lambda x: -x[1])
    max_n         = len(all_cs_sorted)
    if max_n == 0:
        st.error("Ingen nationalitetsdata tilgængelig for det valgte udsnit.")
        return

    top_n = st.number_input(
        "Vis top-N nationaliteter",
        min_value=1, max_value=max_n,
        value=min(10, max_n), step=1,
        key=f"nat_top_n_{year}_{mode}",
    )
    top_cs = [c for c, _ in all_cs_sorted[:top_n]]
    top_vals = [v for _, v in all_cs_sorted[:top_n]]

    st.markdown(f"##### Forfatterbidrag fordelt på nationalitet i {year}")

    _tab_abs, _tab_pct = st.tabs(["Antal", "Andel (%)"])

    nat_colors = ku_color_sequence(len(top_cs) + 1)

    def _nat_traces(normalise = False):
        traces = []
        for i, cs in enumerate(top_cs):
            raw_y = [cs_size[g].get(cs, 0) for g in groups_sorted]
            y = ([round(100 * v / (sum(cs_size[g].values()) or 1), 1)
                  for g, v in zip(groups_sorted, raw_y)]
                 if normalise else raw_y)
            traces.append(go.Bar(
                name=cs,
                y=groups_sorted,
                x=y,
                orientation="h",
                marker_color=nat_colors[i], 
                text=[f"{v}%" for v in y] if normalise else y,
                textposition="inside",
            ))
            andre_raw = [sum(v for k, v in cs_size[g].items() if k not in top_cs)
                     for g in groups_sorted]
        if any(v > 0 for v in andre_raw):
            andre_y = ([round(100 * v / (sum(cs_size[g].values()) or 1), 1)
                        for g, v in zip(groups_sorted, andre_raw)]
                       if normalise else andre_raw)
            traces.append(go.Bar(
                name="Andre",
                y=groups_sorted,
                x=andre_y,
                orientation="h",
                marker_color="#cccccc",
                text=[f"{v}%" for v in andre_y] if normalise else andre_y,
                textposition="inside",
            ))
        return traces

    with _tab_abs:
        fig1 = go.Figure(_nat_traces(normalise=False))
        fig1.update_layout(
            barmode="stack",
            xaxis_title="Forfatterbidrag",
            legend_title="Statsborgerskab",
            height=max(300, 35 * len(groups_sorted)),
            bargap=0.15,
            margin=dict(l=200, r=40, t=20, b=20),
        )
        st.plotly_chart(fig1, width="stretch")

    with _tab_pct:
        fig1p = go.Figure(_nat_traces(normalise=True))
        fig1p.update_layout(
            barmode="stack",
            yaxis=dict(title=group_label, autorange="reversed"),
            xaxis_title="Andel (%)",
            xaxis_range=[0, 100],
            legend_title="Statsborgerskab",
            height=max(300, 35 * len(groups_sorted)),
            bargap=0.15,
            margin=dict(l=200, r=40, t=20, b=20),
        )
        st.plotly_chart(fig1p, width="stretch")
    
    # Tabel
    rows = []
    for g in groups_sorted:
        row = {group_label: g}
        total = sum(cs_size[g].values())
        for cs in top_cs:
            n = cs_size[g].get(cs, 0)
            row[cs] = n
            row[f"{cs} (%)"] = round(100 * n / total, 1) if total else 0.0
        row["Total"] = total
        rows.append(row)

    schema_fields = (
        [(group_label, pa.string())] +
        [(cs, pa.int64()) for cs in top_cs] +
        [(f"{cs} (%)", pa.float64()) for cs in top_cs] +
        [("Total", pa.int64())]
    )
    with st.expander("Se tabel"):
        st.dataframe(build_table(rows, schema_fields), hide_index=True, width="stretch")
        st.download_button(
            "Download (.xlsx)",
            data=rows_to_excel_bytes(rows, [n for n, _ in schema_fields]),
            file_name=f"nationalitetsfordeling_{year}_{mode}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    
    if all_years_data and len(all_years_data) >= 2:
        st.subheader("Udvikling over tid")
        years_sorted = sorted(all_years_data.keys())

        # Find alle nationaliteter på tværs af år – begræns til top_cs for læsbarhed
        _all_cs = top_cs  # bruger samme top_n som resten af tabben

        nat_colors_time = ku_color_sequence(len(_all_cs) + 1)

        fig_nat = go.Figure()
        for i, cs in enumerate(_all_cs):
            fig_nat.add_trace(go.Scatter(
                x=years_sorted,
                y=[all_years_data[y].get("nat_bidrag", {}).get(cs, 0) for y in years_sorted],
                name=cs,
                mode="lines+markers",
                line=dict(color=nat_colors_time[i], width=2),
                marker=dict(size=8),
            ))

        fig_nat.add_trace(go.Scatter(
            x=years_sorted,
            y=[sum(all_years_data[y].get("nat_bidrag", {}).get(cs, 0) for cs in _all_cs)
               for y in years_sorted],
            name="Total (top-N)",
            mode="lines+markers",
            line=dict(color="#3d3d3d", width=2, dash="dot"),
            marker=dict(size=8),
        ))

        fig_nat.update_layout(
            xaxis=dict(tickmode="array", tickvals=years_sorted, dtick=1),
            yaxis_title="Forfatterbidrag",
            legend_title="Statsborgerskab",
            height=500,
            margin=dict(t=20),
        )
        st.plotly_chart(fig_nat, width="stretch")

        # Tabel
        nat_rows = []
        for y in years_sorted:
            nb = all_years_data[y].get("nat_bidrag", {})
            row = {"År": y}
            for cs in _all_cs:
                row[cs] = nb.get(cs, 0)
            row["Total"] = sum(nb.get(cs, 0) for cs in _all_cs)
            nat_rows.append(row)

        nat_col_names = ["År"] + _all_cs + ["Total"]
        nat_schema = [(c, pa.int64()) for c in nat_col_names]

        with st.expander("Se tabel"):
            st.dataframe(build_table(nat_rows, nat_schema), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)",
                data=rows_to_excel_bytes(nat_rows, nat_col_names),
                file_name=f"nationalitet_tidsserie_{mode}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_nat_tid_{mode}",
            )

    # ── Heatmap ───────────────────────────────────────────────────────────────
    st.markdown("#### Sampubliceringer mellem nationaliteter")
    st.markdown(
f"""
Heatmap over sampubliceringsvægt mellem de mest repræsenterede nationaliteter. Kun 
kanter, hvor begge endepunkter har en kendt nationalitet, medtages."""
    )

        # Byg org_cs_combo fra raw_edges
    def _org_from_node_id(nid: str) -> str:
        parts = nid.split("|")
        if group_key == "fac":
            return parts[0] if len(parts) > 0 else "ukendt"
        elif group_key == "inst":
            return parts[1] if len(parts) > 1 else "ukendt"
        elif group_key == "grp":
            return parts[2] if len(parts) > 2 else "ukendt"
        return "ukendt"

    _nid_to_cs: dict[str, str] = {
        nid: (m.get("statsborgerskab", "") or "Ukendt")
        for nid, m in raw_nodes.items()
        if m.get("type") == "grp"
    }

    # Tæl totaler per nationalitetskombination
    total_cs_combo: dict[str, int] = {}
    for edge in raw_edges:
        cs_u = _nid_to_cs.get(edge[0], "Ukendt")
        cs_v = _nid_to_cs.get(edge[1], "Ukendt")
        w    = int(round(edge[2]))
        combo = f"{min(cs_u,cs_v)}–{max(cs_u,cs_v)}"
        total_cs_combo[combo] = total_cs_combo.get(combo, 0) + w

    _cs_matrix: dict[tuple, float] = {}
    for edge in raw_edges:
        u, v, w = edge[0], edge[1], edge[2]
        cs_u = _nid_to_cs.get(u, "")
        cs_v = _nid_to_cs.get(v, "")
        if not cs_u or not cs_v:
            continue
        cs_u_bin = cs_u if cs_u in top_cs else "Andre"
        cs_v_bin = cs_v if cs_v in top_cs else "Andre"
        for a, b in [(cs_u_bin, cs_v_bin), (cs_v_bin, cs_u_bin)]:
            _cs_matrix[(a, b)] = _cs_matrix.get((a, b), 0.0) + w

    _heat_labels = top_cs + (["Andre"] if any("Andre" in k for k in _cs_matrix) else [])

    if _cs_matrix:
        _z    = [[_cs_matrix.get((a, b), 0.0) for b in _heat_labels] for a in _heat_labels]
        _text = [[f"{_cs_matrix.get((a,b),0):.0f}" for b in _heat_labels] for a in _heat_labels]
        _fig_heat = go.Figure(go.Heatmap(
            z=_z, x=_heat_labels, y=_heat_labels,
            colorscale=[[0, "#f0f4f8"], [1, "#122947"]],
            text=_text, texttemplate="%{text}",
            hovertemplate="%{y} → %{x}: %{z:.0f} publikationer<extra></extra>",
            colorbar=dict(title="Sampubliceringsvægt"),
        ))
        _fig_heat.update_layout(
            xaxis_title="Statsborgerskab", yaxis_title="Statsborgerskab",
            height=max(400, 45 * len(_heat_labels)),
            margin=dict(l=80, b=100, t=20, r=20),
        )
        st.plotly_chart(_fig_heat, width="content")

        _pair_rows = []
        seen_pairs = set()
        for (a, b), w in sorted(_cs_matrix.items(), key=lambda x: -x[1]):
            key = tuple(sorted((a, b)))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            _pair_rows.append({
                "Nationalitet A": a, "Nationalitet B": b,
                "Sampubliceringsvægt": round(w, 1),
                "Type": "Intra" if a == b else "Inter",
            })
        _pair_schema = [
            ("Nationalitet A", pa.string()), ("Nationalitet B", pa.string()),
            ("Sampubliceringsvægt", pa.float64()), ("Type", pa.string()),
        ]
        with st.expander("Se tabel"):
            st.dataframe(build_table(_pair_rows[:50], _pair_schema), hide_index=True, width="stretch")
            st.download_button(
                "Download (.xlsx)",
                data=rows_to_excel_bytes(_pair_rows, [n for n, _ in _pair_schema]),
                file_name=f"nationalitet_sampubliceringer_{year}_{mode}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
    else:
        st.error("Ingen kanter med kendte nationaliteter for begge endepunkter i det valgte udsnit.")

    # ── Sampubliceringer per nationalitetskombination ─────────────────────────
    st.markdown("#### Sampubliceringer per nationalitetskombination")
    st.markdown(
        f"""En publikation med flere forfattere indgår typisk med flere kanter – 
        én per forfatterpar – og kan derfor optræde under flere nationalitetskombinationer 
        samtidig. Opgørelsen tæller derfor **forfatterpar**, ikke unikke publikationer.
        """
    )



    # Kønsperspektiv-style tekst: per nationalitet
    cs_perspective: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for edge in raw_edges:
        cs_u = _nid_to_cs.get(edge[0], "Ukendt")
        cs_v = _nid_to_cs.get(edge[1], "Ukendt")
        w    = int(round(edge[2]))
        cs_perspective[cs_u][cs_v] += w
        cs_perspective[cs_v][cs_u] += w

    # Vis kun top_cs i perspektiv-teksten
    for i, cs in enumerate(top_cs[:3]):
        data  = cs_perspective.get(cs, {})
        total = sum(data.values()) or 1
        same  = round(100 * data.get(cs, 0) / total, 1)
        other = round(100 - same, 1)
        top_partner = max(
            {k: v for k, v in data.items() if k != cs},
            key=lambda k: data[k], default=None
        )
        top_pct = round(100 * data.get(top_partner, 0) / total, 1) if top_partner else 0

        if i == 0:
            st.markdown(
                f"Generelt for forfatterpar på KU gælder det, at **{cs}-forfattere** indgår i "
                f"**{same}%** af forfatterparrene med andre **{cs}-forfattere** - og de resterende "
                f"**{other}%** par er med forfattere af anden nationalitet"
                + (f", hvoraf **{top_pct}%** er med **{top_partner}**-forfattere." if top_partner else ".")
            )
        elif i == 1:
            st.markdown(
                f"For **{cs}-forfattere** gælder det tilsvarende, at **{same}%** af deres "
                f"forfatterpar er med andre **{cs}**-forfattere - mens de resterende **{other}%** "
                f"er på tværs af nationaliteter"
                + (f", primært med **{top_partner}**-forfattere (**{top_pct}%**)." if top_partner else ".")
            )
        elif i == 2:
            st.markdown(
                f"Endelig samarbejder **{cs}-forfattere** i **{same}%** af tilfældene med andre "
                f"**{cs}**-forfattere - og i **{other}%** af forfatterparrene er medforfatteren "
                f"af en anden nationalitet"
                + (f", oftest **{top_partner}** (**{top_pct}%**)." if top_partner else ".")
            )



    # ── DK vs. international ──────────────────────────────────────────────────
    st.markdown("#### DK vs. international")

    dk_tot: dict[str, int] = {"DK": 0, "International": 0}
    for nid, m in raw_nodes.items():
        if m.get("type") != "grp":
            continue
        cs   = m.get("statsborgerskab", "")
        size = m.get("size", 0)
        key  = "DK" if cs == "DK" else "International"
        dk_tot[key] += size

    dk_ew: dict[str, float] = {"DK": 0.0, "International": 0.0}
    for edge in raw_edges:
        u, v, w = edge[0], edge[1], edge[2]
        cs_u = _nid_to_cs.get(u, "")
        cs_v = _nid_to_cs.get(v, "")
        dk_ew["DK" if cs_u == "DK" else "International"] += w / 2
        dk_ew["DK" if cs_v == "DK" else "International"] += w / 2

    _grand_tot_dk = sum(dk_tot.values()) or 1
    _grand_ew_dk  = sum(dk_ew.values())  or 1
    _dk_bid_pct   = round(100 * dk_tot.get("DK", 0) / _grand_tot_dk, 1)
    _int_bid_pct  = round(100 * dk_tot.get("International", 0) / _grand_tot_dk, 1)
    _dk_ew_pct    = round(100 * dk_ew.get("DK", 0) / _grand_ew_dk, 1)
    _int_ew_pct   = round(100 * dk_ew.get("International", 0) / _grand_ew_dk, 1)

    # Tabs per org-enhed + samlet
    orgs_for_comparison = sorted(cs_size.keys())

    org_cs_ew: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for edge in raw_edges:
        u, v, w = edge[0], edge[1], edge[2]
        cs_u = _nid_to_cs.get(u, "")
        cs_v = _nid_to_cs.get(v, "")
        org_u = _org_from_node_id(u)
        org_v = _org_from_node_id(v)
        key_u = "DK" if cs_u == "DK" else "International"
        key_v = "DK" if cs_v == "DK" else "International"
        if org_u in cs_size:
            org_cs_ew[org_u][key_u] += w / 2
        if org_v in cs_size:
            org_cs_ew[org_v][key_v] += w / 2

    _tab_overall, *_tabs_orgs = st.tabs(["Samlet KU"] + orgs_for_comparison)
    
    with _tab_overall:
        _render_share_comparison(dk_tot, dk_ew, "DK/International", key="nat_samlet")

    for org, _tab in zip(orgs_for_comparison, _tabs_orgs):
        with _tab:
            _org_tot = {}
            for nid, m in raw_nodes.items():
                if m.get("type") != "grp" or _org_from_node_id(nid) != org:
                    continue
                cs   = m.get("statsborgerskab", "")
                size = m.get("size", 0)
                key  = "DK" if cs == "DK" else "International"
                _org_tot[key] = _org_tot.get(key, 0) + size
            _render_share_comparison(
                _org_tot,
                dict(org_cs_ew.get(org, {})),
                "DK/International",
                key=f"nat_{org}",
            )

    # Samlet tabel
    share_rows = []
    for org in orgs_for_comparison:
        _org_tot = {}
        for nid, m in raw_nodes.items():
            if m.get("type") != "grp" or _org_from_node_id(nid) != org:
                continue
            cs   = m.get("statsborgerskab", "")
            size = m.get("size", 0)
            key  = "DK" if cs == "DK" else "International"
            _org_tot[key] = _org_tot.get(key, 0) + size
        ew     = org_cs_ew.get(org, {})
        fb_tot = sum(_org_tot.values()) or 1
        ew_tot = sum(ew.values()) or 1
        for key in ["DK", "International"]:
            pct_fb = round(100 * _org_tot.get(key, 0) / fb_tot, 1)
            pct_ew = round(100 * ew.get(key, 0.0) / ew_tot, 1)
            share_rows.append({
                group_label:                  org,
                "Nationalitet":               key,
                "Andel forfatterbidrag (%)":  pct_fb,
                "Andel sampubliceringer (%)": pct_ew,
                "Forskel (pp)":               round(pct_ew - pct_fb, 1),
            })

    share_schema = [
        (group_label,                  pa.string()),
        ("Nationalitet",               pa.string()),
        ("Andel forfatterbidrag (%)",   pa.float64()),
        ("Andel sampubliceringer (%)",  pa.float64()),
        ("Forskel (pp)",               pa.float64()),
    ]
    with st.expander("Se samlet tabel"):
        st.dataframe(build_table(share_rows, share_schema), hide_index=True, width="stretch")
        st.download_button(
            "Download (.xlsx)",
            data=rows_to_excel_bytes(share_rows, [n for n, _ in share_schema]),
            file_name=f"nationalitet_andel_{year}_{mode}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"dl_nat_share_{year}_{mode}",
        )

    # ── Heatmap ───────────────────────────────────────────────────────────────
    st.markdown("##### Sampubliceringer mellem nationaliteter")
    st.markdown(
f"""
Heatmap over sampubliceringsvægt mellem de mest repræsenterede nationaliteter. Kun 
kanter, hvor begge endepunkter har en kendt nationalitet, medtages."""
    )

    _cs_matrix: dict[tuple, float] = {}
    for edge in raw_edges:
        u, v, w = edge[0], edge[1], edge[2]
        cs_u = _nid_to_cs.get(u, "")
        cs_v = _nid_to_cs.get(v, "")
        if not cs_u or not cs_v:
            continue
        cs_u_bin = cs_u if cs_u in top_cs else "Andre"
        cs_v_bin = cs_v if cs_v in top_cs else "Andre"
        for a, b in [(cs_u_bin, cs_v_bin), (cs_v_bin, cs_u_bin)]:
            _cs_matrix[(a, b)] = _cs_matrix.get((a, b), 0.0) + w

    _heat_labels = top_cs + (["Andre"] if any("Andre" in k for k in _cs_matrix) else [])

    if _cs_matrix:
        _z    = [[_cs_matrix.get((a, b), 0.0) for b in _heat_labels] for a in _heat_labels]
        _text = [[f"{_cs_matrix.get((a,b),0):.0f}" for b in _heat_labels] for a in _heat_labels]
        _fig_heat = go.Figure(go.Heatmap(
            z=_z, x=_heat_labels, y=_heat_labels,
            colorscale=[[0, "#f0f4f8"], [1, "#122947"]],
            text=_text, texttemplate="%{text}",
            hovertemplate="%{y} → %{x}: %{z:.0f} publikationer<extra></extra>",
            colorbar=dict(title="Sampubliceringsvægt"),
        ))
        _fig_heat.update_layout(
            xaxis_title="Statsborgerskab", yaxis_title="Statsborgerskab",
            height=max(400, 45 * len(_heat_labels)),
            margin=dict(l=80, b=100, t=20, r=20),
        )
        st.plotly_chart(_fig_heat, width="content", key=f"fig_heat_{year}_{mode}")

        _pair_rows = []
        seen_pairs = set()
        for (a, b), w in sorted(_cs_matrix.items(), key=lambda x: -x[1]):
            key = tuple(sorted((a, b)))
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            _pair_rows.append({
                "Nationalitet A": a, "Nationalitet B": b,
                "Sampubliceringsvægt": round(w, 1),
                "Type": "Intra" if a == b else "Inter",
            })
        _pair_schema = [
            ("Nationalitet A", pa.string()), ("Nationalitet B", pa.string()),
            ("Sampubliceringsvægt", pa.float64()), ("Type", pa.string()),
        ]
        st.download_button(
                "Download (.xlsx)",
                data=rows_to_excel_bytes(_pair_rows, [n for n, _ in _pair_schema]),
                file_name=f"nationalitet_sampubliceringer_{year}_{mode}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_nat_pairs_{year}_{mode}",
            )
    else:
        st.error("Ingen kanter med kendte nationaliteter for begge endepunkter i det valgte udsnit.")
if __name__ == "__main__":
    main()