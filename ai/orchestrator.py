"""Floor AI Orchestrator.

Composes on top of an already-computed, real `floor.orchestrator.run(...)`
FloorRunReport. It does not modify or re-run any deterministic component; it
only adds an advisory AI interpretation layer:

    deterministic Structure   -> Structure AI
    deterministic Liquidity   -> Liquidity AI
    deterministic Macro/News  -> Macro AI
                               -> AI Setup Reviewer  (reads the already-final
                                  SetupAssessment; cannot change its status)
                               -> AI Trade Reviewer   (reads the already-final
                                  TradePlan; cannot change any of its levels)

The RiskDecision produced by the Deterministic Risk Engine is carried through
unchanged: this module never calls the Risk Engine differently, never
overrides REJECTED with APPROVED, and never grants execution permission.
REAL_EXECUTION remains DISABLED; this module never talks to a broker.
"""
from ai.agents import liquidity_ai, macro_ai, setup_reviewer_ai, structure_ai, trade_reviewer_ai
from ai.contracts import AI_SCHEMA_VERSION, AIFloorReport, VALID_FLOOR_STATUSES
from ai.prompts import (
    LIQUIDITY_PROMPT_VERSION,
    MACRO_PROMPT_VERSION,
    SETUP_REVIEW_PROMPT_VERSION,
    STRUCTURE_PROMPT_VERSION,
    TRADE_REVIEW_PROMPT_VERSION,
)

PROMPT_VERSIONS = {
    "structure": STRUCTURE_PROMPT_VERSION,
    "liquidity": LIQUIDITY_PROMPT_VERSION,
    "macro": MACRO_PROMPT_VERSION,
    "setup_review": SETUP_REVIEW_PROMPT_VERSION,
    "trade_review": TRADE_REVIEW_PROMPT_VERSION,
}


def _final_status(deterministic_report, ai_setup_review, ai_trade_review):
    base = deterministic_report.final_status
    if "equity_invalid" in deterministic_report.warnings:
        return "NO_DATA"
    if base not in VALID_FLOOR_STATUSES:
        return "ERROR"
    if base == "PLAN_READY":
        setup_disagree = ai_setup_review is not None and ai_setup_review.recommendation == "DISAGREE"
        trade_reject = ai_trade_review is not None and ai_trade_review.recommendation == "REJECT_RECOMMENDATION"
        if setup_disagree or trade_reject:
            return "AI_CAUTION"
    return base


def run(deterministic_report, provider, audit_log=None):
    run_id = deterministic_report.run_id
    as_of = deterministic_report.as_of
    symbol = deterministic_report.symbol

    ai_structure = structure_ai.review(provider, deterministic_report.structure_reports, symbol, run_id, as_of, audit_log)
    ai_liquidity = liquidity_ai.review(provider, deterministic_report.liquidity_reports, symbol, run_id, as_of, audit_log)
    ai_macro = macro_ai.review(provider, deterministic_report.macro_news_report, symbol, run_id, as_of, audit_log)
    ai_setup_review = setup_reviewer_ai.review(
        provider, deterministic_report.setup_assessment, ai_structure, ai_liquidity, ai_macro, symbol, run_id, as_of, audit_log,
    )
    ai_trade_review = None
    if deterministic_report.trade_plan is not None:
        ai_trade_review = trade_reviewer_ai.review(
            provider, deterministic_report.trade_plan, ai_structure, ai_liquidity, ai_macro, symbol, run_id, as_of, audit_log,
        )

    final_status = _final_status(deterministic_report, ai_setup_review, ai_trade_review)

    warnings = list(deterministic_report.warnings)
    for response in (ai_structure, ai_liquidity, ai_macro, ai_setup_review, ai_trade_review):
        if response is not None:
            warnings.extend(response.warnings)

    return AIFloorReport(
        AI_SCHEMA_VERSION, run_id, as_of, symbol,
        deterministic_reports={
            "structure_reports": deterministic_report.structure_reports,
            "liquidity_reports": deterministic_report.liquidity_reports,
            "macro_news_report": deterministic_report.macro_news_report,
            "setup_assessment": deterministic_report.setup_assessment,
        },
        ai_structure=ai_structure,
        ai_liquidity=ai_liquidity,
        ai_macro=ai_macro,
        ai_setup_review=ai_setup_review,
        trade_plan=deterministic_report.trade_plan,
        ai_trade_review=ai_trade_review,
        risk_decision=deterministic_report.risk_decision,
        final_status=final_status,
        warnings=tuple(dict.fromkeys(warnings)),
        prompt_versions=dict(PROMPT_VERSIONS),
        provider_metadata=dict(getattr(provider, "metadata", {})),
    )
