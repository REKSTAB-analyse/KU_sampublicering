import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
from config import METRIC_LABELS, METRIC_DEFAULT, FAC_ORDER, GROUP_ORDER, CPR, country_name, base_mode, dims_for_mode
from data.loader import load_institut_options, load_pub_filter_options, load_max_author_count, load_statsborgerskab_options, load_year_range

def render_sidepanel() -> dict:
    filters = {}

    with st.sidebar:
        st.header("Filtre og visning")
        st.caption(
"""
Brug filtrene nedenfor til at zoome ind på et bestemt fakultet, karrieretrin, publikationstype, år 
eller tilføje en diversitetsdimension.
"""
        )

        # --- Metrik: globalt for hele appen ---
        metric_label = st.radio(
            "**Vælg sampubliceringsmetrik**",
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
            )
        elif metric == "forfatterpar":
            st.caption(
                "**Forfatterpar** vejer publikationer med mange interne "
                "medforfattere tungere - se Datagrundlag-fanen for forklaring."
            )
        
        with st.expander("**Datakilde**"):
            data_source = st.radio(
                "Vælg datakilde",
                options=["CURIS", "OpenAlex", "SciVal"],
                index=0,
                key="data_source_radio",
                help="Vælg datakilden, som skal ligge til grund for analyserne"
            )
            filters["data_source"] = data_source
            #opts = load_filter_options(data_source)

            year_min = 2021
            year_max = 2025 #max(opts["year"])
            aar_range = st.slider(
                "Vælg udgivelsesår",
                min_value=year_min,
                max_value=year_max,
                value=(year_min, year_max),
                key="sp_aar"
            )
            filters["aar_fra"] = aar_range[0]
            filters["aar_til"] = aar_range[1]

        with st.expander("**Organisation**"):
            st.caption(
"""
Fakulteter og institutter er administrative enheder. 

Stillingsgrupper opdeler efter karrieretrin. 
"""
            )
            show_fac = st.checkbox(
                "**Fakulteter**",
                key="cb_fac", value=True,
            )
            show_inst = st.checkbox(
                "**Institutter**",
                key="cb_inst", value=False,
            )
            show_grp = st.checkbox(
                "**Stillingsgrupper**",
                key="cb_grp", value=False,
            )

            if show_fac:
                valgte_fak = st.multiselect(
                    "Vælg fakulteter (tom = alle)",
                    options=FAC_ORDER, default=[], key="sp_fakultet"
                )
                filters["fakultet"] = valgte_fak or FAC_ORDER
                filters["fakultet_explicit"] = bool(valgte_fak)
            else:
                filters["fakultet"] = FAC_ORDER
                filters["fakultet_explicit"] = False
            
            institut_opts = load_institut_options(filters["fakultet"])
            if show_inst:
                valgte_inst = st.multiselect(
                    "Vælg institut (tom = alle)",
                    options=institut_opts, default=[], key="sp_institut",
                )
                filters["institutter"] = valgte_inst or institut_opts or ["__INGEN_INSTITUT__"]
                filters["institutter_explicit"] = bool(valgte_inst)
            else:
                filters["institutter"] = institut_opts or ["__INGEN_INSTITUT__"]
                filters["institutter_explicit"] = False

            if show_grp:
                valgte_stil = st.multiselect(
                    "Vælg stillingsgruppe (tom = alle)",
                    options=GROUP_ORDER, default=[], key="sp_stillingsgrupper",
                )
                filters["stillingsgrupper"] = valgte_stil or GROUP_ORDER
                filters["stillingsgrupper_explicit"] = bool(valgte_stil)
            else:
                filters["stillingsgrupper"] = GROUP_ORDER
                filters["stillingsgrupper_explicit"] = False
            
        with st.expander("**Diversitet**"):
            st.caption(
"""
Aktivér for at tilføje køns- og statsborgerskabfaner. 

Filtrene på køn og statsborgerskab negrænser, hvilke forfattere der indgår i netværket og analyserne.
"""
            )
            diversitetsvalg = st.radio(
                "Tilføj diversitetsdimension",
                options=["Ingen", "Køn", "Statsborgerskab"],
                index=0,
                key="sp_diversitet",
                help=(
                    "Køn og statsborgerskab kombineres ikke i netværksvisningen."
                ),
            )
            show_koen = diversitetsvalg == "Køn"
            show_statsbg = diversitetsvalg == "Statsborgerskab"
 
            filters["vis_koen"] = show_koen
            filters["vis_statsborgerskab"] = show_statsbg
 
            # TODO: bekræft at config.CPR's nøgler ('m'/'k') matcher
            # store/små bogstaver i Koen-kolonnen i jeres parquet.
            if show_koen:
                valgte_koen = st.multiselect(
                    "Vælg køn (tom = begge)",
                    options=["Kvinder", "Mænd"], default=[], key="sp_koen"
                )
                filters["køn"] = valgte_koen or ["Kvinder", "Mænd"]
                filters["køn_explicit"] = bool(valgte_koen)
            else:
                filters["køn"] = ["Kvinder", "Mænd"]
                filters["køn_explicit"] = False
 
            statsbg_koder = load_statsborgerskab_options()
            if show_statsbg:
                statsbg_labels = {kode: country_name(kode) for kode in statsbg_koder}
                label_to_kode = {v: k for k, v in statsbg_labels.items()}
                valgte_labels = st.multiselect(
                    "Vælg statsborgerskab (tom = alle)",
                    options=sorted(statsbg_labels.values()), default=[],
                    key="sp_statsborgerskab",
                )
                valgte_koder = [label_to_kode[v] for v in valgte_labels]
                filters["statsborgerskab"] = valgte_koder or statsbg_koder
                filters["statsborgerskab_explicit"] = bool(valgte_koder)
            else:
                filters["statsborgerskab"] = statsbg_koder
                filters["statsborgerskab_explicit"] = False

        mode = (
            ("F" if show_fac else "")
            + ("I" if show_inst else "")
            + ("G" if show_grp else "")
            + ("S" if show_koen else "")
            + ("N" if show_statsbg else "")
        )
        filters["mode"] = mode or "F"

        with st.expander("**Intra/inter-filtrering**"):
            st.caption(
"""
Vælg, om et forfatterpar inden for samme enhed (intra) og/eller på tværs af enheder (inter)
skal indgå - uafhængigt for hver aktiv dimension. 
"""
            )
            _DIM_LABELS = {"Fak": "fakultet", "Inst": "institut", "Stil": "stillingsgruppe",
                            "Koen": "køn", "Statsbg": "statsborgerskab"}
            edge_type_filters = {}
            for dim in dims_for_mode(mode):
                label = _DIM_LABELS.get(dim, dim.lower())
                valgt = st.multiselect(
                    f"Vis {label}: intra / inter",
                    options=["Intra", "Inter"], default=["Intra", "Inter"],
                    key=f"sp_edgetype_{dim}",
                )
                edge_type_filters[dim] = [v.lower() for v in valgt] or ["intra", "inter"]
            filters["edge_type_filters"] = edge_type_filters

        with st.expander("**Publikationstype og adgang**"):
            st.caption(
                "Filtrene begrænser, hvilke publikationer der vises i netværket og indgår i analyserne."
            )
            opts = load_pub_filter_options()
 
            valgte_typer = st.multiselect(
                "Publikationstype (tom = alle)",
                options=opts["typer"], default=[], key="sp_type",
            )
            filters["typer"] = valgte_typer or opts["typer"]
 
            valgte_indholds = st.multiselect(
                "Indholdstype (tom = alle)",
                options=opts["indholds"], default=[], key="sp_indholds",
            )
            filters["indholdstyper"] = valgte_indholds or opts["indholds"]
 
            valgte_sprog = st.multiselect(
                "Sprog (tom = alle)",
                options=opts["sprog"], default=[], key="sp_sprog",
            )
            filters["sprog"] = valgte_sprog or opts["sprog"]
 
            valgte_peer = st.multiselect(
                "Peer review (tom = alle)",
                options=["Peer reviewed", "Ikke peer reviewed", "Ukendt"],
                default=[], key="sp_peer",
            )
            peer_map = {"Peer reviewed": "Ja", "Ikke peer reviewed": "Nej", "Ukendt": "Ukendt"}
            filters["peer"] = [peer_map[v] for v in valgte_peer] if valgte_peer else ["Ja", "Nej", "Ukendt"]
 
            valgte_oa = st.multiselect(
                "Open access (tom = alle)",
                options=opts["open_access"], default=[], key="sp_oa",
            )
            filters["open_access"] = valgte_oa or opts["open_access"]
 
            valgte_doi = st.multiselect(
                "Har DOI (tom = alle)",
                options=["Ja", "Nej"], default=[], key="sp_har_doi",
            )
            filters["har_doi"] = valgte_doi or ["Ja", "Nej"]
 
        with st.expander("**Forfatterantal**"):
            st.caption(
                "Filtrer publikationer efter det samlede antal forfattere (interne og eksterne tilsammen)."
            )
            MIN_FORFATTERE = 1
            max_forf_i_data = max(load_max_author_count(), MIN_FORFATTERE)
            if max_forf_i_data <= MIN_FORFATTERE:
                filters["min_forfattere"] = MIN_FORFATTERE
                filters["max_forfattere"] = max_forf_i_data
            else:
                forf_range = st.slider(
                    "Antal forfattere",
                    min_value=MIN_FORFATTERE, max_value=max_forf_i_data,
                    value=(MIN_FORFATTERE, max_forf_i_data),
                    key="sp_forf_range",
                )
                filters["min_forfattere"] = forf_range[0]
                filters["max_forfattere"] = forf_range[1]
 
        with st.expander("**Netværksvisning**"):
            st.caption("Justerer selve netværksplottets visning - påvirker kun visualiseringen, ikke de underliggende tal.")

            edge_scale = st.slider(
                "Vægt:",
                min_value=1.0, max_value=50.0, value=20.0, step=0.1,
                key="edge_scale_slider",
                help="Skalerer linjetykkelsen i netværket. Påvirker kun visualiseringen, ikke de underliggende tal.",
            )
            filters["edge_scale"] = edge_scale

            _default_scale = {"I": 400, "F": 800, "G": 400}.get(base_mode(mode), 1200)
            _scale_key = f"network_scale_default_{base_mode(mode)}"
            network_scale = st.slider(
                "Netværksstørrelse",
                min_value=100, max_value=5000,
                value=st.session_state.get(_scale_key, _default_scale),
                key="network_scale_slider",
                help="Justerer størrelsen af hele netværksplottet. Øg værdien, hvis noder overlapper.",
            )
            st.session_state[_scale_key] = network_scale
            filters["network_scale"] = network_scale
 
    return filters