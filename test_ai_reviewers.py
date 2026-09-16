import dataclasses
import unittest
from datetime import datetime, timezone

from ai.agents import setup_reviewer_ai, trade_reviewer_ai
from ai.provider import DeterministicAIProvider, FakeAIProvider
from core.contracts import RiskDecision, SetupAssessment, TradePlan

RUN_ID = "run-review"
AS_OF = datetime(2026, 1, 1, tzinfo=timezone.utc)
SYMBOL = "TEST"


class TestSetupReviewerAI(unittest.TestCase):
    def test_no_setup_forces_insufficient_data_and_cannot_be_overridden(self):
        setup = SetupAssessment("1.0", RUN_ID, AS_OF, SYMBOL, "NO_SETUP", None, ("1h", "15m", "5m"))
        response = setup_reviewer_ai.review(DeterministicAIProvider(), setup, None, None, None, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.recommendation, "INSUFFICIENT_DATA")
        # SetupAssessment itself is frozen: the AI has no attribute-write path to it.
        with self.assertRaises(dataclasses.FrozenInstanceError):
            setup.status = "VALID_SETUP"

    def test_watch_yields_caution_not_executable(self):
        setup = SetupAssessment("1.0", RUN_ID, AS_OF, SYMBOL, "WATCH", "LONG", ("1h", "15m", "5m"))
        response = setup_reviewer_ai.review(DeterministicAIProvider(), setup, None, None, None, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.recommendation, "CAUTION")

    def test_valid_setup_with_aligned_specialists_agrees(self):
        setup = SetupAssessment("1.0", RUN_ID, AS_OF, SYMBOL, "VALID_SETUP", "LONG", ("1h", "15m", "5m"), invalidation=90.0)
        structure_bias = FakeAIProvider(response=None)
        from ai.contracts import AI_SCHEMA_VERSION, AIResponse
        ai_structure = AIResponse(AI_SCHEMA_VERSION, RUN_ID, AS_OF, SYMBOL, "structure_ai", "OK", bias="BULLISH")
        ai_liquidity = AIResponse(AI_SCHEMA_VERSION, RUN_ID, AS_OF, SYMBOL, "liquidity_ai", "OK", bias="BULLISH")
        response = setup_reviewer_ai.review(DeterministicAIProvider(), setup, ai_structure, ai_liquidity, None, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.recommendation, "AGREE")

    def test_valid_setup_with_conflicting_specialists_disagrees(self):
        setup = SetupAssessment("1.0", RUN_ID, AS_OF, SYMBOL, "VALID_SETUP", "LONG", ("1h", "15m", "5m"), invalidation=90.0)
        from ai.contracts import AI_SCHEMA_VERSION, AIResponse
        ai_structure = AIResponse(AI_SCHEMA_VERSION, RUN_ID, AS_OF, SYMBOL, "structure_ai", "OK", bias="BEARISH")
        response = setup_reviewer_ai.review(DeterministicAIProvider(), setup, ai_structure, None, None, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.recommendation, "DISAGREE")


class TestTradeReviewerAI(unittest.TestCase):
    def plan(self, side="LONG"):
        return TradePlan("1.0", SYMBOL, side, "5m", 100.0, 90.0, 130.0, 3.0, invalidation="90.0", run_id=RUN_ID, as_of=AS_OF)

    def test_accepts_clean_plan(self):
        response = trade_reviewer_ai.review(DeterministicAIProvider(), self.plan(), None, None, None, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.recommendation, "ACCEPT")

    def test_cannot_mutate_trade_plan_fields(self):
        plan = self.plan()
        trade_reviewer_ai.review(DeterministicAIProvider(), plan, None, None, None, SYMBOL, RUN_ID, AS_OF)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            plan.entry = 999.0
        self.assertEqual(plan.entry, 100.0)

    def test_cannot_mutate_risk_decision_fields(self):
        decision = RiskDecision("1.0", "APPROVED", SYMBOL, "LONG", 1.0, 10.0, 100.0, 90.0, 130.0, "approved")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            decision.quantity = 999.0

    def test_no_data_when_no_plan(self):
        response = trade_reviewer_ai.review(DeterministicAIProvider(), None, None, None, None, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.status, "NO_DATA")

    def test_missing_invalidation_is_rejected_advisory(self):
        plan = TradePlan("1.0", SYMBOL, "LONG", "5m", 100.0, 90.0, 130.0, 3.0, invalidation="", run_id=RUN_ID, as_of=AS_OF)
        response = trade_reviewer_ai.review(DeterministicAIProvider(), plan, None, None, None, SYMBOL, RUN_ID, AS_OF)
        self.assertEqual(response.recommendation, "REJECT_RECOMMENDATION")


if __name__ == "__main__":
    unittest.main()
