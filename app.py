import streamlit as st

from config import dims_for_mode, BASE_TABS_BY_MODE
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

_TAB_RENDERERS = {
    "Oversigt": tab_oversigt.render,
    "Fakulteter": tab_fakulteter.render,
    "Institutter": tab_institutter.render,
    "Stillingsgrupper": tab_stillingsgrupper.render,
    "Nøgleaktører": tab_noegleaktoerer.render,
    "Samarbejdsmønstre": tab_samarbejdsmoenstre.render,
    "Netværksudvikling": tab_netvaerksudvikling.render,
    "Datagrundlag": tab_datagrundlag.render,
}

def main():
    st.set_page_config(
        page_title="KU Sampublicering",
        page_icon=load_logo(),
        layout="wide",
    )

    # --- Synkroniser pairs- og pub_long-parquet fra ERDA, før noget læser dem ---
    if ERDA_ENABLED:
        sync_data_from_erda()

    if "popup_bekraeftet" not in st.session_state:
        st.session_state.popup_bekraeftet = False

    @st.dialog("Velkommen til Sampublicering på Københavns Universitet")
    def _velkomst_popup():
        st.markdown(
"""
Forfatternes organisatoriske tilknytning (fakultet, institut, stillingsgruppe) er
baseret på HR-data, ikke selve publikationsdata. Det betyder, at tallene ikke
nødvendigvis stemmer overens med de tal, du bliver præsenteret for i andre KU-kilder.
"""
        )
        if st.button("OK", type="primary"):
            st.session_state.popup_bekraeftet = True
            st.rerun()

    if not st.session_state.popup_bekraeftet:
        _velkomst_popup()
        st.stop()

    col_logo, col_title = st.columns([1, 4])
    with col_logo:
        st.image(load_logo(), width=180)
    with col_title:
        st.title("Sampublicering på Københavns Universitet (beta)")

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
    
    # XX
    #from data.loader import get_cursor
    #st.write(
        #get_cursor().execute(
            #"SELECT DISTINCT Edge_type_fak, Edge_type_inst, Edge_type_stil "
            #"FROM pairs LIMIT 20"
        #).fetchall()
    #)

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

    tab_labels = BASE_TABS_BY_MODE.get(mode, ["Oversigt", "Datagrundlag"])
    tabs = st.tabs(tab_labels)
    tabs_dict = dict(zip(tab_labels, tabs))

    for label in tab_labels:
        renderer = _TAB_RENDERERS.get(label)
        if renderer is None:
            continue
        with tabs_dict[label]:
            renderer(filters)

if __name__ == "__main__":
    main()