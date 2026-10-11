"""V2 Phase 8 / P8.3: isolated simulation of the FIRST activation of ``v2_position_catch_up`` (temporary databases
only; nothing is activated anywhere). OFF now includes the Owner HIGH-8.1 correction. Pre-fix durable state is
seeded explicitly when needed; current OFF/ON cycles reconcile with the independent oracle.
"""
import json
from datetime import timedelta
from decimal import Decimal

from execution.contracts import PaperOrder
from storage.database import Store
from storage.evidence_store import EvidenceStore
from test_phase8_catch_up_certification import FIVE, OPEN, SLOT, Harness, oracle

S1 = SLOT                        # 13:30 cycle (flag OFF): bars 13:10..13:25, newest 13:25
S2 = SLOT + timedelta(minutes=15)  # 13:45 cycle: bars 13:30..13:40
S3 = SLOT + timedelta(minutes=30)  # 14:00 cycle: bars 13:45..13:55


def bar(minutes_after_open):
    return OPEN + minutes_after_open * FIVE


def revisions(path):
    """Pre-DEMO review query: every REVISION anomaly of the Evidence Store (read-only)."""
    evidence = EvidenceStore(path, readonly=True)
    try:
        return [tuple(r) for r in evidence.db.execute(
            "SELECT symbol, timeframe, bar_start, kind FROM evidence_anomalies WHERE kind='REVISION' "
            "ORDER BY bar_start, anomaly_id")]
    finally:
        evidence.close()


class ActivationSimulationTests(Harness):
    def full_history_oracle(self, overrides, until):
        side, entry, stop, target, qty = self.position
        return oracle(side, entry, stop, target, qty, self.bars(overrides, until))

    def test_a_missed_touch_before_activation_is_not_retroactively_corrected(self):
        """The flag-OFF cycle skips the B2 stop touch and advances last_processed_at to B4. The first flag-ON cycle
        processes only bars AFTER that watermark: the historic touch is never revisited. Reconciliation against the
        full-history oracle exposes the divergence (the position should already be closed at 95)."""
        self.seed()
        history = {("XAUUSD", bar(2)): (100.0, 100.5, 94.0, 96.0)}
        # Historical pre-fix state: the old runtime had already advanced past the missed touch.
        # Seed that watermark explicitly; the corrected OFF runtime no longer produces this defect.
        self.seed_historical_watermark(bar(4))
        watermark = self.paper()[0].open_positions["XAUUSD"].last_processed_at
        self.assertEqual(watermark, bar(4))
        self.cycle(S2, history, flag=True)  # FIRST activation cycle
        account = self.paper()[0]
        self.assertIn("XAUUSD", account.open_positions)  # still open: no retroactive correction
        self.assertEqual(account.open_positions["XAUUSD"].last_processed_at, bar(7))
        expected = self.full_history_oracle(history, S2)
        self.assertEqual(expected[:3], (bar(2), 95, "stop"))  # what the full history says
        divergence = {"symbol": "XAUUSD", "should_have_closed_at": expected[0].isoformat(),
                      "oracle_exit": str(expected[1]), "oracle_pnl": str(expected[3]), "runtime_state": "OPEN"}
        self.assertEqual(Decimal(divergence["oracle_pnl"]), Decimal(-50))  # reported for Owner review; never auto-closed

    def test_b_touch_between_cycles_is_closed_by_the_first_activation_cycle(self):
        self.seed()
        history = {("XAUUSD", bar(6)): (100.0, 100.4, 94.5, 95.5)}  # 13:35, after the flag-OFF watermark (13:25)
        self.cycle(S1, history, flag=False)
        self.cycle(S2, history, flag=True)
        trade = self.assert_matches_oracle(history, until=S2)
        self.assertEqual((trade.exited_at, trade.exit_price), (bar(6), 95.0))  # retroactive to the activation slot

    def test_b2_same_data_with_flag_off_also_closes_it(self):
        self.seed()
        history = {("XAUUSD", bar(6)): (100.0, 100.4, 94.5, 95.5)}
        self.cycle(S1, history, flag=False)
        self.cycle(S2, history, flag=False)
        self.assert_matches_oracle(history, until=S2)

    def test_c_pending_order_across_activation(self):
        pending = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0, 1.0, 10000.0, 0.0,
                             S1)
        self.seed(side=None, pending=pending)
        self.cycle(S2, {}, flag=True)
        account, orders, fills, journal = self.paper()
        self.assertEqual((orders["pend"].status, next(iter(fills.values())).fill_timestamp), ("FILLED", bar(7)))
        skipped = sorted(e[2]["bar_start"] for e in journal if e[0] == "PENDING_NOT_EVALUATED")
        # P1: only bars strictly after the order's as_of (13:30) and before the gate bar are journaled; the fill uses
        # the current gated bar (13:40), never a past bar.
        self.assertEqual(skipped, [bar(6).isoformat()])

    def test_d_revision_after_activation_is_detectable_before_demo(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, {("XAUUSD", bar(2)): (100.0, 100.5, 94.0, 96.0)}, flag=True)  # provider rewrites 13:15
        self.assertEqual(revisions(self.ev), [("XAUUSD", "5m", bar(2).isoformat(), "REVISION")])
        self.assertIn("XAUUSD", self.paper()[0].open_positions)  # first committed bar stays authoritative

    def test_e_reconciliation_and_rollback_to_flag_off(self):
        self.seed()
        history = {("XAUUSD", bar(9)): (100.0, 111.0, 99.8, 109.0)}  # 13:50 touches TP 110
        self.cycle(S1, history, flag=False)
        self.cycle(S2, history, flag=True)  # activation: nothing to close yet
        self.assertIn("XAUUSD", self.paper()[0].open_positions)
        before_rollback = self.paper()[0].open_positions["XAUUSD"].last_processed_at
        self.cycle(S3, history, flag=False)  # OFF now preserves chronological management.
        account = self.paper()[0]
        self.assertNotIn("XAUUSD", account.open_positions)
        self.assertGreater(account.closed_trades[0].exited_at, before_rollback)
        store = Store(self.db)
        try:
            closes = store.db.execute("SELECT COUNT(*) FROM journal WHERE event_type='POSITION_CLOSED'").fetchone()[0]
        finally:
            store.close()
        self.assertEqual(closes, 1)
        evidence = EvidenceStore(self.ev, readonly=True)  # the Evidence Store is left intact after rollback
        try:
            self.assertGreater(evidence.db.execute("SELECT COUNT(*) FROM market_evidence").fetchone()[0], 0)
        finally:
            evidence.close()
        self.setUp()  # same history, flag kept ON: closed at 13:50, PnL reconciled with the oracle
        self.seed()
        self.cycle(S1, history, flag=False)
        self.cycle(S2, history, flag=True)
        self.cycle(S3, history, flag=True)
        trade = self.assert_matches_oracle(history, until=S3)
        self.assertEqual((trade.exited_at, trade.reason), (bar(9), "target"))
        account = self.paper()[0]
        self.assertAlmostEqual(account.equity, 10000.0 + trade.net_pnl)
        self.assertEqual(json.dumps(account.realized_pnl), json.dumps(trade.net_pnl))
