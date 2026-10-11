"""V2 Phase 5 / P5.0: characterization of the CURRENT Risk Engine and PAPER risk behavior (main 3edb648).

Pins what exists today — including gaps — so every P5.1 difference is an explicit, Owner-approved change.
Non-production: imports production code read-only; changes nothing.
"""
import inspect
from decimal import Decimal
import unittest
from datetime import datetime, timedelta, timezone

from core.contracts import FloorRunReport, SetupAssessment, TradePlan
from core.rr_contract import POLICY_V2_F3
from execution.contracts import PaperAccount, PaperPosition
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from riesgo import crear_configuracion_riesgo_fixed_3r, crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime.config import RuntimeConfig
from runtime.paper_contracts import apply_paper_quantity_increment, paper_instruments

AT = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)
INS = paper_instruments()
V1 = crear_configuracion_riesgo_v2()


def plan(symbol, side, entry, stop, target, rr=3.0, policy="V1"):
    return TradePlan("1.0", symbol, side, "5m", entry, stop, target, rr, invalidation=str(stop), run_id="run",
                     as_of=AT, policy_version=policy)


def paper_qty(p, decision):
    setup = SetupAssessment("1.0", "run", AT, p.symbol, "VALID_SETUP", p.side, ("1h", "15m", "5m"))
    report = FloorRunReport("1.0", "run", AT, p.symbol, {}, {}, None, setup, p, decision, "PLAN_READY")
    return apply_paper_quantity_increment(report, INS[p.symbol]).risk_decision


class IndividualRiskTests(unittest.TestCase):
    """quantity = min(equity x 1% / (|entry - SL| x mult), equity x 100% / (entry x mult)); then PAPER floor."""

    def test_config_defaults_and_not_runtime_configurable(self):
        self.assertEqual((V1["riesgo_por_operacion_pct"], V1["maximo_capital_pct"], V1["ratio_minimo"]), (1.0, 100.0, 3.0))
        fields = set(inspect.signature(RuntimeConfig).parameters)
        self.assertFalse({f for f in fields if "risk" in f or "riesgo" in f})  # no env/config path for risk policy

    def test_worked_examples(self):
        cases = {  # (symbol, side, entry, SL, TP) -> (binding constraint, Risk quantity, capital_at_risk, PAPER qty)
            ("EURUSD", "LONG", 1.085, 1.078, 1.106): ("notional", 9216.589861751152, 64.5161290322571, 9216.0),
            ("EURUSD", "SHORT", 1.085, 1.096, 1.052): ("risk", 9090.909090908992, 100.00000000000001, 9090.0),
            ("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0): ("risk", 2.0, 100.0, 2.0),
            ("XAUUSD", "SHORT", 2650.0, 2660.0, 2620.0): ("notional", 3.7735849056603774, 37.735849056603776, 3.773),
        }
        for (symbol, side, entry, sl, tp), (binding, qty, at_risk, paper) in cases.items():
            with self.subTest(symbol=symbol, side=side):
                p = plan(symbol, side, entry, sl, tp)
                d = evaluar_trade_plan(p, 10000.0, INS[symbol], V1)
                self.assertEqual((d.status, d.quantity, d.capital_at_risk, d.risk_fraction), ("APPROVED", qty, at_risk, 0.01))
                risk_qty, notional_qty = 100.0 / abs(entry - sl), 10000.0 / entry
                self.assertEqual(binding, "risk" if risk_qty <= notional_qty else "notional")
                rounded = paper_qty(p, d)
                self.assertEqual(rounded.quantity, paper)
                self.assertLessEqual(rounded.quantity * abs(entry - sl), 100.0)  # rounding only reduces money risk

    def test_float_epsilon_can_exceed_one_percent_before_paper_rounding(self):
        d = evaluar_trade_plan(plan("EURUSD", "SHORT", 1.085, 1.096, 1.052), 10000.0, INS["EURUSD"], V1)
        self.assertGreater(d.capital_at_risk, 100.0)  # pinned: 100.00000000000001 (P5 LOW finding)

    def test_invalid_equity_and_extreme_stops(self):
        for equity in (0.0, -1.0, float("nan"), float("inf"), True):
            self.assertEqual(evaluar_trade_plan(plan("XAUUSD", "LONG", 2650.0, 2640.0, 2680.0), equity, INS["XAUUSD"],
                                                V1).reason, "invalid_numeric_value")
        tiny = evaluar_trade_plan(plan("XAUUSD", "LONG", 2650.0, 2649.99, 2650.03), 10000.0, INS["XAUUSD"], V1)
        self.assertEqual((tiny.quantity, round(tiny.capital_at_risk, 4)), (3.7735849056603774, 0.0377))  # notional cap
        wide = evaluar_trade_plan(plan("XAUUSD", "LONG", 2650.0, 1650.0, 5650.0), 10000.0, INS["XAUUSD"], V1)
        self.assertEqual((wide.quantity, wide.capital_at_risk), (0.1, 100.0))  # risk-bound, still 1%


class PortfolioStateTests(unittest.TestCase):
    def test_equity_basis_is_starting_plus_realized_plus_unrealized(self):
        account = PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0)
        position = PaperPosition("1.0", "p", "o", "r", "EURUSD", "LONG", 10000.0, 1.08, 1.08, 1.07, 1.11,
                                 AT - timedelta(hours=1), last_price=1.08, contract_multiplier=1.0)
        account.open_positions["EURUSD"] = position
        TradeManager(account, PaperBroker(account)).process_bar({"symbol": "EURUSD", "timestamp": AT, "open": 1.09,
                                                                "high": 1.09, "low": 1.09, "close": 1.09,
                                                                "is_closed": True})
        self.assertAlmostEqual(account.equity, 10100.0)  # unrealized profit raises the sizing basis
        self.assertEqual(account.cash, 10000.0)  # cash is never updated (not a risk input)
        bigger = evaluar_trade_plan(plan("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0), account.equity, INS["XAUUSD"], V1)
        self.assertAlmostEqual(bigger.capital_at_risk, 101.0)

    def test_risk_has_no_portfolio_input(self):
        params = list(inspect.signature(evaluar_trade_plan).parameters)
        self.assertEqual(params, ["plan", "capital_actual", "instrumento", "configuracion", "atr"])
        # Two symbols each approved at 1% with no aggregate check: 2% total open risk is possible today.
        a = evaluar_trade_plan(plan("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0), 10000.0, INS["XAUUSD"], V1)
        b = evaluar_trade_plan(plan("EURUSD", "SHORT", 1.085, 1.096, 1.052), 10000.0, INS["EURUSD"], V1)
        self.assertEqual((a.status, b.status), ("APPROVED", "APPROVED"))

    def test_one_position_per_symbol_is_the_only_exposure_limit(self):
        account = PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0)
        broker = PaperBroker(account, INS["XAUUSD"])
        p = plan("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0)
        d = evaluar_trade_plan(p, 10000.0, INS["XAUUSD"], V1)
        report = FloorRunReport("1.0", "run", AT, "XAUUSD", {}, {}, None, None, p, d, "PLAN_READY")
        account.open_positions["XAUUSD"] = object()
        self.assertIsNone(broker.submit_plan(report, None, AT))  # same symbol open -> refused by the broker


class FillMoneyRiskTests(unittest.TestCase):
    def fill(self, open_price, config, policy=None, risk_plan=None):
        p = risk_plan or plan("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, policy="V1" if policy is None else POLICY_V2_F3)
        d = evaluar_trade_plan(p, 10000.0, INS["XAUUSD"], config)
        report = FloorRunReport("1.0", "run", AT, "XAUUSD", {}, {}, None, None, p, d, "PLAN_READY")
        broker = PaperBroker(PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0), INS["XAUUSD"], rr_policy=policy)
        order = broker.submit_plan(report, None, AT)
        broker.process_next_bar(order, {"symbol": "XAUUSD", "timestamp": AT + timedelta(minutes=5), "open": open_price,
                                        "high": open_price, "low": open_price, "close": open_price, "is_closed": True})
        return order.status, {e.event_type: e.details for e in broker.journal}

    def test_fixed_3r_fill_money_limit_superseded_by_dec_5_7(self):
        # P5.0 pinned a hard-coded 1% money cap at the fill (H1: 2651 was REJECTED post_fill_risk_or_geometry).
        # P5.1 / DEC-5.7 explicitly supersedes it for fixed 3R: actual money risk <= planned money risk x 8/7.
        f3 = crear_configuracion_riesgo_fixed_3r()
        self.assertEqual(self.fill(2650.0, f3, POLICY_V2_F3)[0], "FILLED")  # money risk exactly planned
        status, events = self.fill(2651.0, f3, POLICY_V2_F3)  # actual R:R 2.92 >= 2.50, money 1.02 x planned
        self.assertEqual(status, "FILLED")
        self.assertEqual(Decimal(events["ORDER_FILLED"]["fill_geometry"]["actual_fill_money_risk"]), 102)
        # The limit now follows the approved (TRUE planned) risk, not a fixed equity percentage: approved at 0.5%,
        # the maximum fill money risk is 0.5% x 8/7.
        half = dict(f3, riesgo_por_operacion_pct=0.5)
        p = plan("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, policy=POLICY_V2_F3)
        self.assertEqual(evaluar_trade_plan(p, 10000.0, INS["XAUUSD"], half).capital_at_risk, 50.0)
        status, events = self.fill(2651.0, half, POLICY_V2_F3, risk_plan=p)
        self.assertEqual(status, "FILLED")
        geometry = events["ORDER_FILLED"]["fill_geometry"]
        self.assertEqual((Decimal(geometry["actual_risk"]), Decimal(geometry["planned_money_risk"])), (51, 50))
        self.assertTrue(geometry["maximum_fill_money_risk"].startswith("57.142857"))

    def test_v1_fill_gate_keeps_hard_coded_one_percent_cap(self):
        status, events = self.fill(2650.0, V1)  # frozen V1 path unchanged
        self.assertEqual(status, "FILLED")

if __name__ == "__main__":
    unittest.main()
