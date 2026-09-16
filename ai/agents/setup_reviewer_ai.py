"""AI Setup Reviewer.

Reads Structure AI, Liquidity AI, Macro AI and the deterministic
SetupAssessment. It can only comment (AGREE/CAUTION/DISAGREE/
INSUFFICIENT_DATA); it can never turn NO_SETUP into VALID_SETUP nor WATCH
into an executable setup. The orchestrator enforces this by never reading
`recommendation` as a status override.
"""
from ai.contracts import AI_SCHEMA_VERSION, AIRequest
from ai.prompts import SETUP_REVIEW_PROMPT_VERSION
from ai.runtime import call_agent


def build_request(setup_assessment, ai_structure, ai_liquidity, ai_macro, symbol, run_id, as_of):
    evidence = [{
        "evidence_id": "setup_status",
        "status": getattr(setup_assessment, "status", None),
        "side": getattr(setup_assessment, "side", None),
    }]
    for name, response in (("structure", ai_structure), ("liquidity", ai_liquidity), ("macro", ai_macro)):
        if response is None:
            continue
        evidence.append({
            "evidence_id": f"ai_{name}_bias",
            "bias": getattr(response, "bias", "UNKNOWN"),
            "status": getattr(response, "status", None),
        })
    return AIRequest(
        AI_SCHEMA_VERSION, run_id, as_of, symbol, "setup_reviewer_ai", "setup_reviewer",
        market_context={"stage": "setup_review"},
        deterministic_evidence=tuple(evidence),
        allowed_actions=("agree", "caution", "disagree", "insufficient_data"),
        constraints={"cannot_change_setup_status": True},
        prompt_version=SETUP_REVIEW_PROMPT_VERSION,
    )


def review(provider, setup_assessment, ai_structure, ai_liquidity, ai_macro, symbol, run_id, as_of, audit_log=None):
    request = build_request(setup_assessment, ai_structure, ai_liquidity, ai_macro, symbol, run_id, as_of)
    return call_agent(provider, request, audit_log)
