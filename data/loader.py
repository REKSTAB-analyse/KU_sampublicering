import streamlit as st
import json
import csv
import os
import subprocess
from pathlib import Path
from datetime import datetime
from collections import OrderedDict
import duckdb
import paramiko

from config import PAIRS_PARQUET_PATH, PUB_LONG_PARQUET_PATH, ERDA_ENABLED

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
# ERDA-SYNC (NYT) - to filer: pairs (kanter) + pub_long (node-forfatterantal)
# ---------------------------------------------------------------------------
# Svarer til _sync_parquet_from_erda() i KU_publikationer/data/loader.py.
# Kald sync_data_from_erda() i app.py FØR noget forsøger at læse `pairs`-
# eller `pubs`-viewet.

_SYNC_PARQUET_PATHS = {
    "pairs": PAIRS_PARQUET_PATH,
    "pub_long": PUB_LONG_PARQUET_PATH,
}


@st.cache_resource()
def sync_data_from_erda():
    if not ERDA_ENABLED:
        return
    sftp = get_sftp()
    for name, local_path in _SYNC_PARQUET_PATHS.items():
        remote_filename = Path(local_path).name
        remote_path = f"{DATA_PATH}/{remote_filename}"
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        print(f"[ERDA-sync] Henter {remote_path} ...", flush=True)
        sftp.get(remote_path, local_path)
        print(f"[ERDA-sync] Færdig: {local_path}", flush=True)


# ---------------------------------------------------------------------------
# DUCKDB: ÉN DELT FORBINDELSE (NYT)
# ---------------------------------------------------------------------------
# pairs og pubs ligger i SAMME DuckDB-forbindelse (ikke to separate, som i
# en tidligere version af dette skelet), så de kan JOIN'es - nødvendigt for
# at filtrere pairs-kanter på publikationsattributter (Type, Sprog,
# Peer_review, Open_Access, DOI, Antal_forfattere), som kun findes i
# KU_pub_long.parquet, ikke i pairs-parquet'en.
#
# pub_meta er et afledt view: ÉT row pr. PURE_ID (pubs har ét row PR.
# FORFATTER pr. publikation, så attributter som Type/Year/Antal_forfattere
# gentages på hver forfatter-række for samme publikation - pub_meta fjerner
# den gentagelse, så et JOIN mod pairs ikke laver en fan-out).

@st.cache_resource
def _get_db():
    conn = duckdb.connect()
    conn.execute(f"CREATE VIEW pairs AS SELECT * FROM read_parquet('{PAIRS_PARQUET_PATH}')")
    conn.execute(f"CREATE VIEW pubs AS SELECT * FROM read_parquet('{PUB_LONG_PARQUET_PATH}')")
    conn.execute("""
        CREATE VIEW pub_meta AS
        SELECT DISTINCT PURE_ID, DOI, Open_Access, Type, Sprog, Peer_review,
               Indholdstype, Year, Antal_forfattere
        FROM pubs
    """)
    return conn


def get_cursor():
    return _get_db().cursor()


def get_pairs_cursor():
    """Alias for get_cursor() - pairs- og pubs-viewet er begge synlige fra
    samme forbindelse, så navnet er kun for læsbarhed ved kald-stedet."""
    return get_cursor()


def get_pubs_cursor():
    """Se get_pairs_cursor() - samme forbindelse, andet navn af læsbarhed."""
    return get_cursor()


# ---------------------------------------------------------------------------
# FILTEROPTIONS TIL SIDEPANELET (NYT)
# ---------------------------------------------------------------------------

@st.cache_data
def load_pub_filter_options() -> dict:
    """Distinkte værdier til 'Publikationstype og adgang'-sektionen i
    sidepanelet - udledt af pub_meta (ét row pr. publikation)."""
    conn = _get_db()

    def distinct(col):
        return sorted(
            r[0] for r in conn.execute(
                f"SELECT DISTINCT {col} FROM pub_meta WHERE {col} IS NOT NULL AND {col} != ''"
            ).fetchall()
        )

    return {
        "typer": distinct("Type"),
        "sprog": distinct("Sprog"),
        "indholds": distinct("Indholdstype"),
        "open_access": distinct("Open_Access"),
    }


@st.cache_data
def load_year_range() -> tuple[int, int]:
    """Min/max Year i data - til årstals-sliderens grænser."""
    row = _get_db().execute(
        "SELECT MIN(Year), MAX(Year) FROM pub_meta WHERE Year IS NOT NULL"
    ).fetchone()
    lo, hi = row if row else (None, None)
    return (int(lo) if lo is not None else 2000, int(hi) if hi is not None else 2025)


@st.cache_data
def load_max_author_count() -> int:
    """Højeste Antal_forfattere i data - til 'Forfatterantal'-sliderens øvre grænse."""
    result = _get_db().execute("SELECT MAX(Antal_forfattere) FROM pub_meta").fetchone()
    max_val = result[0]
    return int(max_val) if max_val is not None else 1


@st.cache_data
def load_statsborgerskab_options() -> list:
    """Distinkte statsborgerskaber blandt INTERNE forfattere. Bruger
    pubs-viewet (ikke pub_meta), da Statsbg er en forfatter-attribut, ikke en
    publikations-attribut, og derfor ikke hører hjemme i den deduplikerede
    pub_meta."""
    rows = get_pubs_cursor().execute(
        "SELECT DISTINCT Statsbg FROM pubs "
        "WHERE Intern = 'Intern' AND Statsbg IS NOT NULL AND Statsbg != '' "
        "ORDER BY Statsbg"
    ).fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# ØVRIGE DATA (uændret SFTP/JSON/CSV - se note i modulets docstring)
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner="Indlæser netværksdata...")
def load_network_data() -> dict:
    """Formentlig overflødig nu - se modulets docstring."""
    raw = json.loads(read_file("vip_transformed.json"))
    return {int(k): v for k, v in raw.items()}


@st.cache_data
def load_ku_colors() -> dict:
    return json.loads(read_file("ku-farver02.json"))


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


@st.cache_data(show_spinner="Indlæser forfatterdata...")
def load_forfatterantal() -> dict:
    return json.loads(read_file("forfatterantal.json"))


@st.cache_data(show_spinner="Indlæser forfatterdata...")
def load_forfatterantal_dist() -> dict:
    return json.loads(read_file("forfatterantal_dist.json"))


@st.cache_data
def load_publikationstyper() -> dict:
    return json.loads(read_file("publikationstyper.json"))


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
            fac = row["Fakultet"].strip()
            inst = row["Institut"].strip()
            alt = row["Alternativ"].strip()
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


@st.cache_data(show_spinner="Indlæser forfatterdata...")
def load_forfatterpositioner() -> dict:
    return json.loads(read_file("forfatterpositioner.json"))


@st.cache_data
def load_ku_totals() -> dict:
    raw = json.loads(read_file("ku_totals.json"))
    return {int(k): v for k, v in raw.items()}


@st.cache_data
def load_logo() -> bytes:
    return read_file("KU-logo.png")


@st.cache_data
def load_svg(filename: str) -> str | None:
    try:
        return read_file(filename).decode("utf-8")
    except Exception:
        return None


# ---------------------------------------------------------------------------
# DEPLOY-DATO
# ---------------------------------------------------------------------------

def _get_last_deploy_date() -> str:
    try:
        repo_dir = os.path.dirname(os.path.abspath(__file__))
        ts = subprocess.check_output(
            ["git", "log", "-1", "--format=%ci"],
            cwd=repo_dir,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        dt = datetime.fromisoformat(ts)
        return f"{dt.day}. {dt.strftime('%B').lower()} {dt.year}"
    except Exception:
        d = datetime.today()
        return f"{d.day}. {d.strftime('%B').lower()} {d.year}"


_DEPLOY_DATE = _get_last_deploy_date()
