"""Watchdog/runner stall diagnostics: offline, no real processes, sleeps or network."""
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from runtime import cloud, cloud_runner
from runtime.config import RuntimeConfig
from storage.database import Store

NOW = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
FREEZE = "0123456789abcdef0123456789abcdef01234567"
HEALTHY = {"healthy": True, "scheduler": "RUNNING", "heartbeat_age_seconds": 5, "error_type": None}
LOCKED = {"healthy": False, "scheduler": None, "heartbeat_age_seconds": None, "error_type": "OperationalError"}
STALE = {"healthy": False, "scheduler": "RUNNING", "heartbeat_age_seconds": 300, "error_type": None}


class HealthDetailTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "trading_floor.db"

    def write(self, age_seconds, scheduler="RUNNING"):
        store = Store(self.path)
        try:
            store.heartbeat(NOW - timedelta(seconds=age_seconds), scheduler)
        finally:
            store.close()

    def test_healthy(self):
        self.write(30)
        self.assertEqual(cloud.scheduler_health_detail(self.path, now=NOW),
                         {"healthy": True, "scheduler": "RUNNING", "heartbeat_age_seconds": 30, "error_type": None})
        self.assertIs(cloud.scheduler_healthy(self.path, now=NOW), True)

    def test_stale(self):
        self.write(121)
        detail = cloud.scheduler_health_detail(self.path, now=NOW)
        self.assertEqual((detail["healthy"], detail["heartbeat_age_seconds"], detail["error_type"]), (False, 121, None))
        self.assertIs(cloud.scheduler_healthy(self.path, now=NOW), False)
        self.assertIs(cloud.scheduler_healthy(self.path, now=NOW, limit_seconds=200), True)

    def test_not_running(self):
        self.write(10, scheduler="STOPPED")
        detail = cloud.scheduler_health_detail(self.path, now=NOW)
        self.assertEqual((detail["healthy"], detail["scheduler"], detail["error_type"]), (False, "STOPPED", None))
        self.assertIs(cloud.scheduler_healthy(self.path, now=NOW), False)

    def test_missing_db_reports_error_type_and_creates_nothing(self):
        detail = cloud.scheduler_health_detail(self.path, now=NOW)
        self.assertFalse(detail["healthy"])
        self.assertIsNone(detail["heartbeat_age_seconds"])
        self.assertEqual(detail["error_type"], "OperationalError")
        self.assertIs(cloud.scheduler_healthy(self.path, now=NOW), False)
        self.assertFalse(self.path.exists())


class WatchdogRetryTests(unittest.TestCase):
    def check(self, details):
        sleeps = MagicMock()
        with patch.object(cloud, "scheduler_health_detail", side_effect=details) as detail, \
                patch.object(cloud, "_sleep", sleeps):
            result = cloud.check_scheduler_or_raise("/secret/path/trading_floor.db")
        return result, detail, sleeps

    def test_transient_read_error_then_healthy_does_not_raise(self):
        result, detail, sleeps = self.check([LOCKED, LOCKED, HEALTHY])
        self.assertTrue(result["healthy"])
        self.assertEqual(detail.call_count, 3)
        self.assertEqual([c.args for c in sleeps.call_args_list], [(2,), (2,)])

    def test_healthy_reads_once(self):
        _, detail, sleeps = self.check([HEALTHY])
        self.assertEqual(detail.call_count, 1)
        sleeps.assert_not_called()

    def test_persistent_read_error_raises_after_three_retries(self):
        with self.assertLogs("runtime.cloud", "ERROR") as logs, \
                self.assertRaisesRegex(RuntimeError, "scheduler heartbeat stale"):
            self.check([LOCKED] * 4 + [HEALTHY])
        self.assertIn("error_type=OperationalError", logs.output[0])

    def test_persistent_read_error_retry_count(self):
        sleeps = MagicMock()
        with patch.object(cloud, "scheduler_health_detail", side_effect=[LOCKED] * 4) as detail, \
                patch.object(cloud, "_sleep", sleeps), self.assertLogs("runtime.cloud", "ERROR"), \
                self.assertRaises(RuntimeError):
            cloud.check_scheduler_or_raise("db")
        self.assertEqual((detail.call_count, sleeps.call_count), (4, 3))

    def test_stale_heartbeat_raises_immediately_and_logs_without_paths(self):
        sleeps = MagicMock()
        with patch.object(cloud, "scheduler_health_detail", side_effect=[STALE]) as detail, \
                patch.object(cloud, "_sleep", sleeps), self.assertLogs("runtime.cloud", "ERROR") as logs, \
                self.assertRaisesRegex(RuntimeError, "scheduler heartbeat stale"):
            cloud.check_scheduler_or_raise("/secret/path/trading_floor.db")
        self.assertEqual(detail.call_count, 1)
        sleeps.assert_not_called()
        message = logs.records[0].getMessage()
        self.assertEqual(message, "component=watchdog event=SCHEDULER_STALE scheduler=RUNNING "
                                  "heartbeat_age_s=300 error_type=None")
        self.assertNotIn("/secret", message)
        self.assertNotIn("trading_floor", message)

    def test_logging_configured_only_without_root_handlers(self):
        root = logging.getLogger()
        for handlers, expected in (([], 1), ([logging.NullHandler()], 0)):
            with self.subTest(handlers=len(handlers)), patch.object(root, "handlers", handlers), \
                    patch.object(cloud.logging, "basicConfig") as basic, \
                    patch.object(cloud, "scheduler_health_detail", return_value=STALE), \
                    patch.object(cloud.LOGGER, "error"), self.assertRaises(RuntimeError):
                cloud.check_scheduler_or_raise("db")
            self.assertEqual(basic.call_count, expected)
            if expected:
                self.assertEqual(basic.call_args.kwargs, {"level": logging.INFO, "stream": cloud.sys.stdout})


class ServeWatchdogTests(unittest.TestCase):
    def test_serve_runs_the_watchdog_check_after_warmup(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = RuntimeConfig(db_path=Path(tmp.name) / "trading_floor.db", scheduler_enabled=True)
        child = MagicMock()
        child.poll.return_value = None
        clock = iter([0.0, 200.0])
        env = {"RENDER": "true", "PORT": "10000", "AI_FLOOR_DASHBOARD_PASSWORD": "correct-horse-battery-staple",
               "AI_FLOOR_CLOUD_RUNNER": "1"}
        with patch.dict(os.environ, env, clear=True), patch.object(cloud.subprocess, "Popen", return_value=child), \
                patch.object(cloud.os, "umask"), patch.object(cloud.signal, "signal"), \
                patch("execution.log_redaction.install_log_redaction"), \
                patch.object(cloud.RuntimeConfig, "from_env", return_value=config), \
                patch.object(cloud, "cloud_preflight", return_value=SimpleNamespace(experiment_ready=True)), \
                patch.object(cloud, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=MagicMock())), \
                patch.object(cloud, "_sleep") as sleeps, patch.object(cloud, "archive_pre_demo_db"), \
                patch.object(cloud, "scheduler_health_detail", side_effect=[STALE]) as detail, \
                self.assertLogs("runtime.cloud", "ERROR") as logs, \
                self.assertRaisesRegex(RuntimeError, "scheduler heartbeat stale"):
            cloud.serve()
        detail.assert_called_once_with(config.db_path)
        sleeps.assert_not_called()
        self.assertIn("event=SCHEDULER_STALE", logs.output[0])


class RunnerStallDiagnosticsTests(unittest.TestCase):
    def run_main(self, clock, tick_side_effect=None):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        config = RuntimeConfig(db_path=Path(tmp.name) / "trading_floor.db", scheduler_enabled=True,
                               macro_provider_mode="fxmacrodata")
        handlers = {}
        runner = MagicMock()
        runner.tick.side_effect = tick_side_effect
        runner.daily_summary.side_effect = lambda _at: handlers[cloud_runner.signal.SIGTERM]()
        fake_time = SimpleNamespace(monotonic=MagicMock(side_effect=clock), sleep=MagicMock())
        env = {"RENDER": "true", "AI_FLOOR_CLOUD_RUNNER": "1", "AI_FLOOR_GIT_COMMIT": FREEZE}
        with patch.dict(os.environ, env, clear=True), \
                patch.object(cloud_runner.RuntimeConfig, "from_env", return_value=config), \
                patch.object(cloud_runner, "cloud_preflight", return_value=SimpleNamespace(experiment_ready=True)), \
                patch.object(cloud_runner, "DemoRunner", return_value=runner), \
                patch.object(cloud_runner, "SlackNotificationSink"), \
                patch.object(cloud_runner, "paper_instruments"), \
                patch.object(cloud_runner.logging, "basicConfig"), \
                patch("execution.log_redaction.install_log_redaction"), \
                patch.object(cloud_runner.signal, "signal", side_effect=lambda sig, fn: handlers.__setitem__(sig, fn)), \
                patch.object(cloud_runner, "faulthandler") as fault, \
                patch.object(cloud_runner, "time", fake_time):
            self.fault = fault
            cloud_runner.main()
        return fault, runner, fake_time

    def test_arms_and_cancels_stall_dump_around_each_tick(self):
        with self.assertNoLogs("runtime.cloud_runner", "WARNING"):
            fault, runner, fake_time = self.run_main([0.0, 10.0])
        fault.dump_traceback_later.assert_called_once_with(cloud_runner.STALL_DUMP_SECONDS, repeat=False, file=cloud_runner.sys.stderr)
        fault.cancel_dump_traceback_later.assert_called_once_with()
        self.assertEqual([c[0] for c in fault.mock_calls], ["dump_traceback_later", "cancel_dump_traceback_later"])
        runner.tick.assert_called_once_with()
        runner.close.assert_called_once_with()
        fake_time.sleep.assert_not_called()  # stop was requested during the tick: cadence loop exits as before

    def test_slow_tick_is_logged(self):
        with self.assertLogs("runtime.cloud_runner", "WARNING") as logs:
            self.run_main([100.0, 195.4])
        self.assertEqual(logs.records[0].getMessage(), "component=runner event=SLOW_TICK seconds=95")

    def test_timer_cancelled_when_tick_raises(self):
        with self.assertRaises(sqlite3.OperationalError):
            self.run_main([0.0, 1.0], tick_side_effect=sqlite3.OperationalError("locked"))
        self.fault.cancel_dump_traceback_later.assert_called_once_with()

    def test_diagnostics_never_raise(self):
        with patch.object(cloud_runner, "faulthandler") as fault, \
                patch.object(cloud_runner, "time", SimpleNamespace(monotonic=MagicMock(side_effect=OSError))):
            fault.dump_traceback_later.side_effect = RuntimeError("no fileno")
            fault.cancel_dump_traceback_later.side_effect = RuntimeError("boom")
            cloud_runner._arm_stall_dump()
            cloud_runner._disarm_stall_dump(0.0)


if __name__ == "__main__":
    unittest.main()
