from ui.i18n_es import tr
from ui.adapters import MARKETS


def render(st, vm, enabled_symbols):
    st.header(tr("14-DAY AUTONOMOUS DEMO TEST"))
    st.info(tr("EXPERIMENT NOT STARTED"))
    st.write("Símbolos activos:", ", ".join(enabled_symbols))
    st.write("Soportados pero no activos:", ", ".join(s for s in MARKETS if s not in enabled_symbols) or "—")
    st.caption(tr("No scheduler, durable run history, or experiment metrics are configured. Segment analysis awaits observed data."))
