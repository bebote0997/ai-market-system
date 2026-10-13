# Phase 6A Audit — AI Agent Runtime

- phase: Fase 6A — AI Agent Runtime
- tests_before: 315
- tests_after: 381 OK
- ai_runtime: implemented in `ai/contracts.py`, `ai/provider.py`, `ai/runtime.py`, `ai/prompts.py`
- specialists: `ai/agents/structure_ai.py`, `ai/agents/liquidity_ai.py`, `ai/agents/macro_ai.py`, `ai/agents/setup_reviewer_ai.py`, `ai/agents/trade_reviewer_ai.py`
- orchestrator: `ai/orchestrator.py` composes over an already-computed `floor.orchestrator` `FloorRunReport`; it does not re-run or modify the deterministic orchestrator
- provider_abstraction: `AIProvider.generate(request) -> AIResponse`; `DeterministicAIProvider` (no network) is the default; `FakeAIProvider` is the test double
- grounding_policy: `validate_ai_response` rejects any response with a mismatched run_id, symbol, agent_name, as_of, out-of-range confidence, invalid bias/recommendation, or an `evidence_id` not present on the request
- failure_isolation: provider exceptions are caught and converted to a local `ERROR` response; the rest of the floor run is unaffected
- cost_control: no deterministic evidence -> no provider call -> `NO_DATA` returned directly
- authority_policy: `NO_SETUP`/`WATCH`/`RISK_REJECTED` from the deterministic layer are never converted into an executable state; `PLAN_READY` with an adverse AI review becomes `AI_CAUTION` without touching `RiskDecision`
- immutability: `MarketBar`, `TradePlan`, `RiskDecision`, `InstrumentSpec` are frozen dataclasses; `PaperAccount` is only exposed via `ai.runtime.snapshot_account`, a deep-copied read-only view
- audit_log: in-memory `AuditLog` records run_id, agent, prompt_version, evidence IDs, response, validation result and provider metadata; no secrets recorded
- real_execution_check: REAL_EXECUTION = DISABLED; `ai/` never imports `execution.paper_broker` or `execution.trade_manager`

## Invariant -> test mapping

| Invariant | Test name |
|---|---|
| AIRequest/AIResponse contract shape and frozen | `test_ai_request_is_frozen`, `test_ai_response_is_frozen` (`test_ai_contracts.py`) |
| grounding: run_id/symbol/agent/as_of mismatch rejected | `test_run_id_mismatch_is_rejected`, `test_symbol_mismatch_is_rejected`, `test_agent_mismatch_is_rejected`, `test_future_as_of_is_rejected` (`test_ai_contracts.py`) |
| grounding: invalid bias/recommendation/confidence rejected | `test_invalid_bias_is_rejected`, `test_invalid_recommendation_is_rejected`, `test_confidence_above_one_is_rejected`, `test_confidence_boolean_is_rejected` (`test_ai_contracts.py`) |
| grounding: ungrounded evidence id rejected | `test_ungrounded_supporting_evidence_is_rejected`, `test_ungrounded_conflicting_evidence_is_rejected` (`test_ai_contracts.py`) |
| provider abstraction is a real interface | `test_provider_is_abstract` (`test_ai_provider.py`) |
| deterministic provider never invents evidence | `test_deterministic_provider_never_invents_evidence_ids`, `test_deterministic_provider_mixed_bias_is_neutral` (`test_ai_provider.py`) |
| cost control: NO_DATA skips provider call | `test_no_evidence_skips_provider_call_and_returns_no_data`, `test_audit_log_skip_is_recorded_without_incrementing_provider_calls` (`test_ai_runtime.py`) |
| failure isolation | `test_provider_exception_is_isolated_as_error` (`test_ai_runtime.py`), `test_provider_failure_is_isolated_and_does_not_crash_the_run` (`test_ai_orchestrator.py`) |
| hallucination: run_id/symbol/timestamp/confidence/evidence_id rejected | `test_hallucinated_run_id_is_rejected_to_error`, `test_hallucinated_symbol_is_rejected`, `test_hallucinated_future_timestamp_is_rejected`, `test_hallucinated_confidence_above_one_is_rejected`, `test_invented_evidence_id_is_rejected` (`test_ai_runtime.py`) |
| audit log content and no secrets | `test_audit_log_records_call` (`test_ai_runtime.py`) |
| PaperAccount cannot be mutated by AI | `test_snapshot_account_does_not_expose_mutable_reference` (`test_ai_runtime.py`) |
| multi-timeframe tagging (not inferred from size) | `test_multi_timeframe_evidence_is_tagged_per_timeframe_not_by_size`, `test_missing_timeframe_is_simply_omitted_not_invented` (`test_ai_agents.py`) |
| no-lookahead purity of request building | `test_no_lookahead_same_t1_input_yields_equivalent_request` (`test_ai_agents.py`) |
| liquidity never fabricates order blocks | `test_order_blocks_are_never_fabricated_when_absent` (`test_ai_agents.py`) |
| macro FACT vs INTERPRETATION separation | `test_fact_interpretation_separation`, `test_low_impact_is_not_flagged_as_elevated` (`test_ai_agents.py`) |
| Setup Reviewer cannot upgrade NO_SETUP/WATCH | `test_no_setup_forces_insufficient_data_and_cannot_be_overridden`, `test_watch_yields_caution_not_executable` (`test_ai_reviewers.py`) |
| Trade Reviewer cannot mutate TradePlan/RiskDecision | `test_cannot_mutate_trade_plan_fields`, `test_cannot_mutate_risk_decision_fields` (`test_ai_reviewers.py`) |
| AI cannot upgrade NO_SETUP/RISK_REJECTED end-to-end | `test_no_setup_stays_no_setup_even_if_ai_recommends_trade`, `test_risk_rejected_stays_rejected_even_if_ai_recommends_trade` (`test_ai_orchestrator.py`) |
| PLAN_READY + adverse AI review => AI_CAUTION, RiskDecision unchanged | `test_plan_ready_becomes_ai_caution_when_setup_reviewer_disagrees` (`test_ai_orchestrator.py`) |
| real component end-to-end flow | `test_real_end_to_end_equity_invalid_maps_to_no_data` (`test_ai_orchestrator.py`, uses real `floor.orchestrator.run`) |
| run_id/as_of/symbol propagation | `test_run_id_as_of_symbol_are_propagated` (`test_ai_orchestrator.py`) |
| audit log spans full run | `test_audit_log_accumulates_calls_across_the_full_run` (`test_ai_orchestrator.py`) |
| no broker/real execution reference in ai/ | `test_no_broker_or_real_execution_reference_in_ai_module` (`test_ai_orchestrator.py`) |

Known debt: no real LLM provider connected (deliberate for this phase), no scheduler, no
durable audit log persistence (deferred to Phase 6C), no Phase 6B UI.

Final status: PASSED; REAL_EXECUTION = DISABLED.
