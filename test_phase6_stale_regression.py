"""P6.1D / P6-STALE-01 permanent regression (REAL processes, deterministic barriers).

Exact independent-review race: candidate run R loses its first guarded save to an unrelated PAPER change, recomputes,
and immediately before its second guarded write a competing process commits a decisive outcome for the SAME run R.
The 7648eb9 candidate then appended an unguarded STALE_STATE / NOT_SUBMITTED row carrying a provisional order_id
that never existed, contradicting the durable RESERVED / SUBMITTED outcome. Invariants checked after every case:
- at most one order and at most one decisive CONFLICT_DECISION per run_id; no CONFLICT_DECISION with STALE_STATE;
- every journal row that names an order_id references a committed order of that run;
- a stale observation is non-decisive and carries no economic identifier; at most one per run.
Cases: A RESERVED, B BLOCKED, C RISK_REJECTED (competing same run) · D double stale without a decisive outcome (twice)
· E/F retry and restart after a legitimate STALE · G retry after the competing RESERVED · H a different run changes
state during stale handling. Case I (same setup_id, different run wins) is test_phase6_conflict_concurrency A.
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
from storage.database import Store
from test_phase5_risk_engine import ACCOUNT, ROOT, account, position

CHILD = textwrap.dedent('''
    import json, sys
    from pathlib import Path
    from execution import conflict_path
    from storage.database import Store
    from test_phase5_risk_engine import AT, EUR_SHORT, INS, XAU_LONG
    from test_phase6_conflict_engine import candidate
    path, signal_path, mode, which, run_id = sys.argv[1:]
    args = {"XAU_L": XAU_LONG, "EUR": EUR_SHORT}[which]
    if mode == "step":  # a numbered barrier before EVERY guarded write of the path
        count = {"n": 0}
        def barrier():
            count["n"] += 1
            Path(f"{signal_path}.{count['n']}").write_text("ready", encoding="utf-8")
            assert sys.stdin.readline().strip() == "resume"
        original_save, original_rows = Store.save_paper, conflict_path.commit_decision_rows
        def save(self, broker, **kwargs):
            if kwargs.get("expected_state") is not None:
                barrier()
            return original_save(self, broker, **kwargs)
        def rows(*a, **k):
            barrier()
            return original_rows(*a, **k)
        Store.save_paper, conflict_path.commit_decision_rows = save, rows
    store = Store(path)
    result = conflict_path.submit_with_conflict_control(store, candidate(args, run_id), INS[args[0]],
                                                        account_id="paper-main", as_of=AT)
    print(json.dumps({"status": result.status, "reason": result.reason, "attempts": result.attempts,
                      "order_id": None if result.order is None else result.order.order_id}), flush=True)
    store.close()
''')
EVENTS = ("CONFLICT_DECISION", "CONFLICT_ATTEMPT_STALE", "RISK_V2_DECISION")


class StaleRegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p61d-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "trading_floor.db"
        store = Store(self.path)
        store.save_paper(PaperBroker(account()))
        store.close()
        self.serial = 0

    def spawn(self, mode, which, run_id):
        self.serial += 1
        signal = self.dir / f"{run_id}.{self.serial}"
        child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(self.path), str(signal), mode, which, run_id],
                                 cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)

        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        self.addCleanup(cleanup)
        return child, signal

    def at_barrier(self, child, signal, n):
        flag, deadline = Path(f"{signal}.{n}"), time.monotonic() + 20
        while not flag.exists():
            if child.poll() is not None:
                self.fail(f"child exited before barrier {n}: {child.communicate(timeout=5)}")
            if time.monotonic() >= deadline:
                self.fail(f"child did not reach barrier {n}")
            time.sleep(0.01)

    def resume(self, child):
        child.stdin.write("resume\n")
        child.stdin.flush()

    def finish(self, child):
        out, err = child.communicate(timeout=30)
        self.assertEqual(child.returncode, 0, err)
        return json.loads(out)

    def run_normal(self, which, run_id):
        child, _ = self.spawn("normal", which, run_id)
        return self.finish(child)

    def write(self, action):
        """Another writer commits a real PAPER change through the B2.3A guarded save."""
        store = Store(self.path)
        try:
            held, orders, fills = store.load_paper(ACCOUNT)
            expected = store.paper_state(held, orders, fills)
            broker = PaperBroker(held)
            broker.orders, broker.fills = orders, fills
            action(held, broker)
            self.assertNotEqual(store.save_paper(broker, expected_state=expected), False)
        finally:
            store.close()

    def unrelated(self, _tag=None):
        """An unrelated PAPER change by another writer (a +1.0 realized PnL tick): no symbol exposure, no run R."""
        def tick(held, broker):
            held.realized_pnl += 1.0
            held.equity += 1.0
        self.write(tick)

    def double_stale(self, run_id, competitor):
        """Candidate R: first guarded write loses to an unrelated change; ``competitor`` acts before the second."""
        child, signal = self.spawn("step", "XAU_L", run_id)
        self.at_barrier(child, signal, 1)
        self.unrelated(f"u1-{self.serial}")
        self.resume(child)
        self.at_barrier(child, signal, 2)
        competitor()
        self.resume(child)
        return self.finish(child)

    def truth(self, run_id):
        """Assert every P6-STALE-01 journal invariant for ``run_id``; return (orders, decisions, observations)."""
        store = Store(self.path)
        try:
            _, orders, _ = store.load_paper(ACCOUNT)
            rows = [(r[0], json.loads(r[1])) for r in store.db.execute(
                "SELECT event_type, payload FROM journal WHERE run_id=? AND event_type IN (?,?,?) ORDER BY id",
                (run_id, *EVENTS))]
        finally:
            store.close()
        mine = {o.order_id for o in orders.values() if o.run_id == run_id}
        self.assertLessEqual(len(mine), 1)
        decisions = [p for e, p in rows if e == "CONFLICT_DECISION"]
        self.assertLessEqual(sum(p["decision"] in ("RESERVED", "BLOCKED", "RISK_REJECTED") for p in decisions), 1)
        self.assertNotIn("STALE_STATE", [p.get("decision") for p in decisions])
        for event, payload in rows:
            if payload.get("order_id") is not None:
                self.assertIn(payload["order_id"], mine, (event, payload))  # no provisional / nonexistent order
        observations = [p for e, p in rows if e == "CONFLICT_ATTEMPT_STALE"]
        self.assertLessEqual(len(observations), 1)
        for payload in observations:
            self.assertEqual((payload["decisive"], payload["economic_action"]), (False, "NONE"))
            self.assertTrue({"order_id", "decision", "execution_status", "risk_status", "proposed_reservation"}
                            .isdisjoint(payload))
        if any(p["decision"] == "RESERVED" for p in decisions):
            self.assertEqual(len(mine), 1)
        if any(p["decision"] in ("BLOCKED", "RISK_REJECTED") for p in decisions):
            self.assertEqual(mine, set())
        return mine, [p["decision"] for p in decisions], observations

    def test_a_p6_stale_01_competing_same_run_reserved(self):
        won = {}
        result = self.double_stale("run-r", lambda: won.update(self.run_normal("XAU_L", "run-r")))
        self.assertEqual((won["status"], won["attempts"]), ("RESERVED", 1))
        self.assertEqual(result["status"], "DUPLICATE_RUN")  # the durable RESERVED is authoritative
        self.assertEqual(result["order_id"], won["order_id"])
        mine, decisions, observations = self.truth("run-r")
        self.assertEqual((mine, decisions, observations), ({won["order_id"]}, ["RESERVED"], []))
        # G: retries of the same run stay idempotent; still one order, one reservation.
        for _ in range(2):
            self.assertEqual(self.run_normal("XAU_L", "run-r")["status"], "DUPLICATE_RUN")
        self.assertEqual(self.truth("run-r")[:2], ({won["order_id"]}, ["RESERVED"]))

    def test_b_competing_same_run_blocked(self):
        def block():
            self.write(lambda held, broker: held.open_positions.__setitem__("XAUUSD", replace(
                position("XAUUSD", "LONG", 2650.0, 2600.0, 2800.0, 1.0), setup_id="S-OLD")))
            self.assertEqual(self.run_normal("XAU_L", "run-r")["status"], "BLOCKED")
        result = self.double_stale("run-r", block)
        self.assertEqual((result["status"], result["order_id"]), ("DUPLICATE_RUN", None))
        self.assertEqual(self.truth("run-r"), (set(), ["BLOCKED"], []))

    def test_c_competing_same_run_risk_rejected(self):
        def reject():
            self.write(lambda held, broker: (setattr(held, "equity", 9400.0), setattr(held, "realized_pnl", -600.0)))
            self.assertEqual(self.run_normal("XAU_L", "run-r")["reason"], "ACCOUNT_DRAWDOWN_LIMIT")
        result = self.double_stale("run-r", reject)
        self.assertEqual((result["status"], result["order_id"]), ("DUPLICATE_RUN", None))
        self.assertEqual(self.truth("run-r"), (set(), ["RISK_REJECTED"], []))

    def test_d_e_f_legitimate_stale_then_retry_and_restart(self):
        for n in range(2):  # D: two double-stale attempts of the same run, no decisive outcome anywhere
            result = self.double_stale("run-r", lambda: self.unrelated(f"u2-{self.serial}-{n}"))
            self.assertEqual((result["status"], result["reason"], result["order_id"]), ("STALE", "STALE_STATE", None))
        mine, decisions, observations = self.truth("run-r")
        self.assertEqual((mine, decisions, len(observations)), (set(), [], 1))  # one sanitized observation only
        retry = self.run_normal("XAU_L", "run-r")  # E/F: a new process (restart) retries and decides normally
        self.assertEqual(retry["status"], "RESERVED")
        self.assertEqual(self.truth("run-r")[:2], ({retry["order_id"]}, ["RESERVED"]))
        self.assertEqual(self.run_normal("XAU_L", "run-r")["status"], "DUPLICATE_RUN")

    def test_h_different_run_changes_state_during_stale_handling(self):
        other = {}
        result = self.double_stale("run-r", lambda: other.update(self.run_normal("EUR", "run-other")))
        self.assertEqual(other["status"], "RESERVED")
        self.assertEqual((result["status"], result["order_id"]), ("STALE", None))
        self.assertEqual(self.truth("run-r")[:2], (set(), []))
        self.assertEqual(self.truth("run-other")[:2], ({other["order_id"]}, ["RESERVED"]))
        self.assertEqual(self.run_normal("XAU_L", "run-r")["status"], "RESERVED")  # retry is not suppressed
        self.truth("run-r")


if __name__ == "__main__":
    unittest.main()
