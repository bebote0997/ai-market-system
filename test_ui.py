import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ai.orchestrator import run as run_ai
from ai.contracts import AIResponse
from ai.provider import DeterministicAIProvider
from core.contracts import FloorRunReport, SetupAssessment, TradePlan, RiskDecision, InstrumentSpec
from data.macro_news import InMemoryMacroNewsProvider
from floor.orchestrator import run as run_deterministic
from riesgo import crear_configuracion_riesgo_v2
from ui.adapters import MARKETS, SAFETY_LABELS, assistant_lines, empty_market, from_ai_report, state_label
from ui.components.charts import price_action_figure
from ui.fixtures.demo_floor import SAMPLE_LABEL, demo_market

T = datetime(2026, 1, 1, tzinfo=timezone.utc)


def report(status="NO_SETUP", risk=None):
    setup = SetupAssessment("1.0", "ui-run", T, "XAUUSD", "VALID_SETUP" if risk else status,
                            "LONG" if risk else None, ("1h", "15m", "5m"))
    plan = TradePlan("1.0", "XAUUSD", "LONG", "5m", 100, 90, 130, 3,
                     run_id="ui-run", as_of=T) if risk else None
    return FloorRunReport("1.0", "ui-run", T, "XAUUSD", {}, {}, None, setup, plan, risk, status)


class TestFloorUI(unittest.TestCase):
    def test_real_deterministic_run_to_ai_to_view_model(self):
        instrument = InstrumentSpec("XAUUSD", "synthetic", "XAUUSD", "UTC", 0.01, 0.01, 1.0, ("1h", "15m", "5m"))
        deterministic = run_deterministic({}, T, "XAUUSD", InMemoryMacroNewsProvider(),
                                          instrument, crear_configuracion_riesgo_v2())
        ai = run_ai(deterministic, DeterministicAIProvider())
        vm = from_ai_report(ai, now=T)
        self.assertEqual((vm.run_id, vm.symbol, vm.state, vm.risk_status),
                         (deterministic.run_id, "XAUUSD", "NO_DATA", "NOT CALLED"))
        self.assertEqual(vm.setup_status, "NO_SETUP")
        self.assertIn("equity_invalid", vm.warnings)

    def test_real_report_to_ai_report_to_view_model_and_no_mutation(self):
        deterministic = report()
        ai = run_ai(deterministic, DeterministicAIProvider())
        vm = from_ai_report(ai, now=T)
        self.assertEqual((vm.run_id, vm.symbol, vm.state, vm.risk_status),
                         ("ui-run", "XAUUSD", "NO_SETUP", "NOT CALLED"))
        self.assertEqual(len(vm.agents), 5)
        self.assertEqual(deterministic.final_status, "NO_SETUP")
        with self.assertRaises(FrozenInstanceError):
            ai.final_status = "PLAN_READY"

    def test_risk_authority_and_labels(self):
        rejected = RiskDecision("1.0", "REJECTED", "XAUUSD", "LONG", None, None, 100, 90, 130, "denied")
        approved = RiskDecision("1.0", "APPROVED", "XAUUSD", "LONG", 1, 10, 100, 90, 130, "ok")
        self.assertEqual(from_ai_report(run_ai(report("RISK_REJECTED", rejected), DeterministicAIProvider()), now=T).risk_status, "REJECTED")
        ai_ready = run_ai(report("PLAN_READY", approved), DeterministicAIProvider())
        ready = from_ai_report(replace(ai_ready, final_status="PLAN_READY"), now=T)
        self.assertEqual(ready.risk_status, "APPROVED")
        self.assertEqual(state_label(ready.state), "PAPER PLAN READY")
        self.assertEqual(SAFETY_LABELS, ("PAPER MODE", "REAL EXECUTION DISABLED"))

    def test_missing_data_and_sample_isolation(self):
        vm = empty_market("EURUSD")
        self.assertEqual(vm.risk_status, "NOT CALLED")
        self.assertIsNone(vm.plan)
        self.assertIsNone(price_action_figure(vm.bars))
        self.assertTrue(all(a.confidence is None for a in vm.agents))
        demo = demo_market("NAS100")
        self.assertTrue(demo.sample)
        self.assertIn("SAMPLE / DEMO", SAMPLE_LABEL)
        self.assertEqual(len(demo.bars), 48)
        figure = price_action_figure(demo.bars)
        self.assertEqual(len(figure.data), 1)
        self.assertEqual(figure.data[0].type, "candlestick")
        self.assertEqual(tuple(MARKETS), ("XAUUSD", "NAS100", "EURUSD"))
        self.assertIsNone(demo.risk_decision)

    def test_stale_provider_failure_and_assistant_no_authorization(self):
        ai = run_ai(report(), DeterministicAIProvider())
        stale = from_ai_report(ai, now=T + timedelta(days=1))
        self.assertIn("CRITICAL — STALE DATA", stale.warnings)
        lines = assistant_lines(stale)
        self.assertIn("FACTS", lines)
        self.assertNotIn("APPROVED", " ".join(sum((tuple(v) for v in lines.values()), ())))
        error = AIResponse("1.0", "ui-run", T, "XAUUSD", "Structure AI", "ERROR")
        failed = from_ai_report(replace(ai, ai_structure=error), now=T)
        self.assertIn("PROVIDER_FAILURE", failed.warnings)
        self.assertIsNone(failed.agents[0].confidence)

    def test_inconsistent_plan_fails_closed_and_macro_fact_separated(self):
        ai = run_ai(report(), DeterministicAIProvider())
        inconsistent = from_ai_report(replace(ai, final_status="PLAN_READY"), now=T,
                                      macro_facts=({"event": "test", "source": "sample"},))
        self.assertEqual(inconsistent.state, "STATE_INCONSISTENCY")
        self.assertEqual(inconsistent.risk_status, "NOT CALLED")
        self.assertEqual(inconsistent.macro_facts[0]["event"], "test")
        self.assertEqual(inconsistent.interpretation, ())

    def test_bias_is_context_not_direction(self):
        demo = demo_market()
        self.assertEqual(demo.agents[0].bias, "BULLISH")
        self.assertIsNone(demo.plan)
        self.assertEqual(demo.risk_status, "NOT CALLED")

    def test_renderers_have_no_run_or_broker_calls(self):
        root = Path(__file__).parent / "ui"
        for path in (*root.joinpath("components").glob("*.py"), *root.joinpath("pages").glob("*.py")):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("PaperBroker(", "ai.orchestrator.run(", "floor.orchestrator.run(", "broker.submit("):
                self.assertNotIn(forbidden, source)
        self.assertNotIn("BUY NOW", (root / "app.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
