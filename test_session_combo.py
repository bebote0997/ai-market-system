"""Session combo rules. See execution/session_combo.py."""
from datetime import datetime, timedelta
from decimal import Decimal
import logging
from zoneinfo import ZoneInfo
import unittest

from execution.broker_port import BrokerBalance
from execution.exness_brakes import ExnessBrakes
from execution.exness_broker import REAL_CONFIRM, ExnessBroker
from execution.exness_session import ExnessSession
from execution.exness_sizing import InstrumentSpec
from execution.exness_transport import FakeExnessTransport, Position
from execution.session_combo import (FIX_SYMBOLS, M5Bar, SessionCombo, asian_range, combo_enabled, fix_signal,
                                    london_fill, range_window, size_for_cap)


NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")


def bar(at, open_, high, low, close):
    return M5Bar(at, Decimal(open_), Decimal(high), Decimal(low), Decimal(close))


def ny(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=NY)


def lon(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=LONDON)


def spec_jpy():
    return InstrumentSpec(Decimal("0.01"), Decimal("0.01"), 3, Decimal("100000"), Decimal("0.01"))


def spec_usd():
    return InstrumentSpec(Decimal("0.01"), Decimal("0.01"), 5, Decimal("100000"), Decimal("1"))


def payload(spec):
    return {"volume_min": "0.01", "volume_step": "0.01", "point_digits": spec.point_digits,
            "contractSize": format(spec.contract_size, "f"), "profitRate": format(spec.profit_rate, "f")}


def env_armed():
    return {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_EXNESS_REAL_EXECUTION": "1",
            "AI_FLOOR_EXNESS_REAL_CONFIRM": REAL_CONFIRM, "AI_FLOOR_SESSION_COMBO": "1"}


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def world(at, env=None):
    env = env_armed() if env is None else env
    clock = Clock(at)
    fake = FakeExnessTransport(clock=clock)
    fake.balance = BrokerBalance(Decimal("500"), Decimal("500"), Decimal("0"), "USD")
    fake.instruments = ("EURUSD", "GBPUSD", "USDJPY")
    fake.conditions = {"USDJPY": payload(spec_jpy()), "EURUSD": payload(spec_usd()), "GBPUSD": payload(spec_usd())}
    fake.push_tick(Decimal("150.200"), Decimal("150.202"), at)
    broker = ExnessBroker(fake, env=env, brakes=ExnessBrakes.from_env(fake, env), clock=clock)
    return clock, fake, broker, SessionCombo(broker)


def range_pair(day, high=("150.20", "150.10"), low=("150.15", "150.00")):
    start, end = range_window(day)
    return [bar(start, high[1], high[0], high[1], high[1]),
            bar(end - timedelta(minutes=5), low[0], low[0], low[1], low[1])]


class RangeTests(unittest.TestCase):
    def test_window_follows_new_york_across_dst(self):
        winter_start, winter_end = range_window(ny(2026, 1, 15, 2, 0).date())
        summer_start, _summer_end = range_window(ny(2026, 7, 16, 2, 0).date())
        self.assertEqual((winter_end - winter_start), timedelta(hours=7))
        self.assertNotEqual(winter_start.utcoffset(), summer_start.utcoffset())
        self.assertEqual(winter_start.astimezone(ZoneInfo("UTC")).hour, 0)
        self.assertEqual(summer_start.astimezone(ZoneInfo("UTC")).hour, 23)

    def test_one_bar_spanning_the_range_skips(self):
        day = ny(2026, 1, 15, 2, 0).date()
        start, end = range_window(day)
        bars = [bar(start, "150.00", "150.20", "150.00", "150.10"),
                bar(end - timedelta(minutes=5), "150.10", "150.20", "150.00", "150.10")]
        high, low, window, reason = asian_range(bars, day)
        self.assertIsNone(reason)
        self.assertTrue(any(item.high >= high and item.low <= low for item in window))
        _clock, fake, _broker, combo = world(ny(2026, 1, 15, 2, 0))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(ny(2026, 1, 15, 2, 0), bars)
        text = "\n".join(logs.output)
        self.assertIn("event=LONDON_OCO_SKIPPED", text)
        self.assertIn("reason=both_sides_one_bar", text)
        self.assertEqual(fake.commands, [])

    def test_breakout_bar_crossing_both_is_a_skip(self):
        day = ny(2026, 1, 15, 2, 0).date()
        bars = range_pair(day)
        high, low, _window, reason = asian_range(bars, day)
        self.assertIsNone(reason)
        both = bar(ny(2026, 1, 15, 2, 0), "150.10", str(high + Decimal("0.05")), str(low - Decimal("0.05")), "150.10")
        self.assertEqual(london_fill(bars + [both], day, high, low), (None, "both_sides_one_bar"))

    def test_unfilled_order_cancels_at_0500_and_a_fill_exits_at_1100(self):
        day = ny(2026, 1, 15, 2, 30).date()
        bars = range_pair(day)
        clock, fake, _broker, combo = world(ny(2026, 1, 15, 2, 30))
        combo.tick(clock.at, bars)
        self.assertEqual(fake.commands, [])
        clock.at = ny(2026, 1, 15, 5, 0)
        fake.push_tick(Decimal("150.10"), Decimal("150.12"), clock.at)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(clock.at, bars)
        self.assertIn("reason=unfilled_0500", "\n".join(logs.output))
        self.assertEqual(fake.commands, [])
        clock.at = ny(2026, 1, 16, 2, 10)
        fake.push_tick(Decimal("150.25"), Decimal("150.26"), clock.at)
        morning = range_pair(clock.at.date()) + [bar(ny(2026, 1, 16, 2, 0), "150.18", "150.25", "150.12", "150.22")]
        combo.tick(clock.at, morning)
        self.assertEqual(fake.commands[0].side, "LONG")
        self.assertEqual(fake.commands[0].volume, Decimal("0.02"))
        clock.at = ny(2026, 1, 16, 11, 0)
        fake.push_tick(Decimal("150.30"), Decimal("150.32"), clock.at)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(clock.at, morning)
        self.assertIn("event=TIME_EXIT", "\n".join(logs.output))
        self.assertEqual(fake.commands[-1].kind, "CLOSE")


class FixTests(unittest.TestCase):
    def gbp(self, close):
        return [bar(lon(2026, 1, 15, 15, 45), "1.25000", "1.25200", "1.24980", close),
                bar(lon(2026, 1, 15, 15, 55), close, close, close, close),
                bar(lon(2026, 1, 15, 16, 0), close, close, close, close)]

    def test_threshold_and_direction(self):
        at = lon(2026, 1, 15, 16, 0)
        self.assertIsNone(fix_signal(self.gbp("1.25140"), "GBPUSD", at))  # 14.0 pips
        up = fix_signal(self.gbp("1.25150"), "GBPUSD", at)
        self.assertEqual(up[0], "SHORT")
        down = fix_signal(self.gbp("1.24850"), "GBPUSD", at)
        self.assertEqual(down[0], "LONG")
        self.assertEqual(FIX_SYMBOLS[0], "GBPUSD")

    def test_gbp_is_taken_before_eur_and_exits_after_thirty_minutes(self):
        at = lon(2026, 1, 15, 16, 0)
        bars = self.gbp("1.25150")
        clock, fake, _broker, combo = world(at)
        fake.push_tick(Decimal("1.25150"), Decimal("1.25160"), at)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, bars)
        self.assertIn("event=FIX_ENTRY", "\n".join(logs.output))
        self.assertEqual(fake.commands[0].symbol, "GBPUSD")
        self.assertEqual(fake.commands[0].side, "SHORT")
        self.assertIsNone(fake.commands[0].target)
        clock.at = at + timedelta(minutes=30)
        fake.push_tick(Decimal("1.25100"), Decimal("1.25110"), clock.at)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(clock.at, bars)
        self.assertIn("reason=fix_30m", "\n".join(logs.output))
        self.assertEqual(fake.commands[-1].kind, "CLOSE")

    def test_london_clock_not_a_fixed_new_york_hour(self):
        stamp = lon(2026, 10, 28, 15, 45)
        self.assertEqual(stamp.astimezone(NY).hour, 11)

    def test_minimum_lot_over_five_dollars_is_skipped(self):
        lots, reason = size_for_cap(Decimal("500"), Decimal("500"), Decimal("150.20"), Decimal("149.69"), spec_jpy())
        self.assertEqual((lots, reason), (Decimal("0"), "min_volume_over_cap"))
        lots, reason = size_for_cap(Decimal("500"), Decimal("500"), Decimal("150.20"), Decimal("150.00"), spec_jpy())
        self.assertEqual((lots, reason), (Decimal("0.02"), None))
        at = ny(2026, 1, 15, 2, 10)
        bars = range_pair_wide()
        _clock, fake, _broker, combo = world(at)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, bars)
        self.assertIn("reason=min_volume_over_cap", "\n".join(logs.output))
        self.assertEqual(fake.commands, [])

    def test_open_position_and_brake_block_a_new_order(self):
        at = lon(2026, 1, 15, 16, 0)
        bars = self.gbp("1.25150")
        _clock, fake, _broker, combo = world(at)
        fake.positions.append(Position("9", "USDJPY", "LONG", Decimal("0.01"), Decimal("150"), comment="manual"))
        combo.tick(at, bars)
        self.assertEqual(fake.commands, [])
        fake.positions.clear()
        fake.set_flag("exness_brake_halt", "1")
        fake.set_flag("exness_brake_reason", "daily_loss")
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, bars)
        self.assertIn("reason=daily_loss", "\n".join(logs.output))
        self.assertEqual(fake.commands, [])

    def test_flag_off_is_idle_and_flag_on_without_the_double_key_sends_nothing(self):
        self.assertFalse(combo_enabled({}))
        self.assertFalse(combo_enabled({"AI_FLOOR_SESSION_COMBO": "1", "AI_FLOOR_BROKER": "oanda"}))
        self.assertTrue(combo_enabled(env_armed()))
        at = ny(2026, 1, 15, 2, 10)
        bars = range_pair(at.date()) + [bar(ny(2026, 1, 15, 2, 0), "150.18", "150.25", "150.12", "150.22")]
        clock, fake, broker, _combo = world(at, {"AI_FLOOR_BROKER": "exness"})
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as off:
            session = ExnessSession(broker, env={"AI_FLOOR_BROKER": "exness"}, clock=clock)
        self.assertIn("event=SESSION_COMBO_OFF", "\n".join(off.output))
        self.assertIsNone(session._combo)
        session.manage(at, bars)
        self.assertEqual(fake.commands, [])
        bare = {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_SESSION_COMBO": "1"}
        clock, fake, broker, _combo = world(at, bare)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as started:
            session = ExnessSession(broker, env=bare, clock=clock)
        self.assertIn("event=SESSION_COMBO_ON", "\n".join(started.output))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            session.manage(at, bars)
        self.assertEqual(fake.commands, [])
        self.assertTrue(any("SIZE_SKIP" in line or "LONDON_OCO" in line for line in logs.output))
        self.assertNotIn("123456", "\n".join(logs.output))


def range_pair_wide():
    day = ny(2026, 1, 15, 2, 0).date()
    start, end = range_window(day)
    bars = [bar(start, "150.10", "150.20", "150.10", "150.10"),
            bar(end - timedelta(minutes=5), "149.80", "149.90", "149.69", "149.80"),
            bar(ny(2026, 1, 15, 2, 0), "150.10", "150.30", "150.05", "150.25")]
    return bars
