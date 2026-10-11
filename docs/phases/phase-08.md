# Fase 8 — Execution / Fill / Trade Manager Certification

**Estado:** CERTIFIED (2026-10-10). Merge a main `4974408e443908ceb5723aed13c1eb15a81f3c51` (PR #13, merge commit). Control delegado por el owner; revisión cruzada Codex ↔ Claude Code coordinada por Grok Bot.

**Evidencia:** tests 1426 OK (1 expected failure, ISSUE-012); ruff limpio; CI `lint` + `unit` en verde en PR #13.
- HIGH-8.1 CLOSED (983d2d2) — revisión Codex PASS WITH NOTES.
- M-5 CLOSED (PR #16) — Codex ronda 2 PASS.
- Base de proceso / invariantes de seguridad (PR #17); DEC-8.18a status verifier (PR #18); ISSUE-012 test (PR #19); decisiones (PR #15).
- G-8.INT offline (DEC-8.23): PASS G1, G6, G7, G9, G10, G12, G14; NOT VERIFIED G8, G15; diferidos a Fase 13: G2–G5, G11; G13 = revisiones cruzadas. Sin FAIL.

**Decisiones:** DEC-8.13…8.21, 8.17b, 8.20, 8.18, 8.19, 8.23, 8.18a (ver `docs/decisions.md`). DEC-8.21b PROVISIONAL.

## F08 → tests (mapa preparado por Codex, verificado por Grok Bot)
| ID | Requisito | Tests | Cobertura |
|---|---|---|---|
| F08-T01 | SUBMITTED | `test_execution_observability.py::ExecutionObservabilityTests::test_submitted_then_existing_position_is_explained_and_stable`<br>`test_execution.py::TestExecution::test_journal_sequence_and_no_duplicate_events` | COVERED |
| F08-T02 | PENDING | `test_phase8_execution_characterization.py::PendingLifetimeTests::test_pending_survives_ai_outage_and_fills_later`<br>`test_phase8_adversarial.py::FillTransitionTests::test_submit_fill_then_terminal_and_restart_never_refills` | COVERED |
| F08-T03 | FILLED | `test_execution.py::TestExecution::test_state_machine_filled_terminal_and_open_closed_terminal`<br>`test_phase8_adversarial.py::FillTransitionTests::test_submit_fill_then_terminal_and_restart_never_refills` | COVERED |
| F08-T04 | REJECTED | `test_execution.py::TestExecution::test_current_equity_is_rechecked_at_fill`<br>`test_execution.py::TestExecution::test_post_fill_rr_below_three_is_rejected`<br>`test_execution.py::TestExecution::test_state_machine_pending_rejected` | COVERED |
| F08-T05 | POSITION OPENED | `test_execution.py::TestExecution::test_submit_fill_manage_close_and_idempotency`<br>`test_demo_runner.py::TestDemoRunner::test_open_position_restart_close_without_double_accounting` | COVERED |
| F08-T06 | POSITION CLOSED | `test_execution.py::TestExecution::test_state_machine_filled_terminal_and_open_closed_terminal`<br>`test_demo_runner.py::TestDemoRunner::test_open_position_restart_close_without_double_accounting` | COVERED |
| F08-T07 | SL | `test_execution.py::TestExecution::test_long_normal_stop`<br>`test_execution.py::TestExecution::test_short_normal_stop`<br>`test_execution.py::TestExecution::test_long_stop_first_same_bar` | COVERED |
| F08-T08 | TP | `test_execution.py::TestExecution::test_long_normal_target`<br>`test_execution.py::TestExecution::test_short_normal_target`<br>`test_execution.py::TestExecution::test_long_gap_target` | COVERED |
| F08-T09 | Restart | `test_phase8_process_races.py::RestartDuplicationTests::test_crash_after_submit_commit_restart_never_duplicates`<br>`test_runtime.py::TestStorage::test_paper_account_order_position_survive_restart` | COVERED |
| F08-T10 | Duplicate protection | `test_phase8_process_races.py::LockRaceTests::test_two_processes_same_slot_one_cycle`<br>`test_phase8_process_races.py::RestartDuplicationTests::test_crash_after_submit_commit_restart_never_duplicates`<br>`test_execution.py::TestExecution::test_journal_sequence_and_no_duplicate_events` | COVERED |
| F08-T11 | Multiple positions | `test_execution.py::TestExecution::test_multi_symbol_unrealized_equity_is_preserved` — dos posiciones, una sembrada; verifica equity y aislamiento.<br>`test_phase8_adversarial.py::ConflictTests::test_existing_position_and_pending_block_new_exposure` — bloquea exposición adicional del mismo símbolo. | PARTIAL |
| F08-T12 | Simultaneous setups | `test_phase6_conflict_concurrency.py::ConflictRaceTests::test_b_c_different_setup_ids_same_direction_admit_one`<br>`test_phase6_conflict_concurrency.py::ConflictRaceTests::test_d_opposite_directions_never_hedge`<br>`test_phase6_conflict_concurrency.py::ConflictRaceTests::test_e_cross_symbol_aggregate_cas` | COVERED |
| F08-T13 | Stale data | `test_runtime.py::TestGates::test_freshness_rejects_forming_future_wrong_symbol_and_stale`<br>`test_demo_runner.py::TestDemoRunner::test_no_data_stale_forming_provider_failures_and_diagnostic_no_order` | COVERED |
| F08-T14 | Provider failure | `test_runtime.py::TestRuntime::test_provider_crash_is_durable_and_no_paper_order`<br>`test_runtime.py::TestRuntime::test_ai_stage_crash_after_data_is_durable_and_no_paper`<br>`test_phase8_execution_characterization.py::PendingLifetimeTests::test_pending_survives_ai_outage_and_fills_later` | COVERED |
| F08-T15 | Database failure | `test_phase8_adversarial.py::SqliteFailureMatrixTests::test_failure_points_never_create_phantom_or_duplicate_orders`<br>`test_phase8_adversarial.py::SqliteFailureMatrixTests::test_journal_failure_inside_economic_save_rolls_back_everything`<br>`test_phase8_execution_characterization.py::DatabaseFailureTests::test_finish_failure_is_fail_stop_and_recovered_on_restart` | COVERED |
| F08-T16 | Scheduler restart | `test_phase8_execution_characterization.py::DatabaseFailureTests::test_finish_failure_is_fail_stop_and_recovered_on_restart` — reinicia runner y recupera lock.<br>`test_phase8_adversarial.py::SchedulerAndLockTests::test_same_slot_tick_twice_is_duplicate` — recrea Scheduler en el mismo runtime; no reinicia proceso. | PARTIAL |
| F08-T17 | State recovery | `test_runtime.py::TestStorage::test_durable_claim_and_recovery_no_replay`<br>`test_phase8_adversarial.py::RollbackCompatibilityTests::test_restore_from_copy_is_idempotent`<br>`test_phase8_execution_characterization.py::StartupRecoveryTests::test_orphan_position_without_filled_origin_order_blocks_startup` | COVERED |

PARTIAL aceptados: T11 (una exposición por símbolo por diseño, DEC-6.3/6.4; multi-símbolo cubierto); T16 (reinicio real de servicio se prueba en Fase 11, F11-T11/T12).

Detalle técnico: `V2_PHASE8_EXECUTION.md`, `V2_PHASE8_P86_DESIGN.md`, `V2_PHASE8_P86_HANDOFF.md`, `V2_PHASE8_CERTIFICATION_CHECKLIST.md`.
