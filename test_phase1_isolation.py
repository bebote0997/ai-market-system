"""F01-T20 Phase 1 observability isolation certification (plus F01-T02/T04 hook evidence).

The same deterministic PAPER scenario (order -> fill -> target close) runs with health absent,
healthy, unavailable, corrupt, write-raising and sink-raising; every trading outcome must match.
"""
import copy
from datetime import timedelta
from pathlib import Path
import tempfile
from urllib.error import HTTPError
import unittest

from ai.openai_provider import OpenAIProvider, OpenAIProviderError
from riesgo import crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime import health_hooks
from runtime.config import DEFAULT_ENABLED_SYMBOLS, RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.gates import fresh_snapshot
from runtime.health import health as legacy_health
from runtime.health_observers import SystemHealthSink, observe_risk_evaluation
from runtime.health_probes import health_projection
from runtime.paper_contracts import paper_instruments
from runtime.system_health import HealthPolicy, SystemHealth
from storage.database import Store
from storage.health_store import HealthStore
from test_demo_runner import T, Data, frames, instrument, macro_fixture, patched_scouts
from test_real_providers import openai_payload, request as ai_request

POLICY = HealthPolicy(failed_after_consecutive_errors=2, heartbeat_stale_after_seconds=120,
                      progress_stale_after_seconds=1800)  # Test thresholds only.
HEALTH_TABLES = {"health_schema_info", "component_health", "health_observations"}


class PathSink:
    """Opens the sidecar on every event, so an unavailable/corrupt sidecar fails inside the hook."""
    def __init__(self, path):
        self.path = path

    def __getattr__(self, event):
        def handle(**facts):
            store = HealthStore(self.path)
            try:
                getattr(SystemHealthSink(SystemHealth(store, POLICY)), event)(**facts)
            finally:
                store.close()
        return handle


class RaisingSink:
    def __getattr__(self, event):
        def handle(**facts):
            raise RuntimeError("health sink exploded")
        return handle


def broken_health(store):
    health = SystemHealth(store, POLICY)
    health._observe = lambda *a, **k: (_ for _ in ()).throw(OSError("sidecar write failed"))
    return health


class IsolationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-phase1-iso-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.addCleanup(health_hooks.uninstall_health_sink)

    def install(self, mode, name):
        sidecar = self.dir / f"{name}-health.db"
        if mode == "absent":
            health_hooks.uninstall_health_sink()
        elif mode == "healthy":
            store = HealthStore(sidecar)
            self.addCleanup(store.close)
            health_hooks.install_health_sink(SystemHealthSink(SystemHealth(store, POLICY)))
        elif mode == "unavailable":
            sidecar.mkdir()  # A directory can never be opened as the sidecar.
            health_hooks.install_health_sink(PathSink(sidecar))
        elif mode == "corrupt":
            sidecar.write_bytes(b"corrupted health sidecar" * 50)
            health_hooks.install_health_sink(PathSink(sidecar))
        elif mode == "write_raises":
            store = HealthStore(sidecar)
            self.addCleanup(store.close)
            health_hooks.install_health_sink(SystemHealthSink(broken_health(store)))
        else:
            health_hooks.install_health_sink(RaisingSink())
        return sidecar


class TradingScenarioTests(IsolationCase):
    MODES = ("absent", "healthy", "unavailable", "corrupt", "write_raises", "sink_raises")

    def scenario(self, mode):
        db = self.dir / f"{mode}-trading.db"
        config = RuntimeConfig(db_path=db, enabled_symbols=("XAUUSD", "EURUSD"),
                               market_provider_mode="twelve_data", ai_provider_mode="openai")
        sidecar = self.install(mode, mode)
        statuses = []
        # Same as test_demo_runner: patched LONG scouts open and fill; the close cycle uses real scouts.
        for minutes, close, patched in ((0, 100., True), (15, 100., True), (30, 131., False)):
            at = T + timedelta(minutes=minutes)
            runner = DemoRunner(config, market_provider=Data(close=close), ai_provider=_deterministic(),
                                macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()},
                                clock=lambda at=at: at)
            try:
                with patched_scouts("LONG") if patched else _nullcontext():
                    statuses.append(runner.run_once("XAUUSD", at)["status"])
            finally:
                runner.close()
        return self.outcome(db, statuses), sidecar

    def outcome(self, db, statuses):
        store = Store(db, readonly=True)
        try:
            q = lambda sql: [tuple(row) for row in store.db.execute(sql).fetchall()]
            account, orders, fills = store.load_paper("paper-main")
            return {
                "statuses": statuses,
                "runs": q("SELECT slot_key, status, final_status, prompt_versions, provider_metadata FROM runs "
                          "ORDER BY slot_key"),
                "setups": q("SELECT slot_key, status, side, invalidation FROM setups ORDER BY slot_key"),
                "risk": q("SELECT slot_key, status, reason, quantity, capital_at_risk, entry, stop, target "
                          "FROM risk_decisions ORDER BY slot_key"),
                "agents": q("SELECT slot_key, agent_name, status, recommendation, model_metadata FROM agent_decisions "
                            "ORDER BY slot_key, agent_name"),
                "data_checks": q("SELECT payload FROM journal WHERE event_type='DATA_CHECK' ORDER BY id"),
                "orders": sorted((o.side, o.status, o.quantity, o.stop, o.target, o.planned_entry)
                                 for o in orders.values()),
                "fills": sorted((f.side, f.quantity, f.fill_price) for f in fills.values()),
                "open_positions": sorted((p.side, p.quantity, p.stop, p.target) for p in account.open_positions.values()),
                "closed": [(t.side, t.exit_price, t.quantity, t.net_pnl, t.reason) for t in account.closed_trades],
                "equity": (account.equity, account.realized_pnl, account.unrealized_pnl),
                "schema": store.db.execute("SELECT version FROM schema_info").fetchone()[0],
                "tables": {r[0] for r in store.db.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                "real_execution": legacy_health(store, now=T)["real_execution"],
            }
        finally:
            store.close()

    def test_items_1_to_3_and_9_to_19_trading_identical_across_health_modes(self):
        baseline, _ = self.scenario("absent")
        self.assertEqual(baseline["statuses"][0], "PLAN_READY")
        self.assertEqual(len(baseline["orders"]), 1)
        self.assertEqual(len(baseline["fills"]), 1)
        self.assertEqual(len(baseline["closed"]), 1)
        self.assertEqual(baseline["closed"][0][-1], "target")
        for mode in self.MODES[1:]:
            with self.subTest(mode=mode):
                outcome, sidecar = self.scenario(mode)
                self.assertEqual(outcome, baseline)  # 1-3, 9-12 (orders/fills/positions), 14-17.
                self.assertEqual(outcome["schema"], 3)  # 18.
                self.assertFalse(outcome["tables"] & HEALTH_TABLES)  # 19.
                self.assertEqual(outcome["real_execution"], "DISABLED")  # 13.
        self.assertNotIn("NAS100", DEFAULT_ENABLED_SYMBOLS)

    def test_f01_t02_market_hook_records_latest_bar_evidence(self):
        _, sidecar = self.scenario("healthy")
        projection = health_projection(health_db_path=sidecar, policy=POLICY, now=T + timedelta(minutes=31))
        bars = {c["component"]: c for c in projection["components"] if c["component"].startswith("market_data:XAUUSD:")}
        self.assertEqual(set(bars), {"market_data:XAUUSD:1h", "market_data:XAUUSD:15m", "market_data:XAUUSD:5m"})
        five = bars["market_data:XAUUSD:5m"]
        self.assertEqual((five["status"], five["provider"]), ("HEALTHY", "twelve_data"))
        self.assertEqual(five["details"]["latest_bar_timestamp"], (T + timedelta(minutes=30)).isoformat())
        self.assertEqual(five["details"]["data_state"], "CURRENT")
        self.assertEqual((five["progress_stage"], five["progress_at"]),
                         ("bar_received", (T + timedelta(minutes=30)).isoformat()))


def _nullcontext():
    from contextlib import nullcontext
    return nullcontext()


def _deterministic():
    from ai.provider import DeterministicAIProvider
    return DeterministicAIProvider()


class MarketDataHookTests(IsolationCase):
    def test_item_4_market_observer_failure_leaves_snapshot_and_freshness_unchanged(self):
        limits = RuntimeConfig().max_age_seconds
        for stale in (False, True):
            snapshot = frames(T, age=40 if stale else 0)
            pristine = copy.deepcopy(snapshot)
            verdict = fresh_snapshot(snapshot, "XAUUSD", T, limits)[:2]
            for mode in ("sink_raises", "write_raises", "corrupt"):
                with self.subTest(stale=stale, mode=mode):
                    self.install(mode, f"md-{stale}-{mode}")
                    self.assertIsNone(health_hooks.emit("market_data_observed", symbol="XAUUSD", slot=T,
                                                        snapshot=snapshot, data_state=verdict[1],
                                                        provider_mode="twelve_data"))
                    self.assertEqual(fresh_snapshot(snapshot, "XAUUSD", T, limits)[:2], verdict)
                    for timeframe in pristine:
                        self.assertTrue(snapshot[timeframe].equals(pristine[timeframe]))

    def test_stale_market_data_recorded_as_stale_with_bar_timestamp(self):
        sidecar = self.install("healthy", "md-stale")
        snapshot = frames(T, age=40)
        health_hooks.emit("market_data_observed", symbol="EURUSD", slot=T, snapshot=snapshot,
                          data_state="STALE_DATA", provider_mode="twelve_data")
        store = HealthStore(sidecar)
        self.addCleanup(store.close)
        record = SystemHealth(store, POLICY).get("market_data:EURUSD:5m", "twelve_data")
        self.assertEqual((record.status, record.details["latest_bar_timestamp"]),
                         ("STALE", (T - timedelta(minutes=40)).isoformat()))


class ProviderHookTests(IsolationCase):
    def provider(self, transport):
        return OpenAIProvider(api_key="dummy", retries=1, transport=transport, sleep=lambda _: None)

    def test_item_5_success_result_unchanged_and_f01_t04_evidence(self):
        expected = self.provider(lambda *_: openai_payload()).generate(ai_request())
        for mode in ("sink_raises", "write_raises", "corrupt", "unavailable"):
            with self.subTest(mode=mode):
                self.install(mode, f"ok-{mode}")
                self.assertEqual(self.provider(lambda *_: openai_payload()).generate(ai_request()), expected)
        sidecar = self.install("healthy", "ok-healthy")
        self.assertEqual(self.provider(lambda *_: openai_payload()).generate(ai_request()), expected)
        store = HealthStore(sidecar)
        self.addCleanup(store.close)
        record = SystemHealth(store, POLICY).get("ai_provider", "openai")
        self.assertEqual(record.status, "HEALTHY")
        self.assertIsNotNone(record.latency_ms)
        self.assertEqual((record.details["input_tokens"], record.details["output_tokens"], record.details["total_tokens"],
                          record.details["agent"], record.details["model"]), (20, 10, 30, "structure_ai", "gpt-5.6-terra"))

    def failing(self, error_factory):
        attempts = []

        def transport(*_):
            attempts.append(1)
            raise error_factory()
        return transport, attempts

    def test_item_6_original_exception_preserved_with_any_sink(self):
        cases = {"RATE_LIMITED": lambda: HTTPError("https://api.openai.com/v1/responses", 429, "x", {}, None),
                 "MODEL_UNAVAILABLE": lambda: HTTPError("https://api.openai.com/v1/responses", 404, "x", {}, None),
                 "CONNECTION_ERROR": lambda: ConnectionError("reset")}
        for kind, factory in cases.items():
            self.install("absent", f"err-{kind}-absent")
            transport, attempts = self.failing(factory)
            with self.assertRaises(OpenAIProviderError) as baseline:
                self.provider(transport).generate(ai_request())
            expected = (type(baseline.exception), baseline.exception.kind, baseline.exception.http_status, len(attempts))
            self.assertEqual(expected[1], kind)
            for mode in ("sink_raises", "write_raises", "corrupt", "healthy"):
                with self.subTest(kind=kind, mode=mode):
                    self.install(mode, f"err-{kind}-{mode}")
                    transport, attempts = self.failing(factory)
                    with self.assertRaises(OpenAIProviderError) as caught:
                        self.provider(transport).generate(ai_request())
                    self.assertEqual((type(caught.exception), caught.exception.kind, caught.exception.http_status,
                                      len(attempts)), expected)

    def test_f01_t04_typed_error_kind_and_latency_persisted(self):
        sidecar = self.install("healthy", "typed")
        body = lambda: __import__("io").BytesIO(b'{"error":{"code":"insufficient_quota","message":"sk-secret-detail"}}')
        transport, _ = self.failing(lambda: HTTPError("https://api.openai.com/v1/responses", 429, "q", {}, body()))
        with self.assertRaises(OpenAIProviderError):
            self.provider(transport).generate(ai_request())
        store = HealthStore(sidecar)
        self.addCleanup(store.close)
        record = SystemHealth(store, POLICY).get("ai_provider", "openai")
        self.assertEqual((record.error_type, record.legacy_error_name), ("BILLING_OR_QUOTA", "QUOTA_EXHAUSTED"))
        self.assertIsNotNone(record.latency_ms)
        self.assertNotIn("input_tokens", record.details)  # No usage on failure: absent, never invented.
        payloads = " ".join(r[0] for r in store.db.execute("SELECT payload FROM health_observations"))
        self.assertNotIn("sk-secret-detail", payloads)
        self.assertNotIn("dummy", payloads)


class RiskObserverTests(IsolationCase):
    def test_items_7_8_risk_results_unchanged_when_observer_fails(self):
        store = HealthStore(self.dir / "risk-health.db")
        self.addCleanup(store.close)
        health = broken_health(store)
        base = {"symbol": "XAUUSD", "side": "LONG", "timeframe": "5m", "entry": 2000.0, "stop": 1990.0,
                "target": 2030.0, "risk_reward": 3.0}
        for plan, status in ((base, "APPROVED"), ({**base, "risk_reward": 2.0, "target": 2020.0}, "REJECTED")):
            with self.subTest(status=status):
                args = (plan, 10000.0, paper_instruments()["XAUUSD"], crear_configuracion_riesgo_v2())
                expected, decision = evaluar_trade_plan(*args), evaluar_trade_plan(*args)
                self.assertFalse(observe_risk_evaluation(health, at=T, observation_id=f"r-{status}", decision=decision))
                self.assertEqual((decision, decision.status), (expected, status))


if __name__ == "__main__":
    unittest.main()


class ComponentTaxonomyTests(IsolationCase):
    def test_non_provider_failures_never_get_provider_labels(self):
        from runtime.health_observers import observe_paper_operation
        from runtime.health_probes import probe_disk
        from runtime.system_health import ComponentErrorType, ProviderErrorType
        store = HealthStore(self.dir / "taxonomy.db")
        self.addCleanup(store.close)
        health = SystemHealth(store, POLICY)
        observe_risk_evaluation(health, at=T, observation_id="r", exception=ZeroDivisionError("x"))
        observe_paper_operation(health, at=T, observation_id="p", operation="submit_plan", exception=OSError("x"))
        probe_disk(self.dir / "missing", health, now=T)
        labels = {name: health.get(name, provider).error_type
                  for name, provider in (("risk_engine", None), ("paper_broker", "paper"), ("persistent_disk", None))}
        self.assertEqual(labels, {"risk_engine": "RISK_ENGINE_ERROR", "paper_broker": "PAPER_BROKER_ERROR",
                                  "persistent_disk": "DISK_ERROR"})
        self.assertFalse({e.value for e in ComponentErrorType} & {e.value for e in ProviderErrorType})
        with self.assertRaises(ValueError):
            health.record_error("scheduler", at=T, observation_id="bad", component_error="NOT_A_CLASS")
