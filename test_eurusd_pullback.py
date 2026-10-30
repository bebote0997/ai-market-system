"""EURUSD H1 limit pullback: ATR/EMA, limit price, expiry, partials, breakeven, trail, and the fill-anchor rule.

The router cases use the fake OANDA transport. Existing market-order tests keep the flag at 0; these turn it on.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
import sqlite3
import unittest

from execution import trade_duration as td
from execution.broker_demo_state import CANCELLED, DemoStateStore, PENDING_LIMIT
from execution.eurusd_pullback import (ATR_PERIOD, EMA_PERIOD, STRATEGY, anchor_outcome, ema, limit_expiry,
                                       management_steps, plan_pullback, pullback_flag, round_fx, trail_stop,
                                       true_ranges, wilder_atr)
from execution.oanda_client import round_price
from test_broker_demo_router import A, CID, NOW, OPEN_TRADES, ORDERS, PRICING, RouterBase, context, open_trade, trades
from test_broker_oanda import pricing


PIP = D("0.0001")
PENDING = f"{A}/pendingOrders"


def ramp(n=EMA_PERIOD, start=D("1.00000"), step=D("0.00010"), span=D("0.00200")):
    """Rising closed bars whose true range is ``span`` (so Wilder ATR stays ``span``) and whose close is above the EMA."""
    close, bars = start, []
    for _ in range(n):
        bars.append((close, close - span, close))
        close += step
    return tuple(bars)


def fall(n=EMA_PERIOD, start=D("1.20000"), step=D("0.00010"), span=D("0.00200")):
    """Falling closes, true range fixed at ``span``, last close under the EMA."""
    close, bars = start, []
    for _ in range(n):
        bars.append((close + span, close, close))
        close -= step
    return tuple(bars)


def flat(n=EMA_PERIOD, price=D("1.10000")):
    return tuple((price, price - D("0.00100"), price) for _ in range(n))


class FlagTests(unittest.TestCase):
    def test_default_on_exact_zero_rolls_back(self):
        self.assertTrue(pullback_flag({}))
        self.assertTrue(pullback_flag({"AI_FLOOR_EURUSD_LIMIT_PULLBACK": "1"}))
        self.assertFalse(pullback_flag({"AI_FLOOR_EURUSD_LIMIT_PULLBACK": "0"}))


class IndicatorTests(unittest.TestCase):
    def test_wilder_atr_seeds_then_smooths(self):
        bars = [(D(1), D(1), D(1))]
        bars.extend((D(2), D(1), D(1)) for _ in range(ATR_PERIOD))  # true range 1, fourteen times
        self.assertEqual(true_ranges(bars), [D(1)] * ATR_PERIOD)
        self.assertEqual(wilder_atr(bars), D(1))
        self.assertIsNone(wilder_atr(bars[:ATR_PERIOD]))  # one true range short
        bars.append((D(4), D(1), D(1)))  # true range 3
        self.assertEqual(wilder_atr(bars), (D(13) + D(3)) / D(ATR_PERIOD))

    def test_ema_seeds_with_the_average_then_weights_the_next_close(self):
        closes = tuple(D(1) for _ in range(EMA_PERIOD))
        self.assertEqual(ema(closes), D(1))
        self.assertIsNone(ema(closes[:-1]))
        nxt = closes + (D(3),)
        weight = D(2) / D(EMA_PERIOD + 1)
        self.assertEqual(ema(nxt), D(3) * weight + D(1) * (1 - weight))

    def test_bad_bar_is_no_data(self):
        self.assertEqual(plan_pullback("LONG", D("1.1"), D("1.0"), D("1.2"), None), "h1_no_data")
        broken = list(flat())
        broken[-1] = (D("1"), D("2"), D("1.5"))  # high below low
        self.assertEqual(plan_pullback("LONG", D("1.1"), D("1.0"), D("1.2"), tuple(broken)), "h1_no_data")


class LimitPlanTests(unittest.TestCase):
    def test_long_limit_is_one_atr_under_the_signal_and_short_is_above(self):
        bars = ramp()
        long = plan_pullback("LONG", D("1.09000"), D("1.08000"), D("1.12000"), bars)
        self.assertEqual(long.limit, D("1.08800"))
        self.assertEqual(long.atr, D("0.00200"))
        self.assertEqual(long.risk, D("0.01000"))
        self.assertGreater(long.price, long.ema)
        short = plan_pullback("SHORT", D("1.09000"), D("1.10000"), D("1.07000"), fall())
        self.assertEqual(short.limit, D("1.09200"))
        self.assertLess(short.price, short.ema)

    def test_trend_filter_blocks_the_wrong_direction_and_a_tie(self):
        self.assertEqual(plan_pullback("SHORT", D("1.09000"), D("1.10000"), D("1.07000"), ramp()), "trend_filter")
        self.assertEqual(plan_pullback("LONG", D("1.10000"), D("1.09000"), D("1.12000"), flat()), "trend_flat")

    def test_limit_through_the_stop_is_not_sent(self):
        # ATR 0.002 pulls a 1.09000 buy through a stop that is only 0.001 away.
        reason = plan_pullback("LONG", D("1.09000"), D("1.08900"), D("1.12000"), ramp())
        self.assertEqual(reason, "limit_through_stop")

    def test_expiry_is_24h_and_stops_at_the_friday_entry_ban(self):
        monday = datetime(2026, 10, 12, 14, 0, tzinfo=timezone.utc)
        self.assertEqual(limit_expiry(monday), monday + timedelta(hours=24))
        friday_morning = datetime(2026, 10, 16, 14, 0, tzinfo=timezone.utc)  # 10:00 New York (EDT)
        self.assertEqual(limit_expiry(friday_morning), td.friday_flatten_after(friday_morning))
        self.assertLess(limit_expiry(friday_morning), friday_morning + timedelta(hours=24))


class ManagementTests(unittest.TestCase):
    def steps(self, price, stage="NONE", units=9, remaining=None, stop=D("1.08000")):
        return management_steps(side="LONG", fill=D("1.09000"), risk=D("0.01000"), units=units, stage=stage,
                                remaining=remaining, price=price, stop=stop)

    def test_partials_then_breakeven_then_trail_in_order(self):
        # Not yet at +0.5R (1.09500).
        self.assertEqual(self.steps(D("1.09400")), ())
        half = self.steps(D("1.09500"))
        self.assertEqual([(s.kind, s.label, s.units, s.remaining, s.stage) for s in half],
                         [("PARTIAL", "0.5R", 3, 6, "HALF")])
        # Exactly +1R: second third, then the stop at the fill (breakeven, not yet beyond it).
        one = self.steps(D("1.10000"), stage="HALF", remaining=6)
        self.assertEqual([(s.kind, s.label, s.units, s.remaining) for s in one],
                         [("PARTIAL", "1R", 3, 3), ("STOP", "BE", None, 3)])
        self.assertEqual(one[-1].stop, D("1.09000"))
        self.assertEqual(one[-1].journals, ("BREAKEVEN",))
        # A gap through both levels does the first third, the second third, then a stop past breakeven.
        gap = self.steps(D("1.10200"))
        self.assertEqual([s.kind for s in gap], ["PARTIAL", "PARTIAL", "STOP"])
        self.assertEqual(gap[-1].journals, ("BREAKEVEN", "TRAIL"))
        self.assertEqual(gap[-1].stop, D("1.09200"))  # price − 1R
        trail = self.steps(D("1.10500"), stage="TRAIL", remaining=3, stop=D("1.09200"))
        self.assertEqual([(trail[0].kind, trail[0].journals, trail[0].stop)],
                         [("STOP", ("TRAIL",), D("1.09500"))])
        self.assertEqual(self.steps(D("1.10000"), stage="TRAIL", remaining=3, stop=D("1.09500")), ())

    def test_short_mirror_and_unsplittable_size(self):
        steps = management_steps(side="SHORT", fill=D("1.09000"), risk=D("0.01000"), units=9, stage="NONE",
                                 remaining=None, price=D("1.07800"), stop=D("1.10000"))
        self.assertEqual([s.kind for s in steps], ["PARTIAL", "PARTIAL", "STOP"])
        self.assertEqual(steps[-1].stop, D("1.08800"))  # price + 1R, tighter than the fill
        whole = management_steps(side="LONG", fill=D("1.09000"), risk=D("0.01000"), units=2, stage="NONE",
                                 remaining=None, price=D("1.10000"), stop=D("1.08000"))
        self.assertEqual([(s.kind, s.journals) for s in whole], [("STOP", ("BREAKEVEN",))])
        self.assertEqual(trail_stop("SHORT", D("1.09000"), D("0.01000"), D("1.07000")), D("1.08000"))


class AnchorRuleTests(unittest.TestCase):
    def outcome(self, **kw):
        base = dict(kind="MARKET", pending=False, side="LONG", entry=D("1.10000"), stop=D("1.09550"),
                    target=D("1.10900"), quote_fill=D("1.10000"), limit=D("1.09800"), fill=None, pip=PIP,
                    max_pips=D(10), rounder=round_fx)
        base.update(kw)
        return anchor_outcome(**base)

    def test_market_keeps_the_ten_pip_cap(self):
        status, prices = self.outcome(quote_fill=D("1.10100"))  # exactly 10 pips
        self.assertEqual(status, "anchored")
        self.assertEqual(prices[0], D("1.10100"))
        self.assertEqual(prices[1] - D("1.09550"), D("0.00100"))
        status, prices = self.outcome(quote_fill=D("1.10101"))  # 10.1 pips
        self.assertEqual((status, prices), ("refused", None))

    def test_limit_fill_at_the_level_is_accepted_and_pending_is_not_a_bad_fill(self):
        levels = (D("1.10000"), D("1.09550"), D("1.10900"))
        self.assertEqual(self.outcome(kind="LIMIT", pending=True, quote_fill=D("1.09000")), ("pending", levels))
        self.assertEqual(self.outcome(kind="LIMIT", fill=None), ("pending", levels))
        self.assertEqual(self.outcome(kind="LIMIT", fill=D("1.09800")), ("accepted", levels))
        self.assertEqual(self.outcome(kind="LIMIT", fill=D("1.09700")), ("accepted", levels))  # better buy
        # A short filled below its sell limit sold too cheap: accepted only at the limit or higher.
        status, prices = self.outcome(kind="LIMIT", side="SHORT", fill=D("1.09700"), limit=D("1.09800"))
        self.assertEqual((status, prices), ("worse", levels))
        self.assertEqual(round_fx(D("1.090005")), round_price(D("1.090005")))


class MigrationTests(unittest.TestCase):
    def test_old_rows_gain_empty_columns_and_keep_their_prices(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.db"
            with sqlite3.connect(path) as con:
                con.execute("""CREATE TABLE broker_demo_trades (
                    client_id TEXT PRIMARY KEY, run_id TEXT, symbol TEXT, side TEXT, units INTEGER,
                    entry TEXT, stop TEXT, target TEXT, initial_stop TEXT, trade_id TEXT, fill_price TEXT,
                    opened_at TEXT, stage TEXT, status TEXT, closed_reason TEXT, pending_action TEXT,
                    action_status TEXT, created_at TEXT, updated_at TEXT)""")
                con.execute("INSERT INTO broker_demo_trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            ("old", "run", "EURUSD", "LONG", 10, "1.10000", "1.09500", "1.11000", None, "7",
                             "1.10010", "2026-10-01T00:00:00+00:00", None, "OPEN", None, None, None,
                             "2026-10-01T00:00:00+00:00", "2026-10-01T00:00:00+00:00"))
            row = DemoStateStore(path).trade("old")
        self.assertEqual((row["status"], row["stop"], row["strategy"], row["order_kind"]), ("OPEN", "1.09500", None, None))
        self.assertIsNone(row["limit_price"])


class RouterPullbackTests(RouterBase):
    def bars(self):
        return ramp()

    def resting(self):
        return (201, {"orderCreateTransaction": {"id": "200", "type": "LIMIT_ORDER", "price": "1.08800"}})

    def test_limit_keeps_plan_sl_tp_when_the_market_gap_would_refuse_a_market_order(self):
        router = self.router(AI_FLOOR_EURUSD_LIMIT_PULLBACK="1")
        alerts = []
        router.on_alert = lambda kind, detail=None: alerts.append(kind)
        self.ready(order=self.resting())
        # 16 pips under the 1.09000 signal: a market order is refused above 10 pips. The limit is still placed.
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08830", ask="1.08840")]
        outcome = router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=self.bars())
        self.assertEqual(outcome, (PENDING_LIMIT, "200"))
        order = [b for m, p, _h, b in self.server.calls if (m, p) == ("POST", ORDERS)][0]["order"]
        self.assertEqual(order["type"], "LIMIT")
        self.assertEqual(order["timeInForce"], "GTD")
        self.assertTrue(order["gtdTime"].endswith("Z"))
        self.assertEqual((order["price"], order["stopLossOnFill"]["price"], order["takeProfitOnFill"]["price"]),
                         ("1.08800", "1.08000", "1.12000"))
        row = self.rows()[0]
        self.assertEqual((row["status"], row["strategy"], row["stop"], row["target"]),
                         (PENDING_LIMIT, STRATEGY, "1.08000", "1.12000"))
        self.assertFalse(self.events("FILL_ANCHOR_REFUSED"))
        self.assertFalse(self.events("FILL_OUT_OF_POLICY"))
        self.assertEqual(alerts, ["LIMIT_PENDING"])
        # Still resting: not a fill, and it blocks a second entry.
        self.server.add("GET", PENDING, (200, {"orders": [{"id": "200", "instrument": "EUR_USD", "price": "1.08800",
                                                           "clientExtensions": {"id": CID}}]}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW, h1_bars=self.bars())[0],
                         "NOT_ROUTED")
        self.assertEqual(self.rows()[0]["status"], PENDING_LIMIT)

    def test_unfilled_limit_is_cancelled_at_expiry_and_when_the_broker_drops_it(self):
        router = self.router(AI_FLOOR_EURUSD_LIMIT_PULLBACK="1")
        self.ready(order=self.resting())
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08830", ask="1.08840")]
        router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=self.bars())
        self.server.add("GET", PENDING, (200, {"orders": [{"id": "200", "instrument": "EUR_USD",
                                                           "clientExtensions": {"id": CID}}]}))
        self.server.add("PUT", f"{A}/orders/200/cancel",
                        (200, {"orderCancelTransaction": {"id": "201", "reason": "CLIENT_REQUEST"}}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.now = NOW + timedelta(hours=24)
        self.assertEqual(router.manage(self.now)[CID], "LIMIT_EXPIRED")
        self.assertEqual((self.rows()[0]["status"], self.rows()[0]["closed_reason"]), (CANCELLED, "LIMIT_EXPIRED"))

    def test_limit_the_broker_already_dropped_is_cancelled_without_a_new_entry(self):
        router = self.router(AI_FLOOR_EURUSD_LIMIT_PULLBACK="1")
        self.ready(order=self.resting())
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08830", ask="1.08840")]
        router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=self.bars())
        self.server.add("GET", PENDING, (200, {"orders": []}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.server.add("GET", f"{A}/trades/@{CID}", (404, {"errorCode": "NO_SUCH_TRADE"}))
        self.assertIsNotNone(router.reconcile())
        self.assertEqual(self.rows()[0]["status"], CANCELLED)

    def test_fill_at_the_limit_is_accepted_without_moving_sl_tp(self):
        router = self.router(AI_FLOOR_EURUSD_LIMIT_PULLBACK="1")
        self.ready(order=self.resting())
        self.server.routes[("GET", PRICING)] = [pricing(bid="1.08830", ask="1.08840")]
        router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=self.bars())
        filled = open_trade(ext=CID, price="1.08800", sl="1.08000")
        filled["takeProfitOrder"] = {"id": "2", "price": "1.12000"}
        self.server.routes[("GET", OPEN_TRADES)] = [trades(filled)]
        self.server.routes[("GET", PENDING)] = [(200, {"orders": []})]
        router.reconcile()
        row = self.rows()[0]
        self.assertEqual((row["status"], row["fill_price"], row["stop"], row["target"]),
                         ("OPEN", "1.08800", "1.08000", "1.12000"))
        self.assertTrue(self.events("FILL_VERIFIED"))
        self.assertFalse(self.events("FILL_OUT_OF_POLICY"))

    def test_partials_breakeven_and_trail_and_an_older_position_is_left_alone(self):
        router = self.router(AI_FLOOR_EURUSD_LIMIT_PULLBACK="1")
        opened = "2026-10-12T14:00:00+00:00"
        router._store.insert_intent({
            "client_id": CID, "run_id": "run-1", "symbol": "EURUSD", "side": "LONG", "units": 9,
            "entry": "1.08800", "stop": "1.08000", "target": "1.12000", "trade_id": "6397",
            "fill_price": "1.08800", "opened_at": opened, "status": "OPEN", "order_kind": "LIMIT",
            "strategy": STRATEGY, "scale_stage": "NONE", "units_remaining": 9, "risk_distance": "0.01000",
            "signal_entry": "1.09000", "limit_price": "1.08800"})
        # +1.2R from the fill (risk 0.01000): both partials and a stop beyond breakeven on this tick.
        self.server.add("GET", OPEN_TRADES, trades(open_trade(ext=CID, units="9", price="1.08800", sl="1.08000")))
        self.server.add("GET", PRICING, pricing(bid="1.10000", ask="1.10010"))
        self.server.add("GET", f"{A}/trades/6397", (200, {"trade": open_trade(units="9", price="1.08800", sl="1.08000")}))
        reduced = {"id": "301", "tradeReduced": {"tradeID": "6397", "units": "3"}}
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": reduced}),
                        (200, {"orderFillTransaction": reduced}))
        self.server.add("PUT", f"{A}/trades/6397/orders",
                        (200, {"stopLossOrderTransaction": {"id": "9", "type": "STOP_LOSS", "tradeID": "6397",
                                                            "price": "1.09000", "timeInForce": "GTC"}}))
        result = router.manage(NOW + timedelta(minutes=5))
        self.assertEqual(result[CID], "TRAIL")
        row = self.rows()[0]
        self.assertEqual((row["scale_stage"], int(row["units_remaining"]), row["stop"], row["initial_stop"]),
                         ("TRAIL", 3, "1.09000", "1.08000"))
        closes = [c[3] for c in self.server.calls if c[0] == "PUT" and c[1].endswith("/close")]
        self.assertEqual(closes, [{"units": "3"}, {"units": "3"}])
        self.assertTrue(self.events("PARTIAL_CLOSE"))
        self.assertTrue(self.events("BREAKEVEN"))
        self.assertTrue(self.events("TRAIL"))
        # An open position from before this strategy (no tag) is not scaled, even with the flag on.
        router._store.insert_intent({
            "client_id": "aif-old", "run_id": "old", "symbol": "EURUSD", "side": "LONG", "units": 9,
            "entry": "1.09000", "stop": "1.08000", "target": "1.12000", "trade_id": "7000",
            "fill_price": "1.09000", "opened_at": opened, "status": "OPEN"})
        puts = len([c for c in self.server.calls if c[0] == "PUT"])
        self.server.routes[("GET", OPEN_TRADES)] = [trades(
            open_trade(ext=CID, units="3", price="1.08800", sl="1.09000"),
            open_trade(trade_id="7000", ext="aif-old", units="9", price="1.09000", sl="1.08000"))]
        managed = router.manage(NOW + timedelta(minutes=6))
        self.assertEqual(managed["aif-old"], "KEEP")
        self.assertEqual(len([c for c in self.server.calls if c[0] == "PUT"]), puts)

    def test_flag_off_still_sends_a_market_order(self):
        router = self.router()  # helper forces the rollback flag
        self.assertFalse(router.pullback)
        self.ready()
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW, h1_bars=self.bars())[0], "ACCEPTED")
        order = [b for m, p, _h, b in self.server.calls if (m, p) == ("POST", ORDERS)][0]["order"]
        self.assertEqual(order["type"], "MARKET")


class AlertTextTests(unittest.TestCase):
    def test_pending_partial_and_trail_lines_carry_the_level(self):
        from runtime.broker_alerts import build_message
        pending = build_message("LIMIT_PENDING", {"symbol": "EURUSD", "side": "LONG", "units": 9000,
                                                  "limit_price": "1.08800", "reason": "GTD"})
        partial = build_message("PARTIAL_CLOSE", {"symbol": "EURUSD", "side": "LONG", "units": 3000, "reason": "0.5R"})
        trail = build_message("TRAIL", {"symbol": "EURUSD", "side": "LONG", "stop": "1.09000", "reason": "TRAIL"})
        self.assertIn("límite 1.08800", pending)
        self.assertIn("Cierre parcial", partial)
        self.assertIn("stop 1.09000", trail)
        self.assertNotIn("101-", pending)
