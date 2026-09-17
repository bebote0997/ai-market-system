"""Versioned, bounded review record; only public summaries and evidence IDs."""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ReviewReport:
    schema_version: str
    run_id: str
    slot_key: str
    symbol: str
    as_of: str
    final_status: str
    setup_status: str
    agents: tuple
    risk_decision: dict | None
    paper: dict
    warnings: tuple
    error: str | None = None

    def payload(self):
        return asdict(self)


def build_review(key, deterministic, ai, audit_entries=()):
    audit = {entry["agent"]: entry for entry in audit_entries}
    agents = []
    for response in (ai.ai_structure, ai.ai_liquidity, ai.ai_macro,
                     ai.ai_setup_review, ai.ai_trade_review):
        if response is None:
            continue
        entry = audit.get(response.agent_name, {})
        agents.append({"agent": response.agent_name, "status": response.status,
                       "bias": response.bias, "recommendation": response.recommendation,
                       "reasoning_summary": response.reasoning_summary,
                       "evidence_received": tuple(entry.get("evidence_ids", ())),
                       "evidence_supporting": response.supporting_evidence,
                       "evidence_conflicting": response.conflicting_evidence,
                       "validation": entry.get("validation", "NOT_RECORDED"),
                       "warnings": response.warnings})
    risk = deterministic.risk_decision
    risk_summary = None if risk is None else {field: getattr(risk, field) for field in
        ("status", "reason", "symbol", "side", "risk_fraction", "quantity",
         "capital_at_risk", "entry", "stop", "target")}
    return ReviewReport("1.0", ai.run_id, key, ai.symbol, ai.as_of.isoformat(),
                        ai.final_status, deterministic.setup_assessment.status,
                        tuple(agents), risk_summary, {}, tuple(ai.warnings))
