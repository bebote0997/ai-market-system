"""V2 Phase 8 / P8.1B: isolated certification of the chronological SL/TP catch-up (``v2_position_catch_up`` ON ONLY
inside these tests, on temporary databases). Every other flag stays OFF; the runtime default is untouched.

The oracle below is written independently of ``execution.trade_manager``: Decimal arithmetic and its own reading of the
documented precedence (per bar, in time order: open beyond SL -> exit at open; open beyond TP -> exit at open; low/high
touches SL -> exit at SL; touches TP -> exit at TP; SL before TP when both are touched in the same bar).
"""
import contextlib
import json
import random
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from ai.provider import FakeAIProvider
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from runtime.config import RuntimeConfig
from storage.database import Store
from test_demo_runner import patched_scouts
from test_runtime_catch_up import BASE, Bars, make_runtime

ROOT = Path(__file__).resolve().parent
ACCOUNT = "paper-main"
FIVE = timedelta(minutes=5)
OPEN = datetime(2026, 1, 15, 13, 5, tzinfo=timezone.utc)  # position opened_at (bars after it are managed)
SLOT = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)  # closed bars 13:10, 13:15, 13:20, 13:25
B1, B2, B3, B4 = (OPEN + i * FIVE for i in range(1, 5))
FLAT = (100.0, 100.05, 99.95, 100.0)


# ---------------------------------------------------------------- independent oracle
def oracle(side, entry, stop, target, qty, bars):
    """(exit_time, exit_price, reason, pnl) or None while open. ``bars`` = [(start, o, h, l, c)] in time order."""
    entry, stop, target, qty = (Decimal(str(v)) for v in (entry, stop, target, qty))
    for start, o, h, l, c in bars:
        o, h, l = Decimal(str(o)), Decimal(str(h)), Decimal(str(l))
        if side == "LONG":
            beyond_stop, beyond_target, hit_stop, hit_target = o <= stop, o >= target, l <= stop, h >= target
        else:
            beyond_stop, beyond_target, hit_stop, hit_target = o >= stop, o <= target, h >= stop, l <= target
        if beyond_stop:
            price, reason = o, "stop"
        elif beyond_target:
            price, reason = o, "target"
        elif hit_stop:
            price, reason = stop, "stop"
        elif hit_target:
            price, reason = target, "target"
        else:
            continue
        pnl = (price - entry) * qty if side == "LONG" else (entry - price) * qty
        return start, price, reason, pnl
    return None


# ---------------------------------------------------------------- isolated harness
class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p81b-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "trading_floor.db"
        self.ev = Path(self.tmp.name) / "market_evidence.db"

    def seed(self, side="LONG", entry=100.0, stop=95.0, target=110.0, qty=10.0, symbol="XAUUSD", pending=None):
        store = Store(self.db)
        try:
            broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
            if side is not None:
                order = PaperOrder("1.0", "ord-1", "run-open", symbol, side, qty, entry, stop, target, 1.0, 10000.0,
                                   0.0, OPEN - FIVE, status="FILLED")
                broker.orders[order.order_id] = order
                broker.account.open_positions[symbol] = PaperPosition(
                    "1.0", "pos-1", order.order_id, order.run_id, symbol, side, qty, entry, entry, stop, target, OPEN,
                    last_price=entry, contract_multiplier=1.0)
            if pending is not None:
                broker.orders[pending.order_id] = pending
            store.save_paper(broker)
        finally:
            store.close()
        self.position = (side, entry, stop, target, qty)

    def cycle(self, slot=SLOT, overrides=None, flag=True, ai=None, recovery=120):
        runtime = make_runtime(self.db, self.ev if flag else None, slot, flag=flag, overrides=overrides,
                               recovery=recovery)
        if ai is not None:
            runtime.ai_provider = ai
        try:
            return runtime.run_cycle("XAUUSD", slot)
        finally:
            runtime.close()

    def paper(self):
        store = Store(self.db)
        try:
            account, orders, fills = store.load_paper(ACCOUNT)
            journal = [(r["event_type"], r["source"], json.loads(r["payload"] or "{}"))
                       for r in store.db.execute("SELECT event_type,source,payload FROM journal ORDER BY id")]
            return account, orders, fills, journal
        finally:
            store.close()

    @staticmethod
    def bars(overrides, until=SLOT, start=B1):
        out, stamp = [], start
        while stamp + FIVE <= until:
            o, h, l, c = overrides.get(("XAUUSD", stamp), FLAT)
            out.append((stamp, o, h, l, c))
            stamp += FIVE
        return out

    def assert_matches_oracle(self, overrides, until=SLOT, label=""):
        side, entry, stop, target, qty = self.position
        expected = oracle(side, entry, stop, target, qty, self.bars(overrides, until))
        account, _, _, journal = self.paper()
        if expected is None:
            self.assertIn("XAUUSD", account.open_positions, label)
            self.assertEqual(account.closed_trades, [], label)
            return None
        exit_time, price, reason, pnl = expected
        self.assertNotIn("XAUUSD", account.open_positions, label)
        self.assertEqual(len(account.closed_trades), 1, label)
        trade = account.closed_trades[0]
        self.assertEqual((trade.exited_at, trade.reason), (exit_time, reason), label)
        self.assertEqual(Decimal(str(trade.exit_price)), price, label)
        self.assertEqual(Decimal(str(trade.net_pnl)).quantize(Decimal("1e-9")), pnl.quantize(Decimal("1e-9")), label)
        self.assertAlmostEqual(account.realized_pnl, float(pnl), places=9)
        self.assertAlmostEqual(account.equity, 10000.0 + float(pnl), places=9)
        closes = [e for e in journal if e[0] == "POSITION_CLOSED"]
        self.assertEqual(len(closes), 1, label)  # exactly once
        return trade


# ---------------------------------------------------------------- scenarios 1-6: intermediate touches, gaps, same bar
class IntermediateTouchTests(Harness):
    CASES = {
        "1_long_sl_mid": ("LONG", 100.0, 95.0, 110.0, {("XAUUSD", B2): (100.0, 100.5, 94.0, 96.0)}),
        "2_long_tp_mid": ("LONG", 100.0, 95.0, 110.0, {("XAUUSD", B2): (100.0, 111.0, 99.5, 108.0)}),
        "3_short_sl_mid": ("SHORT", 100.0, 105.0, 90.0, {("XAUUSD", B3): (100.0, 106.0, 99.5, 104.0)}),
        "4_short_tp_mid": ("SHORT", 100.0, 105.0, 90.0, {("XAUUSD", B2): (100.0, 100.5, 89.0, 91.0)}),
        "5a_long_gap_below_sl": ("LONG", 100.0, 95.0, 110.0, {("XAUUSD", B2): (93.0, 94.0, 92.0, 93.5)}),
        "5b_long_gap_above_tp": ("LONG", 100.0, 95.0, 110.0, {("XAUUSD", B2): (112.0, 113.0, 111.5, 112.5)}),
        "5c_short_gap_above_sl": ("SHORT", 100.0, 105.0, 90.0, {("XAUUSD", B2): (107.0, 108.0, 106.5, 107.5)}),
        "5d_short_gap_below_tp": ("SHORT", 100.0, 105.0, 90.0, {("XAUUSD", B2): (88.0, 89.0, 87.5, 88.5)}),
        "6a_long_sl_and_tp_same_bar": ("LONG", 100.0, 95.0, 110.0, {("XAUUSD", B2): (100.0, 111.0, 94.0, 100.0)}),
        "6b_short_sl_and_tp_same_bar": ("SHORT", 100.0, 105.0, 90.0, {("XAUUSD", B3): (100.0, 106.0, 89.0, 100.0)}),
        "no_touch_stays_open": ("LONG", 100.0, 95.0, 110.0, {}),
    }
    EXPECTED = {  # (exit bar, exit price, reason) as documented; the oracle must agree
        "1_long_sl_mid": (B2, 95.0, "stop"), "2_long_tp_mid": (B2, 110.0, "target"),
        "3_short_sl_mid": (B3, 105.0, "stop"), "4_short_tp_mid": (B2, 90.0, "target"),
        "5a_long_gap_below_sl": (B2, 93.0, "stop"), "5b_long_gap_above_tp": (B2, 112.0, "target"),
        "5c_short_gap_above_sl": (B2, 107.0, "stop"), "5d_short_gap_below_tp": (B2, 88.0, "target"),
        "6a_long_sl_and_tp_same_bar": (B2, 95.0, "stop"), "6b_short_sl_and_tp_same_bar": (B3, 105.0, "stop"),
        "no_touch_stays_open": None,
    }

    def test_flag_on_matches_oracle_and_documented_expectation(self):
        for name, (side, entry, stop, target, overrides) in self.CASES.items():
            with self.subTest(case=name):
                self.setUp()
                self.seed(side, entry, stop, target)
                self.assertNotEqual(self.cycle(overrides=overrides), "ERROR")
                trade = self.assert_matches_oracle(overrides, label=name)
                expected = self.EXPECTED[name]
                if expected is None:
                    self.assertIsNone(trade)
                else:
                    self.assertEqual((trade.exited_at, trade.exit_price, trade.reason), expected)
                self.tmp.cleanup()

    def test_flag_off_default_runtime_misses_intermediate_touches(self):
        """HIGH-8.1 path B (default runtime): the same data leaves the position open (newest bar only)."""
        for name in ("1_long_sl_mid", "2_long_tp_mid", "3_short_sl_mid", "4_short_tp_mid"):
            with self.subTest(case=name):
                self.setUp()
                side, entry, stop, target, overrides = self.CASES[name]
                self.seed(side, entry, stop, target)
                self.cycle(overrides=overrides, flag=False)
                account = self.paper()[0]
                self.assertIn("XAUUSD", account.open_positions)  # touch in B2/B3 never seen
                self.assertEqual(account.closed_trades, [])
                self.tmp.cleanup()


# ---------------------------------------------------------------- 7, 15: cadence jumps and repeated cycles
class CadenceAndRepeatTests(Harness):
    def test_7_cadence_jump_processes_each_skipped_bar_once(self):
        self.seed()
        overrides = {("XAUUSD", OPEN + 7 * FIVE): (100.0, 100.4, 94.5, 95.5)}  # 13:40 touches SL 95
        seen, original = [], TradeManager.process_bar

        def spy(manager, bar):
            seen.append(bar["timestamp"])
            return original(manager, bar)
        first, later = datetime(2026, 1, 15, 13, 15, tzinfo=timezone.utc), datetime(2026, 1, 15, 13, 45, tzinfo=timezone.utc)
        with patch.object(TradeManager, "process_bar", spy):
            self.cycle(first, overrides)
            self.cycle(later, overrides)  # the 13:30 cycle never ran
        self.assertEqual(seen, [OPEN + i * FIVE for i in range(1, 8)])  # 13:10 .. 13:40, each exactly once
        self.assert_matches_oracle(overrides, until=later)

    def test_15_same_cycle_repeated_never_duplicates_close(self):
        self.seed()
        overrides = IntermediateTouchTests.CASES["1_long_sl_mid"][4]
        self.cycle(overrides=overrides)
        self.assertEqual(self.cycle(overrides=overrides), "DUPLICATE")  # same slot, new process
        self.cycle(SLOT + timedelta(minutes=15), overrides)  # next slot: nothing left to close
        self.assert_matches_oracle(overrides)


# ---------------------------------------------------------------- 11, 12, 13: pending, evidence failure, AI failure
class InteractionTests(Harness):
    def test_11_pending_order_fills_only_on_current_gated_bar(self):
        pending = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0, 1.0, 10000.0, 0.0,
                             OPEN)
        self.seed(side=None, pending=pending)
        self.cycle(overrides={("XAUUSD", B2): (100.0, 100.5, 94.0, 96.0)})
        account, orders, fills, journal = self.paper()
        fill = next(iter(fills.values()))
        self.assertEqual((orders["pend"].status, fill.fill_timestamp), ("FILLED", B4))  # never an intermediate bar
        not_evaluated = sorted(e[2]["bar_start"] for e in journal if e[0] == "PENDING_NOT_EVALUATED")
        self.assertEqual(not_evaluated, [b.isoformat() for b in (B1, B2, B3)])
        self.assertEqual(account.open_positions["XAUUSD"].opened_at, B4)  # B2 dip before the fill is irrelevant

    def test_12_evidence_failure_fails_closed_then_catches_up_to_the_true_bar(self):
        self.seed()
        overrides = IntermediateTouchTests.CASES["1_long_sl_mid"][4]
        with patch("data.market_evidence.MarketEvidenceEngine.ingest_snapshot", side_effect=OSError("evidence disk")):
            self.cycle(overrides=overrides)
        account, _, _, journal = self.paper()
        self.assertIn("XAUUSD", account.open_positions)  # nothing economic while evidence is unavailable
        self.assertIn("EVIDENCE_UNAVAILABLE", [e[0] for e in journal])
        self.cycle(SLOT + timedelta(minutes=15), overrides)
        self.assert_matches_oracle(overrides, until=SLOT + timedelta(minutes=15))  # closed at B2, not the newest bar

    def test_13_ai_failure_does_not_stop_management_or_grant_orders(self):
        self.seed()
        overrides = IntermediateTouchTests.CASES["2_long_tp_mid"][4]
        with patched_scouts("LONG"):
            self.cycle(overrides=overrides, ai=FakeAIProvider(raises=RuntimeError("provider down")))
        account, orders, _, _ = self.paper()
        self.assert_matches_oracle(overrides)
        self.assertEqual(list(orders), ["ord-1"])  # no new order despite a VALID setup


# ---------------------------------------------------------------- 17 (extra risk): provider revises a committed bar
class RevisionTests(Harness):
    def test_17_revised_history_keeps_first_committed_bar(self):
        """Certified Phase 2 rule: committed evidence is authoritative. A later snapshot that revises an already
        committed bar (now showing a stop touch) is recorded as a REVISION anomaly and NOT applied; the position stays
        open. LOW-8.6: the revision is visible only in evidence_anomalies, not in the trading journal."""
        self.seed()
        self.cycle()  # B1..B4 committed flat
        self.cycle(SLOT + timedelta(minutes=15), IntermediateTouchTests.CASES["1_long_sl_mid"][4])
        account, _, _, journal = self.paper()
        self.assertEqual((list(account.open_positions), account.closed_trades), (["XAUUSD"], []))
        from storage.evidence_store import EvidenceStore
        evidence = EvidenceStore(self.ev, readonly=True)
        try:
            kinds = dict(evidence.db.execute("SELECT kind, count(*) FROM evidence_anomalies GROUP BY kind").fetchall())
        finally:
            evidence.close()
        self.assertEqual(kinds.get("REVISION"), 1)
        # P8.1B pinned LOW-8.6 ("the revision is not in the trading journal"). P8.4 R4 / DEC-8.8 explicitly supersedes
        # that: the revision is now journaled once as EVIDENCE_REVISION (observability only); the first committed bar
        # stays authoritative and the position stays open (asserted above).
        self.assertEqual([e[0] for e in journal if "REVISION" in e[0]], ["EVIDENCE_REVISION"])


# ---------------------------------------------------------------- 14: randomized oracle comparison
class OracleComparisonTests(Harness):
    def test_14_randomized_bar_sequences_match_oracle(self):
        rng = random.Random(20261007)
        for case in range(40):
            with self.subTest(case=case):
                self.setUp()
                side = rng.choice(("LONG", "SHORT"))
                stop, target = (95.0, 110.0) if side == "LONG" else (105.0, 90.0)
                overrides, price = {}, 100.0
                for i in range(1, 5):
                    o = round(price + rng.uniform(-6, 6), 2)
                    h = round(max(o, price) + rng.uniform(0, 7), 2)
                    l = round(min(o, price) - rng.uniform(0, 7), 2)
                    c = round(rng.uniform(l, h), 2)
                    overrides[("XAUUSD", OPEN + i * FIVE)] = (o, h, l, c)
                    price = c
                self.seed(side, 100.0, stop, target, qty=round(rng.uniform(0.5, 20), 3))
                self.cycle(overrides=overrides)
                self.assert_matches_oracle(overrides, label=f"case {case}: {side} {overrides}")
                self.tmp.cleanup()


# ---------------------------------------------------------------- 16: traceability order -> fill -> position -> close
class TraceabilityTests(Harness):
    def test_16_full_chain_ids_are_linked(self):
        self.seed(side=None)
        first = datetime(2026, 1, 15, 13, 15, tzinfo=timezone.utc)
        with patched_scouts("LONG"):
            self.assertEqual(self.cycle(first), "PLAN_READY")  # V1 plan: entry 100, SL 90 (scout support), TP 130
        _, orders, _, _ = self.paper()
        order = next(iter(orders.values()))
        self.assertEqual((order.stop, order.target), (90.0, 130.0))
        history = {("XAUUSD", first + 6 * FIVE): (100.0, 100.5, 89.0, 92.0)}  # ONE consistent history: 13:45 dip
        self.cycle(first + timedelta(minutes=15), history)  # 13:30 cycle: fills at the 13:25 gate bar open (100)
        self.assertEqual(self.paper()[0].open_positions["XAUUSD"].opened_at, first + 2 * FIVE)
        self.cycle(first + timedelta(minutes=45), history)  # 14:00 cycle (13:45 missed): catch-up 13:30..13:55
        account, orders, fills, journal = self.paper()
        self.assertEqual((account.closed_trades[0].exited_at, account.closed_trades[0].exit_price,
                          account.closed_trades[0].reason), (first + 6 * FIVE, 90.0, "stop"))
        order = orders[order.order_id]
        self.assertEqual(order.status, "FILLED")
        fill = next(iter(fills.values()))
        self.assertEqual(fill.order_id, order.order_id)
        self.assertEqual(len(account.closed_trades), 1)
        trade = account.closed_trades[0]
        events = {e[0]: e for e in journal}
        self.assertIn("ORDER_SUBMITTED", events)
        self.assertIn("ORDER_FILLED", events)
        self.assertIn("POSITION_OPENED", events)
        self.assertIn("POSITION_CLOSED", events)
        store = Store(self.db)
        try:
            position = json.loads(store.db.execute("SELECT payload FROM paper_positions").fetchone()[0])
            rows = {r["event_type"]: (r["run_id"], r["source"]) for r in store.db.execute(
                "SELECT event_type,run_id,source FROM journal WHERE event_type IN "
                "('ORDER_SUBMITTED','ORDER_FILLED','POSITION_OPENED','POSITION_CLOSED')")}
        finally:
            store.close()
        self.assertEqual((position["origin_order_id"], position["status"]), (order.order_id, "CLOSED"))
        self.assertEqual(trade.position_id, position["position_id"])
        self.assertEqual({run for run, _ in rows.values()}, {order.run_id})  # one run_id across the whole chain
        self.assertEqual((rows["ORDER_FILLED"][1], rows["POSITION_OPENED"][1], rows["POSITION_CLOSED"][1]),
                         (fill.fill_id, position["position_id"], trade.trade_id))


# ---------------------------------------------------------------- 8, 9, 10: real processes
CHILD = textwrap.dedent('''
    import contextlib, json, os, sys
    from datetime import datetime
    from pathlib import Path
    from execution.trade_manager import TradeManager
    from storage.database import Store
    from test_runtime_catch_up import make_runtime
    db, ev, signal_path, mode, slot, overrides, recovery = sys.argv[1:]
    slot = datetime.fromisoformat(slot)
    overrides = {(s, datetime.fromisoformat(t)): tuple(v) for s, t, v in json.loads(overrides)}
    closed = {"yes": False}
    original_process = TradeManager.process_bar
    def process(self, bar):
        result = original_process(self, bar)
        if result:
            closed["yes"] = True
        return result
    TradeManager.process_bar = process
    def barrier():
        Path(signal_path).write_text("ready", encoding="utf-8")
        assert sys.stdin.readline().strip() == "resume"
    if mode == "crash_before_close_commit":
        original_tx = Store.transaction
        @contextlib.contextmanager
        def tx(self):
            with original_tx(self):
                yield
                if closed["yes"]:
                    os._exit(81)  # close written inside the transaction, never committed
        Store.transaction = tx
    elif mode == "crash_after_close_commit":
        original_save = Store.save_paper
        def save(self, broker, **kwargs):
            outcome = original_save(self, broker, **kwargs)
            if closed["yes"]:
                os._exit(82)  # close committed; process dies before the cycle finishes
            return outcome
        Store.save_paper = save
    elif mode == "pause_before_close_save":
        original_save = Store.save_paper
        def save(self, broker, **kwargs):
            if closed["yes"] and kwargs.get("expected_state") is not None:
                barrier()
            return original_save(self, broker, **kwargs)
        Store.save_paper = save
    runtime = make_runtime(db, ev, slot, overrides=overrides, recovery=int(recovery))
    try:
        print(json.dumps({"status": runtime.run_cycle("XAUUSD", slot)}), flush=True)
    finally:
        runtime.close()
''')


class ProcessTests(Harness):
    OVERRIDES = IntermediateTouchTests.CASES["1_long_sl_mid"][4]

    def spawn(self, mode, slot=SLOT, recovery=120):
        signal = Path(self.tmp.name) / f"{mode}.ready"
        encoded = json.dumps([[s, t.isoformat(), list(v)] for (s, t), v in self.OVERRIDES.items()])
        child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(self.db), str(self.ev), str(signal), mode,
                                  slot.isoformat(), encoded, str(recovery)], cwd=ROOT, stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        self.addCleanup(cleanup)
        return child, signal

    def finish(self, child, code=0, resume=False):
        out, err = child.communicate("resume\n" if resume else None, timeout=90)
        self.assertEqual(child.returncode, code, err[-2000:])
        return json.loads(out.strip().splitlines()[-1])["status"] if code == 0 else None

    def wait(self, child, signal):
        deadline = time.monotonic() + 60
        while not signal.exists():
            if child.poll() is not None:
                self.fail(f"child exited early: {child.communicate(timeout=5)}")
            if time.monotonic() > deadline:
                self.fail("barrier not reached")
            time.sleep(0.01)

    def test_8_crash_before_close_commit_then_restart_closes_once_at_true_bar(self):
        self.seed()
        self.finish(self.spawn("crash_before_close_commit")[0], code=81)
        self.assertIn("XAUUSD", self.paper()[0].open_positions)  # nothing committed
        self.assertEqual(self.finish(self.spawn("normal", SLOT + timedelta(minutes=15), recovery=0)[0]), "NO_SETUP")
        self.assert_matches_oracle(self.OVERRIDES, until=SLOT + timedelta(minutes=15))

    def test_9_crash_after_close_commit_restart_never_double_closes(self):
        self.seed()
        self.finish(self.spawn("crash_after_close_commit")[0], code=82)
        self.assert_matches_oracle(self.OVERRIDES)  # durable close at B2
        for minutes in (15, 30):
            self.finish(self.spawn("normal", SLOT + timedelta(minutes=minutes), recovery=0)[0])
        self.assert_matches_oracle(self.OVERRIDES)  # still exactly one close and one realized PnL

    def test_10_two_processes_competing_for_the_same_position(self):
        self.seed()
        a, signal = self.spawn("pause_before_close_save")
        self.wait(a, signal)  # A computed the close and holds it in memory
        b = self.spawn("normal", SLOT + timedelta(minutes=15), recovery=120)[0]  # B recovers A's stale lock
        self.finish(b)
        self.assertEqual(self.finish(a, resume=True), "ERROR")  # A lost ownership: nothing written
        self.assert_matches_oracle(self.OVERRIDES, until=SLOT + timedelta(minutes=15))


class IsolationTests(unittest.TestCase):
    def test_flags_remain_off_by_default(self):
        for config in (RuntimeConfig(), RuntimeConfig.from_env()):
            self.assertEqual((config.v2_position_catch_up, config.v2_ai_call_audit, config.v2_ai_resilience),
                             (False, False, False))


if __name__ == "__main__":
    unittest.main()
