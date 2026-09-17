"""Deterministic operational tests; no paid API calls."""
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import pandas as pd

from ai.contracts import AIResponse, AI_SCHEMA_VERSION
from ai.provider import DeterministicAIProvider, FakeAIProvider
from core.contracts import AgentMessage, InstrumentSpec, NewsItem
from data.macro_news import InMemoryMacroNewsProvider
from data.twelve_data_provider import TwelveDataProviderError
from runtime.config import RuntimeConfig
from runtime.certify_phase7 import live_result_passes
from runtime.demo_runner import DemoRunner, preflight
from runtime.health import health
from runtime.notifications import FakeNotificationSink
from runtime.scheduler import slot_key
from storage.database import Store, SCHEMA_VERSION


T = datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc)


def instrument(multiplier=1):
    return InstrumentSpec("XAUUSD", "METAL", "XAU/USD", "UTC", .01, .01, multiplier,
                          ("1h", "15m", "5m"))


def frames(at, *, close=100., closed=True, age=0):
    stamp = at - timedelta(minutes=age)
    close = float(close)
    return {tf: pd.DataFrame({"Open": [close], "High": [close + 1],
                              "Low": [close - 1], "Close": [close],
                              "symbol": ["XAUUSD"], "is_closed": [closed]},
                             index=pd.DatetimeIndex([stamp])) for tf in ("1h", "15m", "5m")}


class Data:
    def __init__(self, *, age=0, closed=True, failure=None, close=100.):
        self.age, self.closed, self.failure, self.close = age, closed, failure, close

    def load_snapshot(self, symbol, at):
        if self.failure:
            raise self.failure
        return frames(at, close=self.close, closed=self.closed, age=self.age)


def scout(side):
    def make(frame, symbol, tf, run_id, at):
        bullish = side == "LONG"
        payload = ({"bias": "bullish" if bullish else "bearish",
                    "structure_state": "bullish" if bullish else "bearish",
                    "bos": {"type": "BOS"}, "levels": {"support": 90, "resistance": 110}}
                   if make.kind == "structure" else
                   {"sweeps": [{"type": "low" if bullish else "high"}],
                    "liquidity_above": [], "liquidity_below": []})
        return AgentMessage("1.0", run_id, at, symbol, tf, make.kind, "OK", evidence=(payload,))
    return make


def patched_scouts(side):
    from contextlib import ExitStack
    stack = ExitStack()
    structure = scout(side)
    structure.kind = "structure"
    liquidity = scout(side)
    liquidity.kind = "liquidity"
    stack.enter_context(patch("floor.orchestrator.analizar_estructura", structure))
    stack.enter_context(patch("floor.orchestrator.analizar_liquidez", liquidity))
    return stack


def macro_fixture():
    news = NewsItem("1.0", "fixture", "test", T - timedelta(hours=2),
                    T - timedelta(hours=2), "Synthetic fixture", "currency",
                    symbols=("XAUUSD",), known_at=T - timedelta(hours=2))
    return InMemoryMacroNewsProvider(news=(news,))


class TestDemoRunner(unittest.TestCase):
    def setUp(self):
        self.path = Path("data/runtime") / f"phase7-test-{uuid4().hex}.db"
        self.config = RuntimeConfig(db_path=self.path, enabled_symbols=("XAUUSD", "EURUSD"),
                                    market_provider_mode="twelve_data", ai_provider_mode="openai")

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def runner(self, *, data=None, ai=None, dry_run=False, sink=None, multiplier=1):
        return DemoRunner(self.config, market_provider=data or Data(),
                          ai_provider=ai or DeterministicAIProvider(),
                          macro_provider=macro_fixture(),
                          instruments={"XAUUSD": instrument(multiplier)}, clock=lambda: T,
                          dry_run=dry_run, notification_sink=sink)

    def test_preflight_ready_and_missing_credentials(self):
        good = {"OPENAI_API_KEY": "present", "TWELVE_DATA_API_KEY": "present",
                "OPENAI_MODEL": "gpt-5.6-terra"}
        report = preflight(self.config, env=good)
        self.assertEqual(report.status, "READY")
        self.assertTrue(report.checks["db_writable_schema"])
        self.assertEqual(preflight(self.config, env={}).status, "NOT_READY")
        self.assertEqual(preflight(RuntimeConfig(db_path=self.path,
            enabled_symbols=("XAUUSD",), ai_provider_mode="openai"), env=good).status, "NOT_READY")

    def test_live_gate_accepts_watch_with_real_ai_and_rejects_stale(self):
        result = {"durable_status": "COMPLETED", "review_durable": True,
                  "journal_count": 9, "wall_fresh": True, "status": "WATCH",
                  "paper_order_count": 0, "openai_health": "READY",
                  "openai_usage_total_tokens": 300,
                  "ai_statuses": {"structure_ai": "OK", "liquidity_ai": "OK",
                                  "macro_ai": "NO_DATA", "setup_reviewer_ai": "OK"}}
        self.assertTrue(live_result_passes(result))
        self.assertFalse(live_result_passes({**result, "wall_fresh": False}))
        self.assertFalse(live_result_passes({**result, "openai_usage_total_tokens": 0}))
        self.assertFalse(live_result_passes({**result, "ai_statuses": {**result["ai_statuses"], "structure_ai": "ERROR"}}))

    def test_schema_one_migrates_without_losing_journal_and_readonly_requires_upgrade(self):
        store = Store(self.path)
        store.event(T, "old-run", "XAUUSD", "fixture", "OLD_EVENT")
        store.db.execute("DROP TABLE notification_events")
        store.db.execute("DROP TABLE review_reports")
        store.db.execute("UPDATE schema_info SET version=1")
        store.close()
        with self.assertRaisesRegex(RuntimeError, "upgrade required"):
            Store(self.path, readonly=True)
        store = Store(self.path)
        try:
            self.assertEqual(store.db.execute("SELECT version FROM schema_info").fetchone()[0], SCHEMA_VERSION)
            self.assertEqual(store.journal(run_id="old-run")[0]["event_type"], "OLD_EVENT")
        finally:
            store.close()

    def test_no_setup_watch_and_single_durable_run(self):
        runner = self.runner()
        try:
            first = runner.run_once("XAUUSD", T)
            self.assertIn(first["status"], {"NO_SETUP", "WATCH"})
            self.assertTrue(first["review_durable"])
            self.assertEqual(first["run_id"], runner.store.latest_review("XAUUSD")["run_id"])
            self.assertEqual(runner.run_once("XAUUSD", T)["status"], "DUPLICATE")
            self.assertEqual(runner.store.db.execute("SELECT count(*) FROM runs").fetchone()[0], 1)
            self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
        finally:
            runner.close()

    def test_long_short_risk_and_broker_path(self):
        for side in ("LONG", "SHORT"):
            with self.subTest(side=side):
                runner = self.runner()
                try:
                    with patched_scouts(side):
                        result = runner.run_once("XAUUSD", T)
                    self.assertEqual(result["status"], "PLAN_READY")
                    review = runner.store.review_report(result["run_id"])
                    self.assertEqual(review["setup_status"], "VALID_SETUP")
                    self.assertEqual(review["risk_decision"]["status"], "APPROVED")
                    self.assertLessEqual(review["risk_decision"]["capital_at_risk"], 100)
                    self.assertEqual(review["risk_decision"]["side"], side)
                    self.assertEqual(len(review["paper"]["orders"]), 1)
                    self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 1)
                    self.assertEqual(runner.run_once("XAUUSD", T)["status"], "DUPLICATE")
                finally:
                    runner.close()
            self.tearDown()
            self.setUp()

    def test_risk_rejection_and_ai_caution_cannot_order(self):
        runner = self.runner(multiplier=None)
        try:
            with patched_scouts("LONG"):
                result = runner.run_once("XAUUSD", T)
            self.assertEqual(result["status"], "RISK_REJECTED")
            self.assertEqual(runner.store.latest_review()["risk_decision"]["status"], "REJECTED")
            self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
        finally:
            runner.close()
        self.tearDown(); self.setUp()

        def disagree(request):
            return AIResponse(AI_SCHEMA_VERSION, request.run_id, request.as_of,
                request.symbol, request.agent_name, "OK", bias="BEARISH", confidence=.5,
                recommendation="DISAGREE" if request.role == "setup_reviewer" else None,
                supporting_evidence=tuple(e["evidence_id"] for e in request.deterministic_evidence if e.get("evidence_id")))
        runner = self.runner(ai=FakeAIProvider(respond_fn=disagree))
        try:
            with patched_scouts("LONG"):
                result = runner.run_once("XAUUSD", T)
            self.assertEqual(result["status"], "AI_CAUTION")
            self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
        finally:
            runner.close()

    def test_diagnostic_blocks_candidate_and_ui_projection_is_read_only(self):
        runner = self.runner(dry_run=True)
        try:
            with patched_scouts("LONG"):
                result = runner.run_once("XAUUSD", T)
            self.assertEqual(result["status"], "PLAN_READY")
            self.assertFalse(result["paper_orders_enabled"])
            self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
            self.assertTrue(runner.store.journal(run_id=result["run_id"], event="PAPER_DIAGNOSTIC_BLOCKED"))
            view = health(runner.store, T)
            self.assertEqual(view["real_execution"], "DISABLED")
            self.assertEqual(view["last_run_id"], result["run_id"])
            self.assertEqual(view["open_positions"], 0)
        finally:
            runner.close()

    def test_recovery_of_interrupted_cycle_writes_review(self):
        store = Store(self.path)
        key = slot_key("XAUUSD", T, 15)
        self.assertTrue(store.claim_slot(key, "XAUUSD", T, T))
        store.close()
        store = Store(self.path)
        try:
            self.assertEqual(store.recover(T + timedelta(minutes=5)), 1)
            run = store.run(key)
            self.assertEqual(run["status"], "FAILED")
            self.assertEqual(store.review_report(run["run_id"])["error"], "interrupted_run")
            self.assertFalse(store.claim_slot(key, "XAUUSD", T, T))
        finally:
            store.close()

    def test_no_data_stale_forming_provider_failures_and_diagnostic_no_order(self):
        failures = (Data(age=180), Data(closed=False), Data(failure=TwelveDataProviderError("RATE_LIMITED", "METAL", http_status=429)),
                    Data(failure=TwelveDataProviderError("PROVIDER_FAILURE", "METAL", http_status=503)))
        for data in failures:
            runner = self.runner(data=data, dry_run=True)
            try:
                result = runner.run_once("XAUUSD", T)
                self.assertIn(result["status"], {"STALE_DATA", "NO_DATA"})
                self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
                self.assertTrue(result["review_durable"])
            finally:
                runner.close()
            self.tearDown(); self.setUp()

    def test_notification_persisted_before_delivery_and_restart(self):
        sink = FakeNotificationSink()
        runner = self.runner(sink=sink)
        try:
            with patched_scouts("LONG"):
                result = runner.run_once("XAUUSD", T)
            persisted = runner.store.notification_events(run_id=result["run_id"])
            self.assertTrue(persisted)
            self.assertEqual({event["event_id"] for event in persisted},
                             {event.event_id for event in sink.events if event.run_id == result["run_id"]})
            self.assertFalse(any(e["type"] == "SETUP_NO_SETUP" for e in persisted))
            self.assertEqual(runner.daily_summary(T), True)
            self.assertEqual(runner.daily_summary(T), False)
        finally:
            runner.close()

        runner = self.runner(sink=sink)
        try:
            self.assertEqual(runner.run_once("XAUUSD", T)["status"], "DUPLICATE")
            self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 1)
            self.assertEqual(len({e.event_id for e in sink.events}), len(sink.events))
        finally:
            runner.close()

    def test_open_position_restart_close_without_double_accounting(self):
        runner = self.runner()
        try:
            with patched_scouts("LONG"):
                first = runner.run_once("XAUUSD", T)
            self.assertEqual(first["status"], "PLAN_READY")
        finally:
            runner.close()
        runner = DemoRunner(self.config, market_provider=Data(), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(),
                            instruments={"XAUUSD": instrument()}, clock=lambda: T + timedelta(minutes=15))
        try:
            with patched_scouts("LONG"):
                runner.run_once("XAUUSD", T + timedelta(minutes=15))
            account, orders, fills = runner.store.load_paper("paper-main")
            self.assertEqual(len(orders), 1)
            self.assertEqual(len(fills), 1)
            self.assertIn("XAUUSD", account.open_positions)
            position_id = account.open_positions["XAUUSD"].position_id
            equity = account.equity
        finally:
            runner.close()
        runner = DemoRunner(self.config, market_provider=Data(close=131), ai_provider=DeterministicAIProvider(),
                            macro_provider=macro_fixture(),
                            instruments={"XAUUSD": instrument()}, clock=lambda: T + timedelta(minutes=30))
        try:
            runner.run_once("XAUUSD", T + timedelta(minutes=30))
            account, orders, fills = runner.store.load_paper("paper-main")
            self.assertEqual(account.open_positions, {})
            self.assertEqual(len(account.closed_trades), 1)
            self.assertEqual(account.closed_trades[0].position_id, position_id)
            self.assertGreater(account.equity, equity)
            closed_equity = account.equity
            self.assertEqual(runner.run_once("XAUUSD", T + timedelta(minutes=30))["status"], "DUPLICATE")
            self.assertEqual(runner.store.load_paper("paper-main")[0].equity, closed_equity)
            events = runner.store.notification_events()
            self.assertEqual(len([e for e in events if e["type"] == "POSITION_OPENED"]), 1)
            self.assertEqual(len([e for e in events if e["type"] == "POSITION_CLOSED"]), 1)
        finally:
            runner.close()

    def test_ai_failure_future_bar_and_sink_failure_fail_closed(self):
        class BrokenSink:
            def deliver(self, event):
                raise RuntimeError("offline sink")
        runner = self.runner(ai=FakeAIProvider(raises=RuntimeError("provider down")), sink=BrokenSink())
        try:
            with patched_scouts("LONG"):
                result = runner.run_once("XAUUSD", T)
            self.assertEqual(result["durable_status"], "COMPLETED")
            self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
            self.assertTrue(runner.store.notification_events(run_id=result["run_id"]))
        finally:
            runner.close()
        self.tearDown(); self.setUp()
        class FutureData:
            def load_snapshot(self, symbol, at):
                return frames(at + timedelta(minutes=5))
        runner = self.runner(data=FutureData())
        try:
            result = runner.run_once("XAUUSD", T)
            self.assertEqual(result["status"], "NO_DATA")
            self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
        finally:
            runner.close()


if __name__ == "__main__":
    unittest.main()
