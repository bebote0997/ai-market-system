"""Operator safety: dashboard emergency button wired to the registry kill switch + router flatten request, verified
drift-clear requests, and the Friday close pre-flatten (OANDA halts EUR_USD at 17:00 NY). Offline: fake transport only
(helpers from test_broker_demo_router); the UI flow runs on a minimal fake Streamlit."""
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

from execution import account_registry
from execution import friday_close as fc
from execution import operator_actions as ops
from execution.account_registry import AccountRegistry, read_control_snapshot
from execution.broker_demo_state import DemoStateStore
from execution.broker_port import BrokerGuardError
from execution.oanda_broker import OandaBroker
from test_broker_demo_router import (CID, NOW, OPEN_TRADES, PRICING, TRADE_AT_CID, RouterBase, context, open_trade,
                                     trades)
from test_broker_oanda import ENV as BROKER_ENV
from test_broker_oanda import NOT_FOUND, approved, close_fill, pricing
from test_broker_oanda import plan as broker_plan
from test_broker_oanda_client import A, ACCOUNT, TOKEN, FakeServer
from ui.views import control

NY = ZoneInfo("America/New_York")
UTC = timezone.utc
CLOSE = f"{A}/trades/6397/close"
CLOSED_OK = (200, {"orderFillTransaction": close_fill()})
HALTED = (400, {"errorCode": "MARKET_HALTED"})
FRIDAY_ON = {"AI_FLOOR_FRIDAY_PREFLATTEN": "1"}
FRI_OPEN = "2026-10-16T10:00:00.000000000Z"  # Friday 06:00 NY


def ny(*args):
    return datetime(*args, tzinfo=NY).astimezone(UTC)


class OpsBase(RouterBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(account_registry._EMERGENCY_LATCHES.clear)

    def opened(self, open_time=None, **extra):
        """A live router with one routed OPEN trade (6397); pricing removed so each test sets its own quote."""
        router = self.router(**extra)
        item = open_trade() if open_time is None else open_trade(open_time=open_time)
        self.ready(after=[item])
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", self.now)[0], "ACCEPTED")
        self.server.routes[("GET", OPEN_TRADES)] = [trades(item)]
        del self.server.routes[("GET", PRICING)]
        return router

    def store(self):
        return DemoStateStore(self.db)

    def puts(self):
        return [p for m, p in self.writes() if m == "PUT"]

    def posts(self):
        return [p for m, p in self.writes() if m == "POST"]

    def spy_close(self, router):
        spy = patch.object(router._broker, "close_market", wraps=router._broker.close_market).start()
        self.addCleanup(patch.stopall)
        return spy

    def stop(self, **kwargs):
        return ops.request_emergency_stop("dashboard", "boton del panel", path=self.db, env=self.env, **kwargs)


# -- 1) emergency button -------------------------------------------------------------------------------------------
class EmergencyRequestTests(OpsBase):
    def test_request_latches_registry_audits_and_requests_flatten(self):
        self.router()  # registry exists with broker_demo ON
        result = ops.request_emergency_stop("dashboard", "x" * 300, path=self.db, env=self.env)
        self.assertEqual(result, {"ok": True, "persisted": True, "flatten_requested": True})
        self.assertTrue(self.registry().emergency_active())
        audit = self.registry().audit_log()
        emergency = [r for r in audit if r[2] == "emergency_stop"]
        self.assertEqual([(r[1], r[4], r[5], r[6], len(r[7])) for r in emergency], [("ALL", "1", "dashboard", "dashboard",
                                                                                    120)])
        self.assertIn(("broker_demo", "0", "dashboard"), [(r[1], r[4], r[5]) for r in audit])  # switched OFF
        request = json.loads(self.store().flag("flatten_request"))
        self.assertEqual(request["actor"], "dashboard")
        self.assertTrue(datetime.fromisoformat(request["ts"]).tzinfo)
        [(_kind, _cid, detail)] = self.events("EMERGENCY_BUTTON")
        self.assertEqual((detail["actor"], detail["source"]), ("dashboard", "dashboard"))

    def test_second_process_sees_the_emergency(self):
        self.router()
        self.assertTrue(self.stop()["ok"])
        account_registry._EMERGENCY_LATCHES.clear()  # another process has no in-memory latch
        other = AccountRegistry(self.db, env=self.env)
        self.assertTrue(other.emergency_active())
        self.assertFalse(other.is_allowed("broker_demo"))
        self.assertTrue(read_control_snapshot(self.db, env=self.env)["emergency_stop"])
        state = ops.operator_state(self.db)
        self.assertEqual((state["emergency"], state["flatten_pending"]), (True, True))
        # A restart with the flatten request still pending builds a closing-only router that drains it...
        router = self.router()
        self.assertTrue(router.closing_only)
        self.server.add("GET", OPEN_TRADES, trades())
        router.manage(NOW)
        self.assertIsNone(self.store().flag("flatten_request"))
        self.assertEqual(self.writes(), [])
        self.assertIsNone(self.router())  # ...and once nothing is left a restart builds no router

    def test_never_raises_and_masks_errors(self):
        junk = Path(self.tmp.name) / "junk.db"
        junk.write_bytes(b"not a sqlite database at all" * 10)
        with self.assertLogs("ai_floor.broker.operator_actions", logging.ERROR) as logs:
            result = ops.request_emergency_stop("dashboard", "r", path=junk, env=self.env)
        self.assertEqual(result, {"ok": False, "persisted": False, "flatten_requested": False,
                                  "error": "emergency_not_persisted"})
        self.assertNotIn(str(self.tmp.name), "\n".join(logs.output) + json.dumps(result))
        experiment = Path(self.tmp.name) / "trading_floor.db"
        self.assertEqual(ops.request_emergency_stop("dashboard", "r", path=experiment, env=self.env)["error"],
                         "registry_unavailable")
        self.assertFalse(experiment.exists())
        self.assertEqual(ops.operator_state(junk), {"readable": False, "emergency": None, "flatten_pending": None,
                                                    "drift_clear_pending": None, "blocked_reason": None})
        missing = Path(self.tmp.name) / "missing.db"
        self.assertFalse(ops.operator_state(missing)["readable"])
        self.assertFalse(missing.exists())  # read-only: never creates the DB
        self.assertEqual(ops.request_drift_clear("dashboard", "r", path=junk), {"ok": False, "reason": "no_drift"})

    def test_button_flags(self):
        self.assertEqual(ops.button_flags({}), {"emergency": True, "drift_clear": False})
        self.assertFalse(ops.button_flags({"AI_FLOOR_EMERGENCY_BUTTON": "0"})["emergency"])
        for value in ("1", "", "false", " 0"):
            self.assertTrue(ops.button_flags({"AI_FLOOR_EMERGENCY_BUTTON": value})["emergency"])
        self.assertTrue(ops.button_flags({"AI_FLOOR_DRIFT_CLEAR_BUTTON": "1"})["drift_clear"])
        for value in ("true", "yes", " 1", "0"):
            self.assertFalse(ops.button_flags({"AI_FLOOR_DRIFT_CLEAR_BUTTON": value})["drift_clear"])


class EmergencyRouterTests(OpsBase):
    def test_flatten_closes_open_rows_only_and_clears_the_request(self):
        router = self.opened()
        spy = self.spy_close(router)
        self.assertTrue(self.stop()["ok"])
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]  # reconcile first, then closed
        self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "CLOSED"})
        self.assertEqual(spy.call_args.kwargs["client_id"], f"{CID}-ebtn")
        row = self.rows()[0]
        self.assertEqual((row["status"], row["closed_reason"]), ("CLOSED", "EMERGENCY_BUTTON"))
        self.assertEqual(self.puts(), [CLOSE])
        self.assertEqual(len(self.posts()), 1)  # only the original entry: the flatten never submits
        self.assertIsNone(self.store().flag("flatten_request"))
        self.assertEqual(len(self.events("FLATTEN_REQUEST_DONE")), 1)
        router.manage(NOW + timedelta(minutes=2))
        self.assertEqual(self.puts(), [CLOSE])  # nothing left to do
        # The kill switch itself is the registry emergency: entries are refused.
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW),
                         ("NOT_ROUTED", "broker_demo_not_allowed"))

    def test_on_cycle_processes_the_request_first(self):
        router = self.opened()
        self.stop()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.assertEqual(router.on_cycle(context(), "NO_SETUP", NOW), ("IGNORED", None))
        self.assertEqual(self.rows()[0]["status"], "CLOSED")
        self.assertIsNone(self.store().flag("flatten_request"))

    def test_retries_every_tick_until_flat(self):
        router = self.opened()
        self.stop()
        self.server.add("PUT", CLOSE, HALTED, CLOSED_OK)
        self.assertEqual(router.manage(NOW + timedelta(minutes=1))[CID], "CLOSE_REJECTED")
        self.assertIsNotNone(self.store().flag("flatten_request"))
        self.assertEqual(len(self.events("FLATTEN_REQUEST_PENDING")), 1)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]
        self.assertEqual(router.manage(NOW + timedelta(minutes=2))[CID], "CLOSED")
        self.assertEqual(self.puts(), [CLOSE, CLOSE])
        self.assertIsNone(self.store().flag("flatten_request"))

    def test_unknown_action_rows_are_skipped(self):
        router = self.opened()
        self.store().update(CID, pending_action="CLOSE:X", action_status="UNKNOWN")
        self.stop()
        self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "ACTION_UNRESOLVED"})
        self.assertEqual(self.puts(), [])
        self.assertIsNotNone(self.store().flag("flatten_request"))  # still pending: not flat

    def test_pending_events_are_capped(self):
        router = self.opened()
        self.store().update(CID, pending_action="CLOSE:X", action_status="UNKNOWN")
        self.stop()
        from execution.broker_demo_router import PENDING_EVENT_CAP
        for minute in range(PENDING_EVENT_CAP + 5):
            router.manage(NOW + timedelta(minutes=minute))
        self.assertEqual(len(self.events("FLATTEN_REQUEST_PENDING")), PENDING_EVENT_CAP)

    def test_dry_run_sends_nothing(self):
        self.opened()
        dry = self.router(AI_FLOOR_BROKER_DEMO_DRY_RUN="1")
        self.stop()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        writes = len(self.writes())
        self.assertEqual(dry.manage(NOW + timedelta(minutes=1)), {CID: "DRY_RUN"})
        self.assertEqual(self.writes()[writes:], [])
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        [(_k, cid, detail)] = [e for e in self.events("DRY_RUN") if e[2].get("action") == "emergency_button_close"]
        self.assertEqual((cid, detail["reason"]), (CID, "EMERGENCY_BUTTON"))
        self.assertIsNotNone(self.store().flag("flatten_request"))

    def test_no_request_no_extra_action(self):
        router = self.opened()
        self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "KEEP"})
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.events("FLATTEN_REQUEST_PENDING") + self.events("FLATTEN_REQUEST_DONE"), [])

    def test_events_and_logs_carry_no_secrets(self):
        router = self.opened()
        self.stop()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        router.manage(NOW + timedelta(minutes=1))
        dump = json.dumps(self.events()) + json.dumps(self.registry().audit_log())
        self.assertNotIn(TOKEN, dump)
        self.assertNotIn(ACCOUNT, dump)


# -- 2) drift clear ------------------------------------------------------------------------------------------------
class DriftClearTests(OpsBase):
    def drifted(self):
        router = self.router()
        self.server.add("GET", OPEN_TRADES, trades(open_trade(trade_id="77", ext="manual")))
        router.reconcile()
        self.assertEqual(router.blocked_reason, "drift")
        return router

    def request(self):
        return ops.request_drift_clear("dashboard", "cerrado a mano", path=self.db, env=self.env)

    def test_request_refused_when_not_blocked_by_drift(self):
        self.router()
        self.assertEqual(self.request(), {"ok": False, "reason": "no_drift"})
        self.assertIsNone(self.store().flag("drift_clear_request"))
        self.assertEqual(self.events("DRIFT_CLEAR_REQUESTED"), [])

    def test_verified_clear_when_broker_and_local_state_match(self):
        router = self.drifted()
        episode = self.store().flag("drift_episode")
        self.assertRegex(episode, r"^[0-9a-f]{32}$")
        self.assertEqual(self.request(), {"ok": True, "reason": "requested"})
        self.assertEqual(json.loads(self.store().flag("drift_clear_request"))["episode"], episode)
        self.assertTrue(ops.operator_state(self.db)["drift_clear_pending"])
        [(_k, _c, detail)] = self.events("DRIFT_CLEAR_REQUESTED")
        self.assertEqual((detail["actor"], detail["source"]), ("dashboard", "dashboard"))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]  # the operator closed the unknown trade
        router.reconcile()
        self.assertIsNone(router.blocked_reason)
        self.assertIsNone(self.store().flag("blocked_reason"))
        self.assertIsNone(self.store().flag("drift_clear_request"))
        [(_k, _c, detail)] = self.events("DRIFT_CLEARED")
        self.assertEqual(detail, {"actor": "dashboard", "reason": "cerrado a mano", "source": "dashboard",
                                  "episode": episode})
        self.assertIsNone(self.store().flag("drift_episode"))  # the episode is closed with the block
        self.assertIsNone(self.router().blocked_reason)  # persisted

    def assert_refused(self, router, reasons):
        router.reconcile()
        self.assertEqual(router.blocked_reason, "drift")
        self.assertIsNone(self.store().flag("drift_clear_request"))  # consumed: a new request is needed
        self.assertEqual(self.events("DRIFT_CLEAR_REFUSED")[-1][2]["reasons"], reasons)
        self.assertEqual(self.events("DRIFT_CLEARED"), [])

    def test_refused_with_unmatched_broker_trade(self):
        router = self.drifted()
        self.request()
        self.assert_refused(router, ["drift_present"])
        router.reconcile()  # no request any more: stays blocked, nothing new
        self.assertEqual(len(self.events("DRIFT_CLEAR_REFUSED")), 1)

    def test_refused_with_unresolved_submission(self):
        router = self.drifted()
        self.store().insert_intent({"client_id": CID, "run_id": "run-1", "symbol": "EURUSD", "side": "LONG",
                                    "units": 1, "entry": "1.09", "stop": "1.08", "target": "1.12",
                                    "status": "UNKNOWN"})
        self.request()
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.server.add("GET", TRADE_AT_CID, NOT_FOUND)
        self.assert_refused(router, ["unresolved_submission"])

    def test_refused_with_unknown_action(self):
        router = self.opened()
        router._set_blocked("drift")  # e.g. a manual trade that was closed meanwhile
        self.store().update(CID, pending_action="CLOSE:X", action_status="UNKNOWN")
        self.request()
        self.assert_refused(router, ["unresolved_action"])

    def test_refused_on_broker_read_failure(self):
        router = self.drifted()
        self.request()
        self.server.routes[("GET", OPEN_TRADES)] = [(500, {})]
        self.assertIsNone(router.reconcile())
        self.assertEqual(router.blocked_reason, "drift")
        self.assertEqual(self.events("DRIFT_CLEAR_REFUSED")[-1][2]["reasons"], ["broker_read_failed"])
        self.assertIsNone(self.store().flag("drift_clear_request"))

    def test_never_clears_without_a_request(self):
        router = self.drifted()
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        router.reconcile()
        router.manage(NOW)
        self.assertEqual(router.blocked_reason, "drift")
        self.assertEqual(self.events("DRIFT_CLEARED"), [])

    def test_stale_or_invalid_request_is_refused(self):
        router = self.router()
        self.store().set_flag("drift_clear_request", json.dumps({"actor": "dashboard", "reason": "x", "ts": "t"}))
        self.server.add("GET", OPEN_TRADES, trades())
        router.reconcile()
        self.assertEqual(self.events("DRIFT_CLEAR_REFUSED")[-1][2]["reasons"], ["not_blocked_by_drift"])
        self.assertIsNone(self.store().flag("drift_clear_request"))
        router._set_blocked("drift")
        for raw in ("not json", json.dumps({"reason": "x"}), json.dumps({"actor": ""})):
            self.store().set_flag("drift_clear_request", raw)
            router.reconcile()
            self.assertEqual(router.blocked_reason, "drift")
            self.assertEqual(self.events("DRIFT_CLEAR_REFUSED")[-1][2]["reasons"], ["request_invalid"])

    def test_python_clear_drift_unchanged(self):
        router = self.drifted()
        router.clear_drift("operator", "manual")
        self.assertIsNone(router.blocked_reason)
        self.assertEqual(self.events("DRIFT_CLEARED")[-1][2], {"actor": "operator", "reason": "manual"})

    def test_request_from_an_old_episode_never_clears_a_new_drift(self):
        router = self.drifted()
        old = self.store().flag("drift_episode")
        self.request()
        router.clear_drift("operator", "manual")  # episode closed without a reconcile; the request is still stored
        router.reconcile()  # the manual trade is still there: a NEW drift episode
        new = self.store().flag("drift_episode")
        self.assertNotEqual(new, old)
        self.assertEqual(router.blocked_reason, "drift")
        self.assertIsNone(self.store().flag("drift_clear_request"))  # ignored AND removed
        self.assertEqual(self.events("DRIFT_CLEAR_REFUSED")[-1][2]["reasons"], ["stale_episode"])
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        # A request without an episode (older format) is stale too.
        self.store().set_flag("drift_clear_request", json.dumps({"actor": "dashboard", "reason": "x", "ts": "t"}))
        router.reconcile()
        self.assertEqual(router.blocked_reason, "drift")
        self.assertEqual(self.events("DRIFT_CLEAR_REFUSED")[-1][2]["reasons"], ["stale_episode"])
        self.assertEqual([d["actor"] for _k, _c, d in self.events("DRIFT_CLEARED")], ["operator"])

    def fail_inserts(self, kind):
        with sqlite3.connect(self.db) as con:
            con.execute(f"CREATE TRIGGER fail_{kind} BEFORE INSERT ON broker_demo_events WHEN NEW.kind = '{kind}' "
                        "BEGIN SELECT RAISE(ABORT, 'injected'); END")

    def drop_trigger(self, kind):
        with sqlite3.connect(self.db) as con:
            con.execute(f"DROP TRIGGER fail_{kind}")

    def test_clear_is_atomic_and_a_failed_one_stays_bound_to_its_episode(self):
        router = self.drifted()
        self.request()
        episode, raw = self.store().flag("drift_episode"), self.store().flag("drift_clear_request")
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.fail_inserts("DRIFT_CLEARED")
        with self.assertLogs("ai_floor.broker.demo_router", logging.WARNING) as logs:
            router.reconcile()
        self.assertIn("event=DRIFT_CLEAR_FAILED", "\n".join(logs.output))
        # Nothing changed: block (memory + DB), request and episode exactly as before; no audit row.
        self.assertEqual((router.blocked_reason, self.store().flag("blocked_reason")), ("drift", "drift"))
        self.assertEqual((self.store().flag("drift_clear_request"), self.store().flag("drift_episode")), (raw, episode))
        self.assertEqual(self.events("DRIFT_CLEARED"), [])
        self.drop_trigger("DRIFT_CLEARED")
        router._set_blocked("drift")  # a later drift episode: the kept request can no longer clear anything
        router.reconcile()
        self.assertEqual(router.blocked_reason, "drift")
        self.assertIsNone(self.store().flag("drift_clear_request"))
        self.assertEqual(self.events("DRIFT_CLEAR_REFUSED")[-1][2]["reasons"], ["stale_episode"])
        self.assertEqual(self.events("DRIFT_CLEARED"), [])

    def test_failed_clear_is_retried_within_the_same_episode(self):
        router = self.drifted()
        self.request()
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.fail_inserts("DRIFT_CLEARED")
        with self.assertLogs("ai_floor.broker.demo_router", logging.WARNING):
            router.reconcile()
        self.drop_trigger("DRIFT_CLEARED")
        router.reconcile()
        self.assertIsNone(router.blocked_reason)
        self.assertEqual(len(self.events("DRIFT_CLEARED")), 1)
        self.assertIsNone(self.store().flag("drift_clear_request"))

    def test_refusal_is_atomic_with_its_audit_event(self):
        router = self.drifted()
        self.request()
        raw = self.store().flag("drift_clear_request")
        self.fail_inserts("DRIFT_CLEAR_REFUSED")
        with self.assertLogs("ai_floor.broker.demo_router", logging.WARNING):
            router.reconcile()  # drift still present -> refusal, whose event insert fails
        self.assertEqual(self.store().flag("drift_clear_request"), raw)  # not consumed without its audit row
        self.assertEqual(router.blocked_reason, "drift")
        self.drop_trigger("DRIFT_CLEAR_REFUSED")
        router.reconcile()
        self.assertIsNone(self.store().flag("drift_clear_request"))
        self.assertEqual(self.events("DRIFT_CLEAR_REFUSED")[-1][2]["reasons"], ["drift_present"])


# -- 1b) emergency flatten: reconcile first, never lose the request -------------------------------------------------
class FlattenAfterReconcileTests(OpsBase):
    def pending(self, status="PENDING_SEND"):
        router = self.router()
        self.store().insert_intent({"client_id": CID, "run_id": "run-1", "symbol": "EURUSD", "side": "LONG",
                                    "units": 10000, "entry": "1.09", "stop": "1.08", "target": "1.12",
                                    "status": status})
        self.assertTrue(self.stop()["ok"])
        return router

    def test_filled_pending_send_is_resolved_flattened_then_cleared(self):
        router = self.pending()
        self.server.add("GET", OPEN_TRADES, trades(open_trade()), trades(open_trade()), trades())
        self.server.add("PUT", CLOSE, HALTED, CLOSED_OK)
        self.assertEqual(router.manage(NOW), {CID: "CLOSE_REJECTED"})
        self.assertEqual(self.rows()[0]["status"], "OPEN")  # reconcile resolved the fill before the flatten ran
        self.assertIsNotNone(self.store().flag("flatten_request"))  # not flat yet: kept
        self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "CLOSED"})
        row = self.rows()[0]
        self.assertEqual((row["status"], row["closed_reason"]), ("CLOSED", "EMERGENCY_BUTTON"))
        self.assertIsNone(self.store().flag("flatten_request"))
        kinds = [k for k, _c, _d in self.events() if k in ("RESOLVED", "CLOSED", "FLATTEN_REQUEST_DONE")]
        self.assertEqual(kinds, ["RESOLVED", "CLOSED", "FLATTEN_REQUEST_DONE"])
        self.assertEqual(self.posts(), [])  # the flatten never submits

    def test_broker_read_failure_keeps_the_request(self):
        router = self.router()
        self.stop()
        self.server.add("GET", OPEN_TRADES, (500, {}))
        router.manage(NOW)
        self.assertIsNotNone(self.store().flag("flatten_request"))  # no OPEN rows, but nothing was verified
        self.assertEqual(self.events("FLATTEN_REQUEST_PENDING")[-1][2]["reason"], "broker_read_failed")
        self.assertEqual(self.events("FLATTEN_REQUEST_DONE"), [])
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        router.manage(NOW + timedelta(minutes=1))
        self.assertIsNone(self.store().flag("flatten_request"))
        self.assertEqual(self.writes(), [])

    def test_unknown_submission_keeps_the_request(self):
        router = self.pending(status="UNKNOWN")
        self.server.add("GET", OPEN_TRADES, trades())
        self.server.add("GET", TRADE_AT_CID, NOT_FOUND)  # absence proves nothing: the row stays UNKNOWN
        router.on_cycle(context(run_id="run-2"), "NO_SETUP", NOW)
        self.assertEqual(self.rows()[0]["status"], "UNKNOWN")
        self.assertIsNotNone(self.store().flag("flatten_request"))
        self.assertEqual(self.events("FLATTEN_REQUEST_PENDING")[-1][2]["open_trades"], 1)
        self.assertEqual(self.writes(), [])


# -- 1c) restart with the emergency active: closing-only router ----------------------------------------------------
class ClosingOnlyRestartTests(OpsBase):
    def test_restart_with_emergency_and_open_row_closes_and_never_submits(self):
        self.opened()
        self.registry().emergency_stop(actor="operator", reason="drill")  # no flatten request: implicit flatten
        account_registry._EMERGENCY_LATCHES.clear()  # a new process
        audit = len(self.registry().audit_log())
        router = self.router()
        self.assertTrue(router.closing_only)
        self.assertTrue(router._broker.close_only)
        self.assertEqual(len(self.registry().audit_log()), audit)  # the switch is never turned ON
        self.assertFalse(self.registry().is_allowed("broker_demo"))
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW + timedelta(minutes=1)),
                         ("NOT_ROUTED", "emergency_active"))
        row = self.rows()[0]
        self.assertEqual((row["status"], row["closed_reason"]), ("CLOSED", "EMERGENCY_ACTIVE"))
        self.assertEqual(self.puts(), [CLOSE])
        self.assertEqual(len(self.posts()), 1)  # only the original entry
        self.assertEqual(self.events("FLATTEN_REQUEST_DONE")[-1][2]["implicit"], True)
        self.assertEqual(router.on_cycle(context(run_id="run-3"), "PLAN_READY", NOW + timedelta(minutes=2)),
                         ("NOT_ROUTED", "emergency_active"))
        self.assertEqual(len(self.posts()), 1)
        self.assertIsNone(self.router())  # flat and no request: a restart builds no router again

    def test_restart_drains_a_button_request(self):
        self.opened()
        self.assertTrue(self.stop()["ok"])
        account_registry._EMERGENCY_LATCHES.clear()
        router = self.router()
        self.assertTrue(router.closing_only)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "CLOSED"})
        self.assertEqual(self.rows()[0]["closed_reason"], "EMERGENCY_BUTTON")
        self.assertIsNone(self.store().flag("flatten_request"))

    def test_env_emergency_with_open_row_is_closing_only(self):
        self.opened()
        router = self.router(AI_FLOOR_EMERGENCY_STOP="1")
        self.assertTrue(router.closing_only)
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW), ("NOT_ROUTED", "emergency_active"))

    def test_emergency_without_rows_or_request_returns_none(self):
        self.router()
        self.registry().emergency_stop(actor="operator", reason="drill")
        self.assertIsNone(self.router())
        self.assertIsNone(self.router(AI_FLOOR_EMERGENCY_STOP="1"))
        fresh = Path(self.tmp.name) / "fresh" / "state.db"
        self.assertIsNone(self.router(AI_FLOOR_EMERGENCY_STOP="1", AI_FLOOR_BROKER_STATE_DB_PATH=str(fresh)))
        self.assertFalse(fresh.exists())  # env emergency: nothing created, exactly as before
        self.assertEqual(self.server.calls, [])


class CloseOnlyBrokerTests(OpsBase):
    def broker(self, env=None, **kwargs):
        return OandaBroker(env={**BROKER_ENV, **(env or {})}, mode="broker_demo", registry=self.registry(),
                           transport=self.server, sleep=self.clock.sleep, clock=self.clock, **kwargs)

    def test_close_only_skips_only_the_switch_and_emergency_checks(self):
        self.registry().initialize()  # switch OFF
        with self.assertRaises(BrokerGuardError):
            self.broker()
        self.assertTrue(self.broker(close_only=True).close_only)
        self.registry().emergency_stop(actor="operator", reason="drill")
        with self.assertRaises(BrokerGuardError):
            self.broker()
        self.assertTrue(self.broker(close_only=True).close_only)
        self.assertTrue(self.broker({"AI_FLOOR_EMERGENCY_STOP": "1"}, close_only=True).close_only)
        for env in ({"OANDA_PRACTICE_TOKEN": ""}, {"OANDA_ACCOUNT_ID": "001-001-12345678-001"},
                    {"OANDA_ACCOUNT_ID": ""}, {"AI_FLOOR_BROKER_DEMO_ENABLED": "0"},
                    {"AI_FLOOR_BROKER_DEMO_PROVIDER": "dxtrade"}):
            with self.subTest(env=env), self.assertRaises(BrokerGuardError):
                self.broker(env, close_only=True)
        with self.assertRaises(BrokerGuardError):
            OandaBroker(env=BROKER_ENV, mode="paper", registry=self.registry(), close_only=True)
        with patch("execution.oanda_broker.real_execution_enabled", return_value=True), \
                self.assertRaises(BrokerGuardError):
            self.broker(close_only=True)
        self.assertEqual(self.server.calls, [])

    def test_close_only_refuses_submit_and_modify_but_closes(self):
        self.registry().emergency_stop(actor="operator", reason="drill")
        broker = self.broker(close_only=True)
        p = broker_plan()
        with self.assertRaises(BrokerGuardError):
            broker.submit_plan(p, approved(p))
        with self.assertRaises(BrokerGuardError):
            broker.modify_stop("6397", Decimal("1.09"), client_id="x-be")
        self.assertEqual(self.server.calls, [])
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.assertEqual(broker.close_market("6397", reason="EMERGENCY", client_id="x").status, "ACCEPTED")
        self.server.add("GET", OPEN_TRADES, trades())
        self.assertEqual(broker.open_trades(), ())


# -- 3) Friday close -----------------------------------------------------------------------------------------------
class FridayWindowTests(unittest.TestCase):
    def test_boundaries_edt(self):
        cases = [(ny(2026, 10, 16, 16, 29), fc.OPEN), (ny(2026, 10, 16, 16, 30), fc.PREFLATTEN),
                 (ny(2026, 10, 16, 16, 59), fc.PREFLATTEN), (ny(2026, 10, 16, 17, 0), fc.CLOSED),
                 (ny(2026, 10, 17, 12, 0), fc.CLOSED), (ny(2026, 10, 18, 16, 59), fc.CLOSED),
                 (ny(2026, 10, 18, 17, 0), fc.OPEN), (ny(2026, 10, 15, 16, 45), fc.OPEN),
                 (ny(2026, 10, 19, 9, 0), fc.OPEN), (ny(2026, 10, 16, 0, 0), fc.OPEN)]
        for at, expected in cases:
            with self.subTest(at=at):
                self.assertEqual(fc.friday_window(at, 30), expected)
        self.assertEqual(fc.friday_window(ny(2026, 10, 16, 16, 0), 60), fc.PREFLATTEN)
        self.assertEqual(fc.friday_window(ny(2026, 10, 16, 16, 54), 5), fc.OPEN)

    def test_dst_weeks_in_utc(self):
        # EST (after 2026-11-01): 17:00 NY = 22:00 UTC.
        self.assertEqual(fc.friday_window(datetime(2026, 11, 6, 21, 29, tzinfo=UTC)), fc.OPEN)
        self.assertEqual(fc.friday_window(datetime(2026, 11, 6, 21, 30, tzinfo=UTC)), fc.PREFLATTEN)
        self.assertEqual(fc.friday_window(datetime(2026, 11, 6, 22, 0, tzinfo=UTC)), fc.CLOSED)
        # EDT: 17:00 NY = 21:00 UTC.
        self.assertEqual(fc.friday_window(datetime(2026, 10, 16, 21, 0, tzinfo=UTC)), fc.CLOSED)
        self.assertEqual(fc.friday_window(datetime(2026, 10, 16, 20, 59, tzinfo=UTC)), fc.PREFLATTEN)
        # Spring forward on Sunday 2026-03-08: Friday close in EST, Sunday reopen at 17:00 EDT = 21:00 UTC.
        self.assertEqual(fc.friday_window(datetime(2026, 3, 6, 22, 0, tzinfo=UTC)), fc.CLOSED)
        self.assertEqual(fc.friday_window(datetime(2026, 3, 8, 20, 59, tzinfo=UTC)), fc.CLOSED)
        self.assertEqual(fc.friday_window(datetime(2026, 3, 8, 21, 0, tzinfo=UTC)), fc.OPEN)
        # Fall back on Sunday 2026-11-01: reopen at 17:00 EST = 22:00 UTC.
        self.assertEqual(fc.friday_window(datetime(2026, 11, 1, 21, 59, tzinfo=UTC)), fc.CLOSED)
        self.assertEqual(fc.friday_window(datetime(2026, 11, 1, 22, 0, tzinfo=UTC)), fc.OPEN)

    def test_naive_datetime_and_minutes_validation(self):
        with self.assertRaises(ValueError):
            fc.friday_window(datetime(2026, 10, 16, 16, 30))
        for minutes in (4, 241, 0, -5, "30", 30.0, True, None):
            with self.subTest(minutes=minutes), self.assertRaises(ValueError):
                fc.friday_window(ny(2026, 10, 16, 16, 30), minutes)
        for minutes in (5, 240):
            fc.friday_window(ny(2026, 10, 16, 16, 30), minutes)

    def test_env_settings(self):
        self.assertIsNone(fc.minutes_from_env({}))
        for value in ("true", "yes", " 1", "0", ""):
            self.assertIsNone(fc.minutes_from_env({"AI_FLOOR_FRIDAY_PREFLATTEN": value}))
        self.assertEqual(fc.minutes_from_env(FRIDAY_ON), 30)
        for raw, expected in (("45", 45), ("5", 5), ("240", 240), ("4", 30), ("241", 30), ("x", 30), ("-10", 30),
                              ("12.5", 30)):
            with self.subTest(raw=raw):
                self.assertEqual(fc.preflatten_minutes({"AI_FLOOR_FRIDAY_PREFLATTEN_MINUTES": raw}), expected)

    def test_alert_due(self):
        self.assertFalse(fc.alert_due(ny(2026, 10, 16, 16, 54)))
        self.assertTrue(fc.alert_due(ny(2026, 10, 16, 16, 55)))
        self.assertTrue(fc.alert_due(ny(2026, 10, 17, 10, 0)))
        self.assertFalse(fc.alert_due(ny(2026, 10, 18, 17, 0)))


class FridayRouterTests(OpsBase):
    def setUp(self):
        super().setUp()
        self.now = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)  # Friday 06:00 NY: window open

    def friday(self, **extra):
        return self.opened(open_time=FRI_OPEN, **extra)

    def test_flag_off_is_identical(self):
        router = self.friday()
        self.assertIsNone(router._friday_minutes)
        with patch("execution.broker_demo_router.fc.friday_window") as window:
            before, events = len(self.server.calls), len(self.events())
            self.assertEqual(router.manage(ny(2026, 10, 16, 16, 40)), {CID: "KEEP"})
            self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", ny(2026, 10, 16, 16, 40)),
                             ("NOT_ROUTED", "position_already_open"))
        window.assert_not_called()
        # manage + on_cycle (an OPEN row -> duration management) each read openTrades once: nothing else.
        self.assertEqual([p for _m, p, _h, _b in self.server.calls[before:]], [OPEN_TRADES, OPEN_TRADES])
        self.assertEqual([k for k, _c, _d in self.events()[events:]], ["NOT_ROUTED"])

    def test_preflatten_closes_ahead_of_the_halt(self):
        router = self.friday(**FRIDAY_ON)
        self.assertEqual(router._friday_minutes, 30)
        spy = self.spy_close(router)
        self.server.add("GET", PRICING, pricing())
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.assertEqual(router.manage(ny(2026, 10, 16, 16, 29)), {CID: "KEEP"})  # not yet
        self.assertEqual(self.puts(), [])
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.assertEqual(router.manage(ny(2026, 10, 16, 16, 31)), {CID: "CLOSED"})
        self.assertEqual(spy.call_args.kwargs["client_id"], f"{CID}-fri")
        self.assertEqual((self.rows()[0]["status"], self.rows()[0]["closed_reason"]), ("CLOSED", "FRIDAY_PREFLATTEN"))
        self.assertEqual(self.puts(), [CLOSE])
        self.assertEqual(len(self.posts()), 1)

    def test_preflatten_retries_and_needs_a_tradeable_quote(self):
        router = self.friday(**FRIDAY_ON)
        self.server.add("GET", PRICING, pricing(tradeable=False))
        self.assertEqual(router.manage(ny(2026, 10, 16, 16, 35)), {CID: "KEEP"})  # duration rule: not due yet
        self.assertEqual(self.events("FRIDAY_PREFLATTEN_SKIPPED")[-1][2], {"reason": "not_tradeable"})
        self.assertEqual(self.puts(), [])
        self.server.routes[("GET", PRICING)] = [pricing()]
        self.server.add("PUT", CLOSE, HALTED, CLOSED_OK)
        self.assertEqual(router.manage(ny(2026, 10, 16, 16, 36)), {CID: "CLOSE_REJECTED"})
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.assertEqual(router.manage(ny(2026, 10, 16, 16, 37)), {CID: "CLOSED"})
        self.assertEqual(self.puts(), [CLOSE, CLOSE])

    def test_entries_refused_in_preflatten_and_closed_windows_including_dry_run(self):
        live = self.router(**FRIDAY_ON)
        dry = self.router(AI_FLOOR_BROKER_DEMO_DRY_RUN="1", **FRIDAY_ON)
        for n, at in enumerate((ny(2026, 10, 16, 16, 30), ny(2026, 10, 16, 17, 0), ny(2026, 10, 17, 9, 0),
                                ny(2026, 10, 18, 16, 59))):
            for name, router in (("live", live), ("dry", dry)):
                with self.subTest(at=at, router=name):
                    self.assertEqual(router.on_cycle(context(run_id=f"{name}-{n}"), "PLAN_READY", at),
                                     ("NOT_ROUTED", "friday_close_window"))
        self.assertEqual(self.server.calls, [])
        self.assertEqual(self.rows(), [])

    def test_entries_still_allowed_monday_to_thursday(self):
        for n, at in enumerate((datetime(2026, 10, 12, 14, tzinfo=UTC), ny(2026, 10, 15, 16, 45),
                                ny(2026, 10, 18, 17, 0))):
            with self.subTest(at=at):
                self.server, self.db = FakeServer(), Path(self.tmp.name) / f"state-{n}.db"
                self.env = {**self.env, "AI_FLOOR_BROKER_STATE_DB_PATH": str(self.db)}
                router = self.router(**FRIDAY_ON)
                self.ready()
                self.assertEqual(router.on_cycle(context(), "PLAN_READY", at)[0], "ACCEPTED")

    def test_alert_from_t_minus_5_with_stable_token_rate_limited(self):
        router = self.friday(**FRIDAY_ON)
        router.on_alert = MagicMock()
        self.server.add("GET", PRICING, pricing())
        self.server.add("PUT", CLOSE, HALTED)
        router.manage(ny(2026, 10, 16, 16, 50))
        self.assertEqual(self.events("FRIDAY_FLATTEN_FAILED"), [])  # before 16:55: retries only
        with self.assertLogs("ai_floor.broker.demo_router", logging.ERROR) as logs:
            router.manage(ny(2026, 10, 16, 16, 55))
            router.manage(ny(2026, 10, 16, 16, 58))  # within 5 minutes: no second alert
            router.manage(ny(2026, 10, 16, 17, 0))   # closed: re-alert every 30 min
            router.manage(ny(2026, 10, 16, 17, 25))
            router.manage(ny(2026, 10, 16, 17, 40))
        alerts = [line for line in logs.output if "event=ALERT_FRIDAY_FLATTEN_FAILED" in line]
        self.assertEqual(len(alerts), 2)
        self.assertEqual(len(self.events("FRIDAY_FLATTEN_FAILED")), 2)
        self.assertEqual(router.on_alert.call_count, 2)
        kind, message = router.on_alert.call_args.args
        self.assertEqual(kind, "FRIDAY_FLATTEN_FAILED")
        text = "\n".join(logs.output) + message + json.dumps(self.events("FRIDAY_FLATTEN_FAILED"))
        for secret in ("6397", CID, TOKEN, ACCOUNT):
            self.assertNotIn(secret, text)
        self.assertEqual(len(self.puts()), 6)  # a close attempt on every tick

    def test_once_per_5_minutes_before_the_halt(self):
        router = self.friday(**FRIDAY_ON)
        self.server.add("GET", PRICING, pricing())
        self.server.add("PUT", CLOSE, HALTED)
        with self.assertLogs("ai_floor.broker.demo_router", logging.ERROR):
            for minute in range(55, 60):
                router.manage(ny(2026, 10, 16, 16, minute))
        self.assertEqual(len(self.events("FRIDAY_FLATTEN_FAILED")), 1)

    def test_duration_rule_stays_the_last_fallback(self):
        router = self.friday(**FRIDAY_ON)
        spy = self.spy_close(router)
        self.server.add("GET", PRICING, pricing(tradeable=False))  # no tradeable quote during the whole pre-flatten
        router.manage(ny(2026, 10, 16, 16, 45))
        self.assertEqual(self.puts(), [])
        self.server.routes[("GET", PRICING)] = [pricing()]
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade(open_time=FRI_OPEN)), trades()]
        # Market reopens (Sunday 17:00 NY): the Friday step is idle; the overdue Friday duration close acts.
        self.assertEqual(router.manage(ny(2026, 10, 18, 17, 5)), {CID: "CLOSED"})
        self.assertEqual(spy.call_args.kwargs["client_id"], f"{CID}-close")
        self.assertEqual(self.rows()[0]["closed_reason"], "TIME_EXIT_FRIDAY_NY_CLOSE")

    def test_closed_window_rejections_are_graceful(self):
        router = self.friday(**FRIDAY_ON)
        router.on_alert = MagicMock(side_effect=RuntimeError("pager down"))  # a broken hook never breaks the tick
        self.server.add("PUT", CLOSE, HALTED)
        self.assertEqual(router.manage(ny(2026, 10, 17, 9, 0)), {CID: "CLOSE_REJECTED"})  # no quote needed
        self.server.routes[("PUT", CLOSE)] = [TimeoutError()]
        self.assertEqual(router.manage(ny(2026, 10, 17, 9, 10)), {CID: "UNKNOWN"})  # latched, never retried blind
        self.assertEqual(router.manage(ny(2026, 10, 17, 9, 20)), {CID: "ACTION_UNRESOLVED"})
        self.assertEqual(len(self.puts()), 2)
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.assertEqual(router.on_alert.call_count, 1)

    def test_dry_run_friday_sends_nothing(self):
        self.friday()
        dry = self.router(AI_FLOOR_BROKER_DEMO_DRY_RUN="1", **FRIDAY_ON)
        self.server.add("GET", PRICING, pricing())
        writes = len(self.writes())
        dry.manage(ny(2026, 10, 16, 16, 40))
        self.assertEqual(self.writes()[writes:], [])
        self.assertTrue([e for e in self.events("DRY_RUN") if e[2].get("action") == "friday_preflatten"])


# -- UI ------------------------------------------------------------------------------------------------------------
class FakeSt:
    """Minimal Streamlit stand-in: scripted clicks/inputs; a disabled button never returns True."""

    def __init__(self, session_state, clicks=(), inputs=None):
        self.session_state, self.clicks, self.inputs = session_state, set(clicks), inputs or {}
        self.markdowns, self.buttons, self.text_inputs = [], [], []

    def markdown(self, body, **_):
        self.markdowns.append(body)

    def columns(self, _spec):
        return nullcontext(), nullcontext()

    def toggle(self, _label, value=False, **_):
        return value

    def button(self, label, disabled=False, key=None, **_):
        self.buttons.append({"label": label, "disabled": disabled, "key": key})
        return key in self.clicks and not disabled

    def text_input(self, _label, key=None, **_):
        self.text_inputs.append(key)
        return self.inputs.get(key, "")

    @property
    def content(self):
        return "\n".join(self.markdowns)

    def button_keys(self):
        return [b["key"] for b in self.buttons]


SNAPSHOT = {**control.default_snapshot(), "readable": True}
ROUTER_OFF = {"state": "off", "blocked_reason": None, "open_trade": None, "last_events": []}


def fake_api(blocked=None, emergency=True, drift_clear=False):
    return {"flags": {"emergency": emergency, "drift_clear": drift_clear},
            "state": {"readable": True, "emergency": False, "flatten_pending": False, "drift_clear_pending": False,
                      "blocked_reason": blocked},
            "stop": MagicMock(return_value={"ok": True, "persisted": True, "flatten_requested": True}),
            "drift_clear": MagicMock(return_value={"ok": True, "reason": "requested"})}


class ControlUiTests(unittest.TestCase):
    def setUp(self):
        self.session = {}
        for target, value in (("router_status", ROUTER_OFF), ("account_view", None)):
            patch.object(control, target, return_value=value).start()
        self.addCleanup(patch.stopall)

    def run_view(self, api, clicks=(), inputs=None, snapshot=SNAPSHOT):
        st = FakeSt(self.session, clicks, inputs)
        with patch.object(control, "operator_api", return_value=api):
            control.render(st, snapshot)
        return st

    def test_emergency_needs_click_then_exact_phrase(self):
        api = fake_api()
        st = self.run_view(api)
        stop = next(b for b in st.buttons if b["key"] == "control-emergency-stop")
        self.assertFalse(stop["disabled"])  # enabled by default
        self.assertEqual(st.text_inputs, [])
        st = self.run_view(api, clicks=["control-emergency-stop"])
        self.assertEqual(st.text_inputs, ["control-emergency-phrase"])
        self.assertIn("control-emergency-confirm", st.button_keys())
        for wrong in ("", "parar todo", "PARAR TODO ", "PARAR"):
            st = self.run_view(api, clicks=["control-emergency-confirm"], inputs={"control-emergency-phrase": wrong})
            self.assertIn("La frase no coincide: no se hizo nada.", st.content)
        api["stop"].assert_not_called()
        st = self.run_view(api, clicks=["control-emergency-confirm"], inputs={"control-emergency-phrase": "PARAR TODO"})
        api["stop"].assert_called_once_with(actor="dashboard", reason="boton del panel")
        self.assertIn("Parada activada", st.content)
        st = self.run_view(api)  # the confirmation step is closed after acting
        self.assertEqual(st.text_inputs, [])
        api["stop"].assert_called_once()

    def test_error_result_is_shown(self):
        api = fake_api()
        api["stop"].return_value = {"ok": False, "persisted": False, "flatten_requested": True,
                                    "error": "emergency_not_persisted"}
        self.run_view(api, clicks=["control-emergency-stop"])
        st = self.run_view(api, clicks=["control-emergency-confirm"], inputs={"control-emergency-phrase": "PARAR TODO"})
        self.assertIn("Error: la parada no se pudo guardar", st.content)

    def test_button_disabled_by_flag_or_missing_module(self):
        for api, note in ((fake_api(emergency=False), "botón desactivado por configuración"),
                          (None, "no está disponible")):
            with self.subTest(note=note):
                self.session.clear()
                st = self.run_view(api, clicks=["control-emergency-stop", "control-emergency-confirm"],
                                   inputs={"control-emergency-phrase": "PARAR TODO"})
                self.assertTrue(next(b for b in st.buttons if b["key"] == "control-emergency-stop")["disabled"])
                self.assertEqual(st.text_inputs, [])
                self.assertIn(note, st.content)
        self.assertEqual(self.session.get("control-emergency-result"), None)

    def test_state_line_and_no_emergency_clear_anywhere(self):
        st = self.run_view(fake_api(), snapshot={**SNAPSHOT, "emergency_stop": True})
        self.assertIn("Estado: Activada: todo detenido", st.content)
        self.assertIn("Quitar la parada no es posible desde el panel", st.content)
        self.assertNotIn("todavía no hace nada", st.content)
        labels = " ".join(b["label"] for b in st.buttons).lower()
        for word in ("quitar", "desactivar", "reanudar", "limpiar parada"):
            self.assertNotIn(word, labels)
        for path in ("ui/views/control.py", "execution/operator_actions.py"):
            self.assertNotIn("clear_emergency", Path(path).read_text(encoding="utf-8"))

    def test_drift_section_hidden_unless_blocked(self):
        for blocked in (None, "unresolved_action"):
            st = self.run_view(fake_api(blocked=blocked, drift_clear=True))
            self.assertNotIn("Bloqueo por drift", st.content)
            self.assertNotIn("control-drift-clear", st.button_keys())

    def test_drift_button_only_with_flag(self):
        st = self.run_view(fake_api(blocked="drift"))
        self.assertIn("Bloqueo por drift", st.content)
        self.assertIn("La limpieza desde el panel está desactivada", st.content)
        self.assertNotIn("control-drift-clear", st.button_keys())
        self.assertEqual(st.text_inputs, [])

    def test_drift_request_needs_the_phrase(self):
        api = fake_api(blocked="drift", drift_clear=True)
        st = self.run_view(api, clicks=["control-drift-clear"], inputs={"control-drift-phrase": "limpiar drift"})
        self.assertIn("La frase no coincide", st.content)
        api["drift_clear"].assert_not_called()
        st = self.run_view(api, clicks=["control-drift-clear"], inputs={"control-drift-phrase": "LIMPIAR DRIFT"})
        api["drift_clear"].assert_called_once_with(actor="dashboard", reason="boton del panel")
        self.assertIn(control.DRIFT_SENT, st.content)


class ControlUiIntegrationTests(OpsBase):
    """The real operator_api against a temp state DB (env path), end to end through the fake Streamlit."""

    def setUp(self):
        super().setUp()
        patch.object(control, "router_status", return_value=ROUTER_OFF).start()
        patch.object(control, "account_view", return_value=None).start()
        self.addCleanup(patch.stopall)
        self.session = {}

    def run_view(self, env=None, **kwargs):
        st = FakeSt(self.session, **kwargs)
        with patch.dict(os.environ, {"AI_FLOOR_BROKER_STATE_DB_PATH": str(self.db), **(env or {})}):
            control.render(st, SNAPSHOT)
        return st

    def test_confirmed_click_latches_the_real_kill_switch(self):
        self.router()
        self.run_view(clicks=["control-emergency-stop"])
        self.run_view(clicks=["control-emergency-confirm"], inputs={"control-emergency-phrase": "PARAR TODO"})
        account_registry._EMERGENCY_LATCHES.clear()
        self.assertTrue(self.registry().emergency_active())
        self.assertIsNotNone(self.store().flag("flatten_request"))
        self.assertEqual(len(self.events("EMERGENCY_BUTTON")), 1)

    def test_env_zero_disables_the_button(self):
        self.router()
        self.run_view(env={"AI_FLOOR_EMERGENCY_BUTTON": "0"}, clicks=["control-emergency-stop"])
        st = self.run_view(env={"AI_FLOOR_EMERGENCY_BUTTON": "0"}, clicks=["control-emergency-confirm"],
                           inputs={"control-emergency-phrase": "PARAR TODO"})
        self.assertTrue(next(b for b in st.buttons if b["key"] == "control-emergency-stop")["disabled"])
        self.assertFalse(self.registry().emergency_active())

    def test_drift_flow_against_the_state_db(self):
        router = self.router()
        self.server.add("GET", OPEN_TRADES, trades(open_trade(trade_id="77", ext="manual")))
        router.reconcile()
        hidden = self.run_view()
        self.assertIn("Bloqueo por drift", hidden.content)
        self.assertNotIn("control-drift-clear", hidden.button_keys())
        st = self.run_view(env={"AI_FLOOR_DRIFT_CLEAR_BUTTON": "1"}, clicks=["control-drift-clear"],
                           inputs={"control-drift-phrase": "LIMPIAR DRIFT"})
        self.assertIn(control.DRIFT_SENT, st.content)
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        router.reconcile()
        self.assertIsNone(router.blocked_reason)
        self.assertNotIn("Bloqueo por drift", self.run_view(env={"AI_FLOOR_DRIFT_CLEAR_BUTTON": "1"}).content)


if __name__ == "__main__":
    unittest.main()
