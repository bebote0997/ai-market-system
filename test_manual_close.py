"""Floor «Cerrar operación» (manual close of the routed OANDA demo trade): owner-only, practice-only request stored as a
state-DB flag, closed by the router through its own close path (reason MANUAL_CLOSE), idempotent, Slack alert and
Spanish close reason. Offline: fake transport only (helpers from test_broker_demo_router / test_ops_hardening)."""
from datetime import timedelta
import json
import threading
import unittest
from unittest.mock import MagicMock, patch

from execution import operator_actions as ops
from execution.broker_demo_state import DemoStateStore
from execution.demo_metrics import demo_rows, load_risk_rows
from runtime.broker_alerts import build_message
from test_broker_demo_router import CID, NOW, OPEN_TRADES, open_trade, trades
from test_ops_hardening import CLOSE, CLOSED_OK, HALTED, FakeSt, OpsBase
from ui import auth
from ui import i18n_es as es
from ui.floor_payload import trade_row
from ui.views import floor_actions as floor_view

LATER = NOW + timedelta(minutes=1)


class ManualBase(OpsBase):
    def request(self, request_id="req-1", client_id=CID, env=None, **kwargs):
        return ops.request_manual_close("dashboard", "Floor: Cerrar operación", client_id=client_id,
                                        request_id=request_id, path=self.db, env=self.env if env is None else env,
                                        **kwargs)


# -- request (dashboard process) -----------------------------------------------------------------------------------
class ManualCloseRequestTests(ManualBase):
    def test_request_is_stored_with_audit_event(self):
        self.opened()
        self.assertEqual(self.request(), {"ok": True, "reason": "requested"})
        stored = json.loads(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.assertEqual((stored["client_id"], stored["trade_id"], stored["request_id"]), (CID, "6397", "req-1"))
        events = self.events("MANUAL_CLOSE_REQUESTED")
        self.assertEqual(len(events), 1)
        self.assertEqual((events[0][1], events[0][2]["reason"]), (CID, "MANUAL_CLOSE"))
        self.assertTrue(ops.operator_state(self.db, env=self.env)["manual_close_pending"])

    def test_target_is_the_routed_open_trade(self):
        self.assertIsNone(ops.manual_close_target(self.db, env=self.env))  # no DB yet: nothing, never created
        self.assertFalse(self.db.exists())
        self.opened()
        target = ops.manual_close_target(self.db, env=self.env)
        self.assertEqual((target["client_id"], target["trade_id"], target["symbol"], target["unresolved"]),
                         (CID, "6397", "EURUSD", False))

    def test_refusals(self):
        self.opened()
        cases = {
            "routing_off": {**self.env, "AI_FLOOR_BROKER_DEMO_ROUTING": "0"},
            "not_practice": {**self.env, "OANDA_ACCOUNT_ID": "001-001-12345678-001"},
        }
        for reason, env in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(self.request(env=env), {"ok": False, "reason": reason})
        self.assertEqual(self.request(env={**self.env, "AI_FLOOR_BROKER_DEMO_PROVIDER": "dxtrade"})["reason"],
                         "not_practice")
        with patch("execution.dxtrade_broker.real_execution_enabled", return_value=True):
            self.assertEqual(self.request()["reason"], "real_execution_enabled")
        self.assertEqual(self.request(client_id="aif-other")["reason"], "no_open_trade")
        self.assertEqual(self.request(request_id="")["reason"], "bad_request")
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.assertEqual(self.events("MANUAL_CLOSE_REQUESTED"), [])

    def test_unresolved_action_is_refused(self):
        self.opened()
        self.store().update(CID, pending_action="CLOSE:X", action_status="UNKNOWN")
        self.assertEqual(self.request()["reason"], "action_unresolved")

    def test_second_click_never_queues_a_second_close(self):
        self.opened()
        self.assertEqual(self.request()["reason"], "requested")
        self.assertEqual(self.request(), {"ok": True, "reason": "duplicate"})
        self.assertEqual(self.request(request_id="req-2"), {"ok": False, "reason": "already_pending"})
        self.assertEqual(json.loads(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))["request_id"], "req-1")
        self.assertEqual(len(self.events("MANUAL_CLOSE_REQUESTED")), 1)

    def test_concurrent_requests_store_exactly_one(self):
        self.opened()
        results, barrier = [], threading.Barrier(6)

        def click(n):
            barrier.wait()
            results.append(self.request(request_id=f"req-{n}")["reason"])

        threads = [threading.Thread(target=click, args=(n,)) for n in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(results.count("requested"), 1)
        self.assertEqual(results.count("already_pending"), 5)
        self.assertEqual(len(self.events("MANUAL_CLOSE_REQUESTED")), 1)


# -- router (runner process) ---------------------------------------------------------------------------------------
class ManualCloseRouterTests(ManualBase):
    def test_manage_closes_once_with_manual_reason_and_alerts(self):
        router = self.opened()
        router.on_alert = MagicMock()
        spy = self.spy_close(router)
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]
        self.assertEqual(router.manage(LATER), {CID: "CLOSED"})
        self.assertEqual(spy.call_args.kwargs["client_id"], f"{CID}-manual")
        self.assertEqual(spy.call_args.kwargs["reason"], "MANUAL_CLOSE")
        row = self.rows()[0]
        self.assertEqual((row["status"], row["closed_reason"]), ("CLOSED", "MANUAL_CLOSE"))
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        done = self.events("MANUAL_CLOSE")
        self.assertEqual(len(done), 1)
        self.assertEqual((done[0][2]["request_id"], done[0][2]["outcome"]), ("req-1", "CLOSED"))
        kinds = [c.args[0] for c in router.on_alert.call_args_list]
        self.assertIn("MANUAL_CLOSE", kinds)
        self.assertIn("CLOSED", kinds)
        router.manage(LATER + timedelta(minutes=1))
        router.manage(LATER + timedelta(minutes=2))
        self.assertEqual(self.puts(), [CLOSE])  # never a second close
        self.assertEqual(self.request(request_id="req-2")["reason"], "no_open_trade")

    def test_crash_between_close_and_completion_keeps_the_request_and_attribution(self):
        router = self.opened()
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        with patch.object(DemoStateStore, "consume_flag", side_effect=RuntimeError("crash before commit")):
            router.manage(LATER)
        self.assertEqual((self.rows()[0]["status"], self.rows()[0]["closed_reason"]), ("CLOSED", "MANUAL_CLOSE"))
        self.assertIsNotNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))  # not consumed without its event
        self.assertEqual(self.events("MANUAL_CLOSE"), [])
        restarted = self.router()
        restarted.on_alert = MagicMock()
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        restarted.manage(LATER + timedelta(minutes=1))
        [done] = self.events("MANUAL_CLOSE")
        self.assertEqual((done[1], done[2]["request_id"], done[2]["actor"], done[2]["outcome"]),
                         (CID, "req-1", "dashboard", "CLOSED"))
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.assertEqual([c.args[0] for c in restarted.on_alert.call_args_list].count("MANUAL_CLOSE"), 1)
        self.assertEqual(self.puts(), [CLOSE])  # never a second close

    def test_completion_and_consumption_are_one_transaction(self):
        router = self.opened()
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        with patch("execution.broker_demo_state._OUTBOX_SQL", "INSERT INTO no_such_table VALUES (?, ?, ?, ?, ?)"):
            router.on_alert = MagicMock()
            router.manage(LATER)  # the outbox insert fails -> the event and the consumption roll back too
        self.assertIsNotNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.assertEqual(self.events("MANUAL_CLOSE"), [])
        router.manage(LATER + timedelta(minutes=1))
        self.assertEqual(len(self.events("MANUAL_CLOSE")), 1)
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))

    def test_crash_after_commit_delivers_the_alert_once_from_the_outbox(self):
        router = self.opened()
        router.on_alert = MagicMock()
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        with patch.object(type(router), "_drain_outbox"):  # crash right after the commit: nothing delivered
            router.manage(LATER)
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.assertEqual([r["status"] for r in self.store().outbox()], ["PENDING"])
        restarted = self.router()
        restarted.on_alert = MagicMock()
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        restarted.manage(LATER + timedelta(minutes=1))
        restarted.manage(LATER + timedelta(minutes=2))
        [call] = [c for c in restarted.on_alert.call_args_list if c.args[0] == "MANUAL_CLOSE"]
        self.assertEqual((call.args[1]["request_id"], call.args[1]["outcome"]), ("req-1", "CLOSED"))
        self.assertEqual([r["status"] for r in self.store().outbox()], ["SENT"])

    def test_stale_claim_is_unknown_never_resent(self):
        router = self.opened()
        router.on_alert = MagicMock()
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        with patch.object(DemoStateStore, "finish_outbox", side_effect=RuntimeError("crash mid-delivery")):
            router.manage(LATER)
        self.assertEqual([r["status"] for r in self.store().outbox()], ["CLAIMED"])
        restarted = self.router()
        restarted.on_alert = MagicMock()
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        restarted.manage(LATER + timedelta(minutes=1))  # within the grace: still CLAIMED, not resent
        self.now += timedelta(minutes=11)
        restarted.manage(self.now)
        self.assertEqual([r["status"] for r in self.store().outbox()], ["UNKNOWN"])
        self.assertNotIn("MANUAL_CLOSE", [c.args[0] for c in restarted.on_alert.call_args_list])

    def test_on_cycle_processes_the_request(self):
        router = self.opened()
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        router.on_cycle({"symbol": "EURUSD"}, "NO_SETUP", LATER)
        self.assertEqual(self.rows()[0]["closed_reason"], "MANUAL_CLOSE")
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))

    def test_broker_rejection_drops_the_request_and_alerts(self):
        router = self.opened()
        router.on_alert = MagicMock()
        self.request()
        self.server.add("PUT", CLOSE, HALTED)
        self.assertEqual(router.manage(LATER), {CID: "CLOSE_REJECTED"})
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.assertEqual(len(self.events("MANUAL_CLOSE_FAILED")), 1)
        self.assertIn("MANUAL_CLOSE_FAILED", [c.args[0] for c in router.on_alert.call_args_list])
        router.manage(LATER + timedelta(minutes=1))
        self.assertEqual(self.puts(), [CLOSE])  # not retried blind: the owner decides again

    def test_unknown_close_is_latched_and_resolved_by_reconcile(self):
        router = self.opened()
        self.request()
        self.server.add("PUT", CLOSE, TimeoutError())
        self.assertEqual(router.manage(LATER), {CID: "UNKNOWN"})
        self.assertIsNotNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.assertEqual(router.manage(LATER + timedelta(minutes=1)), {CID: "ACTION_UNRESOLVED"})
        self.assertEqual(self.puts(), [CLOSE])  # never a second close while UNKNOWN
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]  # the close went through
        router.manage(LATER + timedelta(minutes=2))
        row = self.rows()[0]
        self.assertEqual((row["status"], row["closed_reason"]), ("CLOSED", "MANUAL_CLOSE"))
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.assertEqual(self.events("MANUAL_CLOSE")[-1][2]["outcome"], "CLOSED")
        self.assertEqual(self.puts(), [CLOSE])

    def test_trade_already_closed_at_broker(self):
        router = self.opened()
        self.request()
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]  # SL hit before the tick
        router.manage(LATER)
        self.assertEqual(self.puts(), [])
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.assertEqual(len(self.events("MANUAL_CLOSE_NOT_OPEN")), 1)

    def test_dry_run_never_closes(self):
        self.opened()
        self.request()
        dry = self.router(AI_FLOOR_BROKER_DEMO_DRY_RUN="1")
        dry.manage(LATER)
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.assertEqual(len(self.events("MANUAL_CLOSE_DRY_RUN")), 1)
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))

    def test_malformed_request_is_dropped(self):
        router = self.opened()
        self.store().set_flag(ops.MANUAL_CLOSE_REQUEST_FLAG, "{not json")
        router.manage(LATER)
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.events("MANUAL_CLOSE_REFUSED")[-1][2]["reason"], "malformed_request")


# -- texts / Floor close reasons -------------------------------------------------------------------------------------
class ManualCloseTextTests(ManualBase):
    def test_slack_message_and_spanish_close_reason(self):
        text = build_message("MANUAL_CLOSE", {"trade_id": "6397", "reason": "MANUAL_CLOSE", "client_id": CID})
        self.assertIn("Cierre manual", text)
        self.assertIn("…6397", text)
        self.assertNotIn(CID, text)
        self.assertIn("manual", es.close_reason_text("MANUAL_CLOSE"))

    def test_floor_closed_demo_trade_shows_manual_close(self):
        router = self.opened()
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        router.manage(LATER)
        broker_trade = {**open_trade(), "client_id": CID, "state": "CLOSED", "closeTime": "2026-10-12T14:01:00Z",
                        "realizedPL": "-5.0", "averageClosePrice": "1.08950", "initialUnits": "10000"}
        closed, _open = demo_rows([broker_trade], NOW - timedelta(days=1), load_risk_rows(self.db))
        self.assertEqual(closed[0]["close_reason"], "MANUAL_CLOSE")
        row = trade_row(closed[0], {}, status="CLOSED")
        self.assertEqual(row["close_reason"], es.close_reason_text("MANUAL_CLOSE"))


# -- owner auth + Floor button --------------------------------------------------------------------------------------
PASSWORD_ENV = {"AI_FLOOR_DASHBOARD_PASSWORD": "x" * 16}


class OwnerAuthTests(unittest.TestCase):
    def st(self, **state):
        return type("S", (), {"session_state": dict(state)})()

    def test_owner_needs_a_live_password_session(self):
        live = {auth.AUTHENTICATED: True, auth.EXPIRES_AT: 100.0}
        self.assertTrue(auth.owner_authenticated(self.st(**live), PASSWORD_ENV, clock=lambda: 50.0))
        self.assertFalse(auth.owner_authenticated(self.st(**live), PASSWORD_ENV, clock=lambda: 150.0))  # expired
        self.assertFalse(auth.owner_authenticated(self.st(), PASSWORD_ENV, clock=lambda: 50.0))
        self.assertFalse(auth.owner_authenticated(self.st(**live), {}, clock=lambda: 50.0))  # loopback, no password
        self.assertFalse(auth.owner_authenticated(self.st(**live), {"AI_FLOOR_DASHBOARD_PASSWORD": "short"},
                                                  clock=lambda: 50.0))


TARGET = {"client_id": CID, "trade_id": "6397", "symbol": "EURUSD", "side": "LONG", "units": 10000, "unresolved": False}


class FloorStub(FakeSt):
    def caption(self, body, **_):
        self.markdowns.append(body)

    warning = info = caption


def api(problem=None, target=TARGET, pending=False, result=None):
    return {"problem": problem, "target": target, "state": {"manual_close_pending": pending},
            "close": MagicMock(return_value=result or {"ok": True, "reason": "requested"})}


class FloorButtonTests(unittest.TestCase):
    def setUp(self):
        self.session = {}

    def render(self, api_, owner=True, clicks=()):
        st = FloorStub(self.session, clicks)
        floor_view.render_manual_close(st, api=api_, owner=owner)
        return st

    def button(self, st, key="floor-manual-close"):
        return next(b for b in st.buttons if b["key"] == key)

    def test_disabled_states(self):
        cases = ((False, api(), "Solo el owner"), (True, api(problem="routing_off"), "AI_FLOOR_BROKER_DEMO_ROUTING"),
                 (True, api(problem="not_practice"), "práctica"), (True, api(target=None), "No hay ninguna"),
                 (True, api(target={**TARGET, "unresolved": True}), "sin resolver"),
                 (True, api(pending=True), "ya solicitado"))
        for owner, api_, text in cases:
            with self.subTest(text=text):
                self.session.clear()
                st = self.render(api_, owner=owner, clicks={"floor-manual-close", "floor-manual-close-confirm"})
                self.assertTrue(self.button(st)["disabled"])
                self.assertNotIn("floor-manual-close-confirm", st.button_keys())
                self.assertIn(text, st.content)
                api_["close"].assert_not_called()
        self.assertIn("Solo el owner", self.render(None, owner=False).content)

    def test_two_click_flow_sends_one_request(self):
        api_ = api()
        st = self.render(api_, clicks={"floor-manual-close"})
        self.assertFalse(self.button(st)["disabled"])
        self.assertIn("floor-manual-close-confirm", st.button_keys())
        api_["close"].assert_not_called()  # the first click only asks for confirmation
        request_id = self.session[floor_view.CONFIRM_KEY]["request_id"]
        self.render(api_, clicks={"floor-manual-close"})  # clicking again keeps the same request id
        self.assertEqual(self.session[floor_view.CONFIRM_KEY]["request_id"], request_id)
        st = self.render(api_, clicks={"floor-manual-close-confirm"})
        api_["close"].assert_called_once()
        kwargs = api_["close"].call_args.kwargs
        self.assertEqual((kwargs["client_id"], kwargs["request_id"]), (CID, request_id))
        self.assertIn("Cierre solicitado", st.content)
        self.render(api_, clicks={"floor-manual-close-confirm"})  # a re-render / double click sends nothing more
        api_["close"].assert_called_once()

    def test_cancel(self):
        api_ = api()
        self.render(api_, clicks={"floor-manual-close"})
        self.render(api_, clicks={"floor-manual-close-cancel"})
        self.assertNotIn(floor_view.CONFIRM_KEY, self.session)
        api_["close"].assert_not_called()

    def test_refusal_is_shown(self):
        api_ = api(result={"ok": False, "reason": "already_pending"})
        self.render(api_, clicks={"floor-manual-close"})
        st = self.render(api_, clicks={"floor-manual-close-confirm"})
        self.assertIn("Ya hay un cierre pendiente", st.content)

    def test_real_operator_api_is_wired(self):
        with patch.dict("os.environ", {"AI_FLOOR_BROKER_DEMO_ROUTING": "0"}):
            api_ = floor_view.manual_close_api()
        self.assertEqual(api_["problem"], "routing_off")
        self.assertIs(api_["close"], ops.request_manual_close)


if __name__ == "__main__":
    unittest.main()
