"""DEC-FX-SL-1: FX SL clamped into [45, 60] pips, TP fixed at FX_TP_RR (2R) of the clamped SL; non-FX untouched."""
import json
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import floor.orchestrator as floor
from agents.target_planner import plan_fx_fixed_rr
from ai.agents import trade_reviewer_ai
from ai.provider import DeterministicAIProvider
from core import fx_policy
from core.contracts import FloorRunReport
from core.risk_policy import (RISK_POLICY_V2_FX, RISK_POLICY_V2_P5, registered_risk_policy, risk_policy_for,
                              stamp_fx_order, stamps_registered_semantics)
from core.rr_contract import FILL_LIMITS, LIMITS, POLICY_FX, POLICY_V1, POLICY_V2_F3, geometry
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from execution.risk_engine_v2 import evaluate
from riesgo import crear_configuracion_riesgo_fixed_3r, crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime.paper_contracts import paper_instruments
from test_phase4_target_planner import AT, make_setup, market

ROOT = Path(__file__).resolve().parent
INS = paper_instruments()
PIP = Decimal("0.0001")


def fx_plan(side, entry, stop, symbol="EURUSD"):
    setup = make_setup(symbol, side, stop, {})
    return plan_fx_fixed_rr(setup, market(entry), symbol, "run", AT, INS[symbol])


def pips(a, b):
    return abs(Decimal(repr(a)) - Decimal(repr(b))) / PIP


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual((fx_policy.FX_SL_MIN_PIPS, fx_policy.FX_SL_MAX_PIPS, fx_policy.FX_TP_RR),
                         (Decimal(45), Decimal(60), Decimal("2.0")))
        self.assertEqual((POLICY_FX, LIMITS[POLICY_FX], FILL_LIMITS[POLICY_FX][0]),
                         ("V2_FX_SL_CLAMP_2R", (Decimal(2), Decimal(2)), Decimal("1.5")))

    def test_env_override_per_key_and_fail_closed(self):
        self.assertEqual(fx_policy._load({}), (Decimal(45), Decimal(60), Decimal("2.0")))
        self.assertEqual(fx_policy._load({fx_policy.MIN_PIPS_ENV: "40", fx_policy.TP_RR_ENV: "2.5"}),
                         (Decimal(40), Decimal(60), Decimal("2.5")))
        for bad in ({fx_policy.MIN_PIPS_ENV: "70"}, {fx_policy.MAX_PIPS_ENV: "x"}, {fx_policy.MIN_PIPS_ENV: "0"},
                    {fx_policy.TP_RR_ENV: "1"}, {fx_policy.TP_RR_ENV: "nan"}):
            with self.subTest(bad=bad), self.assertLogs("ai_floor.runtime", "WARNING"):
                self.assertEqual(fx_policy._load(bad), (Decimal(45), Decimal(60), Decimal("2.0")))

    def test_tp_rr_configurable_end_to_end(self):
        """A fresh interpreter with AI_FLOOR_FX_TP_RR=2.5: TP = 2.5 x the clamped SL, under its own identity."""
        code = ("import json; from test_fx_sl_clamp import fx_plan; p, d = fx_plan('LONG', 1.085, 1.084);"
                "print(json.dumps([p.stop, p.target, p.risk_reward, p.policy_version]))")
        env = {**os.environ, fx_policy.TP_RR_ENV: "2.5", "PYTHONDONTWRITEBYTECODE": "1"}
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                             check=True).stdout.strip().splitlines()[-1]
        self.assertEqual(json.loads(out), [1.0805, 1.09625, 2.5, "V2_FX_SL_CLAMP_2.5R"])

    def test_fx_symbols(self):
        self.assertTrue(fx_policy.is_fx("EURUSD"))
        self.assertTrue(fx_policy.is_fx("USDJPY"))
        for symbol in ("XAUUSD", "XAGUSD", "NAS100", "US100", "EUREUR", "", None):
            self.assertFalse(fx_policy.is_fx(symbol), symbol)
        self.assertEqual((fx_policy.pip_size("EURUSD"), fx_policy.pip_size("USDJPY")),
                         (Decimal("0.0001"), Decimal("0.01")))
        with self.assertRaises(ValueError):
            fx_policy.pip_size("XAUUSD")


class ClampTests(unittest.TestCase):
    def test_clamp_long_short(self):
        cases = [  # side, entry, stop, expected stop, action
            ("LONG", "1.08500", "1.08300", "1.08050", fx_policy.WIDENED),     # 20 pips -> 45
            ("LONG", "1.08500", "1.07500", "1.07900", fx_policy.NARROWED),    # 100 pips -> 60
            ("LONG", "1.08500", "1.08000", "1.08000", fx_policy.UNCHANGED),   # 50 pips
            ("LONG", "1.08500", "1.08050", "1.08050", fx_policy.UNCHANGED),   # exactly 45
            ("LONG", "1.08500", "1.07900", "1.07900", fx_policy.UNCHANGED),   # exactly 60
            ("SHORT", "1.08500", "1.08700", "1.08950", fx_policy.WIDENED),
            ("SHORT", "1.08500", "1.09500", "1.09100", fx_policy.NARROWED),
            ("SHORT", "1.08500", "1.09050", "1.09050", fx_policy.UNCHANGED),
        ]
        for side, entry, stop, expected, action in cases:
            with self.subTest(side=side, stop=stop):
                got, how, _ = fx_policy.clamp_stop("EURUSD", side, Decimal(entry), Decimal(stop))
                self.assertEqual((got, how), (Decimal(expected), action))

    def test_wrong_side_rejected(self):
        with self.assertRaises(ValueError):
            fx_policy.clamp_stop("EURUSD", "LONG", Decimal("1.085"), Decimal("1.086"))


class PlannerTests(unittest.TestCase):
    def test_below_min_widened_tp_follows(self):
        for side, stop, want_stop, want_tp in (("LONG", 1.083, 1.0805, 1.094), ("SHORT", 1.087, 1.0895, 1.076)):
            with self.subTest(side=side):
                plan, d = fx_plan(side, 1.085, stop)
                self.assertEqual((d["status"], d["sl_clamp"], plan.stop, plan.target, plan.risk_reward,
                                  plan.policy_version), ("PLAN_READY", "WIDENED", want_stop, want_tp, 2.0, POLICY_FX))
                self.assertEqual((pips(plan.entry, plan.stop), pips(plan.entry, plan.target)), (45, 90))
                self.assertEqual(d["structural_stop"], f"{stop:.5f}")

    def test_above_max_narrowed_tp_follows(self):
        for side, stop, want_stop, want_tp in (("LONG", 1.075, 1.079, 1.097), ("SHORT", 1.095, 1.091, 1.073)):
            with self.subTest(side=side):
                plan, d = fx_plan(side, 1.085, stop)
                self.assertEqual((d["sl_clamp"], plan.stop, plan.target), ("NARROWED", want_stop, want_tp))
                self.assertEqual((pips(plan.entry, plan.stop), pips(plan.entry, plan.target)), (60, 120))

    def test_in_range_unchanged_tp_is_2r(self):
        for side, stop, want_tp in (("LONG", 1.08, 1.095), ("SHORT", 1.0903, 1.0744)):
            with self.subTest(side=side):
                plan, d = fx_plan(side, 1.085, stop)
                self.assertEqual((d["sl_clamp"], plan.stop, plan.target), ("UNCHANGED", stop, want_tp))
                self.assertEqual(geometry(side, plan.entry, plan.stop, plan.target)[0].rr, Decimal(2))
                self.assertEqual(pips(plan.entry, plan.target), 2 * pips(plan.entry, plan.stop))

    def test_off_grid_structural_stop_clamped_on_grid(self):
        plan, d = fx_plan("LONG", 1.0850049, 1.0849)  # entry rounds up to 1.08501; 1 pip SL -> 45
        self.assertEqual((d["entry"], d["stop"], d["target"]), ("1.08501", "1.08051", "1.09401"))

    def test_invalid_geometry_still_fails_closed(self):
        for entry, stop in ((1.085, 1.085), (1.085, 1.086), (float("nan"), 1.08)):
            with self.subTest(entry=entry, stop=stop):
                plan, d = fx_plan("LONG", entry, stop)
                self.assertEqual((plan, d["status"]), (None, "INVALID_GEOMETRY"))

    def test_non_fx_planner_refused(self):
        setup = make_setup("XAUUSD", "LONG", 2640.0, {})
        with self.assertRaises(ValueError):
            plan_fx_fixed_rr(setup, market(2650.0), "XAUUSD", "run", AT, INS["XAUUSD"])


class OrchestratorTests(unittest.TestCase):
    """Floor dispatch: FX symbols use the FX planner under V1 and V2_F3; XAUUSD keeps its 3R planners."""

    def run_floor(self, symbol, side, entry, stop, policy):
        setup = make_setup(symbol, side, stop, {})
        snapshot = {"5m": market(entry)["data"]}
        with patch.object(floor, "evaluar_setup", return_value=setup), \
                patch.object(floor, "analizar_macro_news", return_value=None):
            config = crear_configuracion_riesgo_fixed_3r() if policy == POLICY_V2_F3 else crear_configuracion_riesgo_v2()
            return floor.run(snapshot, AT, symbol, None, INS[symbol], config, equity=10000.0, run_id="run",
                             planner_policy=policy)

    def test_fx_under_v1_and_f3(self):
        for policy in (POLICY_V1, POLICY_V2_F3):
            for side, stop, want in (("LONG", 1.083, (1.0805, 1.094)), ("SHORT", 1.095, (1.091, 1.073))):
                with self.subTest(policy=policy, side=side):
                    report = self.run_floor("EURUSD", side, 1.085, stop, policy)
                    self.assertEqual(report.final_status, "PLAN_READY", report.risk_decision)
                    plan, risk = report.trade_plan, report.risk_decision
                    self.assertEqual((plan.policy_version, plan.stop, plan.target), (POLICY_FX, *want))
                    self.assertEqual((risk.status, risk.stop, risk.target), ("APPROVED", *want))
                    self.assertEqual(report.target_decision["sl_clamp"], "WIDENED" if side == "LONG" else "NARROWED")

    def test_non_fx_untouched(self):
        report = self.run_floor("XAUUSD", "LONG", 2650.0, 2640.0, POLICY_V2_F3)
        self.assertEqual((report.trade_plan.policy_version, report.trade_plan.stop, report.trade_plan.target),
                         (POLICY_V2_F3, 2640.0, 2680.0))
        report = self.run_floor("XAUUSD", "SHORT", 2650.0, 2651.0, POLICY_V1)  # 1-point SL: never clamped
        self.assertEqual((report.trade_plan.policy_version, report.trade_plan.stop, report.trade_plan.target),
                         ("V1", 2651.0, 2647.0))
        self.assertNotIn("sl_clamp", report.target_decision or {})


class GateTests(unittest.TestCase):
    def test_risk_judges_fx_plan_by_its_own_contract(self):
        plan, _ = fx_plan("LONG", 1.085, 1.08)
        for config in (crear_configuracion_riesgo_v2(), crear_configuracion_riesgo_fixed_3r()):
            self.assertEqual(evaluar_trade_plan(plan, 10000.0, INS["EURUSD"], config).status, "APPROVED")
        bad = replace(plan, target=1.0951, risk_reward=float(geometry("LONG", 1.085, 1.08, 1.0951)[0].rr))
        self.assertEqual(evaluar_trade_plan(bad, 10000.0, INS["EURUSD"], crear_configuracion_riesgo_v2()).reason,
                         "rr_above_maximum")
        xau = replace(plan, symbol="XAUUSD")  # an FX identity on a non-FX symbol is never honoured
        self.assertEqual(evaluar_trade_plan(xau, 10000.0, INS["XAUUSD"], crear_configuracion_riesgo_v2()).reason,
                         "rr_below_minimum")

    def test_ai_trade_review_accepts_fx_2r(self):
        plan, _ = fx_plan("SHORT", 1.085, 1.0903)
        review = trade_reviewer_ai.review(DeterministicAIProvider(), plan, None, None, None, "EURUSD", "run", AT)
        self.assertNotEqual(review.recommendation, "REJECT_RECOMMENDATION")

    def test_risk_v2_policy_binding(self):
        plan, _ = fx_plan("LONG", 1.085, 1.08)
        account = PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0)
        self.assertIs(risk_policy_for(plan), RISK_POLICY_V2_FX)
        self.assertEqual(evaluate(plan, account, {}, INS["EURUSD"], RISK_POLICY_V2_FX).decision.status, "APPROVED")
        self.assertEqual(evaluate(plan, account, {}, INS["EURUSD"], RISK_POLICY_V2_P5).decision.reason,
                         "rr_policy_mismatch")
        self.assertTrue(stamps_registered_semantics(RISK_POLICY_V2_FX))
        self.assertIs(registered_risk_policy("V2_P5_RISK_1_FX_2R"), RISK_POLICY_V2_FX)
        self.assertEqual(RISK_POLICY_V2_FX.fill_money_risk_factor, Decimal(6) / Decimal(5))

    def fill(self, side, stop, open_price, rr_policy=None):
        plan, _ = fx_plan(side, 1.085, stop)
        decision = evaluar_trade_plan(plan, 10000.0, INS["EURUSD"], crear_configuracion_riesgo_v2())
        report = FloorRunReport("1.0", "run", AT, "EURUSD", {}, {}, None, None, plan, decision, "PLAN_READY")
        broker = PaperBroker(PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0), INS["EURUSD"], rr_policy=rr_policy)
        order = stamp_fx_order(broker.submit_plan(report, None, AT), plan)  # as runtime/service.py submits
        self.assertEqual(order.risk_policy_version, RISK_POLICY_V2_FX.version)
        broker.process_next_bar(order, {"symbol": "EURUSD", "timestamp": AT + timedelta(minutes=5), "open": open_price,
                                        "high": open_price, "low": open_price, "close": open_price, "is_closed": True})
        return order, {e.event_type: e.details for e in broker.journal}

    def test_stamp_only_fx_plans(self):
        plan, _ = fx_plan("LONG", 1.085, 1.08)
        order = type("Order", (), {"risk_policy_version": None})()
        self.assertIsNone(stamp_fx_order(order, replace(plan, policy_version=POLICY_V2_F3)).risk_policy_version)
        self.assertIsNone(stamp_fx_order(None, plan))
        self.assertEqual(stamp_fx_order(order, plan).risk_policy_version, "V2_P5_RISK_1_FX_2R")
        self.assertIn("stamp_fx_order(fresh.submit_plan(", (ROOT / "runtime" / "service.py").read_text("utf-8"))

    def test_paper_fill_floor_1_5r_long_short_any_broker(self):
        # LONG 1.08500 / SL 1.08000 / TP 1.09500 (50 pips, 2R). 1.5R at fill f: (1.095 - f) / (f - 1.08) = 1.5 -> 1.086
        for rr_policy in (None, POLICY_V2_F3):
            with self.subTest(rr_policy=rr_policy):
                order, events = self.fill("LONG", 1.08, 1.086, rr_policy)
                self.assertEqual(order.status, "FILLED")
                self.assertEqual(events["ORDER_FILLED"]["fill_geometry"]["fill_rr_floor"], "1.5")
                order, events = self.fill("LONG", 1.08, 1.08601, rr_policy)
                self.assertEqual((order.status, events["ORDER_REJECTED"]["reason"]),
                                 ("REJECTED", "fill_rr_below_minimum"))
        order, _ = self.fill("SHORT", 1.09, 1.084)  # SHORT 1.085 / 1.09 / 1.075: 1.5R at 1.084
        self.assertEqual(order.status, "FILLED")
        order, _ = self.fill("SHORT", 1.09, 1.08399)
        self.assertEqual(order.status, "REJECTED")


if __name__ == "__main__":
    unittest.main()
