"""SYSTEM HEALTH V2 core (Phase 1 / Batch 1): F01-T11 sidecar foundation, F01-T15..T19."""
import ast
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from runtime import system_health as module
from runtime.system_health import (
    ComponentHealth, HealthPolicy, HealthStatus, ProviderErrorType, SystemHealth, classify_provider_error,
    sanitize_reason,
)
from storage.database import SCHEMA_VERSION, Store
from storage.health_store import HEALTH_APPLICATION_ID, HEALTH_SCHEMA_VERSION, HealthStore, HealthStoreError

ROOT = Path(__file__).resolve().parent
FROZEN_V1_BASELINE = "25726a1f11af8a95d0becdec695cdd267c505438"
# sha256 of storage/database.py (LF-normalized) at the frozen V1 baseline 25726a1 (and at main 2075e7f).
FROZEN_TRADING_DB_MODULE_SHA256 = "80f8c9ae037bb27dbcd45403ea33655b07dfed3983f3cb6637d8704e5ef4ebde"

T = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
POLICY = HealthPolicy(failed_after_consecutive_errors=3, heartbeat_stale_after_seconds=60,
                      progress_stale_after_seconds=1800)


def at(minutes):
    return T + timedelta(minutes=minutes)


class HealthCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-health-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "system_health.db"
        self.store = HealthStore(self.path)
        self.addCleanup(lambda: self.store.close())
        self.health = SystemHealth(self.store, POLICY)

    def reopen(self):
        self.store.close()
        self.store = HealthStore(self.path)
        self.health = SystemHealth(self.store, POLICY)

    def count(self, table):
        return self.store.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


class RecordTests(HealthCase):
    def test_t15_last_success_and_last_error_persist(self):
        self.health.record_success("market_data", provider="twelve_data", at=at(0), observation_id="o1")
        self.health.record_error("market_data", provider="twelve_data", at=at(15), observation_id="o2",
                                 kind="RATE_LIMITED", reason="429 from upstream")
        self.reopen()
        record = self.health.get("market_data", "twelve_data")
        self.assertEqual((record.last_success_at, record.last_error_at, record.last_checked_at),
                         (at(0).isoformat(), at(15).isoformat(), at(15).isoformat()))
        self.assertEqual((record.status, record.error_type), ("DEGRADED", "RATE_LIMITED"))

    def test_t16_consecutive_errors_increment_fail_and_recovery_resets(self):
        for minute in range(3):
            record = self.health.record_error("ai", provider="openai", at=at(minute), observation_id=f"e{minute}",
                                              kind="PROVIDER_FAILURE", http_status=503)
            self.assertEqual(record.consecutive_errors, minute + 1)
        self.assertEqual((record.status, record.error_type), ("FAILED", "PROVIDER_5XX"))
        recovered = self.health.record_success("ai", provider="openai", at=at(5), observation_id="ok",
                                               latency_ms=812.5)
        self.assertEqual((recovered.status, recovered.consecutive_errors, recovered.error_type,
                          recovered.sanitized_error_reason, recovered.last_error_at),
                         ("HEALTHY", 0, None, None, at(2).isoformat()))
        self.reopen()
        self.assertEqual(self.health.get("ai", "openai").latency_ms, 812.5)

    def test_t16_latency_validation(self):
        for bad in (-1, float("nan"), float("inf"), True, "10"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.health.record_success("ai", at=at(0), observation_id=f"x{bad!r}", latency_ms=bad)
        self.assertEqual(self.count("health_observations"), 0)

    def test_t17_heartbeat_persists_and_never_changes_success_facts(self):
        self.health.record_heartbeat("runner", at=at(0), observation_id="hb1")
        self.reopen()
        record = self.health.get("runner")
        self.assertEqual((record.heartbeat_at, record.status, record.last_success_at, record.consecutive_errors),
                         (at(0).isoformat(), "UNKNOWN", None, 0))

    def test_t17_liveness_is_separate_from_progress(self):
        self.health.record_progress("pipeline", at=at(0), observation_id="p1", stage="cycle_completed",
                                    reference="XAUUSD:2026-10-05T13:00")
        self.health.record_success("pipeline", at=at(0), observation_id="s1")
        for minute in range(0, 61, 5):
            self.health.record_heartbeat("pipeline", at=at(minute), observation_id=f"hb{minute}")
        stalled = self.health.project(self.health.get("pipeline"), now=at(61))
        self.assertEqual((stalled["liveness"], stalled["progress"], stalled["status"]),
                         ("ALIVE", "STALLED", "STALE"))  # Alive heartbeat, no progress: never HEALTHY.
        self.health.record_heartbeat("pipeline-b", at=at(9), observation_id="b-hb")
        self.health.record_progress("pipeline-b", at=at(5), observation_id="b-p", stage="cycle_completed")
        self.health.record_success("pipeline-b", at=at(5), observation_id="b-s")
        advancing = self.health.project(self.health.get("pipeline-b"), now=at(10))
        self.assertEqual((advancing["liveness"], advancing["progress"], advancing["status"]),
                         ("ALIVE", "ADVANCING", "HEALTHY"))
        only_heartbeat = self.health.project(self.health.get("runner"), now=at(0))
        self.assertEqual(only_heartbeat["status"], "UNKNOWN")
        record = self.health.get("pipeline")
        self.assertEqual((record.progress_stage, record.progress_ref),
                         ("cycle_completed", "XAUUSD:2026-10-05T13:00"))

    def test_t17_dead_heartbeat_and_unknown(self):
        self.health.record_heartbeat("runner", at=at(0), observation_id="hb")
        self.assertEqual(self.health.project(self.health.get("runner"), now=at(2))["liveness"], "NOT_ALIVE")
        self.assertEqual(self.health.project(self.health.get("never-seen"), now=at(0))["liveness"], "UNKNOWN")

    def test_t18_provider_error_records_affected_component_and_provider(self):
        self.health.record_error("macro", provider="fxmacrodata", at=at(0), observation_id="m1",
                                 kind="AUTH_ERROR", http_status=401)
        self.health.record_success("market_data", provider="twelve_data", at=at(0), observation_id="d1")
        self.reopen()
        macro = self.health.get("macro", "fxmacrodata")
        self.assertEqual((macro.component, macro.provider, macro.error_type, macro.legacy_error_name),
                         ("macro", "fxmacrodata", "AUTH_FAILURE", "AUTH_ERROR"))
        self.assertEqual(self.health.get("market_data", "twelve_data").status, "HEALTHY")
        self.assertEqual([(r.component, r.provider) for r in self.health.records()],
                         [("macro", "fxmacrodata"), ("market_data", "twelve_data")])

    def test_stale_projection_after_quiet_period(self):
        self.health.record_success("market_data", at=at(0), observation_id="s")
        self.assertEqual(self.health.project(self.health.get("market_data"), now=at(31))["status"], "STALE")
        self.assertEqual(self.health.project(self.health.get("market_data"), now=at(30))["status"], "HEALTHY")

    def test_idempotent_retry_and_conflicting_reuse(self):
        for _ in range(3):
            record = self.health.record_error("ai", at=at(0), observation_id="same", kind="TIMEOUT")
        self.assertEqual((record.consecutive_errors, self.count("health_observations")), (1, 1))
        self.reopen()
        self.assertEqual(self.health.record_error("ai", at=at(0), observation_id="same",
                                                  kind="TIMEOUT").consecutive_errors, 1)
        with self.assertRaisesRegex(ValueError, "reused"):
            self.health.record_error("ai", at=at(0), observation_id="same", kind="RATE_LIMITED")
        self.assertEqual(self.health.get("ai").error_type, "TIMEOUT")

    def test_out_of_order_observation_never_regresses(self):
        self.health.record_error("ai", at=at(10), observation_id="new", kind="TIMEOUT")
        record = self.health.record_success("ai", at=at(5), observation_id="old")
        self.assertEqual((record.status, record.consecutive_errors, record.last_success_at),
                         ("DEGRADED", 1, None))
        self.assertEqual(self.count("health_observations"), 2)  # Logged, not applied.

    def test_explicit_time_required(self):
        for bad in (None, datetime(2026, 10, 5, 13, 0)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.health.record_success("ai", at=bad, observation_id="t")
        with self.assertRaises(ValueError):
            HealthPolicy(failed_after_consecutive_errors=0, heartbeat_stale_after_seconds=60,
                         progress_stale_after_seconds=60)


class TaxonomyTests(unittest.TestCase):
    def test_t19_v2_types_pass_through(self):
        for kind in ProviderErrorType:
            self.assertEqual(classify_provider_error(kind=kind.value), (kind, None))

    def test_t19_legacy_names_normalized_and_preserved(self):
        expected = {"QUOTA_EXHAUSTED": "BILLING_OR_QUOTA", "AUTH_ERROR": "AUTH_FAILURE",
                    "ACCESS_DENIED": "AUTH_FAILURE", "NOT_ENTITLED": "UNKNOWN_PROVIDER_FAILURE",
                    "PROVIDER_FAILURE": "UNKNOWN_PROVIDER_FAILURE", "PROVIDER_ERROR": "UNKNOWN_PROVIDER_FAILURE"}
        for legacy, v2 in expected.items():
            with self.subTest(legacy=legacy):
                self.assertEqual(classify_provider_error(kind=legacy), (ProviderErrorType(v2), legacy))

    def test_t19_model_unavailable_and_connection_error_are_canonical(self):
        for name in ("MODEL_UNAVAILABLE", "CONNECTION_ERROR"):
            with self.subTest(name=name):
                self.assertEqual(classify_provider_error(kind=name), (ProviderErrorType(name), None))
                self.assertEqual(classify_provider_error(kind=name, http_status=503)[0], ProviderErrorType(name))
                self.assertNotEqual(classify_provider_error(kind=name)[0],
                                    ProviderErrorType.UNKNOWN_PROVIDER_FAILURE)

    def test_t19_original_provider_error_preserved_as_evidence(self):
        with tempfile.TemporaryDirectory(prefix="v2-health-ev-") as folder:
            store = HealthStore(Path(folder) / "system_health.db")
            try:
                health = SystemHealth(store, POLICY)
                for index, name in enumerate(("MODEL_UNAVAILABLE", "CONNECTION_ERROR", "QUOTA_EXHAUSTED")):
                    record = health.record_error("ai", provider="openai", at=at(index), observation_id=f"e{index}",
                                                 kind=name, http_status=404 if index == 0 else None)
                    original = record.legacy_error_name or record.error_type
                    self.assertEqual(original, name)
                stored = store.db.execute("SELECT payload FROM health_observations ORDER BY observed_at").fetchall()
                self.assertEqual([json.loads(r[0])["error_type"] for r in stored],
                                 ["MODEL_UNAVAILABLE", "CONNECTION_ERROR", "BILLING_OR_QUOTA"])
            finally:
                store.close()

    def test_t19_status_and_exception_classification(self):
        cases = {429: "RATE_LIMITED", 402: "BILLING_OR_QUOTA", 401: "AUTH_FAILURE", 403: "AUTH_FAILURE",
                 408: "TIMEOUT", 504: "TIMEOUT", 500: "PROVIDER_5XX", 503: "PROVIDER_5XX", 404: "UNKNOWN_PROVIDER_FAILURE"}
        for status, v2 in cases.items():
            with self.subTest(status=status):
                self.assertEqual(classify_provider_error(kind="PROVIDER_FAILURE", http_status=status)[0].value, v2)
        self.assertEqual(classify_provider_error(exception=TimeoutError())[0], ProviderErrorType.TIMEOUT)
        self.assertEqual(classify_provider_error(kind="AUTH_ERROR", http_status=503)[0],
                         ProviderErrorType.AUTH_FAILURE)  # A specific name wins over status.

    def test_t19_unknown_and_malformed_errors(self):
        for kind in (None, "SOMETHING_NEW", "lower-case", 42, "x" * 300):
            with self.subTest(kind=kind):
                self.assertEqual(classify_provider_error(kind=kind)[0], ProviderErrorType.UNKNOWN_PROVIDER_FAILURE)
        self.assertEqual(classify_provider_error(kind="SOMETHING_NEW"),
                         (ProviderErrorType.UNKNOWN_PROVIDER_FAILURE, "SOMETHING_NEW"))
        self.assertIsNone(classify_provider_error(kind="lower-case")[1])


class SecretSafetyTests(HealthCase):
    SECRETS = ("sk-proj-abcdef1234567890SECRET", "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig",
               "Basic dXNlcjpwYXNzd29yZA==", "apikey=AbC123SecretValue", "token: 9f8e7d6c5b4a",
               "https://api.example.com/v1?apikey=AbC123SecretValue",
               "-----BEGIN RSA PRIVATE KEY-----MIIEsecretbody-----END RSA PRIVATE KEY-----",
               "a" * 40)

    def test_no_secret_or_raw_sensitive_text_is_persisted(self):
        for index, secret in enumerate(self.SECRETS):
            self.health.record_error("ai", provider="openai", at=at(index), observation_id=f"s{index}",
                                     kind="AUTH_ERROR", reason=f"upstream said {secret}\nmore")
        rows = self.store.db.execute("SELECT payload FROM component_health UNION ALL "
                                     "SELECT payload FROM health_observations").fetchall()
        stored = " ".join(r[0] for r in rows)
        for secret in self.SECRETS:
            self.assertNotIn(secret, stored)
        self.assertNotIn("AbC123SecretValue", stored)
        self.assertNotIn("9f8e7d6c5b4a", stored)
        self.assertNotIn("MIIEsecretbody", stored)
        self.assertIn("[REDACTED]", self.health.get("ai", "openai").sanitized_error_reason)

    def test_reason_is_bounded_single_line(self):
        reason = sanitize_reason("line1\nline2\t" + "word " * 100)
        self.assertNotIn("\n", reason)
        self.assertLessEqual(len(reason), module.MAX_REASON_LENGTH)
        self.assertIsNone(sanitize_reason(None))

    def test_names_restricted(self):
        for bad in ("", "has space", "x" * 65, "sk-key/with/slash"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.health.record_success(bad, at=at(0), observation_id="n")


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class SidecarTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-health-sidecar-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.sidecar = self.dir / "system_health.db"
        self.trading = self.dir / "trading_floor.db"

    def make_trading_db(self):
        store = Store(self.trading)
        store.set_state("retained_v1_evidence", "unchanged")
        store.close()
        return file_digest(self.trading)

    def test_sidecar_creation_schema_and_version(self):
        store = HealthStore(self.sidecar)
        self.addCleanup(store.close)
        tables = {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, {"health_schema_info", "component_health", "health_observations"})
        self.assertEqual(store.db.execute("SELECT version FROM health_schema_info").fetchall(),
                         [(HEALTH_SCHEMA_VERSION,)])
        self.assertEqual(store.db.execute("PRAGMA application_id").fetchone()[0], HEALTH_APPLICATION_ID)

    def test_persistence_survives_reopen_and_read_only_open(self):
        store = HealthStore(self.sidecar)
        SystemHealth(store, POLICY).record_error("ai", provider="openai", at=T, observation_id="e1",
                                                 kind="CONNECTION_ERROR")
        store.close()
        reopened = HealthStore(self.sidecar, readonly=True)
        self.addCleanup(reopened.close)
        record = SystemHealth(reopened, POLICY).get("ai", "openai")
        self.assertEqual((record.error_type, record.consecutive_errors), ("CONNECTION_ERROR", 1))
        with self.assertRaises(HealthStoreError):
            SystemHealth(reopened, POLICY).record_success("ai", at=at(1), observation_id="s")

    def test_trading_db_and_foreign_files_are_refused_and_never_written(self):
        digest = self.make_trading_db()
        for readonly in (False, True):
            with self.subTest(readonly=readonly), self.assertRaisesRegex(HealthStoreError, "not a SYSTEM HEALTH"):
                HealthStore(self.trading, readonly=readonly)
        self.assertEqual(file_digest(self.trading), digest)
        trading = Store(self.trading)
        self.addCleanup(trading.close)
        with self.assertRaisesRegex(ValueError, "sidecar"):
            SystemHealth(trading, POLICY)

    def test_incompatible_and_corrupt_sidecars_fail_safely(self):
        HealthStore(self.sidecar).close()
        db = sqlite3.connect(self.sidecar)
        db.execute("UPDATE health_schema_info SET version=99")
        db.commit()
        db.close()
        with self.assertRaisesRegex(HealthStoreError, "incompatible"):
            HealthStore(self.sidecar)
        garbage = self.dir / "garbage.db"
        garbage.write_bytes(b"this is not a sqlite database at all" * 200)
        before = file_digest(garbage)
        with self.assertRaisesRegex(HealthStoreError, "unreadable"):
            HealthStore(garbage)
        self.assertEqual(file_digest(garbage), before)  # Never repaired or recreated.
        with self.assertRaisesRegex(HealthStoreError, "missing"):
            HealthStore(self.dir / "absent.db", readonly=True)

    def test_corrupt_record_payload_fails_safely(self):
        store = HealthStore(self.sidecar)
        self.addCleanup(store.close)
        health = SystemHealth(store, POLICY)
        health.record_success("ai", at=T, observation_id="s1")
        store.db.execute("UPDATE component_health SET payload=?", ('{"status":"ONLINE"}',))
        with self.assertRaisesRegex(HealthStoreError, "corrupt"):
            health.get("ai")
        with self.assertRaisesRegex(HealthStoreError, "corrupt"):
            health.record_success("ai", at=at(1), observation_id="s2")
        self.assertEqual(store.db.execute("SELECT count(*) FROM health_observations").fetchone()[0], 1)

    def test_sidecar_use_and_failure_never_mutate_trading_db(self):
        digest = self.make_trading_db()
        store = HealthStore(self.sidecar)
        health = SystemHealth(store, POLICY)
        for index in range(5):
            health.record_error("market_data", provider="twelve_data", at=at(index), observation_id=f"e{index}",
                                kind="RATE_LIMITED")
        health.record_heartbeat("runner", at=at(5), observation_id="hb")
        store.db.execute("UPDATE component_health SET payload='broken'")
        with self.assertRaises(HealthStoreError):
            health.get("market_data", "twelve_data")
        store.close()
        self.sidecar.write_bytes(b"corrupted")
        with self.assertRaises(HealthStoreError):
            HealthStore(self.sidecar)
        self.assertEqual(file_digest(self.trading), digest)
        trading = Store(self.trading, readonly=True)
        self.addCleanup(trading.close)
        self.assertEqual(trading.db.execute("SELECT version FROM schema_info").fetchone()[0], 3)
        self.assertEqual(trading.get_state("retained_v1_evidence"), "unchanged")
        tables = {r[0] for r in trading.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(tables & {"component_health", "health_observations", "health_schema_info"})


class TradingDatabaseCompatibilityTests(unittest.TestCase):
    def test_trading_db_module_is_byte_identical_to_frozen_v1_baseline(self):
        source = (ROOT / "storage" / "database.py").read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(hashlib.sha256(source).hexdigest(), FROZEN_TRADING_DB_MODULE_SHA256)
        self.assertEqual(SCHEMA_VERSION, 3)

    def test_frozen_v1_baseline_code_opens_trading_db_after_sidecar_use(self):
        git = shutil.which("git")
        if git is None:
            self.skipTest("git unavailable; covered by the byte-identity test")
        with tempfile.TemporaryDirectory(prefix="v2-health-v1-") as folder:
            folder = Path(folder)
            archive = subprocess.run([git, "archive", "--format=tar", FROZEN_V1_BASELINE, "storage", "execution"],
                                     cwd=ROOT, capture_output=True, timeout=60)
            if archive.returncode != 0:
                self.skipTest("frozen V1 baseline commit not available in this checkout")
            frozen = folder / "frozen"
            frozen.mkdir()
            import io
            import tarfile
            with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
                tar.extractall(frozen, filter="data")
            trading = folder / "trading_floor.db"
            store = Store(trading)
            store.set_state("retained_v1_evidence", "unchanged")
            store.close()
            health_store = HealthStore(folder / "system_health.db")
            SystemHealth(health_store, POLICY).record_error("ai", at=T, observation_id="e", kind="MODEL_UNAVAILABLE")
            health_store.close()
            code = ("import sys; from storage.database import Store, SCHEMA_VERSION\n"
                    "for ro in (True, False):\n"
                    "    s = Store(sys.argv[1], readonly=ro)\n"
                    "    assert s.get_state('retained_v1_evidence') == 'unchanged'\n"
                    "    print(SCHEMA_VERSION, s.db.execute('SELECT version FROM schema_info').fetchone()[0])\n"
                    "    s.close()")
            result = subprocess.run([sys.executable, "-B", "-c", code, str(trading)], cwd=frozen,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.split(), ["3", "3", "3", "3"])


class IsolationTests(unittest.TestCase):
    def test_health_core_is_observational_only(self):
        tree = ast.parse(Path(module.__file__).read_bytes())
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
            a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertFalse({m for m in imports if m.split(".")[0] in {
            "execution", "riesgo", "agents", "ai", "floor", "data", "estrategia", "operaciones", "urllib",
            "requests", "socket", "os", "time", "random", "uuid"}})
        calls = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree) if isinstance(n, ast.Call)}
        self.assertFalse(calls & {"submit_plan", "process_next_bar", "process_bar", "evaluar_trade_plan",
                                  "crear_trade_plan", "now", "utcnow", "time", "uuid4", "save_paper"})
        self.assertEqual([s.value for s in HealthStatus], ["HEALTHY", "DEGRADED", "FAILED", "STALE", "UNKNOWN"])

    def test_record_round_trip(self):
        record = module.empty_health("ai", "openai")
        self.assertEqual(ComponentHealth.from_json(record.to_json()), record)
        with self.assertRaises(HealthStoreError):
            ComponentHealth.from_json(record.to_json().replace("UNKNOWN", "ONLINE"))


if __name__ == "__main__":
    unittest.main()
