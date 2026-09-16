"""Macro/News AI specialist.

Reads only what the deterministic Macro/News Agent accepted (source,
timestamps, impact, freshness window). It separates FACT (verbatim evidence)
from INTERPRETATION (risk-environment reading) and never invents an event,
result, timestamp or directional impact.
"""
import dataclasses

from ai.contracts import AI_SCHEMA_VERSION, AIRequest
from ai.prompts import MACRO_PROMPT_VERSION
from ai.runtime import call_agent


def _payload(message):
    if message is None or message.status not in {"OK", "PARTIAL"} or not message.evidence:
        return None
    first = message.evidence[0]
    return first if isinstance(first, dict) else None


def build_request(macro_report, symbol, run_id, as_of):
    payload = _payload(macro_report)
    evidence = []
    if payload is not None:
        for index, event in enumerate(payload.get("macro_events", ()) or ()):
            evidence.append({
                "evidence_id": f"macro_event_{index}",
                "kind": "macro_event",
                "title": event.get("title"),
                "category": event.get("category"),
                "currency": event.get("currency"),
                "impact": event.get("impact"),
                "window": event.get("window"),
                "high_impact_active_window": event.get("impact") == "HIGH" and event.get("window") == "ACTIVE_WINDOW",
            })
        for index, item in enumerate(payload.get("news_items", ()) or ()):
            evidence.append({
                "evidence_id": f"news_item_{index}",
                "kind": "news_item",
                "headline": item.get("headline"),
                "category": item.get("category"),
                "window": item.get("window"),
            })
    return AIRequest(
        AI_SCHEMA_VERSION, run_id, as_of, symbol, "macro_ai", "macro_specialist",
        market_context={"scope": "macro_news"},
        deterministic_evidence=tuple(evidence),
        allowed_actions=("interpret_macro_context",),
        constraints={"no_new_events": True, "no_invented_impact": True},
        prompt_version=MACRO_PROMPT_VERSION,
    )


def review(provider, macro_report, symbol, run_id, as_of, audit_log=None):
    request = build_request(macro_report, symbol, run_id, as_of)
    response = call_agent(provider, request, audit_log)
    facts = tuple(
        {"evidence_id": item["evidence_id"], "category": item.get("category"), "impact": item.get("impact")}
        for item in request.deterministic_evidence
    )
    active_high_impact = any(item.get("high_impact_active_window") for item in request.deterministic_evidence)
    return dataclasses.replace(
        response,
        model_metadata={
            **response.model_metadata,
            "facts": facts,
            "interpretation": "elevated_risk_environment" if active_high_impact else "no_elevated_risk_flagged",
        },
    )
