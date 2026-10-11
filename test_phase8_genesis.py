"""V2 Phase 8 / P2b Sealed Genesis (design P8.6 5.1.3.2; DEC-8.22-i; Owner D-1..D-4). Ephemeral test keys only:
no Owner signature is ever produced. Temporary databases only; every flag OFF by default.

Covers G0-G5, GEN-1..GEN-5, NG37-NG41, NG47-NG49, NG54-NG61, I-G12..I-G14, I-G17..I-G22, R-GEN-1a..e, the preflights
(D-3), R-HALT interplay, inertness with the flag OFF."""
import ast
import contextlib
from datetime import datetime, timedelta, timezone
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
from replay.genesis_anchor import build
from replay.rex_chain import verify
from runtime import genesis
from runtime.config import SEALED_GENESIS_ENV, RuntimeConfig
from runtime.demo_runner import DemoRunner, preflight
from runtime.genesis import GenesisSealError, SealedContext, verify_startup_context
from runtime.genesis_prepare import main as prepare_main, prepare
from runtime.halt import HaltGate, HaltRefused
from runtime.service import OperationalRuntime
from storage.database import Store
from storage.economic_digest import cj, expected_edg_genesis
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from test_phase8_catch_up_scope import LEGACY_FINGERPRINT, env as clean_env

ROOT = Path(__file__).resolve().parent
SSH = shutil.which("ssh-keygen")
X, Y = "a" * 40, "b" * 40
REAL_CONNECT = sqlite3.connect


class Owner:
    """An EPHEMERAL test key standing in for the Owner (never a real signature)."""

    def __init__(self, directory, name="owner"):
        self.dir, self.key = Path(directory), Path(directory) / name
        subprocess.run([SSH, "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(self.key)], check=True)
        algorithm, blob = (self.key.with_suffix(".pub")).read_text().split()[:2]
        self.line = f'{name} namespaces="v2-genesis-anchor,v2-genesis-authorization" {algorithm} {blob}\n'

    def allowed(self, path=None):
        path = Path(path or self.dir / "allowed_signers")
        path.write_text(self.line, encoding="utf-8")
        return path

    def sign(self, path, namespace):
        subprocess.run([SSH, "-Y", "sign", "-f", str(self.key), "-n", namespace, str(path)], check=True,
                       capture_output=True)
        return path


@unittest.skipUnless(SSH, "ssh-keygen with -Y is required (Owner decision D-2)")
class Genesis(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p2b-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "trading_floor.db"
        self.owner = Owner(self.dir)
        self.allowed = self.owner.allowed()
        baseline = patch("runtime.cloud_runner.EXPERIMENT_BASELINE_SHA", X)  # what Y_P2 deploys (the only pin)
        baseline.start()
        self.addCleanup(baseline.stop)

    # -- builders ----------------------------------------------------------------------------------------------------
    def authorization(self, target=None, *, sign_ns="v2-genesis-authorization", hours=1, **overrides):
        body = {"kind": "V2_GENESIS_AUTHORIZATION/1", "period_id": "P2-test",
                "db_realpath": os.path.realpath(target or self.db), "x_p2": X, "y_p2": Y, "starting_equity": 10000,
                "account_id": "paper-main",
                "not_after_utc": (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()}
        body.update(overrides)
        path = self.dir / f"auth-{len(list(self.dir.glob('auth-*.json')))}.json"
        path.write_text(cj(body), encoding="utf-8")
        if sign_ns:
            self.owner.sign(path, sign_ns)
        return path

    def prepared(self):
        summary = prepare(self.authorization(), self.allowed, self.db)
        self.assertTrue(summary["matches_expected"])
        return summary

    def anchor(self, *, edit=None):
        report, body = build(self.db, period_id="P2-test", x_p2=X, y_p2=Y)
        self.assertEqual(report["result"], "READY_TO_SIGN", report)
        if edit is not None:
            data = json.loads(body)
            edit(data)
            body = cj(data)
        path = self.dir / f"oar_g-{len(list(self.dir.glob('oar_g-*.json')))}.json"
        path.write_text(body, encoding="utf-8")
        self.owner.sign(path, "v2-genesis-anchor")
        return path

    def env(self, oar_g):
        return {"AI_FLOOR_GENESIS_ANCHOR": str(oar_g), "AI_FLOOR_GENESIS_ALLOWED_SIGNERS": str(self.allowed),
                "AI_FLOOR_GIT_COMMIT": Y}

    def config(self, **extra):
        return RuntimeConfig(db_path=self.db, enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=True,
                             v2_sealed_genesis=True, **extra)

    def sealed(self):
        self.prepared()
        oar_g = self.anchor()
        return oar_g, self.env(oar_g)

    def runtime(self, context, minutes=0, close=100., gate=None, **extra):
        return OperationalRuntime(self.config(**extra), market_provider=Data(close=close),
                                  ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                                  instruments={"XAUUSD": instrument()}, clock=lambda: T + timedelta(minutes=minutes),
                                  sealed_context=context, halt_gate=gate)

    def start(self, env):
        """G4: the first sealed DemoRunner start (seal check PASS, then E0)."""
        context = verify_startup_context(self.config(), env)
        DemoRunner(self.config(), market_provider=Data(), ai_provider=DeterministicAIProvider(),
                   macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()}, clock=lambda: T,
                   sealed_context=context, experiment_baseline_sha=X, experiment_freeze_sha=Y).close()

    def cycle(self, env, minutes, close=100.):
        runtime = self.runtime(verify_startup_context(self.config(), env), minutes, close)
        try:
            with patched_scouts("LONG"):
                return runtime.run_cycle("XAUUSD", T + timedelta(minutes=minutes))
        finally:
            runtime.close()

    def sql(self, statement, params=()):
        conn = REAL_CONNECT(self.db)
        try:
            out = conn.execute(statement, params).fetchall()
            conn.commit()
            return out
        finally:
            conn.close()

    def seal_rows(self):
        return [json.loads(r[0]) for r in self.sql("SELECT payload FROM journal WHERE event_type='GENESIS_SEAL_CHECK' "
                                                   "ORDER BY id")]

    def report(self, oar_g=None, **kwargs):
        return verify(self.db, oar_g_path=None if oar_g is None else str(oar_g), allowed_signers=str(self.allowed),
                      **kwargs)

    @staticmethod
    def codes(report, severity="INVALID"):
        return {f["code"] for f in report["findings"] if f["severity"] == severity}


class ToolTests(Genesis):
    def test_g0_g1_prepare_creates_exactly_one_paper_account_atomically(self):
        summary = self.prepared()
        self.assertEqual(summary["edg_genesis"], expected_edg_genesis("paper-main", 10000.0)["edg"])
        rows = self.sql("SELECT id, source, event_type FROM journal ORDER BY id")
        self.assertEqual([tuple(r) for r in rows], [(1, "genesis", "GENESIS_PREPARED")])
        self.assertEqual(self.sql("SELECT count(*) FROM runs"), [(0,)])
        self.assertEqual(self.sql("SELECT value FROM system_state WHERE key='experiment_started'"), [])
        account = json.loads(self.sql("SELECT payload FROM paper_accounts")[0][0])
        self.assertEqual((account["starting_equity"], account["equity"], account["cash"]), (10000.0, 10000.0, 10000.0))

    def test_crash_inside_the_genesis_transaction_leaves_nothing_reusable(self):
        real = Store._event

        def boom(store, *args, **kwargs):
            raise sqlite3.OperationalError("disk I/O error")
        with patch.object(Store, "_event", boom), self.assertRaises(sqlite3.OperationalError):
            prepare(self.authorization(), self.allowed, self.db)
        self.assertEqual(self.sql("SELECT count(*) FROM paper_accounts"), [(0,)])  # account and event: all or nothing
        with self.assertRaises(GenesisSealError):
            prepare(self.authorization(), self.allowed, self.db)  # the file exists: never reused (new genesis)
        self.assertIs(real, Store._event)

    def test_ng61_authorization_refusals(self):
        other = Owner(self.dir, "intruder")
        cases = {
            "expired": lambda: self.authorization(hours=-1),
            "unsigned": lambda: self.authorization(sign_ns=None),
            "wrong_namespace": lambda: self.authorization(sign_ns="v2-genesis-anchor"),
            "other_target": lambda: self.authorization(self.dir / "other.db"),
            "equity_9999": lambda: self.authorization(starting_equity=9999),
            "bad_sha": lambda: self.authorization(x_p2="zz"),
        }
        for name, make in cases.items():
            with self.subTest(case=name), self.assertRaises(GenesisSealError):
                prepare(make(), self.allowed, self.db)
            self.assertFalse(self.db.exists())
        foreign = self.authorization(sign_ns=None)
        other.sign(foreign, "v2-genesis-authorization")  # a key outside allowed_signers
        with self.assertRaises(GenesisSealError):
            prepare(foreign, self.allowed, self.db)
        loose = self.dir / "loose.json"
        loose.write_text(json.dumps(json.loads(self.authorization().read_text()), indent=1), encoding="utf-8")
        self.owner.sign(loose, "v2-genesis-authorization")
        with self.assertRaises(GenesisSealError):
            prepare(loose, self.allowed, self.db)  # signed but not canonical
        self.assertFalse(self.db.exists())

    def test_create_exclusive_and_cli(self):
        self.prepared()
        before = self.db.read_bytes()
        with contextlib.redirect_stdout(open(os.devnull, "w")) as sink:
            self.addCleanup(sink.close)
            self.assertEqual(prepare_main(["--authorization", str(self.authorization()), "--allowed-signers",
                                           str(self.allowed), "--db", str(self.db)]), 2)  # exists: refused
        self.assertEqual(self.db.read_bytes(), before)


class AnchorTests(Genesis):
    def test_g2_refuses_anything_but_a_fresh_genesis(self):
        self.prepared()
        report, body = build(self.db, period_id="P2-test", x_p2=X, y_p2=Y)
        self.assertEqual(report["result"], "READY_TO_SIGN")
        self.assertEqual(json.loads(body)["edg_genesis"], expected_edg_genesis("paper-main", 10000.0)["edg"])
        before = self.db.read_bytes()
        for label, statement in (("experiment", "INSERT INTO system_state VALUES('experiment_started','1')"),
                                 ("order", "INSERT INTO paper_orders VALUES('o','{}')"),
                                 ("event", "INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,"
                                           "payload) VALUES('t','r','XAUUSD','x','ORDER_FILLED','INFO','{}')")):
            with self.subTest(case=label):
                self.db.write_bytes(before)
                self.sql(statement)
                report, body = build(self.db, period_id="P2-test", x_p2=X, y_p2=Y)
                self.assertEqual((report["result"], body), ("REFUSED", None))


class StartupContextTests(Genesis):
    def no_sqlite(self):
        return patch("sqlite3.connect", side_effect=AssertionError("SQLite opened before steps 1-4"))

    def test_ng54_ng60_configuration_absent_refuses_before_any_open(self):
        with self.no_sqlite():
            for env in ({}, {"AI_FLOOR_GENESIS_ANCHOR": "x"}, {"AI_FLOOR_GENESIS_ALLOWED_SIGNERS": str(self.allowed)}):
                with self.subTest(env=sorted(env)), self.assertRaises(GenesisSealError):
                    verify_startup_context(self.config(), env)
                with self.assertRaises(GenesisSealError):
                    OperationalRuntime(self.config())  # I-G21: the constructor needs the verified context
        self.assertFalse(self.db.exists())

    def test_ng58_a_start_mode_variable_requesting_preparation_is_rejected(self):
        _, env = self.sealed()
        with self.no_sqlite(), self.assertRaises(GenesisSealError):
            verify_startup_context(self.config(), {**env, "AI_FLOOR_START_MODE": "GENESIS_PREPARATION"})

    def test_ng59_manipulated_configuration(self):
        oar_g, env = self.sealed()
        intruder = Owner(self.dir, "intruder")
        tampered = self.dir / "tampered.json"
        tampered.write_text(oar_g.read_text(encoding="utf-8").replace('"P2-test"', '"P2-tamp"'), encoding="utf-8")
        shutil.copyfile(str(oar_g) + ".sig", str(tampered) + ".sig")  # the old signature over new bytes
        foreign = self.dir / "foreign.json"
        foreign.write_text(oar_g.read_text(encoding="utf-8"), encoding="utf-8")
        intruder.sign(foreign, "v2-genesis-anchor")
        cases = {"tampered_bytes": {**env, "AI_FLOOR_GENESIS_ANCHOR": str(tampered)},
                 "foreign_signer": {**env, "AI_FLOOR_GENESIS_ANCHOR": str(foreign)},
                 "allowed_signers_swapped": {**env, "AI_FLOOR_GENESIS_ALLOWED_SIGNERS":
                                             str(intruder.allowed(self.dir / "intruder_signers"))},
                 "y_p2_mismatch": {**env, "AI_FLOOR_GIT_COMMIT": "c" * 40}}
        with self.no_sqlite():
            for name, bad in cases.items():
                with self.subTest(case=name), self.assertRaises(GenesisSealError):
                    verify_startup_context(self.config(), bad)
            with patch("runtime.cloud_runner.EXPERIMENT_BASELINE_SHA", genesis.P1_BASELINE_SHA), \
                    self.assertRaises(GenesisSealError):
                verify_startup_context(self.config(), env)  # still the P1 baseline: no sealed start before the cut
            with patch("runtime.cloud_runner.EXPERIMENT_BASELINE_SHA", "d" * 40), self.assertRaises(GenesisSealError):
                verify_startup_context(self.config(), env)

    def test_file_identity_path_inode_and_links(self):
        oar_g, env = self.sealed()
        self.assertIsInstance(verify_startup_context(self.config(), env), SealedContext)
        other = self.dir / "elsewhere.db"
        with self.assertRaises(GenesisSealError):
            verify_startup_context(RuntimeConfig(db_path=other, enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=True,
                                                 v2_sealed_genesis=True), env)
        os.link(self.db, self.dir / "second_link.db")
        with self.assertRaises(GenesisSealError):
            verify_startup_context(self.config(), env)  # st_nlink 2
        os.unlink(self.dir / "second_link.db")
        copy = self.dir / "swap.db"
        shutil.copyfile(self.db, copy)
        os.replace(copy, self.db)  # same path and bytes, new inode (NG41 at the runtime side)
        with self.assertRaises(GenesisSealError):
            verify_startup_context(self.config(), env)

    def test_i_g12_anchor_with_a_wrong_genesis_digest_is_refused(self):
        self.prepared()
        oar_g = self.anchor(edit=lambda body: body.update(edg_genesis="0" * 64))
        with self.assertRaises(GenesisSealError):
            verify_startup_context(self.config(), self.env(oar_g))

    def test_r_gen_1e_inventory(self):
        normal = [p for p in (ROOT / "runtime").glob("*.py") if p.name != "genesis_prepare.py"]
        self.assertEqual([p.name for p in normal if "genesis_prepare" in p.read_text(encoding="utf-8")], [])
        creators = []
        for path in [*ROOT.glob("runtime/*.py"), *ROOT.glob("execution/*.py"), *ROOT.glob("storage/*.py")]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(isinstance(n, ast.Call) and getattr(n.func, "id", "") == "PaperAccount" for n in ast.walk(tree)):
                creators.append(path.relative_to(ROOT).as_posix())
        # The legacy creation branch (flag OFF only) and the genesis tool; storage computes the EXPECTED digest only.
        self.assertEqual(sorted(creators), ["runtime/genesis_prepare.py", "runtime/service.py",
                                            "storage/economic_digest.py"])


class ConstructorTests(Genesis):
    def test_g4_g5_sealed_start_then_cycles_and_restarts(self):
        oar_g, env = self.sealed()
        self.start(env)
        statuses = [self.cycle(env, m, c) for m, c in ((0, 100.), (15, 100.), (30, 80.))]
        self.assertEqual(statuses, ["PLAN_READY", "PLAN_READY", "ERROR"])
        seals = self.seal_rows()
        self.assertTrue(all(s["result"] == "PASS" for s in seals))
        self.assertEqual([s["experiment_started"] for s in seals], [False, True, True, True])  # before / after E0
        report = self.report(oar_g, expect_original=True)
        self.assertEqual(report["genesis"]["status"], "ANCHORED (SQLITE CHECKS PASS)")
        self.assertEqual(report["edg_start"], expected_edg_genesis("paper-main", 10000.0)["edg"])  # GEN-1: from OAR-G
        self.assertEqual(self.codes(report), set())
        self.assertEqual(report["classification"], "NOT VERIFIED")  # never VERIFIED without external evidence

    def test_ng55_ng47_account_missing_is_refused_without_creation(self):
        oar_g, env = self.sealed()
        context = verify_startup_context(self.config(), env)
        self.sql("DELETE FROM paper_accounts")
        with patch.object(Store, "save_paper", side_effect=AssertionError("account creation reached")), \
                self.assertRaises(GenesisSealError):
            self.runtime(context)
        self.assertEqual(self.sql("SELECT count(*) FROM paper_accounts"), [(0,)])  # I-G17
        [seal] = self.seal_rows()
        self.assertEqual((seal["result"], seal["reason"]), ("FAIL", "genesis_sealed_account_missing"))
        self.assertIn("SEAL_CHECK_FAILED", self.codes(self.report(oar_g)))

    def test_edg_mismatch_before_e0_and_chain_head_after_e0(self):
        oar_g, env = self.sealed()
        self.sql("UPDATE paper_accounts SET payload=payload||' '")
        with self.assertRaises(GenesisSealError):
            self.runtime(verify_startup_context(self.config(), env))
        self.assertEqual(self.seal_rows()[-1]["reason"], "edg_mismatch")
        self.sql("UPDATE paper_accounts SET payload=rtrim(payload,' ')")
        self.start(env)
        self.cycle(env, 0)
        self.sql("UPDATE paper_orders SET payload=payload||' '")  # a change after E0 without REX
        with self.assertRaises(GenesisSealError):
            self.runtime(verify_startup_context(self.config(), env), 15)
        self.assertEqual(self.seal_rows()[-1]["reason"], "edg_mismatch")

    def test_identity_swap_between_verification_and_construction(self):
        _, env = self.sealed()
        context = verify_startup_context(self.config(), env)
        copy = self.dir / "swap.db"
        shutil.copyfile(self.db, copy)
        os.replace(copy, self.db)
        with self.assertRaises(GenesisSealError):
            self.runtime(context)
        self.assertEqual(self.seal_rows(), [])  # refused right after opening: no recover, no seal row

    def test_ng56_context_of_another_configuration_is_refused(self):
        _, env = self.sealed()
        context = verify_startup_context(self.config(), env)
        with self.assertRaises(GenesisSealError):
            OperationalRuntime(RuntimeConfig(db_path=self.dir / "x.db", enabled_symbols=("XAUUSD", "EURUSD"),
                                             v2_rex=True, v2_sealed_genesis=True), sealed_context=context)
        with self.assertRaises(GenesisSealError):
            OperationalRuntime(self.config(), sealed_context=object())

    def test_halt_before_the_seal_check_writes_nothing_after_t_h(self):
        _, env = self.sealed()
        gate = HaltGate()
        real = Store.recover

        def recover(store, *args, **kwargs):
            result = real(store, *args, **kwargs)
            gate.request(15)
            return result
        with patch.object(Store, "recover", recover), self.assertRaises(HaltRefused):
            self.runtime(verify_startup_context(self.config(), env), gate=gate, v2_rhalt=True)
        self.assertEqual(self.seal_rows(), [])  # STARTUP site refused after T_h
        self.assertIn({"kind": "startup", "stage": "genesis_seal_check"}, gate.refused)


class PreflightTests(Genesis):
    def test_d3_preflights_never_create_the_database(self):
        missing = self.config()
        report = preflight(missing, env={})
        self.assertFalse(report.checks["sealed_genesis_context"])
        self.assertFalse(report.checks["db_writable_schema"])
        self.assertFalse(self.db.exists())
        from runtime.cloud import cloud_preflight
        cloud = cloud_preflight(missing, env={"RENDER": "true"}, disk_mounted=True)
        self.assertFalse(cloud.checks["sealed_genesis_context"])
        self.assertFalse(self.db.exists())

    def test_d3_valid_context_probe_leaves_nothing_persistent(self):
        _, env = self.sealed()
        before = self.sql("SELECT name FROM sqlite_master ORDER BY name")
        report = preflight(self.config(), env=env)
        self.assertTrue(report.checks["sealed_genesis_context"])
        self.assertTrue(report.checks["db_writable_schema"])
        self.assertEqual(self.sql("SELECT name FROM sqlite_master ORDER BY name"), before)  # the probe rolled back


class VerifierTests(Genesis):
    def test_ng39_no_oar_g_and_unverifiable_oar_g(self):
        oar_g, env = self.sealed()
        self.start(env)
        report = self.report()
        self.assertIn("OAR_G_ABSENT", self.codes(report, "NOT_VERIFIED"))
        self.assertEqual(report["genesis"]["status"], "NOT VERIFIED")
        Path(str(oar_g) + ".sig").write_text("not a signature", encoding="utf-8")
        self.assertIn("OAR_G_NOT_VERIFIED", self.codes(self.report(oar_g), "NOT_VERIFIED"))

    def test_ng40_anchor_genesis_not_expected(self):
        self.prepared()
        oar_g = self.anchor(edit=lambda body: body.update(edg_genesis="0" * 64))
        self.assertIn("OAR_G_GENESIS_NOT_EXPECTED", self.codes(self.report(oar_g)))

    def test_ng41_substituted_file(self):
        oar_g, env = self.sealed()
        copy = self.dir / "copy.db"
        shutil.copyfile(self.db, copy)
        os.replace(copy, self.db)
        self.assertIn("DB_FILE_SUBSTITUTED", self.codes(self.report(oar_g, expect_original=True)))

    def test_ng38_economic_event_between_oar_g_and_e0(self):
        oar_g, env = self.sealed()
        self.sql("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                 "VALUES('2026-01-15T13:00:00+00:00','r','XAUUSD','x','ORDER_SUBMITTED','INFO','{}')")
        self.assertIn("ECONOMIC_EVENT_BEFORE_E0", self.codes(self.report(oar_g)))

    def test_ng37_account_recreated_by_a_legacy_restart_after_e0(self):
        oar_g, env = self.sealed()
        self.start(env)
        self.sql("DELETE FROM paper_accounts")
        legacy = RuntimeConfig(db_path=self.db, enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=True)
        OperationalRuntime(legacy, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                           macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                           clock=lambda: T).close()  # the legacy (flag OFF) path recreates the account at 10000
        report = self.report(oar_g)
        self.assertIn("PROCESS_START_WITHOUT_SEAL_CHECK", self.codes(report, "NOT_VERIFIED"))  # NG49 / I-G18
        self.assertEqual(report["genesis"]["status"], "NOT VERIFIED")

    def test_ng48_identical_restoration_is_not_detected(self):
        oar_g, env = self.sealed()
        row = self.sql("SELECT account_id, payload FROM paper_accounts")[0]
        self.sql("DELETE FROM paper_accounts")
        self.sql("INSERT INTO paper_accounts VALUES(?,?)", tuple(row))
        self.start(env)
        report = self.report(oar_g)
        self.assertEqual(self.codes(report), set())  # stated limit: never reported as detection

    def test_seal_check_bound_to_another_anchor(self):
        oar_g, env = self.sealed()
        self.start(env)
        payload = self.sql("SELECT id, payload FROM journal WHERE event_type='GENESIS_SEAL_CHECK'")[0]
        body = json.loads(payload[1])
        body["oar_g_sha256"] = "0" * 64
        self.sql("UPDATE journal SET payload=? WHERE id=?", (json.dumps(body), payload[0]))
        self.assertIn("SEAL_CHECK_OTHER_ANCHOR", self.codes(self.report(oar_g)))


class FlagTests(unittest.TestCase):
    def test_off_by_default_requires_rex_and_keeps_the_fingerprint(self):
        with clean_env():
            config = RuntimeConfig.from_env()
        self.assertFalse(config.v2_sealed_genesis)
        self.assertEqual(config.fingerprint(), LEGACY_FINGERPRINT)
        with self.assertRaises(ValueError):
            RuntimeConfig(v2_sealed_genesis=True)
        for bad in ("true", "2"):
            with self.subTest(value=bad), clean_env(**{SEALED_GENESIS_ENV: bad}), self.assertRaises(ValueError):
                RuntimeConfig.from_env()

    def test_ng57_a_fresh_flagless_start_keeps_the_legacy_bootstrap(self):
        with tempfile.TemporaryDirectory(prefix="v2-p2b-off-", ignore_cleanup_errors=True) as tmp:
            db = Path(tmp) / "db.sqlite"
            OperationalRuntime(RuntimeConfig(db_path=db, enabled_symbols=("XAUUSD", "EURUSD")), market_provider=Data(),
                               ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                               instruments={"XAUUSD": instrument()}, clock=lambda: T).close()
            conn = REAL_CONNECT(db)
            try:
                self.assertEqual(conn.execute("SELECT count(*) FROM journal WHERE event_type='GENESIS_PREPARED' OR "
                                              "event_type='GENESIS_SEAL_CHECK'").fetchone(), (0,))
                self.assertEqual(conn.execute("SELECT count(*) FROM paper_accounts").fetchone(), (1,))  # unchanged V1
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
