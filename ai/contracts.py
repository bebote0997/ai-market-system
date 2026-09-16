"""Versioned, structured contracts for the AI advisory layer.

AI never talks to other components through free text. Every cross-component
decision is a frozen dataclass so it can be validated, audited and, if a
provider misbehaves, rejected without touching deterministic state.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Tuple


AI_SCHEMA_VERSION = "1.0"

VALID_AI_STATUSES = {"OK", "PARTIAL", "NO_DATA", "ERROR"}
VALID_BIAS = {"BULLISH", "BEARISH", "NEUTRAL", "UNKNOWN"}
VALID_RECOMMENDATIONS = {
    "AGREE", "CAUTION", "DISAGREE", "INSUFFICIENT_DATA",
    "ACCEPT", "REJECT_RECOMMENDATION",
}
VALID_FLOOR_STATUSES = {
    "NO_DATA", "NO_SETUP", "WATCH", "AI_CAUTION", "RISK_REJECTED", "PLAN_READY", "ERROR",
}


@dataclass(frozen=True)
class AIRequest:
    schema_version: str
    run_id: str
    as_of: datetime
    symbol: str
    agent_name: str
    role: str
    market_context: dict
    deterministic_evidence: Tuple[dict, ...]
    allowed_actions: Tuple[str, ...]
    constraints: dict
    prompt_version: str
    deterministic_preferred: bool = True


@dataclass(frozen=True)
class AIResponse:
    schema_version: str
    run_id: str
    as_of: datetime
    symbol: str
    agent_name: str
    status: str
    bias: str = "UNKNOWN"
    confidence: float = 0.0
    recommendation: Optional[str] = None
    observations: Tuple[str, ...] = ()
    supporting_evidence: Tuple[str, ...] = ()
    conflicting_evidence: Tuple[str, ...] = ()
    risks: Tuple[str, ...] = ()
    invalidation_conditions: Tuple[str, ...] = ()
    warnings: Tuple[str, ...] = ()
    model_metadata: dict = field(default_factory=dict)
    reasoning_summary: Optional[str] = None


@dataclass(frozen=True)
class AIFloorReport:
    schema_version: str
    run_id: str
    as_of: datetime
    symbol: str
    deterministic_reports: dict
    ai_structure: Optional[AIResponse]
    ai_liquidity: Optional[AIResponse]
    ai_macro: Optional[AIResponse]
    ai_setup_review: Optional[AIResponse]
    trade_plan: Optional[object]
    ai_trade_review: Optional[AIResponse]
    risk_decision: Optional[object]
    final_status: str
    warnings: Tuple[str, ...] = ()
    prompt_versions: dict = field(default_factory=dict)
    provider_metadata: dict = field(default_factory=dict)


def evidence_ids(deterministic_evidence):
    return {
        item.get("evidence_id")
        for item in deterministic_evidence
        if isinstance(item, dict) and item.get("evidence_id") is not None
    }


def validate_ai_response(response, request):
    """Reject any response that is not fully grounded in the supplied request.

    Returns (is_valid, reason).
    """
    if not isinstance(response, AIResponse):
        return False, "invalid_response_type"
    if response.schema_version != AI_SCHEMA_VERSION:
        return False, "invalid_schema_version"
    if response.run_id != request.run_id:
        return False, "run_id_mismatch"
    if response.symbol != request.symbol:
        return False, "symbol_mismatch"
    if response.agent_name != request.agent_name:
        return False, "agent_mismatch"
    if response.as_of != request.as_of:
        return False, "as_of_mismatch"
    if response.status not in VALID_AI_STATUSES:
        return False, "invalid_status"
    if response.bias not in VALID_BIAS:
        return False, "invalid_bias"
    if response.recommendation is not None and response.recommendation not in VALID_RECOMMENDATIONS:
        return False, "invalid_recommendation"
    if isinstance(response.confidence, bool) or not isinstance(response.confidence, (int, float)):
        return False, "invalid_confidence"
    if not (0.0 <= float(response.confidence) <= 1.0):
        return False, "invalid_confidence"
    allowed = evidence_ids(request.deterministic_evidence)
    if any(item not in allowed for item in response.supporting_evidence):
        return False, "ungrounded_supporting_evidence"
    if any(item not in allowed for item in response.conflicting_evidence):
        return False, "ungrounded_conflicting_evidence"
    return True, "ok"
