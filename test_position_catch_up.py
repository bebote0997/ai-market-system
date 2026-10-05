"""V2 Phase 2 / B2.1: exactly-once chronological catch-up of existing open PAPER positions."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

from data.market_evidence import MarketBar, MarketEvidenceEngine
from execution import position_catch_up as module
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.position_catch_up import CatchUpEvidenceError, catch_up_position
from execution.trade_manager import TradeManager
from storage.database import SCHEMA_VERSION, Store
from storage.evidence_store import EvidenceStore

ROOT = Path(__file__).resolve().parent
T0 = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
FIVE = timedelta(minutes=5)
ACCOUNT = "paper-main"


def at(index):
    return T0 + index * FIVE


def candle(index, *, low=99.0, high=101.0, open_=100.0, close=100.0, symbol="XAUUSD"):
    return MarketBar(symbol, "5m", at(index), open_, high, low, close, provider="twelve_data", is_closed=True)


def quiet(index, close=100.0):
    return candle(index, open_=close, high=close + 1, low=close - 1, close=close)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CatchUpCase(unittest.TestCase):
    """LONG XAUUSD entry 100, stop 95, target 115, opened at bar T0; bars T1.. follow."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-catch-up-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.trading_path = self.dir / "trading_floor.db"
        self.evidence_path = self.dir / "market_evidence.db"
        self.store = Store(self.trading_path)
        self.addCleanup(lambda: self.store.close())
        self.evidence_store = EvidenceStore(self.evidence_path)
        self.addCleanup(lambda: self.evidence_store.close())
        self.evidence = MarketEvidenceEngine(self.evidence_store)

    def open_position(self, *, side="LONG", stop=95.0, target=115.0, with_pending=False):
        account = PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0)
        position = PaperPosition("1.0", "pos-1", "ord-1", "run-1", "XAUUSD", side, 2.0, 100.0, 100.0, stop, target,
                                 at(0), last_price=100.0, contract_multiplier=1.0, cost_rate=0.0)
        account.open_positions["XAUUSD"] = position
        broker = PaperBroker(account)
        if with_pending:
            broker.orders["ord-eur"] = PaperOrder("1.0", "ord-eur", "run-eur", "EURUSD", "LONG", 1.0, 1.1, 1.09,
                                                  1.13, 1.0, 10000.0, 0.0, at(0))
        self.store.save_paper(broker)

    def ingest(self, bars):
        bars = list(bars)
        return self.evidence.ingest("XAUUSD", "5m", bars, as_of=max(b.end for b in bars))

    def run_catch_up(self, as_of=None):
        return catch_up_position(self.store, self.evidence, account_id=ACCOUNT, symbol="XAUUSD",
                                 as_of=as_of or at(50))

    def state(self):
        account, orders, fills = self.store.load_paper(ACCOUNT)
        rows = lambda table: self.store.db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        position = account.open_positions.get("XAUUSD")
        return {"open": None if position is None else (position.last_processed_at, position.last_price),
                "closed": [(t.exit_price, t.exited_at, t.reason, t.net_pnl) for t in account.closed_trades],
                "realized": account.realized_pnl, "unrealized": account.unrealized_pnl, "equity": account.equity,
                "closed_rows": rows("closed_trades"), "position_rows": rows("paper_positions"),
                "orders": sorted((o.order_id, o.status) for o in orders.values()), "fills": len(fills),
                "events": [r[0] for r in self.store.db.execute(
                    "SELECT event_type FROM journal WHERE event_type IN ('STOP_HIT','TARGET_HIT','POSITION_CLOSED') "
                    "ORDER BY id")]}


class ChronologyTests(CatchUpCase):
    def test_a_t1_to_t4_applied_chronologically(self):
        self.open_position()
        self.ingest([quiet(i, 100.0 + i) for i in range(1, 5)])
        result = self.run_catch_up()
        self.assertEqual(result.applied, tuple(at(i).isoformat() for i in range(1, 5)))
        self.assertEqual((result.status, result.watermark), ("APPLIED", at(4).isoformat()))
        self.assertEqual(self.state()["open"], (at(4), 104.0))
        self.assertEqual(self.state()["unrealized"], 8.0)

    def test_b_the_correct_sl_tp_bar_wins(self):
        # T2 trades through the stop; T4 would have reached the target. The stop at T2 must win.
        self.open_position()
        self.ingest([quiet(1), candle(2, low=94.0, high=101.0), quiet(3), candle(4, low=99.0, high=120.0, close=118.0)])
        result = self.run_catch_up()
        self.assertEqual((result.status, result.closed_by), ("CLOSED", at(2).isoformat()))
        state = self.state()
        self.assertEqual(state["closed"], [(95.0, at(2), "stop", -10.0)])
        self.assertEqual((state["realized"], state["equity"]), (-10.0, 9990.0))

    def test_k_after_close_later_bars_have_no_effect(self):
        self.open_position()
        self.ingest([quiet(1), candle(2, low=99.0, high=116.0, close=114.0), candle(3, low=50.0, high=101.0),
                     candle(4, low=99.0, high=200.0)])
        result = self.run_catch_up()
        self.assertEqual((result.applied, result.closed_by), ((at(1).isoformat(), at(2).isoformat()), at(2).isoformat()))
        after_close = self.state()
        self.assertEqual(after_close["closed"], [(115.0, at(2), "target", 30.0)])
        again = self.run_catch_up()
        self.assertEqual((again.status, again.applied), ("NO_OP", ()))
        self.assertEqual(self.state(), after_close)

    def test_resumes_incrementally_as_evidence_arrives(self):
        self.open_position()
        self.ingest([quiet(1), quiet(2)])
        self.assertEqual(len(self.run_catch_up().applied), 2)
        self.ingest([quiet(i) for i in range(1, 6)])
        self.assertEqual(self.run_catch_up().applied, (at(3).isoformat(), at(4).isoformat(), at(5).isoformat()))


class ExactlyOnceTests(CatchUpCase):
    def closing_window(self):
        self.open_position()
        self.ingest([quiet(1), candle(2, low=94.0), quiet(3), quiet(4)])

    def test_c_same_bar_twice_and_ghij_exactly_once(self):
        self.closing_window()
        self.run_catch_up()
        once = self.state()
        for _ in range(3):
            self.ingest([quiet(1), candle(2, low=94.0), quiet(3), quiet(4)])  # Re-presented evidence.
            self.assertEqual(self.run_catch_up().applied, ())
        self.assertEqual(self.state(), once)
        self.assertEqual(once["closed_rows"], 1)
        self.assertEqual(once["events"], ["STOP_HIT", "POSITION_CLOSED"])
        self.assertEqual(once["realized"], -10.0)

    def test_up_to_date_is_a_no_op(self):
        self.open_position()
        self.ingest([quiet(1)])
        self.run_catch_up()
        before = self.state()
        self.assertEqual(self.run_catch_up().status, "UP_TO_DATE")
        self.assertEqual(self.state(), before)

    def test_progress_written_by_another_writer_is_never_reapplied(self):
        self.open_position()
        self.ingest([quiet(1), quiet(2)])
        original = TradeManager.process_bar

        def concurrent_progress(manager, bar):
            if bar["timestamp"] == at(2):  # Simulate another writer having applied T2 meanwhile.
                raise AssertionError("T2 applied twice")
            return original(manager, bar)
        account, orders, fills = self.store.load_paper(ACCOUNT)
        account.open_positions["XAUUSD"].last_processed_at = at(2)
        broker = PaperBroker(account)
        broker.orders, broker.fills = orders, fills
        reads = []
        load = module._load

        def stale_first_read(store, account_id, instrument):
            reads.append(1)
            if len(reads) == 2:
                store.save_paper(broker)
            return load(store, account_id, instrument)
        with mock.patch.object(module, "_load", stale_first_read), \
                mock.patch.object(TradeManager, "process_bar", concurrent_progress):
            result = self.run_catch_up()
        self.assertEqual(result.applied, ())


class CrashRestartTests(CatchUpCase):
    def child(self, body):
        code = textwrap.dedent("""
            import contextlib, os, sys
            from datetime import datetime, timezone
            sys.path.insert(0, {root!r})
            from data.market_evidence import MarketEvidenceEngine
            from execution.position_catch_up import catch_up_position
            from execution.trade_manager import TradeManager
            from storage.database import Store
            from storage.evidence_store import EvidenceStore
            store = Store({trading!r})
            evidence = MarketEvidenceEngine(EvidenceStore({evidence!r}))
            AS_OF = datetime.fromisoformat({as_of!r})
        """).format(root=str(ROOT), trading=str(self.trading_path), evidence=str(self.evidence_path),
                    as_of=at(50).isoformat()) + textwrap.dedent(body) + textwrap.dedent("""
            catch_up_position(store, evidence, account_id="paper-main", symbol="XAUUSD", as_of=AS_OF)
            os._exit(0)
        """)
        return subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)

    def expected(self):
        """The uninterrupted result, computed on an identical copy."""
        reference = CatchUpCase("run")
        reference.setUp()
        try:
            reference.open_position()
            reference.ingest(self.window())
            reference.run_catch_up()
            return reference.state()
        finally:
            reference.doCleanups()

    @staticmethod
    def window():
        return [quiet(1), quiet(2, 103.0), candle(3, low=94.0, high=104.0, open_=103.0, close=96.0), quiet(4)]

    def prepare(self):
        self.open_position()
        self.ingest(self.window())
        self.store.close()
        self.evidence_store.close()

    def reopen(self):
        self.store = Store(self.trading_path)
        self.evidence_store = EvidenceStore(self.evidence_path)
        self.evidence = MarketEvidenceEngine(self.evidence_store)

    def test_d_crash_before_processing_t2_resumes_t2(self):
        self.prepare()
        child = self.child("""
            original = TradeManager.process_bar
            def crash_on_t2(manager, bar):
                if bar["timestamp"].minute == 10:
                    os._exit(41)
                return original(manager, bar)
            TradeManager.process_bar = crash_on_t2
        """)
        self.assertEqual(child.returncode, 41, child.stderr)
        self.reopen()
        self.assertEqual(self.state()["open"], (at(1), 100.0))
        result = self.run_catch_up()
        self.assertEqual(result.applied, (at(2).isoformat(), at(3).isoformat()))
        self.assertEqual(self.state(), self.expected())

    def test_e_crash_inside_t2_transaction_resumes_t2(self):
        self.prepare()
        child = self.child("""
            calls = []
            @contextlib.contextmanager
            def crash_before_second_commit(self):
                self.db.execute("BEGIN IMMEDIATE")
                calls.append(1)
                yield
                if len(calls) == 2:
                    os._exit(42)  # T2's effects are written but never committed.
                self.db.execute("COMMIT")
            Store.transaction = crash_before_second_commit
        """)
        self.assertEqual(child.returncode, 42, child.stderr)
        self.reopen()
        self.assertEqual(self.state()["open"], (at(1), 100.0))
        self.assertEqual(self.run_catch_up().applied, (at(2).isoformat(), at(3).isoformat()))
        self.assertEqual(self.state(), self.expected())

    def test_f_crash_after_t2_commit_resumes_t3(self):
        self.prepare()
        child = self.child("""
            saves = []
            original = Store.save_paper
            def crash_after_second_save(self, broker, **kwargs):
                original(self, broker, **kwargs)
                saves.append(1)
                if len(saves) == 2:
                    os._exit(43)
            Store.save_paper = crash_after_second_save
        """)
        self.assertEqual(child.returncode, 43, child.stderr)
        self.reopen()
        self.assertEqual(self.state()["open"], (at(2), 103.0))
        self.assertEqual(self.run_catch_up().applied, (at(3).isoformat(),))
        final = self.state()
        self.assertEqual(final, self.expected())
        self.assertEqual((final["closed_rows"], final["realized"]), (1, -10.0))

    def test_j_repeated_crashes_converge_to_the_uninterrupted_equity(self):
        self.prepare()
        for _ in range(3):
            child = self.child("""
                original = Store.save_paper
                def crash_after_one_save(self, broker, **kwargs):
                    original(self, broker, **kwargs)
                    os._exit(44)
                Store.save_paper = crash_after_one_save
            """)
            self.assertIn(child.returncode, (0, 44), child.stderr)
        self.reopen()
        self.run_catch_up()
        self.assertEqual(self.state(), self.expected())


class IsolationTests(CatchUpCase):
    def test_l_m_n_no_decision_pipeline_no_pending_orders_no_new_trades(self):
        self.open_position(with_pending=True)
        self.ingest([quiet(1), candle(2, low=99.0, high=116.0), quiet(3)])
        forbidden = AssertionError("decision pipeline invoked during catch-up")
        targets = ("floor.orchestrator.run", "ai.orchestrator.run", "ai.runtime.call_agent",
                   "agents.setup_validator.evaluar_setup", "agents.trade_planner.crear_trade_plan",
                   "riesgo.evaluar_trade_plan", "execution.paper_broker.PaperBroker.submit_plan",
                   "execution.paper_broker.PaperBroker.process_next_bar")
        patches = [mock.patch(target, side_effect=forbidden) for target in targets]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        runs_before = self.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        result = self.run_catch_up()
        self.assertEqual(result.status, "CLOSED")
        state = self.state()
        self.assertEqual(state["orders"], [("ord-eur", "PENDING")])  # Untouched pending order.
        self.assertEqual((state["fills"], state["closed_rows"]), (0, 1))  # Only the existing position's close.
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0], runs_before)
        self.assertEqual(self.store.db.execute(
            "SELECT COUNT(*) FROM journal WHERE event_type IN ('ORDER_SUBMITTED','ORDER_FILLED','POSITION_OPENED')"
        ).fetchone()[0], 0)

    def test_module_imports_no_decision_or_network_code(self):
        tree = ast.parse(Path(module.__file__).read_bytes())
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertFalse({m for m in imports if m.split(".")[0] in {
            "agents", "ai", "floor", "riesgo", "runtime", "data", "urllib", "requests", "socket"}})
        calls = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertFalse(calls & {"submit_plan", "process_next_bar", "evaluar_trade_plan", "crear_trade_plan",
                                  "evaluar_setup", "run_ai", "call_agent", "ingest", "transaction"})

    def test_o_bootstrap_without_open_position_is_a_no_op(self):
        account = PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0)
        self.store.save_paper(PaperBroker(account))
        self.ingest([quiet(i) for i in range(1, 30)])
        before = digest(self.trading_path)
        self.assertEqual(self.run_catch_up().status, "NO_OP")
        self.assertEqual(digest(self.trading_path), before)
        empty = Store(self.dir / "empty.db")
        self.addCleanup(empty.close)
        self.assertEqual(catch_up_position(empty, self.evidence, account_id=ACCOUNT, symbol="XAUUSD",
                                           as_of=at(50)).status, "NO_OP")

    def test_not_wired_into_the_runtime(self):
        users = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
                 if ".venv" not in p.parts and not p.name.startswith("test_") and p.name != "position_catch_up.py"
                 and "position_catch_up" in p.read_text(encoding="utf-8", errors="replace")]
        # B2.3B: the runtime is the only user, behind v2_position_catch_up (OFF by default).
        self.assertEqual(sorted(users), ["runtime/config.py", "runtime/service.py"])


class FailureTests(CatchUpCase):
    def test_p_evidence_failure_fails_closed_without_newest_bar_fallback(self):
        self.open_position()
        self.ingest([quiet(1), candle(2, low=94.0)])
        before = digest(self.trading_path)
        for failure in (OSError("evidence disk"), RuntimeError("corrupt evidence")):
            with self.subTest(failure=type(failure).__name__), \
                    mock.patch.object(MarketEvidenceEngine, "committed", side_effect=failure):
                with self.assertRaises(CatchUpEvidenceError):
                    self.run_catch_up()
                self.assertEqual(digest(self.trading_path), before)
        unordered = [quiet(2), quiet(1)]
        with mock.patch.object(MarketEvidenceEngine, "committed", return_value=unordered):
            with self.assertRaises(CatchUpEvidenceError):
                self.run_catch_up()
        self.assertEqual(digest(self.trading_path), before)
        result = self.run_catch_up()  # A later healthy cycle resumes from the correct bar.
        self.assertEqual((result.applied, result.closed_by), ((at(1).isoformat(), at(2).isoformat()), at(2).isoformat()))

    def test_q_trading_save_failure_leaves_evidence_unchanged_and_bar_eligible(self):
        self.open_position()
        self.ingest([quiet(1), candle(2, low=94.0)])
        evidence_before = digest(self.evidence_path)
        original = Store.save_paper
        saves = []

        def fail_second(store, broker, **kwargs):
            saves.append(1)
            if len(saves) == 2:
                raise OSError("trading disk full")
            return original(store, broker, **kwargs)
        with mock.patch.object(Store, "save_paper", fail_second):
            with self.assertRaises(OSError):
                self.run_catch_up()
        self.assertEqual(digest(self.evidence_path), evidence_before)
        self.assertEqual((self.state()["open"], self.state()["closed_rows"]), ((at(1), 100.0), 0))
        result = self.run_catch_up()
        self.assertEqual((result.applied, result.closed_by), ((at(2).isoformat(),), at(2).isoformat()))
        self.assertEqual(self.state()["closed_rows"], 1)

    def test_r_forming_late_and_revised_evidence_create_no_economics(self):
        self.open_position()
        self.ingest([quiet(1), quiet(3)])  # GAP at T2.
        self.evidence.ingest("XAUUSD", "5m", [candle(2, low=50.0)], as_of=at(10))  # LATE: below the watermark.
        self.evidence.ingest("XAUUSD", "5m", [candle(3, low=50.0)], as_of=at(10))  # REVISION of T3.
        self.evidence.ingest("XAUUSD", "5m", [candle(4, low=50.0)], as_of=at(4) + timedelta(minutes=2))  # Forming.
        self.assertEqual([a.kind for a in self.evidence.anomalies()], ["LATE", "GAP", "REVISION"])
        result = self.run_catch_up()
        self.assertEqual(result.applied, (at(1).isoformat(), at(3).isoformat()))
        self.assertEqual((self.state()["closed_rows"], self.state()["open"]), (0, (at(3), 100.0)))
        # A committed bar that has not closed by the caller's as_of is not applied either.
        self.ingest([candle(4, low=50.0)])
        self.assertEqual(self.run_catch_up(as_of=at(4) + timedelta(minutes=2)).applied, ())
        self.assertEqual(self.state()["closed_rows"], 0)

    def test_schema_and_trading_modules_unchanged(self):
        self.assertEqual(SCHEMA_VERSION, 3)
        self.assertEqual(self.store.db.execute("SELECT version FROM schema_info").fetchone()[0], 3)


if __name__ == "__main__":
    unittest.main()
