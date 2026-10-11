"""Phase 10: render real navigation over immutable, offline SQLite fixtures."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from execution.contracts import PaperAccount
from storage.codec import paper_encode
from storage.database import Store
from storage.health_store import HealthStore
from ui.pages.dashboard import history
from ui.state import dashboard_database

APP = Path(__file__).parent / "ui" / "app.py"
T = "2026-10-11T12:00:00+00:00"


class Phase10DashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "floor.db"
        store = Store(self.path)
        self.addCleanup(store.close)
        for number, symbol in ((1, "XAUUSD"), (2, "EURUSD")):
            run_id, slot = f"run-{number}", f"slot-{number}"
            store.db.execute(
                "INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status,error) VALUES(?,?,?,?,?,?,?)",
                (slot, run_id, symbol, T, T, "FAILED" if number == 2 else "COMPLETED",
                 "TIMEOUT" if number == 2 else None))
            store.db.execute("INSERT INTO setups(slot_key,status,evidence,plan) VALUES(?,?,?,?)",
                             (slot, "WATCH", "[]", json.dumps({"setup_id": f"setup-{number}"})))
            store.db.execute("INSERT INTO agent_decisions(slot_key,agent_name,as_of,status) VALUES(?,?,?,?)",
                             (slot, "Structure AI", T, "OK"))
            store.db.execute("INSERT INTO risk_decisions(slot_key,status,reason) VALUES(?,?,?)",
                             (slot, "REJECTED", "fixture only"))
            store.db.execute(
                "INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) VALUES(?,?,?,?,?,?,?)",
                (T, run_id, symbol, "fixture", "RUN_FAILED", "ERROR", json.dumps({"setup_id": f"setup-{number}"})))
        store.db.execute("INSERT INTO paper_accounts VALUES(?,?)",
                         ("paper-main", paper_encode(PaperAccount("1.0", "paper-main", 10000, 10020, 10025, 20, 5))))
        store.db.execute("INSERT INTO paper_orders VALUES(?,?)", ("pending", json.dumps({
            "order_id": "pending", "run_id": "run-1", "symbol": "XAUUSD", "side": "LONG",
            "schema_version": "1.0", "quantity": 1, "planned_entry": 100, "stop": 99, "target": 103,
            "status": "PENDING", "setup_id": "setup-1"})))
        store.db.execute("INSERT INTO paper_positions VALUES(?,?)", ("open", json.dumps({
            "position_id": "open", "origin_order_id": "pending", "run_id": "run-1", "symbol": "XAUUSD",
            "side": "LONG", "schema_version": "1.0", "quantity": 1, "planned_entry": 100,
            "entry_price": 100, "stop": 99, "target": 103, "opened_at": T, "status": "OPEN",
            "setup_id": "setup-1"})))
        store.db.execute("INSERT INTO notification_events VALUES(?,?,?)", ("alert-1", 1, json.dumps({
            "timestamp_utc": T, "type": "RUN_FAILED", "severity": "ERROR", "run_id": "run-1"})))
        store.db.execute("INSERT INTO notification_deliveries VALUES(?,?,?,?,?)",
                         ("alert-1", "FAILED", T, T, "TIMEOUT"))
        store.set_state("scheduler", "DISABLED")
        store.set_state("api_status", "NO_DATA")
        store.save_snapshot("XAUUSD", {"schema_version": "1.0", "symbol": "XAUUSD",
                                      "run_id": "run-1", "state": "WATCH", "as_of": T,
                                      "freshness": "CURRENT", "setup_status": "WATCH"})
        store.close()
        self.health_path = Path(self.tmp.name) / "health.db"
        health_store = HealthStore(self.health_path)
        health_store.db.execute("INSERT INTO component_health VALUES(?,?,?)", (
            "market_data", "fixture", json.dumps({"status": "DEGRADED", "error_type": "TIMEOUT"})))
        health_store.close()
        env = patch.dict(os.environ, {"AI_FLOOR_DB_PATH": str(self.path),
                                    "AI_FLOOR_HEALTH_DB_PATH": str(self.health_path),
                                    "AI_FLOOR_REMOTE_DASHBOARD": "1", "RENDER": "false",
                                    "AI_FLOOR_DASHBOARD_PASSWORD": "fixture-password"})
        env.start()
        self.addCleanup(env.stop)

    def app(self):
        app = AppTest.from_file(str(APP), default_timeout=15)
        app.session_state["dashboard_authenticated"] = True
        app.session_state["sample_mode"] = False
        return app.run()

    def digest(self):
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def assert_rendered(self, app):
        self.assertFalse(app.exception, str(app.exception))
        self.assertFalse(app.error, str(app.error))

    def test_every_page_renders_and_database_hash_unchanged(self):
        before = self.digest()
        health_before = hashlib.sha256(self.health_path.read_bytes()).hexdigest()
        app = self.app()
        for page in app.radio[0].options:
            with self.subTest(page=page):
                app.radio[0].set_value(page).run()
                self.assert_rendered(app)
                self.assertEqual(self.digest(), before)
                self.assertEqual(hashlib.sha256(self.health_path.read_bytes()).hexdigest(), health_before)
        app.radio[0].set_value("OVERVIEW").run()
        self.assertEqual([metric.value for metric in app.metric], ["10025", "20", "5"])
        app.radio[0].set_value("POSITIONS").run()
        self.assertEqual(app.dataframe[0].value.iloc[0]["position_id"], "open")
        self.assertEqual(app.dataframe[1].value.iloc[0]["order_id"], "pending")
        app.radio[0].set_value("ALERTS").run()
        self.assertEqual(app.dataframe[0].value.iloc[0]["status"], "FAILED")
        app.radio[0].set_value("HEALTH").run()
        self.assertEqual(app.dataframe[0].value.iloc[0]["status"], "DEGRADED")
        services = app.dataframe[1].value.set_index("service")["status"]
        self.assertEqual(services["scheduler"], "DISABLED")
        self.assertEqual(services["api_status"], "NO_DATA")
        app.radio[0].set_value("SETUPS").run()
        self.assertEqual(set(app.dataframe[0].value["symbol"]), {"XAUUSD", "EURUSD"})
        app.radio[0].set_value("DECISIONS").run()
        self.assertEqual(set(app.dataframe[0].value["agent_name"]), {"Structure AI"})
        self.assertEqual(set(app.dataframe[1].value["status"]), {"REJECTED"})
        app.radio[0].set_value("ERRORS").run()
        self.assertEqual(list(app.dataframe[0].value["run_id"]), ["run-2"])

    def test_empty_fixture_every_page_renders(self):
        empty = Path(self.tmp.name) / "empty.db"
        Store(empty).close()
        before = hashlib.sha256(empty.read_bytes()).hexdigest()
        with patch.dict(os.environ, {"AI_FLOOR_DB_PATH": str(empty)}):
            app = self.app()
            for page in app.radio[0].options:
                with self.subTest(page=page):
                    app.radio[0].set_value(page).run()
                    self.assert_rendered(app)
            self.assertEqual(hashlib.sha256(empty.read_bytes()).hexdigest(), before)

    def test_mobile_layout_rules_and_local_password_gate(self):
        from ui import theme
        import inspect
        self.assertIn("@media(max-width:640px)", inspect.getsource(theme.apply))
        with patch.dict(os.environ, {"AI_FLOOR_REMOTE_DASHBOARD": "0"}):
            app = AppTest.from_file(str(APP)).run()
            self.assertEqual(app.text_input[0].label, "Password")
            self.assertFalse(app.radio)

    def test_search_run_id_setup_id_and_combination(self):
        before = self.digest()
        app = self.app()
        app.radio[0].set_value("HISTORY").run()
        app.text_input(key="history_run_id").set_value("run-1").run()
        self.assert_rendered(app)
        self.assertTrue((app.dataframe[0].value["run_id"] == "run-1").all())
        app.text_input(key="history_run_id").set_value("").run()
        app.text_input(key="history_setup_id").set_value("setup-2").run()
        self.assertTrue((app.dataframe[0].value["run_id"] == "run-2").all())
        app.text_input(key="history_run_id").set_value("run-1").run()
        self.assertTrue(any(item.value == "NO_DATA" for item in app.info))
        with dashboard_database() as db:
            self.assertFalse(history(db, "run-1' OR 1=1 --"))
        self.assertEqual(self.digest(), before)

    def test_missing_delivery_table_and_missing_database_show_no_data(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE notification_deliveries")
        before = self.digest()
        app = self.app()
        app.radio[0].set_value("ALERTS").run()
        self.assert_rendered(app)
        self.assertTrue(any(item.value == "NO_DATA" for item in app.info))
        self.assertEqual(self.digest(), before)
        missing = Path(self.tmp.name) / "missing.db"
        with patch.dict(os.environ, {"AI_FLOOR_DB_PATH": str(missing)}):
            app = self.app()
            for page in ("OVERVIEW", "POSITIONS", "SETUPS", "DECISIONS", "HEALTH", "ALERTS", "HISTORY", "ERRORS"):
                app.radio[0].set_value(page).run()
                self.assert_rendered(app)
                self.assertTrue(app.info)
            self.assertFalse(missing.exists())

    def test_password_gate_precedes_database_access(self):
        with patch("ui.state.sqlite3.connect", side_effect=AssertionError("DB read before authentication")):
            app = AppTest.from_file(str(APP)).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.text_input[0].label, "Password")
            app.text_input[0].set_value("wrong-password").run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.radio)
        app.text_input[0].set_value("fixture-password").run()
        app.button[0].click().run()
        self.assert_rendered(app)
        self.assertTrue(app.radio)

    def test_sqlite_connection_rejects_writes(self):
        before = self.digest()
        with dashboard_database() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM runs")
        self.assertEqual(self.digest(), before)


if __name__ == "__main__":
    unittest.main()
