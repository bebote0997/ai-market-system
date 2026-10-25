from ui.i18n_es import readable, tr
from ui.tables import dataframe
from ui.components import panels


def render(st, vm):
    chart_col, assistant_col = st.columns([3, 1])
    with chart_col:
        panels.chart(st, vm)
    with assistant_col:
        panels.assistant(st, vm)
        panels.agents(st, vm)
    setup_col, risk_col, position_col = st.columns(3)
    with setup_col:
        panels.setup(st, vm)
    with risk_col:
        panels.risk(st, vm)
    with position_col:
        panels.positions(st, vm)
    panels.pipeline(st, vm)
    with st.expander(tr("MACRO / NEWS · FACT vs AI INTERPRETATION")):
        st.caption(tr("FACT"))
        if vm.macro_facts:
            dataframe(st, vm.macro_facts, width="stretch")
        else:
            st.write(tr("NO_DATA — no sourced event facts in this snapshot."))
        st.caption(tr("AI INTERPRETATION"))
        macro = vm.agents[2]
        st.write(readable(macro.reasoning_summary))
        st.write("Confianza:", macro.confidence if macro.confidence is not None else "—")
        st.write("Evidencia relacionada:", readable(macro.evidence))
    st.subheader(tr("JOURNAL PREVIEW"))
    if vm.journal:
        dataframe(st, [{"timestamp": event.timestamp, "source": event.entity_id,
                        "event": event.event_type, "details": event.details} for event in vm.journal[-3:]],
                  hide_index=True, width="stretch")
    else:
        st.write(tr("No journal events in this snapshot."))
