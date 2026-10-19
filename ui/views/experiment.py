from datetime import datetime, timezone

from ui.i18n_es import tr
from ui.adapters import MARKETS

DEMO_LENGTH_DAYS = 30


def demo_progress_text(now=None):
    """"Día N de 30" of the OANDA demo (from the first routed trade) while broker-demo routing is ON; None when OFF."""
    try:
        from execution.demo_dashboard import demo_mode, demo_progress
        if not demo_mode():
            return None
        _epoch, _first, day = demo_progress(now or datetime.now(timezone.utc))
    except Exception:  # The demo view is optional: unavailable => the page exactly as before.
        return None
    return f"Demo OANDA: Día {day} de {DEMO_LENGTH_DAYS}" if day else "Demo OANDA: aún sin operaciones del demo"


def render(st, vm, enabled_symbols):
    st.header(tr("30-DAY AUTONOMOUS DEMO TEST"))
    st.info(tr("30-DAY DEMO IN PROGRESS"))
    progress = demo_progress_text()
    if progress is not None:
        st.write(progress)
    st.write("Símbolos activos:", ", ".join(enabled_symbols))
    st.write("Soportados pero no activos:", ", ".join(s for s in MARKETS if s not in enabled_symbols) or "—")
    st.caption(tr("The demo runs on the cloud scheduler with durable history. Metrics and the OANDA demo evidence report grow with observed trades."))
