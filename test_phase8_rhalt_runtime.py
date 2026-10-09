"""V2 Phase 8 / P4a R-HALT in the real runtime: deterministic T_h injection at every point (a)-(g), NR12-NR30,
the post-halt allowlist (SQL spy), the residual measurement, a randomized property test, concurrent processes,
inertness with the flag OFF and the static I-R14 classification. Temporary databases only."""
import ast
from dataclasses import replace
from datetime import timedelta
import json
from pathlib import Path
import random
import re
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ai.provider import DeterministicAIProvider
from execution.paper_broker import PaperBroker
from replay.rex_chain import verify
from runtime.config import RuntimeConfig
from runtime.halt import PERSISTENCE_SITES, HaltGate
from runtime.scheduler import Scheduler
from runtime.service import OperationalRuntime
from storage.database import Store
from storage.economic_digest import H, expected_edg_genesis
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from test_phase8_rex_inertness import ECONOMIC_TABLES, snapshot
from test_phase8_rex_writer import Ids

ROOT = Path(__file__).resolve().parent
GENESIS = expected_edg_genesis("paper-main", 10000.0)["edg"]
WRITE = re.compile(r"^\s*(INSERT|UPDATE|DELETE|REPLACE)\b", re.I)


def rhalt_config(db, **extra):
    return RuntimeConfig(db_path=Path(db), enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=True, v2_rhalt=True, **extra)


class Spy:
    """SQL statements of one connection, each tagged with whether T_h had happened."""

    def __init__(self, gate):
        self.gate, self.statements = gate, []

    def __call__(self, sql):
        self.statements.append((self.gate.halted, sql))

    def post_halt_writes(self):
        return [sql for halted, sql in self.statements if halted and WRITE.match(sql)]


def allowed_post_halt(sql, residual):
    if re.search(r"INTO journal.*'rex'", sql, re.S):
        return True  # R: REX evidence rows
    if re.search(r"INTO journal.*'halt','HALT_OBSERVED'", sql, re.S) or sql.startswith("UPDATE runs SET status='COMPLETED', final_status='HALTED'") \
            or sql.startswith("DELETE FROM symbol_locks WHERE slot_key="):
        return True  # H
    if sql.startswith("INSERT INTO system_state") and re.search(r"VALUES\('(heartbeat|scheduler|runner)'", sql):
        return True  # P
    return residual  # only the admitted in-flight write may touch anything else


class Harness(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p4a-rt-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "trading_floor.db"
        self.ids = Ids()
        uuid_patch = patch("uuid.uuid4", self.ids)
        uuid_patch.start()
        self.addCleanup(uuid_patch.stop)

    def cycle(self, minutes, close=100., gate=None, patches=(), config=None):
        gate = gate or HaltGate()
        runtime = OperationalRuntime(config or rhalt_config(self.db), market_provider=Data(close=close),
                                     ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                                     instruments={"XAUUSD": instrument()},
                                     clock=lambda: T + timedelta(minutes=minutes), halt_gate=gate)
        spy = Spy(gate)
        runtime.store.db.set_trace_callback(spy)
        managers = [patched_scouts("LONG"), *patches]
        try:
            for manager in managers:
                manager.__enter__()
            try:
                status = runtime.run_cycle("XAUUSD", T + timedelta(minutes=minutes))
            finally:
                for manager in reversed(managers):
                    manager.__exit__(None, None, None)
        finally:
            runtime.close()
        return status, gate, spy

    def sql(self, statement, params=()):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(statement, params).fetchall()
        finally:
            conn.close()

    def orders(self):
        return [json.loads(r[0])["status"] for r in self.sql("SELECT payload FROM paper_orders ORDER BY order_id")]

    def report(self):
        return verify(self.db, edg_start=GENESIS)

    def assert_halted(self, status, gate, spy, *, residual):
        self.assertEqual(status, "HALTED")
        self.assertEqual(len(gate.residual()), residual)
        self.assertEqual(self.sql("SELECT count(*) FROM symbol_locks"), [(0,)])
        self.assertEqual(self.sql("SELECT final_status FROM runs ORDER BY started_at DESC LIMIT 1"), [("HALTED",)])
        for sql in spy.post_halt_writes():
            self.assertTrue(allowed_post_halt(sql, residual > 0), f"post-T_h write outside the allowlist: {sql}")
        report = self.report()
        invalid = [f for f in report["findings"] if f["severity"] == "INVALID"]
        self.assertEqual(invalid, [], report["findings"])
        self.assertTrue(all(g["result"] == "PASS" for g in report["g14"]), report["g14"])
        [halt] = [h for h in report["halts"] if "halt_journal_id" in h]
        self.assertEqual(len(halt["residual"]), residual)
        for item in halt["residual"]:
            self.assertGreater(item["read2_to_t_h_ns"], 0)
            self.assertGreater(item["t_h_to_t_stop_ns"], 0)
            self.assertGreaterEqual(item["t_stop_to_t_ack_ns"], 0)  # I-R13
        return report


def on_admit(kind, *, before=True, nth=1):
    real, seen = HaltGate.admit, {"n": 0}

    def admit(gate, k):
        if k == kind:
            seen["n"] += 1
            if seen["n"] == nth and before:
                gate.request(15)
        admission = real(gate, k)
        if k == kind and seen["n"] == nth and not before:
            gate.request(15)
        return admission
    return patch.object(HaltGate, "admit", admit)


def on_pre_save(kind):
    real = HaltGate.pre_save

    def pre_save(gate, admission):
        real(gate, admission)
        if admission.kind == kind:
            gate.request(15)
    return patch.object(HaltGate, "pre_save", pre_save)


class InjectionTests(Harness):
    def seed_pending(self):
        status, _, _ = self.cycle(0)
        self.assertEqual(status, "PLAN_READY")
        self.assertEqual(self.orders(), ["PENDING"])

    def test_a_nr12_halt_before_the_submit_admission(self):
        status, gate, spy = self.cycle(0, patches=[on_admit("submit")])
        self.assert_halted(status, gate, spy, residual=0)
        self.assertEqual(self.orders(), [])  # no order
        self.assertEqual(gate.refused, [{"kind": "submit", "stage": "admission"}])

    def test_b_after_l_w_before_the_load_read2_refuses(self):
        self.seed_pending()
        status, gate, spy = self.cycle(15, patches=[on_admit("pending_fill", before=False)])
        self.assert_halted(status, gate, spy, residual=0)
        self.assertEqual(self.orders(), ["PENDING"])  # no save_paper call, no effect
        self.assertEqual(gate.refused[0]["stage"], "read2")

    def test_c_between_load_and_read2(self):
        self.seed_pending()
        real = PaperBroker.process_next_bar

        def process(broker, order, bar):
            broker_gate.request(15)
            return real(broker, order, bar)
        broker_gate = HaltGate()
        status, gate, spy = self.cycle(15, gate=broker_gate,
                                       patches=[patch.object(PaperBroker, "process_next_bar", process)])
        self.assert_halted(status, gate, spy, residual=0)
        self.assertEqual(self.orders(), ["PENDING"])  # the in-memory fill was discarded at read 2

    def test_c_prime_nr25_between_read2_and_begin_is_the_measured_residual(self):
        self.seed_pending()
        status, gate, spy = self.cycle(15, patches=[on_pre_save("pending_fill")])
        report = self.assert_halted(status, gate, spy, residual=1)
        self.assertEqual(self.orders(), ["FILLED"])  # the single accepted residual committed after T_h
        self.assertEqual(report["halts"][0]["residual"][0]["kind"], "pending_fill")

    def test_d_nr14_inside_the_transaction(self):
        self.seed_pending()
        real_save, real_load = Store.save_paper, Store.load_paper
        state = {"inside": False}
        injection_gate = HaltGate()

        def save(store, broker, **kwargs):
            state["inside"] = True
            try:
                return real_save(store, broker, **kwargs)
            finally:
                state["inside"] = False

        def load(store, account_id):
            if state["inside"] and broker_has_fill():
                injection_gate.request(15)
            return real_load(store, account_id)

        def broker_has_fill():
            return any(a.kind == "pending_fill" for a in injection_gate.admissions)
        status, gate, spy = self.cycle(15, gate=injection_gate, patches=[patch.object(Store, "save_paper", save),
                                                                         patch.object(Store, "load_paper", load)])
        self.assert_halted(status, gate, spy, residual=1)
        self.assertEqual(self.orders(), ["FILLED"])  # a commit is never interrupted

    def test_e_after_the_commit_before_the_next_admission(self):
        self.seed_pending()
        real_save = Store.save_paper
        injection_gate = HaltGate()

        def save(store, broker, **kwargs):
            result = real_save(store, broker, **kwargs)
            if any(a.kind == "pending_fill" for a in injection_gate.admissions):
                injection_gate.request(15)
            return result
        status, gate, spy = self.cycle(15, gate=injection_gate, patches=[patch.object(Store, "save_paper", save)])
        self.assert_halted(status, gate, spy, residual=1)  # T_stop measured after T_h: reported conservatively

    def test_nr16_halt_before_the_management_admission(self):
        self.seed_pending()
        self.cycle(15)  # fill: an open position now exists
        before = self.sql("SELECT payload FROM paper_positions")
        status, gate, spy = self.cycle(30, close=101., patches=[on_admit("management")])
        self.assert_halted(status, gate, spy, residual=0)
        self.assertEqual(self.sql("SELECT payload FROM paper_positions"), before)  # no management write

    def test_nr17_halt_right_after_the_claim(self):
        real = Store.claim_slot
        injection_gate = HaltGate()

        def claim(store, *args, **kwargs):
            result = real(store, *args, **kwargs)
            injection_gate.request(15)
            return result
        status, gate, spy = self.cycle(0, gate=injection_gate, patches=[patch.object(Store, "claim_slot", claim)])
        self.assert_halted(status, gate, spy, residual=0)
        self.assertEqual(self.orders(), [])
        self.assertEqual(self.sql("SELECT count(*) FROM run_metadata"), [(0,)])  # L refused after T_h

    def test_nr22_stale_retry_after_t_h_is_refused(self):
        real = Store.save_paper
        injection_gate = HaltGate()

        def save(store, broker, **kwargs):
            if kwargs.get("expected_state") is not None and broker.orders and not injection_gate.halted:
                other = sqlite3.connect(self.db)  # a concurrent writer makes attempt 1 STALE
                account = json.loads(other.execute("SELECT payload FROM paper_accounts").fetchone()[0])
                account["cash"] -= 1.0
                other.execute("UPDATE paper_accounts SET payload=?", (json.dumps(account, separators=(",", ":")),))
                other.commit()
                other.close()
                injection_gate.request(15)
            return real(store, broker, **kwargs)
        status, gate, _ = self.cycle(0, gate=injection_gate, patches=[patch.object(Store, "save_paper", save)])
        self.assertEqual(status, "HALTED")
        self.assertEqual(gate.refused, [{"kind": "submit", "stage": "admission"}])  # I-R12: the retry's own read
        self.assertEqual(self.orders(), [])

    def test_nr26_signal_during_the_ai_stage(self):
        real = DeterministicAIProvider.generate
        injection_gate = HaltGate()

        def generate(provider, request):
            injection_gate.request(15)
            return real(provider, request)
        status, gate, spy = self.cycle(0, gate=injection_gate,
                                       patches=[patch.object(DeterministicAIProvider, "generate", generate)])
        self.assert_halted(status, gate, spy, residual=0)
        run_id = self.sql("SELECT run_id FROM runs")[0][0]
        self.assertEqual(self.sql("SELECT count(*) FROM review_reports WHERE run_id=?", (run_id,)), [(0,)])
        self.assertEqual(self.sql("SELECT count(*) FROM journal WHERE event_type='DATA_CHECK'"), [(1,)])  # before T_h

    def test_f_nr15_between_catch_up_bars_stops_at_the_last_committed_bar(self):
        from test_phase8_observe_only import XAU_STOP_T3, make
        from test_runtime_catch_up import SLOT, seed
        seed(self.db, positions=("XAUUSD",))
        gate = HaltGate()
        runtime = make(self.db, self.dir / "ev.db", SLOT, ("XAUUSD",), XAU_STOP_T3)
        runtime.config = replace(runtime.config, v2_rex=True, v2_rhalt=True)
        runtime.halt_gate = gate
        try:
            with on_admit("catch_up_bar", nth=2):
                status = runtime.run_cycle("XAUUSD", SLOT)
        finally:
            runtime.close()
        self.assertEqual(status, "HALTED")
        applied = [a for a in gate.admissions if a.kind == "catch_up_bar"]
        self.assertEqual(len(applied), 1)  # one bar committed, the next one refused
        position = json.loads(self.sql("SELECT payload FROM paper_positions")[0][0])
        self.assertEqual(position["status"], "OPEN")
        self.assertIsNotNone(position["last_processed_at"])  # I-R10: the watermark is the last committed bar

    def test_g_nr13_between_symbols_no_further_claim(self):
        from test_phase8_observe_only import make
        from test_runtime_catch_up import SLOT
        gate = HaltGate()
        runtime = make(self.db, None, SLOT, ())
        runtime.config = replace(runtime.config, v2_rex=True, v2_rhalt=True, scheduler_enabled=True)
        runtime.halt_gate = gate
        real = runtime.run_cycle

        def run_cycle(symbol, slot):
            result = real(symbol, slot)
            gate.request(15)
            return result
        runtime.run_cycle = run_cycle
        try:
            results = Scheduler(runtime, lambda: SLOT).tick()
        finally:
            runtime.close()
        self.assertEqual(len(results), 1)
        self.assertEqual([r[0] for r in self.sql("SELECT symbol FROM runs")], ["XAUUSD"])  # no EURUSD claim

    def test_nr28_close_path_records_the_process_halt_and_only_p_keys(self):
        from runtime.demo_runner import DemoRunner
        gate = HaltGate()
        runner = DemoRunner(rhalt_config(self.db), market_provider=Data(), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                            clock=lambda: T, halt_gate=gate)
        spy = Spy(gate)
        runner.store.db.set_trace_callback(spy)
        gate.request(15)  # between ticks
        self.assertFalse(runner.daily_summary(T + timedelta(days=1)))  # S suppressed
        runner.close()
        writes = spy.post_halt_writes()
        self.assertTrue(writes)
        for sql in writes:
            self.assertTrue(allowed_post_halt(sql, residual=False), sql)
        keys = set(re.findall(r"VALUES\('(\w+)'", " ".join(w for w in writes if "system_state" in w)))
        self.assertEqual(keys, {"heartbeat", "scheduler", "runner"})
        self.assertEqual(self.sql("SELECT count(*) FROM journal WHERE event_type='HALT_OBSERVED'"), [(1,)])

    def test_bookkeeping_failure_is_not_a_confirmed_halt(self):
        real = Store._event

        def event(store, at, run_id, symbol, source, event_type, *args, **kwargs):
            if event_type == "HALT_OBSERVED":
                raise sqlite3.OperationalError("disk I/O error")
            return real(store, at, run_id, symbol, source, event_type, *args, **kwargs)
        status, gate, _ = self.cycle(0, patches=[on_admit("submit"), patch.object(Store, "_event", event)])
        self.assertEqual(status, "HALT_UNCONFIRMED")
        self.assertIsNone(gate.recorded_journal_id)
        self.assertEqual(self.sql("SELECT status FROM runs"), [("RUNNING",)])  # rolled back: lock and run untouched
        self.assertEqual(self.sql("SELECT count(*) FROM symbol_locks"), [(1,)])


class RandomizedTests(Harness):
    def test_nr19_randomized_halt_timing(self):
        """Random T_h among every gate read of a 4-cycle period: I-R1b, I-R9, I-R12 always hold."""
        rng = random.Random(20261009)
        steps = ((0, 100.), (15, 100.), (30, 80.), (45, 100.))
        for trial in range(16):
            with self.subTest(trial=trial):
                db = self.dir / f"trial{trial}.db"
                target = rng.randint(1, 9)
                counter = {"n": 0}
                real_admit, real_pre = HaltGate.admit, HaltGate.pre_save

                def tick(gate):
                    counter["n"] += 1
                    if counter["n"] == target:
                        gate.request(15)

                def admit(gate, kind):
                    tick(gate)
                    return real_admit(gate, kind)

                def pre_save(gate, admission):
                    tick(gate)
                    return real_pre(gate, admission)
                gate = HaltGate()
                statuses = []
                with patch.object(HaltGate, "admit", admit), patch.object(HaltGate, "pre_save", pre_save):
                    for minutes, close in steps:
                        if gate.halted:
                            break
                        self.db = db
                        statuses.append(self.cycle(minutes, close, gate=gate)[0])
                self.assertLessEqual(len(gate.residual()), 1)  # I-R9
                t_h = gate.requested_at[0] if gate.halted else None
                for admission in gate.admissions:
                    if t_h is not None:
                        self.assertLess(admission.l_w_ns, t_h)  # I-R1b
                report = verify(db, edg_start=GENESIS)
                self.assertNotIn("INVALID", {f["severity"] for f in report["findings"]}, report["findings"])


class VerifierTamperTests(Harness):
    def halted_period(self):
        gate = HaltGate()  # one process (one process_id) across both cycles
        self.cycle(0, gate=gate)
        self.cycle(15, gate=gate, patches=[on_pre_save("pending_fill")])

    def resign(self, journal_id, edit):
        conn = sqlite3.connect(self.db)
        try:
            body = json.loads(conn.execute("SELECT payload FROM journal WHERE id=?", (journal_id,)).fetchone()[0])
            record = json.loads(body["rex"])
            edit(record)
            text = json.dumps(record, sort_keys=True, separators=(",", ":"))
            conn.execute("UPDATE journal SET payload=? WHERE id=?",
                         (json.dumps({"rex": text, "rex_digest": H("V2REX/1", text)}), journal_id))
            conn.commit()
        finally:
            conn.close()

    def codes(self):
        return {f["code"] for f in self.report()["findings"] if f["severity"] == "INVALID"}

    def writes(self):
        return [r[0] for r in self.sql("SELECT id FROM journal WHERE event_type='REX_WRITE' ORDER BY id")]

    def halt_payload(self):
        return json.loads(self.sql("SELECT payload FROM journal WHERE event_type='HALT_OBSERVED'")[0][0])

    def test_nr30_two_commits_after_t_h(self):
        self.halted_period()
        t_h = self.halt_payload()["t_h_monotonic_ns"]
        first = self.writes()[0]
        self.resign(first, lambda r: r["admission"].update(t_stop_ns=t_h + 10))
        self.assertIn("RESIDUAL_ABOVE_ONE", self.codes())

    def test_admitted_after_halt_and_missing_evidence(self):
        self.halted_period()
        t_h = self.halt_payload()["t_h_monotonic_ns"]
        last = self.writes()[-1]
        self.resign(last, lambda r: r["admission"].update(l_w_ns=t_h + 1))
        self.assertIn("ADMITTED_AFTER_HALT", self.codes())
        self.resign(last, lambda r: r.update(admission=None))
        self.assertIn("ADMISSION_EVIDENCE_MISSING", self.codes())

    def test_post_halt_write_outside_the_allowlist(self):
        self.halted_period()
        self.sql("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                 "VALUES('2026-01-15T14:00:00+00:00',NULL,'XAUUSD','market_data','DATA_CHECK','INFO','{}')")
        conn = sqlite3.connect(self.db)
        conn.execute("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                     "VALUES('2026-01-15T14:00:00+00:00',NULL,'XAUUSD','market_data','DATA_CHECK','INFO','{}')")
        conn.commit()
        conn.close()
        self.assertIn("POST_HALT_WRITE", self.codes())

    def test_malformed_halt_record(self):
        self.halted_period()
        self.sql("UPDATE journal SET payload='{\"process_id\": 1}' WHERE event_type='HALT_OBSERVED'")
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE journal SET payload='{\"process_id\": 1}' WHERE event_type='HALT_OBSERVED'")
        conn.commit()
        conn.close()
        self.assertIn("HALT_RECORD_MALFORMED", self.codes())


class ConcurrencyTests(Harness):
    def test_two_processes_have_independent_gates_and_a_second_writer_invalidates(self):
        """Process A halts; process B (its own gate, connection and process id) keeps writing: A refuses, B does
        not, and the verifier attributes the writes and flags the post-halt writer (single-writer violated)."""
        gate_a, gate_b = HaltGate(), HaltGate()
        self.assertNotEqual(gate_a.process_id, gate_b.process_id)
        runtime_b = OperationalRuntime(rhalt_config(self.db), market_provider=Data(), ai_provider=DeterministicAIProvider(),
                                       macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                                       clock=lambda: T + timedelta(minutes=15), halt_gate=gate_b)
        try:
            status_a = self.cycle(0, patches=[on_admit("submit")], gate=gate_a)[0]
            self.assertEqual(status_a, "HALTED")
            with patched_scouts("LONG"):
                status_b = runtime_b.run_cycle("XAUUSD", T + timedelta(minutes=15))
        finally:
            runtime_b.close()
        self.assertEqual(status_b, "PLAN_READY")  # B's gate was never requested
        self.assertEqual(self.orders(), ["PENDING"])  # only B's order exists
        self.assertIn("POST_HALT_WRITE", {f["code"] for f in self.report()["findings"]})


class InertnessTests(Harness):
    def run_period(self, db, config):
        self.db = db
        out = []
        for minutes, close in ((0, 100.), (15, 100.), (30, 80.), (45, 100.)):
            out.append(self.cycle(minutes, close, config=config(db))[0])
        return out

    def test_rhalt_on_without_a_halt_is_economically_identical(self):
        with patch("uuid.uuid4", Ids()):
            on = self.run_period(self.dir / "on.db", rhalt_config)
        with patch("uuid.uuid4", Ids()):
            off = self.run_period(self.dir / "off.db", lambda db: replace(rhalt_config(db), v2_rhalt=False))
        self.assertEqual(on, off)
        a, b = snapshot(self.dir / "on.db"), snapshot(self.dir / "off.db")
        for name in ECONOMIC_TABLES:
            self.assertEqual(a[0][name], b[0][name], name)
        self.assertEqual(a[1], b[1])
        self.assertEqual(a[2], b[2])


class StaticClassificationTests(unittest.TestCase):
    """I-R14 / NR29: every Store persistence call of the cycle is classified and guarded."""

    def test_every_cycle_persistence_site_is_classified_and_guarded(self):
        source = (ROOT / "runtime" / "service.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        cycle = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "_run_cycle")
        unguarded = []
        for node in ast.walk(cycle):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Attribute) and node.func.value.attr == "store"):
                continue
            method = node.func.attr
            self.assertIn(method, PERSISTENCE_SITES, f"unclassified persistence site: store.{method}")  # NR29
            if PERSISTENCE_SITES[method] in {"READ", "E"} or method == "claim_slot":
                continue
            up, guarded = node, False
            while up in parents:
                up = parents[up]
                if isinstance(up, ast.Call) and getattr(up.func, "attr", "") in {"_persist", "_audit_safely"}:
                    guarded = True
                    break
            if not guarded:
                unguarded.append((method, node.lineno))
        self.assertEqual(unguarded, [])
        self.assertIn("if self.halt_gate is not None and self.halt_gate.halted:\n            return \"HALT_REFUSED\"",
                      source)  # claim_slot: refused by an explicit check before it


if __name__ == "__main__":
    unittest.main()
