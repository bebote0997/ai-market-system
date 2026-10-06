"""V2 Phase 4 / P4.0: frozen V1 Trade Planner economics — the Phase 4 regression oracle.

Pins the EXACT current outputs (main b8b7493) of Setup -> Trade Planner -> Risk -> PAPER quantity
increment -> AI trade review (deterministic provider) -> Paper Broker fill gate. Non-production: it
imports production code read-only and changes nothing. Values include current V1 behavior on purpose
(unrounded targets, manufactured 3R targets). The declared-R:R trust defect is fixed by DEC-4.2 (P4.1).
"""
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pandas as pd

from agents.trade_planner import crear_trade_plan
from ai.agents import trade_reviewer_ai
from ai.provider import DeterministicAIProvider
from core.contracts import FloorRunReport, SetupAssessment, TradePlan
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from riesgo import crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime.paper_contracts import apply_paper_quantity_increment, paper_instruments

AT = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)
INSTRUMENTS = paper_instruments()
CONFIG = crear_configuracion_riesgo_v2()


def market(close):
    return {"data": pd.DataFrame({"Open": [close], "High": [close], "Low": [close], "Close": [close],
                                  "is_closed": [True]}, index=pd.DatetimeIndex([AT - timedelta(minutes=5)]))}


def setup(symbol, side, invalidation, status="VALID_SETUP"):
    return SetupAssessment("1.0", "run", AT, symbol, status, side, ("1h", "15m", "5m"), invalidation=invalidation)


# name: (symbol, side, invalidation, entry) -> (entry, stop, target, risk_reward), (status, reason, quantity,
# capital_at_risk), PAPER quantity after the increment.
VALID = {
    "eur_long": (("EURUSD", "LONG", 1.08200, 1.08500), (1.085, 1.082, 1.0939999999999996, 3.0),
                 ("APPROVED", "approved", 9216.589861751152, 27.649769585252457), 9216.0),
    "eur_short": (("EURUSD", "SHORT", 1.08800, 1.08500), (1.085, 1.088, 1.0759999999999996, 3.0),
                  ("APPROVED", "approved", 9216.589861751152, 27.649769585254504), 9216.0),
    "xau_long": (("XAUUSD", "LONG", 2640.0, 2650.0), (2650.0, 2640.0, 2680.0, 3.0),
                 ("APPROVED", "approved", 3.7735849056603774, 37.735849056603776), 3.773),
    "xau_short": (("XAUUSD", "SHORT", 2660.0, 2650.0), (2650.0, 2660.0, 2620.0, 3.0),
                  ("APPROVED", "approved", 3.7735849056603774, 37.735849056603776), 3.773),
    "eur_precision_edge": (("EURUSD", "LONG", 1.08712, 1.08765), (1.08765, 1.08712, 1.0892399999999998, 3.0),
                           ("APPROVED", "approved", 9194.134142417139, 4.872891095480343), 9194.0),
    "xau_tiny_sl": (("XAUUSD", "LONG", 2649.99, 2650.0), (2650.0, 2649.99, 2650.0300000000007, 3.0),
                    ("APPROVED", "approved", 3.7735849056603774, 0.03773584905742747), 3.773),
    "xau_extreme_sl": (("XAUUSD", "LONG", 2400.0, 2650.0), (2650.0, 2400.0, 3400.0, 3.0),
                       ("APPROVED", "approved", 0.4, 100.0), 0.4),
    "eur_extreme_sl": (("EURUSD", "SHORT", 1.20000, 1.08500), (1.085, 1.2, 0.74, 3.0),
                       ("APPROVED", "approved", 869.5652173913044, 100.0), 869.0),
}


class V1PlannerOracle(unittest.TestCase):
    def plan(self, symbol, side, invalidation, entry, status="VALID_SETUP", data=None):
        return crear_trade_plan(setup(symbol, side, invalidation, status), data if data is not None else market(entry),
                                symbol, "run", AT)

    def test_valid_matrix_planner_risk_and_paper_units(self):
        for name, (inputs, levels, risk, paper_quantity) in VALID.items():
            with self.subTest(name):
                symbol = inputs[0]
                plan = self.plan(*inputs)
                self.assertEqual((plan.entry, plan.stop, plan.target, plan.risk_reward), levels)
                self.assertEqual((plan.symbol, plan.side, plan.timeframe, plan.invalidation),
                                 (symbol, inputs[1], "5m", str(inputs[2])))
                decision = evaluar_trade_plan(plan, 10000.0, INSTRUMENTS[symbol], CONFIG)
                self.assertEqual((decision.status, decision.reason, decision.quantity, decision.capital_at_risk), risk)
                report = FloorRunReport("1.0", "run", AT, symbol, {}, {}, None, setup(*inputs[:3]), plan, decision,
                                        "PLAN_READY")
                self.assertEqual(apply_paper_quantity_increment(report, INSTRUMENTS[symbol]).risk_decision.quantity,
                                 paper_quantity)

    def test_v1_target_is_manufactured_3r_from_entry_and_invalidation(self):
        for name, (inputs, levels, _, _) in VALID.items():
            with self.subTest(name):
                entry, stop, target, rr = levels
                self.assertEqual(rr, 3.0)  # Declared constant, not computed.
                self.assertEqual(target, entry + 3 * (entry - stop))  # No structure, no rounding.

    def test_planner_rejections_return_none(self):
        cases = {
            "wrong_long_geometry": ("XAUUSD", "LONG", 2660.0, 2650.0),
            "wrong_short_geometry": ("XAUUSD", "SHORT", 2640.0, 2650.0),
            "missing_invalidation": ("XAUUSD", "LONG", None, 2650.0),
            "equal_stop_entry": ("XAUUSD", "LONG", 2650.0, 2650.0),
        }
        for name, inputs in cases.items():
            with self.subTest(name):
                self.assertIsNone(self.plan(*inputs))
        self.assertIsNone(self.plan("XAUUSD", "LONG", 2640.0, 2650.0, status="WATCH"))
        self.assertIsNone(self.plan("XAUUSD", "LONG", 2640.0, 2650.0, data={}))  # missing market evidence
        no_close = {"data": market(2650.0)["data"].drop(columns=["Close"])}  # malformed market evidence
        self.assertIsNone(self.plan("XAUUSD", "LONG", 2640.0, 2650.0, data=no_close))
        self.assertIsNone(self.plan("XAUUSD", "LONG", 2640.0, float("nan")))
        self.assertIsNone(self.plan("XAUUSD", "LONG", float("nan"), 2650.0))

    def test_risk_rr_floor_and_declared_rr_trust(self):
        low = TradePlan("1.0", "XAUUSD", "LONG", "5m", 2650.0, 2640.0, 2670.0, 2.0, run_id="run", as_of=AT)
        self.assertEqual(evaluar_trade_plan(low, 10000.0, INSTRUMENTS["XAUUSD"], CONFIG).reason, "rr_below_minimum")
        # P4.0 pinned a V1 defect here (declared 3R / real 1R was APPROVED). DEC-4.2 fixes it in P4.1, for V1
        # too: Risk recomputes R:R from levels. Consistent V1 plans are unaffected (all rows above unchanged).
        lie = replace(low, target=2660.0, risk_reward=3.0)
        decision = evaluar_trade_plan(lie, 10000.0, INSTRUMENTS["XAUUSD"], CONFIG)
        self.assertEqual((decision.status, decision.reason), ("REJECTED", "rr_declared_mismatch"))
        self.assertEqual(CONFIG["ratio_minimo"], 3.0)

    def test_deterministic_ai_trade_review_depends_on_rr_3(self):
        provider = DeterministicAIProvider()
        plan = self.plan("XAUUSD", "LONG", 2640.0, 2650.0)
        ok = trade_reviewer_ai.review(provider, plan, None, None, None, "XAUUSD", "run", AT)
        low = trade_reviewer_ai.review(provider, replace(plan, target=2670.0, risk_reward=2.0), None, None, None,
                                       "XAUUSD", "run", AT)
        self.assertEqual((ok.recommendation, low.recommendation), ("ACCEPT", "REJECT_RECOMMENDATION"))

    def test_broker_fill_gate_rechecks_rr_3_at_fill_price(self):
        plan = self.plan("XAUUSD", "LONG", 2640.0, 2650.0)
        decision = evaluar_trade_plan(plan, 10000.0, INSTRUMENTS["XAUUSD"], CONFIG)
        report = FloorRunReport("1.0", "run", AT, "XAUUSD", {}, {}, None, setup("XAUUSD", "LONG", 2640.0), plan,
                                decision, "PLAN_READY")
        outcomes = {}
        for fill_open in (2649.5, 2650.0, 2650.5):  # Next bar open: better, equal, 0.50 worse.
            broker = PaperBroker(PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0), INSTRUMENTS["XAUUSD"])
            order = broker.submit_plan(report, None, AT)
            bar = {"symbol": "XAUUSD", "timestamp": AT + timedelta(minutes=5), "open": fill_open, "high": fill_open,
                   "low": fill_open, "close": fill_open, "is_closed": True}
            broker.process_next_bar(order, bar)
            outcomes[fill_open] = order.status
            self.assertEqual(order.cost_rate, 0.0)  # V1: no spread/slippage/commission model.
        self.assertEqual(outcomes, {2649.5: "FILLED", 2650.0: "FILLED", 2650.5: "REJECTED"})


if __name__ == "__main__":
    unittest.main()
