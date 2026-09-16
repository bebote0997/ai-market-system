import unittest
from datetime import datetime, timezone

from ai.contracts import AI_SCHEMA_VERSION, AIRequest, AIResponse
from ai.provider import DeterministicAIProvider, FakeAIProvider
from ai.runtime import AuditLog, call_agent, has_usable_evidence, snapshot_account
from execution.contracts import PaperAccount


class TestAIRuntime(unittest.TestCase):
    def request(self, evidence=()):
        return AIRequest(
            AI_SCHEMA_VERSION, "run-1", datetime(2026, 1, 1, tzinfo=timezone.utc), "TEST",
            "structure_ai", "structure_specialist", {}, evidence, ("interpret",), {}, "1.0",
        )

    def test_no_evidence_skips_provider_call_and_returns_no_data(self):
        provider = FakeAIProvider(response=None)
        response = call_agent(provider, self.request(evidence=()))
        self.assertEqual(response.status, "NO_DATA")
        self.assertEqual(len(provider.calls), 0)

    def test_has_usable_evidence(self):
        self.assertFalse(has_usable_evidence(self.request(evidence=())))
        self.assertTrue(has_usable_evidence(self.request(evidence=({"evidence_id": "x"},))))

    def test_valid_response_passes_through(self):
        request = self.request(evidence=({"evidence_id": "structure_1h", "bias": "BULLISH"},))
        provider = DeterministicAIProvider()
        response = call_agent(provider, request)
        self.assertEqual(response.status, "OK")

    def test_provider_exception_is_isolated_as_error(self):
        request = self.request(evidence=({"evidence_id": "structure_1h", "bias": "BULLISH"},))
        provider = FakeAIProvider(raises=RuntimeError("boom"))
        response = call_agent(provider, request)
        self.assertEqual(response.status, "ERROR")
        self.assertIn("provider_exception:RuntimeError", response.warnings)

    def test_hallucinated_run_id_is_rejected_to_error(self):
        request = self.request(evidence=({"evidence_id": "structure_1h", "bias": "BULLISH"},))
        bad = AIResponse(AI_SCHEMA_VERSION, "other-run", request.as_of, request.symbol, request.agent_name, "OK")
        provider = FakeAIProvider(response=bad)
        response = call_agent(provider, request)
        self.assertEqual(response.status, "ERROR")
        self.assertIn("run_id_mismatch", response.warnings)

    def test_hallucinated_symbol_is_rejected(self):
        request = self.request(evidence=({"evidence_id": "structure_1h", "bias": "BULLISH"},))
        bad = AIResponse(AI_SCHEMA_VERSION, request.run_id, request.as_of, "OTHER", request.agent_name, "OK")
        response = call_agent(FakeAIProvider(response=bad), request)
        self.assertEqual(response.status, "ERROR")
        self.assertIn("symbol_mismatch", response.warnings)

    def test_hallucinated_future_timestamp_is_rejected(self):
        request = self.request(evidence=({"evidence_id": "structure_1h", "bias": "BULLISH"},))
        future = datetime(2030, 1, 1, tzinfo=timezone.utc)
        bad = AIResponse(AI_SCHEMA_VERSION, request.run_id, future, request.symbol, request.agent_name, "OK")
        response = call_agent(FakeAIProvider(response=bad), request)
        self.assertEqual(response.status, "ERROR")
        self.assertIn("as_of_mismatch", response.warnings)

    def test_hallucinated_confidence_above_one_is_rejected(self):
        request = self.request(evidence=({"evidence_id": "structure_1h", "bias": "BULLISH"},))
        bad = AIResponse(AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name, "OK", confidence=3.0)
        response = call_agent(FakeAIProvider(response=bad), request)
        self.assertEqual(response.status, "ERROR")
        self.assertIn("invalid_confidence", response.warnings)

    def test_invented_evidence_id_is_rejected(self):
        request = self.request(evidence=({"evidence_id": "structure_1h", "bias": "BULLISH"},))
        bad = AIResponse(
            AI_SCHEMA_VERSION, request.run_id, request.as_of, request.symbol, request.agent_name, "OK",
            supporting_evidence=("structure_1h", "evidence_never_supplied"),
        )
        response = call_agent(FakeAIProvider(response=bad), request)
        self.assertEqual(response.status, "ERROR")
        self.assertIn("ungrounded_supporting_evidence", response.warnings)

    def test_audit_log_records_call(self):
        request = self.request(evidence=({"evidence_id": "structure_1h", "bias": "BULLISH"},))
        audit_log = AuditLog()
        call_agent(DeterministicAIProvider(), request, audit_log)
        self.assertEqual(audit_log.call_count, 1)
        entry = audit_log.calls_for_run("run-1")[0]
        self.assertEqual(entry["agent"], "structure_ai")
        self.assertEqual(entry["evidence_ids"], ("structure_1h",))
        self.assertNotIn("api_key", entry["provider_metadata"])

    def test_audit_log_skip_is_recorded_without_incrementing_provider_calls(self):
        provider = FakeAIProvider(response=None)
        audit_log = AuditLog()
        call_agent(provider, self.request(evidence=()), audit_log)
        self.assertEqual(audit_log.call_count, 1)
        self.assertEqual(audit_log.entries[0]["validation"], "skipped_no_data")
        self.assertEqual(len(provider.calls), 0)

    def test_snapshot_account_does_not_expose_mutable_reference(self):
        account = PaperAccount("1.0", "acc-1", 10000.0, 10000.0, 10000.0)
        snapshot = snapshot_account(account)
        snapshot["open_positions"]["FAKE"] = object()
        self.assertEqual(account.open_positions, {})


if __name__ == "__main__":
    unittest.main()
