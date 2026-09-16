"""AI Trade Reviewer.

Reads the deterministic TradePlan (already produced by Python) plus
specialist context. It cannot change entry, stop, target, quantity,
contract_multiplier, equity or risk_fraction -- TradePlan/RiskDecision are
frozen dataclasses, and this module never constructs a replacement plan. Its
recommendation (ACCEPT/CAUTION/REJECT_RECOMMENDATION) is advisory only; the
Deterministic Risk Engine remains the sole authority to approve or reject.
"""
from ai.contracts import AI_SCHEMA_VERSION, AIRequest
from ai.prompts import TRADE_REVIEW_PROMPT_VERSION
from ai.runtime import call_agent


def build_request(trade_plan, ai_structure, ai_liquidity, ai_macro, symbol, run_id, as_of):
    if trade_plan is None:
        evidence = ()
    else:
        evidence = [{
            "evidence_id": "trade_plan",
            "side": trade_plan.side,
            "risk_reward": trade_plan.risk_reward,
            "invalidation": trade_plan.invalidation,
        }]
        for name, response in (("structure", ai_structure), ("liquidity", ai_liquidity), ("macro", ai_macro)):
            if response is None:
                continue
            evidence.append({
                "evidence_id": f"ai_{name}_bias",
                "bias": getattr(response, "bias", "UNKNOWN"),
                "high_impact_active_window": bool(getattr(response, "model_metadata", {}).get("interpretation") == "elevated_risk_environment"),
            })
    return AIRequest(
        AI_SCHEMA_VERSION, run_id, as_of, symbol, "trade_reviewer_ai", "trade_reviewer",
        market_context={"stage": "trade_review"},
        deterministic_evidence=tuple(evidence),
        allowed_actions=("accept", "caution", "reject_recommendation"),
        constraints={
            "cannot_change_entry": True, "cannot_change_stop": True, "cannot_change_target": True,
            "cannot_change_quantity": True, "cannot_change_contract_multiplier": True,
            "cannot_change_equity": True, "cannot_change_risk_fraction": True,
        },
        prompt_version=TRADE_REVIEW_PROMPT_VERSION,
    )


def review(provider, trade_plan, ai_structure, ai_liquidity, ai_macro, symbol, run_id, as_of, audit_log=None):
    request = build_request(trade_plan, ai_structure, ai_liquidity, ai_macro, symbol, run_id, as_of)
    return call_agent(provider, request, audit_log)
