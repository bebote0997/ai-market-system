"""Phase 13 evidence preparation: with AI_FLOOR_V2_POSITION_CATCH_UP OFF and AI_FLOOR_MARKET_EVIDENCE_PATH set, every
enabled symbol's snapshot is committed observe-only to the Evidence Store, so a later ON activation can pass
``catch_up_evidence_contiguous``. OFF economics, journal and decisions never change; READ-ONLY readiness CLI.
Test scenarios only (temporary databases): nothing operational."""
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from ai.provider import DeterministicAIProvider
from data.market_evidence import MarketBar, MarketEvidenceEngine
from replay import evidence_readiness
from runtime.config import RuntimeConfig, catch_up_scope_checks
from runtime.scheduler import Scheduler, slot_key
from runtime.service import (EVIDENCE_OBSERVE_BUDGET_EXCEEDED, EVIDENCE_OBSERVE_FAILED, EVIDENCE_OBSERVE_SKIPPED_BUSY,
                             OperationalRuntime)
from storage.database import Store
from storage.evidence_store import EvidenceBudgetExceeded, EvidenceStore, EvidenceStoreError
from test_demo_runner import instrument, macro_fixture
from test_phase8_observe_only import EUR_STOP_T2, XAU_STOP_T3, economics
from test_runtime_catch_up import EUR, FIVE, SLOT, T0, Bars, seed

SYMBOLS = ("XAUUSD", "EURUSD")
NEXT_MORNING = datetime(2026, 1, 16, 8, 0, tzinfo=timezone.utc)  # London 08:00: first in-session slot of Jan 16
OFF_SESSION = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)  # after the NY close: SESSION_SKIPPED


def make(db, ev, slot, overrides=None, provider=None, **settings):
    config = RuntimeConfig(db_path=Path(db), enabled_symbols=SYMBOLS,
                           market_evidence_path=None if ev is None else Path(ev), **settings)
    return OperationalRuntime(config, market_provider=provider or Bars(overrides),
                              ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                              instruments={"XAUUSD": instrument(), "EURUSD": EUR}, clock=lambda: slot,
                              recovery_stale_after_seconds=120)


def cycles(db, ev, slots, overrides=None, provider=None):
    """Run the slots in scheduler order. PAPER ids are uuid4: a fresh deterministic sequence per call makes twin
    databases comparable byte-for-byte."""
    counter = iter(range(1, 1 << 30))
    with patch("uuid.uuid4", lambda: uuid.UUID(int=next(counter))):
        return _cycles(db, ev, slots, overrides, provider)


def _cycles(db, ev, slots, overrides, provider):
    results = []
    for slot in slots:
        runtime = make(db, ev, slot, overrides, provider)
        try:
            results += [runtime.run_cycle(symbol, slot) for symbol in SYMBOLS]  # scheduler order
            runtime.flush_evidence_observations()  # as Scheduler.tick: after every cycle of the tick
            assert runtime.evidence is None and not runtime._off_observe_pending  # nothing held after the flush
        finally:
            runtime.close()
    return results


def committed_5m(ev, symbol):
    store = EvidenceStore(ev, readonly=True)
    try:
        return [datetime.fromisoformat(r[0]) for r in store.db.execute(
            "SELECT bar_start FROM market_evidence WHERE symbol=? AND timeframe='5m' ORDER BY stream_seq", (symbol,))]
    finally:
        store.close()


def anomalies_5m(ev, symbol):
    store = EvidenceStore(ev, readonly=True)
    try:
        return [r[0] for r in store.db.execute(
            "SELECT kind FROM evidence_anomalies WHERE symbol=? AND timeframe='5m'", (symbol,))]
    finally:
        store.close()


def journal(db):
    store = Store(db)
    try:
        return [tuple(r) for r in store.db.execute(
            "SELECT timestamp, run_id, symbol, source, event_type, severity, payload FROM journal ORDER BY id")]
    finally:
        store.close()


def observe_state(db):
    store = Store(db)
    try:
        raw = store.get_state("evidence_observe")
        return json.loads(raw) if raw else {}
    finally:
        store.close()


class Case(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="v2-ev-off-")
        self.addCleanup(tmp.cleanup)
        d = Path(tmp.name)
        self.a_db, self.ev, self.b_db = d / "a_trading.db", d / "market_evidence.db", d / "b_trading.db"


class OffPathWritesEvidence(Case):
    def test_off_cycle_commits_every_enabled_symbol(self):
        seed(self.a_db, positions=SYMBOLS)
        self.assertEqual(cycles(self.a_db, self.ev, (SLOT,)), cycles(self.b_db, None, (SLOT,)))
        for symbol in SYMBOLS:
            bars = committed_5m(self.ev, symbol)
            self.assertEqual(bars[-1], SLOT - FIVE)  # the newest closed bar at the slot
            self.assertEqual(bars, [bars[0] + i * FIVE for i in range(len(bars))])
        self.assertEqual(observe_state(self.a_db), {})  # nothing written to the trading DB
        self.assertFalse([row for row in journal(self.a_db) if row[3] == "market_evidence"])

    def test_no_evidence_path_writes_nothing(self):
        cycles(self.a_db, None, (SLOT,))
        self.assertFalse(self.ev.exists())
        self.assertEqual(observe_state(self.a_db), {})

    def test_preflight_report_with_flag_off_is_unchanged(self):
        config = RuntimeConfig(db_path=self.a_db, enabled_symbols=SYMBOLS, market_evidence_path=self.ev)
        self.assertEqual(catch_up_scope_checks(config), {})
        self.assertEqual(config.fingerprint(), RuntimeConfig(db_path=self.a_db, enabled_symbols=SYMBOLS).fingerprint())


class OffEconomicsUnchanged(Case):
    def test_economics_and_journal_identical_to_no_evidence_path(self):
        for db in (self.a_db, self.b_db):
            seed(db, positions=SYMBOLS)
        slots = (SLOT, SLOT + 3 * FIVE, SLOT + 6 * FIVE)
        overrides = {**XAU_STOP_T3, **EUR_STOP_T2}
        self.assertEqual(cycles(self.a_db, self.ev, slots, overrides), cycles(self.b_db, None, slots, overrides))
        for symbol in SYMBOLS:
            self.assertEqual(economics(self.a_db, symbol), economics(self.b_db, symbol))
        self.assertEqual(journal(self.a_db), journal(self.b_db))  # byte-for-byte: no evidence row in the journal
        self.assertTrue(committed_5m(self.ev, "EURUSD"))

    def test_injected_failure_never_changes_the_cycle(self):
        for db in (self.a_db, self.b_db):
            seed(db, positions=SYMBOLS)

        def failing(engine, symbol, snapshot, *, as_of):
            raise RuntimeError("injected")

        overrides = {**XAU_STOP_T3, **EUR_STOP_T2}
        with patch.object(MarketEvidenceEngine, "ingest_snapshot", failing), \
                self.assertLogs("ai_floor.runtime", "WARNING") as logs:
            a = cycles(self.a_db, self.ev, (SLOT,), overrides)
        self.assertEqual(a, cycles(self.b_db, None, (SLOT,), overrides))
        for symbol in SYMBOLS:
            self.assertEqual(economics(self.a_db, symbol), economics(self.b_db, symbol))
        self.assertEqual(journal(self.a_db), journal(self.b_db))
        self.assertTrue(any(EVIDENCE_OBSERVE_FAILED in line for line in logs.output))
        self.assertEqual(observe_state(self.a_db), {})  # log only: no trading-DB state write

    def test_corrupt_evidence_store_never_changes_the_cycle(self):
        for db in (self.a_db, self.b_db):
            seed(db, positions=SYMBOLS)
        self.ev.write_bytes(b"not a sqlite database" * 200)
        overrides = {**XAU_STOP_T3, **EUR_STOP_T2}
        self.assertEqual(cycles(self.a_db, self.ev, (SLOT,), overrides), cycles(self.b_db, None, (SLOT,), overrides))
        for symbol in SYMBOLS:
            self.assertEqual(economics(self.a_db, symbol), economics(self.b_db, symbol))
        self.assertEqual(journal(self.a_db), journal(self.b_db))


class OffObserveIsDeferredAndBounded(Case):
    """Codex review (2026-10-14): the OFF write runs after the tick's trading/exit work and is time-bounded."""

    def test_scheduler_writes_evidence_only_after_every_cycle_of_the_tick(self):
        seed(self.a_db, positions=SYMBOLS)
        runtime = make(self.a_db, self.ev, SLOT, scheduler_enabled=True)
        finished_before_first_write = []
        real = OperationalRuntime._observe_evidence_off

        def spy(rt, symbol, snapshot, slot, run_id):
            if not finished_before_first_write:
                finished_before_first_write.append([rt.store.run(slot_key(s, SLOT, 15))["status"] for s in SYMBOLS])
                finished_before_first_write.append(self.ev.exists())
            return real(rt, symbol, snapshot, slot, run_id)

        try:
            with patch.object(OperationalRuntime, "_observe_evidence_off", spy):
                Scheduler(runtime, lambda: SLOT).tick()
            self.assertEqual(finished_before_first_write, [["COMPLETED", "COMPLETED"], False])
            self.assertEqual(runtime._off_observe_pending, {})
        finally:
            runtime.close()
        for symbol in SYMBOLS:
            self.assertEqual(committed_5m(self.ev, symbol)[-1], SLOT - FIVE)

    def test_pending_queue_keeps_only_the_newest_snapshot_per_symbol(self):
        runtime = make(self.a_db, self.ev, SLOT)
        try:
            for slot in (SLOT, SLOT + 3 * FIVE):
                runtime.clock = lambda slot=slot: slot
                runtime.run_cycle("XAUUSD", slot)
            self.assertEqual(list(runtime._off_observe_pending), ["XAUUSD"])
            self.assertEqual(runtime._off_observe_pending["XAUUSD"][1], SLOT + 3 * FIVE)
            self.assertFalse(self.ev.exists())  # never written inside a cycle
        finally:
            runtime.close()

    def test_budget_exhausted_stops_and_logs_without_changing_the_cycle(self):
        for db in (self.a_db, self.b_db):
            seed(db, positions=SYMBOLS)
        overrides = {**XAU_STOP_T3, **EUR_STOP_T2}
        with patch("runtime.service.OFF_OBSERVE_BUDGET_SECONDS", 0.0), \
                self.assertLogs("ai_floor.runtime", "WARNING") as logs:
            a = cycles(self.a_db, self.ev, (SLOT,), overrides)
        self.assertEqual(a, cycles(self.b_db, None, (SLOT,), overrides))
        self.assertEqual(journal(self.a_db), journal(self.b_db))
        self.assertTrue(any(EVIDENCE_OBSERVE_BUDGET_EXCEEDED in line for line in logs.output))
        self.assertEqual(committed_5m(self.ev, "XAUUSD"), [])  # stopped before the first bar

    def test_locked_store_is_skipped_within_the_short_timeout(self):
        EvidenceStore(self.ev).close()
        holder = sqlite3.connect(self.ev, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        self.addCleanup(holder.close)
        runtime = make(self.a_db, self.ev, SLOT)
        try:
            runtime.run_cycle("XAUUSD", SLOT)
            started = time.monotonic()
            with self.assertLogs("ai_floor.runtime", "WARNING") as logs:
                runtime.flush_evidence_observations()
            self.assertLess(time.monotonic() - started, 1.5)
        finally:
            runtime.close()
        self.assertTrue(any(EVIDENCE_OBSERVE_SKIPPED_BUSY in line for line in logs.output), logs.output)
        self.assertEqual(observe_state(self.a_db), {})

    def test_memory_error_is_swallowed_with_a_warning(self):
        def exhausted(engine, symbol, snapshot, *, as_of):
            raise MemoryError()

        runtime = make(self.a_db, self.ev, SLOT)
        try:
            status = runtime.run_cycle("XAUUSD", SLOT)
            with patch.object(MarketEvidenceEngine, "ingest_snapshot", exhausted), \
                    self.assertLogs("ai_floor.runtime", "WARNING") as logs:
                runtime.flush_evidence_observations()
        finally:
            runtime.close()
        self.assertNotEqual(status, "ERROR")
        self.assertTrue(any(EVIDENCE_OBSERVE_FAILED in line and "MemoryError" in line for line in logs.output))

    def test_queue_is_dropped_after_a_halt(self):
        class Halted:
            halted = True

            @staticmethod
            def allow(category):
                return category != "O"

        runtime = make(self.a_db, self.ev, SLOT)
        try:
            runtime.run_cycle("XAUUSD", SLOT)
            runtime.halt_gate = Halted()
            runtime.flush_evidence_observations()
            self.assertEqual(runtime._off_observe_pending, {})
        finally:
            runtime.halt_gate = None
            runtime.close()
        self.assertFalse(self.ev.exists())


class EvidenceStoreBatch(Case):
    def bars(self, n):
        return [MarketBar("XAUUSD", "5m", T0 + i * FIVE, 1, 1, 1, 1, is_closed=True) for i in range(n)]

    def test_batch_is_one_transaction(self):
        store = EvidenceStore(self.ev)
        statements = []
        store.db.set_trace_callback(statements.append)
        try:
            with store.batch(lambda: False):
                MarketEvidenceEngine(store, enabled_symbols=SYMBOLS).ingest("XAUUSD", "5m", self.bars(6),
                                                                             as_of=T0 + 6 * FIVE)
        finally:
            store.close()
        self.assertEqual([s for s in statements if s.startswith(("BEGIN", "COMMIT"))], ["BEGIN IMMEDIATE", "COMMIT"])
        self.assertEqual(len(committed_5m(self.ev, "XAUUSD")), 6)

    def test_deadline_keeps_the_units_already_done(self):
        store = EvidenceStore(self.ev)
        ticks = iter(range(100))
        try:
            with self.assertRaises(EvidenceBudgetExceeded):
                with store.batch(lambda: next(ticks) > 2):  # units 0, 1 and 2 start in time, unit 3 does not
                    MarketEvidenceEngine(store, enabled_symbols=SYMBOLS).ingest("XAUUSD", "5m", self.bars(6),
                                                                                 as_of=T0 + 6 * FIVE)
        finally:
            store.close()
        self.assertEqual(committed_5m(self.ev, "XAUUSD"), [T0, T0 + FIVE, T0 + 2 * FIVE])

    def test_other_errors_roll_the_whole_batch_back(self):
        store = EvidenceStore(self.ev)
        engine = MarketEvidenceEngine(store, enabled_symbols=SYMBOLS)
        try:
            with self.assertRaises(RuntimeError), store.batch(lambda: False):
                engine.ingest("XAUUSD", "5m", self.bars(3), as_of=T0 + 3 * FIVE)
                raise RuntimeError("injected")
        finally:
            store.close()
        self.assertEqual(committed_5m(self.ev, "XAUUSD"), [])

    def test_hard_interrupt_bounds_the_open(self):
        store = EvidenceStore(self.ev)
        try:
            MarketEvidenceEngine(store, enabled_symbols=SYMBOLS).ingest("XAUUSD", "5m", self.bars(200),
                                                                         as_of=T0 + 200 * FIVE)
        finally:
            store.close()
        with self.assertRaises(EvidenceStoreError):
            EvidenceStore(self.ev, timeout=0.1, interrupt=lambda: True)


class Contiguity(Case):
    def test_consecutive_cycles_stay_contiguous(self):
        slots = [SLOT + i * 3 * FIVE for i in range(4)]
        cycles(self.a_db, self.ev, slots)
        for symbol in SYMBOLS:
            bars = committed_5m(self.ev, symbol)
            self.assertEqual(bars, [bars[0] + i * FIVE for i in range(len(bars))])
            self.assertEqual(bars[-1], slots[-1] - FIVE)
            self.assertEqual(anomalies_5m(self.ev, symbol), [])

    def test_next_in_session_snapshot_backfills_the_off_session_window(self):
        provider = Bars(last=NEXT_MORNING)
        cycles(self.a_db, self.ev, (SLOT,), provider=provider)
        self.assertEqual(cycles(self.a_db, self.ev, (OFF_SESSION,), provider=provider), ["SESSION_SKIPPED"] * 2)
        self.assertEqual(committed_5m(self.ev, "XAUUSD")[-1], SLOT - FIVE)  # off-session: nothing fetched
        cycles(self.a_db, self.ev, (NEXT_MORNING,), provider=provider)
        for symbol in SYMBOLS:
            bars = committed_5m(self.ev, symbol)
            self.assertEqual(bars, [bars[0] + i * FIVE for i in range(len(bars))])  # the night is back-filled
            self.assertEqual(bars[-1], NEXT_MORNING - FIVE)
            self.assertEqual(anomalies_5m(self.ev, symbol), [])


class ReadinessCli(Case):
    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = evidence_readiness.main(["--trading-db", str(self.a_db), "--evidence-db", str(self.ev), *args])
        return code, out.getvalue()

    def on_checks(self, as_of):
        config = RuntimeConfig(db_path=self.a_db, enabled_symbols=SYMBOLS, v2_position_catch_up=True,
                               v2_position_catch_up_symbols=SYMBOLS, market_evidence_path=self.ev)
        return catch_up_scope_checks(config, as_of=as_of)["catch_up_evidence_contiguous"]

    def test_ready_after_off_cycles_and_mirrors_the_preflight(self):
        seed(self.a_db, positions=SYMBOLS)
        cycles(self.a_db, self.ev, (SLOT, SLOT + 3 * FIVE))
        as_of = SLOT + 3 * FIVE + timedelta(minutes=2)
        before = {p: p.read_bytes() for p in (self.a_db, self.ev)}
        code, out = self.run_cli("--as-of", as_of.isoformat())
        self.assertEqual(code, 0, out)
        self.assertIn("XAUUSD: READY", out)
        self.assertTrue(self.on_checks(as_of))
        code, out = self.run_cli("--as-of", as_of.isoformat(), "--json")
        report = json.loads(out)["symbols"]
        self.assertEqual({s: report[s]["verdict"] for s in SYMBOLS}, {s: "READY" for s in SYMBOLS})
        self.assertEqual(report["EURUSD"]["newest_committed"], (SLOT + 2 * FIVE).isoformat())
        self.assertEqual({p: p.read_bytes() for p in before}, before)  # READ-ONLY
        self.assertFalse([p for p in self.ev.parent.iterdir() if p.name.endswith(("-wal", "-journal", "-shm"))])

    def test_not_ready_when_the_newest_closed_bar_is_missing(self):
        seed(self.a_db, positions=SYMBOLS)
        cycles(self.a_db, self.ev, (SLOT,))
        as_of = SLOT + 2 * FIVE  # bars SLOT and SLOT+5m were never committed (no cycle yet)
        code, out = self.run_cli("--as-of", as_of.isoformat(), "--symbols", "XAUUSD")
        self.assertEqual(code, 1)
        self.assertIn("XAUUSD: NOT_READY", out)
        self.assertIn("missing_bars=2", out)
        self.assertFalse(self.on_checks(as_of))

    def test_not_ready_on_a_gap_inside_a_position_window(self):
        seed(self.a_db, positions=("XAUUSD",))
        engine = MarketEvidenceEngine(EvidenceStore(self.ev), enabled_symbols=SYMBOLS)
        try:
            starts = [T0 + i * FIVE for i in range(8) if i != 3]  # bar T0+15m never presented -> GAP
            for symbol in SYMBOLS:
                engine.ingest(symbol, "5m", [MarketBar(symbol, "5m", s, 1, 1, 1, 1, is_closed=True) for s in starts],
                              as_of=T0 + 8 * FIVE)
        finally:
            engine.store.close()
        as_of = T0 + 8 * FIVE
        code, out = self.run_cli("--as-of", as_of.isoformat(), "--json")
        report = json.loads(out)["symbols"]
        self.assertEqual(code, 1)
        self.assertEqual(report["XAUUSD"]["verdict"], "NOT_READY")
        self.assertEqual(report["XAUUSD"]["positions"][0]["missing"], [(T0 + 3 * FIVE).isoformat()])
        self.assertEqual(report["XAUUSD"]["positions"][0]["anomalies"], [["GAP", (T0 + 4 * FIVE).isoformat()]])
        self.assertEqual(report["EURUSD"]["verdict"], "READY")  # same gap, but no open position spans it
        self.assertFalse(self.on_checks(as_of))

    def test_long_window_is_counted_arithmetically_and_capped(self):
        seed(self.a_db, positions=("XAUUSD",))  # watermark T0
        EvidenceStore(self.ev).close()
        as_of = T0 + timedelta(days=13)
        code, out = self.run_cli("--as-of", as_of.isoformat(), "--json", "--symbols", "XAUUSD")
        position = json.loads(out)["symbols"]["XAUUSD"]["positions"][0]
        self.assertEqual(code, 1)
        self.assertEqual(position["missing_count"], 13 * 288 - 1)  # every bar after T0 up to as_of - 5m
        self.assertEqual(len(position["missing"]), evidence_readiness.MAX_LISTED)
        self.assertEqual(position["missing"][0], (T0 + FIVE).isoformat())
        code, out = self.run_cli("--as-of", as_of.isoformat(), "--symbols", "XAUUSD")
        self.assertIn(f"missing_bars={13 * 288 - 1}", out)
        self.assertIn(f"(+{13 * 288 - 1 - evidence_readiness.MAX_LISTED})", out)

    def test_absurd_window_is_not_ready_without_enumeration(self):
        seed(self.a_db, positions=("XAUUSD",))
        EvidenceStore(self.ev).close()
        as_of = T0 + timedelta(days=400)
        with patch.object(evidence_readiness, "_first_after", side_effect=AssertionError("enumerated")):
            code, out = self.run_cli("--as-of", as_of.isoformat(), "--json", "--symbols", "XAUUSD")
        position = json.loads(out)["symbols"]["XAUUSD"]["positions"][0]
        self.assertEqual(code, 1)
        self.assertEqual(json.loads(out)["symbols"]["XAUUSD"]["verdict"], "NOT_READY")
        self.assertIn("14 days", position["error"])
        self.assertEqual(position["missing"], [])

    def test_absent_evidence_store_is_not_ready_and_never_created(self):
        code, out = self.run_cli("--as-of", SLOT.isoformat())
        self.assertEqual(code, 1)
        self.assertIn("evidence store absent", out)
        self.assertFalse(self.ev.exists())
        self.assertFalse(self.a_db.exists())


if __name__ == "__main__":
    unittest.main()
