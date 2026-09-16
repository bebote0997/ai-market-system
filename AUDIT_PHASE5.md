# Phase 5 Audit

- tests_before: 284
- tests_after: 286 OK
- paper_account_model: starting_equity + realized_pnl + unrealized_pnl
- order_state_machine: PENDING -> FILLED/CANCELLED/REJECTED; terminal states are terminal
- position_state_machine: OPEN -> CLOSED
- fill_policy: first eligible closed bar after plan as_of, using real Open
- stale_plan_policy: bars at or before as_of are cancelled; no pursuit
- contract_multiplier_policy: InstrumentSpec is required for PaperBroker execution; unknown multiplier rejects
- post_fill_risk_policy: risk is recalculated from fill, stop, quantity and multiplier; excess risk rejects position
- post_fill_RR_policy: fill-time geometry and R:R >= 3 are rechecked; no level mutation
- timezone_policy: critical execution timestamps must be comparable; ambiguous input fails closed
- OHLC_policy: valid positive finite OHLC and Low <= Open/Close <= High
- symbol_isolation: management bars only affect matching symbol positions
- state_machine: duplicate fills and closed-position reopening are prohibited
- idempotency: repeated run submission returns existing order; repeated fill is ignored
- cost_policy: explicit position cost rate, charged once at close; default zero is paper configuration
- equity_policy: mark-to-market includes contract multiplier and preserves other symbols
- journal_policy: submit, fill, reject/cancel, open, stop/target hit and close events
- no_lookahead: first-bar and sequential timestamp checks
- real_execution_check: REAL_EXECUTION = DISABLED

Known debt: durable persistence, partial fills and reconciliation remain deferred.
Final status: PASSED; REAL_EXECUTION = DISABLED.
