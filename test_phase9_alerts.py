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

from runtime import alert_channels, alert_chart, alert_dispatch
from runtime.alert_catalog import (CATALOG, NO_DATA, TRADE_FIELDS, alert_event, alert_event_id,
                                   from_notification, make_test_alert)
from runtime.alert_channels import AlertChannelError, GmailEmailChannel, SlackAlertChannel
from runtime.alert_dispatch import (AMBIGUOUS_RETRY, AlertDeliveryLedger, AlertDispatcher, AlertNotificationSink,
                                    alerts_enabled, dispatcher_from_env)
from runtime.alert_hook import AlertHook
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
        smtp = smtp_ssl.return_value
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
                    smtp_ssl.return_value.login.side_effect = error
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
                             {"rec": {"status": "DELIVERED", "attempts": 1, "error_type": None, "recovery": None}})
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
        for module in (alert_dispatch, alert_channels, *(ROOT / f"runtime/alert_{name}.py"
                                                         for name in ("catalog", "render", "chart", "hook", "test"))):
            path = module if isinstance(module, Path) else Path(module.__file__)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            names += [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module]
            for name in names:
                self.assertFalse(any(name == t or name.startswith(t + ".") for t in trading), (path.name, name))

    def test_not_wired_into_trading_paths(self):
        """The only wiring is runtime.alert_hook, called by the runner after the cycle (never by the service)."""
        for path in [*ROOT.glob("runtime/*.py"), *ROOT.glob("execution/*.py"), *ROOT.glob("floor/*.py"),
                     ROOT / "app.py", ROOT / "main.py"]:
            if path.name.startswith("alert_"):
                continue
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("alert_dispatch", text, path.name)
            self.assertNotIn("alert_channels", text, path.name)
            if path.name != "demo_runner.py":
                self.assertNotIn("alert_hook", text, path.name)


class TestSmtpCleanupAndClassification(NoNetwork):
    """Codex M1 (QUIT/close after a successful send never retries) and M2 (4xx temporary, 5xx permanent)."""

    def channel(self):
        return GmailEmailChannel(password="pw", environ={})

    def test_m1_quit_failure_after_send_is_delivered_once_without_retry(self):
        for cleanup_error in (smtplib.SMTPServerDisconnected("gone"), TimeoutError(), OSError("reset")):
            with self.subTest(error=type(cleanup_error).__name__), \
                    mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl:
                smtp = smtp_ssl.return_value
                smtp.quit.side_effect = cleanup_error
                smtp.close.side_effect = OSError("close")
                alerts = dispatcher(self.channel())
                with self.assertLogs("runtime.alert_channels", "WARNING") as logs:
                    self.assertEqual(alerts.dispatch(trade_event()), {"email": "DELIVERED"})
                self.assertEqual((smtp_ssl.call_count, smtp.send_message.call_count), (1, 1))
                smtp.close.assert_called_once()
                self.assertIn("SMTP_CLEANUP_FAILED", logs.output[0])
                self.assertEqual(alerts.ledger.status("evt-1")["email"]["attempts"], 1)

    def test_m1_send_error_wins_over_cleanup_error(self):
        with mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl, self.assertRaises(AlertChannelError) as ctx:
            smtp_ssl.return_value.send_message.side_effect = smtplib.SMTPDataError(554, b"spam")
            smtp_ssl.return_value.quit.side_effect = smtplib.SMTPServerDisconnected("gone")
            self.channel().send(trade_event())
        self.assertEqual(ctx.exception.code, "REJECTED")
        smtp_ssl.return_value.quit.assert_called_once()

    def test_m2_reply_codes_classified(self):
        cases = ((smtplib.SMTPDataError(451, b"try later"), "TEMPORARY_FAILURE", True),
                 (smtplib.SMTPDataError(554, b"rejected"), "REJECTED", False),
                 (smtplib.SMTPSenderRefused(421, b"busy", "a@x"), "TEMPORARY_FAILURE", True),
                 (smtplib.SMTPSenderRefused(553, b"no", "a@x"), "REJECTED", False),
                 (smtplib.SMTPRecipientsRefused({"b@x": (450, b"greylist")}), "TEMPORARY_FAILURE", True),
                 (smtplib.SMTPRecipientsRefused({"b@x": (550, b"unknown")}), "REJECTED", False),
                 (smtplib.SMTPRecipientsRefused({"b@x": (450, b""), "c@x": (550, b"")}), "REJECTED", False),
                 (smtplib.SMTPAuthenticationError(454, b"temp auth"), "TEMPORARY_FAILURE", True),
                 (smtplib.SMTPAuthenticationError(535, b"bad"), "AUTH_ERROR", False),
                 (smtplib.SMTPConnectError(421, b"too many"), "TEMPORARY_FAILURE", True),
                 (smtplib.SMTPResponseException(-1, b"?"), "CONNECTION_ERROR", True),
                 (smtplib.SMTPServerDisconnected("x"), "CONNECTION_ERROR", True))
        for error, code, retryable in cases:
            with self.subTest(error=type(error).__name__, code=code):
                self.assertEqual(alert_channels.classify_smtp_error(error), code)
                with mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl, \
                        self.assertRaises(AlertChannelError) as ctx:
                    smtp_ssl.return_value.send_message.side_effect = error
                    self.channel().send(trade_event())
                self.assertEqual((ctx.exception.code, ctx.exception.retryable), (code, retryable))
                self.assertIsNone(ctx.exception.__cause__)
        with mock.patch.object(smtplib, "SMTP_SSL", side_effect=smtplib.SMTPConnectError(421, b"x")), \
                self.assertRaises(AlertChannelError) as ctx:
            self.channel().send(trade_event())
        self.assertEqual(ctx.exception.code, "TEMPORARY_FAILURE")

    def test_m2_dispatcher_retries_4xx_and_stops_on_5xx(self):
        with mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl:
            smtp_ssl.return_value.send_message.side_effect = [smtplib.SMTPDataError(451, b"later"), {}]
            alerts = dispatcher(self.channel())
            self.assertEqual(alerts.dispatch(trade_event()), {"email": "DELIVERED"})
            self.assertEqual(alerts.ledger.status("evt-1")["email"]["attempts"], 2)
        with mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl:
            smtp_ssl.return_value.send_message.side_effect = smtplib.SMTPDataError(554, b"no")
            alerts = dispatcher(self.channel())
            self.assertEqual(alerts.dispatch(trade_event()), {"email": "FAILED"})
            self.assertEqual(smtp_ssl.return_value.send_message.call_count, 1)
            self.assertEqual(alerts.ledger.status("evt-1")["email"]["error_type"], "REJECTED")


class Clock:
    def __init__(self, seconds=0):
        self.seconds = seconds

    def __call__(self):
        return f"2026-10-10T14:{self.seconds // 60:02d}:{self.seconds % 60:02d}Z"


class TestStaleClaimRecovery(NoNetwork):
    """Codex M3: a SENDING claim left by a crashed process is reclaimed after ``stale_after`` (AMBIGUOUS_RETRY)."""

    def test_m3_stale_sending_claim_is_reclaimed_once_and_marked_ambiguous(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "alerts.sqlite")
            crashed = AlertDeliveryLedger(path)
            self.assertEqual(crashed.claim(trade_event(), "rec", Clock(0)()), "CLAIMED")  # then the process dies
            clock, channel = Clock(100), RecordingChannel()
            ledger = AlertDeliveryLedger(path)
            alerts = dispatcher(channel, ledger=ledger, clock=clock, stale_after=300)
            self.assertEqual(alerts.dispatch(trade_event()), {"rec": "DUPLICATE"})  # not stale yet
            self.assertEqual(channel.sent, [])
            clock.seconds = 300
            with self.assertLogs("runtime.alert_dispatch", "WARNING") as logs:
                self.assertEqual(alerts.dispatch(trade_event()), {"rec": "DELIVERED"})
            self.assertIn("event=AMBIGUOUS_RETRY", logs.output[0])
            self.assertEqual(channel.sent, ["evt-1"])
            self.assertEqual(ledger.status("evt-1"), {"rec": {"status": "DELIVERED", "attempts": 1,
                                                              "error_type": None, "recovery": AMBIGUOUS_RETRY}})
            clock.seconds = 3000
            self.assertEqual(alerts.dispatch(trade_event()), {"rec": "DUPLICATE"})  # completed rows never reclaim
            # The slow original sender finishing late cannot overwrite the recovering claim's outcome.
            crashed.complete("evt-1", "rec", Clock(3001)(), 3, "CONNECTION_ERROR", claimed_at=Clock(0)())
            self.assertEqual(ledger.status("evt-1")["rec"]["status"], "DELIVERED")
            self.assertEqual(channel.sent, ["evt-1"])
            crashed.close()
            ledger.close()

    def test_m3_only_one_process_takes_over_a_stale_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "alerts.sqlite")
            first, second = AlertDeliveryLedger(path), AlertDeliveryLedger(path)
            first.claim(trade_event(), "rec", Clock(0)())
            self.assertEqual(first.claim(trade_event(), "rec", Clock(400)(), 300), "RECLAIMED")
            self.assertIsNone(second.claim(trade_event(), "rec", Clock(400)(), 300))
            self.assertIsNone(second.claim(trade_event(), "rec", Clock(401)()))  # no stale_after: plain dedup
            first.close()
            second.close()

    def test_m3_stale_after_bounds(self):
        for bad in (0, 59.9, -1):
            with self.subTest(stale_after=bad), self.assertRaises(ValueError):
                dispatcher(RecordingChannel(), stale_after=bad)
        self.assertEqual(dispatcher(RecordingChannel()).stale_after, 300.0)


class TestChart(NoNetwork):
    """F09-T12: inline SVG sparkline from caller bar closes; PNG only if plotly+kaleido exist; NO_DATA otherwise."""

    def test_f09_t12_sparkline_from_closes(self):
        self.assertIsNone(alert_chart.sparkline_svg(()))
        self.assertIsNone(alert_chart.sparkline_svg(None))
        svg = alert_chart.sparkline_svg((1.0, 3.0, 2.0))
        self.assertTrue(svg.startswith("<svg") and svg.endswith("</svg>"))
        points = svg.split('points="')[1].split('"')[0].split()
        self.assertEqual(points, ["3.0,45.0", "120.0,3.0", "237.0,24.0"])  # min at bottom, max at top
        self.assertEqual(alert_chart.sparkline_svg((5.0, 5.0)).split('points="')[1].split('"')[0], "3.0,24.0 237.0,24.0")
        self.assertIn("points=\"3.0,24.0 237.0,24.0\"", alert_chart.sparkline_svg((5.0,)))

    def test_f09_t12_rendered_inline_or_no_data(self):
        event = alert_event("evt-c", "POSITION_OPENED", AT, trade={"symbol": "XAUUSD"}, closes=[2400, 2401.5, 2399])
        html = render_html(event)
        self.assertIn("<svg", html)
        self.assertNotIn("<img", html)
        self.assertEqual(dict(event.trade_rows())["Chart"], "SPARKLINE (3 closes)")
        self.assertEqual(event.closes, (2400.0, 2401.5, 2399.0))
        bare = render_html(trade_event())
        self.assertNotIn("<svg", bare)
        self.assertEqual(dict(trade_event().trade_rows())["Chart"], NO_DATA)
        caller_ref = alert_event("evt-d", "POSITION_OPENED", AT, trade={"chart_ref": "chart-7"}, closes=[1, 2])
        self.assertEqual(dict(caller_ref.trade_rows())["Chart"], "chart-7")
        self.assertNotIn("<svg", render_html(alert_event("evt-e", "RECOVERY", AT, closes=[1, 2])))  # non-trade
        for bad in ([float("nan")], [float("inf")], ["x"]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                alert_event("evt-f", "POSITION_OPENED", AT, closes=bad)

    def test_f09_t12_png_only_when_kaleido_installed(self):
        real_find_spec = alert_chart.importlib.util.find_spec
        with mock.patch.object(alert_chart.importlib.util, "find_spec",
                               side_effect=lambda name: None if name == "kaleido" else real_find_spec(name)):
            self.assertFalse(alert_chart.kaleido_available())
            self.assertIsNone(alert_chart.chart_png((1.0, 2.0)))
        self.assertIsNone(alert_chart.chart_png(()))
        event = alert_event("evt-g", "POSITION_OPENED", AT, closes=[1, 2])
        channel = GmailEmailChannel(password="pw", environ={})
        with mock.patch.object(alert_channels, "chart_png", return_value=b"\x89PNG fake") as png:
            message = channel.message(event)
        png.assert_called_once_with((1.0, 2.0))
        attachments = [part for part in message.iter_attachments()]
        self.assertEqual([(a.get_content_type(), a.get_filename()) for a in attachments], [("image/png", "chart.png")])
        with mock.patch.object(alert_channels, "chart_png", return_value=None):
            self.assertEqual(list(channel.message(event).iter_attachments()), [])


class TestAlertHook(NoNetwork):
    """Wiring: the runner hands already-persisted notification events to the hook after the cycle."""

    def note(self, kind="POSITION_OPENED", event_id="n-1"):
        return NotificationEvent("1.0", event_id, "run-1", AT, "INFO", kind, "XAUUSD", ("LONDON",), (), None,
                                 ("ord-1",), (), ())

    def test_disabled_makes_no_calls(self):
        for env in ({}, {"AI_FLOOR_ALERTS_ENABLED": "0"}, {"AI_FLOOR_ALERTS_ENABLED": "true"}):
            factory = mock.Mock()
            with self.subTest(env=env), mock.patch("runtime.alert_hook.from_notification") as mapping, \
                    mock.patch.object(smtplib, "SMTP_SSL") as smtp_ssl, \
                    mock.patch("runtime.notifications.urlopen") as urlopen:
                self.assertIsNone(AlertHook(environ=env, dispatcher_factory=factory)([self.note()]))
            factory.assert_not_called()
            mapping.assert_not_called()
            smtp_ssl.assert_not_called()
            urlopen.assert_not_called()

    def test_enabled_dispatches_mapped_events_once(self):
        channel = RecordingChannel()
        built = []

        def factory(**kwargs):
            built.append(kwargs)
            return dispatcher(channel)
        env = {"AI_FLOOR_ALERTS_ENABLED": "1", "AI_FLOOR_ALERTS_LEDGER_PATH": ":memory:"}
        hook = AlertHook(environ=env, dispatcher_factory=factory)
        result = hook([self.note(), self.note("DAILY_SUMMARY", "n-2"), self.note("STATE_INCONSISTENCY", "n-3")])
        self.assertEqual(result, {"n-3": {"rec": "DELIVERED"}, "n-1": {"rec": "DELIVERED"}})
        self.assertEqual(channel.sent, ["n-3", "n-1"])  # SYSTEM_CRITICAL first; DAILY_SUMMARY is not an alert
        self.assertEqual(hook([self.note()]), {"n-1": {"rec": "DUPLICATE"}})
        self.assertEqual(len(built), 1)
        self.assertEqual(built[0]["ledger_path"], ":memory:")
        self.assertEqual(hook([]), {})

    def test_hook_never_raises(self):
        env = {"AI_FLOOR_ALERTS_ENABLED": "1"}
        with self.assertLogs("runtime.alert_hook", "WARNING"):
            self.assertIsNone(AlertHook(environ=env, dispatcher_factory=mock.Mock(side_effect=OSError("x")))(
                [self.note()]))
        self.assertIsNone(AlertHook(environ=env)(None))
        with self.assertLogs("runtime.alert_hook", "WARNING"):
            self.assertEqual(AlertHook(environ=env, dispatcher_factory=lambda **_: dispatcher(RecordingChannel()))(
                [object()]), {})


class TestRunnerWiring(NoNetwork):
    """The DemoRunner calls the hook after the cycle; a failing/disabled hook never changes PAPER results."""

    def run_cycle(self, hook):
        from test_demo_runner import T, Data, DeterministicAIProvider, instrument, macro_fixture, patched_scouts
        from runtime.config import RuntimeConfig
        from runtime.demo_runner import DemoRunner
        with tempfile.TemporaryDirectory() as tmp:
            config = RuntimeConfig(db_path=Path(tmp) / "floor.db", enabled_symbols=("XAUUSD", "EURUSD"),
                                   market_provider_mode="twelve_data", ai_provider_mode="openai")
            runner = DemoRunner(config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                                macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                                clock=lambda: T, alert_hook=hook)
            try:
                with patched_scouts("LONG"):
                    result = runner.run_once("XAUUSD", T)
                account, orders, fills = runner.store.load_paper("paper-main")
                persisted = runner.store.notification_events()
                return ((result["status"], result["durable_status"], result["journal_count"], len(orders),
                         len(fills), account.cash, account.equity, sorted(account.open_positions)), persisted)
            finally:
                runner.close()

    def test_disabled_hook_makes_no_calls_and_failures_never_change_paper_state(self):
        factory = mock.Mock()
        baseline, persisted = self.run_cycle(AlertHook(environ={}, dispatcher_factory=factory))
        factory.assert_not_called()
        self.assertEqual(baseline[0], "PLAN_READY")
        self.assertTrue(persisted)

        calls = []

        def exploding(events):
            calls.append([event.event_id for event in events])
            raise RuntimeError("hook exploded")
        with self.assertLogs("ai_floor.observability", "WARNING") as logs:
            self.assertEqual(self.run_cycle(exploding)[0], baseline)
        self.assertTrue(any("HOOK_FAILED" in line for line in logs.output))
        self.assertTrue(calls[-1])  # called with the events persisted after the cycle

        class Hostile:
            name, configured = "hostile", True

            def send(self, alert):
                raise RuntimeError("smtp exploded")
        hostile = AlertHook(environ={"AI_FLOOR_ALERTS_ENABLED": "1"},
                            dispatcher_factory=lambda **_: dispatcher(Hostile()))
        state, _ = self.run_cycle(hostile)
        self.assertEqual(state, baseline)
        self.assertGreater(hostile.dispatcher.health()["hostile"]["consecutive_failures"], 0)


if __name__ == "__main__":
    unittest.main()
