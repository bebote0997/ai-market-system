"""xAI adapter: schema, tools, budget, retries, redaction, and no fallback to OpenAI."""
from datetime import datetime, timezone
from io import BytesIO
from urllib.error import HTTPError, URLError
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai.openai_provider import OpenAIProvider
from ai.provider_policy import FALLBACK, XAI_BASE_URL, XAI_MODEL, build_provider, selection
from ai.xai_provider import XAIProvider, XAIProviderError, responses_url
from runtime.config import RuntimeConfig
from runtime.service import OperationalRuntime
from test_real_providers import openai_payload, request


NOW = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)


def http_error(code, body=b"{}", headers=None):
    return HTTPError("https://api.x.ai/v1/responses", code, "status", headers or {}, BytesIO(body))


class PolicyTests(unittest.TestCase):
    def test_xai_selection_has_no_fallback(self):
        self.assertIsNone(FALLBACK)
        self.assertEqual(selection("xai"), {"mode": "xai", "fallback": None})
        self.assertEqual(selection("openai")["mode"], "openai")
        with self.assertRaises(ValueError):
            selection("openai-then-xai")

    def test_building_xai_does_not_construct_openai(self):
        with patch.dict(os.environ, {"XAI_API_KEY": "test-key", "XAI_MODEL": XAI_MODEL}, clear=False), \
                patch("ai.openai_provider.OpenAIProvider", side_effect=AssertionError("fallback")):
            provider = build_provider("xai")
        self.assertIsInstance(provider, XAIProvider)
        self.assertEqual(provider.model, XAI_MODEL)
        self.assertNotIn("OPENAI", provider.base_url.upper())

    def test_config_accepts_xai_and_rejects_an_unknown_mode(self):
        path = Path("data/runtime") / f"xai-{uuid4().hex}.db"
        self.addCleanup(path.unlink, missing_ok=True)
        config = RuntimeConfig(db_path=path, ai_provider_mode="xai", market_provider_mode="none")
        self.assertEqual(config.ai_provider_mode, "xai")
        self.assertNotEqual(config.fingerprint(), RuntimeConfig(db_path=path).fingerprint())
        with self.assertRaises(ValueError):
            RuntimeConfig(db_path=path, ai_provider_mode="grok")


class AdapterTests(unittest.TestCase):
    def payload_of(self, **kwargs):
        seen = {}

        def transport(url, payload, api_key, timeout):
            seen.update(url=url, payload=payload, api_key=api_key, timeout=timeout)
            return openai_payload()

        provider = XAIProvider(api_key="test-key", transport=transport, sleep=lambda _seconds: None, **kwargs)
        response = provider.generate(request())
        return provider, seen, response

    def test_request_uses_the_official_responses_endpoint_without_tools(self):
        provider, seen, response = self.payload_of()
        self.assertEqual(seen["url"], "https://api.x.ai/v1/responses")
        self.assertEqual(seen["payload"]["model"], "grok-4.7")
        self.assertFalse(seen["payload"]["store"])
        self.assertEqual(seen["payload"]["reasoning"], {"effort": "low"})
        self.assertNotIn("tools", seen["payload"])
        self.assertEqual(seen["payload"]["text"]["format"]["type"], "json_schema")
        self.assertEqual(seen["payload"]["text"]["format"]["strict"], True)
        self.assertEqual(seen["payload"]["max_output_tokens"], 800)
        self.assertEqual(response.status, "OK")
        self.assertEqual(response.model_metadata["provider"], "xai")
        self.assertEqual(provider.last_usage["total_tokens"], 30)

    def test_base_url_must_be_https(self):
        self.assertEqual(responses_url(None), XAI_BASE_URL + "/responses")
        with self.assertRaises(ValueError):
            XAIProvider(api_key="k", base_url="http://api.x.ai/v1")

    def test_missing_key_does_not_call_the_network(self):
        def transport(*_args):
            raise AssertionError("network")

        provider = XAIProvider(api_key="", transport=transport)
        with self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(str(caught.exception), "NOT_CONFIGURED")

    def test_rate_limit_retries_and_quota_does_not(self):
        calls = {"n": 0}

        def transport(*_args):
            calls["n"] += 1
            if calls["n"] == 1:
                raise http_error(429, headers={"Retry-After": "1"})
            return openai_payload()

        slept = []
        provider = XAIProvider(api_key="k", transport=transport, sleep=slept.append, retries=2)
        with patch("ai.xai_provider.random.uniform", return_value=0):
            self.assertEqual(provider.generate(request()).status, "OK")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(slept, [1])

        def quota(*_args):
            raise http_error(429, body=b'{"error":{"type":"insufficient_quota","code":"insufficient_quota"}}')

        provider = XAIProvider(api_key="k", transport=quota, sleep=lambda _s: (_ for _ in ()).throw(AssertionError("sleep")))
        with self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "QUOTA_EXHAUSTED")

    def test_tool_call_and_bad_schema_fail_closed(self):
        tool = {"status": "completed", "output": [
            {"type": "x_search_call", "id": "xs"},
            {"type": "message", "content": [{"type": "output_text", "text": "{}"}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        provider = XAIProvider(api_key="k", transport=lambda *_a: tool, sleep=lambda _s: None)
        with self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "INVALID_STRUCTURED_RESPONSE")

        broken = openai_payload()
        body = json.loads(broken["output"][0]["content"][0]["text"])
        body["extra"] = "nope"
        broken["output"][0]["content"][0]["text"] = json.dumps(body)
        provider = XAIProvider(api_key="k", transport=lambda *_a: broken, sleep=lambda _s: None)
        with self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "INVALID_STRUCTURED_RESPONSE")

    def test_input_and_total_token_budgets_fail_closed(self):
        def transport(*_args):
            raise AssertionError("network")

        provider = XAIProvider(api_key="k", transport=transport, max_input_tokens=1)
        with self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "TOKEN_BUDGET_EXCEEDED")

        huge = openai_payload()
        huge["usage"] = {"input_tokens": 9000, "output_tokens": 1, "total_tokens": 9001}
        provider = XAIProvider(api_key="k", transport=lambda *_a: huge, max_total_tokens=1000)
        with self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "TOKEN_BUDGET_EXCEEDED")

    def test_errors_and_logs_do_not_carry_the_secret_or_the_body(self):
        secret = "sk-secret-value"

        def transport(*_args):
            raise http_error(500, body=json.dumps({"error": {"message": secret}}).encode())

        provider = XAIProvider(api_key=secret, transport=transport, sleep=lambda _s: None, retries=0)
        with self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(str(caught.exception), "PROVIDER_ERROR")
        self.assertNotIn(secret, str(caught.exception))
        self.assertNotIn(secret, json.dumps(provider.last_call))

    def test_timeout_is_marked_and_not_retried_past_the_cap(self):
        def transport(*_args):
            raise URLError(TimeoutError("slow"))

        provider = XAIProvider(api_key="k", transport=transport, sleep=lambda _s: None, retries=0)
        with self.assertRaises(XAIProviderError) as caught:
            provider.generate(request())
        self.assertTrue(caught.exception.timed_out)
        self.assertEqual(caught.exception.kind, "CONNECTION_ERROR")


class RuntimeTests(unittest.TestCase):
    def test_runtime_xai_mode_does_not_construct_openai(self):
        path = Path("data/runtime") / f"xai-rt-{uuid4().hex}.db"
        self.addCleanup(path.unlink, missing_ok=True)
        config = RuntimeConfig(db_path=path, ai_provider_mode="xai", market_provider_mode="none",
                               macro_provider_mode="none")
        with patch.dict(os.environ, {"XAI_API_KEY": "test-key"}, clear=False), \
                patch("ai.openai_provider.OpenAIProvider", side_effect=AssertionError("fallback")):
            runtime = OperationalRuntime(config, market_provider=object(), clock=lambda: NOW)
        self.addCleanup(runtime.store.close)
        self.assertIsInstance(runtime.ai_provider, XAIProvider)
        self.assertNotIsInstance(runtime.ai_provider, OpenAIProvider)


if __name__ == "__main__":
    unittest.main()
