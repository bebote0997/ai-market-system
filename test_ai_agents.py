import unittest
from datetime import datetime, timezone

from ai.agents import liquidity_ai, macro_ai, structure_ai
from ai.provider import DeterministicAIProvider
from ai.runtime import AuditLog
from core.contracts import AgentMessage

RUN_ID = "run-multi-tf"
AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = "TEST"


def structure_message(timeframe, bias, structure_state="bullish", bos=None, retracement=None):
    payload = {"bias": bias, "structure_state": structure_state, "bos": bos, "retracement": retracement}
    return AgentMessage("1.0", RUN_ID, AS_OF, SYMBOL, timeframe, "structure", "OK", evidence=(payload,))


def liquidity_message(timeframe, sweeps=(), liquidity_above=(), liquidity_below=(), order_blocks=()):
    payload = {"sweeps": sweeps, "liquidity_above": liquidity_above, "liquidity_below": liquidity_below, "order_blocks": order_blocks}
    return AgentMessage("1.0", RUN_ID, AS_OF, SYMBOL, timeframe, "liquidity", "OK", evidence=(payload,))


class TestStructureAI(unittest.TestCase):
    def test_multi_timeframe_evidence_is_tagged_per_timeframe_not_by_size(self):
        reports = {
            "1h": structure_message("1h", "bullish"),
            "15m": structure_message("15m", "bullish", retracement={"direction": "bullish"}),
            "5m": structure_message("5m", "neutral"),
        }
        request = structure_ai.build_request(reports, SYMBOL, RUN_ID, AS_OF)
        by_id = {item["evidence_id"]: item for item in request.deterministic_evidence}
        self.assertEqual(by_id["structure_1h"]["timeframe"], "1h")
        self.assertEqual(by_id["structure_1h"]["bias"], "BULLISH")
        self.assertTrue(by_id["structure_15m"]["retracement"])
        self.assertEqual(by_id["structure_5m"]["bias"], "NEUTRAL")

    def test_missing_timeframe_is_simply_omitted_not_invented(self):
        reports = {"1h": structure_message("1h", "bullish")}
        request = structure_ai.build_request(reports, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(len(request.deterministic_evidence), 1)

    def test_review_returns_grounded_response_with_alignment_metadata(self):
        reports = {
            "1h": structure_message("1h", "bullish"),
            "15m": structure_message("15m", "bullish"),
            "5m": structure_message("5m", "bullish"),
        }
        response = structure_ai.review(DeterministicAIProvider(), reports, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.status, "OK")
        self.assertEqual(response.bias, "BULLISH")
        self.assertEqual(response.model_metadata["timeframe_alignment"], "aligned")
        self.assertTrue(set(response.supporting_evidence).issubset({"structure_1h", "structure_15m", "structure_5m"}))

    def test_no_data_when_no_reports_available(self):
        response = structure_ai.review(DeterministicAIProvider(), {}, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.status, "NO_DATA")

    def test_no_lookahead_same_t1_input_yields_equivalent_request(self):
        reports_t1 = {"1h": structure_message("1h", "bullish")}
        request_a = structure_ai.build_request(reports_t1, SYMBOL, RUN_ID, AS_OF)
        # Simulate future information appearing elsewhere in the system; the
        # t1 input snapshot itself is untouched, so re-running t1 must match.
        reports_t2_elsewhere = {"1h": structure_message("1h", "bullish"), "15m": structure_message("15m", "bearish")}
        _ = structure_ai.build_request(reports_t2_elsewhere, SYMBOL, RUN_ID, AS_OF)
        request_b = structure_ai.build_request(reports_t1, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(request_a, request_b)


class TestLiquidityAI(unittest.TestCase):
    def test_sweep_evidence_is_tagged_per_timeframe(self):
        reports = {
            "1h": liquidity_message("1h"),
            "15m": liquidity_message("15m"),
            "5m": liquidity_message("5m", sweeps=({"type": "low"},)),
        }
        request = liquidity_ai.build_request(reports, SYMBOL, RUN_ID, AS_OF)
        by_id = {item["evidence_id"]: item for item in request.deterministic_evidence}
        self.assertEqual(by_id["liquidity_5m"]["sweeps"], 1)
        self.assertEqual(by_id["liquidity_1h"]["sweeps"], 0)

    def test_order_blocks_are_never_fabricated_when_absent(self):
        reports = {"5m": liquidity_message("5m")}
        request = liquidity_ai.build_request(reports, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(request.deterministic_evidence[0]["order_blocks"], 0)

    def test_review_flags_signal_presence(self):
        reports = {"5m": liquidity_message("5m", sweeps=({"type": "low"},))}
        response = liquidity_ai.review(DeterministicAIProvider(), reports, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.model_metadata["liquidity_quality"], "signal_present")


class TestMacroAI(unittest.TestCase):
    def macro_report(self, events=(), news=()):
        payload = {"macro_events": events, "news_items": news}
        return AgentMessage("1.0", RUN_ID, AS_OF, SYMBOL, "context", "macro_news", "OK", evidence=(payload,))

    def test_fact_interpretation_separation(self):
        events = ({"title": "CPI", "category": "inflation", "currency": "USD", "impact": "HIGH", "window": "ACTIVE_WINDOW"},)
        report = self.macro_report(events=events)
        response = macro_ai.review(DeterministicAIProvider(), report, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.model_metadata["interpretation"], "elevated_risk_environment")
        self.assertEqual(response.model_metadata["facts"][0]["impact"], "HIGH")

    def test_no_data_when_no_macro_report(self):
        response = macro_ai.review(DeterministicAIProvider(), None, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.status, "NO_DATA")

    def test_low_impact_is_not_flagged_as_elevated(self):
        events = ({"title": "Retail Sales", "category": "growth", "currency": "USD", "impact": "LOW", "window": "RECENT"},)
        report = self.macro_report(events=events)
        response = macro_ai.review(DeterministicAIProvider(), report, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.model_metadata["interpretation"], "no_elevated_risk_flagged")

    def test_audit_log_receives_macro_call(self):
        events = ({"title": "CPI", "category": "inflation", "currency": "USD", "impact": "HIGH", "window": "ACTIVE_WINDOW"},)
        audit_log = AuditLog()
        macro_ai.review(DeterministicAIProvider(), self.macro_report(events=events), SYMBOL, RUN_ID, AS_OF, audit_log)
        self.assertEqual(audit_log.call_count, 1)


if __name__ == "__main__":
    unittest.main()
