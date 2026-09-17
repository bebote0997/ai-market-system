import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd

from ai.orchestrator import run as run_ai
from ai.provider import DeterministicAIProvider
from core.contracts import FloorRunReport, SetupAssessment, RiskDecision, TradePlan
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from runtime.config import RuntimeConfig
from runtime.gates import fresh_snapshot, paper_policy
from runtime.health import health
from runtime.scheduler import Scheduler, session_names, slot_at, slot_key
from runtime.service import OperationalRuntime
from storage.codec import parse_utc, public_metadata, utc
from storage.database import Store
from ui.state import current_market
from ui.adapters import from_persisted_snapshot
from ui.fixtures.demo_floor import demo_market

T = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)


class TemporaryDB(unittest.TestCase):
    def setUp(self):
        self.path = Path("data/runtime") / f"test-{uuid4().hex}.db"

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def store(self):
        return Store(self.path)


class TestStorage(TemporaryDB):
    def test_schema_reopen_journal_and_timezone(self):
        s = self.store()
        self.assertEqual(s.db.execute("SELECT version FROM schema_info").fetchone()[0], 1)
        s.event(T, "r1", "XAUUSD", "test", "RUN_STARTED", payload={"safe": True})
        s.close()
        s = self.store()
        self.assertEqual(s.journal(run_id="r1")[0]["event_type"], "RUN_STARTED")
        self.assertEqual(parse_utc(utc(T)), T)
        with self.assertRaises(ValueError):
            utc(datetime(2026, 1, 1))
        with self.assertRaises(ValueError):
            parse_utc("2026-01-01T00:00:00")
        s.close()

    def test_read_only_db_cannot_mutate_journal(self):
        s = self.store()
        s.close()
        reader = Store(self.path, readonly=True)
        self.assertEqual(reader.journal(), [])
        with self.assertRaises(Exception):
            reader.event(T, None, None, "ui", "FORBIDDEN")
        reader.close()

    def test_incompatible_schema_fails_without_destroying_data(self):
        s = self.store()
        s.db.execute("UPDATE schema_info SET version=99")
        s.close()
        with self.assertRaises(RuntimeError):
            self.store()
        self.assertTrue(self.path.exists())

    def test_durable_claim_and_recovery_no_replay(self):
        key = slot_key("XAUUSD", T, 15)
        s = self.store()
        self.assertTrue(s.claim_slot(key, "XAUUSD", T, T))
        self.assertFalse(s.claim_slot(key, "XAUUSD", T, T))
        other_slot = slot_key("XAUUSD", T + timedelta(minutes=15), 15)
        self.assertFalse(s.claim_slot(other_slot, "XAUUSD", T + timedelta(minutes=15), T))
        s.close()
        s = self.store()
        self.assertFalse(s.claim_slot(key, "XAUUSD", T, T))
        self.assertEqual(s.recover(T + timedelta(minutes=5)), 1)
        self.assertTrue(s.claim_slot(other_slot, "XAUUSD", T + timedelta(minutes=15), T + timedelta(minutes=5)))
        self.assertEqual(s.run(key)["status"], "FAILED")
        self.assertFalse(s.claim_slot(key, "XAUUSD", T, T))
        self.assertTrue(any(e["event_type"] == "STATE_INCONSISTENCY" for e in s.journal()))
        s.close()

    def test_paper_account_order_position_survive_restart(self):
        s = self.store()
        account = PaperAccount("1.0", "paper-main", 10000, 10000, 10020, 10, 10)
        position = PaperPosition("1.0", "p1", "o1", "r1", "XAUUSD", "LONG", 2,
                                 100, 101, 90, 133, T, last_price=106, contract_multiplier=1)
        account.open_positions["XAUUSD"] = position
        broker = PaperBroker(account)
        broker.orders["o1"] = PaperOrder("1.0", "o1", "r1", "XAUUSD", "LONG", 2, 100, 90, 133,
                                         contract_multiplier=1, equity_at_submission=10000, as_of=T, status="FILLED")
        s.save_paper(broker)
        s.close()
        s = self.store()
        restored, orders, fills = s.load_paper("paper-main")
        p = restored.open_positions["XAUUSD"]
        self.assertEqual((p.position_id, p.quantity, p.entry_price, p.stop, p.target, p.opened_at),
                         ("p1", 2, 101, 90, 133, T))
        self.assertEqual((restored.equity, restored.realized_pnl, restored.unrealized_pnl), (10020, 10, 10))
        self.assertEqual(len(orders), 1)
        self.assertEqual(fills, {})
        class St:
            session_state = {"sample_mode": False}
        from unittest.mock import patch
        with patch.dict("os.environ", {"AI_FLOOR_DB_PATH": str(self.path)}):
            observed = current_market(St, "XAUUSD", demo_market)
        self.assertEqual(observed.positions[0].position_id, "p1")
        self.assertEqual(observed.account.equity, 10020)
        resumed = PaperBroker(restored)
        resumed.orders = orders
        TradeManager(restored, resumed).process_bar({"symbol": "XAUUSD", "timestamp": T + timedelta(minutes=5),
            "open": 102.0, "high": 134.0, "low": 100.0, "close": 133.0, "is_closed": True})
        s.save_paper(resumed)
        s.close()
        s = self.store()
        completed, orders, _ = s.load_paper("paper-main")
        self.assertEqual(completed.open_positions, {})
        self.assertEqual(len(completed.closed_trades), 1)
        self.assertEqual(orders["o1"].status, "FILLED")
        s.close()

    def test_report_agent_and_risk_tables(self):
        setup = SetupAssessment("1.0", "r1", T, "XAUUSD", "NO_SETUP", None, ("1h", "15m", "5m"))
        deterministic = FloorRunReport("1.0", "r1", T, "XAUUSD", {}, {}, None, setup, None, None, "NO_SETUP")
        ai = run_ai(deterministic, DeterministicAIProvider())
        s = self.store()
        key = slot_key("XAUUSD", T, 15)
        s.claim_slot(key, "XAUUSD", T, T)
        s.save_reports(key, deterministic, ai)
        s.close()
        s = self.store()
        self.assertEqual(s.run(key)["run_id"], "r1")
        self.assertEqual(s.db.execute("SELECT status FROM setups").fetchone()[0], "NO_SETUP")
        self.assertGreater(s.db.execute("SELECT count(*) FROM agent_decisions").fetchone()[0], 0)
        s.close()

    def test_risk_and_plan_fields_are_durable(self):
        setup = SetupAssessment("1.0", "r2", T, "XAUUSD", "VALID_SETUP", "LONG", ("1h", "15m", "5m"), evidence=("e1",), invalidation=90)
        plan = TradePlan("1.0", "XAUUSD", "LONG", "5m", 100, 90, 130, 3, run_id="r2", as_of=T)
        risk = RiskDecision("1.0", "APPROVED", "XAUUSD", "LONG", 1, 10, 100, 90, 130, "ok", equity_at_decision=10000, risk_fraction=0.001, contract_multiplier=1)
        deterministic = FloorRunReport("1.0", "r2", T, "XAUUSD", {}, {}, None, setup, plan, risk, "PLAN_READY")
        ai = run_ai(deterministic, DeterministicAIProvider())
        s = self.store()
        key = slot_key("XAUUSD", T, 15)
        s.claim_slot(key, "XAUUSD", T, T)
        s.save_reports(key, deterministic, ai)
        s.close()
        s = self.store()
        self.assertEqual(s.db.execute("SELECT status,quantity,entry,stop,target FROM risk_decisions").fetchone()[:],
                         ("APPROVED", 1, 100, 90, 130))
        self.assertEqual(json.loads(s.db.execute("SELECT plan FROM setups").fetchone()[0])["entry"], 100)
        s.close()


class TestGates(unittest.TestCase):
    def test_metadata_allowlist_excludes_secrets(self):
        self.assertEqual(public_metadata({"provider": "deterministic", "api_key": "secret", "token": "secret"}),
                         {"provider": "deterministic"})

    def test_final_paper_policy_matrix(self):
        response = SimpleNamespace(status="OK")
        risk = SimpleNamespace(status="APPROVED")
        def ai(state, decision=risk, responses=(response,) * 5):
            return SimpleNamespace(final_status=state, risk_decision=decision, trade_plan=object(),
                ai_structure=responses[0], ai_liquidity=responses[1], ai_macro=responses[2],
                ai_setup_review=responses[3], ai_trade_review=responses[4])
        self.assertTrue(paper_policy(ai("PLAN_READY")))
        self.assertFalse(paper_policy(ai("PLAN_READY", None)))
        self.assertFalse(paper_policy(ai("PLAN_READY", SimpleNamespace(status="REJECTED"))))
        for state in ("AI_CAUTION", "WATCH", "NO_DATA", "RISK_REJECTED", "ERROR", "STALE_DATA", "PROVIDER_FAILURE", "STATE_INCONSISTENCY", "PARTIAL_AI_FAILURE"):
            self.assertFalse(paper_policy(ai(state)), state)
        self.assertFalse(paper_policy(ai("PLAN_READY"), data_state="STALE_DATA"))
        self.assertFalse(paper_policy(ai("PLAN_READY", responses=(response,) * 4 + (None,))))
        self.assertFalse(paper_policy(ai("PLAN_READY", responses=(response,) * 4 + (SimpleNamespace(status="ERROR"),))))

    def test_freshness_rejects_forming_future_wrong_symbol_and_stale(self):
        def frames(last=T, closed=True, symbol="XAUUSD"):
            return {tf: pd.DataFrame({"Open": [100.0], "High": [101.0], "Low": [99.0],
                "Close": [100.5], "is_closed": [closed], "symbol": [symbol]},
                index=pd.DatetimeIndex([last])) for tf in ("1h", "15m", "5m")}
        limits = (("1h", 7200), ("15m", 1800), ("5m", 600))
        self.assertTrue(fresh_snapshot(frames(), "XAUUSD", T, limits)[0])
        self.assertEqual(fresh_snapshot(frames(T - timedelta(hours=3)), "XAUUSD", T, limits)[1], "STALE_DATA")
        self.assertFalse(fresh_snapshot(frames(closed=False), "XAUUSD", T, limits)[0])
        self.assertFalse(fresh_snapshot(frames(T + timedelta(minutes=5)), "XAUUSD", T, limits)[0])
        self.assertFalse(fresh_snapshot(frames(symbol="EURUSD"), "XAUUSD", T, limits)[0])

    def test_sessions_dst_and_slots(self):
        self.assertEqual(slot_at(T + timedelta(minutes=14, seconds=59)), T)
        self.assertEqual(set(session_names(T)), {"LONDON", "NEW_YORK"})
        self.assertEqual(set(session_names(datetime(2026, 7, 15, 12, 30, tzinfo=timezone.utc))), {"LONDON", "NEW_YORK"})
        self.assertEqual(session_names(datetime(2026, 1, 18, 13, 30, tzinfo=timezone.utc)), ())
        with self.assertRaises(ValueError):
            slot_at(datetime(2026, 1, 1))


class TestRuntime(TemporaryDB):
    def config(self, enabled=False):
        return RuntimeConfig(db_path=self.path, scheduler_enabled=enabled, enabled_symbols=("XAUUSD",))

    def test_supported_disabled_symbol_cannot_start_cycle(self):
        runtime = OperationalRuntime(self.config(), clock=lambda: T)
        try:
            with self.assertRaisesRegex(ValueError, "disabled by configuration"):
                runtime.run_cycle("NAS100", T)
            self.assertEqual(runtime.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
        finally:
            runtime.close()

    def test_no_provider_safe_cycle_restart_health_and_ui_read(self):
        runtime = OperationalRuntime(self.config(), clock=lambda: T)
        self.assertEqual(runtime.run_cycle("XAUUSD", T), "NO_DATA")
        self.assertEqual(runtime.run_cycle("XAUUSD", T), "DUPLICATE")
        self.assertEqual(runtime.store.load_snapshot("XAUUSD")["state"], "NO_DATA")
        self.assertEqual(health(runtime.store, T)["paper_broker"], "PAPER")
        runtime.close()
        runtime = OperationalRuntime(self.config(), clock=lambda: T)
        self.assertEqual(runtime.run_cycle("XAUUSD", T), "DUPLICATE")
        self.assertEqual(runtime.store.load_paper("paper-main")[0].equity, 10000)
        runtime.close()
        class St:
            session_state = {"sample_mode": False}
        from unittest.mock import patch
        with patch.dict("os.environ", {"AI_FLOOR_DB_PATH": str(self.path)}):
            vm = current_market(St, "XAUUSD", demo_market)
        self.assertEqual(vm.state, "NO_DATA")
        self.assertFalse(vm.sample)
        self.assertEqual(vm.bars, ())
        with patch.dict("os.environ", {"AI_FLOOR_DB_PATH": str(self.path.with_name("missing.db"))}):
            self.assertEqual(current_market(St, "EURUSD", demo_market).state, "NO_DATA")

    def test_persisted_current_snapshot_becomes_stale_on_read(self):
        payload = {"schema_version": "1.0", "symbol": "XAUUSD", "as_of": utc(T),
                   "state": "WATCH", "freshness": "CURRENT", "risk_status": "NOT CALLED"}
        vm = from_persisted_snapshot(payload, now=T + timedelta(hours=1))
        self.assertEqual(vm.freshness, "STALE_DATA")
        self.assertIn("CRITICAL — STALE DATA", vm.warnings)

    def test_disabled_scheduler_missed_policy_and_duplicate_tick(self):
        runtime = OperationalRuntime(self.config(enabled=False), clock=lambda: T)
        self.assertEqual(Scheduler(runtime, lambda: T).tick(), [])
        self.assertEqual(runtime.store.get_state("scheduler"), "DISABLED")
        runtime.close()
        runtime = OperationalRuntime(self.config(enabled=True), clock=lambda: T)
        scheduler = Scheduler(runtime, lambda: T)
        self.assertEqual(scheduler.tick(), ["NO_DATA"])
        self.assertEqual(scheduler.tick(), ["DUPLICATE"])
        later = T + timedelta(minutes=45)
        Scheduler(runtime, lambda: later).tick()
        self.assertTrue(any(e["event_type"] == "SLOTS_MISSED" for e in runtime.store.journal()))
        runtime.close()

    def test_provider_crash_is_durable_and_no_paper_order(self):
        class Broken:
            def load_snapshot(self, symbol, at):
                raise RuntimeError("down")
        runtime = OperationalRuntime(self.config(), market_provider=Broken(), clock=lambda: T)
        self.assertEqual(runtime.run_cycle("XAUUSD", T), "ERROR")
        key = slot_key("XAUUSD", T, 15)
        self.assertEqual(runtime.store.run(key)["status"], "FAILED")
        self.assertEqual(runtime.store.get_state("market_data_provider"), "ERROR")
        self.assertTrue(any(e["event_type"] == "RUN_FAILED" for e in runtime.store.journal()))
        self.assertEqual(runtime.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
        runtime.close()

    def test_ai_stage_crash_after_data_is_durable_and_no_paper(self):
        class Data:
            def load_snapshot(self, symbol, at):
                return {tf: pd.DataFrame({"Open": [100.0], "High": [101.0], "Low": [99.0],
                    "Close": [100.5], "symbol": [symbol], "is_closed": [True]},
                    index=pd.DatetimeIndex([at])) for tf in ("1h", "15m", "5m")}
        runtime = OperationalRuntime(self.config(), market_provider=Data(), clock=lambda: T)
        from unittest.mock import patch
        with patch("runtime.service.run_ai", side_effect=RuntimeError("ai down")):
            self.assertEqual(runtime.run_cycle("XAUUSD", T), "ERROR")
        self.assertEqual(runtime.store.latest_run()["status"], "FAILED")
        self.assertEqual(runtime.store.get_state("ai_provider"), "ERROR")
        self.assertEqual(runtime.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
        self.assertEqual(runtime.run_cycle("XAUUSD", T), "DUPLICATE")
        runtime.close()
