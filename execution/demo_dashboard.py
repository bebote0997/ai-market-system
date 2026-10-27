"""Read-only OANDA demo view for the dashboard (the ONLY broker entry point of the Overview/Trades/Decisions/experiment
screens; imported lazily and guarded, see test_broker_wiring.py).

Demo mode = ``broker_demo_state.routing_flag`` (``AI_FLOOR_BROKER_DEMO_ROUTING == "1"``). In demo mode the dashboard
numbers come ONLY from the OANDA practice account since ``demo_epoch``: GET summary (NAV/balance) + GET trades via
``execution.oanda_readonly`` (cached, never raises) and the router's ``broker_demo_trades`` rows (``mode=ro``). A failed
read is "sin datos" (``stats`` None), never PAPER numbers. No writes, no orders.
"""
import os

from execution.broker_demo_state import routing_flag
from execution.demo_epoch import demo_day_from_epoch, demo_epoch
from execution.demo_metrics import (as_float, daily_loss_view, demo_capital, demo_rows, demo_statistics,
                                    first_demo_open, load_loss_flags, load_risk_rows)


def demo_mode(env=None):
    return routing_flag(os.environ if env is None else env)


def state_db_path(env=None):
    from execution.broker_config import load_broker_config
    return load_broker_config(os.environ if env is None else env).state_db_path


def _risk_rows(env):
    try:
        return load_risk_rows(state_db_path(env))
    except Exception:  # noqa: BLE001 - unreadable config/DB: no risk data
        return {}


def _loss_flags(env):
    try:
        return load_loss_flags(state_db_path(env))
    except Exception:  # noqa: BLE001 - unreadable config/DB: unknown state
        return None


def demo_snapshot(now, env=None, *, trades_reader=None, account_reader=None):
    """Everything the demo screens need. ``stats`` is None when the OANDA trades read failed (``trades_error`` is
    ``deadline``/``incomplete`` when that was the cause: never partial totals); NAV/balance are None when the summary
    read failed; ``stale``/``as_of``: NAV/balance are the last good read, observed at ``as_of``; ``trades_as_of``: when the
    trades were read. ``read_codes``: non-secret diagnostics of both reads (``account``: AccountView status / error
    code, ``trades``: the read's error code or ``unavailable``; None = read OK). Never raises."""
    env = os.environ if env is None else env
    epoch = demo_epoch(env)
    snapshot = {"epoch": epoch, "stats": None, "curve": [], "open_rows": [], "closed_rows": [], "nav": None,
                "balance": None, "currency": None, "capital": {"start": None, "change": None}, "stale": False,
                "as_of": None, "trades_as_of": None, "trades_error": None, "daily_loss": daily_loss_view(None, None, now, {}),
                "read_codes": {"account": "not_read", "trades": "not_read"}}
    try:
        if trades_reader is None or account_reader is None:
            from execution.oanda_readonly import cached_account_view, cached_demo_trades
            trades_reader = trades_reader or cached_demo_trades
            account_reader = account_reader or cached_account_view
        risk = _risk_rows(env)
        trades = trades_reader(epoch)
        if isinstance(trades, dict):  # observation time of the trades read (peek readers add ``observed_at``)
            snapshot["trades_as_of"] = trades.get("observed_at") or trades.get("as_of")
        if isinstance(trades, dict) and trades.get("error") is not None:  # slow or incomplete read: no totals
            snapshot["trades_error"] = str(trades["error"])
        elif isinstance(trades, dict):
            listed = list(trades.get("closed") or ()) + list(trades.get("open") or ())
            stats, curve, open_rows = demo_statistics(listed, now, epoch, risk)
            snapshot.update(stats=stats, curve=curve, open_rows=open_rows,
                            closed_rows=demo_rows(listed, epoch, risk)[0])
        snapshot["read_codes"] = {**snapshot["read_codes"], "trades": _trades_code(trades, snapshot["stats"])}
        view = account_reader("practice")
        snapshot["read_codes"] = {**snapshot["read_codes"], "account": _account_code(view)}
        if getattr(view, "status", None) == "OK":
            snapshot.update(nav=as_float(view.nav), balance=as_float(view.balance), currency=view.currency,
                            stale=getattr(view, "stale", False) is True, as_of=getattr(view, "as_of", None))
        if snapshot["stats"] is not None:
            snapshot["capital"] = demo_capital(snapshot["nav"], snapshot["balance"], snapshot["stats"])
        snapshot["daily_loss"] = daily_loss_view(_loss_flags(env), snapshot["nav"], now, env)
    except Exception:  # noqa: BLE001 - the demo view is display only: anything unexpected => "sin datos"
        return {**snapshot, "stats": None, "curve": [], "open_rows": [], "closed_rows": [],
                "daily_loss": {**snapshot["daily_loss"], "status": "NO_DATA"},
                "read_codes": {**snapshot["read_codes"], "trades": "unexpected"}}
    return snapshot


def _code(value):
    """Short code token (``[a-z0-9_]``, <= 32 chars) for logs: never free text from a response."""
    text = "".join(c if c.isalnum() or c == "_" else "_" for c in str(value).lower())[:32]
    return text or "unknown"


def _trades_code(trades, stats):
    if stats is not None:
        return None
    if isinstance(trades, dict) and trades.get("error") is not None:
        return _code(trades["error"])
    return "unavailable" if trades is None else "invalid"


def _account_code(view):
    status = getattr(view, "status", None)
    if status == "OK" and getattr(view, "stale", False) is not True:
        return None
    if status == "OK":  # last good view served stale by the cache: the current read failed
        return "stale_" + _code(getattr(view, "error_code", None) or "unknown")
    if status is None:
        return "invalid"
    error = getattr(view, "error_code", None)
    return _code(error if error else status)


def demo_progress(now, env=None):
    """(epoch, first routed trade opened_at or None, day number or None). The day counts from the EPOCH's New York
    date (``execution.demo_epoch.demo_day_from_epoch``, the same rule as the Floor / Overview), with or without a
    routed trade; NOT capped (> 30 = the demo is complete; display via ``ui.i18n_es.demo_day_text``); None only when
    the epoch is unknown or ``now`` is before it. Never raises."""
    env = os.environ if env is None else env
    epoch = demo_epoch(env)
    first = first_demo_open(_risk_rows(env), epoch)
    try:
        day = demo_day_from_epoch(epoch, now)
    except Exception:  # noqa: BLE001 - display only
        day = None
    return epoch, first, day
