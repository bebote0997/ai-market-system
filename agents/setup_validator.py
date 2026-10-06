from dataclasses import replace
from datetime import datetime, timezone
import json
import uuid

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


def _evaluar_setup_v1(structure_reports, liquidity_reports, macro_report, symbol, run_id, as_of):
    """Frozen V1 decision (status, side, invalidation, warnings, evidence). Do not change."""
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


# --- V2 Phase 3 (P3.1): explanation, evidence references and identity --------------------------------
# Information only. The decision is the frozen V1 one above; V2 explains it and never changes it.
VALIDATOR = "setup_validator"
VALIDATOR_VERSION = "2.0"
# The V1 checks, in the exact order V1 evaluates them (it stops at the first that does not pass).
CHECKS = ("timeframes_present", "scout_lineage", "macro_lineage", "macro_timestamp", "scouts_available",
          "macro_available", "no_high_impact_active_window", "scout_evidence_present", "timeframe_alignment",
          "structure_confirmation", "liquidity_confirmation", "invalidation_level")
MACRO_CHECKS = frozenset({"macro_lineage", "macro_timestamp", "macro_available", "no_high_impact_active_window"})
# V1 reason code -> (deciding check, outcome). MISSING = required input unavailable; FAILED = evaluated, not met.
DECIDING_CHECK = {
    "missing_timeframe": ("timeframes_present", "MISSING"),
    "scout_lineage_invalid": ("scout_lineage", "FAILED"),
    "macro_lineage_invalid": ("macro_lineage", "FAILED"),
    "macro_future_timestamp": ("macro_timestamp", "FAILED"),
    "macro_timestamp_invalid": ("macro_timestamp", "FAILED"),
    "scout_unavailable": ("scouts_available", "MISSING"),
    "macro_error": ("macro_available", "MISSING"),
    "high_impact_active_window": ("no_high_impact_active_window", "FAILED"),
    "missing_evidence": ("scout_evidence_present", "MISSING"),
    "timeframe_incompatibility": ("timeframe_alignment", "FAILED"),
    "structure_confirmation_missing": ("structure_confirmation", "FAILED"),
    "liquidity_confirmation_missing": ("liquidity_confirmation", "FAILED"),
    "invalidation_missing": ("invalidation_level", "MISSING"),
}
ALL_PASSED = "all_checks_passed"


def setup_identity(assessment):
    """(setup_id, identity inputs) of a VALID_SETUP, else (None, None).

    Identity = symbol, side, invalidation and the 15m structural anchor (BOS: break bar, broken level,
    direction; else retracement: impulse/protected swing bars and prices). Deliberately excluded: run_id,
    run timestamps, warnings, liquidity details, data quality. Same opportunity (retry, restart, re-run)
    gives the same id; a new anchor, side, invalidation or symbol gives a new id. Same algorithm and
    values as the V1 audit setup_id.
    """
    if assessment is None or assessment.status != "VALID_SETUP":
        return None, None
    structure = assessment.evidence[0] if assessment.evidence else {}
    signal = structure.get("15m", {})
    bos = signal.get("bos") or {}
    retracement = signal.get("retracement") or {}
    anchor = (
        {"kind": "BOS", "timestamp": str(bos.get("break_timestamp")),
         "level": bos.get("broken_level"), "direction": bos.get("direction")}
        if bos else
        {"kind": "RETRACEMENT", "impulse": str(retracement.get("impulse_timestamp")),
         "protected": str(retracement.get("protected_timestamp")),
         "impulse_price": retracement.get("impulse_low", retracement.get("impulse_high")),
         "protected_price": retracement.get("protected_high", retracement.get("protected_low"))}
    )
    identity = {"symbol": assessment.symbol, "side": assessment.side,
                "invalidation": assessment.invalidation, "anchor": anchor}
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity, sort_keys=True))), identity


DURATIONS = {"1h": pd.Timedelta(hours=1), "15m": pd.Timedelta(minutes=15), "5m": pd.Timedelta(minutes=5)}


def _bar_ref(message, source, timeframe, assessment):
    """A Phase 2-style reference (symbol, timeframe, bar_start) to the last closed bar a scout used.

    Explanation metadata only (never read by any decision). VALID only when the report passes the same
    V1 lineage check and its bar is an aware timestamp aligned to the timeframe and closed by as_of;
    otherwise UNAVAILABLE/INVALID with a reason and no bar_start (nothing invented or normalized)."""
    ref = {"source": source, "symbol": assessment.symbol, "timeframe": timeframe,
           "status": message.status if isinstance(message, AgentMessage) else None,
           "ref_state": "UNAVAILABLE", "reason": "report_missing", "bar_start": None}
    if not isinstance(message, AgentMessage):
        return ref
    if not _report_valid(message, assessment.run_id, assessment.symbol, timeframe, assessment.timestamp):
        return {**ref, "ref_state": "INVALID", "reason": "lineage_invalid"}
    payload = _payload(message)
    bars = payload.get("evidence") if payload else None
    claimed = bars[0].get("timestamp") if isinstance(bars, list) and bars and isinstance(bars[0], dict) else None
    if claimed is None:
        return {**ref, "reason": "bar_missing"}
    try:
        start = pd.Timestamp(claimed)
        as_of = pd.Timestamp(assessment.timestamp)
    except (TypeError, ValueError):
        return {**ref, "ref_state": "INVALID", "reason": "bar_timestamp_invalid"}
    if start is pd.NaT or start.tzinfo is None or as_of.tzinfo is None:
        return {**ref, "ref_state": "INVALID", "reason": "bar_timestamp_invalid"}
    duration = DURATIONS[timeframe]
    if start.value % duration.value:
        return {**ref, "ref_state": "INVALID", "reason": "bar_misaligned_for_timeframe"}
    if start + duration > as_of:
        return {**ref, "ref_state": "INVALID", "reason": "bar_not_closed_by_as_of"}
    return {**ref, "ref_state": "VALID", "reason": None, "bar_start": start.tz_convert("UTC").isoformat()}


def _macro_ref(macro_report, assessment):
    """Macro context reference; event ids only when the report passes the V1 macro lineage checks."""
    ref = {"source": "macro", "symbol": assessment.symbol, "timeframe": None,
           "status": macro_report.status if isinstance(macro_report, AgentMessage) else None,
           "ref_state": "INVALID", "reason": "lineage_invalid", "event_ids": ()}
    if (not isinstance(macro_report, AgentMessage) or macro_report.run_id != assessment.run_id
            or macro_report.symbol != assessment.symbol
            or macro_report.status not in {"OK", "PARTIAL", "NO_DATA", "ERROR"}):
        return ref
    try:
        if pd.Timestamp(macro_report.timestamp) > pd.Timestamp(assessment.timestamp):
            return {**ref, "reason": "timestamp_after_as_of"}
    except (TypeError, ValueError):
        return {**ref, "reason": "timestamp_invalid"}
    payload = _payload(macro_report)
    items = payload.get("macro_events", ()) if payload else ()
    return {**ref, "ref_state": "VALID", "reason": None,
            "event_ids": tuple(i.get("event_id") for i in items if isinstance(i, dict) and i.get("event_id"))}


def _evidence_refs(structure_reports, liquidity_reports, macro_report, assessment):
    """Reference (never copy) what each scout looked at; the Evidence Store is not read."""
    refs = [_bar_ref(reports.get(tf) if isinstance(reports, dict) else None, source, tf, assessment)
            for source, reports in (("structure", structure_reports), ("liquidity", liquidity_reports))
            for tf in ("1h", "15m", "5m")]
    if macro_report is not None:
        refs.append(_macro_ref(macro_report, assessment))
    return tuple(refs)


def _details(evaluated, structure_reports, liquidity_reports, macro_report, assessment):
    """The evidence behind each evaluated check that has a measurable input."""
    details = {}
    structure = {tf: _payload((structure_reports or {}).get(tf)) or {} for tf in ("1h", "15m", "5m")}
    liquidity = {tf: _payload((liquidity_reports or {}).get(tf)) or {} for tf in ("1h", "15m", "5m")}
    macro = _payload(macro_report) if macro_report is not None else None
    if "no_high_impact_active_window" in evaluated and macro:
        details["no_high_impact_active_window"] = {"active_high_impact_events": tuple(
            i.get("event_id") for i in macro.get("macro_events", ())
            if isinstance(i, dict) and i.get("impact") == "HIGH" and i.get("window") == "ACTIVE_WINDOW")}
    if "timeframe_alignment" in evaluated:
        details["timeframe_alignment"] = {"1h_bias": structure["1h"].get("bias"),
                                          "15m_structure_state": structure["15m"].get("structure_state"),
                                          "5m_structure_state": structure["5m"].get("structure_state")}
    if "structure_confirmation" in evaluated:
        details["structure_confirmation"] = {"15m_bos": bool(structure["15m"].get("bos")),
                                             "15m_retracement": bool(structure["15m"].get("retracement"))}
    if "liquidity_confirmation" in evaluated:
        details["liquidity_confirmation"] = {key: len(liquidity["5m"].get(key) or ())
                                             for key in ("sweeps", "liquidity_above", "liquidity_below")}
    if "invalidation_level" in evaluated and assessment.side is not None:
        details["invalidation_level"] = {"level": assessment.invalidation,
                                         "source": "15m support" if assessment.side == "LONG" else "15m resistance"}
    return details


def explain(assessment, structure_reports, liquidity_reports, macro_report):
    """Deterministic explanation of a V1 decision, derived from the check V1 stopped at."""
    if assessment.status == "VALID_SETUP":
        reason, deciding, outcome = ALL_PASSED, len(CHECKS), None
    else:
        reason = assessment.warnings[0]
        name, outcome = DECIDING_CHECK[reason]  # Unknown code = contract drift: fail loudly, never guess.
        deciding = CHECKS.index(name)
    groups = {"PASSED": [], "FAILED": [], "MISSING": [], "NOT_APPLICABLE": [], "NOT_EVALUATED": []}
    for index, name in enumerate(CHECKS):
        if macro_report is None and name in MACRO_CHECKS:
            groups["NOT_APPLICABLE"].append(name)  # No macro report: V1 skips every macro check.
        elif index < deciding:
            groups["PASSED"].append(name)
        elif index == deciding:
            groups[outcome].append(name)
        else:
            groups["NOT_EVALUATED"].append(name)
    evaluated = set(groups["PASSED"] + groups["FAILED"] + groups["MISSING"])
    setup_id, identity = setup_identity(assessment)
    return {"validator": VALIDATOR, "validator_version": VALIDATOR_VERSION, "status": assessment.status,
            "side": assessment.side, "decision_reason": reason,
            "passed_checks": tuple(groups["PASSED"]), "failed_checks": tuple(groups["FAILED"]),
            "missing_checks": tuple(groups["MISSING"]), "not_applicable_checks": tuple(groups["NOT_APPLICABLE"]),
            "not_evaluated_checks": tuple(groups["NOT_EVALUATED"]),
            "check_details": _details(evaluated, structure_reports, liquidity_reports, macro_report, assessment),
            "evidence_refs": _evidence_refs(structure_reports or {}, liquidity_reports or {}, macro_report,
                                            assessment),
            "setup_id": setup_id, "identity_inputs": identity}


def evaluar_setup(structure_reports, liquidity_reports, macro_report, symbol, run_id, as_of):
    """Setup Validator V2: the frozen V1 decision plus its structured explanation (P3.1)."""
    assessment = _evaluar_setup_v1(structure_reports, liquidity_reports, macro_report, symbol, run_id, as_of)
    return replace(assessment, explanation=explain(assessment, structure_reports, liquidity_reports, macro_report))
