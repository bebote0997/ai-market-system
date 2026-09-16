import unittest
from datetime import datetime, timezone

import pandas as pd

from agents.trade_planner import crear_trade_plan
from core.contracts import SetupAssessment


class TestTradePlanner(unittest.TestCase):
    def setup(self, side):
        return SetupAssessment("1.0", "run", datetime(2026, 1, 1, tzinfo=timezone.utc), "TEST", "VALID_SETUP", side, ("1h", "15m", "5m"), evidence=({"source": "fixture"},), invalidation=90.0 if side == "LONG" else 110.0)

    def datos(self):
        index = pd.date_range("2026-01-01", periods=2, freq="h")
        return pd.DataFrame({"Close": [100.0, 101.0]}, index=index)

    def test_long_short_and_rr(self):
        self.assertEqual(crear_trade_plan(self.setup("LONG"), {"data": self.datos()}, "TEST", "run", self.datos().index[-1]).side, "LONG")
        plan = crear_trade_plan(self.setup("SHORT"), {"data": self.datos()}, "TEST", "run", self.datos().index[-1])
        self.assertEqual(plan.side, "SHORT")
        self.assertEqual(plan.target, 74.0)

    def test_non_valid_or_missing_stop_returns_none(self):
        setup = self.setup("LONG")
        setup = SetupAssessment(setup.schema_version, setup.run_id, setup.timestamp, setup.symbol, "WATCH", setup.side, setup.timeframes, invalidation=setup.invalidation)
        self.assertIsNone(crear_trade_plan(setup, {"data": self.datos()}, "TEST", "run", self.datos().index[-1]))
        invalid = self.setup("LONG")
        invalid = SetupAssessment(invalid.schema_version, invalid.run_id, invalid.timestamp, invalid.symbol, invalid.status, invalid.side, invalid.timeframes, invalidation=None)
        self.assertIsNone(crear_trade_plan(invalid, {"data": self.datos()}, "TEST", "run", self.datos().index[-1]))

    def test_uses_only_closed_data_as_of(self):
        datos = self.datos()
        plan = crear_trade_plan(self.setup("LONG"), {"data": datos}, "TEST", "run", datos.index[0])
        self.assertEqual(plan.entry, 100.0)


if __name__ == "__main__":
    unittest.main()
