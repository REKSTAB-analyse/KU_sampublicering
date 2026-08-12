import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from config import METRIC_LABELS, METRIC_DEFAULT, FAC_ORDER, GROUP_ORDER, CPR, country_name
from data.loader import load_inst_filter, load_pub_filter_options, load_max_author_count, load_statsborgerskab_options, load_year_range

def render_sidepanel() -> dict:
    filters = {}

    with st.sidebar:
        st.header("Filtre og visning")
        st.caption(
"""
Appen kortlægger sampubliceringsmønstre blandt KU's videnskabelige personale. Brug filtrene til at zoome ind på et bestemt fakultet, 
karrieretrin, år eller tilføje en diversitetsdimension.
"""
        )

        # --- Metrik: globalt for hele appen ---
        metric_label = st.radio(
            "Vis som:",
            options=list(METRIC_LABELS.values()),
            index=list(METRIC_LABELS.keys()).index(METRIC_DEFAULT),
            horizontal=True,
            key="global_metric",
        )
        metric = next(k for k, v in METRIC_LABELS.items() if v == metric_label)
        filters["metric"] = metric
        if metric == "publikationer":
            st.caption(
                "**Publikationer** tæller hver publikation præcis én gang. "
                "**Forfatterpar** vejer publikationer med mange interne "
                "medforfattere tungere - se Datagrundlag-fanen for forklaring."
            )
    
    return filters