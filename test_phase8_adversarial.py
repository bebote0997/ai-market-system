"""V2 Phase 8 / P8.1A: adversarial tests of the CURRENT runtime execution path (flags OFF). Tests only.

Invariants checked after every scenario (``assert_paper_invariants``): at most one order per run, no fill without a
FILLED order, every open position has a FILLED origin order with the same quantity, no duplicate position per symbol,
the runtime can restart on the resulting state, and a re-run of the same slot is DUPLICATE.
"""
import json
import shutil
import sqlite3
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from ai.provider import DeterministicAIProvider
from execution.contracts import PaperAccount, PaperOrder
from execution.paper_broker import PaperBroker
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.scheduler import Scheduler
from runtime.service import OperationalRuntime
from storage.codec import paper_encode
from storage.database import Store
import test_demo_runner as fixture
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts

ACCOUNT = "paper-main"
ROOT = Path(__file__).resolve().parent


def fail_once(target, attribute, *, predicate=lambda *a, **k: True):
    """Patch ``target.attribute`` so its first call satisfying ``predicate`` raises sqlite3.OperationalError."""
    original = getattr(target, attribute)
    state = {"fired": False}

    def wrapper(*args, **kwargs):
        if not state["fired"] and predicate(*args, **kwargs):
            state["fired"] = True
            raise sqlite3.OperationalError("disk I/O error (injected)")
        return original(*args, **kwargs)
    return patch.object(target, attribute, wrapper), state


class Case(unittest.TestCase):
    setUp = fixture.TestDemoRunner.setUp
    tearDown = fixture.TestDemoRunner.tearDown

    def runner(self, minutes, recovery=120, path=None):
        config = self.config if path is None else RuntimeConfig(db_path=path, enabled_symbols=("XAUUSD", "EURUSD"),
                                                                market_provider_mode="twelve_data",
                                                                ai_provider_mode="openai")
        return DemoRunner(config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                          macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                          clock=lambda: T + timedelta(minutes=minutes), recovery_stale_after_seconds=recovery)

    def cycle(self, minutes, side="LONG", recovery=120, path=None, patches=()):
        runner = self.runner(minutes, recovery, path)
        try:
            with patched_scouts(side):
                for p in patches:
                    p.start()
                try:
                    return runner.runtime.run_cycle("XAUUSD", T + timedelta(minutes=minutes))
                finally:
                    for p in patches:
                        p.stop()
        finally:
            runner.close()

    def state(self, path=None):
        store = Store(path or self.path)
        try:
            account, orders, fills = store.load_paper(ACCOUNT)
            runs = {r["slot_key"]: (r["status"], r["final_status"]) for r in store.db.execute(
                "SELECT slot_key,status,final_status FROM runs")}
            locks = [r[0] for r in store.db.execute("SELECT symbol FROM symbol_locks")]
            reviews = {r[0]: json.loads(r[1]) for r in store.db.execute("SELECT run_id,payload FROM review_reports")}
            return account, orders, fills, runs, locks, reviews
        finally:
            store.close()

    def assert_paper_invariants(self, path=None):
        account, orders, fills, _, _, _ = self.state(path)
        per_run = {}
        for order in orders.values():
            per_run[order.run_id] = per_run.get(order.run_id, 0) + 1
        self.assertTrue(all(count == 1 for count in per_run.values()), per_run)
        filled = {o.order_id: o for o in orders.values() if o.status == "FILLED"}
        self.assertEqual({f.order_id for f in fills.values()} - set(filled), set())  # no fill without FILLED order
        self.assertEqual(len(fills), len(filled))  # one fill per FILLED order
        for position in (account.open_positions.values() if account else ()):
            origin = filled.get(position.origin_order_id)
            self.assertIsNotNone(origin)
            self.assertEqual(origin.quantity, position.quantity)
        return account, orders, fills


class SqliteFailureMatrixTests(Case):
    """DEC-8.3 investigation: an injected SQLite failure at each write point of the submission cycle."""

    POINTS = {
        "save_reports": (Store, "save_reports"),
        "submit_save_paper": (Store, "save_paper"),
        "record_execution": (Store, "record_execution"),
        "record_analysis_events": (Store, "record_analysis_events"),
    }

    def test_failure_points_never_create_phantom_or_duplicate_orders(self):
        outcomes = {}
        for name, (target, attribute) in self.POINTS.items():
            with self.subTest(point=name):
                self.setUp()
                predicate = ((lambda store, broker, **k: bool(broker.journal)) if name == "submit_save_paper"
                             else (lambda *a, **k: True))
                injected, fired = fail_once(target, attribute, predicate=predicate)
                result = self.cycle(0, patches=(injected,))
                self.assertTrue(fired["fired"], name)
                _, orders, _ = self.assert_paper_invariants()
                outcomes[name] = (result, len(orders))
                # The next slot, after a restart, never duplicates and always leaves a consistent state.
                self.cycle(15, recovery=0)
                _, orders, fills = self.assert_paper_invariants()
                self.assertLessEqual(len(orders), 1)
                self.assertEqual(self.cycle(15), "DUPLICATE")
                self.tearDown()
        # Pinned outcomes: failures before the submit commit leave no order; audit-only failures keep the decision.
        self.assertEqual(outcomes["save_reports"], ("ERROR", 0))
        self.assertEqual(outcomes["submit_save_paper"], ("ERROR", 0))
        self.assertEqual(outcomes["record_execution"], ("PLAN_READY", 1))  # best effort; order stands
        self.assertEqual(outcomes["record_analysis_events"], ("PLAN_READY", 1))

    def test_finish_failure_on_success_path_after_order(self):
        """finish() raising AFTER the order committed: the order stands exactly once; the run escapes as an exception
        and, after recovery, is recorded as FAILED/interrupted while the review keeps the SUBMITTED execution record
        (truthfulness gap, DEC-8.3)."""
        runner = self.runner(0)
        try:
            with patched_scouts("LONG"), patch.object(Store, "finish",
                                                      side_effect=sqlite3.OperationalError("disk I/O error")):
                with self.assertRaises(sqlite3.OperationalError):
                    runner.runtime.run_cycle("XAUUSD", T)
        finally:
            runner.close()
        _, orders, _, runs, locks, _ = self.state()
        self.assertEqual((len(orders), locks, set(runs.values())), (1, ["XAUUSD"], {("RUNNING", "PLAN_READY")}))
        self.assertEqual(self.cycle(15, recovery=0), "PLAN_READY")  # restart recovers the lock; pending filled
        account, orders, fills = self.assert_paper_invariants()
        _, _, _, runs, _, reviews = self.state()
        first = next(k for k in runs if k.startswith("XAUUSD:2026-01-15T13:30"))
        self.assertEqual(runs[first], ("FAILED", "ERROR"))
        review = next(r for r in reviews.values() if r["slot_key"] == first)
        self.assertEqual((review["error"], review["execution"]["execution_status"]), ("interrupted_run", "SUBMITTED"))
        self.assertEqual((len(orders), len(fills), list(account.open_positions)), (1, 1, ["XAUUSD"]))

    def test_journal_failure_inside_economic_save_rolls_back_everything(self):
        self.assertEqual(self.cycle(0), "PLAN_READY")
        before = self.state()[:3]
        injected, fired = fail_once(Store, "_event", predicate=lambda store, *a, **k: a[4] == "ORDER_FILLED")
        self.assertEqual(self.cycle(15, patches=(injected,)), "ERROR")
        self.assertTrue(fired["fired"])
        after = self.state()[:3]
        self.assertEqual([o.status for o in after[1].values()], ["PENDING"])  # the fill was rolled back atomically
        self.assertEqual((after[2], after[0].open_positions), ({}, {}))
        self.assertEqual(before[1].keys(), after[1].keys())
        self.cycle(30, recovery=0)  # a later cycle fills exactly once
        _, orders, fills = self.assert_paper_invariants()
        self.assertEqual(([o.status for o in orders.values()], len(fills)), (["FILLED"], 1))


class FillTransitionTests(Case):
    def test_submit_fill_then_terminal_and_restart_never_refills(self):
        self.assertEqual(self.cycle(0), "PLAN_READY")
        self.assertEqual([o.status for o in self.state()[1].values()], ["PENDING"])
        self.cycle(15)
        account, orders, fills = self.assert_paper_invariants()
        self.assertEqual(([o.status for o in orders.values()], len(fills), list(account.open_positions)),
                         (["FILLED"], 1, ["XAUUSD"]))
        for minutes, recovery in ((30, 120), (45, 0)):
            self.cycle(minutes, recovery=recovery)
        account, orders, fills = self.assert_paper_invariants()
        self.assertEqual((len(orders), len(fills), len(account.open_positions)), (1, 1, 1))
        self.assertEqual(self.cycle(45), "DUPLICATE")

    def test_terminal_rejected_and_cancelled_orders_are_never_progressed(self):
        store = Store(self.path)
        try:
            account = PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0)
            broker = PaperBroker(account)
            for status in ("REJECTED", "CANCELLED"):
                broker.orders[status] = PaperOrder("1.0", status, "run-" + status, "XAUUSD", "LONG", 1.0, 100.0, 90.0,
                                                   130.0, 1.0, 10000.0, 0.0, T - timedelta(minutes=5), status=status)
            store.save_paper(broker)
        finally:
            store.close()
        self.cycle(0)
        _, orders, fills = self.assert_paper_invariants()
        self.assertEqual({orders["REJECTED"].status, orders["CANCELLED"].status}, {"REJECTED", "CANCELLED"})
        self.assertEqual(sum(1 for o in orders.values() if o.status == "FILLED"), 0)


class SchedulerAndLockTests(Case):
    def test_same_slot_tick_twice_is_duplicate(self):
        config = RuntimeConfig(db_path=self.path, enabled_symbols=("XAUUSD",), market_provider_mode="twelve_data",
                               ai_provider_mode="openai", scheduler_enabled=True)
        runner = DemoRunner(config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()}, clock=lambda: T)
        try:
            with patched_scouts("LONG"):
                first = Scheduler(runner.runtime, lambda: T).tick()
                second = Scheduler(runner.runtime, lambda: T).tick()
            self.assertEqual((first, second), (["PLAN_READY"], ["DUPLICATE"]))
            self.assertEqual(len(runner.store.load_paper(ACCOUNT)[1]), 1)
        finally:
            runner.close()

    def test_recent_interrupted_run_keeps_lock_until_stale_threshold(self):
        runner = self.runner(0)
        try:
            with patched_scouts("LONG"), patch("runtime.service.run_floor", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):  # a hard interruption: finish never runs
                    runner.runtime.run_cycle("XAUUSD", T)
        finally:
            runner.close()
        self.assertEqual(self.state()[4], ["XAUUSD"])
        # A restart 1 minute later with the default 120 s threshold does NOT recover it: the symbol stays locked.
        early = DemoRunner(self.config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                           macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                           clock=lambda: T + timedelta(minutes=1))
        early.close()
        self.assertEqual(self.state()[4], ["XAUUSD"])
        self.assertEqual(self.cycle(15, recovery=120), "PLAN_READY")  # 15 min later: recovered, cycle runs
        self.assert_paper_invariants()


class ConflictTests(Case):
    def test_existing_position_and_pending_block_new_exposure(self):
        self.cycle(0)
        with patch("execution.paper_broker.PaperBroker.process_next_bar"):  # pending not progressed this cycle
            self.cycle(15)
        reviews = self.state()[5]
        self.assertIn("PENDING_ORDER", [r.get("execution", {}).get("execution_reason") for r in reviews.values()])
        self.cycle(30)  # fills
        self.cycle(45, side="SHORT")  # opposite setup
        account, orders, _ = self.assert_paper_invariants()
        reasons = [r.get("execution", {}).get("execution_reason") for r in self.state()[5].values()]
        self.assertIn("EXISTING_POSITION", reasons)
        self.assertEqual((len(orders), account.open_positions["XAUUSD"].side), (1, "LONG"))


class PaperSafetyTests(Case):
    def test_paper_only_real_off_nas100_off(self):
        from runtime import demo_runner
        self.assertFalse(demo_runner.REAL_EXECUTION_ENABLED)
        self.assertNotIn("NAS100", RuntimeConfig().enabled_symbols)
        from runtime.paper_contracts import paper_instruments
        self.assertNotIn("NAS100", paper_instruments())
        runtime = OperationalRuntime(self.config, market_provider=Data(), clock=lambda: T)
        try:
            with self.assertRaises(ValueError):
                runtime.run_cycle("NAS100", T)
        finally:
            runtime.close()
        brokers = [p for p in ROOT.rglob("*.py") if "test_" not in p.name and ".claude" not in str(p)
                   and "class " in p.read_text(encoding="utf-8")
                   and any(f"class {n}" in p.read_text(encoding="utf-8") for n in ("LiveBroker", "RealBroker", "OandaBroker"))]
        self.assertEqual(brokers, [])
        for flag in ("v2_position_catch_up", "v2_ai_call_audit", "v2_ai_resilience"):
            self.assertIs(getattr(RuntimeConfig.from_env(), flag), False)
        from runtime import cloud_runner
        with patch.dict("os.environ", {"RENDER": "", "AI_FLOOR_CLOUD_RUNNER": ""}):
            with self.assertRaisesRegex(RuntimeError, "not activated"):
                cloud_runner.main()


class RollbackCompatibilityTests(Case):
    """DEC-8.4: forward-incompatible rows fail closed; restore-from-copy is idempotent."""

    def test_unknown_future_field_fails_closed_at_startup(self):
        self.cycle(0)
        store = Store(self.path)
        try:
            row = store.db.execute("SELECT order_id,payload FROM paper_orders").fetchone()
            payload = json.loads(row[1])
            payload["future_identity_field"] = "x"  # what an older binary sees in a row written by newer code
            with store.transaction():
                store.db.execute("UPDATE paper_orders SET payload=? WHERE order_id=?", (json.dumps(payload), row[0]))
        finally:
            store.close()
        error = None
        try:
            OperationalRuntime(self.config, market_provider=Data(), clock=lambda: T + timedelta(minutes=15))
        except ValueError as exc:
            error = str(exc)
        self.assertEqual(error, "invalid paper payload")  # fail closed: no runtime, no cycle, no trading
        # LOW-8.5 (pinned): on this path __init__ does not close its Store; the connection is only released when the
        # half-built runtime is garbage-collected.
        import gc
        gc.collect()
        store = Store(self.path)
        try:
            self.assertIn("future_identity_field", store.db.execute("SELECT payload FROM paper_orders").fetchone()[0])
        finally:
            store.close()  # nothing rewritten or dropped

    def test_phase6_stamped_rows_are_read_by_current_code(self):
        self.cycle(0)
        store = Store(self.path)
        try:
            order = next(iter(store.load_paper(ACCOUNT)[1].values()))
            order.setup_id, order.risk_policy_version = "S-1", "V2_P5_RISK_1"
            with store.transaction():
                store.db.execute("UPDATE paper_orders SET payload=? WHERE order_id=?", (paper_encode(order), order.order_id))
            self.assertEqual(next(iter(store.load_paper(ACCOUNT)[1].values())).setup_id, "S-1")
        finally:
            store.close()

    def test_restore_from_copy_is_idempotent(self):
        self.cycle(0)
        copy = self.path.with_name(self.path.stem + "-restore.db")
        self.addCleanup(lambda: [Path(str(copy) + s).unlink(missing_ok=True) for s in ("", "-wal", "-shm")])
        store = Store(self.path)
        try:
            store.db.execute("PRAGMA wal_checkpoint(FULL)")
        finally:
            store.close()
        shutil.copyfile(self.path, copy)
        for target in (self.path, copy):
            self.assertEqual(self.cycle(15, path=target), "PLAN_READY")
            self.assertEqual(self.cycle(15, path=target), "DUPLICATE")
        original, restored = self.state()[:3], self.state(copy)[:3]
        self.assertEqual([(o.run_id, o.status) for o in original[1].values()],
                         [(o.run_id, o.status) for o in restored[1].values()])
        self.assert_paper_invariants(copy)


if __name__ == "__main__":
    unittest.main()
