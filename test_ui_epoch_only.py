"""OANDA demo screens: epoch-only data, real numbers, readable Spanish (feat/floor-ui-fixes chunk 2).

Pre-epoch PAPER cycles stay in the DB but no screen shows them in demo mode ("Esperando el primer ciclo"); the
"Día N de 30" counter starts at the epoch's New York date; XAUUSD reads "pausado por el planificador"; the Setups
page is a real list. Synthetic data only; no network."""
from datetime import datetime, timedelta, timezone
import json
import os
import sqlite3
import sys
import unittest
from unittest.mock import patch

from storage.database import Store
from ui import i18n_es as es
from ui.views import control, dashboard, luxury, setups

UTC = timezone.utc
EPOCH = datetime(2026, 10, 25, 15, 30, 20, tzinfo=UTC)
BEFORE, AFTER = "2026-10-22T20:45:00+00:00", "2026-10-26T07:00:00+00:00"


class EpochFilterTests(unittest.TestCase):
    def test_since_epoch_keeps_only_rows_at_or_after_the_epoch(self):
        rows = [{"as_of": BEFORE}, {"as_of": AFTER}, {"as_of": None}, {"as_of": "garbage"},
                {"timestamp": AFTER}, {"payload": json.dumps({"opened_at": AFTER})}, {"as_of": EPOCH.isoformat()}]
        self.assertEqual(dashboard.since_epoch(rows, EPOCH), [rows[1], rows[4], rows[5], rows[6]])
        self.assertEqual(dashboard.since_epoch(rows, None), rows)  # PAPER mode: unchanged
        self.assertEqual(dashboard.since_epoch(rows, EPOCH, ("as_of",)), [rows[1], rows[6]])

    def test_demo_since_follows_the_results_flag_and_never_leaks_on_a_bad_epoch(self):
        with patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO", "AI_FLOOR_DEMO_EPOCH": EPOCH.isoformat()}):
            self.assertEqual(dashboard.demo_since(), EPOCH)
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AI_FLOOR_RESULTS_SOURCE", None)
            os.environ.pop("AI_FLOOR_BROKER_DEMO_ROUTING", None)
            self.assertIsNone(dashboard.demo_since())
        self.assertEqual(dashboard.epoch_or_far(lambda: 1 / 0), dashboard.FAR_FUTURE)
        self.assertEqual(dashboard.epoch_or_far(lambda: datetime(2026, 10, 25)), dashboard.FAR_FUTURE)  # naive

    def test_unavailable_demo_epoch_filter_fails_closed(self):
        with patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO"}), \
                patch('execution.demo_epoch.demo_epoch', side_effect=RuntimeError):
            since = luxury.demo_since(luxury.demo_unavailable_api())
            self.assertEqual(since, dashboard.FAR_FUTURE)
            self.assertEqual(dashboard.since_epoch([{"as_of": AFTER}], since), [])
        self.assertFalse(dashboard.is_current(AFTER, dashboard.FAR_FUTURE))

    def test_history_hides_pre_epoch_runs_and_events(self):
        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        db.executescript("CREATE TABLE runs(slot_key TEXT, run_id TEXT, symbol TEXT, as_of TEXT, started_at TEXT);"
                         "CREATE TABLE journal(id INTEGER PRIMARY KEY, timestamp TEXT, run_id TEXT, symbol TEXT,"
                         " source TEXT, event_type TEXT, severity TEXT, payload TEXT);"
                         "CREATE TABLE setups(slot_key TEXT, status TEXT, plan TEXT);")
        db.execute("INSERT INTO runs VALUES('old','r-old','EURUSD',?,?)", (BEFORE, BEFORE))
        db.execute("INSERT INTO runs VALUES('new','r-new','EURUSD',?,?)", (AFTER, AFTER))
        db.execute("INSERT INTO setups VALUES('old','VALID_SETUP','{}'), ('new','WATCH','{}')")
        for stamp, event in ((BEFORE, "MANAGE_SKIPPED"), (AFTER, "PLAN_CREATED")):
            db.execute("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                       "VALUES(?,?,?,?,?,?,?)", (stamp, "r", "EURUSD", "engine", event, "INFO", "{}"))
        rows = dashboard.history(db, since=EPOCH)
        self.assertEqual([r["run_id"] for r in rows if r["record"] == "runs"], ["r-new"])
        self.assertEqual([r["event_type"] for r in rows if r["record"] == "journal"], ["PLAN_CREATED"])
        self.assertEqual([r["slot_key"] for r in rows if r["record"] == "setups"], ["new"])
        self.assertEqual(len(dashboard.history(db)), 6)  # PAPER mode: everything

    def test_cycle_times_share_one_rule_and_heartbeat_stays_live(self):
        states = {"last_success": BEFORE, "last_run": AFTER, "heartbeat": BEFORE}
        self.assertEqual(luxury.cycle_time(states, "last_success", EPOCH), "Esperando el primer ciclo")
        self.assertEqual(luxury.cycle_time(states, "last_run", EPOCH), "26 oct 2026 03:00 ET")
        self.assertEqual(luxury.cycle_time(states, "last_success"), "22 oct 2026 16:45 ET")  # PAPER mode
        self.assertEqual(luxury.cycle_time({}, "last_run", EPOCH), "Esperando el primer ciclo")


class DayAndNumbersTests(unittest.TestCase):
    @patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO", "AI_FLOOR_DEMO_EPOCH": EPOCH.isoformat()})
    def test_demo_day_counts_from_the_epoch_new_york_date(self):
        demo = {"epoch": lambda: EPOCH}
        self.assertEqual(luxury.demo_day(demo, datetime(2026, 10, 25, 16, tzinfo=UTC)), 1)
        self.assertEqual(luxury.demo_day(demo, datetime(2026, 10, 26, 7, tzinfo=UTC)), 2)  # Monday 03:00 ET
        self.assertEqual(luxury.demo_day(demo, datetime(2026, 11, 24, 17, tzinfo=UTC)), 31)  # uncapped
        self.assertIsNone(luxury.demo_day(demo, datetime(2026, 10, 25, 15, tzinfo=UTC)))
        with patch("execution.demo_epoch.demo_epoch", side_effect=RuntimeError):
            self.assertIsNone(luxury.demo_day(demo, datetime(2026, 10, 26, tzinfo=UTC)))

    def test_unavailable_demo_day_uses_authoritative_epoch_or_waiting_state(self):
        now = datetime(2026, 10, 26, 7, tzinfo=UTC)
        with patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO",
                                     "AI_FLOOR_DEMO_EPOCH": EPOCH.isoformat()}), \
                patch.dict(sys.modules, {"execution.demo_dashboard": None}):
            demo = luxury.demo_api()
            self.assertNotEqual(demo["epoch"](), EPOCH)
            day = luxury.demo_day(demo, now)
            self.assertEqual(day, 2)
            self.assertIn("Día 2 de 30", luxury.hero_subline(demo, day))
            with patch("execution.demo_epoch.demo_epoch", side_effect=RuntimeError):
                day = luxury.demo_day(demo, now)
                self.assertIsNone(day)
                self.assertIn(dashboard.FIRST_CYCLE_WAIT, luxury.hero_subline(demo, day))
                self.assertNotIn("Día", luxury.hero_subline(demo, day))

    def snapshot(self, **extra):
        stats = {"trades": 0, "wins": 0, "total_pnl": 0.0, "today_pnl": 0.0, "max_drawdown": 0.0, "avg_r": None,
                 "avg_r_reason": None}
        return {"epoch": EPOCH, "stats": stats, "curve": [], "open_rows": [], "closed_rows": [], "nav": 100000.01,
                "balance": 100000.01, "currency": "USD", "capital": {"start": 100000.0, "change": 0.01},
                "stale": False, "trades_error": None, **extra}

    def test_no_trades_yet_is_explicit_never_blank(self):
        html = "".join(luxury.demo_kpis(self.snapshot()))
        self.assertEqual(html.count("sin operaciones aún"), 3)  # win rate, avg R, max drawdown
        for text in ("NAV (equity) · OANDA demo", "$100,000.01", "Balance · OANDA demo", "P&amp;L no realizado",
                     "P&amp;L de hoy (ET)", "Posiciones abiertas", "Operaciones cerradas", "$0.00"):
            self.assertIn(text, html)
        self.assertNotIn('<div class="value "></div>', html)

    def test_numbers_with_trades_and_open_positions(self):
        stats = {"trades": 2, "wins": 1, "total_pnl": 30.0, "today_pnl": -10.0, "max_drawdown": 10.0, "avg_r": 0.5,
                 "avg_r_reason": None}
        html = "".join(luxury.demo_kpis(self.snapshot(stats=stats, open_rows=[{"unrealized_pnl": 4.5},
                                                                             {"unrealized_pnl": -1.25}])))
        for text in ("+$30.00", "−$10.00", "+$3.25", "50%", "+0.50 R", "$10.00", "Acierto · 1/2 operaciones cerradas"):
            self.assertIn(text, html)
        self.assertNotIn("sin operaciones aún", html)
        self.assertIsNone(luxury.unrealized_total([{"unrealized_pnl": None}]))

    def test_failed_read_says_why(self):
        html = "".join(luxury.demo_kpis(self.snapshot(stats=None, trades_error="deadline", nav=None)))
        self.assertIn("sin datos (OANDA tardó demasiado en responder)", html)
        self.assertNotIn("sin operaciones aún", html)

    def test_archive_note_never_says_paper(self):
        self.assertEqual(luxury.DEMO_ARCHIVE_NOTE, "Los datos de la práctica anterior están archivados y no cuentan aquí.")
        self.assertNotIn("PAPER", dashboard.ARCHIVED_NOTE)


class SpanishTextTests(unittest.TestCase):
    def test_codes_become_readable(self):
        self.assertEqual(es.status_text("DEC14_DECISION"), "Decisión ante setups opuestos")
        self.assertEqual(es.status_text("CONFIGURED"), "Configurado")
        self.assertEqual(es.status_text("VALID_SETUP"), "Setup válido")
        self.assertEqual(es.status_text("SOME_NEW_CODE"), "Some new code")
        self.assertEqual(es.status_text("XAUUSD"), "XAUUSD")  # symbols are not codes
        self.assertEqual(es.status_text("Plain prose stays"), "Plain prose stays")

    def test_cycle_labels_and_short_ids(self):
        self.assertEqual(es.cycle_label(AFTER, "EURUSD"), "Ciclo del 26 oct 03:00 ET · EURUSD")
        self.assertEqual(es.cycle_label(None), "Ciclo sin hora guardada")
        self.assertEqual(es.short_id("9c92d1b6-6305-524a-9193-5d302210c37f"), "9c92d1b6…")

    def test_xauusd_is_paused_by_the_scheduler(self):
        self.assertEqual(es.symbol_status("XAUUSD", ("XAUUSD", "EURUSD")), "pausado por el planificador")
        self.assertEqual(es.symbol_status("EURUSD", ("XAUUSD", "EURUSD")), "activo")
        self.assertEqual(es.enabled_caption(("XAUUSD", "EURUSD")), "XAUUSD · pausado por el planificador, EURUSD")

    def test_daily_loss_card_in_spanish(self):
        self.assertEqual(es.DAILY_LOSS_TITLE, "Límite de pérdida diaria")
        self.assertEqual(es.daily_loss_rows({"status": "OK", "fraction": 0.02, "loss": 0.0, "used_pct": 0.0,
                                             "day_start_nav": 100000.0}),
                         [("Estado", "Bien"), ("Pérdida del día (realizada + no realizada)", "$0.00"),
                          ("Límite usado (2 %)", "0 %"), ("NAV al inicio del día (17:00 ET)", "$100,000.00"),
                          ("Freno por caída máxima (5 %)", "Bien")])


class ControlConsistencyTests(unittest.TestCase):
    ACCOUNT = {"key": "broker_demo", "enabled": True, "mode": "DEMO", "label": "Broker demo (OANDA)"}

    def test_demo_row_matches_routing_chip_and_balance_read(self):
        snapshot = {"connected": False}
        self.assertEqual(control.account_status(self.ACCOUNT, snapshot, "on", True)[0],
                         "Conectado · activo (enrutado encendido, saldo leído)")
        self.assertEqual(control.account_status(self.ACCOUNT, snapshot, "off", True)[0],
                         "Conectado (saldo leído) · enrutado apagado")
        self.assertIn("no se pudo leer el saldo", control.account_status(self.ACCOUNT, snapshot, "on", False)[0])
        self.assertEqual(control.account_status(self.ACCOUNT, snapshot)[0], "Saldo no disponible · enrutado apagado")
        real = {"key": "real", "enabled": False, "locked": True}
        self.assertEqual(control.account_status(real, snapshot, "on", True)[0], "Real: bloqueado por seguridad")

    def test_router_events_before_the_epoch_are_hidden_and_readable(self):
        events = [{"ts": BEFORE, "kind": "MANAGE_SKIPPED"}, {"ts": AFTER, "kind": "POSITION_OPENED"}]
        self.assertEqual(control.current_events(events, EPOCH), [("26 oct 2026 03:00 ET", "Posición abierta")])
        self.assertEqual(len(control.current_events(events)), 2)


class SetupsListTests(unittest.TestCase):
    ROWS = [{"as_of": AFTER, "symbol": "EURUSD", "run_id": "r-1", "setup_id": "s-1", "setup_status": "VALID_SETUP",
             "plan": {"side": "LONG", "entry": 1.1, "stop": 1.099, "target": 1.103, "risk_reward": 3}, "setup": {}},
            {"as_of": AFTER, "symbol": "EURUSD", "run_id": "r-2", "setup_id": None, "setup_status": "PLAN_READY",
             "plan": {}, "setup": {"side": "SHORT"}}]

    def test_table_rows(self):
        rows = setups.setup_table_rows(self.ROWS, {"r-1"})
        self.assertEqual(rows[0], {"as_of": AFTER, "symbol": "EURUSD", "side": "LONG", "setup_status": "VALID_SETUP",
                                   "levels": "1.1 / 1.099 / 1.103", "risk_reward": "1 : 3", "traded": "Sí"})
        self.assertEqual((rows[1]["side"], rows[1]["levels"], rows[1]["traded"]), ("SHORT", "— / — / —", "No"))
        self.assertEqual(setups.setup_table_rows(self.ROWS, None)[0]["traded"], "sin datos")

    def test_traded_ids_from_the_demo_trades(self):
        snapshot = {"stats": {}, "open_rows": [{"client_id": "aif-r-1"}], "closed_rows": [{"client_id": "aif-test-x"},
                                                                                           {"client_id": None}]}
        self.assertEqual(setups.traded_ids(None, snapshot), {"r-1", "test-x"})
        self.assertIsNone(setups.traded_ids(None, {"stats": None}))


class DemoPagesTests(unittest.TestCase):
    """App pages in demo mode over the PAPER fixture DB, whose rows are all older than the epoch."""

    def setUp(self):
        import test_demo_from_zero as tz
        self.case = tz.DashboardTests("test_routing_on_overview_is_demo_only")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.trades = {"closed": [], "open": []}
        self.case.trades = self.trades
        self.db = self.case.case.path
        store = Store(self.db)
        stamp = (datetime.now(UTC) - timedelta(minutes=1)).replace(microsecond=0).isoformat()
        store.save_snapshot("EURUSD", {"schema_version": "1.0", "symbol": "EURUSD", "run_id": "run-2",
                                      "state": "VALID_SETUP", "as_of": stamp, "freshness": "CURRENT",
                                      "setup_status": "VALID_SETUP"})
        store.set_state("last_success", stamp)
        store.close()
        self.epoch = (datetime.now(UTC) - timedelta(seconds=20)).isoformat()  # after every fixture row

    def page(self, name, **session):
        with self.case.env(AI_FLOOR_DEMO_EPOCH=self.epoch):
            app = self.case.case.app()
            for key, value in session.items():
                app.session_state[key] = value
            app.radio[0].set_value(name).run()
            self.assertFalse(app.exception, str(app.exception))
            return app, "\n".join(m.value for m in app.markdown)

    def test_la_mesa_waits_for_the_first_cycle_and_xauusd_is_paused(self):
        _app, content = self.page("The Floor")
        self.assertIn("Esperando el primer ciclo", content)
        self.assertIn("XAUUSD · pausado por el planificador", content)
        self.assertNotIn("Setup válido", content)  # the stale pre-epoch EURUSD snapshot
        self.assertNotIn("En observación", content)  # the stale XAUUSD snapshot

    def test_process_ignores_pre_epoch_runs_and_deep_links(self):
        app, content = self.page("Process", process_run_id="run-1")
        self.assertIn("Esperando el primer ciclo", content)
        self.assertNotIn("aún no guardado", content)  # no 9 empty step cards
        self.assertFalse([s for s in app.selectbox if s.label == "Ciclo · el más reciente primero"])

    def test_setups_decisions_and_overview_wait(self):
        for page in ("Setups", "Decisions"):
            with self.subTest(page=page):
                _app, content = self.page(page)
                self.assertIn("Esperando el primer ciclo", content)
        _app, content = self.page("Overview")
        self.assertIn("Último ciclo exitoso", content)
        self.assertIn("Esperando el primer ciclo", content)
        self.assertIn("Día 1 de 30", content)
        self.assertEqual(content.count("sin operaciones aún"), 3)

    def test_unavailable_demo_module_uses_configured_epoch_for_all_cycle_pages(self):
        self.assertGreater(datetime.fromisoformat(self.epoch), luxury.DEMO_DEFAULT_EPOCH)
        with patch.dict(sys.modules, {'execution.demo_dashboard': None}):
            with self.case.env(AI_FLOOR_DEMO_EPOCH=self.epoch):
                demo = luxury.demo_api()
                self.assertEqual(demo['epoch'](), luxury.DEMO_DEFAULT_EPOCH)
                self.assertEqual(luxury.demo_since(demo), datetime.fromisoformat(self.epoch))
            for page in ('The Floor', 'Process', 'Setups', 'Decisions'):
                with self.subTest(page=page):
                    app, content = self.page(page, process_run_id='run-1')
                    self.assertIn('Esperando el primer ciclo', content)
                    self.assertNotIn('Setup válido', content)
                    self.assertNotIn('En observación', content)
                    self.assertFalse([s for s in app.selectbox if s.label == 'Ciclo · el más reciente primero'])

    def test_legacy_pages_hide_pre_epoch_rows(self):
        for page in ("HISTORY", "ERRORS", "DECISIONS"):
            with self.subTest(page=page):
                app, _content = self.page(page)
                self.assertFalse(app.dataframe)
                self.assertNotIn("RUN_FAILED", "\n".join(str(i.value) for i in app.info))


if __name__ == "__main__":
    unittest.main()
