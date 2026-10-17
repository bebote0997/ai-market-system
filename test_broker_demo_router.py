"""Broker-demo router (OANDA practice, EURUSD only): construction from the single flag, candidate gating, dry-run,
live routing with write-ahead intent, idempotency, UNKNOWN latch + reconcile, kill switch, sizing, duration rule,
drift, emergency close and the runtime hooks. Fake transport only; the default urllib transport is patched to fail."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.contracts import RiskDecision, TradePlan
from execution import account_registry
from execution import trade_duration as td
from execution.account_registry import AccountRegistry
from execution.broker_demo_router import (BrokerDemoRouter, broker_units, build_order_preview, duration_action,
                                          router_from_env)
from execution.broker_demo_state import DEFAULT_MAX_UNITS, max_units_setting, read_router_status
from execution.broker_port import BrokerGuardError, OrderPlan
from execution.oanda_client import OandaClient
from test_broker_oanda import FILLED, NOT_FOUND, OPEN_TRADES, ORDERS, PRICING, close_fill, pricing, sl_tx, trade
from test_broker_oanda_client import A, ACCOUNT, TOKEN, FakeClock, FakeServer, raising_transport

NOW = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)  # Monday; Day-1 NY close = 21:00 UTC (EDT)
BASE_ENV = {"OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": ACCOUNT, "AI_FLOOR_BROKER_DEMO_ROUTING": "1"}
SUMMARY = f"{A}/summary"
CID = "aif-run-1"
TRADE_AT_CID = f"{A}/trades/@{CID}"


def summary(nav="10000"):
    return (200, {"account": {"balance": nav, "NAV": nav, "marginUsed": "0", "currency": "USD"}})


def decision(side="LONG", status="APPROVED", symbol="EURUSD", entry=1.09, stop=1.08, target=1.12, rf=0.01):
    return RiskDecision("1.0", status, symbol, side, 1000.0, 10.0, entry, stop, target, "ok", risk_fraction=rf)


def context(run_id="run-1", symbol="EURUSD", eligible=True, final="PLAN_READY", **kwargs):
    dec = decision(symbol=symbol, **kwargs)
    plan = TradePlan("1.0", symbol, dec.side, "5m", dec.entry, dec.stop, dec.target, 3.0)
    det = SimpleNamespace(final_status=final, trade_plan=plan, risk_decision=dec)
    ai = SimpleNamespace(final_status=final, risk_decision=dec)
    return {"symbol": symbol, "run_id": run_id, "deterministic": det, "ai": ai, "eligible": eligible}


def open_trade(trade_id="6397", ext=CID, units="10000", price="1.09000", sl="1.08000", open_time="2026-10-12T14:00:00.123456789Z"):
    item = trade(trade_id=trade_id, units=units, ext=ext, sl=sl)
    item["price"] = price
    item["openTime"] = open_time
    return item


def trades(*items):
    return (200, {"trades": list(items)})


class RouterBase(unittest.TestCase):
    def setUp(self):
        default = patch("execution.oanda_client.urllib_transport", side_effect=raising_transport)
        self.default_transport = default.start()
        self.addCleanup(default.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "broker_state.db"
        self.addCleanup(account_registry._EMERGENCY_LATCHES.pop, account_registry._latch_key(self.db), None)
        self.env = {**BASE_ENV, "AI_FLOOR_BROKER_STATE_DB_PATH": str(self.db)}
        self.server, self.clock, self.now = FakeServer(), FakeClock(), NOW

    def tearDown(self):
        self.default_transport.assert_not_called()

    def router(self, env=None, **extra):
        return router_from_env({**self.env, **extra} if env is None else env, transport=self.server,
                               clock=lambda: self.now, sleep=self.clock.sleep, broker_clock=self.clock)

    def registry(self):
        return AccountRegistry(self.db, env=self.env)

    def rows(self):
        with sqlite3.connect(self.db) as con:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute("SELECT * FROM broker_demo_trades ORDER BY created_at")]

    def events(self, kind=None):
        with sqlite3.connect(self.db) as con:
            rows = con.execute("SELECT kind, client_id, detail FROM broker_demo_events ORDER BY id").fetchall()
        return [(k, c, json.loads(d) if d else None) for k, c, d in rows if kind in (None, k)]

    def writes(self):
        return [(m, p) for m, p, _h, _b in self.server.calls if m != "GET"]

    def ready(self, nav="10000", order=FILLED, after=None):
        """Routes for: reconcile read, balance, the broker's pre-submit gate and the POST, then the open-time read."""
        self.server.add("GET", OPEN_TRADES, trades(), trades(), trades(*(after or [open_trade()])))
        self.server.add("GET", SUMMARY, summary(nav))
        self.server.add("GET", TRADE_AT_CID, NOT_FOUND)
        self.server.add("GET", PRICING, pricing())
        if order is not None:
            self.server.add("POST", ORDERS, order)


class ConstructionTests(RouterBase):
    def test_flag_off_returns_none_without_touching_anything(self):
        for value in (None, "0", "", "true", " 1", "1 ", "yes"):
            env = {k: v for k, v in self.env.items() if k != "AI_FLOOR_BROKER_DEMO_ROUTING"}
            if value is not None:
                env["AI_FLOOR_BROKER_DEMO_ROUTING"] = value
            with self.subTest(value=value):
                self.assertIsNone(self.router(env=env))
        self.assertFalse(self.db.exists())
        self.assertEqual(self.server.calls, [])

    def test_single_flag_derives_demo_env_and_enables_switch_once(self):
        env = dict(self.env)
        before = dict(os.environ)
        router = self.router()
        self.assertIsNotNone(router)
        self.assertEqual(env, self.env)  # the caller's mapping is never mutated
        self.assertNotIn("AI_FLOOR_BROKER_DEMO_ENABLED", self.env)
        self.assertEqual(dict(os.environ), before)  # nor os.environ
        audit = self.registry().audit_log()
        self.assertEqual([(r[1], r[4], r[5], r[6]) for r in audit], [("broker_demo", "1", "router_from_env", "routing_flag")])
        self.assertIsNotNone(self.router())
        self.assertEqual(len(self.registry().audit_log()), 1)  # never re-enabled / re-audited
        self.assertFalse(router.dry_run)  # dry-run is opt-in: the flag flip means live practice routing
        self.assertEqual(router.max_units, DEFAULT_MAX_UNITS)
        self.assertEqual(self.server.calls, [])  # construction never touches the network
        self.assertNotIn(TOKEN, repr(router))
        self.assertNotIn(ACCOUNT, repr(router))

    def test_operator_off_stays_off_and_emergency_wins(self):
        self.assertIsNotNone(self.router())
        registry = self.registry()
        registry.set_enabled("broker_demo", False, actor="operator")
        self.assertIsNone(self.router())
        self.assertFalse(registry.is_allowed("broker_demo"))
        registry.emergency_stop(actor="operator", reason="drill")
        self.assertIsNone(self.router())
        registry.clear_emergency(actor="operator")
        self.assertIsNone(self.router())  # clearing never re-enables
        self.assertFalse(registry.is_allowed("broker_demo"))

    def test_emergency_on_first_construction_never_enables(self):
        AccountRegistry(self.db, env=self.env).emergency_stop(actor="op", reason="x")
        self.assertIsNone(self.router())
        account_registry._EMERGENCY_LATCHES.pop(account_registry._latch_key(self.db), None)
        self.registry().clear_emergency(actor="op")
        self.assertIsNone(self.router())  # the emergency audit row counts as operator history
        self.assertIsNone(self.router(AI_FLOOR_EMERGENCY_STOP="1"))

    def test_missing_token_or_bad_account_fail_closed_with_masked_warning(self):
        env = {k: v for k, v in self.env.items() if k != "OANDA_PRACTICE_TOKEN"}
        with self.assertLogs("ai_floor.broker.demo_router", logging.WARNING) as logs:
            self.assertIsNone(self.router(env=env))
            self.assertIsNone(self.router(env={**env, "OANDA_PRACTICE_TOKEN": "  "}))
            self.assertIsNone(self.router(OANDA_ACCOUNT_ID="001-001-12345678-001"))
        self.assertNotIn(TOKEN, "\n".join(logs.output))
        self.assertNotIn("12345678", "\n".join(logs.output))

    def test_missing_account_id_fails_closed_no_default(self):
        for account in (None, "", "   "):
            env = {k: v for k, v in self.env.items() if k != "OANDA_ACCOUNT_ID"}
            if account is not None:
                env["OANDA_ACCOUNT_ID"] = account
            with self.subTest(account=account), self.assertLogs("ai_floor.broker.demo_router", logging.WARNING) as logs:
                self.assertIsNone(self.router(env=env))
            self.assertIn("reason=OANDA_ACCOUNT_ID_missing", "\n".join(logs.output))
            self.assertNotIn(TOKEN, "\n".join(logs.output))
        self.assertEqual(self.server.calls, [])

    def test_real_execution_must_stay_false(self):
        with patch("runtime.demo_runner.REAL_EXECUTION_ENABLED", True):
            self.assertIsNone(self.router())

    def test_live_host_guard_still_enforced(self):
        router = self.router()
        self.assertEqual(router._broker._client._base, "https://api-fxpractice.oanda.com/v3")
        with self.assertRaises(BrokerGuardError):
            OandaClient(TOKEN, ACCOUNT, base_url="https://api-fxtrade.oanda.com/v3", transport=self.server)

    def test_max_units_setting(self):
        self.assertEqual(max_units_setting({}), DEFAULT_MAX_UNITS)
        for raw, expected in (("500", 500), ("0", DEFAULT_MAX_UNITS), ("-3", DEFAULT_MAX_UNITS), ("x", DEFAULT_MAX_UNITS),
                              ("1.5", DEFAULT_MAX_UNITS)):
            self.assertEqual(max_units_setting({"AI_FLOOR_BROKER_DEMO_MAX_UNITS": raw}), expected)


class GatingTests(RouterBase):
    def test_other_symbols_are_never_routed_even_if_eligible(self):
        router = self.router()
        for symbol in ("XAUUSD", "NAS100", "EUR_USD", "eurusd"):
            with self.subTest(symbol=symbol):
                self.assertEqual(router.on_cycle(context(symbol=symbol), "PLAN_READY", NOW),
                                 ("NOT_ROUTED", "symbol_not_supported"))
        self.assertEqual(self.server.calls, [])
        self.assertEqual(len(self.events("NOT_ROUTED")), 4)
        self.assertEqual(self.rows(), [])

    def test_not_ready_not_eligible_not_approved_send_nothing(self):
        router = self.router()
        cases = [(context(), "NO_SETUP", "IGNORED"), (context(), "RISK_REJECTED", "IGNORED"),
                 (context(eligible=False), "PLAN_READY", "not_eligible"),
                 (context(eligible="yes"), "PLAN_READY", "not_eligible"),
                 (context(final="RISK_REJECTED"), "PLAN_READY", "not_plan_ready"),
                 (context(status="REJECTED"), "PLAN_READY", "risk_not_approved"),
                 (context(stop=1.10), "PLAN_READY", "invalid_plan_geometry")]
        mismatch = context()
        mismatch["ai"].risk_decision = decision(status="REJECTED")
        cases.append((mismatch, "PLAN_READY", "risk_not_approved"))
        drifted = context()
        drifted["deterministic"].trade_plan = TradePlan("1.0", "EURUSD", "LONG", "5m", 1.091, 1.08, 1.12, 3.0)
        cases.append((drifted, "PLAN_READY", "plan_decision_mismatch"))
        for ctx, status, expected in cases:
            with self.subTest(expected=expected):
                outcome = router.on_cycle(ctx, status, NOW)
                self.assertEqual(outcome[1] if outcome[0] == "NOT_ROUTED" else outcome[0], expected)
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.rows(), [])


class DryRunTests(RouterBase):
    def test_dry_run_builds_exact_json_and_never_posts(self):
        router = self.router(AI_FLOOR_BROKER_DEMO_DRY_RUN="1")
        self.assertTrue(router.dry_run)
        self.server.add("GET", OPEN_TRADES, trades())
        self.server.add("GET", SUMMARY, summary())
        self.server.add("GET", PRICING, pricing())
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("DRY_RUN", CID))
        self.assertEqual(self.writes(), [])
        [(_kind, cid, detail)] = self.events("DRY_RUN")
        self.assertEqual(cid, CID)
        self.assertEqual(detail["order"], {
            "type": "MARKET", "instrument": "EUR_USD", "units": "10000", "timeInForce": "FOK", "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": "1.08000", "timeInForce": "GTC"},
            "takeProfitOnFill": {"price": "1.12000", "timeInForce": "GTC"},
            "clientExtensions": {"id": CID, "tag": "aifloor", "comment": "run:run-1"},
            "tradeClientExtensions": {"id": CID, "tag": "aifloor", "comment": "run:run-1"}})
        dump = json.dumps(self.events())
        self.assertNotIn(TOKEN, dump)
        self.assertNotIn(ACCOUNT, dump)
        self.assertNotIn("Authorization", dump)
        self.assertEqual([r["status"] for r in self.rows()], ["DRY_RUN"])
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("ALREADY_ROUTED", CID))
        self.assertEqual(router.manage(NOW + timedelta(days=3)), {})
        self.assertEqual(self.writes(), [])

    def test_preview_is_pure(self):
        plan = OrderPlan("EURUSD", "SHORT", Decimal("5"), Decimal("1.09"), Decimal("1.1"), Decimal("1.05"),
                         client_id="aif-x", source_run_id="r")
        preview = build_order_preview(plan, 5)
        self.assertEqual(preview["units"], "-5")
        with self.assertRaises(ValueError):
            build_order_preview(plan, 0)
        with self.assertRaises(ValueError):
            build_order_preview(OrderPlan("XAUUSD", "LONG", Decimal("1"), Decimal("2"), Decimal("1"), Decimal("3"),
                                          client_id="c", source_run_id="r"), 1)


class LiveRoutingTests(RouterBase):
    def test_routes_one_post_with_sl_tp_and_write_ahead_intent(self):
        router = self.router()
        seen = []
        server = self.server

        def transport(method, url, headers, body, timeout):
            if method == "POST":
                seen.append([r["status"] for r in self.rows()])  # the intent exists BEFORE the send
            return server(method, url, headers, body, timeout)

        router._broker._client._transport = transport
        self.ready()
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("ACCEPTED", "6397"))
        self.assertEqual(seen, [["PENDING_SEND"]])
        posts = [b for m, p, _h, b in self.server.calls if m == "POST"]
        self.assertEqual(len(posts), 1)
        order = posts[0]["order"]
        self.assertEqual((order["units"], order["stopLossOnFill"]["price"], order["takeProfitOnFill"]["price"]),
                         ("10000", "1.08000", "1.12000"))
        [row] = self.rows()
        self.assertEqual((row["status"], row["trade_id"], row["fill_price"], row["units"]), ("OPEN", "6397", "1.09000", 10000))
        self.assertEqual(row["opened_at"], "2026-10-12 14:00:00.123456+00:00")
        # Idempotent: the same run never routes again.
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("ALREADY_ROUTED", CID))
        self.assertEqual(len([c for c in self.server.calls if c[0] == "POST"]), 1)
        # Single net position: a new run is refused while the trade is open.
        self.server.add("GET", OPEN_TRADES, trades(open_trade()))
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "position_already_open"))
        self.assertEqual(len([c for c in self.server.calls if c[0] == "POST"]), 1)
        status = router.status()
        self.assertEqual((status["routing"], status["dry_run"], status["open_trade"]["trade_id"]), (True, False, "6397"))

    def test_broker_rejection_is_recorded(self):
        router = self.router()
        self.ready(order=(201, {"orderCreateTransaction": {"id": "1"},
                                "orderCancelTransaction": {"id": "2", "reason": "MARKET_HALTED"}}))
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("REJECTED", "MARKET_HALTED"))
        self.assertEqual(self.rows()[0]["status"], "REJECTED")

    def test_unknown_blocks_routing_until_reconcile_resolves(self):
        router = self.router()
        self.ready(order=TimeoutError())
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW)[0], "UNKNOWN")
        self.assertEqual(self.rows()[0]["status"], "UNKNOWN")
        # Still unknown at the broker: nothing new is sent.
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "unresolved_submission"))
        self.assertEqual(len([c for c in self.server.calls if c[0] == "POST"]), 1)
        # The order did exist: reconcile resolves it through openTrades' clientExtensions id.
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade())]
        self.assertIsNotNone(router.reconcile())
        self.assertEqual((self.rows()[0]["status"], self.rows()[0]["trade_id"]), ("OPEN", "6397"))
        self.assertTrue(self.events("RESOLVED"))

    def test_unknown_resolved_as_closed_and_operator_release(self):
        router = self.router()
        self.ready(order=TimeoutError())
        router.on_cycle(context(), "PLAN_READY", NOW)
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.server.routes[("GET", TRADE_AT_CID)] = [NOT_FOUND]
        self.assertTrue(router.mark_not_sent(CID, "operator", "checked platform"))
        self.assertEqual(self.rows()[0]["status"], "REJECTED")
        self.assertFalse(router.mark_not_sent(CID, "operator", "again"))

    def test_kill_switch_and_emergency_block_new_orders_but_close_is_allowed(self):
        router = self.router()
        self.ready()
        router.on_cycle(context(), "PLAN_READY", NOW)
        registry = self.registry()
        registry.set_enabled("broker_demo", False, actor="operator")
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]  # the trade closed at the broker meanwhile
        router.reconcile()
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "broker_demo_not_allowed"))
        registry.set_enabled("broker_demo", True, actor="operator")
        registry.emergency_stop(actor="operator", reason="drill")
        self.assertEqual(router.on_cycle(context(run_id="run-3"), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "broker_demo_not_allowed"))
        self.assertEqual(len([c for c in self.server.calls if c[0] == "POST"]), 1)

    def test_emergency_close_all_closes_with_switch_off(self):
        router = self.router()
        self.ready()
        router.on_cycle(context(), "PLAN_READY", NOW)
        self.registry().emergency_stop(actor="operator", reason="drill")
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": close_fill()}))
        self.assertEqual(router.emergency_close_all(reason="drill"), {CID: "CLOSED"})
        self.assertEqual(self.rows()[0]["status"], "CLOSED")
        self.assertEqual(self.writes()[-1], ("PUT", f"{A}/trades/6397/close"))
        self.assertEqual(router.emergency_close_all(reason="again"), {})

    def test_emergency_close_all_in_dry_run_sends_nothing(self):
        self.router()  # a live run left an OPEN trade
        self.ready()
        self.router().on_cycle(context(), "PLAN_READY", NOW)
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        dry = self.router(AI_FLOOR_BROKER_DEMO_DRY_RUN="1")
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": close_fill()}))
        calls = len(self.server.calls)
        self.assertEqual(dry.emergency_close_all(reason="drill"), {CID: "DRY_RUN"})
        self.assertEqual(self.server.calls[calls:], [])  # zero PUT/POST (no request at all)
        self.assertEqual([m for m, _p in self.writes()], ["POST"])
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        [(_kind, cid, detail)] = [e for e in self.events("DRY_RUN") if e[2].get("action") == "emergency_close"]
        self.assertEqual((cid, detail["trade_id"], detail["reason"]), (CID, "6397", "EMERGENCY:drill"))

    def test_zero_units_and_sizing(self):
        router = self.router()
        self.server.add("GET", OPEN_TRADES, trades())
        self.server.add("GET", SUMMARY, summary("0.5"))
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("SKIPPED_ZERO_UNITS", CID))
        self.assertEqual(self.writes(), [])
        dec = decision()
        self.assertEqual(broker_units(Decimal("10000"), dec, Decimal("1.09"), Decimal("1.08"), 100000), 10000)
        self.assertEqual(broker_units(Decimal("10000"), decision(rf=0.005), Decimal("1.09"), Decimal("1.08"), 100000), 5000)
        for rf in (0.05, 0, -0.01, None, True, float("nan")):
            with self.subTest(rf=rf):
                self.assertEqual(broker_units(Decimal("10000"), decision(rf=rf), Decimal("1.09"), Decimal("1.08"),
                                              100000), 10000)  # capped / invalid -> 1%
        self.assertEqual(broker_units(Decimal("10000"), dec, Decimal("1.09"), Decimal("1.08"), 2500), 2500)

    def test_max_units_cap_applies_to_the_order(self):
        router = self.router(AI_FLOOR_BROKER_DEMO_MAX_UNITS="250")
        self.ready()
        router.on_cycle(context(), "PLAN_READY", NOW)
        [post] = [b for m, _p, _h, b in self.server.calls if m == "POST"]
        self.assertEqual(post["order"]["units"], "250")
        self.assertEqual(self.rows()[0]["units"], 250)


class ReconcileTests(RouterBase):
    def opened(self):
        router = self.router()
        self.ready()
        router.on_cycle(context(), "PLAN_READY", NOW)
        return router

    def test_missing_at_broker_is_closed_at_broker(self):
        router = self.opened()
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        router.reconcile()
        row = self.rows()[0]
        self.assertEqual((row["status"], row["closed_reason"]), ("CLOSED_AT_BROKER", "SL_TP_OR_EXTERNAL"))

    def test_read_failure_never_marks_missing_and_blocks_routing(self):
        router = self.opened()
        self.server.routes[("GET", OPEN_TRADES)] = [(500, {})]
        self.assertIsNone(router.reconcile())
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "broker_read_failed"))
        self.assertEqual(self.rows()[0]["status"], "OPEN")

    def test_unknown_broker_trade_is_drift_and_blocks_until_cleared(self):
        router = self.router()
        self.server.add("GET", OPEN_TRADES, trades(open_trade(trade_id="77", ext="manual"),
                                                   trade(trade_id="78", instrument="XAU_USD")))
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("NOT_ROUTED", "blocked:drift"))
        self.assertEqual(router.blocked_reason, "drift")
        [(_k, _c, detail)] = self.events("DRIFT")
        self.assertEqual(detail["trades"], [["77", "EUR_USD"], ["78", "XAU_USD"]])
        self.assertEqual(self.router().blocked_reason, "drift")  # persisted across restarts
        router.clear_drift("operator", "closed manually")
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.server.add("GET", SUMMARY, summary())
        self.server.add("GET", TRADE_AT_CID, NOT_FOUND)
        self.server.add("GET", PRICING, pricing())
        self.server.add("POST", ORDERS, FILLED)
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW)[0], "ACCEPTED")
        with self.assertRaises(ValueError):
            router.clear_drift("", "x")

    def test_read_router_status_is_read_only(self):
        self.assertFalse(read_router_status(self.db)["readable"])
        self.assertFalse(self.db.exists())
        self.opened()
        before = self.db.read_bytes()
        status = read_router_status(self.db, now=NOW + timedelta(minutes=5))
        self.assertEqual((status["readable"], status["routing"], status["dry_run"]), (True, True, False))
        self.assertEqual(status["open_trade"]["trade_id"], "6397")
        self.assertFalse(read_router_status(self.db, now=NOW + timedelta(hours=2))["routing"])  # stale heartbeat
        self.assertEqual(self.db.read_bytes(), before)
        self.assertNotIn(TOKEN, json.dumps(status))


def row(side="LONG", entry="1.09000", stop="1.08000", opened=NOW, stage=None, initial_stop=None):
    return {"client_id": CID, "symbol": "EURUSD", "side": side, "entry": entry, "fill_price": entry, "stop": stop,
            "initial_stop": initial_stop, "opened_at": opened.isoformat(), "stage": stage, "trade_id": "6397",
            "created_at": opened.isoformat()}


class DurationRuleTests(unittest.TestCase):
    DAY1 = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)

    def test_rule_table(self):
        before = self.DAY1 - timedelta(minutes=1)
        cases = [
            (row(), before, Decimal("1.0800"), None),
            (row(), self.DAY1, Decimal("1.09400"), ("CLOSE", td.CLOSE_DAY1_BELOW_HALF_R)),  # +0.4R
            (row(), self.DAY1, Decimal("1.09500"), ("BREAKEVEN", None)),  # +0.5R
            (row(side="SHORT", stop="1.10000"), self.DAY1, Decimal("1.08500"), ("BREAKEVEN", None)),
            (row(side="SHORT", stop="1.10000"), self.DAY1, Decimal("1.08600"), ("CLOSE", td.CLOSE_DAY1_BELOW_HALF_R)),
            (row(), self.DAY1, None, None),  # price unavailable: no Day-1 decision (retried next tick)
            # Day 2: R measured against the ORIGINAL stop, closed at the Day-2 NY close whatever the price.
            (row(stage=td.STAGE_DAY2, stop="1.09000", initial_stop="1.08000"), self.DAY1 + timedelta(hours=1), None, None),
            (row(stage=td.STAGE_DAY2, stop="1.09000", initial_stop="1.08000"), self.DAY1 + timedelta(days=1), None,
             ("CLOSE", td.CLOSE_DAY2_NY_CLOSE)),
            # Max 2 days: a Day-1 trade already past its Day-2 close is closed.
            (row(), self.DAY1 + timedelta(days=1), Decimal("1.2"), ("CLOSE", td.CLOSE_DAY2_NY_CLOSE)),
            # Friday: opened Friday -> its Day-1 close is the Friday close; Thursday trade -> Day-2 close on Friday.
            (row(opened=datetime(2026, 10, 16, 10, tzinfo=timezone.utc)), datetime(2026, 10, 16, 21, tzinfo=timezone.utc),
             None, ("CLOSE", td.CLOSE_FRIDAY)),
            (row(opened=datetime(2026, 10, 15, 10, tzinfo=timezone.utc), stage=td.STAGE_DAY2, stop="1.09000",
                 initial_stop="1.08000"), datetime(2026, 10, 16, 21, tzinfo=timezone.utc), None, ("CLOSE", td.CLOSE_FRIDAY)),
        ]
        for trade_row, now, price, expected in cases:
            with self.subTest(now=now, price=price, stage=trade_row["stage"]):
                self.assertEqual(duration_action(trade_row, now, price), expected)

    def test_dst_edge(self):
        winter = row(opened=datetime(2026, 3, 5, 14, tzinfo=timezone.utc))  # EST: 17:00 NY = 22:00 UTC
        self.assertIsNone(duration_action(winter, datetime(2026, 3, 5, 21, 30, tzinfo=timezone.utc), Decimal("1.08")))
        self.assertIsNotNone(duration_action(winter, datetime(2026, 3, 5, 22, tzinfo=timezone.utc), Decimal("1.08")))
        summer = row(opened=datetime(2026, 3, 9, 14, tzinfo=timezone.utc))  # EDT after 2026-03-08: 21:00 UTC
        self.assertEqual(duration_action(summer, datetime(2026, 3, 9, 21, tzinfo=timezone.utc), Decimal("1.08")),
                         ("CLOSE", td.CLOSE_DAY1_BELOW_HALF_R))

    def test_missing_open_time(self):
        broken = {**row(), "opened_at": None, "created_at": None}
        with self.assertRaises(ValueError):
            duration_action(broken, NOW, Decimal("1"))


class ManageTests(RouterBase):
    DAY1 = datetime(2026, 10, 12, 21, 0, tzinfo=timezone.utc)

    def opened(self):
        router = self.router()
        self.ready()
        router.on_cycle(context(), "PLAN_READY", NOW)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade())]
        del self.server.routes[("GET", PRICING)]  # each test sets its own quote
        return router

    def test_day1_below_half_r_closes_and_retries_until_success(self):
        router = self.opened()
        self.server.add("GET", PRICING, pricing(bid="1.09100", ask="1.09110"))
        close = f"{A}/trades/6397/close"
        self.server.add("PUT", close, (400, {"errorCode": "MARKET_HALTED"}),
                        (200, {"orderFillTransaction": close_fill()}))
        self.assertEqual(router.manage(self.DAY1), {CID: "CLOSE_REJECTED"})
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.assertEqual(router.manage(self.DAY1 + timedelta(minutes=1)), {CID: "CLOSED"})
        row_ = self.rows()[0]
        self.assertEqual((row_["status"], row_["closed_reason"]), ("CLOSED", td.CLOSE_DAY1_BELOW_HALF_R))
        self.assertEqual([p for m, p in self.writes() if m == "PUT"], [close, close])

    def test_day1_keep_moves_stop_to_entry_then_day2_close(self):
        router = self.opened()
        self.server.add("GET", PRICING, pricing(bid="1.09600", ask="1.09610"))  # +0.6R
        self.server.add("GET", f"{A}/trades/6397", (200, {"trade": open_trade()}))
        self.server.add("PUT", f"{A}/trades/6397/orders", (200, {"stopLossOrderTransaction": sl_tx("1.09000")}))
        self.assertEqual(router.manage(self.DAY1), {CID: "BREAKEVEN"})
        row_ = self.rows()[0]
        self.assertEqual((row_["stage"], row_["stop"], row_["initial_stop"]), (td.STAGE_DAY2, "1.09000", "1.08000"))
        [(m, p, _h, body)] = [c for c in self.server.calls if c[0] == "PUT"]
        self.assertEqual(body, {"stopLoss": {"price": "1.09000", "timeInForce": "GTC"}})
        self.assertEqual(router.manage(self.DAY1 + timedelta(hours=2)), {CID: "KEEP"})  # idempotent: no second PUT
        self.assertEqual(len([c for c in self.server.calls if c[0] == "PUT"]), 1)
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": close_fill()}))
        self.assertEqual(router.manage(self.DAY1 + timedelta(days=1)), {CID: "CLOSED"})
        self.assertEqual(self.rows()[0]["closed_reason"], td.CLOSE_DAY2_NY_CLOSE)

    def test_price_unavailable_or_market_closed_no_action_then_retry(self):
        router = self.opened()
        self.server.add("GET", PRICING, (500, {}))
        self.assertEqual(router.manage(self.DAY1), {CID: "NO_PRICE"})
        self.server.routes[("GET", PRICING)] = [pricing(tradeable=False)]
        self.assertEqual(router.manage(self.DAY1), {CID: "MARKET_CLOSED"})
        self.assertEqual(self.writes()[-1][0], "POST")  # nothing but the original order
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.09100")]
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": close_fill()}))
        self.assertEqual(router.manage(self.DAY1 + timedelta(minutes=2)), {CID: "CLOSED"})

    def test_overdue_close_waits_for_market_and_close_unknown_latches(self):
        router = self.opened()
        friday = datetime(2026, 10, 16, 21, tzinfo=timezone.utc)
        self.server.add("GET", PRICING, pricing(tradeable=False))
        self.assertEqual(router.manage(friday), {CID: "MARKET_CLOSED"})
        self.server.routes[("GET", PRICING)] = [pricing()]
        self.server.add("PUT", f"{A}/trades/6397/close", TimeoutError())
        self.assertEqual(router.manage(friday + timedelta(days=2)), {CID: "UNKNOWN"})
        self.server.routes[("GET", OPEN_TRADES)] = [(500, {})]  # reconcile cannot read: the latch holds, no PUT
        self.assertEqual(router.manage(friday + timedelta(days=2, minutes=1)), {"_": "READ_FAILED"})
        self.assertEqual(len([c for c in self.server.calls if c[0] == "PUT"]), 1)
        self.assertEqual(self.rows()[0]["action_status"], "UNKNOWN")
        # A later reconcile finds the trade gone: closed with the pending reason.
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        router.manage(friday + timedelta(days=2, minutes=2))
        row_ = self.rows()[0]
        # Opened Monday: by Friday it is an overdue Day-2 close (max 2 days), with the reason kept while latched.
        self.assertEqual((row_["status"], row_["closed_reason"]), ("CLOSED", td.CLOSE_DAY2_NY_CLOSE))

    def puts(self):
        return [p for m, p in self.writes() if m == "PUT"]

    def test_unknown_close_still_open_stays_latched_until_evidence_or_operator(self):
        router = self.opened()
        self.server.add("GET", PRICING, pricing(bid="1.09100"))
        close = f"{A}/trades/6397/close"
        self.server.add("PUT", close, TimeoutError())
        self.assertEqual(router.manage(self.DAY1), {CID: "UNKNOWN"})
        # The trade is still open: that proves nothing about the close -> no retry, routing blocked.
        self.assertEqual(router.manage(self.DAY1 + timedelta(minutes=1)), {CID: "ACTION_UNRESOLVED"})
        self.assertEqual(self.puts(), [close])
        self.assertEqual((self.rows()[0]["action_status"], self.rows()[0]["pending_action"]),
                         ("UNKNOWN", f"CLOSE:{td.CLOSE_DAY1_BELOW_HALF_R}"))
        self.assertEqual(router.blocked_reason, "unresolved_action")
        self.assertEqual(len(self.events("UNRESOLVED_ACTION")), 1)  # event on the transition only
        self.assertEqual(self.router().blocked_reason, "unresolved_action")  # persisted
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "blocked:unresolved_action"))
        self.assertEqual(self.puts(), [close])
        # Operator clear: the latch is released and the close retried on the next pass.
        with self.assertRaises(ValueError):
            router.clear_unresolved("", "x")
        self.assertEqual(router.clear_unresolved("operator", "checked platform"), [CID])
        self.assertIsNone(router.blocked_reason)
        self.server.routes[("PUT", close)] = [(200, {"orderFillTransaction": close_fill()})]
        self.assertEqual(router.manage(self.DAY1 + timedelta(minutes=2)), {CID: "CLOSED"})
        self.assertEqual(self.puts(), [close, close])

    def test_unknown_close_resolved_by_trade_absent(self):
        router = self.opened()
        self.server.add("GET", PRICING, pricing(bid="1.09100"))
        self.server.add("PUT", f"{A}/trades/6397/close", TimeoutError())
        router.manage(self.DAY1)
        router.reconcile()
        self.assertEqual(router.blocked_reason, "unresolved_action")
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        router.reconcile()
        row_ = self.rows()[0]
        self.assertEqual((row_["status"], row_["closed_reason"], row_["action_status"]),
                         ("CLOSED", td.CLOSE_DAY1_BELOW_HALF_R, None))
        self.assertIsNone(router.blocked_reason)
        self.assertTrue(self.events("UNRESOLVED_ACTION_RESOLVED"))
        self.assertEqual(len(self.puts()), 1)

    def test_unknown_breakeven_resolved_only_when_broker_stop_is_at_entry(self):
        router = self.opened()
        self.server.add("GET", PRICING, pricing(bid="1.09600", ask="1.09610"))  # +0.6R -> breakeven
        self.server.add("GET", f"{A}/trades/6397", (200, {"trade": open_trade()}))
        self.server.add("PUT", f"{A}/trades/6397/orders", TimeoutError())
        self.assertEqual(router.manage(self.DAY1), {CID: "UNKNOWN"})
        # Still open with the ORIGINAL stop: not evidence of success or failure -> stays latched, no second PUT.
        self.assertEqual(router.manage(self.DAY1 + timedelta(minutes=1)), {CID: "ACTION_UNRESOLVED"})
        self.assertEqual(len(self.puts()), 1)
        self.assertEqual((self.rows()[0]["action_status"], self.rows()[0]["stage"]), ("UNKNOWN", None))
        self.assertEqual(router.blocked_reason, "unresolved_action")
        # The broker shows the stop at entry (5dp): breakeven done, stage DAY2, block released.
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade(sl="1.09000"))]
        self.assertEqual(router.manage(self.DAY1 + timedelta(minutes=2)), {CID: "KEEP"})
        row_ = self.rows()[0]
        self.assertEqual((row_["stage"], row_["stop"], row_["initial_stop"], row_["action_status"],
                          row_["pending_action"]), (td.STAGE_DAY2, "1.09000", "1.08000", None, None))
        self.assertIsNone(router.blocked_reason)
        self.assertEqual(len(self.puts()), 1)

    def test_actions_need_a_valid_tradeable_quote(self):
        router = self.opened()
        friday = datetime(2026, 10, 16, 21, tzinfo=timezone.utc)  # overdue close (Day-2 / Friday)

        def boom(_symbol):
            raise OSError("down")

        providers = {"raises": (boom, "NO_PRICE", "pricing_unavailable"),
                     "none": (lambda _s: None, "NO_PRICE", "pricing_unavailable"),
                     "tradeable_false": (lambda _s: (False, Decimal("1.09"), Decimal("1.0901")), "MARKET_CLOSED",
                                         "not_tradeable"),
                     "tradeable_missing": (lambda _s: (None, Decimal("1.09"), Decimal("1.0901")), "MARKET_CLOSED",
                                           "not_tradeable"),
                     "tradeable_truthy": (lambda _s: (1, Decimal("1.09"), Decimal("1.0901")), "MARKET_CLOSED",
                                          "not_tradeable"),
                     "bad_shape": (lambda _s: (True, Decimal("1.09")), "NO_PRICE", "pricing_malformed"),
                     "bad_bid": (lambda _s: (True, None, Decimal("1.0901")), "NO_PRICE", "pricing_malformed")}
        for minute, (name, (provider, outcome, code)) in enumerate(providers.items()):
            with self.subTest(name=name):
                before = len(self.events("MANAGE_SKIPPED"))
                self.assertEqual(router.manage(friday + timedelta(minutes=minute), price_provider=provider),
                                 {CID: outcome})
                skipped = self.events("MANAGE_SKIPPED")[before:]
                self.assertEqual([d["reason"] for _k, _c, d in skipped], [code])  # once per tick
        # Broker pricing: failure, malformed body, missing tradeable -> nothing sent either.
        missing = (200, {"prices": [{"instrument": "EUR_USD", "bids": [{"price": "1.09"}], "asks": [{"price": "1.1"}]}]})
        for response, code in (((500, {}), "pricing_unavailable"), ((200, {"x": 1}), "pricing_malformed"),
                               (missing, "pricing_malformed"), (pricing(tradeable=False), "not_tradeable")):
            with self.subTest(code=code):
                self.server.routes[("GET", PRICING)] = [response]
                router.manage(friday + timedelta(hours=1))
                self.assertEqual(self.events("MANAGE_SKIPPED")[-1][2]["reason"], code)
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        dump = json.dumps(self.events("MANAGE_SKIPPED"))
        self.assertNotIn(ACCOUNT, dump)
        self.assertNotIn(TOKEN, dump)
        # A valid tradeable quote: the overdue close is finally sent.
        self.server.routes[("GET", PRICING)] = [pricing()]
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": close_fill()}))
        self.assertEqual(router.manage(friday + timedelta(hours=2)), {CID: "CLOSED"})

    def test_not_due_needs_no_quote(self):
        router = self.opened()
        before = len(self.server.calls)
        self.assertEqual(router.manage(self.DAY1 - timedelta(minutes=1)), {CID: "KEEP"})
        self.assertEqual([p for _m, p, _h, _b in self.server.calls[before:]], [OPEN_TRADES])
        self.assertEqual(self.events("MANAGE_SKIPPED"), [])


class RuntimeHookTests(unittest.TestCase):
    T = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def runtime(self, name, **kwargs):
        from runtime.config import RuntimeConfig
        from runtime.service import OperationalRuntime
        config = RuntimeConfig(db_path=Path(self.tmp.name) / f"{name}.db", enabled_symbols=("XAUUSD", "EURUSD"))
        runtime = OperationalRuntime(config, clock=lambda: self.T, **kwargs)
        self.addCleanup(runtime.close)
        return runtime

    def test_flag_off_router_is_none_and_cycle_identical(self):
        with patch.dict(os.environ, {"AI_FLOOR_BROKER_DEMO_ROUTING": "0"}), \
                patch("execution.broker_demo_router.router_from_env") as factory:
            auto = self.runtime("auto")
        factory.assert_not_called()  # flag off: the router module is not even used
        self.assertIsNone(auto.broker_router)
        plain = self.runtime("plain", broker_router=None)
        self.assertEqual(auto.run_cycle("EURUSD", self.T), plain.run_cycle("EURUSD", self.T))
        self.assertEqual(auto.manage_trade_duration(self.T), plain.manage_trade_duration(self.T))

    def test_flag_on_uses_router_from_env(self):
        sentinel = object()
        with patch.dict(os.environ, {"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}), \
                patch("execution.broker_demo_router.router_from_env", return_value=sentinel) as factory:
            runtime = self.runtime("on")
        factory.assert_called_once()
        self.assertIs(runtime.broker_router, sentinel)
        with patch.dict(os.environ, {"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}), \
                patch("execution.broker_demo_router.router_from_env", side_effect=RuntimeError("boom")):
            self.assertIsNone(self.runtime("broken").broker_router)

    def test_router_exceptions_never_reach_the_cycle(self):
        class Raising:
            def __init__(self):
                self.calls = []

            def on_cycle(self, context, status, at):
                self.calls.append((context.get("symbol"), status))
                raise RuntimeError("router down")

            def manage(self, at, **_):
                self.calls.append(("manage", at))
                raise RuntimeError("router down")

        router = Raising()
        plain = self.runtime("plain", broker_router=None)
        hooked = self.runtime("hooked", broker_router=router)
        self.assertEqual(hooked.run_cycle("EURUSD", self.T), plain.run_cycle("EURUSD", self.T))
        self.assertEqual(hooked.run_cycle("EURUSD", self.T), "DUPLICATE")
        self.assertEqual(hooked.manage_trade_duration(self.T), plain.manage_trade_duration(self.T))
        self.assertEqual([c[0] for c in router.calls], ["EURUSD", "manage"])  # not called for DUPLICATE

    def test_manage_runs_even_with_paper_disabled(self):
        router = SimpleNamespace(manage=lambda at, **_: calls.append(at))
        calls = []
        runtime = self.runtime("nopaper", broker_router=router, paper_enabled=False)
        self.assertEqual(runtime.manage_trade_duration(self.T), {})
        self.assertEqual(calls, [self.T])

    def test_class_default_is_inert(self):
        from runtime.service import OperationalRuntime
        self.assertIsNone(OperationalRuntime.broker_router)
        self.assertIsInstance(BrokerDemoRouter, type)


if __name__ == "__main__":
    unittest.main()
