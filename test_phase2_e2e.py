"""V2 Phase 2 / B2.3D: end-to-end regression of the market-evidence economic path (flag ON in-test).
Child process: ``python -m test_phase2_e2e child ...``.

Open position on EURUSD (catch-up needs no AI); pending order on XAUUSD (the deterministic AI fixture is
healthy for XAUUSD only; for EURUSD it returns ERROR, so its gate always fails closed)."""
import os
import subprocess
import sys
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import runtime.service as service
from data.market_evidence import MarketEvidenceEngine
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from storage.database import Store
from storage.evidence_store import EvidenceStore
from test_demo_runner import patched_scouts
from test_runtime_catch_up import ACCOUNT, FIVE, SLOT, T0, Bars, make_runtime

ROOT = Path(__file__).resolve().parent


def bar(i):
    return T0 + i * FIVE  # T1 = 13:10 ... T7 = 13:40


S1, S2, S3 = SLOT, SLOT + timedelta(minutes=15), SLOT + timedelta(minutes=30)  # 13:30, 13:45, 14:00
# Second presentation: EUR T3 REVISED with a stop-hitting low (must not override committed T3);
# T6 hits the EUR stop (1.045); T7 would hit the target (1.21).
EUR_LATER = {("EURUSD", bar(3)): (1.10, 1.1005, 1.00, 1.10),
             ("EURUSD", bar(6)): (1.10, 1.1005, 1.04, 1.05),
             ("EURUSD", bar(7)): (1.05, 1.22, 1.049, 1.20)}
XAU_SNAPSHOT_T7 = {("XAUUSD", bar(7)): (100.0, 100.05, 99.95, 100.0)}
XAU_COMMITTED_T7 = {("XAUUSD", bar(7)): (101.0, 101.05, 100.95, 101.0)}


def seed(db):
    store = Store(db)
    try:
        broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
        broker.orders["ord-e"] = PaperOrder("1.0", "ord-e", "run-e", "EURUSD", "LONG", 1000.0, 1.10, 1.045, 1.21,
                                            1.0, 10000.0, 0.0, T0 - FIVE, status="FILLED")
        broker.account.open_positions["EURUSD"] = PaperPosition(
            "1.0", "pos-e", "ord-e", "run-e", "EURUSD", "LONG", 1000.0, 1.10, 1.10, 1.045, 1.21, T0,
            last_price=1.10, contract_multiplier=1.0)
        broker.orders["pend-x"] = PaperOrder("1.0", "pend-x", "run-x", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0,
                                             1.0, 10000.0, 0.0, T0)
        store.save_paper(broker)
    finally:
        store.close()


class E2E(unittest.TestCase):
    def setUp(self):
        tag = uuid4().hex
        self.db = Path("data/runtime") / f"b23d-e2e-{tag}.db"
        self.ev = Path("data/runtime") / f"b23d-ev-{tag}.db"
        self.addCleanup(lambda: [p.with_name(p.name + s).unlink(missing_ok=True)
                                 for p in (self.db, self.ev) for s in ("", "-wal", "-shm")])

    def rt(self, slot, overrides=None):
        runtime = make_runtime(self.db, self.ev, slot, overrides=overrides, recovery=0)
        self.addCleanup(runtime.close)
        return runtime

    def store(self):
        store = Store(self.db)
        self.addCleanup(store.close)
        return store

    def test_end_to_end(self):
        seed(self.db)
        # Real child process: 13:30 EURUSD cycle crashes right after T2's economic commit.
        child = subprocess.run([sys.executable, "-m", "test_phase2_e2e", "child", str(self.db), str(self.ev)],
                               cwd=ROOT, timeout=120)
        self.assertEqual(child.returncode, 9)
        account, _, _ = self.store().load_paper(ACCOUNT)
        self.assertEqual(account.open_positions["EURUSD"].last_processed_at, bar(2))
        ev = EvidenceStore(self.ev, readonly=True)
        try:
            starts = [r[0] for r in ev.db.execute("SELECT bar_start FROM market_evidence WHERE symbol='EURUSD' "
                                                  "AND timeframe='5m' ORDER BY stream_seq")]
        finally:
            ev.close()
        self.assertEqual(starts, sorted(starts))  # 1. chronological commit order.
        self.assertEqual(starts[-4:], [bar(i).isoformat() for i in range(1, 5)])

        position_bars, pending_bars = [], []
        original_pb, original_pnb, real_ai = TradeManager.process_bar, PaperBroker.process_next_bar, service.run_ai

        def pb(manager, b):
            position_bars.append((b["symbol"], b["timestamp"], b["low"]))
            return original_pb(manager, b)

        def pnb(broker, order, b):
            pending_bars.append((order.order_id, b["timestamp"], b["open"]))
            return original_pnb(broker, order, b)
        with patch.object(TradeManager, "process_bar", pb), patch.object(PaperBroker, "process_next_bar", pnb), \
                patch.object(service, "run_floor", wraps=service.run_floor) as floor:
            # 13:30 XAUUSD: AI review unhealthy -> gate fails; T1..T4 NOT_EVALUATED.
            with patched_scouts("LONG"), patch.object(service, "run_ai",
                                                      lambda *a: replace(real_ai(*a), ai_setup_review=None)):
                self.rt(S1).run_cycle("XAUUSD", S1)
            # Another writer committed XAU T7 at 101 before the 13:45 cycle; that cycle's snapshot says 100.
            store = EvidenceStore(self.ev)
            try:
                MarketEvidenceEngine(store).ingest_snapshot(
                    "XAUUSD", Bars(XAU_COMMITTED_T7).load_snapshot("XAUUSD", S2), as_of=S2)
            finally:
                store.close()
            # Restart: 13:45 EURUSD (re-presents everything, T3 revised), then gated XAUUSD; duplicate slot.
            self.rt(S2, EUR_LATER).run_cycle("EURUSD", S2)
            with patched_scouts("LONG"):
                self.rt(S2, XAU_SNAPSHOT_T7).run_cycle("XAUUSD", S2)
                self.assertEqual(self.rt(S2, XAU_SNAPSHOT_T7).run_cycle("XAUUSD", S2), "DUPLICATE")
            # Recovery cycle 14:00 re-presents everything again.
            self.rt(S3, EUR_LATER).run_cycle("EURUSD", S3)
            with patched_scouts("LONG"):
                self.rt(S3, XAU_SNAPSHOT_T7).run_cycle("XAUUSD", S3)
            analysed = floor.call_count

        store = self.store()
        account, orders, fills = store.load_paper(ACCOUNT)
        eur = [(t, low) for s, t, low in position_bars if s == "EURUSD"]
        self.assertEqual([t for t, _ in eur], [bar(i) for i in range(3, 7)])  # 2./10. T3..T6 once; T1/T2 not redone.
        self.assertAlmostEqual(eur[0][1], 1.10 * .9995)  # 9. committed T3, not the revised low 1.00.
        trade = account.closed_trades[0]
        self.assertEqual((len(account.closed_trades), trade.symbol, trade.exit_price), (1, "EURUSD", 1.045))  # 3.
        self.assertEqual(str(trade.exited_at)[:19].replace(" ", "T"), bar(6).isoformat()[:19])  # stop on T6.
        self.assertEqual(pending_bars, [("pend-x", bar(7), 101.0)])  # 5./6./7./9.
        self.assertEqual([(f.fill_timestamp, f.fill_price) for f in fills.values()], [(bar(7), 101.0)])
        rows = sorted(r["timestamp"] for r in store.journal(event="PENDING_NOT_EVALUATED", limit=1000))
        self.assertEqual(rows, [bar(i).isoformat() for i in range(1, 7)])  # 8./11. after as_of, once each.
        self.assertEqual(analysed, 5)  # 4. once per analysed cycle (not per historical bar).
        self.assertEqual(account.realized_pnl, trade.net_pnl)  # 12.
        self.assertAlmostEqual(account.equity, account.starting_equity + account.realized_pnl + account.unrealized_pnl)
        self.assertEqual(sorted(account.open_positions), ["XAUUSD"])
        for event, count in (("POSITION_CLOSED", 1), ("STOP_HIT", 1), ("TARGET_HIT", 0), ("ORDER_FILLED", 1)):
            self.assertEqual(len(store.journal(event=event, limit=1000)), count, event)  # 13.
        ev = EvidenceStore(self.ev, readonly=True)
        try:
            kinds = [tuple(k) for k in ev.db.execute("SELECT kind, bar_start FROM evidence_anomalies")]
        finally:
            ev.close()
        self.assertIn(("REVISION", bar(3).isoformat()), kinds)  # EUR T3 revision recorded, never applied.
        self.assertIn(("REVISION", bar(7).isoformat()), kinds)  # XAU T7 snapshot disagreement recorded.


def _child(db, ev):
    original, calls = Store.save_paper, []

    def crashing(store, broker, **kwargs):
        result = original(store, broker, **kwargs)
        if kwargs.get("expected_state") is not None:
            calls.append(1)
            if len(calls) == 2:
                os._exit(9)
        return result
    with patch.object(Store, "save_paper", crashing):
        make_runtime(db, ev, SLOT).run_cycle("EURUSD", SLOT)
    os._exit(0)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "child":
        _child(sys.argv[2], sys.argv[3])
    else:
        unittest.main()
