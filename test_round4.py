"""Replay-only round 4 (replay/round4.py): no-lookahead for every feature (H4/D1 from closed 1h only, indicators and
setups on closed bars, fills at the next 5m open), the trade walk, costs, financing stress and filters."""
import math
import unittest
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from replay import entry_search as es
from replay import intraday_backtest as bt
from replay import round4 as r4

UTC = timezone.utc
FIVE = 5 * es.MINUTE


def synthetic_frames(days=460, seed=3, start="2021-10-01"):
    """5m random walk with NY weekends removed (Fri 17:00 -> Sun 17:00 NY); 1h / 15m resampled from it."""
    rng = np.random.default_rng(seed)
    index = pd.date_range(start, periods=days * 288, freq="5min", tz="UTC")
    ny = index.tz_convert(es.NY)
    clock = ny.hour * 60 + ny.minute
    weekend = (ny.weekday == 5) | ((ny.weekday == 4) & (clock >= 17 * 60)) | ((ny.weekday == 6) & (clock < 17 * 60))
    index = index[~np.asarray(weekend)]
    steps = rng.normal(0, 0.0003, len(index))
    close = 1.10 + np.cumsum(steps)
    open_ = np.concatenate([[1.10], close[:-1]])
    wick = np.abs(rng.normal(0, 0.0002, len(index)))
    f5 = pd.DataFrame({"Open": open_, "High": np.maximum(open_, close) + wick, "Low": np.minimum(open_, close) - wick,
                       "Close": close}, index=index)
    f5["BidClose"], f5["AskClose"] = f5["Close"] - 0.00007, f5["Close"] + 0.00007
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last"}
    frames = {"5m": f5}
    for tf, rule in (("15m", "15min"), ("1h", "1h")):
        frames[tf] = f5[["Open", "High", "Low", "Close"]].resample(rule).agg(agg).dropna()
    return frames


FRAMES = synthetic_frames()


def bars_of(frames):
    return {tf: r4.htf_bars(frames["1h"], tf) for tf in ("D1", "H4", "H1")}


def cut(frames, end):
    return {tf: f[f.index < end] for tf, f in frames.items()}


class Resampling(unittest.TestCase):
    def test_htf_bars_built_from_closed_1h_only_and_prefix_invariant(self):
        full = bars_of(FRAMES)
        for end in ("2022-03-09 13:00", "2022-06-01 03:00", "2022-11-18 22:00"):
            t = pd.Timestamp(end, tz="UTC")
            part = bars_of(cut(FRAMES, t))
            for tf in ("D1", "H4", "H1"):
                a, b = full[tf], part[tf]
                visible = a.avail <= t.value
                self.assertTrue((b.avail <= t.value).all(), tf)  # nothing visible before its last 1h bar closed
                n = int(visible.sum())
                self.assertEqual(len(b), n, tf)
                for x in ("avail", "o", "h", "l", "c"):
                    np.testing.assert_array_equal(getattr(a, x)[:n], getattr(b, x), err_msg=f"{tf} {x}")

    def test_break_hours_schedule(self):
        f1 = FRAMES["1h"]
        pd.testing.assert_frame_equal(r4.resample(f1, 24), es.resample_closed(f1, 24))
        ny = f1.index.tz_convert(es.NY)
        gapped = f1[ny.hour != 17]  # XAU / indices: no 17:00-18:00 NY bar
        self.assertEqual(len(es.resample_closed(gapped, 24)), 0)
        d1 = r4.resample(gapped, 24, break_hours=(17,))
        self.assertEqual(len(d1), len(es.resample_closed(f1, 24)))
        h4 = r4.resample(gapped, 4, break_hours=(17,))
        self.assertEqual(len(h4), len(es.resample_closed(f1, 4)))
        holed = gapped.drop(gapped.index[500])  # a genuinely missing hour still makes its bucket incomplete
        self.assertEqual(len(r4.resample(holed, 24, break_hours=(17,))), len(d1) - 1)
        with r4.schedule((17,)):
            self.assertEqual(len(es.resample_closed(gapped, 24)), len(d1))
        self.assertIs(es.resample_closed, r4._RESAMPLE)
        t = pd.Timestamp("2022-05-04 10:00", tz="UTC")
        part = r4.resample(gapped[gapped.index < t], 24, break_hours=(17,))
        self.assertTrue((part["available"] <= t.value).all())
        self.assertEqual(len(part), int((d1["available"] <= t.value).sum()))

    def test_d1_closes_at_17_ny(self):
        d1 = bars_of(FRAMES)["D1"]
        clock = pd.to_datetime(d1.avail, utc=True).tz_convert(es.NY)
        self.assertTrue(((clock.hour == 17) & (clock.minute == 0)).all())


class Indicators(unittest.TestCase):
    def test_every_indicator_at_k_uses_bars_up_to_k(self):
        h = bars_of(FRAMES)["H4"]
        k = 700
        p = r4.HBars(h.avail[:k + 1], h.o[:k + 1], h.h[:k + 1], h.l[:k + 1], h.c[:k + 1])
        pairs = [(r4.atr(h, 14), r4.atr(p, 14)), (r4.rsi(h.c, 2), r4.rsi(p.c, 2)), (r4.adx(h, 14), r4.adx(p, 14)),
                 (r4.sma(h.c, 20), r4.sma(p.c, 20)), (r4.prior_max(h.h, 20), r4.prior_max(p.h, 20)),
                 (r4.prior_min(h.l, 10), r4.prior_min(p.l, 10))]
        for full, part in pairs:
            np.testing.assert_allclose(full[:k + 1], part, equal_nan=True)

    def test_prior_extremes_exclude_the_current_bar(self):
        x = np.array([1.0, 3.0, 2.0, 5.0, 4.0])
        np.testing.assert_array_equal(r4.prior_max(x, 2), [np.nan, np.nan, 3.0, 3.0, 5.0])

    def test_rsi_bounds_and_wilder(self):
        c = np.array([1.0, 2.0, 3.0, 1.0, 2.0])
        r = r4.rsi(c, 2)
        self.assertTrue(np.isnan(r[:2]).all())
        self.assertEqual(r[2], 100.0)
        self.assertTrue(0 <= r[3] < 50 < r[4] <= 100)


class SetupsNoLookahead(unittest.TestCase):
    def test_setups_are_prefix_invariant_for_every_family(self):
        full = bars_of(FRAMES)
        t = pd.Timestamp("2022-12-07 15:00", tz="UTC")
        part = bars_of(cut(FRAMES, t))
        for base, (family, _) in r4.BASE.items():
            if family in ("JT", "E3"):
                continue
            a = [s for s in r4.build_setups(base, full) if s.t <= t.value]
            b = r4.build_setups(base, part)
            key = [(s.t, s.side, s.idea, s.stop_dist, s.stop_level, s.tp_r, s.tp_price) for s in a]
            got = [(s.t, s.side, s.idea, s.stop_dist, s.stop_level, s.tp_r, s.tp_price) for s in b]
            self.assertEqual(len(key), len(got), base)
            for x, y in zip(key, got):
                self.assertEqual(x[:3], y[:3], base)
                for u, v in zip(x[3:], y[3:]):
                    self.assertTrue((u is None and v is None) or (math.isnan(u) and math.isnan(v)) or
                                    math.isclose(u, v, rel_tol=1e-12), base)
            # events seen by the walk never precede the decision
            for s in a:
                self.assertTrue(all(e[0] > s.t for e in s.events), base)
        self.assertTrue(any(r4.build_setups(b_, full) for b_ in r4.GRID_A + r4.GRID_B + r4.GRID_C))

    def test_decision_times_are_bar_closes(self):
        full = bars_of(FRAMES)
        for base, tf in (("A:ATR:TP2", "D1"), ("B4:DON20/10:H4", "H4"), ("C1:RSI2:H1", "H1")):
            closes = set(full[tf].avail.tolist())
            self.assertTrue(all(s.t in closes for s in r4.build_setups(base, full)), base)

    def test_month_ends_are_calendar_only(self):
        d1 = bars_of(FRAMES)["D1"]
        ends = r4.month_ends(d1)
        dates = pd.to_datetime(d1.avail[ends], utc=True).tz_convert(es.NY)
        self.assertTrue(all((d + pd.offsets.BDay(1)).month != d.month for d in dates))
        part = r4.HBars(*(x[:ends[3] + 1] for x in (d1.avail, d1.o, d1.h, d1.l, d1.c)))
        np.testing.assert_array_equal(r4.month_ends(part), ends[:4])


def toy_tape(o, h, l, c, spread=0.0001, pip=0.0001, fx=True):  # noqa: E741
    n = len(o)
    start = np.arange(n, dtype=np.int64) * FIVE + pd.Timestamp("2024-01-08 13:00", tz="UTC").value
    return r4.Tape(start, *(np.asarray(x, float) for x in (o, h, l, c)), np.full(n, spread), pip, fx)


class Walk(unittest.TestCase):
    def test_same_bar_double_hit_is_sl(self):
        tape = toy_tape([1.0, 1.0], [1.0, 1.02], [1.0, 0.98], [1.0, 1.0], spread=0.0)
        x, _, price, reason, _, _ = r4.walk(tape, 0, 1, 1.0, 0.99, 1.01, 0.0, 0.0, [])
        self.assertEqual((x, reason, price), (1, r4.SL, 0.99))

    def test_gap_through_stop_fills_at_open(self):
        tape = toy_tape([1.0, 0.98], [1.0, 0.985], [1.0, 0.97], [1.0, 0.98], spread=0.0)
        _, at, price, reason, detail, _ = r4.walk(tape, 0, 1, 1.0, 0.99, 1.05, 0.0, 0.001, [])
        self.assertEqual((reason, detail), (r4.SL, "GAP_SL"))
        self.assertAlmostEqual(price, 0.98 - 0.001)
        self.assertEqual(at, int(tape.start[1]))

    def test_trailing_stop_only_ratchets_and_applies_after_its_close(self):
        tape = toy_tape([1.0] * 4, [1.0, 1.0, 1.0, 1.0], [1.0, 0.995, 0.995, 0.995], [1.0] * 4, spread=0.0)
        t1 = int(tape.start[2])  # level set at the close of bar 1 -> active from bar 2
        _, _, _, reason, detail, _ = r4.walk(tape, 0, 1, 1.0, 0.99, None, 0.0, 0.0,
                                             [(t1, "stop", 0.996), (int(tape.start[3]), "stop", 0.98)])
        self.assertEqual((reason, detail), (r4.SL, "TRAIL"))
        x, *_ = r4.walk(tape, 0, 1, 1.0, 0.99, None, 0.0, 0.0, [(t1, "stop", 0.996)])
        self.assertEqual(x, 2)  # bar 1's low 0.995 does not hit the not-yet-active 0.996

    def test_signal_exit_at_next_open_and_deadline_at_last_close(self):
        tape = toy_tape([1.0, 1.001, 1.002, 1.003], [1.004] * 4, [0.999] * 4, [1.001, 1.002, 1.003, 1.004],
                        spread=0.0)
        t = int(tape.start[2])
        x, at, price, reason, _, free = r4.walk(tape, 0, 1, 1.0, 0.99, None, 0.0, 0.0, [(t, "exit", r4.SIGNAL)])
        self.assertEqual((x, at, price, reason, free), (2, t, 1.002, r4.SIGNAL, t))
        x, at, price, reason, _, _ = r4.walk(tape, 0, 1, 1.0, 0.99, None, 0.0, 0.0, [(t, "close", r4.TIME)])
        self.assertEqual((x, at, price, reason), (1, t, 1.002, r4.TIME))


class RunAndCosts(unittest.TestCase):
    def test_fill_at_open_of_first_5m_bar_at_or_after_decision_with_costs(self):
        tape = toy_tape([1.0, 1.001, 1.002], [1.01] * 3, [0.999] * 3, [1.0] * 3, spread=0.0001)
        setup = r4.Setup(int(tape.start[1]) - 60 * 10**9, 1, "T", "x", stop_dist=0.002)
        (trade,), _ = r4.run([setup], tape, "T")
        self.assertEqual(trade["fill_at"], tape.stamp(1))
        self.assertEqual(trade["mid_fill"], 1.001)
        self.assertAlmostEqual(trade["fill"], 1.001 + 0.00005 + 0.00002)  # half spread + max(0.1 x spread, 0.2 pip)
        self.assertAlmostEqual(trade["slip"], 0.00002)

    def test_slippage_share_and_no_trade_without_causal_spread(self):
        tape = toy_tape([1.0] * 3, [1.0] * 3, [1.0] * 3, [1.0] * 3, spread=0.0005)
        self.assertAlmostEqual(tape.costs(0)[1], 0.00005)
        idx = toy_tape([1.0] * 3, [1.0] * 3, [1.0] * 3, [1.0] * 3, spread=0.5, pip=1.0, fx=False)
        self.assertAlmostEqual(idx.costs(0)[1], 0.05)
        tape.spread[:] = np.nan
        trades, skips = r4.run([r4.Setup(int(tape.start[0]), 1, "T", "x", stop_dist=0.01)], tape, "T")
        self.assertEqual((trades, skips["no_causal_spread"]), ([], 1))

    def test_causal_spread_excludes_the_fill_bar(self):
        f5 = FRAMES["5m"].iloc[:20000].copy()
        f5.iloc[15000, f5.columns.get_loc("AskClose")] += 0.01
        tape = r4.Tape.from_frame(f5, 0.0001, True)
        base = r4.Tape.from_frame(FRAMES["5m"].iloc[:20000], 0.0001, True)
        self.assertEqual(tape.spread[15000], base.spread[15000])

    def test_one_position_at_a_time_and_reversal_on_the_exit_close(self):
        tape = toy_tape([1.0] * 6, [1.0005] * 6, [0.9995] * 6, [1.0] * 6, spread=0.0)
        t2 = int(tape.start[2])
        a = r4.Setup(int(tape.start[0]), 1, "T", "a", stop_dist=0.01, events=[(t2, "exit", r4.SIGNAL)])
        b = r4.Setup(int(tape.start[1]), 1, "T", "b", stop_dist=0.01)
        c = r4.Setup(t2, -1, "T", "c", stop_dist=0.01)
        trades, skips = r4.run([a, b, c], tape, "T")
        self.assertEqual([t["idea"] for t in trades], ["a", "c"])
        self.assertEqual(skips["position_open"], 1)
        self.assertEqual(trades[1]["fill_at"], tape.stamp(2))

    def test_carry_stress_in_r_per_day(self):
        tape = toy_tape([1.0] * 3, [1.0] * 3, [1.0] * 3, [1.0] * 3, spread=0.0)
        trade = {"r": 0.5, "fill": 1.0, "risk_price": 0.01, "fill_at": datetime(2024, 1, 1, tzinfo=UTC),
                 "exit_at": datetime(2024, 1, 11, tzinfo=UTC), "marks": np.array([0.0]),
                 "mark_ns": np.array([pd.Timestamp("2024-01-06", tz="UTC").value])}
        (u,) = r4.with_carry([trade])
        self.assertAlmostEqual(u["carry_r"], 0.03 * 10 / 365 * 1.0 / 0.01)
        self.assertAlmostEqual(u["marks"][0], -0.03 * 5 / 365 * 100)
        self.assertEqual(trade["r"], 0.5)
        del tape


class Filters(unittest.TestCase):
    def test_day_of_week_uses_the_fill_trading_day(self):
        s = r4.Setup(0, 1, "T", "x")
        thu_after_close = datetime(2024, 1, 11, 22, 5, tzinfo=UTC)  # Thu 17:05 NY = Friday's trading day
        self.assertEqual(r4.skip_mon_fri(s, thu_after_close), "d1_monday_friday")
        self.assertIsNone(r4.skip_mon_fri(s, datetime(2024, 1, 10, 14, tzinfo=UTC)))
        sunday_open = datetime(2024, 1, 14, 22, 5, tzinfo=UTC)  # Monday's trading day
        self.assertEqual(r4.skip_mon_fri(s, sunday_open), "d1_monday_friday")

    def test_carry_direction_eurusd_short_only_and_na_elsewhere(self):
        f = r4.carry_filter("EURUSD")
        self.assertEqual(f(r4.Setup(0, 1, "T", "x"), None), "d2_against_carry")
        self.assertIsNone(f(r4.Setup(0, -1, "T", "x"), None))
        self.assertIsNone(r4.carry_filter("GBPUSD"))


class Misc(unittest.TestCase):
    def test_neighbourhood_is_plus_minus_25_percent_one_at_a_time(self):
        nb = dict(r4.neighbours("B1:DON55/20"))
        self.assertEqual(set(nb), {"entry=41", "entry=69", "exit=15", "exit=25", "atr=1.5", "atr=2.5"})
        self.assertEqual(nb["exit=15"], {"entry": 55, "exit": 15, "atr": 2.0})

    def test_instrument_pip_is_restored(self):
        with r4.instrument_pip(0.01):
            self.assertEqual((bt.PIP, es.PIP), (0.01, 0.01))
        self.assertEqual((bt.PIP, es.PIP), (0.0001, 0.0001))

    def test_declared_grid(self):
        self.assertEqual(sum(r4.DECLARED.values()), 4 + 4 + 2 + 4 + 1 + 10 + 12)
        text, total = r4.declaration(type("H", (), {"symbol": "EURUSD", "years": [(datetime(2021, 11, 1, tzinfo=UTC),
                                                                                    datetime(2022, 11, 1, tzinfo=UTC))]})(),
                                     list(r4.INSTRUMENTS), [])
        self.assertEqual(total, 37)
        self.assertIn("**Tests declared: 37**", text)


if __name__ == "__main__":
    unittest.main()
