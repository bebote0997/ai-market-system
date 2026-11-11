"""AI_FLOOR_EMA_ONLY: engine/plan orders off, one resting pullback LIMIT cancelled, EMA left alone.

Exact ``1`` on a practice router. Unset and any other value keep the current route. The flag is read on
each cycle. A non-practice broker cannot enable it. Limit-management v2 stays off in these cases.
"""
from datetime import timedelta
from decimal import Decimal as D
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from execution.broker_demo_router import EMA_ONLY_ENV, BrokerDemoRouter, ema_only_flag
from execution.broker_demo_state import OPEN, PENDING_LIMIT, DemoStateStore
from execution.ema_cross import STRATEGY as EMA_STRATEGY
from execution.eurusd_pullback import STRATEGY
from execution.oanda_broker import PendingOrder
from execution import trade_duration as td
from test_broker_demo_router import NOW, PRICING, RouterBase, context
from test_broker_oanda import ORDERS, pricing
from test_ema_cross import CONFIRM, READY, h1_up, m15_long, macro
from test_eurusd_pullback import ramp


LOG = "ai_floor.broker.demo_router"


class FlagTests(unittest.TestCase):
    def test_only_the_exact_value_opts_in(self):
        self.assertFalse(ema_only_flag({}))
        self.assertFalse(ema_only_flag({EMA_ONLY_ENV: None}))
        for value in ("0", "true", "1 ", " 1", "yes", "on", ""):
            self.assertFalse(ema_only_flag({EMA_ONLY_ENV: value}), value)
        self.assertTrue(ema_only_flag({EMA_ONLY_ENV: "1"}))


class RouteTests(RouterBase):
    def lines(self, logs, event):
        return [record.getMessage() for record in logs.records if f"event={event}" in record.getMessage()]

    def test_startup_log_shows_the_flag(self):
        with self.assertLogs(LOG, "INFO") as on_logs:
            on = self.router(AI_FLOOR_EMA_ONLY="1")
        self.assertTrue(on._ema_only())
        self.assertEqual(self.lines(on_logs, "LIMIT_FLAGS"), [
            "component=broker_demo event=LIMIT_FLAGS limit_management_v2=0 entry_offset_mode=legacy ema_only=1"])
        self.assertEqual(self.lines(on_logs, "EMA_ONLY_ON"), ["component=broker_demo event=EMA_ONLY_ON"])
        with self.assertLogs(LOG, "INFO") as off_logs:
            off = self.router()
        self.assertFalse(off._ema_only())
        self.assertEqual(self.lines(off_logs, "LIMIT_FLAGS"), [
            "component=broker_demo event=LIMIT_FLAGS limit_management_v2=0 entry_offset_mode=legacy ema_only=0"])
        self.assertEqual(self.lines(off_logs, "EMA_ONLY_OFF"), ["component=broker_demo event=EMA_ONLY_OFF"])

    def test_flag_on_refuses_the_market_route_and_off_sends_on_the_next_cycle(self):
        router = self.router(AI_FLOOR_EMA_ONLY="1")
        self.ready()
        refused = router.on_cycle(context(), "PLAN_READY", NOW)
        self.assertEqual(refused, ("NOT_ROUTED", "ema_only"))
        self.assertEqual(self.events("NOT_ROUTED")[-1][2]["reason"], "ema_only")
        self.assertEqual(self.writes(), [])
        self.assertEqual(router._store.trades(), [])
        router._env[EMA_ONLY_ENV] = "0"  # read again; the previous cycle did not cache the flag
        self.assertFalse(router._ema_only())
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("ACCEPTED", "6397"))
        self.assertEqual(len(self.writes()), 1)

    def test_flag_on_refuses_the_pullback_limit(self):
        router = self.router(AI_FLOOR_EURUSD_LIMIT_PULLBACK="1", AI_FLOOR_EMA_ONLY="1")
        self.ready()
        outcome = router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome, ("NOT_ROUTED", "ema_only"))
        self.assertEqual(self.writes(), [])
        self.assertEqual(router._store.trades(), [])

    def test_garbage_values_still_send_the_market_order(self):
        for value in ("0", "true", "1 ", "yes", "on", ""):
            router = self.router(AI_FLOOR_EMA_ONLY=value)
            self.assertFalse(router._ema_only(), value)
        self.ready()
        sent = self.router(AI_FLOOR_EMA_ONLY="true")
        self.assertEqual(sent.on_cycle(context(), "PLAN_READY", NOW), ("ACCEPTED", "6397"))

    def test_pullback_limit_is_unchanged_when_the_flag_is_off(self):
        router = self.router(AI_FLOOR_EURUSD_LIMIT_PULLBACK="1", AI_FLOOR_EMA_ONLY="0")
        self.ready(order=(201, {"orderCreateTransaction": {"id": "200", "type": "LIMIT_ORDER", "price": "1.08875"}}))
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08880", ask="1.08890")]
        outcome = router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome, (PENDING_LIMIT, "200"))
        posted = [body["order"] for method, path, _headers, body in self.server.calls
                  if (method, path) == ("POST", ORDERS)]
        self.assertEqual(posted[0]["type"], "LIMIT")
        self.assertEqual(router._store.trade("aif-run-1")["status"], PENDING_LIMIT)

    def test_a_non_practice_broker_does_not_enable_the_flag(self):
        with patch("runtime.demo_runner.REAL_EXECUTION_ENABLED", True):
            self.assertIsNone(self.router(AI_FLOOR_EMA_ONLY="1"))
        router = self.router(AI_FLOOR_EMA_ONLY="1")
        router._broker.mode = "live"
        self.assertFalse(router._ema_only())
        self.ready()
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("ACCEPTED", "6397"))
        host = self.router(AI_FLOOR_EMA_ONLY="1")
        host._broker._client._base = "https://api-fxtrade.oanda.com/v3"
        self.assertFalse(host._ema_only())
        with patch("execution.broker_demo_router.real_execution_enabled", return_value=True):
            self.assertFalse(host._ema_only())


class BookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "broker_state.db"
        self.store = DemoStateStore(self.db)
        self.store.initialize()
        self.broker = _Book()
        self.registry = SimpleNamespace(emergency_active=lambda: False, is_allowed=lambda _mode: True)

    def router(self, ema_only="1", **extra):
        env = {"AI_FLOOR_LIMIT_MANAGEMENT_V2": "0", "AI_FLOOR_EMA_CROSS": "1", **extra}
        if ema_only is not None:
            env[EMA_ONLY_ENV] = ema_only
        return BrokerDemoRouter(
            self.broker, self.store, registry=self.registry, dry_run=False, max_units=100000,
            pullback=True, ema_cross=True, env=env)

    def seed_limit(self):
        self.store.insert_intent({
            "client_id": "aif-pullback", "run_id": "run-pull", "symbol": "EURUSD", "side": "LONG", "units": 1000,
            "entry": "1.08875", "stop": "1.08625", "target": "1.09375", "status": PENDING_LIMIT,
            "order_kind": "LIMIT", "order_id": "200", "limit_price": "1.08875", "strategy": STRATEGY,
            "scale_stage": "NONE", "expires_at": (CONFIRM + timedelta(hours=20)).isoformat()})
        self.broker.pending.append(PendingOrder("200", "EUR_USD", "aif-pullback", D("1.08875")))

    def events(self, kind=None):
        with sqlite3.connect(self.db) as con:
            rows = con.execute("SELECT kind, detail FROM broker_demo_events ORDER BY id").fetchall()
        parsed = [(k, json.loads(d) if d else None) for k, d in rows]
        return [item for item in parsed if kind in (None, item[0])]

    def test_a_resting_limit_is_cancelled_once_and_the_ema_entry_still_goes_out(self):
        self.seed_limit()
        router = self.router()
        self.assertFalse(router._limit_management_v2())
        later = m15_long() + [(READY, m15_long()[-1][1])]
        with self.assertLogs(LOG, "INFO") as logs:
            router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        self.assertEqual(self.broker.cancelled, ["200"])
        self.assertEqual(self.broker.position_reads, 1)
        self.assertEqual(self.broker.plans, [])
        row = self.store.trade("aif-pullback")
        self.assertEqual((row["status"], row["closed_reason"]), ("CANCELLED", "EMA_ONLY"))
        self.assertTrue(any("event=LIMIT_CANCELLED" in record.getMessage() and "reason=EMA_ONLY" in record.getMessage()
                            for record in logs.records))
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.cancelled, ["200"])
        self.assertEqual(self.broker.position_reads, 1)
        self.assertEqual(len(self.broker.plans), 1)
        self.assertEqual(self.broker.plans[0].side, "LONG")
        ema = [item for item in self.store.trades() if item["strategy"] == EMA_STRATEGY]
        self.assertEqual(len(ema), 1)
        self.assertEqual((ema[0]["status"], ema[0]["order_kind"]), (OPEN, "MARKET"))
        self.assertEqual(self.broker.closes, [])

    def test_flag_off_and_garbage_leave_the_resting_limit_and_block_ema(self):
        for value in (None, "0", "true", "1 "):
            with self.subTest(value=value):
                self.setUp()
                self.seed_limit()
                router = self.router(ema_only=value)
                later = m15_long() + [(READY, m15_long()[-1][1])]
                router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
                router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
                self.assertEqual(self.broker.cancelled, [])
                self.assertEqual(self.broker.plans, [])
                self.assertEqual(self.broker.position_reads, 0)
                self.assertEqual(self.store.trade("aif-pullback")["status"], PENDING_LIMIT)

    def test_a_filled_pullback_and_an_ema_trade_are_not_cancelled(self):
        self.store.insert_intent({
            "client_id": "aif-pullback", "run_id": "run-pull", "symbol": "EURUSD", "side": "LONG", "units": 1000,
            "entry": "1.09000", "stop": "1.08750", "target": "1.09500", "status": OPEN, "trade_id": "9001",
            "order_kind": "LIMIT", "strategy": STRATEGY, "scale_stage": "NONE", "fill_price": "1.09000",
            "opened_at": CONFIRM.isoformat(), "units_remaining": 1000, "risk_distance": "0.00250"})
        self.store.insert_intent({
            "client_id": "ema-open", "run_id": "ema-run", "symbol": "EURUSD", "side": "LONG", "units": 1000,
            "entry": "1.09000", "stop": "1.08800", "target": "1.09400", "status": OPEN, "trade_id": "9002",
            "order_kind": "MARKET", "strategy": EMA_STRATEGY, "scale_stage": "ANCHORED", "fill_price": "1.09000",
            "opened_at": CONFIRM.isoformat()})
        self.broker.trades.extend([
            SimpleNamespace(trade_id="9001", instrument="EUR_USD", client_ext_id="aif-pullback", price=D("1.09000"),
                            open_time=CONFIRM, stop=D("1.08750"), target=D("1.09500"), units=D("1000")),
            SimpleNamespace(trade_id="9002", instrument="EUR_USD", client_ext_id="ema-open", price=D("1.09000"),
                            open_time=CONFIRM, stop=D("1.08800"), target=D("1.09400"), units=D("1000")),
        ])
        self.broker.quote = (True, D("1.09000"), D("1.09010"))
        router = self.router()
        router.on_cycle(context(), "PLAN_READY", CONFIRM)
        self.assertEqual(self.broker.cancelled, [])
        self.assertEqual(self.broker.closes, [])
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(self.store.trade("aif-pullback")["status"], OPEN)
        self.assertEqual(self.store.trade("ema-open")["status"], OPEN)
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", CONFIRM), ("NOT_ROUTED", "ema_only"))

    def test_a_cancel_that_leaves_a_fill_does_not_close_it_or_send_ema(self):
        self.seed_limit()
        self.broker.fill_on_cancel = True
        self.broker.quote = (True, D("1.08875"), D("1.08880"))
        router = self.router()
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.cancelled, ["200"])
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(self.broker.closes, [])
        self.assertEqual(self.store.trade("aif-pullback")["status"], OPEN)

    def test_a_rejected_cancel_is_logged_and_the_limit_stays(self):
        self.seed_limit()
        self.broker.cancel_result = SimpleNamespace(status="REJECTED", reason="broker_rejected")
        router = self.router()
        with self.assertLogs(LOG, "WARNING") as logs:
            router.on_cycle(macro(), "NO_SETUP", CONFIRM)
        self.assertEqual(self.broker.cancelled, ["200"])
        self.assertEqual(self.broker.position_reads, 1)
        self.assertEqual(self.store.trade("aif-pullback")["status"], PENDING_LIMIT)
        self.assertTrue(any(record.getMessage() == "component=broker_demo event=LIMIT_CANCEL_FAILED reason=EMA_ONLY order_id=***200"
                            for record in logs.records))

    def test_the_week_close_still_closes_an_ema_trade(self):
        self.store.insert_intent({
            "client_id": "ema-open", "run_id": "ema-run", "symbol": "EURUSD", "side": "LONG", "units": 1000,
            "entry": "1.09000", "stop": "1.08800", "target": "1.09400", "status": OPEN, "trade_id": "9002",
            "order_kind": "MARKET", "strategy": EMA_STRATEGY, "scale_stage": "ANCHORED", "fill_price": "1.09000",
            "opened_at": CONFIRM.isoformat()})
        self.broker.trades.append(SimpleNamespace(
            trade_id="9002", instrument="EUR_USD", client_ext_id="ema-open", price=D("1.09000"),
            open_time=CONFIRM, stop=D("1.08800"), target=D("1.09400"), units=D("1000")))
        router = self.router()
        boundary, reason = td.hold_boundary(CONFIRM)
        self.assertEqual(router.manage(boundary)["ema-open"], "CLOSED")
        self.assertEqual(self.broker.closes, [("9002", reason)])
        self.assertEqual(self.broker.cancelled, [])
        self.assertEqual(self.store.trade("ema-open")["closed_reason"], reason)

    def test_a_live_mode_broker_does_not_cancel_the_limit(self):
        self.seed_limit()
        self.broker.mode = "live"
        router = self.router()
        self.assertFalse(router._ema_only())
        router.on_cycle(macro(), "NO_SETUP", CONFIRM)
        self.assertEqual(self.broker.cancelled, [])
        self.assertEqual(self.store.trade("aif-pullback")["status"], PENDING_LIMIT)


class _Book:
    mode = "broker_demo"

    def __init__(self):
        self.trades = []
        self.pending = []
        self.cancelled = []
        self.plans = []
        self.closes = []
        self.position_reads = 0
        self.quote = (True, D("1.08990"), D("1.09000"))

    def open_trades(self):
        return list(self.trades)

    def pending_orders(self):
        return list(self.pending)

    def get_positions(self):
        self.position_reads += 1
        return []

    def cancel_order(self, order_id, client_id=None):
        self.cancelled.append(str(order_id))
        outcome = getattr(self, "cancel_result", None)
        if outcome is not None:
            return outcome
        self.pending = [order for order in self.pending if order.order_id != str(order_id)]
        if getattr(self, "fill_on_cancel", False):
            self.trades.append(SimpleNamespace(
                trade_id="9001", instrument="EUR_USD", client_ext_id="aif-pullback", price=D("1.08875"),
                open_time=CONFIRM, stop=D("1.08625"), target=D("1.09375"), units=D("1000")))
        return SimpleNamespace(status="ACCEPTED", reason=None)

    def close_market(self, trade_id, reason=None, client_id=None):
        self.closes.append((str(trade_id), reason))
        self.trades = [trade for trade in self.trades if str(trade.trade_id) != str(trade_id)]
        return SimpleNamespace(status="ACCEPTED", reason=None)

    def get_balance(self):
        return SimpleNamespace(equity=D("100000"))

    def get_quote(self, symbol):
        return self.quote

    def submit_plan(self, plan, approval):
        self.plans.append(plan)
        self.trades.append(SimpleNamespace(
            trade_id="6397", instrument="EUR_USD", client_ext_id=plan.client_id, price=plan.planned_entry,
            open_time=READY, stop=plan.stop, target=plan.target, units=plan.quantity))
        return SimpleNamespace(status="ACCEPTED", broker_ref="6397", reason=None)

    def trade_id_for_client(self, client_id):
        return None
