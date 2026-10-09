"""V2 Phase 8 / P4a R-HALT across real processes: real POSIX signals (SIGTERM delivered with os.kill), hard crashes
(os._exit) after an economic commit and before T_ack, restarts with a pending halt, resume tokens and two distinct
processes. Every scenario runs in an isolated child process; the test runner itself is never signalled."""
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest

from replay.rex_chain import verify
from storage.economic_digest import expected_edg_genesis

ROOT = Path(__file__).resolve().parent
GENESIS = expected_edg_genesis("paper-main", 10000.0)["edg"]
POSIX = os.name == "posix"

CHILD = textwrap.dedent('''
    import json, os, signal, sys
    from datetime import timedelta
    from unittest.mock import patch
    sys.path.insert(0, os.getcwd())
    from ai.provider import DeterministicAIProvider
    from runtime.config import RuntimeConfig
    from runtime.halt import HaltGate, HaltPending
    from runtime import halt as halt_module
    from runtime.service import OperationalRuntime
    from storage.database import Store
    from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts

    db, mode, minutes = sys.argv[1], sys.argv[2], int(sys.argv[3])
    config = RuntimeConfig(db_path=db, enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=True, v2_rhalt=True)
    gate = HaltGate()
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, gate.request)  # the production handler: one assignment
    patches = []

    def sigterm():
        os.kill(os.getpid(), signal.SIGTERM)  # a REAL signal; Python runs the handler at the next bytecode

    if mode in ("sigterm_before_submit", "direct_before_submit"):
        real = HaltGate.admit
        def admit(g, kind):
            if kind == "submit":
                sigterm() if mode == "sigterm_before_submit" else g.request(15)  # real signal / handler body
            return real(g, kind)
        patches.append(patch.object(HaltGate, "admit", admit))
    elif mode == "sigterm_between_read2_and_begin":
        real = HaltGate.pre_save
        def pre_save(g, admission):
            real(g, admission)
            if admission.kind == "pending_fill":
                sigterm()
        patches.append(patch.object(HaltGate, "pre_save", pre_save))
    elif mode == "sigterm_during_ai":
        real = DeterministicAIProvider.generate
        def generate(provider, request):
            sigterm()
            return real(provider, request)
        patches.append(patch.object(DeterministicAIProvider, "generate", generate))
    elif mode == "crash_after_commit":
        real = Store.save_paper
        def save(store, broker, **kwargs):
            result = real(store, broker, **kwargs)
            if broker.orders:
                os._exit(17)  # hard crash right after the economic commit, before its REX row
            return result
        patches.append(patch.object(Store, "save_paper", save))
    elif mode == "crash_before_t_ack":
        real_admit, real_event = HaltGate.admit, Store._event
        def admit(g, kind):
            if kind == "submit":
                g.request(15)
            return real_admit(g, kind)
        def event(store, at, run_id, symbol, source, event_type, *a, **k):
            if event_type == "HALT_OBSERVED":
                os._exit(19)  # killed inside the H transaction: nothing of it commits
            return real_event(store, at, run_id, symbol, source, event_type, *a, **k)
        patches += [patch.object(HaltGate, "admit", admit), patch.object(Store, "_event", event)]
    try:
        runtime = OperationalRuntime(config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                                     macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                                     clock=lambda: T + timedelta(minutes=minutes), halt_gate=gate)
    except HaltPending as exc:
        print(json.dumps({"start": "REFUSED", "reason": str(exc)}))
        sys.exit(3)
    try:
        for p in patches:
            p.start()
        with patched_scouts("LONG"):
            status = runtime.run_cycle("XAUUSD", T + timedelta(minutes=minutes))
    finally:
        for p in patches:
            p.stop()
        runtime.close()
    print(json.dumps({"start": "OK", "status": status, "halted": gate.halted, "residual": gate.residual()}))
''')


class Processes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p4a-proc-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "trading_floor.db"
        self.script = self.dir / "child.py"
        self.script.write_text(CHILD, encoding="utf-8")

    def child(self, mode, minutes, token=None):
        env = {k: v for k, v in os.environ.items() if not k.startswith("AI_FLOOR_")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        if token is not None:
            env["AI_FLOOR_V2_RHALT_RESUME"] = str(token)
        done = subprocess.run([sys.executable, str(self.script), str(self.db), mode, str(minutes)], cwd=ROOT, env=env,
                              capture_output=True, text=True, timeout=240)
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        return done.returncode, (json.loads(lines[-1]) if lines else None), done.stderr

    def sql(self, statement):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(statement).fetchall()
        finally:
            conn.close()

    def halt_id(self):
        rows = self.sql("SELECT id FROM journal WHERE event_type='HALT_OBSERVED' ORDER BY id DESC LIMIT 1")
        return rows[0][0] if rows else None

    def invalid_codes(self):
        return {f["code"] for f in verify(self.db, edg_start=GENESIS)["findings"] if f["severity"] == "INVALID"}


class AnnotateFailures:
    """POSIX-only tests run only in CI, whose logs are not readable here: failures are ALSO emitted as GitHub Actions
    annotations (workflow commands on stdout)."""

    def run(self, result=None):
        before = 0 if result is None else len(result.failures) + len(result.errors)
        outcome = super().run(result)
        if result is not None:
            for test, trace in (result.failures + result.errors)[before:]:
                text = trace[-1800:].replace("%", "%25").replace(chr(13), "%0D").replace(chr(10), "%0A")
                sys.__stdout__.write("::error title=" + test.id() + "::" + text + chr(10))
                sys.__stdout__.flush()
        return outcome


@unittest.skipUnless(POSIX, "real SIGTERM delivery to a Python handler needs POSIX (Windows os.kill terminates)")
class RealSignalTests(AnnotateFailures, Processes):
    def test_real_sigterm_before_the_submit_admission(self):
        code, out, err = self.child("sigterm_before_submit", 0)
        self.assertEqual((code, out["status"], out["halted"]), (0, "HALTED", True), err)
        self.assertEqual(self.sql("SELECT count(*) FROM paper_orders"), [(0,)])
        self.assertEqual(self.invalid_codes(), set())

    def test_real_sigterm_between_read2_and_begin_is_the_single_residual(self):
        self.assertEqual(self.child("plain", 0)[1]["status"], "PLAN_READY")
        token = None
        code, out, err = self.child("sigterm_between_read2_and_begin", 15, token)
        self.assertEqual((code, out["status"]), (0, "HALTED"), err)
        self.assertEqual(len(out["residual"]), 1)
        self.assertEqual(self.invalid_codes(), set())

    def test_real_sigterm_during_ai_persists_nothing_after_t_h(self):
        code, out, err = self.child("sigterm_during_ai", 0)
        self.assertEqual((code, out["status"]), (0, "HALTED"), err)
        self.assertEqual(self.sql("SELECT count(*) FROM review_reports"), [(0,)])
        self.assertEqual(self.invalid_codes(), set())


class CrashRestartTests(Processes):
    def test_crash_after_an_economic_commit_then_restart(self):
        code, _, _ = self.child("crash_after_commit", 0)
        self.assertEqual(code, 17)
        self.assertEqual(self.sql("SELECT status FROM runs"), [("RUNNING",)])  # left RUNNING with its lock
        self.assertEqual(self.sql("SELECT count(*) FROM paper_orders"), [(1,)])  # the commit is all-or-nothing
        code, out, err = self.child("plain", 15)  # no HALT_OBSERVED: the next authorized start may run
        self.assertEqual((code, out["start"]), (0, "OK"), err)
        self.assertIn(("FAILED",), self.sql("SELECT status FROM runs"))  # recover(): interrupted_run
        codes = self.invalid_codes()
        self.assertTrue(codes & {"ORPHAN_ECONOMIC_EVENT", "FINAL_EDG_MISMATCH", "EDG_CHAIN_BREAK"}, codes)

    def test_crash_before_t_ack_leaves_no_halt_and_needs_external_evidence(self):
        code, _, _ = self.child("crash_before_t_ack", 0)
        self.assertEqual(code, 19)
        self.assertIsNone(self.halt_id())  # the H transaction never committed
        self.assertEqual(self.sql("SELECT status FROM runs"), [("RUNNING",)])
        report = verify(self.db, edg_start=GENESIS)
        self.assertNotIn("VERIFIED", {report["classification"]} - {"NOT VERIFIED"})

    def halt_child(self):
        """A halted process: a real SIGTERM on POSIX; elsewhere the handler body itself (same single assignment)."""
        code, out, err = self.child("sigterm_before_submit" if POSIX else "direct_before_submit", 0)
        self.assertEqual(code, 0, err)
        return out["status"]

    def test_pending_halt_refuses_restart_until_the_exact_token(self):
        self.assertEqual(self.halt_child(), "HALTED")
        halt_id = self.halt_id()
        self.assertIsNotNone(halt_id)
        before = self.db.read_bytes()
        for token in (None, "wrong", str(halt_id - 1)):
            code, out, _ = self.child("plain", 15, token)
            self.assertEqual((code, out["start"]), (3, "REFUSED"))  # no automatic resume, no write
        self.assertEqual(self.db.read_bytes(), before)
        code, out, err = self.child("plain", 15, halt_id)
        self.assertEqual((code, out["start"]), (0, "OK"), err)

    def test_a_resumed_process_is_a_new_process_with_its_own_evidence(self):
        self.assertEqual(self.halt_child(), "HALTED")
        halt_id = self.halt_id()
        code, out, err = self.child("plain", 15, halt_id)  # a distinct process, explicitly resumed
        self.assertEqual((code, out["start"], out["status"]), (0, "OK", "PLAN_READY"), err)
        writers = {(json.loads(json.loads(r[0])["rex"]).get("admission") or {}).get("process_id")
                   for r in self.sql("SELECT payload FROM journal WHERE event_type='REX_WRITE'")}
        self.assertEqual(len(writers - {None}), 1)  # only the resumed process wrote; its own process_id
        report = verify(self.db, edg_start=GENESIS)
        self.assertEqual([h["halt_journal_id"] for h in report["halts"] if "halt_journal_id" in h], [halt_id])
        self.assertNotIn("WRITE_AFTER_HALT_RECORD", {f["code"] for f in report["findings"]})  # a new process id


if __name__ == "__main__":
    unittest.main()
