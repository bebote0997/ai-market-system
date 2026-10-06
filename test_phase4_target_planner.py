"""V2 Phase 4 / P4.1: Policy D planner, single R:R contract, Risk/AI/Broker alignment, flag-OFF V1."""
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

import floor.orchestrator as floor
from agents.setup_validator import evaluar_setup
from agents.target_planner import plan_policy_d
from ai.agents import trade_reviewer_ai
from ai.provider import DeterministicAIProvider
from core.contracts import AgentMessage, FloorRunReport, TradePlan
from core.rr_contract import POLICY_V1, POLICY_V2_D, band, classify, declared_matches, geometry
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from riesgo import crear_configuracion_riesgo_phase4, crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime.paper_contracts import paper_instruments

ROOT = Path(__file__).resolve().parent
AT = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)
BAR = {"1h": AT - timedelta(minutes=90), "15m": AT - timedelta(minutes=15), "5m": AT - timedelta(minutes=5)}
INSTRUMENTS = paper_instruments()
P4 = crear_configuracion_riesgo_phase4()
V1 = crear_configuracion_riesgo_v2()
# (entry, stop, R) per symbol/side: XAU R = 10.00, EUR R = 0.00100.
GEOMETRY = {("XAUUSD", "LONG"): (2650.0, 2640.0), ("XAUUSD", "SHORT"): (2650.0, 2660.0),
            ("EURUSD", "LONG"): (1.085, 1.084), ("EURUSD", "SHORT"): (1.085, 1.086)}


def at_r(symbol, side, ratio):
    entry, stop = GEOMETRY[(symbol, side)]
    risk = Decimal(repr(abs(entry - stop))).quantize(Decimal("0.00001"))
    move = risk * Decimal(str(ratio))
    return float(Decimal(repr(entry)) + (move if side == "LONG" else -move))


def make_setup(symbol, side, stop, swings, *, run_id="run", symbol_in_payload=None, ref_bar=None, swing_at=None):
    """A real V2 Setup Validator result whose structure payloads carry ``swings`` (tf -> prices)."""
    bias = "bullish" if side == "LONG" else "bearish"
    key = "highs" if side == "LONG" else "lows"

    def structure(tf):
        levels = [{"price": p, "timestamp": pd.Timestamp(swing_at or (BAR[tf] - timedelta(hours=1))),
                   "pivot_timestamp": pd.Timestamp(BAR[tf] - timedelta(hours=2))} for p in (swings or {}).get(tf, ())]
        payload = {"symbol": symbol_in_payload or symbol, "timeframe": tf, "bias": bias, "structure_state": bias,
                   "bos": {"type": "BOS", "direction": bias, "broken_level": stop,
                           "break_timestamp": pd.Timestamp(AT - timedelta(minutes=45))},
                   "retracement": None,
                   "levels": {"support": stop if side == "LONG" else None, "resistance": stop if side == "SHORT" else None},
                   "evidence": [{"timestamp": pd.Timestamp((ref_bar or {}).get(tf, BAR[tf])), "type": "closed_bar"}]}
        if swings is not None:
            payload["swings"] = {key: levels, ("lows" if side == "LONG" else "highs"): []}
        return AgentMessage("1.0", run_id, AT, symbol, tf, "structure", "OK", evidence=(payload,))
    liquidity = {tf: AgentMessage("1.0", run_id, AT, symbol, tf, "liquidity", "OK", evidence=(
        {"sweeps": [{"type": "low"}], "liquidity_above": [], "liquidity_below": [],
         "evidence": [{"timestamp": pd.Timestamp(BAR[tf]), "closed_bars": 50}]},)) for tf in ("1h", "15m", "5m")}
    result = evaluar_setup({tf: structure(tf) for tf in ("1h", "15m", "5m")}, liquidity, None, symbol, run_id, AT)
    assert result.status == "VALID_SETUP", result.warnings
    return result


def market(close):
    return {"data": pd.DataFrame({"Open": [close], "High": [close], "Low": [close], "Close": [close], "is_closed": [True]},
                                 index=pd.DatetimeIndex([BAR["5m"]]))}


def plan_d(symbol, side, swings, *, entry=None, stop=None, instrument="default", **kwargs):
    base_entry, base_stop = GEOMETRY[(symbol, side)]
    setup = make_setup(symbol, side, base_stop if stop is None else stop, swings, **kwargs)
    return plan_policy_d(setup, market(base_entry if entry is None else entry), symbol, kwargs.get("run_id", "run"), AT,
                         INSTRUMENTS[symbol] if instrument == "default" else instrument)


class RatioMatrixTests(unittest.TestCase):
    CASES = ((1.5, "RR_BELOW_FLOOR"), (2.0, "PLAN_READY"), (2.4, "PLAN_READY"), (3.0, "PLAN_READY"),
             (3.6, "PLAN_READY"), (4.0, "PLAN_READY"), (4.9, "PLAN_READY"), (5.0, "PLAN_READY"),
             (5.5, "OUT_OF_POLICY_EXTENDED_TARGET"))

    def test_first_target_ratio_matrix_all_symbols_and_sides(self):
        for (symbol, side) in GEOMETRY:
            for ratio, status in self.CASES:
                with self.subTest(symbol=symbol, side=side, ratio=ratio):
                    plan, d = plan_d(symbol, side, {"15m": [at_r(symbol, side, ratio)]})
                    self.assertEqual(d["status"], status)
                    self.assertEqual(Decimal(d["gross_rr"]), Decimal(str(ratio)))  # Exact, never rounded to a band.
                    self.assertEqual(plan is not None, status == "PLAN_READY")
                    if plan is not None:
                        self.assertEqual((plan.risk_reward, plan.policy_version), (ratio, POLICY_V2_D))
                        self.assertEqual(plan.target, at_r(symbol, side, ratio))
                    if status == "OUT_OF_POLICY_EXTENDED_TARGET":  # Observational evidence only.
                        self.assertEqual(Decimal(d["selected"]["target_price"]), Decimal(repr(at_r(symbol, side, ratio))))

    def test_just_below_2r_on_the_instrument_grid_is_rejected(self):
        for (symbol, side), tick in {("XAUUSD", "LONG"): -0.01, ("XAUUSD", "SHORT"): 0.01,
                                     ("EURUSD", "LONG"): -0.00001, ("EURUSD", "SHORT"): 0.00001}.items():
            with self.subTest(symbol=symbol, side=side):
                plan, d = plan_d(symbol, side, {"15m": [at_r(symbol, side, 2.0) + tick]})
                self.assertIsNone(plan)
                self.assertEqual(d["status"], "RR_BELOW_FLOOR")
                self.assertLess(Decimal(d["gross_rr"]), 2)

    def test_first_obstacle_is_never_skipped(self):
        plan, d = plan_d("XAUUSD", "LONG", {"15m": [at_r("XAUUSD", "LONG", 3.5)], "1h": [at_r("XAUUSD", "LONG", 1.7)]})
        self.assertEqual((plan, d["status"], Decimal(d["gross_rr"])), (None, "RR_BELOW_FLOOR", Decimal("1.7")))
        plan, d = plan_d("EURUSD", "SHORT", {"15m": [at_r("EURUSD", "SHORT", 4.5), at_r("EURUSD", "SHORT", 2.3)]})
        self.assertEqual((d["status"], Decimal(d["gross_rr"])), ("PLAN_READY", Decimal("2.3")))
        self.assertEqual(plan.target, at_r("EURUSD", "SHORT", 2.3))

    def test_confluence_same_price_merges_sources(self):
        price = at_r("XAUUSD", "LONG", 3.0)
        _, d = plan_d("XAUUSD", "LONG", {"15m": [price], "1h": [price]})
        self.assertEqual(d["selected"]["sources"], ["swing_high_15m", "swing_high_1h"])


class EvidenceTests(unittest.TestCase):
    def test_no_target_and_insufficient_evidence(self):
        below = at_r("XAUUSD", "LONG", -0.5)  # Swing high below entry.
        self.assertEqual(plan_d("XAUUSD", "LONG", {"15m": [below]})[1]["status"], "NO_VALID_TARGET")
        self.assertEqual(plan_d("XAUUSD", "LONG", {})[1]["status"], "NO_VALID_TARGET")
        self.assertEqual(plan_d("XAUUSD", "LONG", None)[1]["status"], "INSUFFICIENT_TARGET_EVIDENCE")

    def test_wrong_symbol_evidence_is_not_trusted(self):
        plan, d = plan_d("XAUUSD", "LONG", {"15m": [2680.0]}, symbol_in_payload="EURUSD")
        self.assertEqual((plan, d["status"]), (None, "INSUFFICIENT_TARGET_EVIDENCE"))
        self.assertTrue(all(c["reason"] == "lineage_mismatch" for c in d["candidates"]))

    def test_future_swing_is_not_a_target(self):
        plan, d = plan_d("XAUUSD", "LONG", {"15m": [2680.0]}, swing_at=AT + timedelta(minutes=5))
        self.assertEqual((plan, d["status"], d["candidates"][0]["reason"]), (None, "NO_VALID_TARGET", "future_evidence"))

    def test_untrusted_phase3_reference_disqualifies_that_timeframe_only(self):
        # Stale/misaligned 15m bar reference (Phase 3 INVALID) -> 15m swings ineligible; 1h swing still used.
        plan, d = plan_d("XAUUSD", "LONG", {"15m": [2670.0], "1h": [2690.0]},
                         ref_bar={"15m": AT - timedelta(minutes=20)})
        self.assertEqual((d["status"], Decimal(d["gross_rr"]), d["selected"]["source"]),
                         ("PLAN_READY", Decimal(4), "swing_high_1h"))
        self.assertEqual({c["reason"] for c in d["candidates"] if c["timeframe"] == "15m"}, {"evidence_ref_not_valid"})

    def test_heuristic_liquidity_is_never_selected(self):
        setup = make_setup("XAUUSD", "LONG", 2640.0, {"15m": [2690.0]})
        liquidity = dict(setup.evidence[1])
        liquidity["15m"] = {**liquidity["15m"], "liquidity_above": [{"price": 2670.0, "type": "equal_high"}]}
        setup = replace(setup, evidence=(setup.evidence[0], liquidity))
        plan, d = plan_policy_d(setup, market(2650.0), "XAUUSD", "run", AT, INSTRUMENTS["XAUUSD"])
        self.assertEqual((d["status"], Decimal(d["gross_rr"])), ("PLAN_READY", Decimal(4)))
        self.assertIn("heuristic_not_target_authority", {c["reason"] for c in d["candidates"]})


class GeometryAndPrecisionTests(unittest.TestCase):
    def test_invalid_geometry_nan_infinity_precision(self):
        self.assertEqual(plan_d("XAUUSD", "LONG", {"15m": [2680.0]}, entry=float("nan"))[1]["status"], "INVALID_GEOMETRY")
        self.assertEqual(plan_d("XAUUSD", "LONG", {"15m": [2680.0]}, entry=float("inf"))[1]["status"], "INVALID_GEOMETRY")
        self.assertEqual(plan_d("XAUUSD", "LONG", {"15m": [2680.0]}, entry=2635.0)[1]["status"], "INVALID_GEOMETRY")
        self.assertEqual(plan_d("XAUUSD", "LONG", {"15m": [2680.0]}, instrument=None)[1]["status"], "UNSUPPORTED_PRECISION")
        self.assertEqual(plan_d("XAUUSD", "LONG", {"15m": [2680.0]}, instrument=INSTRUMENTS["EURUSD"])[1]["status"],
                         "UNSUPPORTED_PRECISION")

    def test_rounding_never_manufactures_eligibility(self):
        # 2669.996 would round to 2670.00 (2R) to nearest; Policy D rounds toward entry -> 2669.99 -> 1.999R.
        _, d = plan_d("XAUUSD", "LONG", {"15m": [2669.996]})
        self.assertEqual((d["status"], d["selected"]["target_price"]), ("RR_BELOW_FLOOR", "2669.99"))
        _, d = plan_d("XAUUSD", "LONG", {"15m": [2670.004]})  # Rounds down to exactly 2R: eligible, not inflated.
        self.assertEqual((d["status"], Decimal(d["gross_rr"])), ("PLAN_READY", Decimal(2)))
        _, d = plan_d("XAUUSD", "LONG", {"15m": [2670.0]}, entry=2650.004)  # Entry rounds UP for LONG -> < 2R.
        self.assertEqual((d["entry"], d["status"]), ("2650.01", "RR_BELOW_FLOOR"))
        plan, d = plan_d("EURUSD", "LONG", {"15m": [1.0870000001]})
        self.assertEqual((plan.target, repr(plan.target)), (1.087, "1.087"))  # No 1.0869999999… artifacts.

    def test_boundaries_3_4_5_exact(self):
        for ratio in (3.0, 4.0, 5.0):
            for (symbol, side) in GEOMETRY:
                _, d = plan_d(symbol, side, {"15m": [at_r(symbol, side, ratio)]})
                self.assertEqual((d["status"], Decimal(d["gross_rr"])), ("PLAN_READY", Decimal(str(ratio))))


class InvalidationFirstAndIdentityTests(unittest.TestCase):
    def test_stop_is_the_frozen_invalidation_whatever_the_targets(self):
        stops = set()
        for swings in ({"15m": [2665.0]}, {"15m": [2680.0]}, {"15m": [2720.0]}, {"1h": [2674.0, 2699.0]}, {}):
            _, d = plan_d("XAUUSD", "LONG", swings)
            stops.add(d["stop"])
        self.assertEqual(stops, {"2640.00"})

    def test_idempotent_retry_restart_irrelevant_metadata(self):
        _, a = plan_d("XAUUSD", "LONG", {"15m": [2674.0]})
        _, b = plan_d("XAUUSD", "LONG", {"15m": [2674.0]})
        _, c = plan_d("XAUUSD", "LONG", {"15m": [2674.0]}, run_id="retry-run")
        self.assertEqual(a, b)
        self.assertEqual((a["decision_id"], a["status"], a["gross_rr"]), (c["decision_id"], c["status"], c["gross_rr"]))
        code = ("import test_phase4_target_planner as t; print(t.plan_d('XAUUSD', 'LONG', {'15m': [2674.0]})[1]['decision_id'])")
        other = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)
        self.assertEqual(other.stdout.strip(), a["decision_id"])
        _, moved = plan_d("XAUUSD", "LONG", {"15m": [2676.0]})
        self.assertNotEqual(moved["decision_id"], a["decision_id"])

    def test_target_metadata_never_changes_setup_id(self):
        setup = make_setup("XAUUSD", "LONG", 2640.0, {"15m": [2674.0]})
        before = setup.explanation["setup_id"]
        _, d = plan_policy_d(setup, market(2650.0), "XAUUSD", "run", AT, INSTRUMENTS["XAUUSD"])
        self.assertEqual((setup.explanation["setup_id"], d["setup_id"]), (before, before))
        self.assertEqual((d["cost_model"], d["net_rr"], d["effective_rr"]), ("NO_COST_MODEL", "UNAVAILABLE", "UNAVAILABLE"))


def p4_plan(target=2674.0, rr=None, entry=2650.0, stop=2640.0, side="LONG", policy=POLICY_V2_D):
    shape, _ = geometry(side, entry, stop, target)
    declared = float(shape.rr) if rr is None and shape is not None else rr
    return TradePlan("1.0", "XAUUSD", side, "5m", entry, stop, target, declared, invalidation=str(stop), run_id="run",
                     as_of=AT, policy_version=policy)


class RiskTests(unittest.TestCase):
    def risk(self, plan, config=P4):
        return evaluar_trade_plan(plan, 10000.0, INSTRUMENTS["XAUUSD"], config)

    def test_risk_approves_genuine_2r_to_5r_and_recomputes(self):
        for target, ok in ((2670.0, True), (2674.0, True), (2700.0, True), (2669.99, False), (2700.01, False)):
            decision = self.risk(p4_plan(target))
            self.assertEqual(decision.status == "APPROVED", ok, target)
        self.assertEqual(self.risk(p4_plan(2700.01)).reason, "rr_above_maximum")
        self.assertEqual(self.risk(p4_plan(2674.0)).quantity, 3.7735849056603774)  # Sizing unchanged.

    def test_adversarial_risk_inputs(self):
        cases = {
            "declared 3 / actual 1": (p4_plan(2660.0, rr=3.0), "rr_declared_mismatch"),
            "declared 2 / actual 1.9": (p4_plan(2669.0, rr=2.0), "rr_declared_mismatch"),
            "declared 1.9 / actual 2.4": (p4_plan(2674.0, rr=1.9), "rr_declared_mismatch"),
            "LONG wrong-side target": (p4_plan(2630.0, rr=2.0), "invalid_level_order"),
            "SHORT wrong-side target": (p4_plan(2670.0, rr=2.0, stop=2660.0, side="SHORT"), "invalid_level_order"),
            "zero risk": (p4_plan(2670.0, rr=2.0, stop=2650.0), "invalid_level_order"),
            "negative risk": (p4_plan(2670.0, rr=2.0, stop=2655.0), "invalid_level_order"),
            "NaN": (p4_plan(float("nan"), rr=2.0), "invalid_numeric_value"),
            "Infinity": (p4_plan(float("inf"), rr=2.0), "invalid_numeric_value"),
            "V1 plan under Phase 4 Risk": (p4_plan(2680.0, policy="V1"), "rr_policy_mismatch"),
        }
        for name, (plan, reason) in cases.items():
            with self.subTest(name):
                decision = self.risk(plan)
                self.assertEqual((decision.status, decision.reason), ("REJECTED", reason))
        self.assertEqual(self.risk(p4_plan(2680.0, rr=3.0), V1).status, "APPROVED")
        self.assertEqual(self.risk(p4_plan(2660.0, rr=3.0), V1).reason, "rr_declared_mismatch")  # V1 fixed too.
        self.assertEqual(self.risk(p4_plan(2674.0), V1).reason, "rr_below_minimum")  # Flag OFF keeps >= 3.

    def test_contract_units(self):
        shape, _ = geometry("LONG", 1.085, 1.084, 1.087)
        self.assertEqual((shape.rr, classify(shape.rr, POLICY_V2_D), band(shape.rr)), (Decimal(2), "WITHIN_POLICY", "2_TO_3"))
        self.assertTrue(declared_matches(2.9999999999998668, Decimal(3)))
        self.assertFalse(declared_matches(3.0, Decimal(1)))
        self.assertEqual(geometry("LONG", 1, 1, 2), (None, "invalid_level_order"))
        self.assertEqual(geometry("UP", 1, 0.5, 2), (None, "invalid_side"))


class AiAndBrokerTests(unittest.TestCase):
    def test_ai_gate_accepts_valid_2r_and_cannot_alter_levels(self):
        provider = DeterministicAIProvider()
        plan = p4_plan(2674.0)
        review = trade_reviewer_ai.review(provider, plan, None, None, None, "XAUUSD", "run", AT)
        self.assertNotEqual(review.recommendation, "REJECT_RECOMMENDATION")
        self.assertEqual((plan.stop, plan.target), (2640.0, 2674.0))
        out = trade_reviewer_ai.review(provider, p4_plan(2700.01), None, None, None, "XAUUSD", "run", AT)
        self.assertEqual(out.recommendation, "REJECT_RECOMMENDATION")
        v1 = replace(p4_plan(2674.0), policy_version="V1")
        self.assertEqual(trade_reviewer_ai.review(provider, v1, None, None, None, "XAUUSD", "run", AT).recommendation,
                         "REJECT_RECOMMENDATION")  # V1 rule (< 3) unchanged.
        self.assertNotIn("rr_policy", trade_reviewer_ai.build_request(v1, None, None, None, "XAUUSD", "run", AT)
                         .deterministic_evidence[0])

    def fill(self, open_price, policy=POLICY_V2_D, target=2674.0):
        plan = p4_plan(target)
        decision = evaluar_trade_plan(plan, 10000.0, INSTRUMENTS["XAUUSD"], P4)
        report = FloorRunReport("1.0", "run", AT, "XAUUSD", {}, {}, None, None, plan, decision, "PLAN_READY")
        broker = PaperBroker(PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0), INSTRUMENTS["XAUUSD"], rr_policy=policy)
        order = broker.submit_plan(report, None, AT)
        bar = {"symbol": "XAUUSD", "timestamp": AT + timedelta(minutes=5), "open": open_price, "high": open_price,
               "low": open_price, "close": open_price, "is_closed": True}
        broker.process_next_bar(order, bar)
        position = broker.account.open_positions.get("XAUUSD")
        return order.status, None if position is None else (position.stop, position.target)

    def test_broker_fill_gate(self):
        self.assertEqual(self.fill(2650.0), ("FILLED", (2640.0, 2674.0)))  # same price
        self.assertEqual(self.fill(2648.0), ("FILLED", (2640.0, 2674.0)))  # favorable, 3.25R
        self.assertEqual(self.fill(2652.0)[0], "REJECTED")  # adverse -> 1.83R < 2
        self.assertEqual(self.fill(2644.0)[0], "REJECTED")  # favorable gap -> 5R+ out of policy
        self.assertEqual(self.fill(2639.0)[0], "REJECTED")  # gap through the stop: invalid geometry
        self.assertEqual(self.fill(2650.0, policy=None)[0], "REJECTED")  # Flag OFF: V1 >= 3 gate unchanged.


class FlagOffTests(unittest.TestCase):
    def test_default_policy_is_v1_and_unknown_policy_refused(self):
        import inspect
        self.assertEqual(inspect.signature(floor.run).parameters["planner_policy"].default, POLICY_V1)
        self.assertNotIn("rr_policy", crear_configuracion_riesgo_v2())
        self.assertIsNone(PaperBroker(PaperAccount("1.0", "a", 1.0, 1.0, 1.0)).rr_policy)
        with self.assertRaises(ValueError):
            floor.run({}, AT, "XAUUSD", None, None, V1, equity=1.0, planner_policy="V3")
        runtime_src = (ROOT / "runtime" / "service.py").read_text(encoding="utf-8")
        self.assertNotIn("planner_policy", runtime_src)  # No runtime path can select Policy D.
        self.assertNotIn("POLICY_V2_D", runtime_src)


if __name__ == "__main__":
    unittest.main()
