"""DEC-LATCH-PERSIST-1: daily-loss / drawdown latches and the day-start NAV (E0) survive a PROCESS restart.

A process restart is simulated by a new store + tracker AND an empty process-local latch table
(``daily_loss_limit._HALT_LATCHES``), so only the SQLite flags (``broker_demo_flags``) can carry the state."""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import sqlite3
import tempfile
import unittest

from execution import daily_loss_limit as dl
from execution.broker_demo_state import DemoStateStore
from execution.daily_loss_limit import DailyLossLimit

UTC = timezone.utc
T0 = datetime(2026, 10, 19, 14, 0, tzinfo=UTC)  # Monday 10:00 NY (trading day keyed 2026-10-18 17:00 NY)


class LatchPersistTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "broker_state.db"
        self.now = T0
        self.addCleanup(dl._HALT_LATCHES.clear)

    def restart(self):
        """New process: nothing in memory, same state DB file."""
        dl._HALT_LATCHES.clear()
        store = DemoStateStore(self.db, clock=lambda: self.now.isoformat())
        return DailyLossLimit(store, clock=lambda: self.now)

    def flag(self, key, value):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE broker_demo_flags SET value=? WHERE key=?", (value, key))
            con.commit()

    def drop(self, key):
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("DELETE FROM broker_demo_flags WHERE key=?", (key,))
            con.commit()

    def test_same_day_restart_stays_latched_with_the_same_baseline(self):
        limit = self.restart()
        limit.evaluate(D("100000"))
        self.now += timedelta(hours=1)
        self.assertTrue(limit.evaluate(D("97900")).halted)  # -2.1 %
        self.now += timedelta(hours=1)
        restarted = self.restart()
        self.assertEqual(restarted.halt_state(), (True, False))
        decision = restarted.evaluate(D("101000"))  # NAV recovered: still latched for the trading day
        self.assertEqual((decision.halted, decision.entry_allowed, decision.newly_halted), (True, False, False))
        self.assertEqual(decision.daily.pnl, D("1000"))  # same day-start NAV (100000), not re-read
        self.assertEqual(restarted.status()["e0"], "100000")

    def test_next_trading_day_resets_the_daily_latch(self):
        limit = self.restart()
        limit.evaluate(D("100000"))
        limit.evaluate(D("97000"))
        self.now = datetime(2026, 10, 19, 21, 0, tzinfo=UTC)  # 17:00 NY: new trading day
        restarted = self.restart()
        self.assertEqual(restarted.halt_state(), (False, False))
        decision = restarted.evaluate(D("97000"))
        self.assertEqual((decision.halted, decision.entry_allowed, decision.daily.pnl), (False, True, D("0")))
        self.assertEqual(restarted.status()["e0"], "97000")

    def test_drawdown_latch_survives_restarts_and_day_rolls(self):
        limit = self.restart()
        limit.evaluate(D("100000"))
        self.now += timedelta(days=1)
        limit.evaluate(D("98500"))
        self.now += timedelta(days=1)
        limit.evaluate(D("97000"))
        self.now += timedelta(days=1)
        self.assertTrue(limit.evaluate(D("95000")).dd_halted)  # 5 % below the first NAV
        self.now += timedelta(days=1)
        restarted = self.restart()
        self.assertEqual(restarted.halt_state()[1], True)
        decision = restarted.evaluate(D("99000"))
        self.assertEqual((decision.dd_halted, decision.entry_allowed, decision.flatten_required), (True, False, True))

    def test_missing_halt_row_is_not_latched_and_baseline_is_re_read(self):
        limit = self.restart()
        limit.evaluate(D("100000"))
        limit.evaluate(D("97000"))
        for key in ("dl_halt", "dl_day", "dl_e0", "dl_transfers"):
            self.drop(key)
        restarted = self.restart()
        self.assertEqual(restarted.halt_state(), (False, False))
        decision = restarted.evaluate(D("97000"))  # E0 re-read: last observed NAV (fresh)
        self.assertEqual((decision.halted, decision.daily.pnl), (False, D("0")))
        self.assertEqual(restarted.status()["e0"], "97000")

    def test_corrupt_row_fails_closed_for_entries_without_inventing_a_latch(self):
        limit = self.restart()
        limit.evaluate(D("100000"))
        self.flag("dl_e0", "garbage")
        restarted = self.restart()
        decision = restarted.evaluate(D("100000"))
        self.assertEqual((decision.entry_allowed, decision.flatten_required, decision.halted, decision.reason),
                         (False, False, False, "state_unreadable"))
        self.assertEqual(restarted.halt_state(), (False, False))  # latched only when the stored flag says so

    def test_corrupt_row_keeps_a_stored_latch(self):
        limit = self.restart()
        limit.evaluate(D("100000"))
        limit.evaluate(D("97000"))
        self.flag("dl_e0", "garbage")
        restarted = self.restart()
        self.assertEqual(restarted.halt_state(), (True, False))
        self.assertFalse(restarted.evaluate(D("101000")).entry_allowed)


if __name__ == "__main__":
    unittest.main()
