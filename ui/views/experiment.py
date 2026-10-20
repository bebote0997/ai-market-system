from datetime import datetime, timezone
import os

from ui import i18n_es as es
from ui.i18n_es import tr
from ui.adapters import MARKETS

DEMO_LENGTH_DAYS = 30


def demo_progress_text(now=None):
    """"Día N de 30" of the OANDA demo (from the first routed trade) while broker-demo routing is ON; None when OFF."""
    if os.environ.get("AI_FLOOR_BROKER_DEMO_ROUTING") != "1":
        return None
    try:
        from execution.demo_dashboard import demo_progress
        _epoch, _first, day = demo_progress(now or datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001 - routing ON but the demo view is unavailable: no day counter
        return "Demo OANDA: sin datos"
    text = es.demo_day_text(day, DEMO_LENGTH_DAYS)
    return text if text == es.DEMO_NO_TRADES else f"Demo OANDA: {text}"


def render(st, vm, enabled_symbols):
    st.header(tr("30-DAY AUTONOMOUS DEMO TEST"))
    st.info(tr("30-DAY DEMO IN PROGRESS"))
    progress = demo_progress_text()
    if progress is not None:
        st.write(progress)
    st.write("Símbolos activos:", ", ".join(enabled_symbols))
    st.write("Soportados pero no activos:", ", ".join(s for s in MARKETS if s not in enabled_symbols) or "—")
    st.caption(tr("The demo runs on the cloud scheduler with durable history. Metrics and the OANDA demo evidence report grow with observed trades."))
