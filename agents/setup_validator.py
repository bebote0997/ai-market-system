from datetime import datetime, timezone

from core.contracts import AgentMessage, SetupAssessment

VALID_STATUSES = {"NO_SETUP", "WATCH", "VALID_SETUP"}


def _payload(message):
    if not isinstance(message, AgentMessage) or not message.evidence:
        return None
    first = message.evidence[0]
    if isinstance(first, dict):
        return first
    return None


def evaluar_setup(structure_reports, liquidity_reports, macro_report, symbol, run_id, as_of):
    structure_reports = structure_reports or {}
    liquidity_reports = liquidity_reports or {}
    timeframes = ("1h", "15m", "5m")
    warnings = []
    if len(structure_reports) < 3 or len(liquidity_reports) < 3:
        return SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, timeframes, warnings=("missing_timeframe",), data_quality={"structure_timeframes": tuple(structure_reports), "liquidity_timeframes": tuple(liquidity_reports)})
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
