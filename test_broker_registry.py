"""Broker skeleton: account registry, emergency stop, read_control_snapshot and per-account isolation."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from execution import account_registry
from execution.account_registry import ACCOUNT_KEYS, AccountRegistry, AccountRunner, read_control_snapshot
from execution.broker_config import BrokerConfig, load_broker_config, mask_account
from execution.broker_port import BrokerGuardError

ENV = {}
SNAPSHOT_KEYS = {"readable", "emergency_stop", "connected", "accounts", "provider"}
ACCOUNT_FIELDS = {"key", "mode", "enabled", "label", "locked"}


class TempDbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state" / "broker_state.db"
        self.addCleanup(account_registry._EMERGENCY_LATCHES.pop, account_registry._latch_key(self.db), None)
        self.hook_calls = []
        self.registry = AccountRegistry(self.db, env=ENV, on_emergency=lambda **kw: self.hook_calls.append(kw))


class RegistryTests(TempDbCase):
    def test_default_off_and_construction_creates_nothing(self):
        self.assertFalse(self.db.exists())
        for key in ACCOUNT_KEYS:
            self.assertFalse(self.registry.is_allowed(key))
        self.assertFalse(self.db.exists())
        self.registry.initialize()
        con = sqlite3.connect(self.db)
        rows = con.execute("SELECT account_key, mode, enabled, shadow_only FROM broker_accounts ORDER BY 1").fetchall()
        con.close()
        self.assertEqual(rows, [("broker_demo", "broker_demo", 0, 1), ("paper", "paper", 0, 1), ("real", "real", 0, 1)])
        self.assertFalse(any(self.registry.is_allowed(key) for key in ACCOUNT_KEYS))
        self.assertFalse(self.registry.emergency_active())
        self.assertFalse(self.registry.is_allowed("unknown"))

    def test_enable_and_audit(self):
        self.registry.set_enabled("broker_demo", True, actor="owner", source="test")
        self.assertTrue(self.registry.is_allowed("broker_demo"))
        self.assertFalse(self.registry.is_allowed("paper"))
        self.registry.set_enabled("broker_demo", False, actor="owner", source="test")
        self.assertFalse(self.registry.is_allowed("broker_demo"))
        audit = [(key, field, old, new, actor) for _, key, field, old, new, actor, _, _ in self.registry.audit_log()]
        self.assertEqual(audit, [("broker_demo", "enabled", "0", "1", "owner"), ("broker_demo", "enabled", "1", "0", "owner")])
        con = sqlite3.connect(self.db)
        for sql in ("UPDATE broker_switches_audit SET actor = 'x'", "DELETE FROM broker_switches_audit"):
            with self.subTest(sql=sql), self.assertRaises(sqlite3.DatabaseError):
                con.execute(sql)
        con.close()
        self.assertEqual(len(self.registry.audit_log()), 2)

    def test_invalid_writes_refused(self):
        for args in (("nope", True), ("paper", 1), ("paper", "yes")):
            with self.subTest(args=args), self.assertRaises(BrokerGuardError):
                self.registry.set_enabled(*args, actor="owner")
        with self.assertRaises(BrokerGuardError):
            self.registry.set_enabled("paper", True, actor="")

    def test_real_is_locked(self):
        with self.assertRaises(BrokerGuardError):
            self.registry.set_enabled("real", True, actor="owner")
        registry = AccountRegistry(self.db, env={"AI_FLOOR_BROKER_REAL_ENABLED": "1"})
        with self.assertRaises(BrokerGuardError):
            registry.set_enabled("real", True, actor="owner")
        self.registry.set_enabled("real", False, actor="owner")
        con = sqlite3.connect(self.db)
        con.execute("UPDATE broker_accounts SET enabled = 1 WHERE account_key = 'real'")  # tampering
        con.commit()
        con.close()
        self.assertFalse(self.registry.is_allowed("real"))
        real = next(a for a in read_control_snapshot(self.db, env=ENV)["accounts"] if a["key"] == "real")
        self.assertEqual((real["enabled"], real["locked"]), (False, True))

    def test_emergency_stop_turns_everything_off_and_persists(self):
        self.registry.set_enabled("paper", True, actor="owner")
        self.registry.set_enabled("broker_demo", True, actor="owner")
        result = self.registry.emergency_stop(actor="owner", reason="drill")
        self.assertTrue(result.persisted and result.hook_ok)
        self.assertEqual(self.hook_calls, [{"reason": "drill"}])
        reopened = AccountRegistry(self.db, env=ENV)  # persistent across instances / restarts
        self.assertTrue(reopened.emergency_active())
        self.assertFalse(any(reopened.is_allowed(key) for key in ACCOUNT_KEYS))
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT max(enabled) FROM broker_accounts").fetchone(), (0,))
        con.close()
        with self.assertRaises(BrokerGuardError):
            reopened.set_enabled("paper", True, actor="owner")
        fields = [(key, field, new) for _, key, field, _, new, _, _, _ in reopened.audit_log()[-3:]]
        self.assertEqual(sorted(fields), [("ALL", "emergency_stop", "1"), ("broker_demo", "enabled", "0"),
                                          ("paper", "enabled", "0")])
        snapshot = read_control_snapshot(self.db, env=ENV)
        self.assertTrue(snapshot["emergency_stop"])
        reopened.clear_emergency(actor="owner", reason="drill over")
        self.assertFalse(reopened.emergency_active())
        self.assertFalse(reopened.is_allowed("paper"))  # clearing never re-enables a switch

    def test_emergency_env_flag_is_honoured(self):
        self.registry.set_enabled("broker_demo", True, actor="owner")
        for value in ("1", "true", "ON", "garbage"):
            with self.subTest(value=value):
                registry = AccountRegistry(self.db, env={"AI_FLOOR_EMERGENCY_STOP": value})
                self.assertTrue(registry.emergency_active())
                self.assertFalse(registry.is_allowed("broker_demo"))
                with self.assertRaises(BrokerGuardError):
                    registry.set_enabled("paper", True, actor="owner")
                self.assertTrue(read_control_snapshot(self.db, env={"AI_FLOOR_EMERGENCY_STOP": value})["emergency_stop"])
        self.assertTrue(AccountRegistry(self.db, env={"AI_FLOOR_EMERGENCY_STOP": "0"}).is_allowed("broker_demo"))

    def test_hook_failure_does_not_undo_the_stop(self):
        def boom(**_):
            raise RuntimeError("cannot close")
        registry = AccountRegistry(self.db, env=ENV, on_emergency=boom)
        registry.set_enabled("paper", True, actor="owner")
        self.assertEqual(registry.emergency_stop(actor="owner").hook_ok, False)
        self.assertTrue(registry.emergency_active())
        self.assertFalse(registry.is_allowed("paper"))

    def test_corrupt_db_fails_closed(self):
        self.db.parent.mkdir(parents=True)
        self.db.write_bytes(b"this is not a sqlite database" * 100)
        self.assertFalse(any(self.registry.is_allowed(key) for key in ACCOUNT_KEYS))
        self.assertTrue(self.registry.emergency_active())
        with self.assertRaises(Exception):
            self.registry.set_enabled("paper", True, actor="owner")
        with self.assertRaises(BrokerGuardError):
            self.registry.emergency_stop(actor="owner", reason="db broken")
        self.assertEqual(self.hook_calls, [{"reason": "db broken"}])  # hook still runs
        snapshot = read_control_snapshot(self.db, env=ENV)
        self.assertEqual((snapshot["readable"], snapshot["emergency_stop"]), (False, True))

    def test_unknown_values_and_missing_rows_are_off(self):
        self.registry.initialize()
        con = sqlite3.connect(self.db)
        con.execute("UPDATE broker_accounts SET enabled = 2 WHERE account_key = 'paper'")
        con.execute("UPDATE broker_accounts SET enabled = 'on' WHERE account_key = 'broker_demo'")
        con.commit()
        self.assertFalse(self.registry.is_allowed("paper"))
        self.assertFalse(self.registry.is_allowed("broker_demo"))
        con.execute("DELETE FROM broker_accounts")
        con.commit()
        self.assertFalse(any(self.registry.is_allowed(key) for key in ACCOUNT_KEYS))
        con.execute("DELETE FROM emergency_state")
        con.commit()
        con.close()
        self.assertTrue(self.registry.emergency_active())  # missing emergency row -> ON

    def test_experiment_db_is_refused(self):
        for path in ("data/runtime/trading_floor.db", Path(self.tmp.name) / "trial" / "trading_floor.db"):
            with self.subTest(path=path), self.assertRaises(BrokerGuardError):
                AccountRegistry(path, env=ENV)
        other = Path(self.tmp.name) / "exp.db"
        with self.assertRaises(BrokerGuardError):
            AccountRegistry(other, env={"AI_FLOOR_DB_PATH": str(other)})
        self.assertEqual(AccountRegistry(env=ENV).path, Path("data/runtime/broker_state.db"))
        snap = read_control_snapshot("data/runtime/trading_floor.db", env=ENV)
        self.assertFalse(snap["readable"])


class EmergencyLatchTests(TempDbCase):
    def raw(self, sql):
        con = sqlite3.connect(self.db)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()

    def failing_connect(self, seen):
        def connect(registry):
            seen.append((registry.local_latch_active(), registry.emergency_active(),
                         registry.is_allowed("broker_demo")))
            raise sqlite3.OperationalError("database is locked")
        return connect

    def test_latch_is_set_before_writing_and_honoured_when_persistence_fails(self):
        self.registry.set_enabled("broker_demo", True, actor="owner")
        self.registry.set_enabled("paper", True, actor="owner")
        seen = []
        with patch.object(AccountRegistry, "_connect", autospec=True, side_effect=self.failing_connect(seen)):
            with self.assertRaises(BrokerGuardError):
                self.registry.emergency_stop(actor="owner", reason="locked")
        self.assertEqual(seen, [(True, True, False)])  # latch already ON when the write was attempted
        self.assertEqual(self.hook_calls, [{"reason": "locked"}])
        # Nothing was persisted: the DB still says ON / no emergency ...
        self.assertEqual(self.raw("SELECT max(enabled) FROM broker_accounts"), [(1,)])
        self.assertEqual(self.raw("SELECT active FROM emergency_state"), [(0,)])
        # ... yet every authorisation check in this process honours the latch, across registry instances.
        for registry in (self.registry, AccountRegistry(self.db, env=ENV)):
            self.assertTrue(registry.local_latch_active())
            self.assertTrue(registry.emergency_active())
            self.assertFalse(any(registry.is_allowed(key) for key in ACCOUNT_KEYS))
            with self.assertRaises(BrokerGuardError):
                registry.set_enabled("paper", True, actor="owner")
        snapshot = read_control_snapshot(self.db, env=ENV)
        self.assertTrue(snapshot["readable"])
        self.assertTrue(snapshot["emergency_stop"])
        self.assertFalse(any(a["enabled"] for a in snapshot["accounts"]))
        self.registry.set_enabled("paper", False, actor="owner")  # turning OFF is still allowed

    def test_latch_survives_until_explicit_successful_recovery(self):
        self.registry.set_enabled("broker_demo", True, actor="owner")
        with patch.object(AccountRegistry, "_connect", autospec=True, side_effect=self.failing_connect([])):
            with self.assertRaises(BrokerGuardError):
                self.registry.emergency_stop(actor="owner", reason="locked")
            with self.assertRaises(sqlite3.OperationalError):
                self.registry.clear_emergency(actor="owner", reason="db still broken")
        self.assertTrue(self.registry.local_latch_active())  # failed recovery keeps the latch
        self.registry.clear_emergency(actor="owner", reason="db repaired")
        self.assertFalse(self.registry.local_latch_active())
        self.assertFalse(self.registry.emergency_active())
        self.assertEqual(self.raw("SELECT max(enabled) FROM broker_accounts"), [(0,)])  # recovery leaves all OFF
        self.assertFalse(self.registry.is_allowed("broker_demo"))
        last = [(key, field, new) for _, key, field, _, new, _, _, _ in self.registry.audit_log()[-2:]]
        self.assertEqual(last, [("broker_demo", "enabled", "0"), ("ALL", "emergency_stop", "0")])
        self.registry.set_enabled("broker_demo", True, actor="owner")
        self.assertTrue(self.registry.is_allowed("broker_demo"))

    def test_successful_stop_also_latches(self):
        self.registry.emergency_stop(actor="owner", reason="drill")
        self.assertTrue(self.registry.local_latch_active())
        other = Path(self.tmp.name) / "other" / "broker_state.db"
        self.addCleanup(account_registry._EMERGENCY_LATCHES.pop, account_registry._latch_key(other), None)
        self.assertFalse(AccountRegistry(other, env=ENV).local_latch_active())  # latch is per state DB


class SnapshotTests(TempDbCase):
    def assertShape(self, snapshot):
        self.assertEqual(set(snapshot), SNAPSHOT_KEYS)
        self.assertIs(snapshot["connected"], False)
        self.assertEqual([a["key"] for a in snapshot["accounts"]], list(ACCOUNT_KEYS))
        for account in snapshot["accounts"]:
            self.assertEqual(set(account), ACCOUNT_FIELDS)
            self.assertIsInstance(account["enabled"], bool)
            self.assertIsInstance(account["label"], str)
            self.assertEqual(account["locked"], account["key"] == "real")
        real = snapshot["accounts"][2]
        self.assertFalse(real["enabled"])

    def test_missing_file_is_unreadable_and_not_created(self):
        snapshot = read_control_snapshot(self.db, env=ENV)
        self.assertShape(snapshot)
        self.assertEqual((snapshot["readable"], snapshot["emergency_stop"]), (False, True))
        self.assertFalse(any(a["enabled"] for a in snapshot["accounts"]))
        self.assertFalse(self.db.exists())
        self.assertFalse(self.db.parent.exists())

    def test_default_and_enabled_state(self):
        self.registry.initialize()
        snapshot = read_control_snapshot(self.db, env=ENV)
        self.assertShape(snapshot)
        self.assertEqual((snapshot["readable"], snapshot["emergency_stop"]), (True, False))
        self.assertFalse(any(a["enabled"] for a in snapshot["accounts"]))
        self.registry.set_enabled("broker_demo", True, actor="owner")
        enabled = {a["key"]: a["enabled"] for a in read_control_snapshot(self.db, env=ENV)["accounts"]}
        self.assertEqual(enabled, {"paper": False, "broker_demo": True, "real": False})

    def test_never_raises_and_is_read_only(self):
        self.registry.set_enabled("paper", True, actor="owner")
        before = self.db.read_bytes()
        for path in (self.db, str(self.db), Path(self.tmp.name), "\0bad", 12345, object(), ""):
            with self.subTest(path=path):
                self.assertShape(read_control_snapshot(path, env=ENV))
        self.assertShape(read_control_snapshot(self.db, env=None))
        self.assertShape(read_control_snapshot(self.db, env=object()))  # broken env -> fail closed
        self.assertTrue(read_control_snapshot(self.db, env=object())["emergency_stop"])
        self.assertEqual(self.db.read_bytes(), before)
        self.assertEqual(sorted(p.name for p in self.db.parent.iterdir()), ["broker_state.db"])

    def test_provider_default_dxtrade_and_oanda_opt_in(self):
        self.registry.initialize()
        cases = {None: ("dxtrade", "Broker demo (OANDA)"), "oanda": ("oanda", "Broker demo (OANDA)"),
                 " OANDA ": ("oanda", "Broker demo (OANDA)"), "fxtrade": ("dxtrade", "Broker demo (OANDA)")}
        for raw, (provider, label) in cases.items():
            env = {} if raw is None else {"AI_FLOOR_BROKER_DEMO_PROVIDER": raw}
            with self.subTest(raw=raw):
                snapshot = read_control_snapshot(self.db, env=env)
                self.assertShape(snapshot)
                self.assertEqual(snapshot["provider"], provider)
                self.assertEqual(snapshot["accounts"][1]["label"], label)
                self.assertFalse(snapshot["accounts"][1]["enabled"])  # provider never enables anything
        self.assertEqual(read_control_snapshot(self.db, env=object())["provider"], "dxtrade")


class ConfigTests(unittest.TestCase):
    def test_safe_defaults(self):
        config = load_broker_config({})
        self.assertEqual((config.demo_enabled, config.emergency_stop, config.real_enabled), (False, False, False))
        self.assertEqual(config.base_url, "https://api.liquidcharts.com/dxsca-web/")
        self.assertEqual(config.state_db_path, Path("data/runtime/broker_state.db"))
        self.assertEqual(config.demo_provider, "dxtrade")
        self.assertEqual(load_broker_config({"AI_FLOOR_BROKER_DEMO_PROVIDER": "oanda"}).demo_provider, "oanda")
        self.assertEqual(load_broker_config({"AI_FLOOR_BROKER_DEMO_PROVIDER": "live"}).demo_provider, "dxtrade")

    def test_repr_redacts(self):
        config = load_broker_config({"DXTRADE_USERNAME": "u-123", "DXTRADE_PASSWORD": "p-456",
                                     "DXTRADE_DOMAIN": "dmn-Zq9-unique", "DXTRADE_ACCOUNT_CODE": "DEM_2775212_1"})
        self.assertEqual(config.domain, "dmn-Zq9-unique")
        for text in (repr(config), str(config), f"{config}"):
            for secret in ("u-123", "p-456", "dmn-Zq9-unique", "DEM_2775212_1"):
                self.assertNotIn(secret, text)
            self.assertIn("DEM_…12_1", text)
        self.assertEqual(mask_account(""), "")
        self.assertIsInstance(config, BrokerConfig)

    def test_real_flag_read_but_never_honoured(self):
        for value in ("1", "true", "yes"):
            config = load_broker_config({"AI_FLOOR_BROKER_REAL_ENABLED": value})
            self.assertTrue(config.real_requested)
            self.assertIs(config.real_enabled, False)


class IsolationTests(unittest.TestCase):
    def test_error_in_one_account_does_not_affect_the_other(self):
        runner = AccountRunner(failure_threshold=2)
        calls = []

        def demo():
            raise ConnectionError("password=hunter2")

        def paper():
            calls.append("paper")
            return "ok"

        first = runner.run_each({"broker_demo": demo, "paper": paper})
        self.assertEqual((first["broker_demo"].status, first["broker_demo"].error), ("ERROR", "ConnectionError"))
        self.assertNotIn("hunter2", repr(first["broker_demo"]))
        self.assertEqual((first["paper"].status, first["paper"].value), ("OK", "ok"))
        second = runner.run_each({"broker_demo": demo, "paper": paper})
        self.assertTrue(runner.is_open("broker_demo"))
        third = runner.run_each({"broker_demo": demo, "paper": paper})
        self.assertEqual((second["broker_demo"].status, third["broker_demo"].status), ("ERROR", "SKIPPED"))
        self.assertEqual(third["paper"].status, "OK")
        self.assertFalse(runner.is_open("paper"))
        self.assertEqual(calls, ["paper"] * 3)
        runner.reset("broker_demo")
        self.assertEqual(runner.run("broker_demo", lambda: 1).status, "OK")

    def test_success_resets_failure_count(self):
        runner = AccountRunner(failure_threshold=2)
        runner.run("a", lambda: 1 / 0)
        runner.run("a", lambda: None)
        runner.run("a", lambda: 1 / 0)
        self.assertFalse(runner.is_open("a"))


if __name__ == "__main__":
    unittest.main()
