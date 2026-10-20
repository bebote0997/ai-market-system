"""Replay-only entry search (replay/entry_search.py): no lookahead in every feature and signal, resampling, sessions,
fills at the next 5m open, exit geometry, news approximation."""
import math
import unittest
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import intraday_backtest as bt

UTC = timezone.utc


def ohlc_frame(index, close, spread=None, rng=None):
    rng = rng or np.random.default_rng(0)
    close = np.asarray(close, dtype=float)
    open_ = np.concatenate([[close[0]], close[:-1]])
    wick = np.abs(rng.normal(0, 0.00015, len(close)))
    frame = pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) + wick,
                          "Low": np.minimum(open_, close) - wick[::-1], "Close": close}, index=index)
    frame["symbol"], frame["is_closed"] = "EURUSD", True
    if spread is not None:
        frame["BidClose"], frame["AskClose"] = close - spread / 2, close + spread / 2
    return frame


def weekday_index(start, days):
    """5m bar starts, FX week only (Sunday 17:00 -> Friday 17:00 New York)."""
    index = pd.date_range(start, periods=days * 288, freq="5min", tz=UTC)
    local = index.tz_convert(es.NY)
    hour = local.hour + local.minute / 60
    keep = ~((local.weekday == 5) | ((local.weekday == 4) & (hour >= 17)) | ((local.weekday == 6) & (hour < 17)))
    return index[keep]


def synthetic_frames(days=60, seed=3):
    """Random-walk 5m mid bars + 15m/1h aggregated from them (bar start labels)."""
    rng = np.random.default_rng(seed)
    index = weekday_index("2026-01-04 22:00", days)
    close = 1.10 + np.cumsum(rng.normal(0, 0.00025, len(index)))
    five = ohlc_frame(index, close, spread=0.00015, rng=rng)
    frames = {"5m": five}
    for tf, rule in (("15m", "15min"), ("1h", "1h")):
        agg = five.resample(rule, label="left", closed="left").agg(
            {"Open": "first", "High": "max", "Low": "min", "Close": "last"}).dropna()
        agg["symbol"], agg["is_closed"] = "EURUSD", True
        frames[tf] = agg
    return frames


def truncate(frames, cutoff):
    """Only bars ENDED by ``cutoff`` (what a live process would have seen at that moment)."""
    durations = {"5m": "5min", "15m": "15min", "1h": "1h"}
    return {tf: f[f.index + pd.Timedelta(durations[tf]) <= cutoff] for tf, f in frames.items()}


def mirror(frames):
    out = {}
    for tf, f in frames.items():
        g = f.copy()
        g["Open"], g["High"], g["Low"], g["Close"] = -f["Open"], -f["Low"], -f["High"], -f["Close"]
        if "BidClose" in f:
            g["BidClose"], g["AskClose"] = -f["AskClose"], -f["BidClose"]
        out[tf] = g
    return out


class Resampling(unittest.TestCase):
    def setUp(self):
        index = pd.date_range("2026-03-02 20:00", periods=40, freq="1h", tz=UTC)  # EST (UTC-5)
        self.hours = ohlc_frame(index, 1.10 + np.arange(40) * 1e-4)

    def test_d1_is_the_17_to_17_new_york_day_and_visible_only_at_its_close(self):
        d1 = es.resample_closed(self.hours, 24)
        first_full = d1.index[1]
        self.assertEqual(first_full, pd.Timestamp("2026-03-02 22:00", tz=UTC))  # 17:00 EST open
        self.assertEqual(d1["available"].iloc[1], pd.Timestamp("2026-03-03 22:00", tz=UTC).value)
        day = self.hours[(self.hours.index >= "2026-03-02 22:00") & (self.hours.index < "2026-03-03 22:00")]
        self.assertAlmostEqual(d1["High"].iloc[1], day["High"].max())
        self.assertAlmostEqual(d1["Close"].iloc[1], day["Close"].iloc[-1])
        bars = es.make_bars(d1, end=d1["available"].to_numpy())
        close = pd.Timestamp("2026-03-03 22:00", tz=UTC).value
        self.assertEqual(bars.asof(close - 1), 0)  # one ns before the 17:00 close: still the previous day
        self.assertEqual(bars.asof(close), 1)

    def test_d1_open_follows_new_york_dst(self):
        index = pd.date_range("2026-03-09 18:00", periods=30, freq="1h", tz=UTC)  # EDT (UTC-4) after Mar 8
        d1 = es.resample_closed(ohlc_frame(index, np.full(30, 1.1)), 24)
        self.assertEqual(d1.index[1], pd.Timestamp("2026-03-09 21:00", tz=UTC))

    def test_h4_buckets_anchored_at_the_open_and_incomplete_buckets_dropped(self):
        h4 = es.resample_closed(self.hours, 4)
        opens = [t for t in h4.index if t >= pd.Timestamp("2026-03-02 22:00", tz=UTC)]
        self.assertEqual([t.hour for t in opens[:6]], [22, 2, 6, 10, 14, 18])
        self.assertTrue((h4["available"] - h4.index.as_unit("ns").asi8 <= 4 * es.HOUR).all())
        last_end = self.hours.index[-1] + pd.Timedelta("1h")
        self.assertTrue((h4["available"] <= last_end.value).all())

    def test_resampling_is_prefix_invariant(self):
        full = es.resample_closed(self.hours, 4)
        cut = pd.Timestamp("2026-03-03 13:00", tz=UTC)
        part = es.resample_closed(self.hours[self.hours.index + pd.Timedelta("1h") <= cut], 4)
        visible = full[full["available"] <= cut.value]
        pd.testing.assert_frame_equal(part, visible)


class Swings(unittest.TestCase):
    def test_pivot_visible_only_after_the_next_bar_closes(self):
        high = np.array([1.0, 2.0, 1.5, 1.4, 3.0, 1.0])
        low = high - 0.5
        sh, _, ph, _ = es.confirmed_swings(high, low)
        self.assertTrue(math.isnan(sh[1]))  # the pivot bar itself
        self.assertEqual(sh[2], 2.0)  # confirmed at the close of bar 2
        self.assertEqual(sh[4], 2.0)  # bar 4's own pivot needs bar 5
        self.assertEqual(sh[5], 3.0)
        self.assertEqual(ph, [(1, 2.0), (4, 3.0)])

    def test_bos_compares_with_swings_confirmed_before_the_bar(self):
        bars = es.Bars(np.arange(4), np.arange(4) + 1, np.zeros(4), np.zeros(4), np.zeros(4),
                       np.array([1.0, 2.5, 2.5, 2.5]))
        bos = es.bos_up(bars, np.array([np.nan, 2.0, 3.0, 3.0]))
        self.assertEqual(list(bos), [False, False, True, False])


class Features(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = synthetic_frames()
        cls.ctx = es.Context(cls.frames)

    def test_asian_and_london_ranges_use_only_their_windows(self):
        frames = {tf: f.copy() for tf, f in self.frames.items()}
        five = frames["5m"]
        day = pd.Timestamp("2026-02-04").date()
        at = lambda clock: pd.Timestamp(f"{day} {clock}", tz=es.NY).tz_convert(UTC)  # noqa: E731
        five.loc[at("03:00"), "High"] = 2.0  # first bar after the Asian window
        five.loc[at("18:55") - pd.Timedelta("1D"), "Low"] = 0.5  # last bar before it
        five.loc[at("08:00"), "Low"] = 0.4  # first bar after the London range
        ctx = es.Context(frames)
        ordinal = (pd.Timestamp(day) - pd.Timestamp("1970-01-01")).days
        asian = es.session_ranges(ctx, ctx.views[1], es.ASIAN)[ordinal]
        window = five[(five.index >= at("19:00") - pd.Timedelta("1D")) & (five.index < at("03:00"))]
        self.assertEqual(asian, (window["Low"].min(), window["High"].max()))
        london = es.session_ranges(ctx, ctx.views[1], es.LONDON_RANGE)[ordinal]
        self.assertGreater(london[0], 0.5)
        self.assertLess(london[1], 2.0 + 1)
        self.assertEqual(london[1], 2.0)  # the 03:00 bar belongs to London

    def test_adr_uses_only_completed_days_before_the_entry_day(self):
        t = pd.Timestamp("2026-02-25 15:00", tz=UTC).value
        d1 = self.ctx.d1
        i = d1.asof(t)
        self.assertLessEqual(d1.end[i], t)
        self.assertGreater(d1.end[i + 1], t)  # the current day is never included
        expected = np.mean(d1.h[i - 19:i + 1] - d1.l[i - 19:i + 1]) / es.PIP
        self.assertAlmostEqual(self.ctx.adr_at(t), expected)
        frames = {tf: f.copy() for tf, f in self.frames.items()}
        today = (frames["1h"].index >= pd.Timestamp("2026-02-25 00:00", tz=UTC))
        frames["1h"].loc[today, "High"] += 0.01  # today's (and later) bars change: ADR at t must not
        self.assertAlmostEqual(es.Context(frames).adr_at(t), expected)

    def test_every_feature_is_prefix_invariant(self):
        cut = pd.Timestamp("2026-02-18 14:35", tz=UTC)
        part = es.Context(truncate(self.frames, cut))
        for t in pd.date_range(pd.Timestamp("2026-02-16 00:00", tz=UTC), cut, freq="35min").as_unit("ns").asi8:
            for name in ("atr_at", "adr_at", "range_so_far", "spread_at"):
                a, b = getattr(self.ctx, name)(t), getattr(part, name)(t)
                self.assertTrue(a == b or (math.isnan(a) and math.isnan(b)), (name, t, a, b))
        for v in (1, -1):
            for attr in ("trend1", "trend4"):
                n = len(getattr(part.views[v], attr))
                np.testing.assert_array_equal(getattr(self.ctx.views[v], attr)[:n], getattr(part.views[v], attr))

    def test_range_so_far_starts_at_the_new_york_open(self):
        t = pd.Timestamp("2026-02-24 22:30", tz=UTC).value  # 17:30 EST: 6 bars into the new trading day
        k = self.ctx.m5.asof(t)
        expected = (self.ctx.m5.h[k - 5:k + 1].max() - self.ctx.m5.l[k - 5:k + 1].min()) / es.PIP
        self.assertAlmostEqual(self.ctx.range_so_far(t), expected)


class SignalsFromClosedBars(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = synthetic_frames(days=90, seed=11)
        cls.ctx = es.Context(cls.frames)
        cls.news = es.high_impact_days(date(2026, 1, 1), date(2026, 4, 30))

    def generate(self, ctx):
        out = {family: es.entry_candidates(ctx, family) for family in ("E1", "E2", "E3", "E4")}
        out["JT"] = es.jt_all(ctx, False, self.news)[0]
        return out

    def test_candidates_never_change_when_future_bars_are_removed(self):
        full = self.generate(self.ctx)
        self.assertTrue(all(full[f] for f in ("E1", "E2", "E3", "E4")), {f: len(c) for f, c in full.items()})
        self.assertTrue(full["JT"])
        jt = pd.Timestamp(full["JT"][0].t, tz=UTC)  # exactly at, and one bar before, a JT decision
        for cut in ("2026-02-11 09:10", "2026-03-05 14:45", "2026-03-20 04:20", jt, jt - pd.Timedelta("5min")):
            cutoff = pd.Timestamp(cut, tz=UTC) if isinstance(cut, str) else cut
            part = self.generate(es.Context(truncate(self.frames, cutoff)))
            for family, cands in full.items():
                expected = [c for c in cands if c.t <= cutoff.value]
                self.assertEqual(part[family], expected, (family, cut))

    def test_decision_time_is_a_bar_close(self):
        ends = set(self.ctx.m5.end.tolist())
        for cands in self.generate(self.ctx).values():
            for c in cands:
                self.assertIn(c.t, ends)

    def test_shorts_are_the_mirrored_longs(self):
        flipped = es.Context(mirror(self.frames))
        for family in ("E2", "E3", "E4"):
            shorts = [c for c in es.entry_candidates(self.ctx, family) if c.side == "SHORT"]
            longs = [c for c in es.entry_candidates(flipped, family) if c.side == "LONG"]
            self.assertEqual([(c.t, -c.struct) for c in longs], [(c.t, c.struct) for c in shorts])


def toy_market(start, bars, price=1.10000, overrides=None):
    starts = pd.date_range(start, periods=bars, freq="5min", tz=UTC)
    ohlc = np.full((bars, 4), price)
    for i, values in (overrides or {}).items():
        ohlc[i] = values
    return bt.Market(starts.as_unit("ns").asi8.copy(), ohlc[:, 0], ohlc[:, 1], ohlc[:, 2], ohlc[:, 3],
                     half_spread=0.5 * bt.PIP, slippage=0.2 * bt.PIP), starts


class StubContext:
    def __init__(self, adr=60.0, atr=0.0010, day_range=10.0, spread=1.0):
        self.adr, self.atr, self.day_range, self.spread = adr, atr, day_range, spread

    def adr_at(self, t):
        return self.adr

    def atr_at(self, t):
        return self.atr

    def range_so_far(self, t):
        return self.day_range

    def spread_at(self, t):
        return self.spread


class Fills(unittest.TestCase):
    def test_fill_at_the_open_of_the_next_5m_bar(self):
        market, starts = toy_market("2026-03-03 13:00", 60, overrides={3: (1.10100, 1.10100, 1.10100, 1.10100)})
        t = starts[3].value  # decision at the close of bar 2
        cand = es.Candidate(t, "LONG", 1.09900, "E2", "x")
        variant = es.Variant("v", "E2", "FIX2", False)
        trades, _ = es.run_variant([cand], StubContext(), market, variant)
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["fill_at"], starts[3].to_pydatetime())
        self.assertAlmostEqual(trades[0]["fill"], 1.10100 + 0.7 * bt.PIP)

    def test_no_fill_when_the_next_bar_is_missing(self):
        market, starts = toy_market("2026-03-03 13:00", 60)
        cand = es.Candidate(starts[3].value + 60 * 10**9, "LONG", 1.09900, "E2", "x")  # between bar starts
        trades, skips = es.run_variant([cand], StubContext(), market, es.Variant("v", "E2", "FIX2", False))
        self.assertEqual((trades, skips["no_next_bar"]), ([], 1))

    def test_one_position_and_filters(self):
        market, starts = toy_market("2026-03-03 13:00", 60)
        a = es.Candidate(starts[2].value, "LONG", 1.09900, "E2", "a")
        b = es.Candidate(starts[4].value, "LONG", 1.09900, "E2", "b")
        trades, skips = es.run_variant([a, b], StubContext(), market, es.Variant("v", "E2", "FIX2", False))
        self.assertEqual((len(trades), skips["position_open"]), (1, 1))
        _, skips = es.run_variant([a], StubContext(day_range=50.0), market, es.Variant("v", "E2", "FIX2", True))
        self.assertEqual(skips["f80_day_range"], 1)  # 50 > 0.8 x 60
        _, skips = es.run_variant([a], StubContext(spread=1.3), market, es.Variant("v", "E2", "FIX2", False, True))
        self.assertEqual(skips["e5_session_or_spread"], 1)


class ExitGeometry(unittest.TestCase):
    def cand(self, struct, targets=()):
        return es.Candidate(0, "LONG", struct, "x", "x", targets)

    def test_fix_widen_and_skip(self):
        stop, target, _ = es.plan_levels(self.cand(1.0995), "FIX2", 1.1000, 0.0, 60)
        self.assertAlmostEqual((1.1000 - stop) / es.PIP, 15)
        self.assertAlmostEqual((target - 1.1000) / es.PIP, 30)
        stop, target, _ = es.plan_levels(self.cand(1.0980), "FIX15", 1.1000, 0.0010, 60)  # 20 + 2 pips buffer
        self.assertAlmostEqual((1.1000 - stop) / es.PIP, 22)
        self.assertAlmostEqual((target - 1.1000) / es.PIP, 33)
        self.assertEqual(es.plan_levels(self.cand(1.0960), "FIX2", 1.1000, 0.0010, 60)[2], "sl_wider_than_30_pips")
        self.assertEqual(es.plan_levels(self.cand(1.1001), "FIX2", 1.1000, 0.0, 60)[2], "structure_beyond_fill")

    def test_adr_clamps(self):
        for adr, sl, tp in ((40, 15, 23.2), (60, 18, 34.8), (120, 30, 60)):
            stop, target, _ = es.plan_levels(self.cand(0), "ADR", 1.1, 0.001, adr)
            self.assertAlmostEqual((1.1 - stop) / es.PIP, sl)
            self.assertAlmostEqual((target - 1.1) / es.PIP, tp)

    def test_liquidity_target_floor_and_cap(self):
        c = self.cand(1.0985, (1.1020, 1.1100))  # risk 15 pips; nearest level 20 pips < 22.5
        self.assertEqual(es.plan_levels(c, "LIQ", 1.1000, 0.0, 60)[2], "liquidity_below_1_5R")
        c = self.cand(1.0985, (1.1100,))  # 100 pips away, capped at 1.0 x ADR = 60
        _, target, _ = es.plan_levels(c, "LIQ", 1.1000, 0.0, 60)
        self.assertAlmostEqual((target - 1.1000) / es.PIP, 60)
        self.assertEqual(es.plan_levels(self.cand(1.0985, (1.0990,)), "LIQ", 1.1, 0.0, 60)[2],
                         "no_opposite_liquidity")


class JTTrigger(unittest.TestCase):
    def test_trigger_close_must_be_inside_or_at_the_ob(self):
        obs = [((1.1000, 1.1010), 5)]
        for close, inside in ((1.1005, True), (1.1000, True), (1.1010, True), (1.1011, False), (1.0999, False)):
            self.assertEqual(es._trigger_ob(obs, 9, close, 1.0990, 1.1020) is not None, inside, close)

    def test_trigger_ob_formed_before_the_bar_and_overlapping_the_zone(self):
        obs = [((1.1000, 1.1010), 9)]
        self.assertIsNone(es._trigger_ob(obs, 9, 1.1005, 1.0990, 1.1020))  # formed on the trigger bar itself
        self.assertIsNone(es._trigger_ob([((1.1000, 1.1010), 5)], 9, 1.1005, 1.1011, 1.1020))  # zone above the OB
        newest = ((1.1002, 1.1008), 7)
        self.assertEqual(es._trigger_ob([((1.1000, 1.1010), 5), newest], 9, 1.1005, 1.0990, 1.1020), newest[0])


def fake_trade(signal_at, hours, r):
    exit_at = signal_at + pd.Timedelta(hours=hours).to_pytimedelta()
    return {"signal_at": signal_at, "fill_at": signal_at, "exit_at": exit_at, "r": r, "gross_r": r + 0.05,
            "reason": bt.TP if r > 0 else bt.SL, "risk_pips": 15.0, "tp_pips": 30.0, "spread_pips": 1.5,
            "marks": np.array([r / 2])}


class InSampleSelection(unittest.TestCase):
    def setUp(self):
        self.split = es.Split(datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 6, 1, 21, tzinfo=UTC),
                              datetime(2026, 9, 1, tzinfo=UTC))
        self.inside = [fake_trade(datetime(2026, 2, 2, 9, tzinfo=UTC) + pd.Timedelta(days=d).to_pytimedelta(), 3,
                                  0.1) for d in range(25)]
        # signalled 1 h before the split, exits 5 h after it: its outcome is mostly out-of-sample
        self.crosser = fake_trade(self.split.split - pd.Timedelta(hours=1).to_pytimedelta(), 6, 50.0)
        self.oos = fake_trade(self.split.split + pd.Timedelta(days=3).to_pytimedelta(), 2, -1.0)

    def test_trades_crossing_the_split_are_purged_from_both_segments(self):
        at_split = fake_trade(self.split.split - pd.Timedelta(hours=2).to_pytimedelta(), 2, 1.0)  # exits AT it
        is_t, oos_t, purged = es.segment(self.inside + [at_split, self.crosser, self.oos], self.split)
        self.assertEqual((len(is_t), oos_t, purged), (26, [self.oos], [self.crosser]))
        self.assertTrue(all(t["exit_at"] <= self.split.split for t in is_t))

    def test_in_sample_ranking_ignores_out_of_sample_outcomes(self):
        losers = [dict(t, r=-0.05, reason=bt.SL) for t in self.inside]
        results = {"A": (self.inside, None), "B": (losers + [self.crosser], None)}
        self.assertEqual(es.top_variants(results, self.split, n=1), ["A"])  # B's +50R crosser would rank it first
        a, _ = es.summarise(results["B"][0], self.split)
        self.assertEqual(a["trades"], 25)
        self.assertAlmostEqual(a["avg_r"], -0.05)
        self.assertIn("| B | ", es.table(results, self.split))


class NewsAndSplit(unittest.TestCase):
    def test_news_days(self):
        self.assertEqual(es.first_friday(2026, 5), date(2026, 5, 1))
        self.assertEqual(es.cpi_day(2026, 7), date(2026, 7, 13))  # 12th = Sunday
        self.assertEqual(es.cpi_day(2026, 9), date(2026, 9, 11))  # 12th = Saturday
        days = es.high_impact_days(date(2026, 1, 1), date(2026, 12, 31))
        self.assertEqual(days[date(2026, 3, 18)], "FOMC")
        nfp = pd.Timestamp("2026-05-01 08:30", tz=es.NY).value
        self.assertTrue(es.news_blocked(nfp, days))
        self.assertFalse(es.news_blocked(nfp + 40 * es.MINUTE, days))  # 09:10

    def test_split_is_eight_months_after_the_warm_up(self):
        ctx = es.Context(synthetic_frames(days=300, seed=5))
        split = es.make_split(ctx)
        self.assertEqual(split.start, datetime.fromtimestamp(ctx.d1.end[es.ADR_DAYS - 1] / 1e9, UTC))
        self.assertAlmostEqual(split.months[0], 8, delta=0.1)
        self.assertEqual(split.split.astimezone(es.NY).hour, 17)


if __name__ == "__main__":
    unittest.main()
