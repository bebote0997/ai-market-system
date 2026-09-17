from ui.adapters import MARKETS, symbol_enablement


def render(st, vm, enabled_symbols):
    st.header("14-DAY AUTONOMOUS DEMO TEST")
    st.info("EXPERIMENT NOT STARTED")
    st.write("Enabled symbols:", ", ".join(enabled_symbols))
    st.write("Supported but not enabled:", ", ".join(s for s in MARKETS if s not in enabled_symbols) or "—")
    st.caption("No scheduler, durable run history, or experiment metrics are configured. Segment analysis awaits observed data.")
