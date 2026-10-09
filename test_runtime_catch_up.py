"""V2 Phase 2 / B2.3B: Evidence ingestion + chronological position catch-up in the runtime (flag OFF
by default). Child processes import this module (``python -m test_runtime_catch_up child ...``)."""
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
from runtime.service import EVIDENCE_UNAVAILABLE, OperationalRuntime
from storage.database import SCHEMA_VERSION, Store
from storage.evidence_store import EvidenceStore
from test_demo_runner import instrument, macro_fixture, patched_scouts

ROOT = Path(__file__).resolve().parent
ACCOUNT = "paper-main"
FIVE = timedelta(minutes=5)
T0 = datetime(2026, 1, 15, 13, 5, tzinfo=timezone.utc)  # Position opened (durable watermark).
SLOT = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)  # Closed 5m bars: T1..T4 = 13:10..13:25.
T1, T2, T3, T4 = (T0 + i * FIVE for i in range(1, 5))
EUR = InstrumentSpec("EURUSD", "FOREX", "EUR/USD", "UTC", .0001, .0001, 1, ("1h", "15m", "5m"))
BASE = {"XAUUSD": 100.0, "EURUSD": 1.10}


def at(start):
    return start if isinstance(start, datetime) else datetime.fromisoformat(start)


class Bars:
    """Provider returning every listed 5m bar closed by the slot (start + 5m <= slot), V1 frame shape.
    ``overrides`` maps (symbol, bar start) -> (open, high, low, close); other bars are flat at BASE."""

    def __init__(self, overrides=None, first=T0 - 4 * FIVE, last=datetime(2026, 1, 15, 14, 55, tzinfo=timezone.utc)):
        self.overrides, self.first, self.last = overrides or {}, first, last

    def load_snapshot(self, symbol, slot):
        stamps, rows = [], []
        stamp = self.first
        while stamp + FIVE <= slot and stamp <= self.last:
            base = BASE[symbol]
            o, h, l, c = self.overrides.get((symbol, stamp), (base, base * 1.0005, base * .9995, base))
            stamps.append(stamp)
            rows.append({"Open": o, "High": h, "Low": l, "Close": c, "symbol": symbol, "is_closed": True})
            stamp += FIVE
        five = pd.DataFrame(rows, index=pd.DatetimeIndex(stamps))
        last = rows[-1]

        def single(start):
            return pd.DataFrame([dict(last)], index=pd.DatetimeIndex([start]))
        return {"1h": single(slot - timedelta(hours=1)), "15m": single(slot - timedelta(minutes=15)), "5m": five}


def config(db, evidence, flag=True):
    return RuntimeConfig(db_path=Path(db), enabled_symbols=("XAUUSD", "EURUSD"), v2_position_catch_up=flag,
                         v2_position_catch_up_symbols=("XAUUSD", "EURUSD") if flag else (),  # P1-B: full scope = prior global
                         market_evidence_path=None if evidence is None else Path(evidence))


def make_runtime(db, evidence, slot, *, flag=True, overrides=None, recovery=120):
    return OperationalRuntime(config(db, evidence, flag), market_provider=Bars(overrides),
                              ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                              instruments={"XAUUSD": instrument(), "EURUSD": EUR},
                              clock=lambda: slot, recovery_stale_after_seconds=recovery)


def seed(db, *, positions=("XAUUSD",), pending=False):
    store = Store(db)
    try:
        broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
        for symbol in positions:
            entry = BASE[symbol]
            order = PaperOrder("1.0", f"ord-{symbol}", f"run-{symbol}", symbol, "LONG", 10.0 if symbol == "XAUUSD" else 10000.0,
                               entry, entry * .95, entry * 1.10, 1.0, 10000.0, 0.0, T0 - FIVE, status="FILLED")
            broker.orders[order.order_id] = order
            broker.account.open_positions[symbol] = PaperPosition(
                "1.0", f"pos-{symbol}", order.order_id, order.run_id, symbol, "LONG", order.quantity, entry, entry,
                order.stop, order.target, T0, last_price=entry, contract_multiplier=1.0)
        if pending:
            broker.orders["pend"] = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0,
                                               1.0, 10000.0, 0.0, T0)
        store.save_paper(broker)
    finally:
        store.close()


# Bar overrides: XAU stop 95, target 110 (position entry 100). EUR stop 1.045.
STOP_THEN_TARGET = {("XAUUSD", T2): (100.0, 100.5, 94.0, 96.0), ("XAUUSD", T3): (96.0, 111.0, 95.5, 108.0)}
EUR_STOP = {("EURUSD", T2): (1.10, 1.101, 1.04, 1.05)}
XAU_STOP = {("XAUUSD", T3): (100.0, 100.5, 94.0, 96.0)}


class CatchUpCase(unittest.TestCase):
    def setUp(self):
        tag = uuid4().hex
        self.db = Path("data/runtime") / f"b23b-test-{tag}.db"
        self.ev = Path("data/runtime") / f"b23b-evidence-{tag}.db"
        self.addCleanup(self.remove)

    def remove(self):
        for path in (self.db, self.ev):
            for suffix in ("", "-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)

    def runtime(self, slot=SLOT, **kwargs):
        runtime = make_runtime(self.db, self.ev, slot, **kwargs)
        self.addCleanup(runtime.close)
        return runtime

    def paper(self):
        store = Store(self.db)
        try:
            return store.load_paper(ACCOUNT)
        finally:
            store.close()

    def journal(self, event):
        store = Store(self.db)
        try:
            return store.journal(event=event, limit=10000)
        finally:
            store.close()

    def evidence_watermark(self, symbol="XAUUSD"):
        store = EvidenceStore(self.ev, readonly=True)
        try:
            row = store.db.execute("SELECT max(bar_start) FROM market_evidence WHERE symbol=? AND timeframe='5m'",
                                   (symbol,)).fetchone()
            return row[0]
        finally:
            store.close()

    def spy_bars(self):
        """Spy every bar reaching the unchanged TradeManager.process_bar."""
        seen, original = [], TradeManager.process_bar

        def spy(manager, bar):
            seen.append((bar["symbol"], bar["timestamp"]))
            return original(manager, bar)
        return seen, patch.object(TradeManager, "process_bar", spy)

    def child(self, *args, timeout=120):
        return subprocess.Popen([sys.executable, "-m", "test_runtime_catch_up", "child", *map(str, args)], cwd=ROOT)


class FlagTests(CatchUpCase):
    def test_1_15_flag_off_default_v1_unchanged(self):
        self.assertIs(RuntimeConfig().v2_position_catch_up, False)
        self.assertIs(RuntimeConfig.from_env().v2_position_catch_up, False)
        off = RuntimeConfig(db_path=self.db)
        legacy = {"cadence_minutes": off.cadence_minutes, "enabled_symbols": off.enabled_symbols,
                  "supported_symbols": ("XAUUSD", "NAS100", "EURUSD"), "sessions": off.sessions,
                  "scheduler_enabled": off.scheduler_enabled, "max_age_seconds": off.max_age_seconds,
                  "paper_mode": True, "market_provider_mode": off.market_provider_mode,
                  "ai_provider_mode": off.ai_provider_mode, "macro_provider_mode": off.macro_provider_mode}
        self.assertEqual(off.fingerprint(), hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest())
        self.assertNotEqual(config(self.db, self.ev).fingerprint(), off.fingerprint())
        with self.assertRaises(ValueError):
            RuntimeConfig(db_path=self.db, v2_position_catch_up=True)  # Needs a separate evidence path.
        with self.assertRaises(ValueError):
            RuntimeConfig(db_path=self.db, v2_position_catch_up=True, market_evidence_path=self.db)
        with self.assertRaises(ValueError):
            RuntimeConfig(db_path=self.db, v2_position_catch_up=1, market_evidence_path=self.ev)
        seed(self.db)
        seen, spy = self.spy_bars()
        runtime = self.runtime(flag=False, overrides=STOP_THEN_TARGET)
        with spy:
            runtime.run_cycle("XAUUSD", SLOT)
        self.assertEqual(seen, [("XAUUSD", T4)])  # V1: newest bar only; T2 stop never seen.
        self.assertIn("XAUUSD", self.paper()[0].open_positions)
        self.assertIsNone(runtime.evidence)
        self.assertFalse(self.ev.exists())  # Nothing opened or created when OFF.


class CatchUpTests(CatchUpCase):
    def test_2_chronological_t1_to_t4(self):
        seed(self.db)
        seen, spy = self.spy_bars()
        with spy:
            self.runtime().run_cycle("XAUUSD", SLOT)
        self.assertEqual(seen, [("XAUUSD", t) for t in (T1, T2, T3, T4)])
        self.assertEqual(self.paper()[0].open_positions["XAUUSD"].last_processed_at, T4)
        self.assertEqual(self.evidence_watermark(), T4.isoformat())

    def test_3_cadence_jump_processes_every_skipped_bar(self):
        seed(self.db)
        seen, spy = self.spy_bars()
        first = datetime(2026, 1, 15, 13, 15, tzinfo=timezone.utc)
        later = datetime(2026, 1, 15, 13, 45, tzinfo=timezone.utc)  # The 13:30 cycle never ran.
        with spy:
            self.runtime(first).run_cycle("XAUUSD", first)
            self.assertEqual(seen, [("XAUUSD", T1)])
            self.runtime(later).run_cycle("XAUUSD", later)
        self.assertEqual(seen, [("XAUUSD", T0 + i * FIVE) for i in range(1, 8)])  # 13:10 .. 13:40, once each.

    def test_4_correct_sl_tp_bar_wins(self):
        seed(self.db)
        self.runtime(overrides=STOP_THEN_TARGET).run_cycle("XAUUSD", SLOT)
        account = self.paper()[0]
        self.assertNotIn("XAUUSD", account.open_positions)
        trade = account.closed_trades[0]
        self.assertEqual((trade.exit_price, at(trade.exited_at)), (95.0, T2))  # T2 stop, not T3 target.
        self.assertEqual(account.realized_pnl, trade.net_pnl)

    def test_5_duplicate_cycle_no_duplicate_economics(self):
        seed(self.db)
        self.runtime(overrides=STOP_THEN_TARGET).run_cycle("XAUUSD", SLOT)
        before = self.paper()[0]
        self.assertEqual(self.runtime(overrides=STOP_THEN_TARGET).run_cycle("XAUUSD", SLOT), "DUPLICATE")
        later = SLOT + timedelta(minutes=15)
        self.runtime(later, overrides=STOP_THEN_TARGET).run_cycle("XAUUSD", later)  # Re-presents T1..T4.
        after = self.paper()[0]
        self.assertEqual((len(after.closed_trades), after.realized_pnl), (1, before.realized_pnl))
        self.assertEqual(len(self.journal("POSITION_CLOSED")), 1)

    def test_11_12_no_historical_pipeline_pending_only_current_bar(self):
        seed(self.db, positions=(), pending=True)
        import runtime.service as service
        seen_pending, original = [], PaperBroker.process_next_bar

        def spy(broker, order, bar):
            seen_pending.append(bar["timestamp"])
            return original(broker, order, bar)
        with patched_scouts("LONG"), patch.object(service, "run_floor", wraps=service.run_floor) as floor, \
                patch.object(service, "run_ai", wraps=service.run_ai) as ai, \
                patch.object(PaperBroker, "submit_plan", wraps=PaperBroker.submit_plan, autospec=True) as submit, \
                patch.object(PaperBroker, "process_next_bar", spy):
            self.runtime().run_cycle("XAUUSD", SLOT)
        self.assertEqual((floor.call_count, ai.call_count, submit.call_count), (1, 1, 0))
        self.assertEqual(seen_pending, [T4])  # Current-cycle bar only; T1..T3 never evaluated.
        self.assertEqual(self.paper()[1]["pend"].status, "FILLED")

    def test_11_position_catch_up_runs_decision_pipeline_once_per_cycle(self):
        import runtime.service as service
        seed(self.db)
        with patch.object(service, "run_floor", wraps=service.run_floor) as floor, \
                patch.object(service, "run_ai", wraps=service.run_ai) as ai:
            self.runtime().run_cycle("XAUUSD", SLOT)
        self.assertEqual((floor.call_count, ai.call_count), (1, 1))
        self.assertEqual(self.paper()[0].open_positions["XAUUSD"].last_processed_at, T4)


class FailureTests(CatchUpCase):
    def test_9_corrupt_evidence_store_fails_closed_no_fallback(self):
        seed(self.db, positions=())
        self.ev.write_bytes(b"not a sqlite database" * 64)
        seen, spy = self.spy_bars()
        runtime = self.runtime()
        with patched_scouts("LONG"), spy:
            runtime.run_cycle("XAUUSD", SLOT)
        self.assertEqual(seen, [])  # No newest-bar fallback.
        run = runtime.store.latest_run("XAUUSD")
        outcome = runtime.store.review_report(run["run_id"])["execution"]
        self.assertEqual((outcome["execution_status"], outcome["execution_reason"]), ("SKIPPED", EVIDENCE_UNAVAILABLE))
        self.assertEqual(self.paper()[1], {})  # No submission over unreconciled PAPER state.
        self.assertEqual(len(self.journal(EVIDENCE_UNAVAILABLE)), 1)

    def test_9_ingest_failure_fails_closed_then_next_cycle_resumes(self):
        seed(self.db)
        first = datetime(2026, 1, 15, 13, 15, tzinfo=timezone.utc)
        self.runtime(first).run_cycle("XAUUSD", first)  # Commits evidence through T1 and applies T1.
        seen, spy = self.spy_bars()
        from data.market_evidence import MarketEvidenceEngine
        with spy, patch.object(MarketEvidenceEngine, "ingest_snapshot", side_effect=OSError("disk")):
            self.runtime().run_cycle("XAUUSD", SLOT)
        self.assertEqual(seen, [])
        self.assertEqual(self.paper()[0].open_positions["XAUUSD"].last_processed_at, T1)  # Watermark unchanged.
        later = SLOT + timedelta(minutes=15)
        with spy:
            self.runtime(later).run_cycle("XAUUSD", later)
        self.assertEqual(seen, [("XAUUSD", T0 + i * FIVE) for i in range(2, 8)])  # Resumes at T2 (13:15..13:40).

    def test_10_trading_save_failure_keeps_evidence_and_resumes(self):
        seed(self.db)
        original, calls = Store.save_paper, []

        def failing(store, broker, **kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise sqlite3.OperationalError("disk I/O error")
            return original(store, broker, **kwargs)
        with patch.object(Store, "save_paper", failing):
            self.assertEqual(self.runtime().run_cycle("XAUUSD", SLOT), "ERROR")
        self.assertEqual(self.paper()[0].open_positions["XAUUSD"].last_processed_at, T1)
        self.assertEqual(self.evidence_watermark(), T4.isoformat())  # Evidence ahead; not the economic watermark.
        seen, spy = self.spy_bars()
        later = SLOT + timedelta(minutes=15)
        with spy:
            self.runtime(later).run_cycle("XAUUSD", later)
        self.assertEqual(seen[0], ("XAUUSD", T2))
        self.assertEqual(len(seen), len(set(seen)))


class ProcessTests(CatchUpCase):
    def crash(self, mode):
        seed(self.db)
        child = self.child("crash", self.db, self.ev, mode)
        self.assertEqual(child.wait(timeout=120), 9)
        return self.paper()[0].open_positions["XAUUSD"].last_processed_at

    def resume(self):
        seen, spy = self.spy_bars()
        later = SLOT + timedelta(minutes=15)
        with spy:
            self.runtime(later, recovery=0).run_cycle("XAUUSD", later)
        return seen

    def test_7_crash_before_economic_save_bar_stays_eligible(self):
        self.assertEqual(self.crash("before"), T1)  # T2's transaction never committed.
        self.assertEqual(self.resume()[0], ("XAUUSD", T2))

    def test_8_crash_after_economic_save_bar_not_repeated(self):
        self.assertEqual(self.crash("after"), T2)
        seen = self.resume()
        self.assertEqual(seen[0], ("XAUUSD", T3))
        self.assertNotIn(("XAUUSD", T2), seen)

    def test_13_same_symbol_two_processes_close_once(self):
        seed(self.db)
        children = [self.child("cycle", self.db, self.ev, "XAUUSD", "xau_stop", "-") for _ in range(2)]
        self.assertEqual([c.wait(timeout=120) for c in children], [0, 0])
        account = self.paper()[0]
        self.assertEqual((len(account.closed_trades), list(account.open_positions)), (1, []))
        self.assertEqual(account.realized_pnl, account.closed_trades[0].net_pnl)
        self.assertEqual(len(self.journal("POSITION_CLOSED")), 1)

    def test_14_cross_symbol_real_processes_forced_interleave(self):
        """A (XAUUSD) has loaded PAPER state for its first catch-up bar and waits; B (EURUSD) closes
        its position and commits; A must go STALE, reload and keep B's update."""
        seed(self.db, positions=("XAUUSD", "EURUSD"))
        signal = Path("data/runtime") / f"b23b-signal-{uuid4().hex}"
        self.addCleanup(lambda: [p.unlink(missing_ok=True) for p in (Path(f"{signal}.a"), Path(f"{signal}.b"),
                                                                       Path(f"{signal}.stale"))])
        a = self.child("cycle", self.db, self.ev, "XAUUSD", "xau_stop", signal)
        deadline = time.time() + 60
        while not Path(f"{signal}.a").exists():
            self.assertLess(time.time(), deadline)
            time.sleep(.05)
        b = self.child("cycle", self.db, self.ev, "EURUSD", "eur_stop", "-")
        self.assertEqual(b.wait(timeout=120), 0)
        Path(f"{signal}.b").write_text("go")
        self.assertEqual(a.wait(timeout=120), 0)
        self.assertTrue(Path(f"{signal}.stale").exists())  # A's first catch-up save was refused.
        account = self.paper()[0]
        self.assertEqual(sorted(t.symbol for t in account.closed_trades), ["EURUSD", "XAUUSD"])
        self.assertEqual(account.open_positions, {})  # Nothing resurrected.
        self.assertAlmostEqual(account.realized_pnl, sum(t.net_pnl for t in account.closed_trades))
        self.assertEqual(len(self.journal("POSITION_CLOSED")), 2)


class ScopeTests(CatchUpCase):
    def test_16_schema_3_separate_stores_no_health(self):
        seed(self.db)
        self.runtime().run_cycle("XAUUSD", SLOT)
        store = Store(self.db)
        try:
            self.assertEqual((SCHEMA_VERSION, store.db.execute("SELECT version FROM schema_info").fetchone()[0]), (3, 3))
            self.assertIsNone(store.db.execute("SELECT name FROM sqlite_master WHERE name='market_evidence'").fetchone())
        finally:
            store.close()
        self.assertIsNone(health_hooks._sink)
        self.assertNotIn("NAS100", config(self.db, self.ev).enabled_symbols)


def _child(argv):
    mode = argv[0]
    if mode == "crash":
        db, ev, when = argv[1:]
        original, calls = Store.save_paper, []

        def crashing(store, broker, **kwargs):
            calls.append(1)
            if len(calls) == 2 and when == "before":
                os._exit(9)
            result = original(store, broker, **kwargs)
            if len(calls) == 2 and when == "after":
                os._exit(9)
            return result
        with patch.object(Store, "save_paper", crashing):
            make_runtime(db, ev, SLOT).run_cycle("XAUUSD", SLOT)
        os._exit(0)
    db, ev, symbol, scenario, signal = argv[1:]
    overrides = {"xau_stop": XAU_STOP, "eur_stop": EUR_STOP}[scenario]
    runtime = make_runtime(db, ev, SLOT, overrides=overrides)
    if signal != "-":
        original, state = Store.save_paper, {"waited": False}

        def interleaved(store, broker, **kwargs):
            if kwargs.get("expected_state") is not None and not state["waited"]:
                state["waited"] = True
                Path(f"{signal}.a").write_text("loaded")
                deadline = time.time() + 60
                while not Path(f"{signal}.b").exists() and time.time() < deadline:
                    time.sleep(.05)
            result = original(store, broker, **kwargs)
            if result is False:
                Path(f"{signal}.stale").write_text("stale")
            return result
        with patch.object(Store, "save_paper", interleaved):
            runtime.run_cycle(symbol, SLOT)
    else:
        runtime.run_cycle(symbol, SLOT)
    runtime.close()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        _child(sys.argv[2:])
    else:
        unittest.main()
