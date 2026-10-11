"""V2 Phase 8 / P4a R-HALT gate unit tests (runtime/halt.py): handler minimality (I-R11), the two reads, allowlist,
the H transaction with the D-1 lock release (rollback included) and the read-only startup barrier."""
import ast
from datetime import timedelta
import inspect
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from runtime import halt
from runtime.config import RHALT_ENV, RuntimeConfig
from runtime.halt import (HaltGate, HaltPending, HaltRefused, PERSISTENCE_SITES, RESUME_ENV, halt_bookkeeping,
                          startup_halt_check)
from storage.database import Store
from test_demo_runner import T
from test_phase8_catch_up_scope import LEGACY_FINGERPRINT, env


class GateTests(unittest.TestCase):
    def test_reads_and_timestamps_order(self):
        gate = HaltGate()
        first = gate.admit("submit")
        gate.pre_save(first)
        self.assertLess(first.l_w_ns, first.read2_ns)
        second = gate.admit("management")
        gate.request(15)
        t_h = gate.requested_at[0]
        self.assertLess(second.l_w_ns, t_h)  # the timestamp precedes the read, so an admission precedes T_h
        with self.assertRaises(HaltRefused) as refused:
            gate.pre_save(second)
        self.assertEqual((refused.exception.kind, refused.exception.stage), ("management", "read2"))
        with self.assertRaises(HaltRefused):
            gate.admit("submit")
        self.assertEqual([r["stage"] for r in gate.refused], ["read2", "admission"])

    def test_handler_is_one_assignment_without_io(self):
        """I-R11 / NR21: no I/O, lock, transaction or exception; a second signal keeps the first T_h."""
        def calls(function):
            source = inspect.getsource(function)
            return {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(ast.parse(source.strip()))
                    if isinstance(n, ast.Call)}
        self.assertLessEqual(calls(HaltGate.request), {"now_ns", "_utc_now"})
        self.assertLessEqual(calls(HaltGate.now_ns), {"monotonic_ns"})  # the gate clock: assignments only
        gate = HaltGate()
        with patch("sqlite3.connect", side_effect=AssertionError("db access")), \
                patch("builtins.open", side_effect=AssertionError("file access")):
            gate.request(15)
            first = gate.requested_at
            gate.request(2)
        self.assertIs(gate.requested_at, first)

    def test_allowlist(self):
        gate = HaltGate()
        self.assertTrue(all(gate.allow(c) for c in "EDOLHPSR"))
        gate.request()
        self.assertEqual({c for c in "DOLHPSR" if gate.allow(c)}, {"H", "P", "R"})
        with self.assertRaises(ValueError):
            gate.allow("X")
        self.assertTrue(set(PERSISTENCE_SITES.values()) <= set(halt.CATEGORIES))

    def test_residual_measurement(self):
        gate = HaltGate()
        a = gate.admit("pending_fill")
        gate.pre_save(a)
        gate.request()
        gate.saved(a, time.monotonic_ns())
        [residual] = gate.residual()
        self.assertGreater(residual["read2_to_t_h_ns"], 0)
        self.assertGreater(residual["t_h_to_t_stop_ns"], 0)


class ConfigTests(unittest.TestCase):
    def test_off_by_default_requires_rex_and_keeps_fingerprint(self):
        with env():
            config = RuntimeConfig.from_env()
        self.assertFalse(config.v2_rhalt)
        self.assertEqual(config.fingerprint(), LEGACY_FINGERPRINT)
        with self.assertRaises(ValueError):
            RuntimeConfig(v2_rhalt=True)  # R-HALT ON requires REX ON
        with env(**{RHALT_ENV: "1"}), self.assertRaises(ValueError):
            RuntimeConfig.from_env()
        with env(**{RHALT_ENV: "1", "AI_FLOOR_V2_REX": "1"}):
            self.assertTrue(RuntimeConfig.from_env().v2_rhalt)
        for bad in ("true", "2", "on"):
            with self.subTest(value=bad), env(**{RHALT_ENV: bad}), self.assertRaises(ValueError):
                RuntimeConfig.from_env()


class StoreCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p4a-gate-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "trading_floor.db"
        self.store = Store(self.db)
        self.addCleanup(self.store.close)
        for symbol in ("XAUUSD", "EURUSD"):
            self.store.claim_slot(f"k-{symbol}", symbol, T, T, run_id=f"r-{symbol}")
        self.gate = HaltGate()
        self.gate.request(15)

    def rows(self, sql):
        return self.store.db.execute(sql).fetchall()


class BookkeepingTests(StoreCase):
    def test_h_transaction_releases_only_the_halted_run_lock(self):
        journal_id = halt_bookkeeping(self.store, self.gate, run_id="r-XAUUSD", slot_key="k-XAUUSD",
                                      symbol="XAUUSD", refused_kind="submit", refused_stage="admission")
        self.assertEqual(self.gate.recorded_journal_id, journal_id)
        self.assertEqual([tuple(r) for r in self.rows("SELECT symbol, slot_key FROM symbol_locks")],
                         [("EURUSD", "k-EURUSD")])  # other runs' locks untouched (D-1)
        self.assertEqual([tuple(r) for r in self.rows("SELECT slot_key, status, final_status FROM runs ORDER BY 1")],
                         [("k-EURUSD", "RUNNING", None), ("k-XAUUSD", "COMPLETED", "HALTED")])
        payload = json.loads(self.rows("SELECT payload FROM journal WHERE event_type='HALT_OBSERVED'")[0][0])
        self.assertTrue(payload["symbol_lock_released"])
        self.assertEqual(payload["refused_kind"], "submit")

    def test_failure_rolls_back_everything_and_confirms_nothing(self):
        before = (self.rows("SELECT * FROM symbol_locks"), self.rows("SELECT status FROM runs"),
                  self.rows("SELECT count(*) FROM journal"))
        with patch.object(self.store, "_event", side_effect=sqlite3.OperationalError("disk I/O error")), \
                self.assertRaises(sqlite3.OperationalError):
            halt_bookkeeping(self.store, self.gate, run_id="r-XAUUSD", slot_key="k-XAUUSD", symbol="XAUUSD")
        self.assertEqual((self.rows("SELECT * FROM symbol_locks"), self.rows("SELECT status FROM runs"),
                          self.rows("SELECT count(*) FROM journal")), before)
        self.assertIsNone(self.gate.recorded_journal_id)

    def test_refuses_a_run_that_is_not_running(self):
        with self.assertRaises(RuntimeError):
            halt_bookkeeping(self.store, self.gate, run_id="other", slot_key="k-XAUUSD", symbol="XAUUSD")
        self.assertEqual(len(self.rows("SELECT * FROM symbol_locks")), 2)

    def test_restart_after_rollback_recovers_the_run(self):
        with patch.object(self.store, "_event", side_effect=sqlite3.OperationalError("x")), \
                self.assertRaises(sqlite3.OperationalError):
            halt_bookkeeping(self.store, self.gate, run_id="r-XAUUSD", slot_key="k-XAUUSD", symbol="XAUUSD")
        self.store.recover(T + timedelta(hours=1), stale_after_seconds=0)
        self.assertEqual({tuple(r) for r in self.rows("SELECT status, error FROM runs")},
                         {("FAILED", "interrupted_run")})
        self.assertEqual(self.rows("SELECT * FROM symbol_locks"), [])


class StartupTests(StoreCase):
    def config(self):
        return RuntimeConfig(db_path=self.db, v2_rex=True, v2_rhalt=True)

    def digest(self):
        return self.db.read_bytes()

    def test_no_halt_starts_and_off_never_checks(self):
        self.assertIsNone(startup_halt_check(self.config(), {}))
        self.assertIsNone(startup_halt_check(RuntimeConfig(db_path=self.db), {}))
        self.assertIsNone(startup_halt_check(RuntimeConfig(db_path=Path(self.tmp.name) / "absent.db", v2_rex=True,
                                                           v2_rhalt=True), {}))

    def test_pending_halt_refuses_without_writing(self):
        first = halt_bookkeeping(self.store, self.gate, run_id="r-XAUUSD", slot_key="k-XAUUSD", symbol="XAUUSD")
        self.store.close()
        before = self.digest()
        for token in (None, "", "wrong", str(first + 1)):
            with self.subTest(token=token), self.assertRaises(HaltPending):
                startup_halt_check(self.config(), {} if token is None else {RESUME_ENV: token})
        self.assertEqual(self.digest(), before)  # read-only barrier
        self.assertEqual(startup_halt_check(self.config(), {RESUME_ENV: str(first)}), first)
        self.store = Store(self.db)
        second_gate = HaltGate()
        second_gate.request()
        second = halt_bookkeeping(self.store, second_gate)  # a new halt: the old token no longer resumes
        self.store.close()
        with self.assertRaises(HaltPending):
            startup_halt_check(self.config(), {RESUME_ENV: str(first)})
        self.assertEqual(startup_halt_check(self.config(), {RESUME_ENV: str(second)}), second)

    def test_check_failure_refuses(self):
        with patch("runtime.halt.sqlite3.connect", side_effect=sqlite3.OperationalError("unable to open")), \
                self.assertRaises(HaltPending):
            startup_halt_check(self.config(), {})

    def test_runtime_refuses_before_any_write(self):
        halt_bookkeeping(self.store, self.gate, run_id="r-XAUUSD", slot_key="k-XAUUSD", symbol="XAUUSD")
        self.store.close()
        before = self.digest()
        from runtime.service import OperationalRuntime
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop(RESUME_ENV, None)
            with patch.object(Store, "recover", side_effect=AssertionError("recover ran")), \
                    self.assertRaises(HaltPending):
                OperationalRuntime(self.config())
        self.assertEqual(self.digest(), before)  # no recovery, no write: no automatic resume


if __name__ == "__main__":
    unittest.main()
