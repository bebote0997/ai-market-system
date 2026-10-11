"""V2 Phase 8 / P8.1A: execution crash/restart and lock races with REAL processes (separate interpreters and SQLite
connections; file-signal + stdin barriers). Flags OFF (current runtime path). Tests only."""
import json
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent

CHILD = textwrap.dedent('''
    import contextlib, json, os, sys
    from datetime import timedelta
    from pathlib import Path
    from ai.provider import DeterministicAIProvider
    from execution.paper_broker import PaperBroker
    from runtime.config import RuntimeConfig
    from runtime.demo_runner import DemoRunner
    from storage.database import Store
    from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
    db, signal_path, mode, minutes, recovery = sys.argv[1:]
    at = T + timedelta(minutes=int(minutes))
    config = RuntimeConfig(db_path=Path(db), enabled_symbols=("XAUUSD", "EURUSD"), market_provider_mode="twelve_data",
                           ai_provider_mode="openai")
    def barrier():
        Path(signal_path).write_text("ready", encoding="utf-8")
        assert sys.stdin.readline().strip() == "resume"
    if mode == "crash_after_submit":  # the order is committed; the process dies before recording/finishing the run
        Store.record_execution = lambda self, *a, **k: os._exit(75)
    elif mode == "crash_before_submit_commit":  # order written inside the transaction, process dies before COMMIT
        submitted = {"yes": False}
        original_submit = PaperBroker.submit_plan
        def submit(self, *a, **k):
            order = original_submit(self, *a, **k)
            submitted["yes"] = order is not None
            return order
        PaperBroker.submit_plan = submit
        original_tx = Store.transaction
        @contextlib.contextmanager
        def tx(self):
            with original_tx(self):
                yield
                if submitted["yes"]:
                    os._exit(76)
        Store.transaction = tx
    elif mode == "pause_after_claim":
        original_claim = Store.claim_slot
        def claim(self, *a, **k):
            won = original_claim(self, *a, **k)
            if won:
                barrier()
            return won
        Store.claim_slot = claim
    runner = DemoRunner(config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                        macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()}, clock=lambda: at,
                        recovery_stale_after_seconds=int(recovery))
    with patched_scouts("LONG"):
        status = runner.runtime.run_cycle("XAUUSD", at)
    print(json.dumps({"status": status}), flush=True)
    runner.close()
''')


class ProcessCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p81a-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "trading_floor.db"
        self.n = 0

    def spawn(self, mode="normal", minutes=0, recovery=120):
        self.n += 1
        signal = self.dir / f"child{self.n}.ready"
        child = subprocess.Popen([sys.executable, "-B", "-c", CHILD, str(self.db), str(signal), mode, str(minutes),
                                  str(recovery)], cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)

        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        self.addCleanup(cleanup)
        return child, signal

    def ready(self, child, signal):
        deadline = time.monotonic() + 30
        while not signal.exists():
            if child.poll() is not None:
                self.fail(f"child exited before barrier: {child.communicate(timeout=5)}")
            if time.monotonic() >= deadline:
                self.fail("child did not reach barrier")
            time.sleep(0.01)

    def result(self, child, code=0, resume=False):
        out, err = child.communicate("resume\n" if resume else None, timeout=60)
        self.assertEqual(child.returncode, code, err[-2000:])
        return json.loads(out.strip().splitlines()[-1])["status"] if code == 0 else None

    def child_cycle(self, mode="normal", minutes=0, recovery=120, code=0):
        return self.result(self.spawn(mode, minutes, recovery)[0], code=code)

    def state(self):
        db = sqlite3.connect(self.db)
        try:
            orders = [json.loads(r[0]) for r in db.execute("SELECT payload FROM paper_orders")]
            fills = db.execute("SELECT COUNT(*) FROM paper_fills").fetchone()[0]
            positions = [json.loads(r[0]) for r in db.execute("SELECT payload FROM paper_positions")]
            runs = sorted(db.execute("SELECT slot_key,status,final_status,error FROM runs").fetchall())
            locks = [r[0] for r in db.execute("SELECT symbol FROM symbol_locks")]
            submitted = db.execute("SELECT COUNT(*) FROM journal WHERE event_type='ORDER_SUBMITTED'").fetchone()[0]
            return {"orders": [(o["run_id"], o["status"]) for o in orders], "fills": fills,
                    "open": [p["symbol"] for p in positions if p["status"] == "OPEN"], "runs": runs, "locks": locks,
                    "submitted_events": submitted}
        finally:
            db.close()


class RestartDuplicationTests(ProcessCase):
    def test_crash_after_submit_commit_restart_never_duplicates(self):
        self.child_cycle("crash_after_submit", 0, code=75)
        crashed = self.state()
        self.assertEqual((len(crashed["orders"]), crashed["orders"][0][1], crashed["submitted_events"], crashed["locks"]),
                         (1, "PENDING", 1, ["XAUUSD"]))
        self.assertEqual(self.child_cycle("normal", 15, recovery=0), "PLAN_READY")  # restart: recover + next slot
        after = self.state()
        self.assertEqual((len(after["orders"]), after["orders"][0][1], after["fills"], after["open"]),
                         (1, "FILLED", 1, ["XAUUSD"]))
        self.assertEqual([r[1:] for r in after["runs"]][0], ("FAILED", "ERROR", "interrupted_run"))
        for _ in range(2):  # further restarts of the same slot change nothing
            self.assertEqual(self.child_cycle("normal", 15, recovery=0), "DUPLICATE")
        self.assertEqual(self.state()["orders"], after["orders"])

    def test_crash_before_submit_commit_leaves_no_order_and_retries_cleanly(self):
        self.child_cycle("crash_before_submit_commit", 0, code=76)
        crashed = self.state()
        self.assertEqual((crashed["orders"], crashed["submitted_events"], crashed["locks"]), ([], 0, ["XAUUSD"]))
        self.assertEqual(self.child_cycle("normal", 15, recovery=0), "PLAN_READY")
        after = self.state()
        self.assertEqual((len(after["orders"]), after["submitted_events"]), (1, 1))  # exactly one, from the new slot


class LockRaceTests(ProcessCase):
    def test_two_processes_same_slot_one_cycle(self):
        a, signal = self.spawn("pause_after_claim", 0)
        self.ready(a, signal)
        self.assertEqual(self.child_cycle("normal", 0), "DUPLICATE")
        self.assertEqual(self.result(a, resume=True), "PLAN_READY")
        state = self.state()
        self.assertEqual((len(state["runs"]), len(state["orders"]), state["locks"]), (1, 1, []))

    def test_second_process_recovering_a_live_run_cannot_duplicate(self):
        """Local runtime (no lifetime lock): process B starts 15 min later, its recover() treats A's still-running
        cycle as stale and takes the symbol. A must then lose slot ownership; at most one order exists."""
        a, signal = self.spawn("pause_after_claim", 0)
        self.ready(a, signal)
        self.assertEqual(self.child_cycle("normal", 15, recovery=120), "PLAN_READY")  # B recovered A's lock and traded
        self.assertEqual(self.result(a, resume=True), "ERROR")  # A: "slot ownership lost" -> no economic write
        state = self.state()
        self.assertEqual(len(state["orders"]), 1)
        self.assertEqual(state["submitted_events"], 1)
        self.assertEqual([r[1] for r in state["runs"]], ["FAILED", "COMPLETED"])


if __name__ == "__main__":
    unittest.main()
