"""V2 Phase 6 / P6.1: conflict engine, durable setup_id, atomic conflict + Risk V2 + reservation path.
Real multi-process races live in test_phase6_conflict_concurrency.py."""
import inspect
import json
import tempfile
import unittest
from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from core.contracts import FloorRunReport, SetupAssessment
from core.risk_policy import RISK_POLICY_V2, RISK_POLICY_V2_P5
from execution import conflict_engine, conflict_path
from execution.conflict_engine import classify
from execution.conflict_path import EVENT, submit_with_conflict_control
from execution.contracts import PaperOrder
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from storage.codec import paper_decode, paper_encode
from storage.database import SCHEMA_VERSION, Store
from test_phase5_risk_engine import (ACCOUNT, AT, INS, XAU_LONG, account, bar, f3_plan, pending,
                                     position)

ROOT = Path(__file__).resolve().parent
XAU_SHORT = ("XAUUSD", "SHORT", 2650.0, 2700.0)


def setup_for(plan, anchor=0):
    """A VALID_SETUP for ``plan``; ``anchor`` selects the 15m BOS bar, so equal anchors give equal setup_ids."""
    bos = {"break_timestamp": pd.Timestamp("2026-01-15T12:00Z") + pd.Timedelta(minutes=15 * anchor),
           "broken_level": 2640.0, "direction": "bullish" if plan.side == "LONG" else "bearish"}
    return SetupAssessment("1.0", plan.run_id, AT, plan.symbol, "VALID_SETUP", plan.side, ("1h", "15m", "5m"),
                           evidence=({"15m": {"bos": bos, "retracement": None}}, {}), invalidation=plan.stop)


def candidate(args, run_id, anchor=0):
    plan = f3_plan(*args, run_id=run_id)
    return FloorRunReport("1.0", run_id, AT, plan.symbol, {}, {}, None, setup_for(plan, anchor), plan, None,
                          "PLAN_READY")


def setup_id_of(report):
    return conflict_path.candidate(report)[0]


class Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p61-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "trading_floor.db"
        store = Store(self.path)
        store.save_paper(PaperBroker(account()))
        store.close()

    def store(self):
        store = Store(self.path)  # a new connection each time = restart
        self.addCleanup(store.close)
        return store

    def seed(self, *items):
        store = self.store()
        held, orders, fills = store.load_paper(ACCOUNT)
        broker = PaperBroker(held)
        broker.orders, broker.fills = orders, fills
        for item in items:
            if isinstance(item, PaperOrder):
                broker.orders[item.order_id] = item
            else:
                held.open_positions[item.symbol] = item
        store.save_paper(broker)

    def submit(self, report, store=None, **kwargs):
        return submit_with_conflict_control(store or self.store(), report, INS[report.symbol], account_id=ACCOUNT,
                                            as_of=AT, **kwargs)

    def rows(self, event=EVENT):
        return [json.loads(r[0]) for r in self.store().db.execute(
            "SELECT payload FROM journal WHERE event_type=? ORDER BY id", (event,))]

    def raw(self, table):
        return sorted(r[0] for r in self.store().db.execute(f"SELECT payload FROM {table}"))

    def paper_state(self):
        store = self.store()
        return store.paper_state(*store.load_paper(ACCOUNT))


class ClassifyTests(unittest.TestCase):
    def test_matrix(self):
        long_pos = replace(position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0), setup_id="S1")
        cases = [  # (position, orders, new side, new id) -> (classification, reason, kind)
            (None, {}, "LONG", "S2", ("NO_CONFLICT", "NO_CONFLICT", None)),
            (long_pos, {}, "LONG", "S1", ("DUPLICATE_SETUP", "DUPLICATE_SETUP", "EXISTING_POSITION_CONFLICT")),
            (long_pos, {}, "LONG", "S2", ("SAME_DIRECTION_NEW_SETUP", "SAME_DIRECTION_POSITION_CONFLICT",
                                          "EXISTING_POSITION_CONFLICT")),
            (long_pos, {}, "SHORT", "S2", ("OPPOSITE_SETUP", "OPPOSITE_POSITION_CONFLICT", "EXISTING_POSITION_CONFLICT")),
            (replace(long_pos, setup_id=None), {}, "LONG", "S1",
             ("UNKNOWN_SETUP_ID", "UNKNOWN_EXISTING_SETUP_ID", "EXISTING_POSITION_CONFLICT")),
        ]
        order = replace(pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0), setup_id="P1")
        cases += [
            (None, {"o": order}, "LONG", "P1", ("DUPLICATE_SETUP", "DUPLICATE_SETUP", "EXISTING_PENDING_CONFLICT")),
            (None, {"o": order}, "LONG", "P2", ("SAME_DIRECTION_NEW_SETUP", "SAME_DIRECTION_PENDING_CONFLICT",
                                                "EXISTING_PENDING_CONFLICT")),
            (None, {"o": order}, "SHORT", "P2", ("OPPOSITE_SETUP", "OPPOSITE_PENDING_CONFLICT", "EXISTING_PENDING_CONFLICT")),
            (None, {"o": replace(order, setup_id=None)}, "LONG", "P1",
             ("UNKNOWN_SETUP_ID", "UNKNOWN_EXISTING_SETUP_ID", "EXISTING_PENDING_CONFLICT")),
            (None, {"o": replace(order, status="FILLED")}, "LONG", "P1", ("NO_CONFLICT", "NO_CONFLICT", None)),
            (None, {"o": replace(order, symbol="EURUSD")}, "SHORT", "P2", ("NO_CONFLICT", "NO_CONFLICT", None)),
        ]
        for held_position, orders, side, setup_id, expected in cases:
            with self.subTest(position=held_position and held_position.setup_id, orders=list(orders), side=side):
                held = account()
                if held_position is not None:
                    held.open_positions["XAUUSD"] = held_position
                d = classify(symbol="XAUUSD", direction=side, setup_id=setup_id, account=held, orders=orders)
                self.assertEqual((d.classification, d.reason_code, d.conflict_kind), expected)
                self.assertEqual(d.status, "ALLOW" if expected[0] == "NO_CONFLICT" else "BLOCK")
                self.assertEqual(d.requires_risk_evaluation, expected[0] == "NO_CONFLICT")
                self.assertEqual(d.economic_action, "CONTINUE_TO_RISK" if d.status == "ALLOW" else "BLOCK_NEW_EXPOSURE")
                self.assertIn(d.reason_code, conflict_engine.REASON_CODES)

    def test_position_blocks_before_pending_and_record_fields(self):
        held = account()
        held.open_positions["XAUUSD"] = replace(position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0), setup_id="S1")
        orders = {"o": replace(pending("XAUUSD", "SHORT", 2650.0, 2700.0, 2500.0, 1.0), setup_id="P1")}
        d = classify(symbol="XAUUSD", direction="SHORT", setup_id="P1", account=held, orders=orders)
        record = d.as_record()
        self.assertEqual((d.blocking_entity_type, record["reason_code"]), ("POSITION", "OPPOSITE_POSITION_CONFLICT"))
        self.assertEqual((record["existing_position_setup_id"], record["existing_order_setup_id"],
                          record["existing_order_direction"]), ("S1", "P1", "SHORT"))
        self.assertEqual(len(record["exposures"]), 2)

    def test_invalid_candidates_and_purity(self):
        held = account()
        self.assertEqual(classify(symbol="XAUUSD", direction="LONG", setup_id=None, account=held, orders={}).reason_code,
                         "MISSING_NEW_SETUP_ID")
        self.assertEqual(classify(symbol="XAUUSD", direction="UP", setup_id="x", account=held, orders={}).reason_code,
                         "INVALID_DIRECTION")
        source = inspect.getsource(conflict_engine)
        for forbidden in ("save_paper", "_event", "del ", "process_next_bar", "TradeManager", "import ai",
                          "SAME_THESIS =", "NEW_STRUCTURE =", "TREND_CHANGE ="):
            self.assertNotIn(forbidden, source)
        import re
        self.assertIsNone(re.search(r"\.(status|stop|target|quantity|setup_id) = (?!=)", source))  # no assignment


class DurableSetupIdTests(Case):
    def test_codec_legacy_bytes_and_round_trip(self):
        legacy_order = pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0, version=None)
        legacy_position = position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0)
        for item, name in ((legacy_order, "PaperOrder"), (legacy_position, "PaperPosition")):
            encoded = paper_encode(item)
            self.assertNotIn("setup_id", encoded)  # legacy payload bytes unchanged
            self.assertIsNone(paper_decode(name, encoded).setup_id)
            stamped = paper_encode(replace(item, setup_id="S-1"))
            self.assertIn('"setup_id":"S-1"', stamped)
            self.assertEqual(paper_decode(name, stamped).setup_id, "S-1")
        self.assertEqual(SCHEMA_VERSION, 3)

    def test_setup_to_order_to_fill_to_position_to_restart(self):
        report = candidate(XAU_LONG, "run-a")
        result = self.submit(report)
        self.assertEqual(result.status, "RESERVED")
        sid = setup_id_of(report)
        _, orders, _ = self.store().load_paper(ACCOUNT)  # restart
        order = orders[result.order.order_id]
        self.assertEqual((order.setup_id, order.risk_policy_version), (sid, RISK_POLICY_V2))
        store = self.store()
        held, orders, fills = store.load_paper(ACCOUNT)
        broker = PaperBroker(held, INS["XAUUSD"])
        broker.orders, broker.fills = orders, fills
        expected = store.paper_state(held, orders, fills)
        broker.process_next_bar(orders[result.order.order_id], bar("XAUUSD", 2650.0))
        self.assertNotEqual(store.save_paper(broker, expected_state=expected), False)
        held, _, _ = self.store().load_paper(ACCOUNT)  # restart after fill
        self.assertEqual(held.open_positions["XAUUSD"].setup_id, sid)  # inherited, never re-derived

    def test_legacy_none_stays_unknown_across_fill_and_restart(self):
        self.seed(pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0, version=None))
        store = self.store()
        held, orders, fills = store.load_paper(ACCOUNT)
        broker = PaperBroker(held, INS["XAUUSD"])
        broker.orders, broker.fills = orders, fills
        broker.process_next_bar(orders["ord-XAUUSD"], bar("XAUUSD", 2650.0))
        store.save_paper(broker)
        held, _, _ = self.store().load_paper(ACCOUNT)
        self.assertIsNone(held.open_positions["XAUUSD"].setup_id)
        self.assertNotIn("setup_id", self.raw("paper_positions")[0])
        result = self.submit(candidate(XAU_LONG, "run-b"))
        self.assertEqual((result.status, result.reason), ("BLOCKED", "UNKNOWN_EXISTING_SETUP_ID"))


class ConflictPathTests(Case):
    def test_no_conflict_reserves_with_conflict_and_risk_rows_in_one_save(self):
        result = self.submit(candidate(XAU_LONG, "run-a"))
        self.assertEqual((result.status, result.reason, result.attempts), ("RESERVED", "approved", 1))
        conflict_rows, risk_rows = self.rows(), self.rows("RISK_V2_DECISION")
        self.assertEqual(len(conflict_rows), 1)
        self.assertEqual(len(risk_rows), 1)
        row = conflict_rows[0]
        self.assertEqual((row["decision"], row["classification"], row["risk_status"], row["order_id"]),
                         ("RESERVED", "NO_CONFLICT", "APPROVED", result.order.order_id))
        self.assertEqual(Fraction(row["proposed_reservation"]).limit_denominator(7), Fraction(800, 7))
        self.assertEqual((row["portfolio_risk_before"], row["risk_policy_version"]), ("0", RISK_POLICY_V2))

    def blocked_against(self, exposure, report):
        self.seed(exposure)
        before_state, before_rows = self.paper_state(), (self.raw("paper_positions"), self.raw("paper_orders"))
        result = self.submit(report)
        self.assertEqual(result.status, "BLOCKED")
        self.assertEqual(self.paper_state(), before_state)  # nothing economic written, nothing touched
        self.assertEqual((self.raw("paper_positions"), self.raw("paper_orders")), before_rows)
        rows = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["decision"], rows[0]["risk_status"], rows[0]["order_id"]),
                         ("BLOCKED", "NOT_EVALUATED", None))
        self.assertEqual(self.rows("RISK_V2_DECISION"), [])  # Risk is not called for a blocked candidate
        return result, rows[0]

    def test_same_direction_position_blocks_and_both_identities_are_traced(self):
        report = candidate(XAU_LONG, "run-b", anchor=1)
        existing = replace(position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0), setup_id="S-OLD")
        result, row = self.blocked_against(existing, report)
        self.assertEqual(result.reason, "SAME_DIRECTION_POSITION_CONFLICT")
        self.assertEqual((row["existing_position_setup_id"], row["new_setup_id"]), ("S-OLD", setup_id_of(report)))

    def test_opposite_position_blocks_never_closes_and_management_continues(self):
        existing = replace(position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0), setup_id="S-OLD")
        result, _ = self.blocked_against(existing, candidate(XAU_SHORT, "run-b"))
        self.assertEqual(result.reason, "OPPOSITE_POSITION_CONFLICT")
        for price, reason in ((2590.0, "STOP_HIT"), (2810.0, "TARGET_HIT")):  # SL and TP management both continue
            with self.subTest(reason=reason):
                held, orders, fills = self.store().load_paper(ACCOUNT)
                broker = PaperBroker(held, INS["XAUUSD"])
                closed = TradeManager(held, broker).process_bar(bar("XAUUSD", price, minutes=5))
                self.assertEqual((len(closed), closed[0].exit_price),
                                 (1, price if reason == "STOP_HIT" else price))
                self.assertIn(reason, [e.event_type for e in broker.journal])

    def test_pending_conflicts_preserve_the_order_and_progression_continues(self):
        order = replace(pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0), setup_id=None)
        result, _ = self.blocked_against(order, candidate(XAU_SHORT, "run-b"))
        self.assertEqual(result.reason, "UNKNOWN_EXISTING_SETUP_ID")  # legacy order NOT re-stamped
        store = self.store()
        held, orders, fills = store.load_paper(ACCOUNT)
        broker = PaperBroker(held, INS["XAUUSD"])
        broker.orders, broker.fills = orders, fills
        broker.process_next_bar(orders["ord-XAUUSD"], bar("XAUUSD", 2650.0))  # existing pending still progresses
        self.assertEqual(orders["ord-XAUUSD"].status, "FILLED")

    def test_duplicate_setup_retry_restart_and_different_run(self):
        first = candidate(XAU_LONG, "run-a")
        self.assertEqual(self.submit(first).status, "RESERVED")
        self.assertEqual(self.submit(first).status, "DUPLICATE_RUN")  # technical retry: same run_id
        again = self.submit(candidate(XAU_LONG, "run-b"))  # same setup_id, different run: economic duplicate
        self.assertEqual((again.status, again.reason), ("BLOCKED", "DUPLICATE_SETUP"))
        retry = self.submit(candidate(XAU_LONG, "run-b"), store=self.store())  # restart + retry of the block
        self.assertEqual((retry.status, retry.reason), ("DUPLICATE_RUN", "run_id_already_decided"))
        self.assertEqual(len(self.store().load_paper(ACCOUNT)[1]), 1)
        self.assertEqual([r["decision"] for r in self.rows()], ["RESERVED", "BLOCKED"])

    def test_risk_rejections_are_recorded_without_orders(self):
        drawdown = account(9500.0, realized=-500.0)
        store = self.store()
        store.save_paper(PaperBroker(drawdown))
        result = self.submit(candidate(XAU_LONG, "run-a"))
        self.assertEqual((result.status, result.reason), ("RISK_REJECTED", "ACCOUNT_DRAWDOWN_LIMIT"))
        self.assertEqual([r["decision"] for r in self.rows()], ["RISK_REJECTED"])
        self.assertEqual(len(self.rows("RISK_V2_DECISION")), 1)
        self.assertEqual(self.store().load_paper(ACCOUNT)[1], {})

    def test_aggregate_cap_and_unknown_pending_policy_still_bind(self):
        self.seed(replace(pending("EURUSD", "SHORT", 1.085, 1.096, 1.052, 9090.0), setup_id="E1"))
        tight = replace(RISK_POLICY_V2_P5, aggregate_portfolio_risk_fraction=Fraction(15, 1000))
        result = self.submit(candidate(XAU_LONG, "run-a"), policy=tight)
        self.assertEqual((result.status, result.reason), ("RISK_REJECTED", "PORTFOLIO_RISK_LIMIT"))
        self.setUp()
        self.seed(replace(pending("EURUSD", "SHORT", 1.085, 1.096, 1.052, 100.0, version=None), setup_id="E1"))
        result = self.submit(candidate(XAU_LONG, "run-a"))
        self.assertEqual((result.status, result.reason), ("RISK_REJECTED", "UNKNOWN_PENDING_RISK_POLICY"))

    def test_risk_engine_cannot_be_bypassed_by_a_wrong_allow(self):
        self.seed(replace(position("XAUUSD", "SHORT", 2650.0, 2700.0, 2500.0, 0.5), setup_id="S-OLD"))
        allow = classify(symbol="XAUUSD", direction="LONG", setup_id="x", account=account(), orders={})
        with patch("execution.conflict_path.classify", return_value=allow):
            result = self.submit(candidate(XAU_LONG, "run-a"))
        self.assertEqual((result.status, result.reason), ("RISK_REJECTED", "SYMBOL_EXPOSURE_LIMIT"))
        self.assertEqual(len(self.store().load_paper(ACCOUNT)[1]), 0)

    def test_stale_state_recomputes_conflict_and_risk(self):
        original = Store.save_paper
        fired = []

        def competitor_first(store, broker, **kwargs):
            if kwargs.get("expected_state") is not None and not fired:
                fired.append(1)  # another writer opens an XAU position between evaluation and save
                other = Store(self.path)
                held, orders, fills = other.load_paper(ACCOUNT)
                held.open_positions["XAUUSD"] = replace(position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0),
                                                        setup_id="S-OTHER")
                rival = PaperBroker(held)
                rival.orders, rival.fills = orders, fills
                original(other, rival)
                other.close()
            return original(store, broker, **kwargs)

        with patch.object(Store, "save_paper", competitor_first):
            result = self.submit(candidate(XAU_SHORT, "run-a"))
        self.assertEqual((result.status, result.reason, result.attempts), ("BLOCKED", "OPPOSITE_POSITION_CONFLICT", 2))
        self.assertEqual([r["decision"] for r in self.rows()], ["BLOCKED"])  # the stale ALLOW left no trace
        self.assertEqual(self.store().load_paper(ACCOUNT)[1], {})

    def test_invalid_candidates_are_not_submitted(self):
        report = candidate(XAU_LONG, "run-a")
        for broken, reason in (
                (replace(report, trade_plan=replace(report.trade_plan, policy_version="V1")), "not_a_fixed_3r_plan"),
                (replace(report, setup_assessment=replace(report.setup_assessment, side="SHORT")),
                 "candidate_lineage_invalid"),
                (replace(report, setup_assessment=replace(report.setup_assessment, explanation={"setup_id": "forged"})),
                 "setup_identity_unavailable"),
                (replace(report, final_status="AI_CAUTION"), "not_a_plan_ready_report")):
            with self.subTest(reason=reason):
                result = self.submit(broken)
                self.assertEqual((result.status, result.reason), ("NOT_SUBMITTED", reason))
        self.assertEqual(self.rows(), [])


class BoundaryTests(unittest.TestCase):
    def test_runtime_not_wired_and_authority_bounded(self):
        service = (ROOT / "runtime/service.py").read_text(encoding="utf-8")
        config = (ROOT / "runtime/config.py").read_text(encoding="utf-8")
        for name in ("conflict_engine", "conflict_path", "submit_with_conflict_control"):
            self.assertNotIn(name, service)
        self.assertNotIn("conflict", config.lower())  # no Phase 6 flag / env path
        source = inspect.getsource(conflict_path)
        for forbidden in ("TradeManager", "process_next_bar", "CANCELLED", ".stop =", ".target =", "import ai",
                          "open_positions[", "del "):
            self.assertNotIn(forbidden, source)
        ai_sources = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "ai").rglob("*.py"))
        self.assertNotIn("conflict_engine", ai_sources)


if __name__ == "__main__":
    unittest.main()
