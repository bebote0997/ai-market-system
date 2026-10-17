"""Dashboard Control view: fresh-disk registry initialisation (all OFF) and the missing-env hint. Offline, mocked."""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from execution.account_registry import ACCOUNT_KEYS, AccountRegistry, ensure_registry_initialized, read_control_snapshot
from execution.oanda_readonly import ERROR, OK, SIN_CUENTA, SIN_TOKEN, AccountView, account_view
from ui.views import control

PRACTICE_ACCOUNT = "101-000-1234567-001"
TOKEN = "0123456789abcdef0123456789abcdef-fedcba9876543210fedcba9876543210"


class EnsureRegistryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "state" / "broker_state.db"

    def test_creates_registry_with_every_switch_off_and_no_emergency(self):
        self.assertFalse(read_control_snapshot(self.db, env={})["readable"])  # the reader alone never creates it
        self.assertTrue(ensure_registry_initialized(self.db, env={}))
        snapshot = read_control_snapshot(self.db, env={})
        self.assertTrue(snapshot["readable"])
        self.assertFalse(snapshot["emergency_stop"])
        self.assertEqual([a["enabled"] for a in snapshot["accounts"]], [False] * 3)
        self.assertEqual([a["key"] for a in snapshot["accounts"]], list(ACCOUNT_KEYS))
        con = sqlite3.connect(self.db)
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM broker_switches_audit").fetchone()[0], 0)
        finally:
            con.close()

    def test_existing_registry_is_never_touched(self):
        registry = AccountRegistry(self.db, env={})
        registry.set_enabled("broker_demo", True, actor="test")
        before = self.db.read_bytes()
        self.assertFalse(ensure_registry_initialized(self.db, env={}))
        self.assertEqual(self.db.read_bytes(), before)
        self.assertTrue(registry.is_allowed("broker_demo"))

    def test_experiment_db_path_is_refused_and_nothing_is_created(self):
        experiment = Path(self.tmp.name) / "trading_floor.db"
        self.assertFalse(ensure_registry_initialized(experiment, env={}))
        self.assertFalse(experiment.exists())

    def test_created_file_is_owner_only_even_with_a_permissive_umask(self):
        old = os.umask(0o022)
        try:
            self.assertTrue(ensure_registry_initialized(self.db, env={}))
        finally:
            os.umask(old)
        self.assertEqual(self.db.stat().st_mode & 0o777, 0o600)

    def test_dangling_symlink_is_never_followed(self):
        self.db.parent.mkdir(parents=True)
        victim = Path(self.tmp.name) / "victim.db"
        self.db.symlink_to(victim)
        self.assertFalse(ensure_registry_initialized(self.db, env={}))
        self.assertFalse(victim.exists())

    def test_file_created_concurrently_is_left_alone(self):
        real_open = os.open

        def racing_open(path, flags, mode=0o777):
            if Path(path) == self.db:
                Path(path).write_bytes(b"other process")
            return real_open(path, flags, mode)

        with patch("execution.account_registry.os.open", side_effect=racing_open):
            self.assertFalse(ensure_registry_initialized(self.db, env={}))
        self.assertEqual(self.db.read_bytes(), b"other process")

    def test_failed_initialisation_removes_only_its_own_empty_file(self):
        with patch("execution.account_registry.AccountRegistry", side_effect=OSError("disk")):
            self.assertFalse(ensure_registry_initialized(self.db, env={}))
        self.assertFalse(self.db.exists())

    def test_never_raises(self):
        with patch("execution.account_registry.AccountRegistry", side_effect=OSError("disk")):
            self.assertFalse(ensure_registry_initialized(self.db, env={}))
        self.assertFalse(ensure_registry_initialized(object(), env={}))

    def test_first_routing_construction_still_sees_a_registry_without_audit_rows(self):
        ensure_registry_initialized(self.db, env={})
        self.assertEqual(AccountRegistry(self.db, env={}).audit_log(), [])


class ControlSnapshotTests(unittest.TestCase):
    def test_banner_condition_clears_on_a_fresh_disk(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "runtime" / "broker_state.db"
            with patch.dict(os.environ, {"AI_FLOOR_BROKER_STATE_DB_PATH": str(db)}):
                snapshot = control.control_snapshot()
            self.assertTrue(snapshot["readable"])
            self.assertFalse(snapshot["emergency_stop"])
            self.assertTrue(all(a["enabled"] is False for a in snapshot["accounts"]))
            real = next(a for a in snapshot["accounts"] if a["key"] == "real")
            self.assertTrue(real["locked"])

    def test_unreadable_existing_db_stays_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "broker_state.db"
            db.write_bytes(b"not a sqlite file")
            with patch.dict(os.environ, {"AI_FLOOR_BROKER_STATE_DB_PATH": str(db)}):
                snapshot = control.control_snapshot()
            self.assertFalse(snapshot["readable"])
            self.assertTrue(all(a["enabled"] is False for a in snapshot["accounts"]))
            self.assertEqual(db.read_bytes(), b"not a sqlite file")

    def test_initialisation_failure_falls_back_to_all_off(self):
        with patch("execution.account_registry.ensure_registry_initialized", side_effect=RuntimeError("x")), \
                tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"AI_FLOOR_BROKER_STATE_DB_PATH": str(Path(tmp) / "none.db")}):
            self.assertFalse(control.control_snapshot()["readable"])


class MissingEnvHintTests(unittest.TestCase):
    def lines(self, view):
        return dict(control.balance_lines(view))

    def test_sin_token_names_the_variable_never_a_value(self):
        for environment, name in (("practice", "OANDA_PRACTICE_TOKEN"), ("live", "OANDA_LIVE_TOKEN")):
            view = account_view(environment, env={})
            self.assertEqual(view.status, SIN_TOKEN)
            lines = self.lines(view)
            self.assertEqual(lines["Falta en el entorno"], name)
            self.assertEqual(lines["Estado"], "sin token")

    def test_hint_names_match_the_read_only_client_env_names(self):
        from execution.oanda_readonly import ENV_NAMES
        self.assertEqual(control.MISSING_ENV_NAMES, ENV_NAMES)

    def test_sin_cuenta_names_the_account_variable(self):
        view = account_view("practice", env={"OANDA_PRACTICE_TOKEN": TOKEN})
        self.assertEqual(view.status, SIN_CUENTA)
        self.assertEqual(self.lines(view)["Falta en el entorno"], "OANDA_ACCOUNT_ID")
        view = account_view("live", env={"OANDA_LIVE_TOKEN": TOKEN})
        self.assertEqual(self.lines(view)["Falta en el entorno"], "OANDA_LIVE_ACCOUNT_ID")

    def test_no_hint_when_connected_or_on_error_and_no_secret_in_text(self):
        for status in (OK, ERROR):
            view = AccountView(status, "practice")
            self.assertNotIn("Falta en el entorno", self.lines(view))
        text = repr(control.balance_lines(account_view("practice", env={"OANDA_PRACTICE_TOKEN": TOKEN,
                                                                         "OANDA_ACCOUNT_ID": PRACTICE_ACCOUNT},
                                                       transport=lambda *a, **k: (500, {}, b""))))
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(PRACTICE_ACCOUNT, text)

    def test_demo_balance_is_read_with_routing_off_and_all_switches_off(self):
        env = {"OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": PRACTICE_ACCOUNT}  # no routing/demo flags at all
        calls = []

        def transport(method, url, headers, body, timeout):
            calls.append((method, url))
            return 200, {}, (b'{"account": {"currency": "USD", "balance": "100000.0000", "NAV": "100000.0000", '
                             b'"marginUsed": "0.0000", "openTradeCount": 0, "unrealizedPL": "0.0000"}}')

        view = account_view("practice", env=env, transport=transport)
        self.assertEqual(view.status, OK)
        self.assertEqual([c[0] for c in calls], ["GET"])
        self.assertTrue(calls[0][1].startswith("https://api-fxpractice.oanda.com/v3/accounts/"))
        self.assertTrue(calls[0][1].endswith("/summary"))
        self.assertEqual(self.lines(view)["Estado"], "conectado")


if __name__ == "__main__":
    unittest.main()
