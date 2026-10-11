"""V2 Phase 8 / P8.0: characterization of CURRENT execution / fill / recovery behavior (main 2753130). Read-only.

Pins behavior not yet covered elsewhere so every P8.1 difference is an explicit, Owner-approved change.
"""
import sqlite3
import unittest
from datetime import timedelta
from unittest.mock import patch

from ai.provider import DeterministicAIProvider, FakeAIProvider
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from runtime.demo_runner import DemoRunner
from runtime.service import OperationalRuntime
from storage.database import Store
import test_demo_runner as fixture
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts

ACCOUNT = "paper-main"


class Case(unittest.TestCase):
    setUp = fixture.TestDemoRunner.setUp
    tearDown = fixture.TestDemoRunner.tearDown

    def runner(self, minutes=0, ai=None, **kwargs):
        return DemoRunner(self.config, market_provider=Data(), ai_provider=ai or DeterministicAIProvider(),
                          macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                          clock=lambda: T + timedelta(minutes=minutes), **kwargs)

    def cycle(self, minutes, ai=None, side="LONG"):
        runner = self.runner(minutes, ai)
        try:
            with patched_scouts(side):
                return runner.run_once("XAUUSD", T + timedelta(minutes=minutes))
        finally:
            runner.close()

    def paper(self):
        store = Store(self.path)
        try:
            return store.load_paper(ACCOUNT)
        finally:
            store.close()


class DatabaseFailureTests(Case):
    """F08-T15 / F08-T16: a DB failure inside finish() escapes run_cycle (fail-stop) and leaves the symbol lock until
    recovery; recovery at the next process start releases it. No order is created on any failed path."""

    def test_finish_failure_is_fail_stop_and_recovered_on_restart(self):
        runner = self.runner(0)
        try:
            with patched_scouts("LONG"), patch("runtime.service.run_floor", side_effect=RuntimeError("analysis")), \
                    patch.object(Store, "finish", side_effect=sqlite3.OperationalError("disk I/O error")):
                with self.assertRaises(sqlite3.OperationalError):
                    runner.runtime.run_cycle("XAUUSD", T)  # escapes: the cycle cannot record its own failure
            with patched_scouts("LONG"):
                later = runner.runtime.run_cycle("XAUUSD", T + timedelta(minutes=15))
            self.assertEqual(later, "DUPLICATE")  # stale symbol lock blocks the symbol in this process
        finally:
            runner.close()
        self.assertEqual(self.paper()[1], {})  # no order anywhere
        restarted = self.runner(30, recovery_stale_after_seconds=0)  # cloud runner restart semantics
        try:
            with patched_scouts("LONG"):
                result = restarted.run_once("XAUUSD", T + timedelta(minutes=30))
            self.assertEqual(result["status"], "PLAN_READY")
            runs = {r["status"] for r in restarted.store.db.execute("SELECT status FROM runs")}
            self.assertIn("FAILED", runs)  # the interrupted run is recorded as FAILED by recover()
        finally:
            restarted.close()


class StartupRecoveryTests(Case):
    """F08-T09 / F08-T17: startup refuses inconsistent PAPER state instead of trading on it."""

    def seed(self, positions=(), orders=(), account=True):
        store = Store(self.path)
        try:
            held = PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0) if account else None
            broker = PaperBroker(held or PaperAccount("1.0", "other", 1.0, 1.0, 1.0))
            for order in orders:
                broker.orders[order.order_id] = order
            for position in positions:
                broker.account.open_positions[position.symbol] = position
            store.save_paper(broker)
        finally:
            store.close()

    def test_orphan_position_without_filled_origin_order_blocks_startup(self):
        position = PaperPosition("1.0", "pos-1", "missing-order", "run-x", "XAUUSD", "LONG", 1.0, 100.0, 100.0, 95.0,
                                 115.0, T, contract_multiplier=1.0)
        self.seed(positions=[position])
        with self.assertRaisesRegex(RuntimeError, "inconsistent paper position"):
            OperationalRuntime(self.config, market_provider=Data(), clock=lambda: T)
        store = Store(self.path)
        try:
            events = [r[0] for r in store.db.execute("SELECT payload FROM journal WHERE event_type='STATE_INCONSISTENCY'")]
            self.assertTrue(any("orphan_position" in e for e in events))
            self.assertEqual(list(store.load_paper(ACCOUNT)[0].open_positions), ["XAUUSD"])  # nothing mutated
        finally:
            store.close()

    def test_quantity_mismatch_with_origin_order_blocks_startup(self):
        order = PaperOrder("1.0", "ord-1", "run-x", "XAUUSD", "LONG", 2.0, 100.0, 95.0, 115.0, 1.0, 10000.0, 0.0, T,
                           status="FILLED")
        position = PaperPosition("1.0", "pos-1", "ord-1", "run-x", "XAUUSD", "LONG", 1.0, 100.0, 100.0, 95.0, 115.0, T,
                                 contract_multiplier=1.0)
        self.seed(positions=[position], orders=[order])
        with self.assertRaisesRegex(RuntimeError, "inconsistent paper position"):
            OperationalRuntime(self.config, market_provider=Data(), clock=lambda: T)


class PendingLifetimeTests(Case):
    """F08-T02: a PENDING order has no time-in-force. While the cycle's AI is unhealthy it is not progressed (certified
    Phase 2 / DEC-7.11); it stays PENDING and may fill on a much later current-cycle bar (V1 fill gate still applies)."""

    def test_pending_survives_ai_outage_and_fills_later(self):
        first = self.cycle(0)
        self.assertEqual(first["status"], "PLAN_READY")
        down = FakeAIProvider(raises=RuntimeError("provider down"))
        for minutes in (15, 30, 45, 60):  # one hour of AI outage
            self.cycle(minutes, ai=down)
        _, orders, _ = self.paper()
        self.assertEqual([o.status for o in orders.values()], ["PENDING"])  # no expiry, no cancel
        self.cycle(75)  # AI healthy again
        account, orders, fills = self.paper()
        order = next(iter(orders.values()))
        self.assertEqual(order.status, "FILLED")
        fill = next(iter(fills.values()))
        self.assertGreaterEqual(fill.fill_timestamp - order.as_of, timedelta(minutes=70))  # filled > 1 h after as_of
        self.assertIn("XAUUSD", account.open_positions)


if __name__ == "__main__":
    unittest.main()
