"""Engine observability: RUN_COMPLETED setup fields, OANDA LIMIT lifecycle log lines, provider failure detail.

Log/alert content only: no decision, gate, threshold or order behaviour is exercised differently here.
"""
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

from ai.alerts import AIProviderAlerts
from ai.contracts import AI_SCHEMA_VERSION, AIRequest
from ai.openai_provider import OpenAIProviderError
from ai.outcomes import (provider_error_detail, provider_failure_detail, provider_failure_fields,
                         provider_failure_reason, sanitize_provider_detail)
from ai.provider import DeterministicAIProvider
from ai.provider_health import ProviderHealthTracker
from ai.runtime import AuditLog, call_agent
from core.contracts import SetupAssessment
from execution import broker_demo_router as bdr
from execution.broker_demo_state import CANCELLED, PENDING_LIMIT
from runtime.alert_catalog import from_notification
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.notification_extras import with_provider_failure
from runtime.notifications import NotificationEvent, compact_payload, provider_failure_execution
from runtime.observability import setup_log_fields
from runtime.system_health import HealthPolicy, SystemHealth
from storage.database import Store
from storage.health_store import HealthStore
from test_broker_demo_router import A, CID, NOW, OPEN_TRADES, PRICING, RouterBase, context, open_trade, trades
from test_broker_oanda import pricing
from test_broker_oanda_client import ACCOUNT, TOKEN
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from test_eurusd_pullback import PENDING, ramp
from test_phase7_ai_call_audit import SECRET, echo_transport
from test_phase7_ai_resilience import openai

RUNTIME_LOG = "ai_floor.runtime"
ROUTER_LOG = "ai_floor.broker.demo_router"
# A provider error body that echoes a key-like string: only the code may ever reach a log, journal or alert.
LEAKY_429 = json.dumps({"error": {"message": f"Rate limit for key {SECRET} (sk-LEAK-0000)", "type": "requests",
                                  "code": "rate_limit_exceeded"}}).encode()


def assessment(status="WATCH", side=None, warnings=()):
    return SetupAssessment("1.0", "run-1", T, "XAUUSD", status, side, ("1h", "15m", "5m"), warnings=warnings)


def make_request():
    return AIRequest(AI_SCHEMA_VERSION, "run-1", T, "XAUUSD", "structure_ai", "structure_specialist", {},
                     ({"evidence_id": "structure_1h", "bias": "BULLISH"},), ("interpret",), {}, "1.0")


def rate_limited(_n):
    raise HTTPError("https://api.example/private", 429, "Too Many", {}, io.BytesIO(LEAKY_429))


# --- 1) RUN_COMPLETED carries setup status, side and the setup validator's warnings ----------------------------------
class SetupLogFieldTests(unittest.TestCase):
    def test_fields_are_tokens_and_dash_when_absent(self):
        self.assertEqual(setup_log_fields(assessment("VALID_SETUP", "LONG")), ("VALID_SETUP", "LONG", "-"))
        self.assertEqual(setup_log_fields(assessment("WATCH", None, ("timeframe_incompatibility",))),
                         ("WATCH", "-", "timeframe_incompatibility"))
        self.assertEqual(setup_log_fields(assessment("WATCH", "SHORT", ("structure_confirmation_missing",
                                                                         "high_impact_active_window"))),
                         ("WATCH", "SHORT", "structure_confirmation_missing,high_impact_active_window"))
        self.assertEqual(setup_log_fields(assessment("WATCH", None, ("a b",))), ("WATCH", "-", "a_b"))
        self.assertEqual(setup_log_fields(None), ("-", "-", "-"))


class RunCompletedLogTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))  # DEC-XAU-OFF-1 (test only)
        self.config = RuntimeConfig(db_path=Path(tmp.name) / "trading.db")

    def run_completed(self, *patches):
        runner = DemoRunner(self.config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()}, clock=lambda: T)
        self.addCleanup(runner.close)
        with patched_scouts("LONG"), self.assertLogs(RUNTIME_LOG, "INFO") as logs:
            for extra in patches:
                self.enterContext(extra)
            result = runner.run_once("XAUUSD", T)
        [line] = [r.getMessage() for r in logs.records if "event=RUN_COMPLETED" in r.getMessage()]
        return result, line

    def test_valid_setup_line_has_status_side_and_no_warnings(self):
        result, line = self.run_completed()
        self.assertRegex(line, r"^run_id=\S+ symbol=XAUUSD scheduled_slot=\S+ component=runtime event=RUN_COMPLETED "
                               r"status=\S+ setup_status=VALID_SETUP side=LONG setup_warnings=-$")
        self.assertIn(f"status={result['status']} ", line)

    def test_watch_line_lists_the_validator_warning(self):
        from test_demo_runner import scout
        bullish, bearish = scout("LONG"), scout("SHORT")
        bullish.kind = bearish.kind = "structure"

        def mixed(frame, symbol, tf, run_id, at):
            return (bearish if tf == "5m" else bullish)(frame, symbol, tf, run_id, at)

        result, line = self.run_completed(patch("floor.orchestrator.analizar_estructura", mixed))
        self.assertEqual(result["status"], "WATCH")
        self.assertTrue(line.endswith(" status=WATCH setup_status=WATCH side=- "
                                      "setup_warnings=timeframe_incompatibility"))


# --- 2) OANDA demo LIMIT lifecycle: SENT / FILLED / EXPIRED / CANCELLED -------------------------------------------
class LimitLifecycleLogTests(RouterBase):
    def setUp(self):
        super().setUp()
        self.router_ = self.router(AI_FLOOR_EURUSD_LIMIT_PULLBACK="1")
        self.ready(order=(201, {"orderCreateTransaction": {"id": "123200", "type": "LIMIT_ORDER", "price": "1.08875"}}))
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08880", ask="1.08890")]

    def lines(self, logs, event):
        return [r.getMessage() for r in logs.records if f"event={event} " in r.getMessage()]

    def assert_safe(self, logs):
        for record in logs.records:
            message = record.getMessage()
            for secret in (ACCOUNT, TOKEN, CID, "123200"):
                self.assertNotIn(secret, message)

    def send(self):
        with self.assertLogs(ROUTER_LOG, "INFO") as logs:
            outcome = self.router_.on_cycle(context(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome, (PENDING_LIMIT, "123200"))  # unchanged routing outcome
        return logs

    def test_sent_line_has_masked_order_id_price_and_utc_expiry(self):
        logs = self.send()
        expires = bdr._parse_time(self.rows()[0]["expires_at"]).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.assertEqual(self.lines(logs, "LIMIT_SENT"), [
            f"component=broker_demo event=LIMIT_SENT order_id=***200 symbol=EURUSD side=LONG price=1.08875 "
            f"expires_at={expires}"])
        self.assertEqual(self.lines(logs, "LIMIT_PENDING"), [
            f"component=broker_demo event=LIMIT_PENDING trade_id=- reason=- order_id=***200 symbol=EURUSD side=LONG "
            f"price=1.08875 expires_at={expires}"])
        self.assertTrue(expires.endswith("Z"))
        self.assert_safe(logs)

    def test_filled_line_on_reconcile(self):
        self.send()
        filled = open_trade(trade_id="776397", ext=CID, price="1.08875", sl="1.08625")
        filled["takeProfitOrder"] = {"id": "2", "price": "1.12000"}
        self.server.routes[("GET", OPEN_TRADES)] = [trades(filled)]
        self.server.routes[("GET", PENDING)] = [(200, {"orders": []})]
        with self.assertLogs(ROUTER_LOG, "INFO") as logs:
            self.router_.reconcile()
        [line] = self.lines(logs, "LIMIT_FILLED")
        self.assertRegex(line, r"^component=broker_demo event=LIMIT_FILLED order_id=\*\*\*200 symbol=EURUSD side=LONG "
                               r"price=1\.08875 expires_at=\S+Z trade_id=\*\*\*397 fill_price=1\.08875$")
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.assert_safe(logs)
        self.assertNotIn("776397", line)

    def test_expired_line_at_gtd(self):
        self.send()
        self.server.add("GET", PENDING, (200, {"orders": [{"id": "123200", "instrument": "EUR_USD",
                                                           "clientExtensions": {"id": CID}}]}))
        self.server.add("PUT", f"{A}/orders/123200/cancel",
                        (200, {"orderCancelTransaction": {"id": "201", "reason": "CLIENT_REQUEST"}}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.now = NOW + timedelta(hours=24)
        with self.assertLogs(ROUTER_LOG, "INFO") as logs:
            self.assertEqual(self.router_.manage(self.now)[CID], "LIMIT_EXPIRED")
        [line] = self.lines(logs, "LIMIT_EXPIRED")
        self.assertRegex(line, r"^component=broker_demo event=LIMIT_EXPIRED trade_id=- reason=LIMIT_EXPIRED "
                               r"order_id=\*\*\*200 symbol=EURUSD side=LONG price=1\.08875 expires_at=\S+Z$")
        self.assert_safe(logs)

    def test_expired_line_when_the_broker_dropped_it(self):
        self.send()
        self.server.add("GET", PENDING, (200, {"orders": []}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.server.add("GET", f"{A}/trades/@{CID}", (404, {"errorCode": "NO_SUCH_TRADE"}))
        with self.assertLogs(ROUTER_LOG, "INFO") as logs:
            self.router_.reconcile()
        [line] = self.lines(logs, "LIMIT_EXPIRED")
        self.assertIn(" reason=LIMIT_EXPIRED order_id=***200 symbol=EURUSD side=LONG price=1.08875 expires_at=", line)
        self.assertEqual(self.rows()[0]["status"], CANCELLED)
        self.assert_safe(logs)

    def test_cancelled_line_carries_the_reason(self):
        self.send()
        self.server.add("PUT", f"{A}/orders/123200/cancel",
                        (200, {"orderCancelTransaction": {"id": "201", "reason": "CLIENT_REQUEST"}}))
        with self.assertLogs(ROUTER_LOG, "INFO") as logs:
            self.router_.emergency_close_all(reason="drill")
        [line] = self.lines(logs, "LIMIT_CANCELLED")
        self.assertRegex(line, r"^component=broker_demo event=LIMIT_CANCELLED trade_id=- reason=EMERGENCY\S* "
                               r"order_id=\*\*\*200 symbol=EURUSD side=LONG price=1\.08875 expires_at=\S+Z$")
        self.assert_safe(logs)

    def test_event_store_payload_and_alerts_are_unchanged(self):
        alerts = []
        self.router_.on_alert = lambda kind, detail=None: alerts.append((kind, sorted(detail)))
        self.send()
        self.assertEqual(alerts, [("LIMIT_PENDING", ["expires_at", "limit_price", "order_id", "side", "stop", "symbol",
                                                     "target", "units"])])

    def test_a_failing_log_line_never_changes_the_order_flow(self):
        with patch.object(bdr, "_limit_fields", side_effect=RuntimeError("boom")), \
                self.assertLogs(ROUTER_LOG, "WARNING") as logs:
            bdr._log_limit("LIMIT_SENT", order_id="123200")
        self.assertEqual(logs.output, ["WARNING:ai_floor.broker.demo_router:component=broker_demo "
                                       "event=LIMIT_LOG_FAILED kind=LIMIT_SENT error_type=RuntimeError"])

    def test_a_failing_limit_formatter_never_skips_the_alert(self):
        alerts = []
        self.router_.on_alert = lambda kind, detail=None: alerts.append(kind)
        with patch.object(bdr, "_limit_fields", side_effect=RuntimeError("boom")), \
                self.assertLogs(ROUTER_LOG, "INFO") as logs:
            outcome = self.router_.on_cycle(context(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome, (PENDING_LIMIT, "123200"))
        self.assertEqual(alerts, ["LIMIT_PENDING"])
        self.assertEqual(self.rows()[0]["status"], PENDING_LIMIT)
        self.assertIn("WARNING:ai_floor.broker.demo_router:component=broker_demo event=ACTION_LOG_FAILED "
                      "kind=LIMIT_PENDING error_type=RuntimeError", logs.output)

    def test_mask_keeps_last_three_and_is_a_token(self):
        self.assertEqual(bdr._mask_id(ACCOUNT), "***" + ACCOUNT[-3:])
        self.assertEqual(bdr._mask_id(None), "-")
        self.assertEqual(bdr._log_utc("not-a-time"), "-")


# --- 4) PROVIDER_FAILURE: provider name, HTTP status and provider error code -------------------------------------
class ProviderDetailTests(unittest.TestCase):
    def test_detail_from_a_sanitized_provider_error(self):
        error = OpenAIProviderError("RATE_LIMITED", http_status=429, error_code="rate_limit_exceeded")
        detail = provider_error_detail(error, "openai")
        self.assertEqual(detail, {"provider": "openai", "http_status": 429, "error_code": "rate_limit_exceeded"})
        expected = "provider=openai http_status=429 error_code=rate_limit_exceeded"
        self.assertEqual((provider_failure_fields(detail), provider_failure_reason(detail)), (expected, expected))
        quota = OpenAIProviderError("QUOTA_EXHAUSTED", http_status=429, error_type="insufficient_quota")
        self.assertEqual(provider_error_detail(quota, "openai")["error_code"], "insufficient_quota")

    def test_absent_and_unsafe_values_are_dropped(self):
        self.assertEqual(provider_failure_fields({}), "provider=- http_status=- error_code=-")
        self.assertIsNone(provider_failure_reason({}))
        self.assertEqual(provider_failure_reason({"provider": "fxmacrodata"}), "provider=fxmacrodata")
        unsafe = sanitize_provider_detail({"provider": "openai", "http_status": True,
                                           "error_code": f"Bearer {SECRET}", "body": "raw"})
        self.assertEqual(unsafe, {"provider": "openai", "http_status": None, "error_code": "redacted"})

    def test_error_code_allowlist_redacts_keys_ids_and_tokens(self):
        unsafe = ("sk-LEAK-0000", "sk_live_abc", "sk-proj-AbC123xyz", ACCOUNT, "101-000-1234567-001",
                  f"Bearer {SECRET}", "Bearer abc.def.ghi", SECRET, "Rate_Limit", "code42", "a" * 65)
        for value in unsafe:
            with self.subTest(value=value):
                error = OpenAIProviderError("PROVIDER_ERROR", http_status=400, error_code=value)
                detail = provider_error_detail(error, "openai")
                self.assertEqual(detail["error_code"], "redacted")
                for text in (provider_failure_fields(detail), provider_failure_reason(detail),
                             provider_failure_fields({"error_code": value}),
                             provider_failure_reason({"provider": "openai", "error_code": value})):
                    self.assertIn("error_code=redacted", text)
                    self.assertNotIn(value, text)
        for value in ("insufficient_quota", "rate_limit_exceeded", "billing_hard_limit_reached", "model_not_found"):
            self.assertEqual(sanitize_provider_detail({"error_code": value})["error_code"], value)
        self.assertIsNone(sanitize_provider_detail({"error_code": None})["error_code"])


class CallAuditTests(unittest.TestCase):
    def test_failed_call_records_provider_status_and_code_but_no_body(self):
        audit = AuditLog()
        call_agent(openai(echo_transport(fail=rate_limited)), make_request(), audit)
        entry = audit.entries[-1]
        self.assertEqual((entry["provider"], entry["http_status"], entry["error_code"], entry["outcome"]),
                         ("openai", 429, "rate_limit_exceeded", "RATE_LIMITED"))
        self.assertNotIn(SECRET, json.dumps({k: v for k, v in entry.items() if k != "response"}, default=str))
        self.assertEqual(provider_failure_detail(audit.entries)["agents"], [entry["agent"]])

    def test_successful_call_names_the_provider_without_error_fields(self):
        audit = AuditLog()
        call_agent(DeterministicAIProvider(), make_request(), audit)
        self.assertEqual((audit.entries[-1]["http_status"], audit.entries[-1]["error_code"]), (None, None))
        self.assertEqual(provider_failure_detail(audit.entries), {})


class CycleProviderFailureTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.enterContext(patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))
        self.enterContext(patch.dict("os.environ", {"AI_FLOOR_ALERTS_ENABLED": "0"}))
        self.config = RuntimeConfig(db_path=Path(tmp.name) / "trading.db", enabled_symbols=("XAUUSD",))

    def test_journal_log_and_notification_carry_provider_status_and_code(self):
        runner = DemoRunner(self.config, market_provider=Data(), macro_provider=macro_fixture(),
                            ai_provider=openai(echo_transport(fail=rate_limited)),
                            instruments={"XAUUSD": instrument()}, clock=lambda: T)
        self.addCleanup(runner.close)
        with patched_scouts("LONG"), self.assertLogs(RUNTIME_LOG, "INFO") as logs:
            result = runner.run_once("XAUUSD", T)
        [row] = runner.store.journal(run_id=result["run_id"], source="ai_provider", event="PROVIDER_FAILURE")
        payload = json.loads(row["payload"])
        self.assertEqual((payload["provider"], payload["http_status"], payload["error_code"], payload["outcome"]),
                         ("openai", 429, "rate_limit_exceeded", "RATE_LIMITED"))
        [line] = [r.getMessage() for r in logs.records
                  if "event=PROVIDER_FAILURE source=ai_provider" in r.getMessage()]
        self.assertIn(" provider=openai http_status=429 error_code=rate_limit_exceeded outcome=RATE_LIMITED agents=",
                      line)
        # run_once already captured the notification; enrich the persisted copy exactly as delivery does.
        persisted = [NotificationEvent(**e) for e in runner.store.notification_events()
                     if e["type"] == "PROVIDER_FAILURE" and e["run_id"] == result["run_id"]]
        [event] = with_provider_failure(runner.store, persisted)
        self.assertEqual(event.execution["reason"], "provider=openai http_status=429 error_code=rate_limit_exceeded")
        self.assertIn("rate_limit_exceeded", json.dumps(compact_payload(event)))
        self.assertEqual(from_notification(event).summary, event.execution["reason"])
        everything = json.dumps([dict(r) for r in runner.store.journal(limit=1000)]) + "\n".join(
            r.getMessage() for r in logs.records)
        self.assertNotIn(SECRET, everything)
        self.assertNotIn("sk-LEAK", everything)


class NotificationProjectionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "trading.db")
        self.addCleanup(self.store.close)

    def capture(self, payload):
        self.store.event(T, "run-1", "EURUSD", "ai_provider", "PROVIDER_FAILURE", "ERROR", payload)
        return with_provider_failure(self.store, self.store.capture_notifications(run_id="run-1"))

    def test_quota_failure_reason(self):
        [event] = self.capture({"provider": "openai", "http_status": 429, "error_code": "insufficient_quota",
                                "agents": ["structure_ai"]})
        self.assertEqual(event.execution, {"reason": "provider=openai http_status=429 error_code=insufficient_quota",
                                           "provider": "openai", "http_status": 429,
                                           "error_code": "insufficient_quota"})

    def test_read_failure_keeps_the_plain_alert(self):
        self.store.event(T, "run-1", "EURUSD", "ai_provider", "PROVIDER_FAILURE", "ERROR", {"provider": "openai"})
        [event] = self.store.capture_notifications(run_id="run-1")
        broken = SimpleNamespace(db=SimpleNamespace(execute=lambda *_a: (_ for _ in ()).throw(RuntimeError("db"))))
        self.assertEqual(with_provider_failure(broken, [event]), [event])

    def test_legacy_empty_payload_is_left_unchanged(self):
        [event] = self.capture(None)
        self.assertIsNone(event.execution)
        self.assertIsNone(provider_failure_execution({"message": SECRET}))


class AIProviderAlertTests(unittest.TestCase):
    def test_failed_transition_alert_and_log_carry_provider_detail(self):
        tracker = ProviderHealthTracker(failed_after=1)
        tracker.observe("QUOTA_EXHAUSTED", at=T, run_id="r1",
                        detail={"provider": "openai", "http_status": 429, "error_code": "insufficient_quota"})
        tracker.observe("OK", at=T + timedelta(minutes=15), run_id="r2")
        events = tracker.drain()
        self.assertEqual([(e["event"], e["to"]) for e in events],
                         [("PROVIDER_FAILED", "FAILED"), ("PROVIDER_RECOVERED", "READY")])  # transitions unchanged
        sink = SimpleNamespace(events=[], deliver=lambda e: sink.events.append(e))
        with self.assertLogs("ai.alerts", "WARNING") as logs:
            self.assertEqual(AIProviderAlerts(sink, enabled=True).process(events, symbol="EURUSD"), 2)
        failed, recovered = sink.events
        self.assertEqual(failed.execution["reason"], "provider=openai http_status=429 error_code=insufficient_quota")
        self.assertEqual(failed.agent_summaries[0]["error_code"], "insufficient_quota")
        self.assertIsNone(recovered.execution)
        self.assertNotIn("error_code", recovered.agent_summaries[0])
        self.assertEqual(logs.output, ["WARNING:ai.alerts:component=ai_alerts event=AI_PROVIDER_FAILED "
                                       "provider=openai http_status=429 error_code=insufficient_quota "
                                       "outcome=QUOTA_EXHAUSTED"])


class SystemHealthProviderTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = HealthStore(Path(tmp.name) / "system_health.db")
        self.addCleanup(store.close)
        self.health = SystemHealth(store, HealthPolicy(3, 300.0, 900.0))

    def test_status_and_code_kept_in_details_and_logged_once(self):
        error = OpenAIProviderError("RATE_LIMITED", http_status=429, error_code="rate_limit_exceeded")
        with self.assertLogs("ai_floor.system_health", "WARNING") as logs:
            record = self.health.record_error("ai_provider", provider="openai", at=T, observation_id="o1",
                                              kind="RATE_LIMITED", exception=error, details={"model": "m"})
            self.health.record_error("ai_provider", provider="openai", at=T, observation_id="o1",
                                     kind="RATE_LIMITED", exception=error, details={"model": "m"})  # idempotent
        self.assertEqual(record.details, {"error_code": "rate_limit_exceeded", "http_status": 429, "model": "m"})
        self.assertEqual(logs.output, ["WARNING:ai_floor.system_health:component=system_health event=PROVIDER_FAILURE "
                                       "health_component=ai_provider provider=openai http_status=429 "
                                       "error_code=rate_limit_exceeded error_type=RATE_LIMITED"])

    def test_absent_fields_add_no_detail_keys(self):
        record = self.health.record_error("macro_data", provider="fxmacrodata", at=T, observation_id="m1",
                                          kind="PROVIDER_FAILURE")
        self.assertIsNone(record.details)

    def test_unsafe_error_codes_are_redacted_in_details_and_log(self):
        for index, value in enumerate(("sk-LEAK-0000", ACCOUNT, "101-000-1234567-001", f"Bearer {SECRET}")):
            with self.subTest(value=value), self.assertLogs("ai_floor.system_health", "WARNING") as logs:
                record = self.health.record_error("ai_provider", provider="openai", at=T + timedelta(minutes=index),
                                                  observation_id=f"u{index}", kind="PROVIDER_FAILURE",
                                                  http_status=400, error_code=value)
            self.assertEqual(record.details, {"error_code": "redacted", "http_status": 400})
            self.assertIn(" error_code=redacted ", logs.output[0])
            self.assertNotIn(value, logs.output[0])


if __name__ == "__main__":
    unittest.main()
