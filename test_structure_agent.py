import unittest

import pandas as pd

from agents.structure_agent import analizar_estructura, analizar_estructura_multitimeframe


class TestStructureAgent(unittest.TestCase):
    def bars(self, opens, highs, lows, closes, closed=None):
        data = {"Open": opens, "High": highs, "Low": lows, "Close": closes}
        if closed is not None:
            data["is_closed"] = closed
        return pd.DataFrame(data, index=pd.date_range("2026-01-01", periods=len(opens), freq="h"))

    def test_bullish_hh_hl_and_levels(self):
        data = self.bars([10, 11, 10, 12, 11, 13, 12], [11, 13, 12, 15, 14, 17, 16], [9, 10, 8, 11, 10, 12, 11], [10, 12, 9, 14, 13, 16, 15])
        report = analizar_estructura(data, "XAUUSD", "1h")
        self.assertEqual(report.status, "OK")
        payload = report.evidence[0]
        self.assertEqual(payload["structure_state"], "bullish")
        self.assertEqual(payload["levels"]["support"], 8.0)
        self.assertEqual(payload["levels"]["resistance"], 17.0)

    def test_bearish_lh_ll(self):
        data = self.bars([10, 9, 10, 8, 9, 7, 8], [13, 12, 14, 11, 12, 10, 9], [8, 7, 9, 5, 6, 4, 5], [10, 8, 12, 7, 8, 6, 7])
        payload = analizar_estructura(data, "XAUUSD", "1h").evidence[0]
        self.assertEqual(payload["structure_state"], "bearish")

    def test_partial_insufficient_and_incomplete_bar(self):
        data = self.bars([10, 11], [11, 12], [9, 10], [10, 11])
        self.assertEqual(analizar_estructura(data, "X", "1h").status, "PARTIAL")
        data = self.bars([10, 11, 12], [11, 12, 13], [9, 10, 11], [10, 11, 12], [True, True, False])
        self.assertEqual(analizar_estructura(data, "X", "1h").status, "PARTIAL")

    def test_multitimeframe_missing_is_partial(self):
        data = self.bars([10, 11, 12], [11, 12, 13], [9, 10, 11], [10, 11, 12])
        report = analizar_estructura_multitimeframe({"1h": data}, "X")
        self.assertEqual(report.status, "PARTIAL")
        self.assertEqual(report.data_quality["timeframes"], ("1h",))

    def test_duplicate_timestamps_do_not_produce_ok(self):
        data = self.bars([10, 11, 12], [11, 12, 13], [9, 10, 11], [10, 11, 12])
        data.index = [data.index[0], data.index[0], data.index[2]]
        report = analizar_estructura(data, "X", "1h")
        self.assertNotEqual(report.status, "OK")

    def test_future_bars_do_not_change_prior_detected_swings(self):
        prefix = self.bars([10, 11, 10, 12, 11], [11, 13, 12, 15, 14], [9, 10, 8, 11, 10], [10, 12, 9, 14, 13])
        future = self.bars([10, 11, 10, 12, 11, 13], [11, 13, 12, 15, 14, 16], [9, 10, 8, 11, 10, 12], [10, 12, 9, 14, 13, 15])
        old = analizar_estructura(prefix, "X", "1h", as_of=prefix.index[-1]).evidence[0]["swings"]
        new = analizar_estructura(future, "X", "1h", as_of=prefix.index[-1]).evidence[0]["swings"]
        self.assertEqual(old, new)


if __name__ == "__main__":
    unittest.main()
