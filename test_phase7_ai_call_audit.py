"""V2 Phase 7 / P7.1 Batch B: durable AI_CALL records, usage, estimated cost, evidence fingerprint, soft budgets."""
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import ai.orchestrator as ai_orchestrator
from ai import call_audit, prompts
from ai.call_audit import PricingTable, SoftBudget, build_records, evaluate_soft_budget, evidence_fingerprint, persist
from ai.openai_provider import OpenAIProvider
from ai.provider import DeterministicAIProvider
from ai.runtime import AuditLog
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from storage.database import Store
import test_demo_runner as fixture
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts
from test_phase7_ai_characterization import full_floor_report, request

SECRET = "sk-test-SECRET-key-123"


def echo_transport(fail=None, usage=True, calls=None):
    """Fake Responses API: echoes each request's identity and cites all supplied evidence ids.
    ``fail(attempt_no)`` may raise to simulate errors."""
    state = {"n": 0}

    def transport(payload, key, timeout):
        state["n"] += 1
        if calls is not None:
            calls.append(json.loads(payload["input"])["agent_name"])
        if fail is not None:
            fail(state["n"])
        req = json.loads(payload["input"])
        ids = [e["evidence_id"] for e in req["deterministic_evidence"] if isinstance(e, dict) and e.get("evidence_id")]
        role = req["role"]
        data = {"schema_version": req["schema_version"], "run_id": req["run_id"], "as_of": req["as_of"],
                "symbol": req["symbol"], "agent_name": req["agent_name"], "status": "OK", "bias": "BULLISH",
                "confidence": 0.7, "recommendation": "AGREE" if role == "setup_reviewer" else
                "ACCEPT" if role == "trade_reviewer" else None,
                "observations": ["grounded"], "supporting_evidence": ids, "conflicting_evidence": [], "risks": [],
                "invalidation_conditions": [], "warnings": [], "reasoning_summary": "grounded summary"}
        raw = {"id": f"resp_test{state['n']}", "status": "completed",
               "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(data)}]}]}
        if usage:
            raw["usage"] = {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140}
        return raw
    return transport


def openai(transport, **kwargs):
    return OpenAIProvider(api_key=SECRET, transport=transport, sleep=lambda _: None, **kwargs)


class Db(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p71b-")
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "trading_floor.db")
        self.addCleanup(lambda: self.store.close())

    def rows(self, event=call_audit.EVENT):
        return [json.loads(r[0]) for r in self.store.db.execute(
            "SELECT payload FROM journal WHERE event_type=? ORDER BY id", (event,))]


class RecordTests(Db):
    def cycle(self, transport):
        log = AuditLog()
        ai_orchestrator.run(full_floor_report(), openai(transport), log)
        return log

    def test_records_usage_attempts_ids_and_cost_labels(self):
        log = self.cycle(echo_transport())
        records = build_records(log.entries, run_id="run-p7", symbol="XAUUSD", setup_id="S-1")
        self.assertEqual([r["agent"] for r in records], ["structure_ai", "liquidity_ai", "macro_ai",
                                                         "setup_reviewer_ai", "trade_reviewer_ai"])
        first = records[0]
        self.assertEqual((first["outcome"], first["usage"], first["usage_reported"], first["attempts"], first["retries"]),
                         ("OK", {"input": 100, "output": 40, "total": 140}, True, 1, 0))
        self.assertEqual((first["response_id"], first["setup_id"], first["model"], first["provider"]),
                         ("resp_test1", "S-1", "gpt-5.6-terra", "openai"))
        self.assertEqual(first["cost"], {"basis": "PRICE_NOT_CONFIGURED"})
        self.assertTrue(first["called"] and first["latency_ms"] >= 0 and first["requested_at"] <= first["responded_at"])
        priced = build_records(log.entries, run_id="run-p7", symbol="XAUUSD",
                               pricing=PricingTable({"gpt-5.6-terra": (2.5, 10)}))
        self.assertEqual(priced[0]["cost"], {"basis": "ESTIMATED", "currency": "USD", "amount": "0.00065",
                                             "pricing_source": "OWNER_CONFIGURED_TABLE"})  # 100*2.5e-6 + 40*1e-5

    def test_missing_usage_is_never_claimed(self):
        records = build_records(self.cycle(echo_transport(usage=False)).entries, run_id="run-p7", symbol="XAUUSD",
                                pricing=PricingTable({"gpt-5.6-terra": (2.5, 10)}))
        self.assertEqual({(r["usage"], r["usage_reported"], r["cost"]["basis"]) for r in records},
                         {(None, False, "USAGE_UNAVAILABLE")})

    def test_retries_are_recorded_per_attempt(self):
        from urllib.error import HTTPError

        def fail(n):
            if n in (1, 2):
                raise HTTPError("u", 429, "x", {}, None)
        records = build_records(self.cycle(echo_transport(fail=fail)).entries, run_id="run-p7", symbol="XAUUSD")
        self.assertEqual((records[0]["attempts"], records[0]["retries"],
                          [a["http_status"] for a in records[0]["attempt_log"]]), (3, 2, [429, 429, 200]))
        self.assertEqual([a["kind"] for a in records[0]["attempt_log"]], ["RATE_LIMITED", "RATE_LIMITED", None])

    def test_typed_failure_survives_into_the_record(self):
        from urllib.error import HTTPError
        import io

        def fail(n):
            raise HTTPError("u", 429, "x", {}, io.BytesIO(b'{"error":{"code":"insufficient_quota"}}'))
        records = build_records(self.cycle(echo_transport(fail=fail)).entries, run_id="run-p7", symbol="XAUUSD")
        self.assertEqual({(r["outcome"], r["error_kind"], r["http_status"], r["usage"]) for r in records},
                         {("QUOTA_EXHAUSTED", "QUOTA_EXHAUSTED", 429, None)})

    def test_persist_is_atomic_idempotent_and_redacted(self):
        records = build_records(self.cycle(echo_transport()).entries, run_id="run-p7", symbol="XAUUSD")
        self.assertEqual(persist(self.store, records, at=T), 5)
        self.assertEqual(persist(self.store, records, at=T), 0)  # retry/restart: no duplicates
        stored = self.rows()
        self.assertEqual([r["call_sequence"] for r in stored], [0, 1, 2, 3, 4])
        self.assertEqual(stored[0]["usage"], {"input": 100, "output": 40, "total": 140})  # survives safe_json
        text = " ".join(r[0] for r in self.store.db.execute("SELECT payload FROM journal"))
        for forbidden in (SECRET, "Bearer", "Authorization", prompts.STRUCTURE_PROMPT.strip()[:40], "instructions"):
            self.assertNotIn(forbidden, text)
        original = Store._event
        count = {"n": 0}

        def boom(store, *args, **kwargs):
            count["n"] += 1
            if count["n"] == 2:
                raise OSError("crash mid-write")
            return original(store, *args, **kwargs)
        other = [replace_run(r, "run-crash") for r in records]
        with patch.object(Store, "_event", boom), self.assertRaises(OSError):
            persist(self.store, other, at=T)
        self.assertEqual([r for r in self.rows() if r["run_id"] == "run-crash"], [])  # all or nothing


def replace_run(record, run_id):
    return {**record, "run_id": run_id}


class FingerprintTests(unittest.TestCase):
    def test_deterministic_and_evidence_sensitive(self):
        base = request()
        self.assertEqual(evidence_fingerprint(base), evidence_fingerprint(replace(base, run_id="other",
                                                                                   as_of=base.as_of + timedelta(hours=1))))
        changed = replace(base, deterministic_evidence=({"evidence_id": "e1", "bias": "BEARISH"},))
        self.assertNotEqual(evidence_fingerprint(base), evidence_fingerprint(changed))
        self.assertNotEqual(evidence_fingerprint(base), evidence_fingerprint(replace(base, symbol="EURUSD")))
        self.assertEqual(len(evidence_fingerprint(base)), 64)
        log = AuditLog()
        ai_orchestrator.run(full_floor_report(), DeterministicAIProvider(), log)
        self.assertTrue(all(len(e["evidence_fingerprint"]) == 64 for e in log.entries))


class SoftBudgetTests(Db):
    def test_observational_once_per_scope(self):
        log = AuditLog()
        ai_orchestrator.run(full_floor_report(), openai(echo_transport()), log)
        records = build_records(log.entries, run_id="run-p7", symbol="XAUUSD",
                                pricing=PricingTable({"gpt-5.6-terra": (2.5, 10)}))
        persist(self.store, records, at=T)
        budget = SoftBudget(max_cycle_estimated_cost="0.001", max_daily_usage_total=500)
        first = evaluate_soft_budget(self.store, records, budget, at=T)
        self.assertEqual({(o["metric"], o["effect"]) for o in first},
                         {("cycle_estimated_cost", "OBSERVATION_ONLY"), ("daily_usage_total", "OBSERVATION_ONLY")})
        self.assertEqual({o["metric"]: o["basis"] for o in first},
                         {"cycle_estimated_cost": "ESTIMATED", "daily_usage_total": "PROVIDER_REPORTED_USAGE"})
        self.assertEqual(evaluate_soft_budget(self.store, records, budget, at=T), [])  # deduplicated
        self.assertEqual(len(self.rows(call_audit.BUDGET_EVENT)), 2)
        self.assertEqual(evaluate_soft_budget(self.store, records, None, at=T), [])
        text = (Path(__file__).resolve().parent / "ai" / "call_audit.py").read_text(encoding="utf-8").lower()
        for word in ("balance", "credit", "funds"):
            self.assertNotIn(f'"{word}', text)


class RuntimeFlagTests(unittest.TestCase):
    setUp = fixture.TestDemoRunner.setUp
    tearDown = fixture.TestDemoRunner.tearDown

    def run_cycle(self, config, **runtime_kwargs):
        runner = DemoRunner(config, market_provider=Data(), ai_provider=openai(echo_transport()),
                            macro_provider=macro_fixture(), instruments={"XAUUSD": instrument()}, clock=lambda: T)
        try:
            for name, value in runtime_kwargs.items():
                setattr(runner.runtime, name, value)
            with patched_scouts("LONG"):
                result = runner.run_once("XAUUSD", T)
            calls = [json.loads(r[0]) for r in runner.store.db.execute(
                "SELECT payload FROM journal WHERE event_type='AI_CALL' ORDER BY id")]
            review = runner.store.review_report(result["run_id"])
            orders = runner.store.load_paper("paper-main")[1]
            return result, calls, review, orders
        finally:
            runner.close()
            for suffix in ("", "-wal", "-shm"):
                self.path.with_name(self.path.name + suffix).unlink(missing_ok=True)

    def test_flag_off_by_default_and_not_from_env(self):
        self.assertFalse(RuntimeConfig().v2_ai_call_audit)
        with patch.dict("os.environ", {"AI_FLOOR_V2_AI_CALL_AUDIT": "1"}):
            self.assertFalse(RuntimeConfig.from_env().v2_ai_call_audit)
        self.assertEqual(self.config.fingerprint(), replace(self.config, v2_ai_call_audit=False).fingerprint())

    def test_off_vs_on_same_decision_and_durable_records(self):
        off = self.run_cycle(self.config)
        on = self.run_cycle(replace(self.config, v2_ai_call_audit=True))
        self.assertEqual(off[1], [])  # OFF: no AI_CALL rows
        self.assertEqual([c["agent"] for c in on[1]], ["structure_ai", "liquidity_ai", "macro_ai",
                                                        "setup_reviewer_ai", "trade_reviewer_ai"])
        self.assertTrue(all(c["setup_id"] == on[2]["execution"]["setup_id"] for c in on[1]))
        for result, _, review, orders in (off, on):  # identical economic outcome
            self.assertEqual((result["status"], review["execution"]["execution_reason"], len(orders)),
                             ("PLAN_READY", "ORDER_SUBMITTED", 1))
        self.assertEqual(on[2]["agents"][0]["usage"], {"input": 100, "output": 40, "total": 140})  # default-durable
        self.assertEqual(off[2]["agents"][0]["usage"], {"input": 100, "output": 40, "total": 140})

    def test_audit_failure_never_changes_the_cycle(self):
        with patch("ai.call_audit.persist", side_effect=OSError("disk full")):
            result, calls, review, orders = self.run_cycle(replace(self.config, v2_ai_call_audit=True))
        self.assertEqual((result["status"], review["execution"]["execution_reason"], len(orders), calls),
                         ("PLAN_READY", "ORDER_SUBMITTED", 1, []))  # missing records are never "approval"

    def test_soft_budget_breach_is_observation_only(self):
        result, calls, review, orders = self.run_cycle(
            replace(self.config, v2_ai_call_audit=True), ai_pricing=PricingTable({"gpt-5.6-terra": (2.5, 10)}),
            ai_soft_budget=SoftBudget(max_cycle_estimated_cost="0"))
        self.assertEqual((result["status"], len(orders)), ("PLAN_READY", 1))


if __name__ == "__main__":
    unittest.main()
