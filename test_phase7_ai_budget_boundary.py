"""P7.1F permanent regression for the P7.2 HIGH: a response that completes after the global AI cycle budget must not
keep or create execution eligibility. Fake clocks/providers only; no network."""
import unittest
from urllib.error import HTTPError

import ai.orchestrator as ai_orchestrator
from ai.call_audit import build_records
from ai.openai_provider import OpenAIProvider, OpenAIProviderError
from ai.provider import DeterministicAIProvider
from ai.provider_health import ProviderHealthTracker
from ai.resilience import AICycleGuard, GuardedProvider
from ai.runtime import AuditLog
from runtime.gates import paper_policy
from test_phase7_ai_call_audit import SECRET, echo_transport
from test_phase7_ai_characterization import full_floor_report


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class SlowValidProvider:
    """Answers validly after ``cost`` simulated seconds and IGNORES any deadline (the reviewer's provider class)."""

    def __init__(self, clock, cost=25.0, fail_at=None):
        self.clock, self.cost, self.fail_at = clock, cost, fail_at
        self.calls = 0
        self.inner = DeterministicAIProvider()

    @property
    def metadata(self):
        return {"provider": "slow-fake", "model": "fake"}

    def generate(self, request):
        self.calls += 1
        self.clock.t += self.cost
        if self.fail_at == self.calls:
            raise OpenAIProviderError("CONNECTION_ERROR", timed_out=True)
        return self.inner.generate(request)


def run(budget, cost=25.0, fail_at=None, health=None):
    clock = Clock()
    provider = SlowValidProvider(clock, cost, fail_at)
    log = AuditLog()
    guard = AICycleGuard(budget, monotonic=clock)
    ai = ai_orchestrator.run(full_floor_report(), GuardedProvider(provider, guard, health), log)
    return ai, log, provider, clock


class LateResponseRegression(unittest.TestCase):
    def test_reviewer_scenario_five_calls_25s_budget_120(self):
        ai, log, provider, clock = run(budget=120.0)
        self.assertEqual(clock.t, 125.0)  # the fifth call began at 100 s (20 s left) and completed at 125 s
        self.assertGreater(clock.t, 120.0)
        self.assertEqual([e["outcome"] for e in log.entries], ["OK"] * 4 + ["AI_TIME_BUDGET_EXHAUSTED"])
        self.assertEqual(ai.ai_trade_review.status, "ERROR")  # late valid response NOT accepted
        self.assertTrue(log.entries[-1]["call"]["late_response_rejected"])
        self.assertEqual(log.entries[-1]["call"]["late_response_status"], "OK")  # truthful: it did answer
        self.assertFalse(paper_policy(ai))  # fail closed
        self.assertEqual(provider.calls, 5)  # no call after exhaustion (there is no later agent here)
        self.assertEqual(ai.final_status, "PLAN_READY")  # final_status semantics unchanged; the gate decides

    def test_not_a_provider_timeout(self):
        _, log, _, _ = run(budget=120.0)
        last = log.entries[-1]
        self.assertEqual((last["outcome"], last["error_kind"], last["http_status"]),
                         ("AI_TIME_BUDGET_EXHAUSTED", "AI_TIME_BUDGET_EXHAUSTED", None))
        self.assertNotIn("ai_outcome:TIMEOUT", log.entries[-1]["response"].warnings)


class BoundaryTests(unittest.TestCase):
    def test_completes_clearly_before_deadline_is_accepted(self):
        ai, log, provider, clock = run(budget=200.0)
        self.assertEqual(({e["outcome"] for e in log.entries}, clock.t), ({"OK"}, 125.0))
        self.assertTrue(paper_policy(ai))  # unchanged when the budget is not exceeded

    def test_completes_exactly_at_deadline_fails_closed(self):
        ai, log, _, clock = run(budget=125.0)
        self.assertEqual(clock.t, 125.0)  # elapsed == budget
        self.assertEqual(log.entries[-1]["outcome"], "AI_TIME_BUDGET_EXHAUSTED")
        self.assertFalse(paper_policy(ai))

    def test_completes_just_inside_deadline_is_accepted(self):
        ai, log, _, _ = run(budget=125.000001)
        self.assertEqual({e["outcome"] for e in log.entries}, {"OK"})
        self.assertTrue(paper_policy(ai))

    def test_exhausted_before_call_means_no_call(self):
        ai, log, provider, clock = run(budget=100.0)  # fourth call ends exactly at 100 s -> rejected; fifth not called
        self.assertEqual(provider.calls, 4)
        self.assertEqual([e["outcome"] for e in log.entries], ["OK"] * 3 + ["AI_TIME_BUDGET_EXHAUSTED"] * 2)
        self.assertTrue(log.entries[3]["call"]["late_response_rejected"])
        self.assertFalse(log.entries[4]["call"].get("late_response_rejected", False))
        self.assertEqual(log.entries[4]["call"]["attempts"], [])  # never reached the provider
        self.assertFalse(paper_policy(ai))

    def test_provider_timeout_before_deadline_stays_timeout(self):
        ai, log, _, clock = run(budget=500.0, fail_at=2)
        self.assertEqual(log.entries[1]["outcome"], "TIMEOUT")
        self.assertLess(clock.t, 500.0)
        self.assertFalse(paper_policy(ai))

    def test_budget_expires_during_successful_openai_call(self):
        clock = Clock()

        def late(payload, key, timeout):  # the transport ignores its capped timeout and answers late
            clock.t += 25.0
            return echo_transport()(payload, key, timeout)
        health, log = ProviderHealthTracker(), AuditLog()
        provider = OpenAIProvider(api_key=SECRET, transport=late, monotonic=clock, sleep=lambda _: None)
        ai = ai_orchestrator.run(full_floor_report(), GuardedProvider(provider, AICycleGuard(120.0, monotonic=clock),
                                                                      health), log)
        self.assertEqual(log.entries[-1]["outcome"], "AI_TIME_BUDGET_EXHAUSTED")
        self.assertFalse(paper_policy(ai))
        self.assertEqual(health.state, "READY")  # provider health is truthful: the provider answered
        record = build_records(log.entries, run_id="run-p7", symbol="XAUUSD")[-1]
        self.assertEqual((record["outcome"], record["late_response_rejected"], record["late_response_status"]),
                         ("AI_TIME_BUDGET_EXHAUSTED", True, "OK"))
        self.assertEqual(record["usage"], {"input": 100, "output": 40, "total": 140})  # consumed usage kept
        self.assertTrue(record["response_id"].startswith("resp_"))
        self.assertEqual(record["attempt_log"][0]["http_status"], 200)

    def test_retry_backoff_crossing_deadline_fails_closed(self):
        clock = Clock()

        def limited(payload, key, timeout):
            clock.t += 1.0
            raise HTTPError("u", 429, "x", {"Retry-After": "10"}, None)
        provider = OpenAIProvider(api_key=SECRET, transport=limited, monotonic=clock,
                                  sleep=lambda s: setattr(clock, "t", clock.t + s))
        log = AuditLog()
        ai = ai_orchestrator.run(full_floor_report(), GuardedProvider(provider, AICycleGuard(15.0, monotonic=clock)),
                                 log)
        self.assertEqual(log.entries[0]["outcome"], "RATE_LIMITED")
        self.assertLessEqual(len(log.entries[0]["call"]["attempts"]), 2)  # retry stopped by the deadline
        self.assertTrue(all(e["outcome"] != "OK" for e in log.entries))
        self.assertFalse(paper_policy(ai))


if __name__ == "__main__":
    unittest.main()
