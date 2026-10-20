"""Read-only daily aggregation of persisted evidence; never invokes the floor."""
from collections import Counter
from datetime import date, datetime, time, timedelta, timezone
import json
from zoneinfo import ZoneInfo

from execution.daily_loss_limit import RESET_HOUR, day_key
from storage.codec import utc


NEW_YORK = ZoneInfo("America/New_York")
NY_CLOSE = time(RESET_HOUR)
MAX_CATCH_UP = 5  # trading days re-sent after a long downtime (oldest first); older missed days are not sent


def trading_day(at):
    """NY date labelling the latest forex trading day that has ended at ``at``: the NY date of its 17:00
    America/New_York end. Same boundary as ``execution.daily_loss_limit.day_key`` (which keys a day by its start)."""
    return datetime.fromisoformat(day_key(at)).date()


def summary_day(at):
    """Latest ended trading day with a weekday label. Fri 17:00 -> Sun 17:00 NY the market is closed: the Saturday-
    and Sunday-labelled windows are never summarized, so from Friday's close until Monday's it is Friday's day."""
    day = trading_day(at)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def pending_summary_days(last_sent, at):
    """Weekday trading days after ``last_sent`` (ISO date or None) up to ``summary_day(at)``, oldest first, at most
    ``MAX_CATCH_UP``. Nothing recorded yet -> only the latest; ``at`` before the last sent day -> nothing."""
    latest = summary_day(at)
    try:
        day = date.fromisoformat(str(last_sent)[:10]) if last_sent else None
    except ValueError:
        day = None
    if day is None:
        return [latest]
    days = []
    while day < latest:
        day += timedelta(days=1)
        if day.weekday() < 5:
            days.append(day)
    return days[-MAX_CATCH_UP:]


def summary_window(day):
    """``(date_ny, start_utc, end_utc, trading_day_key)`` of the trading day labelled ``day`` (NY date of its 17:00
    end): [previous NY date 17:00, ``day`` 17:00) America/New_York as UTC bounds (23 h / 25 h across DST changes).
    ``trading_day_key`` = ``daily_loss_limit.day_key`` of that day (ISO of its 17:00 start)."""
    def instant(d):
        return datetime.combine(d, NY_CLOSE, NEW_YORK)
    start = instant(day - timedelta(days=1))
    return (day.isoformat(), start.astimezone(timezone.utc), instant(day).astimezone(timezone.utc),
            day_key(start))


def _avg_r(store, closed):
    """(avg R, None) or (None, reason) over the window's closed PAPER trades (R as on the dashboard)."""
    from ui.views.luxury import realized_r_with_reason
    if not closed:
        return None, "sin operaciones cerradas"
    values, reason = [], None
    for trade in closed:
        row = store.db.execute("SELECT payload FROM paper_positions WHERE position_id=?",
                               (trade.get("position_id"),)).fetchone()
        value, why = realized_r_with_reason(trade, json.loads(row[0]) if row else None)
        if value is None:
            reason = reason or why
        else:
            values.append(value)
    if len(values) < len(closed):
        return None, f"{reason} en {len(closed) - len(values)} de {len(closed)} operaciones"
    return sum(values) / len(values), None


def build_daily_summary(store, at, day=None):
    """Summary of trading day ``day`` (default ``summary_day(at)``); rows are aggregated over its 17:00 -> 17:00 NY
    window, ``*_current`` fields are the snapshot at ``at``."""
    captured_at = utc(at)
    label, start, end, key = summary_window(day or summary_day(at))
    bounds = tuple(t.strftime("%Y-%m-%d %H:%M:%S") for t in (start, end))
    in_day = "datetime({0}) >= ? AND datetime({0}) < ?"  # datetime() normalises any stored offset to UTC
    runs = store.db.execute(
        "SELECT * FROM runs r WHERE " + in_day.format("started_at") + " "
        "AND coalesce(final_status,'') != 'SESSION_SKIPPED' "
        "AND NOT EXISTS (SELECT 1 FROM journal j WHERE j.run_id=r.run_id AND j.event_type='SESSION_SKIPPED')",
        bounds).fetchall()
    symbols = json.loads(store.get_state("enabled_symbols") or "[]")
    by_symbol = dict.fromkeys(symbols, 0)
    by_symbol.update(Counter(r["symbol"] for r in runs))
    setup_counts = Counter()
    risk_rejected = 0
    for run in runs:
        setup = store.db.execute("SELECT status FROM setups WHERE slot_key=?", (run["slot_key"],)).fetchone()
        risk = store.db.execute("SELECT status FROM risk_decisions WHERE slot_key=?", (run["slot_key"],)).fetchone()
        if setup:
            setup_counts[setup[0]] += 1
        risk_rejected += bool(risk and risk[0] == "REJECTED")

    def count_entities(table, field):
        return store.db.execute(
            f"SELECT count(*) FROM {table} WHERE " + in_day.format(f"json_extract(payload,'$.{field}')"),
            bounds).fetchone()[0]

    closed = [json.loads(r[0]) for r in store.db.execute(
        "SELECT payload FROM closed_trades WHERE " + in_day.format("json_extract(payload,'$.exited_at')"), bounds)]
    pnl = [t.get("net_pnl") for t in closed]
    avg_r, avg_r_reason = _avg_r(store, closed)
    account_row = store.db.execute("SELECT payload FROM paper_accounts WHERE account_id=?",
                                   (store.get_state("account_id"),)).fetchone()
    account = json.loads(account_row[0]) if account_row else {}
    rows = store.db.execute("SELECT * FROM journal WHERE " + in_day.format("timestamp"), bounds).fetchall()
    provider_types = {"PROVIDER_FAILURE", "AUTH_ERROR", "ENTITLEMENT_ERROR", "RATE_LIMITED",
                      "DATA_UNAVAILABLE", "DATA_STALE", "NOT_CONFIGURED", "NO_DATA", "TIMEOUT"}
    # DATA_CHECK and RUN_FAILED can repeat the same provider failure; count its
    # primary journal event only. Other ERROR events remain visible separately.
    problems = [r for r in rows if r["event_type"] in provider_types or (
        r["source"] in {"market_data", "ai_provider", "macro_news"}
        and r["severity"] in {"WARNING", "ERROR"}
        and r["event_type"] not in {"DATA_CHECK", "RUN_FAILED", "MACRO_HIGH_IMPORTANCE", "MACRO_HIGH_RELEVANCE"})]
    macro = {json.loads(r["payload"]).get("event_id") for r in rows
             if r["event_type"] == "MACRO_HIGH_IMPORTANCE"}
    macro.discard(None)
    policy_macro = {json.loads(r["payload"]).get("event_id") for r in rows
                    if r["event_type"] == "MACRO_HIGH_RELEVANCE"}
    policy_macro.discard(None)
    latest = store.latest_run()
    return {
        "date_ny": label, "trading_day_key": key, "window_start_utc": utc(start), "close_at_utc": utc(end),
        "snapshot_at_utc": captured_at,
        "cycles": len(runs), "cycles_by_symbol": by_symbol,
        **{status: setup_counts[status] for status in ("NO_SETUP", "WATCH", "VALID_SETUP")},
        "RISK_REJECTED": risk_rejected,
        "paper_orders_created": count_entities("paper_orders", "as_of"),
        "positions_opened": count_entities("paper_positions", "opened_at"),
        "positions_open_current": store.db.execute(
            "SELECT count(*) FROM paper_positions WHERE json_extract(payload,'$.status')='OPEN'").fetchone()[0],
        "positions_closed": len(closed),
        "realized_pnl_day": sum(pnl) if all(v is not None for v in pnl) else None,
        "wins_day": sum(v > 0 for v in pnl) if all(v is not None for v in pnl) else None,
        "avg_r_day": avg_r, "avg_r_day_reason": avg_r_reason,
        "unrealized_pnl_current": account.get("unrealized_pnl"),
        "equity_current": account.get("equity"),
        "provider_problems": len(problems),
        "errors": sum(r["severity"] == "ERROR" for r in rows),
        "macro_high_events": len(macro),
        "macro_policy_high_events": len(policy_macro),
        "runner_status": store.get_state("runner"),
        "last_cycle_status": latest["status"] if latest else None,
        "last_cycle_final_status": latest["final_status"] if latest else None,
        "scheduler_status": store.get_state("scheduler"),
        "heartbeat_utc": store.get_state("heartbeat"),
    }
