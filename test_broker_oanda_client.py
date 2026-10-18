"""OANDA practice client: host guard, retries/backoff, error mapping, redirects, redaction. Fake transport only; the
default urllib transport is patched to fail."""
from decimal import Decimal
import json
import logging
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from execution import oanda_client
from execution.broker_port import (BrokerAuthError, BrokerGuardError, BrokerRateLimited, BrokerRejected, BrokerTimeout,
                                   BrokerUnavailable)
from execution.oanda_client import (PRACTICE_REST_URL, OandaClient, format_price, format_units, mask_account,
                                    round_price)
from execution.oanda_client import urllib_transport as REAL_URLLIB_TRANSPORT

TOKEN = "tok-FAKE-practice-0123abcd"
ACCOUNT = "101-001-12345678-001"
MASKED = "101-…5678-001"
A = f"/accounts/{ACCOUNT}"


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeServer:
    """Routes ``(method, path)`` (path after ``/v3``) to queued responses; the last one is sticky."""

    def __init__(self):
        self.calls, self.routes = [], {}

    def add(self, method, path, *responses):
        self.routes.setdefault((method, path), []).extend(responses)
        return self

    def paths(self, method=None):
        return [path for m, path, _, _ in self.calls if method in (None, m)]

    def count(self, method, path):
        return sum(1 for m, p, _, _ in self.calls if (m, p) == (method, path))

    def __call__(self, method, url, headers, body, timeout):
        assert url.startswith(PRACTICE_REST_URL + "/"), url
        path = url[len(PRACTICE_REST_URL):]
        self.calls.append((method, path, dict(headers), json.loads(body) if body else None))
        queue = self.routes.get((method, path))
        if not queue:
            raise AssertionError(f"unexpected {method} {path}")
        response = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(response, BaseException):
            raise response
        status, payload = response[:2]
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        return status, (response[2] if len(response) > 2 else {}), raw


def raising_transport(*_args, **_kwargs):
    raise AssertionError("network must not be touched")


class OandaTestBase(unittest.TestCase):
    def setUp(self):
        default = patch("execution.oanda_client.urllib_transport", side_effect=raising_transport)
        self.default_transport = default.start()
        self.addCleanup(default.stop)
        self.server, self.clock = FakeServer(), FakeClock()

    def tearDown(self):
        self.default_transport.assert_not_called()

    def client(self, **kwargs):
        options = {"transport": self.server, "clock": self.clock, "sleep": self.clock.sleep}
        options.update(kwargs)
        return OandaClient(TOKEN, ACCOUNT, **options)


class HostGuardTests(OandaTestBase):
    BAD_URLS = ("https://api-fxtrade.oanda.com/v3", "https://api-fxtrade.oanda.com/v3/",
                "https://user:pw@api-fxpractice.oanda.com/v3", "https://api-fxpractice.oanda.com@evil.test/v3",
                "http://api-fxpractice.oanda.com/v3", "https://api-fxpractice.oanda.com./v3",
                "https://api-fxpractice.oanda.com:443/v3", "https://api-fxpractice.oanda.com:8443/v3",
                "https://API-FXPRACTICE.OANDA.COM/v3", "https://api-fxpractice.oanda.com.evil.test/v3",
                "https://evil.test/v3", "https://api-fxpractice.oanda.com/v3?x=1",
                "https://api-fxpractice.oanda.com/v3#f", "https://api-fxpractice.oanda.com/v20",
                "https://stream-fxpractice.oanda.com/v3", "", None)

    def test_only_practice_base_url_accepted(self):
        for url in self.BAD_URLS:
            with self.subTest(url=url), self.assertRaises(BrokerGuardError) as ctx:
                self.client(base_url=url)
            self.assertNotIn("pw", str(ctx.exception))
        for url in (PRACTICE_REST_URL, PRACTICE_REST_URL + "/"):
            self.assertIn("api-fxpractice.oanda.com", repr(self.client(base_url=url)))
        self.assertEqual(self.server.calls, [])

    def test_request_url_check_refuses_tricks(self):
        good = PRACTICE_REST_URL + A + "/summary"
        self.assertTrue(oanda_client._request_url_ok(good))
        for url in ("https://api-fxtrade.oanda.com/v3/accounts/x", "https://api-fxpractice.oanda.com:443/v3/x",
                    "https://api-fxpractice.oanda.com./v3/x", "https://u@api-fxpractice.oanda.com/v3/x",
                    "http://api-fxpractice.oanda.com/v3/x", "https://api-fxpractice.oanda.com/v2/x", None):
            with self.subTest(url=url):
                self.assertFalse(oanda_client._request_url_ok(url))

    def test_live_account_and_bad_token_refused(self):
        for account in ("001-001-12345678-001", "101-001-12345678", "101-01-1-001", "", None, " " + ACCOUNT):
            with self.subTest(account=account), self.assertRaises(BrokerGuardError):
                OandaClient(TOKEN, account, transport=self.server)
        for token in ("", "   ", None, "abc def", "abc\r\nX-Evil: 1"):
            with self.subTest(token=token), self.assertRaises(BrokerGuardError) as ctx:
                OandaClient(token, ACCOUNT, transport=self.server)
            self.assertNotIn("Evil", str(ctx.exception))

    def test_instruments_other_than_eur_usd_refused_before_network(self):
        client = self.client()
        for instrument in ("XAU_USD", "NAS100_USD", "EURUSD", "eur_usd"):
            with self.subTest(instrument=instrument), self.assertRaises(BrokerGuardError):
                client.get_pricing(instrument)
        with self.assertRaises(BrokerGuardError):
            client.close_position("XAU_USD", "LONG")
        self.assertEqual(self.server.calls, [])


class EndpointTests(OandaTestBase):
    def test_paths_headers_and_bodies(self):
        s = self.server
        s.add("GET", f"{A}/summary", (200, {"account": {}}))
        s.add("GET", f"{A}/pricing?instruments=EUR_USD", (200, {"prices": []}))
        s.add("GET", f"{A}/instruments?instruments=EUR_USD", (200, {"instruments": []}))
        s.add("GET", f"{A}/openTrades", (200, {"trades": []}))
        s.add("GET", f"{A}/openPositions", (200, {"positions": []}))
        s.add("GET", f"{A}/trades/@cid-1", (200, {"trade": {}}))
        s.add("PUT", f"{A}/trades/42/orders", (200, {}))
        s.add("PUT", f"{A}/trades/42/close", (200, {}))
        s.add("PUT", f"{A}/positions/EUR_USD/close", (200, {}))
        client = self.client()
        client.get_summary()
        client.get_pricing()
        client.get_instruments()
        client.get_open_trades()
        client.get_open_positions()
        client.get_trade("@cid-1")
        client.replace_stop_loss("42", Decimal("1.0805"))
        client.close_trade("42")
        client.close_position("EUR_USD", "SHORT")
        bodies = [c[3] for c in s.calls if c[0] == "PUT"]
        self.assertEqual(bodies, [{"stopLoss": {"price": "1.08050", "timeInForce": "GTC"}}, {"units": "ALL"},
                                  {"shortUnits": "ALL"}])
        headers = s.calls[0][2]
        self.assertEqual(headers["Authorization"], f"Bearer {TOKEN}")
        self.assertIsNotNone(client.last_ok_at)
        for bad in ("abc", "@", "@a/b", "1/close", "@" + "x" * 129, ""):
            with self.subTest(specifier=bad), self.assertRaises(ValueError):
                client.get_trade(bad)

    def test_transactions_since_is_a_read_only_get(self):
        path = f"{A}/transactions/sinceid?id=100&type=TRANSFER_FUNDS"
        self.server.add("GET", path, (200, {"transactions": [], "lastTransactionID": "100"}))
        client = self.client()
        self.assertEqual(client.get_transactions_since("100"), {"transactions": [], "lastTransactionID": "100"})
        self.assertEqual([(m, p) for m, p, _h, _b in self.server.calls], [("GET", path)])
        for tx_id, types in (("abc", ("TRANSFER_FUNDS",)), ("1&type=ORDER_FILL", ("TRANSFER_FUNDS",)), (100, None),
                             ("", ("TRANSFER_FUNDS",)), ("1", ()), ("1", "TRANSFER_FUNDS"), ("1", ("x&id=2",))):
            with self.subTest(tx_id=tx_id, types=types), self.assertRaises(ValueError):
                client.get_transactions_since(tx_id, types=types or ())
        self.assertEqual(len(self.server.calls), 1)
        self.server.add("GET", f"{A}/transactions/sinceid?id=101&type=TRANSFER_FUNDS", (404, {"errorMessage": TOKEN}))
        with self.assertRaises(BrokerRejected) as ctx:
            client.get_transactions_since("101")
        self.assertNotIn(ACCOUNT, str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_build_market_order_and_formatting(self):
        ext = {"id": "cid-1", "tag": "aifloor", "comment": "run:r1"}
        order = OandaClient.build_market_order(instrument="EUR_USD", units=-100, stop=Decimal("1.1"),
                                               target=Decimal("1.050004"), client_extensions=ext,
                                               trade_client_extensions=ext)
        self.assertEqual(order, {"type": "MARKET", "instrument": "EUR_USD", "units": "-100", "timeInForce": "FOK",
                                 "positionFill": "DEFAULT",
                                 "stopLossOnFill": {"price": "1.10000", "timeInForce": "GTC"},
                                 "takeProfitOnFill": {"price": "1.05000", "timeInForce": "GTC"},
                                 "clientExtensions": ext, "tradeClientExtensions": ext})
        self.assertEqual(format_price(Decimal("1.085005")), "1.08501")  # ROUND_HALF_UP
        self.assertEqual(round_price(Decimal("1.0850049")), Decimal("1.08500"))
        for bad in (0, 1.5, True, "1"):
            with self.subTest(units=bad), self.assertRaises(ValueError):
                format_units(bad)
        for bad in (Decimal("0"), Decimal("-1"), Decimal("NaN"), 1.1):
            with self.subTest(price=bad), self.assertRaises(ValueError):
                format_price(bad)

    def test_rate_limiter_spaces_requests(self):
        self.server.add("GET", f"{A}/openTrades", (200, {"trades": []}))
        self.server.add("PUT", f"{A}/trades/1/close", (200, {}))
        client = self.client()
        client.get_open_trades()
        client.get_open_trades()
        client.close_trade("1")
        client.close_trade("1")
        self.assertEqual([round(s, 6) for s in self.clock.sleeps], [0.1, 0.5])


class RetryAndErrorTests(OandaTestBase):
    def test_429_bounded_backoff(self):
        self.server.add("GET", f"{A}/openTrades", (429, {}))
        with self.assertRaises(BrokerRateLimited):
            self.client().get_open_trades()
        self.assertEqual(self.server.count("GET", f"{A}/openTrades"), 4)
        self.assertEqual([s for s in self.clock.sleeps if s >= 1.0], [1.0, 2.0, 4.0])

    def test_429_retry_after_honoured_and_capped(self):
        self.server.add("GET", f"{A}/summary", (429, {}, {"Retry-After": "3"}), (429, {}, {"retry-after": "100"}),
                        (200, {"account": {}}))
        self.client().get_summary()
        self.assertEqual([s for s in self.clock.sleeps if s >= 1.0], [3.0, 8.0])

    def test_5xx_on_reads_retried_bounded(self):
        self.server.add("GET", f"{A}/openTrades", (502, {}))
        with self.assertRaises(BrokerUnavailable):
            self.client().get_open_trades()
        self.assertEqual(self.server.count("GET", f"{A}/openTrades"), 3)  # 1 + 2 retries
        server = FakeServer().add("GET", f"{A}/openTrades", (503, {}), (200, {"trades": []}))
        self.assertEqual(self.client(transport=server).get_open_trades(), {"trades": []})

    def test_trading_requests_never_retried_on_5xx_or_timeout(self):
        cases = [((500, {}), BrokerUnavailable), ((503, {}), BrokerUnavailable), (TimeoutError("slow"), BrokerTimeout),
                 (OSError("reset"), BrokerUnavailable), ((200, b"not json"), BrokerUnavailable)]
        for response, error in cases:
            server = FakeServer().add("POST", f"{A}/orders", response).add("PUT", f"{A}/trades/1/close", response)
            client = self.client(transport=server)
            with self.subTest(response=response):
                with self.assertRaises(error):
                    client.create_order({"type": "MARKET"})
                with self.assertRaises(error):
                    client.close_trade("1")
                self.assertEqual(len(server.calls), 2)
                self.assertEqual(client.last_error, error.__name__)

    def test_pre_send_guard_runs_before_every_trading_attempt_only(self):
        events = []

        def guard():
            events.append(("guard", len(self.server.calls)))

        def sleep(seconds):
            events.append(("sleep", seconds))
            self.clock.sleep(seconds)

        self.server.add("POST", f"{A}/orders", (429, {}), (201, {}))
        self.server.add("GET", f"{A}/summary", (429, {}), (200, {"account": {}}))
        client = self.client(pre_send_guard=guard, sleep=sleep)
        client.create_order({"type": "MARKET"})
        self.assertEqual([e for e in events if e[0] == "guard"], [("guard", 0), ("guard", 1)])
        self.assertLess(events.index(("sleep", 1.0)), events.index(("guard", 1)))  # after the 429 backoff
        events.clear()
        client.get_summary()
        self.assertNotIn("guard", [e[0] for e in events])

    def test_pre_send_guard_refusal_sends_nothing(self):
        def refuse():
            raise BrokerGuardError("switch OFF")

        self.server.add("POST", f"{A}/orders", (201, {}))
        self.server.add("PUT", f"{A}/trades/1/orders", (200, {}))
        client = self.client(pre_send_guard=refuse)
        with self.assertRaises(BrokerGuardError):
            client.create_order({"type": "MARKET"})
        with self.assertRaises(BrokerGuardError):
            client.replace_stop_loss("1", Decimal("1.08"))
        self.assertEqual((self.server.calls, client.last_error), ([], "BrokerGuardError"))

        calls = iter([None, BrokerGuardError("emergency")])

        def second_attempt_refused():
            error = next(calls)
            if error:
                raise error

        server = FakeServer().add("POST", f"{A}/orders", (429, {}), (201, {}))
        with self.assertRaises(BrokerGuardError):
            self.client(transport=server, pre_send_guard=second_attempt_refused).create_order({"type": "MARKET"})
        self.assertEqual(len(server.calls), 1)

    def test_auth_and_rejections(self):
        for status in (401, 403):
            server = FakeServer().add("GET", f"{A}/summary", (status, {"errorMessage": "Insufficient authorization"}))
            with self.subTest(status=status), self.assertRaises(BrokerAuthError):
                self.client(transport=server).get_summary()
            self.assertEqual(len(server.calls), 1)
        server = FakeServer().add("POST", f"{A}/orders",
                                  (400, {"errorCode": "INSUFFICIENT_MARGIN", "errorMessage": f"text {TOKEN}"}),
                                  (404, {"orderRejectTransaction": {"rejectReason": "MARKET_HALTED"}}),
                                  (400, {"errorCode": "free text <b>", "errorMessage": "x"}))
        client = self.client(transport=server)
        for expected in ("INSUFFICIENT_MARGIN", "MARKET_HALTED", None):
            with self.assertRaises(BrokerRejected) as ctx:
                client.create_order({})
            self.assertEqual(ctx.exception.code, expected)
            self.assertNotIn(TOKEN, str(ctx.exception))
        self.assertEqual(ctx.exception.status, 400)

    def test_redirect_is_refused_not_followed(self):
        self.server.add("GET", f"{A}/summary", (302, {}, {"Location": "https://api-fxtrade.oanda.com/v3/x"}))
        with self.assertRaises(BrokerUnavailable):
            self.client().get_summary()
        self.assertEqual(len(self.server.calls), 1)


class DefaultTransportTests(OandaTestBase):
    def test_default_transport_refuses_non_practice_urls_before_opening(self):
        with patch("execution.oanda_client._opener", side_effect=AssertionError("no network")) as opener:
            for url in ("https://api-fxtrade.oanda.com/v3/accounts", "http://api-fxpractice.oanda.com/v3/x"):
                with self.subTest(url=url), self.assertRaises(BrokerGuardError):
                    REAL_URLLIB_TRANSPORT("GET", url, {}, None, 1.0)
            opener.assert_not_called()

    def test_default_transport_does_not_follow_redirects(self):
        handler = oanda_client._refuse_redirects()()
        self.assertIsNone(handler.redirect_request(None, None, 302, "Found", {}, "https://api-fxtrade.oanda.com/"))
        opener = oanda_client._opener()  # building an opener does not touch the network
        self.assertTrue(any(type(h).__name__ == "NoRedirectHandler" for h in opener.handlers))

        class FakeOpener:
            def open(self, request, timeout):
                raise HTTPError(request.full_url, 302, "Found", {"Location": "https://evil.test/"}, None)

        with patch("execution.oanda_client._opener", return_value=FakeOpener()):
            status, _headers, body = REAL_URLLIB_TRANSPORT("GET", PRACTICE_REST_URL + A + "/summary", {}, None, 1.0)
        self.assertEqual((status, body), (302, b""))

    def test_constructor_uses_patched_default_transport(self):
        client = OandaClient(TOKEN, ACCOUNT, clock=self.clock, sleep=self.clock.sleep)
        with self.assertRaises(AssertionError):
            client.get_summary()
        self.assertEqual(self.default_transport.call_count, 1)
        self.default_transport.reset_mock()


class RedactionTests(OandaTestBase):
    def test_token_and_account_never_leak(self):
        self.assertEqual(mask_account(ACCOUNT), MASKED)
        self.assertEqual(mask_account(""), "")
        self.server.add("GET", f"{A}/openTrades", (429, {}), (502, {}), (200, {"trades": []}))
        self.server.add("POST", f"{A}/orders", TimeoutError("slow"))
        client = self.client()
        errors = []
        with self.assertLogs("ai_floor.broker", level="DEBUG") as logs:
            client.get_open_trades()
            try:
                client.create_order({})
            except BrokerTimeout as error:
                errors.append(str(error))
        text = "\n".join([*logs.output, *errors, repr(client), str(client), client.scrub(f"{TOKEN} {ACCOUNT}")])
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(ACCOUNT, text)
        self.assertIn(MASKED, text)


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
