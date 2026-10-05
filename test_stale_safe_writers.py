"""V2 Phase 2 / B2.3A: every runtime PAPER writer persists only over the state it computed from."""
import ast
import subprocess
import sys
import textwrap
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pandas as pd

from ai.provider import DeterministicAIProvider
from core.contracts import InstrumentSpec
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from runtime import health_hooks
from runtime.config import RuntimeConfig
from runtime.service import STALE_PAPER_STATE, OperationalRuntime
from storage.database import SCHEMA_VERSION, Store
from test_demo_runner import T, instrument, macro_fixture, patched_scouts

ROOT = Path(__file__).resolve().parent
ACCOUNT = "paper-main"
EUR = InstrumentSpec("EURUSD", "FOREX", "EUR/USD", "UTC", .0001, .0001, 1, ("1h", "15m", "5m"))


class Data:
    """Fresh closed bars at the slot; ``closes`` maps symbol -> close (tight high/low)."""

    def __init__(self, closes):
        self.closes = closes

    def load_snapshot(self, symbol, at):
        close = float(self.closes[symbol])
        spread = close * .001
        return {tf: pd.DataFrame({"Open": [close], "High": [close + spread], "Low": [close - spread],
                                  "Close": [close], "symbol": [symbol], "is_closed": [True]},
                                 index=pd.DatetimeIndex([at])) for tf in ("1h", "15m", "5m")}


def filled(order_id, symbol, entry, stop, target, quantity):
    return PaperOrder("1.0", order_id, f"run-{order_id}", symbol, "LONG", quantity, entry, stop, target,
                      1.0, 10000.0, 0.0, T - timedelta(hours=2), status="FILLED")


def position(order, entry):
    return PaperPosition("1.0", f"pos-{order.order_id}", order.order_id, order.run_id, order.symbol, "LONG",
                         order.quantity, entry, entry, order.stop, order.target, T - timedelta(hours=1),
                         last_price=entry, contract_multiplier=1.0)


class StaleSafeCase(unittest.TestCase):
    def setUp(self):
        self.path = Path("data/runtime") / f"b23a-test-{uuid4().hex}.db"
        self.config = RuntimeConfig(db_path=self.path, enabled_symbols=("XAUUSD", "EURUSD"))
        self.closes = {"XAUUSD": 100.0, "EURUSD": 1.10}
        self.now = [T]
        self.addCleanup(self.remove_db)  # Registered first: runs after every connection is closed.

    def remove_db(self):
        for suffix in ("", "-wal", "-shm"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def runtime(self):
        runtime = OperationalRuntime(self.config, market_provider=Data(self.closes),
                                     ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                                     instruments={"XAUUSD": instrument(), "EURUSD": EUR},
                                     clock=lambda: self.now[0])
        self.addCleanup(runtime.close)
        return runtime

    def other(self):
        """Another process's connection to the same trading DB."""
        store = Store(self.path)
        self.addCleanup(store.close)
        return store

    def seed(self, *, orders=(), positions=(), equity=10000.0):
        store = Store(self.path)
        try:
            broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, equity, equity))
            broker.orders = {o.order_id: o for o in orders}
            broker.account.open_positions = {p.symbol: p for p in positions}
            store.save_paper(broker)
        finally:
            store.close()

    def write(self, store, mutate):
        """Unguarded write by the other process (models any concurrent PAPER writer)."""
        account, orders, fills = store.load_paper(ACCOUNT)
        broker = PaperBroker(account)
        broker.orders, broker.fills = orders, fills
        mutate(broker)
        store.save_paper(broker)

    def paper(self):
        store = Store(self.path)
        try:
            return store.load_paper(ACCOUNT)
        finally:
            store.close()

    def events(self, event_type):
        store = Store(self.path)
        try:
            return store.journal(event=event_type, limit=10000)
        finally:
            store.close()

    def execution(self, runtime, symbol="XAUUSD"):
        run = runtime.store.latest_run(symbol)
        return runtime.store.review_report(run["run_id"])["execution"]


class BootstrapTests(StaleSafeCase):
    def test_a_concurrent_bootstrap_never_overwrites_the_first_account(self):
        original, raced = Store.save_paper, []

        def racing(store, broker, **kwargs):
            if kwargs.get("expected_state") is not None and not raced:
                raced.append(1)  # The other process creates (and trades on) the account first.
                other = Store(self.path)
                try:
                    winner = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10250.0, 10250.0, 250.0))
                    original(other, winner)
                finally:
                    other.close()
            return original(store, broker, **kwargs)

        with patch.object(Store, "save_paper", racing):
            self.runtime()
        account = self.paper()[0]
        self.assertEqual(raced, [1])
        self.assertEqual((account.equity, account.realized_pnl), (10250.0, 250.0))

    def test_a_uncontended_bootstrap_unchanged(self):
        self.runtime()
        account = self.paper()[0]
        self.assertEqual((account.starting_equity, account.equity, account.realized_pnl), (10000.0, 10000.0, 0.0))


class LegacyWriterTests(StaleSafeCase):
    def seed_two_positions(self):
        xau, eur = filled("xau", "XAUUSD", 100.0, 90.0, 130.0, 1.0), filled("eur", "EURUSD", 1.10, 1.09, 1.14, 1000.0)
        self.seed(orders=(xau, eur), positions=(position(xau, 100.0), position(eur, 1.10)))

    def test_i_xau_eur_two_writers_no_lost_update(self):
        """Process A (XAUUSD) loads PAPER state; process B (EURUSD) closes its position and commits;
        A must not resurrect B's position or revert B's realized PnL."""
        self.seed_two_positions()
        self.closes["EURUSD"] = 1.08  # Below the EUR stop: B closes.
        a, b = self.runtime(), self.runtime()  # Separate SQLite connections to one DB file.
        original, interleaved = TradeManager.process_bar, []

        def a_then_b(manager, bar):
            if bar["symbol"] == "XAUUSD" and not interleaved:
                interleaved.append(1)
                b.run_cycle("EURUSD", T)  # B commits after A loaded its state.
            return original(manager, bar)

        with patch.object(TradeManager, "process_bar", a_then_b):
            a.run_cycle("XAUUSD", T)
        account, orders, _ = self.paper()
        self.assertEqual(interleaved, [1])
        self.assertNotIn("EURUSD", account.open_positions)  # Not resurrected.
        self.assertEqual([t.symbol for t in account.closed_trades], ["EURUSD"])
        self.assertEqual(account.realized_pnl, account.closed_trades[0].net_pnl)
        self.assertLess(account.realized_pnl, 0)
        self.assertEqual(account.open_positions["XAUUSD"].last_processed_at, T)  # A's update kept too.
        self.assertEqual(len(self.events("POSITION_CLOSED")), 1)
        self.assertEqual(sorted(o.status for o in orders.values()), ["FILLED", "FILLED"])

    def test_legacy_position_writer_bounded_then_fails_closed(self):
        self.seed_two_positions()
        runtime, other, calls = self.runtime(), self.other(), []
        original = TradeManager.process_bar

        def always_raced(manager, bar):
            calls.append(bar["symbol"])
            self.write(other, lambda broker: broker.orders.__setitem__(
                f"noise-{len(calls)}", PaperOrder("1.0", f"noise-{len(calls)}", "r", "EURUSD", "LONG", 1.0, 1.1, 1.0,
                                                  1.5, 1.0, 10000.0, 0.0, T, status="CANCELLED")))
            return original(manager, bar)

        with patch.object(TradeManager, "process_bar", always_raced):
            self.assertEqual(runtime.run_cycle("XAUUSD", T), "ERROR")
        self.assertEqual(calls, ["XAUUSD", "XAUUSD"])  # One retry only.
        account = self.paper()[0]
        self.assertIsNone(account.open_positions["XAUUSD"].last_processed_at)  # Nothing written.
        self.assertEqual(sorted(o for o in self.paper()[1] if o.startswith("noise")), ["noise-1", "noise-2"])

    def test_pending_writer_stale_retries_once_without_duplicate_fill(self):
        order = PaperOrder("1.0", "pend", "run-p", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0, 1.0, 10000.0, 0.0,
                           T - timedelta(hours=1))
        self.seed(orders=(order,))
        runtime, other, calls = self.runtime(), self.other(), []
        original = PaperBroker.process_next_bar

        def raced_once(broker, pending, bar):
            calls.append(pending.order_id)
            if len(calls) == 1:
                self.write(other, lambda b: b.orders.__setitem__("noise", PaperOrder(
                    "1.0", "noise", "r", "EURUSD", "LONG", 1.0, 1.1, 1.0, 1.5, 1.0, 10000.0, 0.0, T, status="CANCELLED")))
            return original(broker, pending, bar)

        with patched_scouts("LONG"), patch.object(PaperBroker, "process_next_bar", raced_once):
            runtime.run_cycle("XAUUSD", T)  # Healthy AI review: the pending order may progress.
        account, orders, fills = self.paper()
        self.assertEqual(calls, ["pend", "pend"])
        self.assertEqual((orders["pend"].status, len(fills), list(account.open_positions)), ("FILLED", 1, ["XAUUSD"]))
        self.assertIn("noise", orders)
        self.assertEqual(len(self.events("ORDER_FILLED")), 1)


class SubmitTests(StaleSafeCase):
    def submit_with(self, interfere):
        """Run one submitting XAUUSD cycle; ``interfere(attempt, other)`` runs inside each submit attempt,
        after the fresh load and before the guarded save."""
        self.seed()
        runtime, other, attempts = self.runtime(), self.other(), []
        original = PaperBroker.submit_plan

        def racing(broker, *args):
            attempts.append(1)
            interfere(len(attempts), other)
            return original(broker, *args)

        with patched_scouts("LONG"), patch.object(PaperBroker, "submit_plan", racing):
            status = runtime.run_cycle("XAUUSD", T)
        return runtime, status, len(attempts)

    def noise(self, other, name, symbol="EURUSD", status="CANCELLED"):
        self.write(other, lambda b: b.orders.__setitem__(name, PaperOrder(
            "1.0", name, f"run-{name}", symbol, "LONG", 1.0, 100.0, 95.0, 130.0, 1.0, 10000.0, 0.0, T, status=status)))

    def xau_orders(self):
        return [o for o in self.paper()[1].values() if o.symbol == "XAUUSD"]

    def test_d_stale_then_fresh_reload_submits_once(self):
        runtime, status, attempts = self.submit_with(
            lambda n, other: self.noise(other, "noise") if n == 1 else None)
        self.assertEqual((status, attempts), ("PLAN_READY", 2))
        self.assertEqual(self.execution(runtime)["execution_status"], "SUBMITTED")
        self.assertEqual(len(self.xau_orders()), 1)
        self.assertIn("noise", self.paper()[1])  # The other writer's update is kept.
        self.assertEqual(len(self.events("ORDER_SUBMITTED")), 1)  # Discarded attempt left no journal.

    def test_e_bounded_retry_then_stale_paper_state(self):
        runtime, _, attempts = self.submit_with(lambda n, other: self.noise(other, f"noise-{n}"))
        self.assertEqual(attempts, 2)
        outcome = self.execution(runtime)
        self.assertEqual((outcome["execution_status"], outcome["execution_reason"]), ("SKIPPED", STALE_PAPER_STATE))
        self.assertEqual(self.xau_orders(), [])
        self.assertEqual(self.events("ORDER_SUBMITTED"), [])
        self.assertEqual(len(self.events(STALE_PAPER_STATE)), 1)
        # K: a later cycle (restart, next slot) submits normally.
        self.now[0] = T + timedelta(minutes=15)
        with patched_scouts("LONG"):
            self.runtime().run_cycle("XAUUSD", T + timedelta(minutes=15))
        self.assertEqual(len(self.xau_orders()), 1)

    def test_g_changed_equity_fails_closed_without_rerunning_risk(self):
        def lose(n, other):
            if n == 1:
                self.write(other, lambda b: (setattr(b.account, "equity", 9900.0),
                                             setattr(b.account, "realized_pnl", -100.0)))
        with patch("runtime.service.run_floor", wraps=__import__("runtime.service").service.run_floor) as floor:
            runtime, _, attempts = self.submit_with(lose)
        self.assertEqual((attempts, floor.call_count), (1, 1))  # No second submit_plan, no Risk rerun.
        self.assertEqual(self.execution(runtime)["execution_reason"], STALE_PAPER_STATE)
        self.assertEqual(self.xau_orders(), [])
        self.assertEqual((self.paper()[0].equity, self.paper()[0].realized_pnl), (9900.0, -100.0))  # L.

    def test_f_h_changed_eligibility_never_duplicates_order(self):
        runtime, _, attempts = self.submit_with(
            lambda n, other: self.noise(other, "theirs", "XAUUSD", "PENDING") if n == 1 else None)
        self.assertEqual(attempts, 1)
        self.assertEqual(self.execution(runtime)["execution_reason"], STALE_PAPER_STATE)
        self.assertEqual([o.order_id for o in self.xau_orders()], ["theirs"])


class SingleWriterTests(StaleSafeCase):
    def test_b_m_single_writer_all_saves_guarded_never_stale(self):
        self.seed()
        original, calls = Store.save_paper, []

        def spy(store, broker, **kwargs):
            result = original(store, broker, **kwargs)
            calls.append((kwargs.get("expected_state") is not None, result))
            return result

        runtime = self.runtime()
        with patch.object(Store, "save_paper", spy):
            for minutes, close in ((0, 100.0), (15, 100.0), (30, 100.0)):
                self.now[0] = T + timedelta(minutes=minutes)
                self.closes["XAUUSD"] = close
                with patched_scouts("LONG"):
                    runtime.run_cycle("XAUUSD", self.now[0])
        account, orders, fills = self.paper()
        self.assertTrue(calls)
        self.assertEqual(calls, [(True, None)] * len(calls))  # Guarded, and never STALE.
        self.assertEqual(([o.status for o in orders.values()], len(fills), list(account.open_positions)),
                         (["FILLED"], 1, ["XAUUSD"]))
        self.assertEqual(account.open_positions["XAUUSD"].last_processed_at, T + timedelta(minutes=30))
        self.assertEqual(self.events(STALE_PAPER_STATE), [])


class AtomicityAndScopeTests(StaleSafeCase):
    def test_c_j_crash_inside_guarded_transaction_writes_nothing(self):
        self.seed()
        script = textwrap.dedent(f"""
            import os, sys
            sys.path.insert(0, {str(ROOT)!r})
            from execution.paper_broker import PaperBroker
            from storage.database import Store
            store = Store({str(self.path.resolve())!r})
            account, orders, fills = store.load_paper({ACCOUNT!r})
            broker = PaperBroker(account)
            broker.orders, broker.fills = orders, fills
            expected = store.paper_state(account, orders, fills)
            account.equity = 1.0
            broker._event(None, "crash", "XAUUSD", "crash", "CRASH_PROBE")
            store._event = lambda *a, **k: os._exit(7)  # Die after the account write, before COMMIT.
            store.save_paper(broker, expected_state=expected)
        """)
        child = subprocess.run([sys.executable, "-c", script], cwd=ROOT, timeout=60)
        self.assertEqual(child.returncode, 7)
        account, orders, fills = self.paper()
        self.assertEqual(account.equity, 10000.0)
        store = self.other()
        broker = PaperBroker(account)
        expected = store.paper_state(account, orders, fills)
        account.equity = 10001.0
        self.assertIsNone(store.save_paper(broker, expected_state=expected))  # DB usable; guard passes.
        self.assertEqual(self.paper()[0].equity, 10001.0)

    def test_p_q_r_s_scope_schema_wiring_health(self):
        self.runtime()
        self.assertEqual(SCHEMA_VERSION, 3)
        store = self.other()
        self.assertEqual(store.db.execute("SELECT version FROM schema_info").fetchone()[0], 3)
        imports = set()
        for node in ast.walk(ast.parse((ROOT / "runtime" / "service.py").read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                imports.add(node.module)
            elif isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
        for module in ("data.market_evidence", "storage.evidence_store", "execution.position_catch_up",
                       "execution.pending_order_gate"):
            self.assertNotIn(module, imports)
        self.assertIsNone(health_hooks._sink)
        self.assertNotIn("NAS100", self.config.enabled_symbols)
        self.assertIn('"paper_mode": True', (ROOT / "runtime" / "config.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
