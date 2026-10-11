"""V2 Phase 8 / P3 G15 chain verifier (replay/rex_chain.py): read-only, SQLite-provable checks, adversarial
evidence edits, and the explicit boundary with external evidence (never VERIFIED). Temporary databases only.

Edits that re-sign a REX row (recompute ``rex_digest``) model an actor with database write access rewriting evidence
consistently; edits that do not re-sign model plain tampering.
"""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch

from ai.provider import DeterministicAIProvider
from replay.rex_chain import main as chain_main, verify
from runtime.config import RuntimeConfig
from runtime.rex import REX_VERSION, RexRecorder
from runtime.service import OperationalRuntime
from storage.economic_digest import H, expected_edg_genesis
from test_demo_runner import Data, T, instrument, macro_fixture
from test_phase8_rex_writer import catch_up_scenario, plan_scenario, rex_rows

GENESIS = expected_edg_genesis("paper-main", 10000.0)["edg"]


def codes(report, severity="INVALID"):
    return {f["code"] for f in report["findings"] if f["severity"] == severity}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_class = tempfile.TemporaryDirectory(prefix="v2-p3-chain-")
        cls.master = Path(cls.tmp_class.name) / "master.db"
        plan_scenario(cls.master)
        cls.cu_master = Path(cls.tmp_class.name) / "cu_master.db"
        catch_up_scenario(cls.cu_master, Path(cls.tmp_class.name) / "ev.db")

    @classmethod
    def tearDownClass(cls):
        cls.tmp_class.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p3-chain-case-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "trading_floor.db"
        shutil.copyfile(self.master, self.db)

    def sql(self, statement, params=()):
        conn = sqlite3.connect(self.db)  # simulated tampering of the temporary test copy only
        try:
            conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()

    def resign(self, journal_id, edit):
        """Rewrite one REX row consistently (record edited, digest recomputed)."""
        conn = sqlite3.connect(self.db)
        try:
            body = json.loads(conn.execute("SELECT payload FROM journal WHERE id=?", (journal_id,)).fetchone()[0])
            record = json.loads(body["rex"])
            edit(record)
            text = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            body = {"rex": text, "rex_digest": H(REX_VERSION, text)}
            conn.execute("UPDATE journal SET payload=? WHERE id=?", (json.dumps(body, separators=(",", ":")),
                                                                     journal_id))
            conn.commit()
        finally:
            conn.close()

    def report(self, **kwargs):
        return verify(self.db, edg_start=kwargs.pop("edg_start", GENESIS), **kwargs)


class CleanTests(Base):
    def test_clean_period_is_sqlite_pass_and_never_verified(self):
        report = self.report()
        self.assertEqual(report["sqlite_result"], "SQLITE_CHECKS_PASS", report["findings"])
        self.assertEqual(report["classification"], "NOT VERIFIED")
        self.assertEqual(report["external_evidence"], "NOT EVALUATED")
        self.assertTrue(report["requires_external"])
        self.assertNotIn("VERIFIED", {report["sqlite_result"], report["classification"]} - {"NOT VERIFIED"})
        self.assertTrue(all(g["result"] == "PASS" for g in report["g14"]))
        self.assertEqual(report["counts"]["claimed_runs"], report["counts"]["rex_runs"])
        self.assertGreater(report["counts"]["economic_events"], 0)

    def test_catch_up_period_passes(self):
        shutil.copyfile(self.cu_master, self.db)
        report = verify(self.db)
        self.assertEqual(report["sqlite_result"], "SQLITE_CHECKS_PASS", report["findings"])
        self.assertIn("CHAIN_START_UNANCHORED", codes(report, "NOTE"))

    def test_read_only_and_output_guard(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        os.chmod(self.db, stat.S_IREAD)
        self.addCleanup(os.chmod, self.db, stat.S_IREAD | stat.S_IWRITE)
        out = self.dir / "report.json"
        self.assertEqual(chain_main(["--trading-db", str(self.db), "--edg-start", GENESIS, "--out", str(out)]), 0)
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["source_unchanged"])
        for suffix in ("", "-wal", "-shm"):
            with self.subTest(suffix=suffix), self.assertRaises(SystemExit):
                chain_main(["--trading-db", str(self.db), "--out", str(self.db) + suffix])
        with self.assertRaises(FileNotFoundError):
            verify(self.dir / "absent.db")


class ChainTamperTests(Base):
    def writes(self):
        return [(jid, record) for jid, _, _, record, _ in rex_rows(self.db, "REX_WRITE")]

    def test_ng_b3_backfilled_rex_row(self):
        jid, _ = self.writes()[0]
        conn = sqlite3.connect(self.db)
        try:
            row = conn.execute("SELECT timestamp,run_id,symbol,source,event_type,severity,payload FROM journal "
                               "WHERE id=?", (jid,)).fetchone()
            conn.execute("DELETE FROM journal WHERE id=?", (jid,))
            conn.execute("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                         "VALUES(?,?,?,?,?,?,?)", row)  # the same evidence, written later
            conn.commit()
        finally:
            conn.close()
        report = self.report()
        self.assertEqual(report["classification"], "CERTIFICATION_INVALID")
        self.assertTrue(codes(report) & {"BACKFILL_OR_INTERLEAVING", "RUN_WRITE_LINK_MISMATCH",
                                         "REX_ORDER_CONTRADICTION", "EDG_CHAIN_BREAK"}, report["findings"])

    def test_ng_b4_psh_chain_break_and_edg_chain_break(self):
        jid, _ = self.writes()[1]
        self.resign(jid, lambda r: r.update(psh_before="0" * 64, pre={**r["pre"], "psh": "0" * 64}))
        self.assertIn("PSH_CHAIN_BREAK", codes(self.report()))
        shutil.copyfile(self.master, self.db)
        self.resign(jid, lambda r: r.update(edg_before="1" * 64, pre={**r["pre"], "edg": "1" * 64}))
        self.assertIn("EDG_CHAIN_BREAK", codes(self.report()))

    def test_unsigned_tampering_is_detected(self):
        jid, _ = self.writes()[0]
        conn = sqlite3.connect(self.db)
        try:
            payload = conn.execute("SELECT payload FROM journal WHERE id=?", (jid,)).fetchone()[0]
            conn.execute("UPDATE journal SET payload=? WHERE id=?", (payload.replace("COMMITTED", "COMMITED"), jid))
            conn.commit()
        finally:
            conn.close()
        self.assertIn("REX_DIGEST_MISMATCH", codes(self.report()))

    def test_orphan_economic_event(self):
        self.sql("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                 "VALUES('2026-01-15T15:00:00+00:00','x','XAUUSD','ghost','ORDER_FILLED','INFO','{}')")
        self.assertIn("ORPHAN_ECONOMIC_EVENT", codes(self.report()))

    def test_final_edg_differs_after_a_closed_position_edit(self):
        self.sql("UPDATE paper_positions SET payload=payload||' ' WHERE json_extract(payload,'$.status')='CLOSED'")
        report = self.report()
        self.assertIn("FINAL_EDG_MISMATCH", codes(report))
        self.assertNotIn("FINAL_PSH_MISMATCH", codes(report))  # psh never covered CLOSED rows; EDG does

    def test_run_without_coverage(self):
        run_jid, run_id = next((jid, run) for jid, run, _, record, _ in rex_rows(self.db, "REX_RUN")
                               if record["writes"])
        self.sql("DELETE FROM journal WHERE id=?", (run_jid,))
        self.assertIn("WRITE_WITHOUT_RUN_RECORD", codes(self.report()))

    def test_nwr_days_above_the_limit(self):
        for day in (16, 17, 18):
            self.sql("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                     "VALUES(?,?,?,?,?,?,?)", (f"2026-01-{day}T14:00:00+00:00", f"nwr-{day}", "XAUUSD", "runtime",
                                               "RUN_STARTED", "INFO", "{}"))
        report = self.report()
        self.assertEqual(report["counts"]["nwr_days"], ["2026-01-16", "2026-01-17", "2026-01-18"])
        self.assertIn("NWR_DAYS_ABOVE_LIMIT", codes(report))

    def test_ng13_reordered_state_tuple(self):
        jid, which, part = next((jid, which, part) for jid, r in self.writes() for which in ("pre_state", "post_state")
                                for part in (1, 2, 3, 4) if len(r[which][part]) > 1)
        self.resign(jid, lambda r: r[which].__setitem__(part, list(reversed(r[which][part]))))
        self.assertTrue(codes(self.report()) & {"STATE_NOT_CANONICAL", "PSH_RECOMPUTE_MISMATCH"})

    def test_g14_failure_with_and_without_economic_write(self):
        run_jid = next(jid for jid, _, _, record, _ in rex_rows(self.db, "REX_RUN") if record["writes"])

        def flip(record):
            st5 = next(s for s in record["stages"] if s["stage"] == "ST5")
            st5["final_status"] = "AI_CAUTION"
        self.resign(run_jid, flip)
        self.assertIn("G14_FAIL_WITH_ECONOMIC_WRITE", codes(self.report()))

    def test_contradictory_evidence(self):
        run_jid = rex_rows(self.db, "REX_RUN")[0][0]
        self.resign(run_jid, lambda r: r.update(run_id="someone-else"))
        self.assertIn("REX_IDENTITY_CONTRADICTION", codes(self.report()))

    def test_rule_identity_changed_inside_the_window(self):
        run_jid = rex_rows(self.db, "REX_RUN")[0][0]
        self.resign(run_jid, lambda r: r["identity"]["rule_identity"]["files"].update({"riesgo.py": "f" * 64}))
        self.assertIn("RULE_IDENTITY_CHANGED", codes(self.report()))

    def test_ng12_missing_catch_up_bar(self):
        shutil.copyfile(self.cu_master, self.db)
        run_jid = next(jid for jid, _, _, record, _ in rex_rows(self.db, "REX_RUN")
                       if any(s.get("observer") == "catch_up_bar" for s in record["stages"]))

        def drop(record):
            first = next(i for i, s in enumerate(record["stages"]) if s.get("observer") == "catch_up_bar")
            del record["stages"][first]
        self.resign(run_jid, drop)
        report = verify(self.db)
        self.assertIn("G14_FAIL_WITH_ECONOMIC_WRITE", codes(report))
        self.assertIn("CATCH_UP_BAR_COVERAGE", json.dumps(report["findings"]))


class RuntimeEventTests(Base):
    def run_plan(self, db, **kwargs):
        return plan_scenario(db, **kwargs)

    def test_crash_after_economic_commit_before_rex(self):
        """NG-B2: the economic commit persists; the REX_WRITE row never appears -> INVALID, never repaired."""
        db = self.dir / "crash.db"
        real = RexRecorder._write_row

        def crash(recorder, kind, record):
            context = getattr(record["context"], "value", record["context"])
            if kind == "REX_WRITE" and context.get("stage") == "ST9":
                raise KeyboardInterrupt("process killed between the economic commit and its REX row")
            return real(recorder, kind, record)
        with patch.object(RexRecorder, "_write_row", crash), self.assertRaises(KeyboardInterrupt):
            plan_scenario(db, steps=((0, 100.),))
        report = verify(db, edg_start=GENESIS)
        self.assertEqual(report["classification"], "CERTIFICATION_INVALID")
        self.assertTrue(codes(report) & {"ORPHAN_ECONOMIC_EVENT", "FINAL_EDG_MISMATCH"})

    def test_account_created_after_the_period_start(self):
        db = self.dir / "recreated.db"
        plan_scenario(db, steps=((0, 100.),))
        conn = sqlite3.connect(db)
        conn.execute("DELETE FROM paper_orders")
        conn.execute("DELETE FROM paper_accounts")
        conn.commit()
        conn.close()
        config = RuntimeConfig(db_path=db, enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=True, starting_equity=9000.0)
        OperationalRuntime(config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                           macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                           clock=lambda: T + timedelta(minutes=15)).close()  # bootstrap recreates the account
        report = verify(db, edg_start=GENESIS)
        self.assertEqual(report["classification"], "CERTIFICATION_INVALID")
        self.assertIn("FINAL_EDG_MISMATCH", codes(report))

    def test_ng14_stale_attempt_is_reported_conservatively(self):
        """A STALE attempt implies a second writer: its entry is flagged and the period is not certifiable."""
        db = self.dir / "stale.db"
        from storage.database import Store
        real = Store.save_paper
        state = {"done": False}

        def save(store, broker, **kwargs):
            if not state["done"] and kwargs.get("expected_state") is not None and broker.orders:
                state["done"] = True  # another writer commits between the REX pre-snapshot and the CAS
                other = sqlite3.connect(db)
                account = json.loads(other.execute("SELECT payload FROM paper_accounts").fetchone()[0])
                account["cash"] = account["cash"] - 1.0
                other.execute("UPDATE paper_accounts SET payload=?", (json.dumps(account, separators=(",", ":")),))
                other.commit()
                other.close()
            return real(store, broker, **kwargs)
        with patch.object(Store, "save_paper", save):
            plan_scenario(db, steps=((0, 100.),))
        attempts = [(w["context"]["stage"], w["attempt"], w["result"], w["psh_after"] is not None)
                    for _, _, _, w, _ in rex_rows(db, "REX_WRITE")]
        self.assertIn(("ST9", 1, "STALE", False), attempts)
        self.assertIn(("ST9", 2, "COMMITTED", True), attempts)
        report = verify(db, edg_start=GENESIS)
        self.assertIn("FOREIGN_WRITE_DETECTED", codes(report))


if __name__ == "__main__":
    unittest.main()
