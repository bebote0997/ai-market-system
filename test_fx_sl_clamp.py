"""DEC-FX-SL-3: FX SL fixed at 25 pips (min == max), TP fixed at FX_TP_RR (2R) = 50 pips; non-FX untouched."""
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
                         (Decimal(25), Decimal(25), Decimal("2.0")))
        self.assertEqual((POLICY_FX, LIMITS[POLICY_FX], FILL_LIMITS[POLICY_FX][0]),
                         ("V2_FX_SL_CLAMP_2R", (Decimal(2), Decimal(2)), Decimal("1.5")))

    def test_retired_env_overrides_are_ignored_with_a_warning(self):
        fixed = (Decimal(25), Decimal(25), Decimal("2.0"))
        with self.assertNoLogs("ai_floor.runtime", "WARNING"):
            self.assertEqual(fx_policy._load({}), fixed)
        for env in ({fx_policy.MIN_PIPS_ENV: "15"}, {fx_policy.MAX_PIPS_ENV: "60"}, {fx_policy.TP_RR_ENV: "3.0"},
                    {fx_policy.TP_RR_ENV: ""}, {fx_policy.MIN_PIPS_ENV: "x", fx_policy.MAX_PIPS_ENV: "70",
                                                fx_policy.TP_RR_ENV: "2.5"}):
            with self.subTest(env=env), self.assertLogs("ai_floor.runtime", "WARNING") as logs:
                self.assertEqual(fx_policy._load(env), fixed)
            line = "\n".join(logs.output)
            self.assertIn("FX_SL_OVERRIDE_IGNORED", line)
            for name, value in env.items():
                self.assertIn(name, line)
                if value:
                    self.assertNotIn(f"={value}", line)
                    self.assertNotIn(f" {value};", line)

    def test_env_overrides_cannot_change_the_policy_end_to_end(self):
        """A fresh interpreter with all three retired keys set: still SL 25 / TP 2R, identity 2R, names-only warning."""
        code = ("import json; from test_fx_sl_clamp import fx_plan; p, d = fx_plan('LONG', 1.085, 1.084);"
                "print(json.dumps([p.stop, p.target, p.risk_reward, p.policy_version]))")
        env = {**os.environ, fx_policy.MIN_PIPS_ENV: "15", fx_policy.MAX_PIPS_ENV: "60", fx_policy.TP_RR_ENV: "3.5",
               "PYTHONDONTWRITEBYTECODE": "1"}
        run = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True,
                             check=True)
        self.assertEqual(json.loads(run.stdout.strip().splitlines()[-1]), [1.0825, 1.09, 2.0, "V2_FX_SL_CLAMP_2R"])
        self.assertIn("FX_SL_OVERRIDE_IGNORED", run.stderr)
        self.assertIn(fx_policy.TP_RR_ENV, run.stderr)
        self.assertNotIn("3.5", run.stderr)

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
    def test_default_stop_is_fixed_at_25_pips(self):
        cases = [  # side, entry, stop, expected stop, action
            ("LONG", "1.08500", "1.08400", "1.08250", fx_policy.WIDENED),     # 10 pips -> 25
            ("LONG", "1.08500", "1.08280", "1.08250", fx_policy.WIDENED),     # 22 pips -> 25
            ("LONG", "1.08500", "1.08000", "1.08250", fx_policy.NARROWED),    # 50 pips -> 25
            ("LONG", "1.08500", "1.08250", "1.08250", fx_policy.UNCHANGED),   # exactly 25
            ("SHORT", "1.08500", "1.08600", "1.08750", fx_policy.WIDENED),
            ("SHORT", "1.08500", "1.09500", "1.08750", fx_policy.NARROWED),
            ("SHORT", "1.08500", "1.08750", "1.08750", fx_policy.UNCHANGED),
        ]
        for side, entry, stop, expected, action in cases:
            with self.subTest(side=side, stop=stop):
                got, how, _ = fx_policy.clamp_stop("EURUSD", side, Decimal(entry), Decimal(stop))
                self.assertEqual((got, how), (Decimal(expected), action))

    def test_explicit_band_still_clamps(self):
        for stop, expected, action in (("1.08400", "1.08300", fx_policy.WIDENED),
                                       ("1.08000", "1.08250", fx_policy.NARROWED),
                                       ("1.08280", "1.08280", fx_policy.UNCHANGED)):
            with self.subTest(stop=stop):
                got, how, _ = fx_policy.clamp_stop("EURUSD", "LONG", Decimal("1.08500"), Decimal(stop),
                                                   low=Decimal(20), high=Decimal(25))
                self.assertEqual((got, how), (Decimal(expected), action))

    def test_wrong_side_rejected(self):
        with self.assertRaises(ValueError):
            fx_policy.clamp_stop("EURUSD", "LONG", Decimal("1.085"), Decimal("1.086"))


class PlannerTests(unittest.TestCase):
    def test_below_25_widened_tp_follows(self):
        for side, stop, want_stop, want_tp in (("LONG", 1.084, 1.0825, 1.09), ("SHORT", 1.086, 1.0875, 1.08)):
            with self.subTest(side=side):
                plan, d = fx_plan(side, 1.085, stop)
                self.assertEqual((d["status"], d["sl_clamp"], plan.stop, plan.target, plan.risk_reward,
                                  plan.policy_version), ("PLAN_READY", "WIDENED", want_stop, want_tp, 2.0, POLICY_FX))
                self.assertEqual((pips(plan.entry, plan.stop), pips(plan.entry, plan.target)), (25, 50))
                self.assertEqual(d["structural_stop"], f"{stop:.5f}")

    def test_above_25_narrowed_tp_follows(self):
        for side, stop, want_stop, want_tp in (("LONG", 1.075, 1.0825, 1.09), ("SHORT", 1.095, 1.0875, 1.08)):
            with self.subTest(side=side):
                plan, d = fx_plan(side, 1.085, stop)
                self.assertEqual((d["sl_clamp"], plan.stop, plan.target), ("NARROWED", want_stop, want_tp))
                self.assertEqual((pips(plan.entry, plan.stop), pips(plan.entry, plan.target)), (25, 50))

    def test_any_structural_distance_gives_exactly_25_and_50(self):
        for side, stop, action in (("LONG", 1.0828, "WIDENED"), ("LONG", 1.0825, "UNCHANGED"),
                                   ("SHORT", 1.0873, "WIDENED"), ("SHORT", 1.0875, "UNCHANGED")):
            with self.subTest(side=side, stop=stop):
                plan, d = fx_plan(side, 1.085, stop)
                self.assertEqual(d["sl_clamp"], action)
                self.assertEqual((pips(plan.entry, plan.stop), pips(plan.entry, plan.target)), (25, 50))
                self.assertEqual(geometry(side, plan.entry, plan.stop, plan.target)[0].rr, Decimal(2))
                self.assertEqual(pips(plan.entry, plan.target), 2 * pips(plan.entry, plan.stop))

    def test_off_grid_structural_stop_clamped_on_grid(self):
        plan, d = fx_plan("LONG", 1.0850049, 1.0849)  # entry rounds up to 1.08501; 1 pip SL -> 25
        self.assertEqual((d["entry"], d["stop"], d["target"]), ("1.08501", "1.08251", "1.09001"))

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
            for side, stop, want in (("LONG", 1.084, (1.0825, 1.09)), ("SHORT", 1.095, (1.0875, 1.08))):
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
        plan, _ = fx_plan("LONG", 1.085, 1.0825)
        for config in (crear_configuracion_riesgo_v2(), crear_configuracion_riesgo_fixed_3r()):
            self.assertEqual(evaluar_trade_plan(plan, 10000.0, INS["EURUSD"], config).status, "APPROVED")
        bad = replace(plan, target=1.0901, risk_reward=float(geometry("LONG", 1.085, 1.0825, 1.0901)[0].rr))
        self.assertEqual(evaluar_trade_plan(bad, 10000.0, INS["EURUSD"], crear_configuracion_riesgo_v2()).reason,
                         "rr_above_maximum")
        xau = replace(plan, symbol="XAUUSD")  # an FX identity on a non-FX symbol is never honoured
        self.assertEqual(evaluar_trade_plan(xau, 10000.0, INS["XAUUSD"], crear_configuracion_riesgo_v2()).reason,
                         "rr_below_minimum")

    def test_ai_trade_review_accepts_fx_2r(self):
        plan, _ = fx_plan("SHORT", 1.085, 1.0873)
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
        # LONG 1.08500 / SL 1.08250 / TP 1.09000 (25 pips, 2R). 1.5R at fill f: (1.09 - f) / (f - 1.0825) = 1.5
        # -> f = 1.0855
        for rr_policy in (None, POLICY_V2_F3):
            with self.subTest(rr_policy=rr_policy):
                order, events = self.fill("LONG", 1.0825, 1.0855, rr_policy)
                self.assertEqual(order.status, "FILLED")
                self.assertEqual(events["ORDER_FILLED"]["fill_geometry"]["fill_rr_floor"], "1.5")
                order, events = self.fill("LONG", 1.0825, 1.08551, rr_policy)
                self.assertEqual((order.status, events["ORDER_REJECTED"]["reason"]),
                                 ("REJECTED", "fill_rr_below_minimum"))
        order, _ = self.fill("SHORT", 1.0875, 1.0845)  # SHORT 1.085 / 1.0875 / 1.08: 1.5R at 1.0845
        self.assertEqual(order.status, "FILLED")
        order, _ = self.fill("SHORT", 1.0875, 1.08449)
        self.assertEqual(order.status, "REJECTED")


if __name__ == "__main__":
    unittest.main()
