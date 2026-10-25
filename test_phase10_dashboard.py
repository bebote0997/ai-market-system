"""Phase 10: render real navigation over immutable, offline SQLite fixtures."""
import hashlib
import time
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, Mock, patch

from streamlit.testing.v1 import AppTest

from execution.contracts import PaperAccount
from storage.codec import paper_encode
from storage.database import Store
from storage.health_store import HealthStore
from ui.views.dashboard import history, records, ROW_LIMIT
from ui.state import dashboard_database, enabled_market_symbols
from runtime.config import DEFAULT_ENABLED_SYMBOLS
from ui.i18n_es import NO_DATA, page_key

APP = Path(__file__).parent / "ui" / "app.py"
# Relative to the real clock so the fixture never ages into STALE_DATA (fixed timestamp broke on 2026-10-11).
T = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat()


# The Control page reads the OANDA balance (GET summary) when a token is in the environment: never in tests.
_NO_BALANCE_NETWORK = patch("execution.oanda_readonly.urllib_transport",
                            side_effect=AssertionError("network must not be touched"))


def setUpModule():
    _NO_BALANCE_NETWORK.start()


def tearDownModule():
    _NO_BALANCE_NETWORK.stop()


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
        app.session_state["dashboard_auth_expires_at"] = time.monotonic() + 3600
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
        for page in [page_key(o) for o in app.radio[0].options]:
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
        services = app.dataframe[1].value.set_index("Servicio")["Estado"]
        self.assertEqual(services["Programador"], "DISABLED")
        self.assertEqual(services["Estado de la API"], "NO_DATA")
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
            for page in [page_key(o) for o in app.radio[0].options]:
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
            self.assertEqual(app.text_input[0].label, "Contraseña")
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
        self.assertTrue(any(item.value == NO_DATA for item in app.info))
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
        self.assertTrue(any(item.value == NO_DATA for item in app.info))
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
            self.assertEqual(app.text_input[0].label, "Contraseña")
            app.text_input[0].set_value("wrong-password").run()
            app.button[0].click().run()
            self.assertFalse(app.exception)
            self.assertFalse(app.radio)
        app.text_input[0].set_value("fixture-password").run()
        app.button[0].click().run()
        self.assert_rendered(app)
        self.assertTrue(app.radio)

    def test_phase9_ledger_and_v1_render_without_writes(self):
        from runtime.alert_dispatch import AlertDeliveryLedger
        path = Path(self.tmp.name) / "alerts.db"
        ledger = AlertDeliveryLedger(path)
        ledger.db.execute("INSERT INTO alert_deliveries "
                          "(event_id,channel,event_type,priority,status,attempts,error_type,claimed_at,recovery) "
                          "VALUES(?,?,?,?,?,?,?,?,?)",
                          ("phase9", "slack", "RUN_FAILED", "HIGH", "FAILED", 3,
                           "TIMEOUT", T, "AMBIGUOUS_RETRY"))
        ledger.close()
        before = path.read_bytes()
        with patch.dict(os.environ, {"AI_FLOOR_ALERTS_LEDGER_PATH": str(path)}):
            app = self.app()
            app.radio[0].set_value("ALERTS").run()
            self.assert_rendered(app)
            row = app.dataframe[0].value.iloc[0]
            self.assertEqual((row["channel"], row["attempts"], row["recovery"]),
                             ("slack", 3, "AMBIGUOUS_RETRY"))
            self.assertEqual(app.dataframe[1].value.iloc[0]["event_id"], "alert-1")
            with dashboard_database(path) as db:
                with self.assertRaises(sqlite3.OperationalError):
                    db.execute("DELETE FROM alert_deliveries")
        self.assertEqual(path.read_bytes(), before)

    def test_phase9_missing_unconfigured_and_empty_ledger(self):
        empty = Path(self.tmp.name) / "empty-ledger.db"
        sqlite3.connect(empty).close()
        missing = Path(self.tmp.name) / "missing-ledger.db"
        for path in ("", str(missing), str(empty)):
            with self.subTest(path=path), patch.dict(os.environ, {"AI_FLOOR_ALERTS_LEDGER_PATH": path}):
                app = self.app()
                app.radio[0].set_value("ALERTS").run()
                self.assert_rendered(app)
                self.assertTrue(any(item.value == NO_DATA for item in app.info))
                self.assertEqual(app.dataframe[0].value.iloc[0]["event_id"], "alert-1")
        self.assertFalse(missing.exists())

    def test_bounded_ordered_queries_and_sql_history_filters(self):
        with sqlite3.connect(self.path) as db:
            db.executemany("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                           "VALUES(?,?,?,?,?,?,?)",
                           [(T, f"bulk-{i}", "XAUUSD", "fixture", "TEST", "INFO", "{}")
                            for i in range(ROW_LIMIT + 10)])
        with dashboard_database() as db:
            queries = []
            db.set_trace_callback(queries.append)
            first = records(db, "journal")
            second = records(db, "journal", offset=ROW_LIMIT)
            self.assertEqual(len(first), ROW_LIMIT)
            self.assertEqual(first[0]["run_id"], "bulk-209")
            self.assertFalse({r["id"] for r in first} & {r["id"] for r in second})
            result = history(db, "run-1", "setup-1")
            self.assertIn("journal", {r["record"] for r in result})
            self.assertTrue(all("LIMIT 200 OFFSET" in q and "ORDER BY" in q
                                for q in queries if q.startswith("SELECT t.*")))
            self.assertTrue(any("json_tree" in q and "WHERE" in q for q in queries))
        app = self.app()
        app.radio[0].set_value("HISTORY").run()
        app.number_input(key="history_page").set_value(2).run()
        self.assert_rendered(app)
        self.assertEqual(app.dataframe[0].value.iloc[0]["run_id"], "bulk-9")

    def test_nested_identity_and_parent_run_outside_recent_page(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE setups SET plan=? WHERE slot_key='slot-1'",
                       (json.dumps({"nested": [{"setup_id": "nested-id"}], "text": "fake-id"}),))
            db.executemany("INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status) VALUES(?,?,?,?,?,?)",
                           [(f"new-{i}", f"new-run-{i}", "XAUUSD", T, T, "COMPLETED")
                            for i in range(ROW_LIMIT + 1)])
        with dashboard_database() as db:
            result = history(db, "run-1", "nested-id")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["run_id"], "run-1")
            self.assertFalse(history(db, setup_id="fake-id"))

    def test_missing_system_state_uses_defaults_and_app_renders(self):
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE system_state")
        self.assertEqual(enabled_market_symbols(), DEFAULT_ENABLED_SYMBOLS)
        self.assert_rendered(self.app())

    def test_positions_routes_to_command_center(self):
        self.assertFalse((APP.parent / "pages" / "positions.py").exists())
        app = self.app()
        app.radio[0].set_value("POSITIONS").run()
        self.assert_rendered(app)
        self.assertEqual(app.dataframe[0].value.iloc[0]["position_id"], "open")

    def test_sqlite_connection_rejects_writes(self):
        before = self.digest()
        with dashboard_database() as db:
            with self.assertRaises(sqlite3.OperationalError):
                db.execute("DELETE FROM runs")
        self.assertEqual(self.digest(), before)


if __name__ == "__main__":
    unittest.main()


class TestDashboardSqlFilters(unittest.TestCase):
    def test_history_renders_every_record_type_across_pages(self):
        from ui.views import dashboard
        record_types = ("runs", "setups", "review_reports", "paper_orders", "paper_fills",
                        "paper_positions", "closed_trades", "journal")
        with sqlite3.connect(":memory:") as db:
            db.row_factory = sqlite3.Row
            db.execute("CREATE TABLE runs(slot_key TEXT, run_id TEXT, symbol TEXT)")
            db.executemany("INSERT INTO runs VALUES(?,?,?)",
                           [(f"slot-{i}", f"run-{i}", "XAUUSD") for i in range(ROW_LIMIT + 1)])
            for record_type in record_types[1:]:
                db.execute(f"CREATE TABLE {record_type}(slot_key TEXT, payload TEXT)")
                db.executemany(f"INSERT INTO {record_type} VALUES(?,?)",
                               [(f"slot-{i}", json.dumps({"setup_id": f"setup-{i}"}))
                                for i in range(ROW_LIMIT + 1)])

            def rendered(page, run_id="", setup_id=""):
                st = MagicMock()
                st.text_input.side_effect = [run_id, setup_id]
                st.number_input.return_value = page
                with patch.object(dashboard, "dashboard_database") as database:
                    database.return_value.__enter__.return_value = db
                    dashboard.render(st, "HISTORY")
                st.error.assert_not_called()
                return [call.args[0] for call in st.dataframe.call_args_list]

            first, second = rendered(1), rendered(2)
            for pages, count in ((first, ROW_LIMIT), (second, 1)):
                self.assertEqual(len(pages), len(record_types))
                for record_type, rows in zip(record_types, pages):
                    self.assertEqual(len(rows), count)
                    self.assertEqual({row["record"] for row in rows}, {record_type})
            for first_rows, second_rows in zip(first, second):
                first_ids = {row["run_id"] for row in first_rows}
                second_ids = {row["run_id"] for row in second_rows}
                self.assertFalse(first_ids & second_ids)
                self.assertEqual(first_ids | second_ids,
                                 {f"run-{i}" for i in range(ROW_LIMIT + 1)})
            filtered = rendered(1, "run-0", "setup-0")
            self.assertEqual(len(filtered), len(record_types) - 1)
            self.assertTrue(all(len(rows) == 1 and rows[0]["run_id"] == "run-0"
                                for rows in filtered))
            self.assertEqual(rendered(1, "run-0", "setup-1"), [])
            self.assertEqual(rendered(3), [])

    def test_status_and_error_filters_precede_limit(self):
        for name, columns, filters, expected, parameters in (
                ("paper_positions", ("position_id", "payload"), {"statuses": ("OPEN",)},
                 "json_extract(t.payload, '$.status') IN (?)", ("OPEN",)),
                ("paper_orders", ("order_id", "payload"), {"statuses": ("PENDING",)},
                 "json_extract(t.payload, '$.status') IN (?)", ("PENDING",)),
                ("journal", ("timestamp", "severity"), {"statuses": ("ERROR", "CRITICAL")},
                 't."severity" IN (?,?)', ("ERROR", "CRITICAL")),
                ("runs", ("status", "error"), {"failed": True},
                 "(t.status='FAILED' OR COALESCE(t.error, '') <> '')", ())):
            with self.subTest(table=name):
                db = Mock()
                db.execute.side_effect = [Mock(fetchone=lambda: (1,)),
                                          [(n, column) for n, column in enumerate(columns)], []]
                self.assertEqual(records(db, name, **filters), [])
                sql, params = db.execute.call_args.args
                self.assertIn(" WHERE " + expected, sql)
                self.assertLess(sql.index(expected), sql.index("LIMIT"))
                self.assertEqual(params, (*parameters, ROW_LIMIT, 0))

    def test_t10_render_catches_database_read_error(self):
        from ui.views import dashboard
        st = Mock()
        with patch.object(dashboard, "dashboard_database") as database:
            database.return_value.__enter__.return_value.execute.side_effect = sqlite3.OperationalError("read")
            dashboard.render(st, "POSITIONS")
        st.error.assert_called_once_with(
            "No se pudieron leer los datos guardados (OperationalError).")

    def test_positions_and_errors_request_sql_filters(self):
        from ui.views import dashboard
        st = Mock()
        with patch.object(dashboard, "dashboard_database"), patch.object(
                dashboard, "payloads", return_value=[]) as payloads:
            dashboard.render(st, "POSITIONS")
        self.assertEqual([call.kwargs for call in payloads.call_args_list],
                         [{"statuses": ("OPEN",)}, {"statuses": ("PENDING",)}])
        with patch.object(dashboard, "dashboard_database"), patch.object(
                dashboard, "records", return_value=[]) as query:
            dashboard.render(st, "ERRORS")
        self.assertEqual([call.kwargs for call in query.call_args_list],
                         [{"failed": True}, {"statuses": ("ERROR", "CRITICAL")}])
