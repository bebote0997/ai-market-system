"""Practice-only limit management (A) and entry offset (B). Flags off reproduce the 24h / 12.5-pip limit.

``AI_FLOOR_LIMIT_MANAGEMENT_V2`` exact ``1``: 4h GTD, cancel when price runs 15 pips in favor unfilled,
same-side PLAN_READY replaces, an EMA entry cancels the resting pullback LIMIT. A filled position still blocks.
``AI_FLOOR_ENTRY_OFFSET_MODE`` ``5pips`` or ``market``; anything else, including unset, is the 12.5-pip limit.
The flags are read on each cycle. A non-practice broker cannot take either path.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from execution.broker_demo_router import BrokerDemoRouter
from execution.broker_demo_state import OPEN, PENDING_LIMIT, DemoStateStore
from execution.ema_cross import STRATEGY as EMA_STRATEGY
from execution.eurusd_pullback import (ENTRY_OFFSET_ENV, FAVORABLE_CANCEL_PIPS, LIMIT_MANAGEMENT_ENV, LIMIT_OFFSET_PIPS,
                                      LIMIT_TTL_V2, OFFSET_5, OFFSET_5_PIPS, STRATEGY, entry_offset_mode,
                                      favorable_runaway, gtd_timestamp, limit_expiry, limit_management_v2,
                                      plan_pullback)
from execution.oanda_broker import PendingOrder
from test_broker_demo_router import A, CID, NOW, OPEN_TRADES, ORDERS, PRICING, SUMMARY, RouterBase, context, open_trade
from test_broker_demo_router import trades
from test_broker_oanda import NOT_FOUND, pricing
from test_ema_cross import CONFIRM, READY, h1_up, m15_long, macro
from test_eurusd_pullback import PIP, fall, flat, ramp


V2 = LIMIT_MANAGEMENT_ENV
OFFSET = ENTRY_OFFSET_ENV
LOG = "ai_floor.broker.demo_router"


def flags(**values):
    return {V2: values.get("v2", "0"), OFFSET: values.get("offset", "legacy")}


class FlagAndPureTests(unittest.TestCase):
    def test_defaults_are_legacy_and_only_exact_values_opt_in(self):
        self.assertFalse(limit_management_v2({}))
        self.assertFalse(limit_management_v2({V2: "0"}))
        self.assertFalse(limit_management_v2({V2: "true"}))
        self.assertFalse(limit_management_v2({V2: "1 "}))
        self.assertTrue(limit_management_v2({V2: "1"}))
        self.assertEqual(entry_offset_mode({}), "legacy")
        self.assertEqual(entry_offset_mode({OFFSET: "legacy"}), "legacy")
        self.assertEqual(entry_offset_mode({OFFSET: "5PIPS"}), "legacy")
        self.assertEqual(entry_offset_mode({OFFSET: " 5pips"}), "legacy")
        self.assertEqual(entry_offset_mode({OFFSET: "market "}), "legacy")
        self.assertEqual(entry_offset_mode({OFFSET: "5pips"}), "5pips")
        self.assertEqual(entry_offset_mode({OFFSET: "market"}), "market")

    def test_default_plan_stays_12_5_pips_and_24h(self):
        self.assertEqual(LIMIT_OFFSET_PIPS, D("12.5"))
        plan = plan_pullback("LONG", D("1.09000"), D("1.08000"), D("1.12000"), ramp())
        self.assertEqual((plan.limit, plan.offset_pips, plan.stop, plan.target),
                         (D("1.08875"), D("12.5"), D("1.08625"), D("1.09375")))
        monday = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(limit_expiry(monday), monday + timedelta(hours=24))

    def test_five_pip_offset_keeps_25_and_50_from_the_new_limit(self):
        self.assertEqual((OFFSET_5_PIPS, OFFSET_5), (D("5"), D("0.00050")))
        plan = plan_pullback("LONG", D("1.09000"), D("1.08750"), D("1.09500"), ramp(), offset=OFFSET_5)
        self.assertEqual((plan.limit, plan.offset_pips, plan.stop, plan.target),
                         (D("1.08950"), D("5"), D("1.08700"), D("1.09450")))
        short = plan_pullback("SHORT", D("1.09000"), D("1.09250"), D("1.08500"), fall(), offset=OFFSET_5)
        self.assertEqual((short.limit, short.stop, short.target), (D("1.09050"), D("1.09300"), D("1.08550")))
        self.assertEqual(plan_pullback("SHORT", D("1.09000"), D("1.09250"), D("1.08500"), ramp(), offset=OFFSET_5),
                         "trend_filter")

    def test_four_hour_ttl_still_stops_at_the_friday_ban(self):
        monday = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(limit_expiry(monday, LIMIT_TTL_V2), monday + timedelta(hours=4))
        friday = datetime(2026, 10, 16, 19, 0, tzinfo=timezone.utc)  # 15:00 New York, 1h45 before the ban
        self.assertLess(limit_expiry(friday, LIMIT_TTL_V2), friday + timedelta(hours=4))
        self.assertEqual(limit_expiry(friday, LIMIT_TTL_V2), limit_expiry(friday))

    def test_favorable_15_pips_is_exact_and_ignores_a_move_toward_the_limit(self):
        limit = D("1.08875")
        gap = FAVORABLE_CANCEL_PIPS * PIP
        self.assertTrue(favorable_runaway("LONG", limit, limit + gap, limit + gap + PIP, pip=PIP))
        self.assertFalse(favorable_runaway("LONG", limit, limit + gap - PIP, limit + gap, pip=PIP))
        self.assertFalse(favorable_runaway("LONG", limit, limit - D("0.00100"), limit - D("0.00090"), pip=PIP))
        sell = D("1.09125")
        self.assertTrue(favorable_runaway("SHORT", sell, sell - gap - PIP, sell - gap, pip=PIP))
        self.assertFalse(favorable_runaway("SHORT", sell, sell - gap, sell - gap + PIP, pip=PIP))
        self.assertFalse(favorable_runaway("SHORT", sell, sell + D("0.00090"), sell + D("0.00100"), pip=PIP))


class RouterFlagTests(RouterBase):
    def make(self, **flag):
        merged = {**self.env, "AI_FLOOR_EURUSD_LIMIT_PULLBACK": "1", **flags(**flag)}
        return self.router(env=merged)

    def resting(self, price="1.08875"):
        return (201, {"orderCreateTransaction": {"id": "200", "type": "LIMIT_ORDER", "price": price}})

    def posted(self):
        return [body["order"] for method, path, _headers, body in self.server.calls
                if (method, path) == ("POST", ORDERS)]

    def cancels(self):
        return [path for method, path, _headers, _body in self.server.calls if method == "PUT" and path.endswith("/cancel")]

    def test_flags_off_keep_12_5_pips_24h_and_block_the_next_plan(self):
        router = self.make()
        self.ready(order=self.resting())
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08880", ask="1.08890")]
        with self.assertLogs(LOG, "INFO") as logs:
            outcome = router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome, (PENDING_LIMIT, "200"))
        order = self.posted()[0]
        self.assertEqual(order["type"], "LIMIT")
        self.assertEqual((order["price"], order["stopLossOnFill"]["price"], order["takeProfitOnFill"]["price"]),
                         ("1.08875", "1.08625", "1.09375"))
        self.assertEqual(order["gtdTime"], gtd_timestamp(limit_expiry(NOW)))
        self.assertEqual(router._store.trade(CID)["expires_at"], limit_expiry(NOW).isoformat())
        pending = self.events("LIMIT_PENDING")[0][2]
        self.assertNotIn("ttl_hours", pending)
        self.assertNotIn("limit_management_v2", pending)
        self.assertNotIn("entry_offset_mode", pending)
        self.assertFalse(any("event=CYCLE_FLAGS" in record.getMessage() for record in logs.records))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.server.add("GET", f"{A}/pendingOrders", (200, {"orders": [
            {"id": "200", "instrument": "EUR_USD", "price": "1.08875", "clientExtensions": {"id": CID}}]}))
        blocked = router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(blocked, ("NOT_ROUTED", "position_already_open"))
        self.assertEqual(self.cancels(), [])
        self.assertEqual(router._store.trade(CID)["status"], PENDING_LIMIT)

    def test_flags_off_do_not_cancel_a_15_pip_favorable_run(self):
        router = self.make()
        self._seed(router, limit="1.08875")
        self._book(limit="1.08875", bid="1.09075", ask="1.09085")  # 20 pips above the buy limit
        self.assertIsNone(router.manage(NOW + timedelta(hours=1)).get(CID))
        self.assertEqual(router._store.trade(CID)["status"], PENDING_LIMIT)
        self.assertEqual(self.cancels(), [])

    def test_v2_expires_at_4h_and_logs_the_mode(self):
        router = self.make(v2="1")
        self.ready(order=self.resting())
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08880", ask="1.08890")]
        outcome = router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome, (PENDING_LIMIT, "200"))
        self.assertEqual(self.posted()[0]["gtdTime"], gtd_timestamp(NOW + timedelta(hours=4)))
        self.assertEqual(self.events("LIMIT_PENDING")[0][2]["ttl_hours"], "4")
        self.assertEqual(self.events("LIMIT_PENDING")[0][2]["limit_management_v2"], "1")
        self.server.add("GET", f"{A}/pendingOrders", (200, {"orders": [
            {"id": "200", "instrument": "EUR_USD", "price": "1.08875", "clientExtensions": {"id": CID}}]}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08880", ask="1.08890")]
        self.now = NOW + timedelta(hours=4) - timedelta(seconds=1)
        self.assertIsNone(router.manage(self.now).get(CID))
        self.assertEqual(router._store.trade(CID)["status"], PENDING_LIMIT)
        self.server.add("PUT", f"{A}/orders/200/cancel",
                        (200, {"orderCancelTransaction": {"id": "201", "reason": "CLIENT_REQUEST"}}))
        self.now = NOW + timedelta(hours=4)
        self.assertEqual(router.manage(self.now)[CID], "LIMIT_EXPIRED")
        row = router._store.trade(CID)
        self.assertEqual((row["status"], row["closed_reason"]), ("CANCELLED", "LIMIT_EXPIRED"))
        expired = self.events("LIMIT_EXPIRED")[0][2]
        self.assertEqual((expired["ttl_hours"], expired["limit_management_v2"], expired["reason"]),
                         ("4", "1", "LIMIT_EXPIRED"))

    def test_v2_cancels_at_exactly_15_favorable_pips_and_not_before_or_against(self):
        router = self.make(v2="1")
        self._seed(router, limit="1.08875")
        self._book(limit="1.08875", bid="1.09024", ask="1.09034")  # 14.9 pips
        self.assertIsNone(router.manage(NOW + timedelta(minutes=30)).get(CID))
        self.assertEqual(self.cancels(), [])
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08700", ask="1.08710")]  # toward the buy limit
        self.assertIsNone(router.manage(NOW + timedelta(minutes=31)).get(CID))
        self.assertEqual(router._store.trade(CID)["status"], PENDING_LIMIT)
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.09025", ask="1.09035")]  # exactly 15 pips
        self.server.add("PUT", f"{A}/orders/200/cancel",
                        (200, {"orderCancelTransaction": {"id": "201", "reason": "CLIENT_REQUEST"}}))
        self.assertEqual(router.manage(NOW + timedelta(minutes=32))[CID], "LIMIT_CANCELLED")
        detail = self.events("LIMIT_CANCELLED")[0][2]
        self.assertEqual((detail["reason"], detail["distance_pips"], detail["limit_management_v2"]),
                         ("FAVORABLE_15_PIPS", "15", "1"))
        self.assertEqual(router._store.trade(CID)["closed_reason"], "FAVORABLE_15_PIPS")

    def test_v2_cancels_a_short_that_ran_15_pips_down(self):
        router = self.make(v2="1")
        self._seed(router, side="SHORT", limit="1.09125", stop="1.09375", target="1.08625")
        self._book(limit="1.09125", bid="1.08960", ask="1.08975")  # ask is exactly 15 pips under the sell limit
        self.server.add("PUT", f"{A}/orders/200/cancel",
                        (200, {"orderCancelTransaction": {"id": "201", "reason": "CLIENT_REQUEST"}}))
        self.assertEqual(router.manage(NOW + timedelta(minutes=5))[CID], "LIMIT_CANCELLED")
        self.assertEqual(self.events("LIMIT_CANCELLED")[0][2]["reason"], "FAVORABLE_15_PIPS")

    def test_v2_replaces_a_same_side_plan_and_leaves_the_opposite_side(self):
        router = self.make(v2="1")
        self._seed(router, limit="1.08875")
        self._book(limit="1.08875", bid="1.08880", ask="1.08890")
        self.server.add("GET", SUMMARY, (200, {"account": {"balance": "10000", "NAV": "10000", "marginUsed": "0",
                                                           "currency": "USD"}}))
        self.server.add("GET", f"{A}/trades/@aif-run-2", NOT_FOUND)
        self.server.add("PUT", f"{A}/orders/200/cancel",
                        (200, {"orderCancelTransaction": {"id": "201", "reason": "CLIENT_REQUEST"}}))
        self.server.add("POST", ORDERS, self.resting("1.09075"))
        ctx = context(run_id="run-2", entry=1.092, stop=1.089, target=1.097)
        outcome = router.on_cycle(ctx, "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome[0], PENDING_LIMIT)
        self.assertEqual(router._store.trade(CID)["closed_reason"], "SAME_SIDE_REPLACE")
        self.assertEqual(self.events("LIMIT_CANCELLED")[0][2]["reason"], "SAME_SIDE_REPLACE")
        replacement = router._store.trade("aif-run-2")
        self.assertEqual((replacement["status"], replacement["side"], replacement["limit_price"]),
                         (PENDING_LIMIT, "LONG", "1.09075"))
        self.assertEqual(self.posted()[-1]["price"], "1.09075")
        # The opposite plan does not cancel or replace.
        self.setUp()
        router = self.make(v2="1")
        self._seed(router, limit="1.08875")
        self._book(limit="1.08875", bid="1.08880", ask="1.08890")
        opposite = context(run_id="run-2", side="SHORT", entry=1.09, stop=1.0925, target=1.085)
        self.assertEqual(router.on_cycle(opposite, "PLAN_READY", NOW, h1_bars=fall()),
                         ("NOT_ROUTED", "position_already_open"))
        self.assertEqual(self.cancels(), [])
        self.assertEqual(router._store.trade(CID)["status"], PENDING_LIMIT)
        self.assertEqual(self.posted(), [])

    def test_a_rejected_same_side_plan_does_not_cancel_the_resting_limit(self):
        router = self.make(v2="1")
        self._seed(router, limit="1.08875")
        self._book(limit="1.08875", bid="1.08880", ask="1.08890")
        refused = router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW, h1_bars=flat())
        self.assertEqual(refused, ("NOT_ROUTED", "trend_flat"))
        self.assertEqual(self.cancels(), [])
        self.assertEqual(router._store.trade(CID)["status"], PENDING_LIMIT)

    def test_an_open_position_still_blocks_with_v2_on(self):
        router = self.make(v2="1", offset="5pips")
        opened = NOW.isoformat()
        router._store.insert_intent({
            "client_id": CID, "run_id": "run-1", "symbol": "EURUSD", "side": "LONG", "units": 40000,
            "entry": "1.08875", "stop": "1.08625", "target": "1.09375", "trade_id": "6397",
            "fill_price": "1.08875", "opened_at": opened, "status": OPEN, "order_kind": "LIMIT",
            "strategy": STRATEGY, "scale_stage": "NONE", "units_remaining": 40000, "risk_distance": "0.00250",
            "limit_price": "1.08875", "signal_entry": "1.09000"})
        self.server.add("GET", OPEN_TRADES, trades(open_trade(ext=CID, price="1.08875", sl="1.08625")))
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08880", ask="1.08890")]
        blocked = router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(blocked, ("NOT_ROUTED", "position_already_open"))
        self.assertEqual(self.cancels(), [])
        self.assertEqual(router._store.trade(CID)["status"], OPEN)
        self.assertEqual(self.posted(), [])

    def test_an_accepted_cancel_that_leaves_a_trade_does_not_send(self):
        router = self.make(v2="1")
        filled = trades(open_trade(ext=CID, price="1.08875", sl="1.08625"))
        self._arm_cancel(router, opens=(trades(), filled, filled), cancel=self.cancel_ok())
        outcome = router.on_cycle(self.replacement(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome, ("NOT_ROUTED", "broker_trade_open"))
        self.assertEqual(self.posted(), [])
        self.assertEqual(router._store.trade(CID)["status"], OPEN)
        self.assertEqual(self.events("RESOLVED")[0][2]["reason"], "cancel_left_a_fill")

    def test_cancel_not_found_and_unknown_do_not_send(self):
        router = self.make(v2="1")
        self._arm_cancel(router, opens=(trades(),), cancel=(404, {"errorCode": "NO_SUCH_ORDER"}))
        self.assertEqual(router.on_cycle(self.replacement(), "PLAN_READY", NOW, h1_bars=ramp()),
                         ("NOT_ROUTED", "limit_cancel_failed"))
        self.assertEqual(self.posted(), [])
        self.assertEqual(router._store.trade(CID)["status"], PENDING_LIMIT)
        self.setUp()
        router = self.make(v2="1")
        self._arm_cancel(router, opens=(trades(),), cancel=(200, {}))
        self.assertEqual(router.on_cycle(self.replacement(), "PLAN_READY", NOW, h1_bars=ramp()),
                         ("NOT_ROUTED", "limit_cancel_failed"))
        self.assertEqual(self.posted(), [])
        self.assertEqual(router._store.trade(CID)["action_status"], "UNKNOWN")

    def test_a_fill_that_appears_between_cancel_and_submit_does_not_send(self):
        router = self.make(v2="1")
        filled = trades(open_trade(ext=CID, price="1.08875", sl="1.08625"))
        # Reconcile is empty. The open-trade read is still empty. The position read then shows the fill.
        self._arm_cancel(router, opens=(trades(), trades(), filled), cancel=self.cancel_ok())
        outcome = router.on_cycle(self.replacement(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome, ("NOT_ROUTED", "broker_position_open"))
        self.assertEqual(self.posted(), [])

    def test_a_favorable_cancel_does_not_let_the_same_cycle_send_over_a_fill(self):
        router = self.make(v2="1")
        self._seed(router, limit="1.08875")
        filled = trades(open_trade(ext=CID, price="1.09025", sl="1.08625"))
        order = {"id": "200", "instrument": "EUR_USD", "price": "1.08875", "clientExtensions": {"id": CID}}
        self.server.add("GET", OPEN_TRADES, trades(), filled, filled)
        self.server.add("GET", f"{A}/pendingOrders", (200, {"orders": [order]}), (200, {"orders": []}))
        self.server.add("GET", PRICING, pricing(bid="1.09025", ask="1.09035"))
        self.server.add("GET", SUMMARY, (200, {"account": {"balance": "10000", "NAV": "10000", "marginUsed": "0",
                                                           "currency": "USD"}}))
        self.server.add("PUT", f"{A}/orders/200/cancel", self.cancel_ok())
        self.server.add("POST", ORDERS, self.resting())
        outcome = router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome, ("NOT_ROUTED", "broker_trade_open"))
        self.assertEqual(self.posted(), [])
        self.assertEqual(self.cancels(), [f"{A}/orders/200/cancel"])
        self.assertEqual(router._store.trade(CID)["status"], OPEN)

    def test_five_pips_is_independent_of_the_4h_flag(self):
        router = self.make(offset="5pips")
        self.ready(order=self.resting("1.08950"))
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08940", ask="1.08950")]
        outcome = router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome[0], PENDING_LIMIT)
        order = self.posted()[0]
        self.assertEqual((order["price"], order["stopLossOnFill"]["price"], order["takeProfitOnFill"]["price"]),
                         ("1.08950", "1.08700", "1.09450"))
        self.assertEqual(order["gtdTime"], gtd_timestamp(limit_expiry(NOW)))  # still 24h while v2 is off
        self.assertEqual(self.events("PULLBACK_PLAN")[0][2]["offset_pips"], "5")
        self.assertEqual(self.events("LIMIT_PENDING")[0][2]["entry_offset_mode"], "5pips")

    def test_market_mode_reuses_the_fill_anchor_market_order(self):
        router = self.make(offset="market", v2="1")
        self.ready()  # quote ask equals the 1.09000 signal, so the anchor delta is zero
        ctx = context(entry=1.09, stop=1.0875, target=1.095)
        outcome = router.on_cycle(ctx, "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(outcome[0], "ACCEPTED")
        order = self.posted()[0]
        self.assertEqual(order["type"], "MARKET")
        self.assertNotIn("price", order)
        self.assertEqual((order["stopLossOnFill"]["price"], order["takeProfitOnFill"]["price"]),
                         ("1.08750", "1.09500"))
        self.assertFalse(self.events("PULLBACK_PLAN"))
        self.assertTrue(self.events("FILL_ANCHORED"))
        self.assertEqual(router._store.trade(CID)["order_kind"], "MARKET")

    def test_turning_the_flags_off_restores_legacy_on_the_next_cycle(self):
        router = self.make(v2="1", offset="5pips")
        self.ready(order=self.resting("1.08950"))
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08940", ask="1.08950")]
        router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=ramp())
        self.assertEqual(self.posted()[0]["price"], "1.08950")
        self.assertEqual(router._store.trade(CID)["expires_at"], (NOW + timedelta(hours=4)).isoformat())
        router._env[V2] = "0"
        router._env[OFFSET] = "legacy"
        self.assertFalse(router._limit_management_v2())
        self.assertEqual(router._entry_offset_mode(), "legacy")
        # The order already sent keeps its 4h GTD. The new rules stop immediately.
        self.server.add("GET", f"{A}/pendingOrders", (200, {"orders": [
            {"id": "200", "instrument": "EUR_USD", "price": "1.08950", "clientExtensions": {"id": CID}}]}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.09150", ask="1.09160")]  # >15 pips in favor
        self.now = NOW + timedelta(hours=1)
        self.assertIsNone(router.manage(self.now).get(CID))
        self.assertEqual(self.cancels(), [])
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", self.now, h1_bars=ramp())[1],
                         "position_already_open")
        router._store.update(CID, status="CANCELLED", closed_reason="LIMIT_EXPIRED")
        self.server.routes.clear()
        self.ready(order=self.resting())
        self.server.add("GET", f"{A}/trades/@aif-run-2", NOT_FOUND)
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08880", ask="1.08890")]
        self.now = NOW + timedelta(hours=2)
        with self.assertLogs(LOG, "INFO") as logs:
            outcome = router.on_cycle(context(run_id="run-2"), "PLAN_READY", self.now, h1_bars=ramp())
        self.assertEqual(outcome[0], PENDING_LIMIT)
        self.assertEqual(self.posted()[-1]["price"], "1.08875")
        self.assertEqual(router._store.trade("aif-run-2")["expires_at"], limit_expiry(self.now).isoformat())
        self.assertFalse(any("event=CYCLE_FLAGS" in record.getMessage() for record in logs.records))
        self.assertNotIn("ttl_hours", self.events("LIMIT_PENDING")[-1][2])

    def test_a_non_practice_broker_cannot_use_either_flag(self):
        with patch("runtime.demo_runner.REAL_EXECUTION_ENABLED", True):
            self.assertIsNone(self.make(v2="1", offset="market"))
        router = self.make(v2="1", offset="5pips")
        router._broker.mode = "live"
        self.assertFalse(router._limit_management_v2())
        self.assertEqual(router._entry_offset_mode(), "legacy")
        router._broker.mode = "broker_demo"
        router._broker._client._base = "https://api-fxtrade.oanda.com/v3"
        self.assertFalse(router._limit_management_v2())
        self.assertEqual(router._entry_offset_mode(), "legacy")

    def replacement(self):
        return context(run_id="run-2", entry=1.092, stop=1.089, target=1.097)

    def cancel_ok(self):
        return (200, {"orderCancelTransaction": {"id": "201", "reason": "CLIENT_REQUEST"}})

    def _arm_cancel(self, router, *, opens, cancel):
        self._seed(router, limit="1.08875")
        order = {"id": "200", "instrument": "EUR_USD", "price": "1.08875", "clientExtensions": {"id": CID}}
        self.server.add("GET", OPEN_TRADES, *opens)
        self.server.add("GET", f"{A}/pendingOrders", (200, {"orders": [order]}), (200, {"orders": []}))
        self.server.add("GET", PRICING, pricing(bid="1.08880", ask="1.08890"))
        self.server.add("GET", SUMMARY, (200, {"account": {"balance": "10000", "NAV": "10000", "marginUsed": "0",
                                                           "currency": "USD"}}))
        self.server.add("GET", f"{A}/trades/@aif-run-2", NOT_FOUND)
        self.server.add("PUT", f"{A}/orders/200/cancel", cancel)
        self.server.add("POST", ORDERS, self.resting("1.09075"))

    def _seed(self, router, *, side="LONG", limit="1.08875", stop="1.08625", target="1.09375"):
        router._store.insert_intent({
            "client_id": CID, "run_id": "run-1", "symbol": "EURUSD", "side": side, "units": 40000,
            "entry": limit, "stop": stop, "target": target, "status": PENDING_LIMIT, "order_kind": "LIMIT",
            "order_id": "200", "limit_price": limit, "expires_at": (NOW + timedelta(hours=24)).isoformat(),
            "strategy": STRATEGY, "scale_stage": "NONE", "units_remaining": 40000, "risk_distance": "0.00250",
            "signal_entry": "1.09000"})

    def _book(self, *, limit, bid, ask):
        self.server.add("GET", OPEN_TRADES, trades())
        order = {"id": "200", "instrument": "EUR_USD", "price": limit, "clientExtensions": {"id": CID}}
        self.server.add("GET", f"{A}/pendingOrders", (200, {"orders": [order]}), (200, {"orders": []}))
        self.server.add("GET", PRICING, pricing(bid=bid, ask=ask))


class EmaPriorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "broker_state.db"
        self.store = DemoStateStore(self.db)
        self.store.initialize()
        self.broker = _Book()
        self.registry = SimpleNamespace(emergency_active=lambda: False, is_allowed=lambda _mode: True)

    def router(self, **flag):
        return BrokerDemoRouter(
            self.broker, self.store, registry=self.registry, dry_run=False, max_units=100000, pullback=True,
            ema_cross=True, env={**flags(**flag), "AI_FLOOR_EMA_CROSS": "1"})

    def seed_limit(self):
        self.store.insert_intent({
            "client_id": "aif-pullback", "run_id": "run-pull", "symbol": "EURUSD", "side": "LONG", "units": 1000,
            "entry": "1.08875", "stop": "1.08625", "target": "1.09375", "status": PENDING_LIMIT,
            "order_kind": "LIMIT", "order_id": "200", "limit_price": "1.08875", "strategy": STRATEGY,
            "scale_stage": "NONE", "expires_at": (CONFIRM + timedelta(hours=20)).isoformat()})
        self.broker.pending.append(PendingOrder("200", "EUR_USD", "aif-pullback", D("1.08875")))

    def test_flag_off_keeps_the_pullback_limit_through_the_ema_window(self):
        self.seed_limit()
        router = self.router()
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(self.broker.cancelled, [])
        self.assertEqual(self.store.trade("aif-pullback")["status"], PENDING_LIMIT)

    def test_ema_entry_cancels_the_pullback_limit_and_sends(self):
        self.seed_limit()
        router = self.router(v2="1")
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        self.assertEqual(self.broker.cancelled, [])  # arming does not cancel
        self.assertEqual(self.store.trade("aif-pullback")["status"], PENDING_LIMIT)
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.cancelled, ["200"])
        self.assertEqual(self.store.trade("aif-pullback")["closed_reason"], "EMA_PRIORITY")
        self.assertEqual(len(self.broker.plans), 1)
        self.assertEqual(self.broker.plans[0].side, "LONG")
        ema = [row for row in self.store.trades() if row["strategy"] == EMA_STRATEGY]
        self.assertEqual(len(ema), 1)
        self.assertEqual(ema[0]["order_kind"], "MARKET")

    def test_an_open_pullback_still_blocks_the_ema_when_v2_is_on(self):
        self.store.insert_intent({
            "client_id": "aif-pullback", "run_id": "run-pull", "symbol": "EURUSD", "side": "LONG", "units": 1000,
            "entry": "1.08875", "stop": "1.08625", "target": "1.09375", "status": OPEN, "trade_id": "9001",
            "order_kind": "LIMIT", "strategy": STRATEGY, "scale_stage": "NONE", "fill_price": "1.08875",
            "opened_at": CONFIRM.isoformat()})
        self.broker.trades.append(SimpleNamespace(
            trade_id="9001", instrument="EUR_USD", client_ext_id="aif-pullback", price=D("1.08875"),
            open_time=CONFIRM, stop=D("1.08625")))
        router = self.router(v2="1")
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(self.broker.cancelled, [])
        self.assertEqual(self.store.trade("aif-pullback")["status"], OPEN)

    def test_a_non_demo_mode_does_not_let_ema_cancel_the_limit(self):
        self.seed_limit()
        self.broker.mode = "live"
        router = self.router(v2="1")
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(self.broker.cancelled, [])
        self.assertEqual(self.store.trade("aif-pullback")["status"], PENDING_LIMIT)

    def test_ema_does_not_send_when_the_cancel_leaves_a_trade(self):
        self.seed_limit()
        self.broker.fill_on_cancel = True
        router = self.router(v2="1")
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.cancelled, ["200"])
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(self.store.trade("aif-pullback")["status"], OPEN)

    def test_ema_does_not_send_when_the_cancel_is_missing_or_unknown(self):
        for status, reason in (("REJECTED", "order_not_found"), ("UNKNOWN", "no_acceptance_evidence")):
            with self.subTest(status=status):
                self.setUp()
                self.seed_limit()
                self.broker.cancel_result = SimpleNamespace(status=status, reason=reason)
                router = self.router(v2="1")
                later = m15_long() + [(READY, m15_long()[-1][1])]
                router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
                router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
                self.assertEqual(self.broker.plans, [])
                self.assertEqual(self.store.trade("aif-pullback")["status"], PENDING_LIMIT)


class _Book:
    mode = "broker_demo"

    def __init__(self):
        self.trades = []
        self.pending = []
        self.cancelled = []
        self.plans = []

    def open_trades(self):
        return list(self.trades)

    def pending_orders(self):
        return list(self.pending)

    def cancel_order(self, order_id, client_id=None):
        self.cancelled.append(str(order_id))
        outcome = getattr(self, "cancel_result", None)
        if outcome is not None:
            return outcome
        self.pending = [order for order in self.pending if order.order_id != str(order_id)]
        if getattr(self, "fill_on_cancel", False):
            self.trades.append(SimpleNamespace(
                trade_id="9001", instrument="EUR_USD", client_ext_id="aif-pullback", price=D("1.08875"),
                open_time=READY, stop=D("1.08625"), units=D("1000")))
        return SimpleNamespace(status="ACCEPTED", reason=None)

    def get_balance(self):
        return SimpleNamespace(equity=D("100000"))

    def get_quote(self, symbol):
        return (True, D("1.08990"), D("1.09000"))

    def submit_plan(self, plan, approval):
        self.plans.append(plan)
        return SimpleNamespace(status="ACCEPTED", broker_ref="6397", reason=None)

    def trade_id_for_client(self, client_id):
        return None
