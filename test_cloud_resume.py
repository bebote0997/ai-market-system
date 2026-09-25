"""Offline regression for resuming the same frozen PAPER experiment."""
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from execution.contracts import PaperAccount, PaperPosition
from execution.paper_broker import PaperBroker
from runtime.cloud import cloud_preflight
from runtime.config import RuntimeConfig
from storage.database import Store

FREEZE = "4428fc20d121d8b34ace9b97a0e87c4528f3d6f7"
BASELINE = "f5032baeb87766ad74905093c1b4195117995092"
AT = datetime(2026, 9, 19, 22, 8, tzinfo=timezone.utc)


class ExistingExperimentPreflight(unittest.TestCase):
    def setUp(self):
        self.path = (Path("data/runtime") / f"resume-test-{uuid4().hex}.db").resolve()
        self.config = RuntimeConfig(db_path=self.path, ai_provider_mode="openai",
                                    macro_provider_mode="fxmacrodata")
        self.env = {
            "AI_FLOOR_DURABLE_MOUNT": str(self.path.parent),
            "AI_FLOOR_INSTANCE_COUNT": "1", "AI_FLOOR_CLOUD_RUNNER": "0",
            "AI_FLOOR_GIT_COMMIT": FREEZE, "FXMACRODATA_API_KEY": "test",
            "OPENAI_API_KEY": "test", "TWELVE_DATA_API_KEY": "test",
            "SLACK_WEBHOOK_URL": "test", "AI_FLOOR_DASHBOARD_PASSWORD": "test",
        }
        with closing(Store(self.path)) as store:
            account = PaperAccount("1.0", "paper-main", 10000, 10000, 10000)
            account.open_positions["XAUUSD"] = PaperPosition(
                "1.0", "position", "order", "run", "XAUUSD", "SHORT",
                1, 4300, 4300, 4350, 4150, AT, last_price=4300,
                contract_multiplier=1)
            store.save_paper(PaperBroker(account))
            store.start_experiment_if_unstarted(AT, BASELINE, FREEZE)
            store.claim_slot("fixture", "XAUUSD", AT, AT, run_id="run")
            store.finish("fixture", AT, "COMPLETED", "WATCH")
            store.save_run_metadata("fixture", replace(self.config, scheduler_enabled=True).fingerprint(),
                                    10000, FREEZE)

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def snapshot(self):
        with closing(Store(self.path, readonly=True)) as store:
            return list(store.db.iterdump())

    def test_same_experiment_resumes_without_mutating_durable_state(self):
        before = self.snapshot()
        result = cloud_preflight(self.config, env=self.env, disk_mounted=True)
        self.assertTrue(result.experiment_ready)
        self.assertFalse(result.checks["experiment_not_started"])
        self.assertTrue(result.checks["experiment_resumable"])
        self.assertEqual(self.snapshot(), before)

    def test_different_or_missing_freeze_remains_blocked(self):
        for freeze in ("", "a" * 40):
            with self.subTest(freeze=freeze):
                result = cloud_preflight(self.config,
                    env={**self.env, "AI_FLOOR_GIT_COMMIT": freeze}, disk_mounted=True)
                self.assertFalse(result.experiment_ready)

    def test_runtime_configuration_drift_remains_blocked(self):
        changed = replace(self.config, max_age_seconds=(("1h", 7200), ("15m", 1800), ("5m", 900)))
        self.assertFalse(cloud_preflight(changed, env=self.env, disk_mounted=True).experiment_ready)

    def test_missing_account_and_credentials_remain_blocked(self):
        self.assertFalse(cloud_preflight(self.config,
            env={**self.env, "FXMACRODATA_API_KEY": ""}, disk_mounted=True).experiment_ready)
        with closing(Store(self.path)) as store:
            store.db.execute("DELETE FROM paper_accounts")
            store.db.commit()
        self.assertFalse(cloud_preflight(self.config, env=self.env, disk_mounted=True).experiment_ready)
