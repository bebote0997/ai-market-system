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
from execution.exness_transport import Command, RestExnessTransport


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


class LimiterClockAndLockTests(unittest.TestCase):
    def test_the_window_uses_the_monotonic_clock_and_the_signature_uses_the_wall_clock(self):
        wall = [5_000_000]
        mono = [1_000]
        sent = []

        def caller(method, url, headers, body):
            del method, body
            sent.append((url, headers["EXN-TIMESTAMP"], mono[0]))
            if "access-point" in url:
                return 200, {"access_point": "https://ap.example"}, {}
            return 200, {"candles": []}, {}

        def sleep(seconds):
            mono[0] += int(round(seconds * 1000))
            wall[0] -= 3_600_000  # the wall clock steps back an hour while we wait

        client = ExnessClient(ENV, caller=caller, sign=lambda data: b"sig", now_ms=lambda: wall[0],
                              monotonic_ms=lambda: mono[0], sleep=sleep)
        for symbol in ("USDJPY", "GBPUSD", "EURUSD"):
            client.get_json(f"/v1/market-data/accounts/123456/candles?instrument={symbol}")
        candles = [row for row in sent if "/candles" in row[0]]
        self.assertGreaterEqual(candles[2][2] - candles[0][2], OPERATION_WINDOW_MS)
        self.assertEqual(candles[2][1], str(wall[0]))  # signed with the wall clock at send time

    def test_the_lock_is_free_while_the_limiter_sleeps(self):
        server = FakeServer()
        client = server.client()
        free = []

        def sleep(seconds):
            got = client._lock.acquire(blocking=False)
            free.append(got)
            if got:
                client._lock.release()
            server.sleep(seconds)

        client._sleep = sleep
        for symbol in ("USDJPY", "GBPUSD", "EURUSD"):
            client.get_json(f"/v1/market-data/accounts/123456/candles?instrument={symbol}")
        self.assertTrue(free)
        self.assertTrue(all(free))

    def test_one_sleep_never_exceeds_one_window(self):
        server = FakeServer()
        client = server.client()
        naps = []

        def sleep(seconds):
            naps.append(seconds)
            server.sleep(seconds)

        client._sleep = sleep
        for _ in range(5):
            client.get_json("/v1/market-data/accounts/123456/candles?instrument=EURUSD")
        self.assertTrue(naps)
        self.assertTrue(all(0 < nap <= OPERATION_WINDOW_MS / 1000 for nap in naps))

    def test_an_order_that_gets_429_stays_rejected_and_is_not_resent(self):
        server = FakeServer()
        posts = []

        def caller(method, url, headers, body):
            server.now[0] += 20
            if "access-point" in url:
                return 200, {"access_point": "https://ap.example"}, {}
            posts.append((method, url))
            return 429, {"code": 3015, "error_message": "REQUEST_RATE_LIMIT"}, {}

        client = ExnessClient(ENV, caller=caller, sign=lambda data: b"sig", now_ms=lambda: server.now[0],
                              sleep=server.sleep)
        transport = RestExnessTransport(client)
        command = Command("combo-fix-2026-11-16-GBPUSD", "MARKET", "GBPUSD", side="SHORT",
                          volume=Decimal("0.01"), stop=Decimal("1.3000"))
        first = transport.submit(command)
        second = transport.submit(command)
        self.assertEqual((first.status, first.reason), ("REJECTED", "rate_limited"))
        self.assertEqual((second.status, second.reason), ("REJECTED", "rate_limited"))
        self.assertEqual(len(posts), 1)


if __name__ == "__main__":
    unittest.main()


class WindowFromResponseTests(unittest.TestCase):
    """The server counts a call when it arrives. A slow first connection used to let the third candle GET land
    inside the same server second (live 429s on EURUSD after the send-time window was deployed)."""

    def test_a_slow_first_arrival_does_not_let_the_third_candle_call_hit_429(self):
        now = [1_000_000]
        arrivals = []
        statuses = []
        delays = iter([400, 20, 20, 20])  # send -> arrival: the first call opens a slow connection

        def caller(method, url, headers, body):
            del method, headers, body
            if "access-point" in url:
                now[0] += 20
                return 200, {"access_point": "https://ap.example"}, {}
            now[0] += next(delays)
            recent = [t for t in arrivals if now[0] - t < 1000]
            status = 429 if len(recent) >= 2 else 200
            if status == 200:
                arrivals.append(now[0])
            statuses.append(status)
            now[0] += 20  # arrival -> response
            return status, ({"candles": []} if status == 200 else {"code": 3015}), {}

        def sleep(seconds):
            now[0] += int(round(seconds * 1000))

        client = ExnessClient(ENV, caller=caller, sign=lambda data: b"sig", now_ms=lambda: now[0], sleep=sleep)
        for symbol in ("USDJPY", "GBPUSD", "EURUSD"):
            client.get_json(f"/v1/market-data/accounts/123456/candles?instrument={symbol}")
        self.assertEqual(statuses, [200, 200, 200])
        self.assertGreaterEqual(arrivals[2] - arrivals[0], 1000)

    def test_a_failed_call_still_moves_its_stamp_to_the_end_of_the_call(self):
        now = [1_000_000]

        def caller(method, url, headers, body):
            if "access-point" in url:
                return 200, {"access_point": "https://ap.example"}, {}
            now[0] += 300
            raise OSError("reset")

        def sleep(seconds):
            now[0] += int(round(seconds * 1000))

        client = ExnessClient(ENV, caller=caller, sign=lambda data: b"sig", now_ms=lambda: now[0], sleep=sleep)
        client.discover()
        with self.assertRaises(OSError):
            client.get_json("/v1/market-data/accounts/123456/candles?instrument=EURUSD")
        self.assertEqual(list(client._recent["candles"]), [now[0]])
