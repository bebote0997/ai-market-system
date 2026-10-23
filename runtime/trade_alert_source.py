"""OANDA demo data behind the Floor-style alerts (``runtime.trade_alerts``). Read-only; never raises (None = no data).

Only OANDA practice-demo sources are read: the router's ``broker_demo_trades`` row (``mode=ro``), the OANDA practice
account (NAV) and closed trades (realized P&L + financing), the demo epoch / day counter and the candle cache. The
experiment DB is read only for the deterministic setup evidence of the run that produced the routed plan (structured
facts behind the «Por qué» sentence) and the macro agent's text (macro line of the daily summary): never a PAPER number. Runs in the alert worker thread, never in the
router's critical path.
"""
from contextlib import closing
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import sqlite3

from runtime import trade_alerts as ta

LOG = logging.getLogger(__name__)
OPEN_KINDS = frozenset({"ACCEPTED"})
CLOSE_KINDS = frozenset({"CLOSED", "CLOSED_AT_BROKER"})
RICH_KINDS = OPEN_KINDS | CLOSE_KINDS
FOLDED_KINDS = frozenset({"MANUAL_CLOSE"})  # the CLOSED event (reason MANUAL_CLOSE) already reports it
SYMBOL = "EURUSD"


def _ro(path):
    target = Path(path)
    if not target.is_file():
        return None
    con = sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)
    con.row_factory = sqlite3.Row
    return con


def router_row(env, trade_id):
    from execution.demo_dashboard import state_db_path
    con = _ro(state_db_path(env))
    if con is None:
        return None
    with closing(con):
        row = con.execute("SELECT * FROM broker_demo_trades WHERE trade_id=? ORDER BY updated_at DESC LIMIT 1",
                          (str(trade_id),)).fetchone()
    return dict(row) if row else None


def _experiment_db(env):
    return _ro(env.get("AI_FLOOR_DB_PATH") or "data/runtime/trading_floor.db")


def _direction(value):
    return value.get("direction") if isinstance(value, dict) else None


def setup_facts(env, run_id):
    """Structured facts of ``run_id``'s VALID_SETUP evidence (``setups.evidence`` = [structure, liquidity] per
    timeframe, from the deterministic validator) for ``trade_alerts.why_line``: ``bias_1h``, ``bos_15m``,
    ``retracement_15m`` (direction words), ``sweep_5m`` / ``equal_levels_5m`` (bool). Missing -> {}."""
    con = _experiment_db(env) if run_id else None
    if con is None:
        return {}
    with closing(con):
        row = con.execute("SELECT s.evidence FROM setups s JOIN runs r ON r.slot_key=s.slot_key WHERE r.run_id=?",
                          (run_id,)).fetchone()
    evidence = json.loads(row[0]) if row and row[0] else None
    if not isinstance(evidence, list) or len(evidence) != 2 or not all(isinstance(e, dict) for e in evidence):
        return {}
    structure, liquidity = evidence
    s1h, s15, l5 = (structure.get("1h") or {}), (structure.get("15m") or {}), (liquidity.get("5m") or {})
    return {"bias_1h": s1h.get("bias"), "bos_15m": _direction(s15.get("bos")),
            "retracement_15m": _direction(s15.get("retracement")), "sweep_5m": bool(l5.get("sweeps")),
            "equal_levels_5m": bool(l5.get("liquidity_above") or l5.get("liquidity_below"))}


def latest_macro(env, start, end, symbol=SYMBOL):
    """Latest macro agent summary for ``symbol`` with ``as_of`` in the reported trading day [``start``, ``end``)
    (aware datetimes; folded into the daily summary instead of separate alerts). A delayed summary never picks up a
    later day's macro view."""
    con = _experiment_db(env)
    if con is None:
        return None
    bounds = tuple(value.astimezone(timezone.utc).isoformat() for value in (start, end))
    with closing(con):
        row = con.execute("SELECT a.reasoning_summary FROM agent_decisions a JOIN runs r ON r.slot_key=a.slot_key "
                          "WHERE r.symbol=? AND a.agent_name LIKE 'macro%' AND a.reasoning_summary IS NOT NULL "
                          "AND a.reasoning_summary<>'' AND julianday(a.as_of) >= julianday(?) "
                          "AND julianday(a.as_of) < julianday(?) ORDER BY julianday(a.as_of) DESC LIMIT 1",
                          (symbol, *bounds)).fetchone()
    return row[0] if row else None


def _window(date_label, window):
    """[start, end) of the reported trading day: ``window`` (aware datetimes), else 17:00 -> 17:00 NY of
    ``date_label``; None when neither is usable (then no macro line, never an unbounded one)."""
    if window and all(isinstance(v, datetime) and v.tzinfo for v in window):
        return tuple(window)
    if not date_label:
        return None
    from datetime import date
    from storage.daily_summary import summary_window
    _, start, end, _ = summary_window(date.fromisoformat(str(date_label)[:10]))
    return start, end


def _safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - one missing source never blocks an alert
        LOG.warning("component=alerts event=SOURCE_FAILED source=%s error_type=%s", getattr(fn, "__name__", "?"),
                    type(exc).__name__)
        return default


def _nav(env, account_fetch):
    from execution.demo_metrics import as_float
    view = account_fetch("practice", env)
    return (as_float(view.nav), view.currency) if getattr(view, "status", None) == "OK" else (None, None)


def _closed_trade(env, trade_id, row, trades_fetch):
    from execution.demo_epoch import demo_epoch
    from execution.demo_metrics import demo_rows
    epoch = demo_epoch(env)
    trades = trades_fetch(env, since=epoch)
    if not isinstance(trades, dict) or trades.get("error") is not None:
        return None
    closed, _ = demo_rows(list(trades.get("closed") or ()), epoch, {row["client_id"]: row})
    return next((item for item in closed if str(item.get("trade_id")) == str(trade_id)), None)


def _infer_exit(symbol, reason, exit_price, stop, target):
    """SL_TP_OR_EXTERNAL -> TP / SL when the exit price sits at that level (within 2 pips / 0.05 %), else unchanged."""
    if reason != "SL_TP_OR_EXTERNAL" or exit_price is None:
        return reason
    from core.fx_policy import is_fx, pip_size
    tolerance = 2 * float(pip_size(symbol)) if is_fx(symbol) else abs(exit_price) * 0.0005
    for level, label in ((target, "TP"), (stop, "SL")):
        level = ta._num(level)
        if level is not None and abs(exit_price - level) <= tolerance:
            return label
    return reason


def trade_alert(kind, detail, env, *, now=None, trades_fetch=None, account_fetch=None):
    """TradeAlert for a router ACCEPTED / CLOSED / CLOSED_AT_BROKER event, or None (fallback to the plain line)."""
    try:
        if kind not in RICH_KINDS or not isinstance(detail, dict) or not detail.get("trade_id"):
            return None
        from execution.demo_dashboard import demo_progress
        from execution.demo_metrics import DEMO_LENGTH_DAYS, as_float, parse_time
        from runtime import candle_cache
        from runtime.scheduler import session_names
        if trades_fetch is None or account_fetch is None:
            from execution.oanda_readonly import account_view, demo_trades
            trades_fetch = trades_fetch or demo_trades
            account_fetch = account_fetch or account_view
        now = now or datetime.now(timezone.utc)
        row = router_row(env, detail["trade_id"])
        if row is None:
            return None
        symbol, units = row["symbol"], as_float(row.get("units"))
        entry = as_float(row.get("fill_price"))  # confirmed OANDA fill only: the planned entry is never "actual"
        stop = as_float(row.get("initial_stop")) or as_float(row.get("stop"))
        risk = None if None in (entry, stop, units) else abs(entry - stop) * abs(units)
        nav, currency = _safe(_nav, env, account_fetch, default=(None, None))
        opened_at = parse_time(row.get("opened_at")) or now
        closed = None
        if kind in CLOSE_KINDS:
            closed = _safe(_closed_trade, env, detail["trade_id"], row, trades_fetch)
        reason = detail.get("reason") or row.get("closed_reason")
        if closed is not None:
            reason = _infer_exit(symbol, reason, closed.get("exit_price"), row.get("stop"), row.get("target"))
        return ta.TradeAlert(
            kind="OPENED" if kind in OPEN_KINDS else "CLOSED", symbol=symbol, side=row["side"], entry=entry,
            stop=stop, target=row.get("target"), risk_amount=risk,
            risk_pct=None if not risk or not nav else risk / nav * 100,
            session=_safe(session_names, opened_at, default=()) or (),
            why=ta.why_line(_safe(setup_facts, env, row.get("run_id"), default={})),
            close_reason=None if kind in OPEN_KINDS else reason,
            result_r=None if closed is None else closed.get("realized_r"),
            pnl=None if closed is None else closed.get("net_pnl"), currency=currency or "USD",
            demo_day=_safe(lambda: demo_progress(now, env)[2]), demo_length=DEMO_LENGTH_DAYS,
            closes=_safe(candle_cache.closes, candle_cache.path_from_env(env), symbol, now.isoformat(),
                         default=()) or (),
            url=ta.floor_url(env))
    except Exception as exc:  # noqa: BLE001 - the plain one-line alert is the fallback
        LOG.warning("component=alerts event=TRADE_ALERT_FAILED error_type=%s", type(exc).__name__)
        return None


def daily_alert(lines, env, *, date_label=None, demo_day=None, pnl=None, window=None):
    """DailyAlert from the OANDA demo summary lines ("Etiqueta: valor"); the macro view of the reported trading day
    (``window`` = (start, end) aware, else derived from ``date_label``) folded in as one line. The first line (demo
    day text) and the trading-day line become the header/footer, not grid cells."""
    from execution.demo_metrics import DEMO_LENGTH_DAYS
    pairs = []
    for line in lines:
        label, sep, value = str(line).partition(": ")
        if not sep and isinstance(demo_day, int):
            continue  # "Día N de 30" text: rendered by the footer
        if label == "Día de trading" and date_label:
            continue  # in the header
        pairs.append((label, value) if sep else ("Estado", label))
    bounds = _safe(_window, date_label, window)
    macro = None if bounds is None else _safe(latest_macro, env, *bounds)
    return ta.DailyAlert(lines=tuple(pairs), date_label=date_label, macro=macro, pnl=pnl,
                         demo_day=demo_day, demo_length=DEMO_LENGTH_DAYS, url=ta.floor_url(env))


__all__ = ["CLOSE_KINDS", "FOLDED_KINDS", "OPEN_KINDS", "RICH_KINDS", "daily_alert", "setup_facts",
           "latest_macro", "router_row", "trade_alert"]
