"""Per-operation REST limits. Live limits (2026-11-15): candles 2/s, snapshot 2/s, conditions 4/s.

Before this, three candle GETs 100 ms apart made the third one (EURUSD) HTTP 429 REQUEST_RATE_LIMIT,
then the 1 s block failed the account read: M5_GAP candles_unavailable + MANAGE_FAILED BrokerUnavailable.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from execution.exness_brakes import ExnessBrakes
from execution.exness_broker import ExnessBroker
from execution.exness_client import OPERATION_WINDOW_MS, ExnessClient, operation_limit
from execution.exness_transport import RestExnessTransport


ENV = {"AI_FLOOR_BROKER": "exness", "EXN_API_KEY": "exnsk-public-key-value", "EXN_ACCOUNT_ID": "123456",
       "EXN_DISCOVERY_BASE_URL": "https://gateway.example"}
LIMITS = {"candles": 2, "snapshot": 2, "instruments": 2, "conditions": 4, "account": 4}
NOW = datetime(2026, 11, 15, 22, 40, tzinfo=timezone.utc)


class FakeServer:
    """Answers like the live gateway: a sliding 1 s window per operation, HTTP 429 code 3015 past it."""

    def __init__(self, latency_ms=20):
        self.now = [1_000_000]
        self.latency = latency_ms
        self.sent = {}
        self.calls = []

    def sleep(self, seconds):
        self.now[0] += int(round(seconds * 1000))

    def caller(self, method, url, headers, body):
        del headers, body
        self.now[0] += self.latency
        path = url.split("example", 1)[1]
        self.calls.append((method, path))
        if "access-point" in path:
            return 200, {"access_point": "https://ap.example"}, {}
        found = operation_limit(method, path)
        if found is not None:
            name, _ = found
            stamps = [t for t in self.sent.get(name, []) if self.now[0] - t < 1000]
            if len(stamps) >= LIMITS[name]:
                return 429, {"code": 3015, "error_message": "REQUEST_RATE_LIMIT"}, {}
            self.sent[name] = stamps + [self.now[0]]
        if "/candles" in path:
            return 200, {"candles": [{"time": "2026-11-15T22:30:00+00:00", "open": 1.1, "high": 1.2, "low": 1.0,
                                      "close": 1.1, "volume": 3.0}]}, {}
        return 200, {"ok": True}, {}

    def client(self):
        return ExnessClient(ENV, caller=self.caller, sign=lambda data: b"sig", now_ms=lambda: self.now[0],
                            sleep=self.sleep)


class OperationLimitTests(unittest.TestCase):
    def test_paths_map_to_the_published_limits(self):
        cases = {
            "/v1/market-data/accounts/123456/candles?instrument=EURUSD&timeframe=M5": ("candles", 2),
            "/v1/trading/accounts/123456/snapshot": ("snapshot", 2),
            "/v1/configuration/accounts/123456/instruments": ("instruments", 2),
            "/v1/configuration/accounts/123456/instruments/EURUSD/conditions": ("conditions", 4),
            "/v1/configuration/accounts/123456/account": ("account", 4),
            "/v1/configuration/accounts/123456/limits": ("limits", 2),
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(operation_limit("GET", path), expected)
        self.assertIsNone(operation_limit("GET", "/v1/trading/accounts/123456/operations/op-1"))
        self.assertIsNone(operation_limit("GET", "/v1/trading/access-point?account_id=123456"))
        self.assertIsNone(operation_limit("POST", "/v1/trading/accounts/123456/positions"))

    def test_three_candle_gets_wait_for_the_window_and_none_is_rate_limited(self):
        server = FakeServer()
        client = server.client()
        for symbol in ("USDJPY", "GBPUSD", "EURUSD"):
            payload = client.get_json(f"/v1/market-data/accounts/123456/candles?instrument={symbol}")
            self.assertIn("candles", payload)
        candle_calls = [path for method, path in server.calls if "/candles" in path]
        self.assertEqual(len(candle_calls), 3)

    def test_the_third_candle_call_waits_at_least_the_window_after_the_first(self):
        server = FakeServer()
        client = server.client()
        sent_at = []
        inner = server.caller

        def stamp(method, url, headers, body):
            sent_at.append(server.now[0])
            return inner(method, url, headers, body)

        client._caller = stamp
        for symbol in ("USDJPY", "GBPUSD", "EURUSD"):
            client.get_json(f"/v1/market-data/accounts/123456/candles?instrument={symbol}")
        candle_sends = sent_at[1:]  # first send is discovery
        self.assertGreaterEqual(candle_sends[2] - candle_sends[0], OPERATION_WINDOW_MS)
        self.assertLess(candle_sends[1] - candle_sends[0], 200)

    def test_a_manage_style_burst_reads_every_symbol_and_then_the_account(self):
        server = FakeServer()
        client = server.client()
        transport = RestExnessTransport(client)
        broker = ExnessBroker(transport, env=ENV, brakes=ExnessBrakes.from_env(transport, ENV), clock=lambda: NOW)
        start, end = NOW - timedelta(hours=1), NOW
        for symbol in ("USDJPY", "GBPUSD", "EURUSD"):
            with self.assertNoLogs("ai_floor.exness", level="WARNING"):
                bars = broker.candles(symbol, "M5", start, end)
            self.assertEqual([bar.symbol for bar in bars], [symbol])
        client.get_json("/v1/configuration/accounts/123456/account")
        client.get_json("/v1/trading/accounts/123456/snapshot")
        client.get_json("/v1/configuration/accounts/123456/instruments")
        self.assertEqual(len(server.calls), 7)  # discovery + 3 candles + account, snapshot, instruments

    def test_two_back_to_back_account_reads_keep_conditions_under_four_per_second(self):
        server = FakeServer()
        client = server.client()
        for _ in range(2):
            for name in ("EURUSD", "GBPUSD", "USDJPY"):
                self.assertEqual(client.get_json(f"/v1/configuration/accounts/123456/instruments/{name}/conditions"),
                                 {"ok": True})
            self.assertEqual(client.get_json("/v1/trading/accounts/123456/snapshot"), {"ok": True})


if __name__ == "__main__":
    unittest.main()
