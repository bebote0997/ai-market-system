import unittest
from datetime import datetime

from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from core.contracts import InstrumentSpec


class Report:
    final_status = "PLAN_READY"
    run_id = "run-1"
    symbol = "TEST"
    trade_plan = type("Plan", (), {"run_id": "run-1", "side": "LONG"})()
    risk_decision = type("Decision", (), {"status": "APPROVED", "symbol": "TEST", "side": "LONG", "quantity": 1.0, "entry": 100.0, "stop": 90.0, "target": 130.0})()


class TestExecution(unittest.TestCase):
    def account(self):
        return PaperAccount("1.0", "paper", 10000.0, 10000.0, 10000.0)

    def test_submit_fill_manage_close_and_idempotency(self):
        account = self.account()
        broker = PaperBroker(account, InstrumentSpec("TEST", "synthetic", "TEST", "UTC", 0.01, 0.01, 1.0, ("5m",)))
        order = broker.submit_plan(Report(), {}, datetime(2026, 1, 1))
        self.assertIsNotNone(order)
        self.assertIs(broker.submit_plan(Report(), {}, datetime(2026, 1, 1)), order)
        position = broker.process_next_bar(order, {"timestamp": datetime(2026, 1, 2), "open": 100, "high": 101, "low": 99, "close": 100})
        self.assertEqual(position.status, "OPEN")
        manager = TradeManager(account, broker)
        closed = manager.process_bar({"timestamp": datetime(2026, 1, 3), "open": 100, "high": 131, "low": 99, "close": 130})
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].reason, "target")
        self.assertEqual(len(account.open_positions), 0)

    def test_reject_without_plan_ready(self):
        account = self.account()
        broker = PaperBroker(account)
        class Bad:
            final_status = "WATCH"
        self.assertIsNone(broker.submit_plan(Bad(), {}, datetime(2026, 1, 1)))

    def test_missing_multiplier_rejects_and_first_bar_only(self):
        account = self.account()
        broker = PaperBroker(account)
        order = broker.submit_plan(Report(), {}, datetime(2026, 1, 1))
        self.assertIsNone(order)

    def test_multiplier_and_symbol_isolation(self):
        instrument = __import__("core.contracts", fromlist=["InstrumentSpec"]).InstrumentSpec("TEST", "synthetic", "TEST", "UTC", .01, .01, 2.0, ("5m",))
        account = self.account()
        broker = PaperBroker(account, instrument)
        order = broker.submit_plan(Report(), {}, datetime(2026, 1, 1))
        position = broker.process_next_bar(order, {"symbol": "TEST", "is_closed": True, "timestamp": datetime(2026, 1, 2), "open": 100, "high": 101, "low": 99, "close": 100})
        self.assertIsNotNone(position)
        manager = TradeManager(account, broker)
        manager.process_bar({"symbol": "OTHER", "is_closed": True, "timestamp": datetime(2026, 1, 3), "open": 100, "high": 200, "low": 50, "close": 100})
        self.assertIn("TEST", account.open_positions)


if __name__ == "__main__":
    unittest.main()
