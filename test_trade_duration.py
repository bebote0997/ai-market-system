"""Pre-demo trade-duration rule: Day-1 / Day-2 NY close, Day-2 structure exit, Friday close (temporary DBs)."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pandas as pd

from ai.provider import DeterministicAIProvider
from execution import trade_duration as td
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from runtime.config import RuntimeConfig
from runtime.scheduler import Scheduler
from runtime.service import OperationalRuntime
from storage.codec import paper_decode, paper_encode
from storage.database import Store
from test_demo_runner import instrument, macro_fixture
from test_runtime_catch_up import EUR

ACCOUNT = "paper-main"
FIVE = timedelta(minutes=5)
UTC = timezone.utc
# January 2026 (EST, UTC-5): 17:00 New York = 22:00 UTC. Tue 13, Wed 14, Thu 15, Fri 16.
TUE_OPEN = datetime(2026, 1, 13, 18, 0, tzinfo=UTC)
WED_OPEN = datetime(2026, 1, 14, 18, 0, tzinfo=UTC)
THU_OPEN = datetime(2026, 1, 15, 15, 0, tzinfo=UTC)
WED_CLOSE = datetime(2026, 1, 14, 22, 0, tzinfo=UTC)
THU_SLOT = datetime(2026, 1, 15, 13, 30, tzinfo=UTC)  # London + New York session
FRI_CLOSE = datetime(2026, 1, 16, 22, 0, tzinfo=UTC)


def position(side="LONG", entry=100.0, stop=95.0, target=110.0, opened_at=WED_OPEN, stage=None, initial_stop=None,
             symbol="XAUUSD"):
    return PaperPosition("1.0", "pos-1", "ord-1", "run-open", symbol, side, 10.0, entry, entry, stop, target,
                         opened_at, last_price=entry, contract_multiplier=1.0, initial_stop=initial_stop,
                         duration_stage=stage)


class BoundaryTests(unittest.TestCase):
    def test_ny_close_is_dst_correct(self):
        self.assertEqual(td.ny_close_after(datetime(2026, 1, 15, 14, tzinfo=UTC)), datetime(2026, 1, 15, 22, tzinfo=UTC))
        self.assertEqual(td.ny_close_after(datetime(2026, 7, 15, 14, tzinfo=UTC)), datetime(2026, 7, 15, 21, tzinfo=UTC))
        # US DST starts Sunday 2026-03-08: Friday close is EST, the next Monday close is EDT.
        self.assertEqual(td.ny_close_after(datetime(2026, 3, 6, 15, tzinfo=UTC)), datetime(2026, 3, 6, 22, tzinfo=UTC))
        self.assertEqual(td.ny_close_after(datetime(2026, 3, 6, 23, tzinfo=UTC)), datetime(2026, 3, 9, 21, tzinfo=UTC))
        # DST ends Sunday 2026-11-01.
        self.assertEqual(td.ny_close_after(datetime(2026, 10, 30, 23, tzinfo=UTC)), datetime(2026, 11, 2, 22, tzinfo=UTC))

    def test_close_exactly_at_boundary_rolls_to_next_day_and_weekends_are_skipped(self):
        self.assertEqual(td.ny_close_after(datetime(2026, 1, 15, 22, tzinfo=UTC)), datetime(2026, 1, 16, 22, tzinfo=UTC))
        sunday_open = datetime(2026, 1, 18, 23, tzinfo=UTC)  # Sunday 18:00 New York
        self.assertEqual(td.ny_close_after(sunday_open), datetime(2026, 1, 19, 22, tzinfo=UTC))

    def test_day1_and_day2_closes(self):
        p = position(opened_at=TUE_OPEN)
        self.assertEqual(td.day1_close(p), datetime(2026, 1, 13, 22, tzinfo=UTC))
        self.assertEqual(td.day2_close(p), WED_CLOSE)
        self.assertEqual(td.next_boundary(p), td.day1_close(p))
        p.duration_stage = td.STAGE_DAY2
        self.assertEqual(td.next_boundary(p), WED_CLOSE)


class DecisionTests(unittest.TestCase):
    def test_day1_keeps_at_half_r_and_closes_below(self):
        p = position()
        self.assertEqual(td.boundary_decision(p, WED_CLOSE, 102.5), ("BREAKEVEN", None))
        self.assertEqual(td.boundary_decision(p, WED_CLOSE, 102.49), ("CLOSE", td.CLOSE_DAY1_BELOW_HALF_R))
        self.assertEqual(td.boundary_decision(p, WED_CLOSE, 90.0), ("CLOSE", td.CLOSE_DAY1_BELOW_HALF_R))

    def test_short_side_mirrors(self):
        p = position("SHORT", 100.0, 105.0, 90.0)
        self.assertEqual(td.unrealized_r(p, 97.5), Decimal("0.5"))
        self.assertEqual(td.boundary_decision(p, WED_CLOSE, 97.5), ("BREAKEVEN", None))
        self.assertEqual(td.boundary_decision(p, WED_CLOSE, 98.0)[1], td.CLOSE_DAY1_BELOW_HALF_R)

    def test_day2_and_friday_always_close(self):
        self.assertEqual(td.boundary_decision(position(stage=td.STAGE_DAY2, stop=100.0, initial_stop=95.0),
                                              WED_CLOSE, 109.0), ("CLOSE", td.CLOSE_DAY2_NY_CLOSE))
        self.assertEqual(td.boundary_decision(position(), FRI_CLOSE, 109.0), ("CLOSE", td.CLOSE_FRIDAY))

    def test_move_stop_to_entry_keeps_r_on_original_distance(self):
        p = position()
        td.move_stop_to_entry(p)
        self.assertEqual((p.stop, p.initial_stop, p.duration_stage), (100.0, 95.0, td.STAGE_DAY2))
        self.assertEqual(td.unrealized_r(p, 105.0), Decimal("1"))

    def test_unmeasurable_r_never_keeps_the_position(self):
        self.assertEqual(td.boundary_decision(position(stop=100.0), WED_CLOSE, 120.0)[1], td.CLOSE_DAY1_BELOW_HALF_R)

    def test_optional_fields_keep_legacy_bytes_and_round_trip(self):
        legacy = position()
        self.assertNotIn("initial_stop", json.loads(paper_encode(legacy)))
        self.assertNotIn("duration_stage", json.loads(paper_encode(legacy)))
        td.move_stop_to_entry(legacy)
        decoded = paper_decode("PaperPosition", paper_encode(legacy))
        self.assertEqual((decoded.initial_stop, decoded.duration_stage, decoded.stop), (95.0, td.STAGE_DAY2, 100.0))


def frame(stamps, closes, symbol, spread=0.05, lows=None, highs=None):
    lows = lows or [c - spread for c in closes]
    highs = highs or [c + spread for c in closes]
    return pd.DataFrame({"Open": closes, "High": highs, "Low": lows, "Close": closes, "symbol": symbol,
                         "is_closed": True}, index=pd.DatetimeIndex(stamps))


class Prices:
    """5m bars from ``price(stamp)`` between ``first`` and the slot; 15m/1h aggregated from them (closed only)
    unless ``fifteen`` is supplied."""

    def __init__(self, price, first, fifteen=None):
        self.price, self.first, self.fifteen = price, first, fifteen

    def load_snapshot(self, symbol, slot):
        stamps = list(pd.date_range(self.first, slot - FIVE, freq="5min"))
        five = frame(stamps, [self.price(s.to_pydatetime()) for s in stamps], symbol)

        def aggregate(rule, minutes):
            out = five[["Open", "High", "Low", "Close"]].resample(rule).agg(
                {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
            out = out[out.index + pd.Timedelta(minutes=minutes) <= slot]
            return out.assign(symbol=symbol, is_closed=True)
        return {"1h": aggregate("1h", 60), "15m": self.fifteen if self.fifteen is not None else aggregate("15min", 15),
                "5m": five}


class RuntimeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="trade-duration-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "trading_floor.db"

    def seed(self, p):
        store = Store(self.db)
        try:
            broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
            stop = p.initial_stop if p.initial_stop is not None else p.stop
            order = PaperOrder("1.0", p.origin_order_id, p.run_id, p.symbol, p.side, p.quantity, p.entry_price, stop,
                               p.target, 1.0, 10000.0, 0.0, p.opened_at - FIVE, status="FILLED")
            broker.orders[order.order_id] = order
            broker.account.open_positions[p.symbol] = p
            store.save_paper(broker)
        finally:
            store.close()

    def runtime(self, provider, clock):
        config = RuntimeConfig(db_path=self.db, enabled_symbols=("XAUUSD", "EURUSD"))
        runtime = OperationalRuntime(config, market_provider=provider, ai_provider=DeterministicAIProvider(),
                                     macro_provider=macro_fixture(),
                                     instruments={"XAUUSD": instrument(), "EURUSD": EUR}, clock=lambda: clock)
        self.addCleanup(runtime.close)
        return runtime

    def paper(self):
        store = Store(self.db)
        try:
            account, _, _ = store.load_paper(ACCOUNT)
            journal = [(r["event_type"], json.loads(r["payload"] or "{}"))
                       for r in store.db.execute("SELECT event_type,payload FROM journal ORDER BY id")]
            return account, journal
        finally:
            store.close()

    def closed_trade(self):
        account, journal = self.paper()
        self.assertNotIn("XAUUSD", account.open_positions)
        self.assertEqual(len(account.closed_trades), 1)
        return account.closed_trades[0], journal


class Day1RuntimeTests(RuntimeCase):
    def test_below_half_r_closes_at_ny_close_before_later_bars_can_stop_it(self):
        self.seed(position())

        def price(at):
            if at == WED_CLOSE - FIVE:
                return 101.0  # +0.2R at the 17:00 New York close
            return 94.0 if at >= WED_CLOSE + timedelta(hours=3) else 100.0  # later stop-out must not apply
        runtime = self.runtime(Prices(price, WED_OPEN - 4 * FIVE), THU_SLOT)
        self.assertNotEqual(runtime.run_cycle("XAUUSD", THU_SLOT), "ERROR")
        trade, journal = self.closed_trade()
        self.assertEqual((trade.reason, trade.exit_price, trade.exited_at),
                         (td.CLOSE_DAY1_BELOW_HALF_R, 101.0, WED_CLOSE))
        self.assertAlmostEqual(trade.net_pnl, 10.0)
        closed = [payload for event, payload in journal if event == "POSITION_CLOSED"]
        self.assertEqual(closed, [{"exit_reason": td.CLOSE_DAY1_BELOW_HALF_R}])
        exit_event = next(payload for event, payload in journal if event == "TRADE_DURATION_EXIT")
        self.assertEqual((exit_event["unrealized_r"], exit_event["boundary_utc"]), ("0.2", WED_CLOSE.isoformat()))
        review = Store(self.db)
        try:
            execution = json.loads(review.db.execute(
                "SELECT payload FROM journal WHERE event_type='EXECUTION_DECISION'").fetchone()[0])
        finally:
            review.close()
        self.assertNotEqual(execution["execution_status"], "SUBMITTED")

    def test_at_least_half_r_keeps_with_stop_moved_to_entry(self):
        self.seed(position())
        runtime = self.runtime(Prices(lambda at: 103.0 if at >= WED_CLOSE - FIVE else 100.0,
                                      WED_OPEN - 4 * FIVE), THU_SLOT)
        self.assertNotEqual(runtime.run_cycle("XAUUSD", THU_SLOT), "ERROR")
        account, journal = self.paper()
        p = account.open_positions["XAUUSD"]
        self.assertEqual((p.stop, p.initial_stop, p.duration_stage), (100.0, 95.0, td.STAGE_DAY2))
        self.assertEqual(account.closed_trades, [])
        moved = next(payload for event, payload in journal if event == td.STOP_MOVED_TO_ENTRY)
        self.assertEqual((moved["initial_stop"], moved["stop"], moved["unrealized_r"]), (95.0, 100.0, "0.6"))

    def test_breakeven_stop_after_keep_is_managed_by_trade_manager(self):
        self.seed(position())

        def price(at):
            if WED_CLOSE - FIVE <= at < WED_CLOSE + timedelta(hours=2):
                return 103.0
            return 99.0 if at >= WED_CLOSE + timedelta(hours=2) else 100.0
        runtime = self.runtime(Prices(price, WED_OPEN - 4 * FIVE), THU_SLOT)
        self.assertNotEqual(runtime.run_cycle("XAUUSD", THU_SLOT), "ERROR")
        trade, _ = self.closed_trade()
        self.assertEqual((trade.reason, trade.exit_price), ("stop", 99.0))  # the bar opens through the moved stop


class Day2RuntimeTests(RuntimeCase):
    def test_day2_ny_close_force_closes(self):
        self.seed(position(opened_at=TUE_OPEN))
        day1 = td.day1_close(position(opened_at=TUE_OPEN))
        runtime = self.runtime(Prices(lambda at: 104.0 if at >= day1 - FIVE else 100.0, TUE_OPEN - 4 * FIVE), THU_SLOT)
        self.assertNotEqual(runtime.run_cycle("XAUUSD", THU_SLOT), "ERROR")
        trade, journal = self.closed_trade()
        self.assertEqual((trade.reason, trade.exit_price, trade.exited_at), (td.CLOSE_DAY2_NY_CLOSE, 104.0, WED_CLOSE))
        self.assertEqual([event for event, _ in journal].count(td.STOP_MOVED_TO_ENTRY), 1)

    def test_adverse_structure_change_on_day2_closes_early(self):
        self.seed(position(target=120.0, stop=100.0, initial_stop=95.0, stage=td.STAGE_DAY2))
        slot = datetime(2026, 1, 15, 13, 15, tzinfo=UTC)
        stamps = list(pd.date_range(datetime(2026, 1, 15, 11, tzinfo=UTC), periods=9, freq="15min"))
        lows = [108, 107, 108, 109, 110, 109.5, 110.5, 111, 105.5]
        closes = [low + .5 for low in lows[:-1]] + [106.0]
        fifteen = frame(stamps, closes, "XAUUSD", lows=lows, highs=[low + 1 for low in lows])
        runtime = self.runtime(Prices(lambda at: 106.0 if at >= stamps[0] else 104.0, WED_OPEN - 4 * FIVE, fifteen),
                               slot)
        self.assertNotEqual(runtime.run_cycle("XAUUSD", slot), "ERROR")
        trade, journal = self.closed_trade()
        self.assertEqual((trade.reason, trade.exit_price, trade.exited_at), (td.CLOSE_DAY2_STRUCTURE, 106.0, slot))
        exit_event = next(payload for event, payload in journal if event == "TRADE_DURATION_EXIT")
        self.assertEqual(exit_event["bos_direction"], "bearish")

    def test_no_structure_exit_on_day1(self):
        p = position(opened_at=datetime(2026, 1, 15, 9, tzinfo=UTC))
        stamps = list(pd.date_range(datetime(2026, 1, 15, 11, tzinfo=UTC), periods=9, freq="15min"))
        lows = [108, 107, 108, 109, 110, 109.5, 110.5, 111, 105.5]
        fifteen = frame(stamps, [low + .5 for low in lows], "XAUUSD", lows=lows)
        self.assertIsNone(td.adverse_structure(p, fifteen, datetime(2026, 1, 15, 13, 15, tzinfo=UTC)))


class FridayAndOffSessionTests(RuntimeCase):
    def test_friday_ny_close_closes_everything_out_of_session(self):
        p = position(opened_at=THU_OPEN)
        self.seed(p)
        runtime = self.runtime(Prices(lambda at: 109.0 if at >= td.day1_close(p) - FIVE else 100.0,
                                      THU_OPEN - 4 * FIVE), FRI_CLOSE)
        self.assertEqual(runtime.manage_trade_duration(FRI_CLOSE), {"XAUUSD": "CHECKED"})
        trade, _ = self.closed_trade()
        self.assertEqual((trade.reason, trade.exit_price, trade.exited_at), (td.CLOSE_FRIDAY, 109.0, FRI_CLOSE))

    def test_friday_open_closes_at_its_first_close_even_in_profit(self):
        opened = datetime(2026, 1, 16, 14, tzinfo=UTC)
        self.seed(position(opened_at=opened))
        runtime = self.runtime(Prices(lambda at: 109.0, opened - 4 * FIVE), FRI_CLOSE + timedelta(minutes=10))
        runtime.manage_trade_duration(FRI_CLOSE + timedelta(minutes=10))
        trade, _ = self.closed_trade()
        self.assertEqual((trade.reason, trade.exited_at), (td.CLOSE_FRIDAY, FRI_CLOSE))

    def test_off_session_pass_is_a_noop_before_the_boundary_and_without_positions(self):
        self.seed(position(opened_at=THU_OPEN))
        before = datetime(2026, 1, 15, 21, 45, tzinfo=UTC)
        runtime = self.runtime(Prices(lambda at: 100.0, THU_OPEN - 4 * FIVE), before)
        self.assertEqual(runtime.manage_trade_duration(before), {})
        self.assertIn("XAUUSD", self.paper()[0].open_positions)

    def test_scheduler_runs_the_pass_only_outside_sessions(self):
        calls = []

        class Fake:
            config = RuntimeConfig(db_path=self.db, scheduler_enabled=True)
            store = Store(self.db)

            def manage_trade_duration(self, at):
                calls.append(at)

            def run_cycle(self, symbol, slot):
                return "RAN"
        fake = Fake()
        self.addCleanup(fake.store.close)
        Scheduler(fake, lambda: FRI_CLOSE).tick()
        self.assertEqual(calls, [FRI_CLOSE])
        self.assertEqual(Scheduler(fake, lambda: THU_SLOT).tick(), ["RAN", "RAN"])
        self.assertEqual(calls, [FRI_CLOSE])


if __name__ == "__main__":
    unittest.main()
