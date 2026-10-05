"""SYSTEM HEALTH V2 component probes and passive observers (Phase 1 / Batch 2: F01-T01..T14 prep)."""
import ast
import copy
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from riesgo import crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime import health_observers, health_probes
from runtime.config import RuntimeConfig
from runtime.health_observers import (
    observe_agent_response, observe_ai_provider, observe_paper_operation, observe_risk_evaluation,
)
from runtime.health_probes import collect, health_projection, probe_disk, sidecar_status
from runtime.paper_contracts import paper_instruments
from runtime.service import OperationalRuntime
from runtime.system_health import HealthPolicy, SystemHealth
from storage.codec import safe_json, utc
from storage.database import Store
from storage.health_store import HealthStore

T = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)
POLICY = HealthPolicy(failed_after_consecutive_errors=2, heartbeat_stale_after_seconds=120,
                      progress_stale_after_seconds=1800)  # Test thresholds only; production values are open.
SECRET = "sk-proj-SECRETVALUE1234567890abcdef"


def at(minutes):
    return T + timedelta(minutes=minutes)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ProbeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-probes-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.trading_path = self.dir / "trading_floor.db"
        self.health_path = self.dir / "system_health.db"
        self.trading = Store(self.trading_path)
        self.addCleanup(lambda: self.trading.close())

    # --- trading-DB evidence written through the existing repository API ---------------------------------
    def add_run(self, slot_minute, *, status="COMPLETED", final="NO_SETUP", symbol="XAUUSD", seconds=30, error=None):
        key = f"{symbol}:{utc(at(slot_minute))}:15m"
        run_id = f"run-{symbol}-{slot_minute}"
        self.trading.claim_slot(key, symbol, at(slot_minute), at(slot_minute), run_id=run_id)
        self.trading.finish(key, at(slot_minute) + timedelta(seconds=seconds), status, final, error)
        return key, run_id

    def event(self, minute, source, event_type, payload=None, symbol="XAUUSD", run_id=None, severity="INFO"):
        self.trading.event(at(minute), run_id, symbol, source, event_type, severity, payload)

    def agent(self, key, name, status, *, warnings=(), provider="openai"):
        self.trading.db.execute("INSERT INTO agent_decisions VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                                (key, name, utc(T), status, None, None, None, None, "{}", safe_json(list(warnings)),
                                 safe_json({"provider": provider, "model": "gpt-test"})))

    def risk(self, key, status):
        self.trading.db.execute("INSERT INTO risk_decisions(slot_key,status,reason) VALUES(?,?,?)",
                                (key, status, "fixture"))

    def collected(self, now=None):
        self.trading.close()
        before = digest(self.trading_path)
        outcome = collect(trading_db_path=self.trading_path, health_db_path=self.health_path,
                          runtime_dir=self.dir, policy=POLICY, now=now or at(120))
        self.assertEqual(digest(self.trading_path), before)  # Probes never write the trading DB.
        self.trading = Store(self.trading_path)
        self.store = HealthStore(self.health_path)
        self.addCleanup(self.store.close)
        self.health = SystemHealth(self.store, POLICY)
        return outcome

    def get(self, component, provider=None):
        return self.health.get(component, provider)


class SchedulerTests(ProbeCase):
    def test_t01_alive_and_progressing_vs_alive_but_stalled(self):
        self.trading.heartbeat(at(31), "RUNNING")
        self.add_run(0)
        self.add_run(15)
        self.assertEqual(set(self.collected().values()), {None})
        record = self.get("scheduler")
        self.assertEqual((record.status, record.progress_stage, record.latency_ms), ("HEALTHY", "run_completed", 30000.0))
        self.assertEqual(record.details["scheduler_state"], "RUNNING")
        alive = self.health.project(record, now=at(32))
        self.assertEqual((alive["liveness"], alive["progress"], alive["status"]), ("ALIVE", "ADVANCING", "HEALTHY"))
        self.store.close()
        self.trading.heartbeat(at(80), "RUNNING")  # Heartbeat keeps beating; no new run completes.
        self.collected(now=at(81))
        stalled = self.health.project(self.get("scheduler"), now=at(81))
        self.assertEqual((stalled["liveness"], stalled["progress"], stalled["status"]), ("ALIVE", "STALLED", "STALE"))

    def test_t01_failed_runs_count_errors(self):
        self.add_run(0, status="FAILED", final="ERROR", error="RuntimeError")
        self.add_run(15, status="FAILED", final="ERROR", error="RuntimeError")
        self.collected()
        record = self.get("scheduler")
        self.assertEqual((record.status, record.consecutive_errors), ("FAILED", 2))
        self.assertIn("RuntimeError", record.sanitized_error_reason)


class MarketAndMacroTests(ProbeCase):
    def test_t02_current_stale_no_data_and_provider_failure(self):
        cases = (("XAUUSD", "DATA_CHECK", {"state": "CURRENT"}, "HEALTHY", None),
                 ("EURUSD", "DATA_CHECK", {"state": "STALE_DATA"}, "STALE", None),
                 ("NAS100", "DATA_UNAVAILABLE", None, "UNKNOWN", None),
                 ("GBPUSD", "RATE_LIMITED", None, "DEGRADED", "RATE_LIMITED"))
        self.trading.set_state("market_provider_mode", "twelve_data")
        for index, (symbol, event_type, payload, *_ ) in enumerate(cases):
            self.event(index, "market_data", event_type, payload, symbol=symbol, run_id=f"r{index}")
        self.collected()
        for symbol, _, _, status, error_type in cases:
            with self.subTest(symbol=symbol):
                record = self.get(f"market_data:{symbol}", "twelve_data")
                self.assertEqual((record.status, record.error_type), (status, error_type))
                self.assertEqual(record.consecutive_errors, 1 if error_type else 0)
        self.assertEqual(self.get("market_data:EURUSD", "twelve_data").details["data_state"], "STALE_DATA")

    def test_t03_valid_legitimate_no_data_and_provider_failure(self):
        self.trading.set_state("macro_provider_mode", "fxmacrodata")
        self.event(0, "macro", "MACRO_COMPLETE", {"status": "RECORDED"}, run_id="r1")
        self.event(15, "macro", "MACRO_COMPLETE", {"status": "NO_DATA"}, run_id="r2")
        self.collected()
        record = self.get("macro_data", "fxmacrodata")
        self.assertEqual((record.status, record.details["data_state"]), ("HEALTHY", "NO_DATA"))  # Not a failure.
        self.store.close()
        self.trading.set_state("macro_provider", "RATE_LIMITED")
        self.event(30, "macro_news", "PROVIDER_FAILURE", {"provider": "fxmacrodata"}, run_id="r3", severity="ERROR")
        self.event(30, "macro", "MACRO_COMPLETE", {"status": "NO_DATA"}, run_id="r3")
        self.collected()
        record = self.get("macro_data", "fxmacrodata")
        self.assertEqual((record.status, record.error_type, record.consecutive_errors), ("DEGRADED", "RATE_LIMITED", 1))

    def test_t03_unconfigured_macro_is_unknown_not_healthy(self):
        self.event(0, "macro", "MACRO_COMPLETE", {"status": "NO_DATA"}, run_id="r1")
        self.collected()
        record = self.get("macro_data", "none")
        self.assertEqual((record.status, record.sanitized_error_reason), ("UNKNOWN", "NOT_CONFIGURED"))


class AITests(ProbeCase):
    def test_t05_t08_agents_are_independent_of_each_other_and_of_the_provider(self):
        key, _ = self.add_run(0, final="AI_CAUTION")
        self.agent(key, "structure_ai", "OK")
        self.agent(key, "liquidity_ai", "OK")
        self.agent(key, "macro_ai", "ERROR", warnings=("supporting_evidence_not_supplied",))
        self.agent(key, "setup_reviewer_ai", "NO_DATA")
        self.collected()
        self.assertEqual(self.get("structure_ai", "openai").status, "HEALTHY")
        self.assertEqual(self.get("liquidity_ai", "openai").status, "HEALTHY")
        self.assertEqual(self.get("setup_reviewer_ai", "openai").status, "HEALTHY")
        macro = self.get("macro_ai", "openai")
        self.assertEqual((macro.status, macro.error_type), ("DEGRADED", "INVALID_RESPONSE"))
        self.assertEqual(self.get("ai_provider", "openai").status, "HEALTHY")  # Provider fine, one agent invalid.

    def test_t04_provider_exception_degrades_provider_and_that_agent(self):
        key, _ = self.add_run(0)
        self.agent(key, "structure_ai", "ERROR", warnings=("provider_exception:OpenAIProviderError",))
        self.agent(key, "liquidity_ai", "OK")
        self.collected()
        self.assertEqual(self.get("ai_provider", "openai").status, "DEGRADED")
        self.assertEqual(self.get("structure_ai", "openai").status, "DEGRADED")
        self.assertEqual(self.get("liquidity_ai", "openai").status, "HEALTHY")

    def test_t04_live_provider_taxonomy(self):
        store = HealthStore(self.health_path)
        self.addCleanup(store.close)
        health = SystemHealth(store, POLICY)
        cases = (None, "RATE_LIMITED", "QUOTA_EXHAUSTED", "MODEL_UNAVAILABLE", "CONNECTION_ERROR", "SOMETHING_NEW")
        expected = (None, "RATE_LIMITED", "BILLING_OR_QUOTA", "MODEL_UNAVAILABLE", "CONNECTION_ERROR",
                    "UNKNOWN_PROVIDER_FAILURE")
        for index, (failure, error_type) in enumerate(zip(cases, expected)):
            provider = SimpleNamespace(metadata={"provider": f"openai{index}", "model": "gpt-test"},
                                       last_failure=failure, last_usage={"input_tokens": 10, "output_tokens": 3})
            with self.subTest(failure=failure):
                self.assertTrue(observe_ai_provider(health, provider, at=at(index), observation_id=f"p{index}",
                                                    latency_ms=250))
                record = health.get("ai_provider", f"openai{index}")
                self.assertEqual(record.error_type, error_type)
                self.assertEqual(record.status, "HEALTHY" if failure is None else "DEGRADED")
        self.assertEqual(health.get("ai_provider", "openai0").details["input_tokens"], 10)
        self.assertEqual(health.get("ai_provider", "openai2").legacy_error_name, "QUOTA_EXHAUSTED")

    def test_live_agent_observer_uses_provider_kind_when_supplied(self):
        store = HealthStore(self.health_path)
        self.addCleanup(store.close)
        health = SystemHealth(store, POLICY)
        response = SimpleNamespace(agent_name="macro_ai", status="ERROR", warnings=("provider_exception:X",),
                                   model_metadata={"provider": "openai"})
        observe_agent_response(health, response, at=T, observation_id="a1", provider_kind="CONNECTION_ERROR")
        self.assertEqual(health.get("macro_ai", "openai").error_type, "CONNECTION_ERROR")


class RiskAndPaperTests(ProbeCase):
    def test_t09_approved_and_rejected_are_operational_success(self):
        self.risk(self.add_run(0)[0], "APPROVED")
        self.risk(self.add_run(15)[0], "REJECTED")
        self.collected()
        record = self.get("risk_engine")
        self.assertEqual((record.status, record.consecutive_errors, record.details["decision"]),
                         ("HEALTHY", 0, "REJECTED"))

    def test_t09_exception_is_health_failure(self):
        store = HealthStore(self.health_path)
        self.addCleanup(store.close)
        health = SystemHealth(store, POLICY)
        observe_risk_evaluation(health, at=at(0), observation_id="r1", decision=SimpleNamespace(status="REJECTED"))
        self.assertEqual(health.get("risk_engine").status, "HEALTHY")
        observe_risk_evaluation(health, at=at(1), observation_id="r2", exception=ZeroDivisionError("boom"))
        self.assertEqual(health.get("risk_engine").status, "DEGRADED")

    def test_t10_business_rejection_is_not_failure_but_operational_error_is(self):
        self.event(0, "order-1", "ORDER_SUBMITTED")
        self.event(5, "order-1", "ORDER_REJECTED", {"reason": "post_fill_risk_or_geometry"})
        self.collected()
        self.assertEqual(self.get("paper_broker", "paper").status, "HEALTHY")
        self.store.close()
        self.event(10, "order-2", "ORDER_REJECTED", {"reason": "invalid_order_contract"})
        self.collected()
        record = self.get("paper_broker", "paper")
        self.assertEqual((record.status, record.details["real_execution"]), ("DEGRADED", "DISABLED"))
        observe_paper_operation(self.health, at=at(20), observation_id="x", operation="process_next_bar",
                                exception=RuntimeError("io"))
        self.assertEqual(self.get("paper_broker", "paper").status, "FAILED")


class InfrastructureTests(ProbeCase):
    def test_t11_trading_db_read_only_schema_three_unchanged(self):
        self.collected()
        record = self.get("trading_db")
        self.assertEqual((record.status, record.details["schema_version"], record.details["quick_check"]),
                         ("HEALTHY", 3, "ok"))
        tables = {r[0] for r in self.trading.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(tables & {"component_health", "health_observations", "health_schema_info"})

    def test_t11_unopenable_trading_db_is_recorded_without_creating_it(self):
        self.trading.close()
        missing = self.dir / "missing.db"
        outcome = collect(trading_db_path=missing, health_db_path=self.health_path, runtime_dir=self.dir,
                          policy=POLICY, now=T)
        self.assertIn("trading_evidence", outcome)
        self.assertFalse(missing.exists())
        store = HealthStore(self.health_path, readonly=True)
        self.addCleanup(store.close)
        self.assertEqual(SystemHealth(store, POLICY).get("trading_db").status, "DEGRADED")
        self.trading = Store(self.trading_path)

    def test_t11_broken_sidecar_projected_in_memory_without_recursion(self):
        self.health_path.write_bytes(b"corrupted sidecar")
        before = digest(self.health_path)
        self.trading.close()
        outcome = collect(trading_db_path=self.trading_path, health_db_path=self.health_path, runtime_dir=self.dir,
                          policy=POLICY, now=T)
        self.assertEqual(list(outcome), ["health_sidecar"])
        status = sidecar_status(self.health_path)
        self.assertEqual(status["status"], "FAILED")
        self.assertEqual(digest(self.health_path), before)  # Nothing written back into the broken sidecar.
        projection = health_projection(health_db_path=self.health_path, policy=POLICY, now=T)
        self.assertEqual(projection["components"][0]["status"], "FAILED")
        self.trading = Store(self.trading_path)

    def test_t12_disk_accessible_and_unavailable(self):
        self.collected()
        record = self.get("persistent_disk")
        self.assertEqual(record.status, "HEALTHY")
        self.assertGreater(record.details["free_bytes"], 0)
        probe_disk(self.dir / "absent", self.health, now=at(200))
        self.assertEqual(self.get("persistent_disk").status, "DEGRADED")
        self.assertFalse((self.dir / "absent").exists())

    def test_t13_email_truthfully_not_implemented(self):
        self.collected()
        projection = health_projection(health_db_path=self.health_path, policy=POLICY, now=at(120))
        email = next(c for c in projection["components"] if c["component"] == "email")
        self.assertEqual((email["status"], email["reason"]), ("UNKNOWN", "NOT_IMPLEMENTED"))

    def test_t14_projection_is_complete_sanitized_and_read_only(self):
        self.trading.heartbeat(at(1), "RUNNING")
        self.add_run(0, status="FAILED", final="ERROR", error=f"Bearer {SECRET} apikey={SECRET}")
        self.collected()
        self.store.close()
        before = digest(self.health_path)
        projection = health_projection(health_db_path=self.health_path, policy=POLICY, now=at(2))
        self.assertEqual(digest(self.health_path), before)
        self.assertIsNone(projection["overall_status"])
        self.assertEqual(projection["overall_policy"], "OWNER_DECISION_REQUIRED")
        scheduler = next(c for c in projection["components"] if c["component"] == "scheduler")
        for key in ("status", "liveness", "progress", "last_success_at", "last_error_at", "last_checked_at",
                    "heartbeat_at", "progress_at", "consecutive_errors", "latency_ms", "error_type", "reason"):
            self.assertIn(key, scheduler)
        text = json.dumps(projection)
        self.assertNotIn(SECRET, text)
        self.assertNotIn("SECRETVALUE", text)


class IsolationTests(ProbeCase):
    """F01-T20 preparation: health failure never changes trading behavior."""

    def broken_health(self):
        store = HealthStore(self.health_path)
        self.addCleanup(store.close)
        health = SystemHealth(store, POLICY)
        health._observe = lambda *a, **k: (_ for _ in ()).throw(OSError("sidecar write failed"))
        return health

    def test_sidecar_write_failure_does_not_alter_a_real_trading_cycle(self):
        self.trading.close()
        results = []
        for index, sidecar_broken in enumerate((False, True)):
            path = self.dir / f"cycle-{index}.db"
            if sidecar_broken:
                self.health_path.write_bytes(b"broken")
            runtime = OperationalRuntime(RuntimeConfig(db_path=path, enabled_symbols=("XAUUSD",)),
                                         clock=lambda: datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc))
            try:
                status = runtime.run_cycle("XAUUSD", datetime(2026, 1, 15, 13, 30, tzinfo=timezone.utc))
            finally:
                runtime.close()
            outcome = collect(trading_db_path=path, health_db_path=self.health_path, runtime_dir=self.dir,
                              policy=POLICY, now=T)
            store = Store(path, readonly=True)
            try:
                rows = store.db.execute("SELECT status, final_status FROM runs").fetchall()
                orders = store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0]
            finally:
                store.close()
            results.append((status, [tuple(r) for r in rows], orders))
            self.assertEqual(bool(outcome.get("health_sidecar")), sidecar_broken)
        self.assertEqual(results[0], results[1])
        self.trading = Store(self.trading_path)

    def test_probe_exception_does_not_alter_risk_result(self):
        plan = {"symbol": "XAUUSD", "side": "LONG", "timeframe": "5m", "entry": 2000.0, "stop": 1990.0,
                "target": 2030.0, "risk_reward": 3.0}
        args = (plan, 10000.0, paper_instruments()["XAUUSD"], crear_configuracion_riesgo_v2())
        expected = evaluar_trade_plan(*args)
        decision = evaluar_trade_plan(*args)
        self.assertFalse(observe_risk_evaluation(self.broken_health(), at=T, observation_id="r", decision=decision))
        self.assertEqual(decision, expected)
        self.assertEqual(decision.status, "APPROVED")

    def test_probe_exception_does_not_create_cancel_or_mutate_paper_state(self):
        account = PaperAccount("1.0", "paper-main", 10000.0, 10000.0, 10000.0)
        broker = PaperBroker(account)
        before = (copy.deepcopy(vars(account)), dict(broker.orders), dict(broker.fills), list(broker.journal))
        health = self.broken_health()
        self.assertFalse(observe_paper_operation(health, at=T, observation_id="p", operation="submit_plan"))
        self.assertFalse(observe_paper_operation(health, at=T, observation_id="q", operation="process_next_bar",
                                                 exception=RuntimeError("x")))
        self.assertEqual((vars(account), broker.orders, broker.fills, broker.journal), before)

    def test_health_cannot_enable_real_execution_or_touch_trading_modules(self):
        for module in (health_probes, health_observers):
            tree = ast.parse(Path(module.__file__).read_bytes())
            imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
                a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
            with self.subTest(module=module.__name__):
                self.assertFalse({m for m in imports if m.split(".")[0] in {
                    "execution", "riesgo", "agents", "ai", "floor", "estrategia", "operaciones", "urllib",
                    "requests", "socket", "time", "random", "uuid"}})
                calls = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree)
                         if isinstance(n, ast.Call)}
                self.assertFalse(calls & {"submit_plan", "process_next_bar", "process_bar", "evaluar_trade_plan",
                                          "crear_trade_plan", "save_paper", "set_state", "heartbeat", "generate",
                                          "now", "utcnow", "finish", "claim_slot", "executescript"})
                source = Path(module.__file__).read_text(encoding="utf-8")
                self.assertNotIn("REAL_EXECUTION", source.upper().replace("REAL_EXECUTION\": \"DISABLED", ""))

    def test_observers_never_raise_even_with_invalid_input(self):
        health = self.broken_health()
        with patch.object(health_observers.LOG, "warning") as log:
            self.assertFalse(observe_ai_provider(health, object(), at=None, observation_id=""))
            self.assertFalse(observe_agent_response(health, None, at=T, observation_id="a"))
        self.assertEqual(log.call_count, 2)


if __name__ == "__main__":
    unittest.main()
