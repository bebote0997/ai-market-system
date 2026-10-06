"""V2 Phase 5 / P5.1 (DEC-5.4): risk reservation under REAL concurrent processes (separate interpreters and SQLite
connections). Deterministic interleavings use file signals + stdin barriers, as in the B2.3A writer tests.

A cross-symbol race that would exceed the aggregate limit: the loser re-evaluates and is rejected.
B cross-symbol race that fits: both reserved, the loser after one fresh re-evaluation.
C crash before commit: nothing reserved; the lock is released to the next process; the retry reserves.
D crash after commit: exactly one reservation; the retry returns it (idempotent).
E stale writer: PAPER state changed by another writer between evaluation and save; the save is refused and the
  evaluation is redone on the new state.
P5.1C: reserved orders carry durable policy identity across processes, crashes and restarts; an identity-less
(legacy / V1 / policy D) PENDING order blocks all NEW risk in every process and is never reinterpreted.
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

from execution.paper_broker import PaperBroker
from execution.risk_reservation import EVENT
from storage.database import Store
from core.risk_policy import RISK_POLICY_V2
from execution.contracts import PaperOrder
from test_phase5_risk_engine import ACCOUNT, AT, ROOT, account

CHILD = textwrap.dedent('''
    import contextlib, json, os, sys
    from dataclasses import replace
    from fractions import Fraction
    from pathlib import Path
    from core.risk_policy import RISK_POLICY_V2_P5
    from execution.risk_reservation import reserve_and_submit
    from storage.database import Store
    from test_phase5_risk_engine import ACCOUNT, AT, EUR_SHORT, INS, XAU_LONG, f3_plan, report
    path, signal_path, mode, which, run_id, aggregate = sys.argv[1:]
    args = {"XAU": XAU_LONG, "EUR": EUR_SHORT}[which]
    policy = replace(RISK_POLICY_V2_P5, aggregate_portfolio_risk_fraction=Fraction(aggregate))
    store = Store(path)
    def signal():
        Path(signal_path).write_text("ready", encoding="utf-8")
    if mode == "pause":
        original = Store.save_paper
        def paused(self, broker, **kwargs):
            if not getattr(paused, "done", False):
                paused.done = True
                signal()  # Evaluated and built the order on the loaded state; not yet saved.
                assert sys.stdin.readline().strip() == "resume"
            return original(self, broker, **kwargs)
        Store.save_paper = paused
    elif mode in ("crash_before_commit", "contend"):
        original = Store.transaction
        @contextlib.contextmanager
        def transaction(self):
            if mode == "contend":
                signal()  # About to request the write lock the crashing process holds.
            with original(self):
                yield
                if mode == "crash_before_commit":
                    assert self.db.in_transaction
                    signal()  # Order + decision written, lock held, no commit.
                    assert sys.stdin.readline().strip() == "resume"
                    os._exit(71)
        Store.transaction = transaction
    elif mode == "crash_after_commit":
        original = Store.save_paper
        def committed(self, broker, **kwargs):
            outcome = original(self, broker, **kwargs)
            os._exit(72)  # Durable commit happened; the caller never learns the result.
        Store.save_paper = committed
    result = reserve_and_submit(store, report(f3_plan(*args, run_id=run_id)), INS[args[0]], account_id=ACCOUNT,
                                as_of=AT, setup_id="setup-" + run_id, policy=policy)
    print(json.dumps({"status": result.status, "reason": result.reason, "attempts": result.attempts}), flush=True)
    store.close()
''')


class RiskConcurrencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p5-race-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "trading_floor.db"
        store = Store(self.path)
        store.save_paper(PaperBroker(account()))
        store.close()

    def spawn(self, mode, which, run_id, aggregate="23/1000"):
        signal = self.dir / (run_id + "." + mode + ".ready")
        child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(self.path), str(signal), mode, which, run_id,
                                  aggregate], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
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
            _, orders, _ = store.load_paper(ACCOUNT)
            decisions = [(r["run_id"], json.loads(r["payload"])["risk_status"]) for r in store.journal(event=EVENT)]
            return sorted((o.run_id, o.symbol, o.status) for o in orders.values()), sorted(decisions)
        finally:
            store.close()

    def race(self, aggregate):
        a, signal = self.spawn("pause", "XAU", "run-a", aggregate)
        self.ready(a, signal)  # A evaluated an EMPTY portfolio and holds a built order in memory.
        b, _ = self.spawn("normal", "EUR", "run-b", aggregate)
        self.assertEqual(self.result(b), {"status": "RESERVED", "reason": "approved", "attempts": 1})
        return self.result(a, resume=True)

    def test_a_cross_symbol_race_exceeding_aggregate_rejects_loser(self):
        # Test policy 1.5%: each 1% x 8/7 reservation fits alone (1.143%), both together (2.27%) do not.
        loser = self.race("15/1000")
        self.assertEqual(loser, {"status": "REJECTED", "reason": "PORTFOLIO_RISK_LIMIT", "attempts": 2})
        self.assertEqual(self.state(), ([("run-b", "EURUSD", "PENDING")],
                                        [("run-a", "REJECTED"), ("run-b", "APPROVED")]))

    def test_b_cross_symbol_race_that_fits_reserves_both(self):
        loser = self.race("23/1000")
        self.assertEqual(loser, {"status": "RESERVED", "reason": "approved", "attempts": 2})
        orders, decisions = self.state()
        self.assertEqual(orders, [("run-a", "XAUUSD", "PENDING"), ("run-b", "EURUSD", "PENDING")])
        self.assertEqual(decisions, [("run-a", "APPROVED"), ("run-b", "APPROVED")])  # no stale-save leftovers

    def test_same_symbol_race_admits_one(self):
        a, signal = self.spawn("pause", "XAU", "run-a")
        self.ready(a, signal)
        b, _ = self.spawn("normal", "XAU", "run-b")
        self.assertEqual(self.result(b)["status"], "RESERVED")
        self.assertEqual(self.result(a, resume=True),
                         {"status": "REJECTED", "reason": "SYMBOL_EXPOSURE_LIMIT", "attempts": 2})
        self.assertEqual(self.state()[0], [("run-b", "XAUUSD", "PENDING")])

    def test_c_crash_before_commit_reserves_nothing_and_releases_lock(self):
        before = self.state()
        a, a_signal = self.spawn("crash_before_commit", "XAU", "run-a")
        self.ready(a, a_signal)
        self.assertEqual(self.state(), before)  # uncommitted reservation invisible
        b, b_signal = self.spawn("contend", "EUR", "run-b")
        self.ready(b, b_signal)
        self.result(a, resume=True, code=71)
        self.assertEqual(self.result(b)["status"], "RESERVED")
        self.assertEqual(self.state()[0], [("run-b", "EURUSD", "PENDING")])
        retry, _ = self.spawn("normal", "XAU", "run-a")  # restart of the crashed submission
        self.assertEqual(self.result(retry)["status"], "RESERVED")
        orders, decisions = self.state()
        self.assertEqual(orders, [("run-a", "XAUUSD", "PENDING"), ("run-b", "EURUSD", "PENDING")])
        self.assertEqual(decisions, [("run-a", "APPROVED"), ("run-b", "APPROVED")])

    def test_d_crash_after_commit_retry_is_idempotent(self):
        a, _ = self.spawn("crash_after_commit", "XAU", "run-a")
        self.result(a, code=72)
        committed = self.state()
        self.assertEqual(committed, ([("run-a", "XAUUSD", "PENDING")], [("run-a", "APPROVED")]))
        for _ in range(2):
            retry, _ = self.spawn("normal", "XAU", "run-a")
            self.assertEqual(self.result(retry), {"status": "DUPLICATE_RUN", "reason": "run_id_already_submitted",
                                                  "attempts": 1})
            self.assertEqual(self.state(), committed)

    def test_e_stale_writer_forces_reevaluation_on_new_state(self):
        a, signal = self.spawn("pause", "XAU", "run-a")
        self.ready(a, signal)  # A approved against equity 10000.
        writer = Store(self.path)  # Another writer realizes a loss that crosses the drawdown gate.
        try:
            held, orders, fills = writer.load_paper(ACCOUNT)
            broker = PaperBroker(replace(held, equity=9400.0, realized_pnl=-600.0))
            broker.orders, broker.fills = orders, fills
            self.assertNotEqual(writer.save_paper(broker, expected_state=writer.paper_state(held, orders, fills)), False)
        finally:
            writer.close()
        self.assertEqual(self.result(a, resume=True),
                         {"status": "REJECTED", "reason": "ACCOUNT_DRAWDOWN_LIMIT", "attempts": 2})
        self.assertEqual(self.state(), ([], [("run-a", "REJECTED")]))

    # ---- P5.1C: durable policy identity under real processes -------------------------------------------------

    def identities(self):
        store = Store(self.path)
        try:
            _, orders, _ = store.load_paper(ACCOUNT)
            return sorted((o.run_id, o.risk_policy_version) for o in orders.values())
        finally:
            store.close()

    def seed_legacy(self, symbol="XAUUSD"):
        """A PENDING order written by another (non-Phase-5) writer: no durable risk-policy identity."""
        store = Store(self.path)
        try:
            held, orders, fills = store.load_paper(ACCOUNT)
            expected = store.paper_state(held, orders, fills)
            broker = PaperBroker(held)
            broker.orders, broker.fills = orders, fills
            args = {"XAUUSD": (2650.0, 2600.0, 2800.0, 2.0), "EURUSD": (1.085, 1.096, 1.052, 9090.0)}[symbol]
            side = "LONG" if symbol == "XAUUSD" else "SHORT"
            broker.orders["legacy"] = PaperOrder("1.0", "legacy", "run-legacy", symbol, side, args[3], *args[:3], 1.0,
                                                 10000.0, 0.0, AT)
            self.assertNotEqual(store.save_paper(broker, expected_state=expected), False)
        finally:
            store.close()

    def test_proven_orders_keep_identity_across_processes_and_restarts(self):
        self.race("23/1000")
        self.assertEqual(self.identities(), [("run-a", RISK_POLICY_V2), ("run-b", RISK_POLICY_V2)])
        a, _ = self.spawn("crash_after_commit", "XAU", "run-c")  # same symbol: rejected, nothing written
        out, err = a.communicate(timeout=30)
        self.assertEqual(json.loads(out)["reason"], "SYMBOL_EXPOSURE_LIMIT", err)
        self.assertEqual(self.identities(), [("run-a", RISK_POLICY_V2), ("run-b", RISK_POLICY_V2)])

    def test_crash_after_commit_identity_is_durable(self):
        a, _ = self.spawn("crash_after_commit", "XAU", "run-a")
        self.result(a, code=72)
        self.assertEqual(self.identities(), [("run-a", RISK_POLICY_V2)])
        retry, _ = self.spawn("normal", "XAU", "run-a")
        self.assertEqual(self.result(retry)["status"], "DUPLICATE_RUN")
        self.assertEqual(self.identities(), [("run-a", RISK_POLICY_V2)])

    def test_unknown_pending_blocks_concurrent_new_risk_and_stays_unknown(self):
        self.seed_legacy("XAUUSD")
        before = self.state()
        children = [self.spawn("normal", "EUR", "run-%d" % n) for n in range(2)]
        for child, _ in children:
            self.assertEqual(self.result(child), {"status": "REJECTED", "reason": "UNKNOWN_PENDING_RISK_POLICY",
                                                  "attempts": 1})
        for _ in range(2):  # restarts never reinterpret it
            retry, _ = self.spawn("normal", "EUR", "run-retry")
            self.assertEqual(self.result(retry)["reason"], "UNKNOWN_PENDING_RISK_POLICY")
        self.assertEqual(self.identities(), [("run-legacy", None)])
        self.assertEqual(self.state()[0], before[0])  # legacy order untouched

    def test_stale_writer_adding_unknown_pending_forces_fail_closed(self):
        a, signal = self.spawn("pause", "XAU", "run-a")
        self.ready(a, signal)  # A approved on an empty portfolio, not yet saved.
        self.seed_legacy("EURUSD")  # another writer commits an identity-less pending order
        self.assertEqual(self.result(a, resume=True),
                         {"status": "REJECTED", "reason": "UNKNOWN_PENDING_RISK_POLICY", "attempts": 2})
        self.assertEqual(self.identities(), [("run-legacy", None)])


if __name__ == "__main__":
    unittest.main()
