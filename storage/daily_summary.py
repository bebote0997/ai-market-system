"""Read-only daily aggregation of persisted evidence; never invokes the floor."""
from collections import Counter
from datetime import timedelta, timezone
import json

from storage.codec import utc


def build_daily_summary(store, at):
    captured_at = utc(at)
    # Only summarize a closed UTC day, even when called just after midnight.
    date = (at.astimezone(timezone.utc) - timedelta(days=1)).date().isoformat()
    runs = store.db.execute(
        "SELECT * FROM runs WHERE date(started_at)=? AND coalesce(final_status,'') != 'SESSION_SKIPPED'",
        (date,)).fetchall()
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
            f"SELECT count(*) FROM {table} WHERE date(json_extract(payload,'$.{field}'))=?",
            (date,)).fetchone()[0]

    closed = [json.loads(r[0]) for r in store.db.execute(
        "SELECT payload FROM closed_trades WHERE date(json_extract(payload,'$.exited_at'))=?", (date,))]
    pnl = [t.get("net_pnl") for t in closed]
    account_row = store.db.execute("SELECT payload FROM paper_accounts WHERE account_id=?",
                                   (store.get_state("account_id"),)).fetchone()
    account = json.loads(account_row[0]) if account_row else {}
    rows = store.db.execute("SELECT * FROM journal WHERE date(timestamp)=?", (date,)).fetchall()
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
        "date_utc": date, "snapshot_at_utc": captured_at,
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
