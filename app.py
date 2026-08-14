import streamlit as st

from config import dims_for_mode
from data.loader import load_logo, sync_data_from_erda, _DEPLOY_DATE, ERDA_ENABLED
from data.network import load_edges, load_node_totals
from components.sidepanel import render_sidepanel
from components.network_view import render_pyvis_network

import tabs.oversigt as tab_oversigt
import tabs.fakulteter as tab_fakulteter
import tabs.institutter as tab_institutter
import tabs.stillingsgrupper as tab_stillingsgrupper
import tabs.noegleaktoerer as tab_noegleaktoerer
import tabs.samarbejdsmoenstre as tab_samarbejdsmoenstre
import tabs.koen as tab_koen
import tabs.nationaliteter as tab_nationaliteter
import tabs.internationalt as tab_internationalt
import tabs.fwci as tab_fwci
import tabs.forskningsoutput as tab_forskningsoutput
import tabs.netvaerksudvikling as tab_netvaerksudvikling
import tabs.datagrundlag as tab_datagrundlag

def main():
    st.set_page_config(
        page_title="KU Sampublicering",
        page_icon=load_logo(),
        layout="wide",
    )

    # --- Synkroniser pairs- og pub_long-parquet fra ERDA, før noget læser dem ---
    if ERDA_ENABLED:
        sync_data_from_erda()

    col_logo, col_title = st.columns([1, 4])
    with col_logo:
        st.image(load_logo(), width=180)
    with col_title:
        st.title("Sampublicering på Københavns Universitet")

    # --- Skriftstørrelse i widgets (undtagen sidepanelet) ---
    st.markdown(
        """
        <style>
        [data-testid="stWidgetLabel"] p {
            font-size: 1rem !important;
            font-weight: 600 !important;
        }
        [data-testid="stSidebar"] [data-testid="stWidgetLabel"] p {
            font-size: unset !important;
            font-weight: unset !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    # --- Sidepanel med aktive filtre ---
    filters = render_sidepanel()
 
    # ---------------------------------------------------------------------
    # MIDLERTIDIG: direkte netværkstest gennem det rigtige sidepanel.
    # Erstat med rigtig fane-dispatch (se MIGRATION_MAP.md), når I er klar
    # til at bygge Oversigt/Fakulteter/osv. for alvor.
    # ---------------------------------------------------------------------
    st.divider()
    mode = filters["mode"]
    dims = dims_for_mode(mode)
 
    edges = load_edges(filters, mode)
    st.caption(f"Mode: `{mode}` · {len(edges)} kanter matcher de valgte filtre")
 
    node_totals = load_node_totals(filters, mode)
    render_pyvis_network(
        edges, dims, mode,
        node_sizes=node_totals,
        network_scale=filters["network_scale"],
        edge_scale=filters["edge_scale"],
        metric=filters["metric"],
    )


if __name__ == "__main__":
    main()