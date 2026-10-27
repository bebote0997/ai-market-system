"""Read-only OANDA balance client (dashboard Demo/Real VIEW): GET summary only, host allow-list, account prefixes,
token isolation, statuses, error codes, redaction, cache. Fake transport only; the default transport is patched."""
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import re
import time
import unittest
from unittest.mock import Mock, patch

from execution import oanda_readonly as ro
from execution.oanda_readonly import (CachedAccountViews, OandaReadOnlyClient, ReadOnlyError, account_view)

ROOT = Path(__file__).resolve().parent
PRACTICE_TOKEN = "tok-FAKE-practice-0123abcd"
LIVE_TOKEN = "tok-FAKE-live-9876zyxw"
PRACTICE_ACCOUNT = "101-001-12345678-001"
LIVE_ACCOUNT = "001-001-87654321-001"
NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
SUMMARY = {"account": {"currency": "USD", "balance": "100000.1234", "NAV": "100010.50", "marginUsed": "33.20",
                       "openTradeCount": 1, "unrealizedPL": "10.3766", "id": PRACTICE_ACCOUNT}}


def refuse_network(*_args, **_kwargs):
    raise AssertionError("network must not be touched")


class Recorder:
    def __init__(self, *responses):
        self.calls, self.responses = [], list(responses) or [(200, SUMMARY)]

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        response = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(response, BaseException):
            raise response
        status, payload = response
        return status, {}, payload if isinstance(payload, bytes) else json.dumps(payload).encode()


class ReadOnlyBase(unittest.TestCase):
    def setUp(self):
        default = patch("execution.oanda_readonly.urllib_transport", side_effect=refuse_network)
        self.default = default.start()
        self.addCleanup(default.stop)

    def tearDown(self):
        self.default.assert_not_called()


class ClientTests(ReadOnlyBase):
    def test_only_get_summary_on_the_allow_listed_host(self):
        for environment, token, account, host in (("practice", PRACTICE_TOKEN, PRACTICE_ACCOUNT, "api-fxpractice.oanda.com"),
                                                   ("live", LIVE_TOKEN, LIVE_ACCOUNT, "api-fxtrade.oanda.com")):
            with self.subTest(environment=environment):
                transport = Recorder()
                client = OandaReadOnlyClient(token, account, environment, transport=transport)
                self.assertEqual(client.get_account_summary()["currency"], "USD")
                for method, url, headers, body, timeout in transport.calls:
                    self.assertEqual(method, "GET")
                    self.assertIsNone(body)
                    self.assertTrue(url.startswith(f"https://{host}/v3/"))
                    self.assertRegex(url[len(f"https://{host}"):], rf"^/v3/accounts/{re.escape(account)}/summary$")
                    self.assertEqual(headers["Authorization"], f"Bearer {token}")
                    self.assertLessEqual(timeout, 5)

    def test_class_has_no_write_path(self):
        public = {name for name in dir(OandaReadOnlyClient) if not name.startswith("_")}
        self.assertEqual(public, {"get_account_summary", "get_trades"})  # both GET (trades list: dashboard demo)
        source = (ROOT / "execution/oanda_readonly.py").read_text(encoding="utf-8")
        for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"', "'POST'", "'PUT'", "/orders", "/close", "/trades/",
                     "/transactions", "/positions"):
            self.assertNotIn(verb, source)
        self.assertEqual(source.count('"/trades"'), 1)  # the one list path (no per-trade endpoints)
        self.assertEqual(ro.HOSTS, {"practice": "api-fxpractice.oanda.com", "live": "api-fxtrade.oanda.com"})

    def test_default_transport_refuses_non_get_and_other_hosts(self):
        for method, url in (("POST", "https://api-fxpractice.oanda.com/v3/accounts/x/summary"),
                            ("GET", "http://api-fxpractice.oanda.com/v3/x"), ("GET", "https://evil.test/v3/x"),
                            ("GET", "https://api-fxtrade.oanda.com:8443/v3/x"),
                            ("GET", "https://u@api-fxtrade.oanda.com/v3/x")):
            with self.subTest(method=method, url=url), self.assertRaises(ReadOnlyError):
                REAL_TRANSPORT(method, url, {}, None, 1)  # refused before any socket is opened

    def test_account_prefix_and_environment_checks(self):
        for token, account, environment in ((PRACTICE_TOKEN, LIVE_ACCOUNT, "practice"),
                                            (LIVE_TOKEN, PRACTICE_ACCOUNT, "live"),
                                            (LIVE_TOKEN, "001-1-2", "live"), (PRACTICE_TOKEN, "", "practice")):
            with self.subTest(account=account, environment=environment), self.assertRaises(ReadOnlyError) as ctx:
                OandaReadOnlyClient(token, account, environment, transport=Recorder())
            self.assertEqual(ctx.exception.code, "account_prefix_mismatch")
        with self.assertRaises(ValueError):
            OandaReadOnlyClient(LIVE_TOKEN, LIVE_ACCOUNT, "fxtrade", transport=Recorder())
        for token in ("", " ", None, "a b"):
            with self.subTest(token=token), self.assertRaises(ReadOnlyError):
                OandaReadOnlyClient(token, LIVE_ACCOUNT, "live", transport=Recorder())

    def test_error_mapping_is_codes_only(self):
        cases = (((401, {"errorMessage": "secret broker text"}), "auth"), ((403, {}), "auth"),
                 ((429, {}), "rate_limited"), ((500, {}), "http_500"), ((302, {}), "redirect"),
                 ((200, b"not json"), "malformed"), ((200, {"x": 1}), "malformed"),
                 (TimeoutError(), "timeout"), (OSError(f"failed {LIVE_TOKEN}"), "unavailable"))
        for response, code in cases:
            with self.subTest(code=code), self.assertRaises(ReadOnlyError) as ctx:
                OandaReadOnlyClient(LIVE_TOKEN, LIVE_ACCOUNT, "live", transport=Recorder(response)).get_account_summary()
            self.assertEqual(ctx.exception.code, code)
            self.assertNotIn("secret", str(ctx.exception))
            self.assertNotIn(LIVE_TOKEN, str(ctx.exception))

    def test_repr_redacts(self):
        client = OandaReadOnlyClient(LIVE_TOKEN, LIVE_ACCOUNT, "live", transport=Recorder())
        for text in (repr(client), str(client)):
            self.assertNotIn(LIVE_TOKEN, text)
            self.assertNotIn(LIVE_ACCOUNT, text)
            self.assertIn("001-…4321-001", text)


REAL_TRANSPORT = ro.urllib_transport


class AccountViewTests(ReadOnlyBase):
    def test_practice_view_ok(self):
        transport = Recorder()
        view = account_view("practice", {"OANDA_PRACTICE_TOKEN": PRACTICE_TOKEN, "OANDA_ACCOUNT_ID": PRACTICE_ACCOUNT},
                            transport=transport, now=NOW)
        self.assertEqual((view.status, view.currency, view.balance, view.nav, view.margin_used, view.open_trades,
                          view.unrealized_pl), ("OK", "USD", Decimal("100000.1234"), Decimal("100010.50"),
                                                Decimal("33.20"), 1, Decimal("10.3766")))
        self.assertEqual(view.account_masked, "101-…5678-001")
        self.assertNotIn(PRACTICE_ACCOUNT, repr(view))
        self.assertNotIn(PRACTICE_TOKEN, repr(view))

    def test_practice_without_account_id_is_sin_cuenta(self):
        transport = Recorder()
        for account in (None, "", "  "):
            env = {"OANDA_PRACTICE_TOKEN": PRACTICE_TOKEN}
            if account is not None:
                env["OANDA_ACCOUNT_ID"] = account
            with self.subTest(account=account):
                self.assertEqual(account_view("practice", env, transport=transport).status, "SIN_CUENTA")
        self.assertEqual(transport.calls, [])  # no default account id, no discovery request
        self.assertFalse(hasattr(ro, "DEFAULT_PRACTICE_ACCOUNT"))

    def test_token_isolation(self):
        transport = Recorder()
        env = {"OANDA_PRACTICE_TOKEN": PRACTICE_TOKEN, "OANDA_ACCOUNT_ID": PRACTICE_ACCOUNT,
               "OANDA_LIVE_ACCOUNT_ID": LIVE_ACCOUNT}
        self.assertEqual(account_view("live", env, transport=transport).status, "SIN_TOKEN")
        env = {"OANDA_LIVE_TOKEN": LIVE_TOKEN, "OANDA_LIVE_ACCOUNT_ID": LIVE_ACCOUNT}
        self.assertEqual(account_view("practice", env, transport=transport).status, "SIN_TOKEN")
        self.assertEqual(transport.calls, [])
        both = {"OANDA_PRACTICE_TOKEN": PRACTICE_TOKEN, "OANDA_LIVE_TOKEN": LIVE_TOKEN,
                "OANDA_LIVE_ACCOUNT_ID": LIVE_ACCOUNT, "OANDA_ACCOUNT_ID": PRACTICE_ACCOUNT}
        account_view("live", both, transport=transport)
        account_view("practice", both, transport=transport)
        self.assertEqual([c[2]["Authorization"] for c in transport.calls],
                         [f"Bearer {LIVE_TOKEN}", f"Bearer {PRACTICE_TOKEN}"])
        self.assertIn("api-fxtrade", transport.calls[0][1])
        self.assertIn("api-fxpractice", transport.calls[1][1])

    def test_sin_cuenta_never_discovers_accounts(self):
        transport = Recorder()
        self.assertEqual(account_view("live", {"OANDA_LIVE_TOKEN": LIVE_TOKEN}, transport=transport).status, "SIN_CUENTA")
        self.assertEqual(transport.calls, [])

    def test_prefix_mismatch_and_errors_are_error_views(self):
        view = account_view("live", {"OANDA_LIVE_TOKEN": LIVE_TOKEN, "OANDA_LIVE_ACCOUNT_ID": PRACTICE_ACCOUNT},
                            transport=Recorder())
        self.assertEqual((view.status, view.error_code), ("ERROR", "account_prefix_mismatch"))
        view = account_view("live", {"OANDA_LIVE_TOKEN": LIVE_TOKEN, "OANDA_LIVE_ACCOUNT_ID": LIVE_ACCOUNT},
                            transport=Recorder((401, {"errorMessage": LIVE_TOKEN})))
        self.assertEqual((view.status, view.error_code, view.balance), ("ERROR", "auth", None))
        self.assertNotIn(LIVE_TOKEN, repr(view))
        self.assertEqual(account_view("practice", {}).status, "SIN_TOKEN")  # no env: no network, no crash

    def test_cache_ttl_and_stale_last_good(self):
        now = [0.0]
        views = iter([ro.AccountView("OK", "practice", balance=Decimal("1")),
                      ro.AccountView("ERROR", "practice", error_code="rate_limited")])
        calls = []

        def fetch(environment, env):
            calls.append(environment)
            return next(views)

        cache = CachedAccountViews(ttl=1, clock=lambda: now[0], fetch=fetch)
        self.assertEqual(cache.ttl, 5.0)  # never below 5 s
        self.assertEqual(cache.get("practice").balance, Decimal("1"))
        now[0] = 4.9
        cache.get("practice")
        self.assertEqual(calls, ["practice"])
        now[0] = 5.0
        stale = cache.get("practice")
        self.assertEqual((stale.status, stale.balance, stale.stale, stale.error_code), ("OK", Decimal("1"), True,
                                                                                        "rate_limited"))
        self.assertEqual(len(calls), 2)

    def test_lock_is_not_held_during_the_network_read(self):
        import threading
        now = [0.0]
        release, entered = threading.Event(), threading.Event()
        views = iter([ro.AccountView("OK", "practice", balance=Decimal("1")),
                      ro.AccountView("OK", "practice", balance=Decimal("2"))])
        calls = []

        def fetch(environment, env):
            calls.append(environment)
            if len(calls) == 2:  # the second (TTL-expired) refresh blocks on the "network"
                entered.set()
                release.wait(5)
            return next(views)

        cache = CachedAccountViews(ttl=5, clock=lambda: now[0], fetch=fetch, wait_seconds=0.2)
        self.assertEqual(cache.get("practice").balance, Decimal("1"))
        now[0] = 6.0
        result = {}
        refresher = threading.Thread(target=lambda: result.update(view=cache.get("practice")))
        refresher.start()
        self.assertTrue(entered.wait(5))
        started = time.monotonic()
        other = cache.get("practice")  # another session while the GET is in flight: the previous view at once
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(other.balance, Decimal("1"))
        release.set()
        refresher.join(5)
        self.assertEqual(result["view"].balance, Decimal("2"))
        self.assertEqual(cache.get("practice").balance, Decimal("2"))
        self.assertEqual(len(calls), 2)  # one refresh, never one per reader

    def test_first_read_waits_for_the_refresh_then_times_out_with_an_error_view(self):
        import threading
        release, entered = threading.Event(), threading.Event()

        def fetch(environment, env):
            entered.set()
            release.wait(5)
            return ro.AccountView("OK", environment, balance=Decimal("3"))

        cache = CachedAccountViews(fetch=fetch, wait_seconds=0.1)
        refresher = threading.Thread(target=cache.get, args=("practice",))
        refresher.start()
        self.assertTrue(entered.wait(5))
        cold = cache.get("practice")  # nothing cached yet: waits up to wait_seconds, never raises
        self.assertEqual((cold.status, cold.error_code), ("ERROR", "timeout"))
        release.set()
        refresher.join(5)
        self.assertEqual(cache.get("practice").balance, Decimal("3"))

    def test_fetch_exception_clears_the_inflight_marker(self):
        cache = CachedAccountViews(fetch=Mock(side_effect=[RuntimeError("boom"),
                                                           ro.AccountView("OK", "practice", balance=Decimal("4"))]))
        with self.assertRaises(RuntimeError):
            cache.get("practice")
        self.assertEqual(cache.get("practice").balance, Decimal("4"))  # not stuck "refreshing"

    def test_cached_account_view_never_raises(self):
        with patch.object(ro._VIEWS, "get", side_effect=RuntimeError("boom")):
            self.assertEqual(ro.cached_account_view("live").status, "ERROR")


class IsolationTests(unittest.TestCase):
    def test_order_path_and_readonly_do_not_import_each_other(self):
        import ast
        tree = ast.parse((ROOT / "execution/oanda_readonly.py").read_text(encoding="utf-8"))
        imported = {alias.name for n in ast.walk(tree) if isinstance(n, ast.Import) for alias in n.names}
        imported |= {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        self.assertFalse([m for m in imported if m.split(".")[0] not in {
            "dataclasses", "datetime", "decimal", "json", "os", "re", "threading", "time", "urllib", "socket"}])
        for rel in ("execution/oanda_client.py", "execution/oanda_broker.py", "execution/broker_demo_router.py",
                    "execution/broker_demo_state.py", "scripts/oanda_first_test_order.py"):
            self.assertNotIn("oanda_readonly", (ROOT / rel).read_text(encoding="utf-8"))

    def test_order_client_still_refuses_the_live_host(self):
        from execution.broker_port import BrokerGuardError
        from execution.oanda_client import OandaClient
        with self.assertRaises(BrokerGuardError):
            OandaClient(PRACTICE_TOKEN, PRACTICE_ACCOUNT, base_url="https://api-fxtrade.oanda.com/v3",
                        transport=refuse_network)
        from runtime import demo_runner
        self.assertIs(demo_runner.REAL_EXECUTION_ENABLED, False)


if __name__ == "__main__":
    unittest.main()
