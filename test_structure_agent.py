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

    def test_bullish_bos_uses_confirmed_swing(self):
        data = self.bars([9, 11, 10, 11, 14], [10, 12, 11, 13, 15], [8, 9, 8, 9, 12], [9, 11, 10, 12, 14])
        report = analizar_estructura(data, "X", "1h")
        bos = report.evidence[0]["bos"]
        self.assertEqual(bos["direction"], "bullish")
        self.assertEqual(bos["broken_level"], 12.0)
        self.assertEqual(bos["confirmation_timestamp"], data.index[2])
        self.assertEqual(bos["break_timestamp"], data.index[4])

    def test_bearish_bos_uses_confirmed_swing(self):
        data = self.bars([11, 9, 10, 9, 3], [13, 12, 14, 11, 8], [8, 7, 9, 5, 2], [11, 9, 12, 7, 3])
        report = analizar_estructura(data, "X", "1h")
        bos = report.evidence[0]["bos"]
        self.assertEqual(bos["direction"], "bearish")
        self.assertEqual(bos["broken_level"], 7.0)

    def test_no_bos_without_closed_break_or_before_confirmation(self):
        data = self.bars([9, 11, 10], [10, 12, 11], [8, 9, 8], [9, 11, 11])
        self.assertIsNone(analizar_estructura(data, "X", "1h").evidence[0]["bos"])
        extended = self.bars([9, 11, 10, 11, 14], [10, 12, 11, 13, 15], [8, 9, 8, 9, 12], [9, 11, 10, 12, 14])
        self.assertIsNone(analizar_estructura(extended, "X", "1h", as_of=extended.index[3]).evidence[0]["bos"])

    def test_bullish_retracement_is_heuristic(self):
        data = self.bars([9, 11, 10, 13, 11, 12, 11], [10, 13, 11, 15, 12, 14, 13], [8, 9, 7, 10, 9, 11, 10], [9, 12, 8, 14, 10, 13, 12])
        retracement = analizar_estructura(data, "X", "1h").evidence[0]["retracement"]
        self.assertEqual(retracement["direction"], "bullish")
        self.assertEqual(retracement["classification"], "HEURISTIC")
        self.assertEqual(retracement["current_timestamp"], data.index[-1])

    def test_bearish_retracement_and_no_evidence(self):
        data = self.bars([11, 9, 10, 8, 10, 9, 10], [13, 11, 12, 10, 11, 9, 10], [8, 7, 9, 5, 6, 4, 5], [11, 8, 10, 6, 9, 7, 8])
        retracement = analizar_estructura(data, "X", "1h").evidence[0]["retracement"]
        self.assertEqual(retracement["direction"], "bearish")
        short = self.bars([10, 11, 10], [11, 12, 11], [9, 10, 9], [10, 11, 10])
        self.assertIsNone(analizar_estructura(short, "X", "1h").evidence[0]["retracement"])


if __name__ == "__main__":
    unittest.main()
