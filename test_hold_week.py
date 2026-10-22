"""DEC-HOLD-WEEK-1: max hold one week (the earlier of Friday 16:45 NY and 5 trading days after the fill), no entries
from Friday 16:45 NY until the Sunday 17:00 NY reopen, PAPER and demo router alike; legacy Day-1 check OFF."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from execution import friday_close as fc
from execution import trade_duration as td
from execution.broker_demo_router import duration_action
from storage.database import Store
from test_broker_demo_router import RouterBase, context, row
from test_demo_runner import patched_scouts
from test_trade_duration import FIVE, FRI_CLOSE, FRI_FLATTEN, THU_SLOT, Prices, RuntimeCase, position

UTC = timezone.utc


def ny(*args):
    return datetime(*args, tzinfo=td.NEW_YORK).astimezone(UTC)


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        self.assertEqual((td.MAX_HOLD_TRADING_DAYS, td.FRIDAY_FLATTEN_NY.isoformat(timespec="minutes"), td.DAY1_RULE),
                         (5, "16:45", False))
        self.assertEqual((td.FRIDAY_FLATTEN_LEAD_MINUTES, fc.DEFAULT_MINUTES), (15, 15))

    def test_env_override_and_fail_closed(self):
        self.assertEqual(td.max_hold_from_env({td.MAX_HOLD_ENV: "3"}), 3)
        self.assertEqual(td.friday_flatten_from_env({td.FRIDAY_FLATTEN_ENV: "16:30"}).isoformat(), "16:30:00")
        for bad in ("0", "21", "x", "-1", "2.5"):
            with self.subTest(bad=bad), self.assertLogs("ai_floor.runtime", "WARNING"):
                self.assertEqual(td.max_hold_from_env({td.MAX_HOLD_ENV: bad}), 5)
        for bad in ("17:00", "12:59", "16:5", "x", "25:00"):
            with self.subTest(bad=bad), self.assertLogs("ai_floor.runtime", "WARNING"):
                self.assertEqual(td.friday_flatten_from_env({td.FRIDAY_FLATTEN_ENV: bad}).isoformat(), "16:45:00")


class BoundaryTests(unittest.TestCase):
    def test_midweek_fill_closes_friday_16_45_dst_correct(self):
        # EDT (October): 16:45 NY = 20:45 UTC; EST (after the 2026-11-01 fall back): 21:45 UTC.
        self.assertEqual(td.hold_boundary(ny(2026, 10, 12, 10, 0)), (datetime(2026, 10, 16, 20, 45, tzinfo=UTC),
                                                                     td.CLOSE_FRIDAY))
        self.assertEqual(td.hold_boundary(ny(2026, 11, 2, 10, 0)), (datetime(2026, 11, 6, 21, 45, tzinfo=UTC),
                                                                    td.CLOSE_FRIDAY))
        # Fill in the DST-change week (Thursday EDT) -> Friday still EDT; a Sunday-evening fill -> that Friday.
        self.assertEqual(td.hold_boundary(ny(2026, 10, 29, 10, 0))[0], datetime(2026, 10, 30, 20, 45, tzinfo=UTC))
        self.assertEqual(td.hold_boundary(ny(2026, 11, 1, 18, 0))[0], datetime(2026, 11, 6, 21, 45, tzinfo=UTC))
        # Spring forward (Sunday 2026-03-08): a Monday fill closes Friday 16:45 EDT = 20:45 UTC.
        self.assertEqual(td.hold_boundary(ny(2026, 3, 9, 3, 0))[0], datetime(2026, 3, 13, 20, 45, tzinfo=UTC))

    def test_friday_before_and_at_the_flatten(self):
        self.assertEqual(td.hold_boundary(ny(2026, 10, 16, 16, 44))[0], datetime(2026, 10, 16, 20, 45, tzinfo=UTC))
        at = ny(2026, 10, 16, 16, 45)  # at/after the flatten: due at once, never held over the weekend
        self.assertEqual(td.hold_boundary(at), (at, td.CLOSE_FRIDAY))

    def test_late_fill_after_friday_flatten_is_closed_at_once(self):
        for at in (ny(2026, 10, 16, 16, 50), ny(2026, 10, 16, 23, 0), ny(2026, 10, 17, 12, 0),
                   ny(2026, 10, 18, 16, 59)):
            with self.subTest(at=at):
                self.assertEqual(td.hold_boundary(at), (at, td.CLOSE_FRIDAY))
        reopen = ny(2026, 10, 18, 17, 0)  # Sunday reopen: the coming Friday
        self.assertEqual(td.hold_boundary(reopen)[0], datetime(2026, 10, 23, 20, 45, tzinfo=UTC))

    def test_five_trading_days_skip_weekends_count_holidays(self):
        self.assertEqual(td.add_trading_days(ny(2026, 10, 15, 10, 0), 5), ny(2026, 10, 22, 10, 0))  # Thu -> Thu
        self.assertEqual(td.add_trading_days(ny(2026, 10, 16, 10, 0), 1), ny(2026, 10, 19, 10, 0))  # Fri -> Mon
        # Thanksgiving (Thu 2026-11-26) and Christmas (Fri 2026-12-25) are trading days for this rule.
        self.assertEqual(td.add_trading_days(ny(2026, 11, 25, 10, 0), 1), ny(2026, 11, 26, 10, 0))
        self.assertEqual(td.add_trading_days(ny(2026, 12, 24, 10, 0), 2), ny(2026, 12, 28, 10, 0))
        # Across the fall-back weekend the NY wall-clock time is kept (UTC shifts by one hour).
        self.assertEqual(td.add_trading_days(ny(2026, 10, 30, 10, 0), 1), datetime(2026, 11, 2, 15, 0, tzinfo=UTC))

    def test_max_hold_binds_when_configured_shorter_and_friday_wins_a_tie(self):
        with patch.object(td, "MAX_HOLD_TRADING_DAYS", 2):
            self.assertEqual(td.hold_boundary(ny(2026, 10, 12, 10, 0)), (ny(2026, 10, 14, 10, 0), td.CLOSE_MAX_HOLD))
            self.assertEqual(td.hold_boundary(ny(2026, 10, 15, 10, 0))[1], td.CLOSE_FRIDAY)  # Mon 10:00 > Fri
            tie = ny(2026, 10, 14, 16, 45)
            self.assertEqual(td.hold_boundary(tie), (ny(2026, 10, 16, 16, 45), td.CLOSE_FRIDAY))
        with patch.object(td, "MAX_HOLD_TRADING_DAYS", 5):  # default: Friday always comes first
            for day in range(12, 17):
                self.assertEqual(td.hold_boundary(ny(2026, 10, day, 9, 0))[1], td.CLOSE_FRIDAY)

    def test_entries_blocked_window(self):
        cases = [(ny(2026, 10, 16, 16, 44), False), (ny(2026, 10, 16, 16, 45), True), (ny(2026, 10, 17, 9, 0), True),
                 (ny(2026, 10, 18, 16, 59), True), (ny(2026, 10, 18, 17, 0), False), (ny(2026, 10, 15, 16, 50), False),
                 (datetime(2026, 11, 6, 21, 44, tzinfo=UTC), False), (datetime(2026, 11, 6, 21, 45, tzinfo=UTC), True)]
        for at, expected in cases:
            with self.subTest(at=at):
                self.assertEqual(td.entries_blocked(at), expected)

    def test_day1_rule_default_off(self):
        p = position()  # opened Wednesday: no Day-1 boundary, only the Friday flatten
        self.assertEqual(td.next_boundary(p), FRI_FLATTEN)
        self.assertEqual(td.boundary_decision(p, FRI_FLATTEN, 90.0), ("CLOSE", td.CLOSE_FRIDAY))


class RouterTests(unittest.TestCase):
    def test_router_uses_the_same_week_rule(self):
        monday = datetime(2026, 10, 12, 14, tzinfo=UTC)
        flatten = datetime(2026, 10, 16, 20, 45, tzinfo=UTC)
        self.assertIsNone(duration_action(row(opened=monday), datetime(2026, 10, 13, 21, tzinfo=UTC), None))
        self.assertIsNone(duration_action(row(opened=monday), flatten - timedelta(minutes=1), Decimal("1.2")))
        for side in ("LONG", "SHORT"):
            trade = row(side=side, opened=monday, stop="1.08000" if side == "LONG" else "1.10000")
            self.assertEqual(duration_action(trade, flatten, None), ("CLOSE", td.CLOSE_FRIDAY))
        with patch.object(td, "MAX_HOLD_TRADING_DAYS", 2):
            self.assertEqual(duration_action(row(opened=monday), monday + timedelta(days=2), None),
                             ("CLOSE", td.CLOSE_MAX_HOLD))
        late = ny(2026, 10, 16, 16, 55)  # a fill after the flatten is closed on the first managed tick
        self.assertEqual(duration_action(row(opened=late), late, None), ("CLOSE", td.CLOSE_FRIDAY))


class RouterEntryTests(RouterBase):
    def test_entries_refused_from_friday_16_45_even_with_preflatten_off(self):
        router = self.router()
        self.assertIsNone(router._friday_minutes)
        for n, at in enumerate((ny(2026, 10, 16, 16, 45), ny(2026, 10, 17, 9, 0), ny(2026, 10, 18, 16, 59))):
            with self.subTest(at=at):
                self.assertEqual(router.on_cycle(context(run_id=f"w-{n}"), "PLAN_READY", at),
                                 ("NOT_ROUTED", "friday_close_window"))
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.rows(), [])


class PaperRuntimeTests(RuntimeCase):
    def execution(self):
        store = Store(self.db)
        try:
            return [json.loads(r[0]) for r in store.db.execute(
                "SELECT payload FROM journal WHERE event_type='EXECUTION_DECISION' ORDER BY id")]
        finally:
            store.close()

    def test_control_thursday_entry_is_submitted(self):
        control = THU_SLOT
        runtime = self.runtime(Prices(lambda at: 100.0, control - timedelta(hours=6)), control)
        with patched_scouts("LONG"):
            runtime.run_cycle("XAUUSD", control)
        self.assertEqual(self.execution()[-1]["execution_reason"], "ORDER_SUBMITTED")

    def test_entry_refused_in_the_window(self):
        # The NY session runs until 17:00 ET: the 16:45 slot is the only in-session cycle of the window.
        for n, slot in enumerate((FRI_FLATTEN,)):
            with self.subTest(slot=slot):
                self.db = self.db.with_name(f"window-{n}.db")
                runtime = self.runtime(Prices(lambda at: 100.0, slot - timedelta(hours=6)), slot)
                with patched_scouts("LONG"):
                    runtime.run_cycle("XAUUSD", slot)
                decision = self.execution()[-1]
                self.assertEqual((decision["execution_status"], decision["execution_reason"]),
                                 ("SKIPPED", "FRIDAY_FLATTEN_WINDOW"))
                self.assertEqual(runtime.store.load_paper("paper-main")[1], {})

    def test_max_hold_close_in_a_cycle(self):
        self.enterContext(patch.object(td, "MAX_HOLD_TRADING_DAYS", 1))
        opened = datetime(2026, 1, 14, 13, 0, tzinfo=UTC)  # Wednesday 08:00 NY -> due Thursday 08:00 NY
        self.seed(position(opened_at=opened, stop=90.0, target=130.0))
        runtime = self.runtime(Prices(lambda at: 104.0, opened - 4 * FIVE), THU_SLOT)
        runtime.run_cycle("XAUUSD", THU_SLOT)
        trade, journal = self.closed_trade()
        self.assertEqual((trade.reason, trade.exit_price, trade.exited_at),
                         (td.CLOSE_MAX_HOLD, 104.0, opened + timedelta(days=1)))
        self.assertEqual(next(p for e, p in journal if e == "TRADE_DURATION_EXIT")["exit_reason"], td.CLOSE_MAX_HOLD)

    def test_late_weekend_fill_closed_by_the_next_pass(self):
        opened = FRI_FLATTEN + timedelta(minutes=5)  # a fill after the flatten (never expected; closed at once)
        self.seed(position(opened_at=opened, stop=90.0, target=130.0))
        runtime = self.runtime(Prices(lambda at: 101.0, opened - 4 * FIVE), FRI_CLOSE)
        self.assertEqual(runtime.manage_trade_duration(FRI_CLOSE), {"XAUUSD": "CHECKED"})
        trade, _ = self.closed_trade()
        self.assertEqual((trade.reason, trade.exit_price), (td.CLOSE_FRIDAY, 101.0))
        self.assertGreater(trade.exited_at, opened)


def setUpModule():  # DEC-XAU-OFF-1: these tests drive XAUUSD PAPER cycles; the pause itself is in test_pause_xauusd
    import unittest.mock
    unittest.enterModuleContext(unittest.mock.patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))


if __name__ == "__main__":
    unittest.main()
