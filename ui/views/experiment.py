from ui.i18n_es import tr
from ui.adapters import MARKETS


def render(st, vm, enabled_symbols):
    st.header(tr("30-DAY AUTONOMOUS DEMO TEST"))
    st.info(tr("30-DAY DEMO IN PROGRESS"))
    st.write("Símbolos activos:", ", ".join(enabled_symbols))
    st.write("Soportados pero no activos:", ", ".join(s for s in MARKETS if s not in enabled_symbols) or "—")
    st.caption(tr("The demo runs on the cloud scheduler with durable history. Metrics and the OANDA demo evidence report grow with observed trades."))
