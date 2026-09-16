import dataclasses
import unittest
from datetime import datetime, timezone

from ai.contracts import AI_SCHEMA_VERSION, AIRequest, AIResponse, validate_ai_response


class TestAIContracts(unittest.TestCase):
    def request(self, **overrides):
        base = dict(
            schema_version=AI_SCHEMA_VERSION,
            run_id="run-1",
            as_of=datetime(2026, 1, 1, tzinfo=timezone.utc),
            symbol="TEST",
            agent_name="structure_ai",
            role="structure_specialist",
            market_context={},
            deterministic_evidence=({"evidence_id": "structure_1h", "bias": "BULLISH"},),
            allowed_actions=("interpret_structure",),
            constraints={},
            prompt_version="1.0",
        )
        base.update(overrides)
        return AIRequest(**base)

    def response(self, request, **overrides):
        base = dict(
            schema_version=AI_SCHEMA_VERSION,
            run_id=request.run_id,
            as_of=request.as_of,
            symbol=request.symbol,
            agent_name=request.agent_name,
            status="OK",
            bias="BULLISH",
            confidence=0.7,
            supporting_evidence=("structure_1h",),
        )
        base.update(overrides)
        return AIResponse(**base)

    def test_valid_response_is_accepted(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request), request)
        self.assertTrue(valid)
        self.assertEqual(reason, "ok")

    def test_run_id_mismatch_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request, run_id="other-run"), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "run_id_mismatch")

    def test_symbol_mismatch_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request, symbol="OTHER"), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "symbol_mismatch")

    def test_agent_mismatch_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request, agent_name="liquidity_ai"), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "agent_mismatch")

    def test_future_as_of_is_rejected(self):
        request = self.request()
        future = datetime(2027, 1, 1, tzinfo=timezone.utc)
        valid, reason = validate_ai_response(self.response(request, as_of=future), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "as_of_mismatch")

    def test_invalid_bias_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request, bias="MOON"), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "invalid_bias")

    def test_confidence_above_one_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request, confidence=1.5), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "invalid_confidence")

    def test_confidence_boolean_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request, confidence=True), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "invalid_confidence")

    def test_ungrounded_supporting_evidence_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request, supporting_evidence=("structure_1h", "invented_id")), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "ungrounded_supporting_evidence")

    def test_ungrounded_conflicting_evidence_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request, conflicting_evidence=("invented_id",)), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "ungrounded_conflicting_evidence")

    def test_invalid_recommendation_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response(self.response(request, recommendation="EXECUTE_NOW"), request)
        self.assertFalse(valid)
        self.assertEqual(reason, "invalid_recommendation")

    def test_non_response_type_is_rejected(self):
        request = self.request()
        valid, reason = validate_ai_response({"bias": "BULLISH"}, request)
        self.assertFalse(valid)
        self.assertEqual(reason, "invalid_response_type")

    def test_ai_request_is_frozen(self):
        request = self.request()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            request.symbol = "OTHER"

    def test_ai_response_is_frozen(self):
        request = self.request()
        response = self.response(request)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            response.bias = "BEARISH"


if __name__ == "__main__":
    unittest.main()
