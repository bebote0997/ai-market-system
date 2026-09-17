"""Offline contract and fail-closed checks for the real provider adapters."""
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError
import json
import io
from pathlib import Path
from types import SimpleNamespace
import unittest
from uuid import uuid4
import pandas as pd

from ai.contracts import AIRequest, AIResponse
from ai.openai_provider import OpenAIProvider, OpenAIProviderError
from ai.runtime import call_agent
from data.massive_provider import MassiveMarketDataProvider, MassiveProviderError
from runtime.config import RuntimeConfig
from runtime.certify_providers import certify, certify_openai, certify_massive, _fx_market_state
from runtime.retry_after import retry_after_seconds
from runtime.service import OperationalRuntime
from unittest.mock import patch


NOW = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)


def request():
    return AIRequest("1.0", "run-1", NOW, "XAUUSD", "structure_ai", "structure_specialist",
                     {}, ({"evidence_id": "e1", "bias": "BULLISH"},),
                     ("interpret_structure",), {}, "1.0")


def openai_payload(**changes):
    data = {
        "schema_version": "1.0", "run_id": "run-1", "as_of": NOW.isoformat(),
        "symbol": "XAUUSD", "agent_name": "structure_ai", "status": "OK",
        "bias": "BULLISH", "confidence": 0.6, "recommendation": None,
        "observations": ["evidence e1"], "supporting_evidence": ["e1"],
        "conflicting_evidence": [], "risks": [], "invalidation_conditions": [],
        "warnings": [], "reasoning_summary": "Evidence e1 is bullish.",
    }
    data.update(changes)
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(data)}]}],
        "usage": {"input_tokens": 20, "output_tokens": 10, "total_tokens": 30}}


def massive_payload(url, key, timeout):
    ticker = "I:NDX" if "I:NDX" in url else "C:XAUUSD"
    minutes = 60 if "/1/hour/" in url else 15 if "/15/minute/" in url else 5
    stamp = NOW - timedelta(minutes=minutes)
    return {"status": "OK", "ticker": ticker,
            "results": [{"t": int(stamp.timestamp() * 1000),
                         "o": 100, "h": 101, "l": 99, "c": 100.5}]}


class OpenAITests(unittest.TestCase):
    def test_certification_accepts_grounded_no_data(self):
        class FakeOpenAI:
            api_key = "dummy"
            model = "configured-model"
            last_usage = {"total_tokens": 7}
            def __init__(self, **kwargs):
                pass
            def generate(self, req):
                return AIResponse("1.0", req.run_id, req.as_of, req.symbol, req.agent_name,
                                  "NO_DATA", reasoning_summary="No usable evidence")
        with patch("runtime.certify_providers.OpenAIProvider", FakeOpenAI):
            result = certify_openai()
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["response_status"], "NO_DATA")
        self.assertEqual(result["contract"], "ok")

    def test_certification_rejects_error_status_with_valid_identity(self):
        class FakeOpenAI:
            api_key = "dummy"
            model = "configured-model"
            last_usage = {"total_tokens": 7}
            def __init__(self, **kwargs):
                pass
            def generate(self, req):
                return AIResponse("1.0", req.run_id, req.as_of, req.symbol, req.agent_name, "ERROR")
        with patch("runtime.certify_providers.OpenAIProvider", FakeOpenAI):
            result = certify_openai()
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["response_status"], "ERROR")

    def test_structured_grounded_response_and_safe_metadata(self):
        seen = []
        def transport(payload, key, timeout):
            seen.append(payload)
            return openai_payload()
        provider = OpenAIProvider(api_key="test-secret", transport=transport)
        response = call_agent(provider, request())
        self.assertEqual(response.status, "OK")
        self.assertEqual(response.model_metadata["total_tokens"], 30)
        self.assertTrue(seen[0]["text"]["format"]["strict"])
        identity = seen[0]["text"]["format"]["schema"]["properties"]
        self.assertEqual(identity["agent_name"]["enum"], ["structure_ai"])
        self.assertEqual(identity["run_id"]["enum"], ["run-1"])
        self.assertEqual(identity["supporting_evidence"]["items"]["enum"], ["e1"])
        self.assertEqual(identity["conflicting_evidence"]["items"]["enum"], ["e1"])
        self.assertIn(None, identity["recommendation"]["enum"])
        self.assertIn("ACCEPT", identity["recommendation"]["enum"])
        self.assertFalse(seen[0]["store"])
        self.assertNotIn("test-secret", json.dumps(seen))

    def test_grounding_mismatch_blocks(self):
        provider = OpenAIProvider(api_key="dummy", transport=lambda *_: openai_payload(run_id="other"))
        self.assertEqual(call_agent(provider, request()).status, "ERROR")

    def test_malformed_output_fails_closed(self):
        provider = OpenAIProvider(api_key="dummy", transport=lambda *_: {"status": "completed", "output": []})
        self.assertEqual(call_agent(provider, request()).status, "ERROR")

    def test_rate_limit_is_bounded(self):
        attempts = []
        def transport(*_):
            attempts.append(1)
            raise HTTPError("https://api.openai.com/v1/responses", 429, "limit", {}, None)
        provider = OpenAIProvider(api_key="dummy", retries=2, transport=transport, sleep=lambda _: None)
        with self.assertRaises(OpenAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "RATE_LIMITED")
        self.assertEqual(len(attempts), 3)

    def test_quota_error_is_classified_without_retries_or_response_body(self):
        attempts = []
        def transport(*_):
            attempts.append(1)
            body = io.BytesIO(b'{"error":{"code":"insufficient_quota","message":"private detail"}}')
            raise HTTPError("https://api.openai.com/v1/responses", 429, "quota", {}, body)
        provider = OpenAIProvider(api_key="dummy", retries=2, transport=transport, sleep=lambda _: None)
        with self.assertRaises(OpenAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "QUOTA_EXHAUSTED")
        self.assertNotIn("private detail", str(caught.exception))
        self.assertEqual(len(attempts), 1)

    def test_credit_balance_exhausted_is_quota_not_rate_limit(self):
        attempts = []
        def transport(*_):
            attempts.append(1)
            body = io.BytesIO(b'{"error":{"type":"insufficient_quota","code":"credit_balance_exhausted","message":"private detail"}}')
            raise HTTPError("https://api.openai.com/v1/responses", 429, "quota", {}, body)
        provider = OpenAIProvider(api_key="dummy", retries=2, transport=transport, sleep=lambda _: None)
        with self.assertRaises(OpenAIProviderError) as caught:
            provider.generate(request())
        self.assertEqual(caught.exception.kind, "QUOTA_EXHAUSTED")
        self.assertEqual(caught.exception.http_status, 429)
        self.assertEqual(caught.exception.error_code, "credit_balance_exhausted")
        self.assertEqual(len(attempts), 1)


class MassiveTests(unittest.TestCase):
    def test_massive_only_certification_never_contacts_openai(self):
        with patch("runtime.certify_providers.load_local_env"), \
             patch("runtime.certify_providers.certify_massive", return_value={"status": "PASS"}) as market, \
             patch("runtime.certify_providers.certify_openai", side_effect=AssertionError("OpenAI contacted")):
            result = certify(only="massive")
        self.assertEqual(result["scope"], "massive")
        self.assertNotIn("openai", result)
        self.assertEqual(result["overall"], "PASS")
        market.assert_called_once_with(("XAUUSD", "EURUSD"))

    def test_certifier_skips_supported_but_disabled_nas100(self):
        with patch.dict("os.environ", {"MASSIVE_API_KEY": ""}):
            result = certify_massive(("XAUUSD", "EURUSD"))
        self.assertEqual(result["status"], "NOT_CONFIGURED")
        self.assertEqual(result["symbols"]["NAS100"]["status"], "NOT_ENABLED")
        self.assertFalse(result["symbols"]["NAS100"]["certified"])
        self.assertEqual(result["symbols"]["XAUUSD"]["status"], "NOT_CONFIGURED")

    def test_two_symbol_pass_does_not_query_nas100(self):
        calls = []
        class FakeMassive:
            api_key = "dummy"
            health_by_asset = {"METAL": "READY", "FOREX": "READY", "INDEX": "NOT_CONFIGURED"}
            def __init__(self, **kwargs):
                self.delayed_symbols = set()
            def _bars(self, symbol, timeframe, now):
                calls.append(symbol)
                return pd.DataFrame({"Close": [100]}, index=pd.DatetimeIndex([now - timedelta(minutes=5)]))
        with patch("runtime.certify_providers.MassiveMarketDataProvider", FakeMassive), \
             patch("runtime.certify_providers.fresh_snapshot", return_value=(True, "CURRENT", None)):
            result = certify_massive(("XAUUSD", "EURUSD"))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["symbols"]["NAS100"]["status"], "NOT_ENABLED")
        self.assertFalse(any("I:NDX" in call or call == "NAS100" for call in calls))

    def test_fx_market_state_requires_current_server_time(self):
        provider = SimpleNamespace(_get=lambda *_: {"currencies": {"fx": "closed"},
                                                  "serverTime": NOW.isoformat()})
        self.assertEqual(_fx_market_state(provider, NOW), "CLOSED")
        self.assertEqual(_fx_market_state(provider, NOW + timedelta(minutes=3)), "UNKNOWN")

    def test_closed_market_stale_bars_are_explicit_and_fail_closed(self):
        class FakeMassive:
            api_key = "dummy"
            health_by_asset = {"METAL": "NOT_CONFIGURED", "FOREX": "READY", "INDEX": "NOT_CONFIGURED"}
            delayed_symbols = set()
            def __init__(self, **kwargs):
                pass
            def _bars(self, symbol, timeframe, now):
                return pd.DataFrame({"Close": [100]}, index=pd.DatetimeIndex([now - timedelta(hours=3)]))
        with patch("runtime.certify_providers.MassiveMarketDataProvider", FakeMassive), \
             patch("runtime.certify_providers._fx_market_state", return_value="CLOSED"), \
             patch("runtime.certify_providers.fresh_snapshot", return_value=(False, "STALE_DATA", None)):
            result = certify_massive(("EURUSD",))
        self.assertEqual(result["symbols"]["EURUSD"]["freshness"], "MARKET_CLOSED")
        self.assertFalse(result["symbols"]["EURUSD"]["certified"])

    def test_retry_after_and_bounded_massive_retry(self):
        self.assertEqual(retry_after_seconds({"Retry-After": "3"}), 3)
        self.assertIsNone(retry_after_seconds({"Retry-After": "invalid"}))
        attempts, waits = [], []
        def transport(url, key, timeout):
            attempts.append(1)
            raise HTTPError(url, 429, "limit", {"Retry-After": "2"}, None)
        provider = MassiveMarketDataProvider(api_key="dummy", retries=1, transport=transport, sleep=waits.append)
        with self.assertRaises(MassiveProviderError) as caught:
            provider._get("https://api.massive.com/test", "FOREX")
        self.assertEqual(caught.exception.retry_after, 2)
        self.assertEqual(len(attempts), 2)
        self.assertGreaterEqual(waits[0], 2)

    def test_runtime_default_disables_nas100_but_preserves_catalog(self):
        from runtime.config import SUPPORTED_SYMBOLS
        self.assertEqual(RuntimeConfig().enabled_symbols, ("XAUUSD", "EURUSD"))
        self.assertIn("NAS100", SUPPORTED_SYMBOLS)
        with self.assertRaisesRegex(ValueError, "enabled_symbols"):
            RuntimeConfig(enabled_symbols=("XAUUSD", "BOGUS"))

    def test_closed_utc_bars_for_all_timeframes(self):
        provider = MassiveMarketDataProvider(api_key="dummy", transport=massive_payload)
        snapshot = provider.load_snapshot("XAUUSD", NOW)
        self.assertEqual(set(snapshot), {"1h", "15m", "5m"})
        self.assertTrue(all(frame.iloc[-1]["is_closed"] for frame in snapshot.values()))
        self.assertTrue(all(str(frame.index.tz) == "UTC" for frame in snapshot.values()))
        self.assertEqual(snapshot["5m"].iloc[-1]["provider_symbol"], "C:XAUUSD")

    def test_forming_bar_is_filtered(self):
        def transport(url, key, timeout):
            result = massive_payload(url, key, timeout)
            result["results"].append({"t": int(NOW.timestamp() * 1000),
                                      "o": 101, "h": 102, "l": 100, "c": 101})
            return result
        provider = MassiveMarketDataProvider(api_key="dummy", transport=transport)
        self.assertEqual(len(provider.load_snapshot("NAS100", NOW)["5m"]), 1)

    def test_invalid_ohlc_is_rejected(self):
        def transport(url, key, timeout):
            result = massive_payload(url, key, timeout)
            result["results"][0]["h"] = 90
            return result
        provider = MassiveMarketDataProvider(api_key="dummy", transport=transport)
        with self.assertRaises(MassiveProviderError):
            provider.load_snapshot("XAUUSD", NOW)

    def test_entitlement_is_explicit(self):
        def transport(url, key, timeout):
            raise HTTPError(url, 403, "forbidden", {}, None)
        provider = MassiveMarketDataProvider(api_key="dummy", transport=transport)
        with self.assertRaises(MassiveProviderError) as caught:
            provider.load_snapshot("NAS100", NOW)
        self.assertEqual(caught.exception.kind, "ENTITLEMENT_ERROR")
        self.assertEqual(provider.health_by_asset["INDEX"], "ENTITLEMENT_ERROR")

    def test_missing_key_is_explicit(self):
        provider = MassiveMarketDataProvider(api_key="", transport=massive_payload)
        with self.assertRaises(MassiveProviderError) as caught:
            provider.load_snapshot("XAUUSD", NOW)
        self.assertEqual(caught.exception.kind, "NOT_CONFIGURED")

    def test_delayed_entitlement_blocks_paper_even_with_recent_timestamps(self):
        def transport(url, key, timeout):
            result = massive_payload(url, key, timeout)
            result["status"] = "DELAYED"
            return result
        provider = MassiveMarketDataProvider(api_key="dummy", transport=transport)
        path = Path("data/runtime") / f"phase6d-{uuid4().hex}.db"
        runtime = OperationalRuntime(RuntimeConfig(db_path=path, enabled_symbols=("XAUUSD",)),
                                     market_provider=provider, clock=lambda: NOW)
        try:
            self.assertEqual(runtime.run_cycle("XAUUSD", NOW), "STALE_DATA")
            self.assertEqual(runtime.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
        finally:
            runtime.close()
            for suffix in ("", "-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
