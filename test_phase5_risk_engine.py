"""V2 Phase 5 / P5.1: Risk Engine V2 (DEC-5.1 .. DEC-5.8) — policy, sizing, portfolio risk, fill money rule,
reservation and idempotency. Multi-process concurrency lives in test_phase5_risk_concurrency.py."""
import ast
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

from core.contracts import FloorRunReport, SetupAssessment, TradePlan
from core.risk_policy import RISK_POLICY_V2, RISK_POLICY_V2_P5, fill_money_risk_factor
from core.rr_contract import FILL_LIMITS, LIMITS, POLICY_V2_D, POLICY_V2_F3
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.risk_engine_v2 import evaluate, pending_reservation, position_risk
from execution.risk_reservation import EVENT, reserve_and_submit
from runtime.paper_contracts import paper_instruments
from storage.database import SCHEMA_VERSION, Store

ROOT = Path(__file__).resolve().parent
AT = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)
INS = paper_instruments()
ACCOUNT = "paper-main"
P = RISK_POLICY_V2_P5
F = Fraction


def f3_plan(symbol, side, entry, stop, run_id="run-1", rr=3.0):
    distance = abs(Decimal(repr(entry)) - Decimal(repr(stop)))
    target = Decimal(repr(entry)) + (3 * distance if side == "LONG" else -3 * distance)
    return TradePlan("1.0", symbol, side, "5m", entry, stop, float(target), rr, invalidation=str(stop), run_id=run_id,
                     as_of=AT, policy_version=POLICY_V2_F3)


def report(plan):
    setup = SetupAssessment("1.0", plan.run_id, AT, plan.symbol, "VALID_SETUP", plan.side, ("1h", "15m", "5m"))
    return FloorRunReport("1.0", plan.run_id, AT, plan.symbol, {}, {}, None, setup, plan, None, "PLAN_READY")


def account(equity=10000.0, realized=0.0, unrealized=0.0, starting=10000.0):
    return PaperAccount("1.0", ACCOUNT, starting, starting, equity, realized, unrealized)


def position(symbol, side, fill, stop, target, qty, planned=None):
    return PaperPosition("1.0", "pos-" + symbol, "ord-" + symbol, "run-" + symbol, symbol, side, qty, planned or fill,
                         fill, stop, target, AT - timedelta(hours=1), last_price=fill, contract_multiplier=1.0)


def pending(symbol, side, entry, stop, target, qty, order_id=None, version=RISK_POLICY_V2):
    """A PENDING order; ``version`` None = no durable policy identity (V1 / policy D / legacy)."""
    return PaperOrder("1.0", order_id or "ord-" + symbol, "run-" + symbol, symbol, side, qty, entry, stop, target, 1.0,
                      10000.0, 0.0, AT - timedelta(minutes=5), risk_policy_version=version)


def bar(symbol, price, minutes=5):
    return {"symbol": symbol, "timestamp": AT + timedelta(minutes=minutes), "open": price, "high": price,
            "low": price, "close": price, "is_closed": True}


XAU_LONG = ("XAUUSD", "LONG", 2650.0, 2600.0)  # risk-bound: qty 2.000, exactly 1%
XAU_LONG_70 = ("XAUUSD", "LONG", 2650.0, 2580.0)  # D = 70: worst permitted fill 2660 (risk 80 = 8/7 x 70)
EUR_SHORT = ("EURUSD", "SHORT", 1.085, 1.096)  # risk-bound: 9090.909 -> 9090 (P5.0 float-epsilon case)
EUR_LONG = ("EURUSD", "LONG", 1.085, 1.078)  # notional-bound: 9216.59 -> 9216 (0.645%)


class PolicyTests(unittest.TestCase):
    def test_single_versioned_policy(self):
        self.assertEqual(P.version, RISK_POLICY_V2)
        self.assertEqual((P.per_trade_risk_fraction, P.notional_fraction_cap, P.aggregate_portfolio_risk_fraction,
                          P.account_drawdown_fraction, P.max_positions_or_pending_per_symbol),
                         (F(1, 100), F(1), F(23, 1000), F(5, 100), 1))
        self.assertEqual((P.planned_rr, P.minimum_actual_fill_rr), (F(LIMITS[POLICY_V2_F3][0]), F(FILL_LIMITS[POLICY_V2_F3][0])))
        self.assertTrue(P.correlation_adjustment.startswith("OFF"))
        self.assertTrue(P.daily_loss_limit.startswith("NONE"))
        self.assertIn("UNAVAILABLE", P.spread_threshold)
        self.assertIn("UNAVAILABLE", P.volatility_threshold)

    def test_fill_factor_is_derived_not_a_magic_constant(self):
        self.assertEqual(P.fill_money_risk_factor, F(8, 7))
        self.assertEqual(fill_money_risk_factor(3, F(5, 2)), F(8, 7))
        self.assertEqual(fill_money_risk_factor(3, 3), 1)  # no tolerance -> no extra money risk
        for path in ("core/risk_policy.py", "execution/risk_engine_v2.py", "execution/paper_broker.py",
                     "execution/risk_reservation.py"):
            source = (ROOT / path).read_text(encoding="utf-8")
            self.assertNotIn("1.142857", source)
            self.assertNotIn("1.15", source)
        # Two maximal reservations fit the 2.30% aggregate (DEC-5.2 rationale): 2 x 1% x 8/7 = 2.2857%.
        self.assertLess(2 * P.per_trade_risk_fraction * P.fill_money_risk_factor, P.aggregate_portfolio_risk_fraction)
        self.assertGreater(3 * P.per_trade_risk_fraction * P.fill_money_risk_factor, P.aggregate_portfolio_risk_fraction)


class SizingTests(unittest.TestCase):
    def test_worked_examples_true_planned_risk_after_paper_floor(self):
        cases = {  # -> (quantity, TRUE planned money risk)
            EUR_LONG: ("9216", "64.512"),
            EUR_SHORT: ("9090", "99.990"),  # P5.0 float epsilon (100.00000000000001) eliminated
            XAU_LONG: ("2.000", "100.000"),
            ("XAUUSD", "SHORT", 2650.0, 2660.0): ("3.773", "37.730"),
        }
        for args, (qty, money) in cases.items():
            with self.subTest(args=args):
                result = evaluate(f3_plan(*args), account(), {}, INS[args[0]])
                self.assertEqual(result.decision.status, "APPROVED", result.record)
                self.assertEqual(Decimal(result.record["quantity"]), Decimal(qty))
                self.assertEqual(Decimal(result.record["planned_monetary_risk"]), Decimal(money))
                self.assertLessEqual(Decimal(result.record["planned_monetary_risk"]), Decimal(100))
                self.assertEqual(Decimal(result.record["reserved_fill_risk"]),
                                 Decimal(money) * 8 / 7)
                self.assertEqual(result.decision.quantity, float(qty))

    def test_floor_never_increases_risk_and_tiny_size_rejects(self):
        tiny = account(equity=1.0, starting=1.0)
        result = evaluate(f3_plan(*XAU_LONG), tiny, {}, INS["XAUUSD"])
        self.assertEqual(result.decision.reason, "PAPER_QUANTITY_BELOW_INCREMENT")  # 0.0002 < 0.001 increment

    def test_conservative_equity_basis(self):
        plan = f3_plan(*XAU_LONG)
        # Unrealized profit never raises capacity; cash is not an input.
        up = evaluate(plan, replace(account(10300.0, unrealized=300.0), cash=50000.0), {}, INS["XAUUSD"]).record
        self.assertEqual((Decimal(up["conservative_equity"]), Decimal(up["planned_monetary_risk"])), (10000, 100))
        # Unrealized loss reduces capacity.
        down = evaluate(plan, account(9800.0, unrealized=-200.0), {}, INS["XAUUSD"]).record
        self.assertEqual((Decimal(down["conservative_equity"]), Decimal(down["quantity"])), (9800, Decimal("1.96")))
        # Realized gains are capacity; current equity below realized balance binds.
        gain = evaluate(plan, account(10450.0, realized=500.0, unrealized=-50.0), {}, INS["XAUUSD"]).record
        self.assertEqual((Decimal(gain["realized_balance"]), Decimal(gain["conservative_equity"])), (10500, 10450))

    def test_invalid_state_and_geometry_fail_closed(self):
        plan = f3_plan(*XAU_LONG)
        self.assertEqual(evaluate(plan, None, {}, INS["XAUUSD"]).decision.reason, "INVALID_ACCOUNT_STATE")
        broken = account()
        broken.equity = float("nan")
        self.assertEqual(evaluate(plan, broken, {}, INS["XAUUSD"]).decision.reason, "INVALID_ACCOUNT_STATE")
        negative = account(-5.0, realized=-10005.0)
        self.assertEqual(evaluate(plan, negative, {}, INS["XAUUSD"]).decision.reason, "INVALID_ACCOUNT_STATE")
        self.assertEqual(evaluate(replace(plan, policy_version="V1"), account(), {}, INS["XAUUSD"]).decision.reason,
                         "rr_policy_mismatch")
        self.assertEqual(evaluate(replace(plan, target=2800.5), account(), {}, INS["XAUUSD"]).decision.reason,
                         "rr_declared_mismatch")
        self.assertEqual(evaluate(replace(plan, target=2800.5, risk_reward=3.01), account(), {},
                                  INS["XAUUSD"]).decision.reason, "rr_above_maximum")
        self.assertEqual(evaluate(replace(plan, stop=2700.0), account(), {}, INS["XAUUSD"]).decision.reason,
                         "invalid_level_order")
        self.assertEqual(evaluate(plan, account(), {}, INS["EURUSD"]).decision.reason,
                         "instrument_contract_data_unavailable")
        self.assertEqual(evaluate(plan, account(), {}, None).decision.reason, "instrument_contract_data_unavailable")
        self.assertNotIn("NAS100", INS)  # NAS100 has no PAPER contract: Risk V2 cannot size it even if enabled by env
        self.assertEqual(evaluate(replace(plan, symbol="NAS100"), account(), {}, INS.get("NAS100")).decision.reason,
                         "instrument_contract_data_unavailable")
        bad = position("EURUSD", "LONG", 1.08, 1.07, 1.11, 100.0)
        bad.quantity = float("inf")
        held = account()
        held.open_positions["EURUSD"] = bad
        self.assertEqual(evaluate(plan, held, {}, INS["XAUUSD"]).decision.reason, "INVALID_ACCOUNT_STATE")


class PortfolioTests(unittest.TestCase):
    def test_drawdown_gate_boundary(self):
        plan = f3_plan(*XAU_LONG)
        self.assertEqual(evaluate(plan, account(9500.0, realized=-500.0), {}, INS["XAUUSD"]).decision.reason,
                         "ACCOUNT_DRAWDOWN_LIMIT")
        self.assertEqual(evaluate(plan, account(9500.0, unrealized=-500.0), {}, INS["XAUUSD"]).decision.reason,
                         "ACCOUNT_DRAWDOWN_LIMIT")  # unrealized loss counts
        self.assertEqual(evaluate(plan, account(9500.01, realized=-499.99), {}, INS["XAUUSD"]).decision.status,
                         "APPROVED")
        # Recovery above the threshold re-enables new entries (no high-water mark, no latch).
        self.assertEqual(evaluate(plan, account(9600.0, realized=-400.0), {}, INS["XAUUSD"]).decision.status,
                         "APPROVED")

    def test_open_and_pending_risk_amounts(self):
        long = position("XAUUSD", "LONG", 2660.0, 2580.0, 2860.0, 1.428, planned=2650.0)
        self.assertEqual(position_risk(long), F(80) * F("1.428"))  # actual fill price to SL
        favorable = position("XAUUSD", "LONG", 2640.0, 2580.0, 2860.0, 1.428, planned=2650.0)
        self.assertEqual(position_risk(favorable), F(60) * F("1.428"))
        order = pending("EURUSD", "SHORT", 1.085, 1.096, 1.052, 9090.0)
        self.assertEqual(pending_reservation(order)["reserved"], F("0.011") * 9090 * F(8, 7))

    def test_two_max_trades_fit_third_symbol_cannot_add(self):
        held = account()
        orders = {"o": pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 2.0)}
        result = evaluate(f3_plan(*EUR_SHORT), held, orders, INS["EURUSD"])
        self.assertEqual(result.decision.status, "APPROVED", result.record)
        total = F(100) * F(8, 7) + F("99.990") * F(8, 7)
        self.assertEqual(Decimal(result.record["post_trade_portfolio_risk"]),
                         Decimal(total.numerator) / Decimal(total.denominator))
        self.assertEqual(Decimal(result.record["portfolio_risk_limit"]), 230)

    def test_aggregate_limit_binds_with_open_risk_and_realized_loss(self):
        held = account(9700.0, realized=-300.0)
        held.open_positions["XAUUSD"] = position("XAUUSD", "LONG", 2660.0, 2580.0, 2860.0, 1.428, planned=2650.0)
        result = evaluate(f3_plan(*EUR_SHORT), held, {}, INS["EURUSD"])
        self.assertEqual(result.decision.reason, "PORTFOLIO_RISK_LIMIT", result.record)
        record = result.record
        self.assertEqual(Decimal(record["existing_open_risk"]), Decimal("114.240"))
        self.assertEqual(Decimal(record["quantity"]), 8818)
        self.assertEqual(Decimal(record["portfolio_risk_limit"]), Decimal("223.1"))
        self.assertGreater(Decimal(record["post_trade_portfolio_risk"]), Decimal("223.1"))
        # Same state, smaller open risk -> fits.
        held.open_positions["XAUUSD"] = position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0)
        self.assertEqual(evaluate(f3_plan(*EUR_SHORT), held, {}, INS["EURUSD"]).decision.status, "APPROVED")

    def test_symbol_rule_open_and_pending(self):
        plan = f3_plan(*XAU_LONG)
        held = account()
        held.open_positions["XAUUSD"] = position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 0.001)
        self.assertEqual(evaluate(plan, held, {}, INS["XAUUSD"]).decision.reason, "SYMBOL_EXPOSURE_LIMIT")
        orders = {"o": pending("XAUUSD", "SHORT", 2650.0, 2660.0, 2620.0, 0.001)}
        self.assertEqual(evaluate(plan, account(), orders, INS["XAUUSD"]).decision.reason, "SYMBOL_EXPOSURE_LIMIT")
        finished = {"o": replace(orders["o"], status="FILLED"), "r": replace(orders["o"], order_id="r", status="REJECTED")}
        self.assertEqual(evaluate(plan, account(), finished, INS["XAUUSD"]).decision.status, "APPROVED")

    def test_rejection_precedence(self):
        # Every gate fails at once; the documented order decides.
        held = account(9400.0, realized=-600.0)
        held.open_positions["XAUUSD"] = position("XAUUSD", "LONG", 2650.0, 2000.0, 4600.0, 3.0)
        plan = f3_plan(*XAU_LONG)
        self.assertEqual(evaluate(replace(plan, policy_version="V1"), held, {}, INS["XAUUSD"]).decision.reason,
                         "rr_policy_mismatch")  # geometry before equity/drawdown
        self.assertEqual(evaluate(plan, held, {}, INS["XAUUSD"]).decision.reason, "ACCOUNT_DRAWDOWN_LIMIT")
        held.equity, held.realized_pnl = 9600.0, -400.0
        self.assertEqual(evaluate(plan, held, {}, INS["XAUUSD"]).decision.reason, "SYMBOL_EXPOSURE_LIMIT")
        self.assertEqual(evaluate(f3_plan(*EUR_SHORT), held, {}, INS["EURUSD"]).decision.reason, "PORTFOLIO_RISK_LIMIT")

    def test_traceability_record(self):
        result = evaluate(f3_plan(*XAU_LONG), account(), {}, INS["XAUUSD"], setup_id="setup-1", at=AT)
        required = {"policy_version", "run_id", "setup_id", "symbol", "direction", "planned_entry", "sl", "tp",
                    "planned_rr", "minimum_fill_rr", "current_equity", "realized_balance", "conservative_equity",
                    "risk_fraction", "risk_budget", "quantity", "planned_monetary_risk", "fill_risk_factor",
                    "reserved_fill_risk", "existing_open_risk", "existing_pending_risk", "existing_symbol_risk",
                    "post_trade_portfolio_risk", "portfolio_risk_limit", "drawdown_fraction", "drawdown_limit",
                    "risk_status", "risk_reason", "timestamp", "correlation_state"}
        self.assertFalse(required - set(result.record))
        self.assertEqual((result.record["policy_version"], result.record["setup_id"], result.record["fill_risk_factor"]),
                         (RISK_POLICY_V2, "setup-1", "8/7"))

    def test_pure_no_side_effects_no_market_data(self):
        held = account(9800.0, unrealized=-200.0)
        held.open_positions["EURUSD"] = position("EURUSD", "SHORT", 1.085, 1.096, 1.052, 100.0)
        before = repr(held)
        evaluate(f3_plan(*XAU_LONG), held, {}, INS["XAUUSD"])
        self.assertEqual(repr(held), before)
        tree = ast.parse((ROOT / "execution/risk_engine_v2.py").read_text(encoding="utf-8"))
        imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertEqual(imported, {"dataclasses", "decimal", "fractions", "core.contracts", "core.risk_policy",
                                    "core.rr_contract"})


class FillMoneyRuleTests(unittest.TestCase):
    """DEC-5.7: fixed 3R fills require actual R:R >= 2.50 AND actual money risk <= planned money risk x 8/7."""

    def fill(self, args, open_price, policy=POLICY_V2_F3, equity=10000.0, plan=None):
        plan = plan or f3_plan(*args)
        held = account(equity)
        decision = evaluate(plan, held, {}, INS[args[0]]).decision
        broker = PaperBroker(held, INS[args[0]], rr_policy=policy)
        order = broker.submit_plan(replace(report(plan), risk_decision=decision), None, AT)
        broker.process_next_bar(order, bar(args[0], open_price))
        events = {e.event_type: e.details for e in broker.journal}
        return order, events

    def test_exact_boundary_fills_and_equivalence_with_rr_floor(self):
        order, events = self.fill(XAU_LONG_70, 2660.0)  # risk 80 = 8/7 x 70, R:R 200/80 = 2.5
        geo = events["ORDER_FILLED"]["fill_geometry"]
        self.assertEqual(order.status, "FILLED")
        self.assertEqual((geo["actual_fill_rr"], Decimal(geo["fill_money_risk_ratio"])), ("2.5", Decimal(8) / 7))
        self.assertEqual(Decimal(geo["actual_fill_money_risk"]), Decimal(geo["maximum_fill_money_risk"]))
        order, events = self.fill(XAU_LONG_70, 2660.01)  # one tick worse: R:R floor fires first (equivalent gates)
        self.assertEqual((order.status, events["ORDER_REJECTED"]["reason"]), ("REJECTED", "fill_rr_below_minimum"))

    def test_hard_coded_one_percent_cap_removed_for_fixed_3r(self):
        order, events = self.fill(XAU_LONG, 2651.0)  # R:R 2.92, money 1.02% of equity, 1.02 x planned
        self.assertEqual(order.status, "FILLED")
        self.assertEqual(Decimal(events["ORDER_FILLED"]["fill_geometry"]["actual_fill_money_risk"]), 102)

    def test_limit_follows_true_planned_risk_not_one_percent(self):
        order, events = self.fill(EUR_LONG, 1.086)  # notional-bound plan: 0.64512% planned
        geo = events["ORDER_FILLED"]["fill_geometry"]
        self.assertEqual(Decimal(geo["planned_money_risk"]), Decimal("64.512"))
        self.assertEqual(Decimal(geo["maximum_fill_money_risk"]), Decimal("64.512") * 8 / 7)
        self.assertLess(Decimal(geo["maximum_fill_money_risk"]), 100)

    def test_money_rule_is_independent_defense_in_depth(self):
        # A non-3R order (cannot come from Risk V2) with acceptable R:R but money risk 1.2 x planned is refused.
        held = account()
        broker = PaperBroker(held, INS["XAUUSD"], rr_policy=POLICY_V2_F3)
        order = PaperOrder("1.0", "o", "r", "XAUUSD", "LONG", 1.0, 100.0, 90.0, 140.0, 1.0, 10000.0, 0.0, AT)
        broker.orders["o"] = order
        broker.process_next_bar(order, bar("XAUUSD", 102.0))
        details = broker.journal[-1].details
        self.assertEqual((order.status, details["reason"]), ("REJECTED", "fill_money_risk_above_limit"))
        self.assertEqual(Decimal(details["fill_geometry"]["fill_money_risk_ratio"]), Decimal("1.2"))

    def test_gap_through_stop_and_invalid_state(self):
        order, events = self.fill(XAU_LONG, 2599.0)
        self.assertEqual(events["ORDER_REJECTED"]["reason"], "fill_invalid_geometry")
        order, events = self.fill(XAU_LONG, 2651.0, equity=10000.0)
        held = account()
        broker = PaperBroker(held, INS["XAUUSD"], rr_policy=POLICY_V2_F3)
        stale = PaperOrder("1.0", "o", "r", "XAUUSD", "LONG", 2.0, 2650.0, 2600.0, 2800.0, 1.0, None, 0.0, AT)
        broker.process_next_bar(stale, bar("XAUUSD", 2650.0))
        self.assertEqual(broker.journal[-1].details["reason"], "post_fill_risk_or_geometry")

    def test_other_policies_unchanged(self):
        # Policy D research path keeps the existing 1% equity caps.
        plan = TradePlan("1.0", "XAUUSD", "LONG", "5m", 2650.0, 2600.0, 2850.0, 4.0, invalidation="2600.0",
                         run_id="d", as_of=AT, policy_version=POLICY_V2_D)
        held = account()
        broker = PaperBroker(held, INS["XAUUSD"], rr_policy=POLICY_V2_D)
        order = PaperOrder("1.0", "o", "d", "XAUUSD", "LONG", 2.0, 2650.0, 2600.0, 2850.0, 1.0, 10000.0, 0.0, AT)
        broker.process_next_bar(order, bar("XAUUSD", 2651.0))
        self.assertEqual(broker.journal[-1].details["reason"], "post_fill_risk_or_geometry")
        self.assertEqual(plan.policy_version, POLICY_V2_D)
        # Frozen V1 (rr_policy None) is byte-identical: same expression, same reason, no fill_geometry.
        v1 = PaperBroker(account(), INS["XAUUSD"])
        order = PaperOrder("1.0", "o", "v", "XAUUSD", "LONG", 2.0, 2650.0, 2600.0, 2800.0, 1.0, 10000.0, 0.0, AT)
        v1.process_next_bar(order, bar("XAUUSD", 2650.0))
        self.assertEqual(order.status, "FILLED")
        self.assertIsNone(v1.journal[0].details or None)


class ReservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p5-risk-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "trading_floor.db"
        self.store = Store(self.path)
        self.addCleanup(lambda: self.store.close())
        self.store.save_paper(PaperBroker(account()))

    def submit(self, args, run_id, store=None, **kwargs):
        return reserve_and_submit(store or self.store, report(f3_plan(*args, run_id=run_id)), INS[args[0]],
                                  account_id=ACCOUNT, as_of=AT, setup_id="setup-" + run_id, **kwargs)

    def test_reserve_persists_order_and_decision_once(self):
        result = self.submit(XAU_LONG, "run-a")
        self.assertEqual((result.status, result.attempts), ("RESERVED", 1))
        _, orders, _ = self.store.load_paper(ACCOUNT)
        self.assertEqual([(o.run_id, o.status, o.quantity) for o in orders.values()], [("run-a", "PENDING", 2.0)])
        rows = self.store.journal(event=EVENT)
        self.assertEqual(len(rows), 1)
        self.assertEqual(SCHEMA_VERSION, 3)

    def test_retry_is_idempotent(self):
        first = self.submit(XAU_LONG, "run-a")
        again = self.submit(XAU_LONG, "run-a")
        self.assertEqual((again.status, again.order.order_id), ("DUPLICATE_RUN", first.order.order_id))
        _, orders, _ = self.store.load_paper(ACCOUNT)
        self.assertEqual(len(orders), 1)
        self.assertEqual(len(self.store.journal(event=EVENT)), 1)

    def test_second_symbol_fits_and_same_symbol_rejected(self):
        self.assertEqual(self.submit(XAU_LONG, "run-a").status, "RESERVED")
        self.assertEqual(self.submit(EUR_SHORT, "run-b").status, "RESERVED")
        rejected = self.submit(XAU_LONG, "run-c")
        self.assertEqual((rejected.status, rejected.reason), ("REJECTED", "SYMBOL_EXPOSURE_LIMIT"))
        self.assertEqual(len(self.store.load_paper(ACCOUNT)[1]), 2)
        self.assertEqual(len(self.store.journal(event=EVENT)), 3)  # rejections are traced too

    def test_pending_to_fill_no_gap_no_double_count(self):
        self.submit(XAU_LONG_70, "run-a")
        held, orders, fills = self.store.load_paper(ACCOUNT)
        before = evaluate(f3_plan(*EUR_SHORT, run_id="probe"), held, orders, INS["EURUSD"]).record
        broker = PaperBroker(held, INS["XAUUSD"], rr_policy=POLICY_V2_F3)
        broker.orders, broker.fills = orders, fills
        expected = self.store.paper_state(held, orders, fills)
        broker.process_next_bar(next(iter(orders.values())), bar("XAUUSD", 2660.0))  # worst permitted fill
        self.assertNotEqual(self.store.save_paper(broker, expected_state=expected), False)
        held, orders, fills = self.store.load_paper(ACCOUNT)
        after = evaluate(f3_plan(*EUR_SHORT, run_id="probe"), held, orders, INS["EURUSD"]).record
        self.assertEqual((Decimal(before["existing_pending_risk"]), Decimal(before["existing_open_risk"])),
                         (Decimal("114.240"), 0))
        self.assertEqual((Decimal(after["existing_pending_risk"]), Decimal(after["existing_open_risk"])),
                         (0, Decimal("114.240")))  # reservation at the worst fill == open risk after it
        self.assertEqual(before["post_trade_portfolio_risk"], after["post_trade_portfolio_risk"])

    def test_not_fixed_3r_is_not_submitted(self):
        plan = replace(f3_plan(*XAU_LONG), policy_version="V1")
        result = reserve_and_submit(self.store, report(plan), INS["XAUUSD"], account_id=ACCOUNT, as_of=AT)
        self.assertEqual(result.status, "NOT_SUBMITTED")
        self.assertEqual(self.store.load_paper(ACCOUNT)[1], {})

    def test_reservation_module_scope(self):
        source = (ROOT / "execution/risk_reservation.py").read_text(encoding="utf-8")
        for forbidden in ("evaluar_trade_plan", "from runtime", "import runtime", "TradeManager", "process_next_bar", "CREATE TABLE",
                          "ALTER TABLE", "NAS100"):
            self.assertNotIn(forbidden, source)


class RiskAuditHelperTests(unittest.TestCase):
    def test_exit_precedence_matches_fill_audit_walk(self):
        import numpy as np
        from replay.fill_audit import walk
        from replay.risk_audit import exit_of
        rng = np.random.default_rng(7)
        for _ in range(300):
            close = 100 + np.cumsum(rng.normal(0, 1, 40))
            o, h, l = close, close + rng.uniform(0, 2, 40), close - rng.uniform(0, 2, 40)
            h, l = np.maximum(h, o), np.minimum(l, o)
            for side, stop, target in (("LONG", o[0] - 3, o[0] + 9), ("SHORT", o[0] + 3, o[0] - 9)):
                kind, price, _ = exit_of(o, h, l, 0, side, stop, target)
                self.assertEqual((kind, price), walk(o, h, l, 0, side, stop, target))


class PolicyIdentityTests(unittest.TestCase):
    """P5.1C: a pending reservation factor is applied only when the order's policy is proven by durable state."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p51c-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "trading_floor.db"
        store = Store(self.path)
        store.save_paper(PaperBroker(account()))
        store.close()

    def reopen(self):
        """A brand-new connection: nothing survives from the previous one except durable rows (restart)."""
        store = Store(self.path)
        self.addCleanup(store.close)
        return store

    def raw_orders(self):
        return [row[0] for row in self.reopen().db.execute("SELECT payload FROM paper_orders")]

    def persist(self, *orders):
        store = self.reopen()
        held, existing, fills = store.load_paper(ACCOUNT)
        broker = PaperBroker(held)
        broker.orders, broker.fills = existing, fills
        broker.orders.update({o.order_id: o for o in orders})
        store.save_paper(broker)

    def evaluate_after_restart(self, args=EUR_SHORT, policy=P):
        held, orders, _ = self.reopen().load_paper(ACCOUNT)
        return evaluate(f3_plan(*args, run_id="probe"), held, orders, INS[args[0]], policy), orders

    def test_reviewer_high_regression_production_policy(self):
        # Existing PENDING order with NO durable identity, planned $100 (XAU 2650/2600 x 2). Geometry is exactly 3R,
        # but policy D (planned 2R..5R, fill floor 2R) can also produce it: worst fill 4/3 x planned under that policy.
        self.persist(pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 2.0, version=None))
        result, _ = self.evaluate_after_restart()
        proposed = F("99.990")
        naive = (F(100) + proposed) * F(8, 7)  # what P5.1 computed: 228.56 <= 230 -> APPROVED (the HIGH)
        supported = F(100) * fill_money_risk_factor(3, 2) + proposed * F(8, 7)  # 247.61 > 230
        self.assertLessEqual(naive, F(230))
        self.assertGreater(supported, F(230))
        self.assertEqual(round(float(naive), 2), 228.56)
        self.assertEqual(round(float(supported), 2), 247.61)
        self.assertEqual(Decimal(result.record["portfolio_risk_limit"]), 230)  # real 2.30% production cap
        self.assertNotEqual(result.decision.status, "APPROVED")
        self.assertEqual(result.decision.reason, "UNKNOWN_PENDING_RISK_POLICY")
        trace = result.record["pending_exposures"][0]
        self.assertEqual((trace["policy_identity_known"], trace["risk_policy_version"], trace["reservation_factor"],
                          trace["reserved_monetary_risk"], trace["fail_closed_reason"]),
                         (False, "UNKNOWN", None, None, "UNKNOWN_PENDING_RISK_POLICY"))
        self.assertEqual(Decimal(trace["planned_monetary_risk"]), 100)
        self.assertEqual(result.record["unknown_pending_orders"], ["ord-XAUUSD"])

    def test_geometry_alone_and_unregistered_stamps_are_not_identity(self):
        for version in (None, "V2_P9_UNREGISTERED", ""):
            with self.subTest(version=version):
                order = pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 2.0, version=version)
                item = pending_reservation(order)
                self.assertIsNone(item["reserved"])
                self.assertFalse(item["trace"]["policy_identity_known"])
                result = evaluate(f3_plan(*EUR_SHORT), account(), {"o": order}, INS["EURUSD"])
                self.assertEqual(result.decision.reason, "UNKNOWN_PENDING_RISK_POLICY")
        # A non-PENDING unknown order is not exposure.
        done = replace(pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 2.0, version=None), status="CANCELLED")
        self.assertEqual(evaluate(f3_plan(*EUR_SHORT), account(), {"o": done}, INS["EURUSD"]).decision.status,
                         "APPROVED")

    def test_unknown_survives_restart_and_order_is_untouched(self):
        self.persist(pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 2.0, version=None))
        raw = self.raw_orders()
        self.assertNotIn("risk_policy_version", raw[0])  # legacy payload keeps its pre-P5.1C shape
        for _ in range(3):  # retries / reloads never convert UNKNOWN into a known policy
            result, orders = self.evaluate_after_restart()
            self.assertEqual(result.decision.reason, "UNKNOWN_PENDING_RISK_POLICY")
            self.assertIsNone(orders["ord-XAUUSD"].risk_policy_version)
        self.assertEqual(self.raw_orders(), raw)
        out = reserve_and_submit(self.reopen(), report(f3_plan(*EUR_SHORT, run_id="run-new")), INS["EURUSD"],
                                 account_id=ACCOUNT, as_of=AT)
        self.assertEqual((out.status, out.reason), ("REJECTED", "UNKNOWN_PENDING_RISK_POLICY"))
        self.assertEqual(self.raw_orders(), raw)  # not deleted, cancelled or modified
        # Normal lifecycle continues under the processing broker's unchanged rules (frozen V1 fills at 2650).
        held, orders, _ = self.reopen().load_paper(ACCOUNT)
        PaperBroker(held, INS["XAUUSD"]).process_next_bar(orders["ord-XAUUSD"], bar("XAUUSD", 2650.0))
        self.assertEqual(orders["ord-XAUUSD"].status, "FILLED")

    def test_reserved_order_identity_survives_restart_exact_8_7(self):
        first = reserve_and_submit(self.reopen(), report(f3_plan(*XAU_LONG, run_id="run-a")), INS["XAUUSD"],
                                   account_id=ACCOUNT, as_of=AT)
        self.assertEqual(first.status, "RESERVED")
        self.assertIn('"risk_policy_version":"' + RISK_POLICY_V2 + '"', self.raw_orders()[0])
        result, orders = self.evaluate_after_restart()
        self.assertEqual(orders[first.order.order_id].risk_policy_version, RISK_POLICY_V2)
        self.assertEqual(result.decision.status, "APPROVED", result.record)
        trace = result.record["pending_exposures"][0]
        self.assertEqual((trace["policy_identity_known"], trace["risk_policy_version"], trace["reservation_factor"]),
                         (True, RISK_POLICY_V2, "8/7"))
        expected = F(100) * F(8, 7)
        self.assertEqual(Decimal(trace["reserved_monetary_risk"]),
                         Decimal(expected.numerator) / Decimal(expected.denominator))
        self.assertEqual(Decimal(result.record["existing_pending_risk"]), Decimal(trace["reserved_monetary_risk"]))

    def test_happy_path_reservation_is_true_planned_risk_times_8_7(self):
        for qty, planned in ((2.0, 100), (1.5, 75), (1.26, 63), (0.5, 25)):  # 1.00%, 0.75%, 0.63%, 0.25%
            with self.subTest(planned=planned):
                self.setUp()
                self.persist(pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, qty))
                result, _ = self.evaluate_after_restart()
                trace = result.record["pending_exposures"][0]
                self.assertEqual(Decimal(trace["planned_monetary_risk"]), planned)
                expected = F(planned) * F(8, 7)
                self.assertEqual(Decimal(trace["reserved_monetary_risk"]),
                                 Decimal(expected.numerator) / Decimal(expected.denominator))
                self.assertEqual(result.decision.status, "APPROVED")

    def test_known_plus_unknown_pending_fails_closed(self):
        self.persist(pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 0.5),
                     pending("EURUSD", "SHORT", 1.085, 1.096, 1.052, 100.0, version=None))
        result, _ = self.evaluate_after_restart(("XAUUSD", "SHORT", 2650.0, 2660.0))
        self.assertEqual(result.decision.reason, "UNKNOWN_PENDING_RISK_POLICY")  # precedes the symbol rule
        known = [t for t in result.record["pending_exposures"] if t["policy_identity_known"]]
        self.assertEqual([t["risk_policy_version"] for t in known], [RISK_POLICY_V2])

    def test_evaluating_policy_must_be_registered(self):
        loose = replace(P, minimum_actual_fill_rr=F(2))  # same version string, different fill semantics
        self.assertEqual(evaluate(f3_plan(*XAU_LONG), account(), {}, INS["XAUUSD"], loose).decision.reason,
                         "RISK_POLICY_NOT_REGISTERED")
        out = reserve_and_submit(self.reopen(), report(f3_plan(*XAU_LONG)), INS["XAUUSD"], account_id=ACCOUNT,
                                 as_of=AT, policy=loose)
        self.assertEqual((out.status, out.reason), ("NOT_SUBMITTED", "RISK_POLICY_NOT_REGISTERED"))
        tighter_cap = replace(P, aggregate_portfolio_risk_fraction=F(15, 1000))  # limits only: still registered
        self.assertEqual(evaluate(f3_plan(*XAU_LONG), account(), {}, INS["XAUUSD"], tighter_cap).decision.status,
                         "APPROVED")


class StampedFillBindingTests(unittest.TestCase):
    """The stamped identity also binds the fill gate, so the reservation's worst fill holds for any broker."""

    def order(self, version=RISK_POLICY_V2):
        return PaperOrder("1.0", "o", "r", "XAUUSD", "LONG", 1.428, 2650.0, 2580.0, 2860.0, 1.0, 10000.0, 0.0, AT,
                          risk_policy_version=version)

    def test_any_broker_fills_stamped_order_only_within_8_7(self):
        for rr_policy in (POLICY_V2_D, None, POLICY_V2_F3):
            with self.subTest(broker=rr_policy):
                broker = PaperBroker(account(), INS["XAUUSD"], rr_policy=rr_policy)
                order = self.order()
                broker.process_next_bar(order, bar("XAUUSD", 2667.0))  # R:R 193/87 = 2.22 (policy D would accept)
                self.assertEqual((order.status, broker.journal[-1].details["reason"]),
                                 ("REJECTED", "fill_rr_below_minimum"))
                broker = PaperBroker(account(), INS["XAUUSD"], rr_policy=rr_policy)
                order = self.order()
                broker.process_next_bar(order, bar("XAUUSD", 2660.0))  # exactly 8/7: fills under every broker
                self.assertEqual(order.status, "FILLED")
                geometry = broker.journal[-2].details["fill_geometry"]
                self.assertEqual((geometry["policy"], geometry["risk_policy_version"]), (POLICY_V2_F3, RISK_POLICY_V2))

    def test_unregistered_stamp_is_rejected_at_fill(self):
        broker = PaperBroker(account(), INS["XAUUSD"], rr_policy=POLICY_V2_F3)
        order = self.order("V2_P9_UNREGISTERED")
        broker.process_next_bar(order, bar("XAUUSD", 2650.0))
        self.assertEqual((order.status, broker.journal[-1].details["reason"]),
                         ("REJECTED", "unknown_order_risk_policy"))

    def test_unstamped_orders_keep_broker_behavior_and_payload(self):
        from storage.codec import paper_decode, paper_encode
        legacy = self.order(None)
        encoded = paper_encode(legacy)
        self.assertNotIn("risk_policy_version", encoded)
        self.assertEqual(paper_decode("PaperOrder", encoded), legacy)
        self.assertEqual(paper_decode("PaperOrder", paper_encode(self.order())).risk_policy_version, RISK_POLICY_V2)
        v1 = PaperBroker(account(), INS["XAUUSD"])
        v1.process_next_bar(legacy, bar("XAUUSD", 2660.0))  # frozen V1: R:R 200/80 < 3 -> rejected, V1 reason
        self.assertEqual(v1.journal[-1].details, {"reason": "post_fill_risk_or_geometry"})
        d = PaperBroker(account(), INS["XAUUSD"], rr_policy=POLICY_V2_D)
        unstamped = replace(self.order(None), quantity=1.0)  # money 87 <= 1% cap
        d.process_next_bar(unstamped, bar("XAUUSD", 2667.0))  # policy D broker unchanged: R:R 2.22 >= 2 -> fills
        self.assertEqual(unstamped.status, "FILLED")


if __name__ == "__main__":
    unittest.main()
