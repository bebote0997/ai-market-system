import unittest
from datetime import datetime, timezone

from core.contracts import InstrumentSpec, MarketBar, RiskDecision, TradePlan
from core.timeframes import AUTHORIZED_TIMEFRAMES, validar_timeframe


class TestCoreContracts(unittest.TestCase):
    def test_market_bar_and_trade_plan_are_versioned(self):
        bar = MarketBar("1.0", "XAUUSD", "5m", datetime.now(timezone.utc), 1, 2, 0.5, 1.5, None, "synthetic")
        plan = TradePlan("1.0", "XAUUSD", "short", "5m", 100, 110, 70, 3)
        self.assertEqual(bar.schema_version, "1.0")
        self.assertTrue(bar.is_closed)
        self.assertEqual(plan.side, "short")
        self.assertEqual(plan.risk_reward, 3)

    def test_risk_decision_status(self):
        decision = RiskDecision("1.0", "REJECTED", "XAUUSD", "long", None, None, 0, 0, 0, "test")
        self.assertEqual(decision.status, "REJECTED")


if __name__ == "__main__":
    unittest.main()
