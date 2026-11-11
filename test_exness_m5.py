"""M5 feed: UTC bars from ticks, per-symbol quotes, reconnect and the session combo."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import logging
import unittest
from zoneinfo import ZoneInfo

from execution.broker_port import BrokerBalance
from execution.exness_brakes import ExnessBrakes
from execution.exness_broker import REAL_CONFIRM, ExnessBroker
from execution.exness_m5 import (FEED_SYMBOLS, M5Aggregator, M5Feed, TickStream, candle_query, candles_path,
                                floor_m5_utc, m5_feed_enabled, parse_candle_payload, parse_tick_frame)
from execution.exness_session import ExnessSession
from execution.exness_stream import QuoteBook
from execution.exness_transport import FakeExnessTransport
from execution.session_combo import range_window


UTC = timezone.utc
NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")


def utc(y, m, d, hh, mm, ss=0, us=0):
    return datetime(y, m, d, hh, mm, ss, us, tzinfo=UTC)


def armed(**extra):
    env = {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_EXNESS_REAL_EXECUTION": "1",
           "AI_FLOOR_EXNESS_REAL_CONFIRM": REAL_CONFIRM, "AI_FLOOR_SESSION_COMBO": "1",
           "AI_FLOOR_EXNESS_M5_FEED": "1"}
    env.update(extra)
    return env


def jpy_payload():
    return {"volume_min": "0.01", "volume_step": "0.01", "point_digits": 3,
            "contractSize": "100000", "profitRate": "0.01"}


def usd_payload():
    return {"volume_min": "0.01", "volume_step": "0.01", "point_digits": 5,
            "contractSize": "100000", "profitRate": "1"}


class Clock:
    def __init__(self, at):
        self.at = at

    def __call__(self):
        return self.at


def session(at, env=None):
    env = armed() if env is None else env
    clock = Clock(at)
    fake = FakeExnessTransport(clock=clock)
    fake.balance = BrokerBalance(Decimal("500"), Decimal("500"), Decimal("0"), "USD")
    fake.instruments = ("EURUSD", "GBPUSD", "USDJPY")
    fake.conditions = {"USDJPY": jpy_payload(), "EURUSD": usd_payload(), "GBPUSD": usd_payload()}
    broker = ExnessBroker(fake, env=env, brakes=ExnessBrakes.from_env(fake, env), clock=clock)
    return clock, fake, ExnessSession(broker, env=env, clock=clock)


def tick(symbol, bid, ask, at):
    return {"instrument": symbol, "bid": bid, "ask": ask, "time": at.isoformat()}


class BarTests(unittest.TestCase):
    def test_boundary_tick_opens_the_next_bar_and_dst_offsets_differ(self):
        winter = utc(2026, 1, 15, 0, 0)
        summer = utc(2026, 7, 15, 23, 0)
        self.assertEqual(floor_m5_utc(winter).astimezone(NY).hour, 19)
        self.assertEqual(floor_m5_utc(summer).astimezone(NY).hour, 19)
        self.assertNotEqual(winter.astimezone(NY).utcoffset(), summer.astimezone(NY).utcoffset())
        agg = M5Aggregator("USDJPY")
        just_before = utc(2026, 1, 15, 6, 59, 59, 999000)
        agg.on_tick(Decimal("150.10"), just_before)
        self.assertEqual(agg._bar["open"], utc(2026, 1, 15, 6, 55))
        closed = agg.on_tick(Decimal("150.40"), utc(2026, 1, 15, 7, 0))
        self.assertEqual(closed[0].open_time, utc(2026, 1, 15, 6, 55))
        self.assertEqual(closed[0].close, Decimal("150.10"))
        self.assertEqual(agg._bar["open"], utc(2026, 1, 15, 7, 0))
        self.assertEqual(agg._bar["open_px"], Decimal("150.40"))

    def test_clock_close_emits_the_0155_bar_at_0200_and_a_jump_is_a_gap(self):
        agg = M5Aggregator("USDJPY")
        agg.on_tick(Decimal("150.00"), utc(2026, 1, 15, 6, 55))
        agg.on_tick(Decimal("149.80"), utc(2026, 1, 15, 6, 57))
        emitted = agg.close_elapsed(utc(2026, 1, 15, 7, 0))
        self.assertEqual(emitted[0].open_time.astimezone(NY).strftime("%H:%M"), "01:55")
        self.assertEqual((emitted[0].high, emitted[0].low, emitted[0].close),
                         (Decimal("150.00"), Decimal("149.80"), Decimal("149.80")))
        self.assertIsNone(agg._bar)
        agg.on_tick(Decimal("150.10"), utc(2026, 1, 15, 7, 0))
        agg.on_tick(Decimal("150.20"), utc(2026, 1, 15, 7, 12))
        self.assertEqual(agg.gaps, [utc(2026, 1, 15, 7, 5)])

    def test_symbols_do_not_share_a_quote_and_a_drop_stales_every_symbol(self):
        feed = M5Feed(QuoteBook())
        at = utc(2026, 1, 15, 7, 0)
        feed.on_tick("USDJPY", Decimal("150.20"), Decimal("150.21"), at)
        feed.on_tick("GBPUSD", Decimal("1.25000"), Decimal("1.25010"), at)
        self.assertEqual(feed.quotes.quote("USDJPY").bid, Decimal("150.20"))
        self.assertEqual(feed.quotes.quote("GBPUSD").ask, Decimal("1.25010"))
        self.assertNotEqual(feed.quotes.quote("USDJPY").bid, feed.quotes.quote("GBPUSD").bid)
        self.assertTrue(feed.quotes.fresh(at, 5, "USDJPY"))
        feed.mark_disconnected()
        self.assertFalse(feed.quotes.fresh(at, 5, "USDJPY"))
        self.assertFalse(feed.quotes.fresh(at, 5, "GBPUSD"))
        feed.on_tick("GBPUSD", Decimal("1.25020"), Decimal("1.25030"), at)
        self.assertFalse(feed.quotes.fresh(at, 5, "USDJPY"))
        self.assertTrue(feed.quotes.fresh(at, 5, "GBPUSD"))

    def test_unknown_frame_is_ignored_and_reconnect_backfills_or_keeps_the_gap(self):
        self.assertIsNone(parse_tick_frame({"id": "floor-ticks", "code": 200}))
        self.assertIsNone(parse_tick_frame("not-json"))
        feed = M5Feed(QuoteBook())
        stream = TickStream(feed)
        opened = stream.on_open(utc(2026, 1, 15, 0, 0))
        self.assertEqual(set(opened["subscribe"]["instruments"]), set(FEED_SYMBOLS))
        self.assertEqual(opened["subscribe"]["event"], "ticks")
        stream.on_message(tick("USDJPY", "150.10", "150.11", utc(2026, 1, 15, 0, 0)), utc(2026, 1, 15, 0, 0))
        stream.on_message(tick("USDJPY", "150.20", "150.21", utc(2026, 1, 15, 0, 10)), utc(2026, 1, 15, 0, 10))
        self.assertEqual(feed.aggs["USDJPY"].gaps, [utc(2026, 1, 15, 0, 5)])
        self.assertIsNone(stream.poll(utc(2026, 1, 15, 0, 10) + timedelta(seconds=10)))
        self.assertEqual(stream.poll(utc(2026, 1, 15, 0, 10) + timedelta(seconds=16)), 1)
        self.assertFalse(feed.quotes.fresh(utc(2026, 1, 15, 0, 26), 5, "USDJPY"))
        missing = utc(2026, 1, 15, 0, 5)
        payload = {"candles": [{"open_time": missing.isoformat(), "open": "150.12", "high": "150.18",
                                "low": "150.11", "close": "150.15"}]}
        self.assertEqual(candle_query("USDJPY", missing, missing + timedelta(minutes=5))["timeframe"], "M5")
        self.assertTrue(candles_path("123456").endswith("/candles"))
        filled = stream.reconnect(utc(2026, 1, 15, 0, 15), {"USDJPY": payload})["subscribe"]["instruments"]
        self.assertIn("USDJPY", filled)
        self.assertEqual(feed.aggs["USDJPY"].gaps, [])
        self.assertEqual(len(feed.aggs["GBPUSD"].closed), 0)
        before = len(feed.aggs["USDJPY"].closed)
        with self.assertLogs("ai_floor.exness_m5", level=logging.INFO) as logs:
            feed.accept_candles("USDJPY", {"candles": [{"open": 1}]})
        self.assertIn("reason=schema_unknown", "\n".join(logs.output))
        self.assertEqual(len(feed.aggs["USDJPY"].closed), before)
        self.assertEqual(parse_candle_payload({"candles": [{"open": 1}]}, "USDJPY")[1], "schema_unknown")


class FeedSessionTests(unittest.TestCase):
    def test_flag_off_does_not_build_a_feed_and_the_stream_drives_london_then_the_fix(self):
        self.assertFalse(m5_feed_enabled({}))
        self.assertFalse(m5_feed_enabled({"AI_FLOOR_EXNESS_M5_FEED": "1", "AI_FLOOR_BROKER": "oanda"}))
        at = datetime(2026, 1, 15, 2, 0, tzinfo=NY)
        _clock, fake, idle = session(at, armed(**{"AI_FLOOR_EXNESS_M5_FEED": "0"}))
        self.assertIsNone(idle._feed)
        idle.manage(at)
        self.assertEqual(fake.commands, [])
        clock, fake, live = session(at)
        self.assertIsNotNone(live._feed)
        start, end = range_window(at.date())
        live._feed.on_tick("USDJPY", Decimal("150.10"), Decimal("150.11"), start.astimezone(UTC))
        live._feed.on_tick("USDJPY", Decimal("150.20"), Decimal("150.21"), start.astimezone(UTC) + timedelta(minutes=2))
        live._feed.on_tick("GBPUSD", Decimal("1.20000"), Decimal("1.20010"), start.astimezone(UTC))
        live._feed.on_tick("USDJPY", Decimal("150.00"), Decimal("150.01"), (end - timedelta(minutes=5)).astimezone(UTC))
        live._feed.on_tick("USDJPY", Decimal("150.25"), Decimal("150.26"), end.astimezone(UTC))
        clock.at = at
        live.manage(at)
        markets = [command for command in fake.commands if command.kind == "MARKET"]
        self.assertEqual(len(markets), 1)
        self.assertEqual(markets[0].symbol, "USDJPY")
        self.assertEqual(markets[0].side, "LONG")
        self.assertEqual(markets[0].price, Decimal("150.26"))
        self.assertEqual(fake.quotes.quote("GBPUSD").bid, Decimal("1.20000"))
        fix_at = datetime(2026, 1, 15, 16, 0, tzinfo=LONDON)
        clock, fake, live = session(fix_at)
        live._feed.on_tick("GBPUSD", Decimal("1.25000"), Decimal("1.25010"), datetime(2026, 1, 15, 15, 45, tzinfo=LONDON))
        live._feed.on_tick("EURUSD", Decimal("1.08000"), Decimal("1.08010"), datetime(2026, 1, 15, 15, 45, tzinfo=LONDON))
        live._feed.on_tick("GBPUSD", Decimal("1.25150"), Decimal("1.25160"), datetime(2026, 1, 15, 15, 55, tzinfo=LONDON))
        live._feed.on_tick("GBPUSD", Decimal("1.25140"), Decimal("1.25150"), fix_at)
        clock.at = fix_at
        live.manage(fix_at)
        markets = [command for command in fake.commands if command.kind == "MARKET"]
        self.assertEqual([command.symbol for command in markets], ["GBPUSD"])
        self.assertEqual(markets[0].side, "SHORT")
        bare = armed()
        del bare["AI_FLOOR_EXNESS_REAL_EXECUTION"]
        del bare["AI_FLOOR_EXNESS_REAL_CONFIRM"]
        clock, fake, live = session(at, bare)
        live._feed.on_tick("USDJPY", Decimal("150.10"), Decimal("150.11"), start.astimezone(UTC))
        live._feed.on_tick("USDJPY", Decimal("150.20"), Decimal("150.21"), start.astimezone(UTC) + timedelta(minutes=2))
        live._feed.on_tick("USDJPY", Decimal("150.00"), Decimal("150.01"), (end - timedelta(minutes=5)).astimezone(UTC))
        live._feed.on_tick("USDJPY", Decimal("150.25"), Decimal("150.26"), end.astimezone(UTC))
        clock.at = at
        live.manage(at)
        self.assertEqual(fake.commands, [])
