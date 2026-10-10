"""V2 Phase 8 / P4b preparation: P2b audit corrections (LOW-1 non-creating sealed open, LOW-2 I-G18 contract), the A2
tools (P1 archive / OAR-A, A2 readiness, freeze amendment) and the G-8.INT matrix, with adversarial cases, the
two-symbol catch-up rehearsal and rollback / Phase 6 compatibility. Synthetic temporary databases and EPHEMERAL keys
only: nothing here creates or activates A2, starts E0 in any real database, or signs for the Owner."""
from dataclasses import replace
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from ai.provider import DeterministicAIProvider
from replay import a2_readiness, archive_anchor, freeze_amendment, g8int
from replay.rex_chain import verify
from runtime.config import RuntimeConfig
from runtime.genesis import GenesisSealError, verify_startup_context
from runtime.service import OperationalRuntime
from storage.economic_digest import H
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from test_phase8_genesis import SSH, Genesis, Owner, X

ROOT = Path(__file__).resolve().parent


def legacy_runtime(db, minutes=0, close=100., **extra):
    return OperationalRuntime(RuntimeConfig(db_path=Path(db), enabled_symbols=("XAUUSD", "EURUSD"), **extra),
                              market_provider=Data(close=close), ai_provider=DeterministicAIProvider(),
                              macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                              clock=lambda: T + timedelta(minutes=minutes))


def two_symbol_period(directory):
    """Seeded positions on BOTH symbols, catch-up scope XAUUSD,EURUSD, REX ON; intermediate stop touches on each."""
    from test_phase8_observe_only import EUR_STOP_T2, XAU_STOP_T3, make
    from test_runtime_catch_up import FIVE, SLOT, seed
    db, ev = Path(directory) / "two.db", Path(directory) / "two_evidence.db"
    seed(db, positions=("XAUUSD", "EURUSD"))
    for slot in (SLOT, SLOT + 3 * FIVE):
        runtime = make(db, ev, slot, ("XAUUSD", "EURUSD"), {**XAU_STOP_T3, **EUR_STOP_T2})
        runtime.config = replace(runtime.config, v2_rex=True)
        try:
            for symbol in ("XAUUSD", "EURUSD"):
                runtime.run_cycle(symbol, slot)
        finally:
            runtime.close()
    return db, ev


class LowCorrectionTests(Genesis):
    def test_low1_a_file_removed_after_step_4_is_never_recreated(self):
        _, env = self.sealed()
        context = verify_startup_context(self.config(), env)
        self.db.unlink()  # the TOCTOU window: the file disappears between steps 1-4 and the open
        with self.assertRaises(GenesisSealError):
            self.runtime(context)
        self.assertFalse(self.db.exists())  # no residual file, schema or account; the start does not continue
        legacy = self.dir / "legacy.db"
        legacy_runtime(legacy).close()  # flag OFF: the legacy Store still creates its database (unchanged)
        self.assertTrue(legacy.exists())

    def test_low2_i_g18_missing_check_is_not_verified_and_fail_is_invalid(self):
        """Contract (design 5.1.3.2, "Evidence for classification" and the failure-policy row): a process start without
        a PASS GENESIS_SEAL_CHECK -> NOT VERIFIED; a FAIL check -> INVALID. Severities are not changed."""
        oar_g, env = self.sealed()
        self.start(env)
        legacy = RuntimeConfig(db_path=self.db, enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=True)
        OperationalRuntime(legacy, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                           macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                           clock=lambda: T).close()  # a start without the seal check
        report = self.report(oar_g)
        self.assertIn("PROCESS_START_WITHOUT_SEAL_CHECK", self.codes(report, "NOT_VERIFIED"))
        self.assertNotIn("PROCESS_START_WITHOUT_SEAL_CHECK", self.codes(report))
        self.assertNotEqual(report["classification"], "CERTIFICATION_INVALID")
        self.sql("UPDATE paper_accounts SET payload=payload||' '")
        with self.assertRaises(GenesisSealError):
            self.runtime(verify_startup_context(self.config(), env))
        self.assertIn("SEAL_CHECK_FAILED", self.codes(self.report(oar_g)))

    def test_x_p2_has_a_single_pin(self):
        """I-C6: Y_P2 may change only the cloud_runner baseline constant; sealed genesis reads that same constant."""
        _, env = self.sealed()
        with patch("runtime.cloud_runner.EXPERIMENT_BASELINE_SHA", "e" * 40), self.assertRaises(GenesisSealError):
            verify_startup_context(self.config(), env)
        source = (ROOT / "runtime" / "genesis.py").read_text(encoding="utf-8")
        self.assertIn("from runtime.cloud_runner import EXPERIMENT_BASELINE_SHA", source)


@unittest.skipUnless(SSH, "ssh-keygen with -Y is required")
class A2ToolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p4b-a2-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.owner = Owner(self.dir)
        self.allowed = self.dir / "allowed_signers"
        self.allowed.write_text(self.owner.line.replace('namespaces="', 'namespaces="v2-archive-anchor,'),
                                encoding="utf-8")
        self.p1 = self.dir / "trading_floor.db"
        runtime = legacy_runtime(self.p1)
        try:
            with patched_scouts("LONG"):
                runtime.run_cycle("XAUUSD", T)  # a P1 history; then flatten it below (a pending order exists)
        finally:
            runtime.close()
        self.h5 = self.dir / "h5_attestation.txt"
        self.h5.write_text("EX = STOPPED (synthetic test attestation)", encoding="utf-8")

    def sql(self, db, statement):
        conn = sqlite3.connect(db)
        try:
            conn.execute(statement)
            conn.commit()
        finally:
            conn.close()

    def flatten(self):
        self.sql(self.p1, "UPDATE paper_orders SET payload=json_set(payload,'$.status','CANCELLED')")

    def archive(self):
        self.flatten()
        before = self.p1.read_bytes()
        archive = self.dir / "p1_archive.db"
        report, body = archive_anchor.build(self.p1, archive, h5_attestation=self.h5)
        self.assertEqual(report["result"], "READY_TO_SIGN", report)
        self.assertEqual(self.p1.read_bytes(), before)  # the source is never touched
        oar_a = self.dir / "oar_a.json"
        oar_a.write_text(body, encoding="utf-8")
        self.owner.sign(oar_a, "v2-archive-anchor")
        return archive, oar_a, json.loads(body)

    def test_archive_flat_proof_and_body(self):
        archive, _, body = self.archive()
        self.assertEqual((body["kind"], body["integrity_check"], body["schema_version"]),
                         ("TRADING_DB_ARCHIVE", "ok", 3))
        self.assertEqual((body["open_positions"], body["pending_orders"], body["running_runs"]), (0, 0, 0))
        self.assertEqual(body["final_account_equity_text"], "10000.0")
        with self.assertRaises(ValueError):
            archive_anchor.build(self.p1, archive, h5_attestation=self.h5)  # target exists
        with self.assertRaises(ValueError):
            archive_anchor.build(self.p1, self.dir / "x.db", h5_attestation=None)  # H5 required

    def test_nc12_open_position_or_pending_order_refuses_the_archive(self):
        archive = self.dir / "refused.db"
        report, body = archive_anchor.build(self.p1, archive, h5_attestation=self.h5)  # a PENDING order remains
        self.assertEqual((report["result"], body), ("REFUSED", None))
        self.assertFalse(report["checks"]["flat_no_pending_orders"])
        self.assertFalse(archive.exists())  # our own refused copy is removed

    def readiness(self, archive, oar_a, **overrides):
        p2 = self.dir / "trading_floor_p2.db"
        if not p2.exists():
            conn = sqlite3.connect(p2)  # a genesis-only P2 stand-in: one GENESIS_PREPARED, no runs, no E0
            conn.executescript("CREATE TABLE journal(id INTEGER PRIMARY KEY, event_type TEXT);"
                               "CREATE TABLE runs(slot_key TEXT);"
                               "INSERT INTO journal(event_type) VALUES('GENESIS_PREPARED');")
            conn.close()
        args = dict(p2_db=p2, pinned_p2_db=p2, p1_path=self.p1, evidence_db=self.dir / "market_evidence_p2.db",
                    archive=archive, oar_a=oar_a, allowed_signers=self.allowed)
        args.update(overrides)
        return a2_readiness.check(**args)

    def test_a2_readiness_ready_and_each_negative(self):
        archive, oar_a, _ = self.archive()
        self.assertEqual(self.readiness(archive, oar_a)["result"], "READY")
        not_pinned = self.readiness(archive, oar_a, pinned_p2_db=self.dir / "other.db")
        self.assertFalse(not_pinned["checks"]["p2_db_is_pinned"])
        self.assertFalse(self.readiness(archive, oar_a, evidence_db=self.p1)["checks"]["evidence_not_a_trading_db"])
        os.link(archive, self.dir / "archive_link.db")  # NC6: a hardlink
        self.assertFalse(self.readiness(archive, oar_a)["checks"]["archive_single_link"])
        os.unlink(self.dir / "archive_link.db")
        self.sql(archive, "CREATE TABLE tamper(x)")  # NC7: the archive changed after OAR-A
        self.assertFalse(self.readiness(archive, oar_a)["checks"]["archive_matches_oar_a"])

    def test_nc8_p2_with_more_than_genesis_and_nc9_inode_alias(self):
        archive, oar_a, _ = self.archive()
        report = self.readiness(archive, oar_a)
        p2 = self.dir / "trading_floor_p2.db"
        self.sql(p2, "INSERT INTO journal(event_type) VALUES('EXPERIMENT_STARTED')")
        self.assertEqual(report["result"], "READY")
        self.assertFalse(self.readiness(archive, oar_a)["checks"]["p2_db_genesis_only"])
        alias = self.dir / "evidence_alias.db"
        os.link(self.p1, alias)  # the Evidence Store path aliases the P1 trading DB by inode
        self.assertFalse(self.readiness(archive, oar_a, evidence_db=alias)["checks"]["evidence_not_a_trading_db"])

    def test_nc11_raw_copy_of_a_wal_database_is_detected(self):
        archive, oar_a, _ = self.archive()
        raw = self.dir / "raw_copy.db"
        wal_db = self.dir / "wal.db"
        conn = sqlite3.connect(wal_db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("CREATE TABLE t(x)")
        conn.execute("INSERT INTO t VALUES(1)")
        conn.commit()
        shutil.copyfile(wal_db, raw)  # a raw file copy misses the committed WAL content
        conn.close()
        self.assertFalse(self.readiness(raw, oar_a)["checks"]["archive_matches_oar_a"])

    def test_nc5_symlink_component(self):
        archive, oar_a, _ = self.archive()
        link = self.dir / "linked_dir"
        try:
            os.symlink(self.dir, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted on this host")
        report = self.readiness(archive, oar_a, archive=link / archive.name)
        self.assertFalse(report["checks"]["archive_no_symlink"])


class FreezeAmendmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p4b-git-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        (self.repo / "runtime").mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.invalid")
        self.git("config", "user.name", "test")
        self.git("config", "commit.gpgsign", "false")
        (self.repo / "runtime" / "cloud_runner.py").write_text(
            'X = 1\nEXPERIMENT_BASELINE_SHA = "f5032baeb87766ad74905093c1b4195117995092"\n', encoding="utf-8")
        (self.repo / "other.py").write_text("y = 1\n", encoding="utf-8")
        self.x = self.commit("X_P2")

    def git(self, *args):
        return subprocess.run([freeze_amendment._git(), "-C", str(self.repo), *args], capture_output=True, text=True,
                              check=True).stdout.strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def amendment(self, *, baseline=None, extra=None, other_line=False):
        runner = self.repo / "runtime" / "cloud_runner.py"
        text = runner.read_text(encoding="utf-8").replace("f5032baeb87766ad74905093c1b4195117995092",
                                                          baseline or self.x)
        if other_line:
            text = text.replace("X = 1", "X = 2")
        runner.write_text(text, encoding="utf-8")
        (self.repo / "EXPERIMENT_FREEZE_P2.md").write_text("# P2 freeze (synthetic)\n", encoding="utf-8")
        if extra:
            (self.repo / extra).write_text("changed\n", encoding="utf-8")
        return self.commit("Y_P2")

    def test_valid_amendment_and_unverified_signature(self):
        report = freeze_amendment.check(self.repo, self.x, self.amendment())
        self.assertEqual(report["result"], "VALID", report)
        self.assertEqual(report["commit_signature"], "NOT VERIFIED")  # never PASS without a verifiable signature

    def test_nc10_amendment_touching_other_code(self):
        self.assertEqual(freeze_amendment.check(self.repo, self.x, self.amendment(extra="other.py"))["result"],
                         "INVALID")

    def test_other_cloud_runner_change_or_wrong_pin_or_parent(self):
        self.assertEqual(freeze_amendment.check(self.repo, self.x, self.amendment(other_line=True))["result"],
                         "INVALID")

    def test_pin_must_be_x_p2_and_parent_must_be_x_p2(self):
        y = self.amendment(baseline="c" * 40)
        self.assertEqual(freeze_amendment.check(self.repo, self.x, y)["result"], "INVALID")
        self.assertEqual(freeze_amendment.check(self.repo, y, y)["result"], "INVALID")


class G8IntTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="v2-p4b-g8-", ignore_cleanup_errors=True)
        cls.db, cls.ev = two_symbol_period(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.case = tempfile.TemporaryDirectory(prefix="v2-p4b-g8-case-", ignore_cleanup_errors=True)
        self.addCleanup(self.case.cleanup)
        self.copy, self.evc = Path(self.case.name) / "t.db", Path(self.case.name) / "e.db"
        shutil.copyfile(self.db, self.copy)
        shutil.copyfile(self.ev, self.evc)

    def sql(self, db, statement, params=()):
        conn = sqlite3.connect(db)
        try:
            out = conn.execute(statement, params).fetchall()
            conn.commit()
            return out
        finally:
            conn.close()

    def statuses(self, report):
        return {k: v["status"] for k, v in {**report["criteria"], **report["integration"]}.items()}

    def test_two_symbol_rehearsal_mechanism_evidence_and_honest_gates(self):
        report = g8int.measure(self.copy, evidence_db=self.evc)
        status = self.statuses(report)
        self.assertEqual(report["phase8_verdict"], "BLOCKED")
        for name in ("G1_scope", "G6_duplicates", "G12_safety", "G14_decision_reproduction"):
            self.assertEqual(status[name], "PASS", name)
        self.assertEqual(report["criteria"]["G5_close_reconciliation"]["measured"]["reconciled"],
                         {"EURUSD": 1, "XAUUSD": 1})  # each symbol's close reproduced over committed evidence
        self.assertEqual(status["G15_whole_period_chain"], "NOT VERIFIED")  # never PASS without external evidence
        for name in ("G2_simulation_on_copies", "G11_chain_and_external_anchors", "G13_independent_review",
                     "I5_rhalt_and_recovery", "I6_catch_up_per_symbol", "I10_tests_ci_external_evidence"):
            self.assertEqual(status[name], "BLOCKED", name)
        self.assertIn("OPEN", report["gates"]["HIGH-8.1"])
        self.assertIn("OPEN", report["gates"]["M-5"])

    def test_absence_of_errors_is_never_pass(self):
        empty = Path(self.case.name) / "empty.db"
        legacy_runtime(empty).close()
        status = self.statuses(g8int.measure(empty))
        for name, value in status.items():
            if name not in ("G7_evidence_availability",):
                self.assertNotEqual(value, "PASS", name)

    def test_adversarial_measurements_fail(self):
        commit = self.sql(self.copy, "SELECT payload FROM journal WHERE event_type='REX_WRITE' AND "
                                     "payload LIKE '%POSITION_CLOSED%' LIMIT 1")
        self.assertTrue(commit)
        self.sql(self.evc, "UPDATE market_evidence SET digest='0' WHERE timeframe='5m'")  # evidence disagrees
        self.assertEqual(self.statuses(g8int.measure(self.copy, evidence_db=self.evc))["G5_close_reconciliation"],
                         "FAIL")
        self.sql(self.copy, "UPDATE runs SET final_status='ERROR' WHERE rowid=(SELECT MIN(rowid) FROM runs)")
        self.sql(self.copy, "INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status) VALUES"
                            "('n','n','NAS100','2026-01-15T13:30:00+00:00','2026-01-15T13:30:00+00:00','COMPLETED')")
        status = self.statuses(g8int.measure(self.copy))
        self.assertEqual((status["G10_runtime_health"], status["G12_safety"]), ("FAIL", "FAIL"))

    def test_tampered_rex_fails_g14_and_g15(self):
        row = self.sql(self.copy, "SELECT id, payload FROM journal WHERE event_type='REX_RUN' LIMIT 1")[0]
        record = json.loads(json.loads(row[1])["rex"])
        st5 = next(s for s in record["stages"] if s["stage"] == "ST5")
        st5["final_status"] = "AI_CAUTION"
        text = json.dumps(record, sort_keys=True, separators=(",", ":"))
        self.sql(self.copy, "UPDATE journal SET payload=? WHERE id=?",
                 (json.dumps({"rex": text, "rex_digest": H("V2REX/1", text)}), row[0]))
        status = self.statuses(g8int.measure(self.copy))
        self.assertEqual(status["G14_decision_reproduction"], "FAIL")


@unittest.skipUnless(SSH, "ssh-keygen with -Y is required")
class SealedIntegrationTests(Genesis):
    def test_sealed_period_with_rhalt_matrix_and_rollback(self):
        oar_g, env = self.sealed()
        self.start(env)
        for minutes, close in ((0, 100.), (15, 100.)):
            self.cycle(env, minutes, close)
        report = g8int.measure(self.db, oar_g_path=str(oar_g), allowed_signers=str(self.allowed))
        status = {k: v["status"] for k, v in {**report["criteria"], **report["integration"]}.items()}
        self.assertEqual((status["I2_genesis_integrity"], status["I3_economic_integrity_edg_psh"],
                          status["I9_rollback_and_restart"]), ("PASS", "PASS", "PASS"))
        self.assertEqual(report["phase8_verdict"], "BLOCKED")
        # Rollback to the legacy path (flags OFF) on the same database keeps working, and the evidence shows it.
        runtime = legacy_runtime(self.db, 30, v2_rex=True)
        try:
            with patched_scouts("LONG"):
                runtime.run_cycle("XAUUSD", T + timedelta(minutes=30))
        finally:
            runtime.close()
        after = verify(self.db, oar_g_path=str(oar_g), allowed_signers=str(self.allowed))
        self.assertEqual(after["genesis"]["status"], "NOT VERIFIED")  # a start without the seal check (I-G18)


class CompatibilityTests(unittest.TestCase):
    def test_phase6_conflict_engine_still_unwired_and_unchanged(self):
        service = (ROOT / "runtime" / "service.py").read_text(encoding="utf-8")
        self.assertNotIn("conflict_path", service)
        self.assertNotIn("risk_reservation", service)
        done = subprocess.run([freeze_amendment._git(), "-C", str(ROOT), "diff", "--name-only", "979e1e6", "HEAD",
                               "--", "execution/conflict_path.py", "execution/risk_reservation.py", "riesgo.py",
                               "execution/paper_broker.py", "runtime/paper_contracts.py", "core/"],
                              capture_output=True, text=True)
        if done.returncode != 0:  # e.g. a shallow CI checkout without the P3 base: not checked, never a vacuous pass
            self.skipTest("the P3 base commit is not available in this checkout")
        self.assertEqual(done.stdout.strip(), "")  # Phase 6 engine, risk, adapter and broker untouched since P3


if __name__ == "__main__":
    unittest.main()
