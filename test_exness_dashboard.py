"""Dashboard presentation when AI_FLOOR_BROKER=exness: labels, brake card, and the order clock.

Read-only. Synthetic flags and a fake balance. No network, no flag writes, no launch reset.
"""
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from execution.exness_brakes import ExnessBrakes, SqliteFlags
from ui import i18n_es as es
from execution.exness_broker import REAL_CONFIRM, REAL_CONFIRM_ENV, REAL_FLAG
from execution.session_combo import FIX_SYMBOLS, LONDON_SYMBOL
from ui.exness_present import (
    ACTIVE_BADGE, LABEL, OFF_SAFETY, WAITING, combo_caption, combo_symbols, panel_html, session_clock,
    show_experiment_market, usd_equivalent)
from ui.nav_menu import chrome
from ui.views.luxury import hero_subline

UTC = timezone.utc
CUTOFF = "2026-11-14T21:00:00Z"  # 16:00 ET on the launch day
BEFORE = datetime(2026, 11, 15, 16, tzinfo=UTC)  # the next calendar day, still no Exness order
ORDER = "2026-11-16T15:30:00+00:00"  # 10:30 ET, first fill
AFTER = datetime(2026, 11, 16, 18, tzinfo=UTC)
NEXT = datetime(2026, 11, 17, 18, tzinfo=UTC)
EARLY = "2026-11-14T18:00:00+00:00"  # before the cutoff


def _flags(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE flags (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        con.executemany("INSERT INTO flags(key, value) VALUES (?, ?)", rows)


def _order(opened, status="filled", client="combo-1"):
    return ('{"client_id": "%s", "opened_at": "%s", "position_id": "pos-1", "status": "%s"}'
            % (client, opened, status))


class ExnessDashboardTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "exness.db"
        self.env = {
            "AI_FLOOR_BROKER": "exness",
            "AI_FLOOR_EXNESS_STATE_DB_PATH": str(self.db),
            "AI_FLOOR_EXNESS_RISK_CAP": "300",
            "AI_FLOOR_EXNESS_DAILY_LOSS_USD": "600",
            "AI_FLOOR_EXNESS_MAX_DRAWDOWN_USD": "3000",
        }
        self.reader = lambda _env: {"balance": "30086", "equity": "30086", "currency": "USC"}

    def _patch(self):
        return patch.dict(os.environ, self.env, clear=False)

    def _seed(self, extra=()):
        rows = [
            ("exness_launch_start", CUTOFF),
            ("exness_brake_start", "30086"),
            ("exness_brake_day_equity", "30086"),
            ("combo_fix_2026-11-14", _order(EARLY, status="filled", client="old")),
            ("combo_lon_2026-11-14", '{"status": "armed", "opened_at": null}'),
            ("combo_fix_2026-11-13", '{"status": "done", "opened_at": "%s"}' % EARLY),
        ]
        _flags(self.db, rows + list(extra))

    def test_labels_switch_only_for_exness(self):
        self._seed()
        with self._patch():
            line = hero_subline(True, 4)
            rail = chrome(None)
            safety = es.safety_label()
        self.assertIn(LABEL, line)
        self.assertIn(WAITING, line)
        self.assertNotIn("OANDA", line)
        self.assertIn(LABEL, rail)
        self.assertNotIn("OANDA", rail)
        self.assertIn(LABEL, safety)
        self.assertNotIn("OANDA", safety)
        with patch.dict(os.environ, {"AI_FLOOR_BROKER": "oanda"}, clear=False):
            self.assertEqual(hero_subline(True, 3), "V2.1 · OANDA Demo · Día 3 de 30")

    def test_execution_badge_follows_the_adapter_arming_check(self):
        off = f"{LABEL} · {OFF_SAFETY}"
        observe = "Solo observación · no envía órdenes reales"
        cases = (
            ({REAL_FLAG: "", REAL_CONFIRM_ENV: ""}, off, observe),
            ({REAL_FLAG: "1", REAL_CONFIRM_ENV: ""}, off, observe),
            ({REAL_FLAG: "", REAL_CONFIRM_ENV: REAL_CONFIRM}, off, observe),
            ({REAL_FLAG: "true", REAL_CONFIRM_ENV: REAL_CONFIRM}, off, observe),
            ({REAL_FLAG: "1", REAL_CONFIRM_ENV: "yes"}, off, observe),
            ({REAL_FLAG: "1", REAL_CONFIRM_ENV: REAL_CONFIRM}, ACTIVE_BADGE, ACTIVE_BADGE),
        )
        for extra, safety, watch in cases:
            with self.subTest(extra=extra), self._patch(), patch.dict(os.environ, extra, clear=False):
                self.assertEqual(es.safety_label(), safety)
                self.assertEqual(es.tr("Observation only · no live orders"), watch)
                self.assertEqual(es.tr("PAPER · REAL EXECUTION DISABLED"), safety)
        with patch.dict(os.environ, {"AI_FLOOR_BROKER": "oanda", REAL_FLAG: "1",
                                     REAL_CONFIRM_ENV: REAL_CONFIRM}, clear=False):
            self.assertIn(OFF_SAFETY, es.safety_label())
            self.assertNotIn(ACTIVE_BADGE, es.safety_label())

    def test_sidebar_lists_session_combo_symbols_and_hides_the_experiment_market(self):
        self.assertFalse(show_experiment_market(self.env))
        self.assertTrue(show_experiment_market({"AI_FLOOR_BROKER": "oanda"}))
        caption = combo_caption()
        self.assertEqual(combo_symbols(), (LONDON_SYMBOL, *FIX_SYMBOLS))
        self.assertEqual(caption, "Estrategia 1 (session combo) · pares: " + ", ".join(combo_symbols()))
        self.assertNotIn("Activos en el experimento", caption)
        self.assertNotIn("Mercado", caption)
        with patch("execution.session_combo.LONDON_SYMBOL", "ZZZUSD"), \
                patch("execution.session_combo.FIX_SYMBOLS", ("AAAUSD", "BBBUSD")):
            shown = combo_caption()
        self.assertIn("ZZZUSD, AAAUSD, BBBUSD", shown)
        self.assertNotIn("USDJPY", shown)

    def test_brake_panel_reads_caps_and_stored_state_without_writing(self):
        self._seed()
        before = self.db.read_bytes()
        with self._patch(), patch.object(ExnessBrakes, "evaluate", side_effect=AssertionError("evaluate")), \
                patch.object(SqliteFlags, "set_flag", side_effect=AssertionError("set_flag")), \
                patch("execution.launch_reset.apply_launch_reset", side_effect=AssertionError("reset")):
            html = panel_html(self.env, now=BEFORE, reader=self.reader)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertIn(LABEL, html)
        self.assertIn("30,086.00 USC", html)
        self.assertIn("300.86 USD", html)
        self.assertIn("300.00 USC", html)
        self.assertIn("600.00 USC", html)
        self.assertIn("3,000.00 USC", html)
        self.assertIn(">no</span>", html)
        self.assertIn("ninguno", html)
        self.assertNotIn("OANDA", html)
        self.assertNotIn("pos-1", html)

    def test_counter_waits_until_the_first_real_order_after_the_cutoff(self):
        self._seed()
        with self._patch():
            waiting = session_clock(self.env, now=BEFORE)
            html = panel_html(self.env, now=BEFORE, reader=self.reader)
        self.assertEqual(waiting, {"waiting": True, "day": 0, "trades": 0, "first_at": None})
        self.assertIn(WAITING, html)
        self.assertIn("Día 0", html)
        self.assertIn("0 operaciones", html)
        self.assertNotIn("Día 2", html)

    def test_counter_starts_at_the_first_real_fill(self):
        self._seed(extra=[("combo_lon_2026-11-16", _order(ORDER))])
        with self._patch():
            clock = session_clock(self.env, now=AFTER)
            later = session_clock(self.env, now=NEXT)
        self.assertFalse(clock["waiting"])
        self.assertEqual(clock["day"], 1)
        self.assertEqual(clock["trades"], 1)
        self.assertEqual(clock["first_at"], datetime(2026, 11, 16, 15, 30, tzinfo=UTC))
        self.assertEqual((later["day"], later["trades"]), (2, 1))
        with self._patch():
            from runtime.notifications import oanda_demo_summary_text
            from ui.exness_present import set_account_reader
            set_account_reader(self.reader)
            self.addCleanup(lambda: set_account_reader(None))
            text = oanda_demo_summary_text(sent_at=AFTER)
        self.assertIn(LABEL, text)
        self.assertIn("Día 1 de 30 · 1 operación", text)
        self.assertIn("Operaciones: 1", text)
        self.assertIn("300.86 USD", text)
        self.assertNotIn("OANDA", text)

    def test_floor_overlay_names_exness_and_drops_practice_rows(self):
        self._seed()
        payload = {"source": "OANDA_DEMO", "header": {"day_text": "Día 2 de 30", "equity": 1},
                   "trades": [{"status": "OPEN"}], "curve": [1], "epoch_text": "14 nov",
                   "agents": {"Exec": {"status": "Posición abierta"}}}
        with self._patch():
            from ui.exness_present import floor_overlay
            shown = floor_overlay(payload, now=BEFORE, env=self.env, reader=self.reader)
        self.assertEqual(shown["broker_label"], LABEL)
        self.assertEqual(shown["trades"], [])
        self.assertIsNone(shown["curve"])
        self.assertIsNone(shown["agents"]["Exec"])
        self.assertIn(WAITING, shown["header"]["day_text"])
        self.assertEqual(shown["header"]["equity"], 30086.0)
        self.assertEqual(usd_equivalent(Decimal("30086"), "USC"), Decimal("300.86"))


class NoExnessCallsFromTheDashboardTests(unittest.TestCase):
    """The dashboard is its own process. It must not open an Exness client: that client would not share the
    trading process rate limiter (candles/snapshot 2/s) and could make the trading reads HTTP 429."""

    setUp = ExnessDashboardTests.setUp
    _seed = ExnessDashboardTests._seed
    _patch = ExnessDashboardTests._patch

    def _no_network(self):
        boom = AssertionError("network")
        return (patch("execution.exness_client.ExnessClient.__init__", side_effect=boom),
                patch("execution.exness_live.urllib_caller", side_effect=boom),
                patch("urllib.request.urlopen", side_effect=boom),
                patch("socket.create_connection", side_effect=boom))

    def test_render_paths_make_no_exness_call_and_show_the_stored_day_base(self):
        self._seed()
        guards = self._no_network()
        with self._patch(), guards[0], guards[1], guards[2], guards[3]:
            from ui.exness_present import floor_overlay, read_account, summary_lines
            account = read_account(self.env)
            html = panel_html(self.env, now=BEFORE)
            lines = summary_lines(self.env, now=BEFORE)
            shown = floor_overlay({"header": {}}, now=BEFORE, env=self.env)
        self.assertTrue(account["stored"])
        self.assertEqual((account["equity"], account["balance"], account["currency"]), (Decimal("30086"), None, "USC"))
        self.assertIn("Equity (base del día, 17:00 ET)", html)
        self.assertIn("30,086.00 USC · 300.86 USD", html)
        self.assertIn("no se consulta en vivo", html)
        self.assertIn("Equity (base del día, 17:00 ET): 30,086.00 USC · 300.86 USD", lines)
        self.assertEqual(shown["header"]["equity"], 30086.0)
        self.assertTrue(shown["header"]["equity_stale"])
        self.assertIsNone(shown["header"]["balance"])

    def test_without_a_stored_day_base_the_panel_says_no_data_and_still_calls_nothing(self):
        guards = self._no_network()
        with self._patch(), guards[0], guards[1], guards[2], guards[3]:
            html = panel_html(self.env, now=BEFORE)
        self.assertIn("sin datos", html)

    def test_the_module_has_no_live_account_reader(self):
        import ui.exness_present as present
        self.assertFalse(hasattr(present, "_live_account"))
        source = Path(present.__file__).read_text(encoding="utf-8")
        self.assertNotIn("ExnessClient", source)
        self.assertNotIn("urllib_caller", source)
