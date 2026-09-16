import unittest
from datetime import datetime, timezone

from ai.contracts import AI_SCHEMA_VERSION, AIRequest, AIResponse
from ai.provider import AIProvider, DeterministicAIProvider, FakeAIProvider


class TestAIProvider(unittest.TestCase):
    def request(self, evidence=(), role="structure_specialist", agent_name="structure_ai"):
        return AIRequest(
            AI_SCHEMA_VERSION, "run-1", datetime(2026, 1, 1, tzinfo=timezone.utc), "TEST",
            agent_name, role, {}, evidence, ("interpret",), {}, "1.0",
        )

    def test_provider_is_abstract(self):
        with self.assertRaises(TypeError):
            AIProvider()

    def test_deterministic_provider_returns_no_data_without_evidence(self):
        provider = DeterministicAIProvider()
        response = provider.generate(self.request(evidence=()))
        self.assertEqual(response.status, "NO_DATA")

    def test_deterministic_provider_never_invents_evidence_ids(self):
        provider = DeterministicAIProvider()
        evidence = ({"evidence_id": "structure_1h", "bias": "BULLISH"}, {"evidence_id": "structure_15m", "bias": "BULLISH"})
        response = provider.generate(self.request(evidence=evidence))
        self.assertTrue(set(response.supporting_evidence).issubset({item["evidence_id"] for item in evidence}))
        self.assertEqual(response.bias, "BULLISH")

    def test_deterministic_provider_mixed_bias_is_neutral(self):
        provider = DeterministicAIProvider()
        evidence = ({"evidence_id": "structure_1h", "bias": "BULLISH"}, {"evidence_id": "structure_15m", "bias": "BEARISH"})
        response = provider.generate(self.request(evidence=evidence))
        self.assertEqual(response.bias, "NEUTRAL")

    def test_deterministic_provider_confidence_within_bounds(self):
        provider = DeterministicAIProvider()
        evidence = tuple({"evidence_id": f"e{i}", "bias": "BULLISH"} for i in range(10))
        response = provider.generate(self.request(evidence=evidence))
        self.assertLessEqual(response.confidence, 1.0)
        self.assertGreaterEqual(response.confidence, 0.0)

    def test_setup_reviewer_no_setup_is_insufficient_data(self):
        provider = DeterministicAIProvider()
        request = self.request(
            evidence=({"evidence_id": "setup_status", "status": "NO_SETUP", "side": None},),
            role="setup_reviewer", agent_name="setup_reviewer_ai",
        )
        response = provider.generate(request)
        self.assertEqual(response.recommendation, "INSUFFICIENT_DATA")

    def test_fake_provider_returns_configured_response(self):
        request = self.request()
        canned = AIResponse(AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name, "OK")
        provider = FakeAIProvider(response=canned)
        self.assertIs(provider.generate(request), canned)
        self.assertEqual(len(provider.calls), 1)

    def test_fake_provider_can_raise(self):
        provider = FakeAIProvider(raises=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            provider.generate(self.request())


if __name__ == "__main__":
    unittest.main()
