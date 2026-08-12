import streamlit as st

from config import BASE_TABS_BY_MODE, base_mode
from data.loader import load_logo, sync_data_from_erda, _DEPLOY_DATE, ERDA_ENABLED
from components.sidepanel import render_sidepanel

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
    "Køn": tab_koen.render,
    "Nationaliteter": tab_nationaliteter.render,
    "Internationalt samarbejde": tab_internationalt.render,
    "FWCI": tab_fwci.render,
    "Forskningsoutput": tab_forskningsoutput.render,
    "Netværksudvikling": tab_netvaerksudvikling.render,
    "Datagrundlag": tab_datagrundlag.render,
}

def _tabs_to_show(mode: str, filters: dict) -> list[str]:
    """TODO (fase 2): port den betingede indsættelse af Køn/Nationaliteter/
    Internationalt samarbejde/FWCI/Forskningsoutput fra gl. main(),
    linje 3888-3903. Lige nu vises kun grundfanerne fra BASE_TABS_BY_MODE.
    """
    return BASE_TABS_BY_MODE.get(mode) or BASE_TABS_BY_MODE.get(base_mode(mode), ["Oversigt"])

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


if __name__ == "__main__":
    main()