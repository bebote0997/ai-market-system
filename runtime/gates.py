"""Deterministic operational gates. No AI authorization."""
from datetime import timezone
import math

import pandas as pd

from runtime.scheduler import session_names


def fresh_snapshot(snapshot, symbol, as_of, limits):
    if not isinstance(snapshot, dict):
        return False, "NO_DATA", None
    latest = None
    for timeframe, max_age in limits:
        frame = snapshot.get(timeframe)
        if not isinstance(frame, pd.DataFrame) or frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
            return False, "NO_DATA", None
        if frame.index.tz is None or frame.index.has_duplicates or "is_closed" not in frame.columns:
            return False, "NO_DATA", None
        if "symbol" not in frame.columns or not all(frame["symbol"] == symbol):
            return False, "NO_DATA", None
        if any(frame.index > as_of) or any(frame["is_closed"] != True):  # noqa: E712
            return False, "NO_DATA", None
        last = frame.index.max().to_pydatetime().astimezone(timezone.utc)
        age = (as_of - last).total_seconds()
        if age < 0 or age > max_age:
            return False, "STALE_DATA", None
        if timeframe == "5m":
            latest = frame.loc[frame.index.max()]
            if not all(k in latest for k in ("Open", "High", "Low", "Close")):
                return False, "NO_DATA", None
            values = [latest[k] for k in ("Open", "High", "Low", "Close")]
            if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)) and v > 0 for v in values):
                return False, "NO_DATA", None
    return True, "CURRENT", latest


BLOCKED = {"NO_DATA", "NO_SETUP", "WATCH", "AI_CAUTION", "RISK_REJECTED", "ERROR",
           "STATE_INCONSISTENCY", "STALE_DATA", "PROVIDER_FAILURE", "PARTIAL_AI_FAILURE"}


def paper_policy(ai_report, *, data_state="CURRENT"):
    if data_state != "CURRENT" or ai_report is None or ai_report.final_status != "PLAN_READY":
        return False
    decision = ai_report.risk_decision
    if decision is None or decision.status != "APPROVED" or ai_report.trade_plan is None:
        return False
    responses = (ai_report.ai_structure, ai_report.ai_liquidity, ai_report.ai_macro,
                 ai_report.ai_setup_review, ai_report.ai_trade_review)
    # Missing or failed review cannot silently become a paper order.
    if any(r is None or r.status not in {"OK", "PARTIAL"} for r in responses):
        return False
    return True
