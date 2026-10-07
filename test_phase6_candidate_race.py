"""P6.1E permanent regression (REAL processes): one run_id can never carry two candidate truths.

Exact independent-review reproduction: process A (run "shared", EURUSD, NO_CONFLICT) is paused immediately before its
economic commit; process B (run "shared", a DIFFERENT, conflicting XAUUSD candidate) runs to completion. On 6bcf7cc
B committed XAUUSD BLOCKED and A then committed EURUSD RESERVED under the same run_id. Corrected: B fails closed
(RUN_ID_CANDIDATE_MISMATCH) and only A's candidate is authoritative. This file uses only APIs present at 6bcf7cc so it
can be executed against it (the claim-only crash case additionally needs the P6.1E claim and fails there).
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
    import json, os, sys
    from pathlib import Path
    from execution import conflict_path
    from storage.database import Store
    from test_phase5_risk_engine import AT, EUR_SHORT, INS, XAU_LONG
    from test_phase6_conflict_engine import XAU_SHORT, candidate
    path, signal_path, mode, which, run_id = sys.argv[1:]
    args, anchor = {"EUR": (EUR_SHORT, 0), "XAU_L": (XAU_LONG, 0), "XAU_L_A1": (XAU_LONG, 1), "XAU_S": (XAU_SHORT, 0),
                    "XAU_L_SL": (("XAUUSD", "LONG", 2650.0, 2590.0), 0)}[which]
    def barrier():
        Path(signal_path).write_text("ready", encoding="utf-8")
        assert sys.stdin.readline().strip() == "resume"
    if mode == "pause":  # immediately before the first guarded write: the economic commit or the decision rows
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
    elif mode == "crash_after_claim":
        original = conflict_path.claim_run
        def claim(*a, **k):
            original(*a, **k)
            os._exit(73)  # the claim is durable; no conflict, Risk, reservation or decision happened
        conflict_path.claim_run = claim
    store = Store(path)
    result = conflict_path.submit_with_conflict_control(store, candidate(args, run_id, anchor), INS[args[0]],
                                                        account_id="paper-main", as_of=AT)
    print(json.dumps({"status": result.status, "reason": result.reason,
                      "order_id": None if result.order is None else result.order.order_id}), flush=True)
    store.close()
''')
DECISIVE = ("RESERVED", "BLOCKED", "RISK_REJECTED")


class CandidateRaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p61e-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "trading_floor.db"
        store = Store(self.path)
        store.save_paper(PaperBroker(account()))
        store.close()
        self.serial = 0

    def spawn(self, mode, which, run_id="shared"):
        self.serial += 1
        signal = self.dir / f"{run_id}.{self.serial}.ready"
        child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(self.path), str(signal), mode, which, run_id],
                                 cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True)

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
                self.fail("child did not reach barrier")
            time.sleep(0.01)

    def result(self, child, resume=False, code=0):
        out, err = child.communicate("resume\n" if resume else None, timeout=30)
        self.assertEqual(child.returncode, code, err)
        return json.loads(out) if code == 0 else None

    def durable(self, run_id="shared"):
        store = Store(self.path)
        try:
            _, orders, _ = store.load_paper(ACCOUNT)
            rows = [(e, json.loads(p)) for e, p in store.db.execute(
                "SELECT event_type, payload FROM journal WHERE run_id=? ORDER BY id", (run_id,))]
            return [o for o in orders.values() if o.run_id == run_id], rows
        finally:
            store.close()

    def assert_one_candidate_truth(self, run_id="shared"):
        """INV-6.1/6.6/6.7 on durable state: one claim; one decisive outcome at most; order and decision match it."""
        orders, rows = self.durable(run_id)
        decisive = [p for e, p in rows if e == "CONFLICT_DECISION" and p.get("decision") in DECISIVE]
        meanings = {(p["symbol"], p["new_direction"], p["new_setup_id"]) for p in decisive}
        meanings |= {(o.symbol, o.side, o.setup_id) for o in orders}
        self.assertLessEqual(len(meanings), 1, meanings)  # never two candidate truths for one run_id
        self.assertLessEqual(len(decisive), 1)
        self.assertLessEqual(len(orders), 1)
        claims = [p for e, p in rows if e == "RUN_CANDIDATE_CLAIM"]
        self.assertEqual(len(claims), 1)
        for payload in decisive:
            self.assertEqual(payload["candidate_fingerprint"], claims[0]["fingerprint"])
        if orders:
            self.assertEqual((orders[0].symbol, orders[0].side), (claims[0]["candidate"]["symbol"],
                                                                  claims[0]["candidate"]["side"]))
        return orders, decisive

    def collide(self, first, second, seed_xau=False):
        if seed_xau:  # makes any XAUUSD candidate a deterministic conflict BLOCK
            store = Store(self.path)
            held, orders, fills = store.load_paper(ACCOUNT)
            held.open_positions["XAUUSD"] = replace(position("XAUUSD", "SHORT", 2650.0, 2700.0, 2500.0, 1.0),
                                                    setup_id="S-OLD")
            store.save_paper(PaperBroker(held))
            store.close()
        a, signal = self.spawn("pause", first)
        self.ready(a, signal)  # A holds its candidate's evaluation, right before its first guarded write
        b = self.result(self.spawn("normal", second)[0])
        return self.result(a, resume=True), b

    def test_copilot_exact_reproduction_eurusd_reserved_vs_xauusd_blocked(self):
        a, b = self.collide("EUR", "XAU_L", seed_xau=True)
        orders, rows = self.durable()
        decisive = [(p["symbol"], p["decision"]) for e, p in rows if e == "CONFLICT_DECISION"
                    and p.get("decision") in DECISIVE]
        # The contradiction found by the review must be impossible:
        self.assertFalse(("XAUUSD", "BLOCKED") in decisive and ("EURUSD", "RESERVED") in decisive, decisive)
        self.assertEqual((b["status"], b["reason"]), ("NOT_SUBMITTED", "RUN_ID_CANDIDATE_MISMATCH"))
        self.assertEqual(a["status"], "RESERVED")
        self.assertEqual(decisive, [("EURUSD", "RESERVED")])
        self.assertEqual([o.symbol for o in orders], ["EURUSD"])
        self.assert_one_candidate_truth()

    def test_concurrent_collision_matrix(self):
        for first, second in (("XAU_L", "EUR"), ("XAU_L", "XAU_L_A1"), ("XAU_L", "XAU_S"), ("XAU_L", "XAU_L_SL")):
            with self.subTest(first=first, second=second):
                self.setUp()
                a, b = self.collide(first, second)
                self.assertEqual((a["status"], b["status"], b["reason"]),
                                 ("RESERVED", "NOT_SUBMITTED", "RUN_ID_CANDIDATE_MISMATCH"))
                self.assert_one_candidate_truth()

    def test_concurrent_identical_candidate_converges(self):
        a, b = self.collide("XAU_L", "XAU_L")
        self.assertEqual(b["status"], "RESERVED")
        self.assertEqual((a["status"], a["order_id"]), ("DUPLICATE_RUN", b["order_id"]))  # A adopts the same order
        orders, decisive = self.assert_one_candidate_truth()
        self.assertEqual([o.order_id for o in orders], [b["order_id"]])

    def test_claim_only_crash_then_restart(self):
        self.result(self.spawn("crash_after_claim", "XAU_L")[0], code=73)
        orders, rows = self.durable()
        self.assertEqual((orders, [e for e, _ in rows]), ([], ["RUN_CANDIDATE_CLAIM"]))  # identity only
        other = self.result(self.spawn("normal", "EUR")[0])
        self.assertEqual(other["reason"], "RUN_ID_CANDIDATE_MISMATCH")
        resumed = self.result(self.spawn("normal", "XAU_L")[0])
        self.assertEqual(resumed["status"], "RESERVED")
        self.assert_one_candidate_truth()


if __name__ == "__main__":
    unittest.main()
