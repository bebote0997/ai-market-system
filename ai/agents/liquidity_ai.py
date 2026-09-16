"""Liquidity AI specialist: interprets pools, equal levels and sweeps that the
deterministic Liquidity Agent already found. It never invents order blocks.
"""
import dataclasses

from ai.contracts import AI_SCHEMA_VERSION, AIRequest
from ai.prompts import LIQUIDITY_PROMPT_VERSION
from ai.runtime import call_agent

TIMEFRAMES = ("1h", "15m", "5m")


def _payload(message):
    if message is None or message.status not in {"OK", "PARTIAL"} or not message.evidence:
        return None
    first = message.evidence[0]
    return first if isinstance(first, dict) else None


def build_request(liquidity_reports, symbol, run_id, as_of):
    liquidity_reports = liquidity_reports if isinstance(liquidity_reports, dict) else {}
    evidence = []
    for timeframe in TIMEFRAMES:
        payload = _payload(liquidity_reports.get(timeframe))
        if payload is None:
            continue
        evidence.append({
            "evidence_id": f"liquidity_{timeframe}",
            "timeframe": timeframe,
            "sweeps": len(payload.get("sweeps", ()) or ()),
            "liquidity_above": len(payload.get("liquidity_above", ()) or ()),
            "liquidity_below": len(payload.get("liquidity_below", ()) or ()),
            "order_blocks": len(payload.get("order_blocks", ()) or ()),
        })
    return AIRequest(
        AI_SCHEMA_VERSION, run_id, as_of, symbol, "liquidity_ai", "liquidity_specialist",
        market_context={"timeframes": TIMEFRAMES},
        deterministic_evidence=tuple(evidence),
        allowed_actions=("interpret_liquidity",),
        constraints={"no_new_order_blocks": True},
        prompt_version=LIQUIDITY_PROMPT_VERSION,
    )


def review(provider, liquidity_reports, symbol, run_id, as_of, audit_log=None):
    request = build_request(liquidity_reports, symbol, run_id, as_of)
    response = call_agent(provider, request, audit_log)
    has_signal = any(
        item.get("sweeps") or item.get("liquidity_above") or item.get("liquidity_below")
        for item in request.deterministic_evidence
    )
    quality = "signal_present" if has_signal else ("evidence_only" if request.deterministic_evidence else "none")
    return dataclasses.replace(
        response, model_metadata={**response.model_metadata, "liquidity_quality": quality},
    )
