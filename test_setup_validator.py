import unittest
from datetime import datetime, timezone

from agents.setup_validator import evaluar_setup
from core.contracts import AgentMessage


class TestSetupValidator(unittest.TestCase):
    def msg(self, tf, payload, status="OK"):
        return AgentMessage("1.0", "run", datetime(2026, 1, 1, tzinfo=timezone.utc), "TEST", tf, "scout", status, evidence=(payload,))

    def scouts(self, bias="bullish"):
        structure = {tf: self.msg(tf, {"bias": bias, "structure_state": bias, "bos": {"type": "BOS"}, "retracement": {"classification": "HEURISTIC"}, "levels": {"support": 90, "resistance": 110}}) for tf in ("1h", "15m", "5m")}
        liquidity = {tf: self.msg(tf, {"sweeps": [{"type": "low"}], "liquidity_above": [], "liquidity_below": []}) for tf in ("1h", "15m", "5m")}
        return structure, liquidity

    def test_valid_long_and_missing_timeframes(self):
        structure, liquidity = self.scouts()
        result = evaluar_setup(structure, liquidity, None, "TEST", "run", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result.status, "VALID_SETUP")
        self.assertEqual(result.side, "LONG")
        partial = evaluar_setup({"1h": structure["1h"]}, {"1h": liquidity["1h"]}, None, "TEST", "run", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(partial.status, "NO_SETUP")

    def test_watch_and_macro_block(self):
        structure, liquidity = self.scouts()
        liquidity["5m"] = self.msg("5m", {"sweeps": [], "liquidity_above": [], "liquidity_below": []})
        self.assertEqual(evaluar_setup(structure, liquidity, None, "TEST", "run", datetime(2026, 1, 1, tzinfo=timezone.utc)).status, "WATCH")
        macro = AgentMessage("1.0", "run", datetime(2026, 1, 1, tzinfo=timezone.utc), "TEST", "context", "macro_news", "OK", evidence=({"macro_events": ({"impact": "HIGH", "window": "ACTIVE_WINDOW"},)},))
        self.assertEqual(evaluar_setup(structure, liquidity, macro, "TEST", "run", datetime(2026, 1, 1, tzinfo=timezone.utc)).status, "WATCH")

    def test_error_no_data_and_short(self):
        structure, liquidity = self.scouts("bearish")
        structure["1h"] = self.msg("1h", {}, "ERROR")
        self.assertEqual(evaluar_setup(structure, liquidity, None, "TEST", "run", datetime(2026, 1, 1, tzinfo=timezone.utc)).status, "NO_SETUP")
        structure, liquidity = self.scouts("bearish")
        result = evaluar_setup(structure, liquidity, None, "TEST", "run", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result.side, "SHORT")

    def test_mismatched_lineage_symbol_timeframe_and_future_fail_closed(self):
        structure, liquidity = self.scouts()
        structure["1h"] = AgentMessage("1.0", "other", datetime(2026, 1, 1, tzinfo=timezone.utc), "OTHER", "1h", "structure", "OK", evidence=structure["1h"].evidence)
        result = evaluar_setup(structure, liquidity, None, "TEST", "run", datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(result.status, "NO_SETUP")


if __name__ == "__main__":
    unittest.main()
