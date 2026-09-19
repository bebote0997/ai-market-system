"""Offline cloud, macro and notification controls; no external requests."""
import json
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from agents.macro_news_agent import analizar_macro_news
from core.contracts import MacroEvent
from data.finnhub_macro_provider import FinnhubMacroDataProvider, FinnhubMacroError
from data.macro_news import InMemoryMacroNewsProvider
from data.macro_news import NoMacroDataProvider
from runtime.cloud import cloud_preflight
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.notifications import SlackNotificationSink
from runtime.review_export import review_bundle, review_bundle_json
from runtime.service import OperationalRuntime
from storage.database import Store, SCHEMA_VERSION
from ui.auth import require_dashboard_access


T = datetime(2026, 9, 17, 13, 30, tzinfo=timezone.utc)


class CloudDB(unittest.TestCase):
    def setUp(self):
        self.path = Path("data/runtime") / f"cloud-test-{uuid4().hex}.db"

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def test_finnhub_normalization_cache_and_no_lookahead(self):
        calls = []
        clock = [T]
        def transport(key, start, end, timeout):
            calls.append((start, end))
            return {"economicCalendar": [{"country": "US", "event": "FOMC Rate Decision",
                "time": "2026-09-17T16:00:00Z", "impact": "high",
                "actual": "4.5", "prev": "4.75", "estimate": "4.5"},
                {"country": "DE", "event": "CPI Inflation",
                 "time": "2026-09-18T08:00:00+00:00", "impact": "medium"}]}
        provider = FinnhubMacroDataProvider(api_key="dummy", transport=transport,
                                            clock=lambda: clock[0])
        self.assertEqual(provider.macro_events_at(T - timedelta(minutes=1)), ())
        clock[0] = T + timedelta(minutes=15)
        events = provider.macro_events_at(T + timedelta(minutes=15))
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0].currency, "USD")
        self.assertEqual(events[1].currency, "EUR")
        self.assertIsNone(events[0].actual)
        self.assertEqual(events[0].importance, "HIGH")
        report = analizar_macro_news(provider, "XAUUSD", T + timedelta(minutes=15), "run")
        self.assertEqual(report.status, "OK")
        self.assertEqual(report.evidence[0]["macro_events"][0]["window"], "UPCOMING")
        self.assertEqual(len(calls), 1)
        self.assertEqual(analizar_macro_news(provider, "XAUUSD", T - timedelta(minutes=1), "past").status,
                         "NO_DATA")

    def test_finnhub_missing_key_naive_time_and_auth_fail_closed(self):
        provider = FinnhubMacroDataProvider(api_key="", clock=lambda: T)
        with self.assertRaisesRegex(FinnhubMacroError, "NOT_CONFIGURED"):
            provider.events_at(T)
        provider = FinnhubMacroDataProvider(api_key="dummy", clock=lambda: T,
            transport=lambda *_: {"economicCalendar": [{"country": "US", "event": "CPI",
                                                     "time": "2026-09-17 14:00:00"}]})
        with self.assertRaisesRegex(FinnhubMacroError, "UNKNOWN_TIMEZONE"):
            provider.events_at(T)
        self.assertEqual(provider.health, "UNKNOWN_TIMEZONE")

    def test_schema_two_migrates_and_delivery_ledger_prevents_restart_duplicate(self):
        store = Store(self.path)
        store.db.execute("DROP TABLE notification_deliveries")
        store.db.execute("DROP TABLE macro_awareness")
        store.db.execute("UPDATE schema_info SET version=2")
        store.event(T, "run", "XAUUSD", "test", "RUN_FAILED", "ERROR")
        store.close()
        with self.assertRaisesRegex(RuntimeError, "upgrade required"):
            Store(self.path, readonly=True)
        sent = []
        sink = SlackNotificationSink(webhook_url="https://hooks.slack.com/services/dummy",
                                     transport=lambda url, payload, timeout: sent.append(payload))
        config = RuntimeConfig(db_path=self.path, market_provider_mode="none")
        runner = DemoRunner(config, notification_sink=sink, clock=lambda: T)
        try:
            self.assertEqual(runner.store.db.execute("SELECT version FROM schema_info").fetchone()[0], SCHEMA_VERSION)
            first = [json.loads(item["text"].split("\n", 1)[1]) for item in sent]
            self.assertEqual(len([item for item in first if item["type"] == "RUN_FAILED"]), 1)
            self.assertEqual({r[0] for r in runner.store.db.execute("SELECT status FROM notification_deliveries")}, {"DELIVERED"})
            self.assertNotIn("dummy", json.dumps(sent))
        finally:
            runner.close()
        runner = DemoRunner(config, notification_sink=sink, clock=lambda: T)
        try:
            all_items = [json.loads(item["text"].split("\n", 1)[1]) for item in sent]
            self.assertEqual(len([item for item in all_items if item["type"] == "RUN_FAILED"]), 1)
            self.assertEqual(len({item["event_id"] for item in all_items}), len(all_items))
        finally:
            runner.close()

    def test_failed_slack_attempt_is_recorded_without_retry_or_trading_effect(self):
        store = Store(self.path)
        store.event(T, "run", "EURUSD", "test", "RATE_LIMITED", "WARNING")
        store.close()
        calls = []
        def transport(*_):
            calls.append(1)
            raise TimeoutError()
        sink = SlackNotificationSink(webhook_url="https://hooks.slack.com/services/dummy", transport=transport)
        config = RuntimeConfig(db_path=self.path, market_provider_mode="none")
        runner = DemoRunner(config, notification_sink=sink, clock=lambda: T)
        runner.close()
        runner = DemoRunner(config, notification_sink=sink, clock=lambda: T)
        try:
            self.assertEqual(len(calls), runner.store.db.execute("SELECT count(*) FROM notification_deliveries").fetchone()[0])
            self.assertEqual({r[0] for r in runner.store.db.execute("SELECT status FROM notification_deliveries")}, {"FAILED"})
            self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
        finally:
            runner.close()

    def test_macro_awareness_only_once_per_event_and_symbol(self):
        store = Store(self.path)
        try:
            item = {"event_id": "finnhub:abc", "impact": "HIGH", "window": "UPCOMING",
                    "event_timestamp": T + timedelta(hours=2)}
            store.record_macro_awareness(T, "r1", "XAUUSD", (item, item))
            store.record_macro_awareness(T, "r2", "XAUUSD", (item,))
            self.assertEqual(len(store.journal(event="MACRO_HIGH_IMPORTANCE")), 1)
            notifications = store.capture_notifications()
            macro = [event for event in notifications if event.type == "MACRO_HIGH_IMPORTANCE"]
            self.assertEqual(len(macro), 1)
            self.assertIn("finnhub:abc", macro[0].evidence_refs)
        finally:
            store.close()

    def test_cloud_preflight_distinguishes_infrastructure_and_experiment(self):
        mount = self.path.parent.resolve()
        path = mount / self.path.name
        config = RuntimeConfig(db_path=path, ai_provider_mode="openai",
                               macro_provider_mode="none")
        env = {"AI_FLOOR_DURABLE_MOUNT": str(mount), "AI_FLOOR_INSTANCE_COUNT": "1",
               "OPENAI_API_KEY": "present", "TWELVE_DATA_API_KEY": "present",
               "SLACK_WEBHOOK_URL": "present",
               "AI_FLOOR_DASHBOARD_PASSWORD": "present"}
        ready = cloud_preflight(config, env=env, disk_mounted=True)
        self.assertEqual(ready.status, "INFRA_READY")
        self.assertTrue(ready.infra_ready)
        self.assertFalse(ready.experiment_ready)
        self.assertTrue(ready.checks["macro_provider_disabled"])
        self.assertTrue(ready.checks["macro_provider_valid"])
        self.assertFalse(ready.checks["macro_provider_certified"])
        self.assertEqual(cloud_preflight(config, env={**env, "OPENAI_API_KEY": ""}, disk_mounted=True).status,
                         "INFRA_READY")
        self.assertEqual(cloud_preflight(config, env={**env, "AI_FLOOR_DASHBOARD_PASSWORD": ""}, disk_mounted=True).status,
                         "NOT_READY")
        self.assertEqual(cloud_preflight(RuntimeConfig(db_path=path, ai_provider_mode="openai",
                         macro_provider_mode="finnhub"), env=env, disk_mounted=True).status, "NOT_READY")
        self.assertEqual(cloud_preflight(config, env=env, disk_mounted=False).status, "NOT_READY")

    def test_cloud_preflight_accepts_certified_fxmacrodata_without_starting_experiment(self):
        mount = self.path.parent.resolve()
        path = mount / self.path.name
        config = RuntimeConfig(db_path=path, ai_provider_mode="openai",
                               macro_provider_mode="fxmacrodata")
        env = {"AI_FLOOR_DURABLE_MOUNT": str(mount), "AI_FLOOR_INSTANCE_COUNT": "1",
               "AI_FLOOR_CLOUD_RUNNER": "0", "FXMACRODATA_API_KEY": "present",
               "OPENAI_API_KEY": "present", "TWELVE_DATA_API_KEY": "present",
               "SLACK_WEBHOOK_URL": "present",
               "AI_FLOOR_DASHBOARD_PASSWORD": "present"}
        ready = cloud_preflight(config, env=env, disk_mounted=True)
        self.assertEqual(ready.status, "INFRA_READY")
        self.assertTrue(ready.infra_ready)
        self.assertTrue(ready.experiment_ready)
        self.assertFalse(ready.checks["macro_provider_disabled"])
        self.assertTrue(ready.checks["macro_provider_valid"])
        self.assertTrue(ready.checks["macro_provider_certified"])
        self.assertTrue(ready.checks["scheduler_not_started"])
        self.assertTrue(ready.checks["experiment_not_started"])

    def test_cloud_preflight_rejects_fxmacrodata_without_key(self):
        mount = self.path.parent.resolve()
        path = mount / self.path.name
        config = RuntimeConfig(db_path=path, ai_provider_mode="openai",
                               macro_provider_mode="fxmacrodata")
        env = {"AI_FLOOR_DURABLE_MOUNT": str(mount), "AI_FLOOR_INSTANCE_COUNT": "1",
               "AI_FLOOR_CLOUD_RUNNER": "0", "OPENAI_API_KEY": "present",
               "TWELVE_DATA_API_KEY": "present", "SLACK_WEBHOOK_URL": "present",
               "AI_FLOOR_DASHBOARD_PASSWORD": "present"}
        blocked = cloud_preflight(config, env=env, disk_mounted=True)
        self.assertEqual(blocked.status, "NOT_READY")
        self.assertFalse(blocked.infra_ready)
        self.assertFalse(blocked.experiment_ready)
        self.assertFalse(blocked.checks["macro_provider_valid"])
        self.assertFalse(blocked.checks["macro_provider_certified"])

    def test_disabled_macro_is_explicitly_no_data(self):
        provider = NoMacroDataProvider()
        report = analizar_macro_news(provider, "XAUUSD", T, "run")
        self.assertEqual(provider.health, "NO_DATA")
        self.assertEqual(report.status, "NO_DATA")
        self.assertEqual(report.evidence[0]["macro_events"], ())
        runtime = OperationalRuntime(RuntimeConfig(db_path=self.path, market_provider_mode="none",
                                                    macro_provider_mode="none"), clock=lambda: T)
        try:
            self.assertIsInstance(runtime.macro_provider, NoMacroDataProvider)
            self.assertEqual(runtime.store.get_state("macro_provider"), "NO_DATA")
        finally:
            runtime.close()

    def test_cloud_runner_cannot_start_without_certified_macro(self):
        from runtime import cloud_runner
        config = RuntimeConfig(db_path=self.path, scheduler_enabled=True, macro_provider_mode="none")
        with patch.dict("os.environ", {"RENDER": "true", "AI_FLOOR_CLOUD_RUNNER": "1"}), \
             patch.object(RuntimeConfig, "from_env", return_value=config):
            with self.assertRaisesRegex(RuntimeError, "experiment activation preflight not ready"):
                cloud_runner.main()

    def test_private_dashboard_gate_and_bounded_review_export(self):
        class Stop(Exception):
            pass
        class UI:
            session_state = {}
            def error(self, *_): pass
            def title(self, *_): pass
            def text_input(self, *_, **__): return "wrong"
            def button(self, *_): return True
            def stop(self): raise Stop()
        with self.assertRaises(Stop):
            require_dashboard_access(UI(), {"RENDER": "true", "AI_FLOOR_DASHBOARD_PASSWORD": "right"})
        self.assertTrue(require_dashboard_access(UI(), {}))
        store = Store(self.path)
        try:
            bundle = review_bundle(store)
            self.assertTrue(bundle["paper_only"])
            self.assertFalse("api_key" in json.dumps(bundle).lower())
        finally:
            store.close()

    def test_review_export_redacts_secret_like_text_and_enforces_size(self):
        secret = "sk-" + "abcdefghijklmnopqrstuvwxyz123456"
        class FakeDB:
            def execute(self, sql, *_):
                from types import SimpleNamespace
                if "review_reports" in sql:
                    return SimpleNamespace(fetchall=lambda: [(json.dumps({"agents": [{"agent": "macro", "reasoning_summary": secret}],
                                         "warnings": ["https://hooks.slack.com/services/fake/secret"]}),)])
                return SimpleNamespace(fetchall=lambda: [])
        class FakeStore:
            db = FakeDB()
            def get_state(self, _): return None
        exported = review_bundle_json(FakeStore())
        self.assertNotIn(secret, exported)
        self.assertNotIn("hooks.slack.com/services", exported)
        with patch("runtime.review_export.MAX_EXPORT_BYTES", 100):
            with self.assertRaisesRegex(ValueError, "safe size"):
                review_bundle_json(FakeStore())

    def test_render_blueprint_static_safety_configuration(self):
        blueprint = Path("render.yaml").read_text(encoding="utf-8")
        self.assertEqual(blueprint.count("  - type: web"), 1)
        self.assertNotIn("  - type: worker", blueprint)
        self.assertNotIn("  - type: cron", blueprint)
        self.assertIn("    numInstances: 1", blueprint)
        self.assertIn("    autoDeployTrigger: off", blueprint)
        self.assertIn("    healthCheckPath: /_stcore/health", blueprint)
        self.assertIn("    disk:\n", blueprint)
        self.assertIn("      mountPath: /opt/render/project/src/data/runtime", blueprint)
        self.assertIn("      sizeGB: 1", blueprint)
        keys = re.findall(r"^      - key: ([A-Z0-9_]+)$", blueprint, re.MULTILINE)
        self.assertEqual(len(keys), len(set(keys)))
        self.assertIn("      - key: AI_FLOOR_DB_PATH\n        value: /opt/render/project/src/data/runtime/trading_floor.db", blueprint)
        self.assertIn('        value: "0"', blueprint)
        for secret in ("OPENAI_API_KEY", "TWELVE_DATA_API_KEY",
                       "SLACK_WEBHOOK_URL", "AI_FLOOR_DASHBOARD_PASSWORD"):
            self.assertIn(f"      - key: {secret}\n        sync: false", blueprint)
        self.assertIn("      - key: AI_FLOOR_MACRO_PROVIDER\n        value: none", blueprint)
        self.assertNotIn("      - key: FINNHUB_API_KEY", blueprint)
        self.assertNotIn("AI_FLOOR_CLOUD_RUNNER\n        value: \"1\"", blueprint)
        self.assertNotIn("AI_FLOOR_SCHEDULER\n        value: \"1\"", blueprint)


if __name__ == "__main__":
    unittest.main()
