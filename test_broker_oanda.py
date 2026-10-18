"""OANDA practice adapter (EURUSD only): guards, sizing, pre-submit gate, idempotency, response mapping, modify/close,
positions, reconciliation, redaction. Fake transport only; the default urllib transport is patched to fail."""
from decimal import Decimal
import json
import logging
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from execution import account_registry
from execution.account_registry import AccountRegistry
from execution.broker_port import (ACCEPTED, REJECTED, UNKNOWN, BrokerGuardError, BrokerMalformedResponse, BrokerPort,
                                   BrokerUnavailable, OrderPlan, RiskApproval)
from execution import oanda_broker
from execution.oanda_broker import OandaBroker, client_extension_id, size_units
from execution.oanda_client import PRACTICE_REST_URL
from test_broker_oanda_client import A, ACCOUNT, MASKED, TOKEN, FakeClock, FakeServer, raising_transport

ENV = {"AI_FLOOR_BROKER_DEMO_ENABLED": "1", "AI_FLOOR_BROKER_DEMO_PROVIDER": "oanda", "OANDA_PRACTICE_TOKEN": TOKEN,
       "OANDA_ACCOUNT_ID": ACCOUNT}
PRICING = f"{A}/pricing?instruments=EUR_USD"
OPEN_TRADES = f"{A}/openTrades"
ORDERS = f"{A}/orders"
NOT_FOUND = (404, {"errorCode": "TRADE_DOES_NOT_EXIST", "errorMessage": "The Trade specified does not exist"})


def plan(side="LONG", client_id="cid-1", symbol="EURUSD", quantity="100", entry="1.09000", stop=None, target=None):
    default_stop, default_target = ("1.08000", "1.12000") if side == "LONG" else ("1.10000", "1.06000")
    return OrderPlan(symbol, side, Decimal(quantity), Decimal(entry), Decimal(stop or default_stop),
                     Decimal(target or default_target), client_id=client_id, source_run_id="run-1")


def approved(p, status="APPROVED"):
    return RiskApproval(status, p.source_run_id, p.symbol, p.side, p.quantity, p.planned_entry, p.stop, p.target)


def submit(broker, p):
    return broker.submit_plan(p, approved(p))


def trade(trade_id="6397", units="100", instrument="EUR_USD", sl="1.08000", tp="1.12000", ext=None, state="OPEN"):
    item = {"id": trade_id, "instrument": instrument, "price": "1.09012", "currentUnits": units,
            "initialUnits": units, "state": state}
    if sl is not None:
        item["stopLossOrder"] = {"id": "1", "price": sl}
    if tp is not None:
        item["takeProfitOrder"] = {"id": "2", "price": tp}
    if ext is not None:
        item["clientExtensions"] = {"id": ext, "tag": "aifloor"}
    return item


def pricing(bid="1.09500", ask="1.09510", tradeable=True):
    return (200, {"prices": [{"instrument": "EUR_USD", "tradeable": tradeable, "bids": [{"price": bid}],
                              "asks": [{"price": ask}]}]})


FILLED = (201, {"orderCreateTransaction": {"id": "100"},
                "orderFillTransaction": {"id": "101", "tradeOpened": {"tradeID": "6397", "units": "100"}}})


def sl_tx(price, tx_id="5"):
    return {"id": tx_id, "type": "STOP_LOSS", "tradeID": "6397", "price": price, "timeInForce": "GTC"}


def close_fill(tx_id="130", trade_id="6397"):
    return {"id": tx_id, "tradesClosed": [{"tradeID": trade_id, "units": "-100"}]}


def hooked(server, hooks):
    """``server`` with ``hooks[(method, path)]()`` run after that call answered (e.g. to flip the switch mid-gate)."""
    def transport(method, url, headers, body, timeout):
        response = server(method, url, headers, body, timeout)
        hook = hooks.get((method, url[len(PRACTICE_REST_URL):]))
        if hook is not None:
            hook()
        return response
    return transport


class OandaBrokerBase(unittest.TestCase):
    def setUp(self):
        default = patch("execution.oanda_client.urllib_transport", side_effect=raising_transport)
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
        options = {"mode": "broker_demo", "registry": self.registry, "transport": self.server, "clock": self.clock,
                   "sleep": self.clock.sleep}
        options.update(kwargs)
        return OandaBroker(env=ENV if env is None else env, **options)

    def ready(self, client_id="cid-1", trades=(), price=None, order=FILLED):
        """Routes for a clean pre-submit gate and the order POST."""
        self.server.add("GET", f"{A}/trades/@{client_extension_id(client_id)}", NOT_FOUND)
        self.server.add("GET", OPEN_TRADES, (200, {"trades": list(trades)}))
        self.server.add("GET", PRICING, price or pricing())
        if order is not None:
            self.server.add("POST", ORDERS, order)


class SizingTests(unittest.TestCase):
    def test_size_units(self):
        self.assertEqual(size_units(Decimal("100"), Decimal("1.09000"), Decimal("1.08000")), 10000)
        self.assertEqual(size_units(Decimal("10"), Decimal("1.09000"), Decimal("1.08997")), 333333)  # floor
        self.assertEqual(size_units(Decimal("100"), Decimal("1.08000"), Decimal("1.09000")), 10000)  # SHORT
        self.assertEqual(size_units(Decimal("0.001"), Decimal("1.09"), Decimal("1.08")), 0)  # < 1 unit -> skip
        for args in ((Decimal("0"), Decimal("1.09"), Decimal("1.08")), (Decimal("-1"), Decimal("1.09"), Decimal("1.08")),
                     (Decimal("10"), Decimal("1.09"), Decimal("1.09")), (Decimal("10"), Decimal("0"), Decimal("1")),
                     (10.0, Decimal("1.09"), Decimal("1.08")), (Decimal("NaN"), Decimal("1.09"), Decimal("1.08"))):
            with self.subTest(args=args), self.assertRaises(ValueError):
                size_units(*args)

    def test_client_extension_id(self):
        self.assertEqual(client_extension_id("cid-1"), "cid-1")
        long_id = "x" * 200
        for unsafe in (long_id, "a b", "a/b", "@x", "ñ", "id;drop"):
            with self.subTest(unsafe=unsafe):
                ext = client_extension_id(unsafe)
                self.assertRegex(ext, r"^aif-[0-9a-f]{32}$")
                self.assertEqual(ext, client_extension_id(unsafe))  # deterministic
        self.assertNotEqual(client_extension_id("a b"), client_extension_id("a  b"))
        with self.assertRaises(ValueError):
            client_extension_id("")


class GuardTests(OandaBrokerBase):
    def assertRefused(self, env=None, **kwargs):
        calls = []

        def transport(*args, **_kw):
            calls.append(args)
            raise AssertionError("network must not be touched")

        with self.assertRaises(BrokerGuardError) as ctx:
            self.broker(env=env, transport=transport, **kwargs)
        self.assertEqual(calls, [])
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_flags_mode_and_provider(self):
        for value in (None, "0", "", "true", " 2"):
            env = {k: v for k, v in ENV.items() if k != "AI_FLOOR_BROKER_DEMO_ENABLED"}
            if value is not None:
                env["AI_FLOOR_BROKER_DEMO_ENABLED"] = value
            with self.subTest(enabled=value):
                self.assertRefused(env=env)
        for provider in (None, "dxtrade", "OANDA-live", "fxtrade"):
            env = {k: v for k, v in ENV.items() if k != "AI_FLOOR_BROKER_DEMO_PROVIDER"}
            if provider is not None:
                env["AI_FLOOR_BROKER_DEMO_PROVIDER"] = provider
            with self.subTest(provider=provider):
                self.assertRefused(env=env)
        for mode in ("real", "paper", "BROKER_DEMO", None):
            with self.subTest(mode=mode):
                self.assertRefused(mode=mode)
        self.assertRefused(env={**ENV, "AI_FLOOR_BROKER_REAL_ENABLED": "1"}, mode="real")

    def test_real_execution_flag_must_stay_false(self):
        with patch("runtime.demo_runner.REAL_EXECUTION_ENABLED", True):
            self.assertRefused()

    def test_emergency_switch_and_registry(self):
        self.assertRefused(env={**ENV, "AI_FLOOR_EMERGENCY_STOP": "1"})
        self.assertRefused(env={**ENV, "AI_FLOOR_EMERGENCY_STOP": "maybe"})
        self.assertRefused(registry=None)
        self.registry.set_enabled("broker_demo", False, actor="test")
        self.assertRefused()
        self.registry.set_enabled("broker_demo", True, actor="test")
        self.registry.emergency_stop(actor="test", reason="drill")
        self.assertRefused()

    def test_token_and_account(self):
        for token in (None, "", "   "):
            env = {k: v for k, v in ENV.items() if k != "OANDA_PRACTICE_TOKEN"}
            if token is not None:
                env["OANDA_PRACTICE_TOKEN"] = token
            with self.subTest(token=token):
                self.assertRefused(env=env)
        for account in ("001-001-12345678-001", "101-001-12345678", "DEM_1_1", "101-1-2-3", "101-001-x-001"):
            with self.subTest(account=account):
                self.assertRefused(env={**ENV, "OANDA_ACCOUNT_ID": account})
        env = {k: v for k, v in ENV.items() if k != "OANDA_ACCOUNT_ID"}
        for account in (None, "", "   "):  # OANDA_ACCOUNT_ID is required: no default lives in code
            with self.subTest(account=account):
                self.assertRefused(env=env if account is None else {**env, "OANDA_ACCOUNT_ID": account})
        self.assertFalse(hasattr(oanda_broker, "DEFAULT_ACCOUNT_ID"))
        synthetic = self.broker(env={**ENV, "OANDA_ACCOUNT_ID": "101-000-1234567-001"})
        self.assertEqual(synthetic.health().account_masked, "101-…4567-001")
        padded = self.broker(env={**ENV, "OANDA_PRACTICE_TOKEN": f"  {TOKEN}\n"})
        self.server.add("GET", f"{A}/summary", (200, {"account": {"balance": "1", "NAV": "1"}}))
        padded.get_balance()
        self.assertEqual(self.server.calls[-1][2]["Authorization"], f"Bearer {TOKEN}")

    def test_construction_is_lazy_and_implements_port(self):
        broker = self.broker()
        self.assertIsInstance(broker, BrokerPort)
        self.assertEqual(self.server.calls, [])
        health = broker.health()
        self.assertEqual((health.mode, health.account_masked, health.session_active, health.connected),
                         ("broker_demo", MASKED, True, False))


class SubmitPolicyTests(OandaBrokerBase):
    def test_symbols_other_than_eurusd_rejected_before_network(self):
        broker = self.broker()
        for symbol in ("XAUUSD", "NAS100", "EUR_USD", "eurusd", "GBPUSD", "EURUSD "):
            p = plan(symbol=symbol)
            with self.subTest(symbol=symbol):
                result = submit(broker, p)
                self.assertEqual((result.status, result.reason), (REJECTED, "symbol_not_supported"))
        self.assertEqual(self.server.calls, [])

    def test_approval_quantity_and_rounding_checks_before_network(self):
        broker = self.broker()
        p = plan()
        self.assertEqual(broker.submit_plan(p, None).reason, "risk_approval_missing")
        self.assertEqual(broker.submit_plan(p, approved(p, status="REJECTED")).reason, "risk_not_approved")
        self.assertEqual(submit(broker, plan(quantity="100.5")).reason, "quantity_not_integer")
        self.assertEqual(submit(broker, plan(stop="1.09100")).reason, "invalid_plan_geometry")
        collapsed = plan(entry="1.090004", stop="1.090001", target="1.2")
        self.assertTrue(collapsed.geometry_ok())
        self.assertEqual(submit(broker, collapsed).reason, "price_rounding_breaks_geometry")
        self.assertEqual(self.server.calls, [])

    def test_switch_off_after_construction_blocks_submit_and_modify(self):
        broker = self.broker()
        self.registry.set_enabled("broker_demo", False, actor="test")
        with self.assertRaises(BrokerGuardError):
            submit(broker, plan())
        with self.assertRaises(BrokerGuardError):
            broker.modify_stop("6397", Decimal("1.085"), client_id="m")
        self.assertEqual(self.server.calls, [])


class SubmitFlowTests(OandaBrokerBase):
    def test_order_payload_shape(self):
        self.ready()
        result = submit(self.broker(), plan())
        self.assertEqual((result.status, result.broker_ref, result.client_id), (ACCEPTED, "6397", "cid-1"))
        self.assertEqual([m for m, *_ in self.server.calls], ["GET", "GET", "GET", "POST"])  # gate reads first
        body = self.server.calls[-1][3]["order"]
        ext = {"id": "cid-1", "tag": "aifloor", "comment": "run:run-1"}
        self.assertEqual(body, {"type": "MARKET", "instrument": "EUR_USD", "units": "100", "timeInForce": "FOK",
                                "positionFill": "DEFAULT",
                                "stopLossOnFill": {"price": "1.08000", "timeInForce": "GTC"},
                                "takeProfitOnFill": {"price": "1.12000", "timeInForce": "GTC"},
                                "clientExtensions": ext, "tradeClientExtensions": ext})

    def test_short_units_negative_and_unsafe_client_id_hashed(self):
        cid = "plan/with spaces/" + "x" * 150
        self.ready(client_id=cid)
        self.assertEqual(submit(self.broker(), plan("SHORT", client_id=cid)).status, ACCEPTED)
        body = self.server.calls[-1][3]["order"]
        self.assertEqual((body["units"], body["stopLossOnFill"]["price"], body["takeProfitOnFill"]["price"]),
                         ("-100", "1.10000", "1.06000"))
        self.assertRegex(body["clientExtensions"]["id"], r"^aif-[0-9a-f]{32}$")
        self.assertEqual(body["clientExtensions"], body["tradeClientExtensions"])

    def test_existing_client_id_returns_accepted_without_second_post(self):
        self.server.add("GET", f"{A}/trades/@cid-1", (200, {"trade": trade(trade_id="77", ext="cid-1")}))
        result = submit(self.broker(), plan())
        self.assertEqual((result.status, result.broker_ref, result.reason), (ACCEPTED, "77", "already_submitted"))
        self.assertEqual(self.server.paths("POST"), [])
        server = FakeServer().add("GET", f"{A}/trades/@cid-1", NOT_FOUND)
        server.add("GET", OPEN_TRADES, (200, {"trades": [trade(trade_id="78", ext="cid-1")]}))
        result = submit(self.broker(transport=server), plan())
        self.assertEqual((result.status, result.broker_ref, result.reason), (ACCEPTED, "78", "already_submitted"))
        self.assertEqual(server.paths("POST"), [])

    def test_gate_read_failure_rejects_without_post(self):
        for response in ((404, {"errorCode": "SOMETHING_ELSE"}), (502, {}), TimeoutError("slow"), (200, {"x": 1})):
            server = FakeServer().add("GET", f"{A}/trades/@cid-1", NOT_FOUND)
            server.add("GET", OPEN_TRADES, response)
            with self.subTest(response=response):
                result = submit(self.broker(transport=server), plan())
                self.assertEqual(result.status, REJECTED)
                self.assertTrue(result.reason.startswith("pre_submit_check_failed:"))
                self.assertEqual(server.paths("POST"), [])

    def test_hedge_and_scale_in_policy(self):
        self.ready(trades=[trade(units="-50")], order=None)
        result = submit(self.broker(), plan("LONG"))
        self.assertEqual((result.status, result.reason), (REJECTED, "would_hedge_or_net"))
        server = FakeServer()
        self.server = server
        self.ready(trades=[trade(units="50")], order=None)
        result = submit(self.broker(), plan("LONG"))
        self.assertEqual((result.status, result.reason), (REJECTED, "position_already_open"))
        self.assertEqual(server.paths("POST"), [])
        self.server = FakeServer()
        self.ready(trades=[trade(units="50"), trade(trade_id="9", instrument="XAU_USD", units="-1")])
        self.assertEqual(submit(self.broker(allow_scale_in=True), plan("LONG")).status, ACCEPTED)
        self.server = FakeServer()
        self.ready(trades=[trade(units="-50")], order=None)
        self.assertEqual(submit(self.broker(allow_scale_in=True), plan("LONG")).reason, "would_hedge_or_net")

    def test_not_tradeable_rejected(self):
        self.ready(price=pricing(tradeable=False), order=None)
        result = submit(self.broker(), plan())
        self.assertEqual((result.status, result.reason), (REJECTED, "instrument_not_tradeable"))
        self.assertEqual(self.server.paths("POST"), [])


class SubmitResponseMappingTests(OandaBrokerBase):
    def run_case(self, response):
        self.server = FakeServer()
        self.ready(order=response)
        return submit(self.broker(), plan())

    def test_fok_cancel_and_reject_map_to_code_only(self):
        cases = [
            ((201, {"orderCreateTransaction": {}, "orderCancelTransaction": {"id": "7", "reason": "MARKET_HALTED"}}),
             "MARKET_HALTED"),
            ((201, {"orderCreateTransaction": {},
                    "orderCancelTransaction": {"id": "7", "reason": "FOK not filled, see " + TOKEN}}),
             "UNRECOGNIZED_REASON"),
            ((201, {"orderRejectTransaction": {"id": "7", "rejectReason": "INSUFFICIENT_MARGIN"}}),
             "INSUFFICIENT_MARGIN"),
            ((201, {"orderRejectTransaction": {"id": "7", "reason": "CLIENT_ORDER"}}), "CLIENT_ORDER"),
            ((400, {"orderRejectTransaction": {"rejectReason": "INSUFFICIENT_MARGIN"},
                    "errorCode": "INSUFFICIENT_MARGIN", "errorMessage": f"account {ACCOUNT} no margin"}),
             "INSUFFICIENT_MARGIN"),
            ((400, {"errorMessage": "bad"}), "broker_rejected:400"),
        ]
        for response, reason in cases:
            with self.subTest(reason=reason):
                result = self.run_case(response)
                self.assertEqual((result.status, result.reason, result.broker_ref), (REJECTED, reason, None))
                self.assertEqual(len(self.server.paths("POST")), 1)

    def test_success_without_fill_evidence_is_unknown(self):
        cases = [((201, {"orderCreateTransaction": {"id": "1"}}), "no_fill_evidence"),
                 ((201, {}), "no_acceptance_evidence"), ((200, []), "no_acceptance_evidence"),
                 ((201, {"orderFillTransaction": {"id": "2"}}), "malformed_fill_evidence"),
                 ((201, {"orderFillTransaction": {"id": "2", "tradeOpened": {"tradeID": 5}}}), "malformed_fill_evidence")]
        for response, reason in cases:
            with self.subTest(reason=reason):
                result = self.run_case(response)
                self.assertEqual((result.status, result.reason), (UNKNOWN, reason))

    def test_timeout_reconciles_once_and_finds_trade(self):
        self.ready(order=TimeoutError("slow"))
        self.server.routes[("GET", f"{A}/trades/@cid-1")] = [NOT_FOUND, (200, {"trade": trade(ext="cid-1")})]
        result = submit(self.broker(), plan())
        self.assertEqual((result.status, result.broker_ref, result.reason),
                         (ACCEPTED, "6397", "reconciled_after_timeout"))
        self.assertEqual(self.server.count("POST", ORDERS), 1)
        self.assertEqual(self.server.count("GET", f"{A}/trades/@cid-1"), 2)  # gate + ONE reconciliation read

    def test_timeout_not_found_stays_unknown_and_never_reposts(self):
        self.ready(order=TimeoutError("slow"))
        result = submit(self.broker(), plan())
        self.assertEqual((result.status, result.reason), (UNKNOWN, "BrokerTimeout:not_found_unproven"))
        self.assertEqual(self.server.count("POST", ORDERS), 1)
        self.assertEqual(self.server.count("GET", f"{A}/trades/@cid-1"), 2)

    def test_5xx_on_post_not_retried_and_failed_reconcile_is_unknown(self):
        self.ready(order=(503, {}))
        self.server.routes[("GET", f"{A}/trades/@cid-1")] = [NOT_FOUND, TimeoutError("slow")]
        result = submit(self.broker(), plan())
        self.assertEqual((result.status, result.reason), (UNKNOWN, "BrokerUnavailable:reconcile_failed"))
        self.assertEqual(self.server.count("POST", ORDERS), 1)


class ModifyStopTests(OandaBrokerBase):
    def setup_trade(self, units="100", sl="1.08000", price=None):
        self.server.add("GET", f"{A}/trades/6397", (200, {"trade": trade(units=units, sl=sl)}))
        self.server.add("GET", PRICING, price or pricing())

    def test_tighten_ok_keeps_take_profit_untouched(self):
        self.setup_trade()
        self.server.add("PUT", f"{A}/trades/6397/orders", (200, {"stopLossOrderTransaction": sl_tx("1.08500", "120"),
                                                                 "stopLossOrderCancelTransaction": {"id": "119"}}))
        result = self.broker().modify_stop("6397", Decimal("1.085"), client_id="m1")
        self.assertEqual((result.status, result.broker_ref), (ACCEPTED, "120"))
        self.assertEqual(self.server.calls[-1][3], {"stopLoss": {"price": "1.08500", "timeInForce": "GTC"}})

    def test_widen_and_wrong_side_rejected_without_put(self):
        cases = [("100", Decimal("1.07999"), "would_widen_stop"), ("100", Decimal("1.09500"), "stop_wrong_side_of_market"),
                 ("100", Decimal("1.10"), "stop_wrong_side_of_market"),
                 ("-100", Decimal("1.10001"), "would_widen_stop"), ("-100", Decimal("1.09510"), "stop_wrong_side_of_market")]
        for units, stop, reason in cases:
            self.server = FakeServer()
            self.setup_trade(units=units, sl="1.08000" if units == "100" else "1.10000")
            with self.subTest(units=units, stop=stop):
                result = self.broker().modify_stop("6397", stop, client_id="m")
                self.assertEqual((result.status, result.reason), (REJECTED, reason))
                self.assertEqual(self.server.paths("PUT"), [])

    def test_short_tighten_and_equal_stop_allowed(self):
        for stop in (Decimal("1.09900"), Decimal("1.10000")):
            self.server = FakeServer()
            self.setup_trade(units="-100", sl="1.10000")
            self.server.add("PUT", f"{A}/trades/6397/orders", (200, {"stopLossOrderTransaction": sl_tx(f"{stop:f}")}))
            with self.subTest(stop=stop):
                self.assertEqual(self.broker().modify_stop("6397", stop, client_id="m").status, ACCEPTED)

    def test_timeout_unknown_and_rejections(self):
        self.setup_trade()
        self.server.add("PUT", f"{A}/trades/6397/orders", TimeoutError("slow"),
                        (200, {"stopLossOrderRejectTransaction": {"id": "9",
                                                                  "rejectReason": "STOP_LOSS_ON_FILL_PRICE_INVALID"}}),
                        (200, {}))
        broker = self.broker()
        result = broker.modify_stop("6397", Decimal("1.085"), client_id="m")
        self.assertEqual((result.status, result.reason), (UNKNOWN, "BrokerTimeout"))
        self.assertEqual(self.server.count("PUT", f"{A}/trades/6397/orders"), 1)
        self.assertEqual(broker.modify_stop("6397", Decimal("1.085"), client_id="m").reason,
                         "STOP_LOSS_ON_FILL_PRICE_INVALID")
        self.assertEqual(broker.modify_stop("6397", Decimal("1.085"), client_id="m").status, UNKNOWN)

    def test_invalid_inputs_closed_or_foreign_trade(self):
        broker = self.broker()
        self.assertEqual(broker.modify_stop("6397", Decimal("0"), client_id="m").reason, "invalid_stop")
        self.assertEqual(broker.modify_stop("@cid", Decimal("1.08"), client_id="m").reason, "invalid_position_ref")
        self.server.add("GET", f"{A}/trades/1", (200, {"trade": trade(trade_id="1", state="CLOSED")}))
        self.server.add("GET", f"{A}/trades/2", (200, {"trade": trade(trade_id="2", instrument="XAU_USD")}))
        self.server.add("GET", f"{A}/trades/3", NOT_FOUND)
        self.assertEqual(broker.modify_stop("1", Decimal("1.085"), client_id="m").reason, "trade_not_open")
        self.assertEqual(broker.modify_stop("2", Decimal("1.085"), client_id="m").reason, "symbol_not_supported")
        self.assertEqual(broker.modify_stop("3", Decimal("1.085"), client_id="m").reason, "no_open_position")
        self.assertEqual(self.server.paths("PUT"), [])


class CloseMarketTests(OandaBrokerBase):
    CLOSE = f"{A}/trades/6397/close"

    def test_close_ok(self):
        self.server.add("PUT", self.CLOSE, (200, {"orderCreateTransaction": {}, "orderFillTransaction": close_fill("130")}))
        result = self.broker().close_market("6397", reason="TIME_EXIT", client_id="c1")
        self.assertEqual((result.status, result.broker_ref), (ACCEPTED, "130"))
        self.assertEqual(self.server.calls[-1][3], {"units": "ALL"})

    def test_absent_trade_verified_through_open_trades(self):
        self.server.add("PUT", self.CLOSE, NOT_FOUND)
        self.server.add("GET", OPEN_TRADES, (200, {"trades": [trade(trade_id="1")]}))
        result = self.broker().close_market("6397", reason="x", client_id="c")
        self.assertEqual((result.status, result.reason), (REJECTED, "no_open_position"))

    def test_read_failure_or_timeout_is_unknown(self):
        for response in ((200, {}), (200, {"trades": [{"id": "1"}]}), TimeoutError("slow"), (502, {})):
            self.server = FakeServer().add("PUT", self.CLOSE, NOT_FOUND).add("GET", OPEN_TRADES, response)
            with self.subTest(response=response):
                result = self.broker().close_market("6397", reason="x", client_id="c")
                self.assertEqual(result.status, UNKNOWN)
                self.assertTrue(result.reason.startswith("position_state_unknown:"))
        self.server = FakeServer().add("PUT", self.CLOSE, TimeoutError("slow"))
        result = self.broker().close_market("6397", reason="x", client_id="c")
        self.assertEqual((result.status, result.reason), (UNKNOWN, "BrokerTimeout"))
        self.assertEqual(self.server.count("PUT", self.CLOSE), 1)
        self.assertEqual(self.broker().close_market("bad", reason="x", client_id="c").reason, "invalid_position_ref")

    def test_allowed_with_switch_off_and_emergency(self):
        broker = self.broker()
        self.registry.emergency_stop(actor="test", reason="drill")
        self.server.add("PUT", self.CLOSE, (200, {"orderFillTransaction": close_fill("131")}))
        self.assertEqual(broker.close_market("6397", reason="EMERGENCY", client_id="c").status, ACCEPTED)


class PositionsAndReconcileTests(OandaBrokerBase):
    def test_positions_mapping_and_unsupported(self):
        self.server.add("GET", OPEN_TRADES, (200, {"trades": [
            trade(), trade(trade_id="7", units="-3", instrument="XAU_USD", sl=None, tp=None),
            trade(trade_id="8", units="1000", instrument="GBP_JPY")]}))
        broker = self.broker()
        with self.assertLogs("ai_floor.broker.oanda", level="WARNING") as logs:
            positions = broker.get_positions()
        self.assertNotIn(ACCOUNT, "\n".join(logs.output))
        eur, gold = positions
        self.assertEqual((eur.position_ref, eur.symbol, eur.side, eur.quantity, eur.entry_price, eur.stop, eur.target),
                         ("6397", "EURUSD", "LONG", Decimal("100"), Decimal("1.09012"), Decimal("1.08000"),
                          Decimal("1.12000")))
        self.assertEqual((gold.symbol, gold.side, gold.quantity, gold.stop), ("XAUUSD", "SHORT", Decimal("3"), None))
        self.assertEqual(broker.unsupported_open_trades(), [("7", "XAU_USD"), ("8", "GBP_JPY")])

    def test_malformed_raises_never_empty(self):
        good = trade()
        payloads = [{}, {"trades": None}, {"trades": {}}, [], "x", {"trades": [good, "x"]},
                    {"trades": [{**good, "currentUnits": "0"}]}, {"trades": [{**good, "currentUnits": "abc"}]},
                    {"trades": [{**good, "currentUnits": None}]}, {"trades": [{**good, "currentUnits": "1.5"}]},
                    {"trades": [{**good, "id": ""}]}, {"trades": [{**good, "price": "-1"}]},
                    {"trades": [{k: v for k, v in good.items() if k != "instrument"}]},
                    {"trades": [{**good, "stopLossOrder": {"price": "bad"}}]}]
        for payload in payloads:
            server = FakeServer().add("GET", OPEN_TRADES, (200, payload))
            with self.subTest(payload=payload), self.assertRaises(BrokerMalformedResponse):
                self.broker(transport=server).get_positions()
        server = FakeServer().add("GET", OPEN_TRADES, (200, {"trades": []}))
        self.assertEqual(self.broker(transport=server).get_positions(), [])

    def test_reconcile_report(self):
        self.server.add("GET", OPEN_TRADES, (200, {"trades": [
            trade(trade_id="1", ext="cid-a"), trade(trade_id="2"), trade(trade_id="3", ext="aif-x"),
            trade(trade_id="9", instrument="XAU_USD")]}))
        report = self.broker().reconcile(["cid-a", "2", "404", "cid-gone"])
        self.assertEqual(report.ok_refs, ("cid-a", "2"))
        self.assertEqual(report.missing_at_broker, ("404", "cid-gone"))
        self.assertEqual(report.unknown_at_broker, ("3", "9"))
        self.assertEqual(report.unsupported, (("9", "XAU_USD"),))

    def test_reconcile_read_failure_raises(self):
        for response in ((200, {}), (502, {})):
            server = FakeServer().add("GET", OPEN_TRADES, response)
            with self.subTest(response=response), self.assertRaises((BrokerMalformedResponse, BrokerUnavailable)):
                self.broker(transport=server).reconcile(["cid-a"])

    def test_balance(self):
        self.server.add("GET", f"{A}/summary", (200, {"account": {"balance": "100000.0000", "NAV": "100012.5",
                                                                  "marginUsed": "33.1", "currency": "USD"}}))
        balance = self.broker().get_balance()
        self.assertEqual((balance.balance, balance.equity, balance.margin_used, balance.currency),
                         (Decimal("100000.0000"), Decimal("100012.5"), Decimal("33.1"), "USD"))
        for payload, error in (({}, BrokerMalformedResponse), ([], BrokerMalformedResponse),
                               ({"account": {"balance": "1"}}, BrokerUnavailable)):
            server = FakeServer().add("GET", f"{A}/summary", (200, payload))
            with self.subTest(payload=payload), self.assertRaises(error):
                self.broker(transport=server).get_balance()
        self.assertIsNone(balance.last_transaction_id)
        account = {"balance": "1", "NAV": "1", "lastTransactionID": "6356"}
        for payload, expected in (({"account": account, "lastTransactionID": "6357"}, "6357"),
                                  ({"account": account}, "6356"),
                                  ({"account": {**account, "lastTransactionID": "x"}}, None),
                                  ({"account": account, "lastTransactionID": 6357}, None)):
            server = FakeServer().add("GET", f"{A}/summary", (200, payload))
            with self.subTest(payload=payload):
                self.assertEqual(self.broker(transport=server).get_balance().last_transaction_id, expected)

    def test_transfers_since(self):
        path = f"{A}/transactions/sinceid?id=100&type=TRANSFER_FUNDS"
        items = [{"id": "101", "type": "TRANSFER_FUNDS", "amount": "500.25"},
                 {"id": "102", "type": "ORDER_FILL", "amount": "x"},  # other types are ignored
                 {"id": "103", "type": "TRANSFER_FUNDS", "amount": "-200"},
                 {"id": "105", "type": "TRANSFER_FUNDS", "amount": "1000"}]
        self.server.add("GET", path, (200, {"transactions": items, "lastTransactionID": "105"}))
        broker = self.broker()
        self.assertEqual(broker.transfers_since("100"), (Decimal("1300.25"), "105"))
        self.assertEqual(broker.transfers_since("100", upto="104"), (Decimal("300.25"), "104"))
        self.assertEqual(self.server.paths(), [path, path])
        bad_items = ({"id": "101", "type": "TRANSFER_FUNDS", "amount": "NaN"},
                     {"id": "101", "type": "TRANSFER_FUNDS"}, {"type": "TRANSFER_FUNDS", "amount": "1"}, "x")
        for payload in ({}, [], {"transactions": {}, "lastTransactionID": "1"}, {"transactions": []},
                        *({"transactions": [item], "lastTransactionID": "105"} for item in bad_items)):
            server = FakeServer().add("GET", path, (200, payload))
            with self.subTest(payload=payload), self.assertRaises(BrokerMalformedResponse):
                self.broker(transport=server).transfers_since("100")
        self.assertEqual([m for m, *_ in self.server.calls], ["GET", "GET"])


def pricing_item(**overrides):
    item = {"instrument": "EUR_USD", "tradeable": True, "bids": [{"price": "1.09500"}], "asks": [{"price": "1.09510"}]}
    item.update(overrides)
    return (200, {"prices": [{k: v for k, v in item.items() if v is not ...}]})


class UnresolvedLatchTests(OandaBrokerBase):
    def test_timeout_then_repeat_posts_exactly_once(self):
        self.ready(order=TimeoutError("slow"))
        broker = self.broker()
        self.assertEqual(submit(broker, plan()).status, UNKNOWN)
        self.assertEqual(broker.unresolved_client_ids(), ("cid-1",))
        for _ in range(2):
            result = submit(broker, plan())
            self.assertEqual((result.status, result.reason), (UNKNOWN, "previous_submission_unresolved"))
        self.assertEqual(self.server.count("POST", ORDERS), 1)
        self.assertEqual(self.server.count("GET", OPEN_TRADES), 1)  # the gate is not re-run while latched
        self.server.routes[("GET", f"{A}/trades/@cid-1")] = [TimeoutError("slow")]
        self.assertEqual(submit(broker, plan()).reason, "previous_submission_unresolved")
        self.server.routes[("GET", f"{A}/trades/@cid-1")] = [(200, {"trade": trade(ext="cid-1")})]
        result = submit(broker, plan())
        self.assertEqual((result.status, result.broker_ref, result.reason), (ACCEPTED, "6397", "already_submitted"))
        self.assertEqual(broker.unresolved_client_ids(), ())
        self.assertEqual(self.server.count("POST", ORDERS), 1)

    def test_unknown_mapping_latches_and_definite_answers_clear(self):
        self.ready(order=(201, {"orderCreateTransaction": {"id": "1"}}))
        broker = self.broker()
        self.assertEqual(submit(broker, plan()).reason, "no_fill_evidence")
        self.assertEqual(broker.unresolved_client_ids(), ("cid-1",))
        self.assertEqual(submit(broker, plan()).reason, "previous_submission_unresolved")
        self.assertEqual(self.server.count("POST", ORDERS), 1)
        for order in ((201, {"orderCancelTransaction": {"id": "7", "reason": "MARKET_HALTED"}}), (400, {"errorCode": "X"}),
                      FILLED):
            self.server = FakeServer()
            self.ready(client_id="cid-2", order=order)
            broker = self.broker()
            with self.subTest(order=order):
                self.assertIn(submit(broker, plan(client_id="cid-2")).status, (REJECTED, ACCEPTED))
                self.assertEqual(broker.unresolved_client_ids(), ())


class FreshSwitchCheckTests(OandaBrokerBase):
    def test_switch_off_during_pricing_read_blocks_post(self):
        self.ready()
        off = {("GET", PRICING): lambda: self.registry.set_enabled("broker_demo", False, actor="test")}
        broker = self.broker(transport=hooked(self.server, off))
        with self.assertRaises(BrokerGuardError):
            submit(broker, plan())
        self.assertEqual(self.server.paths("POST"), [])
        self.assertEqual(broker.unresolved_client_ids(), ())

    def test_emergency_during_modify_reads_blocks_put_but_close_allowed(self):
        self.server.add("GET", f"{A}/trades/6397", (200, {"trade": trade()}))
        self.server.add("GET", PRICING, pricing())
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": close_fill()}))
        stop = {("GET", PRICING): lambda: self.registry.emergency_stop(actor="test", reason="drill")}
        broker = self.broker(transport=hooked(self.server, stop))
        with self.assertRaises(BrokerGuardError):
            broker.modify_stop("6397", Decimal("1.085"), client_id="m")
        self.assertEqual(self.server.paths("PUT"), [])
        self.assertEqual(broker.close_market("6397", reason="EMERGENCY", client_id="c").status, ACCEPTED)


class SleepingTradingLimiter:
    """Limiter that waits (``sleep``) before every trading request, like a real one with a recent trading call."""

    def __init__(self, sleep):
        self.sleep, self.trading_waits = sleep, 0

    def acquire(self, category):
        if category == "trading":
            self.trading_waits += 1
            self.sleep(0.5)


class PreSendGuardTests(OandaBrokerBase):
    CLOSE = f"{A}/trades/6397/close"

    def emergency(self):
        self.registry.emergency_stop(actor="test", reason="drill")

    def test_emergency_during_rate_limit_sleep_blocks_post_but_close_allowed(self):
        self.ready()
        self.server.add("PUT", self.CLOSE, (200, {"orderFillTransaction": close_fill()}))

        def sleep(seconds):
            self.clock.sleep(seconds)
            self.emergency()

        limiter = SleepingTradingLimiter(sleep)
        broker = self.broker(limiter=limiter)
        with self.assertRaises(BrokerGuardError):
            submit(broker, plan())
        self.assertEqual((limiter.trading_waits, self.server.paths("POST")), (1, []))
        self.assertEqual(broker.unresolved_client_ids(), ())
        self.assertEqual(broker.close_market("6397", reason="EMERGENCY", client_id="c").status, ACCEPTED)
        self.assertEqual(limiter.trading_waits, 2)  # close also waited, but is never guarded

    def test_emergency_during_429_backoff_blocks_repost_but_close_allowed(self):
        self.ready(order=None)
        self.server.add("POST", ORDERS, (429, {}), FILLED)
        self.server.add("PUT", self.CLOSE, (429, {}), (200, {"orderFillTransaction": close_fill()}))

        def sleep(seconds):
            self.clock.sleep(seconds)
            if self.server.count("POST", ORDERS):  # only the backoff after the first (429) POST attempt
                self.emergency()

        broker = self.broker(sleep=sleep)
        with self.assertRaises(BrokerGuardError):
            submit(broker, plan())
        self.assertEqual(self.server.count("POST", ORDERS), 1)
        self.assertIn(1.0, self.clock.sleeps)
        self.assertEqual(broker.unresolved_client_ids(), ())
        self.assertEqual(broker.close_market("6397", reason="EMERGENCY", client_id="c").status, ACCEPTED)
        self.assertEqual(self.server.count("PUT", self.CLOSE), 2)  # close retried its 429 despite the emergency

    def test_emergency_during_429_backoff_blocks_modify_put(self):
        self.server.add("GET", f"{A}/trades/6397", (200, {"trade": trade()}))
        self.server.add("GET", PRICING, pricing())
        self.server.add("PUT", f"{A}/trades/6397/orders", (429, {}), (200, {"stopLossOrderTransaction": sl_tx("1.08500")}))

        def sleep(seconds):
            self.clock.sleep(seconds)
            if self.server.paths("PUT"):
                self.emergency()

        broker = self.broker(sleep=sleep)
        with self.assertRaises(BrokerGuardError):
            broker.modify_stop("6397", Decimal("1.085"), client_id="m")
        self.assertEqual(self.server.count("PUT", f"{A}/trades/6397/orders"), 1)


class ConcurrencyTests(OandaBrokerBase):
    def race(self, respond, first, second):
        """``first(broker)`` runs here; at its pricing read ``second(broker)`` starts on another thread. Returns both
        results and whether the second thread reached the transport while the first was still mid-call."""
        entered, out, threads, calls = threading.Event(), {}, [], []

        def transport(method, url, headers, body, timeout):
            path = url[len(PRACTICE_REST_URL):]
            worker = threading.current_thread() is not threading.main_thread()
            if worker:
                entered.set()
            calls.append((method, path))
            status, payload = respond(method, path, json.loads(body) if body else None)
            if not worker and (method, path) == ("GET", PRICING) and not threads:
                thread = threading.Thread(target=lambda: out.setdefault("second", second(broker)), daemon=True)
                threads.append(thread)
                thread.start()
                out["interleaved"] = entered.wait(0.3)
            return status, {}, json.dumps(payload).encode()

        broker = self.broker(transport=transport)
        first_result = first(broker)
        threads[0].join(5)
        self.assertFalse(threads[0].is_alive())
        return first_result, out["second"], out["interleaved"], calls

    def test_second_submit_sees_first_trade(self):
        opened = []

        def respond(method, path, body):
            if path.startswith(f"{A}/trades/@"):
                return NOT_FOUND
            if path == OPEN_TRADES:
                return 200, {"trades": list(opened)}
            if path == PRICING:
                return pricing()
            opened.append(trade(ext=body["order"]["clientExtensions"]["id"]))
            return FILLED

        for side, reason in (("LONG", "position_already_open"), ("SHORT", "would_hedge_or_net")):
            opened.clear()
            with self.subTest(side=side):
                first, second, interleaved, calls = self.race(
                    respond, lambda b: submit(b, plan(client_id="cid-1")),
                    lambda b, side=side: submit(b, plan(side, client_id="cid-2")))
                self.assertFalse(interleaved)
                self.assertEqual(first.status, ACCEPTED)
                self.assertEqual((second.status, second.reason), (REJECTED, reason))
                self.assertEqual(calls.count(("POST", ORDERS)), 1)

    def test_interleaved_modify_cannot_widen_stop(self):
        state = {"sl": "1.08000"}
        puts = []

        def respond(method, path, body):
            if path == PRICING:
                return pricing()
            if method == "GET":
                return 200, {"trade": trade(sl=state["sl"])}
            state["sl"] = body["stopLoss"]["price"]
            puts.append(state["sl"])
            return 200, {"stopLossOrderTransaction": sl_tx(state["sl"])}

        first, second, interleaved, _calls = self.race(
            respond, lambda b: b.modify_stop("6397", Decimal("1.085"), client_id="m1"),
            lambda b: b.modify_stop("6397", Decimal("1.082"), client_id="m2"))
        self.assertFalse(interleaved)
        self.assertEqual(first.status, ACCEPTED)
        self.assertEqual((second.status, second.reason), (REJECTED, "would_widen_stop"))
        self.assertEqual((puts, state["sl"]), (["1.08500"], "1.08500"))


class EvidenceAndPricingStrictnessTests(OandaBrokerBase):
    def test_submit_fill_evidence_must_be_valid(self):
        fills = [{}, "x", None, {"id": "", "tradeOpened": {"tradeID": "1"}}, {"id": "1", "tradeOpened": {}},
                 {"id": "1", "tradeOpened": {"tradeID": ""}}, {"id": "1", "tradeOpened": {"tradeID": "abc"}},
                 {"tradeOpened": {"tradeID": "6397"}}]
        for fill in fills:
            self.server = FakeServer()
            self.ready(order=(201, {"orderCreateTransaction": {"id": "1"}, "orderFillTransaction": fill}))
            broker = self.broker()
            with self.subTest(fill=fill):
                result = submit(broker, plan())
                self.assertEqual((result.status, result.reason, result.broker_ref),
                                 (UNKNOWN, "malformed_fill_evidence", None))
                self.assertEqual(broker.unresolved_client_ids(), ("cid-1",))

    def test_close_fill_evidence_must_name_the_trade(self):
        fills = [{}, "x", {"id": "", "tradesClosed": [{"tradeID": "6397"}]}, {"id": "130"},
                 {"id": "130", "tradesClosed": {"tradeID": "6397"}}, {"id": "130", "tradesClosed": [{}]},
                 {"id": "130", "tradesClosed": [{"tradeID": "6398"}]}, {"id": "130", "tradesClosed": []}]
        for fill in fills:
            self.server = FakeServer().add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": fill}))
            with self.subTest(fill=fill):
                result = self.broker().close_market("6397", reason="x", client_id="c")
                self.assertEqual((result.status, result.reason), (UNKNOWN, "malformed_fill_evidence"))

    def test_modify_evidence_must_match_requested_stop(self):
        transactions = [{}, "x", {**sl_tx("1.08500"), "id": ""}, {**sl_tx("1.08500"), "type": "TAKE_PROFIT"},
                        sl_tx("1.08400"), {k: v for k, v in sl_tx("1.08500").items() if k != "price"},
                        {k: v for k, v in sl_tx("1.08500").items() if k != "type"}]
        for transaction in transactions:
            self.server = FakeServer().add("GET", f"{A}/trades/6397", (200, {"trade": trade()}))
            self.server.add("GET", PRICING, pricing())
            self.server.add("PUT", f"{A}/trades/6397/orders", (200, {"stopLossOrderTransaction": transaction}))
            with self.subTest(transaction=transaction):
                result = self.broker().modify_stop("6397", Decimal("1.085"), client_id="m")
                self.assertEqual((result.status, result.reason), (UNKNOWN, "malformed_stop_evidence"))

    BAD_REJECTS = [{}, "x", None, [], {"id": "", "reason": "MARKET_HALTED"}, {"id": 7, "reason": "MARKET_HALTED"},
                   {"reason": "MARKET_HALTED"}, {"id": "7"}, {"id": "7", "reason": ""}, {"id": "7", "reason": 5},
                   {"id": "7", "rejectReason": ""}]

    def test_submit_reject_evidence_must_be_valid(self):
        for key in ("orderCancelTransaction", "orderRejectTransaction"):
            for transaction in self.BAD_REJECTS:
                self.server = FakeServer()
                self.ready(order=(201, {"orderCreateTransaction": {"id": "1"}, key: transaction}))
                broker = self.broker()
                with self.subTest(key=key, transaction=transaction):
                    result = submit(broker, plan())
                    self.assertEqual((result.status, result.reason), (UNKNOWN, "malformed_reject_evidence"))
                    self.assertEqual(broker.unresolved_client_ids(), ("cid-1",))
                    self.assertEqual(self.server.count("POST", ORDERS), 1)

    def test_close_and_modify_reject_evidence_must_be_valid(self):
        for transaction in self.BAD_REJECTS:
            self.server = FakeServer().add("PUT", f"{A}/trades/6397/close", (200, {"orderCancelTransaction": transaction}))
            with self.subTest(close=transaction):
                result = self.broker().close_market("6397", reason="x", client_id="c")
                self.assertEqual((result.status, result.reason), (UNKNOWN, "malformed_reject_evidence"))
            self.server = FakeServer().add("GET", f"{A}/trades/6397", (200, {"trade": trade()}))
            self.server.add("GET", PRICING, pricing())
            self.server.add("PUT", f"{A}/trades/6397/orders", (200, {"stopLossOrderRejectTransaction": transaction}))
            with self.subTest(modify=transaction):
                result = self.broker().modify_stop("6397", Decimal("1.085"), client_id="m")
                self.assertEqual((result.status, result.reason), (UNKNOWN, "malformed_reject_evidence"))
        self.server = FakeServer().add("PUT", f"{A}/trades/6397/close",
                                       (200, {"orderCancelTransaction": {"id": "8", "reason": "MARKET_HALTED"}}))
        result = self.broker().close_market("6397", reason="x", client_id="c")
        self.assertEqual((result.status, result.reason), (REJECTED, "MARKET_HALTED"))

    def test_tradeable_must_be_boolean(self):
        bad = [pricing_item(tradeable=...), pricing_item(tradeable=None), pricing_item(tradeable="true"),
               pricing_item(tradeable=1), pricing_item(bids=[])]
        for price in bad:
            self.server = FakeServer()
            self.ready(price=price, order=None)
            with self.subTest(price=price):
                result = submit(self.broker(), plan())
                self.assertEqual((result.status, result.reason), (REJECTED, "pricing_malformed"))
                self.assertEqual(self.server.paths("POST"), [])
            self.server = FakeServer().add("GET", f"{A}/trades/6397", (200, {"trade": trade()}))
            self.server.add("GET", PRICING, price)
            with self.subTest(modify=price):
                result = self.broker().modify_stop("6397", Decimal("1.085"), client_id="m")
                self.assertEqual((result.status, result.reason),
                                 (REJECTED, "pre_modify_check_failed:BrokerMalformedResponse"))
                self.assertEqual(self.server.paths("PUT"), [])


class RedactionTests(OandaBrokerBase):
    def test_token_and_account_never_in_repr_logs_or_errors(self):
        self.server.add("GET", OPEN_TRADES, (429, {}), (200, {"trades": [trade(trade_id="8", instrument="GBP_JPY")]}))
        self.server.add("GET", f"{A}/summary", (401, {"errorMessage": f"bad token {TOKEN} for {ACCOUNT}"}))
        broker = self.broker()
        texts = []
        with self.assertLogs("ai_floor.broker", level="DEBUG") as logs:
            broker.get_positions()
            try:
                broker.get_balance()
            except Exception as error:  # noqa: BLE001 - the text is what matters here
                texts.append(str(error))
            self.server.add("GET", f"{A}/trades/@cid-1", (400, {"errorCode": "X", "errorMessage": TOKEN}))
            texts.append(submit(broker, plan()).reason)
        text = "\n".join([*logs.output, *texts, repr(broker), str(broker), repr(broker._client), repr(broker.health())])
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(ACCOUNT, text)
        self.assertIn(MASKED, text)


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    unittest.main()
