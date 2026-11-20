"""Ops hardening: soft provider alerts, disk full, Exness label, one auth retry, combo adopt, watchdog."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import os
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from execution.combo_admin import CONFIRM, adopt_position, inspect_flags, main
from execution.exness_brakes import MemoryFlags, SqliteFlags
from execution.exness_client import ExnessClient
from execution.results_source import results_label
from runtime.alert_catalog import from_notification, soft_provider_failure
from runtime.cloud import scheduler_health_detail
from runtime.health_probes import probe_disk
from runtime.notifications import NotificationEvent, _summary_source, compact_payload, provider_failure_execution
from runtime.system_health import HealthPolicy, HealthStatus, SystemHealth
from storage.database import Store
from storage.disk_guard import (is_disk_full, maintain_disk, prune_old_archives, prune_rex_journal,
                                reset_disk_warning, warn_if_tight)
from storage.health_store import HealthStore
from test_watchdog_diagnostics import NOW


def _note(kind, execution=None, severity="ERROR"):
    return NotificationEvent("1.0", "n-1", "run-1", "2026-11-19T22:02:00+00:00", severity, kind, "EURUSD",
                             (), (), None, (), (), (), None, execution)


class AlertSeverityTests(unittest.TestCase):
    def test_xai_timeout_and_fxmacro_are_not_high_alerts(self):
        xai = _note("PROVIDER_FAILURE", {"provider": "xai", "outcome": "TIMEOUT", "reason": "provider=xai"})
        macro = _note("PROVIDER_FAILURE", {"provider": "fxmacrodata", "error_code": "PROVIDER_FAILURE",
                                           "reason": "provider=fxmacrodata"})
        hard = _note("PROVIDER_FAILURE", {"provider": "twelve_data", "http_status": 503,
                                          "reason": "provider=twelve_data http_status=503"})
        self.assertTrue(soft_provider_failure(xai))
        self.assertTrue(soft_provider_failure(macro))
        self.assertIsNone(from_notification(xai))
        self.assertIsNone(from_notification(macro))
        self.assertIsNone(compact_payload(xai))
        self.assertIsNone(compact_payload(macro))
        self.assertEqual(from_notification(hard).priority, "HIGH")
        self.assertEqual(from_notification(_note("RUN_FAILED")).priority, "HIGH")
        self.assertEqual(from_notification(_note("TICK_FAILED")).priority, "HIGH")

    def test_outcome_is_copied_when_it_is_a_token(self):
        got = provider_failure_execution({"provider": "xai", "outcome": "TIMEOUT"})
        self.assertEqual(got["outcome"], "TIMEOUT")
        noisy = provider_failure_execution({"provider": "xai", "outcome": "bad token"})
        self.assertNotIn("outcome", noisy)


class DiskTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = Store(Path(tmp.name) / "trading_floor.db")
        self.addCleanup(self.store.close)
        self.tmp = Path(tmp.name)

    def test_disk_full_on_save_and_recover_does_not_raise(self):
        self.assertTrue(is_disk_full(sqlite3.OperationalError("database or disk is full")))
        self.assertFalse(is_disk_full(sqlite3.OperationalError("database is locked")))

        class Full:
            def execute(self, *_args):
                raise sqlite3.OperationalError("database or disk is full")

        from storage.disk_guard import disk_soft
        real = self.store.db
        self.store.db = Full()
        try:
            with self.assertLogs("ai_floor.disk", "ERROR") as logs:
                self.assertFalse(disk_soft(lambda: self.store.save_paper(SimpleNamespace(account=SimpleNamespace())),
                                           "save_paper"))
                self.assertEqual(disk_soft(lambda: self.store.recover(NOW), "recover", fallback=0), 0)
            self.assertEqual(sum("DISK_FULL" in line for line in logs.output), 2)
        finally:
            self.store.db = real

    def test_rex_prune_keeps_recent_rows_and_non_rex_rows(self):
        old = NOW - timedelta(days=40)
        self.store.event(old, "r", "EURUSD", "rex", "REX_WRITE", "INFO", {"rex": "old"})
        self.store.event(NOW, "r", "EURUSD", "rex", "REX_WRITE", "INFO", {"rex": "new"})
        self.store.event(old, "r", "EURUSD", "runtime", "RUN_FAILED", "ERROR", {})
        removed = prune_rex_journal(self.store, now=NOW)
        self.assertEqual(removed, 1)
        kinds = [row["event_type"] for row in self.store.journal(limit=10)]
        self.assertEqual(kinds.count("REX_WRITE"), 1)
        self.assertIn("RUN_FAILED", kinds)

    def test_old_archives_except_the_newest_are_removed(self):
        folder = self.tmp / "archive"
        folder.mkdir()
        old = folder / "old.db"
        keep = folder / "keep.db"
        old.write_bytes(b"old")
        keep.write_bytes(b"keep")
        os.utime(old, (1, 1))
        self.assertEqual(prune_old_archives(self.tmp / "trading_floor.db", now=datetime(2026, 11, 20, tzinfo=timezone.utc)), 1)
        self.assertFalse(old.exists())
        self.assertTrue(keep.exists())

    def test_warning_at_eighty_percent(self):
        reset_disk_warning()
        self.assertIsNone(warn_if_tight(self.tmp, ratio=0.79))
        with self.assertLogs("ai_floor.disk", "WARNING") as logs:
            detail = warn_if_tight(self.tmp, ratio=0.80)
        self.assertEqual(detail["severity"], "WARNING")
        self.assertIn("DISK_USAGE_WARNING", logs.output[0])
        self.assertIsNotNone(warn_if_tight(self.tmp, ratio=0.91))
        self.assertEqual(len(logs.output), 1)
        health_dir = self.tmp / "runtime"
        health_dir.mkdir()
        (health_dir / "trading_floor.db").write_bytes(b"")
        health = SystemHealth(HealthStore(self.tmp / "health.db"),
                              HealthPolicy(failed_after_consecutive_errors=2, heartbeat_stale_after_seconds=120,
                                           progress_stale_after_seconds=120))
        usage = SimpleNamespace(total=100, free=10)
        probe_disk(health_dir, health, now=NOW, disk_usage=lambda _path: usage)
        self.assertEqual(health.get("persistent_disk").status, HealthStatus.DEGRADED.value)

    def test_maintain_never_raises(self):
        self.assertIsNone(maintain_disk(self.store))


class LabelTests(unittest.TestCase):
    def test_exness_replaces_a_leftover_oanda_demo_source(self):
        env = {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO",
               "AI_FLOOR_BROKER_DEMO_ROUTING": "1"}
        self.assertEqual(results_label(env), "EXNESS")
        self.assertEqual(results_label({"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}), "OANDA_DEMO")
        self.assertEqual(results_label({}), "PAPER")
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(_summary_source(), "EXNESS")


def _client(caller):
    now = [0]

    def sleep(seconds):
        now[0] += int(seconds * 1000) + 1

    return ExnessClient(
        {"EXN_API_KEY": "test-key", "EXN_ACCOUNT_ID": "100", "EXN_DISCOVERY_BASE_URL": "https://gateway.example"},
        caller=caller, sign=lambda data: b"sig", now_ms=lambda: now[0], sleep=sleep)


class AuthRetryTests(unittest.TestCase):
    def test_one_auth_retry_then_stop(self):
        calls = []

        def caller(method, url, headers, body):
            del headers, body
            calls.append((method, url))
            if url.endswith("/snapshot") and sum(1 for _m, item in calls if item.endswith("/snapshot")) == 1:
                return 401, {"error_message": "AUTH_INVALID_API_KEY"}, {}
            if "access-point" in url:
                return 200, {"access_point": "https://trade.example"}, {}
            return 200, {"ok": True}, {}

        client = _client(caller)
        with self.assertLogs("ai_floor.exness", "WARNING") as logs:
            payload = client.get_json("/v1/trading/accounts/100/snapshot")
        self.assertEqual(payload, {"ok": True})
        self.assertEqual(sum(1 for _m, url in calls if url.endswith("/snapshot")), 2)
        self.assertIn("AUTH_RETRY", logs.output[0])
        self.assertNotIn("test-key", logs.output[0])
        self.assertNotIn("gateway.example", logs.output[0])

    def test_a_second_401_is_not_retried_again(self):
        calls = []

        def caller(method, url, headers, body):
            del method, headers, body
            calls.append(url)
            if "access-point" in url:
                return 200, {"access_point": "https://trade.example"}, {}
            return 401, {"error_message": "AUTH_INVALID_API_KEY"}, {}

        client = _client(caller)
        with self.assertLogs("ai_floor.exness", "WARNING"), self.assertRaises(RuntimeError):
            client.get_json("/v1/trading/accounts/100/snapshot")
        self.assertEqual(sum(1 for url in calls if url.endswith("/snapshot")), 2)


class AdoptTests(unittest.TestCase):
    def test_adopt_is_read_only_until_the_confirm_token(self):
        store = MemoryFlags()
        day = datetime(2026, 11, 18, tzinfo=timezone.utc).date()
        plan = adopt_position(store, day=day, position_id="998877", symbol="USDJPY", side="LONG")
        self.assertFalse(plan["wrote"])
        self.assertEqual(plan["reason"], "confirm_required")
        self.assertEqual(inspect_flags(store), [])
        done = adopt_position(store, day=day, position_id="998877", symbol="USDJPY", side="LONG", confirm=CONFIRM)
        self.assertTrue(done["wrote"])
        saved = json.loads(store.get_flag("combo_lon_2026-11-18"))
        self.assertEqual(saved["status"], "filled")
        self.assertEqual(saved["position_id"], "998877")
        audit = json.loads(store.get_flag("combo_admin_audit"))
        self.assertEqual(audit[0]["position_tail"], "8877")
        self.assertNotIn("998877", json.dumps(audit))

    def test_cli_default_prints_status_only(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "flags.db"
        store = SqliteFlags(path)
        store.set_flag("combo_lon_2026-11-18", json.dumps({"status": "filled"}))
        with patch("sys.stdout") as out:
            code = main(["--db", str(path)])
        self.assertEqual(code, 0)
        text = "".join(call.args[0] for call in out.write.call_args_list)
        self.assertIn("combo_lon_2026-11-18 status=filled", text)
        self.assertNotIn("position_id", text)


class WatchdogSkewTests(unittest.TestCase):
    def test_heartbeat_slightly_ahead_is_not_stale(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = Path(tmp.name) / "trading_floor.db"
        store = Store(path)
        store.heartbeat(NOW + timedelta(milliseconds=400), "RUNNING")
        store.close()
        detail = scheduler_health_detail(path, now=NOW)
        self.assertEqual(detail["heartbeat_age_seconds"], 0)
        self.assertTrue(detail["healthy"])
        self.assertIsNone(detail["error_type"])


if __name__ == "__main__":
    unittest.main()
