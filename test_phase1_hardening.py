"""V2 Phase 1 final hardening: liveness invariant, timeout classification, NOT_CONFIGURED state.

Focused regression coverage for the three authorized observability fixes. Health remains
observational: provider results/exceptions are compared with the health sink absent.
"""
from datetime import datetime, timedelta, timezone
import io
import json
import tempfile
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
import unittest
from unittest import mock

from ai.openai_provider import OpenAIProvider, OpenAIProviderError
from runtime.health_observers import observe_agent_response, observe_ai_provider
from runtime.system_health import (
    NOT_CONFIGURED, HealthPolicy, HealthStatus, ProviderErrorType, SystemHealth, classify_provider_error,
)
from storage.health_store import HealthStore
from test_phase1_isolation import IsolationCase, POLICY as ISOLATION_POLICY
from test_real_providers import openai_payload, request as ai_request

T = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
POLICY = HealthPolicy(failed_after_consecutive_errors=3, heartbeat_stale_after_seconds=60,
                      progress_stale_after_seconds=1800)  # Test thresholds only.
URL = "https://api.openai.com/v1/responses"


def seconds(value):
    return T + timedelta(seconds=value)


class HealthCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-phase1-hardening-")
        self.addCleanup(self.tmp.cleanup)
        self.store = HealthStore(Path(self.tmp.name) / "system_health.db")
        self.addCleanup(self.store.close)
        self.health = SystemHealth(self.store, POLICY)


class LivenessInvariantTests(HealthCase):
    """Fix #1: an explicitly NOT_ALIVE heartbeat never projects HEALTHY (LIVENESS != PROGRESS)."""

    def build(self, name, *, success=None, heartbeat=None, progress=None):
        if success is not None:
            self.health.record_success(name, at=seconds(success), observation_id=f"{name}-s")
        if heartbeat is not None:
            self.health.record_heartbeat(name, at=seconds(heartbeat), observation_id=f"{name}-h")
        if progress is not None:
            self.health.record_progress(name, at=seconds(progress), observation_id=f"{name}-p", stage="run_completed")
        return self.health.get(name)

    def view(self, record, now):
        projected = self.health.project(record, now=seconds(now))
        return projected["liveness"], projected["progress"], projected["status"]

    def test_a_alive_and_current_progress_may_be_healthy(self):
        record = self.build("a", success=3600, heartbeat=3600, progress=3600)
        self.assertEqual(self.view(record, 3610), ("ALIVE", "ADVANCING", "HEALTHY"))

    def test_b_alive_with_stale_progress_is_not_healthy(self):
        record = self.build("b", success=3600, heartbeat=3600, progress=0)
        self.assertEqual(self.view(record, 3610), ("ALIVE", "STALLED", "STALE"))

    def test_c_not_alive_with_current_progress_is_not_healthy(self):
        record = self.build("c", success=3600, heartbeat=3300, progress=3600)
        liveness, progress, status = self.view(record, 3610)
        self.assertEqual((liveness, progress), ("NOT_ALIVE", "ADVANCING"))
        self.assertNotEqual(status, "HEALTHY")
        self.assertEqual(status, "STALE")

    def test_d_not_alive_with_stale_progress_is_not_healthy(self):
        record = self.build("d", success=3600, heartbeat=3300, progress=0)
        self.assertEqual(self.view(record, 3610), ("NOT_ALIVE", "STALLED", "STALE"))

    def test_e_unknown_heartbeat_is_reported_truthfully_and_never_manufactures_healthy(self):
        never_seen = self.health.get("never-seen")
        self.assertEqual(self.view(never_seen, 0), ("UNKNOWN", "UNKNOWN", "UNKNOWN"))
        progress_only = self.build("e-progress", progress=3600)
        self.assertEqual(self.view(progress_only, 3610), ("UNKNOWN", "ADVANCING", "UNKNOWN"))
        # Without a heartbeat the projection keeps the status the recorded evidence supports.
        evidenced = self.build("e-success", success=3600, progress=3600)
        self.assertEqual(self.view(evidenced, 3610), ("UNKNOWN", "ADVANCING", evidenced.status))

    def test_future_heartbeat_is_not_alive_and_not_healthy(self):
        record = self.build("future", success=3600, heartbeat=3700, progress=3600)
        self.assertEqual(self.view(record, 3610), ("NOT_ALIVE", "ADVANCING", "STALE"))

    def test_not_alive_never_upgrades_or_hides_error_states(self):
        for errors, expected in ((1, "DEGRADED"), (3, "FAILED")):
            name = f"err{errors}"
            for index in range(errors):
                self.health.record_error(name, at=seconds(3600 + index), observation_id=f"{name}-{index}",
                                         kind="RATE_LIMITED")
            self.health.record_heartbeat(name, at=seconds(3000), observation_id=f"{name}-h")
            with self.subTest(expected=expected):
                self.assertEqual(self.view(self.health.get(name), 3610)[::2], ("NOT_ALIVE", expected))

    def test_projection_writes_nothing(self):
        record = self.build("pure", success=3600, heartbeat=3300, progress=3600)
        rows = self.store.db.execute("SELECT count(*) FROM health_observations").fetchone()[0]
        self.view(record, 3610)
        self.assertEqual(self.store.db.execute("SELECT count(*) FROM health_observations").fetchone()[0], rows)
        self.assertEqual(self.health.get("pure"), record)


class TimeoutClassifierTests(unittest.TestCase):
    """Fix #2 at the classifier: timeout evidence refines only the generic CONNECTION_ERROR."""

    def test_connection_error_with_timeout_evidence_is_timeout(self):
        timed_out = OpenAIProviderError("CONNECTION_ERROR", timed_out=True)
        self.assertEqual(classify_provider_error(kind="CONNECTION_ERROR", exception=timed_out),
                         (ProviderErrorType.TIMEOUT, "CONNECTION_ERROR"))
        self.assertEqual(classify_provider_error(kind="CONNECTION_ERROR", exception=TimeoutError()),
                         (ProviderErrorType.TIMEOUT, "CONNECTION_ERROR"))
        self.assertEqual(classify_provider_error(exception=timed_out)[0], ProviderErrorType.TIMEOUT)

    def test_genuine_connection_failure_stays_connection_error(self):
        for exception in (None, OpenAIProviderError("CONNECTION_ERROR"), ConnectionResetError("reset")):
            with self.subTest(exception=type(exception).__name__):
                self.assertEqual(classify_provider_error(kind="CONNECTION_ERROR", exception=exception),
                                 (ProviderErrorType.CONNECTION_ERROR, None))

    def test_specific_classes_are_not_overridden_by_timeout_evidence(self):
        for kind, expected in (("RATE_LIMITED", "RATE_LIMITED"), ("QUOTA_EXHAUSTED", "BILLING_OR_QUOTA"),
                               ("MODEL_UNAVAILABLE", "MODEL_UNAVAILABLE"), ("AUTH_ERROR", "AUTH_FAILURE")):
            with self.subTest(kind=kind):
                self.assertEqual(classify_provider_error(kind=kind, exception=TimeoutError())[0].value, expected)

    def test_hostile_exception_never_raises(self):
        class Hostile(Exception):
            def __getattribute__(self, name):
                if name == "timed_out":
                    raise RuntimeError("boom")
                return super().__getattribute__(name)
        self.assertEqual(classify_provider_error(kind="CONNECTION_ERROR", exception=Hostile()),
                         (ProviderErrorType.CONNECTION_ERROR, None))


class ProviderTimeoutEndToEndTests(IsolationCase):
    """Fix #2 through the real OpenAIProvider transport path; provider behavior is unchanged."""

    MODES = ("sink_raises", "write_raises", "corrupt", "unavailable", "healthy")

    @staticmethod
    def provider(transport):
        return OpenAIProvider(api_key="dummy", retries=1, transport=transport, sleep=lambda _: None)

    @staticmethod
    def failing(factory):
        attempts = []

        def transport(*_):
            attempts.append(1)
            raise factory()
        return transport, attempts

    def outcome(self, factory):
        transport, attempts = self.failing(factory)
        provider = self.provider(transport)
        with self.assertRaises(OpenAIProviderError) as caught:
            provider.generate(ai_request())
        error = caught.exception
        return (type(error), error.kind, error.http_status, str(error), len(attempts), provider.last_failure), error

    CASES = {
        # name: (factory, provider kind (unchanged), health error_type, health legacy name, timed_out flag)
        "F_read_timeout": (lambda: TimeoutError("timed out"), "CONNECTION_ERROR", "TIMEOUT", "CONNECTION_ERROR", True),
        "F_connect_timeout": (lambda: URLError(TimeoutError("timed out")), "CONNECTION_ERROR", "TIMEOUT",
                              "CONNECTION_ERROR", True),
        "G_connection_reset": (lambda: ConnectionError("reset"), "CONNECTION_ERROR", "CONNECTION_ERROR", None, False),
        "G_connection_refused": (lambda: URLError(ConnectionRefusedError("refused")), "CONNECTION_ERROR",
                                 "CONNECTION_ERROR", None, False),
        "H_rate_limit": (lambda: HTTPError(URL, 429, "x", {}, None), "RATE_LIMITED", "RATE_LIMITED", None, False),
        "I_billing_quota": (lambda: HTTPError(URL, 429, "q", {}, io.BytesIO(
            b'{"error":{"code":"insufficient_quota"}}')), "QUOTA_EXHAUSTED", "BILLING_OR_QUOTA",
                            "QUOTA_EXHAUSTED", False),
        "J_model_unavailable": (lambda: HTTPError(URL, 404, "x", {}, None), "MODEL_UNAVAILABLE",
                                "MODEL_UNAVAILABLE", None, False),
        "K_unknown_provider_failure": (lambda: HTTPError(URL, 418, "x", {}, None), "PROVIDER_ERROR",
                                       "UNKNOWN_PROVIDER_FAILURE", "PROVIDER_ERROR", False),
    }

    def test_f_to_k_provider_failures_classified_and_provider_unchanged(self):
        for name, (factory, kind, error_type, legacy, timed_out) in self.CASES.items():
            with self.subTest(case=name):
                self.install("absent", f"{name}-absent")
                baseline, error = self.outcome(factory)
                self.assertEqual((baseline[1], baseline[5], error.timed_out), (kind, kind, timed_out))
                sidecar = self.install("healthy", f"{name}-healthy")
                observed, _ = self.outcome(factory)
                self.assertEqual(observed, baseline)
                store = HealthStore(sidecar)
                self.addCleanup(store.close)
                record = SystemHealth(store, ISOLATION_POLICY).get("ai_provider", "openai")
                self.assertEqual((record.error_type, record.legacy_error_name, record.status),
                                 (error_type, legacy, "DEGRADED"))

    def test_o_observer_failure_cannot_replace_timeout_exception(self):
        factory = self.CASES["F_read_timeout"][0]
        self.install("absent", "o-absent")
        baseline, error = self.outcome(factory)
        for mode in self.MODES:
            with self.subTest(mode=mode):
                self.install(mode, f"o-{mode}")
                observed, observed_error = self.outcome(factory)
                self.assertEqual((observed, observed_error.timed_out), (baseline, error.timed_out))

    def test_p_timeout_retry_behavior_pinned_and_independent_of_timed_out_and_sink(self):
        # Existing _post policy: a transport timeout is a transient CONNECTION_ERROR, attempted
        # retries+1 times with the configured timeout, sleeping min(4.0, 0.4 * 2**attempt) + jitter.
        def run(factory, succeed_on=None):
            attempts, sleeps = [], []

            def transport(payload, api_key, timeout):
                attempts.append(timeout)
                if len(attempts) == succeed_on:
                    return openai_payload()
                raise factory()
            provider = OpenAIProvider(api_key="dummy", timeout=7, retries=2, transport=transport, sleep=sleeps.append)
            with mock.patch("ai.openai_provider.random.uniform", return_value=0.05):
                try:
                    result = provider.generate(ai_request())
                except OpenAIProviderError as error:
                    result = (type(error), error.kind, error.http_status, error.retry_after, str(error),
                              error.timed_out)
            return result, attempts, [round(delay, 6) for delay in sleeps], provider.last_failure

        exhausted = ([7.0, 7.0, 7.0], [0.45, 0.85], "CONNECTION_ERROR")
        failure = (OpenAIProviderError, "CONNECTION_ERROR", None, None, "CONNECTION_ERROR")
        self.install("absent", "p-absent")
        recovered = self.provider(lambda *_: openai_payload()).generate(ai_request())
        cases = {
            "read_timeout": (lambda: TimeoutError("timed out"), None, (*failure, True), exhausted),
            "connect_timeout": (lambda: URLError(TimeoutError("timed out")), None, (*failure, True), exhausted),
            "connection_reset": (lambda: ConnectionError("reset"), None, (*failure, False), exhausted),
            "timeout_then_success": (lambda: TimeoutError("timed out"), 3, recovered,
                                     ([7.0, 7.0, 7.0], [0.45, 0.85], None)),
        }
        for mode in ("absent", *self.MODES):
            for name, (factory, succeed_on, result, (attempts, sleeps, last_failure)) in cases.items():
                with self.subTest(mode=mode, case=name):
                    self.install(mode, f"p-{mode}-{name}")
                    self.assertEqual(run(factory, succeed_on), (result, attempts, sleeps, last_failure))

    def test_n_observer_failure_cannot_change_successful_result(self):
        self.install("absent", "n-absent")
        expected = self.provider(lambda *_: openai_payload()).generate(ai_request())
        for mode in self.MODES:
            with self.subTest(mode=mode):
                self.install(mode, f"n-{mode}")
                self.assertEqual(self.provider(lambda *_: openai_payload()).generate(ai_request()), expected)


class NotConfiguredTests(HealthCase):
    """Fix #3: NOT_CONFIGURED is a known configuration state, not HEALTHY and not a provider failure."""

    def assert_not_configured(self, record):
        self.assertEqual((record.status, record.sanitized_error_reason, record.error_type, record.legacy_error_name,
                          record.consecutive_errors), ("UNKNOWN", NOT_CONFIGURED, None, None, 0))
        self.assertNotEqual(self.health.project(record, now=T)["status"], HealthStatus.HEALTHY.value)

    def test_l_m_not_configured_kind_is_a_state_not_healthy_or_unknown_failure(self):
        record = self.health.record_error("macro_data", provider="fxmacrodata", at=T, observation_id="nc",
                                          kind=NOT_CONFIGURED, reason="no key", details={"data_state": "NO_DATA"})
        self.assert_not_configured(record)
        self.assertEqual(record.details, {"data_state": "NO_DATA"})
        stored = json.loads(self.store.db.execute(
            "SELECT payload FROM health_observations WHERE observation_id='nc'").fetchone()[0])
        self.assertEqual((stored["kind"], stored["status"]), ("STATE", "UNKNOWN"))
        self.assertNotIn("UNKNOWN_PROVIDER_FAILURE", json.dumps(stored))

    def test_not_configured_does_not_count_as_error(self):
        self.health.record_error("ai_provider", provider="openai", at=T, observation_id="e1", kind="RATE_LIMITED")
        record = self.health.record_error("ai_provider", provider="openai", at=seconds(1), observation_id="e2",
                                          kind=NOT_CONFIGURED)
        self.assertEqual((record.status, record.consecutive_errors, record.error_type), ("UNKNOWN", 1, None))

    def test_unknown_provider_failure_preserved_for_real_unclassified_failures(self):
        record = self.health.record_error("ai_provider", provider="openai", at=T, observation_id="u",
                                          kind="SOMETHING_NEW")
        self.assertEqual((record.error_type, record.legacy_error_name, record.status),
                         ("UNKNOWN_PROVIDER_FAILURE", "SOMETHING_NEW", "DEGRADED"))

    def test_component_errors_are_not_intercepted(self):
        record = self.health.record_error("scheduler", at=T, observation_id="c", kind=NOT_CONFIGURED,
                                          component_error="SCHEDULER_ERROR")
        self.assertEqual((record.error_type, record.status), ("SCHEDULER_ERROR", "DEGRADED"))

    def test_unconfigured_provider_observer_is_not_healthy(self):
        unconfigured = OpenAIProvider(api_key="", transport=lambda *_: openai_payload(), sleep=lambda _: None)
        self.assertEqual((unconfigured.health, unconfigured.last_failure), (NOT_CONFIGURED, None))
        self.assertTrue(observe_ai_provider(self.health, unconfigured, at=T, observation_id="o1"))
        self.assert_not_configured(self.health.get("ai_provider", "openai"))
        configured = SimpleNamespace(metadata={"provider": "openai-ok", "model": "m"}, last_failure=None,
                                     last_usage={}, health="READY")
        self.assertTrue(observe_ai_provider(self.health, configured, at=T, observation_id="o2"))
        self.assertEqual(self.health.get("ai_provider", "openai-ok").status, "HEALTHY")

    def test_agent_failing_because_provider_unconfigured_is_not_unknown_failure(self):
        response = SimpleNamespace(agent_name="macro_ai", status="ERROR", warnings=("provider_exception:X",),
                                   model_metadata={"provider": "openai"})
        self.assertTrue(observe_agent_response(self.health, response, at=T, observation_id="a",
                                               provider_kind=NOT_CONFIGURED))
        self.assert_not_configured(self.health.get("macro_ai", "openai"))


class NotConfiguredProviderEndToEndTests(IsolationCase):
    def test_unconfigured_provider_call_recorded_as_not_configured_and_exception_unchanged(self):
        def run():
            provider = OpenAIProvider(api_key="", transport=lambda *_: openai_payload(), sleep=lambda _: None)
            with self.assertRaises(OpenAIProviderError) as caught:
                provider.generate(ai_request())
            return type(caught.exception), caught.exception.kind, provider.last_failure
        self.install("absent", "nc-absent")
        baseline = run()
        self.assertEqual(baseline[1:], (NOT_CONFIGURED, None))
        sidecar = self.install("healthy", "nc-healthy")
        self.assertEqual(run(), baseline)
        store = HealthStore(sidecar)
        self.addCleanup(store.close)
        record = SystemHealth(store, ISOLATION_POLICY).get("ai_provider", "openai")
        self.assertEqual((record.status, record.sanitized_error_reason, record.error_type, record.consecutive_errors),
                         ("UNKNOWN", NOT_CONFIGURED, None, 0))


if __name__ == "__main__":
    unittest.main()
