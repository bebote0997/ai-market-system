"""Replay-only 1-year backtest V2_F3 vs V2_I2 (replay/intraday_backtest.py): no lookahead, I2 stop rules, exits."""
import unittest
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from replay import intraday_backtest as bt

UTC = timezone.utc


def frame(starts, rows, freq_minutes):
    index = pd.DatetimeIndex(starts, tz=UTC) if not isinstance(starts, pd.DatetimeIndex) else starts
    data = pd.DataFrame(rows, columns=["Open", "High", "Low", "Close"], index=index, dtype=float)
    data["symbol"], data["is_closed"] = "EURUSD", True
    return data


def flat_market(start, bars, price=1.10000, overrides=None):
    """5m Market of flat bars (O=H=L=C=price) from ``start``; ``overrides`` {bar index: (o, h, l, c)}."""
    starts = pd.date_range(start, periods=bars, freq="5min", tz=UTC)
    ohlc = np.full((bars, 4), price)
    for index, values in (overrides or {}).items():
        ohlc[index] = values
    return bt.Market(starts.as_unit("ns").asi8.copy(), ohlc[:, 0], ohlc[:, 1], ohlc[:, 2], ohlc[:, 3],
                     half_spread=0.5 * bt.PIP, slippage=0.2 * bt.PIP)


class NoLookahead(unittest.TestCase):
    def test_snapshot_excludes_bars_not_closed_by_the_slot(self):
        slot = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)
        five = frame(pd.date_range("2026-03-03 09:40", periods=5, freq="5min", tz=UTC), [[1, 1, 1, 1]] * 5, 5)
        fifteen = frame(pd.date_range("2026-03-03 09:15", periods=4, freq="15min", tz=UTC), [[1, 1, 1, 1]] * 4, 15)
        hour = frame(pd.date_range("2026-03-03 08:00", periods=3, freq="1h", tz=UTC), [[1, 1, 1, 1]] * 3, 60)
        snapshot = bt.snapshot_at({"5m": five, "15m": fifteen, "1h": hour}, slot)
        self.assertEqual(snapshot["5m"].index[-1], pd.Timestamp("2026-03-03 09:55", tz=UTC))  # ends exactly at t
        self.assertEqual(snapshot["15m"].index[-1], pd.Timestamp("2026-03-03 09:45", tz=UTC))
        self.assertEqual(snapshot["1h"].index[-1], pd.Timestamp("2026-03-03 09:00", tz=UTC))  # 10:00 bar still open
        self.assertTrue(all(snapshot[tf].index[-1] + bt.FIVE * k <= slot for tf, k in (("5m", 1), ("15m", 3))))

    def test_fill_uses_the_bar_starting_at_the_slot(self):
        slot = datetime(2026, 3, 3, 10, 0, tzinfo=UTC)
        market = flat_market(slot - timedelta(minutes=10), 30, overrides={2: (1.10100, 1.101, 1.101, 1.101)})
        result = bt.simulate_trade(market, slot, "LONG", 1.09800, 1.10500, "I2")
        self.assertAlmostEqual(result["mid_fill"], 1.10100)
        self.assertAlmostEqual(result["fill"], 1.10100 + 0.7 * bt.PIP)


    def test_causal_spread_uses_only_bars_before_the_fill_and_falls_back(self):
        starts = pd.date_range("2026-01-01", periods=6, freq="5min", tz=UTC)
        five = frame(starts, [[1.1] * 4] * 6, 5)
        five["BidClose"], five["AskClose"] = 1.1, 1.1 + np.array([1, 1, 3, 3, 3, 50]) * bt.PIP
        spread = bt.causal_spread_pips(five)
        self.assertTrue(np.isnan(spread.iloc[0]))  # nothing before the first bar
        self.assertAlmostEqual(spread.iloc[2], 1.0)  # bars 0-1 only, bar 2 itself excluded
        self.assertAlmostEqual(spread.iloc[5], 3.0)  # the 50-pip bar 5 never prices its own fill
        market = bt.Market.from_frames({"5m": five}, bt.FALLBACK_SPREAD_PIPS, causal=True)
        self.assertEqual(market.half_spread_at(0), (bt.FALLBACK_SPREAD_PIPS * bt.PIP / 2, True))
        self.assertAlmostEqual(market.half_spread_at(5)[0], 1.5 * bt.PIP)
        self.assertEqual(bt.causal_spread_pips(five[["Open", "High", "Low", "Close"]]).isna().all(), True)


class I2Stop(unittest.TestCase):
    def setUp(self):
        self.five_empty = frame(pd.date_range("2026-03-03", periods=3, freq="5min", tz=UTC), [[1.1, 1.1, 1.1, 1.1]] * 3, 5)

    @staticmethod
    def swing_low_frame(low, freq="15min", tail=1.1000):
        rows = [[tail, tail + 0.0005, tail - 0.0001, tail], [low + 0.0002, low + 0.0005, low, low + 0.0003],
                [tail, tail + 0.0005, tail - 0.0001, tail], [tail, tail + 0.0005, tail - 0.0001, tail]]
        return frame(pd.date_range("2026-03-03", periods=len(rows), freq=freq, tz=UTC), rows, 15)

    def test_close_swing_is_widened_to_15_pips(self):
        stop, source = bt.i2_stop("LONG", 1.1000, self.swing_low_frame(1.0995), self.five_empty, atr=0.0010)
        self.assertEqual(source, "15m_swing_widened_to_15")
        self.assertAlmostEqual(stop, 1.1000 - 15 * bt.PIP)

    def test_swing_within_band_keeps_swing_minus_buffer(self):
        stop, source = bt.i2_stop("LONG", 1.1000, self.swing_low_frame(1.0980), self.five_empty, atr=0.0010)
        self.assertEqual(source, "15m_swing")
        self.assertAlmostEqual(stop, 1.0980 - 0.0002)  # 0.2 x ATR buffer

    def test_wide_15m_swing_falls_back_to_5m_swing_or_skips(self):
        wide = self.swing_low_frame(1.0960)
        five = self.swing_low_frame(1.0982, freq="5min")
        stop, source = bt.i2_stop("LONG", 1.1000, wide, five, atr=0.0010)
        self.assertEqual(source, "5m_swing")
        self.assertAlmostEqual(stop, 1.0982 - 0.0002)
        stop, reason = bt.i2_stop("LONG", 1.1000, wide, self.swing_low_frame(1.0960, freq="5min"), atr=0.0010)
        self.assertIsNone(stop)
        self.assertEqual(reason, "sl_wider_than_30_pips")

    def test_short_uses_swing_high_above_entry_and_missing_inputs_skip(self):
        rows = [[1.1, 1.1001, 1.0995, 1.1], [1.1015, 1.1020, 1.1012, 1.1015], [1.1, 1.1001, 1.0995, 1.1],
                [1.1, 1.1001, 1.0995, 1.1]]
        highs = frame(pd.date_range("2026-03-03", periods=4, freq="15min", tz=UTC), rows, 15)
        stop, source = bt.i2_stop("SHORT", 1.1000, highs, self.five_empty, atr=0.0010)
        self.assertEqual(source, "15m_swing")
        self.assertAlmostEqual(stop, 1.1020 + 0.0002)
        self.assertEqual(bt.i2_stop("SHORT", 1.1030, highs, self.five_empty, atr=0.001)[1], "no_15m_swing_beyond_entry")
        self.assertEqual(bt.i2_stop("SHORT", 1.1, highs, self.five_empty, atr=None)[1], "atr_unavailable")


class I2ExitTimes(unittest.TestCase):
    def check(self, entry, expected, reason):
        close, why = bt.i2_exit(entry)
        self.assertEqual(close, expected)
        self.assertEqual(why, reason)

    def test_london_entry_closes_at_ny_17_same_day_across_dst(self):
        self.check(datetime(2026, 1, 13, 9, 0, tzinfo=UTC), datetime(2026, 1, 13, 22, 0, tzinfo=UTC), bt.TIME)
        self.check(datetime(2026, 7, 14, 9, 0, tzinfo=UTC), datetime(2026, 7, 14, 21, 0, tzinfo=UTC), bt.TIME)
        # US already on DST, UK not yet (2026-03-08 .. 03-29): NY 17:00 EDT = 21:00 UTC.
        self.check(datetime(2026, 3, 10, 13, 0, tzinfo=UTC), datetime(2026, 3, 10, 21, 0, tzinfo=UTC), bt.TIME)

    def test_ny_only_entry_closes_at_next_weekday_london_17(self):
        self.check(datetime(2026, 1, 13, 18, 0, tzinfo=UTC), datetime(2026, 1, 14, 17, 0, tzinfo=UTC), bt.TIME)
        self.check(datetime(2026, 7, 14, 17, 0, tzinfo=UTC), datetime(2026, 7, 15, 16, 0, tzinfo=UTC), bt.TIME)  # BST
        # Mismatch week: London closed at 17:00 GMT, NY open until 21:00 UTC.
        self.check(datetime(2026, 3, 10, 17, 30, tzinfo=UTC), datetime(2026, 3, 11, 17, 0, tzinfo=UTC), bt.TIME)

    def test_friday_close_overrides(self):
        friday_close = datetime(2026, 1, 16, 22, 0, tzinfo=UTC)
        self.check(datetime(2026, 1, 16, 18, 0, tzinfo=UTC), friday_close, bt.FRIDAY)  # NY only -> would be Monday
        self.check(datetime(2026, 1, 16, 9, 0, tzinfo=UTC), friday_close, bt.FRIDAY)
        self.check(datetime(2026, 1, 15, 18, 0, tzinfo=UTC), datetime(2026, 1, 16, 17, 0, tzinfo=UTC), bt.TIME)
        with self.assertRaises(ValueError):
            bt.i2_exit(datetime(2026, 1, 13, 23, 0, tzinfo=UTC))


class Simulation(unittest.TestCase):
    slot = datetime(2026, 1, 13, 10, 0, tzinfo=UTC)

    def test_same_bar_stop_and_target_assumes_stop_first(self):
        market = flat_market(self.slot, 20, overrides={1: (1.1, 1.1060, 1.0960, 1.1)})
        result = bt.simulate_trade(market, self.slot, "LONG", 1.0980, 1.1040, "I2")
        self.assertEqual(result["reason"], bt.SL)
        self.assertAlmostEqual(result["exit"], 1.0980 - 0.2 * bt.PIP)
        self.assertLess(result["r"], -1)
        self.assertEqual(len(result["marks"]), 1)  # fill-bar close (bid) marked, exit bar realised
        self.assertAlmostEqual(result["marks"][0], (1.1 - 0.5 * bt.PIP - result["fill"]) / (result["fill"] - 1.0980))

    def test_gap_through_stop_fills_at_bar_open(self):
        market = flat_market(self.slot, 20, overrides={2: (1.0950, 1.0955, 1.0940, 1.0950)})
        result = bt.simulate_trade(market, self.slot, "LONG", 1.0980, 1.1040, "I2")
        self.assertEqual(result["detail"], "GAP_SL")
        self.assertAlmostEqual(result["exit"], 1.0950 - 0.5 * bt.PIP - 0.2 * bt.PIP)

    def test_target_is_a_limit_on_the_bid_without_slippage(self):
        market = flat_market(self.slot, 20, overrides={3: (1.1, 1.1046, 1.0999, 1.1)})
        result = bt.simulate_trade(market, self.slot, "LONG", 1.0980, 1.1040, "I2")
        self.assertEqual(result["reason"], bt.TP)
        self.assertAlmostEqual(result["exit"], 1.1040)
        short = bt.simulate_trade(flat_market(self.slot, 20, overrides={3: (1.1, 1.1001, 1.0954, 1.1)}), self.slot,
                                  "SHORT", 1.1020, 1.0960, "I2")
        self.assertEqual(short["reason"], bt.TP)  # ask low = 1.0954 + 0.5 pip <= 1.0960

    def test_i2_time_exit_at_ny_close_on_bid_minus_slippage(self):
        market = flat_market(self.slot, 200)
        result = bt.simulate_trade(market, self.slot, "LONG", 1.0980, 1.1040, "I2")
        self.assertEqual((result["reason"], result["exit_at"]), (bt.TIME, datetime(2026, 1, 13, 22, 0, tzinfo=UTC)))
        self.assertAlmostEqual(result["exit"], 1.1 - 0.7 * bt.PIP)

    def test_f3_day1_below_half_r_closes_and_friday_close(self):
        market = flat_market(self.slot, 200)
        result = bt.simulate_trade(market, self.slot, "LONG", 1.0900, 1.1300, "F3", bt.F3_FILL_RR_FLOOR)
        self.assertEqual(result["detail"], "TIME_EXIT_DAY1_BELOW_0_5R")
        self.assertEqual(result["exit_at"], datetime(2026, 1, 13, 22, 0, tzinfo=UTC))
        friday = datetime(2026, 1, 16, 10, 0, tzinfo=UTC)
        result = bt.simulate_trade(flat_market(friday, 200), friday, "LONG", 1.0900, 1.1300, "F3", 2.5)
        self.assertEqual((result["reason"], result["exit_at"]), (bt.FRIDAY, datetime(2026, 1, 16, 22, 0, tzinfo=UTC)))

    def test_f3_day1_above_half_r_moves_stop_to_entry_then_breakeven(self):
        bars = 400
        overrides = {i: (1.1060, 1.1060, 1.1060, 1.1060) for i in range(1, 140)}  # +0.6R at the Day-1 close
        overrides[200] = (1.1060, 1.1060, 1.0990, 1.1000)  # back through the entry on Day 2
        market = flat_market(self.slot, bars, price=1.1060, overrides={0: (1.1, 1.1, 1.1, 1.1), **overrides})
        result = bt.simulate_trade(market, self.slot, "LONG", 1.0900, 1.1300, "F3", 2.5)
        self.assertEqual(result["reason"], bt.BE)
        self.assertGreater(result["exit_at"], datetime(2026, 1, 13, 22, 0, tzinfo=UTC))

    def test_evaluation_cutoff_closes_open_position_at_last_bar_ending_by_it(self):
        market = flat_market(self.slot, 200, overrides={i: (1.1010, 1.1010, 1.1010, 1.1010) for i in range(5, 200)})
        market.cutoff = self.slot + timedelta(minutes=60)
        market.close[12] = 1.2  # bar ending after the cutoff (would hit TP) is never read
        market.high[12] = 1.2
        result = bt.simulate_trade(market, self.slot, "LONG", 1.0980, 1.1040, "I2")
        self.assertEqual((result["reason"], result["exit_at"]), (bt.END, self.slot + timedelta(minutes=60)))
        self.assertEqual(result["exit_index"], 11)
        self.assertAlmostEqual(result["exit"], 1.1010 - 0.7 * bt.PIP)
        self.assertEqual(bt.simulate_trade(market, self.slot + timedelta(minutes=60), "LONG", 1.0980, 1.1040, "I2"),
                         {"rejected": "no_fill_data"})

    def test_boundary_at_the_cutoff_is_a_time_exit_not_open_at_end(self):
        market = flat_market(self.slot, 144)  # last bar 21:55-22:00 = the I2 NY 17:00 close
        market.cutoff = datetime(2026, 1, 13, 22, 0, tzinfo=UTC)
        result = bt.simulate_trade(market, self.slot, "LONG", 1.0980, 1.1040, "I2")
        self.assertEqual((result["reason"], result["exit_at"], result["stale_exit"]),
                         (bt.TIME, market.cutoff, False))

    def test_boundary_priced_by_last_bar_ending_before_it_across_a_data_gap(self):
        before = pd.date_range(self.slot, datetime(2026, 1, 13, 20, 55, tzinfo=UTC), freq="5min")
        starts = before.append(pd.date_range("2026-01-13 23:00", periods=20, freq="5min", tz=UTC))
        closes = np.full(len(starts), 1.1)
        closes[len(before) - 1] = 1.1010
        market = bt.Market(starts.as_unit("ns").asi8.copy(), closes.copy(), closes.copy(), closes.copy(), closes,
                           half_spread=0.5 * bt.PIP, slippage=0.2 * bt.PIP)
        result = bt.simulate_trade(market, self.slot, "LONG", 1.0980, 1.1040, "I2")
        self.assertEqual((result["exit_at"], result["exit_index"], result["stale_exit"]),
                         (datetime(2026, 1, 13, 22, 0, tzinfo=UTC), len(before) - 1, False))
        self.assertAlmostEqual(result["exit"], 1.1010 - 0.7 * bt.PIP)

    def test_boundary_without_a_bar_before_it_uses_first_bar_after_and_is_flagged_stale(self):
        fill = datetime(2026, 1, 16, 21, 58, tzinfo=UTC)  # Friday 16:58 NY, Friday close at 17:00 NY
        market = flat_market(fill, 10)
        result = bt.simulate_trade(market, fill, "LONG", 1.0980, 1.1040, "I2")
        self.assertEqual((result["reason"], result["exit_at"], result["stale_exit"]),
                         (bt.FRIDAY, fill + bt.FIVE, True))

    def test_i2_deadline_from_actual_fill_time_and_fill_outside_session_skipped(self):
        slot = datetime(2026, 1, 13, 16, 55, tzinfo=UTC)  # London open at the signal -> 22:00 UTC same day
        market = flat_market(datetime(2026, 1, 13, 17, 30, tzinfo=UTC), 400)  # slot bar missing; fill NY-only
        result = bt.simulate_trade(market, slot, "LONG", 1.0980, 1.1040, "I2")
        self.assertEqual(result["fill_at"], datetime(2026, 1, 13, 17, 30, tzinfo=UTC))
        self.assertEqual(result["exit_at"], datetime(2026, 1, 14, 17, 0, tzinfo=UTC))  # next weekday 17:00 London
        late = flat_market(datetime(2026, 1, 13, 22, 30, tzinfo=UTC), 50)  # fill after the NY close
        self.assertEqual(bt.simulate_trade(late, slot, "LONG", 1.0980, 1.1040, "I2"),
                         {"rejected": "fill_outside_session"})

    def test_f3_fill_gate_rejects_fill_below_2_5r(self):
        market = flat_market(self.slot, 20)
        self.assertEqual(bt.simulate_trade(market, self.slot, "LONG", 1.0990, 1.1020, "F3", 2.5),
                         {"rejected": "fill_rr_below_2_5"})


class Portfolio(unittest.TestCase):
    def test_one_position_at_a_time_and_one_entry_per_setup(self):
        slot = datetime(2026, 1, 13, 10, 0, tzinfo=UTC)
        market = flat_market(slot, 400)
        plan = {"entry": 1.1, "stop": 1.0980, "target": 1.1040}

        def signal(minutes, setup_id):
            return {"slot": (slot + timedelta(minutes=minutes)).isoformat(), "status": "VALID_SETUP",
                    "setup_id": setup_id, "side": "LONG", "V2_I2": plan}
        signals = [signal(0, "a"), signal(15, "b"), signal(60 * 24, "a"), signal(60 * 24 + 15, "c")]
        trades, skips, _, halts = bt.run_policy(signals, market, "V2_I2")
        self.assertEqual([t["setup_id"] for t in trades], ["a", "c"])
        self.assertEqual(skips["position_open"], 1)
        self.assertEqual(skips["setup_already_traded"], 1)
        self.assertEqual(halts, [])

    def test_trading_day_key_rolls_at_ny_17(self):
        self.assertEqual(str(bt.day_key(datetime(2026, 1, 13, 21, 59, tzinfo=UTC))), "2026-01-13")
        self.assertEqual(str(bt.day_key(datetime(2026, 1, 13, 22, 0, tzinfo=UTC))), "2026-01-14")

    def test_drawdown_and_streak(self):
        self.assertAlmostEqual(bt.max_drawdown([1, -1, -1, 2, -1.5]), 2.0)
        trades = [{"r": 1.0, "marks": np.array([0.5, 2.0, -0.2])}, {"r": -1.0, "marks": np.array([-0.5])}]
        self.assertAlmostEqual(bt.mtm_drawdown(trades), 2.2)  # open peak +2.0 -> open trough -0.2
        self.assertAlmostEqual(bt.mtm_drawdown([{"r": 0.5, "marks": np.array([-0.9])}]), 0.9)
        self.assertEqual(bt.longest_losing_streak([1, -1, -1, 2, -1]), 2)


if __name__ == "__main__":
    unittest.main()
