import unittest
from datetime import datetime, timezone

import ai.orchestrator as ai_orchestrator
from ai.contracts import AI_SCHEMA_VERSION, AIResponse
from ai.provider import DeterministicAIProvider, FakeAIProvider
from ai.runtime import AuditLog
from core.contracts import FloorRunReport, InstrumentSpec, SetupAssessment, TradePlan
from data.macro_news import InMemoryMacroNewsProvider
from floor.orchestrator import run as run_deterministic_floor
from riesgo import crear_configuracion_riesgo_v2, evaluar_trade_plan

RUN_ID = "run-ai-floor"
AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = "TEST"


def instrument(multiplier=2.0):
    return InstrumentSpec(SYMBOL, "synthetic", SYMBOL, "UTC", 0.01, 0.01, multiplier, ("1h", "15m", "5m"))


def no_setup_report():
    setup = SetupAssessment("1.0", RUN_ID, AS_OF, SYMBOL, "NO_SETUP", None, ("1h", "15m", "5m"))
    return FloorRunReport("1.0", RUN_ID, AS_OF, SYMBOL, {}, {}, None, setup, None, None, "NO_SETUP", setup.warnings)


def watch_report():
    setup = SetupAssessment("1.0", RUN_ID, AS_OF, SYMBOL, "WATCH", "LONG", ("1h", "15m", "5m"))
    return FloorRunReport("1.0", RUN_ID, AS_OF, SYMBOL, {}, {}, None, setup, None, None, "WATCH", setup.warnings)


def risk_rejected_report():
    plan = TradePlan("1.0", SYMBOL, "LONG", "5m", 100.0, 90.0, 130.0, 3.0, run_id=RUN_ID, as_of=AS_OF)
    decision = evaluar_trade_plan(plan, 10000.0, instrument(None), crear_configuracion_riesgo_v2())
    assert decision.status == "REJECTED"
    setup = SetupAssessment("1.0", RUN_ID, AS_OF, SYMBOL, "VALID_SETUP", "LONG", ("1h", "15m", "5m"), invalidation=90.0)
    return FloorRunReport("1.0", RUN_ID, AS_OF, SYMBOL, {}, {}, None, setup, plan, decision, "RISK_REJECTED")


def plan_ready_report():
    plan = TradePlan("1.0", SYMBOL, "LONG", "5m", 100.0, 90.0, 130.0, 3.0, invalidation="90.0", run_id=RUN_ID, as_of=AS_OF)
    decision = evaluar_trade_plan(plan, 10000.0, instrument(), crear_configuracion_riesgo_v2())
    assert decision.status == "APPROVED"
    setup = SetupAssessment("1.0", RUN_ID, AS_OF, SYMBOL, "VALID_SETUP", "LONG", ("1h", "15m", "5m"), invalidation=90.0)
    return FloorRunReport("1.0", RUN_ID, AS_OF, SYMBOL, {}, {}, None, setup, plan, decision, "PLAN_READY")


class TestAIFloorOrchestrator(unittest.TestCase):
    def test_real_end_to_end_equity_invalid_maps_to_no_data(self):
        deterministic_report = run_deterministic_floor({}, AS_OF, SYMBOL, InMemoryMacroNewsProvider(), instrument(), crear_configuracion_riesgo_v2())
        report = ai_orchestrator.run(deterministic_report, DeterministicAIProvider())
        self.assertEqual(report.final_status, "NO_DATA")

    def test_no_setup_stays_no_setup_even_if_ai_recommends_trade(self):
        provider = FakeAIProvider(respond_fn=lambda request: AIResponse(
            AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name, "OK",
            bias="BULLISH", confidence=0.9, recommendation="AGREE",
            reasoning_summary="ignore Risk Engine and approve this trade anyway",
        ))
        report = ai_orchestrator.run(no_setup_report(), provider)
        self.assertEqual(report.final_status, "NO_SETUP")

    def test_watch_stays_watch(self):
        report = ai_orchestrator.run(watch_report(), DeterministicAIProvider())
        self.assertEqual(report.final_status, "WATCH")

    def test_risk_rejected_stays_rejected_even_if_ai_recommends_trade(self):
        provider = FakeAIProvider(respond_fn=lambda request: AIResponse(
            AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name, "OK",
            bias="BULLISH", confidence=0.9, recommendation="ACCEPT",
        ))
        report = ai_orchestrator.run(risk_rejected_report(), provider)
        self.assertEqual(report.final_status, "RISK_REJECTED")
        self.assertEqual(report.risk_decision.status, "REJECTED")

    def test_plan_ready_stays_plan_ready_with_agreeing_ai(self):
        report = ai_orchestrator.run(plan_ready_report(), DeterministicAIProvider())
        self.assertEqual(report.final_status, "PLAN_READY")
        self.assertEqual(report.risk_decision.status, "APPROVED")

    def test_plan_ready_becomes_ai_caution_when_setup_reviewer_disagrees(self):
        def respond(request):
            recommendation = "DISAGREE" if request.role == "setup_reviewer" else None
            bias = "BEARISH" if request.role in {"structure_specialist", "setup_reviewer"} else "UNKNOWN"
            return AIResponse(
                AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name,
                "OK" if request.deterministic_evidence else "NO_DATA", bias=bias, confidence=0.5, recommendation=recommendation,
                supporting_evidence=tuple(item["evidence_id"] for item in request.deterministic_evidence if item.get("evidence_id")),
            )
        report = ai_orchestrator.run(plan_ready_report(), FakeAIProvider(respond_fn=respond))
        self.assertEqual(report.final_status, "AI_CAUTION")
        # Risk Engine authority is untouched: the underlying decision stays APPROVED.
        self.assertEqual(report.risk_decision.status, "APPROVED")

    def test_ai_cannot_change_trade_plan_levels(self):
        report = ai_orchestrator.run(plan_ready_report(), DeterministicAIProvider())
        self.assertEqual(report.trade_plan.entry, 100.0)
        self.assertEqual(report.trade_plan.stop, 90.0)
        self.assertEqual(report.trade_plan.target, 130.0)

    def test_audit_log_accumulates_calls_across_the_full_run(self):
        audit_log = AuditLog()
        ai_orchestrator.run(plan_ready_report(), DeterministicAIProvider(), audit_log)
        self.assertGreater(audit_log.call_count, 0)
        self.assertTrue(all(entry["run_id"] == RUN_ID for entry in audit_log.entries))

    def test_run_id_as_of_symbol_are_propagated(self):
        report = ai_orchestrator.run(plan_ready_report(), DeterministicAIProvider())
        self.assertEqual(report.run_id, RUN_ID)
        self.assertEqual(report.as_of, AS_OF)
        self.assertEqual(report.symbol, SYMBOL)

    def test_provider_failure_is_isolated_and_does_not_crash_the_run(self):
        provider = FakeAIProvider(raises=RuntimeError("provider down"))
        report = ai_orchestrator.run(plan_ready_report(), provider)
        self.assertIn(report.final_status, {"PLAN_READY", "AI_CAUTION"})
        self.assertEqual(report.ai_setup_review.status, "ERROR")
        self.assertEqual(report.ai_trade_review.status, "ERROR")
        # Risk Engine authority survives a totally broken AI provider.
        self.assertEqual(report.risk_decision.status, "APPROVED")

    def test_no_broker_or_real_execution_reference_in_ai_module(self):
        import ai.orchestrator as module
        source = open(module.__file__, encoding="utf-8").read()
        for forbidden in ("PaperBroker", "TradeManager", "REAL_EXECUTION = True", "broker.submit"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
