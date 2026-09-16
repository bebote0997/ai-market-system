import unittest
from datetime import datetime

from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager


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
        broker = PaperBroker(account)
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


if __name__ == "__main__":
    unittest.main()
