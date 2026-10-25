"""Trade-duration rule (temporary DBs): the optional legacy Day-1 NY close check (OFF by default since
DEC-HOLD-WEEK-1, turned ON here per class) and the Friday 16:45 NY flatten. Week rule: test_hold_week.py."""
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from ai.provider import DeterministicAIProvider
from execution import trade_duration as td
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from runtime.config import RuntimeConfig
from runtime.notification_extras import trade_block
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
FRI_FLATTEN = datetime(2026, 1, 16, 21, 45, tzinfo=UTC)  # DEC-HOLD-WEEK-1: Friday 16:45 New York


class Day1On:
    """The legacy Day-1 check is OFF by default (DEC-HOLD-WEEK-1); these tests characterize it switched ON."""

    def setUp(self):
        super().setUp()
        self.enterContext(patch.object(td, "DAY1_RULE", True))


def position(side="LONG", entry=100.0, stop=95.0, target=110.0, opened_at=WED_OPEN, stage=None, initial_stop=None,
             symbol="XAUUSD"):
    return PaperPosition("1.0", "pos-1", "ord-1", "run-open", symbol, side, 10.0, entry, entry, stop, target,
                         opened_at, last_price=entry, contract_multiplier=1.0, initial_stop=initial_stop,
                         duration_stage=stage)


class BoundaryTests(Day1On, unittest.TestCase):
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

    def test_day1_close_then_the_week_boundary(self):
        p = position(opened_at=TUE_OPEN)
        self.assertEqual(td.day1_close(p), datetime(2026, 1, 13, 22, tzinfo=UTC))
        self.assertEqual(td.next_boundary(p), td.day1_close(p))
        p.duration_stage = td.STAGE_DAY2  # after a Day-1 keep the week rule decides (no Day-2 close any more)
        self.assertEqual(td.next_boundary(p), FRI_FLATTEN)
        with patch.object(td, "DAY1_RULE", False):
            self.assertEqual(td.next_boundary(position(opened_at=TUE_OPEN)), FRI_FLATTEN)


class DecisionTests(Day1On, unittest.TestCase):
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

    def test_week_boundary_always_closes(self):
        self.assertEqual(td.boundary_decision(position(stage=td.STAGE_DAY2, stop=100.0, initial_stop=95.0),
                                              FRI_FLATTEN, 109.0), ("CLOSE", td.CLOSE_FRIDAY))
        self.assertEqual(td.boundary_decision(position(), FRI_FLATTEN, 109.0), ("CLOSE", td.CLOSE_FRIDAY))
        with patch.object(td, "DAY1_RULE", False):  # default: no Day-1 decision at all
            self.assertEqual(td.boundary_decision(position(), WED_CLOSE, 102.5), ("CLOSE", td.CLOSE_FRIDAY))

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


class Day1RuntimeTests(Day1On, RuntimeCase):
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


class KeepThenWeekTests(Day1On, RuntimeCase):
    def test_day1_keep_lives_until_the_friday_flatten(self):
        """The former Day-2 NY close is retired: a kept position is closed by the week rule (Friday 16:45 NY)."""
        self.seed(position(opened_at=TUE_OPEN))
        day1 = td.day1_close(position(opened_at=TUE_OPEN))
        runtime = self.runtime(Prices(lambda at: 104.0 if at >= day1 - FIVE else 100.0, TUE_OPEN - 4 * FIVE), THU_SLOT)
        self.assertNotEqual(runtime.run_cycle("XAUUSD", THU_SLOT), "ERROR")
        account, journal = self.paper()
        self.assertEqual(account.open_positions["XAUUSD"].duration_stage, td.STAGE_DAY2)  # alive after Wed close
        self.assertEqual([event for event, _ in journal].count(td.STOP_MOVED_TO_ENTRY), 1)
        runtime.manage_trade_duration(FRI_CLOSE)
        trade, _ = self.closed_trade()
        self.assertEqual((trade.reason, trade.exited_at), (td.CLOSE_FRIDAY, FRI_FLATTEN))


class DurationWriteRevalidationTests(Day1On, RuntimeCase):
    def test_concurrent_runner_already_moved_the_stop_so_day1_boundary_is_not_reused(self):
        """Codex MED: another runner moves the stop at the Day-1 close between our read and our guarded write."""
        self.seed(position())
        runtime = self.runtime(Prices(lambda at: 103.0 if at >= WED_CLOSE - FIVE else 100.0,
                                      WED_OPEN - 4 * FIVE), THU_SLOT)
        original, raced = runtime._guarded_paper_write, []

        def other_runner_first(symbol, key, apply, store=None, kind="paper_write"):
            if kind == "trade_duration" and not raced:
                raced.append(kind)
                other = Store(self.db)
                try:
                    account, orders, fills = other.load_paper(ACCOUNT)
                    broker = PaperBroker(account)
                    broker.orders, broker.fills = orders, fills
                    td.move_stop_to_entry(broker.account.open_positions["XAUUSD"])
                    other.save_paper(broker)
                finally:
                    other.close()
            return original(symbol, key, apply, store, kind)
        runtime._guarded_paper_write = other_runner_first
        self.assertEqual(runtime.manage_trade_duration(THU_SLOT), {"XAUUSD": "CHECKED"})
        self.assertEqual(raced, ["trade_duration"])
        account, journal = self.paper()
        p = account.open_positions["XAUUSD"]  # not force-closed at the Day-1 boundary as a "Day-2 close"
        self.assertEqual((p.stop, p.initial_stop, p.duration_stage), (100.0, 95.0, td.STAGE_DAY2))
        self.assertEqual(account.closed_trades, [])
        self.assertNotIn("TRADE_DURATION_EXIT", [event for event, _ in journal])


class CommittedEvidencePricingTests(Day1On, RuntimeCase):
    def test_provider_revision_does_not_reprice_the_day1_decision(self):
        """Codex MED: with evidence catch-up ON, the duration rule prices from committed evidence."""
        self.seed(position())
        evidence = Path(self.tmp.name) / "market_evidence.db"
        config = RuntimeConfig(db_path=self.db, enabled_symbols=("XAUUSD", "EURUSD"), v2_position_catch_up=True,
                               v2_position_catch_up_symbols=("XAUUSD", "EURUSD"), market_evidence_path=evidence)
        committed = Prices(lambda at: 103.0 if at >= WED_CLOSE - FIVE else 100.0, WED_OPEN - 4 * FIVE)
        revised = Prices(lambda at: 101.0 if at == WED_CLOSE - FIVE else 103.0 if at >= WED_CLOSE else 100.0,
                         WED_OPEN - 4 * FIVE)
        runtime = OperationalRuntime(config, market_provider=revised, ai_provider=DeterministicAIProvider(),
                                     macro_provider=macro_fixture(),
                                     instruments={"XAUUSD": instrument(), "EURUSD": EUR}, clock=lambda: THU_SLOT)
        self.addCleanup(runtime.close)
        self.assertIsNone(runtime._ingest_evidence("XAUUSD", committed.load_snapshot("XAUUSD", WED_CLOSE), WED_CLOSE))
        self.assertEqual(runtime.manage_trade_duration(THU_SLOT), {"XAUUSD": "CHECKED"})
        account, journal = self.paper()
        p = account.open_positions["XAUUSD"]  # committed 103.0 (+0.6R) keeps; the revised 101.0 would have closed
        self.assertEqual((p.stop, p.duration_stage), (100.0, td.STAGE_DAY2))
        moved = next(payload for event, payload in journal if event == td.STOP_MOVED_TO_ENTRY)
        self.assertEqual((moved["price"], moved["unrealized_r"]), (103.0, "0.6"))


class ClosedAlertStopTests(Day1On, RuntimeCase):
    def test_different_run_close_keeps_originating_signal_time(self):
        self.seed(position())
        store = Store(self.db)
        self.addCleanup(store.close)
        account, orders, fills = store.load_paper(ACCOUNT)
        broker = PaperBroker(account)
        broker.orders, broker.fills = orders, fills
        broker.close_market("XAUUSD", 103.0, THU_SLOT, exit_reason="test_close", decision_run_id="run-later")
        store.save_paper(broker)
        row = store.db.execute("SELECT * FROM journal WHERE event_type='POSITION_CLOSED'").fetchone()
        self.assertEqual(row["run_id"], "run-later")
        signal_at = WED_OPEN - FIVE
        review_signal_at = signal_at - FIVE
        original_review = {"as_of": review_signal_at.isoformat(), "setup": {"setup_id": "S-ORIGINAL"}}
        later_review = {"as_of": THU_SLOT.isoformat(), "setup": {"setup_id": "S-LATER"}}
        with patch.object(store, "review_report", side_effect=lambda run: original_review if run == "run-open"
                          else later_review):
            block = trade_block(store, row, later_review)
            self.assertEqual(block["setup_id"], "S-ORIGINAL")
            self.assertEqual(block["signal_at_utc"], signal_at.isoformat())
            self.assertEqual(block["closed_at_utc"], THU_SLOT.isoformat())
            # Missing order timestamp uses only the submitting review; missing both stays NO_DATA.
            orders["ord-1"].as_of = None
            store.save_paper(broker)
            self.assertEqual(trade_block(store, row, later_review)["signal_at_utc"], review_signal_at.isoformat())
            original_review.pop("as_of")
            self.assertNotIn("signal_at_utc", trade_block(store, row, later_review))

    def test_closed_alert_after_breakeven_move_shows_the_position_stop(self):
        """Codex MED: the POSITION_CLOSED alert reports the moved stop, not the original order SL."""
        self.seed(position())

        def price(at):
            if WED_CLOSE - FIVE <= at < WED_CLOSE + timedelta(hours=2):
                return 103.0
            return 99.0 if at >= WED_CLOSE + timedelta(hours=2) else 100.0
        runtime = self.runtime(Prices(price, WED_OPEN - 4 * FIVE), THU_SLOT)
        self.assertNotEqual(runtime.run_cycle("XAUUSD", THU_SLOT), "ERROR")
        self.assertEqual(self.closed_trade()[0].reason, "stop")
        store = Store(self.db)
        try:
            row = store.db.execute("SELECT * FROM journal WHERE event_type='POSITION_CLOSED'").fetchone()
            block = trade_block(store, row, None)
        finally:
            store.close()
        self.assertEqual((block["stop_loss"], block["entry"], block["take_profit"]), (100.0, 100.0, 110.0))
        self.assertEqual(block["rr"], "1:2.00")  # R:R stays on the original stop distance


class FridayAndOffSessionTests(RuntimeCase):
    def test_friday_ny_close_closes_everything_out_of_session(self):
        p = position(opened_at=THU_OPEN)
        self.seed(p)
        runtime = self.runtime(Prices(lambda at: 109.0 if at >= td.day1_close(p) - FIVE else 100.0,
                                      THU_OPEN - 4 * FIVE), FRI_CLOSE)
        self.assertEqual(runtime.manage_trade_duration(FRI_CLOSE), {"XAUUSD": "CHECKED"})
        trade, _ = self.closed_trade()
        self.assertEqual((trade.reason, trade.exit_price, trade.exited_at), (td.CLOSE_FRIDAY, 109.0, FRI_FLATTEN))

    def test_friday_open_closes_at_its_first_close_even_in_profit(self):
        opened = datetime(2026, 1, 16, 14, tzinfo=UTC)
        self.seed(position(opened_at=opened))
        runtime = self.runtime(Prices(lambda at: 109.0, opened - 4 * FIVE), FRI_CLOSE + timedelta(minutes=10))
        runtime.manage_trade_duration(FRI_CLOSE + timedelta(minutes=10))
        trade, _ = self.closed_trade()
        self.assertEqual((trade.reason, trade.exited_at), (td.CLOSE_FRIDAY, FRI_FLATTEN))

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




class BreakevenShortGapTests(unittest.TestCase):
    def test_moved_short_stop_fills_at_gap_open_without_spread_markup(self):
        from execution.trade_manager import TradeManager
        p = position('SHORT', 1.11936, 1.12100, 1.11444, symbol='EURUSD')
        p.quantity, p.contract_multiplier, p.cost_rate = 0.08921052631578948, 100000.0, 0.0
        td.move_stop_to_entry(p)
        # Use the durable round-trip too: the stop really is at entry.
        p = paper_decode('PaperPosition', paper_encode(p))
        account = PaperAccount('1.0', 'paper-main', 10000, 10000, 10000, open_positions={'EURUSD': p})
        broker = PaperBroker(account, EUR)
        manager = TradeManager(account, broker)
        at = WED_CLOSE + FIVE
        bar = {'symbol': 'EURUSD', 'timestamp': at, 'is_closed': True,
               'open': 1.11974, 'high': 1.11990, 'low': 1.11930, 'close': 1.11950}
        trade, = manager.process_bar(bar)
        self.assertEqual((p.stop, p.initial_stop, trade.exit_price, trade.reason),
                         (1.11936, 1.12100, 1.11974, 'stop'))
        self.assertAlmostEqual(trade.net_pnl, -3.39, places=8)
        self.assertEqual(trade.cost, 0)
        self.assertEqual(manager.process_bar(bar), [])

    def test_no_gap_crossing_fills_at_entry_and_charges_cost_once(self):
        from execution.trade_manager import TradeManager
        p = position('SHORT', 1.11936, 1.12100, 1.11444, symbol='EURUSD')
        p.quantity, p.contract_multiplier, p.cost_rate = 0.1, 100000.0, 0.01
        td.move_stop_to_entry(p)
        account = PaperAccount('1.0', 'paper-main', 10000, 10000, 10000, open_positions={'EURUSD': p})
        manager = TradeManager(account, PaperBroker(account, EUR))
        trade, = manager.process_bar({'symbol': 'EURUSD', 'timestamp': WED_CLOSE + FIVE, 'is_closed': True,
                                     'open': 1.11900, 'high': 1.11974, 'low': 1.11890, 'close': 1.11950})
        self.assertEqual(trade.exit_price, p.entry_price)
        self.assertEqual(trade.gross_pnl, 0)
        self.assertEqual(trade.net_pnl, -trade.cost)
        self.assertEqual(account.realized_pnl, -trade.cost)


def setUpModule():  # DEC-XAU-OFF-1: these tests drive XAUUSD cycles; the pause itself is in test_pause_xauusd
    import unittest.mock
    unittest.enterModuleContext(unittest.mock.patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))


if __name__ == "__main__":
    unittest.main()
