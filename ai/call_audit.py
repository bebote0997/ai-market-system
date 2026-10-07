"""V2 Phase 7 (P7.1 Batch B): durable per-call AI observability (DEC-7.2, DEC-7.3, DEC-7.10).

``AI_CALL`` journal rows in the trading DB (schema 3, no DDL), one per agent call of a cycle, built only from
whitelisted fields of the in-memory AuditLog. Never persisted: API key, Authorization header, HTTP headers, prompts,
request or response bodies. Writing is observability: callers wrap it so a failure can never change a decision.

Key names never contain the substring "token": ``storage.codec.safe_json`` (the persistence sanitizer) deliberately
drops such keys as potential secrets, so provider usage counts are stored as ``usage.input/output/total``.

Cost is an ESTIMATE from an Owner-configured price table only. Nothing here reads, infers or names an account balance.
Soft budgets are observational: they write ``AI_SOFT_BUDGET`` rows and never block, allow or alter anything.
"""
from dataclasses import dataclass, field
from datetime import timezone
from decimal import Decimal
import hashlib
import json

RECORD_VERSION = "V2_P7_AI_CALL_1"
FINGERPRINT_VERSION = "V2_P7_EVIDENCE_1"
EVENT = "AI_CALL"
BUDGET_EVENT = "AI_SOFT_BUDGET"
SOURCE = "ai_call_audit"


def evidence_fingerprint(request):
    """sha256 of the canonical evidence an agent call evaluated (symbol, agent, role and the supplied deterministic
    evidence; no timestamps, run_id or prompt). Identical evidence -> identical fingerprint."""
    canonical = json.dumps({"fingerprint_version": FINGERPRINT_VERSION, "symbol": request.symbol,
                            "agent": request.agent_name, "role": request.role,
                            "evidence": list(request.deterministic_evidence)},
                           sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(canonical.encode("ascii")).hexdigest()


def usage_of(metadata):
    """Provider-reported usage counts, or None when the provider did not report them (never invented)."""
    metadata = metadata or {}
    values = {name: metadata.get(f"{name}_tokens") for name in ("input", "output", "total")}
    if not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values.values()):
        return None
    return values


@dataclass(frozen=True)
class PricingTable:
    """Owner-configured USD prices per 1,000,000 input / output units, by model. Estimates only."""
    prices: dict = field(default_factory=dict)  # model -> (input_per_million, output_per_million)
    currency: str = "USD"

    def estimate(self, model, usage):
        if usage is None:
            return {"basis": "USAGE_UNAVAILABLE"}
        price = self.prices.get(model)
        if price is None:
            return {"basis": "PRICE_NOT_CONFIGURED"}
        amount = (Decimal(str(price[0])) * usage["input"] + Decimal(str(price[1])) * usage["output"]) / 1_000_000
        return {"basis": "ESTIMATED", "currency": self.currency, "amount": str(amount.normalize()),
                "pricing_source": "OWNER_CONFIGURED_TABLE"}


def build_records(audit_entries, *, run_id, symbol, setup_id=None, pricing=None):
    """One AI_CALL record per AuditLog entry of ``run_id`` (call_sequence = order within the cycle)."""
    records = []
    for sequence, entry in enumerate(e for e in audit_entries if e.get("run_id") == run_id):
        response = entry.get("response")
        call = dict(entry.get("call") or {})
        metadata = dict(getattr(response, "model_metadata", {}) or {})
        provider = dict(entry.get("provider_metadata") or {})
        usage = usage_of(metadata) if entry.get("outcome") == "OK" else None
        model = provider.get("model") or metadata.get("model")
        records.append({
            "record_version": RECORD_VERSION, "run_id": run_id, "setup_id": setup_id, "symbol": symbol,
            "call_sequence": sequence, "agent": entry.get("agent"), "provider": provider.get("provider"),
            "model": model, "prompt_version": entry.get("prompt_version"),
            "evidence_fingerprint": entry.get("evidence_fingerprint"),
            "evidence_ids": list(entry.get("evidence_ids") or ()),
            "called": call.get("called", False), "requested_at": call.get("requested_at"),
            "responded_at": call.get("responded_at"), "latency_ms": call.get("latency_ms"),
            "attempts": len(call.get("attempts") or ()) or (1 if call.get("called") else 0),
            "retries": max(0, len(call.get("attempts") or ()) - 1),
            "attempt_log": [{k: a.get(k) for k in ("attempt", "http_status", "kind", "latency_ms")}
                            for a in call.get("attempts") or ()],
            "response_id": call.get("response_id"),
            "status": getattr(response, "status", None), "outcome": entry.get("outcome"),
            "error_kind": entry.get("error_kind"), "http_status": entry.get("http_status"),
            "validation": entry.get("validation"),
            "usage": usage, "usage_reported": usage is not None,
            "cost": (pricing or PricingTable()).estimate(model, usage),
            "health_before": call.get("health_before"), "health_after": call.get("health_after"),
        })
    return records


def persist(store, records, *, at, owner_key=None, symbol=None):
    """Write the AI_CALL rows of one cycle in ONE transaction; rows already present for (run_id, call_sequence) are
    skipped, so a retry never duplicates. Returns the number of rows written."""
    written = 0
    with store.transaction():
        if owner_key is not None and not store.owns_slot(owner_key, symbol):
            raise RuntimeError("ai call audit without slot ownership")
        for record in records:
            exists = store.db.execute(
                "SELECT 1 FROM journal WHERE event_type=? AND run_id=? AND json_extract(payload,'$.call_sequence')=?",
                (EVENT, record["run_id"], record["call_sequence"])).fetchone()
            if exists is None:
                store._event(at, record["run_id"], record["symbol"], SOURCE, EVENT, "INFO", record)
                written += 1
    return written


@dataclass(frozen=True)
class SoftBudget:
    """Observational thresholds (None = not configured). Crossing one writes AI_SOFT_BUDGET; nothing else."""
    max_cycle_estimated_cost: object = None
    max_daily_estimated_cost: object = None
    max_cycle_usage_total: object = None
    max_daily_usage_total: object = None


def _totals(records):
    usage = sum(r["usage"]["total"] for r in records if r.get("usage"))
    cost = sum((Decimal(r["cost"]["amount"]) for r in records if r.get("cost", {}).get("basis") == "ESTIMATED"),
               Decimal(0))
    return usage, cost


def evaluate_soft_budget(store, records, budget, *, at):
    """Compare this cycle and today's (UTC) persisted AI_CALL totals with ``budget``; journal each newly crossed
    threshold once per day (cycle thresholds once per run). Returns the observations; never raises into callers'
    decisions (callers wrap it like every observability write)."""
    if budget is None or not records:
        return []
    day = at.astimezone(timezone.utc).date().isoformat()
    cycle_usage, cycle_cost = _totals(records)
    rows = [json.loads(r[0]) for r in store.db.execute(
        "SELECT payload FROM journal WHERE event_type=? AND substr(timestamp,1,10)=?", (EVENT, day))]
    daily_usage, daily_cost = _totals(rows)
    checks = (("cycle_estimated_cost", cycle_cost, budget.max_cycle_estimated_cost, records[0]["run_id"]),
              ("daily_estimated_cost", daily_cost, budget.max_daily_estimated_cost, day),
              ("cycle_usage_total", cycle_usage, budget.max_cycle_usage_total, records[0]["run_id"]),
              ("daily_usage_total", daily_usage, budget.max_daily_usage_total, day))
    observations = []
    with store.transaction():
        for metric, value, limit, scope in checks:
            if limit is None or Decimal(str(value)) <= Decimal(str(limit)):
                continue
            seen = store.db.execute(
                "SELECT 1 FROM journal WHERE event_type=? AND json_extract(payload,'$.metric')=? "
                "AND json_extract(payload,'$.scope')=?", (BUDGET_EVENT, metric, scope)).fetchone()
            if seen is not None:
                continue
            observation = {"metric": metric, "scope": scope, "value": str(value), "soft_limit": str(limit),
                           "basis": "ESTIMATED" if "cost" in metric else "PROVIDER_REPORTED_USAGE",
                           "effect": "OBSERVATION_ONLY"}
            store._event(at, records[0]["run_id"], records[0]["symbol"], SOURCE, BUDGET_EVENT, "WARNING", observation)
            observations.append(observation)
    return observations


__all__ = ["BUDGET_EVENT", "EVENT", "FINGERPRINT_VERSION", "PricingTable", "RECORD_VERSION", "SoftBudget",
           "build_records", "evaluate_soft_budget", "evidence_fingerprint", "persist", "usage_of"]
