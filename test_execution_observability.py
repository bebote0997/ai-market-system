"""Audit outcomes without altering PAPER decisions or sending network traffic."""
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from ai.provider import DeterministicAIProvider
from runtime.demo_runner import DemoRunner
from runtime.notifications import SlackNotificationSink
from runtime.review_export import review_bundle_json, run_evidence_json
import test_demo_runner as fixture
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts


class ExecutionObservabilityTests(unittest.TestCase):
    setUp = fixture.TestDemoRunner.setUp
    tearDown = fixture.TestDemoRunner.tearDown
    runner = fixture.TestDemoRunner.runner
    def test_submitted_then_existing_position_is_explained_and_stable(self):
        sent = []
        sink = SlackNotificationSink(webhook_url="https://example.invalid/test",
                                     transport=lambda _url, payload, _timeout: sent.append(payload["text"]))
        runner = self.runner(sink=sink)
        try:
            with patched_scouts("LONG"):
                first = runner.run_once("XAUUSD", T)
            initial = runner.store.review_report(first["run_id"])["execution"]
            self.assertEqual(initial["execution_status"], "SUBMITTED")
            self.assertEqual(initial["execution_reason"], "ORDER_SUBMITTED")
            self.assertTrue(initial["order_id"])
            self.assertIn('"execution_status": "SUBMITTED"', "\n".join(sent))
            self.assertIn('"plan_status": "PLAN_READY"', "\n".join(sent))
            self.assertEqual(len(runner.store.load_paper("paper-main")[1]), 1)
        finally:
            runner.close()

        runner = DemoRunner(self.config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                            clock=lambda: T + timedelta(minutes=15), notification_sink=sink)
        try:
            with patched_scouts("LONG"):
                second = runner.run_once("XAUUSD", T + timedelta(minutes=15))
            review = runner.store.review_report(second["run_id"])
            outcome = review["execution"]
            position = runner.store.load_paper("paper-main")[0].open_positions["XAUUSD"]
            self.assertEqual(second["status"], "PLAN_READY")
            self.assertEqual(outcome["execution_status"], "SKIPPED")
            self.assertEqual(outcome["execution_reason"], "EXISTING_POSITION")
            self.assertEqual(outcome["blocking_position_id"], position.position_id)
            self.assertIsNone(outcome["blocking_order_id"])
            self.assertEqual(outcome["setup_id"], initial["setup_id"])
            self.assertEqual(len(runner.store.load_paper("paper-main")[1]), 1)
            self.assertIn('"execution_reason": "EXISTING_POSITION"', "\n".join(sent))
            self.assertEqual(json.loads(run_evidence_json(runner.store, second["run_id"]))
                             ["review"]["execution"], outcome)
            self.assertEqual(len(runner.store.journal(run_id=second["run_id"],
                                                       event="EXECUTION_DECISION")), 1)
        finally:
            runner.close()

    def test_pending_order_takes_precedence_without_creating_duplicate(self):
        runner = self.runner()
        try:
            with patched_scouts("LONG"):
                runner.run_once("XAUUSD", T)
        finally:
            runner.close()
        runner = DemoRunner(self.config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                            clock=lambda: T + timedelta(minutes=15))
        try:
            with patched_scouts("LONG"), patch("execution.paper_broker.PaperBroker.process_next_bar"):
                second = runner.run_once("XAUUSD", T + timedelta(minutes=15))
            outcome = runner.store.review_report(second["run_id"])["execution"]
            self.assertEqual(outcome["execution_reason"], "PENDING_ORDER")
            self.assertTrue(outcome["blocking_order_id"])
            self.assertEqual(len(runner.store.load_paper("paper-main")[1]), 1)
        finally:
            runner.close()

    def test_oversized_export_does_not_break_system_view(self):
        runner = self.runner()
        try:
            runner.run_once("XAUUSD", T)
            for index in range(110):
                payload = {"run_id": f"synthetic-{index}", "agents": [{"agent": f"test-{n}",
                           "reasoning_summary": "x" * 15000} for n in range(10)]}
                runner.store.db.execute("INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status) "
                                        "VALUES(?,?,?,?,?,?)", (f"synthetic-slot-{index}",
                                        f"synthetic-{index}", "XAUUSD", T.isoformat(),
                                        T.isoformat(), "COMPLETED"))
                runner.store.db.execute("INSERT INTO review_reports VALUES(?,?,?)",
                                        (f"synthetic-{index}", f"synthetic-slot-{index}",
                                         json.dumps(payload)))
            output = json.loads(review_bundle_json(runner.store))
            self.assertLess(len(output["reviews"]), 100)
            self.assertLessEqual(len(json.dumps(output).encode()), 1_000_000)
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
