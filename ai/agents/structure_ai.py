"""Structure AI specialist: interprets multi-timeframe alignment.

It never re-derives swings/BOS from raw prices; it only reads what the
deterministic Structure Agent already produced.
"""
import dataclasses

from ai.contracts import AI_SCHEMA_VERSION, AIRequest
from ai.prompts import STRUCTURE_PROMPT_VERSION
from ai.runtime import call_agent

BIAS_MAP = {"bullish": "BULLISH", "bearish": "BEARISH", "neutral": "NEUTRAL"}
TIMEFRAMES = ("1h", "15m", "5m")


def _payload(message):
    if message is None or message.status not in {"OK", "PARTIAL"} or not message.evidence:
        return None
    first = message.evidence[0]
    return first if isinstance(first, dict) else None


def build_request(structure_reports, symbol, run_id, as_of):
    structure_reports = structure_reports if isinstance(structure_reports, dict) else {}
    evidence = []
    for timeframe in TIMEFRAMES:
        payload = _payload(structure_reports.get(timeframe))
        if payload is None:
            continue
        evidence.append({
            "evidence_id": f"structure_{timeframe}",
            "timeframe": timeframe,
            "bias": BIAS_MAP.get(payload.get("bias"), "UNKNOWN"),
            "structure_state": payload.get("structure_state"),
            "bos": bool(payload.get("bos")),
            "retracement": bool(payload.get("retracement")),
        })
    return AIRequest(
        AI_SCHEMA_VERSION, run_id, as_of, symbol, "structure_ai", "structure_specialist",
        market_context={"timeframes": TIMEFRAMES},
        deterministic_evidence=tuple(evidence),
        allowed_actions=("interpret_structure",),
        constraints={"no_new_price_evidence": True},
        prompt_version=STRUCTURE_PROMPT_VERSION,
    )


def review(provider, structure_reports, symbol, run_id, as_of, audit_log=None):
    request = build_request(structure_reports, symbol, run_id, as_of)
    response = call_agent(provider, request, audit_log)
    biases = [item["bias"] for item in request.deterministic_evidence if item.get("bias") in {"BULLISH", "BEARISH"}]
    if not request.deterministic_evidence:
        alignment, quality = "unknown", "none"
    elif len(biases) == len(request.deterministic_evidence) and len(set(biases)) == 1:
        alignment = "aligned"
        quality = "high" if len(request.deterministic_evidence) == len(TIMEFRAMES) else "partial"
    else:
        alignment, quality = "mixed", "partial"
    return dataclasses.replace(
        response,
        model_metadata={**response.model_metadata, "timeframe_alignment": alignment, "structure_quality": quality},
    )
