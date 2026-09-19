"""Approved PAPER economics and quantity grid, without a real broker."""
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace
import unittest

from core.contracts import FloorRunReport, SetupAssessment, TradePlan
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from riesgo import crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime.paper_contracts import apply_paper_quantity_increment, paper_instruments
from test_demo_runner import T
from datetime import timedelta


class PaperContractTests(unittest.TestCase):
    def report(self, symbol, side='LONG', entry=None):
        entry = entry or (2500.03 if symbol == 'XAUUSD' else 1.10003)
        distance = 7.13 if symbol == 'XAUUSD' else .00123
        stop = entry-distance if side == 'LONG' else entry+distance
        target = entry+3*distance if side == 'LONG' else entry-3*distance
        plan = TradePlan('1.0', symbol, side, '5m', entry, stop, target, 3., run_id='paper-test', as_of=T)
        risk = evaluar_trade_plan(plan, 10000, paper_instruments()[symbol], crear_configuracion_riesgo_v2())
        setup = SetupAssessment('1.0', 'paper-test', T, symbol, 'VALID_SETUP', side, ('1h', '15m', '5m'))
        return FloorRunReport('1.0', 'paper-test', T, symbol, {}, {}, None, setup, plan, risk, 'PLAN_READY')

    def test_paper_units_and_increments_are_explicit(self):
        instruments = paper_instruments()
        self.assertEqual(set(instruments), {'XAUUSD', 'EURUSD'})
        self.assertEqual((instruments['XAUUSD'].price_increment, instruments['XAUUSD'].quantity_increment), (.01, .001))
        self.assertEqual((instruments['EURUSD'].price_increment, instruments['EURUSD'].quantity_increment), (.00001, 1.))
        self.assertEqual({i.contract_multiplier for i in instruments.values()}, {1.})

    def test_rounding_never_increases_risk_or_changes_prices_long_short(self):
        for symbol, instrument in paper_instruments().items():
            for side in ('LONG', 'SHORT'):
                with self.subTest(symbol=symbol, side=side):
                    original = self.report(symbol, side)
                    adjusted = apply_paper_quantity_increment(original, instrument)
                    self.assertEqual(adjusted.trade_plan, original.trade_plan)
                    self.assertEqual(adjusted.risk_decision.status, 'APPROVED')
                    self.assertLessEqual(adjusted.risk_decision.quantity, original.risk_decision.quantity)
                    self.assertLessEqual(adjusted.risk_decision.capital_at_risk, original.risk_decision.capital_at_risk)
                    self.assertLessEqual(adjusted.risk_decision.capital_at_risk, 100.)
                    self.assertEqual(Decimal(str(adjusted.risk_decision.quantity)) % Decimal(str(instrument.quantity_increment)), 0)
                    broker = PaperBroker(PaperAccount('1.0', 'paper-main', 10000, 10000, 10000), instrument)
                    order = broker.submit_plan(adjusted, {}, T)
                    self.assertIsNotNone(order)
                    self.assertEqual(order.quantity, adjusted.risk_decision.quantity)

    def test_subminimum_quantity_is_rejected_not_rounded_up(self):
        report = self.report('EURUSD')
        report = replace(report, risk_decision=replace(report.risk_decision, quantity=.9, capital_at_risk=.01))
        result = apply_paper_quantity_increment(report, paper_instruments()['EURUSD'])
        self.assertEqual(result.final_status, 'RISK_REJECTED')
        self.assertEqual(result.risk_decision.quantity, 0)
        self.assertEqual(result.risk_decision.reason, 'paper_quantity_below_increment')

    def test_rejected_or_missing_risk_is_never_promoted(self):
        report = self.report('XAUUSD')
        for decision in (None, replace(report.risk_decision, status='REJECTED')):
            blocked = replace(report, risk_decision=decision, final_status='RISK_REJECTED')
            self.assertIs(apply_paper_quantity_increment(blocked, None), blocked)

    def test_invalid_quantity_step_fails_closed(self):
        report = self.report('XAUUSD')
        for step in (None, 0, -1, float('nan'), True):
            with self.assertRaises(ValueError):
                apply_paper_quantity_increment(report, SimpleNamespace(quantity_increment=step))

    def test_decimal_grid_boundary_preserves_exact_quantity(self):
        report = self.report('XAUUSD')
        for quantity, expected in ((1.234, 1.234), (1.2349, 1.234), (.001, .001)):
            report = replace(report, risk_decision=replace(report.risk_decision, quantity=quantity))
            result = apply_paper_quantity_increment(report, paper_instruments()['XAUUSD'])
            self.assertEqual(result.risk_decision.quantity, expected)

    def test_internal_units_have_direct_usd_pnl_semantics(self):
        for symbol in ('XAUUSD', 'EURUSD'):
            for side in ('LONG', 'SHORT'):
                report = self.report(symbol, side, entry=100. if symbol == 'XAUUSD' else 1.)
                # Binary-exact geometry isolates unit semantics from floating
                # point R:R boundary behavior, already covered by broker tests.
                stop = report.trade_plan.entry + (-.125 if side == 'LONG' else .125)
                target = report.trade_plan.entry + (.5 if side == 'LONG' else -.5)
                plan = replace(report.trade_plan, stop=stop, target=target, risk_reward=4.)
                spec = paper_instruments()[symbol]
                risk = evaluar_trade_plan(plan, 10000, spec, crear_configuracion_riesgo_v2())
                report = apply_paper_quantity_increment(replace(report, trade_plan=plan, risk_decision=risk), spec)
                account = PaperAccount('1.0', 'paper-main', 10000, 10000, 10000)
                broker = PaperBroker(account, spec)
                order = broker.submit_plan(report, {}, T)
                position = broker.process_next_bar(order, {'symbol': symbol, 'timestamp': T+timedelta(minutes=5),
                    'open': plan.entry, 'high': plan.entry+.01, 'low': plan.entry-.01, 'close': plan.entry, 'is_closed': True})
                self.assertIsNotNone(position)
                closed = TradeManager(account, broker).process_bar({'symbol': symbol, 'timestamp': T+timedelta(minutes=10),
                    'open': plan.entry, 'high': max(plan.entry, plan.target), 'low': min(plan.entry, plan.target),
                    'close': plan.target, 'is_closed': True})
                self.assertEqual(len(closed), 1)
                self.assertAlmostEqual(closed[0].gross_pnl, .5 * order.quantity)
                self.assertEqual(closed[0].cost, 0.)
                self.assertAlmostEqual(account.equity, 10000 + .5 * order.quantity)


if __name__ == '__main__':
    unittest.main()
