"""V2 Phase 3 / F03-T09: formal V1/V2 Setup Validator regression.

The oracle below is the V1 Setup Validator copied verbatim from certified main c7aaafb (only the public
function renamed ``v1_evaluar_setup``). Every decision family is compared field by field, together with
the downstream authority it feeds (Trade Planner, Risk, AI setup-review request)."""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pandas as pd

from agents.setup_validator import evaluar_setup
from agents.trade_planner import crear_trade_plan
from ai.agents.setup_reviewer_ai import build_request
from core.contracts import AgentMessage, InstrumentSpec, SetupAssessment
from riesgo import crear_configuracion_riesgo_v2, evaluar_trade_plan
from test_setup_validator_v2 import AT, macro, msg, scenarios, scouts

# ---- verbatim V1 oracle (agents/setup_validator.py @ c7aaafb) ----------------------------------------
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


def v1_evaluar_setup(structure_reports, liquidity_reports, macro_report, symbol, run_id, as_of):
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

# ---- regression ---------------------------------------------------------------------------------------
INSTRUMENT = InstrumentSpec("XAUUSD", "METAL", "XAU/USD", "UTC", .01, .01, 1, ("1h", "15m", "5m"))
FIVE_MIN = {"data": pd.DataFrame({"Open": [100.0, 100.0], "High": [100.5, 100.5], "Low": [99.5, 99.5],
                                  "Close": [100.0, 100.0], "is_closed": [True, True]},
                                 index=pd.DatetimeIndex([AT - timedelta(minutes=10), AT - timedelta(minutes=5)]))}
SHORT_FIVE_MIN = {"data": FIVE_MIN["data"].assign(Close=[115.0, 115.0])}  # Below the 120 resistance.


def matrix():
    """Every decision family; keys are the expected (status, reason)."""
    cases = dict(scenarios())
    s, l = scouts()
    no_data = dict(s)
    no_data["1h"] = msg("1h", None, "NO_DATA")
    cases["scout_no_data"] = (no_data, l, None)
    return cases


EXPECTED = {
    "valid_long": ("VALID_SETUP", "LONG", None), "valid_long_macro": ("VALID_SETUP", "LONG", None),
    "valid_short": ("VALID_SETUP", "SHORT", None),
    "missing_timeframe": ("NO_SETUP", None, "missing_timeframe"),
    "scout_lineage_invalid": ("NO_SETUP", None, "scout_lineage_invalid"),
    "macro_lineage_invalid": ("NO_SETUP", None, "macro_lineage_invalid"),
    "macro_future_timestamp": ("NO_SETUP", None, "macro_future_timestamp"),
    "macro_timestamp_invalid": ("NO_SETUP", None, "macro_timestamp_invalid"),
    "scout_unavailable": ("NO_SETUP", None, "scout_unavailable"),
    "scout_no_data": ("NO_SETUP", None, "scout_unavailable"),
    "macro_error": ("NO_SETUP", None, "macro_error"),
    "missing_evidence": ("NO_SETUP", None, "missing_evidence"),
    "invalidation_missing": ("NO_SETUP", None, "invalidation_missing"),
    "high_impact_active_window": ("WATCH", None, "high_impact_active_window"),
    "timeframe_incompatibility": ("WATCH", None, "timeframe_incompatibility"),
    "structure_confirmation_missing": ("WATCH", "LONG", "structure_confirmation_missing"),
    "liquidity_confirmation_missing": ("WATCH", "LONG", "liquidity_confirmation_missing"),
}


class V1V2RegressionMatrix(unittest.TestCase):
    def test_every_decision_family_matches_v1(self):
        cases = matrix()
        self.assertEqual(set(cases), set(EXPECTED))
        families = set()
        for name, (structure, liquidity, macro_report) in cases.items():
            with self.subTest(name):
                v1 = v1_evaluar_setup(structure, liquidity, macro_report, "XAUUSD", "run", AT)
                v2 = evaluar_setup(structure, liquidity, macro_report, "XAUUSD", "run", AT)
                status, side, reason = EXPECTED[name]
                self.assertEqual((v1.status, v1.side, v1.warnings[:1] or (None,)), (status, side, (reason,)))
                for field in ("schema_version", "run_id", "timestamp", "symbol", "status", "side", "timeframes",
                              "evidence", "invalidation", "warnings", "data_quality"):
                    self.assertEqual(getattr(v2, field), getattr(v1, field), field)
                self.assertEqual(replace(v2, explanation={}), v1)  # The only difference is additive.
                self.assertEqual(v2.explanation["decision_reason"], reason or "all_checks_passed")
                families.add(status)
        self.assertEqual(families, {"VALID_SETUP", "WATCH", "NO_SETUP"})

    def test_downstream_authority_identical(self):
        config = crear_configuracion_riesgo_v2()
        for name, (structure, liquidity, macro_report) in matrix().items():
            with self.subTest(name):
                v1 = v1_evaluar_setup(structure, liquidity, macro_report, "XAUUSD", "run", AT)
                v2 = evaluar_setup(structure, liquidity, macro_report, "XAUUSD", "run", AT)
                market = SHORT_FIVE_MIN if v1.side == "SHORT" else FIVE_MIN
                plan_v1 = crear_trade_plan(v1, market, "XAUUSD", "run", AT)
                plan_v2 = crear_trade_plan(v2, market, "XAUUSD", "run", AT)
                self.assertEqual(plan_v2, plan_v1)
                if v1.status == "VALID_SETUP":
                    self.assertIsNotNone(plan_v1)  # A real plan, not None == None.
                    self.assertEqual(evaluar_trade_plan(plan_v2, 10000.0, INSTRUMENT, config),
                                     evaluar_trade_plan(plan_v1, 10000.0, INSTRUMENT, config))
                self.assertEqual(build_request(v2, None, None, None, "XAUUSD", "run", AT),
                                 build_request(v1, None, None, None, "XAUUSD", "run", AT))


if __name__ == "__main__":
    unittest.main()
