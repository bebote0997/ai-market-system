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
    BASE_SECONDS, CALL_LOCK, FLAG, MAX_ATTEMPTS, MAX_BACKOFF_SECONDS, MAX_SINGLE_WAIT_SECONDS,
    QUIET_RESET_SECONDS, TOTAL_WAIT_CAP_SECONDS, enabled, plan_wait, reset_state,
)
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
        self.assertEqual(TOTAL_WAIT_CAP_SECONDS, 90.0)
        self.assertEqual(MAX_SINGLE_WAIT_SECONDS, 60.0)
        self.assertEqual(QUIET_RESET_SECONDS, 120.0)
        self.assertEqual(MAX_ATTEMPTS, 6)
        self.assertEqual(BASE_SECONDS, 1.0)
        self.assertLessEqual(TOTAL_WAIT_CAP_SECONDS, 120.0)
        self.assertLess(TOTAL_WAIT_CAP_SECONDS, 15 * 60)
        self.assertLessEqual(MAX_SINGLE_WAIT_SECONDS, MAX_BACKOFF_SECONDS + 30)

    def test_jitter_never_waits_less_than_retry_after_and_stays_bounded(self):
        reset_state()
        self.addCleanup(reset_state)
        with CALL_LOCK:
            short = plan_wait(0, None, now=0.0, uniform=lambda _a, _b: 999)
            honored = plan_wait(0, 20, now=0.0, uniform=lambda _a, _b: -5)
            capped = plan_wait(0, 20, now=0.0, uniform=lambda _a, _b: 999)
            refused = plan_wait(0, 61, now=0.0, uniform=lambda _a, _b: 0)
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
        self.assertLessEqual(sum(state["slept"]), TOTAL_WAIT_CAP_SECONDS)
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
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            if calls["n"] in {1, 3}:
                raise http_error(429, headers={"Retry-After": "60"})
            return openai_payload()

        provider, state = self.provider(transport)
        with patch.dict(os.environ, {FLAG: "1"}), patch("ai.xai_provider.random.uniform", return_value=0):
            self.assertEqual(provider.generate(request()).status, "OK")
            with self.assertRaises(XAIProviderError) as caught:
                provider.generate(request())
            self.assertEqual(caught.exception.kind, "RATE_LIMITED")
            self.assertEqual(state["slept"], [60.0])
            state["t"] = state["slept"][0] + QUIET_RESET_SECONDS
            calls["n"] = 2
            self.assertEqual(provider.generate(request()).status, "OK")
        self.assertEqual(state["slept"], [60.0, 60.0])

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
