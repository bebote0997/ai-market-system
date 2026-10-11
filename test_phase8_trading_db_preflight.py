"""V2 Phase 8 / P8.4G FIX A (DEC-8.11, P8.5R HIGH): the trading DB preflights require a PERSISTENT write.

Before the fix ``cloud_preflight`` and ``demo_runner.preflight`` probed with a TEMP table, which SQLite keeps in its
temp database: a read-only trading DB was reported writable (INFRA_READY / READY)."""
import os
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from replay.activation_preview import sha256
from runtime import config as config_module
from runtime.cloud import cloud_preflight
from runtime.config import RuntimeConfig, persistent_write_probe
from runtime.demo_runner import REAL_EXECUTION_ENABLED, preflight
from storage.database import SCHEMA_VERSION, Store

ENV = {"AI_FLOOR_INSTANCE_COUNT": "1", "OPENAI_API_KEY": "present", "TWELVE_DATA_API_KEY": "present",
       "SLACK_WEBHOOK_URL": "present", "AI_FLOOR_DASHBOARD_PASSWORD": "present"}


class TradingDbPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mount = Path(self.tmp.name).resolve()
        self.path = self.mount / "trading_floor.db"
        store = Store(self.path)
        try:
            with store.transaction():
                store.set_state("economic_marker", "unchanged")
        finally:
            store.close()

    def tearDown(self):
        os.chmod(self.path, stat.S_IREAD | stat.S_IWRITE)
        self.tmp.cleanup()

    def both(self):
        env = {**ENV, "AI_FLOOR_DURABLE_MOUNT": str(self.mount)}
        cloud = cloud_preflight(RuntimeConfig(db_path=self.path, ai_provider_mode="openai",
                                              macro_provider_mode="none"), env=env, disk_mounted=True)
        demo = preflight(RuntimeConfig(db_path=self.path, market_provider_mode="twelve_data",
                                       ai_provider_mode="openai"), env=env)
        return cloud, demo

    def assert_not_ready(self):
        cloud, demo = self.both()
        self.assertEqual((cloud.status, cloud.checks["db_writable_schema"]), ("NOT_READY", False))
        self.assertEqual((demo.status, demo.checks["db_writable_schema"]), ("NOT_READY", False))

    def test_valid_db_stays_ready_without_any_change(self):
        before = sha256(self.path)
        cloud, demo = self.both()
        self.assertEqual((cloud.status, cloud.checks["db_writable_schema"]), ("INFRA_READY", True))
        self.assertEqual((demo.status, demo.checks["db_writable_schema"]), ("READY", True))
        self.assertEqual(sha256(self.path), before)  # no economic, schema or version change; no residue
        db = sqlite3.connect(self.path)
        try:
            self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE '%probe%'").fetchone())
            self.assertEqual(db.execute("SELECT version FROM schema_info").fetchone()[0], SCHEMA_VERSION)
        finally:
            db.close()
        self.assertFalse(REAL_EXECUTION_ENABLED)
        self.assertTrue(cloud.checks["paper_only"] and demo.checks["real_execution_disabled"])

    def test_read_only_db_is_not_ready(self):
        """P8.5R regression: the TEMP probe reported this file writable (INFRA_READY / READY)."""
        os.chmod(self.path, stat.S_IREAD)
        before = sha256(self.path)
        self.assert_not_ready()
        self.assertEqual(sha256(self.path), before)

    def test_insufficient_directory_permission_is_not_ready(self):
        real = os.access
        parent = str(self.mount)

        def access(path, mode, *args, **kwargs):
            if str(Path(path).resolve()) == parent and mode == os.W_OK:
                return False
            return real(path, mode, *args, **kwargs)

        with patch.object(config_module.os, "access", side_effect=access):
            self.assert_not_ready()

    def test_sqlite_error_during_the_probe_is_not_ready_and_rolled_back(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("CREATE TABLE v2_preflight_write_probe(value INTEGER)")  # makes the probe's CREATE fail
            db.commit()
        finally:
            db.close()
        self.assert_not_ready()
        store = Store(self.path)
        try:
            self.assertFalse(store.db.in_transaction)
            with self.assertRaises(sqlite3.OperationalError):
                persistent_write_probe(store.db, self.path)
            self.assertFalse(store.db.in_transaction)  # always rolled back
            self.assertEqual(store.get_state("economic_marker"), "unchanged")
        finally:
            store.close()

    def test_corrupt_db_is_not_ready(self):
        self.path.write_bytes(b"not a sqlite database" * 100)
        self.assert_not_ready()


if __name__ == "__main__":
    unittest.main()
