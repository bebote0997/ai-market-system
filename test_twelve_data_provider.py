"""Offline Twelve Data adapter and active-provider certification checks."""
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
import unittest
from unittest.mock import patch

import pandas as pd

from data.twelve_data_provider import TwelveDataMarketDataProvider, TwelveDataProviderError
from runtime.certify_providers import certify, certify_twelve_data
from runtime.env import load_local_env


NOW = datetime(2026, 9, 17, 17, 30, tzinfo=timezone.utc)
DURATIONS = {"5min": timedelta(minutes=5), "15min": timedelta(minutes=15), "1h": timedelta(hours=1)}


def response(url, timeout):
    query = parse_qs(urlparse(url).query)
    symbol, interval = query["symbol"][0], query["interval"][0]
    duration = DURATIONS[interval]
    closed = NOW - duration
    def bar(at):
        return {"datetime": at.strftime("%Y-%m-%d %H:%M:%S"),
                "open": "100", "high": "101", "low": "99", "close": "100.5"}
    return {"status": "ok", "meta": {"symbol": symbol, "interval": interval},
            "values": [bar(NOW), bar(closed)]}


class TwelveDataTests(unittest.TestCase):
    def test_local_env_loads_key_name_without_logging_value(self):
        with patch.object(Path, "is_symlink", return_value=False), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(Path, "read_text", return_value=(
                 "TWELVE_DATA_API_KEY=dummy-key\nTWELVE_DATA_TIMEOUT_SECONDS=12\n")), \
             patch.dict(os.environ, {}, clear=True):
            load_local_env(".env.local")
            self.assertEqual(os.environ["TWELVE_DATA_API_KEY"], "dummy-key")
            self.assertEqual(os.environ["TWELVE_DATA_TIMEOUT_SECONDS"], "12")

    def test_canonical_symbols_closed_bars_utc_and_no_fabricated_volume(self):
        urls = []
        def transport(url, timeout):
            urls.append(url)
            return response(url, timeout)
        provider = TwelveDataMarketDataProvider(api_key="test-secret", transport=transport)
        gold = provider.load_snapshot("XAUUSD", NOW)
        euro = provider.load_snapshot("EURUSD", NOW)
        self.assertEqual(len(urls), 6)
        self.assertEqual({parse_qs(urlparse(u).query)["symbol"][0] for u in urls}, {"XAU/USD", "EUR/USD"})
        self.assertTrue(all(parse_qs(urlparse(u).query)["timezone"] == ["UTC"] for u in urls))
        self.assertTrue(all(frame.index.tz is not None and frame.iloc[-1]["is_closed"]
                            for frame in (*gold.values(), *euro.values())))
        self.assertTrue(all(frame.iloc[-1]["volume"] is None for frame in gold.values()))
        self.assertTrue(all(frame.index.max().to_pydatetime() < NOW for frame in gold.values()))

    def test_cache_reuses_hour_until_next_close_and_returns_copy(self):
        calls = []
        def transport(url, timeout):
            calls.append(url)
            return response(url, timeout)
        provider = TwelveDataMarketDataProvider(api_key="dummy", transport=transport)
        first = provider._bars("EURUSD", "1h", NOW)
        first.iloc[0, first.columns.get_loc("Close")] = 777
        second = provider._bars("EURUSD", "1h", NOW + timedelta(minutes=15))
        self.assertEqual(len(calls), 1)
        self.assertEqual(second.iloc[-1]["Close"], 100.5)

    def test_invalid_ohlc_and_symbol_fail_closed(self):
        def invalid(url, timeout):
            data = response(url, timeout)
            data["values"][1]["high"] = "90"
            return data
        provider = TwelveDataMarketDataProvider(api_key="dummy", transport=invalid)
        with self.assertRaises(TwelveDataProviderError) as caught:
            provider._bars("XAUUSD", "5m", NOW)
        self.assertEqual(caught.exception.kind, "INVALID_BAR")
        self.assertEqual(provider.health_by_asset["METAL"], "ERROR")
        def mismatch(url, timeout):
            data = response(url, timeout)
            data["meta"]["symbol"] = "OTHER"
            return data
        provider = TwelveDataMarketDataProvider(api_key="dummy", transport=mismatch)
        with self.assertRaises(TwelveDataProviderError):
            provider._bars("EURUSD", "5m", NOW)

    def test_rate_limit_retry_after_is_bounded_and_secret_free(self):
        waits, calls = [], []
        def limited(url, timeout):
            calls.append(1)
            raise HTTPError(url, 429, "private", {"Retry-After": "2"}, io.BytesIO(b"private"))
        provider = TwelveDataMarketDataProvider(api_key="test-secret", retries=1,
                                                 transport=limited, sleep=waits.append)
        with self.assertRaises(TwelveDataProviderError) as caught:
            provider._bars("EURUSD", "5m", NOW)
        self.assertEqual(caught.exception.kind, "RATE_LIMITED")
        self.assertEqual(caught.exception.retry_after, 2)
        self.assertEqual(len(calls), 2)
        self.assertGreaterEqual(waits[0], 2)
        self.assertNotIn("test-secret", str(caught.exception))

    def test_entitlement_body_error_and_stale_are_explicit(self):
        provider = TwelveDataMarketDataProvider(
            api_key="dummy", transport=lambda *_: {"status": "error", "code": 403,
                                                       "message": "private"})
        with self.assertRaises(TwelveDataProviderError) as caught:
            provider._bars("XAUUSD", "5m", NOW)
        self.assertEqual(caught.exception.kind, "ENTITLEMENT_ERROR")
        self.assertNotIn("private", str(caught.exception))
        def stale(url, timeout):
            data = response(url, timeout)
            data["values"] = [data["values"][-1]]
            data["values"][0]["datetime"] = (NOW - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            return data
        provider = TwelveDataMarketDataProvider(api_key="dummy", transport=stale)
        provider._bars("EURUSD", "5m", NOW)
        self.assertEqual(provider.health_by_asset["FOREX"], "STALE")

    def test_certification_does_not_call_inactive_massive_or_nas100(self):
        calls = []
        class FakeTwelve:
            api_key = "dummy"
            health_by_asset = {"METAL": "READY", "FOREX": "READY"}
            def __init__(self, **kwargs):
                pass
            def _bars(self, symbol, timeframe, now):
                calls.append((symbol, timeframe))
                duration = {"5m": 5, "15m": 15, "1h": 60}[timeframe]
                stamp = now - timedelta(minutes=duration)
                return pd.DataFrame({"Open": [100.0], "High": [101.0], "Low": [99.0], "Close": [100.5],
                                     "symbol": [symbol], "is_closed": [True]},
                                    index=pd.DatetimeIndex([stamp]))
        with patch("runtime.certify_providers.TwelveDataMarketDataProvider", FakeTwelve), \
             patch("runtime.certify_providers.certify_massive", side_effect=AssertionError("Massive called")):
            result = certify_twelve_data(("XAUUSD", "EURUSD"))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(len(calls), 6)
        self.assertEqual(result["symbols"]["NAS100"]["status"], "NOT_ENABLED")
        self.assertFalse(any(symbol == "NAS100" for symbol, _ in calls))

    def test_default_certification_pass_ignores_inactive_massive(self):
        with patch("runtime.certify_providers.load_local_env"), \
             patch("runtime.certify_providers.certify_openai", return_value={"status": "PASS"}), \
             patch("runtime.certify_providers.certify_twelve_data", return_value={"status": "PASS"}), \
             patch("runtime.certify_providers.certify_massive", side_effect=AssertionError("Massive called")):
            result = certify(only="all")
        self.assertEqual(result["overall"], "PASS")
        self.assertEqual(result["massive"]["status"], "NOT_ACTIVE")


if __name__ == "__main__":
    unittest.main()
