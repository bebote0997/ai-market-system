"""V2 Phase 8 / P3 REX economic inertness (rule 4): twin runs REX ON / OFF must be economically identical.

Twin temporary databases; deterministic uuid4 in both twins (REX never draws one). Compared: the five PAPER tables
byte-for-byte, the runs table, and every non-REX journal row; also under injected REX failures at every point.
"""
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime import rex
from runtime.config import REX_ENV, RuntimeConfig
from runtime.rex import RexRecorder
from test_phase8_catch_up_scope import LEGACY_FINGERPRINT, env
from test_phase8_rex_writer import catch_up_scenario, plan_scenario, rex_rows

ECONOMIC_TABLES = ("paper_accounts", "paper_orders", "paper_fills", "paper_positions", "closed_trades")


def snapshot(db):
    conn = sqlite3.connect(db)
    try:
        tables = {name: conn.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall() for name in ECONOMIC_TABLES}
        runs = conn.execute("SELECT slot_key,run_id,symbol,as_of,started_at,completed_at,status,final_status,error "
                            "FROM runs ORDER BY slot_key").fetchall()
        journal = conn.execute("SELECT timestamp,run_id,symbol,source,event_type,severity,payload FROM journal "
                               "WHERE source<>'rex' ORDER BY id").fetchall()
        return tables, runs, journal
    finally:
        conn.close()


class Twins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p3-twin-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.off = self.dir / "off.db"
        plan_scenario(self.off, rex_on=False)
        self.baseline = snapshot(self.off)

    def assert_identical(self, db):
        tables, runs, journal = snapshot(db)
        off_tables, off_runs, off_journal = self.baseline
        for name in ECONOMIC_TABLES:
            self.assertEqual(tables[name], off_tables[name], name)
        self.assertEqual(runs, off_runs)
        self.assertEqual(journal, off_journal)

    def test_rex_on_is_economically_identical_to_off(self):
        on = self.dir / "on.db"
        self.assertEqual(plan_scenario(on), plan_scenario(self.dir / "off2.db", rex_on=False))
        self.assert_identical(on)
        self.assertTrue(rex_rows(on))
        self.assertTrue(any(self.baseline[0][name] for name in ECONOMIC_TABLES[1:]))  # the scenario trades

    def test_catch_up_path_is_identical(self):
        a, b = self.dir / "cu_on.db", self.dir / "cu_off.db"
        self.assertEqual(catch_up_scenario(a, self.dir / "ev_on.db", symbols=("XAUUSD", "EURUSD")),
                         catch_up_scenario(b, self.dir / "ev_off.db", rex_on=False, symbols=("XAUUSD", "EURUSD")))
        on, off = snapshot(a), snapshot(b)
        self.assertEqual(on[0], off[0])
        self.assertEqual(on[1], off[1])
        self.assertEqual(on[2], off[2])

    def test_injected_rex_failures_never_change_economics(self):
        def boom(*args, **kwargs):
            raise RuntimeError("injected REX failure")
        injections = {
            "serialization": patch.object(rex, "rex_encode", side_effect=boom),
            "hash": patch.object(rex, "H", side_effect=boom),
            "canonical_json": patch.object(rex, "cj", side_effect=boom),
            "edg": patch.object(rex, "edg", side_effect=boom),
            "psh": patch.object(rex, "psh", side_effect=boom),
            "data_version_snapshot": patch.object(RexRecorder, "_snapshot", side_effect=boom),
            "persistence": patch.object(RexRecorder, "_write_row", side_effect=boom),
            "payload_too_large": patch.object(rex, "REX_MAX_BYTES", 10),
            "after_commit": patch.object(RexRecorder, "_after_write", side_effect=boom),
            "before_write": patch.object(RexRecorder, "before_write", side_effect=boom),
            "stage": patch.object(RexRecorder, "stage", side_effect=boom),
        }
        for name, injection in injections.items():
            with self.subTest(injection=name):
                db = self.dir / f"inject_{name}.db"
                results = plan_scenario(db, extra=(injection,))
                self.assertEqual(results, ["PLAN_READY", "PLAN_READY", "ERROR", "PLAN_READY"])
                self.assert_identical(db)
                rows = rex_rows(db)
                complete = [r for r in rows if r[2] in ("REX_RUN", "REX_WRITE") and r[3].get("complete") is True]
                # Evidence of incompleteness when possible: no complete REX survives a failing recording point.
                self.assertTrue(not complete or any(r[2] == "REX_FAILURE" for r in rows)
                                or any(r[3].get("complete") is False for r in rows if "complete" in r[3]))

    def test_observer_hook_failures_are_ignored(self):
        a, b = self.dir / "obs_on.db", self.dir / "obs_off.db"
        with patch.object(RexRecorder, "observer", side_effect=RuntimeError("observer down")):
            catch_up_scenario(a, self.dir / "ev_a.db")
        catch_up_scenario(b, self.dir / "ev_b.db", rex_on=False)
        self.assertEqual(snapshot(a)[0], snapshot(b)[0])


class FlagTests(unittest.TestCase):
    def test_flag_off_keeps_the_legacy_fingerprint(self):
        with env():
            config = RuntimeConfig.from_env()
        self.assertFalse(config.v2_rex)
        self.assertEqual(config.fingerprint(), LEGACY_FINGERPRINT)
        with env(**{REX_ENV: "0"}):
            self.assertEqual(RuntimeConfig.from_env().fingerprint(), LEGACY_FINGERPRINT)

    def test_flag_values_fail_closed(self):
        with env(**{REX_ENV: "1"}):
            on = RuntimeConfig.from_env()
        self.assertTrue(on.v2_rex)
        self.assertNotEqual(on.fingerprint(), LEGACY_FINGERPRINT)
        for bad in ("true", "yes", "2", " 1", "on"):
            with self.subTest(value=bad), env(**{REX_ENV: bad}), self.assertRaises(ValueError):
                RuntimeConfig.from_env()
        with self.assertRaises(ValueError):
            RuntimeConfig(v2_rex="1")

    def test_no_operational_activation(self):
        root = Path(__file__).resolve().parent
        for name in ("render.yaml", ".env.example", "Procfile"):
            path = root / name
            if path.exists():
                self.assertNotIn(f"{REX_ENV}=1", path.read_text(encoding="utf-8"))
                self.assertNotIn(f"{REX_ENV}: \"1\"", path.read_text(encoding="utf-8"))
        self.assertNotEqual(os.environ.get(REX_ENV), "1")


if __name__ == "__main__":
    unittest.main()
