"""DEC-FILL-ANCHOR-1: the router translates entry / SL / TP of an FX plan onto the expected MARKET fill (ask for LONG,
bid for SHORT) when the quote is within 5 pips of the planned entry, so the SL/TP pips and R stay exactly the policy's
(DEC-FX-SL-1). Above the tolerance / without a valid quote / non-FX: unchanged. Fake transport only; no network."""
from datetime import timedelta
from decimal import Decimal as D
from unittest.mock import patch

from core.fx_policy import FX_FILL_RR_FLOOR, FX_SL_MAX_PIPS, FX_TP_RR
from execution import broker_demo_router as bdr
from execution.broker_port import approval_problem
from test_broker_demo_router import (A, CID, NOW, OPEN_TRADES, ORDERS, PRICING, RouterBase, close_fill, context,
                                     open_trade, summary, trades)
from test_broker_oanda import pricing
from test_reversal import NEW_CID, NEW_FILL
from test_reversal import SHORT as REV_SHORT

PIP = D("0.0001")
# Oct 27 04:30 EURUSD SHORT (clamped to the 60 pip maximum): planned at the candle close 1.11552.
SHORT = {"side": "SHORT", "entry": 1.11552, "stop": 1.12152, "target": 1.10352}
LONG = {"side": "LONG", "entry": 1.09, "stop": 1.084, "target": 1.102}


class FillAnchorTests(RouterBase):
    def route(self, geometry, quote, **env):
        router = self.router(**env)
        self.ready(nav="100000")
        self.server.routes[("GET", PRICING)] = [quote]
        spy = patch.object(router._broker, "submit_plan", wraps=router._broker.submit_plan).start()
        self.addCleanup(patch.stopall)
        outcome = router.on_cycle(context(**geometry), "PLAN_READY", self.now)
        return outcome, spy

    def order(self):
        [body] = [b for m, p, _h, b in self.server.calls if (m, p) == ("POST", ORDERS)]
        return body["order"]

    def prices(self):
        row = self.rows()[0]
        return tuple(D(str(row[k])).quantize(D("0.00001")) for k in ("entry", "stop", "target"))

    def assert_policy_distances(self, entry, stop, target, sl_pips=FX_SL_MAX_PIPS):
        self.assertEqual(abs(entry - stop) / PIP, sl_pips)
        self.assertEqual(abs(target - entry) / PIP, sl_pips * FX_TP_RR)
        self.assertEqual(abs(target - entry) / abs(entry - stop), FX_TP_RR)
        self.assertGreaterEqual(abs(target - entry) / abs(entry - stop), FX_FILL_RR_FLOOR)

    def test_short_anchored_on_the_bid_keeps_exactly_60_pips_and_2r(self):
        outcome, spy = self.route(SHORT, pricing(bid="1.11536", ask="1.11546"))
        self.assertEqual(outcome[0], "ACCEPTED")
        entry, stop, target = self.prices()
        self.assertEqual((entry, stop, target), (D("1.11536"), D("1.12136"), D("1.10336")))
        self.assert_policy_distances(entry, stop, target)
        order = self.order()
        self.assertEqual((order["stopLossOnFill"]["price"], order["takeProfitOnFill"]["price"]), ("1.12136", "1.10336"))
        plan, approval = spy.call_args.args
        self.assertIsNone(approval_problem(plan, approval))
        self.assertEqual((plan.planned_entry, plan.stop, plan.target), (entry, stop, target))
        [(_kind, cid, detail)] = self.events("FILL_ANCHORED")
        self.assertEqual((cid, detail["delta_pips"], detail["sl_pips"], detail["tp_pips"]), (CID, "-1.6", "60", "120"))
        self.assertFalse(any("trade_id" in k or "account" in k for k in detail))
        [(_k, _c, intent)] = self.events("INTENT")
        self.assertEqual((intent["entry"], intent["stop"], intent["target"]), ("1.11536", "1.12136", "1.10336"))

    def test_long_anchored_on_the_ask(self):
        outcome, spy = self.route(LONG, pricing(bid="1.09020", ask="1.09030"))
        self.assertEqual(outcome[0], "ACCEPTED")
        entry, stop, target = self.prices()
        self.assertEqual((entry, stop, target), (D("1.09030"), D("1.08430"), D("1.10230")))
        self.assert_policy_distances(entry, stop, target)
        self.assertIsNone(approval_problem(*spy.call_args.args))
        self.assertEqual(self.events("FILL_ANCHORED")[0][2]["delta_pips"], "3")

    def test_units_are_unchanged(self):
        self.route(SHORT, pricing(bid="1.11536", ask="1.11546"))
        anchored_units = self.rows()[0]["units"]
        self.setUp()
        self.route(SHORT, pricing(bid="1.11480", ask="1.11490"))  # above the tolerance: planned prices
        self.assertEqual(self.rows()[0]["units"], anchored_units)

    def test_above_tolerance_is_unchanged(self):
        outcome, spy = self.route(SHORT, pricing(bid="1.11480", ask="1.11490"))  # -7.2 pips
        self.assertEqual(outcome[0], "ACCEPTED")
        self.assertEqual(self.prices(), (D("1.11552"), D("1.12152"), D("1.10352")))
        self.assertEqual(self.events("FILL_ANCHORED"), [])
        [(_k, _c, detail)] = self.events("FILL_ANCHOR_SKIPPED")
        self.assertEqual((detail["reason"], detail["delta_pips"]), ("delta_above_tolerance", "-7.2"))
        self.assertIsNone(approval_problem(*spy.call_args.args))

    def test_exactly_at_the_tolerance_is_anchored(self):
        self.route(SHORT, pricing(bid="1.11502", ask="1.11512"))  # -5.0 pips
        self.assertEqual(self.prices(), (D("1.11502"), D("1.12102"), D("1.10302")))

    def test_quote_missing_or_not_tradeable_is_unchanged(self):
        for quote, reason in (((200, {"prices": []}), "pricing_malformed"),
                              (pricing(bid="1.11536", ask="1.11546", tradeable=False), "not_tradeable")):
            with self.subTest(reason=reason):
                self.setUp()
                outcome, _spy = self.route(SHORT, quote, AI_FLOOR_BROKER_DEMO_DRY_RUN="1")
                self.assertEqual(outcome[0], "DRY_RUN")
                self.assertEqual(self.prices(), (D("1.11552"), D("1.12152"), D("1.10352")))
                self.assertEqual(self.events("FILL_ANCHOR_SKIPPED")[0][2]["reason"], reason)

    def test_dry_run_previews_the_anchored_order(self):
        outcome, _spy = self.route(SHORT, pricing(bid="1.11536", ask="1.11546"), AI_FLOOR_BROKER_DEMO_DRY_RUN="1")
        self.assertEqual(outcome[0], "DRY_RUN")
        self.assertEqual(self.prices(), (D("1.11536"), D("1.12136"), D("1.10336")))
        [(_k, _c, detail)] = self.events("DRY_RUN")
        order = detail["order"]["order"] if "order" in detail["order"] else detail["order"]
        self.assertEqual(order["stopLossOnFill"]["price"], "1.12136")
        self.assertEqual([m for m, *_ in self.server.calls if m != "GET"], [])  # read-only

    def test_non_fx_symbol_is_untouched_without_a_quote_read(self):
        router = self.router()
        before = len(self.server.calls)
        prices = (D("4139.45"), D("4216.21"), D("3907.85"))
        self.assertEqual(router._fill_anchor(CID, "XAUUSD", "SHORT", *prices), prices)
        self.assertEqual(len(self.server.calls), before)
        self.assertEqual(self.events("FILL_ANCHORED") + self.events("FILL_ANCHOR_SKIPPED"), [])

    def anchor(self, symbol, side, prices, quote):
        router = self.router()
        with patch.object(router._broker, "get_quote", return_value=quote):
            return router._fill_anchor(CID, symbol, side, *prices)

    def test_malformed_quote_is_unchanged(self):
        prices = (D("1.11552"), D("1.12152"), D("1.10352"))
        for name, quote in (("ask_below_bid", (True, D("1.11546"), D("1.11536"))),
                            ("zero_bid", (True, D("0"), D("1.11546"))),
                            ("negative_bid", (True, D("-1.11536"), D("1.11546"))),
                            ("two_items", (True, D("1.11536"))),
                            ("four_items", (True, D("1.11536"), D("1.11546"), D("0"))),
                            ("not_a_tuple", [True, D("1.11536"), D("1.11546")])):
            with self.subTest(name=name):
                self.setUp()
                self.assertEqual(self.anchor("EURUSD", "SHORT", prices, quote), prices)
                self.assertEqual(self.events("FILL_ANCHORED"), [])
                [(_k, cid, detail)] = self.events("FILL_ANCHOR_SKIPPED")
                self.assertEqual((cid, detail["reason"]), (CID, "pricing_malformed"))

    def test_jpy_pair_uses_a_0_01_pip(self):
        # USDJPY: 60 pip SL = 0.60, 120 pip TP = 1.20; +/-5 pips = 0.05 (pip 0.01, not 0.0001).
        long_prices = (D("150.000"), D("149.400"), D("151.200"))
        short_prices = (D("150.000"), D("150.600"), D("148.800"))
        cases = (("LONG", long_prices, (True, D("150.040"), D("150.050")), "5",
                  (D("150.05"), D("149.45"), D("151.25"))),
                 ("SHORT", short_prices, (True, D("149.950"), D("149.960")), "-5",
                  (D("149.95"), D("150.55"), D("148.75"))))
        for side, prices, quote, delta, expected in cases:
            with self.subTest(side=side):
                self.setUp()
                entry, stop, target = self.anchor("USDJPY", side, prices, quote)
                self.assertEqual((entry, stop, target), expected)
                self.assertEqual(abs(entry - stop) / D("0.01"), FX_SL_MAX_PIPS)
                self.assertEqual(abs(target - entry) / D("0.01"), FX_SL_MAX_PIPS * FX_TP_RR)
                [(_k, _c, detail)] = self.events("FILL_ANCHORED")
                self.assertEqual((detail["delta_pips"], detail["sl_pips"], detail["tp_pips"]), (delta, "60", "120"))
        self.setUp()  # 5.1 JPY pips is above the tolerance
        self.assertEqual(self.anchor("USDJPY", "LONG", long_prices, (True, D("150.041"), D("150.051"))), long_prices)
        self.assertEqual(self.events("FILL_ANCHOR_SKIPPED")[0][2]["delta_pips"], "5.1")


class FillAnchorAfterReversalTests(RouterBase):
    def test_reversal_resizes_then_anchors(self):
        router = self.router()
        self.ready()
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW)[0], "ACCEPTED")  # LONG 1.09000
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade())]
        # 1st quote: the LONG (fill 1.09000) is in profit -> reverse; 2nd quote: the SHORT anchors on the bid (-2 pips).
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.09500", ask="1.09510"),
                                                pricing(bid="1.09480", ask="1.09490")]
        self.server.routes[("GET", f"{A}/summary")] = [summary("10050"), summary("5000")]  # NAV before / after close
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": close_fill()}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades(), trades(),
                                                    trades(open_trade(trade_id="6400", ext=NEW_CID, units="-100"))]
        self.server.add("GET", f"{A}/trades/@{NEW_CID}", (404, {"errorCode": "TRADE_DOES_NOT_EXIST"}))
        self.server.routes[("POST", ORDERS)] = [NEW_FILL]
        spy = patch.object(router._broker, "submit_plan", wraps=router._broker.submit_plan).start()
        self.addCleanup(patch.stopall)
        self.assertEqual(router.on_cycle(context(run_id="run-2", **REV_SHORT), "PLAN_READY", NOW + timedelta(hours=1)),
                         ("ACCEPTED", "6400"))
        self.assertEqual(self.events(bdr.REVERSAL)[0][2], {"closed_client_id": CID, "outcome": "CLOSED"})
        dec = context(run_id="run-2", **REV_SHORT)["deterministic"].risk_decision
        units = bdr.broker_units(D("5000"), dec, D("1.095"), D("1.1"), router.max_units)  # re-sized after the close
        self.assertNotEqual(units, bdr.broker_units(D("10050"), dec, D("1.095"), D("1.1"), router.max_units))
        order = [b for m, p, _h, b in self.server.calls if m == "POST" and p == ORDERS][-1]["order"]
        self.assertEqual(order["units"], str(-units))  # anchoring keeps the re-sized units
        self.assertEqual((order["stopLossOnFill"]["price"], order["takeProfitOnFill"]["price"]), ("1.09980", "1.08480"))
        plan, approval = spy.call_args.args
        self.assertIsNone(approval_problem(plan, approval))
        self.assertEqual((plan.planned_entry, plan.stop, plan.target, plan.quantity),
                         (D("1.09480"), D("1.09980"), D("1.08480"), D(units)))
        [(_k, cid, detail)] = [e for e in self.events("FILL_ANCHORED") if e[1] == NEW_CID]
        self.assertEqual((detail["delta_pips"], detail["sl_pips"], detail["tp_pips"]), ("-2", "50", "100"))
