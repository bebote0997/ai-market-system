"""Demo dashboard additions (feat/ops-diag-fixes): daily-loss status vs the 2 % limit (from the router's persisted
flags + OANDA NAV), R per routed trade ("—" without an initial stop) and the "Día N de 30" counter (day 1 = NY date
of the first routed trade for the Slack summary; the screens count from the demo EPOCH, ``ui.demo_clock``; capped
"30 de 30 – completo"). Synthetic data only; no network."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from execution import daily_loss_limit as dl
from execution.broker_demo_state import DemoStateStore
from execution.demo_metrics import daily_loss_view, demo_day, load_loss_flags
from ui import i18n_es as es

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
NOW = datetime(2026, 10, 19, 18, 0, tzinfo=UTC)  # Monday 14:00 ET
KEY = dl.day_key(NOW)
ON = {"AI_FLOOR_DAILY_LOSS_LIMIT": "1"}


class DayCounterTests(unittest.TestCase):
    FIRST = datetime(2026, 10, 19, 8, 1, tzinfo=ET)  # owner: first routed trade 2026-10-19 08:01 ET = day 1

    def day(self, *local):
        return demo_day(self.FIRST, datetime(*local, tzinfo=ET), length=None)

    def test_ny_calendar_days_weekends_included(self):
        self.assertEqual(self.day(2026, 10, 19, 8, 1), 1)
        self.assertEqual(self.day(2026, 10, 19, 23, 59), 1)
        self.assertEqual(self.day(2026, 10, 20, 0, 0), 2)
        self.assertEqual(self.day(2026, 10, 25, 12, 0), 7)  # Sunday counts
        self.assertEqual(self.day(2026, 11, 17, 12, 0), 30)  # across the DST change (Nov 1)
        self.assertEqual(self.day(2026, 11, 18, 12, 0), 31)
        self.assertEqual(demo_day(self.FIRST, datetime(2026, 11, 18, 12, tzinfo=ET)), 30)  # default stays capped
        self.assertIsNone(demo_day(None, NOW, length=None))

    def test_text(self):
        self.assertEqual(es.demo_day_text(1), "Día 1 de 30")
        self.assertEqual(es.demo_day_text(30), "Día 30 de 30")
        self.assertEqual(es.demo_day_text(31), "Día 30 de 30 – completo")
        self.assertEqual(es.demo_day_text(400), "Día 30 de 30 – completo")
        for missing in (None, 0, True, "3"):
            self.assertEqual(es.demo_day_text(missing), es.DEMO_NO_TRADES)

    def test_experiment_counts_from_the_epoch_in_et(self):
        from ui.views.experiment import demo_progress_text
        env = {"AI_FLOOR_BROKER_DEMO_ROUTING": "1", "AI_FLOOR_DEMO_EPOCH": "2026-10-25T15:30:20Z"}
        prefix = "Cuenta de práctica OANDA Demo desde 25 oct 2026 11:30 ET; "
        with patch.dict(os.environ, env), \
                patch("execution.demo_dashboard.demo_progress", side_effect=AssertionError("first-trade rule")):
            self.assertEqual(demo_progress_text(datetime(2026, 10, 25, 16, tzinfo=UTC)), prefix + "Día 1 de 30")
            self.assertEqual(demo_progress_text(datetime(2026, 10, 26, 7, tzinfo=UTC)), prefix + "Día 2 de 30")
            self.assertEqual(demo_progress_text(datetime(2026, 11, 23, 17, tzinfo=UTC)), prefix + "Día 30 de 30")
            self.assertEqual(demo_progress_text(datetime(2026, 11, 24, 17, tzinfo=UTC)),
                             prefix + "Día 30 de 30 – completo")
            self.assertEqual(demo_progress_text(datetime(2026, 10, 25, 15, tzinfo=UTC)),
                             prefix + "Esperando el primer ciclo")  # before the epoch: no day yet

    def test_slack_summary_day_line(self):
        from runtime.notifications import oanda_demo_summary_text
        snapshot = {"stats": {"trades": 0, "wins": 0, "avg_r": None, "avg_r_reason": None, "today_pnl": 0.0},
                    "nav": 100000.0, "currency": "USD", "capital": {"change": 0.0}, "open_rows": []}
        with patch("execution.demo_dashboard.demo_snapshot", return_value=snapshot), \
                patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 31)):
            self.assertIn("\nDía 30 de 30 – completo\n", oanda_demo_summary_text("2026-11-18T22:00:00Z"))
        with patch("execution.demo_dashboard.demo_snapshot", return_value=snapshot), \
                patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 2)):
            self.assertIn("\nDía 2 de 30\n", oanda_demo_summary_text("2026-10-20T22:00:00Z"))

    def test_slack_summary_stale_account_is_not_current(self):
        from datetime import datetime, timezone
        from runtime.notifications import oanda_demo_summary_text
        sent = datetime(2026, 10, 20, 21, 5, tzinfo=timezone.utc)
        snapshot = {"stats": None, "nav": 100000.0, "balance": 99000.0, "currency": "USD",
                    "capital": {"change": 50.0}, "stale": True, "as_of": datetime(2026, 10, 19, 14, 30, tzinfo=timezone.utc)}
        with patch("execution.demo_dashboard.demo_snapshot", return_value=snapshot), \
                patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 2)):
            text = oanda_demo_summary_text("2026-10-20T21:00:00Z", sent_at=sent)
        self.assertIn("NAV (a las 2026-10-19 10:30 ET, dato antiguo): 100,000.00 USD", text)
        self.assertIn("Balance (a las 2026-10-19 10:30 ET, dato antiguo): 99,000.00 USD", text)
        self.assertIn("P&L desde el inicio del demo (a las 2026-10-19 10:30 ET, dato antiguo): +50.00 USD", text)
        self.assertNotIn("17:05 ET", text)
        with patch("execution.demo_dashboard.demo_snapshot", return_value={**snapshot, "as_of": None}), \
                patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 2)):
            text = oanda_demo_summary_text("2026-10-20T21:00:00Z", sent_at=sent)
        self.assertIn("NAV (a las 17:05 ET): sin datos", text)
        self.assertIn("Balance (a las 17:05 ET): sin datos", text)
        self.assertIn("P&L desde el inicio del demo (a las 17:05 ET): sin datos", text)
        self.assertNotIn("100,000", text)


class DailyLossViewTests(unittest.TestCase):
    def flags(self, **extra):
        return {dl.DAY_FLAG: KEY, dl.E0_FLAG: "100000", dl.TRANSFERS_FLAG: "0", **extra}

    def test_off_unless_enabled(self):
        view = daily_loss_view(self.flags(), 99000, NOW, {})
        self.assertEqual((view["status"], view["used_pct"]), ("OFF", None))
        self.assertEqual(es.daily_loss_rows(view), [("Estado", "Apagado")])

    def test_ok_with_percent_used(self):
        view = daily_loss_view(self.flags(), 99500.0, NOW, ON)  # -500 of a 2 000 limit
        self.assertEqual((view["status"], view["loss"], view["day_start_nav"]), ("OK", 500.0, 100000.0))
        self.assertAlmostEqual(view["used_pct"], 25.0)
        rows = dict(es.daily_loss_rows(view))
        self.assertEqual(rows["Estado"], "Bien")
        self.assertEqual(rows["Límite usado (2 %)"], "25 %")
        self.assertEqual(rows["Pérdida del día (realizada + no realizada)"], "$500.00")
        self.assertEqual(rows["NAV al inicio del día (17:00 ET)"], "$100,000.00")
        self.assertEqual(rows["Freno por caída máxima (5 %)"], "Bien")

    def test_gain_is_zero_used_and_transfers_excluded(self):
        view = daily_loss_view(self.flags(**{dl.TRANSFERS_FLAG: "1000"}), 100500, NOW, ON)  # +1000 deposit, -500
        self.assertEqual((view["loss"], view["used_pct"]), (500.0, 25.0))
        gain = daily_loss_view(self.flags(), 100300, NOW, ON)
        self.assertEqual((gain["loss"], gain["used_pct"], gain["pnl"]), (0.0, 0.0, 300.0))

    def test_triggered_is_latched_even_after_recovery(self):
        view = daily_loss_view(self.flags(**{dl.HALT_FLAG: KEY}), 100100, NOW, ON)
        self.assertEqual(view["status"], "TRIGGERED")
        self.assertIn("Límite alcanzado", dict(es.daily_loss_rows(view))["Estado"])

    def test_previous_day_halt_is_not_today(self):
        old = dl.day_key(NOW - timedelta(days=1))
        view = daily_loss_view({dl.DAY_FLAG: old, dl.E0_FLAG: "100000", dl.TRANSFERS_FLAG: "0", dl.HALT_FLAG: old},
                               98000, NOW, ON)
        self.assertEqual((view["status"], view["used_pct"]), ("PENDING", None))

    def test_unreadable_and_missing(self):
        self.assertEqual(daily_loss_view(None, 99000, NOW, ON)["status"], "NO_DATA")
        self.assertEqual(daily_loss_view({}, 99000, NOW, ON)["status"], "PENDING")
        unknown_nav = daily_loss_view(self.flags(), None, NOW, ON)
        self.assertEqual((unknown_nav["status"], unknown_nav["used_pct"]), ("NO_DATA", None))
        self.assertEqual(es.daily_loss_rows(unknown_nav), [("Estado", es.DAILY_LOSS_STATUS["NO_DATA"])])
        garbage = daily_loss_view(self.flags(**{dl.E0_FLAG: "garbage"}), 99000, NOW, ON)
        self.assertEqual((garbage["status"], garbage["day_start_nav"]), ("NO_DATA", None))

    def test_invalid_inputs_are_no_data_but_halt_stays_triggered(self):
        for flags in (self.flags(**{dl.E0_FLAG: "0"}), self.flags(**{dl.E0_FLAG: "-5"}),
                      {dl.DAY_FLAG: KEY, dl.E0_FLAG: "100000"}, {dl.DAY_FLAG: KEY}):
            with self.subTest(flags=flags):
                self.assertEqual(daily_loss_view(flags, 99000, NOW, ON)["status"], "NO_DATA")
                halted = daily_loss_view({**flags, dl.HALT_FLAG: KEY}, None, NOW, ON)
                self.assertEqual(halted["status"], "TRIGGERED")

    def test_drawdown_latch_and_stricter_fraction(self):
        view = daily_loss_view(self.flags(**{dl.DD_HALT_FLAG: "2026-10-19T10:00:00+00:00"}), 99500, NOW,
                               {**ON, "AI_FLOOR_DAILY_LOSS_PCT": "0.01"})
        self.assertTrue(view["drawdown_halted"])
        self.assertAlmostEqual(view["used_pct"], 50.0)
        rows = dict(es.daily_loss_rows(view))
        self.assertEqual(rows["Límite usado (1 %)"], "50 %")
        self.assertEqual(rows["Freno por caída máxima (5 %)"], "Límite alcanzado")


class LoadLossFlagsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "broker_state.db"
        self.addCleanup(dl._HALT_LATCHES.clear)

    def test_missing_file_and_table(self):
        self.assertEqual(load_loss_flags(self.db), {})
        self.assertFalse(self.db.exists())  # never created
        with closing(sqlite3.connect(self.db)):
            pass
        self.assertEqual(load_loss_flags(self.db), {})

    def test_reads_the_tracker_state_read_only(self):
        store = DemoStateStore(self.db, clock=lambda: NOW.isoformat())
        limit = dl.DailyLossLimit(store, clock=lambda: NOW)
        limit.evaluate(D("100000"))
        limit.evaluate(D("97900"))
        store.set_flag("unrelated", "x")
        flags = load_loss_flags(self.db)
        self.assertEqual((flags[dl.DAY_FLAG], flags[dl.E0_FLAG], flags[dl.HALT_FLAG]), (KEY, "100000", KEY))
        self.assertNotIn("unrelated", flags)
        view = daily_loss_view(flags, 97900, NOW, ON)
        self.assertEqual(view["status"], "TRIGGERED")
        self.assertAlmostEqual(view["used_pct"], 105.0)

    def test_corrupt_file_is_none(self):
        self.db.write_bytes(b"not a database" * 100)
        self.assertIsNone(load_loss_flags(self.db))


class DemoRCellTests(unittest.TestCase):
    def test_dash_when_initial_stop_unknown(self):
        from ui.views.luxury import DEMO_TRADE_CELLS
        cell = DEMO_TRADE_CELLS["realized_r"]
        html = cell({"realized_r": None, "realized_r_reason": "sin datos del stop original"})
        self.assertIn(">—<", html)
        self.assertIn('title="sin datos del stop original"', html)
        self.assertIn("+1.50R", cell({"realized_r": 1.5}))


class OverviewCardTests(unittest.TestCase):
    def test_overview_shows_day_counter_and_daily_loss_card(self):
        import test_demo_from_zero as tz
        case = tz.DashboardTests("test_routing_on_overview_is_demo_only")
        case.setUp()
        self.addCleanup(case.doCleanups)
        store = DemoStateStore(case.state, clock=lambda: tz.NOW.isoformat())
        key = dl.day_key(datetime.now(UTC))  # the app renders with the real clock
        for flag, value in ((dl.DAY_FLAG, key), (dl.E0_FLAG, "100100"), (dl.TRANSFERS_FLAG, "0"),
                            (dl.HALT_FLAG, key)):
            store.set_flag(flag, value)
        with closing(sqlite3.connect(case.state)) as con:
            con.execute("INSERT INTO broker_demo_trades(client_id,run_id,symbol,side,units,entry,stop,target,status,"
                        "created_at,updated_at,opened_at) VALUES ('aif-r1','r','EURUSD','LONG',1000,'1.1','1.09',"
                        "'1.13','CLOSED','t','t','2026-10-19T12:01:13+00:00')")
            con.commit()
        with case.env(**ON):
            _app, content = case.page("Overview")
        self.assertIn("Límite de pérdida diaria", content)
        self.assertIn("Límite alcanzado", content)
        self.assertIn("NAV al inicio del día", content)
        self.assertRegex(content, r"Día \d+ de 30")


if __name__ == "__main__":
    unittest.main()
