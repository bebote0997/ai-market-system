"""Small, non-authoritative cycle summaries beside the trading DB (30 days).

Only existing verdicts are copied. This database is never a trading input.
Connections and lock waits are short-lived; callers suppress all failures.
"""
from contextlib import closing
from datetime import datetime, timedelta
import json
from pathlib import Path
import sqlite3

from agents.setup_validator import CHECKS
from storage.codec import utc

RETENTION_DAYS = 30
MAX_RECORDS = 10000
NOT_EVALUATED = "NOT_EVALUATED"
AI_FIELDS = ("ai_structure", "ai_liquidity", "ai_macro", "ai_setup_review", "ai_trade_review")


def path_for(trading_path):
    path = Path(trading_path)
    return path.with_name(path.stem + ".cycles.db")


def summarize(context, status, *, paper_enabled):
    """Copy bounded scalar verdicts, including gates not reached on early exits."""
    deterministic = context.get("deterministic")
    ai = context.get("ai")
    setup = deterministic.setup_assessment if deterministic is not None else None
    explanation = setup.explanation if setup is not None else {}
    gates = {name: NOT_EVALUATED for name in CHECKS}
    for group, result in (("passed_checks", "PASSED"), ("failed_checks", "FAILED"),
                          ("missing_checks", "MISSING"), ("not_applicable_checks", "NOT_APPLICABLE"),
                          ("not_evaluated_checks", NOT_EVALUATED)):
        for name in explanation.get(group, ()):
            gates[name] = result
    for name in ("fresh", "execution_fresh", "session_open", "ai_healthy", "eligible"):
        gates[name] = ("PASSED" if context[name] else "FAILED") if name in context else NOT_EVALUATED
    gates["session"] = context.get("session", NOT_EVALUATED)
    gates["market_data"] = context.get("data_state", NOT_EVALUATED)
    gates["execution_data"] = context.get("execution_state", NOT_EVALUATED)
    gates["paper_enabled"] = "ENABLED" if paper_enabled else "DISABLED"
    gates["paper_state"] = (context["paper_blocked"] or "PASSED") if "paper_blocked" in context else NOT_EVALUATED
    risk = deterministic.risk_decision if deterministic is not None else None
    gates["risk"] = risk.status if risk is not None else NOT_EVALUATED
    gates["planner"] = ("PLAN_CREATED" if deterministic.trade_plan is not None else
                        deterministic.final_status) if deterministic is not None else NOT_EVALUATED
    macro = deterministic.macro_news_report if deterministic is not None else None
    gates["macro"] = macro.status if macro is not None else NOT_EVALUATED
    timeframes = {}
    for tf, field in (("1h", "bias"), ("15m", "structure_state"), ("5m", "structure_state")):
        report = deterministic.structure_reports.get(tf) if deterministic is not None else None
        evidence = report.evidence[0] if report is not None and report.evidence else {}
        timeframes[tf] = {"status": report.status if report is not None else "NO_DATA",
                          field: evidence.get(field, "NO_DATA")}
    reviews = {}
    for field in AI_FIELDS:
        response = getattr(ai, field) if ai is not None else None
        reviews[field] = (response.status if response is not None else "NOT_REQUIRED"
                          if ai is not None and field == "ai_trade_review" and ai.trade_plan is None
                          else NOT_EVALUATED)
    reason = explanation.get("decision_reason")
    if reason == "all_checks_passed":
        reason = None
    if reason is None and setup is not None and setup.status != "VALID_SETUP" and setup.warnings:
        reason = setup.warnings[0]
    execution_reason = context.get("execution_reason")
    if execution_reason is None:
        execution_reason = context.get("execution_state") if context.get("execution_fresh") is False else status
    return {"schema_version": "1.0", "status": status, "stage": context.get("stage"),
            "gates": gates, "timeframes": timeframes,
            "setup_status": setup.status if setup is not None else "NO_DATA",
            "rejection_reason": reason or (execution_reason if status != "PLAN_READY" else None),
            "ai_reviews": reviews, "execution_status": context.get("execution_status", "SKIPPED"),
            "execution_reason": execution_reason, "risk_reason": risk.reason if risk is not None else None}


def record(trading_path, context, status, *, paper_enabled):
    """Upsert one cycle, prune by the newest slot (also safe for older replays)."""
    if str(trading_path) == ":memory:":
        return  # In-memory test runtimes have no persistent trading database.
    payload = summarize(context, status, paper_enabled=paper_enabled)
    slot = context["slot"]
    path = path_for(trading_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path, timeout=0.1)) as db:
        db.execute("CREATE TABLE IF NOT EXISTS cycles(symbol TEXT NOT NULL, slot_time TEXT NOT NULL, "
                   "run_id TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(symbol, slot_time)) WITHOUT ROWID")
        db.execute("CREATE INDEX IF NOT EXISTS cycles_slot ON cycles(slot_time)")
        with db:
            db.execute("INSERT OR REPLACE INTO cycles VALUES(?,?,?,?)",
                       (context["symbol"], utc(slot), context["run_id"],
                        json.dumps(payload, separators=(",", ":"), allow_nan=False)))
            newest = db.execute("SELECT MAX(slot_time) FROM cycles").fetchone()[0]
            cutoff = utc(datetime.fromisoformat(newest) - timedelta(days=RETENTION_DAYS))
            db.execute("DELETE FROM cycles WHERE slot_time < ?", (cutoff,))
            db.execute("DELETE FROM cycles WHERE (symbol,slot_time) IN "
                       "(SELECT symbol,slot_time FROM cycles ORDER BY slot_time DESC,symbol LIMIT -1 OFFSET ?)",
                       (MAX_RECORDS,))
