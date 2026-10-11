"""V2 Phase 8 / M-5 R-HALT race certification (design P8.6 3.8 "Race tests"; checklist I5).

One injector drives every halt point, in process (the handler body) and across real processes (a REAL
``os.kill(os.getpid(), SIGTERM)`` / ``SIGINT`` handled by the production handler ``HaltGate.request``):
(a) just before L(W), (b) after L(W) before the load, (c) between the load and BEGIN (the computation, and the
read2 -> BEGIN residual window), (d) inside the save transaction (at its first economic statement), (e) after the
commit before the next admission, (f) between catch-up bars, (g) between symbols. A randomized property test draws
T_h uniformly over every injection event of a 4-cycle period (gate reads, computation, AI, claim, every statement of
every economic transaction, every commit) and checks, for each draw: I-R1b (``L(W) < T_h`` and ``read2 < T_h``),
I-R9 (at most one residual write), every ``save_paper`` call is admitted, the post-T_h allowlist (H / P / R plus the
single in-flight write), the economic prefix property (the halted state is the reference state after some committed
write) and a clean verifier. Also: a coarse monotonic clock (the defect fixed with ``HaltGate.now_ns``) and the
verifier's strict ``L(W) < T_h`` boundary. Temporary databases only; the test runner itself is never signalled."""
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import random
import re
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from ai.provider import DeterministicAIProvider
from execution.paper_broker import PaperBroker
from replay import halt_verifier
from replay.rex_chain import verify
from runtime.halt import HaltGate
from runtime.scheduler import Scheduler
from runtime.service import OperationalRuntime
from storage.database import Store
from storage.economic_digest import expected_edg_genesis
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from test_phase8_rex_inertness import snapshot
from test_phase8_rex_writer import Ids
from test_phase8_rhalt_runtime import WRITE, allowed_post_halt, rhalt_config

ROOT = Path(__file__).resolve().parent
GENESIS = expected_edg_genesis("paper-main", 10000.0)["edg"]
POSIX = os.name == "posix"
PERIOD = ((0, 100.), (15, 100.), (30, 80.), (45, 100.))  # submit, fill, management (stop), idle
PAPER_TABLES = re.compile(r"\b(paper_\w+|closed_trades)\b")


class Injector:
    """Counts injection events and fires the halt once: at the first event matching ``point`` (name, kind, nth), or
    at the ``target``-th event overall. Also records every SQL statement of the runtime connection with whether T_h
    had happened when the statement was issued, and whether it ran inside a ``save_paper`` call."""

    def __init__(self, gate, fire, *, point=None, target=None):
        self.gate, self.fire, self.point, self.target = gate, fire, point, target
        self.count, self.fired_at, self.matches = 0, None, 0
        self.in_save, self.in_tx = False, False
        self.statements, self.saves, self.saves_after_t_h, self.log = [], 0, 0, []

    def event(self, name, kind=None):
        self.count += 1
        self.log.append(name)
        if self.fired_at is not None:
            return
        hit = self.target is not None and self.count == self.target
        if self.point is not None and (name, kind) == self.point[:2]:
            self.matches += 1
            hit = self.matches == self.point[2]
        if hit:
            self.fired_at = (self.count, name, kind)
            self.fire()

    def kind(self):
        return None if self.gate.current is None else self.gate.current.kind

    def trace(self, sql):
        self.statements.append((self.gate.halted, self.in_save, sql))  # issued before any halt fired here
        if not self.in_save:
            return
        if sql.startswith("BEGIN"):
            self.in_tx = True
        elif sql.startswith(("COMMIT", "ROLLBACK")):
            self.in_tx = False
            self.event("commit_stmt", self.kind())
        elif self.in_tx and WRITE.match(sql) and PAPER_TABLES.search(sql):
            self.event("in_tx", self.kind())

    def attach(self, store):
        store.db.set_trace_callback(self.trace)

    @contextmanager
    def patches(self):
        injector = self
        real_admit, real_pre, real_saved = HaltGate.admit, HaltGate.pre_save, HaltGate.saved
        real_save, real_claim = Store.save_paper, Store.claim_slot
        real_process, real_generate = PaperBroker.process_next_bar, DeterministicAIProvider.generate

        def admit(gate, kind):
            injector.event("before_admit", kind)
            admission = real_admit(gate, kind)
            injector.event("after_admit", kind)
            return admission

        def pre_save(gate, admission):
            injector.event("before_read2", admission.kind)
            real_pre(gate, admission)
            injector.event("after_read2", admission.kind)

        def saved(gate, admission, t_stop_ns):
            real_saved(gate, admission, t_stop_ns)
            injector.event("after_commit", admission.kind)

        def save_paper(store, broker, **kwargs):
            injector.saves += 1
            injector.saves_after_t_h += injector.gate.halted
            injector.in_save = True
            try:
                return real_save(store, broker, **kwargs)
            finally:
                injector.in_save = injector.in_tx = False

        def claim(store, *args, **kwargs):
            result = real_claim(store, *args, **kwargs)
            injector.event("after_claim")
            return result

        def process(broker, order, bar):
            injector.event("compute", "pending_fill")
            return real_process(broker, order, bar)

        def generate(provider, request):
            injector.event("ai")
            return real_generate(provider, request)
        with ExitStack() as stack:
            for owner, name, function in ((HaltGate, "admit", admit), (HaltGate, "pre_save", pre_save),
                                          (HaltGate, "saved", saved), (Store, "save_paper", save_paper),
                                          (Store, "claim_slot", claim), (PaperBroker, "process_next_bar", process),
                                          (DeterministicAIProvider, "generate", generate)):
                stack.enter_context(patch.object(owner, name, function))
            yield

    def evidence(self):
        gate = self.gate
        return {"halted": gate.halted, "t_h": None if not gate.halted else gate.requested_at[0],
                "signal": None if not gate.halted else gate.requested_at[2],
                "admissions": [a.evidence() for a in gate.admissions], "residual": gate.residual(),
                "refused": gate.refused, "recorded_journal_id": gate.recorded_journal_id,
                "fired_at": self.fired_at, "events": self.count, "saves": self.saves,
                "saves_after_t_h": self.saves_after_t_h, "statements": self.statements}


def run_period(db, gate, injector, steps=PERIOD, on_save=None):
    """The 4-cycle XAUUSD period, one runtime per cycle (one process gate), until the halt."""
    statuses = []
    for minutes, close in steps:
        if gate.halted:
            break
        runtime = OperationalRuntime(rhalt_config(db), market_provider=Data(close=close, age=5),
                                     ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                                     instruments={"XAUUSD": instrument()},
                                     clock=lambda m=minutes: T + timedelta(minutes=m), halt_gate=gate)
        if on_save is not None:
            on_save()  # the state after the start (account creation): a valid prefix
        injector.attach(runtime.store)
        try:
            with patched_scouts("LONG"), injector.patches():
                if on_save is None:
                    statuses.append(runtime.run_cycle("XAUUSD", T + timedelta(minutes=minutes)))
                else:
                    real = Store.save_paper

                    def save(store, broker, **kwargs):
                        result = real(store, broker, **kwargs)
                        on_save()
                        return result
                    with patch.object(Store, "save_paper", save):
                        statuses.append(runtime.run_cycle("XAUUSD", T + timedelta(minutes=minutes)))
        finally:
            runtime.close()
    return statuses


def run_catch_up(dir_, gate, injector):
    """(f): an in-scope open position with three bars to catch up (stop on the third)."""
    from test_phase8_observe_only import XAU_STOP_T3, make
    from test_runtime_catch_up import SLOT, seed
    db = Path(dir_) / "trading_floor.db"
    seed(db, positions=("XAUUSD",))
    runtime = make(db, Path(dir_) / "ev.db", SLOT, ("XAUUSD",), XAU_STOP_T3)
    runtime.config = replace(runtime.config, v2_rex=True, v2_rhalt=True)
    runtime.halt_gate = gate
    injector.attach(runtime.store)
    try:
        with injector.patches():
            return [runtime.run_cycle("XAUUSD", SLOT)]
    finally:
        runtime.close()


def run_symbols(dir_, gate, injector):
    """(g): one scheduler tick over XAUUSD then EURUSD; the halt fires when the first symbol's cycle returned."""
    from test_phase8_observe_only import make
    from test_runtime_catch_up import SLOT
    runtime = make(Path(dir_) / "trading_floor.db", None, SLOT, ())
    runtime.config = replace(runtime.config, v2_rex=True, v2_rhalt=True, scheduler_enabled=True)
    runtime.halt_gate = gate
    injector.attach(runtime.store)
    real = runtime.run_cycle

    def run_cycle(symbol, slot):
        result = real(symbol, slot)
        injector.event("between_symbols", symbol)
        return result
    runtime.run_cycle = run_cycle
    try:
        with injector.patches():
            return Scheduler(runtime, lambda: SLOT).tick()
    finally:
        runtime.close()


def scenario(name, dir_, gate, injector):
    with patch("uuid.uuid4", Ids()):
        if name == "catch_up":
            return run_catch_up(dir_, gate, injector)
        if name == "symbols":
            return run_symbols(dir_, gate, injector)
        return run_period(Path(dir_) / "trading_floor.db", gate, injector)


def reference_period(dir_):
    """The unhalted period: every injection event (in order) and the economic state after the start and after every
    committed write (the valid prefixes of a halted period)."""
    gate, prefixes = HaltGate(), []
    db = Path(dir_) / "trading_floor.db"
    db.parent.mkdir(parents=True)
    injector = Injector(gate, lambda: None)
    with patch("uuid.uuid4", Ids()):
        statuses = run_period(db, gate, injector, on_save=lambda: prefixes.append(snapshot(db)[0]))
    return statuses, injector.log, prefixes


def draws(log, rng, extra):
    """One random event of every kind of injection point, plus ``extra`` uniform draws (1-based event indexes)."""
    names = sorted(set(log))
    chosen = {rng.choice([i for i, name in enumerate(log, 1) if name == wanted]) for wanted in names}
    chosen |= set(rng.sample(range(1, len(log) + 1), min(extra, len(log))))
    return sorted(chosen)


def child(argv):
    """Child-process entry: ``<dir> <scenario> <point name|-> <kind|-> <nth|target> <SIGTERM|SIGINT>``. Installs the
    production handler, fires a REAL signal at the injection point and prints the evidence as one JSON line."""
    dir_, name, point, kind, number, signame = argv
    gate = HaltGate()  # created before the uuid patch, as in production (its own process id)
    signum = getattr(signal, signame)
    signal.signal(signal.SIGTERM, gate.request)  # the production handler: one assignment, no KeyboardInterrupt
    signal.signal(signal.SIGINT, gate.request)

    def fire():
        os.kill(os.getpid(), signum)  # Python runs the handler at the next bytecode boundary of the main thread
    if point == "-":
        injector = Injector(gate, fire, target=int(number))
    else:
        injector = Injector(gate, fire, point=(point, None if kind == "-" else kind, int(number)))
    statuses = scenario(name, dir_, gate, injector)
    print(json.dumps({"statuses": statuses, **injector.evidence()}))


class Checks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-m5-race-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def sql(self, db, statement, params=()):
        conn = sqlite3.connect(db)
        try:
            return conn.execute(statement, params).fetchall()
        finally:
            conn.close()

    def orders(self, db):
        return [json.loads(r[0])["status"] for r in self.sql(db, "SELECT payload FROM paper_orders ORDER BY order_id")]

    def in_process(self, name, point=None, target=None, dir_=None):
        dir_ = Path(dir_ or self.dir)
        dir_.mkdir(parents=True, exist_ok=True)
        gate = HaltGate()
        injector = Injector(gate, lambda: gate.request(15), point=point, target=target)
        statuses = scenario(name, dir_, gate, injector)
        return {"statuses": statuses, **injector.evidence()}

    def assert_invariants(self, out, db, *, verifier=True, prefixes=None):
        """The halt properties every draw must satisfy."""
        self.assertTrue(out["halted"], out["fired_at"])
        t_h = out["t_h"]
        for admission in out["admissions"]:
            self.assertLess(admission["l_w_ns"], t_h)  # I-R1b: L(W) < T_h
            if admission["read2_ns"] is not None:
                self.assertLess(admission["read2_ns"], t_h)  # I-R1b (Alternative 1): read2 < T_h
        self.assertLessEqual(len(out["residual"]), 1)  # I-R9
        self.assertLessEqual(out["saves_after_t_h"], 1)
        self.assertLessEqual(out["saves_after_t_h"], len(out["residual"]))  # a save begun after T_h is the residual
        self.assertEqual(out["saves"], sum(a["read2_ns"] is not None for a in out["admissions"]))  # all admitted
        for halted, in_save, sql in out["statements"]:
            if halted and WRITE.match(sql):
                self.assertTrue(in_save or allowed_post_halt(sql, residual=False),
                                f"post-T_h write outside the allowlist at {out['fired_at']}: {sql}")
        if verifier:
            report = verify(db, edg_start=GENESIS)
            invalid = [f for f in report["findings"] if f["severity"] == "INVALID"]
            self.assertEqual(invalid, [], (out["fired_at"], report["findings"]))
            self.assertTrue(all(g["result"] == "PASS" for g in report["g14"]), report["g14"])
            halts = [h for h in report["halts"] if "halt_journal_id" in h]
            self.assertLessEqual(len(halts), 1)
            if out["recorded_journal_id"] is not None:
                [halt] = halts
                self.assertEqual(len(halt["residual"]), len(out["residual"]))
                for item in halt["residual"]:
                    self.assertGreater(item["read2_to_t_h_ns"], 0)
                    self.assertGreater(item["t_h_to_t_stop_ns"], 0)
                    self.assertGreater(item["t_stop_to_t_ack_ns"], 0)  # I-R13, strict with the gate clock
        if prefixes is not None:  # the halted economic state is the reference state after some committed write
            self.assertIn(snapshot(db)[0], prefixes, out["fired_at"])

    def assert_point(self, name, out, db):
        """The expected outcome of each named point (a)-(g)."""
        if name in ("a", "b", "c"):
            self.assertEqual(out["statuses"][-1], "HALTED")
            self.assertEqual(self.orders(db), ["PENDING"])  # no fill: nothing economic after T_h
            self.assertEqual(out["residual"], [])
            self.assertEqual(out["refused"][0], {"kind": "pending_fill", "stage": "admission"} if name == "a"
                             else {"kind": "pending_fill", "stage": "read2", "seq": out["admissions"][-1]["seq"]})
        elif name in ("c2", "d"):  # the single accepted residual: begun (c2) or in its transaction (d) at T_h
            self.assertEqual(out["statuses"][-1], "HALTED")
            self.assertEqual(self.orders(db), ["FILLED"])
            [residual] = out["residual"]
            self.assertEqual(residual["kind"], "pending_fill")
            self.assertEqual(out["saves_after_t_h"], 1 if name == "c2" else 0)
        elif name == "e":  # committed (T_stop recorded) before T_h: no residual, nothing further admitted
            self.assertEqual(out["statuses"][-1], "HALTED")
            self.assertEqual(self.orders(db), ["FILLED"])
            self.assertEqual(out["residual"], [])
            self.assertEqual(out["admissions"][-1]["kind"], "pending_fill")
        elif name == "f":
            self.assertEqual(out["statuses"], ["HALTED"])
            self.assertEqual([a["kind"] for a in out["admissions"]], ["catch_up_bar"])  # bar 1 only
            self.assertEqual(out["refused"], [{"kind": "catch_up_bar", "stage": "admission"}])  # bar 2 refused
            self.assertEqual(out["residual"], [])
            position = json.loads(self.sql(db, "SELECT payload FROM paper_positions")[0][0])
            self.assertEqual(position["status"], "OPEN")  # the bar-3 stop was never applied
            self.assertEqual(self.sql(db, "SELECT count(*) FROM closed_trades"), [(0,)])
        elif name == "f2":
            self.assertEqual(out["statuses"][-1], "HALTED")
            self.assertEqual(out["refused"][-1]["stage"], "read2")
            self.assertEqual(out["refused"][-1]["kind"], "catch_up_bar")
            self.assertEqual(out["residual"], [])
            self.assertEqual(self.sql(db, "SELECT count(*) FROM closed_trades"), [(0,)])  # its stop never applied
        elif name == "g":
            self.assertEqual(len(out["statuses"]), 1)  # no EURUSD cycle
            self.assertEqual([r[0] for r in self.sql(db, "SELECT symbol FROM runs")], ["XAUUSD"])
            self.assertEqual(self.sql(db, "SELECT count(*) FROM symbol_locks"), [(0,)])
            self.assertEqual(out["recorded_journal_id"], None)  # outside any run: recorded by DemoRunner.close
        if name not in ("f", "g"):
            self.assertEqual(self.sql(db, "SELECT count(*) FROM symbol_locks"), [(0,)])  # D-1
            self.assertEqual(self.sql(db, "SELECT count(*) FROM journal WHERE event_type='HALT_OBSERVED'"), [(1,)])


# name -> (scenario, (event, kind, nth))
POINTS = {
    "a": ("period", ("before_admit", "pending_fill", 2)),  # nth 1: cycle 0, before any order exists
    "b": ("period", ("after_admit", "pending_fill", 2)),
    "c": ("period", ("compute", "pending_fill", 1)),
    "c2": ("period", ("after_read2", "pending_fill", 1)),
    "d": ("period", ("in_tx", "pending_fill", 1)),
    "e": ("period", ("after_commit", "pending_fill", 1)),
    "f": ("catch_up", ("after_commit", "catch_up_bar", 1)),
    "f2": ("period", ("before_read2", "catch_up_bar", 1)),  # a computed bar refused at read 2 (oracle NG12 fix)
    "g": ("symbols", ("between_symbols", "XAUUSD", 1)),
}


class InjectionPointTests(Checks):
    """Deterministic fault injection (the handler body) at every 3.8 point."""

    def test_every_point(self):
        for name, (kind, point) in POINTS.items():
            with self.subTest(point=name):
                dir_ = self.dir / name
                out = self.in_process(kind, point=point, dir_=dir_)
                db = dir_ / "trading_floor.db"
                self.assertIsNotNone(out["fired_at"], f"point {name} never reached")
                self.assert_point(name, out, db)
                self.assert_invariants(out, db, verifier=kind == "period")


class RandomizedPropertyTests(Checks):
    """T_h drawn uniformly over every injection event of the period; every invariant holds for every draw."""

    def test_randomized_halt_timing_property(self):
        statuses, log, prefixes = reference_period(self.dir / "reference")
        self.assertEqual(len(statuses), len(PERIOD))
        self.assertNotIn("HALTED", statuses)
        self.assertEqual(set(log), {"after_claim", "ai", "before_admit", "after_admit", "compute", "before_read2",
                                    "after_read2", "in_tx", "commit_stmt", "after_commit"})
        seen = set()
        for target in draws(log, random.Random(20261010), 40):
            with self.subTest(target=target):
                dir_ = self.dir / f"t{target}"
                out = self.in_process("period", target=target, dir_=dir_)
                self.assertEqual(out["fired_at"][0], target)
                seen.add(out["fired_at"][1])
                self.assert_invariants(out, dir_ / "trading_floor.db", prefixes=prefixes)
        self.assertEqual(seen, set(log))


class CoarseClockTests(Checks):
    """The defect fixed by ``HaltGate.now_ns``: with a coarse monotonic clock (~15.6 ms, as on Windows) read2, T_h,
    T_stop and T_ack collided, the residual's REX_WRITE was refused (orphan economic events) and T_stop == T_h hid
    the residual. The gate clock keeps every halt instant strictly ordered."""

    def test_gate_clock_is_strictly_increasing_on_a_frozen_clock(self):
        gate = HaltGate()
        with patch("time.monotonic_ns", return_value=1_000):
            admission = gate.admit("submit")
            gate.pre_save(admission)
            gate.request(15)
            t_stop = gate.now_ns()
        self.assertLess(admission.l_w_ns, admission.read2_ns)
        self.assertLess(admission.read2_ns, gate.requested_at[0])
        self.assertLess(gate.requested_at[0], t_stop)

    def test_every_point_on_a_coarse_clock(self):
        real = time.monotonic_ns
        with patch("time.monotonic_ns", lambda: real() // 15_625_000 * 15_625_000):
            for name in ("a", "b", "c", "c2", "d", "e"):
                with self.subTest(point=name):
                    dir_ = self.dir / name
                    out = self.in_process("period", point=POINTS[name][1], dir_=dir_)
                    self.assert_point(name, out, dir_ / "trading_floor.db")
                    self.assert_invariants(out, dir_ / "trading_floor.db")


class VerifierBoundaryTests(unittest.TestCase):
    """The verifier's strict proof obligation ``L(W) < T_h`` / ``read2 < T_h`` and its I-R13 / residual checks."""

    T_H = 1_000

    def halt(self, **extra):
        payload = {"process_id": "p", "t_h_monotonic_ns": self.T_H, "t_ack_ns": 2_000, "run_id": "r",
                   "residual": [], **extra}
        return [(10, json.dumps(payload))]

    def write(self, l_w, read2, t_stop, seq=1, process_id="p"):
        return (5, {"result": "COMMITTED", "admission": {"process_id": process_id, "seq": seq, "kind": "submit",
                                                         "l_w_ns": l_w, "read2_ns": read2, "t_stop_ns": t_stop}})

    def codes(self, halts, writes):
        findings, _ = halt_verifier.check(halts, writes, [(1, "runtime", "RECOVERY_STARTED", None)])
        return {f["code"] for f in findings}

    def test_strictly_before_t_h_is_clean(self):
        self.assertEqual(self.codes(self.halt(), [self.write(998, 999, 999)]), set())

    def test_l_w_equal_to_t_h_is_admitted_after_halt(self):
        self.assertIn("ADMITTED_AFTER_HALT", self.codes(self.halt(), [self.write(self.T_H, 999, 999)]))
        self.assertIn("ADMITTED_AFTER_HALT", self.codes(self.halt(), [self.write(self.T_H + 1, 999, 999)]))

    def test_read2_equal_to_t_h_is_flagged(self):
        self.assertIn("READ2_AFTER_HALT", self.codes(self.halt(), [self.write(998, self.T_H, 999)]))

    def test_t_ack_before_t_stop(self):
        residual = [{"seq": 1}]
        self.assertIn("T_ACK_BEFORE_T_STOP", self.codes(self.halt(residual=residual), [self.write(998, 999, 2_001)]))

    def test_recorded_residual_must_match(self):
        self.assertIn("HALT_RECORD_CONTRADICTION", self.codes(self.halt(), [self.write(998, 999, 1_500)]))
        self.assertEqual(self.codes(self.halt(residual=[{"seq": 1}]), [self.write(998, 999, 1_500)]), set())

    def test_missing_read2_or_t_stop(self):
        self.assertIn("ADMISSION_EVIDENCE_MISSING", self.codes(self.halt(), [self.write(998, None, 999)]))
        self.assertIn("ADMISSION_EVIDENCE_MISSING", self.codes(self.halt(), [self.write(998, 999, None)]))


@unittest.skipUnless(POSIX, "real SIGTERM delivery to a Python handler needs POSIX (Windows os.kill terminates)")
class RealSignalRaceTests(Checks):
    """The same points and a randomized sample with REAL signals, each in an isolated child process."""

    def child(self, dir_, name, point, number, signame="SIGTERM"):
        dir_.mkdir(parents=True, exist_ok=True)
        env = {k: v for k, v in os.environ.items() if not k.startswith("AI_FLOOR_")}
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        event, kind = point if point is not None else ("-", "-")
        code = "import sys; sys.path.insert(0, '.'); from test_phase8_rhalt_races import child; child(sys.argv[1:])"
        done = subprocess.run([sys.executable, "-c", code, str(dir_), name, event, kind or "-", str(number), signame],
                              cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr[-3000:])
        lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
        out = json.loads(lines[-1])
        self.assertEqual(out["signal"], getattr(signal, signame))  # T_h was set by the real handler
        return out

    def test_real_sigterm_at_every_point(self):
        for name, (kind, (event, event_kind, nth)) in POINTS.items():
            with self.subTest(point=name):
                dir_ = self.dir / name
                out = self.child(dir_, kind, (event, event_kind), nth)
                db = dir_ / "trading_floor.db"
                self.assert_point(name, out, db)
                self.assert_invariants(out, db, verifier=kind == "period")

    def test_real_sigint_uses_the_same_handler(self):
        out = self.child(self.dir / "sigint", "period", ("in_tx", "pending_fill"), 1, "SIGINT")
        self.assert_point("d", out, self.dir / "sigint" / "trading_floor.db")
        self.assert_invariants(out, self.dir / "sigint" / "trading_floor.db")

    def test_real_sigterm_randomized(self):
        _, log, prefixes = reference_period(self.dir / "reference")
        for target in draws(log, random.Random(5_2026_10_10), 4):
            with self.subTest(target=target):
                dir_ = self.dir / f"r{target}"
                out = self.child(dir_, "period", None, target)
                self.assertEqual(out["fired_at"][0], target)
                self.assert_invariants(out, dir_ / "trading_floor.db", prefixes=prefixes)


if __name__ == "__main__":
    unittest.main()
