import unittest

import pandas as pd

from agents.liquidity_agent import analizar_liquidez, analizar_liquidez_multitimeframe


class TestLiquidityAgent(unittest.TestCase):
    def bars(self, highs, lows, closes=None, closed=None):
        closes = closes or [(high + low) / 2 for high, low in zip(highs, lows)]
        data = {"High": highs, "Low": lows, "Close": closes}
        if closed is not None:
            data["is_closed"] = closed
        return pd.DataFrame(data, index=pd.date_range("2026-01-01", periods=len(highs), freq="h"))

    def test_equal_highs_lows_and_pools(self):
        data = self.bars([100, 110, 100, 110, 100, 105], [90, 80, 90, 80, 90, 85])
        report = analizar_liquidez(data, "XAUUSD", "15m")
        self.assertEqual(report.status, "OK")
        payload = report.evidence[0]
        self.assertTrue(payload["equal_highs"])
        self.assertTrue(payload["equal_lows"])
        self.assertTrue(payload["liquidity_above"])
        self.assertTrue(payload["liquidity_below"])
        self.assertEqual(payload["equal_highs"][0]["classification"], "HEURISTIC")

    def test_sweep_high_and_low(self):
        high_sweep = self.bars([100, 102, 105], [90, 91, 92], [95, 101, 99])
        low_sweep = self.bars([110, 109, 108], [100, 98, 95], [105, 99, 101])
        self.assertEqual(len(analizar_liquidez(high_sweep, "X", "5m").evidence[0]["sweeps"]), 1)
        self.assertEqual(analizar_liquidez(high_sweep, "X", "5m").evidence[0]["sweeps"][0]["type"], "high")
        self.assertEqual(analizar_liquidez(low_sweep, "X", "5m").evidence[0]["sweeps"][0]["type"], "low")

    def test_partial_and_incomplete(self):
        data = self.bars([100, 101], [90, 91])
        self.assertEqual(analizar_liquidez(data, "X", "5m").status, "PARTIAL")
        data = self.bars([100, 101, 102], [90, 91, 92], closed=[True, True, False])
        self.assertEqual(analizar_liquidez(data, "X", "5m").status, "PARTIAL")

    def test_missing_timeframe_and_duplicates(self):
        data = self.bars([100, 101, 102], [90, 91, 92])
        self.assertEqual(analizar_liquidez_multitimeframe({"5m": data}, "X").status, "PARTIAL")
        data.index = [data.index[0], data.index[0], data.index[2]]
        self.assertNotEqual(analizar_liquidez(data, "X", "5m").status, "OK")

    def test_future_bars_do_not_change_prior_sweep(self):
        prefix = self.bars([100, 102, 105], [90, 91, 92], [95, 101, 99])
        future = self.bars([100, 102, 105, 120], [90, 91, 92, 80], [95, 101, 99, 100])
        old = analizar_liquidez(prefix, "X", "5m", as_of=prefix.index[-1]).evidence[0]["sweeps"]
        new = analizar_liquidez(future, "X", "5m", as_of=prefix.index[-1]).evidence[0]["sweeps"]
        self.assertEqual(old, new)


if __name__ == "__main__":
    unittest.main()
