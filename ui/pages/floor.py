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
    with st.expander("MACRO / NEWS · FACT vs AI INTERPRETATION"):
        st.caption("FACT")
        if vm.macro_facts:
            st.dataframe(vm.macro_facts, use_container_width=True)
        else:
            st.write("NO_DATA — no sourced event facts in this snapshot.")
        st.caption("AI INTERPRETATION")
        macro = vm.agents[2]
        st.write(macro.reasoning_summary or "NO_DATA")
        st.write("Confidence:", macro.confidence if macro.confidence is not None else "—")
        st.write("Linked evidence:", macro.evidence or "—")
    st.subheader("JOURNAL PREVIEW")
    st.write("No journal events in this snapshot." if not vm.journal else vm.journal[-3:])
