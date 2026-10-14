"""F11-T01..T17: isolated fault injection, real runtime/storage, no external I/O.

V2 conflict/risk paths are exercised offline, without enabling runtime flags.
Observer failures preserve trading policy; restart means a new SQLite connection.
"""
import errno
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from ai.provider import DeterministicAIProvider, FakeAIProvider
from data.macro_news import InMemoryMacroNewsProvider
from data.twelve_data_provider import TwelveDataProviderError
from execution.conflict_path import EVENT, submit_with_conflict_control
from execution.paper_broker import PaperBroker
from runtime.alert_catalog import make_test_alert
from runtime.alert_channels import GmailEmailChannel
from runtime.alert_dispatch import AlertDeliveryLedger, AlertDispatcher, AlertNotificationSink
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.scheduler import Scheduler, slot_key
from storage.database import Store
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from test_phase5_risk_engine import ACCOUNT, INS, XAU_LONG, account, pending, position
from test_phase6_conflict_engine import candidate
from test_phase7_ai_call_audit import echo_transport
from test_phase7_ai_resilience import openai, quota_error
from ui.fixtures.demo_floor import demo_market
from ui.state import current_market


class ChaosHarness(unittest.TestCase):
    """Mutable faults and clock; cleanup closes connections before removing the DB."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="phase11-chaos-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "paper.db"
        self.now = T
        self.config = RuntimeConfig(db_path=self.path, enabled_symbols=("XAUUSD",),
                                    market_provider_mode="none", ai_provider_mode="deterministic")
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.dict("os.environ", {"AI_FLOOR_ALERTS_ENABLED": "0"}))

    def runner(self, **kwargs):
        defaults = dict(market_provider=Data(), ai_provider=DeterministicAIProvider(),
                        macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                        clock=lambda: self.now)
        defaults.update(kwargs)
        runner = DemoRunner(self.config, **defaults)
        self.addCleanup(runner.store.close)
        return runner

    def cycle(self, runner, minutes=0, side="LONG"):
        self.now = T + timedelta(minutes=minutes)
        with patched_scouts(side):
            return runner.run_once("XAUUSD", self.now)

    def state(self, runner):
        return runner.store.paper_state(*runner.store.load_paper(ACCOUNT))

    def count(self, runner):
        return len(runner.store.load_paper(ACCOUNT)[1])

    def event(self, runner, name, run_id=None):
        rows = runner.store.journal(run_id=run_id, event=name)
        self.assertTrue(rows, name)
        return rows

    def recover_order(self, runner, minutes=15):
        result = self.cycle(runner, minutes)
        self.assertEqual(result["status"], "PLAN_READY")
        review = runner.store.review_report(result["run_id"])
        self.assertEqual(review["execution"]["execution_reason"], "ORDER_SUBMITTED")
        self.assertEqual(self.count(runner), 1)
        before = self.state(runner)
        self.assertEqual(self.cycle(runner, minutes)["status"], "DUPLICATE")
        self.assertEqual(self.state(runner), before)

    def ai_failure(self, provider, outcome, repair):
        runner = self.runner(ai_provider=provider)
        before = self.state(runner)
        result = self.cycle(runner)
        self.assertEqual(result["status"], "PLAN_READY")
        review = runner.store.review_report(result["run_id"])
        self.assertEqual(review["execution"]["execution_reason"], "POLICY_REVIEW_UNAVAILABLE")
        self.assertEqual(self.state(runner), before)
        self.assertEqual(self.count(runner), 0)
        self.event(runner, "PROVIDER_FAILURE", result["run_id"])
        agents = runner.store.review_report(result["run_id"])["agents"]
        self.assertIn(outcome, {a["outcome"] for a in agents})
        repair()
        self.recover_order(runner)

    def opened(self):
        runner = self.runner()
        self.recover_order(runner, 0)
        self.cycle(runner, 15)
        self.assertEqual(len(runner.store.load_paper(ACCOUNT)[2]), 1)
        return runner

    def exposure_block(self, runner, side="LONG"):
        held, orders, fills = runner.store.load_paper(ACCOUNT)
        geometry = {s: (p.position_id, p.side, p.quantity, p.stop, p.target)
                    for s, p in held.open_positions.items()}
        result = self.cycle(runner, 30, side)
        review = runner.store.review_report(result["run_id"])
        self.assertEqual(review["execution"]["execution_reason"], "EXISTING_POSITION")
        self.event(runner, "EXECUTION_DECISION", result["run_id"])
        after, new_orders, new_fills = runner.store.load_paper(ACCOUNT)
        self.assertEqual({s: (p.position_id, p.side, p.quantity, p.stop, p.target)
                          for s, p in after.open_positions.items()}, geometry)
        self.assertEqual(new_orders, orders)
        self.assertEqual(new_fills, fills)
        self.assertEqual((after.cash, after.realized_pnl), (held.cash, held.realized_pnl))
        before = self.state(runner)
        self.assertEqual(self.cycle(runner, 30, side)["status"], "DUPLICATE")
        self.assertEqual(self.state(runner), before)
        # Removing the setup conflict does not authorize a reversal; normal bar management still closes at TP.
        runner.runtime.market_provider = Data(close=131, age=5)
        self.now = T + timedelta(minutes=45)
        runner.run_once("XAUUSD", self.now)
        closed, final_orders, final_fills = runner.store.load_paper(ACCOUNT)
        self.assertNotIn("XAUUSD", closed.open_positions)
        self.assertEqual(len(closed.closed_trades), 1)
        self.assertEqual(len(final_orders), len(orders))
        self.assertEqual(final_fills, fills)
        self.event(runner, "POSITION_CLOSED")
        before = self.state(runner)
        self.assertEqual(runner.run_once("XAUUSD", self.now)["status"], "DUPLICATE")
        self.assertEqual(self.state(runner), before)

    def submit(self, store, report):
        return submit_with_conflict_control(store, report, INS[report.symbol], account_id=ACCOUNT, as_of=T)


class Phase11ChaosTests(ChaosHarness):
    def test_chaos_F11_T01_openai_quota_exhausted(self):
        fault = {"active": True}

        def fail(n):
            if fault["active"]:
                quota_error(n)

        provider = openai(echo_transport(fail=fail))
        self.ai_failure(provider, "QUOTA_EXHAUSTED", lambda: fault.update(active=False))

    def test_chaos_F11_T02_openai_unavailable(self):
        fault = {"active": True}

        def fail(n):
            if fault["active"]:
                raise URLError("offline")

        provider = openai(echo_transport(fail=fail))
        self.ai_failure(provider, "CONNECTION_ERROR", lambda: fault.update(active=False))

    def test_chaos_F11_T03_invalid_provider_response(self):
        # Real contract validator rejects a provider returning prose instead of AIResponse.
        provider = FakeAIProvider(response="not a structured response")
        self.ai_failure(provider, "INVALID_SCHEMA",
                        lambda: setattr(provider, "_respond_fn", DeterministicAIProvider().generate))

    def test_chaos_F11_T04_market_data_unavailable(self):
        data = Data(failure=TwelveDataProviderError("PROVIDER_FAILURE", "METAL", http_status=503))
        runner = self.runner(market_provider=data)
        before = self.state(runner)
        result = self.cycle(runner)
        self.assertEqual(result["status"], "NO_DATA")
        self.event(runner, "PROVIDER_FAILURE", result["run_id"])
        self.assertEqual(self.state(runner), before)
        data.failure = None
        self.recover_order(runner)

    def test_chaos_F11_T05_market_data_stale(self):
        data = Data(age=180)
        runner = self.runner(market_provider=data)
        before = self.state(runner)
        result = self.cycle(runner)
        self.assertEqual(result["status"], "STALE_DATA")
        self.event(runner, "DATA_STALE", result["run_id"])
        self.assertEqual(self.state(runner), before)
        data.age = 0
        self.recover_order(runner)

    def test_chaos_F11_T06_macro_unavailable(self):
        macro = InMemoryMacroNewsProvider(error=OSError("macro offline"))
        runner = self.runner(macro_provider=macro)
        before = self.state(runner)
        result = self.cycle(runner)
        self.assertEqual(result["status"], "NO_SETUP")
        self.event(runner, "PROVIDER_FAILURE", result["run_id"])
        self.assertEqual(self.state(runner), before)
        runner.runtime.macro_provider = macro_fixture()
        self.recover_order(runner)

    def test_chaos_F11_T07_email_unavailable(self):
        runner = self.runner()
        before = self.state(runner)
        ledger = AlertDeliveryLedger(Path(self.path.parent) / "alerts.db")
        self.addCleanup(ledger.close)
        channel = GmailEmailChannel(password="synthetic", sender="a@example.invalid", recipient="b@example.invalid", smtp_factory=lambda *a, **k: (_ for _ in ()).throw(
            OSError("SMTP offline")))
        dispatcher = AlertDispatcher([channel], enabled=True, ledger=ledger, sleep=lambda _: None,
                                     clock=lambda: T.isoformat(), max_attempts=1)
        event = make_test_alert(T.isoformat(), nonce="failure")
        self.assertEqual(dispatcher.dispatch(event), {"email": "FAILED"})
        self.assertEqual(ledger.status(event.event_id)["email"]["error_type"], "CONNECTION_ERROR")
        self.assertEqual(self.state(runner), before)
        self.assertEqual(self.count(runner), 0)
        runner.sink = AlertNotificationSink(dispatcher)
        self.recover_order(runner, 0)
        self.assertTrue(ledger.failures())
        after_order = self.state(runner)
        self.assertEqual(self.count(runner), 1)  # the one policy-authorized order, despite SMTP failure
        smtp = SimpleNamespace(login=lambda *a: None, send_message=lambda *a: None, quit=lambda: None)
        channel.smtp_factory = lambda *a, **k: smtp
        recovered = make_test_alert(T.isoformat(), nonce="recovered")
        self.assertEqual(dispatcher.dispatch(recovered), {"email": "DELIVERED"})
        self.assertEqual(self.state(runner), after_order)
        self.assertEqual(self.count(runner), 1)

    def test_chaos_F11_T08_sqlite_error(self):
        runner = self.runner()
        before = self.state(runner)
        # SQLite itself rejects the authoritative report write inside its transaction.
        runner.store.db.execute("CREATE TRIGGER chaos_reports BEFORE INSERT ON review_reports "
                                "BEGIN SELECT RAISE(ABORT, 'injected SQLite failure'); END")
        # The failure handler also needs review_reports: no durable failure row is possible until repair.
        with self.assertRaises(sqlite3.IntegrityError):
            self.cycle(runner)
        self.assertEqual(self.state(runner), before)
        self.assertFalse(runner.store.db.in_transaction)
        key = slot_key("XAUUSD", T, 15)
        self.assertEqual(runner.store.run(key)["status"], "RUNNING")
        self.assertTrue(runner.store.owns_slot(key, "XAUUSD"))
        runner.store.db.execute("DROP TRIGGER chaos_reports")
        self.now = T + timedelta(minutes=15)
        self.assertEqual(runner.store.recover(self.now, stale_after_seconds=0), 1)
        self.event(runner, "STATE_INCONSISTENCY")
        self.assertEqual(runner.store.run(key)["status"], "FAILED")
        self.assertEqual(self.cycle(runner)["status"], "DUPLICATE")
        self.recover_order(runner)

    def test_chaos_F11_T09_persistent_disk_issue(self):
        runner = self.runner()
        before = self.state(runner)
        original = runner.store._event
        fired = []

        def disk_full(at, run_id, symbol, source, event_type, *args, **kwargs):
            if event_type == "ORDER_SUBMITTED":
                fired.append(event_type)
                raise OSError(errno.ENOSPC, "injected disk full")
            return original(at, run_id, symbol, source, event_type, *args, **kwargs)

        with patch.object(runner.store, "_event", side_effect=disk_full):
            result = self.cycle(runner)
            repeated = self.cycle(runner, 15)
        self.assertEqual(fired, ["ORDER_SUBMITTED"] * 2)
        self.assertEqual(repeated["status"], "ERROR")
        self.event(runner, "RUN_FAILED", repeated["run_id"])
        self.assertEqual(result["status"], "ERROR")
        self.event(runner, "RUN_FAILED", result["run_id"])
        self.assertEqual(self.state(runner), before)
        self.assertFalse(runner.store.db.in_transaction)
        self.recover_order(runner, 30)

    def test_chaos_F11_T10_dashboard_error(self):
        runner = self.runner()
        self.recover_order(runner, 0)
        before = self.state(runner)
        st = SimpleNamespace(session_state={"sample_mode": False})
        with patch.dict("os.environ", {"AI_FLOOR_DB_PATH": str(self.path)}):
            with patch.object(Store, "load_snapshot", side_effect=sqlite3.OperationalError("UI read failure")):
                with self.assertRaisesRegex(sqlite3.OperationalError, "UI read failure"):
                    current_market(st, "XAUUSD", demo_market)
            self.assertEqual(self.state(runner), before)
            self.assertEqual(len(current_market(st, "XAUUSD", demo_market).orders), 1)
        self.assertEqual(self.state(runner), before)
        self.assertEqual(self.cycle(runner)["status"], "DUPLICATE")
        self.event(runner, "ORDER_SUBMITTED")

    def test_chaos_F11_T11_scheduler_restart(self):
        self.config = replace(self.config, scheduler_enabled=True)
        runner = self.runner()
        with patched_scouts("LONG"):
            self.assertEqual(Scheduler(runner, lambda: self.now).tick(), ["PLAN_READY"])
        before = self.state(runner)
        self.assertEqual(Scheduler(runner, lambda: self.now).tick(), ["DUPLICATE"])
        self.assertEqual(self.state(runner), before)
        self.now = T + timedelta(minutes=45)
        with patched_scouts("LONG"):
            self.assertEqual(Scheduler(runner, lambda: self.now).tick(), ["PLAN_READY"])
        self.event(runner, "SLOTS_MISSED")
        self.assertEqual(self.count(runner), 1)
        self.assertEqual(len(runner.store.load_paper(ACCOUNT)[2]), 1)
        before = self.state(runner)
        self.assertEqual(Scheduler(runner, lambda: self.now).tick(), ["DUPLICATE"])
        self.assertEqual(self.state(runner), before)

    def test_chaos_F11_T12_render_restart(self):
        runner = self.runner()
        before = self.state(runner)
        with patch("runtime.service.run_floor", side_effect=KeyboardInterrupt("process stopped")):
            with self.assertRaises(KeyboardInterrupt):
                self.cycle(runner)
        self.assertEqual(self.state(runner), before)
        runner.close()
        self.now = T + timedelta(minutes=15)
        restarted = self.runner()
        run = restarted.store.run(slot_key("XAUUSD", T, 15))
        self.assertEqual((run["status"], run["error"]), ("FAILED", "interrupted_run"))
        self.event(restarted, "STATE_INCONSISTENCY", run["run_id"])
        self.assertEqual(self.state(restarted), before)
        self.assertEqual(self.cycle(restarted)["status"], "DUPLICATE")
        self.recover_order(restarted)
        restarted.close()
        again = self.runner()
        self.assertEqual(self.cycle(again, 15)["status"], "DUPLICATE")
        self.cycle(again, 30)
        self.assertEqual((self.count(again), len(again.store.load_paper(ACCOUNT)[2])), (1, 1))

    def test_chaos_F11_T13_existing_position(self):
        self.exposure_block(self.opened())

    def test_chaos_F11_T14_multiple_positions(self):
        runner = self.opened()
        held, orders, fills = runner.store.load_paper(ACCOUNT)
        extra = position("EURUSD", "LONG", 1.08, 1.07, 1.11, 1)
        held.open_positions[extra.symbol] = extra
        orders[extra.origin_order_id] = replace(pending("EURUSD", "LONG", 1.08, 1.07, 1.11, 1), status="FILLED")
        broker = PaperBroker(held)
        broker.orders, broker.fills = orders, fills
        runner.store.save_paper(broker)
        self.assertEqual(len(runner.store.load_paper(ACCOUNT)[0].open_positions), 2)
        self.exposure_block(runner)
        self.assertEqual(set(runner.store.load_paper(ACCOUNT)[0].open_positions), {"EURUSD"})

    def test_chaos_F11_T15_two_simultaneous_setups(self):
        runner = self.runner()
        other = Store(self.path)
        self.addCleanup(other.close)
        original = runner.store.save_paper
        winners, winning_state = [], []

        def interleave(broker, **kwargs):
            if not winners:
                # Both candidates evaluated the same empty account; competitor commits before our CAS.
                winners.append(self.submit(other, candidate(XAU_LONG, "winner", anchor=1)))
                winning_state.append(self.state(runner))
            return original(broker, **kwargs)

        with patch.object(runner.store, "save_paper", side_effect=interleave):
            loser = self.submit(runner.store, candidate(XAU_LONG, "loser"))
        self.assertEqual(winners[0].status, "RESERVED")
        self.assertEqual((loser.status, loser.reason, loser.attempts),
                         ("BLOCKED", "SAME_DIRECTION_PENDING_CONFLICT", 2))
        self.assertEqual(self.count(runner), 1)
        self.event(runner, EVENT, "loser")
        self.assertEqual(self.state(runner), winning_state[0])
        before = self.state(runner)
        self.assertEqual(self.submit(other, candidate(XAU_LONG, "winner", anchor=1)).status, "DUPLICATE_RUN")
        self.assertEqual(self.state(runner), before)

    def test_chaos_F11_T16_opposite_setup(self):
        self.exposure_block(self.opened(), "SHORT")

    def test_chaos_F11_T17_risk_limit_reached(self):
        runner = self.runner()
        runner.store.save_paper(PaperBroker(account(equity=9500, realized=-500)))
        before = self.state(runner)
        result = self.submit(runner.store, candidate(XAU_LONG, "drawdown"))
        self.assertEqual((result.status, result.reason), ("RISK_REJECTED", "ACCOUNT_DRAWDOWN_LIMIT"))
        self.event(runner, "RISK_V2_DECISION", "drawdown")
        self.assertEqual(self.state(runner), before)
        self.assertEqual(self.count(runner), 0)
        # Fixture repair of account, never runtime reset of a real drawdown.
        runner.store.save_paper(PaperBroker(account()))
        self.assertEqual(self.submit(runner.store, candidate(XAU_LONG, "healthy")).status, "RESERVED")
        before = self.state(runner)
        self.assertEqual(self.submit(runner.store, candidate(XAU_LONG, "healthy")).status, "DUPLICATE_RUN")
        self.assertEqual(self.state(runner), before)
        self.assertEqual(self.count(runner), 1)
