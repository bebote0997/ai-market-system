"""DXtrade skeleton: client, adapter and demo-only guards. Fake transport only; the default transport must never run."""
from decimal import Decimal
import json
import logging
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.contracts import RiskDecision
from execution import account_registry
from execution.account_registry import AccountRegistry
from execution.broker_config import DEFAULT_BASE_URL, load_broker_config
from execution.broker_port import (ACCEPTED, REJECTED, UNKNOWN, BrokerApiNotPermitted, BrokerAuthError,
                                   BrokerGuardError, BrokerMalformedResponse, BrokerPort, BrokerPreconditionFailed,
                                   BrokerRateLimited, OrderPlan, RiskApproval)
from execution.dxtrade_broker import DxtradeBroker
from execution.dxtrade_client import DxtradeClient

ACCOUNT = "DEM_1000001_1"
PASSWORD = "pw-S3cr3t-Value!"
USERNAME = "demo-user-xyz"
TOKEN = "tok-SESSION-9f8e7d"
DOMAIN = "dom-Pr1vate-Q7z"
ENV = {"AI_FLOOR_BROKER_DEMO_ENABLED": "1", "DXTRADE_USERNAME": USERNAME, "DXTRADE_PASSWORD": PASSWORD,
       "DXTRADE_DOMAIN": DOMAIN, "DXTRADE_ACCOUNT_CODE": ACCOUNT}
ORDERS = f"accounts/{ACCOUNT}/orders"
POSITIONS = f"accounts/{ACCOUNT}/positions"
SYMBOLS = {"EURUSD": "EUR/USD", "XAUUSD": "XAU/USD", "NAS100": "NAS100", "GBPUSD": "GBP/USD"}


class FakeClock:
    def __init__(self):
        self.now, self.sleeps = 1000.0, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeServer:
    """Routes ``(method, path)`` to queued responses (the last one is sticky). Login answers a token by default."""

    def __init__(self):
        self.calls, self.routes = [], {}

    def add(self, method, path, *responses):
        self.routes.setdefault((method, path), []).extend(responses)
        return self

    def paths(self, method=None):
        return [path for m, path, _, _ in self.calls if method in (None, m)]

    def __call__(self, method, url, headers, body, timeout):
        assert url.startswith(DEFAULT_BASE_URL), url
        path = url[len(DEFAULT_BASE_URL):]
        self.calls.append((method, path, dict(headers), json.loads(body) if body else None))
        queue = self.routes.get((method, path))
        if queue:
            response = queue.pop(0) if len(queue) > 1 else queue[0]
        elif (method, path) == ("POST", "login"):
            response = (200, {"sessionToken": TOKEN, "timeout": "00:30:00"})
        else:
            raise AssertionError(f"unexpected {method} {path}")
        if isinstance(response, BaseException):
            raise response
        status, payload = response[:2]
        return status, (response[2] if len(response) > 2 else {}), json.dumps(payload).encode()


def raising_transport(*_args, **_kwargs):
    raise AssertionError("network must not be touched")


def plan(side="LONG", client_id="cid-1"):
    stop, target = ("1.0800", "1.1100") if side == "LONG" else ("1.1100", "1.0800")
    return OrderPlan("EURUSD", side, Decimal("8963"), Decimal("1.0900"), Decimal(stop), Decimal(target),
                     client_id=client_id, source_run_id="run-1")


def approved(p, status="APPROVED", **changes):
    values = {"status": status, "run_id": p.source_run_id, "symbol": p.symbol, "side": p.side, "quantity": p.quantity,
              "entry": p.planned_entry, "stop": p.stop, "target": p.target}
    values.update(changes)
    return RiskApproval(**values)


def submit(broker, p):
    return broker.submit_plan(p, approved(p))


class BrokerTestBase(unittest.TestCase):
    def setUp(self):
        default = patch("execution.dxtrade_client.urllib_transport", side_effect=raising_transport)
        self.default_transport = default.start()
        self.addCleanup(default.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "broker_state.db"
        self.addCleanup(account_registry._EMERGENCY_LATCHES.pop, account_registry._latch_key(self.db), None)
        self.registry = AccountRegistry(self.db, env=ENV)
        self.registry.set_enabled("broker_demo", True, actor="test")
        self.server, self.clock = FakeServer(), FakeClock()

    def tearDown(self):
        self.default_transport.assert_not_called()

    def broker(self, env=None, **kwargs):
        options = {"mode": "broker_demo", "account_code": ACCOUNT, "registry": self.registry,
                   "symbol_map": SYMBOLS, "transport": self.server, "clock": self.clock, "sleep": self.clock.sleep}
        options.update(kwargs)
        return DxtradeBroker(env=ENV if env is None else env, **options)


class DxtradeClientTests(BrokerTestBase):
    def test_login_then_authorised_request(self):
        broker = self.broker()
        self.assertIsInstance(broker, BrokerPort)
        self.assertEqual(self.server.calls, [])  # Lazy: construction never touches the network.
        self.server.add("GET", f"accounts/{ACCOUNT}/positions", (200, {"positions": []}))
        self.assertEqual(broker.get_positions(), [])
        (m1, p1, _, login_body), (m2, p2, headers, _) = self.server.calls
        self.assertEqual((m1, p1, login_body), ("POST", "login",
                                               {"username": USERNAME, "domain": DOMAIN, "password": PASSWORD}))
        self.assertEqual((m2, p2, headers["Authorization"]), ("GET", f"accounts/{ACCOUNT}/positions", f"DXAPI {TOKEN}"))
        self.assertTrue(broker.health().session_active)
        self.assertFalse(broker.health().connected)

    def test_ping_uses_session(self):
        client = DxtradeClient(DEFAULT_BASE_URL, USERNAME, PASSWORD, DOMAIN, transport=self.server,
                               clock=self.clock, sleep=self.clock.sleep)
        self.server.add("POST", "ping", (200, {}))
        client.ping()
        self.assertEqual(self.server.paths(), ["login", "ping"])

    def test_https_required(self):
        with self.assertRaises(ValueError):
            DxtradeClient("http://api.example.test/", USERNAME, PASSWORD, "d", transport=self.server)

    def test_build_order_types(self):
        kwargs = {"order_code": "c", "instrument": "EUR/USD", "side": "BUY", "quantity": Decimal("1"),
                  "position_effect": "OPEN"}
        self.assertNotIn("stopPrice", DxtradeClient.build_order("MARKET", **kwargs))
        self.assertEqual(DxtradeClient.build_order("LIMIT", price=Decimal("1.1"), **kwargs)["limitPrice"], "1.1")
        self.assertEqual(DxtradeClient.build_order("STOP", price=Decimal("1.0"), **kwargs)["stopPrice"], "1.0")
        for bad in ("STOP_LIMIT", "market"):
            with self.assertRaises(ValueError):
                DxtradeClient.build_order(bad, **kwargs)
        with self.assertRaises(ValueError):
            DxtradeClient.build_order("STOP", **kwargs)  # STOP without price

    def test_rate_limiter_spaces_requests(self):
        broker = self.broker()
        self.server.add("GET", f"accounts/{ACCOUNT}/positions", (200, {"positions": []}))
        self.server.add("POST", f"{ORDERS}/group", (200, {"orderId": "1"}))
        broker.get_positions()
        broker.get_positions()  # data: 0.5 s spacing
        submit(broker, plan(client_id="a"))
        submit(broker, plan(client_id="b"))  # trading: 1 s spacing
        self.assertEqual(self.clock.sleeps, [0.5, 1.0])


class DxtradeBrokerTests(BrokerTestBase):
    def test_submit_plan_is_one_if_then_group(self):
        self.server.add("POST", f"{ORDERS}/group", (200, {"orderId": 4242}))
        result = submit(self.broker(), plan())
        self.assertEqual((result.status, result.broker_ref, result.client_id), (ACCEPTED, "4242", "cid-1"))
        method, path, _, body = self.server.calls[-1]
        self.assertEqual((method, path, body["contingencyType"]), ("POST", f"{ORDERS}/group", "IF_THEN"))
        parent, stop, take = body["orders"]
        self.assertEqual((parent["type"], parent["side"], parent["positionEffect"], parent["orderCode"]),
                         ("MARKET", "BUY", "OPEN", "cid-1"))
        self.assertEqual((stop["type"], stop["side"], stop["positionEffect"], stop["stopPrice"]),
                         ("STOP", "SELL", "CLOSE", "1.0800"))
        self.assertEqual((take["type"], take["side"], take["positionEffect"], take["limitPrice"]),
                         ("LIMIT", "SELL", "CLOSE", "1.1100"))
        self.assertEqual({o["quantity"] for o in body["orders"]}, {"8963"})
        self.assertEqual({o["instrument"] for o in body["orders"]}, {"EUR/USD"})

    def test_short_plan_sides(self):
        self.server.add("POST", f"{ORDERS}/group", (200, {"orderId": 1}))
        submit(self.broker(), plan("SHORT"))
        sides = [o["side"] for o in self.server.calls[-1][3]["orders"]]
        self.assertEqual(sides, ["SELL", "BUY", "BUY"])

    def test_unmapped_symbol_and_bad_geometry_rejected_without_network(self):
        broker = self.broker(symbol_map={})
        self.assertEqual(submit(broker, plan()).reason, "symbol_not_mapped")
        bad = OrderPlan("EURUSD", "LONG", Decimal("1"), Decimal("1.09"), Decimal("1.10"), Decimal("1.11"), "c", "r")
        self.assertEqual(submit(self.broker(), bad).reason, "invalid_plan_geometry")
        self.assertEqual(self.server.calls, [])

    def test_modify_stop_uses_put_with_if_match(self):
        stop_order = {"orderId": 9, "orderCode": "cid-1-SL", "type": "STOP", "positionEffect": "CLOSE",
                      "positionCode": "P1", "stopPrice": "1.0800", "version": 7}
        self.server.add("GET", ORDERS, (200, {"orders": [{"type": "LIMIT", "positionCode": "P1"}, stop_order]}))
        self.server.add("PUT", ORDERS, (200, {"orderId": 9}))
        result = self.broker().modify_stop("P1", Decimal("1.0900"), client_id="m1")
        self.assertEqual(result.status, ACCEPTED)
        method, _, headers, body = self.server.calls[-1]
        self.assertEqual((method, headers["If-Match"], body["stopPrice"], body["orderCode"]),
                         ("PUT", "7", "1.0900", "cid-1-SL"))
        self.assertNotIn("version", body)

    def test_modify_stop_412_rereads_once_then_raises(self):
        order = {"type": "STOP", "positionEffect": "CLOSE", "positionCode": "P1", "stopPrice": "1.08"}
        self.server.add("GET", ORDERS, (200, {"orders": [{**order, "version": 7}]}),
                        (200, {"orders": [{**order, "version": 8}]}))
        self.server.add("PUT", ORDERS, (412, {}), (200, {"orderId": 1}))
        self.assertEqual(self.broker().modify_stop("P1", Decimal("1.09"), client_id="m").status, ACCEPTED)
        self.assertEqual([c[2]["If-Match"] for c in self.server.calls if c[0] == "PUT"], ["7", "8"])
        server = FakeServer().add("GET", ORDERS, (200, {"orders": [{**order, "version": 1}]}))
        server.add("PUT", ORDERS, (412, {}))
        with self.assertRaises(BrokerPreconditionFailed):
            self.broker(transport=server).modify_stop("P1", Decimal("1.09"), client_id="m")
        self.assertEqual(len(server.paths("PUT")), 2)

    def test_positions_balance_and_close(self):
        self.server.add("GET", f"accounts/{ACCOUNT}/positions", (200, {"positions": [
            {"positionCode": "P1", "symbol": "EURUSD", "side": "BUY", "quantity": 8963, "openPrice": 1.09031}]}))
        self.server.add("GET", f"accounts/{ACCOUNT}/metrics", (200, {"metrics": [
            {"balance": 10000.00, "equity": 10012.5, "margin": 300.1, "currency": "USD"}]}))
        self.server.add("POST", ORDERS, (200, {"orderId": 77}))
        broker = self.broker()
        (position,) = broker.get_positions()
        self.assertEqual((position.position_ref, position.side, position.quantity, position.entry_price),
                         ("P1", "LONG", Decimal("8963"), Decimal("1.09031")))
        balance = broker.get_balance()
        self.assertEqual((balance.balance, balance.equity, balance.margin_used, balance.currency),
                         (Decimal("10000.00"), Decimal("10012.5"), Decimal("300.1"), "USD"))
        result = broker.close_market("P1", reason="TIME_EXIT", client_id="close-1")
        self.assertEqual((result.status, result.broker_ref), (ACCEPTED, "77"))
        body = self.server.calls[-1][3]
        self.assertEqual((body["type"], body["side"], body["positionEffect"], body["positionCode"], body["quantity"]),
                         ("MARKET", "SELL", "CLOSE", "P1", "8963"))
        self.assertEqual(broker.close_market("NOPE", reason="x", client_id="c").reason, "no_open_position")

    def test_401_relogin_once(self):
        self.server.add("GET", f"accounts/{ACCOUNT}/positions", (401, {}), (200, {"positions": []}))
        self.assertEqual(self.broker().get_positions(), [])
        self.assertEqual(self.server.paths().count("login"), 2)
        server = FakeServer().add("GET", f"accounts/{ACCOUNT}/positions", (401, {}))
        with self.assertRaises(BrokerAuthError):
            self.broker(transport=server).get_positions()
        self.assertEqual(server.paths().count("login"), 2)
        self.assertEqual(len(server.paths("GET")), 2)

    def test_login_refused(self):
        self.server.add("POST", "login", (401, {"description": f"bad password {PASSWORD}"}))
        with self.assertRaises(BrokerAuthError) as ctx:
            self.broker().get_positions()
        self.assertNotIn(PASSWORD, str(ctx.exception))
        self.assertEqual(self.server.paths(), ["login"])

    def test_404_code_2_is_api_not_permitted_without_retry(self):
        self.server.add("POST", "login", (404, {"errorCode": "2", "description": "API not permitted"}))
        with self.assertRaises(BrokerApiNotPermitted):
            self.broker().get_positions()
        self.assertEqual(self.server.paths(), ["login"])
        self.assertEqual(self.clock.sleeps, [])

    def test_429_bounded_backoff_no_tight_loop(self):
        self.server.add("GET", f"accounts/{ACCOUNT}/positions", (429, {}))
        with self.assertRaises(BrokerRateLimited):
            self.broker().get_positions()
        self.assertEqual(len(self.server.paths("GET")), 4)  # 1 + 3 bounded retries
        backoffs = [s for s in self.clock.sleeps if s >= 1.0]
        self.assertEqual(backoffs, [1.0, 2.0, 4.0])
        self.assertTrue(all(s <= 8.0 for s in self.clock.sleeps))

    def test_429_honours_retry_after_then_succeeds(self):
        self.server.add("POST", f"{ORDERS}/group", (429, {}, {"Retry-After": "3"}), (200, {"orderId": 5}))
        self.assertEqual(submit(self.broker(), plan()).status, ACCEPTED)
        self.assertIn(3.0, self.clock.sleeps)
        self.assertEqual(len(self.server.paths("POST")) - 1, 2)

    def test_timeout_is_unknown_and_never_retried(self):
        self.server.add("POST", f"{ORDERS}/group", TimeoutError("slow"))
        result = submit(self.broker(), plan())
        self.assertEqual((result.status, result.reason), (UNKNOWN, "BrokerTimeout"))
        self.assertEqual(self.server.paths("POST").count(f"{ORDERS}/group"), 1)

    def test_5xx_unknown_and_4xx_rejected(self):
        self.server.add("POST", f"{ORDERS}/group", (502, {}), (400, {"description": "bad qty"}))
        broker = self.broker()
        self.assertEqual(submit(broker, plan()).status, UNKNOWN)
        rejected = submit(broker, plan())
        self.assertEqual((rejected.status, rejected.reason), (REJECTED, "broker_rejected:400"))

    def test_secrets_never_in_repr_logs_or_exceptions(self):
        self.server.add("GET", f"accounts/{ACCOUNT}/positions", (401, {}), (429, {}), (200, {"positions": []}))
        self.server.add("POST", f"{ORDERS}/group", (400, {"description": f"{USERNAME} {PASSWORD} {TOKEN} {DOMAIN}"}))
        broker = self.broker()
        errors = []
        with self.assertLogs("ai_floor.broker", level="DEBUG") as logs:
            broker.get_positions()
            errors.append(submit(broker, plan()).reason)
            failing = FakeServer().add("GET", f"accounts/{ACCOUNT}/metrics", (404, {"errorCode": 2,
                                                                                     "description": f"{PASSWORD} {DOMAIN}"}))
            try:
                self.broker(transport=failing).get_balance()
            except BrokerApiNotPermitted as error:
                errors.append(str(error))
        config = load_broker_config(ENV)
        text = "\n".join([*logs.output, *errors, repr(broker), str(broker), repr(broker._client), repr(config),
                          str(config), repr(broker.health())])
        for secret in (PASSWORD, TOKEN, USERNAME, ACCOUNT, DOMAIN):
            self.assertNotIn(secret, text)
        self.assertIn("DEM_…01_1", text)
        self.assertIn("domain='***'", repr(config))


class RiskApprovalAndSymbolPolicyTests(BrokerTestBase):
    def setUp(self):
        super().setUp()
        self.server = FakeServer()  # Any request (even login) is unexpected unless a route is added.
        self.server.routes[("POST", "login")] = [AssertionError("network must not be touched")]

    def test_missing_or_mismatched_approval_rejected_before_network(self):
        broker, p = self.broker(), plan()
        cases = [(None, "risk_approval_missing"), ({"status": "APPROVED"}, "risk_approval_missing"),
                 (approved(p, status="REJECTED"), "risk_not_approved"),
                 (approved(p, status="approved"), "risk_not_approved")]
        changes = {"run_id": "run-other", "symbol": "XAUUSD", "side": "SHORT", "quantity": Decimal("8964"),
                   "entry": Decimal("1.0901"), "stop": Decimal("1.0790"), "target": Decimal("1.1200")}
        cases += [(approved(p, **{name: value}), "risk_approval_mismatch") for name, value in changes.items()]
        for approval, reason in cases:
            with self.subTest(reason=reason, approval=approval):
                result = broker.submit_plan(p, approval)
                self.assertEqual((result.status, result.reason), (REJECTED, reason))
        with self.assertRaises(TypeError):
            broker.submit_plan(p)  # approval is a required argument of the port
        self.assertEqual(self.server.calls, [])

    def test_symbol_policy_only_eurusd_xauusd(self):
        broker = self.broker()
        for symbol in ("NAS100", "GBPUSD", "eurusd", "EURUSD.x", "XAUUSD "):
            p = OrderPlan(symbol, "LONG", Decimal("1"), Decimal("1.09"), Decimal("1.08"), Decimal("1.11"), "c", "r")
            with self.subTest(symbol=symbol):
                result = broker.submit_plan(p, approved(p))
                self.assertEqual((result.status, result.reason), (REJECTED, "symbol_not_allowed"))
        self.assertEqual(self.server.calls, [])

    def test_approved_plan_from_risk_decision_reaches_the_broker(self):
        self.server.routes.pop(("POST", "login"))
        self.server.add("POST", f"{ORDERS}/group", (200, {"orderId": 1}))
        p = OrderPlan("XAUUSD", "LONG", Decimal("2"), Decimal("2400.5"), Decimal("2390.25"), Decimal("2430"),
                      client_id="x-1", source_run_id="run-x")
        decision = RiskDecision("1.0", "APPROVED", "XAUUSD", "LONG", 2.0, 30.0, 2400.5, 2390.25, 2430.0, "ok")
        result = self.broker().submit_plan(p, RiskApproval.from_decision(decision, run_id="run-x"))
        self.assertEqual(result.status, ACCEPTED)
        self.assertEqual({o["instrument"] for o in self.server.calls[-1][3]["orders"]}, {"XAU/USD"})


class AcceptanceEvidenceTests(BrokerTestBase):
    def test_success_without_explicit_acceptance_is_unknown(self):
        for payload in ({}, [], {"status": "ok"}, {"orderId": None}, {"orderId": ""}, {"orderId": True},
                        {"orderId": {"id": 1}}, "accepted", [{"orderId": 1}]):
            server = FakeServer().add("POST", f"{ORDERS}/group", (200, payload))
            with self.subTest(payload=payload):
                result = submit(self.broker(transport=server), plan())
                self.assertEqual((result.status, result.reason, result.broker_ref),
                                 (UNKNOWN, "no_acceptance_evidence", None))

    def test_close_and_modify_without_acceptance_are_unknown(self):
        self.server.add("GET", POSITIONS, (200, {"positions": [
            {"positionCode": "P1", "symbol": "EURUSD", "side": "BUY", "quantity": "1"}]}))
        self.server.add("POST", ORDERS, (200, {}))
        self.server.add("GET", ORDERS, (200, {"orders": [{"type": "STOP", "positionEffect": "CLOSE",
                                                          "positionCode": "P1", "version": 1}]}))
        self.server.add("PUT", ORDERS, (200, []))
        broker = self.broker()
        self.assertEqual(broker.close_market("P1", reason="x", client_id="c").status, UNKNOWN)
        self.assertEqual(broker.modify_stop("P1", Decimal("1.09"), client_id="m").status, UNKNOWN)


class MalformedPositionsTests(BrokerTestBase):
    def test_valid_empty_collection_is_empty(self):
        for payload in ({"positions": []}, []):
            server = FakeServer().add("GET", POSITIONS, (200, payload))
            with self.subTest(payload=payload):
                self.assertEqual(self.broker(transport=server).get_positions(), [])

    def test_malformed_answers_raise_typed_error(self):
        good = {"positionCode": "P1", "symbol": "EURUSD", "side": "BUY", "quantity": "1"}
        payloads = [{}, {"positions": None}, {"positions": {}}, {"positions": "P1"}, {"data": []}, "x", 5,
                    {"positions": [good, "P2"]}, {"positions": [{**good, "side": "FLAT"}]},
                    {"positions": [{**good, "side": None}]}, {"positions": [{**good, "quantity": "abc"}]},
                    {"positions": [{**good, "quantity": 0}]}, {"positions": [{**good, "quantity": None}]},
                    {"positions": [{**good, "quantity": "NaN"}]}, {"positions": [{**good, "positionCode": ""}]},
                    {"positions": [{k: v for k, v in good.items() if k != "symbol"}]},
                    {"positions": [{**good, "openPrice": "bad"}]}]
        for payload in payloads:
            server = FakeServer().add("GET", POSITIONS, (200, payload))
            with self.subTest(payload=payload), self.assertRaises(BrokerMalformedResponse):
                self.broker(transport=server).get_positions()

    def test_close_with_unknown_state_is_unknown_not_no_open_position(self):
        for response in ((200, {"positions": {}}), (200, {"positions": [{"positionCode": "P1", "side": "?"}]}),
                         (502, {}), TimeoutError("slow")):
            server = FakeServer().add("GET", POSITIONS, response)
            with self.subTest(response=response):
                result = self.broker(transport=server).close_market("P1", reason="x", client_id="c")
                self.assertEqual(result.status, UNKNOWN)
                self.assertTrue(result.reason.startswith("position_state_unknown:"))
                self.assertEqual(server.paths("POST"), ["login"])  # no close order sent


class BaseUrlTests(BrokerTestBase):
    def test_userinfo_query_fragment_rejected(self):
        for url in ("https://user:pw-XYZ@api.example.test/dx/", "https://user@api.example.test/",
                    "https://api.example.test/dx/?token=abc", "https://api.example.test/dx/?",
                    "https://api.example.test/dx/#frag", "http://api.example.test/", "https:///dx/",
                    "https://api.example.test:99999/", "", None):
            with self.subTest(url=url):
                with self.assertRaises(ValueError) as ctx:
                    DxtradeClient(url, USERNAME, PASSWORD, DOMAIN, transport=self.server)
                self.assertNotIn("pw-XYZ", str(ctx.exception))
                self.assertNotIn("token=abc", str(ctx.exception))
                with self.assertRaises(BrokerGuardError):
                    self.broker(env={**ENV, "DXTRADE_BASE_URL": url or "http://x"}, transport=raising_transport)
        self.assertEqual(self.server.calls, [])

    def test_repr_shows_only_sanitised_endpoint(self):
        client = DxtradeClient("https://api.example.test:8443/dxsca-web", USERNAME, PASSWORD, DOMAIN,
                               transport=self.server)
        self.assertIn("'https://api.example.test:8443/dxsca-web/'", repr(client))
        config = load_broker_config({**ENV, "DXTRADE_BASE_URL": "https://u:Secr3tPw@api.example.test/x?token=abc#f"})
        text = repr(config)
        self.assertIn("'https://api.example.test/x'", text)
        for leaked in ("Secr3tPw", "token=abc", "#f", "u:"):
            self.assertNotIn(leaked, text)


class EmergencyLatchTests(BrokerTestBase):
    def test_failed_persistence_latch_blocks_opening_without_network(self):
        broker = self.broker()
        with patch.object(AccountRegistry, "_connect", side_effect=account_registry.sqlite3.OperationalError("locked")):
            with self.assertRaises(BrokerGuardError):
                self.registry.emergency_stop(actor="test", reason="db locked")
        self.assertTrue(load_broker_config(ENV).demo_enabled)
        with self.assertRaises(BrokerGuardError):
            submit(broker, plan())
        with self.assertRaises(BrokerGuardError):
            broker.modify_stop("P1", Decimal("1.09"), client_id="m")
        with self.assertRaises(BrokerGuardError):
            self.broker()  # constructor guard honours the latch too
        self.assertEqual(self.server.calls, [])


class DemoGuardTests(BrokerTestBase):
    def assertRefused(self, env=None, **kwargs):
        transport_calls = []

        def transport(*args, **_kw):
            transport_calls.append(args)
            raise AssertionError("network must not be touched")

        with self.assertRaises(BrokerGuardError):
            self.broker(env=env, transport=transport, **kwargs)
        self.assertEqual(transport_calls, [])

    def test_disabled_flag_means_zero_network(self):
        for value in (None, "0", "", "true", "yes", " 2"):
            env = {k: v for k, v in ENV.items() if k != "AI_FLOOR_BROKER_DEMO_ENABLED"}
            if value is not None:
                env["AI_FLOOR_BROKER_DEMO_ENABLED"] = value
            with self.subTest(value=value):
                self.assertRefused(env=env)

    def test_wrong_mode_or_account_refused(self):
        for mode in ("real", "paper", "BROKER_DEMO", None):
            with self.subTest(mode=mode):
                self.assertRefused(mode=mode)
        self.assertRefused(account_code="DEM_9999999_1")  # Not the configured one.
        for code in ("ECN_2775212_1", "ECN_2775212_2", "VIP_1", "NOC_1", "ecn_1", "LIVE_1", ""):
            with self.subTest(code=code):
                self.assertRefused(env={**ENV, "DXTRADE_ACCOUNT_CODE": code}, account_code=code)
        self.assertRefused(env={**ENV, "DXTRADE_ACCOUNT_CODE": ""}, account_code=ACCOUNT)

    def test_real_enabled_env_is_ignored(self):
        env = {**ENV, "AI_FLOOR_BROKER_REAL_ENABLED": "1"}
        config = load_broker_config(env)
        self.assertTrue(config.real_requested)
        self.assertIs(config.real_enabled, False)
        self.assertRefused(env=env, mode="real")
        self.assertRefused(env={**env, "AI_FLOOR_BROKER_DEMO_ENABLED": "0"})
        from runtime import demo_runner
        self.assertIs(demo_runner.REAL_EXECUTION_ENABLED, False)
        self.assertEqual(self.broker(env=env).mode, "broker_demo")  # demo still demo, nothing more

    def test_real_execution_flag_must_stay_false(self):
        with patch("runtime.demo_runner.REAL_EXECUTION_ENABLED", True):
            self.assertRefused()

    def test_emergency_switch_registry_and_credentials(self):
        self.assertRefused(env={**ENV, "AI_FLOOR_EMERGENCY_STOP": "1"})
        self.assertRefused(env={**ENV, "AI_FLOOR_EMERGENCY_STOP": "maybe"})  # unknown -> active
        self.assertRefused(registry=None)
        self.assertRefused(env={**ENV, "DXTRADE_PASSWORD": ""})
        self.registry.set_enabled("broker_demo", False, actor="test")
        self.assertRefused()
        self.registry.set_enabled("broker_demo", True, actor="test")
        self.registry.emergency_stop(actor="test", reason="drill")
        self.assertRefused()

    def test_switch_off_after_construction_blocks_opening_not_closing(self):
        broker = self.broker()
        self.registry.emergency_stop(actor="test", reason="drill")
        with self.assertRaises(BrokerGuardError):
            submit(broker, plan())
        with self.assertRaises(BrokerGuardError):
            broker.modify_stop("P1", Decimal("1.09"), client_id="m")
        self.assertEqual(self.server.calls, [])
        self.server.add("GET", f"accounts/{ACCOUNT}/positions", (200, {"positions": [
            {"positionCode": "P1", "symbol": "EURUSD", "side": "SELL", "quantity": "10"}]}))
        self.server.add("POST", ORDERS, (200, {"orderId": 1}))
        self.assertEqual(broker.close_market("P1", reason="EMERGENCY", client_id="c").status, ACCEPTED)
        self.assertEqual(self.server.calls[-1][3]["side"], "BUY")


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
