"""V2 Phase 8 / P4a MEDIUM-1 (Copilot) and the restricted category R.

MEDIUM-1: a halt (T_h) before or during process start must stop every constructor write (schema creation,
recover(), startup system_state, preflight probe, EXPERIMENT_STARTED, notification capture). Checked with SQL tracing
on EVERY connection the process opens. A signal arriving between a startup check and its write is covered by
deferring the halt signals across check + write (POSIX; verified with real signals in isolated child processes).

R: after T_h only REX evidence of the halted run of this process (one REX_WRITE per admitted write whose read 2
preceded T_h, one REX_RUN); everything else is refused at write time and flagged by the verifier."""
import ast
from datetime import timedelta
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

from ai.provider import DeterministicAIProvider
from replay.rex_chain import verify
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.halt import HaltGate, HaltRefused
from runtime.rex import RexRecorder
from storage.database import Store
from storage.economic_digest import H, expected_edg_genesis
from test_demo_runner import Data, T, instrument, macro_fixture

ROOT = Path(__file__).resolve().parent
POSIX = os.name == "posix"
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", re.I)
GENESIS = expected_edg_genesis("paper-main", 10000.0)["edg"]
REAL_CONNECT = sqlite3.connect


def rhalt_config(db):
    return RuntimeConfig(db_path=Path(db), enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=True, v2_rhalt=True)


class Traced(unittest.TestCase):
    """Every sqlite3 connection opened during the test is traced; each statement is tagged with T_h status."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p4a-start-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "trading_floor.db"
        self.gate = HaltGate()
        self.seen = []

        def connect(*args, **kwargs):
            conn = REAL_CONNECT(*args, **kwargs)
            conn.set_trace_callback(lambda sql: self.seen.append((self.gate.halted, sql)))
            return conn
        tracer = patch("sqlite3.connect", connect)
        tracer.start()
        self.addCleanup(tracer.stop)

    def existing(self):
        DemoRunner(RuntimeConfig(db_path=self.db, enabled_symbols=("XAUUSD", "EURUSD")), market_provider=Data(),
                   ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                   instruments={"XAUUSD": instrument()}, clock=lambda: T).close()
        self.seen.clear()

    def start(self, patches=()):
        managers = list(patches)
        for manager in managers:
            manager.__enter__()
        try:
            runner = DemoRunner(rhalt_config(self.db), market_provider=Data(), ai_provider=DeterministicAIProvider(),
                                macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                                clock=lambda: T, halt_gate=self.gate,
                                experiment_baseline_sha="a" * 40, experiment_freeze_sha="b" * 40)
        finally:
            for manager in reversed(managers):
                manager.__exit__(None, None, None)
        return runner

    def post_halt_writes(self):
        return [sql for halted, sql in self.seen if halted and WRITE.match(sql)]

    def state(self, key):
        conn = REAL_CONNECT(self.db)
        try:
            row = conn.execute("SELECT value FROM system_state WHERE key=?", (key,)).fetchone()
            return None if row is None else row[0]
        finally:
            conn.close()

    def after(self, owner, name):
        """Request T_h right after ``owner.name`` returns (a halt observed between two startup sites)."""
        real = getattr(owner, name)
        gate = self.gate

        def wrapper(*args, **kwargs):
            result = real(*args, **kwargs)
            gate.request(15)
            return result
        return patch.object(owner, name, wrapper)

    def assert_stopped(self, site):
        self.assertEqual(self.post_halt_writes(), [])
        self.assertIn({"kind": "startup", "stage": site}, self.gate.refused)


class StartupBarrierTests(Traced):
    def test_halt_before_the_constructor_on_a_new_database(self):
        self.gate.request(15)
        with self.assertRaises(HaltRefused):
            self.start()
        self.assert_stopped("store_open")
        self.assertFalse(self.db.exists())  # not even the schema

    def test_halt_before_the_constructor_on_an_existing_database(self):
        self.existing()
        before = self.db.read_bytes()
        self.gate.request(15)
        with self.assertRaises(HaltRefused):
            self.start()
        self.assert_stopped("store_open")
        self.assertEqual(self.db.read_bytes(), before)

    def test_halt_after_recover_stops_before_account_and_state(self):
        with self.assertRaises(HaltRefused):
            self.start([self.after(Store, "recover")])
        self.assert_stopped("account_creation")
        self.assertIsNone(self.state("account_id"))

    def test_halt_before_set_state(self):
        self.existing()
        with self.assertRaises(HaltRefused):
            self.start([self.after(Store, "recover")])  # existing account: the next site is the state block
        self.assert_stopped("startup_state")

    def test_halt_before_preflight(self):
        from runtime import service
        with self.assertRaises(HaltRefused):
            self.start([self.after(service.OperationalRuntime, "_startup")])
        self.assert_stopped("preflight")
        self.assertNotEqual(self.state("runner"), "RUNNING")

    def test_halt_before_experiment_started(self):
        from runtime import demo_runner
        with self.assertRaises(HaltRefused):
            self.start([self.after(demo_runner, "preflight")])
        self.assert_stopped("runner_state")
        self.assertIsNone(self.state("experiment_started"))  # never started after T_h

    def test_halt_before_notification_events(self):
        with self.assertRaises(HaltRefused):
            self.start([self.after(Store, "start_experiment_if_unstarted")])
        self.assert_stopped("notifications")
        conn = REAL_CONNECT(self.db)
        try:
            self.assertEqual(conn.execute("SELECT count(*) FROM notification_events").fetchone()[0], 0)
        finally:
            conn.close()

    def test_flag_off_constructor_is_unchanged(self):
        runner = DemoRunner(RuntimeConfig(db_path=self.db, enabled_symbols=("XAUUSD", "EURUSD")),
                            market_provider=Data(), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()}, clock=lambda: T)
        runner.close()
        self.assertIsNone(runner.halt_gate)
        self.assertEqual(self.state("runner"), "STOPPED")

    def test_every_startup_write_is_behind_a_barrier(self):
        """I-R14 for the constructors: each Store write in _startup / _start is inside ``with startup_write``."""
        for path, function in (("runtime/service.py", "_startup"), ("runtime/demo_runner.py", "_start")):
            tree = ast.parse((ROOT / path).read_text(encoding="utf-8"))
            parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
            body = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == function)
            for node in ast.walk(body):
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                    continue
                target = node.func
                writes = isinstance(target.value, ast.Attribute) and target.value.attr == "store" and target.attr in {
                    "recover", "event", "save_paper", "transaction", "set_state", "heartbeat",
                    "start_experiment_if_unstarted"}
                writes = writes or target.attr in {"_capture_and_deliver"} or getattr(node.func, "id", "") == "preflight"
                if not writes:
                    continue
                up, guarded = node, False
                while up in parents:
                    up = parents[up]
                    if isinstance(up, ast.With) and any(getattr(getattr(i.context_expr, "func", None), "id", "")
                                                        == "startup_write" for i in up.items):
                        guarded = True
                        break
                self.assertTrue(guarded, f"{path}:{function}: unguarded startup write at line {node.lineno}")


CHILD = textwrap.dedent('''
    import json, os, re, signal, sqlite3, sys
    sys.path.insert(0, os.getcwd())
    from unittest.mock import patch
    from ai.provider import DeterministicAIProvider
    from runtime.config import RuntimeConfig
    from runtime.demo_runner import DemoRunner
    from runtime.halt import HaltGate, HaltRefused
    from runtime import demo_runner as demo_module
    from storage.database import Store
    from test_demo_runner import Data, T, instrument, macro_fixture

    db, site, existing = sys.argv[1], sys.argv[2], sys.argv[3] == "1"
    WRITE = re.compile(r"^\\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\\b", re.I)
    if existing:
        DemoRunner(RuntimeConfig(db_path=db, enabled_symbols=("XAUUSD", "EURUSD")), market_provider=Data(),
                   ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                   instruments={"XAUUSD": instrument()}, clock=lambda: T).close()
    gate = HaltGate()
    signal.signal(signal.SIGTERM, gate.request)
    seen = []
    real_connect = sqlite3.connect
    def connect(*a, **k):
        c = real_connect(*a, **k)
        c.set_trace_callback(lambda sql: seen.append((gate.halted, sql)))
        return c
    targets = {"recover": (Store, "recover"), "set_state": (Store, "set_state"),
               "experiment": (Store, "start_experiment_if_unstarted"), "preflight": (demo_module, "preflight"),
               "notifications": (Store, "capture_notifications")}
    owner, name = targets[site]
    real = getattr(owner, name)
    sent = {"done": False}
    def inside(*a, **k):
        if not sent["done"]:
            sent["done"] = True
            os.kill(os.getpid(), signal.SIGTERM)  # a REAL signal between the barrier check and the write
        return real(*a, **k)
    outcome = "constructed"
    with patch("sqlite3.connect", connect), patch.object(owner, name, inside):
        try:
            DemoRunner(RuntimeConfig(db_path=db, enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=True, v2_rhalt=True),
                       market_provider=Data(), ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                       instruments={"XAUUSD": instrument()}, clock=lambda: T, halt_gate=gate,
                       experiment_baseline_sha="a" * 40, experiment_freeze_sha="b" * 40).close()
        except HaltRefused as exc:
            outcome = "refused:" + exc.stage
    print(json.dumps({"outcome": outcome, "halted": gate.halted, "signal_sent": sent["done"],
                      "post_halt_writes": [s for h, s in seen if h and WRITE.match(s)]}))
''')


@unittest.skipUnless(POSIX, "real SIGTERM delivery needs POSIX (CI)")
class RealSignalDuringStartupTests(unittest.TestCase):
    """A real SIGTERM sent INSIDE a startup write: the handler is deferred until that write completes, so no write
    follows T_h and the constructor stops at the next site."""

    def run_child(self, site, existing):
        with tempfile.TemporaryDirectory(prefix="v2-p4a-sig-") as tmp:
            script = Path(tmp) / "child.py"
            script.write_text(CHILD, encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if not k.startswith("AI_FLOOR_")}
            done = subprocess.run([sys.executable, str(script), str(Path(tmp) / "db.sqlite"), site,
                                   "1" if existing else "0"], cwd=ROOT, env=env, capture_output=True, text=True,
                                  timeout=240)
            self.assertEqual(done.returncode, 0, done.stderr)
            return json.loads(done.stdout.strip().splitlines()[-1])

    def test_signal_during_each_startup_write(self):
        for site, existing in (("recover", False), ("recover", True), ("set_state", True), ("preflight", True),
                               ("experiment", True), ("notifications", True)):
            with self.subTest(site=site, existing=existing):
                out = self.run_child(site, existing)
                self.assertTrue(out["signal_sent"] and out["halted"])
                self.assertTrue(out["outcome"].startswith("refused:"), out)
                self.assertEqual(out["post_halt_writes"], [])


class RestrictedEvidenceTests(unittest.TestCase):
    """Category R: allow_evidence and its enforcement at REX write time."""

    def halted(self):
        gate = HaltGate()
        gate.active_run = "run-1"
        admitted = gate.admit("pending_fill")
        gate.pre_save(admitted)
        late = gate.admit("submit")  # admitted, but its read 2 never happened before T_h
        gate.request(15)
        return gate, admitted, late

    def test_only_the_halted_runs_evidence_of_this_process(self):
        gate, admitted, late = self.halted()
        pid = gate.process_id
        self.assertFalse(gate.allow_evidence("REX_WRITE", process_id=pid, run_id="run-2", admission=admitted))
        self.assertFalse(gate.allow_evidence("REX_WRITE", process_id="other-process", run_id="run-1",
                                             admission=admitted))
        self.assertFalse(gate.allow_evidence("REX_WRITE", process_id=pid, run_id="run-1", admission=None))
        self.assertFalse(gate.allow_evidence("REX_WRITE", process_id=pid, run_id="run-1", admission=late))
        self.assertFalse(gate.allow_evidence("REX_FAILURE", process_id=pid, run_id="run-1"))
        self.assertTrue(gate.allow_evidence("REX_WRITE", process_id=pid, run_id="run-1", admission=admitted))
        self.assertFalse(gate.allow_evidence("REX_WRITE", process_id=pid, run_id="run-1", admission=admitted))  # dup
        self.assertTrue(gate.allow_evidence("REX_RUN", process_id=pid, run_id="run-1"))
        self.assertFalse(gate.allow_evidence("REX_RUN", process_id=pid, run_id="run-1"))  # duplicate
        gate.active_run = None  # the halted run returned: no new run may add evidence
        self.assertFalse(gate.allow_evidence("REX_RUN", process_id=pid, run_id="run-3"))

    def test_rex_rows_outside_r_are_not_written(self):
        with tempfile.TemporaryDirectory(prefix="v2-p4a-r-", ignore_cleanup_errors=True) as tmp:
            store = Store(Path(tmp) / "db.sqlite")
            try:
                gate, _, _ = self.halted()
                foreign = RexRecorder(store, account_id="paper-main", run_id="run-2", slot_key="k", symbol="XAUUSD",
                                      slot=T, identity={})
                foreign.halt_gate = gate
                foreign.finish("HALTED")  # another run: refused
                foreign.fail("x", RuntimeError("y"))  # REX_FAILURE after T_h: logged only
                mine = RexRecorder(store, account_id="paper-main", run_id="run-1", slot_key="k", symbol="XAUUSD",
                                   slot=T, identity={})
                mine.halt_gate = gate
                mine.finish("HALTED")
                mine.finished = False
                mine.finish("HALTED")  # a duplicate REX_RUN: refused
                rows = store.db.execute("SELECT run_id, event_type FROM journal WHERE source='rex'").fetchall()
            finally:
                store.close()
        self.assertEqual([tuple(r) for r in rows], [("run-1", "REX_RUN")])


class RuleIdentityCacheTests(unittest.TestCase):
    def test_a_first_call_without_extras_never_leaks_into_the_runtime_identity(self):
        """Found while testing R: the cache ignored its arguments, so a bare RexRecorder could drop the gate constants
        from every later runtime identity (G14 then failed with RULE_CONSTANTS_MISMATCH)."""
        from runtime.rex import rule_identity
        bare = rule_identity()
        full = rule_identity(extra_files=("execution/pending_order_gate.py",),
                             extra_constants={"gate_blocking_final_statuses": ["AI_CAUTION", "ERROR", "RISK_REJECTED"]})
        self.assertNotIn("gate_blocking_final_statuses", bare)
        self.assertIn("gate_blocking_final_statuses", full)
        self.assertIn("execution/pending_order_gate.py", full["files"])


class VerifierEvidenceTests(unittest.TestCase):
    """The verifier side of R: legitimate halted-run evidence passes; foreign or duplicate evidence is INVALID."""

    @classmethod
    def setUpClass(cls):
        from test_phase8_rhalt_runtime import Harness, on_pre_save
        cls.tmp = tempfile.TemporaryDirectory(prefix="v2-p4a-rv-", ignore_cleanup_errors=True)
        cls.master = Path(cls.tmp.name) / "master.db"
        harness = Harness("run")
        harness.setUp()
        harness.db = cls.master
        gate = HaltGate()
        harness.cycle(0, gate=gate)
        harness.cycle(15, gate=gate, patches=[on_pre_save("pending_fill")])
        harness.doCleanups()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(prefix="v2-p4a-rv-case-", ignore_cleanup_errors=True)
        self.addCleanup(self.td.cleanup)
        self.db = Path(self.td.name) / "db.sqlite"
        self.db.write_bytes(self.master.read_bytes())

    def sql(self, statement, params=()):
        conn = REAL_CONNECT(self.db)
        try:
            out = conn.execute(statement, params).fetchall()
            conn.commit()
            return out
        finally:
            conn.close()

    def codes(self):
        return {f["code"] for f in verify(self.db, edg_start=GENESIS)["findings"] if f["severity"] == "INVALID"}

    def halted_run_record(self):
        halt_id, halt_payload = self.sql("SELECT id, payload FROM journal WHERE event_type='HALT_OBSERVED'")[0]
        run = json.loads(halt_payload)["run_id"]
        rows = self.sql("SELECT id, timestamp, run_id, symbol, source, event_type, severity, payload FROM journal "
                        "WHERE event_type='REX_RUN' AND run_id=?", (run,))
        return halt_id, json.loads(halt_payload), rows[0]

    def test_legitimate_halted_run_evidence_passes(self):
        halt_id, payload, row = self.halted_run_record()
        self.assertGreater(row[0], halt_id)  # the halted run's REX_RUN, written after HALT_OBSERVED
        self.assertEqual(json.loads(json.loads(row[7])["rex"])["process_id"], payload["process_id"])
        self.assertEqual(self.codes(), set())

    def test_duplicate_rex_run_after_the_halt(self):
        _, _, row = self.halted_run_record()
        self.sql("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) VALUES(?,?,?,?,?,?,?)",
                 row[1:])
        self.assertTrue(self.codes() & {"POST_HALT_EVIDENCE_NOT_AUTHORIZED", "REX_RUN_DUPLICATED"})

    def test_foreign_process_rex_run_after_the_halt(self):
        _, _, row = self.halted_run_record()
        record = json.loads(json.loads(row[7])["rex"])
        record["process_id"] = "another-process"
        text = json.dumps(record, sort_keys=True, separators=(",", ":"))
        self.sql("UPDATE journal SET payload=? WHERE id=?",
                 (json.dumps({"rex": text, "rex_digest": H("V2REX/1", text)}), row[0]))
        self.assertIn("POST_HALT_EVIDENCE_NOT_AUTHORIZED", self.codes())

    def test_rex_of_another_run_or_a_failure_after_the_halt(self):
        self.sql("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                 "VALUES('2026-01-15T14:00:00+00:00','other-run','XAUUSD','rex','REX_FAILURE','WARNING','{}')")
        self.assertIn("POST_HALT_WRITE", self.codes())

    def test_new_run_after_the_halt(self):
        self.sql("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                 "VALUES('2026-01-15T14:00:00+00:00','new-run','XAUUSD','runtime','RUN_STARTED','INFO','{}')")
        self.assertIn("POST_HALT_WRITE", self.codes())


if __name__ == "__main__":
    unittest.main()
