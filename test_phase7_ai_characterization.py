"""V2 Phase 7 / P7.0: characterization of the CURRENT AI layer (main 405df6a). Read-only; no live provider calls.

Pins what exists today — including gaps — so every P7.1 difference is an explicit, Owner-approved change.
(The legacy V1 "Phase 7" artifacts AUDIT_PHASE7.md / PHASE7_CERTIFICATION.md / runtime/certify_phase7.py are
unrelated to V2 Phase 7.)
"""
import io
import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError

import ai.orchestrator as ai_orchestrator
from ai.agents import macro_ai
from ai.contracts import AIRequest, AIResponse, validate_ai_response
from ai.openai_provider import OpenAIProvider, OpenAIProviderError
from ai.provider import DeterministicAIProvider, FakeAIProvider
from ai.runtime import AuditLog, call_agent
from agents.macro_news_agent import analizar_macro_news
from data.macro_news import NoMacroDataProvider
from floor.orchestrator import run as run_floor
from riesgo import crear_configuracion_riesgo_v2
from runtime.gates import paper_policy
from storage.codec import public_metadata
from test_ai_orchestrator import no_setup_report, plan_ready_report, risk_rejected_report, watch_report
from test_demo_runner import Data, T, instrument as demo_instrument, macro_fixture, patched_scouts

NOW = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)
URL = "https://api.openai.com/v1/responses"
AGENTS_WITH_PLAN = ("structure_ai", "liquidity_ai", "macro_ai", "setup_reviewer_ai", "trade_reviewer_ai")


def request(evidence=({"evidence_id": "e1", "bias": "BULLISH"},)):
    return AIRequest("1.0", "run-1", NOW, "XAUUSD", "structure_ai", "structure_specialist", {}, evidence,
                     ("interpret_structure",), {}, "1.0")


def completed(data=None, usage=True, **changes):
    payload = {"schema_version": "1.0", "run_id": "run-1", "as_of": NOW.isoformat(), "symbol": "XAUUSD",
               "agent_name": "structure_ai", "status": "OK", "bias": "BULLISH", "confidence": 0.6,
               "recommendation": None, "observations": ["e1"], "supporting_evidence": ["e1"],
               "conflicting_evidence": [], "risks": [], "invalidation_conditions": [], "warnings": [],
               "reasoning_summary": "e1 bullish."}
    payload.update(changes)
    raw = {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": data if data is not None else json.dumps(payload)}]}]}
    if usage:
        raw["usage"] = {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}
    return raw


def http(code, body=None, headers=None):
    def raise_():
        raise HTTPError(URL, code, "x", headers or {}, None if body is None else io.BytesIO(body))
    return raise_


def provider(effect, retries=2):
    """OpenAIProvider over a fake transport; ``effect`` is a callable raising, or a raw response."""
    calls, sleeps = [], []

    def transport(payload, key, timeout):
        calls.append(payload["model"])
        return effect() if callable(effect) else effect
    p = OpenAIProvider(api_key="test-key", transport=transport, retries=retries, sleep=sleeps.append)
    return p, calls, sleeps


def full_floor_report():
    """A real deterministic PLAN_READY floor report where every specialist has evidence (all 5 agents call)."""
    with patched_scouts("LONG"):
        report = run_floor(Data().load_snapshot("XAUUSD", T), T, "XAUUSD", macro_fixture(), demo_instrument(),
                           crear_configuracion_riesgo_v2(), equity=10000.0, run_id="run-p7")
    assert report.final_status == "PLAN_READY", report.final_status
    return report


class ProviderErrorTaxonomyTests(unittest.TestCase):
    """ai/openai_provider.py classification and retry counts (default retries=2 -> at most 3 attempts)."""

    def test_taxonomy_and_attempts(self):
        quota = b'{"error":{"type":"insufficient_quota","code":"insufficient_quota","message":"secret detail"}}'
        cases = {  # effect -> (kind, http_status, attempts, timed_out)
            "401": (http(401), ("AUTH_ERROR", 401, 1, False)),
            "403": (http(403), ("AUTH_ERROR", 403, 1, False)),
            "404": (http(404), ("MODEL_UNAVAILABLE", 404, 1, False)),
            "429": (http(429), ("RATE_LIMITED", 429, 3, False)),
            "429_quota": (http(429, quota), ("QUOTA_EXHAUSTED", 429, 1, False)),
            "429_retry_after_20s": (http(429, headers={"Retry-After": "20"}), ("RATE_LIMITED", 429, 1, False)),
            "408": (http(408), ("PROVIDER_ERROR", 408, 3, False)),
            "500": (http(500), ("PROVIDER_ERROR", 500, 3, False)),
            "400": (http(400), ("PROVIDER_ERROR", 400, 1, False)),
            "timeout": (lambda: (_ for _ in ()).throw(TimeoutError()), ("CONNECTION_ERROR", None, 3, True)),
            "connect": (lambda: (_ for _ in ()).throw(URLError("refused")), ("CONNECTION_ERROR", None, 3, False)),
            "incomplete": ({"status": "incomplete"}, ("INCOMPLETE_RESPONSE", None, 1, False)),
            "malformed_json": (completed(data="{not json"), ("INVALID_STRUCTURED_RESPONSE", None, 1, False)),
            "extra_key": (completed(extra="x"), ("INVALID_STRUCTURED_RESPONSE", None, 1, False)),
        }
        for name, (effect, (kind, status, attempts, timed_out)) in cases.items():
            with self.subTest(case=name):
                p, calls, _ = provider(effect)
                with self.assertRaises(OpenAIProviderError) as caught:
                    p.generate(request())
                error = caught.exception
                self.assertEqual((error.kind, error.http_status, len(calls), error.timed_out),
                                 (kind, status, attempts, timed_out))
                self.assertNotIn("secret", str(error))
                self.assertNotIn("test-key", repr(vars(error)))
                self.assertEqual(p.last_failure, kind)
                self.assertEqual(p.health, "ERROR")

    def test_backoff_is_bounded_and_honours_short_retry_after(self):
        p, calls, sleeps = provider(http(429))
        with self.assertRaises(OpenAIProviderError):
            p.generate(request())
        self.assertEqual(len(sleeps), 2)
        self.assertTrue(all(0.4 <= s <= 0.9 for s in sleeps))  # 0.4 x 2^attempt + jitter <= 0.1
        p, calls, sleeps = provider(http(429, headers={"Retry-After": "3"}))
        with self.assertRaises(OpenAIProviderError):
            p.generate(request())
        self.assertTrue(all(3.0 <= s <= 3.1 for s in sleeps))


class ErrorPropagationTests(unittest.TestCase):
    def test_call_agent_preserves_typed_provider_kinds(self):
        # P7.0 pinned the collapse into one warning (H-7.1). P7.1 / DEC-7.1 explicitly supersedes it: the legacy
        # warning stays first (System Health reads warnings[0]) and the typed outcome is appended and audited.
        quota = b'{"error":{"code":"insufficient_quota"}}'
        expected = {"quota": ("QUOTA_EXHAUSTED", "QUOTA_EXHAUSTED", 429), "rate": ("RATE_LIMITED", "RATE_LIMITED", 429),
                    "auth": ("AUTH_ERROR", "AUTH_ERROR", 401), "timeout": ("TIMEOUT", "CONNECTION_ERROR", None),
                    "connect": ("CONNECTION_ERROR", "CONNECTION_ERROR", None),
                    "model": ("MODEL_UNAVAILABLE", "MODEL_UNAVAILABLE", 404),
                    "server": ("PROVIDER_FAILURE", "PROVIDER_ERROR", 500),
                    "invalid": ("INVALID_RESPONSE", "INVALID_STRUCTURED_RESPONSE", None)}
        effects = {"quota": http(429, quota), "rate": http(429), "auth": http(401),
                   "timeout": lambda: (_ for _ in ()).throw(TimeoutError()),
                   "connect": lambda: (_ for _ in ()).throw(URLError("x")), "model": http(404), "server": http(500),
                   "invalid": completed(data="{bad")}
        for name, effect in effects.items():
            with self.subTest(case=name):
                p, _, _ = provider(effect, retries=0)
                log = AuditLog()
                response = call_agent(p, request(), log)
                entry = log.entries[-1]
                outcome, kind, status = expected[name]
                self.assertEqual(response.warnings, ("provider_exception:OpenAIProviderError", "ai_outcome:" + outcome))
                self.assertEqual((entry["outcome"], entry["error_kind"], entry["http_status"], entry["validation"]),
                                 (outcome, kind, status, "provider_exception"))
                self.assertEqual(p.last_failure, kind)

    def test_usage_tokens_are_returned_but_dropped_by_persistence_whitelist(self):
        p, _, _ = provider(completed())
        response = call_agent(p, request())
        self.assertEqual({k: response.model_metadata[k] for k in ("input_tokens", "output_tokens", "total_tokens")},
                         {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30})
        persisted = public_metadata(response.model_metadata)  # what agent_decisions / runs store
        self.assertFalse({"input_tokens", "output_tokens", "total_tokens"} & set(persisted))
        self.assertEqual((p.health, p.last_usage["total_tokens"]), ("READY", 30))

    def test_missing_usage_is_accepted_and_reports_degraded(self):
        p, _, _ = provider(completed(usage=False))
        response = call_agent(p, request())
        self.assertEqual(response.status, "OK")
        self.assertNotIn("total_tokens", response.model_metadata)
        self.assertEqual(p.health, "DEGRADED")  # success without usage looks like a degraded provider

    def test_recovery_without_restart(self):
        state = {"fail": True}
        quota = b'{"error":{"code":"insufficient_quota"}}'

        def effect():
            if state["fail"]:
                http(429, quota)()
            return completed()
        p, calls, _ = provider(effect)
        self.assertEqual(call_agent(p, request()).status, "ERROR")
        self.assertEqual((p.health, p.last_failure), ("ERROR", "QUOTA_EXHAUSTED"))
        state["fail"] = False  # balance recharged
        self.assertEqual(call_agent(p, request()).status, "OK")  # same instance, no restart
        self.assertEqual((p.health, p.last_failure), ("READY", None))


class GroundingTests(unittest.TestCase):
    def test_invented_evidence_id_is_rejected(self):
        bad = AIResponse("1.0", "run-1", NOW, "XAUUSD", "structure_ai", "OK", supporting_evidence=("invented",))
        self.assertEqual(validate_ai_response(bad, request()), (False, "ungrounded_supporting_evidence"))
        p, _, _ = provider(completed(supporting_evidence=["invented"]))
        # P7.1 / DEC-7.1: the validation reason stays first; the typed outcome is appended.
        self.assertEqual(call_agent(p, request()).warnings,
                         ("ungrounded_supporting_evidence", "ai_outcome:UNGROUNDED_RESPONSE"))

    def test_free_text_and_bias_are_not_validated_against_evidence(self):
        # Evidence ids are positional labels (e.g. structure_1h, macro_event_0), not content hashes; free text is
        # never checked. A grounded-by-id response with an invented price in prose and a bias contrary to the
        # evidence passes validation. It is advisory text: no deterministic gate reads it.
        claim = AIResponse("1.0", "run-1", NOW, "XAUUSD", "structure_ai", "OK", bias="BEARISH",
                           observations=("price broke 1234.56 resistance",), supporting_evidence=("e1",),
                           reasoning_summary="invented level 1234.56")
        self.assertEqual(validate_ai_response(claim, request()), (True, "ok"))

    def test_no_evidence_means_no_call(self):
        fake = FakeAIProvider(raises=AssertionError("must not be called"))
        self.assertEqual(call_agent(fake, request(evidence=())).status, "NO_DATA")
        self.assertEqual(fake.calls, [])


class AuthorityTests(unittest.TestCase):
    """AI can veto (AI_CAUTION) but never create, upgrade or alter economic authority."""

    def forged(self, recommendation):
        def respond(req):
            return AIResponse("1.0", req.run_id, req.as_of, req.symbol, req.agent_name, "OK", bias="BULLISH",
                              confidence=1.0, recommendation=recommendation,
                              supporting_evidence=tuple(e["evidence_id"] for e in req.deterministic_evidence
                                                        if isinstance(e, dict) and e.get("evidence_id")))
        return FakeAIProvider(respond_fn=respond)

    def test_ai_cannot_upgrade_non_executable_outcomes(self):
        for report in (no_setup_report(), watch_report(), risk_rejected_report()):
            for rec in ("AGREE", "ACCEPT"):
                with self.subTest(status=report.final_status, rec=rec):
                    ai = ai_orchestrator.run(report, self.forged(rec))
                    self.assertEqual(ai.final_status, report.final_status)
                    self.assertIs(ai.trade_plan, report.trade_plan)
                    self.assertIs(ai.risk_decision, report.risk_decision)
                    self.assertFalse(paper_policy(ai))

    def test_only_disagree_and_reject_veto(self):
        report = plan_ready_report()
        outcomes = {rec: ai_orchestrator.run(report, self.forged(rec)).final_status
                    for rec in ("AGREE", "CAUTION", "INSUFFICIENT_DATA", "ACCEPT", "DISAGREE",
                                "REJECT_RECOMMENDATION")}
        self.assertEqual(outcomes, {"AGREE": "PLAN_READY", "CAUTION": "PLAN_READY", "INSUFFICIENT_DATA": "PLAN_READY",
                                    "ACCEPT": "PLAN_READY", "DISAGREE": "AI_CAUTION",
                                    "REJECT_RECOMMENDATION": "AI_CAUTION"})
        for rec in outcomes:
            ai = ai_orchestrator.run(report, self.forged(rec))
            self.assertEqual((ai.trade_plan.entry, ai.trade_plan.stop, ai.trade_plan.target, ai.risk_decision.quantity),
                             (report.trade_plan.entry, report.trade_plan.stop, report.trade_plan.target,
                              report.risk_decision.quantity))


class FailClosedTests(unittest.TestCase):
    def test_total_outage_keeps_plan_ready_label_but_is_not_execution_eligible(self):
        report = full_floor_report()
        quota = b'{"error":{"code":"insufficient_quota"}}'
        p, calls, _ = provider(http(429, quota))
        ai = ai_orchestrator.run(report, p, AuditLog())
        self.assertEqual(ai.final_status, "PLAN_READY")  # the aggregate label does not reflect the outage
        self.assertEqual({r.status for r in (ai.ai_structure, ai.ai_liquidity, ai.ai_macro, ai.ai_setup_review,
                                             ai.ai_trade_review)}, {"ERROR"})
        self.assertFalse(paper_policy(ai))  # fail closed at the execution gate
        self.assertEqual(len(calls), len(AGENTS_WITH_PLAN))  # quota: one attempt per agent, no retry

    def test_rate_limit_outage_multiplies_calls_per_cycle(self):
        p, calls, sleeps = provider(http(429))
        ai = ai_orchestrator.run(full_floor_report(), p, AuditLog())
        self.assertFalse(paper_policy(ai))
        self.assertEqual(len(calls), 3 * len(AGENTS_WITH_PLAN))  # 5 agents x 3 attempts, no shared circuit breaker
        self.assertEqual(len(sleeps), 2 * len(AGENTS_WITH_PLAN))

    def test_single_agent_failure_blocks_execution(self):
        report = full_floor_report()

        def respond(req):
            if req.agent_name == "liquidity_ai":
                raise OpenAIProviderError("CONNECTION_ERROR")
            return DeterministicAIProvider().generate(req)
        ai = ai_orchestrator.run(report, FakeAIProvider(respond_fn=respond))
        self.assertEqual((ai.final_status, ai.ai_liquidity.status), ("PLAN_READY", "ERROR"))
        self.assertFalse(paper_policy(ai))
        healthy = ai_orchestrator.run(report, DeterministicAIProvider())
        self.assertTrue(paper_policy(healthy))

    def test_legitimate_absence_of_macro_evidence_blocks_execution(self):
        report = full_floor_report()
        no_macro = analizar_macro_news(NoMacroDataProvider(), "XAUUSD", T, "run-p7")
        ai = ai_orchestrator.run(replace(report, macro_news_report=no_macro), DeterministicAIProvider())
        self.assertEqual((ai.final_status, ai.ai_macro.status), ("PLAN_READY", "NO_DATA"))
        self.assertFalse(paper_policy(ai))  # NO_DATA is not OK/PARTIAL: no macro events = no PAPER order
        self.assertEqual(macro_ai.build_request(no_macro, "XAUUSD", "run-p7", T).deterministic_evidence, ())


if __name__ == "__main__":
    unittest.main()
