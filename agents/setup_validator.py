from datetime import datetime, timezone

import pandas as pd

from core.contracts import AgentMessage, SetupAssessment

VALID_STATUSES = {"NO_SETUP", "WATCH", "VALID_SETUP"}


def _payload(message):
    if not isinstance(message, AgentMessage) or not message.evidence:
        return None
    first = message.evidence[0]
    if isinstance(first, dict):
        return first
    return None


def _report_valid(message, run_id, symbol, timeframe, as_of):
    if not isinstance(message, AgentMessage):
        return False
    if message.run_id != run_id or message.symbol != symbol or message.timeframe != timeframe:
        return False
    if message.status not in {"OK", "PARTIAL", "NO_DATA", "ERROR"}:
        return False
    try:
        report_time = pd.Timestamp(message.timestamp)
        cutoff = pd.Timestamp(as_of)
        if report_time.tzinfo is None or cutoff.tzinfo is None:
            return False
        return report_time <= cutoff
    except (TypeError, ValueError):
        return False


def evaluar_setup(structure_reports, liquidity_reports, macro_report, symbol, run_id, as_of):
    structure_reports = structure_reports or {}
    liquidity_reports = liquidity_reports or {}
    timeframes = ("1h", "15m", "5m")
    warnings = []
    if len(structure_reports) < 3 or len(liquidity_reports) < 3:
        return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("missing_timeframe",), data_quality={"structure_timeframes": tuple(structure_reports), "liquidity_timeframes": tuple(liquidity_reports)})
    if any(
        not _report_valid(structure_reports.get(tf), run_id, symbol, tf, as_of)
        or not _report_valid(liquidity_reports.get(tf), run_id, symbol, tf, as_of)
        for tf in timeframes
    ):
        return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("scout_lineage_invalid",))
    if macro_report is not None and (
        not isinstance(macro_report, AgentMessage)
        or macro_report.run_id != run_id
        or macro_report.symbol != symbol
        or macro_report.status not in {"OK", "PARTIAL", "NO_DATA", "ERROR"}
    ):
        return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("macro_lineage_invalid",))
    if macro_report is not None:
        try:
            if pd.Timestamp(macro_report.timestamp) > pd.Timestamp(as_of):
                return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("macro_future_timestamp",))
        except (TypeError, ValueError):
            return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("macro_timestamp_invalid",))
    if any(report.status in {"ERROR", "NO_DATA"} for report in list(structure_reports.values()) + list(liquidity_reports.values())):
        return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("scout_unavailable",))
    if macro_report is not None and macro_report.status == "ERROR":
        return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("macro_error",))
    if macro_report is not None:
        macro_items = macro_report.evidence[0].get("macro_events", ()) if macro_report.evidence else ()
        if any(item.get("impact") == "HIGH" and item.get("window") == "ACTIVE_WINDOW" for item in macro_items):
            return SetupAssessment("1.0", run_id, as_of, symbol, "WATCH", None, timeframes, warnings=("high_impact_active_window",))
    structure = {tf: _payload(structure_reports[tf]) for tf in timeframes}
    liquidity = {tf: _payload(liquidity_reports[tf]) for tf in timeframes}
    if any(value is None for value in structure.values()) or any(value is None for value in liquidity.values()):
        return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("missing_evidence",))
    bias = structure["1h"].get("bias")
    setup_state = structure["15m"].get("structure_state")
    timing_state = structure["5m"].get("structure_state")
    side = "LONG" if bias == setup_state == timing_state == "bullish" else "SHORT" if bias == setup_state == timing_state == "bearish" else None
    if side is None:
        return SetupAssessment("1.0", run_id, as_of, symbol, "WATCH", None, timeframes, warnings=("timeframe_incompatibility",))
    if not structure["15m"].get("bos") and not structure["15m"].get("retracement"):
        return SetupAssessment("1.0", run_id, as_of, symbol, "WATCH", side, timeframes, warnings=("structure_confirmation_missing",))
    if not liquidity["5m"].get("sweeps") and not liquidity["5m"].get("liquidity_above") and not liquidity["5m"].get("liquidity_below"):
        return SetupAssessment("1.0", run_id, as_of, symbol, "WATCH", side, timeframes, warnings=("liquidity_confirmation_missing",))
    invalidation = structure["15m"].get("levels", {}).get("support" if side == "LONG" else "resistance")
    if invalidation is None:
        return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("invalidation_missing",))
    return SetupAssessment("1.0", run_id, as_of, symbol, "VALID_SETUP", side, timeframes, evidence=(structure, liquidity), invalidation=float(invalidation), warnings=tuple(warnings), data_quality={"as_of": as_of})
