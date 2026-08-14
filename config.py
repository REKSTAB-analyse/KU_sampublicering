import streamlit as st
from pathlib import Path

# ---------------------------------------------------------------------------
# STILLINGSHIERARKI
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
    "k": "Kvinder",
}

GROUP_ORDER = sorted(HIERARKI.keys(), key=lambda g: HIERARKI[g])
LVL_MIN, LVL_MAX = min(HIERARKI.values()), max(HIERARKI.values())

# ---------------------------------------------------------------------------
# FAKULTETER
# ---------------------------------------------------------------------------

FAC_ORDER = ["SAMF", "SCIENCE", "TEO", "SUND", "HUM", "JUR"]

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
    if existing is not None:
        original = abbr
        i = 1
        while abbr in existing:
            abbr = original + (words[0][i].upper() if i < len(words[0]) else str(i))
            i += 1
    return abbr


# ---------------------------------------------------------------------------
# ORGANISATORISKE DIMENSIONER / MODE-SYSTEM
# ---------------------------------------------------------------------------
# En "mode"-streng er sammensat af bogstaverne F (fakultet), I (institut),
# G (stillingsgruppe), S (køn) og N (nationalitet), fx "FIG" eller "FIGS".
# Samme bogstav-konvention som MODE_COLS i KU_publikationer/config.py.

MODE_COLS = {"F": "Fak", "I": "Inst", "G": "Stil", "S": "Koen", "N": "Statsbg"}
_MODE_ORDER = "FIGSN"


def sex_in_mode(mode: str) -> bool:
    return "S" in mode


def nat_in_mode(mode: str) -> bool:
    return "N" in mode


def fac_in_mode(mode: str) -> bool:
    return "F" in mode


def inst_in_mode(mode: str) -> bool:
    return "I" in mode


def grp_in_mode(mode: str) -> bool:
    return "G" in mode


def base_mode(mode: str) -> str:
    """Returnerer mode uden S- og N-suffikset, fx 'FIG' fra 'FIGS'."""
    return mode.replace("S", "").replace("N", "")


def network_mode(mode: str) -> str:
    return mode


def dims_for_mode(mode: str) -> list[str]:
    """Fx 'FIG' -> ['Fak', 'Inst', 'Stil']. Bruges to steder:
    - Mod pairs-parquet'en (kanter): hver dimension optræder med _1/_2-suffiks
      (fx 'Fak_1', 'Fak_2') - kald-stedet tilføjer selv suffikset.
      Se data/network.py::load_edges().
    - Mod KU_pub_long.parquet (node-forfatterantal): kolonnenavnene bruges
      direkte, uden suffiks. Se data/network.py::load_node_totals().
    """
    return [MODE_COLS[c] for c in _MODE_ORDER if c in mode]


# ---------------------------------------------------------------------------
# DATAKILDE: PAIRS-PARQUET (NYT)
# ---------------------------------------------------------------------------
# Denne app forespørger direkte på en dedikeret forfatterpar-parquet via
# DuckDB - én række pr. forfatterpar pr. publikation, med organisatoriske
# dimensioner for begge forfattere (Fak_1/Fak_2, Inst_1/Inst_2, Stil_1/Stil_2,
# Koen_1/Koen_2, Statsbg_1/Statsbg_2) samt et publikations-ID (PURE_ID), så
# publikationsantal kan skelnes fra forfatterpar-antal (se METRIC_* nedenfor).
#
# Filen er SEPARAT fra KU_pub_pairs_long.parquet (som bruges af
# KU_publikationer/tabs/sampublicering.py) - denne har ekstra Koen_*/Statsbg_*
# -kolonner, som Køn- og Nationaliteter-fanerne her læner sig op ad.

try:
    ERDA_ENABLED = st.secrets.get("erda", {}).get("use_erda", True)
except Exception:
    ERDA_ENABLED = True

if ERDA_ENABLED:
    _DATA_CACHE_DIR = Path(__file__).parent / "data_cache"
    PAIRS_PARQUET_PATH = str(_DATA_CACHE_DIR / "KU_CURIS_sampub_pairs_long.parquet")
    PUB_LONG_PARQUET_PATH = str(_DATA_CACHE_DIR / "KU_pub_long.parquet")
else:
    # Lokal udvikling: peg på dine egne, allerede byggede filer
    PAIRS_PARQUET_PATH = r"H:\Sampubliceringsapp\Data\KU_CURIS_sampub_pairs_long.parquet"
    PUB_LONG_PARQUET_PATH = r"H:\Sampubliceringsapp\Data\KU_pub_long.parquet"

# NB: PUB_LONG_PARQUET_PATH peger på SAMME fysiske fil som
# KU_publikationer/config.py's PARQUET_PATHS["CURIS"] - én række pr.
# forfatter pr. publikation, med Fak/Inst/Stil/Koen/Statsbg/Intern/ext_id.
# Lokalt (ERDA_ENABLED=False) ligger den under din egen Sampubliceringsapp\Data
# -mappe herover, adskilt fra H:\Sampubliceringsapp\GitHub (repoet) - samme
# konvention som du allerede bruger, så data aldrig havner i en
# git-sporet mappe. Vil du undgå at vedligeholde to lokale kopier af samme
# fil, kan du i stedet pege direkte på din eksisterende
# H:\Publikationsapp\Data\KU_pub_long.parquet - begge virker, det er blot et
# spørgsmål om du foretrækker ét delt eksemplar eller ét pr. projekt.
# Denne app synkroniserer sin egen lokale kopi fra ERDA (se
# data/loader.py::sync_data_from_erda), men kilden er delt med
# KU_publikationer. Bruges til NODE-forfatterantal (load_node_totals) -
# IKKE til kanterne, som stadig kommer fra PAIRS_PARQUET_PATH. Begrundelse:
# pairs-parquet'en dækker kun forfattere med mindst ét internt
# medforfatterskab, mens KU_pub_long.parquet dækker ALLE KU-forfattere,
# inkl. rene solo-publicister - se MIGRATION_MAP.md.


# ---------------------------------------------------------------------------
# METRIK: PUBLIKATIONER VS. FORFATTERPAR (NYT)
# ---------------------------------------------------------------------------
# Globalt valg i sidepanelet (filters['metric']) - bruges af ALLE faner, der
# forespørger på pairs-parquet'en, så tallene er konsistente på tværs af hele
# appen. I modsætning til KU_publikationer, hvor kun Sampublicering-fanen har
# dette valg lokalt, ligger det her i components/sidepanel.py.

METRIC_LABELS = {
    "forfatterpar": "Forfatterpar",
    "publikationer": "Publikationer",
}
METRIC_DEFAULT = "forfatterpar"


def metric_count_sql(metric: str, id_col: str = "PURE_ID") -> str:
    """SQL-optællingsudtryk: COUNT(*) for forfatterpar, COUNT(DISTINCT
    id_col) for publikationer. NB ved gruppering på et specifikt kant-par
    (fx Fak_1, Fak_2): en publikation med forfattere fra 3+ enheder tælles
    med i FLERE kanter samtidig - det er forventet, ikke en fejl, men husk
    det ved fortolkning. Se MIGRATION_MAP.md for uddybning."""
    return "COUNT(*)" if metric == "forfatterpar" else f"COUNT(DISTINCT {id_col})"


# ---------------------------------------------------------------------------
# FANER
# ---------------------------------------------------------------------------
# Grundfaner pr. mode. Ekstra-faner (Køn, Nationaliteter, Internationalt
# samarbejde, FWCI, Forskningsoutput) indsættes betinget i app.py ud fra
# sidepanel-valg - præcis som i den gamle main(), linje 3888-3903.

BASE_TABS_BY_MODE = {
    "F":   ["Oversigt", "Fakulteter", "Nøgleaktører", "Samarbejdsmønstre", "Netværksudvikling", "Datagrundlag"],
    "FI":  ["Oversigt", "Fakulteter", "Institutter", "Nøgleaktører", "Samarbejdsmønstre", "Netværksudvikling", "Datagrundlag"],
    "FIG": ["Oversigt", "Fakulteter", "Institutter", "Stillingsgrupper", "Nøgleaktører", "Samarbejdsmønstre", "Netværksudvikling", "Datagrundlag"],
    "FG":  ["Oversigt", "Fakulteter", "Stillingsgrupper", "Nøgleaktører", "Samarbejdsmønstre", "Netværksudvikling", "Datagrundlag"],
    "IG":  ["Oversigt", "Institutter", "Stillingsgrupper", "Nøgleaktører", "Samarbejdsmønstre", "Netværksudvikling", "Datagrundlag"],
    "I":   ["Oversigt", "Institutter", "Nøgleaktører", "Samarbejdsmønstre", "Netværksudvikling", "Datagrundlag"],
    "G":   ["Oversigt", "Stillingsgrupper", "Nøgleaktører", "Samarbejdsmønstre", "Netværksudvikling", "Datagrundlag"],
    "FS": ["Oversigt", "Datagrundlag"], "IS": ["Oversigt", "Datagrundlag"], "GS": ["Oversigt", "Datagrundlag"],
    "FIS": ["Oversigt", "Datagrundlag"], "FIGS": ["Oversigt", "Datagrundlag"],
    "FGS": ["Oversigt", "Datagrundlag"], "IGS": ["Oversigt", "Datagrundlag"],
    "FN": ["Oversigt", "Datagrundlag"], "IN": ["Oversigt", "Datagrundlag"], "GN": ["Oversigt", "Datagrundlag"],
    "FIN": ["Oversigt", "Datagrundlag"], "FIGN": ["Oversigt", "Datagrundlag"],
    "FGN": ["Oversigt", "Datagrundlag"], "IGN": ["Oversigt", "Datagrundlag"],
    # Køn OG Statsborgerskab samtidig (mulige siden sidepanelet bruger
    # uafhængige checkbokse for de to, ikke et gensidigt udelukkende radio-valg):
    "SN": ["Oversigt", "Datagrundlag"],
    "FSN": ["Oversigt", "Datagrundlag"], "ISN": ["Oversigt", "Datagrundlag"], "GSN": ["Oversigt", "Datagrundlag"],
    "FISN": ["Oversigt", "Datagrundlag"], "FIGSN": ["Oversigt", "Datagrundlag"],
    "FGSN": ["Oversigt", "Datagrundlag"], "IGSN": ["Oversigt", "Datagrundlag"],
}

# ---------------------------------------------------------------------------
# NATIONALITETER
# ---------------------------------------------------------------------------

_COUNTRY_NAMES_DA = {
    "DK": "Danmark", "D": "Tyskland", "CN": "Kina", "I": "Italien",
    "GB": "Storbritannien", "E": "Spanien", "USA": "USA", "S": "Sverige",
    "NL": "Holland", "IND": "Indien", "F": "Frankrig", "GR": "Grækenland",
    "N": "Norge", "PL": "Polen", "IR": "Iran", "AUS": "Australien",
    "CDN": "Canada", "P": "Portugal", "BR": "Brasilien", "B": "Belgien",
    "RUS": "Rusland", "SF": "Finland", "A": "Østrig", "IRL": "Irland",
    "CH": "Schweiz", "MEX": "Mexico", "J": "Japan", "TR": "Tyrkiet",
    "PAK": "Pakistan", "ROK": "Sydkorea", "R": "Rumænien", "LTU": "Litauen",
    "IS": "Island", "H": "Ungarn", "ETH": "Etiopien", "RCH": "Chile",
    "CZE": "Tjekkiet", "CO": "Colombia", "HRV": "Kroatien", "BG": "Bulgarien",
    "IL": "Israel", "UKR": "Ukraine", "NEP": "Nepal", "LVA": "Letland",
    "SVN": "Slovenien", "SVK": "Slovakiet", "EST": "Estland", "SRB": "Serbien",
    "VN": "Vietnam", "PE": "Peru", "RI": "Indonesien", "ZA": "Sydafrika",
    "ET": "Egypten", "T": "Thailand", "AR": "Argentina", "NZ": "New Zealand",
    "PI": "Filippinerne", "ZW": "Zimbabwe", "EAK": "Kenya", "RC": "Taiwan",
    "ARM": "Armenien", "RL": "Libanon", "MAL": "Malaysia", "BD": "Bangladesh",
    "GH": "Ghana", "SGP": "Singapore", "HKJ": "Jordan", "GDA": "Ukendt",
    "BHU": "Bhutan", "MOZ": "Mozambique", "CL": "Sri Lanka", "L": "Luxembourg",
    "UZB": "Usbekistan", "EAT": "Tanzania", "BH": "Bahrain", "EC": "Ecuador",
    "DY": "Benin", "MDA": "Moldova", "RWA": "Rwanda", "EAU": "Uganda",
    "YV": "Venezuela", "MS": "Mauritius", "BLR": "Belarus", "AL": "Albanien",
    "BIH": "Bosnien-Hercegovina", "SN": "Senegal", "YMN": "Yemen",
    "WAN": "Nigeria", "KAZ": "Kasakhstan", "SU": "Sovjetunionen",
    "MAK": "Nordmakedonien", "MDG": "Madagaskar", "SWA": "Namibia",
    "CY": "Cypern", "BOL": "Bolivia", "DZ": "Algeriet", "SYR": "Syrien",
    "KWT": "Kuwait", "GEO": "Georgien", "TN": "Tunesien",
    "DOM": "Dominikanske Republik", "CAM": "Cameroun", "NIC": "Nicaragua",
    "FL": "Liechtenstein", "MA": "Marokko", "OMN": "Oman", "Ukendt": "Ukendt",
}


def country_name(code: str) -> str:
    """Returnér det danske landenavn for en bil-kendingskode. Falder tilbage til koden selv hvis ukendt."""
    if not code:
        return "Ukendt"
    return _COUNTRY_NAMES_DA.get(code, _COUNTRY_NAMES_DA.get(code.upper(), code))