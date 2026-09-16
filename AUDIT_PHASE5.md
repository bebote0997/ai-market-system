# Phase 5 Audit

- phase: Fase 5.2 — final execution safety gate
- tests_before: 286
- tests_after: 315 OK
- paper_account_model: starting_equity + realized_pnl + unrealized_pnl
- order_state_machine: PENDING -> FILLED/CANCELLED/REJECTED; terminal states are terminal
- position_state_machine: OPEN -> CLOSED
- fill_policy: first eligible closed bar after plan as_of, using real Open
- stale_plan_policy: bars at or before as_of are cancelled; no pursuit
- contract_multiplier_policy: InstrumentSpec is required for PaperBroker execution; unknown, None, NaN, inf, bool and non-positive multipliers reject; no economic default exists
- post_fill_risk_policy: risk is recalculated from fill, stop, quantity and multiplier; excess risk rejects position
- post_fill_RR_policy: fill-time geometry and R:R >= 3 are rechecked; no level mutation
- timezone_policy: critical execution timestamps must be timezone-aware; naive as_of, bar.timestamp and opened_at fail closed
- opened_at_policy: management requires timestamp > opened_at and then timestamp > last_processed_at
- closed_bar_policy: TradeManager requires is_closed is True and does not mutate forming-bar state
- current_equity_policy: fill risk must fit both current account equity and equity at submission
- side_policy: execution accepts only LONG and SHORT
- OHLC_policy: valid positive finite OHLC and Low <= Open/Close <= High
- symbol_isolation: management bars only affect matching symbol positions
- state_machine: duplicate fills and closed-position reopening are prohibited
- idempotency: repeated run submission returns existing order; repeated fill is ignored
- cost_policy: explicit position cost rate, charged once at close; default zero is paper configuration
- equity_policy: mark-to-market includes contract multiplier and preserves other symbols
- journal_policy: submit, fill, reject/cancel, open, stop/target hit and close events
- no_lookahead: first-bar and sequential timestamp checks
- real_execution_check: REAL_EXECUTION = DISABLED

## Invariant -> test mapping

| Invariant | Test name |
|---|---|
| contract multiplier fallback removed | `test_multiplier_values_fail_closed`, `test_multiplier_pnl_unrealized_and_cost_once` |
| post-fill risk and R:R | `test_current_equity_is_rechecked_at_fill`, `test_post_fill_rr_below_three_is_rejected`, `test_state_machine_pending_rejected` |
| current equity | `test_current_equity_is_rechecked_at_fill` |
| opened_at and ordering | `test_opened_at_guard_duplicate_and_out_of_order`, `test_no_lookahead_cutoff_state_is_stable_and_late_bar_is_ignored` |
| closed bars | `test_forming_bar_does_not_mutate_management_state`, `test_broker_requires_exact_symbol_closed_complete_ohlc` |
| timezone | `test_broker_rejects_naive_as_of_and_bar_timestamp`, `test_manager_rejects_naive_opened_at_and_missing_symbol` |
| symbol isolation and required symbol | `test_multiplier_and_symbol_isolation`, `test_broker_requires_exact_symbol_closed_complete_ohlc` |
| idempotency and terminal states | `test_submit_fill_manage_close_and_idempotency`, `test_state_machine_filled_terminal_and_open_closed_terminal` |
| LONG exits | `test_long_normal_stop`, `test_long_normal_target`, `test_long_gap_stop`, `test_long_gap_target`, `test_long_stop_first_same_bar` |
| SHORT exits | `test_short_normal_stop`, `test_short_normal_target`, `test_short_gap_stop`, `test_short_gap_target`, `test_short_stop_first_same_bar` |
| PnL, multiplier and cost once | `test_multiplier_pnl_unrealized_and_cost_once`, `test_short_gross_pnl_and_unrealized_use_multiplier`, `test_multi_symbol_unrealized_equity_is_preserved` |
| journal and rejection reason | `test_journal_sequence_and_no_duplicate_events`, `test_current_equity_is_rechecked_at_fill` |
| no-lookahead | `test_opened_at_guard_duplicate_and_out_of_order`, `test_no_lookahead_cutoff_state_is_stable_and_late_bar_is_ignored` |
| real component flow | `test_end_to_end_real_risk_broker_manager_account_journal` |

Known debt: durable persistence, partial fills and reconciliation remain deferred. The existing short orchestrator fixture remains `NO_SETUP`; no false `PLAN_READY` claim is made for it.
Final status: PASSED; REAL_EXECUTION = DISABLED.
