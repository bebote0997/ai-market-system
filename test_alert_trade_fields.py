"""Pre-demo fix: ORDER SUBMITTED / POSITION OPENED / POSITION CLOSED alerts carry the durable order data."""
import unittest
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from ai.provider import DeterministicAIProvider
from runtime.alert_catalog import NO_DATA, from_notification
from runtime.alert_render import render_text
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.notifications import FakeNotificationSink, NotificationEvent
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts

OPTIONAL = {"Chart"}  # Chart: item 5 (closes)


class AlertTradeFieldTests(unittest.TestCase):
    def setUp(self):
        self.path = Path("data/runtime") / f"alert-fields-{uuid4().hex}.db"
        self.addCleanup(self.remove)

    def remove(self):
        for suffix in ("", "-wal", "-shm"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def run_two_cycles(self):
        sink = FakeNotificationSink()
        config = RuntimeConfig(db_path=self.path, enabled_symbols=("XAUUSD", "EURUSD"),
                               market_provider_mode="twelve_data", ai_provider_mode="openai")
        clock = [T]
        runner = DemoRunner(config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                            clock=lambda: clock[0], notification_sink=sink)
        self.addCleanup(runner.close)
        with patched_scouts("LONG"):
            runner.run_once("XAUUSD", T)
            clock[0] = T + timedelta(minutes=15)
            runner.runtime.clock = runner.clock = lambda: clock[0]
            runner.run_once("XAUUSD", clock[0])
        return runner, sink

    def alert(self, sink, event_type):
        alerts = [a for a in map(from_notification, sink.events) if a is not None and a.event_type == event_type]
        self.assertEqual(len(alerts), 1, event_type)
        return alerts[0], dict(alerts[0].trade_rows())

    def test_order_submitted_and_position_opened_show_real_values(self):
        runner, sink = self.run_two_cycles()
        order = next(iter(runner.store.load_paper("paper-main")[1].values()))
        submitted, rows = self.alert(sink, "ORDER_SUBMITTED")
        self.assertEqual((rows["Entry"], rows["SL"], rows["TP"], rows["Quantity"]),
                         tuple(str(v) for v in (order.planned_entry, order.stop, order.target, order.quantity)))
        risk = abs(order.planned_entry - order.stop)
        self.assertEqual(rows["R:R"], f"1:{abs(order.target - order.planned_entry) / risk:.2f}")
        self.assertEqual(rows["Risk %"], f"{risk * order.quantity / order.equity_at_submission * 100:.2f}%")
        self.assertEqual(rows["Signal (UTC)"], T.isoformat())
        self.assertEqual((rows["Executed (UTC)"], rows["Closed (UTC)"]), ("PENDING_FILL", "NOT_FILLED"))
        self.assertEqual(rows["Order ID"], order.order_id)

        opened, rows = self.alert(sink, "POSITION_OPENED")
        position = runner.store.load_paper("paper-main")[0].open_positions["XAUUSD"]
        self.assertEqual((rows["Entry"], rows["SL"], rows["TP"]),
                         tuple(str(v) for v in (position.entry_price, position.stop, position.target)))
        self.assertEqual((rows["Executed (UTC)"], rows["Closed (UTC)"]), (position.opened_at.isoformat(), "OPEN"))
        self.assertEqual(rows["Order ID"], order.order_id)  # never the position id
        for alert_rows in (dict(submitted.trade_rows()), rows):
            self.assertEqual([label for label, value in alert_rows.items() if value == NO_DATA and label not in OPTIONAL],
                             [])
        self.assertIn("Entry: " + str(position.entry_price), render_text(opened))
        # Original (V1) setup path: the order is unstamped; Setup ID comes from the submitting run's record.
        self.assertIsNone(order.setup_id)
        setup_id = runner.store.review_report(order.run_id)["execution"]["setup_id"]
        self.assertTrue(setup_id)
        self.assertEqual((dict(submitted.trade_rows())["Setup ID"], rows["Setup ID"]), (setup_id, setup_id))

    def test_event_without_trade_block_keeps_no_data(self):
        event = NotificationEvent("1.0", "n-1", "run-1", T.isoformat(), "INFO", "POSITION_OPENED", "XAUUSD", (), (),
                                  None, ("pos-1",), (), ())
        rows = dict(from_notification(event).trade_rows())
        self.assertEqual((rows["Entry"], rows["Order ID"]), (NO_DATA, NO_DATA))


def setUpModule():  # DEC-XAU-OFF-1: these tests drive XAUUSD cycles; the pause itself is in test_pause_xauusd
    import unittest.mock
    unittest.enterModuleContext(unittest.mock.patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))


if __name__ == "__main__":
    unittest.main()
