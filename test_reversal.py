"""DEC-REVERSAL-1: opposite-direction setup while the trade is in profit -> close and open the new direction (every
gate applies); in loss -> keep; same direction -> no action. PAPER (V2_OPPOSITE_SETUP_POLICY=REVERSE_IN_PROFIT) and
the OANDA demo router (ON by default with routing; AI_FLOOR_DEMO_REVERSAL=0 -> OFF). Fake transport, no network."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from execution import broker_demo_router as bdr
from execution import opposite_policy
from execution.contracts import PaperOrder
from execution.paper_broker import PaperBroker
from runtime.config import OPPOSITE_SETUP_POLICIES, RuntimeConfig
from runtime.demo_runner import REAL_EXECUTION_ENABLED
from runtime.scheduler import slot_key
from runtime.service import OperationalRuntime
from test_broker_demo_router import (A, CID, NOW, OPEN_TRADES, PRICING, RouterBase, close_fill, context, open_trade,
                                     pricing, trades)
from test_broker_demo_router import summary
from test_broker_oanda import ORDERS
from test_dec14_opposite_policy import reports, snapshot
from test_demo_runner import T
from test_phase5_risk_engine import INS, account, position

ROOT = Path(__file__).resolve().parent


class PaperReversalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = RuntimeConfig(db_path=Path(self.tmp.name) / "paper.db",
                                    v2_opposite_setup_policy="REVERSE_IN_PROFIT", market_provider_mode="none")
        self.rt = OperationalRuntime(self.config, instruments=INS, clock=lambda: T)
        self.addCleanup(lambda: self.rt.close())
        self.key = slot_key("XAUUSD", T, 15)
        self.assertTrue(self.rt.store.claim_slot(self.key, "XAUUSD", T, T, run_id="reverse"))
        self.seed()

    def seed(self, side="LONG", entry=2650., stop=2600., target=2900., realized=0.):
        held = account(equity=10000 + realized, realized=realized)
        held.open_positions["XAUUSD"] = replace(position("XAUUSD", side, entry, stop, target, 1.), cost_rate=0.)
        broker = PaperBroker(held, INS["XAUUSD"])
        broker.orders["ord-XAUUSD"] = PaperOrder("1.0", "ord-XAUUSD", "run-XAUUSD", "XAUUSD", side, 1., entry, stop,
                                                target, 1., 10000., 0., T - timedelta(hours=1), status="FILLED")
        self.rt.store.save_paper(broker)

    def execute(self, report=None, snap=None, allowed=True):
        return opposite_policy.execute(self.rt, report or reports(), snap if snap is not None else snapshot(),
                                       key=self.key, at=T, ai_allowed=allowed)

    def paper(self):
        return self.rt.store.load_paper("paper-main")

    def decisions(self):
        return [json.loads(r[0]) for r in self.rt.store.db.execute(
            "SELECT payload FROM journal WHERE event_type=?", (opposite_policy.DECISION,))]

    def test_in_profit_long_reverses_to_short_through_risk_v2(self):
        outcome = self.execute()  # LONG 2650, last close 2660, SHORT plan
        self.assertEqual((outcome.status, outcome.reason), ("SUBMITTED", "STRUCTURE_REVERSE_SUBMITTED"))
        held, orders, _ = self.paper()
        self.assertEqual(held.open_positions, {})
        self.assertEqual((held.closed_trades[0].reason, held.closed_trades[0].gross_pnl),
                         ("EXIT_REVERSAL_IN_PROFIT", 10.))
        order = orders[outcome.order_id]
        self.assertEqual((order.side, order.status), ("SHORT", "PENDING"))
        self.assertIsNotNone(order.risk_policy_version)  # reserved by Risk V2
        self.assertEqual({d["policy"] for d in self.decisions()}, {"DEC_REVERSAL_1"})

    def test_no_bos_beyond_stop_needed(self):
        self.assertEqual(self.execute(reports(bos=False)).status, "SUBMITTED")

    def test_in_profit_short_reverses_to_long(self):
        self.seed(side="SHORT", entry=2700., stop=2750., target=2500.)
        snap = snapshot(2660.)
        outcome = self.execute(reports(side="LONG", entry=2660., stop=2620., snap=snap), snap)
        self.assertEqual(outcome.status, "SUBMITTED")
        self.assertEqual(self.paper()[1][outcome.order_id].side, "LONG")
        self.assertEqual(self.paper()[0].closed_trades[0].gross_pnl, 40.)

    def test_in_loss_or_flat_is_kept(self):
        for price in (2640., 2650.):  # loss, zero
            with self.subTest(price=price):
                self.rt.store.db.execute("DELETE FROM journal WHERE run_id='reverse'")
                outcome = self.execute(reports(entry=price), snapshot(price))
                self.assertEqual((outcome.status, outcome.reason), ("SKIPPED", "OPPOSITE_IN_LOSS_KEPT"))
                held, orders, _ = self.paper()
                self.assertIn("XAUUSD", held.open_positions)
                self.assertEqual((held.closed_trades, len(orders)), ([], 1))

    def test_same_direction_no_action(self):
        self.assertEqual(self.execute(reports(side="LONG", entry=2662.51, stop=2600.)).reason, "SAME_DIRECTION")
        self.assertEqual(self.paper()[0].closed_trades, [])

    def test_gates_still_apply(self):
        before = self.rt.store.paper_state(*self.paper())
        self.assertEqual(self.execute(allowed=False).reason, "AI_CAUTION_OR_UNHEALTHY")  # AI can only block
        self.assertEqual(self.rt.store.paper_state(*self.paper()), before)

    def test_risk_rejection_after_the_close_stays_flat(self):
        self.seed(realized=-700.)  # Risk V2 drawdown gate (5 %) refuses the new direction
        outcome = self.execute()
        self.assertEqual(outcome.reason, "RISK_REJECTED_AFTER_CLOSE")
        held, orders, _ = self.paper()
        self.assertEqual((held.open_positions, len(orders)), ({}, 1))

    def test_config_values_default_and_fingerprint(self):
        self.assertEqual(OPPOSITE_SETUP_POLICIES, ("BLOCK", "STRUCTURE_CHANGE", "REVERSE_IN_PROFIT"))
        default = RuntimeConfig(market_provider_mode="none")
        self.assertEqual(default.v2_opposite_setup_policy, "BLOCK")  # default unchanged
        with patch.dict(os.environ, {"V2_OPPOSITE_SETUP_POLICY": "REVERSE_IN_PROFIT"}):
            self.assertEqual(RuntimeConfig.from_env().v2_opposite_setup_policy, "REVERSE_IN_PROFIT")
        # Documented in DEC-REVERSAL-1: setting it changes the fingerprint (a resumed experiment would not start).
        self.assertNotEqual(replace(default, v2_opposite_setup_policy="REVERSE_IN_PROFIT").fingerprint(),
                            default.fingerprint())
        source = (ROOT / "runtime" / "service.py").read_text(encoding="utf-8")
        self.assertIn('v2_opposite_setup_policy in ("STRUCTURE_CHANGE", "REVERSE_IN_PROFIT")', source)


SHORT = {"side": "SHORT", "entry": 1.095, "stop": 1.1, "target": 1.085}
NEW_CID = "aif-run-2"
NEW_FILL = (201, {"orderCreateTransaction": {"id": "200"},
                  "orderFillTransaction": {"id": "201", "tradeOpened": {"tradeID": "6400", "units": "-100"}}})


class RouterReversalTests(RouterBase):
    def opened(self, **env):
        router = self.router(**env)
        self.ready()
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW)[0], "ACCEPTED")  # LONG 1.09000
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade())]
        del self.server.routes[("GET", PRICING)]
        return router

    def puts(self):
        return [p for m, p in self.writes() if m == "PUT"]

    def test_in_profit_closes_and_routes_the_opposite_direction(self):
        router = self.opened()
        self.server.add("GET", PRICING, pricing(bid="1.09500", ask="1.09510"))  # LONG fill 1.09000: in profit
        self.server.routes[("GET", f"{A}/summary")] = [summary("10050")]
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": close_fill()}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades(), trades(),
                                                    trades(open_trade(trade_id="6400", ext=NEW_CID, units="-100"))]
        self.server.add("GET", f"{A}/trades/@{NEW_CID}", (404, {"errorCode": "TRADE_DOES_NOT_EXIST"}))
        self.server.routes[("POST", ORDERS)] = [NEW_FILL]
        self.assertEqual(router.on_cycle(context(run_id="run-2", **SHORT), "PLAN_READY", NOW + timedelta(hours=1)),
                         ("ACCEPTED", "6400"))
        rows = {r["client_id"]: r for r in self.rows()}
        self.assertEqual((rows[CID]["status"], rows[CID]["closed_reason"]), ("CLOSED", bdr.REVERSAL))
        self.assertEqual((rows[NEW_CID]["status"], rows[NEW_CID]["side"]), ("OPEN", "SHORT"))
        self.assertEqual(self.puts(), [f"{A}/trades/6397/close"])
        self.assertEqual(self.events(bdr.REVERSAL)[0][2], {"closed_client_id": CID, "outcome": "CLOSED"})

    def test_in_loss_or_flat_keeps_the_trade(self):
        router = self.opened()
        for n, bid in enumerate(("1.08900", "1.09000")):
            with self.subTest(bid=bid):
                self.server.routes[("GET", PRICING)] = [pricing(bid=bid, ask="1.09010")]
                self.assertEqual(router.on_cycle(context(run_id=f"loss-{n}", **SHORT), "PLAN_READY", NOW),
                                 ("NOT_ROUTED", "position_already_open"))
        self.assertEqual(self.puts(), [])
        self.assertEqual([r["status"] for r in self.rows()], ["OPEN"])
        self.assertEqual(len(self.events("REVERSAL_SKIPPED")), 2)

    def test_same_direction_no_action(self):
        router = self.opened()
        self.server.add("GET", PRICING, pricing(bid="1.09500"))
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "position_already_open"))
        self.assertEqual(self.puts(), [])

    def test_gates_apply_before_any_close(self):
        router = self.opened()
        self.server.add("GET", PRICING, pricing(bid="1.09500"))  # in profit
        self.registry().emergency_stop(actor="operator", reason="test")  # broker_demo no longer allowed
        self.assertEqual(router.on_cycle(context(run_id="emg", **SHORT), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "broker_demo_not_allowed"))
        self.assertEqual(self.puts(), [])  # refused before the reversal: the profitable trade is kept
        self.assertEqual(self.events(bdr.REVERSAL), [])
        self.assertEqual([r["status"] for r in self.rows()], ["OPEN"])

    def test_reversal_off_by_env(self):
        router = self.opened(AI_FLOOR_DEMO_REVERSAL="0")
        self.assertFalse(router.reversal)
        self.server.add("GET", PRICING, pricing(bid="1.09500"))
        self.assertEqual(router.on_cycle(context(run_id="run-2", **SHORT), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "position_already_open"))
        self.assertEqual(self.puts(), [])
        self.assertTrue(bdr.reversal_flag({}))
        self.assertTrue(bdr.reversal_flag({"AI_FLOOR_DEMO_REVERSAL": "1"}))

    def test_real_execution_stays_blocked(self):
        self.assertFalse(REAL_EXECUTION_ENABLED)
        with patch("execution.broker_demo_router.real_execution_enabled", return_value=True):
            self.assertIsNone(self.router())  # the router (and so any reversal) cannot exist with REAL on


if __name__ == "__main__":
    unittest.main()
