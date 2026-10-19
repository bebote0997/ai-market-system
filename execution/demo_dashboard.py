"""Read-only OANDA demo view for the dashboard (the ONLY broker entry point of the Overview/Trades/Decisions/experiment
screens; imported lazily and guarded, see test_broker_wiring.py).

Demo mode = ``broker_demo_state.routing_flag`` (``AI_FLOOR_BROKER_DEMO_ROUTING == "1"``). In demo mode the dashboard
numbers come ONLY from the OANDA practice account since ``demo_epoch``: GET summary (NAV/balance) + GET trades via
``execution.oanda_readonly`` (cached, never raises) and the router's ``broker_demo_trades`` rows (``mode=ro``). A failed
read is "sin datos" (``stats`` None), never PAPER numbers. No writes, no orders.
"""
import os

from execution.broker_demo_state import routing_flag
from execution.demo_epoch import demo_epoch
from execution.demo_metrics import (as_float, demo_capital, demo_day, demo_rows, demo_statistics, first_demo_open,
                                    load_risk_rows)


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


def demo_snapshot(now, env=None, *, trades_reader=None, account_reader=None):
    """Everything the demo screens need. ``stats`` is None when the OANDA trades read failed; NAV/balance are None
    when the summary read failed. Never raises."""
    env = os.environ if env is None else env
    epoch = demo_epoch(env)
    snapshot = {"epoch": epoch, "stats": None, "curve": [], "open_rows": [], "closed_rows": [], "nav": None,
                "balance": None, "currency": None, "capital": {"start": None, "change": None}, "stale": False}
    try:
        if trades_reader is None or account_reader is None:
            from execution.oanda_readonly import cached_account_view, cached_demo_trades
            trades_reader = trades_reader or cached_demo_trades
            account_reader = account_reader or cached_account_view
        risk = _risk_rows(env)
        trades = trades_reader(epoch)
        if isinstance(trades, dict):
            listed = list(trades.get("closed") or ()) + list(trades.get("open") or ())
            stats, curve, open_rows = demo_statistics(listed, now, epoch, risk)
            snapshot.update(stats=stats, curve=curve, open_rows=open_rows,
                            closed_rows=demo_rows(listed, epoch, risk)[0])
        view = account_reader("practice")
        if getattr(view, "status", None) == "OK":
            snapshot.update(nav=as_float(view.nav), balance=as_float(view.balance), currency=view.currency,
                            stale=getattr(view, "stale", False) is True)
        if snapshot["stats"] is not None:
            snapshot["capital"] = demo_capital(snapshot["nav"], snapshot["balance"], snapshot["stats"])
    except Exception:  # noqa: BLE001 - the demo view is display only: anything unexpected => "sin datos"
        return {**snapshot, "stats": None, "curve": [], "open_rows": [], "closed_rows": []}
    return snapshot


def demo_progress(now, env=None):
    """(epoch, first routed trade opened_at or None, day number or None). Never raises."""
    env = os.environ if env is None else env
    epoch = demo_epoch(env)
    first = first_demo_open(_risk_rows(env), epoch)
    return epoch, first, demo_day(first, now)
