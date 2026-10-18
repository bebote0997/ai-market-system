"""Best-effort Slack alerts for broker-demo router events (runtime/broker_alerts.py + BrokerDemoRouter.on_alert).
Offline: fake transports only; secrets below are obviously fake."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
from pathlib import Path
import tempfile
import unittest
from urllib.error import URLError

from execution import daily_loss_limit as dl
from execution.broker_demo_router import ALERT_KINDS, FRIDAY_ALERT, BrokerDemoRouter
from execution.broker_demo_state import DemoStateStore
from runtime import broker_alerts as ba
from runtime.service import attach_broker_alerts
from test_broker_demo_router import NOW, RouterBase, context

WEBHOOK = "https://hooks.slack.com/services/T000FAKE/B000FAKE/xxxxFAKExxxx"
FAKE_TOKEN = "tok-FAKE-practice-0123abcd"
FAKE_ACCOUNT = "101-004-9999999-001"
ENV = {"AI_FLOOR_ALERTS_ENABLED": "1", "SLACK_WEBHOOK_URL": WEBHOOK, "OANDA_PRACTICE_TOKEN": FAKE_TOKEN,
       "OANDA_ACCOUNT_ID": FAKE_ACCOUNT}


class FakeTransport:
    def __init__(self, error=None):
        self.calls, self.error = [], error

    def __call__(self, url, payload, timeout):
        self.calls.append((url, payload, timeout))
        if self.error is not None:
            raise self.error

    @property
    def texts(self):
        return [payload["text"] for _url, payload, _t in self.calls]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def sink(transport=None, clock=None, env=None):
    return ba.broker_alert_sink_from_env(ENV if env is None else env, transport=transport or FakeTransport(),
                                         clock=clock or Clock())


class FactoryTests(unittest.TestCase):
    def test_none_unless_alerts_flag_is_exactly_one_and_webhook_set(self):
        for value in (None, "0", "true", "", " 1", "yes"):
            env = {k: v for k, v in ENV.items() if k != "AI_FLOOR_ALERTS_ENABLED"}
            if value is not None:
                env["AI_FLOOR_ALERTS_ENABLED"] = value
            with self.subTest(value=value):
                self.assertIsNone(ba.broker_alert_sink_from_env(env, transport=FakeTransport()))
        self.assertIsNone(ba.broker_alert_sink_from_env({"AI_FLOOR_ALERTS_ENABLED": "1"}))
        self.assertIsNone(ba.broker_alert_sink_from_env({**ENV, "SLACK_WEBHOOK_URL": ""}))
        self.assertIsNone(ba.broker_alert_sink_from_env({**ENV, "SLACK_WEBHOOK_URL": "http://example.test/x"}))
        self.assertIsInstance(sink(), ba.BrokerAlertSink)

    def test_repr_hides_webhook(self):
        self.assertNotIn("hooks", repr(sink()))


class MessageTests(unittest.TestCase):
    def test_every_whitelisted_kind_has_a_spanish_line(self):
        for kind in ALERT_KINDS | {FRIDAY_ALERT}:
            with self.subTest(kind=kind):
                text = ba.build_message(kind, {})
                self.assertTrue(text.startswith("[OANDA demo] "))
        self.assertIsNone(ba.build_message("INTENT", {"units": 1}))
        self.assertIsNone(ba.build_message("ROUTER_READY", {}))

    def test_fields_and_masking(self):
        text = ba.build_message("ACCEPTED", {"symbol": "EURUSD", "side": "LONG", "units": 1000,
                                             "trade_id": "1234566397", "reason": "reconciled_after_timeout"})
        self.assertEqual(text, "[OANDA demo] Orden enviada · EURUSD LONG 1000 u · trade …6397 · "
                               "motivo: reconciled_after_timeout")
        self.assertNotIn("1234566397", text)
        halt = ba.build_message("DAILY_LOSS_HALT", {"pnl": "-20", "floor": "980.004", "nav": "980",
                                                    "day": "2026-10-13"})
        self.assertEqual(halt, "[OANDA demo] Límite de pérdida diaria alcanzado: entradas detenidas · P&L -20.00 · "
                               "suelo 980.00")
        drift = ba.build_message("DRIFT", {"trades": [("777777", "EUR_USD"), ("888888", "EUR_USD")]})
        self.assertEqual(drift, "[OANDA demo] Desajuste broker/estado local: entradas bloqueadas · operaciones: 2")
        refused = ba.build_message("DRIFT_CLEAR_REFUSED", {"reasons": ["stale_episode", "unmatched_broker_trade"]})
        self.assertIn("motivo: stale_episode,unmatched_broker_trade", refused)

    def test_no_secrets_ids_or_urls_ever(self):
        detail = {"reason": f"{WEBHOOK} {FAKE_TOKEN}", "trade_id": FAKE_ACCOUNT, "client_id": "aif-run-secret",
                  "account": FAKE_ACCOUNT, "token": FAKE_TOKEN, "url": WEBHOOK, "symbol": FAKE_ACCOUNT}
        texts = [ba.build_message(kind, detail) for kind in ALERT_KINDS]
        texts.append(ba.build_message(FRIDAY_ALERT, f"open {FAKE_ACCOUNT} see {WEBHOOK} 2 trade(s)"))
        for text in texts:
            for secret in (FAKE_TOKEN, FAKE_ACCOUNT, "9999999", "hooks.slack.com", "T000FAKE", "aif-run-secret",
                           "practice-0123abcd"):
                self.assertNotIn(secret, text)

    def test_friday_string_message_is_accepted(self):
        transport = FakeTransport()
        alert = sink(transport)
        alert(FRIDAY_ALERT, "Friday flatten incomplete: 2 routed trade(s) open (preflatten)")
        self.assertEqual(transport.texts, ["[OANDA demo] Cierre previo al viernes incompleto · "
                                           "Friday flatten incomplete: 2 routed trade(s) open (preflatten)"])
        self.assertEqual(transport.calls[0][0], WEBHOOK)
        self.assertLessEqual(transport.calls[0][2], 5)


class RateLimitTests(unittest.TestCase):
    def test_identical_kind_reason_once_per_minute(self):
        transport, clock = FakeTransport(), Clock()
        alert = sink(transport, clock)
        alert("REJECTED", {"reason": "MARKET_HALTED"})
        alert("REJECTED", {"reason": "MARKET_HALTED"})
        alert("REJECTED", {"reason": "INSUFFICIENT_MARGIN"})  # different reason: sent
        clock.t += 59
        alert("REJECTED", {"reason": "MARKET_HALTED"})
        clock.t += 1
        alert("REJECTED", {"reason": "MARKET_HALTED"})
        self.assertEqual(len(transport.calls), 3)
        self.assertEqual((alert.sent, alert.dropped), (3, 2))

    def test_hourly_cap(self):
        transport, clock = FakeTransport(), Clock()
        alert = sink(transport, clock)
        for i in range(25):
            alert("CLOSE_FAILED", {"reason": f"Err{i}"})
        self.assertEqual((len(transport.calls), alert.dropped), (20, 5))
        clock.t += 3600
        alert("CLOSE_FAILED", {"reason": "Later"})
        self.assertEqual(len(transport.calls), 21)

    def test_unknown_kind_is_ignored_without_counting(self):
        transport = FakeTransport()
        alert = sink(transport)
        alert("INTENT", {"units": 5})
        self.assertEqual((transport.calls, alert.dropped), ([], 0))


class FailureTests(unittest.TestCase):
    def test_transport_failures_are_swallowed_and_logged_by_type(self):
        for error in (URLError(WEBHOOK), RuntimeError(FAKE_TOKEN), TimeoutError()):
            alert = sink(FakeTransport(error))
            with self.subTest(error=type(error).__name__), self.assertLogs("ai_floor.broker.alerts",
                                                                           logging.WARNING) as logs:
                self.assertIsNone(alert("ACCEPTED", {"trade_id": "6397"}))
            self.assertEqual(alert.failed, 1)
            joined = "\n".join(logs.output)
            self.assertNotIn(FAKE_TOKEN, joined)
            self.assertNotIn("hooks.slack.com", joined)

    def test_garbage_detail_never_raises(self):
        alert = sink()
        for detail in (None, 42, ["x"], {"units": object(), "pnl": "nan?", "trades": "abc"}):
            alert("CLOSED", detail)


class FakeStore:
    def __init__(self, fail=False):
        self.events, self.fail = [], fail

    def flag(self, _key):
        return None

    def event(self, kind, client_id, detail):
        if self.fail:
            raise OSError("disk")
        self.events.append((kind, client_id, detail))


def bare_router(store=None, **kwargs):
    return BrokerDemoRouter(object(), store or FakeStore(), registry=None, dry_run=False, max_units=1000, **kwargs)


class RouterHookTests(unittest.TestCase):
    def test_only_whitelisted_kinds_reach_the_hook(self):
        calls = []
        router = bare_router(on_alert=lambda kind, detail: calls.append((kind, detail)))
        router._event("INTENT", "aif-run-1", units=1000)
        router._event("ACCEPTED", "aif-run-1", trade_id="6397", reason="filled")
        router._event("NOT_ROUTED", None, reason="not_eurusd")
        self.assertEqual(calls, [("ACCEPTED", {"trade_id": "6397", "reason": "filled"})])
        self.assertEqual([e[0] for e in router._store.events], ["INTENT", "ACCEPTED", "NOT_ROUTED"])

    def test_raising_hook_never_breaks_the_event(self):
        def boom(kind, detail):
            raise RuntimeError("sink down")

        store = FakeStore()
        router = bare_router(store)
        router.on_alert = boom
        with self.assertLogs("ai_floor.broker.demo_router", logging.WARNING):
            self.assertIsNone(router._event("REJECTED", "aif-run-1", reason="x"))
        self.assertEqual(store.events, [("REJECTED", "aif-run-1", {"reason": "x"})])

    def test_hook_still_called_when_the_event_write_fails(self):
        calls = []
        router = bare_router(FakeStore(fail=True), on_alert=lambda k, d: calls.append(k))
        router._event("DRIFT", None, trades=[])
        self.assertEqual(calls, ["DRIFT"])

    def test_without_hook_nothing_is_called(self):
        store = FakeStore()
        router = bare_router(store)
        self.assertIsNone(router.on_alert)
        router._event("ACCEPTED", "aif-run-1", trade_id="6397")
        self.assertEqual(store.events, [("ACCEPTED", "aif-run-1", {"trade_id": "6397"})])

    def test_loss_limit_halt_events_reach_the_hook(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(dl._HALT_LATCHES.clear)
        now = [datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)]
        store = DemoStateStore(Path(tmp.name) / "broker_state.db", clock=lambda: now[0].isoformat())
        store.initialize()
        limit = dl.DailyLossLimit(store, clock=lambda: now[0])
        calls = []
        bare_router(store, loss_limit=limit, on_alert=lambda k, d: calls.append((k, d)))
        limit.evaluate(Decimal("1000"))
        now[0] += timedelta(hours=1)
        limit.evaluate(Decimal("980"))
        self.assertEqual([k for k, _d in calls], ["DAILY_LOSS_HALT"])
        self.assertIn("[OANDA demo] Límite de pérdida diaria", ba.build_message(*calls[0]))


class RouterFlowTests(RouterBase):
    def test_accepted_order_sends_one_safe_slack_line(self):
        router = self.router()
        transport = FakeTransport()
        router.on_alert = ba.broker_alert_sink_from_env(ENV, transport=transport)
        self.ready()
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("ACCEPTED", "6397"))
        self.assertEqual(len(transport.texts), 1)
        self.assertTrue(transport.texts[0].startswith("[OANDA demo] Orden enviada · trade …6397"))
        for secret in (FAKE_TOKEN, FAKE_ACCOUNT, self.env["OANDA_PRACTICE_TOKEN"], self.env["OANDA_ACCOUNT_ID"],
                       "aif-run-1"):
            self.assertNotIn(secret, transport.texts[0])

    def test_router_from_env_has_no_hook(self):
        self.assertIsNone(self.router().on_alert)


class ServiceWiringTests(unittest.TestCase):
    def test_sets_on_alert_only_when_sink_exists(self):
        router = bare_router()
        attach_broker_alerts(router, {"SLACK_WEBHOOK_URL": WEBHOOK})
        self.assertIsNone(router.on_alert)
        attach_broker_alerts(router, {**ENV, "AI_FLOOR_ALERTS_ENABLED": "0"})
        self.assertIsNone(router.on_alert)
        attach_broker_alerts(router, ENV)
        self.assertIsInstance(router.on_alert, ba.BrokerAlertSink)

    def test_never_raises(self):
        class Frozen:
            __slots__ = ()

        with self.assertLogs("ai_floor.runtime", logging.WARNING):
            attach_broker_alerts(Frozen(), ENV)

    def test_service_calls_it_after_router_from_env(self):
        source = Path(__file__).resolve().parent.joinpath("runtime/service.py").read_text(encoding="utf-8")
        built = source.index("broker_router = router_from_env(os.environ)")
        self.assertLess(built, source.index("attach_broker_alerts(broker_router, os.environ)"))


if __name__ == "__main__":
    unittest.main()
