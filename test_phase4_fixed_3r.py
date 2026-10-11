"""V2 Phase 4 / DEC-4.6: fixed 3R from the certified structural invalidation; SL/TP immutable; truthful fill."""
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

import floor.orchestrator as floor
from agents.target_planner import plan_fixed_3r
from ai.agents import trade_reviewer_ai
from ai.provider import DeterministicAIProvider
from core.contracts import FloorRunReport, TradePlan
from core.rr_contract import POLICY_V2_F3, geometry
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from riesgo import crear_configuracion_riesgo_fixed_3r, crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime.paper_contracts import paper_instruments
from test_phase4_target_planner import AT, make_setup, market

ROOT = Path(__file__).resolve().parent
INS = paper_instruments()
F3 = crear_configuracion_riesgo_fixed_3r()


def plan3(symbol, side, entry, stop, swings=None, run_id="run"):
    setup = make_setup(symbol, side, stop, swings or {}, run_id=run_id)
    return plan_fixed_3r(setup, market(entry), symbol, run_id, AT, INS[symbol])


class PlannerTests(unittest.TestCase):
    def test_exact_3r_long_short_both_symbols(self):
        cases = {("XAUUSD", "LONG", 2650.0, 2640.0): 2680.0, ("XAUUSD", "SHORT", 2650.0, 2660.0): 2620.0,
                 ("EURUSD", "LONG", 1.085, 1.084): 1.088, ("EURUSD", "SHORT", 1.085, 1.086): 1.082}
        for (symbol, side, entry, stop), target in cases.items():
            with self.subTest(symbol=symbol, side=side):
                plan, d = plan3(symbol, side, entry, stop)
                self.assertEqual((d["status"], plan.target, plan.stop, plan.risk_reward, plan.policy_version),
                                 ("PLAN_READY", target, stop, 3.0, POLICY_V2_F3))
                self.assertEqual(Decimal(d["planned_rr"]), Decimal(3))
                self.assertEqual(geometry(side, plan.entry, plan.stop, plan.target)[0].rr, Decimal(3))

    def test_sl_is_the_structural_invalidation_never_derived_from_rr(self):
        stops = {plan3("XAUUSD", "LONG", 2650.0, stop)[0].stop for stop in (2640.0,)}
        for swings in ({"15m": [2655.0]}, {"15m": [2700.0]}, {"1h": [2660.0, 2720.0]}, {}):
            plan, d = plan3("XAUUSD", "LONG", 2650.0, 2640.0, swings)
            stops.add(plan.stop)
            self.assertEqual(plan.target, 2680.0)  # observational structure never moves TP
        self.assertEqual(stops, {2640.0})
        _, d = plan3("XAUUSD", "LONG", 2650.0, 2640.0, {"15m": [2655.0, 2700.0]})
        self.assertEqual([(c["target_price"], c["tp_beyond_level"]) for c in sorted(d["observational_context"],
                         key=lambda c: c["target_price"])], [("2655.00", True), ("2700.00", False)])

    def test_invalid_geometry_fails_closed(self):
        for entry, stop in ((2650.0, 2650.0), (2650.0, 2655.0), (float("nan"), 2640.0), (2650.0, float("inf")),
                            (float("inf"), 2640.0)):
            with self.subTest(entry=entry, stop=stop):
                plan, d = plan3("XAUUSD", "LONG", entry, stop)
                self.assertEqual((plan, d["status"]), (None, "INVALID_GEOMETRY"))
        plan, d = plan3("XAUUSD", "SHORT", 2650.0, 3700.0)  # 3R target would be <= 0
        self.assertEqual((plan, d["status"]), (None, "INVALID_GEOMETRY"))
        plan, d = plan3("XAUUSD", "SHORT", 2650.0, 2640.0)  # wrong side for SHORT
        self.assertEqual(d["status"], "INVALID_GEOMETRY")

    def test_precision_boundary_never_fakes_3r(self):
        plan, d = plan3("XAUUSD", "LONG", 2650.004, 2640.006)  # off-grid: entry up, SL down
        self.assertEqual((d["entry"], d["stop"], d["target"]), ("2650.01", "2640.00", "2680.04"))
        self.assertEqual(Decimal(d["planned_rr"]), Decimal(3))
        plan, d = plan3("EURUSD", "SHORT", 1.0850049, 1.0860051)
        self.assertEqual((d["entry"], d["stop"], d["target"], plan.target), ("1.08500", "1.08601", "1.08197", 1.08197))

    def test_deterministic_same_input_same_plan(self):
        a, b = plan3("XAUUSD", "LONG", 2650.0, 2640.0), plan3("XAUUSD", "LONG", 2650.0, 2640.0, run_id="retry")
        self.assertEqual((a[1]["decision_id"], a[0].target, a[0].stop), (b[1]["decision_id"], b[0].target, b[0].stop))
        self.assertNotEqual(plan3("XAUUSD", "LONG", 2650.0, 2641.0)[1]["decision_id"], a[1]["decision_id"])


def f3_plan(target=2680.0, rr=None, entry=2650.0, stop=2640.0, side="LONG", policy=POLICY_V2_F3):
    shape, _ = geometry(side, entry, stop, target)
    return TradePlan("1.0", "XAUUSD", side, "5m", entry, stop, target,
                     float(shape.rr) if rr is None and shape else rr, invalidation=str(stop), run_id="run", as_of=AT,
                     policy_version=policy)


class RiskAndAiTests(unittest.TestCase):
    def risk(self, plan, config=F3):
        return evaluar_trade_plan(plan, 10000.0, INS["XAUUSD"], config)

    def test_risk_recomputes_and_requires_exact_3r(self):
        self.assertEqual(self.risk(f3_plan()).status, "APPROVED")
        cases = {"declared 3 / actual 1": (f3_plan(2660.0, rr=3.0), "rr_declared_mismatch"),
                 "one tick inside 3R": (f3_plan(2679.99), "rr_below_minimum"),
                 "one tick beyond 3R": (f3_plan(2680.01), "rr_above_maximum"),
                 "V1 plan under fixed-3R Risk": (f3_plan(policy="V1"), "rr_policy_mismatch"),
                 "zero risk": (f3_plan(2680.0, rr=3.0, stop=2650.0), "invalid_level_order"),
                 "NaN": (f3_plan(float("nan"), rr=3.0), "invalid_numeric_value")}
        for name, (plan, reason) in cases.items():
            with self.subTest(name):
                self.assertEqual((self.risk(plan).status, self.risk(plan).reason), ("REJECTED", reason))
        self.assertEqual(self.risk(f3_plan(), crear_configuracion_riesgo_v2()).status, "APPROVED")  # V1 path ok

    def test_ai_gate_and_no_level_authority(self):
        provider = DeterministicAIProvider()
        plan = f3_plan()
        ok = trade_reviewer_ai.review(provider, plan, None, None, None, "XAUUSD", "run", AT)
        self.assertNotEqual(ok.recommendation, "REJECT_RECOMMENDATION")
        bad = trade_reviewer_ai.review(provider, f3_plan(2670.0), None, None, None, "XAUUSD", "run", AT)
        self.assertEqual(bad.recommendation, "REJECT_RECOMMENDATION")
        self.assertEqual((plan.entry, plan.stop, plan.target, plan.risk_reward), (2650.0, 2640.0, 2680.0, 3.0))


def eur_plan(side):
    """EURUSD fixed-3R plan with risk 0.00700: LONG 1.08500/1.07800 -> TP 1.10600; SHORT 1.08500/1.09200 -> 1.06400."""
    entry, stop = 1.085, (1.078 if side == "LONG" else 1.092)
    target = 1.106 if side == "LONG" else 1.064
    return TradePlan("1.0", "EURUSD", side, "5m", entry, stop, target, 3.0, invalidation=str(stop), run_id="run",
                     as_of=AT, policy_version=POLICY_V2_F3)


class BrokerTests(unittest.TestCase):
    def fill(self, open_price, side="LONG", policy=POLICY_V2_F3, plan=None):
        plan = plan or eur_plan(side)
        decision = evaluar_trade_plan(plan, 10000.0, INS[plan.symbol], F3)
        self.assertEqual(decision.status, "APPROVED")
        report = FloorRunReport("1.0", "run", AT, plan.symbol, {}, {}, None, None, plan, decision, "PLAN_READY")
        broker = PaperBroker(PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0), INS[plan.symbol], rr_policy=policy)
        order = broker.submit_plan(report, None, AT)
        bar = {"symbol": plan.symbol, "timestamp": AT + timedelta(minutes=5), "open": open_price, "high": open_price,
               "low": open_price, "close": open_price, "is_closed": True}
        broker.process_next_bar(order, bar)
        events = {e.event_type: e.details for e in broker.journal}
        return order, events, broker.account.open_positions.get(plan.symbol)

    def assert_outcome(self, open_price, side, status, rr=None, reason=None, direction=None):
        order, events, position = self.fill(open_price, side)
        self.assertEqual(order.status, status, (side, open_price))
        event = events["ORDER_FILLED" if status == "FILLED" else "ORDER_REJECTED"]
        geometry_detail = event["fill_geometry"]
        self.assertEqual((geometry_detail["planned_rr"], geometry_detail["fill_rr_floor"]), ("3", "2.5"))
        if rr is not None:
            self.assertEqual(Decimal(geometry_detail["actual_fill_rr"]), Decimal(rr))
        if reason is not None:
            self.assertEqual(event["reason"], reason)
        if direction is not None:
            self.assertEqual(geometry_detail["fill_direction"], direction)
        self.assertEqual((order.stop, order.target), (eur_plan(side).stop, eur_plan(side).target))  # never repaired
        if position is not None:
            self.assertEqual((position.stop, position.target), (order.stop, order.target))  # immutable after fill
        return geometry_detail

    def test_boundaries_long(self):
        self.assert_outcome(1.085, "LONG", "FILLED", rr="3", direction="EQUAL")  # exactly 3.00R
        self.assert_outcome(1.084, "LONG", "FILLED", rr="3.666666666666666666666666666666667", direction="FAVORABLE")
        detail = self.assert_outcome(1.08547, "LONG", "FILLED", direction="ADVERSE")  # ~2.75R accepted
        self.assertTrue(Decimal("2.74") < Decimal(detail["actual_fill_rr"]) < Decimal("2.76"))
        self.assertEqual(Decimal(detail["displacement_r"]) * Decimal("0.007"), Decimal(detail["displacement_price"]))
        self.assert_outcome(1.086, "LONG", "FILLED", rr="2.5")  # exactly 2.50R on the grid: ACCEPT
        detail = self.assert_outcome(1.08601, "LONG", "REJECTED", reason="fill_rr_below_minimum")  # one tick worse
        self.assertLess(Decimal(detail["actual_fill_rr"]), Decimal("2.5"))
        self.assert_outcome(1.09, "LONG", "REJECTED", reason="fill_rr_below_minimum")  # materially below

    def test_boundaries_short(self):
        self.assert_outcome(1.086, "SHORT", "FILLED", direction="FAVORABLE")
        self.assert_outcome(1.0845, "SHORT", "FILLED", direction="ADVERSE")  # ~2.78R accepted
        self.assert_outcome(1.084, "SHORT", "FILLED", rr="2.5")  # exactly 2.50R
        self.assert_outcome(1.08399, "SHORT", "REJECTED", reason="fill_rr_below_minimum")

    def test_fill_at_or_through_sl_and_invalid_geometry(self):
        self.assert_outcome(1.078, "LONG", "REJECTED", reason="fill_invalid_geometry")  # at SL
        self.assert_outcome(1.07, "LONG", "REJECTED", reason="fill_invalid_geometry")  # through SL
        self.assert_outcome(1.11, "LONG", "REJECTED", reason="fill_invalid_geometry")  # beyond TP
        self.assert_outcome(1.095, "SHORT", "REJECTED", reason="fill_invalid_geometry")  # through SL (SHORT)
        order, events, _ = self.fill(float("nan"))
        self.assertEqual((order.status, events["ORDER_CANCELLED"]["reason"]), ("CANCELLED", "invalid_fill_bar"))
        order, events, _ = self.fill(float("inf"))
        self.assertEqual(order.status, "CANCELLED")

    def test_v1_flag_off_unchanged(self):
        plan = replace(eur_plan("LONG"), policy_version="V1")
        decision = evaluar_trade_plan(plan, 10000.0, INS["EURUSD"], crear_configuracion_riesgo_v2())
        report = FloorRunReport("1.0", "run", AT, "EURUSD", {}, {}, None, None, plan, decision, "PLAN_READY")
        for price, status in ((1.085, "FILLED"), (1.08547, "REJECTED")):  # V1 keeps strict >= 3 at the fill
            broker = PaperBroker(PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0), INS["EURUSD"])
            order = broker.submit_plan(report, None, AT)
            broker.process_next_bar(order, {"symbol": "EURUSD", "timestamp": AT + timedelta(minutes=5), "open": price,
                                            "high": price, "low": price, "close": price, "is_closed": True})
            events = {e.event_type: e.details for e in broker.journal}
            self.assertEqual(order.status, status)
            self.assertEqual(events.get("ORDER_FILLED", events.get("ORDER_REJECTED")),
                             {} if status == "FILLED" else {"reason": "post_fill_risk_or_geometry"})
        self.assertNotIn("POLICY_V2_F3", (ROOT / "runtime" / "service.py").read_text(encoding="utf-8"))
        with self.assertRaises(ValueError):
            floor.run({}, AT, "XAUUSD", None, None, {}, equity=1.0, planner_policy="FIXED_2R")


if __name__ == "__main__":
    unittest.main()
