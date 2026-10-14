"""Pre-demo fix: macro AI review NO_DATA -> clear reason, alert, one retry in the same cycle; still NO_DATA -> no trade."""
import json
import unittest
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from ai.provider import DeterministicAIProvider
from data.macro_news import InMemoryMacroNewsProvider
from runtime.alert_catalog import from_notification
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.notifications import FakeNotificationSink
from runtime.service import AI_REVIEW_UNHEALTHY, MACRO_NO_DATA_REASON
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts


class Flaky:
    """No macro data for the first ``empty`` provider reads, then the fixture's news item."""

    def __init__(self, empty):
        self.empty, self.calls, self.data = empty, 0, macro_fixture()

    def macro_events(self):
        return ()

    def news_items(self):
        self.calls += 1
        return () if self.calls <= self.empty else self.data.news_items()


class MacroRetryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path("data/runtime") / f"macro-retry-{uuid4().hex}.db"
        self.addCleanup(self.remove)

    def remove(self):
        for suffix in ("", "-wal", "-shm"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def runner(self, macro, sink=None):
        config = RuntimeConfig(db_path=self.path, enabled_symbols=("XAUUSD", "EURUSD"),
                               market_provider_mode="twelve_data", ai_provider_mode="openai")
        runner = DemoRunner(config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro, instruments={"XAUUSD": instrument()}, clock=lambda: T,
                            notification_sink=sink)
        self.addCleanup(runner.close)
        return runner

    def events(self, runner, event_type):
        return [json.loads(row["payload"]) for row in reversed(runner.store.journal(event=event_type))]

    def orders(self, runner):
        return runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0]

    def test_retry_recovers_in_the_same_cycle_and_trades(self):
        macro = Flaky(empty=1)
        runner = self.runner(macro)
        with patched_scouts("LONG"):
            result = runner.run_once("XAUUSD", T)
        self.assertEqual(macro.calls, 2)
        self.assertEqual(result["status"], "PLAN_READY")
        self.assertEqual(self.orders(runner), 1)
        unhealthy = self.events(runner, AI_REVIEW_UNHEALTHY)
        self.assertEqual([(e["reason"], e["attempt"], e["action"]) for e in unhealthy],
                         [(MACRO_NO_DATA_REASON, 1, "RETRY")])
        self.assertEqual(len(self.events(runner, "AI_REVIEW_RECOVERED")), 1)
        statuses = {a["agent"]: a["status"] for a in runner.store.latest_review()["agents"]}
        self.assertEqual(statuses["macro_ai"], "OK")

    def test_still_no_data_after_retry_means_no_trade_and_one_alert(self):
        sink = FakeNotificationSink()
        runner = self.runner(Flaky(empty=10 ** 6), sink)
        with patched_scouts("LONG"):
            runner.run_once("XAUUSD", T)
        self.assertEqual(self.orders(runner), 0)
        unhealthy = self.events(runner, AI_REVIEW_UNHEALTHY)
        self.assertEqual([(e["attempt"], e["action"], e["notify"]) for e in unhealthy],
                         [(1, "RETRY", True), (2, "NO_TRADE", False)])
        decision = self.events(runner, "EXECUTION_DECISION")[-1]
        self.assertEqual((decision["execution_status"], decision["execution_reason"]), ("SKIPPED", AI_REVIEW_UNHEALTHY))
        alerts = [e for e in sink.events if e.type == AI_REVIEW_UNHEALTHY]
        self.assertEqual(len(alerts), 1)
        alert = from_notification(alerts[0])
        self.assertEqual((alert.event_type, alert.summary), ("SYSTEM_DEGRADED", MACRO_NO_DATA_REASON))

    def test_alert_is_sent_on_the_transition_only(self):
        sink = FakeNotificationSink()
        runner = self.runner(Flaky(empty=10 ** 6), sink)
        with patched_scouts("LONG"):
            runner.run_once("XAUUSD", T)
            runner.runtime.clock = lambda: T + timedelta(minutes=15)
            runner.run_once("XAUUSD", T + timedelta(minutes=15))
        self.assertEqual(len(self.events(runner, AI_REVIEW_UNHEALTHY)), 4)
        self.assertEqual(sum(e.type == AI_REVIEW_UNHEALTHY for e in sink.events), 1)

    def test_watch_cycle_with_macro_no_data_does_not_retry_or_alert(self):
        macro = Flaky(empty=10 ** 6)
        runner = self.runner(macro)
        runner.run_once("XAUUSD", T)  # unpatched scouts on a one-bar snapshot: no plan
        self.assertEqual(macro.calls, 1)
        self.assertEqual(self.events(runner, AI_REVIEW_UNHEALTHY), [])

    def test_healthy_macro_never_retries(self):
        macro = Flaky(empty=0)
        runner = self.runner(macro)
        with patched_scouts("LONG"):
            runner.run_once("XAUUSD", T)
        self.assertEqual(macro.calls, 1)
        self.assertEqual(self.events(runner, AI_REVIEW_UNHEALTHY), [])
        self.assertIsInstance(macro.data, InMemoryMacroNewsProvider)


if __name__ == "__main__":
    unittest.main()
