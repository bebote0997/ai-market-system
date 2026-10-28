"""Background producer of the read-only OANDA demo caches that the Floor peeks (``ui.floor_cache``). Display only.

The Floor never fetches: it only reads the module-level TTL caches of ``execution.oanda_readonly`` (``_TRADES`` keyed
by the demo epoch, ``_VIEWS`` keyed by ``"practice"``). Without a producer those caches are filled only when someone
opens another page, so a phone / wall screen / Slack link that lands straight on the Floor saw "sin datos" or old data.

``ensure_started(env=None)`` (idempotent: module lock + flag) starts ONE daemon thread in the dashboard process: at
server start from the ASGI lifespan (``ui/asgi_app.py``), and again (a no-op once running) from ``ui/app.py`` and the
Floor view. It starts only while the dashboard shows OANDA demo results (broker-demo routing ON or
``AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO``: exactly when the Floor peeks the caches), the demo epoch is known and the
practice read credentials are present (presence only; never logged). Every ``INTERVAL_SECONDS`` it calls the existing
``cached_demo_trades(epoch)`` and ``cached_account_view("practice")`` with the SAME keys the Floor peeks. Those caches
keep their 10 s TTL, so the GET rate is bounded by the TTL, not by this loop: one summary GET + the trades GETs (CLOSED
+ OPEN) per TTL window, about 3 GETs per 10 s. The short loop only narrows the moment after a TTL expiry in which a
page visitor would refresh (and wait) instead of the producer. No new endpoint or request type, no writes, no orders.

Each iteration is guarded (only the exception TYPE is logged) and never raises; after ``FAILURES_BEFORE_BACKOFF``
consecutive failed reads it waits ``BACKOFF_SECONDS``. Sleep and readers are injectable; ``stop()`` (tests).
``warming_up()``: running but the Floor's entries are still absent, i.e. never read (a stored failed read is not absent):
``ui/app.py`` then retries the Floor after ``FIRST_RETRY`` instead of 60 s, and the Floor shows "Cargando datos del
demo…".
"""
from contextlib import asynccontextmanager
import logging
import os
import threading

INTERVAL_SECONDS = 2.0
BACKOFF_SECONDS = 30.0
FAILURES_BEFORE_BACKOFF = 3
THREAD_NAME = "floor-demo-refresher"
FIRST_RETRY = "8s"  # Floor fragment refresh while the caches are still cold (then back to 60 s)
IDLE_REFRESH = "60s"  # page auto-refresh with no open trade
OPEN_REFRESH = "30s"  # page auto-refresh while an OPEN trade is cached (the broker cache TTL stays 10 s)

log = logging.getLogger(__name__)
_lock = threading.Lock()
_stop = threading.Event()
_thread = None
_epoch = None  # the ``since`` key of the running producer


def demo_results_shown(env):
    """Same rule as the Floor / dashboard (``luxury.demo_api``): routing ON or results source OANDA_DEMO."""
    try:
        from execution.results_source import demo_results
        return bool(demo_results(env))
    except Exception:  # noqa: BLE001 - unreadable flag: the routing flag alone
        return env.get("AI_FLOOR_BROKER_DEMO_ROUTING") == "1"


def credentials_present(env):
    """True when the practice token and account id are set (presence only: the values are never read further)."""
    from execution.oanda_readonly import ENV_NAMES
    return all(isinstance(env.get(name), str) and env.get(name).strip() for name in ENV_NAMES["practice"])


def demo_epoch_key(env):
    """The demo epoch (aware UTC datetime): the exact ``since`` key the Floor peeks; None when unknown."""
    from execution.demo_epoch import demo_epoch
    epoch = demo_epoch(env)
    return epoch if getattr(epoch, "tzinfo", None) is not None else None


def should_run(env):
    """(ok, epoch). Never raises."""
    try:
        if not demo_results_shown(env) or not credentials_present(env):
            return False, None
        epoch = demo_epoch_key(env)
        return epoch is not None, epoch
    except Exception:  # noqa: BLE001
        return False, None


def default_readers():
    from execution.oanda_readonly import cached_account_view, cached_demo_trades
    return cached_demo_trades, cached_account_view


def refresh_once(epoch, trades_reader, account_reader):
    """One pass over both caches; True when both reads are usable. A ``stale`` OK view (the last good view kept after
    a failed refresh) is still shown by the pages but counts as a failed read here (backoff). Never raises."""
    try:
        trades = trades_reader(epoch)
        view = account_reader("practice")
    except Exception as error:  # noqa: BLE001 - only the type: never broker text, URLs or secrets
        log.warning("floor demo refresher: read failed (%s)", type(error).__name__)
        return False
    trades_ok = isinstance(trades, dict) and trades.get("error") is None
    return trades_ok and getattr(view, "status", None) == "OK" and not getattr(view, "stale", False)


def run(epoch, trades_reader, account_reader, *, sleep, stop_event, interval=INTERVAL_SECONDS,
        backoff=BACKOFF_SECONDS, failures_before_backoff=FAILURES_BEFORE_BACKOFF):
    """The loop: refresh, then sleep ``interval`` (``backoff`` after repeated failures) until ``stop_event``."""
    failures = 0
    while not stop_event.is_set():
        failures = 0 if refresh_once(epoch, trades_reader, account_reader) else failures + 1
        delay = backoff if failures >= failures_before_backoff else interval
        try:
            sleep(delay)
        except Exception:  # noqa: BLE001 - a broken sleep must not spin
            stop_event.wait(delay)


def ensure_started(env=None, *, sleep=None, trades_reader=None, account_reader=None, interval=INTERVAL_SECONDS,
                   backoff=BACKOFF_SECONDS):
    """Start the producer thread once (no-op while it runs, or when the demo / epoch / credentials are missing).
    Returns True when the thread is running after the call. Never raises."""
    global _thread, _epoch
    try:
        env = os.environ if env is None else env
        with _lock:
            if _thread is not None and _thread.is_alive():
                return True
            ok, epoch = should_run(env)
            if not ok:
                return False
            if trades_reader is None or account_reader is None:
                default_trades, default_account = default_readers()
                trades_reader = trades_reader or default_trades
                account_reader = account_reader or default_account
            _stop.clear()
            _epoch = epoch
            _thread = threading.Thread(
                target=run, name=THREAD_NAME, daemon=True, args=(epoch, trades_reader, account_reader),
                kwargs={"sleep": sleep or _stop.wait, "stop_event": _stop, "interval": interval, "backoff": backoff})
            _thread.start()
            log.info("floor demo refresher started")
            return True
    except Exception as error:  # noqa: BLE001 - the dashboard never fails because of the producer
        log.warning("floor demo refresher: not started (%s)", type(error).__name__)
        return False


def running():
    return _thread is not None and _thread.is_alive()


def warming_up():
    """True while the producer runs but a Floor cache entry (trades for the epoch / practice view) is still absent:
    the first paint after a process start. A stored failed read (None / ERROR) is not warming up: the Floor goes back
    to the 60 s refresh and shows "sin datos". Peek only (``ui.floor_cache``); never raises."""
    try:
        if not running():
            return False
        from ui.floor_cache import read_attempted
        return not read_attempted("_TRADES", _epoch) or not read_attempted("_VIEWS", "practice")
    except Exception:  # noqa: BLE001
        return False


def has_open_trade():
    """True when the cached demo trades (peek only, never a broker call) hold at least one OPEN trade. Never raises."""
    try:
        if not running():
            return False
        from ui.floor_cache import peek_demo_trades
        data = peek_demo_trades(_epoch)
        return isinstance(data, dict) and data.get("error") is None and bool(data.get("open"))
    except Exception:  # noqa: BLE001
        return False


def open_trade_refresh():
    """Page auto-refresh interval: ``OPEN_REFRESH`` while a trade is open (fresher unrealized P&L), else ``IDLE_REFRESH``.
    Display timing only: the OANDA read cache keeps its 10 s TTL, so the GET rate does not change."""
    return OPEN_REFRESH if has_open_trade() else IDLE_REFRESH


def stop(timeout=5.0):
    """Stop the thread (tests / server shutdown). Never raises."""
    global _thread
    _stop.set()
    thread = _thread
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout)
    with _lock:
        if _thread is thread and (thread is None or not thread.is_alive()):
            _thread = None


@asynccontextmanager
async def lifespan(_app):
    """ASGI lifespan for ``st.App`` (``ui/asgi_app.py``): start the producer with the server, stop it on shutdown."""
    ensure_started()
    try:
        yield None
    finally:
        stop(timeout=1.0)
