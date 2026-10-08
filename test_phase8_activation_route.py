"""V2 Phase 8 / P8.4 R1 + R2: explicit catch-up activation route and fail-closed evidence preflight.
Everything runs on temporary paths; nothing is activated outside these tests."""
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from execution.trade_manager import TradeManager
from runtime.cloud import cloud_preflight
from runtime.config import RuntimeConfig, catch_up_storage_checks
from runtime.demo_runner import preflight
from runtime.service import OperationalRuntime
from storage.evidence_store import EvidenceStore
from test_phase8_catch_up_certification import SLOT, Harness
from test_runtime_catch_up import Bars, make_runtime

ENV_KEYS = ("AI_FLOOR_V2_POSITION_CATCH_UP", "AI_FLOOR_MARKET_EVIDENCE_PATH", "AI_FLOOR_DB_PATH")


def clean_env(**values):
    base = {k: "" for k in ENV_KEYS}
    base.update(values)
    return patch.dict("os.environ", base)


class R1ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p84-cfg-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_default_and_explicit_off(self):
        self.assertIs(RuntimeConfig().v2_position_catch_up, False)
        for value in ("", "0"):
            with clean_env(AI_FLOOR_V2_POSITION_CATCH_UP=value):
                config = RuntimeConfig.from_env()
            self.assertEqual((config.v2_position_catch_up, config.market_evidence_path), (False, None))

    def test_only_the_exact_value_1_with_a_separate_path_enables(self):
        db, evidence = self.dir / "trading_floor.db", self.dir / "market_evidence.db"
        with clean_env(AI_FLOOR_V2_POSITION_CATCH_UP="1", AI_FLOOR_MARKET_EVIDENCE_PATH=str(evidence),
                       AI_FLOOR_DB_PATH=str(db)):
            config = RuntimeConfig.from_env()
        self.assertEqual((config.v2_position_catch_up, config.market_evidence_path), (True, evidence))
        self.assertNotEqual(config.fingerprint(), replace(config, v2_position_catch_up=False,
                                                          market_evidence_path=None).fingerprint())

    def test_accidental_or_ambiguous_activation_fails_closed(self):
        db = self.dir / "trading_floor.db"
        for value in ("true", "yes", "on", "2", " 1", "1 ", "TRUE"):
            with self.subTest(value=value), clean_env(AI_FLOOR_V2_POSITION_CATCH_UP=value,
                                                      AI_FLOOR_MARKET_EVIDENCE_PATH=str(self.dir / "e.db")):
                with self.assertRaises(ValueError):
                    RuntimeConfig.from_env()
        with clean_env(AI_FLOOR_V2_POSITION_CATCH_UP="1"):  # no evidence path
            with self.assertRaises(ValueError):
                RuntimeConfig.from_env()
        with clean_env(AI_FLOOR_V2_POSITION_CATCH_UP="1", AI_FLOOR_MARKET_EVIDENCE_PATH=str(db),
                       AI_FLOOR_DB_PATH=str(db)):  # evidence path == trading DB
            with self.assertRaises(ValueError):
                RuntimeConfig.from_env()
        with clean_env(AI_FLOOR_MARKET_EVIDENCE_PATH=str(self.dir / "e.db")):  # a path alone never enables
            self.assertIs(RuntimeConfig.from_env().v2_position_catch_up, False)

    def test_other_flags_still_have_no_env_route(self):
        with clean_env(AI_FLOOR_V2_AI_CALL_AUDIT="1", AI_FLOOR_V2_AI_RESILIENCE="1"):
            config = RuntimeConfig.from_env()
        self.assertEqual((config.v2_ai_call_audit, config.v2_ai_resilience), (False, False))


class R2PreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p84-pre-")
        self.addCleanup(self.tmp.cleanup)
        self.mount = Path(self.tmp.name) / "disk"
        self.mount.mkdir()

    def config(self, evidence, flag=True):
        return RuntimeConfig(db_path=self.mount / "trading_floor.db", enabled_symbols=("XAUUSD", "EURUSD"),
                             market_provider_mode="twelve_data", ai_provider_mode="openai",
                             v2_position_catch_up=flag, market_evidence_path=evidence if flag else None)

    def test_off_adds_no_checks(self):
        self.assertEqual(catch_up_storage_checks(self.config(None, flag=False), mount=self.mount), {})
        env = {"AI_FLOOR_DURABLE_MOUNT": str(self.mount), "AI_FLOOR_DASHBOARD_PASSWORD": "x"}
        report = cloud_preflight(self.config(None, flag=False), env=env, disk_mounted=True)
        self.assertFalse([k for k in report.checks if k.startswith("catch_up_")])

    def test_matrix(self):
        valid = self.mount / "market_evidence.db"
        existing = self.mount / "existing_evidence.db"
        EvidenceStore(existing).close()
        garbage = self.mount / "garbage_evidence.db"
        garbage.write_bytes(b"not a database at all")
        outside = Path(self.tmp.name) / "outside_evidence.db"
        missing_parent = self.mount / "nope" / "market_evidence.db"
        cases = {  # path -> expected checks
            "absent_on_mount": (valid, {"catch_up_evidence_path_set": True, "catch_up_evidence_separate": True,
                                        "catch_up_evidence_durable": True,
                                        "catch_up_evidence_writable_schema": True}),
            "existing_valid": (existing, {"catch_up_evidence_writable_schema": True}),
            "garbage_file": (garbage, {"catch_up_evidence_writable_schema": False}),
            "outside_mount": (outside, {"catch_up_evidence_durable": False}),
            "missing_parent": (missing_parent, {"catch_up_evidence_writable_schema": False}),
        }
        for name, (path, expected) in cases.items():
            with self.subTest(case=name):
                checks = catch_up_storage_checks(self.config(path), mount=self.mount, disk_mounted=True)
                for key, value in expected.items():
                    self.assertIs(checks[key], value, (name, key, checks))
        self.assertFalse(valid.exists())  # the preflight never creates the store
        stub = SimpleNamespace(v2_position_catch_up=True, market_evidence_path=None,
                               db_path=self.mount / "trading_floor.db")
        self.assertFalse(catch_up_storage_checks(stub, mount=self.mount)["catch_up_evidence_path_set"])
        same_name = SimpleNamespace(v2_position_catch_up=True, db_path=self.mount / "trading_floor.db",
                                    market_evidence_path=self.mount / "other" / "trading_floor.db")
        self.assertFalse(catch_up_storage_checks(same_name, mount=self.mount)["catch_up_evidence_separate"])
        self.assertFalse(catch_up_storage_checks(self.config(valid), mount=self.mount,
                                               disk_mounted=False)["catch_up_evidence_durable"])

    def test_read_only_evidence_store_fails_and_probe_leaves_no_residue(self):
        """P8.5 HIGH regression: the former TEMP-table probe passed a read-only Evidence DB."""
        import os
        import sqlite3
        import stat
        path = self.mount / "market_evidence.db"
        EvidenceStore(path).close()

        def schema():
            db = sqlite3.connect(path)
            try:
                return (sorted(r[0] for r in db.execute("SELECT name FROM sqlite_master")),
                        db.execute("SELECT COUNT(*) FROM market_evidence").fetchone()[0],
                        db.execute("PRAGMA integrity_check").fetchone()[0])
            finally:
                db.close()
        before = schema()
        self.assertTrue(catch_up_storage_checks(self.config(path), mount=self.mount)["catch_up_evidence_writable_schema"])
        self.assertEqual(schema(), before)  # persistent probe rolled back: no table, no row, integrity ok
        os.chmod(path, stat.S_IREAD)
        self.addCleanup(os.chmod, path, stat.S_IREAD | stat.S_IWRITE)
        checks = catch_up_storage_checks(self.config(path), mount=self.mount)
        self.assertIs(checks["catch_up_evidence_writable_schema"], False)
        env = {"AI_FLOOR_DURABLE_MOUNT": str(self.mount), "AI_FLOOR_DASHBOARD_PASSWORD": "x"}
        self.assertFalse(cloud_preflight(self.config(path), env=env, disk_mounted=True).infra_ready)

    def test_cloud_preflight_is_not_ready_on_any_evidence_failure(self):
        env = {"AI_FLOOR_DURABLE_MOUNT": str(self.mount), "AI_FLOOR_DASHBOARD_PASSWORD": "x"}
        good = cloud_preflight(self.config(self.mount / "market_evidence.db"), env=env, disk_mounted=True)
        self.assertTrue(good.infra_ready, good.checks)
        bad = cloud_preflight(self.config(Path(self.tmp.name) / "outside.db"), env=env, disk_mounted=True)
        self.assertFalse(bad.infra_ready)
        self.assertEqual(bad.status, "NOT_READY")

    def test_local_doctor_applies_the_same_checks(self):
        report = preflight(self.config(self.mount / "nope" / "e.db"), env={})
        self.assertFalse(report.checks["catch_up_evidence_writable_schema"])
        self.assertEqual(report.status, "NOT_READY")
        off = preflight(self.config(None, flag=False), env={})
        self.assertFalse([k for k in off.checks if k.startswith("catch_up_")])


class NoSilentDegradationTests(Harness):
    def test_unusable_evidence_store_never_falls_back_to_newest_bar(self):
        self.seed()
        self.ev.mkdir()  # a directory: the Evidence Store cannot open
        seen, original = [], TradeManager.process_bar

        def spy(manager, bar):
            seen.append(bar["timestamp"])
            return original(manager, bar)
        with patch.object(TradeManager, "process_bar", spy):
            self.cycle(overrides={("XAUUSD", SLOT): (100.0, 100.5, 94.0, 96.0)})
        account, _, _, journal = self.paper()
        self.assertEqual(seen, [])  # no TradeManager call at all: fail closed, no V1 newest-bar fallback
        self.assertIn("EVIDENCE_UNAVAILABLE", [e[0] for e in journal])
        self.assertIn("XAUUSD", account.open_positions)

    def test_env_built_config_runs_only_isolated_and_restarts_cleanly(self):
        self.seed()
        overrides = {("XAUUSD", SLOT.replace(minute=15)): (100.0, 100.5, 94.0, 96.0)}
        with clean_env(AI_FLOOR_V2_POSITION_CATCH_UP="1", AI_FLOOR_MARKET_EVIDENCE_PATH=str(self.ev),
                       AI_FLOOR_DB_PATH=str(self.db)):
            config = RuntimeConfig.from_env()
        self.assertTrue(config.v2_position_catch_up)
        from ai.provider import DeterministicAIProvider
        from test_demo_runner import instrument, macro_fixture
        for expected in (None, "DUPLICATE"):
            runtime = OperationalRuntime(config, market_provider=Bars(overrides), ai_provider=DeterministicAIProvider(),
                                         macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                                         clock=lambda: SLOT)
            try:
                status = runtime.run_cycle("XAUUSD", SLOT)
            finally:
                runtime.close()
            if expected:
                self.assertEqual(status, expected)
        self.assert_matches_oracle(overrides)


if __name__ == "__main__":
    unittest.main()
