"""V2 Phase 8 / P1-B (DEC-8.14, DEC-8.15): per-symbol runtime paths, observe-only evidence outside the scope.

Twin temporary databases: A = catch-up scope XAUUSD only; B = flag OFF (legacy). EURUSD (outside the scope) must keep
the legacy newest-bar economics byte-for-byte; XAUUSD (inside) uses catch-up. Test scenarios only: nothing operational.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from ai.provider import DeterministicAIProvider
from data.market_evidence import MarketEvidenceEngine
from runtime import revision_review
from runtime.config import RuntimeConfig
from runtime.revision_review import demo_gate, record_review
from runtime.service import (EVIDENCE_BUSY_TIMEOUT_MS, EVIDENCE_OBSERVE_FAILED, EVIDENCE_OBSERVE_SKIPPED_BUSY,
                             OperationalRuntime)
from execution.contracts import PaperAccount, PaperOrder
from execution.paper_broker import PaperBroker
from storage.database import Store
from test_demo_runner import instrument, macro_fixture
from test_runtime_catch_up import ACCOUNT, EUR, FIVE, SLOT, T0, T2, T3, Bars, seed

XAU_STOP_T3 = {("XAUUSD", T3): (100.0, 100.5, 94.0, 96.0)}   # intermediate XAU SL touch (newest bar T4 is flat)
EUR_STOP_T2 = {("EURUSD", T2): (1.10, 1.101, 1.04, 1.05)}    # intermediate EUR SL touch the legacy path misses
ECONOMIC_EVENTS = {"ORDER_SUBMITTED", "ORDER_FILLED", "ORDER_REJECTED", "ORDER_CANCELLED", "POSITION_OPENED",
                   "STOP_HIT", "TARGET_HIT", "POSITION_CLOSED"}


def config(db, ev, scope):
    if not scope:
        return RuntimeConfig(db_path=Path(db), enabled_symbols=("XAUUSD", "EURUSD"))
    return RuntimeConfig(db_path=Path(db), enabled_symbols=("XAUUSD", "EURUSD"), v2_position_catch_up=True,
                         v2_position_catch_up_symbols=scope, market_evidence_path=Path(ev))


def make(db, ev, slot, scope, overrides=None):
    return OperationalRuntime(config(db, ev, scope), market_provider=Bars(overrides),
                              ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                              instruments={"XAUUSD": instrument(), "EURUSD": EUR}, clock=lambda: slot,
                              recovery_stale_after_seconds=120)


def economics(db, symbol):
    """Everything economic about ``symbol``: position, orders, fills, closed trades, economic journal events."""
    store = Store(db)
    try:
        account, orders, fills = store.load_paper(ACCOUNT)
        events = [(r["event_type"], r["timestamp"], r["source"], r["payload"]) for r in store.db.execute(
            "SELECT event_type, timestamp, source, payload FROM journal WHERE symbol=? ORDER BY id", (symbol,))
            if r["event_type"] in ECONOMIC_EVENTS]
        return {"position": account.open_positions.get(symbol),
                "orders": sorted((o.order_id, o) for o in orders.values() if o.symbol == symbol),
                "fills": sorted((f.fill_id, f) for f in fills.values() if f.symbol == symbol),
                "closed": [t for t in account.closed_trades if t.symbol == symbol], "events": events}
    finally:
        store.close()


class Twins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p1b-run-")
        self.addCleanup(self.tmp.cleanup)
        d = Path(self.tmp.name)
        self.a_db, self.a_ev, self.b_db = d / "a_trading.db", d / "a_evidence.db", d / "b_trading.db"

    def run_both(self, overrides, slots=(SLOT,), symbols=("XAUUSD", "EURUSD"), scope=("XAUUSD",)):
        for db, ev, sc in ((self.a_db, self.a_ev, scope), (self.b_db, None, ())):
            for slot in slots:
                runtime = make(db, ev, slot, sc, overrides)
                try:
                    for symbol in symbols:  # scheduler order
                        runtime.run_cycle(symbol, slot)
                finally:
                    runtime.close()


class RuntimeScopeTests(Twins):
    def test_r1_xau_catches_up_and_eur_economics_identical_to_flag_off(self):
        for db in (self.a_db, self.b_db):
            seed(db, positions=("XAUUSD", "EURUSD"))
        self.run_both({**XAU_STOP_T3, **EUR_STOP_T2})
        xau_a, xau_b = economics(self.a_db, "XAUUSD"), economics(self.b_db, "XAUUSD")
        self.assertIsNone(xau_a["position"])  # catch-up applied the intermediate T3 touch
        self.assertEqual([(t.exit_price, t.reason, t.exited_at) for t in xau_a["closed"]], [(95.0, "stop", T3)])
        self.assertIsNotNone(xau_b["position"])  # legacy newest-bar path missed it (HIGH-8.1, unchanged)
        eur_a, eur_b = economics(self.a_db, "EURUSD"), economics(self.b_db, "EURUSD")
        self.assertEqual(eur_a, eur_b)  # EURUSD outside the scope: legacy economics byte-for-byte
        self.assertIsNotNone(eur_a["position"])  # the legacy path still misses EUR's intermediate touch

    def test_pending_orders_follow_their_symbol_path(self):
        for db in (self.a_db, self.b_db):
            store = Store(db)
            try:
                broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
                broker.orders["pend-eur"] = PaperOrder("1.0", "pend-eur", "run-pend-eur", "EURUSD", "LONG", 1000.0,
                                                       1.10, 1.045, 1.265, 1.0, 10000.0, 0.0, T0)
                store.save_paper(broker)
            finally:
                store.close()
        self.run_both({}, slots=(SLOT, SLOT + 3 * FIVE))
        self.assertEqual(economics(self.a_db, "EURUSD"), economics(self.b_db, "EURUSD"))

    def test_scope_with_all_symbols_still_catches_up_eur(self):
        seed(self.a_db, positions=("XAUUSD", "EURUSD"))
        runtime = make(self.a_db, self.a_ev, SLOT, ("XAUUSD", "EURUSD"), {**EUR_STOP_T2})
        try:
            runtime.run_cycle("XAUUSD", SLOT)
            runtime.run_cycle("EURUSD", SLOT)
        finally:
            runtime.close()
        self.assertIsNone(economics(self.a_db, "EURUSD")["position"])  # in scope -> catch-up closes on T2


class ObserveOnlyTests(Twins):
    def setUp(self):
        super().setUp()
        for db in (self.a_db, self.b_db):
            seed(db, positions=("XAUUSD", "EURUSD"))

    def observe_state(self):
        store = Store(self.a_db)
        try:
            raw = store.get_state("evidence_observe")
            events = [r["event_type"] for r in store.db.execute(
                "SELECT event_type FROM journal WHERE symbol='EURUSD' AND event_type LIKE 'EVIDENCE_OBSERVE%'")]
            return (json.loads(raw) if raw else {}), events
        finally:
            store.close()

    def test_injected_exception_never_changes_the_eur_cycle(self):
        original = MarketEvidenceEngine.ingest_snapshot

        def failing(engine, symbol, snapshot, *, as_of):
            if symbol == "EURUSD":
                raise RuntimeError("injected")
            return original(engine, symbol, snapshot, as_of=as_of)

        with patch.object(MarketEvidenceEngine, "ingest_snapshot", failing):
            self.run_both(EUR_STOP_T2)
        self.assertEqual(economics(self.a_db, "EURUSD"), economics(self.b_db, "EURUSD"))
        state, events = self.observe_state()
        self.assertEqual((state["EURUSD"]["consecutive_failures"], state["EURUSD"]["state"], events),
                         (1, "OK", [EVIDENCE_OBSERVE_FAILED]))

    def test_locked_evidence_store_is_skipped_within_the_bound_and_timeout_restored(self):
        runtime = make(self.a_db, self.a_ev, SLOT, ("XAUUSD",))
        try:
            runtime.run_cycle("XAUUSD", SLOT)  # opens the evidence engine normally
            self.assertEqual(runtime.evidence.store.db.execute("PRAGMA busy_timeout").fetchone()[0],
                             EVIDENCE_BUSY_TIMEOUT_MS)
            holder = sqlite3.connect(self.a_ev, timeout=0, isolation_level=None)
            holder.execute("BEGIN IMMEDIATE")  # another writer holds the evidence lock
            try:
                started = time.monotonic()
                runtime.run_cycle("EURUSD", SLOT)
                elapsed = time.monotonic() - started
            finally:
                holder.execute("ROLLBACK")
                holder.close()
            self.assertEqual(runtime.evidence.store.db.execute("PRAGMA busy_timeout").fetchone()[0],
                             EVIDENCE_BUSY_TIMEOUT_MS)  # restored after the failure
        finally:
            runtime.close()
        state, events = self.observe_state()
        self.assertEqual(events, [EVIDENCE_OBSERVE_SKIPPED_BUSY])
        self.assertLess(state["EURUSD"]["last_elapsed_ms"], 2000)  # bounded wait (500 ms), not the 10 s default
        self.assertLess(elapsed, 30)
        runtime = make(self.b_db, None, SLOT, ())
        try:
            runtime.run_cycle("XAUUSD", SLOT)
            runtime.run_cycle("EURUSD", SLOT)
        finally:
            runtime.close()
        self.assertEqual(economics(self.a_db, "EURUSD"), economics(self.b_db, "EURUSD"))

    def test_timeout_restored_after_success(self):
        runtime = make(self.a_db, self.a_ev, SLOT, ("XAUUSD",))
        try:
            runtime.run_cycle("XAUUSD", SLOT)
            runtime.run_cycle("EURUSD", SLOT)
            self.assertEqual(runtime.evidence.store.db.execute("PRAGMA busy_timeout").fetchone()[0],
                             EVIDENCE_BUSY_TIMEOUT_MS)
        finally:
            runtime.close()
        state, events = self.observe_state()
        self.assertEqual((state["EURUSD"]["state"], state["EURUSD"]["consecutive_failures"], events), ("OK", 0, []))

    def test_corrupt_evidence_store_xau_fails_closed_eur_unchanged(self):
        self.a_ev.write_bytes(b"not a sqlite database" * 200)
        self.run_both({**XAU_STOP_T3, **EUR_STOP_T2})
        store = Store(self.a_db)
        try:
            blocked = [r["event_type"] for r in store.db.execute(
                "SELECT event_type FROM journal WHERE symbol='XAUUSD' AND event_type='EVIDENCE_UNAVAILABLE'")]
        finally:
            store.close()
        self.assertTrue(blocked)  # in scope: fail closed (no newest-bar fallback)
        self.assertIsNotNone(economics(self.a_db, "XAUUSD")["position"])
        self.assertEqual(economics(self.a_db, "EURUSD"), economics(self.b_db, "EURUSD"))
        self.assertEqual(self.observe_state()[1], [EVIDENCE_OBSERVE_FAILED])

    def test_four_consecutive_failures_degrade_and_a_success_resets(self):
        original = MarketEvidenceEngine.ingest_snapshot
        fail = {"on": True}

        def maybe_failing(engine, symbol, snapshot, *, as_of):
            if symbol == "EURUSD" and fail["on"]:
                raise RuntimeError("injected")
            return original(engine, symbol, snapshot, as_of=as_of)

        slots = [SLOT + i * 3 * FIVE for i in range(5)]
        with patch.object(MarketEvidenceEngine, "ingest_snapshot", maybe_failing):
            for i, slot in enumerate(slots[:4]):
                runtime = make(self.a_db, self.a_ev, slot, ("XAUUSD",))
                try:
                    runtime.run_cycle("EURUSD", slot)
                finally:
                    runtime.close()
                state = self.observe_state()[0]["EURUSD"]
                self.assertEqual((state["consecutive_failures"], state["state"]),
                                 (i + 1, "DEGRADED" if i + 1 >= 4 else "OK"))
            fail["on"] = False
            runtime = make(self.a_db, self.a_ev, slots[4], ("XAUUSD",))
            try:
                runtime.run_cycle("EURUSD", slots[4])
            finally:
                runtime.close()
        state = self.observe_state()[0]["EURUSD"]
        self.assertEqual((state["consecutive_failures"], state["state"]), (0, "OK"))


class OutOfScopeRevisionTests(Twins):
    def test_eur_revision_is_non_material_newest_bar_and_escalation_still_blocks(self):
        seed(self.a_db, positions=("XAUUSD", "EURUSD"))
        later = SLOT + 3 * FIVE
        for slot, overrides in ((SLOT, {}), (later, EUR_STOP_T2)):  # T2 re-presented crossing the EUR stop
            runtime = make(self.a_db, self.a_ev, slot, ("XAUUSD",), overrides)
            try:
                runtime.run_cycle("XAUUSD", slot)
                runtime.run_cycle("EURUSD", slot)
            finally:
                runtime.close()
        store = Store(self.a_db)
        try:
            records = [json.loads(r[0]) for r in store.db.execute(
                "SELECT payload FROM journal WHERE event_type=? AND symbol='EURUSD'", (revision_review.EVENT,))]
        finally:
            store.close()
        self.assertEqual([(r["material"], r["managed_by"], r["affected"]) for r in records],
                         [(False, "NEWEST_BAR", [])])  # crosses the SL, but EUR never reads committed evidence
        gate = demo_gate(self.a_db, self.a_ev)
        self.assertEqual((gate["status"], len(gate["non_material_unreviewed"])), ("CLEAR", 1))
        store = Store(self.a_db)
        try:
            record_review(store, records[0]["anomaly_id"], decision="ESCALATED", reviewer="owner")
        finally:
            store.close()
        self.assertEqual(demo_gate(self.a_db, self.a_ev)["blockers"], ["ESCALATED_UNRESOLVED"])  # DEC-8.15


if __name__ == "__main__":
    unittest.main()
