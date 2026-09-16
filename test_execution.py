import math
import unittest
from datetime import datetime, timedelta, timezone

from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from core.contracts import FloorRunReport, InstrumentSpec, RiskDecision, SetupAssessment, TradePlan
from riesgo import crear_configuracion_riesgo_v2, evaluar_trade_plan


class Report:
    final_status = "PLAN_READY"
    run_id = "run-1"
    symbol = "TEST"
    trade_plan = type("Plan", (), {"run_id": "run-1", "side": "LONG"})()
    risk_decision = type("Decision", (), {"status": "APPROVED", "symbol": "TEST", "side": "LONG", "quantity": 1.0, "entry": 100.0, "stop": 90.0, "target": 130.0})()


class TestExecution(unittest.TestCase):
    def account(self, equity=10000.0):
        return PaperAccount("1.0", "paper", 10000.0, equity, equity)

    def t(self, days):
        return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=days)

    def instrument(self, multiplier=1.0):
        return InstrumentSpec("TEST", "synthetic", "TEST", "UTC", 0.01, 0.01, multiplier, ("5m",))

    def bar(self, timestamp, symbol="TEST", open_price=100, high=101, low=99, close=100, is_closed=True):
        return {"symbol": symbol, "timestamp": timestamp, "open": open_price, "high": high, "low": low, "close": close, "is_closed": is_closed}

    def test_submit_fill_manage_close_and_idempotency(self):
        account = self.account()
        broker = PaperBroker(account, self.instrument())
        order = broker.submit_plan(Report(), {}, self.t(0))
        self.assertIsNotNone(order)
        self.assertIs(broker.submit_plan(Report(), {}, self.t(0)), order)
        position = broker.process_next_bar(order, self.bar(self.t(1)))
        self.assertEqual(position.status, "OPEN")
        manager = TradeManager(account, broker)
        closed = manager.process_bar(self.bar(self.t(2), high=131, close=130))
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].reason, "target")
        self.assertEqual(len(account.open_positions), 0)

    def test_reject_without_plan_ready(self):
        account = self.account()
        broker = PaperBroker(account)
        class Bad:
            final_status = "WATCH"
        self.assertIsNone(broker.submit_plan(Bad(), {}, self.t(0)))

    def test_missing_multiplier_rejects_and_first_bar_only(self):
        account = self.account()
        broker = PaperBroker(account)
        order = broker.submit_plan(Report(), {}, self.t(0))
        self.assertIsNone(order)

    def test_multiplier_and_symbol_isolation(self):
        account = self.account()
        broker = PaperBroker(account, self.instrument(2.0))
        order = broker.submit_plan(Report(), {}, self.t(0))
        position = broker.process_next_bar(order, self.bar(self.t(1)))
        self.assertIsNotNone(position)
        manager = TradeManager(account, broker)
        manager.process_bar({"symbol": "OTHER", "is_closed": True, "timestamp": datetime(2026, 1, 3), "open": 100, "high": 200, "low": 50, "close": 100})
        self.assertIn("TEST", account.open_positions)

    def report(self, side="LONG", entry=100.0, stop=90.0, target=130.0, run_id="run-1"):
        plan = type("Plan", (), {"run_id": run_id, "side": side, "as_of": self.t(0)})()
        decision = type("Decision", (), {"status": "APPROVED", "symbol": "TEST", "side": side, "quantity": 1.0, "entry": entry, "stop": stop, "target": target})()
        return type("Report", (), {"final_status": "PLAN_READY", "run_id": run_id, "symbol": "TEST", "trade_plan": plan, "risk_decision": decision})()

    def open_position(self, side="LONG", multiplier=1.0, equity=10000.0, stop=90.0, target=130.0):
        account = self.account(equity)
        broker = PaperBroker(account, self.instrument(multiplier))
        order = broker.submit_plan(self.report(side, stop=stop, target=target), {}, self.t(0))
        position = broker.process_next_bar(order, self.bar(self.t(1)))
        return account, broker, order, position

    def test_multiplier_values_fail_closed(self):
        values = [None, float("nan"), float("inf"), True, 0.0, -1.0]
        for value in values:
            with self.subTest(value=value):
                account = self.account()
                broker = PaperBroker(account, self.instrument())
                order = PaperOrder("1.0", "o", "r", "TEST", "LONG", 1.0, 100, 90, 130, value)
                self.assertIsNone(broker.process_next_bar(order, self.bar(self.t(1))))
                self.assertEqual(order.status, "REJECTED")
                position = PaperPosition("1.0", "p", "o", "r", "TEST", "LONG", 1.0, 100, 100, 90, 130, self.t(1), contract_multiplier=value)
                account.open_positions["TEST"] = position
                self.assertEqual(TradeManager(account, broker).process_bar(self.bar(self.t(2), close=105, high=106)), [])
                self.assertIsNone(position.last_processed_at)

    def test_broker_rejects_naive_as_of_and_bar_timestamp(self):
        account = self.account()
        broker = PaperBroker(account, self.instrument())
        self.assertIsNone(broker.submit_plan(self.report(), {}, datetime(2026, 1, 1)))
        order = broker.submit_plan(self.report("LONG", run_id="aware"), {}, self.t(0))
        self.assertIsNone(broker.process_next_bar(order, self.bar(datetime(2026, 1, 2))))
        self.assertEqual(order.status, "CANCELLED")

    def test_broker_requires_exact_symbol_closed_complete_ohlc(self):
        account = self.account()
        broker = PaperBroker(account, self.instrument())
        for bad_bar in (
            self.bar(self.t(1), symbol="OTHER"),
            {**self.bar(self.t(1)), "symbol": None},
            {**self.bar(self.t(1)), "is_closed": False},
            {**self.bar(self.t(1)), "high": 95},
            {**self.bar(self.t(1)), "close": float("nan")},
        ):
            order = broker.submit_plan(self.report(run_id=str(id(bad_bar))), {}, self.t(0))
            self.assertIsNone(broker.process_next_bar(order, bad_bar))
            self.assertEqual(order.status, "CANCELLED")

    def test_current_equity_is_rechecked_at_fill(self):
        account = self.account(10000.0)
        broker = PaperBroker(account, self.instrument(2.0))
        order = broker.submit_plan(self.report(), {}, self.t(0))
        account.equity = 1000.0
        self.assertIsNone(broker.process_next_bar(order, self.bar(self.t(1))))
        self.assertEqual(order.status, "REJECTED")
        self.assertTrue(any(event.event_type == "ORDER_REJECTED" for event in broker.journal))

    def test_post_fill_rr_below_three_is_rejected(self):
        account = self.account()
        broker = PaperBroker(account, self.instrument())
        order = broker.submit_plan(self.report(target=120.0), {}, self.t(0))
        self.assertIsNone(broker.process_next_bar(order, self.bar(self.t(1))))
        self.assertEqual(order.status, "REJECTED")
        self.assertEqual(len(account.open_positions), 0)

    def test_quantity_is_preserved_from_decision_to_position(self):
        account = self.account()
        broker = PaperBroker(account, self.instrument(2.0))
        report = self.report()
        report.risk_decision.quantity = 3.0
        order = broker.submit_plan(report, {}, self.t(0))
        self.assertEqual(order.quantity, 3.0)
        position = broker.process_next_bar(order, self.bar(self.t(1)))
        self.assertEqual(position.quantity, order.quantity)

    def test_opened_at_guard_duplicate_and_out_of_order(self):
        account, broker, order, position = self.open_position()
        manager = TradeManager(account, broker)
        before = self.bar(self.t(0), close=120, high=121)
        same = self.bar(self.t(1), close=120, high=121)
        first = self.bar(self.t(2), close=105, high=106)
        self.assertEqual(manager.process_bar(before), [])
        self.assertEqual(position.last_price, 100)
        self.assertEqual(manager.process_bar(same), [])
        self.assertIsNone(position.last_processed_at)
        self.assertEqual(manager.process_bar(first), [])
        self.assertEqual(position.last_price, 105)
        self.assertEqual(manager.process_bar(first), [])
        self.assertEqual(manager.process_bar(self.bar(self.t(1), close=110, high=111)), [])
        self.assertEqual(position.last_price, 105)

    def test_forming_bar_does_not_mutate_management_state(self):
        account, broker, order, position = self.open_position()
        manager = TradeManager(account, broker)
        self.assertEqual(manager.process_bar(self.bar(self.t(2), high=131, close=130, is_closed=False)), [])
        self.assertEqual(position.last_price, 100)
        self.assertIsNone(position.last_processed_at)
        self.assertIn("TEST", account.open_positions)

    def test_invalid_side_fails_closed(self):
        account = self.account()
        broker = PaperBroker(account, self.instrument())
        self.assertIsNone(broker.submit_plan(self.report("INVALID"), {}, self.t(0)))

    def test_state_machine_pending_cancelled(self):
        account = self.account()
        broker = PaperBroker(account, self.instrument())
        order = broker.submit_plan(self.report(), {}, self.t(0))
        self.assertIsNone(broker.process_next_bar(order, self.bar(self.t(0))))
        self.assertEqual(order.status, "CANCELLED")
        self.assertIsNone(broker.process_next_bar(order, self.bar(self.t(1))))
        self.assertEqual(order.status, "CANCELLED")

    def test_state_machine_pending_rejected(self):
        account = self.account(1000.0)
        broker = PaperBroker(account, self.instrument(2.0))
        order = broker.submit_plan(self.report(), {}, self.t(0))
        self.assertIsNone(broker.process_next_bar(order, self.bar(self.t(1))))
        self.assertEqual(order.status, "REJECTED")
        self.assertIsNone(broker.process_next_bar(order, self.bar(self.t(2))))

    def test_state_machine_filled_terminal_and_open_closed_terminal(self):
        account, broker, order, position = self.open_position()
        self.assertEqual(order.status, "FILLED")
        self.assertIsNone(broker.process_next_bar(order, self.bar(self.t(2))))
        manager = TradeManager(account, broker)
        closed = manager.process_bar(self.bar(self.t(2), high=131, close=130))
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].reason, "target")
        self.assertEqual(position.status, "CLOSED")
        self.assertEqual(manager.process_bar(self.bar(self.t(3), high=131, close=130)), [])

    def test_long_normal_stop(self):
        account, broker, order, position = self.open_position()
        self.assertEqual(TradeManager(account, broker).process_bar(self.bar(self.t(2), low=89, close=95))[0].reason, "stop")

    def test_long_normal_target(self):
        account, broker, order, position = self.open_position()
        self.assertEqual(TradeManager(account, broker).process_bar(self.bar(self.t(2), high=131, close=130))[0].reason, "target")

    def test_long_gap_stop(self):
        account, broker, order, position = self.open_position()
        self.assertEqual(TradeManager(account, broker).process_bar(self.bar(self.t(2), open_price=85, high=90, low=80, close=85))[0].reason, "stop")

    def test_long_gap_target(self):
        account, broker, order, position = self.open_position()
        self.assertEqual(TradeManager(account, broker).process_bar(self.bar(self.t(2), open_price=135, high=140, low=135, close=136))[0].reason, "target")

    def test_long_stop_first_same_bar(self):
        account, broker, order, position = self.open_position()
        closed = TradeManager(account, broker).process_bar(self.bar(self.t(2), high=131, low=89, close=100))
        self.assertEqual(closed[0].reason, "stop")

    def test_short_normal_stop(self):
        account, broker, order, position = self.open_position("SHORT", stop=110, target=70)
        self.assertEqual(TradeManager(account, broker).process_bar(self.bar(self.t(2), high=111, close=105))[0].reason, "stop")

    def test_short_normal_target(self):
        account, broker, order, position = self.open_position("SHORT", stop=110, target=70)
        self.assertEqual(TradeManager(account, broker).process_bar(self.bar(self.t(2), low=69, close=70))[0].reason, "target")

    def test_short_gap_stop(self):
        account, broker, order, position = self.open_position("SHORT", stop=110, target=70)
        self.assertEqual(TradeManager(account, broker).process_bar(self.bar(self.t(2), open_price=115, high=120, low=115, close=116))[0].reason, "stop")

    def test_short_gap_target(self):
        account, broker, order, position = self.open_position("SHORT", stop=110, target=70)
        self.assertEqual(TradeManager(account, broker).process_bar(self.bar(self.t(2), open_price=65, high=65, low=60, close=64))[0].reason, "target")

    def test_short_stop_first_same_bar(self):
        account, broker, order, position = self.open_position("SHORT", stop=110, target=70)
        closed = TradeManager(account, broker).process_bar(self.bar(self.t(2), high=111, low=69, close=100))
        self.assertEqual(closed[0].reason, "stop")

    def test_multiplier_pnl_unrealized_and_cost_once(self):
        account = self.account()
        broker = PaperBroker(account, self.instrument(2.0))
        order = broker.submit_plan(self.report(), {}, self.t(0))
        order.cost_rate = 0.1
        position = broker.process_next_bar(order, self.bar(self.t(1)))
        manager = TradeManager(account, broker)
        manager.process_bar(self.bar(self.t(2), close=105, high=106))
        self.assertEqual(account.unrealized_pnl, 10.0)
        closed = manager.process_bar(self.bar(self.t(3), high=131, close=130))
        self.assertEqual(closed[0].gross_pnl, 60.0)
        self.assertEqual(closed[0].cost, 0.1)
        self.assertEqual(closed[0].net_pnl, 59.9)
        self.assertEqual(account.realized_pnl, 59.9)
        self.assertEqual(account.equity, 10059.9)

    def test_journal_sequence_and_no_duplicate_events(self):
        account, broker, order, position = self.open_position()
        manager = TradeManager(account, broker)
        manager.process_bar(self.bar(self.t(2), high=131, close=130))
        manager.process_bar(self.bar(self.t(2), high=131, close=130))
        events = [event.event_type for event in broker.journal]
        self.assertEqual(events, ["ORDER_SUBMITTED", "ORDER_FILLED", "POSITION_OPENED", "TARGET_HIT", "POSITION_CLOSED"])

    def test_short_gross_pnl_and_unrealized_use_multiplier(self):
        account, broker, order, position = self.open_position("SHORT", multiplier=2.0, stop=110, target=70)
        manager = TradeManager(account, broker)
        manager.process_bar(self.bar(self.t(2), close=95, low=94))
        self.assertEqual(account.unrealized_pnl, 10.0)
        trade = manager.process_bar(self.bar(self.t(3), low=69, close=70))[0]
        self.assertEqual(trade.gross_pnl, 60.0)
        self.assertEqual(account.realized_pnl, 60.0)

    def test_multi_symbol_unrealized_equity_is_preserved(self):
        account, broker, order, test_position = self.open_position()
        other_position = PaperPosition("1.0", "other-position", "other-order", "other-run", "OTHER", "LONG", 1.0, 190, 190, 180, 220, self.t(1), last_price=200, contract_multiplier=2.0)
        account.open_positions["OTHER"] = other_position
        TradeManager(account, broker).process_bar(self.bar(self.t(2), close=105, high=106))
        self.assertEqual(account.unrealized_pnl, 25.0)
        self.assertEqual(account.equity, 10025.0)
        self.assertEqual(account.open_positions["OTHER"].last_price, 200)

    def test_no_lookahead_cutoff_state_is_stable_and_late_bar_is_ignored(self):
        account, broker, order, position = self.open_position()
        manager = TradeManager(account, broker)
        manager.process_bar(self.bar(self.t(2), close=105, high=106))
        state_at_cutoff = (position.last_price, position.last_processed_at, account.equity)
        self.assertEqual(manager.process_bar(self.bar(self.t(1), close=120, high=121)), [])
        self.assertEqual((position.last_price, position.last_processed_at, account.equity), state_at_cutoff)
        self.assertEqual(manager.process_bar(self.bar(self.t(3), close=110, high=111)), [])
        self.assertEqual((position.last_price, position.last_processed_at, account.equity), (110, self.t(3), 10010.0))

    def test_manager_rejects_naive_opened_at_and_missing_symbol(self):
        account, broker, order, position = self.open_position()
        position.opened_at = datetime(2026, 1, 2)
        manager = TradeManager(account, broker)
        self.assertEqual(manager.process_bar(self.bar(self.t(2), close=130, high=131)), [])
        position.opened_at = self.t(1)
        self.assertEqual(manager.process_bar({**self.bar(self.t(2), close=130, high=131), "symbol": None}), [])
        self.assertEqual(position.last_price, 100)

    def test_end_to_end_real_risk_broker_manager_account_journal(self):
        instrument = self.instrument(2.0)
        plan = TradePlan("1.0", "TEST", "LONG", "5m", 100.0, 90.0, 130.0, 3.0, run_id="real-run", as_of=self.t(0))
        decision = evaluar_trade_plan(plan, 10000.0, instrument, crear_configuracion_riesgo_v2())
        self.assertEqual(decision.status, "APPROVED")
        setup = SetupAssessment("1.0", "real-run", self.t(0), "TEST", "VALID_SETUP", "LONG", ("1h", "15m", "5m"), invalidation=90.0)
        report = FloorRunReport("1.0", "real-run", self.t(0), "TEST", {}, {}, None, setup, plan, decision, "PLAN_READY")
        account = self.account()
        broker = PaperBroker(account, instrument)
        order = broker.submit_plan(report, {}, self.t(0))
        position = broker.process_next_bar(order, self.bar(self.t(1)))
        TradeManager(account, broker).process_bar(self.bar(self.t(2), high=131, close=130))
        self.assertEqual(position.status, "CLOSED")
        self.assertEqual(account.realized_pnl, 300.0)
        self.assertEqual(account.equity, 10300.0)
        self.assertEqual([event.event_type for event in broker.journal], ["ORDER_SUBMITTED", "ORDER_FILLED", "POSITION_OPENED", "TARGET_HIT", "POSITION_CLOSED"])


if __name__ == "__main__":
    unittest.main()
