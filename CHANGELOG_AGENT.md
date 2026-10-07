# Agent Changelog

## 2026-10-06 — V2 Phase 6 P6.1 (conflict engine, durable setup_id, atomic conflict + Risk V2 path)

- Agent: Claude (author; not the reviewer). Branch `v2/phase6-multi-setup-conflict`; starting SHA
  `f83b63c70bfc6ba35e7ecf4afc1c977272b5d97c`; final SHA = the commit that adds this entry.
- New: `execution/conflict_engine.py` (pure classification, `V2_P6_CONFLICT_1`), `execution/conflict_path.py`
  (conflict + Risk V2 + reservation in one guarded save; `CONFLICT_DECISION` rows), `test_phase6_conflict_engine.py`,
  `test_phase6_conflict_concurrency.py` (real multi-process A–L).
- Changed: optional `setup_id` on `PaperOrder`/`PaperPosition` (omitted when None; schema 3), broker fill inherits it,
  `storage/codec.py` optional-identity omission, `execution/risk_reservation.py` extracts `prepare_reservation`
  (Phase 5 behavior identical), `replay/conflict_audit.py` uses the engine, one P6.0 characterization assertion
  explicitly superseded by DEC-6.1, `V2_PHASE6_MULTI_SETUP_CONFLICT.md` P6.1 section.
- Replay reproduces P6.0 exactly (21 / 1,020 / 882 / 17 / pending 0). Runtime unchanged; no flag; REAL DISABLED;
  NAS100 OFF. Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-06 — V2 Phase 6 P6.0 (multi-setup / position conflict: audit, characterization, owner decisions)

- Agent: Claude. Branch `v2/phase6-multi-setup-conflict` from `main` `b3e93dfcff06233a7f0d22748d626e9a4b8d57bf`;
  final SHA = the commit that adds this entry. Audit only; no production code changed; no runtime behavior changed.
- `V2_PHASE6_MULTI_SETUP_CONFLICT.md` (behavior map, data model, setup_id, taxonomy, Risk V2 integration,
  concurrency, observability, replay, DEC-6.1..6.11, P6.1 plan); `test_phase6_conflict_characterization.py`
  (12 tests); `replay/conflict_audit.py` (offline, observational).
- Findings: same-symbol multi-position structurally unsafe (latent HIGH); pending progression coupled to the
  current-cycle analysis (HIGH design constraint); setup_id is opportunity identity, not thesis identity.
- REAL DISABLED; NAS100 OFF; schema 3; runtime OFF. Status: **READY FOR OWNER DECISIONS**.

## 2026-10-06 — V2 Phase 5 P5.1C (certification-blocker correction: durable pending-risk policy identity)

- Agent: Claude (author; not the reviewer). Branch `v2/phase5-risk-engine`; starting SHA
  `a71688ee4279f2257a72d921a8231290a8782b21`; final SHA = the commit that adds this entry.
- HIGH fixed: pending reservations no longer assume 8/7. `PaperOrder.risk_policy_version` (additive, omitted when
  None; schema 3), registry in `core/risk_policy.py`, stamping in `execution/risk_reservation.py`, identity-bound
  reservation and `UNKNOWN_PENDING_RISK_POLICY` in `execution/risk_engine_v2.py`, stamped-order fill binding in
  `execution/paper_broker.py`, codec omission in `storage/codec.py`, audit stamping in `replay/risk_audit.py`.
- Docs: open risk renamed entry-basis loss to stop (no "never understates" claim); P5.1C section C1–C8.
- Tests: reviewer HIGH regression on the real 2.30% cap, restart/reload identity, unknown legacy, fixed-3R happy
  path (1.00/0.75/0.63/0.25%), fill binding, 4 new multi-process cases.
- REAL DISABLED; NAS100 OFF; schema 3; runtime not wired/activated. Status: **READY FOR INDEPENDENT DELTA REVIEW**.

## 2026-10-06 — V2 Phase 5 P5.1 (Risk Engine V2 implementation, DEC-5.1 → DEC-5.8)

- Agent: Claude (author; not the reviewer). Branch `v2/phase5-risk-engine`; starting SHA
  `9c9e9817deaeb1c17f7cf946f5c20b535c8a7113`; final SHA = the commit that adds this entry.
- New: `core/risk_policy.py` (versioned policy `V2_P5_RISK_1`, derived 8/7), `execution/risk_engine_v2.py` (pure
  portfolio-aware Risk: conservative equity, 1% / 100% notional sizing, open + pending + proposed ≤ 2.30%, one per
  symbol, 5% drawdown gate, traceability record), `execution/risk_reservation.py` (atomic reservation = PENDING
  order via B2.3A CAS, run_id idempotency), `replay/risk_audit.py`, `test_phase5_risk_engine.py`,
  `test_phase5_risk_concurrency.py` (real multi-process cases A–E).
- Changed: `execution/paper_broker.py` DEC-5.7 fill money rule for fixed 3R (V1 and policy D unchanged);
  `test_phase5_risk_characterization.py` H1 assertion explicitly superseded; `V2_PHASE5_RISK_ENGINE.md` P5.1 section.
- Replay: the old 1% cap rejected 523/1,940 fills; DEC-5.7 rejects 0 money fills; R:R ≥ 2.50 vs 8/7 disagreements 0.
- REAL DISABLED; NAS100 OFF; schema 3; runtime not wired/activated. Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-06 — V2 Phase 5 P5.0 (Risk Engine V2 audit & owner-decision package)

- Agent: Claude. Branch `v2/phase5-risk-engine` from `main` `3edb64804cfc4b1432dbc79aa7ed3d810be4e558`; final SHA
  = the commit that adds this entry. Audit/design only; no production code changed.
- `V2_PHASE5_RISK_ENGINE.md` (authority map, F05 gap matrix, sizing, portfolio, drawdown, correlation, invalid
  geometry, abnormal market, concurrency, architecture); `test_phase5_risk_characterization.py` (8 tests).
- Finding: the broker's hard-coded 1% money-at-risk cap rejects 27.4% of DEC-4.7-accepted fills in replay.
- REAL DISABLED; NAS100 OFF; schema 3; runtime OFF. Status: **READY FOR OWNER DECISIONS**.

## 2026-10-06 — V2 Phase 4 DEC-4.7 (fixed-3R fill execution floor 2.50R)

- Agent: Claude. Branch `v2/phase4-trade-planner`; starting SHA `de24ccd771dd28a8506fc7be3808ca9ca6bef45a`;
  final SHA = the commit that adds this entry.
- `core/rr_contract.py`: `FILL_LIMITS[POLICY_V2_F3] = 2.50` (execution tolerance; planning stays exactly 3R).
  `execution/paper_broker.py`: V2 fill check with explicit reasons (`fill_rr_below_minimum`,
  `fill_invalid_geometry`) and full fill telemetry; V1 fill gate byte-identical. `replay/fill_audit.py`: strict vs
  DEC-4.7 comparison (vectorized). `test_phase4_fixed_3r.py`: DEC-4.7 boundary matrix (LONG/SHORT, exact 2.50).
- Replay: fill rejection 47.37% -> 1.55% (runtime NEXT_CYCLE model). REAL DISABLED; NAS100 OFF; Phase 4 runtime
  OFF; schema 3; Phase 3 unchanged. Status: **READY FOR INDEPENDENT PHASE 4 REVIEW** (not certified).

## 2026-10-06 — V2 Phase 4 DEC-4.6 (fixed 3R policy) and fill-geometry audit

- Agent: Claude. Branch `v2/phase4-trade-planner`; starting SHA `944f29f40329788a554b8cbe4f781bd4e20d5d56`;
  final SHA = the commit that adds this entry. P4.1B superseded (stopped, not committed).
- `core/rr_contract.py` (`POLICY_V2_F3`, provisional `FILL_LIMITS`), `agents/target_planner.py`
  (`plan_fixed_3r`), `floor/orchestrator.py` (opt-in policy), `riesgo.py` (`crear_configuracion_riesgo_fixed_3r`),
  `ai/provider.py` (contract gate), `execution/paper_broker.py` (fill-geometry trace; V1 unchanged),
  `replay/engine.py` (fill limits), new `replay/fill_audit.py`, `test_phase4_fixed_3r.py`.
- Fill audit: ~47% fill rejection is sign-driven (every adverse tick) under both fill models; DEC-4.7 required.
- REAL DISABLED; NAS100 OFF; Phase 4 runtime OFF; schema 3. Phase 3 not reopened. Status: **DEC-4.7 REQUIRED**.

## 2026-10-06 — V2 Phase 4 P4.1A (Target Policy Lab, offline, DEC-4.5 option a)

- Agent: Claude. Branch `v2/phase4-trade-planner`; starting SHA `36d7fc5da1d15149b9be1de2570c2bb4a643f32d`;
  final SHA = the commit that adds this entry. Offline only; no production code changed.
- New `replay/lab.py` (pre-registered variants D0–D4, split, selection rule; sha256 `81e30c9c…c114`),
  `replay/lab_run.py` (15-minute resumable runner), `replay/lab_report.py` (protocol-ordered report),
  `test_phase4_lab.py` (lookahead and invariant tests).
- Result: D0 0, D1 7, D2 53, D3 5 tradeable of 1,941 valid setups (12 months, 15-minute cadence); D2 selected on
  discovery (32) and confirmed frequency on holdout (21); observational outcomes stop-dominated. Status:
  **READY FOR OWNER DECISION**. REAL DISABLED; NAS100 OFF; Phase 4 runtime OFF; schema 3.

## 2026-10-06 — V2 Phase 4 P4.1 (Policy D, single R:R contract, replay foundation)

- Agent: Claude. Branch `v2/phase4-trade-planner`; starting SHA `f862176d6660b81a8574d8ba8007fb1504b7e402`
  (P4.0); final SHA = the commit that adds this entry. Owner decisions DEC-4.1..4.4 recorded.
- New `core/rr_contract.py` (single R:R authority), `agents/target_planner.py` (Policy D, explicit opt-in),
  `replay/` (separate Replay Store, acquisition, lookahead-free engine, comparison). Changed: `riesgo.py`
  (recompute R:R for every plan; Phase 4 config), `floor/orchestrator.py` (`planner_policy`, default V1),
  `ai/provider.py` + `ai/agents/trade_reviewer_ai.py` (policy-aware deterministic gate; V1 unchanged),
  `execution/paper_broker.py` (`rr_policy`, default V1 expression), `core/contracts.py` and `runtime/review.py`
  (additive fields), `.gitignore` (`data/replay/`). Runtime cannot select Policy D.
- Fixed: Risk declared-R:R trust (DEC-4.2). Tests: new Phase 4 suites 28/28; oracle updated for DEC-4.2;
  mutations 12/12 killed. 12-month real-data replay: Policy D 0 plans (HIGH finding, owner decision).
- REAL DISABLED; NAS100 OFF; schema 3; System Health inactive. Status: **READY FOR INDEPENDENT REVIEW**;
  Phase 4 NOT certified.

## 2026-10-06 — V2 Phase 4 P4.0 (Trade Planner + Target/R:R design & owner decision package)

- Agent: Claude. Branch `v2/phase4-trade-planner` from `main` `b8b7493d91b37156e653d8ce7a8850a9abd82fb0`;
  final SHA = the commit that adds this entry. Design/audit only: no production code changed.
- `V2_PHASE4_TRADE_PLANNER.md`: V1 audit, evidence classification, invalidation-first design, policies
  0/A/B/C/D, bands, scenarios, fail-closed matrix, replay/metrics/certification plans, 4 owner decisions.
- `test_phase4_v1_planner_oracle.py`: frozen V1 planner/Risk/PAPER/AI/fill-gate outputs (6 tests).
- Full suite once 783 pass / 0 fail / 0 skip. REAL DISABLED; NAS100 OFF; V2 runtime not authorized;
  System Health inactive; schema 3. Status: **READY FOR OWNER DECISION**; P4.1 NOT STARTED.

## 2026-10-05 — V2 Phase 3 P3.2 (final regression, E2E, certification candidate)

- Agent: Claude. Branch `v2/phase3-setup-validator`; starting SHA
  `55e1585e9259d3dd456777d08ec31b64e482302a` (P3.1 accepted); final SHA = the commit that adds this entry.
- LOW fix (metadata only): evidence references are VALID only for lineage-valid reports whose bar is
  aligned to its timeframe and closed by as_of; otherwise UNAVAILABLE/INVALID with a reason, no bar.
- F03-T09: `test_setup_validator_regression.py` (verbatim V1 oracle, 17 cases, real Planner/Risk/AI).
  New `test_phase3_e2e.py` (real runtime). P3.1 `test_13` fixed (it compared None == None).
- Skip explained: the git-dependent V1 rollback test (environmental). Carry-forward: same-setup re-entry
  (owner policy), invalidation-window aging (pre-existing).
- Focused 29/29; Phase 2 targeted 143/143; mutations 10/10 killed. REAL DISABLED; NAS100 OFF; flag OFF;
  System Health inactive; schema 3. Status: **READY FOR INDEPENDENT CERTIFICATION** (Phase 3).

## 2026-10-05 — V2 Phase 3 P3.1 (Setup Validator V2 core)

- Agent: Claude. Branch `v2/phase3-setup-validator` from certified `main`
  `c7aaafb4ff0fe35c6985d7089d8ea85a144b2121`; final SHA = the commit that adds this entry.
- `agents/setup_validator.py`: frozen V1 decision (byte-identical) + structured explanation (checks,
  details, evidence refs, setup identity); `core/contracts.py`: optional `SetupAssessment.explanation`;
  `runtime/observability.py`: setup_id delegates to the validator (same values); `runtime/review.py`:
  review report carries the explanation. No schema change; `storage/database.py` untouched.
- Confidence not implemented (no defensible methodology). F03-T09/T11 not started.
- Tests: new `test_setup_validator_v2.py` 15/15; mutations 5/5 killed; full suite 767 pass / 0 fail /
  0 skip. REAL DISABLED; NAS100 OFF; V2 runtime activation not authorized; System Health inactive.
- Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-05 — V2 Phase 2 B2.3D regression preservation (test-only)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `05afa4e0c64d5731d4f3a8fcfd31b7b237d9526c`; final SHA = the commit that adds this entry.
- New permanent `test_phase2_e2e.py`: the B2.3D deterministic end-to-end scenario, assertions
  unchanged from the certification run. No production code change.
- B2.3D PASS. H02 CLOSED (owner-accepted) for the V2 implementation under P1. Runtime activation NOT
  AUTHORIZED; flag OFF by default. Schema 3; REAL DISABLED; NAS100 OFF; System Health inactive.
- Status: **READY FOR FINAL INDEPENDENT PHASE 2 CERTIFICATION**.

## 2026-10-05 — V2 Phase 2 B2.3C (runtime pending orders via CurrentCycleGate, flag OFF)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `a5b1ce51a1fc109c1a43df4189f207c66f69012a`; final SHA = the commit that adds this entry.
- `runtime/service.py`: with the flag ON, pending orders progress only through B2.2
  `gate_pending_orders` with a `CurrentCycleGate` built from this cycle's real V1 values and the
  committed current Evidence bar; missing bar / evidence error / persistent STALE fail closed. The
  V1 session check is computed once into `session_open` (same expression). Flag OFF unchanged.
- Tests: new `test_runtime_pending_gate.py` 14/14 (real-process crash and contention); isolation
  tests updated. Mutations 6/6 killed; flag-OFF old/new byte-identical; full suite once 751 pass /
  0 fail / 0 skip.
- Schema 3; REAL DISABLED; NAS100 OFF; System Health inactive; H02 PARTIAL; B2.3D NOT STARTED.
  Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-05 — V2 Phase 2 B2.3B (Evidence + position catch-up runtime wiring, flag OFF)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `5258e125f03eed7dda711ad9af090858cc5c0a83`; final SHA = the commit that adds this entry.
- `runtime/config.py`: `v2_position_catch_up` (default False, never from env) + `market_evidence_path`.
- `runtime/service.py`: with the flag ON, snapshot ingestion into the Evidence Store, then B2.1
  catch-up replaces the newest-bar TradeManager step; evidence failure / persistent STALE fails
  closed for PAPER economics (no fallback, no pending progress, no submission).
- Tests: new `test_runtime_catch_up.py` 15/15 (real-process crash and contention); isolation tests in
  `test_market_evidence.py`, `test_position_catch_up.py`, `test_stale_safe_writers.py` updated to the
  authorized flag-gated wiring. Mutations 6/6 killed; flag-OFF old/new byte-identical; full suite once
  737 pass / 0 fail / 0 skip.
- Schema 3; REAL DISABLED; NAS100 OFF; System Health inactive; H02 PARTIAL; B2.3C NOT STARTED.
  Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-05 — V2 Phase 2 B2.3A (stale-safe runtime PAPER writers)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `a261f62aea8b52e4edb1c905c920693ecd35d30a`; final SHA = the commit that adds this entry.
- `runtime/service.py`: all runtime PAPER writers (bootstrap, legacy TradeManager, legacy pending,
  submit) use the B2.1 `expected_state` guard via `_guarded_paper_write`; one fresh recompute on
  STALE; submit fails closed as `STALE_PAPER_STATE` on changed eligibility/equity; later decisions
  never use a stale broker. Fixes a V1 cross-process lost update (XAU/EUR sharing one account).
- New `test_stale_safe_writers.py` 12/12 (pre-fix fails 9/12); mutations 4/4 killed; single-writer
  old/new equivalence byte-identical; full suite once 722 pass / 0 fail / 0 skip.
- No Evidence/catch-up/gate wiring; no flag; schema 3; System Health inactive; REAL DISABLED;
  NAS100 OFF; H02 PARTIAL. Status: **READY FOR INDEPENDENT REVIEW**.

## 2026-10-05 — V2 Phase 2 B2.2 F1 fix (fail closed on missing AI final status)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `4c7c956478acd24b5c26ccfa2acf8636c811d5e4`; final SHA = the commit that adds this entry.
- F1: `CurrentCycleGate.passed()` now requires a non-empty `str` `ai_final_status` before the
  unchanged V1 deny-list; None/empty/non-str never authorize evaluation. Tests `test_7b`/`test_7c`.
- Focused 26/26; B2.1 28/28; mutations 2/2 killed; full suite once 710 pass / 0 fail / 0 skip.
- No other changes. REAL DISABLED; NAS100 OFF; schema 3; H02 PARTIAL; B2.3 NOT STARTED.
- Status: **READY FOR F1-ONLY INDEPENDENT RE-REVIEW**. Phase 2 IN PROGRESS / NOT CERTIFIED.

## 2026-10-05 — V2 Phase 2 B2.2 (cycle-gated pending orders, isolated)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `8e880bfa09bb724c75eb5cd82de0534fc46fab63`; final SHA = the commit that adds this entry.
- Owner decision P1 APPROVED: no current cycle gate = no pending-order evaluation.
- Scope: new `execution/pending_order_gate.py` (`CurrentCycleGate`, `gate_pending_orders`), new
  `test_pending_order_gate.py`, Phase 2 doc section. Not wired into the runtime. No schema change.
- Intermediate bars journaled idempotently as `PENDING_NOT_EVALUATED` / `NO_CURRENT_CYCLE_GATE` in
  the existing journal; only the current gate's own bar reaches unchanged `process_next_bar`, only
  after `order.as_of`; stale gates are not current; evaluation persisted via B2.1 guarded save.
- Tests: focused 24/24; B2.1 28/28; mutations 4/4 killed; full suite once 708 pass / 0 fail / 0 skip.
- Trading semantics unchanged (fill/stop/target/quantity/economics/gates). REAL DISABLED; NAS100 OFF;
  H02 PARTIAL; B2.3 NOT STARTED (opt-in stale guard recorded as B2.3 activation gate).
- Status: **READY FOR INDEPENDENT REVIEW**, not accepted. Phase 2 IN PROGRESS / NOT CERTIFIED.

## 2026-10-05 — V2 Phase 2 B2.1 P1 concurrency correction

- Agent: Codex. Branch `v2/phase2-market-evidence`; clean/fetched starting SHA
  `8c99d7dfd1b726443c85b4ebc5ccb0004dec7579`; final SHA = this corrective commit.
- Reproduced A-computes / B-commits / A-resumes before production edits: two closed trades and
  POSITION_CLOSED events; account realized -10 versus persisted trade sum -20.
- Fix: immutable pre-computation PAPER snapshot compared with durable state inside the existing
  `save_paper` BEGIN IMMEDIATE. Mismatch returns False before writes; catch-up discards the computed
  transition and returns STALE. Whole-account/object comparison also excludes cross-symbol lost
  updates. SQLite serializes processes; no Python-only lock or Evidence Store transaction coupling.
- Files: `execution/position_catch_up.py`, narrow schema-neutral `storage/database.py` opt-in guard,
  new `test_position_catch_up_concurrency.py`, narrow `test_system_health.py` compatibility assertion,
  this changelog and `V2_PHASE2_MARKET_EVIDENCE.md`. Original B2.1 tests unchanged.
- Focused 28/28; includes two independent processes, progress/close, crash holding write transaction,
  repeated restarts, PnL reconciliation, other-symbol progress and bidirectional transaction isolation.
  Exactly 3 scratch mutants detected: no revalidation, stale watermark accepted, duplicate close path.
- Full local suite once: 683 pass / 1 failure / 0 skipped, 684 total. Only failure was the historical
  whole-file frozen hash gate. Updated it to permit only the authorized opt-in addition while keeping
  the original hash for every remaining byte; targeted compatibility rerun 2/2 PASS. No production
  edits after full run and no full rerun. Actual frozen V1 code also opens a guarded-close DB read-only
  and read-write, schema 3, one close/event, PnL -10 and equity 9990. Final-SHA Linux push CI pending.
- Windows sandbox temp-permission adjustment confined to external launcher; no skipped tests.
- Comparability: unchanged single-writer SL/TP/economics; removes invalid duplicate concurrent effects.
  No historical data repair. TradeManager, Paper Broker/pending orders, Risk, AI, Planner, providers,
  freshness, strategy, config and runtime unchanged; schema 3 preserved. REAL DISABLED; NAS100 OFF;
  H02 PARTIAL; B2.2/B2.3 NOT STARTED. No PR/merge/deploy. Phase 2 IN PROGRESS / NOT CERTIFIED.
- Status: **READY FOR INDEPENDENT RE-REVIEW**, not accepted/certified.

## 2026-10-05 — V2 Phase 2 B2.1 (position catch-up core, isolated)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `e12797701eb4184c47837bbe0c83388a86db60c2`; final SHA = the commit that adds this entry.
- Scope: `execution/position_catch_up.py` (`catch_up_position`), `test_position_catch_up.py`
  (19 tests), Phase 2 doc section. Not wired into the runtime.
- Invariant: per (position, closed 5m bar) either no effect commits and the bar stays eligible, or
  all effects plus `last_processed_at` commit in one existing `Store.save_paper` transaction.
  Watermark from the trading DB only; evidence read-only; fail closed on evidence failure.
- Tests: focused 19/19 (A–R incl. real-process crash before/inside/after a bar transaction and
  repeated crashes). Scratch mutations 5/5 detected (newest-bar-only, reversed ordering, watermark
  check removed, duplicate bar allowed, latest-bar fallback on evidence failure).
- Unchanged: `storage/database.py`, `execution/trade_manager.py`, `execution/paper_broker.py`,
  runtime, Risk, AI, Setup, Planner, providers, pending-order eligibility; trading DB schema 3.
- Safety: REAL EXECUTION = DISABLED; NAS100 = OFF. H02 PARTIAL. B2.2 and B2.3 not started.
  No PR, no merge, no deploy. Phase 2 IN PROGRESS / NOT CERTIFIED.

## 2026-10-05 — V2 Phase 2 Batch 1 final store hardening (M1 URI alias, M2 effective schema)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `dd9459f137b06589e869e847387afed1e12ada35`; final SHA = the commit that adds this entry.
  Independent re-review of `dd9459f`: M3 closed; M1 and M2 reopened (MEDIUM).
- M1: reproduced `trading_floor.db#evidence` initializing an empty protected `trading_floor.db`
  (raw path interpolated into a SQLite URI). `EvidenceStore` now resolves the path once, validates
  that target and opens exactly it: plain filename for read-write, `Path.as_uri()` (percent-encoded)
  for read-only; SQLite's `PRAGMA database_list` must report the same target before any write.
- M2: the schema contract now also refuses partial/extra UNIQUE indexes, partial or expression
  indexes, hidden/generated columns, foreign keys, affinity changes and (by a literal/comment-free
  keyword scan of the table definitions) CHECK, ON CONFLICT, non-BINARY COLLATE, STRICT,
  WITHOUT ROWID, AUTOINCREMENT. Harmless syntactic variants still open.
- Tests: focused 47/47 (was 43). Full suite 656/656 pass, 0 failed, 0 errors, 0 skipped. Scratch
  mutation checks 12/12 detected (raw URI, fragment bypass, partial indexes, CHECK, extra UNIQUE,
  triggers, collation, ON CONFLICT, generated columns, unsafe non-unique indexes, foreign keys,
  affinity).
- Unchanged: M3 crash tests, owner decisions A–E, Batch 2 trading-side idempotency gate (HIGH,
  OPEN; pending-order gate reason reconfirmed), H02 **PARTIAL**. `storage/database.py` unchanged,
  trading DB schema 3, no runtime wiring, no trading/risk/Paper Broker/provider/freshness/session/
  cadence change. REAL EXECUTION = DISABLED; NAS100 = OFF. No deploy, no merge, no PR, Batch 2 not
  started. Status: **PHASE 2 — IN PROGRESS / NOT CERTIFIED**.

## 2026-10-05 — V2 Phase 2 Batch 1 review fixes (M1–M3, decisions A–E)

- Agent: Claude. Branch `v2/phase2-market-evidence`; starting SHA
  `817a35947b305de6524b593c008d1c5fecc03c4a`; final SHA = the commit that adds this entry.
- M1: `EvidenceStore` refuses the trading DB by name (case-insensitive, after link/`..`
  resolution, Windows trailing dot/space and stream suffix ignored) and by identity (same file as a
  sibling trading DB; initialized trading DB under any name), before any connection or write,
  including an existing empty `trading_floor.db` / `TRADING_FLOOR.DB`.
- M2: an existing sidecar must match the schema contract read from SQLite metadata (columns, types,
  NOT NULL, primary-key membership, exact UNIQUE/PRIMARY KEY column sets with BINARY collation, no
  triggers); otherwise it is refused on open and left unchanged. Equivalent schemas are accepted.
- M3: the old pre-commit crash test actually crashed in the duplicate-T0 transaction; renamed to
  what it proves. New real-process test crashes after a genuine INSERT and before COMMIT (first
  and middle new bar); restart commits the bar exactly once, nothing lost or duplicated.
- `V2_PHASE2_MARKET_EVIDENCE.md`: owner-approved decisions A–E replace the open questions; Batch 2
  trading-side idempotency recorded as an OPEN HIGH gate (pending orders have no durable per-bar
  progress); V1 defect and `contiguous` (ingestion continuity != market-history completeness)
  stated precisely. H02 remains **PARTIAL**.
- Tests: focused 43/43 (was 39). Full suite 652/652 pass, 0 failed, 0 errors, 0 skipped. Scratch
  mutation checks: 8/8 detected (case-insensitive refusal, empty trading DB initialization, column
  validation, uniqueness validation, defective crash test, trigger check, hard-link identity,
  collation).
- Safety: only `storage/evidence_store.py`, its test and the Phase 2 doc changed (plus this entry).
  `storage/database.py` unchanged, trading DB schema 3, no runtime wiring, no trading, risk, Paper
  Broker, provider, freshness, session or cadence change. REAL EXECUTION = DISABLED; NAS100 = OFF.
  No deploy, no merge, no PR, Batch 2 not started. Status: **PHASE 2 — IN PROGRESS / NOT CERTIFIED**.

## 2026-10-05 — V2 Phase 2 Batch 1 (Market Evidence Engine foundation + H02 evidence processing)

- Agent: Claude. Branch `v2/phase2-market-evidence` (worktree `ai-market-system-phase2-evidence`);
  starting `main` SHA `1f3362f47cf2439f7d1c6df844a2a9f7656dee1b`; final SHA = the commit that adds
  this entry.
- Scope: isolated V2 market-evidence foundation. `data/market_evidence.py` (`MarketBar` contract,
  V1-frame adapter, `MarketEvidenceEngine`), `storage/evidence_store.py` (isolated MARKET EVIDENCE
  SQLite sidecar, application_id "V2ME", schema 1), `test_market_evidence.py` (39 tests),
  `V2_PHASE2_MARKET_EVIDENCE.md` (V1 path audit, design, limitations, owner decisions).
- H02 status: **PARTIAL**. Evidence processing complete and proven: every new closed bar, per
  stream, chronological, idempotent across overlap/retry/duplicate slot/restart, one bar per
  transaction, real-process crash/restart tests, gaps/late/revised bars recorded and never
  fabricated or overwritten. Not done: applying intermediate bars to 5m position management,
  SL/TP, pending orders and fills (runtime integration; owner decisions in the Phase 2 doc).
- Persistence decision: isolated sidecar; `trading_floor.db` not migrated (schema 3, byte-identical
  in tests).
- Tests: baseline 609/609 pass, 0 skipped. Focused 39/39. Full suite 648/648 pass, 0 failed,
  0 errors, 0 skipped. Scratch mutation testing (no file modified): newest-bar-only, reversed
  chronology, duplicate processing, in-memory-only recovery, skipped middle bar, duplicate commit
  after restart and forming-bar commit are all caught.
- Safety: no runtime wiring; no change to runtime, providers, config, freshness, sessions, cadence,
  setup/AI/prompts, risk, Paper Broker, execution, PnL or existing tests. SYSTEM HEALTH untouched
  and inactive. REAL EXECUTION = DISABLED; NAS100 = OFF. No deploy, no merge, no PR.
  Status: **PHASE 2 — IN PROGRESS / NOT CERTIFIED**.

## 2026-10-05 — V2 Phase 1 final PR polish (documentation + test only)

- Agent: Claude. Branch `v2/phase1-system-health`; starting SHA
  `0b6b6936965d704c9a6a9579664726345ee4d588`; final SHA = the commit that adds this entry.
- From the final independent re-review; no production code changed.
  1. `V2_PHASE1_SYSTEM_HEALTH.md`: H04 corrected to **PARTIALLY SATISFIED BY PHASE 1**
     (liveness/progress separation satisfied; bounded supervisor/recovery/shutdown behavior still
     open, mandatory, carried forward). AI-provider timeout observation consistency recorded as a
     second blocker before SYSTEM HEALTH runtime activation, next to synchronous SQLite latency.
  2. `test_phase1_hardening.py`: one regression test pins the existing timeout retry behavior
     (3 attempts at `retries=2`, configured timeout, unchanged backoff, final `CONNECTION_ERROR`)
     for every health-sink mode, closing the reviewer's surviving `timed_out` retry mutation.
  3. This changelog: Phase 1 Batch 1–3 history added from git evidence.
- Safety: REAL EXECUTION = DISABLED; NAS100 = OFF; trading DB schema 3. No deploy, no merge,
  Phase 2 not started, Phase 1 not certified.

## 2026-10-05 — V2 Phase 1 final hardening (SYSTEM HEALTH semantics)

- Agent: Claude. Branch `v2/phase1-system-health`; starting SHA
  `0a9687b9a78b8466903f29a667dcefc130d3116b`; final SHA = the commit that adds this entry.
- Purpose: three authorized observability fixes before final independent re-review.
  1. An explicitly `NOT_ALIVE` heartbeat never projects `HEALTHY` (H04: liveness != progress).
  2. A real OpenAI transport timeout is classified `TIMEOUT`, not `CONNECTION_ERROR`
     (`OpenAIProviderError.timed_out` evidence; provider kind, retries and exceptions unchanged).
  3. `NOT_CONFIGURED` is recorded as an `UNKNOWN` configuration state, never `HEALTHY` and never
     `UNKNOWN_PROVIDER_FAILURE`.
- Tests: `test_phase1_hardening.py` (22 focused tests: liveness A–E, timeout/connection/rate/
  billing/model/unknown classification, `NOT_CONFIGURED`, observer-failure isolation). Full suite
  608/608 pass, 0 skipped.
- Owner decisions recorded in `V2_PHASE1_SYSTEM_HEALTH.md`: H04 → Phase 1 (code/test evidence);
  H02 → mandatory Phase 2; H15 and deploy/build/config/run traceability → mandatory transversal V2;
  F01-T13 → Phase 1 representation only, Email Notification Engine → Phase 9; synchronous health
  SQLite persistence → blocker before SYSTEM HEALTH runtime activation.
- Safety: REAL EXECUTION = DISABLED; NAS100 = OFF; `storage/database.py` and trading DB schema 3
  unchanged; no trading, risk, execution or runtime behavior changed. No deploy, no merge,
  Phase 2 not started. Status: IMPLEMENTATION COMPLETE — PENDING FINAL CERTIFICATION.

## 2026-10-04 — V2 Phase 1 Batches 1–3 (history recorded 2026-10-05 from git)

Branch `v2/phase1-system-health` from `main` @ `2075e7f`. Test counts are Phase 1 test methods in
`test_system_health.py`, `test_health_probes.py` and `test_phase1_isolation.py` at each commit.

- `894def6` — Batch 1, SYSTEM HEALTH core (F01-T11 foundation, F01-T15..T19): per-component
  status, last success/error/check, consecutive errors, latency, heartbeat kept separate from
  progress, typed provider errors with legacy-name normalization, sanitized reasons. Initially an
  additive trading DB migration v3 → v4 (superseded by `43fdfc6`). 25 tests.
- `fbb883a` — Batch 1 review fix: `MODEL_UNAVAILABLE` and `CONNECTION_ERROR` kept as canonical
  error classes instead of `UNKNOWN_PROVIDER_FAILURE`. 27 tests.
- `43fdfc6` — Batch 1 owner decision: health persistence moved to an isolated sidecar
  (`storage/health_store.py`, application_id `V2HS`, schema 1) that refuses the trading DB;
  v3 → v4 migration reverted, `storage/database.py` byte-identical to the frozen V1 baseline
  (schema 3); frozen V1 code proven to open the trading DB after sidecar use. 30 tests.
- `a07ee5a` — Batch 2, component probes (F01-T01..T13, F01-T14 projection prep): read-only probes
  of trading DB evidence recorded only to the sidecar; email reported as `NOT_IMPLEMENTED`;
  exception-isolated observers not wired into the runtime; `record_state` for non-error
  conditions. 53 tests.
- `0a9687b` — Batch 3: passive no-op-by-default hooks (`runtime/health_hooks.py`) after the
  existing market-data verdict (F01-T02) and around `OpenAIProvider.generate` (F01-T04, result
  and exception unchanged); `ComponentErrorType`; read-only dashboard projection; F01-T20 PAPER
  scenario identical across six health modes. 62 tests.
- Safety throughout: REAL EXECUTION = DISABLED; NAS100 = OFF; no trading, risk, provider or
  execution behavior change; health sink never installed in the runtime; no deploy, no merge.

## 2026-10-02 — ISSUE-001/005 test contract and governance

- Reproduction: the full 523-test baseline suite had two failures because the
  cloud-runner test fixtures omitted `AI_FLOOR_GIT_COMMIT`; the runner rejected
  startup before the intended assertions.
- Change: cloud-runner fixtures now supply the full SHA of the commit that
  introduced the official freeze document. Added regression coverage that
  missing or malformed freeze identity remains fail-closed. No runtime code or
  CI workflow behavior changed.
- Governance: documented required `main` branch rules, independent review,
  successful `unit` CI, and PR/SHA/CI/deploy evidence. Added a PR template.
  GitHub branch protection remains an administrator setting and must be enabled
  before ISSUE-005 can be considered closed; no Render or production settings
  were changed.
- Comparability: test fixtures and governance documentation only; no trading,
  risk, execution, provider, prompt, or runtime behavior changed.

## 2026-09-25 — Authorized PAPER restart repair during Slack routing

- Reproduction: after a configuration-only redeploy of frozen commit 4428fc20,
  cloud startup rejected the existing experiment because experiment_not_started
  was required on every boot. User explicitly authorized this startup-only fix.
- Change: runtime/cloud.py accepts an existing experiment only when its freeze
  identity, account and durable run metadata match the active configuration.
  All other infrastructure/provider checks and the exclusive runner lock remain.
- Preservation: no trading logic, strategy, prompts, sizing, providers, sessions,
  cadence, freshness, economics or SQLite records are manually modified. The
  original freeze identity and start timestamp remain unchanged. The new commit
  is an operational repair, not a new trading baseline; record its deploy SHA
  separately from AI_FLOOR_GIT_COMMIT (the original experiment identity).
- Validation: baseline 509 tests pass with the required freeze environment;
  regression reproduces the rejected resume, then 513 tests pass. Existing
  Windows temporary-directory cleanup warnings occur after the successful suite.
  New tests cover unchanged durable state, identity/configuration drift and
  missing account/credentials. Production table hashes are compared after deploy.
- Comparability: no decision/execution rule changes. Restart resets process caches;
  repair is performed outside trading sessions. Any downtime crossing a scheduled
  slot must be recorded rather than backfilled. Do not clear experiment_started.

## 2026-09-19 — Experiment lifecycle

- `agent`: GitHub Copilot
- `task`: Añadir lifecycle durable e idempotente para el primer arranque autorizado del cloud runner.
- `lifecycle`: preflight es read-only; la marca se realiza solo tras adquirir el slot exclusivo y conserva `experiment_started_at_utc`, baseline SHA y freeze SHA.
- `safety`: no se modificaron estrategia, riesgo, prompts, proveedores, sesiones, cadence, freshness ni economía; no se añadió configuración que active Render, runner o scheduler.

## 2026-09-19 — Freeze oficial del experimento PAPER

- `status`: EXPERIMENT_FROZEN; baseline f5032baeb87766ad74905093c1b4195117995092.
- `economics`: USD, equity 10000, comisión/spread adicional/swap 0; fill al Open
  siguiente barra elegible y gaps actuales, aprobados expresamente por el usuario.
- `policy`: Solo fixes técnicos que invaliden experimento/integridad; cambios con
  impacto económico o decisional requieren evaluar nueva baseline y reinicio.
- `validation`: 507 tests OK; diff check y secret scan PASS. Este commit es
  exclusivamente documental y no cambia el ejecutable probado.
- `activation`: Runner/scheduler apagados; experimento no iniciado; Render sin
  desplegar la nueva baseline. Ver EXPERIMENT_FREEZE.md para pasos pendientes.

## 2026-09-19 — Auditoría final y preparación de freeze PAPER

- `agent`: Codex.
- `base`: f9ae6ce79deb9baaa0dab0faa0271e462599d843.
- `bugs`: Frescura/sesión solo por slot, ausencia de contratos en cloud,
  recuperación incompleta tras reinicio rápido, awareness FXMacroData omitido,
  y SESSION_SKIPPED contado por DAILY_SUMMARY. Reproducidos con fixtures offline.
- `fixes`: Doble gate temporal con reloj real; recuperación reciente solo bajo
  lock cloud exclusivo; awareness y conteo corregidos. Contratos PAPER aprobados
  con multiplier=1 y quantity grid inferior, sin modificar Risk Engine ni precios.
- `validation`: 489 tests baseline; 507 tests finales OK (18 nuevos);
  ver AUDIT_FREEZE.md y PAPER_EXECUTION_SPEC.md.
- `economics`: Usuario confirmó USD, equity 10000, comisión/spread adicional/swap
  0, fill al Open de siguiente barra elegible y gaps SL/TP existentes.
- `state`: Preparado para freeze oficial de código; sin activar runner/scheduler/
  experimento. Render sigue desplegado en f3af0c3; no se despliega en esta misión.

## 2026-09-19 — DAILY_SUMMARY y validación de notificaciones

- `agent`: Codex.
- `task`: Resumen del día UTC anterior completo desde SQLite: ciclos/símbolo,
  setups/riesgo, órdenes/posiciones PAPER, PnL/equity, problemas de proveedor,
  macro HIGH y estado operativo persistido. Contrato y ejemplo en DAILY_SUMMARY.md.
- `delivery`: Snapshot confirmado antes de Slack; deduplicación por día durable,
  incluso concurrente y tras reinicio; transporte fallido aislado del trading.
- `scope`: Solo supervisión y tests; sin cambios en estrategia, agentes/prompts,
  riesgo, gates, providers, sesiones ni ejecución PAPER. Sin activar experimento.
- `validation`: Baseline aislada 480 tests; final 489 tests OK, con 9 nuevos
  tests offline; Slack falso; git diff --check y secret scan por patrones.
- `workspace`: Cambios previos no committed de proveedores excluidos del commit.

## 2026-09-19 — Cloud preflight para FXMacroData certificado

- `agent`: Codex.
- `task`: Reconocer `fxmacrodata` como proveedor macro certificado cuando su clave existe, manteniendo `none` solo para infraestructura sin readiness de experimento.
- `safety`: PAPER only; runner/scheduler y experimento siguen apagados; ningún otro gate se relajó.
- `tests`: añadidos casos de FXMacroData con y sin credencial y preservado el caso `none`.

## 2026-09-19 — FXMacroData macro provider

- `agent`: Codex.
- `task`: Integrar y certificar calendario USD/EUR, announcements, predictions, changes y research/panel con caché, errores explícitos y no-lookahead por fetch/publicación/vintage.
- `decision`: `FXMACRODATA_CERTIFIED` live; adapter configurable y todavía no activo. El Blueprint conserva macro `none`.
- `safety`: runner/scheduler `0`, PAPER only; sin experimento, commit ni push.
- `tests_after`: 485 OK; certificación live PASS; `git diff --check` y secret scan OK.

## 2026-09-18 — Infraestructura cloud con macro diferido

- `agent`: Codex.
- `task`: Separar readiness de infraestructura y experimento, fijar macro efectivo `none`/`NO_DATA`, reforzar el gate del supervisor/runner, conservar una sola autoridad PAPER, redacción y límite de exportaciones, y retirar Finnhub de las variables del Blueprint activo.
- `decision`: INFRA_READY para despliegue controlado con montaje/entorno simulados; EXPERIMENT_READY=false por MACRO_PROVIDER_NOT_CERTIFIED. EODHD Free HTTP 403, sin adapter nuevo ni más consultas macro.
- `safety`: scheduler y runner apagados, real execution deshabilitada, sin despliegue, experimento, commit ni push.
- `tests_after`: 470 OK; `git diff --check` y secret scan OK; preflight simulado INFRA_READY/experiment_ready=false. Ver `AUDIT_POST_PHASE7.md`.

## 2026-09-18 — Evaluación del híbrido macro oficial

- `agent`: Codex.
- `task`: Implementar `OfficialMacroProvider` sobre calendarios ICS BLS/BEA/Eurostat y RSS Fed/BCE, con procedencia, precisión temporal, caché, aislamiento de fallos y evidencia en `ReviewReport`.
- `decision`: OFFICIAL_MACRO_INSUFFICIENT. BEA, Eurostat, Fed y BCE accesibles; BLS Access Denied. Los RSS monetarios no ofrecen agenda futura FOMC/BCE; Eurostat observado publica solo fecha. USD y EUR siguen PARTIAL.
- `safety`: Finnhub y FRED inactivos; macro `none` en Blueprint, scheduler apagado; sin despliegue, experimento, commit ni push.
- `tests_after`: 467 OK; `git diff --check` y secret scan OK; preflight NOT_READY por gate macro y entorno local. Ver `AUDIT_POST_PHASE7.md`.

## 2026-09-17 — Preparación cloud posterior a Fase 7

- `agent`: Codex.
- `task`: Preparar Render, persistencia durable, dashboard privado, Slack, evidencias compartidas y Finnhub Economic Calendar sin activar el experimento.
- `tests_before`: 447 OK.
- `tests_after`: 455 OK offline.
- `status`: READY_FOR_CLOUD_SETUP; Finnhub live sin certificar por HTTP 403 con la clave presente.
- `safety`: PAPER, scheduler y runner apagados; sin despliegue, mensajes Slack reales, commit ni push.

## 2026-09-17 — Evaluación FRED/ALFRED

- `agent`: Codex.
- `task`: Evaluar oficialmente FRED/ALFRED como fuente macro única para XAUUSD/EURUSD y endurecer el gate temporal.
- `decision`: FRED_INSUFFICIENT; calendario futuro solo por fecha, sin hora/zona, consenso ni importancia; FOMC/ECB no acreditados como cobertura completa. No se creó adapter activo ni se solicitó clave.
- `status`: Finnhub SUPPORTED/NOT_ACTIVE, macro provider `none`, sin despliegue, scheduler, experimento, commit ni push.
- `tests_after`: 459 OK; `git diff --check` y secret scan OK.

## 2026-09-17 — Fase 6D: Twelve Data activo

- `agent`: Codex
- `task`: Añadir Twelve Data como MarketDataProvider activo para la demo PAPER, con XAU/USD y EUR/USD en 5m/15m/1h; conservar Massive configurable/NOT_ACTIVE y NAS100 soportado/NOT_ENABLED.
- `tests_before`: 427 OK.
- `tests_after`: 435 OK en la suite final de cierre.
- `safety`: barras cerradas UTC, ticker exacto, validación OHLC, caché únicamente de datos actuales, freshness gate intacto, 429/5xx con reintentos acotados y sin exposición de credenciales. Scheduler y experimento de 14 días siguen apagados.
- `certification`: PASS de OpenAI `gpt-5.6-terra` y Twelve Data XAUUSD/EURUSD en 5m/15m/1h, 17 de septiembre de 2026. Ver `PROVIDER_CERTIFICATION.md` y `AUDIT_PHASE6D.md`.

## 2026-09-16 — Fase 6C

- `agent`: Codex
- `task`: Implementar runtime PAPER durable con SQLite, scheduler, recovery, health y compuerta final de ejecución.
- `tests_before`: 389 OK.
- `tests_after`: 405 OK.
- `files_created`: `storage/`, `runtime/`, `test_runtime.py`, `OPERATIONAL_RUNTIME_SPEC.md`, `AUDIT_PHASE6C.md`.
- `files_modified`: `.gitignore`, `ui/adapters.py`, `ui/state.py`, `ui/app.py`, `ui/pages/journal.py`, `ui/pages/system.py`, `ARCHITECTURE.md`, `ROADMAP.md`, `UI_SPEC.md`, `CHANGELOG_AGENT.md`.
- `safety`: PAPER only; `AI_CAUTION` bloquea progresión incluso con riesgo APPROVED; datos stale, barras abiertas y fallos bloquean nuevos paper orders.
- `next_phase`: preparación Fase 7, no implementada.

## 2026-09-16 — Fase 6B

- `agent`: Codex
- `task`: Implementar Hybrid Trading Floor UI en Streamlit y Plotly, preservando `app.py` legacy.
- `tests_before`: 381 OK.
- `tests_after`: 389 OK.
- `files_created`: `ui/`, `test_ui.py`, `requirements-ui.txt`, `UI_SPEC.md`, `AUDIT_PHASE6B.md`.
- `files_modified`: `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `integration`: `FloorRunReport → AIFloorReport → FloorViewModel`; renderers de solo lectura.
- `safety`: PAPER MODE, REAL EXECUTION DISABLED, RiskDecision como única autoridad, SAMPLE / DEMO aislado.
- `next_phase`: Fase 6C, no implementada.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 6A — AI Agent Runtime
- `task`: Construir el motor de agentes IA advisory sobre el Trading Floor determinista existente, sin reemplazarlo.
- `tests_before`: 315 tests OK.
- `tests_after`: 381 tests OK.
- `files_created`: `ai/__init__.py`, `ai/contracts.py`, `ai/provider.py`, `ai/runtime.py`, `ai/prompts.py`, `ai/orchestrator.py`, `ai/agents/__init__.py`, `ai/agents/structure_ai.py`, `ai/agents/liquidity_ai.py`, `ai/agents/macro_ai.py`, `ai/agents/setup_reviewer_ai.py`, `ai/agents/trade_reviewer_ai.py`, `test_ai_contracts.py`, `test_ai_provider.py`, `test_ai_runtime.py`, `test_ai_agents.py`, `test_ai_reviewers.py`, `test_ai_orchestrator.py`, `AI_RUNTIME_SPEC.md`, `AUDIT_PHASE6A.md`.
- `files_modified`: `ARCHITECTURE.md`, `TRADING_FLOOR_SPEC.md`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `provider_abstraction`: `AIProvider.generate(request) -> AIResponse`; `DeterministicAIProvider` es el proveedor por defecto (sin red, sin API keys) y `FakeAIProvider` es el doble de prueba; un proveedor LLM real puede añadirse después sin tocar Risk Engine, Paper Broker ni scouts.
- `grounding_policy`: `validate_ai_response` rechaza cualquier respuesta con `run_id`, `symbol`, `agent_name`, `as_of`, `bias`, `recommendation` o `confidence` fuera de lo esperado, o que cite un `evidence_id` no presente en el `AIRequest`.
- `failure_isolation`: una excepción del proveedor se captura y se convierte en `AIResponse(status="ERROR")` local; el resto del run determinista continúa.
- `cost_control`: si no hay evidencia determinista utilizable, no se invoca al proveedor y se retorna `NO_DATA` directamente.
- `authority_policy`: `ai/orchestrator.py` compone sobre un `FloorRunReport` ya calculado por `floor/orchestrator.py` (no lo modifica); `NO_SETUP`, `WATCH` y `RISK_REJECTED` deterministas nunca se convierten en un estado ejecutable; un `PLAN_READY` con revisión IA adversa (`DISAGREE`/`REJECT_RECOMMENDATION`) se reporta como `AI_CAUTION` sin alterar el `RiskDecision` original.
- `immutability`: `MarketBar`, `TradePlan`, `RiskDecision` e `InstrumentSpec` son dataclasses frozen (cualquier intento de escritura lanza `FrozenInstanceError`); `PaperAccount` solo se expone a la capa IA mediante `ai.runtime.snapshot_account`, una copia profunda de solo lectura.
- `audit_log`: estructura en memoria con `run_id`, agente, versión de prompt, IDs de evidencia, respuesta, resultado de validación y metadata del proveedor; sin API keys ni credenciales. Persistencia durable diferida a Fase 6C.
- `real_execution_check`: REAL_EXECUTION = DISABLED; `ai/` no importa `execution.paper_broker` ni `execution.trade_manager`.
- `warnings`: warnings legacy de Streamlit/pandas preexistentes; sin red, broker ni dinero real.
- `known_debt`: sin proveedor LLM real conectado (deliberado), sin scheduler, sin persistencia durable del audit log.
- `next_phase`: Fase 6B — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `task`: Crear protocolo persistente y documentación base para AI Trading Floor V2.
- `files_changed`: `AGENTS.md`, `TRADING_FLOOR_SPEC.md`, `ARCHITECTURE.md`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `tests_before`: 230 tests OK.
- `tests_after`: No se ejecutaron cambios de código; baseline confirmado con 230 tests OK.
- `decisions`: Fase 0 marcada como completada; Fase 1 marcada como siguiente. Se conserva el prototipo legacy y se separan contratos, datos, agentes y motores deterministas en el diseño futuro.
- `warnings`: `git` no estaba disponible en el PATH de la terminal; no se modificó código Python.
- `next_step`: Diseñar e implementar Fase 1 solo con autorización explícita.

## Estado inicial conocido

- Baseline actual: `230 tests OK`.
- Checkpoint previo: Risk Engine V1 existente.
- No se ha autorizado todavía la implementación de Fase 1.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `task`: Implementar el vertical slice determinista de Fase 1: contratos, InstrumentSpec, timeframes y Risk Engine LONG/SHORT con simulación explícita.
- `files_changed`: `core/contracts.py`, `core/timeframes.py`, `core/__init__.py`, `data/instruments.py`, `data/__init__.py`, `riesgo.py`, `simulador.py`, `estrategia.py`, `metricas_estrategia.py`, `evaluacion_estrategia.py`, `test_core_contracts.py`, `test_risk_v2.py`, `test_simulador.py`, `test_metricas_estrategia.py`, `test_evaluacion_estrategia.py`, `ROADMAP.md`.
- `tests_before`: 230 tests OK.
- `tests_after`: 237 tests OK.
- `decisions`: Mantener las APIs legacy; añadir una ruta V2 explícita. Rechazar instrumentos sin multiplicador contractual conocido. Mantener UTC y timeframes 1h/15m/5m. Usar APPROVED/REJECTED determinista y soportar LONG/SHORT.
- `warnings`: Warnings existentes de Streamlit y pandas durante la suite; no se conectó red ni broker.
- `next_step`: Fase 2: Structure Agent y Liquidity Agent deterministas.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 2
- `task`: Implementar Structure Agent y Liquidity Agent deterministas con contrato AgentMessage versionado.
- `tests_before`: 237 tests OK.
- `tests_after`: 248 tests OK.
- `files_created`: `agents/__init__.py`, `agents/structure_agent.py`, `agents/liquidity_agent.py`, `test_structure_agent.py`, `test_liquidity_agent.py`.
- `files_modified`: `core/contracts.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `definitions_used`: swings confirmados con una barra cerrada posterior; HH/HL/LH/LL por comparación de swings; soporte/resistencia como extremos de swings; displacement como cuerpo/rango >= 0.6; equal levels con tolerancia relativa 0.1%; sweep high/low por ruptura del extremo previo y cierre de vuelta dentro.
- `heuristics`: displacement y equal-level detection están marcados `HEURISTIC`; order blocks no se implementaron.
- `warnings`: warnings preexistentes de Streamlit y pandas durante tests; sin red ni broker.
- `next_phase`: Fase 3 — Macro/News Agent.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 4
- `task`: Implementar Setup Validator, Trade Planner y Orchestrator deterministas.
- `tests_before`: 266 tests OK.
- `tests_after`: 278 tests OK.
- `files_created`: `agents/setup_validator.py`, `agents/trade_planner.py`, `floor/__init__.py`, `floor/orchestrator.py`, `test_setup_validator.py`, `test_trade_planner.py`, `test_orchestrator.py`.
- `files_modified`: `core/contracts.py`, `agents/structure_agent.py`, `agents/liquidity_agent.py`, `agents/trade_planner.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`, `AUDIT_PRE_PHASE4.md`.
- `setup_policy`: requiere los tres timeframes explícitos; estructura compatible; evidencia 15m y liquidez 5m; macro HIGH ACTIVE_WINDOW bloquea; estados NO_SETUP/WATCH/VALID_SETUP.
- `multi_timeframe_policy`: 1h contexto, 15m setup, 5m confirmación; faltantes fallan cerrado.
- `liquidity_policy`: utiliza únicamente sweeps y pools/equal levels existentes; order blocks no implementados.
- `macro_filter_policy`: macro actúa como filtro de contexto; no genera dirección ni orden.
- `entry_policy`: último Close cerrado del timeframe 5m hasta `as_of`.
- `stop_policy`: invalidación estructural del SetupAssessment; no se inventa fallback.
- `target_policy`: target matemático mínimo R:R 3.0.
- `risk_handoff`: todo TradePlan se entrega al Risk Engine; Orchestrator nunca convierte VALID_SETUP directamente en APPROVED.
- `orchestrator_flow`: scouts -> SetupAssessment -> TradePlan -> RiskDecision -> FloorRunReport; no ejecución.
- `run_id_policy`: un UUID por run se propaga a scouts y reporte.
- `as_of_policy`: un instante lógico compartido por todos los scouts y planner.
- `fail_closed_policy`: datos faltantes, errores o evidencia insuficiente producen NO_SETUP/WATCH/RISK_REJECTED.
- `warnings`: warnings legacy de Streamlit/pandas; sin broker ni dinero real.
- `next_phase`: Fase 5 — Paper Execution y Trade Manager.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 5
- `task`: Implementar Paper Broker y Trade Manager locales, deterministas y sin ejecución real.
- `tests_before`: 282 tests OK.
- `tests_after`: 284 tests OK.
- `files_created`: `execution/__init__.py`, `execution/contracts.py`, `execution/paper_broker.py`, `execution/trade_manager.py`, `test_execution.py`.
- `files_modified`: `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `paper_account_model`: equity = starting_equity + realized_pnl + unrealized_pnl; una posición abierta por símbolo.
- `order_state_machine`: PENDING -> FILLED/CANCELLED; validación fail-closed.
- `position_state_machine`: OPEN -> CLOSED; no reapertura.
- `fill_policy`: próxima barra elegible, Open real, sin sustitución por planned entry.
- `stale_plan_policy`: orden local idempotente por run_id; no se persigue una orden duplicada.
- `gap_policy`: manager evalúa gaps antes de niveles intrabar y conserva stop-first.
- `post_fill_risk_policy`: Paper Broker valida orden aprobada; no hay broker ni dinero real.
- `RR_policy`: RiskDecision debe llegar APPROVED desde el Risk Engine.
- `PnL_policy`: PnL LONG/SHORT determinista, costes paper y journal de transiciones.
- `equity_policy`: mark-to-market sobre posiciones abiertas y PnL realizado al cerrar.
- `journal_policy`: eventos estructurados con timestamp, run_id, símbolo y entity_id.
- `idempotency_policy`: segundo submit del mismo run devuelve la orden existente.
- `no_lookahead_check`: tests de fill en próxima barra y gestión secuencial.
- `real_execution_check`: REAL_EXECUTION = DISABLED.
- `known_debt`: expiración avanzada, partial fills, persistencia y reconciliación quedan para evolución posterior.
- `next_phase`: Fase 6 — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 5.1 hardening
- `task`: Auditar y endurecer Paper Broker/Trade Manager antes de Fase 6.
- `tests_before`: 284 tests OK.
- `tests_after`: 286 tests OK.
- `files_created`: `AUDIT_PHASE5.md`.
- `files_modified`: `execution/contracts.py`, `execution/paper_broker.py`, `execution/trade_manager.py`, `test_execution.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `decisions`: InstrumentSpec/multiplier requerido para ejecución; primera barra cerrada posterior a as_of; post-fill risk/RR/geometry fail-closed; símbolo y timestamp aislados; costes explícitos y journal estructurado.
- `warnings`: sin broker, red, credenciales ni dinero real.
- `next_phase`: Fase 6 — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 5.2 final execution safety gate
- `task`: Cerrar invariantes de Paper Execution con pruebas aisladas y flujo end-to-end real.
- `tests_before`: 286 tests OK.
- `tests_after`: 315 tests OK.
- `files_modified`: `execution/contracts.py`, `execution/paper_broker.py`, `execution/trade_manager.py`, `core/contracts.py`, `riesgo.py`, `test_execution.py`, `AUDIT_PHASE5.md`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `decisions`: eliminar fallback económico del multiplier; exigir timestamps aware, símbolo exacto, OHLC completo y vela cerrada; proteger opened_at/current equity; alinear LONG/SHORT en Risk Engine; propagar metadata y costes.
- `warnings`: el fixture sintético actual del Orchestrator sigue produciendo NO_SETUP; no se declara PLAN_READY sin evidencia.
- `next_phase`: Fase 6 — Floor Assistant y Trading Floor UI.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 4.1 hardening
- `task`: Eliminar equity hardcodeada, exigir barras cerradas en Trade Planner, validar lineage/run_id, symbol, timeframe y as_of, y conservar PLAN_UNAVAILABLE.
- `tests_before`: 278 tests OK.
- `tests_after`: 282 tests OK.
- `files_created`: ninguno.
- `files_modified`: `core/timeframes.py`, `agents/structure_agent.py`, `agents/liquidity_agent.py`, `agents/trade_planner.py`, `agents/setup_validator.py`, `floor/orchestrator.py`, `core/contracts.py`, `test_trade_planner.py`, `test_setup_validator.py`, `test_orchestrator.py`, `CHANGELOG_AGENT.md`.
- `decisions`: equity ahora es argumento explícito del Orchestrator; equity inválida falla cerrado. Barras forming no pueden ser planned entry. Lineage y timestamps inconsistentes producen NO_SETUP. VALID_SETUP sin plan produce PLAN_UNAVAILABLE.
- `warnings`: warnings legacy de Streamlit/pandas; sin broker, scheduler ni ejecución real.
- `next_phase`: Fase 5 — Paper Execution y Trade Manager.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Pre-Phase-4 Audit
- `task`: Auditoría integral y hardening de la base Fase 0-3.
- `tests_before`: 253 tests OK.
- `tests_after`: 268 tests OK.
- `files_created`: `AUDIT_PRE_PHASE4.md`.
- `files_modified`: `agents/structure_agent.py`, `agents/liquidity_agent.py`, `agents/macro_news_agent.py`, `core/contracts.py`, `validacion_historica.py`, `test_structure_agent.py`, `test_liquidity_agent.py`, `test_macro_news.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `defects_fixed`: selección temporal incorrecta de BOS; equal highs/lows basados en barras no estructurales; Macro/News sin known_at/result_timestamp; deduplicación sin namespace de source; warning pandas de parsing temporal ambiguo.
- `known_non_blocking_debt`: dataclasses frozen con contenedores internos mutables; especificaciones contractuales desconocidas; limitaciones intradía de yfinance; warnings ScriptRunContext de Streamlit.
- `warnings`: no se encontraron secrets; no se conectó red, broker ni dinero real.
- `next_phase`: Fase 4 — Setup Validator, Trade Planner y Orchestrator.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 3
- `task`: Implementar Macro/News Agent determinista con proveedor normalizado, freshness, relevancia y deduplicación.
- `tests_before`: 253 tests OK.
- `tests_after`: 266 tests OK.
- `files_created`: `data/macro_news.py`, `agents/macro_news_agent.py`, `test_macro_news.py`.
- `files_modified`: `core/contracts.py`, `ROADMAP.md`, `CHANGELOG_AGENT.md`.
- `provider_contract`: `MacroNewsProvider` con `macro_events()` y `news_items()`; `InMemoryMacroNewsProvider` para tests sin red.
- `timestamp_policy`: fuente/evento/publicación/recepción separados; timestamps timezone-aware normalizados a UTC; datos futuros rechazados.
- `no_lookahead_policy`: solo se aceptan datos cuyo `received_at` sea <= `as_of`; actuals sobre eventos futuros se rechazan.
- `freshness_policy`: ventanas explícitas UPCOMING 24h, ACTIVE_WINDOW 1h, RECENT 24h y STALE posterior.
- `dedup_policy`: IDs de proveedor cuando existen; fallback estable con campos disponibles.
- `relevance_policy`: categorías macro explícitas y relevancia USD/EUR/símbolo; sin causalidad ni dirección de precio.
- `warnings`: warnings legacy de Streamlit y mensajes esperados de tests; sin red ni claves API.
- `next_phase`: Fase 4 — Setup Validator, Trade Planner y Orchestrator.

## 2026-09-16

- `timestamp`: 2026-09-16
- `agent`: GitHub Copilot
- `phase`: Fase 2.1 closure gate
- `task`: Formalizar Break of Structure, retracement y parsing temporal sin warning ambiguo.
- `tests_before`: 248 tests OK.
- `tests_after`: 253 tests OK.
- `files_created`: ninguno.
- `files_modified`: `agents/structure_agent.py`, `agents/liquidity_agent.py`, `validacion_historica.py`, `test_structure_agent.py`, `test_liquidity_agent.py`, `ROADMAP.md` sin cambio de estado, `CHANGELOG_AGENT.md`.
- `BOS_definition`: Ruptura bullish/bearish por cierre de vela cerrada sobre el último swing confirmado; el swing requiere una vela cerrada posterior de confirmación y se registra evidencia completa.
- `retracement_definition`: Tras impulso confirmado y pivote protegido, el cierre actual queda entre ambos niveles sin invalidar el pivote; resultado marcado `HEURISTIC`.
- `no_lookahead_tests`: BOS y retracement usan `as_of`; barras futuras no cambian los reportes históricos.
- `pandas_warning_fix`: `pd.to_datetime(..., format="mixed")` elimina la inferencia ambigua sin silenciar warnings.
- `warnings_remaining`: warnings legítimos de ScriptRunContext de Streamlit y mensajes legacy esperados; warning temporal ambiguo de pandas eliminado.
- `next_phase`: Fase 3 — Macro/News Agent.
# Fase 6D — integración de proveedores

- Añadidos adapters Massive y OpenAI tras contratos existentes, con timeouts, reintentos acotados, salidas estructuradas, validación de barras cerradas y estados de error explícitos.
- Configuración local segura, modos de proveedor, certificador sin trading y CI de Python 3.13.
- Baseline 405 tests OK; suite ampliada 415 tests OK. Certificación en vivo pendiente de MASSIVE_API_KEY y confirmación de proyecto OpenAI. El conector Massive muestra NOT_ENTITLED para I:NDX reciente y RATE_LIMIT tras consultas adicionales; no se inicia la demo.
- Decisión posterior: enabled_symbols predeterminado XAUUSD,EURUSD; NAS100 soportado mediante I:NDX pero NOT_ENABLED/NOT_CERTIFIED. Runtime, scheduler, certificador y UI distinguen selección habilitada del catálogo. La clave OpenAI de Default project fue aceptada; 419 tests offline OK; la certificación live espera MASSIVE_API_KEY.
- Certificación live solicitada: MASSIVE_API_KEY no aparece en el .env.local indicado ni en el entorno; Massive no se consultó. OpenAI devolvió HTTP 429 RATE_LIMITED para gpt-5.6-terra. Se añadió clasificación segura de cuota agotada y prueba; 420 tests offline OK. Fase 6D sigue PARTIAL y sin commit/push.
- Reintento posterior con MASSIVE_API_KEY presente: XAUUSD referencia y tres intervalos PASS, pero STALE_DATA; EURUSD referencia PASS, pero RATE_LIMITED antes de certificar barras. NAS100 no se consultó. Massive permanece PARTIAL; OpenAI no se reintentó en esta pasada; no se hace commit/push.

# Fase 7 — Demo Runner PAPER

- Demo Runner durable por slot, preflight local READY/NOT_READY, modo diagnóstico sin órdenes y certificador offline/live.
- SQLite schema 2 agrega revisiones y notificaciones versionadas; migración desde schema 1, recuperación de ciclos interrumpidos y lectura de UI sin escritura.
- Tests E2E sintéticos cubren setup LONG/SHORT, riesgo, IA adversa, datos inválidos, fallos de proveedores, duplicados y reinicio/cierre de posición.
- Proveedores activos de la demo: Twelve Data y OpenAI; Massive soportado/no activo; NAS100 soportado/no habilitado. El experimento no se inicia.
- Certificación final local: 447 tests OK; preflight READY; diagnóstico live XAUUSD/EURUSD PASS con barras actuales, IA contractual y cero órdenes.

# 2026-09-29 — PAPER execution observability (PR only)

- Reproduced a `PLAN_READY`/risk `APPROVED` cycle with no order because an
  existing position or pending order owns the symbol. The audit result is now
  persisted in the review and journal and sent as a separate final Slack
  `EXECUTION_DECISION`. No new field participates in the PAPER policy gate.
- Added an advisory `setup_id` from existing 15m structural evidence. It is
  never used for deduplication, sizing, risk, or order submission.
- SYSTEM can look up a persisted review and journal by run ID. Its bulk export
  shrinks to the newest records when the 1 MB bound is reached, avoiding a
  page-wide exception; a single oversized review yields an explicit warning.
- Comparability: trading signals, risk decisions, order/fill economics, and
  exit logic are unchanged. Additional journal/notification records and review
  metadata affect observability only. The deployed commit remains untouched.
- Tests: 512 passed on the main-based PR with a valid `AI_FLOOR_GIT_COMMIT`
  supplied to the existing cloud-runner test harness. Without that variable, two pre-existing baseline
  tests fail before this change because cloud_runner requires a commit SHA.
- No deploy, merge, production database mutation, or REAL execution activation.

## 2026-09-29 — Slack signal policy and isolated PAPER audit (PR #2 follow-up)

- Reproduced redundant cycle alerts and injected setup ID, audit transaction,
  analysis journal, post-persist order journal, dashboard snapshot, notification capture and Slack
  failures. PAPER decisions and saved orders survive these audit failures.
- Capture immediate SUBMITTED, first VALID_SETUP blocked outcome or changed
  blocker/reason, RISK_REJECTED, AI_CAUTION, position transitions and operational
  failures. WATCH/NO_SETUP and repeated identical blockers remain in journal and
  review; SETUP_VALID_SETUP and duplicate order alert no longer add Slack noise.
- The run export enforces its byte bound even when compact evidence is oversized.
  Capture and delivery errors log only the exception type, never credentials.
- The operational restart repair is separately proposed in PR #3. Revalidate
  the combined tree before either merge; keep the original freeze identity.
