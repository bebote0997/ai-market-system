from datetime import datetime, timezone
import os

from ui import i18n_es as es
from ui.i18n_es import tr
from ui.adapters import MARKETS

DEMO_LENGTH_DAYS = 30
DEMO_NO_DATA = "Demo OANDA: sin datos"


def demo_progress_text(now=None):
    """"Cuenta de práctica OANDA Demo desde <epoch ET>; Día N de 30" while broker-demo routing is ON or the results
    source is OANDA_DEMO (day 1 = the epoch's New York date, ``ui.demo_clock``); None otherwise. Never raises."""
    from execution.results_source import demo_results
    if not demo_results(os.environ):
        return None
    try:
        from execution.demo_epoch import demo_epoch
        from ui.demo_clock import demo_day_from_epoch
        epoch = demo_epoch(os.environ)
        day = demo_day_from_epoch(epoch, now or datetime.now(timezone.utc))
    except Exception:  # noqa: BLE001 - routing ON but the epoch is unreadable: no day counter
        return DEMO_NO_DATA
    if epoch is None:
        return DEMO_NO_DATA
    from ui.views.dashboard import FIRST_CYCLE_WAIT
    counter = es.demo_day_text(day, DEMO_LENGTH_DAYS) if day else FIRST_CYCLE_WAIT
    return f"Cuenta de práctica OANDA Demo desde {es.datetime_et(epoch)}; {counter}"


def demo_numbers(st, now=None):
    """The same real OANDA demo tiles and daily-loss card as the start page (``luxury.demo_kpis``)."""
    from ui.views import luxury
    demo = luxury.demo_api()
    if demo is None:
        return
    snapshot = demo["snapshot"](now or datetime.now(timezone.utc))
    luxury.kpi_grid(st, luxury.demo_kpis(snapshot))
    luxury.card(st, es.DAILY_LOSS_TITLE, es.daily_loss_rows(snapshot.get("daily_loss")))


def render(st, vm, enabled_symbols):
    st.header(tr("EXPERIMENT"))
    progress = demo_progress_text()
    st.info(progress if progress is not None else tr("30-DAY DEMO IN PROGRESS"))
    demo_numbers(st)
    st.write("Símbolos del experimento:", es.enabled_caption(enabled_symbols))
    st.write("Soportados pero no activos:", ", ".join(s for s in MARKETS if s not in enabled_symbols) or "—")
    st.caption(tr("The demo runs on the cloud scheduler with durable history. Metrics and the OANDA demo evidence report grow with observed trades."))
