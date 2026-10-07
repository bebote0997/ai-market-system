"""V2 Phase 7 / P7.1 Batch C: provider health, per-cycle short-circuit, AI time budget, recovery, alerts (OFF)."""
import io
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from unittest.mock import patch
from urllib.error import HTTPError

import ai.orchestrator as ai_orchestrator
from ai.alerts import AIProviderAlerts
from ai.openai_provider import OpenAIProvider
from ai.provider_health import ProviderHealthTracker
from ai.resilience import AICycleGuard, GuardedProvider
from ai.runtime import AuditLog
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.gates import paper_policy
from runtime.notifications import FakeNotificationSink
import test_demo_runner as fixture
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from test_phase7_ai_call_audit import SECRET, echo_transport
from test_phase7_ai_characterization import full_floor_report

QUOTA = b'{"error":{"type":"insufficient_quota","code":"insufficient_quota"}}'
AGENTS = ["structure_ai", "liquidity_ai", "macro_ai", "setup_reviewer_ai", "trade_reviewer_ai"]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def quota_error(_n):
    raise HTTPError("u", 429, "x", {}, io.BytesIO(QUOTA))


def openai(transport, clock=None, **kwargs):
    return OpenAIProvider(api_key=SECRET, transport=transport, sleep=(lambda s: setattr(clock, "t", clock.t + s))
                          if clock else (lambda _: None), monotonic=clock or __import__("time").monotonic, **kwargs)


class HealthTrackerTests(unittest.TestCase):
    def test_transitions_and_recovery(self):
        h, at = ProviderHealthTracker(failed_after=3), T
        self.assertEqual(h.state, "UNKNOWN")
        self.assertEqual(h.observe("OK", at=at), ("UNKNOWN", "READY"))
        self.assertEqual(h.drain(), [])  # first success is not a recovery
        self.assertEqual(h.observe("RATE_LIMITED", at=at), ("READY", "DEGRADED"))
        self.assertEqual(h.observe("TIMEOUT", at=at + timedelta(minutes=1)), ("DEGRADED", "DEGRADED"))
        self.assertEqual(h.observe("CONNECTION_ERROR", at=at + timedelta(minutes=2)), ("DEGRADED", "FAILED"))
        self.assertEqual(h.observe("RATE_LIMITED", at=at + timedelta(minutes=3)), ("FAILED", "FAILED"))  # no event
        self.assertEqual(h.observe("SHORT_CIRCUITED", at=at), ("FAILED", "FAILED"))  # no provider evidence
        self.assertEqual(h.observe("OK", at=at + timedelta(minutes=10)), ("FAILED", "READY"))
        events = h.drain()
        self.assertEqual([e["event"] for e in events], ["PROVIDER_DEGRADED", "PROVIDER_FAILED", "PROVIDER_RECOVERED"])
        self.assertEqual((events[2]["incident_started_at"], events[2]["duration_seconds"]), (at.isoformat(), 600.0))

    def test_non_transient_fails_immediately(self):
        for outcome in ("QUOTA_EXHAUSTED", "AUTH_ERROR", "MODEL_UNAVAILABLE", "NOT_CONFIGURED"):
            h = ProviderHealthTracker()
            h.observe("OK", at=T)
            self.assertEqual(h.observe(outcome, at=T), ("READY", "FAILED"))
            self.assertEqual(h.drain()[0]["outcome"], outcome)


class CycleGuardTests(unittest.TestCase):
    def run_cycle(self, provider, health=None, budget=120.0, clock=None):
        log = AuditLog()
        guard = AICycleGuard(budget, monotonic=clock or __import__("time").monotonic)
        ai = ai_orchestrator.run(full_floor_report(), GuardedProvider(provider, guard, health), log)
        return ai, log

    def test_non_transient_short_circuits_the_rest_of_the_cycle(self):
        for name, fail in (("quota", quota_error), ("auth", lambda n: (_ for _ in ()).throw(HTTPError("u", 401, "x", {}, None))),
                           ("model", lambda n: (_ for _ in ()).throw(HTTPError("u", 404, "x", {}, None)))):
            with self.subTest(case=name):
                calls = []
                ai, log = self.run_cycle(openai(echo_transport(fail=fail, calls=calls)))
                self.assertEqual(len(calls), 1)  # only the first agent reached the provider (no retries either)
                outcomes = [e["outcome"] for e in log.entries]
                self.assertEqual(outcomes[1:], ["SHORT_CIRCUITED"] * 4)
                self.assertEqual({e["call"].get("short_circuit_cause") for e in log.entries[1:]}, {outcomes[0]})
                self.assertEqual(ai.ai_availability["state"], "TOTAL_FAILURE")
                self.assertFalse(paper_policy(ai))  # fail closed, exactly as without the guard

    def test_transient_failures_stay_bounded_without_short_circuit(self):
        calls = []
        ai, log = self.run_cycle(openai(echo_transport(fail=lambda n: (_ for _ in ()).throw(
            HTTPError("u", 429, "x", {}, None)), calls=calls)))
        self.assertEqual(len(calls), 15)  # 5 agents x 3 attempts: bounded, no cross-agent suppression for 429
        self.assertEqual({e["outcome"] for e in log.entries}, {"RATE_LIMITED"})
        self.assertFalse(paper_policy(ai))

    def test_time_budget_caps_attempts_and_skips_later_agents(self):
        clock, timeouts = Clock(), []

        def slow(payload, key, timeout):  # the provider would need 50 s; every allowed attempt (<= 30 s) times out
            timeouts.append(timeout)
            clock.t += min(50.0, timeout)
            raise TimeoutError()
        ai, log = self.run_cycle(openai(slow, clock=clock, retries=2), budget=120.0, clock=clock)
        # Agent 1: 3 attempts x 30 s (+ backoff). Agent 2: one attempt capped by the remaining budget (< 30 s), then
        # no retry because it could not start before the deadline. Agents 3-5: never called.
        self.assertEqual(timeouts[:3], [30.0, 30.0, 30.0])
        self.assertEqual(len(timeouts), 4)
        self.assertLess(timeouts[3], 30.0)
        self.assertLessEqual(clock.t - 1000.0, 120.0 + 1e-9)  # never beyond the budget
        self.assertEqual([e["outcome"] for e in log.entries],
                         ["TIMEOUT", "TIMEOUT"] + ["AI_TIME_BUDGET_EXHAUSTED"] * 3)
        self.assertEqual([e["call"].get("attempts") and len(e["call"]["attempts"]) for e in log.entries[:2]], [3, 1])
        self.assertFalse(paper_policy(ai))

    def test_budget_bound_under_permanent_timeouts(self):
        clock = Clock()

        def hang(payload, key, timeout):
            clock.t += timeout  # each attempt consumes its full (capped) timeout
            raise TimeoutError()
        ai, log = self.run_cycle(openai(hang, clock=clock), budget=90.0, clock=clock)
        self.assertLessEqual(clock.t - 1000.0, 90.0 + 1e-9)
        self.assertFalse(paper_policy(ai))
        unguarded = Clock()

        def hang2(payload, key, timeout):
            unguarded.t += timeout
            raise TimeoutError()
        log = AuditLog()
        ai_orchestrator.run(full_floor_report(), openai(hang2, clock=unguarded), log)  # no guard: unchanged behavior
        self.assertGreater(unguarded.t - 1000.0, 400.0)  # 5 agents x 3 x 30 s + backoff

    def test_recovery_on_the_next_cycle_without_restart(self):
        health, state, calls = ProviderHealthTracker(), {"fail": True}, []

        def fail(n):
            if state["fail"]:
                quota_error(n)
        provider = openai(echo_transport(fail=fail, calls=calls))
        ai, _ = self.run_cycle(provider, health)
        self.assertEqual((health.state, paper_policy(ai)), ("FAILED", False))
        state["fail"] = False  # balance recharged; same process, same provider object
        ai, log = self.run_cycle(provider, health)  # new cycle = new guard: no carried-over short-circuit
        self.assertEqual((health.state, {e["outcome"] for e in log.entries}), ("READY", {"OK"}))
        self.assertTrue(paper_policy(ai))
        self.assertEqual([e["event"] for e in health.drain()], ["PROVIDER_FAILED", "PROVIDER_RECOVERED"])
        self.assertEqual(len(calls), 1 + 5)

    def test_health_before_after_in_call_records(self):
        health = ProviderHealthTracker()
        _, log = self.run_cycle(openai(echo_transport(fail=quota_error)), health)
        self.assertEqual((log.entries[0]["call"]["health_before"], log.entries[0]["call"]["health_after"]),
                         ("UNKNOWN", "FAILED"))


class AlertTests(unittest.TestCase):
    def events(self):
        h = ProviderHealthTracker()
        h.observe("OK", at=T)
        h.observe("QUOTA_EXHAUSTED", at=T, run_id="r1")
        h.observe("QUOTA_EXHAUSTED", at=T + timedelta(minutes=15), run_id="r2")  # identical repeat: no event
        h.observe("OK", at=T + timedelta(minutes=30), run_id="r3")
        return h.drain()

    def test_default_off(self):
        sink = FakeNotificationSink()
        alerts = AIProviderAlerts(sink)  # enabled defaults to False
        self.assertEqual((alerts.process(self.events()), sink.events), (0, []))
        self.assertEqual(AIProviderAlerts(sink, enabled="yes").enabled, False)  # only an explicit True enables
        self.assertIsNone(RuntimeConfig().__dict__.get("ai_alerts"))

    def test_enabled_dedup_and_safe_delivery(self):
        sink, events = FakeNotificationSink(), self.events()
        alerts = AIProviderAlerts(sink, enabled=True)
        self.assertEqual(alerts.process(events), 2)
        self.assertEqual(alerts.process(events), 0)  # the same incident is never re-sent
        self.assertEqual([e.type for e in sink.events], ["AI_PROVIDER_FAILED", "AI_PROVIDER_RECOVERED"])
        payload = json.dumps([e.payload() for e in sink.events], default=str)
        self.assertIn("QUOTA_EXHAUSTED", payload)
        for forbidden in (SECRET, "Authorization", "balance"):
            self.assertNotIn(forbidden, payload)

        class Broken:
            def deliver(self, event):
                raise OSError("smtp down")
        self.assertEqual(AIProviderAlerts(Broken(), enabled=True).process(self.events()), 0)  # swallowed


class RuntimeResilienceTests(unittest.TestCase):
    setUp = fixture.TestDemoRunner.setUp
    tearDown = fixture.TestDemoRunner.tearDown

    def two_cycles(self, config, fail_first=True):
        now, state, calls = [T], {"fail": fail_first}, []

        def fail(n):
            if state["fail"]:
                quota_error(n)
        runner = DemoRunner(config, market_provider=Data(), ai_provider=openai(echo_transport(fail=fail, calls=calls)),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()}, clock=lambda: now[0])
        try:
            results = []
            for index in range(2):
                now[0] = T + timedelta(minutes=15 * index)
                runner.runtime.clock = lambda: now[0]
                start = len(calls)
                with patched_scouts("LONG"):
                    result = runner.run_once("XAUUSD", now[0])
                review = runner.store.review_report(result["run_id"])
                results.append((result["status"], review["execution"]["execution_reason"], len(calls) - start,
                                len(runner.store.load_paper("paper-main")[1])))
                state["fail"] = False
            journal = [r[0] for r in runner.store.db.execute(
                "SELECT event_type FROM journal WHERE source='ai_provider_health' ORDER BY id")]
            return results, journal
        finally:
            runner.close()
            for suffix in ("", "-wal", "-shm"):
                self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def test_flag_off_by_default_and_not_from_env(self):
        self.assertFalse(RuntimeConfig().v2_ai_resilience)
        with patch.dict("os.environ", {"AI_FLOOR_V2_AI_RESILIENCE": "1"}):
            self.assertFalse(RuntimeConfig.from_env().v2_ai_resilience)
        self.assertEqual(self.config.fingerprint(), replace(self.config, v2_ai_resilience=False).fingerprint())

    def test_off_vs_on_same_decisions_fewer_calls_and_recovery_events(self):
        off, off_journal = self.two_cycles(self.config)
        on, on_journal = self.two_cycles(replace(self.config, v2_ai_resilience=True))
        # Cycle 1 (quota): no order either way; ON reaches the provider once instead of 5 times.
        self.assertEqual([r[:2] for r in off], [r[:2] for r in on])
        self.assertEqual((off[0][2], on[0][2]), (5, 1))
        self.assertEqual((off[0][3], on[0][3]), (0, 0))
        # Cycle 2 (recharged): identical submission; recovery is observed without restart.
        self.assertEqual((off[1][1], on[1][1], off[1][3], on[1][3]), ("ORDER_SUBMITTED", "ORDER_SUBMITTED", 1, 1))
        self.assertEqual(off_journal, [])
        self.assertEqual(on_journal, ["AI_PROVIDER_FAILED", "AI_PROVIDER_RECOVERED"])


if __name__ == "__main__":
    unittest.main()
