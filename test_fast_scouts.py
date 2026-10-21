"""Replay-only scout accelerator (replay/fast_scouts.py): identical outputs to the production helpers on real-like and
tie-heavy windows, and an identical production floor report under the patch."""
import unittest

import numpy as np
import pandas as pd

import agents.liquidity_agent as liquidity_agent
import agents.structure_agent as structure_agent
from replay import fast_scouts as fs


def window(n, seed, ties=False):
    rng = np.random.default_rng(seed)
    close = 1.1 + np.cumsum(rng.normal(0, 3e-4, n))
    if ties:
        close = np.round(close, 3)  # coarse grid -> many equal highs/lows
    high = close + np.abs(rng.normal(0, 2e-4, n))
    low = close - np.abs(rng.normal(0, 2e-4, n))
    if ties:
        high, low = np.round(high, 3), np.round(low, 3)
    index = pd.date_range("2024-03-01", periods=n, freq="5min", tz="UTC")
    return pd.DataFrame({"Open": np.roll(close, 1), "High": high, "Low": low, "Close": close}, index=index)


class Equivalence(unittest.TestCase):
    def test_same_outputs_as_production(self):
        for seed in range(40):
            for ties in (False, True):
                frame = window(int(3 + seed * 13 % 500), seed, ties)
                highs, lows = structure_agent._swings(frame)
                self.assertEqual(fs.swings(frame), (highs, lows))
                self.assertEqual(fs.break_of_structure(frame, highs, lows),
                                 structure_agent._break_of_structure(frame, highs, lows))
                self.assertEqual(fs.confirmed_pivots(frame), liquidity_agent._confirmed_pivots(frame))

    def test_tiny_windows(self):
        for n in (0, 1, 2, 3):
            frame = window(max(n, 1), 1).iloc[:n]
            self.assertEqual(fs.swings(frame), structure_agent._swings(frame))
            self.assertEqual(fs.confirmed_pivots(frame), liquidity_agent._confirmed_pivots(frame))

    def test_patch_is_scoped_and_reports_match(self):
        frame = window(500, 3)
        frame["is_closed"] = True
        as_of = (frame.index[-1] + pd.Timedelta(minutes=5)).to_pydatetime()
        slow = structure_agent.analizar_estructura(frame, "EURUSD", "5m", "r", as_of)
        slow_liq = liquidity_agent.analizar_liquidez(frame, "EURUSD", "5m", "r", as_of)
        original = structure_agent._swings
        with fs.patched():
            self.assertIs(structure_agent._swings, fs.swings)
            fast = structure_agent.analizar_estructura(frame, "EURUSD", "5m", "r", as_of)
            fast_liq = liquidity_agent.analizar_liquidez(frame, "EURUSD", "5m", "r", as_of)
        self.assertIs(structure_agent._swings, original)
        self.assertEqual(fast.evidence, slow.evidence)
        self.assertEqual(fast_liq.evidence, slow_liq.evidence)


if __name__ == "__main__":
    unittest.main()
