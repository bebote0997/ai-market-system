"""Read-only daily aggregation of persisted evidence; never invokes the floor."""
from collections import Counter
from datetime import datetime, time, timedelta, timezone
import json
from zoneinfo import ZoneInfo

from storage.codec import utc


NEW_YORK = ZoneInfo("America/New_York")
NY_CLOSE = time(17)


def summary_window(at):
    """``(date_ny, start_utc, end_utc, close_utc)`` of the NY trading day the summary at ``at`` covers: the latest NY
    calendar date whose 17:00 America/New_York close is <= ``at`` (DST-correct). Rows are aggregated over that whole NY
    calendar date ([00:00, 24:00) NY, as UTC bounds), like the OANDA "P&L de hoy (ET)" figure."""
    local = at.astimezone(NEW_YORK)
    day = local.date() if local.time() >= NY_CLOSE else local.date() - timedelta(days=1)

    def instant(d, t):
        return datetime.combine(d, t, NEW_YORK).astimezone(timezone.utc)
    return day.isoformat(), instant(day, time()), instant(day + timedelta(days=1), time()), instant(day, NY_CLOSE)


def build_daily_summary(store, at):
    captured_at = utc(at)
    date, start, end, close = summary_window(at)
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
        "date_ny": date, "close_at_utc": utc(close), "snapshot_at_utc": captured_at,
        "cycles": len(runs), "cycles_by_symbol": by_symbol,
        **{status: setup_counts[status] for status in ("NO_SETUP", "WATCH", "VALID_SETUP")},
        "RISK_REJECTED": risk_rejected,
        "paper_orders_created": count_entities("paper_orders", "as_of"),
        "positions_opened": count_entities("paper_positions", "opened_at"),
        "positions_open_current": store.db.execute(
            "SELECT count(*) FROM paper_positions WHERE json_extract(payload,'$.status')='OPEN'").fetchone()[0],
        "positions_closed": len(closed),
        "realized_pnl_day": sum(pnl) if all(v is not None for v in pnl) else None,
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
