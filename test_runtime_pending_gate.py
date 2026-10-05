"""V2 Phase 2 / B2.3C: runtime pending orders through CurrentCycleGate on the committed Evidence bar
(flag ON only). Child processes: ``python -m test_runtime_pending_gate child ...``."""
import os
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import runtime.service as service
from data.market_evidence import MarketEvidenceEngine
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.pending_order_gate import CurrentCycleGate
from runtime.service import EVIDENCE_UNAVAILABLE, STALE_PAPER_STATE
from storage.database import Store
from storage.evidence_store import EvidenceStore
from test_demo_runner import patched_scouts
from test_runtime_catch_up import (ACCOUNT, BASE, EUR_STOP, FIVE, ROOT, SLOT, T0, T1, T2, T3, T4, Bars,
                                   CatchUpCase, make_runtime)

T7 = T0 + 7 * FIVE  # Current bar of the 13:45 cycle.
LATER = SLOT + timedelta(minutes=15)
MISMATCH = {("XAUUSD", T4): (101.0, 101.5, 100.5, 101.0)}  # Committed T4 differs from the later snapshot.


def seed(db, *, as_of=T0, eur_position=False):
    store = Store(db)
    try:
        broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
        broker.orders["pend"] = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0,
                                           1.0, 10000.0, 0.0, as_of)
        if eur_position:
            order = PaperOrder("1.0", "ord-EURUSD", "run-EURUSD", "EURUSD", "LONG", 10000.0, 1.10, 1.045, 1.21,
                               1.0, 10000.0, 0.0, T0 - FIVE, status="FILLED")
            broker.orders[order.order_id] = order
            broker.account.open_positions["EURUSD"] = PaperPosition(
                "1.0", "pos-EURUSD", order.order_id, order.run_id, "EURUSD", "LONG", order.quantity, 1.10, 1.10,
                order.stop, order.target, T0, last_price=1.10, contract_multiplier=1.0)
        store.save_paper(broker)
    finally:
        store.close()


def fills_of(store_path):
    store = Store(store_path)
    try:
        return sorted((f.fill_timestamp, f.fill_price) for f in store.load_paper(ACCOUNT)[2].values())
    finally:
        store.close()


class GateCase(CatchUpCase):
    def cycle(self, slot=SLOT, *, healthy=True, **kwargs):
        runtime = self.runtime(slot, **kwargs)
        if healthy:
            with patched_scouts("LONG"):
                return runtime, runtime.run_cycle("XAUUSD", slot)
        real = service.run_ai  # The setup review is missing: the runtime's own ai_healthy is False.
        with patched_scouts("LONG"), patch.object(service, "run_ai",
                                                  lambda *a: replace(real(*a), ai_setup_review=None)):
            return runtime, runtime.run_cycle("XAUUSD", slot)

    def fresh(self):
        """New DB paths (subtests). Removal is registered before any runtime on them, so it runs after
        those runtimes close (cleanups are LIFO)."""
        tag = uuid4().hex
        self.db = Path("data/runtime") / f"b23c-test-{tag}.db"
        self.ev = Path("data/runtime") / f"b23c-evidence-{tag}.db"
        new = (self.db, self.ev)
        self.addCleanup(lambda: [p.with_name(p.name + x).unlink(missing_ok=True) for p in new for x in ("", "-wal", "-shm")])

    def spy_next_bar(self):
        seen, original = [], PaperBroker.process_next_bar

        def spy(broker, order, bar):
            seen.append((bar["timestamp"], bar["open"]))
            return original(broker, order, bar)
        return seen, patch.object(PaperBroker, "process_next_bar", spy)

    def order(self):
        return self.paper()[1]["pend"]

    def not_evaluated(self):
        return sorted(r["timestamp"] for r in self.journal("PENDING_NOT_EVALUATED"))

    def pre_ingest(self, overrides):
        store = EvidenceStore(self.ev)
        try:
            MarketEvidenceEngine(store).ingest_snapshot("XAUUSD", Bars(overrides).load_snapshot("XAUUSD", SLOT),
                                                        as_of=SLOT)
        finally:
            store.close()

    def gate_factory(self, **forced):
        built = []

        def factory(**values):
            values.update(forced)
            built.append(CurrentCycleGate(**values))
            return built[-1]
        return built, patch.object(service, "CurrentCycleGate", side_effect=factory)


class WiringTests(GateCase):
    def test_1_gate_built_from_real_cycle_values(self):
        seed(self.db)
        with patch.object(service, "gate_pending_orders", wraps=service.gate_pending_orders) as gate_call:
            runtime, status = self.cycle()
        gate = gate_call.call_args.kwargs["gate"]
        run = runtime.store.latest_run("XAUUSD")
        self.assertEqual((gate.run_id, gate.symbol, gate.paper_enabled, gate.execution_fresh, gate.session_open,
                          gate.ai_healthy, gate.ai_final_status),
                         (run["run_id"], "XAUUSD", True, True, True, True, status))
        self.assertEqual(gate.bar, {"symbol": "XAUUSD", "timestamp": T4, "open": 100.0, "high": 100.05,
                                    "low": 99.95, "close": 100.0, "is_closed": True})
        self.assertEqual(gate_call.call_args.kwargs["as_of"], SLOT)

    def test_2_3_intermediate_never_t4_only(self):
        seed(self.db)
        seen, spy = self.spy_next_bar()
        with spy:
            self.cycle()
        self.assertEqual(seen, [(T4, 100.0)])
        self.assertEqual((self.order().status, fills_of(self.db)), ("FILLED", [(T4, 100.0)]))
        self.assertEqual(self.not_evaluated(), [t.isoformat() for t in (T1, T2, T3)])

    def test_4_committed_evidence_price_wins_over_snapshot(self):
        seed(self.db)
        self.pre_ingest(MISMATCH)  # Committed T4 open 101; this cycle's snapshot says 100 (REVISION).
        seen, spy = self.spy_next_bar()
        with spy:
            self.cycle()
        self.assertEqual(seen, [(T4, 101.0)])
        self.assertEqual(fills_of(self.db), [(T4, 101.0)])
        store = EvidenceStore(self.ev, readonly=True)
        try:
            kinds = [r[0] for r in store.db.execute("SELECT kind FROM evidence_anomalies WHERE bar_start=?",
                                                     (T4.isoformat(),))]
        finally:
            store.close()
        self.assertEqual(kinds, ["REVISION"])

    def test_4b_flag_off_keeps_v1_snapshot_price(self):
        seed(self.db)
        seen, spy = self.spy_next_bar()
        with spy:
            self.cycle(flag=False, overrides=MISMATCH)
        self.assertEqual(seen, [(T4, 101.0)])  # V1 path: the snapshot bar it was given, unchanged.
        self.assertEqual(self.journal("PENDING_NOT_EVALUATED"), [])
        self.assertFalse(self.ev.exists())

    def test_7_order_as_of_protection(self):
        seed(self.db, as_of=T4)
        seen, spy = self.spy_next_bar()
        with spy:
            self.cycle()
        self.assertEqual((seen, self.order().status, self.not_evaluated()), ([], "PENDING", []))

    def test_8_restart_exactly_once_and_no_duplicate_rows(self):
        seed(self.db)
        self.cycle(healthy=False)  # AI review unhealthy: gate fails, nothing evaluated.
        self.assertEqual(self.not_evaluated(), [t.isoformat() for t in (T1, T2, T3, T4)])
        self.cycle(LATER, healthy=False)
        self.assertEqual(self.not_evaluated(), [(T0 + i * FIVE).isoformat() for i in range(1, 8)])
        self.assertEqual(self.order().status, "PENDING")
        third = LATER + timedelta(minutes=15)
        self.cycle(third)
        fills = fills_of(self.db)
        self.assertEqual(fills, [(third - FIVE, 100.0)])  # The 14:00 cycle's own bar, never an older one.
        self.assertEqual(self.cycle(third)[1], "DUPLICATE")
        self.cycle(third + timedelta(minutes=15))
        self.assertEqual(fills_of(self.db), fills)
        self.assertEqual(len(self.journal("ORDER_FILLED")), 1)
        rows = self.not_evaluated()
        self.assertEqual(len(rows), len(set(rows)))


class FailClosedTests(GateCase):
    def assert_untouched(self, seen):
        self.assertEqual(seen, [])
        self.assertEqual((self.order().status, fills_of(self.db)), ("PENDING", []))

    def test_5_missing_committed_current_bar_no_fallback(self):
        seed(self.db)
        self.pre_ingest({})  # Evidence through T4 ... then hide this cycle's bar by a no-op ingest:
        later = LATER  # The 13:45 cycle's current bar (13:40) is never committed.
        seen, spy = self.spy_next_bar()
        with spy, patch.object(MarketEvidenceEngine, "ingest_snapshot", return_value={}):
            runtime, _ = self.cycle(later)
        self.assert_untouched(seen)
        run = runtime.store.latest_run("XAUUSD")
        self.assertEqual(runtime.store.review_report(run["run_id"])["execution"]["execution_reason"],
                         EVIDENCE_UNAVAILABLE)
        self.assertEqual(len(self.journal(EVIDENCE_UNAVAILABLE)), 1)

    def test_5b_corrupt_evidence_store(self):
        seed(self.db)
        self.ev.write_bytes(b"corrupt" * 512)
        seen, spy = self.spy_next_bar()
        with spy:
            self.cycle()
        self.assert_untouched(seen)

    def test_6_missing_or_invalid_gate_values_fail_closed(self):
        for forced in ({"ai_final_status": None}, {"ai_final_status": ""}, {"ai_healthy": "yes"},
                       {"session_open": None}, {"execution_fresh": 1}):
            with self.subTest(**{k: repr(v) for k, v in forced.items()}):
                self.fresh()
                seed(self.db)
                built, factory = self.gate_factory(**forced)
                seen, spy = self.spy_next_bar()
                with spy, factory:
                    self.cycle()
                self.assertEqual(len(built), 1)
                self.assert_untouched(seen)
                self.assertEqual(self.not_evaluated(), [t.isoformat() for t in (T1, T2, T3, T4)])

    def test_11_stale_reload_bounded_retry_fills_once(self):
        seed(self.db)
        other, original, raced = Store(self.db), Store.save_paper, []
        self.addCleanup(other.close)

        def racing(store, broker, **kwargs):
            if kwargs.get("expected_state") is not None and any(e.event_type == "ORDER_FILLED" for e in broker.journal):
                raced.append(1)
                if len(raced) == 1:
                    noise(other)
            return original(store, broker, **kwargs)
        with patch.object(Store, "save_paper", racing), \
                patch.object(service, "gate_pending_orders", wraps=service.gate_pending_orders) as gate_call:
            self.cycle()
        self.assertEqual((len(raced), gate_call.call_count), (2, 2))  # STALE once, fresh retry once.
        self.assertEqual(fills_of(self.db), [(T4, 100.0)])
        self.assertIn("noise", self.paper()[1])
        self.assertEqual(len(self.journal("ORDER_FILLED")), 1)

    def test_12_persistent_stale_no_economics(self):
        seed(self.db)
        other, original, raced = Store(self.db), Store.save_paper, []
        self.addCleanup(other.close)

        def racing(store, broker, **kwargs):
            if kwargs.get("expected_state") is not None and any(e.event_type == "ORDER_FILLED" for e in broker.journal):
                raced.append(1)
                noise(other, f"noise-{len(raced)}")
            return original(store, broker, **kwargs)
        with patch.object(Store, "save_paper", racing):
            runtime, _ = self.cycle()
        self.assertEqual(len(raced), 2)
        self.assertEqual((self.order().status, fills_of(self.db)), ("PENDING", []))
        run = runtime.store.latest_run("XAUUSD")
        self.assertEqual(runtime.store.review_report(run["run_id"])["execution"]["execution_reason"], STALE_PAPER_STATE)
        self.assertEqual(self.journal("ORDER_FILLED"), [])


def noise(store, name="noise"):
    account, orders, fills = store.load_paper(ACCOUNT)
    broker = PaperBroker(account)
    broker.orders, broker.fills = orders, fills
    broker.orders[name] = PaperOrder("1.0", name, f"run-{name}", "EURUSD", "LONG", 1.0, 1.1, 1.0, 1.5, 1.0,
                                     10000.0, 0.0, T0, status="CANCELLED")
    store.save_paper(broker)


class ProcessTests(GateCase):
    def crash(self, when):
        seed(self.db)
        child = self.child_proc("crash", self.db, self.ev, when)
        self.assertEqual(child.wait(timeout=120), 9)

    def child_proc(self, *args):
        return subprocess.Popen([sys.executable, "-m", "test_runtime_pending_gate", "child", *map(str, args)], cwd=ROOT)

    def test_9_crash_before_save_later_cycle_fills_once_on_its_own_bar(self):
        for when in ("before", "during"):
            with self.subTest(when=when):
                self.fresh()
                self.crash(when)
                self.assertEqual((self.order().status, fills_of(self.db)), ("PENDING", []))
                self.cycle(LATER, recovery=0)
                self.assertEqual(fills_of(self.db), [(T7, 100.0)])  # Never retroactively at T4.
                self.assertEqual(len(self.journal("ORDER_FILLED")), 1)

    def test_10_crash_after_save_no_duplicate(self):
        self.crash("after")
        self.assertEqual(fills_of(self.db), [(T4, 100.0)])
        self.cycle(LATER, recovery=0)
        self.assertEqual(fills_of(self.db), [(T4, 100.0)])
        self.assertEqual(len(self.journal("ORDER_FILLED")), 1)

    def test_13_cross_symbol_real_processes(self):
        """A (XAUUSD) has computed its gated fill and waits before saving; B (EURUSD) closes its position;
        A goes STALE, reloads, fills once, and B's close survives."""
        seed(self.db, eur_position=True)
        signal = Path("data/runtime") / f"b23c-signal-{uuid4().hex}"
        self.addCleanup(lambda: [Path(f"{signal}.{s}").unlink(missing_ok=True) for s in ("a", "b", "stale")])
        a = self.child_proc("cycle", self.db, self.ev, "XAUUSD", signal)
        deadline = time.time() + 60
        while not Path(f"{signal}.a").exists():
            self.assertLess(time.time(), deadline)
            time.sleep(.05)
        b = self.child_proc("cycle", self.db, self.ev, "EURUSD", "-")
        self.assertEqual(b.wait(timeout=120), 0)
        Path(f"{signal}.b").write_text("go")
        self.assertEqual(a.wait(timeout=120), 0)
        self.assertTrue(Path(f"{signal}.stale").exists())
        account, orders, _ = self.paper()
        self.assertEqual((orders["pend"].status, fills_of(self.db)), ("FILLED", [(T4, 100.0)]))
        self.assertEqual([t.symbol for t in account.closed_trades], ["EURUSD"])
        self.assertEqual(list(account.open_positions), ["XAUUSD"])  # EUR not resurrected.
        self.assertEqual(account.realized_pnl, account.closed_trades[0].net_pnl)
        self.assertEqual(len(self.journal("ORDER_FILLED")), 1)


def _is_gate_fill(kwargs, broker):
    return kwargs.get("expected_state") is not None and any(e.event_type == "ORDER_FILLED" for e in broker.journal)


def _child(argv):
    mode = argv[0]
    if mode == "crash":
        db, ev, when = argv[1:]
        original = Store.save_paper

        def crashing(store, broker, **kwargs):
            if _is_gate_fill(kwargs, broker):
                if when == "before":
                    os._exit(9)
                if when == "during":
                    store._event = lambda *a, **k: os._exit(9)  # Inside BEGIN IMMEDIATE, before COMMIT.
                result = original(store, broker, **kwargs)
                os._exit(9)
            return original(store, broker, **kwargs)
        with patched_scouts("LONG"), patch.object(Store, "save_paper", crashing):
            make_runtime(db, ev, SLOT).run_cycle("XAUUSD", SLOT)
        os._exit(0)
    db, ev, symbol, signal = argv[1:]
    runtime = make_runtime(db, ev, SLOT, overrides=EUR_STOP)
    original, state = Store.save_paper, {"waited": False}

    def interleaved(store, broker, **kwargs):
        if signal != "-" and _is_gate_fill(kwargs, broker) and not state["waited"]:
            state["waited"] = True
            Path(f"{signal}.a").write_text("computed")
            deadline = time.time() + 60
            while not Path(f"{signal}.b").exists() and time.time() < deadline:
                time.sleep(.05)
        result = original(store, broker, **kwargs)
        if signal != "-" and result is False:
            Path(f"{signal}.stale").write_text("stale")
        return result
    with patched_scouts("LONG"), patch.object(Store, "save_paper", interleaved):
        runtime.run_cycle(symbol, SLOT)
    runtime.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        _child(sys.argv[2:])
    else:
        unittest.main()
