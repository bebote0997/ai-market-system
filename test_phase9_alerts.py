"""V2 Phase 9: alert engine (F09-T01…T19). Mocks only: smtplib and urlopen are patched and every socket
connection attempt fails the test, so no real email or Slack message can be sent."""
import ast
import copy
import email
import io
from pathlib import Path
import smtplib
import socket
import tempfile
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from runtime import alert_channels, alert_dispatch
from runtime.alert_catalog import (CATALOG, NO_DATA, TRADE_FIELDS, alert_event, alert_event_id,
                                   from_notification, make_test_alert)
from runtime.alert_channels import AlertChannelError, GmailEmailChannel, SlackAlertChannel
from runtime.alert_dispatch import (AlertDeliveryLedger, AlertDispatcher, AlertNotificationSink,
                                    alerts_enabled, dispatcher_from_env)
from runtime.alert_render import render_html, render_text, subject
from runtime.notifications import NotificationEvent

ROOT = Path(__file__).resolve().parent
AT = "2026-10-10T14:30:00Z"
TRADE = {"symbol": "XAUUSD", "side": "LONG", "entry": "2400.10", "stop_loss": "2395.10",
         "take_profit": "2415.10", "quantity": "0.40", "rr": "3.0", "risk_pct": "1.0",
         "session": "LONDON+NEW_YORK", "run_id": "run-1", "setup_id": "setup-1", "order_id": "ord-1",
         "signal_at_utc": "2026-10-10T14:25:00Z", "executed_at_utc": "2026-10-10T14:30:00Z"}
TEN_EVENTS = ("VALID_EXECUTED_TRADE", "ORDER_SUBMITTED", "POSITION_OPENED", "POSITION_CLOSED", "RISK_REJECTED",
              "AI_CAUTION", "PROVIDER_FAILURE", "SYSTEM_DEGRADED", "SYSTEM_CRITICAL", "RECOVERY")


def trade_event(event_type="VALID_EXECUTED_TRADE", trade=TRADE, event_id="evt-1"):
    return alert_event(event_id, event_type, AT, run_id="run-1", symbol="XAUUSD",
                       session=("LONDON", "NEW_YORK"), summary="VALID_SETUP executed", trade=trade)


class RecordingChannel:
    def __init__(self, name="rec", failures=(), configured=True):
        self.name, self.configured = name, configured
        self.failures = list(failures)
        self.sent = []

    def send(self, event):
        self.sent.append(event.event_id)
        if self.failures:
            raise self.failures.pop(0)


def dispatcher(*channels, **kwargs):
    kwargs.setdefault("enabled", True)
    kwargs.setdefault("sleep", lambda _delay: None)
    return AlertDispatcher(channels, **kwargs)


class NoNetwork(unittest.TestCase):
    def setUp(self):
        guard = mock.patch.object(socket, "create_connection", side_effect=AssertionError("network attempted"))
        guard.start()
        self.addCleanup(guard.stop)


class TestCatalog(NoNetwork):
    def test_f09_t01_t10_ten_events_with_priority(self):  # F09-T01…T10, F09-T19
        self.assertEqual(tuple(CATALOG), TEN_EVENTS)
        self.assertEqual(CATALOG["SYSTEM_CRITICAL"].priority, "CRITICAL")
        self.assertEqual(CATALOG["RECOVERY"].priority, "LOW")
        for event_type in TEN_EVENTS:
            with self.subTest(event_type=event_type):
                event = trade_event(event_type)
                self.assertIn(event.priority, {"CRITICAL", "HIGH", "MEDIUM", "LOW"})
                self.assertIn(f"[{event.priority}]", subject(event))
                self.assertIn(CATALOG[event_type].label, render_text(event))
        with self.assertRaises(ValueError):
            alert_event("x", "SOMETHING_ELSE", AT)
        with self.assertRaises(ValueError):
            alert_event(" ", "RECOVERY", AT)

    def test_f09_t11_trade_content_complete(self):  # F09-T11
        text = render_text(trade_event())
        for label, expected in (("Symbol", "XAUUSD"), ("Side", "LONG"), ("Entry", "2400.10"), ("SL", "2395.10"),
                                ("TP", "2415.10"), ("Quantity", "0.40"), ("R:R", "3.0"), ("Risk %", "1.0"),
                                ("Session", "LONDON+NEW_YORK"), ("Run ID", "run-1"), ("Setup ID", "setup-1"),
                                ("Signal (UTC)", "2026-10-10T14:25:00Z"), ("Event (UTC)", AT),
                                ("Event ID", "evt-1")):
            self.assertIn(f"{label}: {expected}\n", text)

    def test_f09_t11_absent_values_are_no_data_never_invented(self):  # F09-T11
        event = alert_event("evt-2", "POSITION_OPENED", None, trade={"entry": 0, "quantity": ""})
        rows = dict(event.trade_rows())
        self.assertEqual(rows["Entry"], "0")  # a real zero is data, not absence
        for label in ("Symbol", "Side", "SL", "TP", "Quantity", "R:R", "Risk %", "Session", "Run ID",
                      "Setup ID", "Executed (UTC)", "Chart"):
            self.assertEqual(rows[label], NO_DATA, label)
        self.assertIn(f"Event (UTC): {NO_DATA}", render_text(event))
        self.assertEqual(len(event.trade_rows()), len(TRADE_FIELDS))
        with self.assertRaises(ValueError):
            alert_event("evt-3", "POSITION_OPENED", AT, trade={"pnl_guess": 1})

    def test_f09_t11_values_reported_verbatim_and_snapshotted(self):  # F09-T11
        trade = dict(TRADE, entry=2400.123456789)
        event = trade_event(trade=trade)
        trade["entry"] = 1.0
        self.assertEqual(dict(event.trade_rows())["Entry"], "2400.123456789")

    def test_non_trade_events_have_no_trade_block(self):
        event = alert_event("evt-4", "PROVIDER_FAILURE", AT, summary="twelve_data RATE_LIMITED")
        self.assertEqual(event.trade_rows(), ())
        self.assertNotIn("R:R", render_text(event))

    def test_event_id_deterministic(self):  # F09-T14
        self.assertEqual(alert_event_id("POSITION_OPENED", "pos-1"), alert_event_id("POSITION_OPENED", "pos-1"))
        self.assertNotEqual(alert_event_id("POSITION_OPENED", "pos-1"), alert_event_id("POSITION_CLOSED", "pos-1"))

    def test_maps_existing_durable_notification_contract(self):
        def note(kind, execution=None):
            return NotificationEvent("1.0", "n-1", "run-9", AT, "WARNING", kind, "EURUSD", ("LONDON",), (),
                                     {"status": "APPROVED", "side": "SHORT"}, ("ord-9",), (), (), None, execution)
        self.assertEqual(from_notification(note("POSITION_OPENED")).event_type, "POSITION_OPENED")
        self.assertEqual(from_notification(note("STATE_INCONSISTENCY")).event_type, "SYSTEM_CRITICAL")
        self.assertEqual(from_notification(note("RATE_LIMITED")).event_type, "PROVIDER_FAILURE")
        self.assertIsNone(from_notification(note("DAILY_SUMMARY")))
        self.assertIsNone(from_notification(note("EXECUTION_DECISION", {"execution_status": "SKIPPED"})))
        submitted = from_notification(note("EXECUTION_DECISION", {"execution_status": "SUBMITTED",
                                                                  "setup_id": "s-9", "order_id": "ord-9"}))
        rows = dict(submitted.trade_rows())
        self.assertEqual((submitted.event_type, rows["Setup ID"], rows["Side"], rows["Order ID"]),
                         ("ORDER_SUBMITTED", "s-9", "SHORT", "ord-9"))
        self.assertEqual(rows["Entry"], NO_DATA)


class TestRender(NoNetwork):
    def test_f09_t12_t13_responsive_html_escaped(self):  # F09-T12, F09-T13
        event = trade_event(trade=dict(TRADE, setup_id="<script>alert(1)</script>"))
        html = render_html(event)
        self.assertIn('name="viewport"', html)
        self.assertIn("@media only screen and (max-width:480px)", html)
        self.assertIn("max-width:600px", html)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<img", html)  # no remote images/tracking
        self.assertIn("PAPER", html)
        self.assertIn(f"Chart</td><td class=\"cell\" style=\"color:#f4efe1;\">{NO_DATA}", html)

    def test_f09_t18_test_alert_is_labelled(self):  # F09-T18
        event = make_test_alert(AT, nonce="n1")
        self.assertTrue(event.is_test)
        self.assertTrue(subject(event).startswith("[TEST] "))
        self.assertTrue(all(value == NO_DATA for label, value in event.trade_rows()))


class TestEmailChannel(NoNetwork):
    def channel(self, **kwargs):
        kwargs.setdefault("password", "abcd efgh ijkl mnop")
        kwargs.setdefault("environ", {})
        return GmailEmailChannel(**kwargs)

    def test_defaults_and_env_overrides(self):
        channel = GmailEmailChannel(environ={})
        self.assertEqual((channel.sender, channel.recipient), ("yefersontejeda@gmail.com",) * 2)
        self.assertFalse(channel.configured)
        env = {"GMAIL_APP_PASSWORD": "pw", "AI_FLOOR_ALERT_EMAIL_FROM": "a@example.invalid",
               "AI_FLOOR_ALERT_EMAIL_TO": "b@example.invalid"}
        channel = GmailEmailChannel(environ=env)
        self.assertEqual((channel.password, channel.sender, channel.recipient),
                         ("pw", "a@example.invalid", "b@example.invalid"))

    def test_sends_via_gmail_smtp_ssl_with_html_and_text(self):
        with mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl:
            self.channel().send(trade_event())
        args, kwargs = smtp_ssl.call_args
        self.assertEqual(args, ("smtp.gmail.com", 465))
        self.assertIn("context", kwargs)
        smtp = smtp_ssl.return_value.__enter__.return_value
        smtp.login.assert_called_once_with("yefersontejeda@gmail.com", "abcdefghijklmnop")
        message = smtp.send_message.call_args[0][0]
        self.assertEqual(message["To"], "yefersontejeda@gmail.com")
        self.assertEqual(message["X-AI-Floor-Event-ID"], "evt-1")
        parsed = email.message_from_bytes(message.as_bytes())
        types = [part.get_content_type() for part in parsed.walk()]
        self.assertIn("text/plain", types)
        self.assertIn("text/html", types)

    def test_not_configured_never_connects(self):
        with mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl, self.assertRaises(AlertChannelError) as ctx:
            self.channel(password="").send(trade_event())
        self.assertEqual(ctx.exception.code, "NOT_CONFIGURED")
        smtp_ssl.assert_not_called()

    def test_f09_t16_errors_typed_and_secret_free(self):  # F09-T16
        cases = ((smtplib.SMTPAuthenticationError(535, b"bad abcd efgh"), "AUTH_ERROR", False),
                 (smtplib.SMTPRecipientsRefused({}), "REJECTED", False),
                 (smtplib.SMTPServerDisconnected("x"), "CONNECTION_ERROR", True),
                 (TimeoutError(), "CONNECTION_ERROR", True), (OSError("dns"), "CONNECTION_ERROR", True))
        for error, code, retryable in cases:
            with self.subTest(code=code, error=type(error).__name__):
                with mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl, \
                        self.assertRaises(AlertChannelError) as ctx:
                    smtp_ssl.return_value.__enter__.return_value.login.side_effect = error
                    self.channel().send(trade_event())
                self.assertEqual((ctx.exception.code, ctx.exception.retryable), (code, retryable))
                self.assertNotIn("abcd", str(ctx.exception))
                self.assertIsNone(ctx.exception.__cause__)


class TestSlackChannel(NoNetwork):
    def test_reuses_slack_transport_with_text_payload(self):
        response = mock.MagicMock(status=200)
        with mock.patch("runtime.notifications.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = response
            SlackAlertChannel(webhook_url="https://hooks.slack.invalid/x", environ={}).send(trade_event())
        request = urlopen.call_args[0][0]
        self.assertEqual(request.full_url, "https://hooks.slack.invalid/x")
        self.assertIn(b"Risk %: 1.0", request.data)

    def test_errors_typed(self):
        cases = ((HTTPError("u", 429, "x", {}, io.BytesIO()), "RATE_LIMITED"),
                 (HTTPError("u", 500, "x", {}, io.BytesIO()), "PROVIDER_FAILURE"),
                 (URLError("down"), "CONNECTION_ERROR"))
        for error, code in cases:
            with self.subTest(code=code), mock.patch("runtime.notifications.urlopen", side_effect=error), \
                    self.assertRaises(AlertChannelError) as ctx:
                SlackAlertChannel(webhook_url="https://hooks.slack.invalid/secret", environ={}).send(trade_event())
            self.assertEqual(ctx.exception.code, code)
            self.assertNotIn("secret", str(ctx.exception))
        with mock.patch("runtime.notifications.urlopen") as urlopen, self.assertRaises(AlertChannelError) as ctx:
            SlackAlertChannel(environ={}).send(trade_event())
        self.assertEqual(ctx.exception.code, "NOT_CONFIGURED")
        urlopen.assert_not_called()


class TestDispatcher(NoNetwork):
    def test_off_by_default(self):
        self.assertFalse(alerts_enabled({}))
        for value in ("0", "", "true", "yes", " 1", "on", "2"):
            self.assertFalse(alerts_enabled({"AI_FLOOR_ALERTS_ENABLED": value}), value)
        self.assertTrue(alerts_enabled({"AI_FLOOR_ALERTS_ENABLED": "1"}))
        channel = RecordingChannel()
        self.assertEqual(AlertDispatcher((channel,), environ={}).dispatch(trade_event()), {"_": "DISABLED"})
        self.assertEqual(AlertDispatcher((channel,), enabled="1").dispatch(trade_event()), {"_": "DISABLED"})
        self.assertEqual(channel.sent, [])
        with mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl, \
                mock.patch("runtime.notifications.urlopen") as urlopen:
            env = {"GMAIL_APP_PASSWORD": "pw", "SLACK_WEBHOOK_URL": "https://hooks.slack.invalid/x"}
            self.assertEqual(dispatcher_from_env(environ=env).dispatch(trade_event()), {"_": "DISABLED"})
        smtp_ssl.assert_not_called()
        urlopen.assert_not_called()

    def test_env_example_and_render_keep_alerts_off(self):
        example = (ROOT / ".env.example").read_text(encoding="utf-8")
        self.assertIn("AI_FLOOR_ALERTS_ENABLED=0\n", example)
        self.assertIn("GMAIL_APP_PASSWORD=\n", example)
        render = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertNotIn("AI_FLOOR_ALERTS_ENABLED", render)

    def test_enabled_from_env_sends_both_channels_mocked(self):
        env = {"AI_FLOOR_ALERTS_ENABLED": "1", "GMAIL_APP_PASSWORD": "pw",
               "SLACK_WEBHOOK_URL": "https://hooks.slack.invalid/x"}
        with mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl, \
                mock.patch("runtime.notifications.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = mock.MagicMock(status=200)
            result = dispatcher_from_env(environ=env).dispatch(trade_event())
        self.assertEqual(result, {"email": "DELIVERED", "slack": "DELIVERED"})
        self.assertEqual(smtp_ssl.call_count, 1)
        self.assertEqual(urlopen.call_count, 1)

    def test_f09_t14_t15_dedup_and_durable_status(self):  # F09-T14, F09-T15
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "alerts.sqlite")
            channel = RecordingChannel()
            first = dispatcher(channel, ledger=AlertDeliveryLedger(path))
            self.assertEqual(first.dispatch(trade_event()), {"rec": "DELIVERED"})
            self.assertEqual(first.dispatch(trade_event()), {"rec": "DUPLICATE"})
            # A new process sharing the ledger file still deduplicates.
            ledger = AlertDeliveryLedger(path)
            self.assertEqual(dispatcher(channel, ledger=ledger).dispatch(trade_event()), {"rec": "DUPLICATE"})
            self.assertEqual(channel.sent, ["evt-1"])
            self.assertEqual(ledger.status("evt-1"),
                             {"rec": {"status": "DELIVERED", "attempts": 1, "error_type": None}})
            ledger.close()
            first.ledger.close()

    def test_not_configured_channel_is_not_claimed(self):
        channel = RecordingChannel(configured=False)
        alerts = dispatcher(channel)
        self.assertEqual(alerts.dispatch(trade_event()), {"rec": "NOT_CONFIGURED"})
        self.assertEqual(alerts.ledger.status("evt-1"), {})
        self.assertEqual(alerts.health()["rec"]["status"], "NOT_CONFIGURED")

    def test_f09_t17_bounded_backoff_retry(self):  # F09-T17
        delays = []
        channel = RecordingChannel(failures=[AlertChannelError("CONNECTION_ERROR"),
                                             AlertChannelError("RATE_LIMITED")])
        alerts = dispatcher(channel, sleep=delays.append, base_delay=1.0, max_delay=8.0, max_attempts=3)
        self.assertEqual(alerts.dispatch(trade_event()), {"rec": "DELIVERED"})
        self.assertEqual(delays, [1.0, 2.0])
        self.assertEqual(alerts.ledger.status("evt-1")["rec"]["attempts"], 3)
        self.assertEqual([alerts.backoff(n) for n in range(1, 7)], [1.0, 2.0, 4.0, 8.0, 8.0, 8.0])
        for bad in ({"max_attempts": 0}, {"max_attempts": 6}, {"max_delay": 31.0}, {"base_delay": -1.0},
                    {"base_delay": 9.0, "max_delay": 8.0}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                dispatcher(channel, **bad)

    def test_f09_t17_retry_exhaustion_and_non_retryable(self):  # F09-T16, F09-T17
        delays = []
        channel = RecordingChannel(failures=[ConnectionError("x")] * 5)
        alerts = dispatcher(channel, sleep=delays.append, max_attempts=3)
        self.assertEqual(alerts.dispatch(trade_event()), {"rec": "FAILED"})
        self.assertEqual((len(channel.sent), delays), (3, [1.0, 2.0]))
        self.assertEqual(alerts.ledger.failures(),
                         [{"event_id": "evt-1", "channel": "rec", "event_type": "VALID_EXECUTED_TRADE",
                           "priority": "HIGH", "attempts": 3, "error_type": "ConnectionError"}])
        auth = RecordingChannel(failures=[AlertChannelError("AUTH_ERROR")])
        alerts = dispatcher(auth)
        self.assertEqual(alerts.dispatch(trade_event()), {"rec": "FAILED"})
        self.assertEqual(len(auth.sent), 1)  # no retry for non-retryable failures

    def test_f09_t16_failure_detection(self):  # F09-T16
        channel = RecordingChannel(failures=[AlertChannelError("AUTH_ERROR")] * 3)
        alerts = dispatcher(channel)
        self.assertEqual(alerts.health()["rec"]["status"], "OK")
        with self.assertLogs("runtime.alert_dispatch", "WARNING") as logs:
            alerts.dispatch(trade_event(event_id="a"))
        self.assertIn("error_type=AUTH_ERROR", logs.output[0])
        self.assertEqual(alerts.health()["rec"], {"status": "DEGRADED", "consecutive_failures": 1,
                                                  "last_error_type": "AUTH_ERROR"})
        alerts.dispatch(trade_event(event_id="b"))
        alerts.dispatch(trade_event(event_id="c"))
        self.assertEqual(alerts.health()["rec"]["status"], "FAILING")
        alerts.dispatch(trade_event(event_id="d"))
        self.assertEqual(alerts.health()["rec"]["status"], "OK")
        self.assertEqual(AlertDispatcher((channel,), enabled=False).health()["rec"]["status"], "DISABLED")

    def test_f09_t19_priority_order(self):  # F09-T19
        channel = RecordingChannel()
        events = [alert_event("low", "RECOVERY", AT), alert_event("crit", "SYSTEM_CRITICAL", AT),
                  alert_event("med", "AI_CAUTION", AT), alert_event("high", "PROVIDER_FAILURE", AT)]
        dispatcher(channel).dispatch_many(events)
        self.assertEqual(channel.sent, ["crit", "high", "med", "low"])

    def test_dispatch_never_raises(self):
        class Exploding:
            name = "boom"

            @property
            def configured(self):
                raise RuntimeError("config")

        class BrokenLedger:
            def claim(self, *_):
                raise OSError("disk")

        self.assertEqual(dispatcher(Exploding()).dispatch(trade_event()), {"boom": "LEDGER_FAILED"})
        self.assertEqual(dispatcher(RecordingChannel(), ledger=BrokenLedger()).dispatch(trade_event()),
                         {"rec": "LEDGER_FAILED"})
        self.assertEqual(dispatcher(RecordingChannel()).dispatch(None), {"_": "INVALID_EVENT"})
        self.assertEqual(dispatcher(RecordingChannel()).dispatch_many([None]), {})
        sink = AlertNotificationSink(dispatcher(RecordingChannel(failures=[RuntimeError("x")] * 3)))
        self.assertIsNone(sink.deliver(object()))
        self.assertFalse(sink.durable_delivery)


class TestTradingIsolation(NoNetwork):
    """Invariant: an alert failure never creates or alters orders/positions."""

    def paper_state(self):
        from datetime import datetime, timezone
        from core.contracts import InstrumentSpec
        from execution.contracts import PaperAccount
        from execution.paper_broker import PaperBroker
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        account = PaperAccount("1.0", "paper", 10000.0, 10000.0, 10000.0)
        broker = PaperBroker(account, InstrumentSpec("TEST", "synthetic", "TEST", "UTC", 0.01, 0.01, 1.0, ("5m",)))
        decision = type("D", (), {"status": "APPROVED", "symbol": "TEST", "side": "LONG", "quantity": 1.0,
                                  "entry": 100.0, "stop": 90.0, "target": 130.0})()
        plan = type("P", (), {"run_id": "run-1", "side": "LONG", "as_of": t0})()
        report = type("R", (), {"final_status": "PLAN_READY", "run_id": "run-1", "symbol": "TEST",
                                "trade_plan": plan, "risk_decision": decision})()
        order = broker.submit_plan(report, {}, t0)
        position = broker.process_next_bar(order, {"symbol": "TEST", "timestamp": t0.replace(hour=1),
                                                   "open": 100, "high": 101, "low": 99, "close": 100,
                                                   "is_closed": True})
        self.assertIsNotNone(position)
        return account, broker, order, position

    def test_failing_channels_cannot_change_orders_or_positions(self):
        account, broker, order, position = self.paper_state()
        before = copy.deepcopy((account, broker.orders, broker.fills, broker.journal))
        event = alert_event(alert_event_id("POSITION_OPENED", position.position_id), "POSITION_OPENED", AT,
                            run_id=order.run_id, symbol=position.symbol,
                            trade={"side": position.side, "entry": position.entry_price,
                                   "stop_loss": position.stop, "take_profit": position.target,
                                   "quantity": position.quantity, "order_id": order.order_id})

        class Hostile:
            """A channel that fails every way it can, including trying to mutate what it is handed."""
            name, configured = "hostile", True

            def send(self, alert):
                object.__setattr__(alert, "trade", ())  # only its own frozen snapshot is reachable
                raise RuntimeError("smtp exploded")

        channels = (Hostile(), RecordingChannel("a", failures=[ConnectionError()] * 3),
                    RecordingChannel("b", failures=[AlertChannelError("AUTH_ERROR")]))
        with mock.patch.object(smtplib, "SMTP_SSL", side_effect=OSError("down")), \
                mock.patch("runtime.notifications.urlopen", side_effect=URLError("down")):
            email_slack = (GmailEmailChannel(password="pw", environ={}),
                           SlackAlertChannel(webhook_url="https://hooks.slack.invalid/x", environ={}))
            result = dispatcher(*channels, *email_slack).dispatch(event)
            AlertNotificationSink(dispatcher(*email_slack)).deliver(
                NotificationEvent("1.0", "n-1", "run-1", AT, "INFO", "POSITION_OPENED", "TEST", (), (), None,
                                  (order.order_id,), (), ()))
        self.assertEqual(set(result.values()), {"FAILED"})
        self.assertEqual(copy.deepcopy((account, broker.orders, broker.fills, broker.journal)), before)
        self.assertEqual(list(account.open_positions), ["TEST"])
        self.assertEqual(broker.orders[order.order_id].status, before[1][order.order_id].status)

    def test_alert_modules_import_no_trading_code(self):
        trading = ("execution", "riesgo", "floor", "storage", "core", "runtime.service", "runtime.demo_runner",
                   "runtime.cloud_runner", "runtime.scheduler")
        for module in (alert_dispatch, alert_channels, Path(ROOT / "runtime/alert_catalog.py"),
                       Path(ROOT / "runtime/alert_render.py")):
            path = module if isinstance(module, Path) else Path(module.__file__)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            names += [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
            for name in names:
                self.assertFalse(any(name == t or name.startswith(t + ".") for t in trading), (path.name, name))

    def test_not_wired_into_trading_paths(self):
        for path in [*ROOT.glob("runtime/*.py"), *ROOT.glob("execution/*.py"), *ROOT.glob("floor/*.py"),
                     ROOT / "app.py", ROOT / "main.py"]:
            if path.name.startswith("alert_"):
                continue
            self.assertNotIn("alert_dispatch", path.read_text(encoding="utf-8"), path.name)
            self.assertNotIn("alert_channels", path.read_text(encoding="utf-8"), path.name)


if __name__ == "__main__":
    unittest.main()
