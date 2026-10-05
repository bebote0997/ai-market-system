"""V2 Phase 3 / P3.1: Setup Validator V2 core — frozen V1 decision, structured explanation, evidence
references, traceability and stable setup identity."""
import hashlib
import inspect
import json
import subprocess
import sys
import unittest
import uuid
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

import agents.setup_validator as validator
from agents.setup_validator import CHECKS, DECIDING_CHECK, _evaluar_setup_v1, evaluar_setup, setup_identity
from agents.trade_planner import crear_trade_plan
from ai.agents.setup_reviewer_ai import build_request
from core.contracts import AgentMessage
from runtime.config import RuntimeConfig
from runtime.observability import setup_id as audit_setup_id

ROOT = Path(__file__).resolve().parent
AT = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)
BAR = {"1h": AT - timedelta(hours=1), "15m": AT - timedelta(minutes=15), "5m": AT - timedelta(minutes=5)}
# sha256 of the V1 ``evaluar_setup`` body at certified main c7aaafb (now ``_evaluar_setup_v1``).
V1_DECISION_BODY_SHA256 = "6184c74d0cfa48081b0f1f16ac578d4777891bf8ab601951f43d01bbaff55170"
# Independent oracle (read from the V1 code, not from the module under test): V1 reason code -> the check
# that produced it and whether its input was unavailable (MISSING) or evaluated and not met (FAILED).
EXPECTED_DECIDING = {
    "missing_timeframe": ("timeframes_present", "MISSING"), "scout_lineage_invalid": ("scout_lineage", "FAILED"),
    "macro_lineage_invalid": ("macro_lineage", "FAILED"), "macro_future_timestamp": ("macro_timestamp", "FAILED"),
    "macro_timestamp_invalid": ("macro_timestamp", "FAILED"), "scout_unavailable": ("scouts_available", "MISSING"),
    "macro_error": ("macro_available", "MISSING"),
    "high_impact_active_window": ("no_high_impact_active_window", "FAILED"),
    "missing_evidence": ("scout_evidence_present", "MISSING"),
    "timeframe_incompatibility": ("timeframe_alignment", "FAILED"),
    "structure_confirmation_missing": ("structure_confirmation", "FAILED"),
    "liquidity_confirmation_missing": ("liquidity_confirmation", "FAILED"),
    "invalidation_missing": ("invalidation_level", "MISSING")}


def v1_setup_id(assessment):
    """The V1 audit setup_id algorithm verbatim (runtime/observability.py at c7aaafb): oracle."""
    if assessment is None or assessment.status != "VALID_SETUP":
        return None
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
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity, sort_keys=True)))


def msg(tf, payload, status="OK", run_id="run", symbol="XAUUSD", kind="scout"):
    return AgentMessage("1.0", run_id, AT, symbol, tf, kind, status, evidence=(payload,) if payload is not None else ())


def scouts(bias="bullish", run_id="run", symbol="XAUUSD", break_at=None, level=101.0, support=90.0,
           sweeps=1, bos=True, retracement=True):
    def structure_payload(tf):
        return {"bias": bias, "structure_state": bias,
                "bos": ({"type": "BOS", "direction": bias, "broken_level": level,
                         "break_timestamp": pd.Timestamp(break_at or AT - timedelta(minutes=45))} if bos else None),
                "retracement": ({"classification": "HEURISTIC", "impulse_high": 105.0, "protected_low": 95.0,
                                 "impulse_timestamp": pd.Timestamp(AT - timedelta(hours=2)),
                                 "protected_timestamp": pd.Timestamp(AT - timedelta(hours=3))} if retracement else None),
                "levels": {"support": support, "resistance": 120.0},
                "evidence": [{"timestamp": pd.Timestamp(BAR[tf]), "type": "closed_bar"}]}
    structure = {tf: msg(tf, structure_payload(tf), run_id=run_id, symbol=symbol) for tf in ("1h", "15m", "5m")}
    liquidity = {tf: msg(tf, {"sweeps": [{"type": "low"}] * sweeps, "liquidity_above": [], "liquidity_below": [],
                              "evidence": [{"timestamp": pd.Timestamp(BAR[tf]), "closed_bars": 50}]},
                         run_id=run_id, symbol=symbol) for tf in ("1h", "15m", "5m")}
    return structure, liquidity


def macro(events, status="OK", run_id="run", symbol="XAUUSD", at=AT):
    return AgentMessage("1.0", run_id, at, symbol, "context", "macro_news", status,
                        evidence=({"macro_events": tuple(events)},))


def run(structure, liquidity, macro_report=None, symbol="XAUUSD", run_id="run", as_of=AT):
    return evaluar_setup(structure, liquidity, macro_report, symbol, run_id, as_of)


def scenarios():
    """One input per V1 outcome: every reason code plus VALID LONG/SHORT, with and without macro."""
    out = {}
    s, l = scouts()
    out["valid_long"] = (s, l, None)
    out["valid_long_macro"] = (s, l, macro([{"event_id": "e1", "impact": "LOW", "window": "ACTIVE_WINDOW"}]))
    out["valid_short"] = (*scouts("bearish")[:2], None)
    out["missing_timeframe"] = ({"1h": s["1h"]}, {"1h": l["1h"]}, None)
    bad = dict(s); bad["1h"] = msg("1h", s["1h"].evidence[0], run_id="other")
    out["scout_lineage_invalid"] = (bad, l, None)
    out["macro_lineage_invalid"] = (s, l, macro([], run_id="other"))
    out["macro_future_timestamp"] = (s, l, macro([], at=AT + timedelta(minutes=1)))
    out["macro_timestamp_invalid"] = (s, l, AgentMessage("1.0", "run", "not-a-time", "XAUUSD", "context", "macro_news",
                                                         "OK", evidence=({"macro_events": ()},)))
    err = dict(s); err["5m"] = msg("5m", {}, "ERROR")
    out["scout_unavailable"] = (err, l, None)
    out["macro_error"] = (s, l, macro([], status="ERROR"))
    out["high_impact_active_window"] = (s, l, macro([{"event_id": "fomc", "impact": "HIGH", "window": "ACTIVE_WINDOW"}]))
    empty = dict(l); empty["15m"] = msg("15m", None)
    out["missing_evidence"] = (s, empty, None)
    mixed = dict(s); mixed["5m"] = scouts("bearish")[0]["5m"]
    out["timeframe_incompatibility"] = (mixed, l, None)
    out["structure_confirmation_missing"] = (*scouts(bos=False, retracement=False)[:1], l, None)
    out["liquidity_confirmation_missing"] = (s, scouts(sweeps=0)[1], None)
    out["invalidation_missing"] = (*scouts(support=None)[:1], l, None)
    return out


class FrozenDecisionTests(unittest.TestCase):
    def test_v1_decision_body_is_byte_identical_to_certified_main(self):
        body = inspect.getsource(_evaluar_setup_v1).split("\n", 2)[2]  # Skip def line and docstring.
        self.assertEqual(hashlib.sha256(body.encode()).hexdigest(), V1_DECISION_BODY_SHA256)

    def test_1_2_3_status_side_invalidation_warnings_evidence_unchanged(self):
        seen = set()
        for name, (s, l, m) in scenarios().items():
            with self.subTest(name):
                v1 = _evaluar_setup_v1(s, l, m, "XAUUSD", "run", AT)
                v2 = run(s, l, m)
                self.assertEqual(replace(v2, explanation={}), v1)  # Only the explanation is added.
                seen.add(v1.status)
                if v1.status != "VALID_SETUP":
                    self.assertEqual(v1.warnings[0], name)  # The scenario hits the intended V1 branch.
        self.assertEqual(seen, {"VALID_SETUP", "WATCH", "NO_SETUP"})
        self.assertEqual(DECIDING_CHECK, EXPECTED_DECIDING)
        self.assertEqual(set(EXPECTED_DECIDING), {n for n in scenarios() if not n.startswith("valid")})

    def test_13_downstream_planner_ai_receive_identical_inputs(self):
        s, l, _ = scenarios()["valid_long"]
        v2 = run(s, l)
        v1 = replace(v2, explanation={})
        self.assertEqual(crear_trade_plan(v2, 100.0, "XAUUSD", "run", AT), crear_trade_plan(v1, 100.0, "XAUUSD", "run", AT))
        self.assertEqual(build_request(v2, None, None, None, "XAUUSD", "run", AT),
                         build_request(v1, None, None, None, "XAUUSD", "run", AT))


class ExplanationTests(unittest.TestCase):
    def test_4_explanation_matches_actual_checks(self):
        for name, (s, l, m) in scenarios().items():
            with self.subTest(name):
                result = run(s, l, m)
                e = result.explanation
                groups = [e[k] for k in ("passed_checks", "failed_checks", "missing_checks",
                                         "not_applicable_checks", "not_evaluated_checks")]
                self.assertEqual(sorted(c for g in groups for c in g), sorted(CHECKS))  # Each check exactly once.
                self.assertEqual((e["status"], e["side"], e["validator"], e["validator_version"]),
                                 (result.status, result.side, "setup_validator", "2.0"))
                if result.status == "VALID_SETUP":
                    self.assertEqual(e["decision_reason"], "all_checks_passed")
                    self.assertEqual((e["failed_checks"], e["missing_checks"], e["not_evaluated_checks"]), ((), (), ()))
                    continue
                check, outcome = EXPECTED_DECIDING[name]
                self.assertEqual(e["decision_reason"], name)
                self.assertEqual(e["failed_checks" if outcome == "FAILED" else "missing_checks"], (check,))
                position = CHECKS.index(check)
                self.assertTrue(all(CHECKS.index(c) < position for c in e["passed_checks"]))
                self.assertTrue(all(CHECKS.index(c) > position for c in e["not_evaluated_checks"]))
                if m is None:
                    self.assertEqual(set(e["not_applicable_checks"]), validator.MACRO_CHECKS)

    def test_6_missing_evidence_is_never_counted_as_passed(self):
        for name in ("missing_timeframe", "scout_unavailable", "missing_evidence", "macro_error", "invalidation_missing"):
            with self.subTest(name):
                e = run(*scenarios()[name]).explanation
                check = EXPECTED_DECIDING[name][0]
                self.assertIn(check, e["missing_checks"])
                self.assertNotIn(check, e["passed_checks"])
                self.assertIsNone(e["setup_id"])

    def test_4b_details_derive_from_the_evidence(self):
        s, l, _ = scenarios()["valid_long"]
        d = run(s, l).explanation["check_details"]
        self.assertEqual(d["timeframe_alignment"], {"1h_bias": "bullish", "15m_structure_state": "bullish",
                                                    "5m_structure_state": "bullish"})
        self.assertEqual(d["structure_confirmation"], {"15m_bos": True, "15m_retracement": True})
        self.assertEqual(d["liquidity_confirmation"], {"sweeps": 1, "liquidity_above": 0, "liquidity_below": 0})
        self.assertEqual(d["invalidation_level"], {"level": 90.0, "source": "15m support"})
        hi = run(*scenarios()["high_impact_active_window"]).explanation["check_details"]
        self.assertEqual(hi, {"no_high_impact_active_window": {"active_high_impact_events": ("fomc",)}})

    def test_5_evidence_refs_point_at_actual_scout_bars(self):
        s, l, m = scenarios()["valid_long_macro"]
        refs = run(s, l, m).explanation["evidence_refs"]
        self.assertEqual([(r["source"], r["timeframe"], r["bar_start"]) for r in refs[:6]],
                         [(src, tf, BAR[tf].isoformat()) for src in ("structure", "liquidity") for tf in ("1h", "15m", "5m")])
        self.assertEqual(refs[6], {"source": "macro", "timeframe": None, "status": "OK", "event_ids": ("e1",)})
        refs = run(*scenarios()["missing_timeframe"]).explanation["evidence_refs"]
        self.assertEqual([(r["timeframe"], r["status"], r["bar_start"]) for r in refs if r["source"] == "structure"],
                         [("1h", "OK", BAR["1h"].isoformat()), ("15m", None, None), ("5m", None, None)])
        refs = run(*scenarios()["scout_unavailable"]).explanation["evidence_refs"]
        self.assertEqual(refs[2], {"source": "structure", "timeframe": "5m", "status": "ERROR", "bar_start": None})

    def test_7_no_confidence_metadata(self):
        e = run(*scenarios()["valid_long"]).explanation
        self.assertNotIn("confidence", json.dumps(e, default=str))


class IdentityTests(unittest.TestCase):
    def setup(self, **kwargs):
        run_id = kwargs.pop("run_id", "run")
        as_of = kwargs.pop("as_of", AT)
        s, l = scouts(run_id=run_id, **kwargs)
        return evaluar_setup(s, l, None, kwargs.get("symbol", "XAUUSD"), run_id, as_of)

    def test_identity_values_unchanged_from_v1_algorithm(self):
        for name, (s, l, m) in scenarios().items():
            result = run(s, l, m)
            self.assertEqual(result.explanation["setup_id"], v1_setup_id(result))
            self.assertEqual(audit_setup_id(result), v1_setup_id(result))
        retracement_only = self.setup(bos=False)
        self.assertEqual(retracement_only.explanation["identity_inputs"]["anchor"]["kind"], "RETRACEMENT")
        self.assertEqual(retracement_only.explanation["setup_id"], v1_setup_id(retracement_only))

    def test_8_11_same_opportunity_same_id_across_retry_and_irrelevant_metadata(self):
        base = self.setup().explanation["setup_id"]
        self.assertIsNotNone(base)
        for variant in (self.setup(run_id="retry"), self.setup(as_of=AT + timedelta(minutes=15), run_id="later"),
                        self.setup(sweeps=3)):
            self.assertEqual(variant.explanation["setup_id"], base)
        s, l = scouts()
        with_macro = evaluar_setup(s, l, macro([{"event_id": "x", "impact": "LOW", "window": "OUTSIDE"}]),
                                   "XAUUSD", "run", AT)
        self.assertEqual(with_macro.explanation["setup_id"], base)

    def test_9_restart_same_id_in_a_new_process(self):
        here = self.setup().explanation["setup_id"]
        code = ("import test_setup_validator_v2 as t; print(t.IdentityTests('setUp').setup().explanation['setup_id'])")
        other = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(other.stdout.strip(), here)

    def test_10_12_material_change_new_id(self):
        base = self.setup().explanation["setup_id"]
        for variant in (self.setup(break_at=AT - timedelta(minutes=30)), self.setup(level=102.0),
                        self.setup(support=91.0), self.setup(bias="bearish"), self.setup(symbol="EURUSD")):
            self.assertNotEqual(variant.explanation["setup_id"], base)
            self.assertIsNotNone(variant.explanation["setup_id"])

    def test_non_valid_has_no_identity(self):
        for name, (s, l, m) in scenarios().items():
            if not name.startswith("valid"):
                e = run(s, l, m).explanation
                self.assertEqual((e["setup_id"], e["identity_inputs"]), (None, None))


class TraceabilityTests(unittest.TestCase):
    def test_review_carries_explanation_and_execution_links_same_setup_id(self):
        from unittest.mock import patch
        import test_demo_runner as fixture
        case = fixture.TestDemoRunner("test_preflight_ready_and_missing_credentials")
        case.setUp()
        self.addCleanup(case.tearDown)
        runner = case.runner()
        self.addCleanup(runner.close)
        with fixture.patched_scouts("LONG"):
            outcome = runner.run_once("XAUUSD", fixture.T)
        review = runner.store.review_report(outcome["run_id"])
        setup = review["setup"]
        self.assertEqual((setup["status"], setup["decision_reason"]), ("VALID_SETUP", "all_checks_passed"))
        self.assertEqual(review["execution"]["setup_id"], setup["setup_id"])
        self.assertIsNotNone(setup["setup_id"])
        self.assertEqual(review["setup_status"], setup["status"])

    def test_14_15_16_scope(self):
        self.assertIs(RuntimeConfig().v2_position_catch_up, False)
        self.assertNotIn("NAS100", RuntimeConfig().enabled_symbols)
        self.assertIn('"paper_mode": True', (ROOT / "runtime" / "config.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
