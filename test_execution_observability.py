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
            self.assertEqual(len([message for message in sent
                                  if 'PAPER execution' in message]), 1)
            self.assertFalse(any('"type": "SETUP_VALID_SETUP"' in message or
                                 '"type": "ORDER_SUBMITTED"' in message for message in sent))
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

    def test_order_survives_setup_id_and_observability_storage_failures(self):
        for failure_target in ("runtime.service.audit_setup_id", "storage.database.Store.record_execution",
                               "storage.database.Store.record_analysis_events"):
            with self.subTest(failure_target=failure_target):
                runner = self.runner()
                try:
                    with patched_scouts("LONG"), patch(failure_target, side_effect=RuntimeError("private failure")):
                        result = runner.run_once("XAUUSD", T)
                    self.assertEqual(result["durable_status"], "COMPLETED")
                    self.assertEqual(result["status"], "PLAN_READY")
                    self.assertEqual(len(runner.store.load_paper("paper-main")[1]), 1)
                    self.assertEqual(len(runner.store.journal(run_id=result["run_id"],
                                                       event="EXECUTION_DECISION")),
                                     1 if failure_target.endswith("record_analysis_events") else 0)
                    self.assertEqual(runner.run_once("XAUUSD", T)["status"], "DUPLICATE")
                    self.assertEqual(len(runner.store.load_paper("paper-main")[1]), 1)
                finally:
                    runner.close()
                self.tearDown(); self.setUp()

    def test_order_survives_post_persist_journal_failure(self):
        runner = self.runner()
        try:
            original_event = runner.store.event
            def unreliable_event(*args, **kwargs):
                if args[4] == "PAPER_ORDER_SUBMITTED":
                    raise RuntimeError("private failure")
                return original_event(*args, **kwargs)
            with patched_scouts("LONG"), patch.object(runner.store, "event",
                                                      side_effect=unreliable_event):
                result = runner.run_once("XAUUSD", T)
            self.assertEqual(result["durable_status"], "COMPLETED")
            self.assertEqual(result["status"], "PLAN_READY")
            self.assertEqual(len(runner.store.load_paper("paper-main")[1]), 1)
            self.assertEqual(len(runner.store.journal(event="ORDER_SUBMITTED")), 1)
        finally:
            runner.close()

    def test_capture_and_slack_failures_do_not_change_paper_order(self):
        runner = self.runner()
        try:
            with patched_scouts("LONG"), patch.object(runner.store, "capture_notifications",
                                                      side_effect=RuntimeError("private failure")):
                result = runner.run_once("XAUUSD", T)
            self.assertEqual(result["durable_status"], "COMPLETED")
            self.assertEqual(len(runner.store.load_paper("paper-main")[1]), 1)
        finally:
            runner.close()
        self.tearDown(); self.setUp()
        sink = SlackNotificationSink(webhook_url="https://example.invalid/test",
                                     transport=lambda *_: (_ for _ in ()).throw(TimeoutError()))
        runner = self.runner(sink=sink)
        try:
            with patched_scouts("LONG"):
                result = runner.run_once("XAUUSD", T)
            self.assertEqual(result["durable_status"], "COMPLETED")
            self.assertEqual(len(runner.store.load_paper("paper-main")[1]), 1)
            self.assertEqual({r[0] for r in runner.store.db.execute(
                "SELECT status FROM notification_deliveries")}, {"FAILED"})
        finally:
            runner.close()

    def test_repeated_position_block_silent_but_changed_id_or_reason_alerts(self):
        runner = self.runner()
        try:
            store = runner.store
            def decision(number, *, position=None, order=None, reason="EXISTING_POSITION"):
                run_id = f"synthetic-{number}"
                store.db.execute("INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status) "
                                 "VALUES(?,?,?,?,?,?)", (run_id, run_id, "XAUUSD", T.isoformat(),
                                                          T.isoformat(), "COMPLETED"))
                store.db.execute("INSERT INTO review_reports VALUES(?,?,?)", (run_id, run_id,
                    json.dumps({"run_id": run_id, "setup_status": "VALID_SETUP",
                                "final_status": "PLAN_READY", "risk_decision": {"status": "APPROVED"},
                                "agents": []})))
                store.record_execution(T, run_id, "XAUUSD", status="SKIPPED", reason=reason,
                                       setup_id=f"setup-{number}", blocking_position_id=position,
                                       blocking_order_id=order)
                return run_id
            for i in range(4):
                run_id = decision(i, position="position-1")
                store.capture_notifications(run_id=run_id)
            self.assertEqual(len([e for e in store.notification_events()
                                  if e["type"] == "EXECUTION_DECISION"]), 1)
            self.assertEqual(len(store.journal(event="EXECUTION_DECISION")), 4)
            for number, position, order, reason in ((4, "position-2", None, "EXISTING_POSITION"),
                                                     (5, None, "order-1", "PENDING_ORDER")):
                store.capture_notifications(run_id=decision(number, position=position,
                                                             order=order, reason=reason))
            self.assertEqual(len([e for e in store.notification_events()
                                  if e["type"] == "EXECUTION_DECISION"]), 3)
        finally:
            runner.close()

    def test_important_events_and_normal_cycles_policy(self):
        runner = self.runner()
        try:
            store = runner.store
            for event in ("RISK_REJECTED", "AI_CAUTION", "POSITION_OPENED", "POSITION_CLOSED"):
                store.event(T, "alerts", "XAUUSD", "test", event)
            for status in ("WATCH", "NO_SETUP"):
                store.db.execute("INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status) "
                                 "VALUES(?,?,?,?,?,?)", (status, status, "XAUUSD", T.isoformat(),
                                                          T.isoformat(), "COMPLETED"))
                store.db.execute("INSERT INTO review_reports VALUES(?,?,?)", (status, status,
                    json.dumps({"run_id": status, "setup_status": status,
                                "final_status": status, "agents": []})))
                store.record_execution(T, status, "XAUUSD", status="SKIPPED", reason=status)
            captured = store.capture_notifications()
            self.assertEqual({e.type for e in captured},
                             {"RISK_REJECTED", "AI_CAUTION", "POSITION_OPENED", "POSITION_CLOSED"})
            self.assertEqual(len(store.journal(event="EXECUTION_DECISION")), 2)
        finally:
            runner.close()

    def test_run_export_has_hard_bound_for_oversized_record(self):
        runner = self.runner()
        try:
            runner.run_once("XAUUSD", T)
            run_id = runner.store.latest_run("XAUUSD")["run_id"]
            with patch.object(runner.store, "review_report", return_value={
                    "run_id": run_id, "paper": {"oversized": ["x" * 2000] * 2000}}), \
                 patch("runtime.review_export.MAX_EXPORT_BYTES", 1000):
                exported = run_evidence_json(runner.store, run_id)
            self.assertLessEqual(len(exported.encode()), 1000)
            self.assertEqual(json.loads(exported)["warning"], "run_evidence_exceeds_export_limit")
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
