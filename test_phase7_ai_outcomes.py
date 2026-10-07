"""V2 Phase 7 / P7.1 Batch A: typed AI outcomes (DEC-7.1) and non-authoritative ai_availability (DEC-7.6)."""
import inspect
import unittest
from dataclasses import replace
from pathlib import Path

import ai.orchestrator as ai_orchestrator
from ai import outcomes
from ai.contracts import AIResponse
from ai.openai_provider import OpenAIProvider, OpenAIProviderError
from ai.provider import DeterministicAIProvider, FakeAIProvider
from ai.runtime import AuditLog, call_agent
from agents.macro_news_agent import analizar_macro_news
from data.macro_news import NoMacroDataProvider
from runtime.gates import paper_policy
from runtime.review import build_review
from test_phase7_ai_characterization import (NOW, T, completed, full_floor_report, http, provider, request)

ROOT = Path(__file__).resolve().parent


def failing(agents, error=None):
    """Deterministic answers, except ``agents`` which raise ``error``."""
    def respond(req):
        if req.agent_name in agents:
            raise error or OpenAIProviderError("CONNECTION_ERROR")
        return DeterministicAIProvider().generate(req)
    return FakeAIProvider(respond_fn=respond)


class TypedOutcomeTests(unittest.TestCase):
    def test_validation_and_model_outcomes(self):
        cases = {
            "invalid_schema": (completed(run_id="other"), "INVALID_SCHEMA", ("run_id_mismatch", "ai_outcome:INVALID_SCHEMA")),
            "ungrounded": (completed(supporting_evidence=["nope"]), "UNGROUNDED_RESPONSE",
                           ("ungrounded_supporting_evidence", "ai_outcome:UNGROUNDED_RESPONSE")),
            "model_error": (completed(status="ERROR", warnings=["model says no"]), "MODEL_REPORTED_ERROR",
                            ("model says no", "ai_outcome:MODEL_REPORTED_ERROR")),
            "ok": (completed(), "OK", ()),
            "partial": (completed(status="PARTIAL"), "OK", ()),
            "no_data": (completed(status="NO_DATA", supporting_evidence=[]), "NO_DATA", ()),
        }
        for name, (raw, outcome, warnings) in cases.items():
            with self.subTest(case=name):
                p, _, _ = provider(raw)
                log = AuditLog()
                response = call_agent(p, request(), log)
                self.assertEqual((log.entries[-1]["outcome"], outcomes.outcome_of(response)), (outcome, outcome))
                self.assertEqual(response.warnings, warnings)

    def test_generic_exception_and_not_configured(self):
        log = AuditLog()
        response = call_agent(FakeAIProvider(raises=RuntimeError("boom")), request(), log)
        self.assertEqual(response.warnings, ("provider_exception:RuntimeError", "ai_outcome:UNKNOWN_PROVIDER_ERROR"))
        self.assertEqual((log.entries[-1]["error_kind"], log.entries[-1]["http_status"]), (None, None))
        response = call_agent(OpenAIProvider(api_key="", transport=lambda *_: completed()), request())
        self.assertEqual(outcomes.outcome_of(response), "NOT_CONFIGURED")

    def test_skip_without_evidence_is_no_data(self):
        log = AuditLog()
        call_agent(FakeAIProvider(raises=AssertionError()), request(evidence=()), log)
        self.assertEqual((log.entries[-1]["outcome"], log.entries[-1]["validation"]), ("NO_DATA", "skipped_no_data"))

    def test_outcome_sets(self):
        self.assertEqual(outcomes.NON_TRANSIENT, {"AUTH_ERROR", "QUOTA_EXHAUSTED", "MODEL_UNAVAILABLE", "NOT_CONFIGURED"})
        self.assertTrue(outcomes.NON_TRANSIENT.isdisjoint(outcomes.TRANSIENT))
        self.assertTrue(outcomes.NON_TRANSIENT | outcomes.TRANSIENT <= outcomes.FAILURE_OUTCOMES)


class AvailabilityTests(unittest.TestCase):
    def setUp(self):
        self.report = full_floor_report()

    def availability(self, provider_, report=None):
        ai = ai_orchestrator.run(report or self.report, provider_)
        return ai, ai.ai_availability

    def test_combinations(self):
        ai, a = self.availability(DeterministicAIProvider())
        self.assertEqual((a["state"], a["failed_agents"], a["authoritative"]), ("ALL_AVAILABLE", [], False))
        self.assertTrue(paper_policy(ai))
        ai, a = self.availability(failing({"liquidity_ai"}))
        self.assertEqual((a["state"], a["failed_agents"], a["failure_outcomes"]),
                         ("PARTIAL_FAILURE", ["liquidity_ai"], ["CONNECTION_ERROR"]))
        self.assertFalse(paper_policy(ai))
        ai, a = self.availability(failing({"liquidity_ai", "macro_ai"}, OpenAIProviderError("RATE_LIMITED")))
        self.assertEqual((a["state"], a["failed_agents"], a["failure_outcomes"]),
                         ("PARTIAL_FAILURE", ["liquidity_ai", "macro_ai"], ["RATE_LIMITED"]))
        quota = b'{"error":{"code":"insufficient_quota"}}'
        p, _, _ = provider(http(429, quota))
        ai, a = self.availability(p)
        self.assertEqual((a["state"], a["failure_outcomes"], len(a["agents"])), ("TOTAL_FAILURE", ["QUOTA_EXHAUSTED"], 5))
        self.assertEqual(ai.final_status, "PLAN_READY")  # final_status semantics unchanged (P7.1)
        self.assertFalse(paper_policy(ai))
        ai, a = self.availability(OpenAIProvider(api_key=""))  # provider unavailable
        self.assertEqual((a["state"], a["failure_outcomes"]), ("TOTAL_FAILURE", ["NOT_CONFIGURED"]))
        ai, a = self.availability(DeterministicAIProvider())  # recovery: the next run is fully available
        self.assertEqual(a["state"], "ALL_AVAILABLE")

    def test_no_data_is_not_a_failure_but_still_blocks_execution(self):
        no_macro = analizar_macro_news(NoMacroDataProvider(), "XAUUSD", T, "run-p7")
        ai, a = self.availability(DeterministicAIProvider(), replace(self.report, macro_news_report=no_macro))
        self.assertEqual((a["state"], a["no_data_agents"]), ("ALL_AVAILABLE", ["macro_ai"]))
        self.assertFalse(paper_policy(ai))  # DEC-7.7: strict policy unchanged

    def test_availability_is_never_authoritative(self):
        ai = ai_orchestrator.run(self.report, failing({"macro_ai"}))
        forged = replace(ai, ai_availability={**ai.ai_availability, "state": "ALL_AVAILABLE", "failed_agents": []})
        self.assertEqual(paper_policy(forged), paper_policy(ai))
        self.assertFalse(paper_policy(forged))
        for path in ("runtime/gates.py", "runtime/service.py"):
            self.assertNotIn("ai_availability", (ROOT / path).read_text(encoding="utf-8"))
        self.assertNotIn("ai_availability", inspect.getsource(ai_orchestrator._final_status))

    def test_review_payload_carries_typed_outcomes(self):
        log = AuditLog()
        ai = ai_orchestrator.run(self.report, failing({"macro_ai"}, OpenAIProviderError("AUTH_ERROR", http_status=401)),
                                 log)
        review = build_review("slot", self.report, ai, log.entries).payload()
        self.assertEqual(review["ai_availability"]["failure_outcomes"], ["AUTH_ERROR"])
        outcome = {a["agent"]: a["outcome"] for a in review["agents"]}
        self.assertEqual((outcome["macro_ai"], outcome["structure_ai"]), ("AUTH_ERROR", "OK"))
        self.assertEqual(log.entries[2]["http_status"], 401)

    def test_ai_does_not_reach_conflict_or_risk_authorities(self):
        ai_sources = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "ai").rglob("*.py"))
        for authority in ("conflict_engine", "conflict_path", "risk_engine_v2", "risk_reservation", "save_paper",
                          "submit_plan", "PaperBroker"):
            self.assertNotIn(authority, ai_sources)
        for module in ("execution/conflict_path.py", "execution/conflict_engine.py", "execution/risk_engine_v2.py"):
            self.assertNotIn("from ai", (ROOT / module).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
