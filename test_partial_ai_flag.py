"""Pre-demo fix: the Trade Reviewer is NOT_REQUIRED (not MISSING) when the cycle has no TradePlan."""
import unittest
from dataclasses import replace

from ai.contracts import AIResponse
from ai.orchestrator import run as run_ai
from ai.provider import DeterministicAIProvider
from core.contracts import RiskDecision
from test_ui import T, report
from ui.adapters import NOT_REQUIRED, from_ai_report


def ok(name):
    return AIResponse("1.0", "ui-run", T, "XAUUSD", name, "OK")


def healthy(ai, **overrides):
    return replace(ai, **{"ai_structure": ok("structure_ai"), "ai_liquidity": ok("liquidity_ai"),
                          "ai_macro": ok("macro_ai"), "ai_setup_review": ok("setup_reviewer_ai"), **overrides})


class PartialAIFlagTests(unittest.TestCase):
    def test_watch_cycle_without_plan_is_not_a_partial_ai_failure(self):
        ai = healthy(run_ai(report("WATCH"), DeterministicAIProvider()))
        self.assertIsNone(ai.ai_trade_review)
        vm = from_ai_report(ai, now=T)
        self.assertEqual(vm.agents[4].status, NOT_REQUIRED)
        self.assertNotIn("PARTIAL_AI_FAILURE", vm.warnings)

    def test_missing_trade_review_with_a_plan_is_still_flagged(self):
        approved = RiskDecision("1.0", "APPROVED", "XAUUSD", "LONG", 1, 10, 100, 90, 130, "ok")
        ai = healthy(run_ai(report("PLAN_READY", approved), DeterministicAIProvider()), ai_trade_review=None)
        vm = from_ai_report(ai, now=T)
        self.assertEqual(vm.agents[4].status, "MISSING")
        self.assertIn("PARTIAL_AI_FAILURE", vm.warnings)

    def test_other_missing_or_no_data_agents_still_flag(self):
        ai = healthy(run_ai(report("WATCH"), DeterministicAIProvider()), ai_macro=None)
        self.assertIn("PARTIAL_AI_FAILURE", from_ai_report(ai, now=T).warnings)


if __name__ == "__main__":
    unittest.main()
