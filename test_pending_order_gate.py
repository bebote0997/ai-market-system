"""V2 Phase 2 / B2.2: P1 cycle-gated evaluation of PENDING PAPER orders."""
import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from data.market_evidence import MarketBar, MarketEvidenceEngine
from execution import pending_order_gate as module
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.pending_order_gate import (CurrentCycleGate, PendingGateEvidenceError,
                                          gate_pending_orders)
from storage.database import SCHEMA_VERSION, Store
from storage.evidence_store import EvidenceStore

ROOT = Path(__file__).resolve().parent
T0 = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
FIVE = timedelta(minutes=5)
ACCOUNT = "paper-main"
# Opens: T1 would fill at 101, T2 (below the stop) would be rejected, T3 would fill at 102, T4 fills at 103.
OPENS = {1: 101.0, 2: 94.0, 3: 102.0, 4: 103.0, 5: 104.0}
FORBIDDEN = ("floor.orchestrator.run", "ai.orchestrator.run", "ai.runtime.call_agent",
             "agents.setup_validator.evaluar_setup", "agents.trade_planner.crear_trade_plan",
             "riesgo.evaluar_trade_plan", "execution.paper_broker.PaperBroker.submit_plan")


def at(index):
    return T0 + index * FIVE


def candle(index):
    price = OPENS[index]
    return MarketBar("XAUUSD", "5m", at(index), price, price + 1, price - 1, price, provider="twelve_data",
                     is_closed=True)


def bar_dict(index):
    bar = candle(index)
    return {"symbol": "XAUUSD", "timestamp": bar.start, "open": bar.open, "high": bar.high, "low": bar.low,
            "close": bar.close, "is_closed": True}


def gate(index, **overrides):
    values = dict(run_id=f"run-cycle-{index}", symbol="XAUUSD", bar=bar_dict(index), paper_enabled=True,
                  execution_fresh=True, session_open=True, ai_healthy=True, ai_final_status="NO_TRADE")
    values.update(overrides)
    return CurrentCycleGate(**values)


class GateCase(unittest.TestCase):
    """PENDING LONG XAUUSD order: entry 100, stop 95, target 140, quantity 2, as_of T0 by default."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-pending-gate-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.trading_path = self.dir / "trading_floor.db"
        self.store = Store(self.trading_path)
        self.addCleanup(lambda: self.store.close())
        self.evidence_store = EvidenceStore(self.dir / "market_evidence.db")
        self.addCleanup(lambda: self.evidence_store.close())
        self.evidence = MarketEvidenceEngine(self.evidence_store)

    def submit(self, as_of_index=0):
        account = PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0)
        broker = PaperBroker(account)
        broker.orders["ord-1"] = PaperOrder("1.0", "ord-1", "run-1", "XAUUSD", "LONG", 2.0, 100.0, 95.0, 140.0,
                                            1.0, 10000.0, 0.0, at(as_of_index))
        self.store.save_paper(broker)

    def ingest(self, indexes):
        bars = [candle(i) for i in indexes]
        return self.evidence.ingest("XAUUSD", "5m", bars, as_of=max(b.end for b in bars))

    def run_gate(self, current=None, store=None, as_of=None):
        return gate_pending_orders(store or self.store, self.evidence, account_id=ACCOUNT, symbol="XAUUSD",
                                   as_of=as_of or at(50), gate=current)

    def order(self):
        return self.store.load_paper(ACCOUNT)[1]["ord-1"]

    def economics(self):
        account, orders, fills = self.store.load_paper(ACCOUNT)
        return ({k: o.status for k, o in orders.items()}, sorted(f.fill_price for f in fills.values()),
                sorted(account.open_positions), account.equity, account.realized_pnl)

    def rows(self, event=module.EVENT):
        return [(r["symbol"], r["source"], r["timestamp"], r["run_id"]) for r in
                reversed(self.store.journal(event=event, limit=10000))]

    def payloads(self):
        import json
        return [json.loads(r["payload"]) for r in reversed(self.store.journal(event=module.EVENT, limit=10000))]


class IntermediateBarTests(GateCase):
    def test_1_2_intermediate_bars_not_evaluated_and_journaled(self):
        self.submit()
        self.ingest([1, 2, 3])
        with mock.patch.object(PaperBroker, "process_next_bar", side_effect=AssertionError("evaluated")):
            result = self.run_gate(None)
        self.assertEqual((result.status, result.gate), ("NOT_EVALUATED", "NO_CURRENT_CYCLE_GATE"))
        self.assertEqual(result.not_evaluated, tuple(("ord-1", at(i).isoformat()) for i in (1, 2, 3)))
        self.assertEqual(self.economics(), ({"ord-1": "PENDING"}, [], [], 10000.0, 0.0))  # No fill, no reject.
        self.assertEqual(self.rows(), [("XAUUSD", "ord-1", at(i).isoformat(), "run-1") for i in (1, 2, 3)])
        self.assertEqual(self.payloads(), [{"order_id": "ord-1", "bar_start": at(i).isoformat(),
                                            "reason": "NO_CURRENT_CYCLE_GATE"} for i in (1, 2, 3)])
        self.assertEqual(self.rows("ORDER_REJECTED") + self.rows("ORDER_CANCELLED") + self.rows("ORDER_FILLED"), [])

    def test_3_4_t4_gate_evaluates_only_its_own_bar(self):
        self.submit()
        self.ingest([1, 2, 3, 4])
        with mock.patch.object(PaperBroker, "process_next_bar", autospec=True,
                               side_effect=PaperBroker.process_next_bar) as spy:
            result = self.run_gate(gate(4))
        self.assertEqual([call.args[2]["timestamp"] for call in spy.call_args_list], [at(4)])
        self.assertEqual((result.status, result.gate, result.evaluated), ("EVALUATED", "CURRENT", (("ord-1", "FILLED"),)))
        self.assertEqual(result.not_evaluated, tuple(("ord-1", at(i).isoformat()) for i in (1, 2, 3)))
        status, fills, open_symbols, _, _ = self.economics()
        self.assertEqual((status, fills, open_symbols), ({"ord-1": "FILLED"}, [103.0], ["XAUUSD"]))  # T4 open only.
        position = self.store.load_paper(ACCOUNT)[0].open_positions["XAUUSD"]
        self.assertEqual((position.opened_at, position.entry_price), (at(4), 103.0))
        self.assertEqual(len(self.rows()), 3)

    def test_4_t4_gate_without_intermediate_evidence_still_uses_only_t4(self):
        self.submit()  # Evidence lag: no committed bars at all; the gate bar is the cycle's own bar.
        self.run_gate(gate(4))
        self.assertEqual(self.economics()[1], [103.0])

    def test_6_order_submitted_at_t3_ignores_t1_t2(self):
        self.submit(as_of_index=3)
        self.ingest([1, 2, 3, 4, 5])
        result = self.run_gate(gate(5))
        self.assertEqual(result.not_evaluated, (("ord-1", at(4).isoformat()),))
        self.assertEqual(self.economics()[:2], ({"ord-1": "FILLED"}, [104.0]))


class AsOfTests(GateCase):
    def test_5_bar_at_or_before_order_as_of_is_never_evaluated(self):
        for order_index, gate_index in ((3, 3), (4, 3)):
            with self.subTest(order=order_index, gate=gate_index):
                self.store.db.execute("DELETE FROM paper_orders")
                self.submit(as_of_index=order_index)
                self.ingest([1, 2, 3])
                with mock.patch.object(PaperBroker, "process_next_bar",
                                       side_effect=AssertionError("old bar evaluated")):
                    result = self.run_gate(gate(gate_index))
                self.assertEqual((result.status, result.evaluated, result.not_evaluated), ("EVALUATED", (), ()))
                self.assertEqual(self.order().status, "PENDING")
        self.assertEqual(self.rows("ORDER_CANCELLED"), [])


class FailedGateTests(GateCase):
    def assert_not_evaluated(self, current):
        for table in ("paper_orders", "paper_accounts", "journal"):  # Fresh order per gate variant.
            self.store.db.execute(f"DELETE FROM {table}")
        self.submit()
        self.ingest([1, 2, 3, 4])
        with mock.patch.object(PaperBroker, "process_next_bar", side_effect=AssertionError("evaluated")):
            result = self.run_gate(current)
        self.assertEqual((result.status, result.gate), ("NOT_EVALUATED", "NO_CURRENT_CYCLE_GATE"))
        self.assertEqual(result.not_evaluated, tuple(("ord-1", at(i).isoformat()) for i in (1, 2, 3, 4)))
        self.assertEqual(self.economics(), ({"ord-1": "PENDING"}, [], [], 10000.0, 0.0))

    def test_7_ai_final_status_gate_failure(self):
        for status in ("AI_CAUTION", "ERROR", "RISK_REJECTED"):
            with self.subTest(status=status):
                self.assert_not_evaluated(gate(4, ai_final_status=status))

    def test_7b_missing_or_invalid_ai_final_status_fails_closed(self):
        for status in (None, "", 1, True, ("NO_TRADE",), b"NO_TRADE"):
            with self.subTest(status=status):
                self.assertFalse(gate(4, ai_final_status=status).passed())
                self.assert_not_evaluated(gate(4, ai_final_status=status))
                self.assertEqual(self.rows("ORDER_REJECTED") + self.rows("ORDER_CANCELLED")
                                 + self.rows("ORDER_FILLED"), [])

    def test_7c_valid_ai_final_status_still_passes_existing_deny_list(self):
        for status in ("NO_TRADE", "VALID_SETUP", "WATCH"):
            with self.subTest(status=status):
                self.assertTrue(gate(4, ai_final_status=status).passed())
        for status in ("AI_CAUTION", "ERROR", "RISK_REJECTED"):
            with self.subTest(status=status):
                self.assertFalse(gate(4, ai_final_status=status).passed())

    def test_8_ai_failure(self):
        self.assert_not_evaluated(gate(4, ai_healthy=False))

    def test_9_freshness_failure(self):
        self.assert_not_evaluated(gate(4, execution_fresh=False))

    def test_10_session_failure(self):
        self.assert_not_evaluated(gate(4, session_open=False))

    def test_11_paper_disabled(self):
        self.assert_not_evaluated(gate(4, paper_enabled=False))

    def test_gate_flags_must_be_true_not_truthy(self):
        self.assert_not_evaluated(gate(4, execution_fresh="FRESH"))

    def test_gate_of_another_symbol_or_malformed_bar_is_not_current(self):
        self.assert_not_evaluated(gate(4, symbol="EURUSD"))
        self.assert_not_evaluated(gate(4, bar={**bar_dict(4), "timestamp": None}))

    def test_previous_gate_is_not_carried_forward(self):
        self.submit()
        self.ingest([1])
        self.ingest([2, 3, 4])
        self.assert_stale_gate_ignored(gate(1))

    def assert_stale_gate_ignored(self, old):
        with mock.patch.object(PaperBroker, "process_next_bar", side_effect=AssertionError("evaluated")):
            result = self.run_gate(old)
        self.assertEqual(result.gate, "NO_CURRENT_CYCLE_GATE")
        self.assertEqual(self.economics()[:2], ({"ord-1": "PENDING"}, []))

    def test_12_crashed_cycle_invents_no_gate_and_order_stays_pending(self):
        self.submit()
        self.ingest([1, 2])
        self.run_gate(None)  # Cycle crashed before reaching its gates: there is no gate.
        with mock.patch.object(Store, "save_paper", side_effect=OSError("crash inside the gated save")):
            with self.assertRaises(OSError):
                self.run_gate(gate(2))  # Crash during the gated cycle's commit: nothing committed.
        self.assertEqual(self.economics(), ({"ord-1": "PENDING"}, [], [], 10000.0, 0.0))
        self.ingest([3])
        self.run_gate(None)
        self.assertEqual(self.order().status, "PENDING")
        self.ingest([4])
        self.run_gate(gate(4))  # Next successfully gated cycle; T2's crashed approval is not reused.
        self.assertEqual(self.economics()[1], [103.0])


class RestartAndDuplicateTests(GateCase):
    def reopen(self):
        self.store.close()
        self.store = Store(self.trading_path)

    def test_13_restart_keeps_intermediates_unevaluated_without_retroactive_fill(self):
        self.submit()
        self.ingest([1, 2, 3])
        self.run_gate(None)
        self.reopen()
        self.assertEqual(self.run_gate(None).journaled, 0)
        self.assertEqual(self.order().status, "PENDING")
        self.reopen()
        self.ingest([4])
        result = self.run_gate(gate(4))
        self.assertEqual((result.journaled, self.economics()[1]), (0, [103.0]))
        self.assertEqual(len(self.rows()), 3)

    def test_14_duplicate_evidence_and_retries_have_no_duplicate_effects(self):
        self.submit()
        self.ingest([1, 2, 3])
        self.run_gate(None)
        self.assertEqual(self.ingest([1, 2, 3]).duplicates, tuple(at(i).isoformat() for i in (1, 2, 3)))
        for _ in range(3):
            self.assertEqual(self.run_gate(None).journaled, 0)
        self.ingest([4])
        self.run_gate(gate(4))
        after_first = (self.economics(), self.rows(), self.rows("ORDER_FILLED"), self.rows("POSITION_OPENED"))
        for _ in range(2):  # Duplicate delivery of the same gated cycle.
            self.assertEqual(self.run_gate(gate(4)).status, "NO_OP")
        self.assertEqual((self.economics(), self.rows(), self.rows("ORDER_FILLED"), self.rows("POSITION_OPENED")),
                         after_first)
        self.assertEqual(len(after_first[2]), 1)

    def test_concurrent_gated_writer_loses_without_economic_effect(self):
        self.submit()
        self.ingest([1, 2, 3, 4])
        other = Store(self.trading_path)
        self.addCleanup(other.close)
        original = Store.save_paper
        raced = []

        def interleave(store, broker, **kwargs):
            if store is self.store and not raced:
                raced.append(self.run_gate(gate(4), store=other))  # B commits between A's compute and save.
            return original(store, broker, **kwargs)
        with mock.patch.object(Store, "save_paper", interleave):
            result = self.run_gate(gate(4))
        self.assertEqual((raced[0].status, result.status), ("EVALUATED", "STALE"))
        self.assertEqual(self.economics()[:3], ({"ord-1": "FILLED"}, [103.0], ["XAUUSD"]))
        self.assertEqual((len(self.rows("ORDER_FILLED")), len(self.rows())), (1, 3))


class IsolationTests(GateCase):
    def test_15_no_historical_decision_pipeline(self):
        self.submit()
        self.ingest([1, 2, 3, 4])
        patches = [mock.patch(target, side_effect=AssertionError(f"{target} invoked")) for target in FORBIDDEN]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch.object(PaperBroker, "process_next_bar", side_effect=AssertionError("historical")):
            self.run_gate(None)
        runs_before = self.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        self.run_gate(gate(4))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], runs_before)
        self.assertEqual(self.rows("ORDER_SUBMITTED"), [])

    def test_module_imports_and_calls(self):
        tree = ast.parse(Path(module.__file__).read_bytes())
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertFalse({m for m in imports if m.split(".")[0] in {
            "agents", "ai", "floor", "riesgo", "runtime", "data", "urllib", "requests", "socket"}})
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        names = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in calls}
        self.assertFalse(names & {"submit_plan", "evaluar_trade_plan", "crear_trade_plan", "evaluar_setup",
                                  "run_ai", "call_agent", "ingest", "process_bar"})
        evaluations = [n for n in calls if getattr(n.func, "attr", "") == "process_next_bar"]
        self.assertEqual([ast.unparse(n.args[1]) for n in evaluations], ["gate.bar"])  # Only the gate's own bar.

    def test_20_not_wired_into_the_runtime(self):
        users = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
                 if ".venv" not in p.parts and not p.name.startswith("test_") and p.name != "pending_order_gate.py"
                 and "pending_order_gate" in p.read_text(encoding="utf-8", errors="replace")]
        self.assertEqual(users, [])

    def test_open_position_and_other_symbol_are_untouched(self):
        account = PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0)
        account.open_positions["EURUSD"] = PaperPosition("1.0", "pos-e", "ord-e", "run-e", "EURUSD", "LONG", 1.0,
                                                         1.1, 1.1, 1.09, 1.13, at(0), last_price=1.1,
                                                         contract_multiplier=1.0, cost_rate=0.0)
        self.store.save_paper(PaperBroker(account))
        self.ingest([1, 2])
        self.assertEqual(self.run_gate(gate(2)).status, "NO_OP")
        account = self.store.load_paper(ACCOUNT)[0]
        self.assertEqual((account.open_positions["EURUSD"].last_price, self.rows()), (1.1, []))

    def test_evidence_failure_fails_closed(self):
        self.submit()
        self.ingest([1, 2])
        for failure in (OSError("evidence disk"), [candle(2), candle(1)]):
            kwargs = {"return_value": failure} if isinstance(failure, list) else {"side_effect": failure}
            with self.subTest(failure=failure), mock.patch.object(MarketEvidenceEngine, "committed", **kwargs):
                with self.assertRaises(PendingGateEvidenceError):
                    self.run_gate(gate(2))
        self.assertEqual((self.economics()[0], self.rows()), ({"ord-1": "PENDING"}, []))

    def test_bars_not_closed_by_as_of_are_ignored(self):
        self.submit()
        self.ingest([1, 2])
        result = self.run_gate(None, as_of=at(2) + timedelta(minutes=2))
        self.assertEqual(result.not_evaluated, (("ord-1", at(1).isoformat()),))

    def test_18_schema_3(self):
        self.assertEqual(SCHEMA_VERSION, 3)
        self.assertEqual(self.store.db.execute("SELECT version FROM schema_info").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
