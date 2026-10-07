"""V2 Phase 6 / P6.1: conflict + Risk V2 + reservation under REAL concurrent processes (separate interpreters and
SQLite connections; file-signal + stdin barriers, as in the B2.3A / Phase 5 race tests).

A same setup_id, same symbol          -> one reservation; the loser reloads and BLOCKS DUPLICATE_SETUP
B different setup_ids, same direction -> one exposure;   loser BLOCKS SAME_DIRECTION_PENDING_CONFLICT
D opposite directions                 -> one exposure;   loser BLOCKS OPPOSITE_PENDING_CONFLICT (no hedge/reverse)
E cross-symbol NO_CONFLICT pair       -> Phase 5 aggregate CAS stays authoritative
F position closes during evaluation   -> stale BLOCK is discarded; recomputed NO_CONFLICT is reserved
G pending fills during evaluation     -> stale reload sees the position; no second exposure
H crash before commit                 -> no economic reservation, no decision row; retry reserves
I/J crash after commit                -> durable order + setup_id; retries are DUPLICATE_RUN
K legacy setup_id None                -> UNKNOWN across processes/restarts, never re-stamped
L unknown Phase 5 pending risk policy -> new risk still fails closed
"""
import json
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from dataclasses import replace
from pathlib import Path

from execution.conflict_path import EVENT
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from storage.database import Store
from test_phase5_risk_engine import ACCOUNT, ROOT, account, bar, pending, position

CHILD = textwrap.dedent('''
    import contextlib, json, os, sys
    from dataclasses import replace
    from fractions import Fraction
    from pathlib import Path
    from core.risk_policy import RISK_POLICY_V2_P5
    from execution import conflict_path
    from storage.database import Store
    from test_phase5_risk_engine import AT, EUR_SHORT, INS, XAU_LONG
    from test_phase6_conflict_engine import XAU_SHORT, candidate
    path, signal_path, mode, which, run_id, anchor, aggregate = sys.argv[1:]
    args = {"XAU_L": XAU_LONG, "XAU_S": XAU_SHORT, "EUR": EUR_SHORT}[which]
    policy = replace(RISK_POLICY_V2_P5, aggregate_portfolio_risk_fraction=Fraction(aggregate))
    def signal():
        Path(signal_path).write_text("ready", encoding="utf-8")
    def barrier():
        signal()
        assert sys.stdin.readline().strip() == "resume"
    if mode == "pause":  # evaluated on the loaded state, before ANY guarded write (order save or decision rows)
        state = {"done": False}
        original_save, original_rows = Store.save_paper, conflict_path.commit_decision_rows
        def once():
            if not state["done"]:
                state["done"] = True
                barrier()
        def save(self, broker, **kwargs):
            if kwargs.get("expected_state") is not None:
                once()
            return original_save(self, broker, **kwargs)
        def rows(*a, **k):
            once()
            return original_rows(*a, **k)
        Store.save_paper, conflict_path.commit_decision_rows = save, rows
    elif mode == "crash_before_commit":
        original = Store.transaction
        @contextlib.contextmanager
        def transaction(self):
            with original(self):
                yield
                assert self.db.in_transaction
                barrier()  # order + rows written, lock held, no commit
                os._exit(71)
        Store.transaction = transaction
    elif mode == "crash_after_commit":
        original = Store.save_paper
        def committed(self, broker, **kwargs):
            original(self, broker, **kwargs)
            os._exit(72)
        Store.save_paper = committed
    store = Store(path)
    result = conflict_path.submit_with_conflict_control(store, candidate(args, run_id, int(anchor)), INS[args[0]],
                                                        account_id="paper-main", as_of=AT, policy=policy)
    print(json.dumps({"status": result.status, "reason": result.reason, "attempts": result.attempts}), flush=True)
    store.close()
''')


class ConflictRaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p61-race-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "trading_floor.db"
        store = Store(self.path)
        store.save_paper(PaperBroker(account()))
        store.close()

    def spawn(self, mode, which, run_id, anchor=0, aggregate="23/1000"):
        signal = self.dir / f"{run_id}.{mode}.ready"
        child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(self.path), str(signal), mode, which, run_id,
                                  str(anchor), aggregate], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)

        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        self.addCleanup(cleanup)
        return child, signal

    def ready(self, child, signal):
        deadline = time.monotonic() + 20
        while not signal.exists():
            if child.poll() is not None:
                self.fail(f"child exited before barrier: {child.communicate(timeout=5)}")
            if time.monotonic() >= deadline:
                self.fail("child did not reach deterministic barrier")
            time.sleep(0.01)

    def result(self, child, resume=False, code=0):
        out, err = child.communicate("resume\n" if resume else None, timeout=30)
        self.assertEqual(child.returncode, code, err)
        return json.loads(out) if code == 0 else None

    def state(self):
        store = Store(self.path)
        try:
            held, orders, _ = store.load_paper(ACCOUNT)
            decisions = [(r[0], json.loads(r[1])["decision"]) for r in store.db.execute(
                "SELECT run_id, payload FROM journal WHERE event_type=? ORDER BY id", (EVENT,))]
            return {"orders": sorted((o.run_id, o.symbol, o.side, o.status, o.setup_id is not None)
                                     for o in orders.values()),
                    "positions": sorted((p.symbol, p.side, p.setup_id) for p in held.open_positions.values()),
                    "decisions": decisions}
        finally:
            store.close()

    def seed(self, item):
        store = Store(self.path)
        try:
            held, orders, fills = store.load_paper(ACCOUNT)
            broker = PaperBroker(held)
            broker.orders, broker.fills = orders, fills
            if hasattr(item, "order_id"):
                broker.orders[item.order_id] = item
            else:
                held.open_positions[item.symbol] = item
            store.save_paper(broker)
        finally:
            store.close()

    def mutate(self, action):
        """Another writer commits a real PAPER transition through the B2.3A guarded save."""
        store = Store(self.path)
        try:
            held, orders, fills = store.load_paper(ACCOUNT)
            expected = store.paper_state(held, orders, fills)
            broker = PaperBroker(held, None)
            broker.orders, broker.fills = orders, fills
            action(held, orders, broker)
            self.assertNotEqual(store.save_paper(broker, expected_state=expected), False)
        finally:
            store.close()

    def race(self, a_which, b_which, a_anchor=0, b_anchor=0, aggregate="23/1000"):
        a, signal = self.spawn("pause", a_which, "run-a", a_anchor, aggregate)
        self.ready(a, signal)  # A evaluated the EMPTY portfolio and holds an ALLOW + built order in memory
        b, _ = self.spawn("normal", b_which, "run-b", b_anchor, aggregate)
        self.assertEqual(self.result(b), {"status": "RESERVED", "reason": "approved", "attempts": 1})
        return self.result(a, resume=True)

    def test_a_same_setup_id_admits_one(self):
        self.assertEqual(self.race("XAU_L", "XAU_L"), {"status": "BLOCKED", "reason": "DUPLICATE_SETUP", "attempts": 2})
        self.assertEqual(self.state()["orders"], [("run-b", "XAUUSD", "LONG", "PENDING", True)])
        self.assertEqual(self.state()["decisions"], [("run-b", "RESERVED"), ("run-a", "BLOCKED")])

    def test_b_c_different_setup_ids_same_direction_admit_one(self):
        self.assertEqual(self.race("XAU_L", "XAU_L", a_anchor=1),
                         {"status": "BLOCKED", "reason": "SAME_DIRECTION_PENDING_CONFLICT", "attempts": 2})
        self.assertEqual(len(self.state()["orders"]), 1)

    def test_d_opposite_directions_never_hedge(self):
        self.assertEqual(self.race("XAU_L", "XAU_S"),
                         {"status": "BLOCKED", "reason": "OPPOSITE_PENDING_CONFLICT", "attempts": 2})
        self.assertEqual(self.state()["orders"], [("run-b", "XAUUSD", "SHORT", "PENDING", True)])

    def test_e_cross_symbol_aggregate_cas(self):
        self.assertEqual(self.race("XAU_L", "EUR"), {"status": "RESERVED", "reason": "approved", "attempts": 2})
        self.setUp()
        self.assertEqual(self.race("XAU_L", "EUR", aggregate="15/1000"),
                         {"status": "RISK_REJECTED", "reason": "PORTFOLIO_RISK_LIMIT", "attempts": 2})
        self.assertEqual([o[1] for o in self.state()["orders"]], ["EURUSD"])

    def test_f_position_closes_during_evaluation(self):
        self.seed(replace(position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0), setup_id="S-OLD"))
        a, signal = self.spawn("pause", "XAU_S", "run-a")
        self.ready(a, signal)  # A computed BLOCK (opposite position) but has not journaled it
        self.mutate(lambda held, orders, broker: TradeManager(held, broker).process_bar(bar("XAUUSD", 2590.0)))
        self.assertEqual(self.result(a, resume=True), {"status": "RESERVED", "reason": "approved", "attempts": 2})
        state = self.state()
        self.assertEqual((state["positions"], state["orders"]), ([], [("run-a", "XAUUSD", "SHORT", "PENDING", True)]))
        self.assertEqual(state["decisions"], [("run-a", "RESERVED")])  # the stale BLOCK left no trace

    def test_g_pending_fills_during_evaluation(self):
        self.seed(replace(pending("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0), setup_id="S-OLD"))
        a, signal = self.spawn("pause", "XAU_L", "run-a", anchor=1)
        self.ready(a, signal)  # A computed BLOCK against the PENDING order
        self.mutate(lambda held, orders, broker: PaperBroker(held, None).process_next_bar(
            orders["ord-XAUUSD"], bar("XAUUSD", 2650.0)))
        self.assertEqual(self.result(a, resume=True),
                         {"status": "BLOCKED", "reason": "SAME_DIRECTION_POSITION_CONFLICT", "attempts": 2})
        state = self.state()
        self.assertEqual(state["positions"], [("XAUUSD", "LONG", "S-OLD")])  # identity inherited at fill
        self.assertEqual([o[3] for o in state["orders"]], ["FILLED"])  # no second exposure

    def test_h_crash_before_commit(self):
        before = self.state()
        a, signal = self.spawn("crash_before_commit", "XAU_L", "run-a")
        self.ready(a, signal)
        self.assertEqual(self.state(), before)  # uncommitted order + decision rows invisible
        self.result(a, resume=True, code=71)
        self.assertEqual(self.state(), before)
        retry, _ = self.spawn("normal", "XAU_L", "run-a")
        self.assertEqual(self.result(retry)["status"], "RESERVED")
        self.assertEqual(self.state()["decisions"], [("run-a", "RESERVED")])

    def test_i_j_crash_after_commit_is_durable_and_idempotent(self):
        a, _ = self.spawn("crash_after_commit", "XAU_L", "run-a")
        self.result(a, code=72)
        committed = self.state()
        self.assertEqual(committed["orders"], [("run-a", "XAUUSD", "LONG", "PENDING", True)])  # setup_id durable
        self.assertEqual(committed["decisions"], [("run-a", "RESERVED")])
        for _ in range(2):
            retry, _ = self.spawn("normal", "XAU_L", "run-a")
            self.assertEqual(self.result(retry), {"status": "DUPLICATE_RUN", "reason": "run_id_already_submitted",
                                                  "attempts": 1})
            self.assertEqual(self.state(), committed)
        other, _ = self.spawn("normal", "XAU_L", "run-b")  # same setup in a new run, after restart
        self.assertEqual(self.result(other)["reason"], "DUPLICATE_SETUP")

    def test_k_legacy_setup_id_stays_unknown(self):
        self.seed(position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0))  # setup_id None
        children = [self.spawn("normal", which, f"run-{n}") for n, which in enumerate(("XAU_L", "XAU_S"))]
        for child, _ in children:
            self.assertEqual(self.result(child)["reason"], "UNKNOWN_EXISTING_SETUP_ID")
        self.assertEqual(self.state()["positions"], [("XAUUSD", "LONG", None)])  # never re-stamped

    def test_l_unknown_phase5_pending_policy_fails_closed(self):
        self.seed(replace(pending("EURUSD", "SHORT", 1.085, 1.096, 1.052, 100.0, version=None), setup_id="E1"))
        child, _ = self.spawn("normal", "XAU_L", "run-a")
        self.assertEqual(self.result(child), {"status": "RISK_REJECTED", "reason": "UNKNOWN_PENDING_RISK_POLICY",
                                              "attempts": 1})
        self.assertEqual(self.state()["decisions"], [("run-a", "RISK_REJECTED")])


if __name__ == "__main__":
    unittest.main()
