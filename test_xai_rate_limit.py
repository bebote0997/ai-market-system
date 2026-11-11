"""XAI_RATE_LIMIT_V2: off matches today's retries; on serializes xAI and retries HTTP 429.

The flag is read from the environment at call time. Unset is off. These tests put it
back, and they reset the shared wait budget so a later test does not inherit it.
"""
from dataclasses import replace
import json
import os
from pathlib import Path
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai.openai_provider import OpenAIProvider
from ai.runtime import AuditLog, call_agent
from ai.xai_provider import XAIProvider, XAIProviderError
from ai.xai_rate_limit import (
    BASE_SECONDS, CALL_LOCK, CALL_WALL_SECONDS, CYCLE_WALL_SECONDS, FLAG, MAX_ATTEMPTS,
    MAX_BACKOFF_SECONDS, MAX_SINGLE_WAIT_SECONDS, QUIET_RESET_SECONDS, enabled, plan_wait,
    reset_state, wall_spent,
)
from runtime.cloud import WATCHDOG_STALE_SECONDS
from runtime.config import RuntimeConfig
from runtime.gates import paper_policy
from test_real_providers import openai_payload, request
from test_xai_provider import http_error


OFF_VALUES = ("0", "", "true", "yes", " 1", "on", "2")


def _clock():
    state = {"t": 0.0, "slept": []}

    def monotonic():
        return state["t"]

    def sleep(seconds):
        state["t"] += seconds
        state["slept"].append(seconds)

    return state, monotonic, sleep


class FlagTests(unittest.TestCase):
    def test_only_exact_one_enables_the_policy(self):
        self.assertFalse(enabled({}))
        self.assertFalse(enabled({FLAG: None}))
        for value in OFF_VALUES:
            self.assertFalse(enabled({FLAG: value}), value)
        self.assertTrue(enabled({FLAG: "1"}))

    def test_caps_finish_inside_a_cycle(self):
        # 10 calls (5 agents + a macro retry of the floor) x 6 attempts x 30s = 1800s unchecked.
        self.assertEqual(WATCHDOG_STALE_SECONDS, 300)
        self.assertIs(RuntimeConfig.__dataclass_fields__["v2_ai_resilience"].default, False)
        self.assertEqual(CALL_WALL_SECONDS, 90.0)
        self.assertEqual(CYCLE_WALL_SECONDS, 180.0)
        self.assertEqual(MAX_SINGLE_WAIT_SECONDS, 60.0)
        self.assertEqual(QUIET_RESET_SECONDS, 120.0)
        self.assertEqual(MAX_ATTEMPTS, 6)
        self.assertEqual(BASE_SECONDS, 1.0)
        self.assertLessEqual(CYCLE_WALL_SECONDS, WATCHDOG_STALE_SECONDS - 120)
        self.assertLessEqual(CALL_WALL_SECONDS, CYCLE_WALL_SECONDS)
        self.assertGreaterEqual(CALL_WALL_SECONDS, 30 + 1 + 30)
        # Two full timeouts plus the two short backoffs before a third attempt do not fit.
        self.assertLess(CALL_WALL_SECONDS, 30 + BASE_SECONDS + 30 + BASE_SECONDS + 30)
        self.assertLess(CYCLE_WALL_SECONDS, 10 * MAX_ATTEMPTS * 30)
        self.assertLessEqual(MAX_SINGLE_WAIT_SECONDS, MAX_BACKOFF_SECONDS + 30)

    def test_jitter_never_waits_less_than_retry_after_and_stays_bounded(self):
        reset_state()
        self.addCleanup(reset_state)
        kwargs = {"now": 0.0, "call_started": 0.0, "timeout": 30.0}

        def draw(value):
            return lambda _a, _b: value

        with CALL_LOCK:
            short = plan_wait(0, None, uniform=draw(999), **kwargs)
            honored = plan_wait(0, 20, uniform=draw(-5), **kwargs)
            capped = plan_wait(0, 20, uniform=draw(999), **kwargs)
            refused = plan_wait(0, 61, uniform=draw(0), **kwargs)
        self.assertEqual(short, 1.25)
        self.assertEqual(honored, 20.0)
        self.assertEqual(capped, 21.0)
        self.assertIsNone(refused)

    def test_flag_is_not_part_of_the_runtime_fingerprint(self):
        path = Path("data/runtime") / f"xai-rl-{uuid4().hex}.db"
        self.addCleanup(path.unlink, missing_ok=True)
        plain = RuntimeConfig(db_path=path, market_provider_mode="none")
        with patch.dict(os.environ, {FLAG: "1"}, clear=False):
            flagged = RuntimeConfig(db_path=path, market_provider_mode="none")
        self.assertEqual(plain.fingerprint(), flagged.fingerprint())
        self.assertNotIn(FLAG, json.dumps(plain.fingerprint_content()))


class ProviderRetryTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.addCleanup(reset_state)

    def provider(self, transport, **kwargs):
        state, monotonic, sleep = _clock()
        provider = XAIProvider(api_key="k", transport=transport, sleep=sleep, monotonic=monotonic, **kwargs)
        return provider, state

    def test_flag_off_matches_the_legacy_retry_schedule(self):
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            raise http_error(429)

        for value in (None, *OFF_VALUES):
            with self.subTest(value=value):
                calls["n"] = 0
                env = {} if value is None else {FLAG: value}
                provider, state = self.provider(transport, retries=2)
                with patch.dict(os.environ, env, clear=False), \
                        patch("ai.xai_provider.random.uniform", return_value=0), \
                        self.assertNoLogs("ai_floor.xai", level="INFO"), \
                        self.assertRaises(XAIProviderError) as caught:
                    if value is None:
                        os.environ.pop(FLAG, None)
                    provider.generate(request())
                self.assertEqual(caught.exception.kind, "RATE_LIMITED")
                self.assertEqual(calls["n"], 3)
                self.assertEqual(state["slept"], [0.4, 0.8])

        calls["n"] = 0

        def with_header(*_args):
            calls["n"] += 1
            raise http_error(429, headers={"Retry-After": "20"})

        provider, state = self.provider(with_header, retries=2)
        with patch.dict(os.environ, {FLAG: "0"}, clear=False), self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual((caught.exception.kind, calls["n"], state["slept"]), ("RATE_LIMITED", 1, []))

        calls["n"] = 0

        def short_header(*_args):
            calls["n"] += 1
            if calls["n"] < 3:
                raise http_error(429, headers={"Retry-After": "3"})
            return openai_payload()

        provider, state = self.provider(short_header, retries=2)
        with patch.dict(os.environ, {}, clear=False), patch("ai.xai_provider.random.uniform", return_value=0):
            os.environ.pop(FLAG, None)
            self.assertEqual(provider.generate(request()).status, "OK")
        self.assertEqual(state["slept"], [3, 3])

    def test_flag_on_retries_without_retry_after_then_succeeds_and_logs(self):
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            if calls["n"] < 3:
                raise http_error(429)
            return openai_payload(agent_name="setup_reviewer_ai")

        provider, state = self.provider(transport)
        review = replace(request(), agent_name="setup_reviewer_ai", role="setup_reviewer")
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0), \
                self.assertLogs("ai_floor.xai", level="INFO") as logs:
            self.assertEqual(provider.generate(review).status, "OK")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(state["slept"], [1.0, 2.0])
        text = "\n".join(logs.output)
        self.assertIn("event=RATE_LIMIT_RETRY agent=setup_reviewer_ai attempt=1 wait_seconds=1.000", text)
        self.assertIn("event=RATE_LIMIT_RETRY agent=setup_reviewer_ai attempt=2 wait_seconds=2.000", text)
        self.assertNotIn("test-key", text)

    def test_flag_on_honors_retry_after_above_the_legacy_15_second_stop(self):
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http_error(429, headers={"Retry-After": "20"})
            return openai_payload(agent_name="liquidity_ai")

        provider, state = self.provider(transport)
        review = replace(request(), agent_name="liquidity_ai", role="liquidity_specialist")
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0), \
                self.assertLogs("ai_floor.xai", level="INFO") as logs:
            self.assertEqual(provider.generate(review).status, "OK")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(state["slept"], [20.0])
        self.assertIn("agent=liquidity_ai attempt=1 wait_seconds=20.000", "\n".join(logs.output))

    def test_flag_on_fails_closed_after_the_attempt_cap_without_another_provider(self):
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            raise http_error(429)

        provider, state = self.provider(transport)
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0), \
                patch("ai.openai_provider.OpenAIProvider", side_effect=AssertionError("fallback")), \
                self.assertLogs("ai_floor.xai", level="INFO") as logs, \
                self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "RATE_LIMITED")
        self.assertEqual(str(caught.exception), "RATE_LIMITED")
        self.assertEqual(calls["n"], MAX_ATTEMPTS)
        self.assertEqual(state["slept"], [BASE_SECONDS * 2 ** i for i in range(MAX_ATTEMPTS - 1)])
        self.assertLessEqual(sum(state["slept"]), CYCLE_WALL_SECONDS)
        self.assertEqual(len(logs.output), MAX_ATTEMPTS - 1)
        self.assertIsNotNone(OpenAIProvider)

    def test_retry_after_above_the_single_wait_cap_fails_closed_without_sleeping(self):
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            raise http_error(429, headers={"Retry-After": "61"})

        provider, state = self.provider(transport)
        with patch.dict(os.environ, {FLAG: "1"}), self.assertNoLogs("ai_floor.xai", level="INFO"), \
                self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual((caught.exception.kind, caught.exception.retry_after, calls["n"], state["slept"]),
                         ("RATE_LIMITED", 61, 1, []))

    def test_shared_budget_fails_the_next_call_closed_and_a_quiet_gap_resets_it(self):
        pending = {"n": 0}

        def transport(*_args):
            if pending["n"]:
                pending["n"] -= 1
                raise http_error(429, headers={"Retry-After": "60"})
            return openai_payload()

        def once():
            pending["n"] = 1

        provider, state = self.provider(transport)
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0):
            once()
            self.assertEqual(provider.generate(request()).status, "OK")
            once()
            self.assertEqual(provider.generate(request()).status, "OK")
            once()
            with self.assertRaises(XAIProviderError) as caught:
                provider.generate(request())
            self.assertEqual(caught.exception.kind, "RATE_LIMITED")
            self.assertEqual(state["slept"], [60.0, 60.0])
            self.assertEqual(wall_spent(), 120.0)
            state["t"] += QUIET_RESET_SECONDS
            once()
            self.assertEqual(provider.generate(request()).status, "OK")
        self.assertEqual(state["slept"], [60.0, 60.0, 60.0])
        self.assertEqual(wall_spent(), 60.0)

    def test_a_raised_sleep_releases_the_reserved_wait(self):
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            raise http_error(429, headers={"Retry-After": "60"})

        def boom(_seconds):
            raise RuntimeError("sleep failed")

        provider, _state = self.provider(transport)
        provider._sleep = boom
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0), \
                self.assertRaises(RuntimeError):
            provider.generate(request())
        self.assertEqual(calls["n"], 1)
        self.assertEqual(wall_spent(), 0.0)

    def test_deadline_still_cuts_a_wait_that_would_pass_it(self):
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            raise http_error(429)

        provider, state = self.provider(transport)
        provider.deadline = 1.5
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0), \
                self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "RATE_LIMITED")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(state["slept"], [1.0])

    def test_quota_and_server_errors_keep_the_legacy_schedule_when_the_flag_is_on(self):
        def quota(*_args):
            raise http_error(429, body=b'{"error":{"type":"insufficient_quota","code":"insufficient_quota"}}')

        provider, state = self.provider(quota)
        with patch.dict(os.environ, {FLAG: "1"}), self.assertNoLogs("ai_floor.xai", level="INFO"), \
                self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual((caught.exception.kind, state["slept"]), ("QUOTA_EXHAUSTED", []))

        calls = {"n": 0}

        def server(*_args):
            calls["n"] += 1
            raise http_error(500)

        provider, state = self.provider(server, retries=2)
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0), \
                self.assertNoLogs("ai_floor.xai", level="INFO"), \
                self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "PROVIDER_ERROR")
        self.assertEqual(calls["n"], 3)
        self.assertEqual(state["slept"], [0.4, 0.8])

    def test_unsafe_agent_text_is_not_written_into_the_retry_log(self):
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http_error(429)
            return openai_payload(agent_name="liquidity ai")

        provider, _state = self.provider(transport)
        review = replace(request(), agent_name="liquidity ai", role="liquidity_specialist")
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0), \
                self.assertLogs("ai_floor.xai", level="INFO") as logs:
            self.assertEqual(provider.generate(review).status, "OK")
        self.assertIn("agent=- attempt=1 wait_seconds=1.000", logs.output[0])
        self.assertNotIn("liquidity ai", logs.output[0])

    def test_capped_429_stays_an_error_review_and_cannot_authorize_a_paper_order(self):
        def transport(*_args):
            raise http_error(429, headers={"Retry-After": "61"})

        provider, _state = self.provider(transport)
        log = AuditLog()
        with patch.dict(os.environ, {FLAG: "1"}):
            response = call_agent(provider, request(), log)
        self.assertEqual(response.status, "ERROR")
        self.assertIn("ai_outcome:RATE_LIMITED", response.warnings)
        self.assertEqual(log.entries[-1]["outcome"], "RATE_LIMITED")
        others = tuple(type("Review", (), {"status": "OK"})() for _ in range(4))
        report = type("Report", (), {
            "final_status": "PLAN_READY",
            "risk_decision": type("Risk", (), {"status": "APPROVED"})(),
            "trade_plan": object(),
            "ai_structure": response,
            "ai_liquidity": others[0],
            "ai_macro": others[1],
            "ai_setup_review": others[2],
            "ai_trade_review": others[3],
        })()
        self.assertFalse(paper_policy(report))
        report.final_status = "WATCH"
        self.assertFalse(paper_policy(report))


class SlowCycleTests(unittest.TestCase):
    """Each 429 burns the full HTTP timeout on the fake clock. No deadline is set."""

    def setUp(self):
        reset_state()
        self.addCleanup(reset_state)

    def test_ten_slow_calls_stay_under_the_watchdog(self):
        # 5 specialists, then the macro NO_DATA path runs the floor again.
        agents = ("structure_ai", "liquidity_ai", "macro_ai", "setup_reviewer_ai", "trade_reviewer_ai")
        state = {"t": 0.0, "calls": 0, "timeouts": []}

        def monotonic():
            return state["t"]

        def sleep(seconds):
            state["t"] += seconds

        def transport(*args):
            timeout = args[-1]
            state["calls"] += 1
            state["timeouts"].append(timeout)
            state["t"] += timeout
            raise http_error(429)

        provider = XAIProvider(api_key="k", transport=transport, sleep=sleep, monotonic=monotonic,
                               timeout=30, retries=2)
        self.assertIsNone(provider.deadline)
        per_call = []
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0):
            for _agent in agents + agents:
                started = state["t"]
                before = state["calls"]
                with self.assertRaises(XAIProviderError) as caught:
                    provider.generate(request())
                self.assertEqual(caught.exception.kind, "RATE_LIMITED")
                per_call.append((state["calls"] - before, state["t"] - started))
        self.assertLessEqual(state["t"], CYCLE_WALL_SECONDS)
        self.assertLess(state["t"], WATCHDOG_STALE_SECONDS)
        self.assertGreaterEqual(WATCHDOG_STALE_SECONDS - state["t"], 120)
        self.assertTrue(state["timeouts"])
        self.assertTrue(all(timeout == 30.0 for timeout in state["timeouts"]))
        self.assertLessEqual(len(state["timeouts"]) * 30.0, CYCLE_WALL_SECONDS)
        self.assertLess(len(state["timeouts"]), len(agents) * 2 * MAX_ATTEMPTS)
        self.assertTrue(all(calls <= 2 and elapsed <= CALL_WALL_SECONDS for calls, elapsed in per_call))
        self.assertTrue(any(calls == 0 for calls, _elapsed in per_call))
        self.assertLessEqual(wall_spent(), CYCLE_WALL_SECONDS)
        self.assertAlmostEqual(wall_spent(), state["t"])


class SerializationTests(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.addCleanup(reset_state)

    def _pair(self, transport, sleep, flag):
        providers = [
            XAIProvider(api_key="k", transport=transport, sleep=sleep, retries=2),
            XAIProvider(api_key="k", transport=transport, sleep=sleep, retries=2),
        ]
        barrier = threading.Barrier(2)
        errors = []

        def work(provider):
            try:
                barrier.wait(timeout=2)
                provider.generate(request())
            except Exception as exc:  # noqa: BLE001 - the assertion below reports it
                errors.append(exc)

        threads = [threading.Thread(target=work, args=(provider,)) for provider in providers]
        with patch.dict(os.environ, {FLAG: flag}):
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])

    def test_flag_on_never_overlaps_calls_even_while_one_is_backing_off(self):
        guard = threading.Lock()
        state = {"n": 0, "current": 0, "max": 0, "during_backoff": None}
        slept = []

        def transport(*_args):
            with guard:
                state["n"] += 1
                number = state["n"]
                state["current"] += 1
                state["max"] = max(state["max"], state["current"])
            try:
                time.sleep(0.02)
                if number == 1:
                    raise http_error(429)
                return openai_payload()
            finally:
                with guard:
                    state["current"] -= 1

        def sleep(seconds):
            time.sleep(0.15)
            with guard:
                state["during_backoff"] = state["current"]
            slept.append(seconds)

        with patch("ai.xai_provider.random.uniform", return_value=0):
            self._pair(transport, sleep, "1")
        self.assertEqual(state["max"], 1)
        self.assertEqual(state["during_backoff"], 0)
        self.assertGreaterEqual(state["n"], 3)
        self.assertTrue(slept)

    def test_flag_off_still_allows_overlapping_calls(self):
        guard = threading.Lock()
        state = {"current": 0, "max": 0}

        def transport(*_args):
            with guard:
                state["current"] += 1
                state["max"] = max(state["max"], state["current"])
            try:
                time.sleep(0.2)
                return openai_payload()
            finally:
                with guard:
                    state["current"] -= 1

        self._pair(transport, lambda _seconds: None, "0")
        self.assertEqual(state["max"], 2)


if __name__ == "__main__":
    unittest.main()
