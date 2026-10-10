"""V2 Phase 6 / P6.0: characterization of CURRENT multi-setup / existing-exposure behavior (main b3e93df).

Pins what exists today — including limits — so every P6.1 difference is an explicit, Owner-approved change.
Non-production: imports production code read-only; changes nothing.
"""
import dataclasses
import inspect
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from agents.setup_validator import setup_identity
from agents.structure_agent import analizar_estructura
from ai.provider import DeterministicAIProvider
from core.contracts import SetupAssessment
from core.risk_policy import RISK_POLICY_V2_P5
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.risk_engine_v2 import evaluate
from execution.trade_manager import TradeManager
from runtime.demo_runner import DemoRunner
from storage.database import Store
import test_demo_runner as fixture
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from test_phase5_risk_engine import AT, EUR_SHORT, INS, XAU_LONG, account, bar, f3_plan, pending, position

ACCOUNT = "paper-main"


def valid(side="LONG", invalidation=2600.0, anchor=None, run_id="run-1", symbol="XAUUSD"):
    bos = anchor if anchor is not None else {"break_timestamp": pd.Timestamp("2026-01-15T12:00Z"),
                                              "broken_level": 2640.0, "direction": "bullish"}
    structure = {"15m": {"bos": bos, "retracement": None}}
    return SetupAssessment("1.0", run_id, AT, symbol, "VALID_SETUP", side, ("1h", "15m", "5m"),
                           evidence=(structure, {}), invalidation=invalidation)


class RuntimeExistingExposureTests(unittest.TestCase):
    """The full analysis runs; EXISTING_POSITION / PENDING_ORDER is decided only at the submit step."""
    setUp = fixture.TestDemoRunner.setUp
    tearDown = fixture.TestDemoRunner.tearDown
    runner = fixture.TestDemoRunner.runner

    def cycle(self, minutes, side, *extra):
        """One real runtime cycle in a fresh process-like runner (new connection); returns (result, store)."""
        runner = DemoRunner(self.config, market_provider=Data(age=5), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                            clock=lambda: T + timedelta(minutes=minutes))
        try:
            with patched_scouts(side):
                if extra:
                    with extra[0]:
                        result = runner.run_once("XAUUSD", T + timedelta(minutes=minutes))
                else:
                    result = runner.run_once("XAUUSD", T + timedelta(minutes=minutes))
            held, orders, _ = runner.store.load_paper(ACCOUNT)
            return result, held, orders, runner.store.review_report(result["run_id"])
        finally:
            runner.close()  # closed before tearDown removes the DB (Windows file locks)

    def test_opposite_setup_with_open_long_is_analyzed_then_skipped_never_closes(self):
        self.cycle(0, "LONG")  # submits
        _, held, _, _ = self.cycle(15, "LONG")  # the cycle-1 order fills (pending progresses before the decision)
        before = held.open_positions["XAUUSD"]
        third, held, orders, review = self.cycle(30, "SHORT")
        after = held.open_positions.get("XAUUSD")
        self.assertIsNotNone(after)  # the opposite setup never closes the LONG
        self.assertEqual((after.position_id, after.side, after.stop, after.target, after.quantity),
                         (before.position_id, "LONG", before.stop, before.target, before.quantity))
        self.assertEqual(len(orders), 1)  # no second order, no reverse order
        self.assertEqual(review["execution"]["execution_status"], "SKIPPED")
        self.assertEqual(review["setup"]["side"], "SHORT")  # the opposite setup WAS analyzed
        self.assertEqual(third["status"], "PLAN_READY")  # SHORT plan produced, V1 Risk APPROVED, AI reviewed ...
        self.assertEqual(review["execution"]["execution_reason"], "EXISTING_POSITION")  # ... then skipped
        self.assertEqual(review["execution"]["blocking_position_id"], after.position_id)
        self.assertNotEqual(review["execution"]["setup_id"], None)

    def test_management_runs_before_analysis_and_survives_analysis_failure(self):
        self.cycle(0, "LONG")
        _, held, _, _ = self.cycle(15, "LONG")
        opened = held.open_positions["XAUUSD"]
        result, held, _, _ = self.cycle(30, "LONG", patch("runtime.service.run_floor",
                                                          side_effect=RuntimeError("analysis failed")))
        self.assertEqual(result["status"], "ERROR")
        managed = held.open_positions.get("XAUUSD")
        # TradeManager ran on this cycle's bar BEFORE analysis and was persisted (watermark advanced).
        self.assertIsNotNone(managed)
        self.assertGreater(managed.last_processed_at, opened.last_processed_at or opened.opened_at)

    def test_pending_progression_is_coupled_to_the_current_cycle_analysis(self):
        self.cycle(0, "LONG")
        result, _, orders, _ = self.cycle(15, "LONG", patch("runtime.service.run_floor",
                                                            side_effect=RuntimeError("analysis failed")))
        self.assertEqual(result["status"], "ERROR")
        # The cycle-1 order is not progressed when THIS cycle's new-setup analysis fails (certified P1 coupling).
        self.assertEqual([o.status for o in orders.values()], ["PENDING"])


class SetupIdentityTests(unittest.TestCase):
    def test_identity_inputs_are_exactly_symbol_side_invalidation_anchor(self):
        setup_id, inputs = setup_identity(valid())
        self.assertEqual(set(inputs), {"symbol", "side", "invalidation", "anchor"})
        self.assertEqual(set(inputs["anchor"]), {"kind", "timestamp", "level", "direction"})
        self.assertEqual(setup_identity(valid(run_id="other-run"))[0], setup_id)  # retry / restart / re-run
        self.assertIsNone(setup_identity(dataclasses.replace(valid(), status="WATCH"))[0])

    def test_any_identity_input_change_gives_a_new_id(self):
        base = setup_identity(valid())[0]
        later_bos = {"break_timestamp": pd.Timestamp("2026-01-15T12:15Z"), "broken_level": 2640.0,
                     "direction": "bullish"}
        for changed in (valid(side="SHORT"), valid(invalidation=2599.99), valid(anchor=later_bos),
                        valid(symbol="EURUSD")):
            self.assertNotEqual(setup_identity(changed)[0], base)

    def test_entry_sl_tp_are_not_identity_and_order_position_setup_id_is_optional(self):
        self.assertNotIn("entry", setup_identity(valid())[1])
        # P6.0 pinned "orders/positions carry no setup_id". P6.1 / DEC-6.1 explicitly supersedes it with an additive
        # OPTIONAL durable field: default None (UNKNOWN), omitted from the payload when None (legacy bytes kept).
        for cls in (PaperOrder, PaperPosition):
            field = {f.name: f for f in dataclasses.fields(cls)}["setup_id"]
            self.assertIsNone(field.default)
        # The only durable link is run_id -> review_reports.execution.setup_id (audit record, not an execution gate).
        source = inspect.getsource(__import__("runtime.observability", fromlist=["x"]))
        self.assertIn("never used as execution gates", source)

    def test_invalidation_is_the_window_extreme_so_a_rolling_window_can_change_identity(self):
        index = pd.date_range("2026-01-15T00:00Z", periods=12, freq="15min")
        lows = [100, 98, 100, 101, 99, 101, 102, 100.5, 102, 103, 102.5, 104]
        frame = pd.DataFrame({"Open": [l + 1 for l in lows], "High": [l + 2 for l in lows], "Low": lows,
                              "Close": [l + 1.5 for l in lows], "is_closed": True}, index=index)
        full = analizar_estructura(frame, "XAUUSD", "15m", "r", index[-1] + pd.Timedelta(minutes=15))
        rolled = analizar_estructura(frame.iloc[3:], "XAUUSD", "15m", "r", index[-1] + pd.Timedelta(minutes=15))
        support = lambda message: message.evidence[0]["levels"]["support"]
        self.assertEqual(support(full), 98.0)  # minimum swing low of the whole window
        self.assertNotEqual(support(rolled), support(full))  # same later structure, older swing dropped


class DataModelTests(unittest.TestCase):
    def test_open_positions_are_keyed_by_symbol_and_a_fill_overwrites(self):
        held = account()
        first = position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0)
        held.open_positions["XAUUSD"] = first
        broker = PaperBroker(held, INS["XAUUSD"], rr_policy=None)
        order = pending("XAUUSD", "SHORT", 2650.0, 2660.0, 2620.0, 1.0, version=None)
        broker.orders[order.order_id] = order
        broker.process_next_bar(order, bar("XAUUSD", 2650.0))
        # The broker does not refuse a same-symbol fill: the dict entry is replaced (first is lost in memory).
        self.assertEqual(held.open_positions["XAUUSD"].side, "SHORT")
        self.assertEqual(len(held.open_positions), 1)

    def test_broker_submit_refuses_open_symbol_but_not_a_second_pending(self):
        held = account()
        broker = PaperBroker(held, INS["XAUUSD"], rr_policy=None)
        from test_phase5_risk_engine import report
        first = evaluate(f3_plan(*XAU_LONG, run_id="a"), held, {}, INS["XAUUSD"]).decision
        second = evaluate(f3_plan(*XAU_LONG, run_id="b"), held, {}, INS["XAUUSD"]).decision
        a = broker.submit_plan(dataclasses.replace(report(f3_plan(*XAU_LONG, run_id="a")), risk_decision=first), None, AT)
        b = broker.submit_plan(dataclasses.replace(report(f3_plan(*XAU_LONG, run_id="b")), risk_decision=second), None, AT)
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)  # the broker itself has no pending-per-symbol guard (runtime / Risk V2 have)
        held.open_positions["XAUUSD"] = position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0)
        c = broker.submit_plan(dataclasses.replace(report(f3_plan(*XAU_LONG, run_id="c")), risk_decision=first), None, AT)
        self.assertIsNone(c)

    def test_load_paper_rejects_two_open_positions_on_one_symbol(self):
        tmp = tempfile.TemporaryDirectory(prefix="v2-p60-")
        self.addCleanup(tmp.cleanup)
        store = Store(Path(tmp.name) / "trading_floor.db")
        self.addCleanup(store.close)
        held = account()
        broker = PaperBroker(held)
        store.save_paper(broker)
        for pid, side in (("p1", "LONG"), ("p2", "SHORT")):
            p = dataclasses.replace(position("XAUUSD", side, 2650.0, 2600.0 if side == "LONG" else 2700.0,
                                             2800.0 if side == "LONG" else 2500.0, 1.0), position_id=pid)
            from storage.codec import paper_encode
            store.db.execute("INSERT INTO paper_positions VALUES(?,?)", (pid, paper_encode(p)))
        with self.assertRaisesRegex(RuntimeError, "duplicate open paper position"):
            store.load_paper(ACCOUNT)

    def test_trade_manager_manages_one_position_per_symbol_key(self):
        source = inspect.getsource(TradeManager.process_bar)
        self.assertIn("for symbol, position in list(self.account.open_positions.items())", source)
        self.assertIn("del self.account.open_positions[symbol]", source)


class RiskV2SymbolRuleTests(unittest.TestCase):
    def test_symbol_rule_is_inside_the_engine_and_direction_blind(self):
        self.assertEqual(RISK_POLICY_V2_P5.max_positions_or_pending_per_symbol, 1)
        for side, stop, target in (("LONG", 2600.0, 2800.0), ("SHORT", 2700.0, 2500.0)):
            held = account()
            held.open_positions["XAUUSD"] = position("XAUUSD", side, 2650.0, stop, target, 0.5)
            result = evaluate(f3_plan(*XAU_LONG), held, {}, INS["XAUUSD"])
            self.assertEqual(result.decision.reason, "SYMBOL_EXPOSURE_LIMIT")
        orders = {"o": pending("XAUUSD", "SHORT", 2650.0, 2660.0, 2620.0, 0.5)}
        self.assertEqual(evaluate(f3_plan(*XAU_LONG), account(), orders, INS["XAUUSD"]).decision.reason,
                         "SYMBOL_EXPOSURE_LIMIT")
        # Cross-symbol exposure is governed by the aggregate cap, not the symbol rule.
        orders = {"o": pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 2.0)}
        self.assertEqual(evaluate(f3_plan(*EUR_SHORT), account(), orders, INS["EURUSD"]).decision.status, "APPROVED")


if __name__ == "__main__":
    unittest.main()
