# V2 Phase 3 — Setup Validator V2

Status: **PHASE 3 — CERTIFICATION CANDIDATE / NOT CERTIFIED.** P3.1 accepted (independent review PASS at
`55e1585`); P3.2 (LOW fix, F03-T09 regression, Phase 3 E2E) READY FOR INDEPENDENT CERTIFICATION
(F03-T11). Branch `v2/phase3-setup-validator` · Base: `main` @
`c7aaafb4ff0fe35c6985d7089d8ea85a144b2121`.

Mode: PAPER only. REAL EXECUTION: DISABLED. NAS100: OFF. V2 position/evidence runtime activation:
NOT AUTHORIZED (`v2_position_catch_up` OFF). System Health: INACTIVE.

## P3.1 — V1 contract audit (F03-T01/T02/T03)

Producer: `agents/setup_validator.py` (V1 `evaluar_setup`, now `_evaluar_setup_v1`, body byte-identical
to `c7aaafb`, sha256 `6184c74d…` pinned by a test). Called by `floor/orchestrator.py` with the
deterministic structure/liquidity scout reports (1h, 15m, 5m) and the macro report; no AI input.

V1 evaluates checks in this order and stops at the first that does not pass:

| # | Check | Not met → status | V1 reason code |
|---|---|---|---|
| 1 | all 3 timeframes reported | NO_SETUP | `missing_timeframe` |
| 2 | scout lineage (run, symbol, timeframe, not future) | NO_SETUP | `scout_lineage_invalid` |
| 3 | macro lineage (if macro) | NO_SETUP | `macro_lineage_invalid` |
| 4 | macro timestamp valid, not future (if macro) | NO_SETUP | `macro_future_timestamp` / `macro_timestamp_invalid` |
| 5 | no scout ERROR/NO_DATA | NO_SETUP | `scout_unavailable` |
| 6 | macro not ERROR (if macro) | NO_SETUP | `macro_error` |
| 7 | no HIGH impact event in ACTIVE_WINDOW (if macro) | WATCH (side None) | `high_impact_active_window` |
| 8 | scout evidence payloads present | NO_SETUP | `missing_evidence` |
| 9 | 1h bias = 15m state = 5m state (bullish/bearish) | WATCH (side None) | `timeframe_incompatibility` |
| 10 | 15m BOS or retracement | WATCH (side set) | `structure_confirmation_missing` |
| 11 | 5m sweep or liquidity above/below | WATCH (side set) | `liquidity_confirmation_missing` |
| 12 | 15m support (LONG) / resistance (SHORT) | NO_SETUP | `invalidation_missing` |
| — | all passed | **VALID_SETUP**, side, invalidation, evidence (structure, liquidity) | — |

Consumers: Trade Planner (`side`, `invalidation`, `evidence`), Risk (via the plan), AI setup reviewer
(`status`, `side` only), `paper_policy`/execution (via the floor report), `setups` table, review report
(`setup_status`), UI adapters, audit `setup_id` (execution record, notification de-duplication).

**Frozen (unchanged by P3.1):** the decision — status, side, invalidation, warnings, evidence, data
quality — for every input, its check order and reason codes, and every downstream input. **Enriched:**
why (explanation), what evidence was looked at (references), identity and traceability.

## P3.1 — Setup Validator V2 (F03-T04/T05/T07/T08/T10)

`evaluar_setup` = frozen V1 decision + `explain(...)`, attached as `SetupAssessment.explanation` (new
optional field, default `{}`; information only — never read by status, Planner, Risk, AI or execution):

- `validator` `setup_validator`, `validator_version` `2.0`, `status`, `side`, `decision_reason`
  (`all_checks_passed` or the V1 reason code).
- `passed_checks`, `failed_checks` (evaluated, not met), `missing_checks` (input unavailable),
  `not_applicable_checks` (macro checks without a macro report), `not_evaluated_checks` (after the
  deciding check). Every check appears exactly once; derived from the check V1 stopped at.
- `check_details`: the measured inputs of evaluated checks (timeframe states, 15m BOS/retracement,
  5m liquidity counts, invalidation level and source, active high-impact event ids).
- `evidence_refs` (F03-T04): per scout and timeframe, a reference to its last closed bar — the Phase 2
  Market Evidence identity (`symbol`, `timeframe`, `bar_start`) — with `ref_state` VALID / UNAVAILABLE /
  INVALID and a `reason`, plus a macro reference (event ids). References, not copies; no second
  market-evidence authority; the Evidence Store is not read. (Shape tightened in P3.2, below.)
- `setup_id` and `identity_inputs` (F03-T08).

Traceability (F03-T07): the explanation is persisted in the existing review report (`review_reports`
JSON, new `setup` key) next to `execution.setup_id`; `run_id` links review, plan, risk decision,
agent decisions and journal. No schema change (trading DB stays 3); `storage/database.py` untouched.

**setup_id (F03-T08):** algorithm and values unchanged from V1 (now `setup_identity` in the validator;
`runtime/observability.setup_id` delegates). Inputs: symbol, side, invalidation and the 15m anchor
(BOS break bar, broken level, direction; else retracement impulse/protected bars and prices). Excluded:
run_id, run/as_of time, warnings, liquidity details, data quality, macro context. Only VALID_SETUP has
an identity. Same opportunity on retry, later cycle or restart (new process) → same id; new anchor,
level, invalidation, side or symbol → new id.

**False duplicates (F03-T10):** setup-level de-duplication exists only in notification capture
(repeated SKIPPED decision with identical status, reason, blocking ids and — except for
EXISTING_POSITION/PENDING_ORDER — setup_id is not re-alerted). Retry/restart/irrelevant metadata keep the
id (no false "new"); a materially new opportunity gets a new id (no false suppression). Paper Broker
economics unchanged.

**Confidence (F03-T06): NOT IMPLEMENTED.** No calibrated outcome data exists to justify a score; any
number would be arbitrary and could be mistaken for a rule. The structured checks and details are the
defensible quality information.

Tests: `test_setup_validator_v2.py` 15/15 (decision byte-identity and per-reason equivalence for all 13
V1 reason codes + VALID LONG/SHORT; explanation vs an independent oracle; missing never counted as
passed; details; evidence refs; no confidence; setup_id values = V1 algorithm, stable across
retry/irrelevant metadata/new process, new for material changes; Planner/AI inputs identical; review
`setup` links to `execution.setup_id`; scope). Mutations 5/5 killed. Full suite 767 pass / 0 fail /
0 skip.

Findings (not changed in P3.1):
- MEDIUM — after a position closes, the same still-VALID opportunity (same `setup_id`) can be submitted
  again (V1 re-entry: submission only checks for a pending order/open position). Changing it changes
  which trades happen → owner decision.
- LOW — invalidation is the extreme swing level in the provider window; if the window drops that swing,
  the stop and the `setup_id` change for the same structural anchor (treated as material: the plan
  changes).
- LOW — `SETUP_*` journal events keep an empty payload (`storage/database.py` is hash-pinned); the
  setup is traced through the review report and the execution record instead.

## P3.1 independent review — PASS (accepted at `55e1585`)

Confirmed: exact checkpoint; V1 decision byte-identical; identity stable under non-material metadata;
Planner/Risk/execution unchanged; Phase 2 regression; CI; schema 3; REAL disabled; NAS100 off; flag off.
One LOW finding (below).

## P3.2 — LOW finding: evidence-reference auditability (FIXED)

Finding: a reference could expose a scout's nested timestamp without the report passing lineage (e.g. a
future report next to `NO_SETUP`/`scout_lineage_invalid`), or a lineage-valid report could claim a bar that
does not fit its timeframe. Explanation metadata only — never status, side, Planner, Risk or execution.

Fix (`agents/setup_validator.py` `_bar_ref`/`_macro_ref`, metadata only): a bar reference is `VALID` only
if the report passes the same V1 lineage check (`_report_valid`: run, symbol, timeframe, not after as_of)
and its claimed bar is an aware timestamp, aligned to the timeframe (1h/15m/5m boundary) and closed by
as_of. Otherwise `ref_state` is `UNAVAILABLE` (`report_missing`, `bar_missing`) or `INVALID`
(`lineage_invalid`, `bar_timestamp_invalid`, `bar_misaligned_for_timeframe`, `bar_not_closed_by_as_of`)
and `bar_start` is `None` — nothing invented or normalized. The macro reference lists event ids only when
the macro report passes the V1 macro lineage checks. Status, side, reason, warnings, invalidation,
setup_id, plan, AI request and Risk are unchanged (tested with tampered timestamps).

## P3.2 — F03-T09 V1/V2 regression: PASS

`test_setup_validator_regression.py` embeds the V1 validator verbatim from `c7aaafb` as an independent
oracle and compares 17 cases: NO_SETUP (missing timeframe, invalid lineage, macro lineage, macro future
and invalid timestamp, scout ERROR, scout NO_DATA, macro ERROR, missing evidence, missing invalidation),
WATCH (active HIGH-impact event, timeframe disagreement, missing 15m structure, missing 5m liquidity),
VALID_SETUP (LONG, LONG with macro, SHORT). Every field except the additive `explanation` is identical;
real Trade Plans (LONG 100/90/130, SHORT 115/120/100), Risk decisions (APPROVED, 10 and 20 units) and
AI setup-review requests are identical. (P3.1's `test_13` passed a non-market argument, so it compared
`None == None`; fixed in P3.2 to use a real 5m frame.)

## P3.2 — Phase 3 E2E: PASS

`test_phase3_e2e.py` runs the real PAPER runtime (flag OFF, V1 path) with aligned closed bars:
VALID_SETUP LONG → explanation (all checks passed, invalidation 90 from 15m support) → six VALID
references at the actual last closed 1h/15m/5m bars → AI request carries only status/side → plan → Risk
APPROVED → order (LONG 10 @ 100, stop 90, target 130) → review `setup.setup_id` = `execution.setup_id`
= EXECUTION_DECISION journal `setup_id`, order `run_id` = review `run_id`. The next cycle (same
opportunity) keeps the setup_id and adds no order; a new 15m break bar gets a new setup_id. With a
fabricated contradictory explanation and all-INVALID references, the order, risk decision, final status,
execution and AI request are identical and the execution `setup_id` stays the real one.

## P3.2 — setup_id and false duplicates: PASS

Stable across retry, later cycle, fresh process, irrelevant metadata, explanation and evidence-reference
metadata (tampered references leave the id unchanged); new for symbol, side, anchor, level and
invalidation changes. No owner-policy change.

## Full-suite skip — explained (environmental)

The suite has one conditional test: `test_system_health.TradingDatabaseCompatibilityTests.
test_frozen_v1_baseline_code_opens_trading_db_after_sidecar_use` (V1 rollback compatibility; the only
`skipTest` in the suite). It skips when `git` is not on PATH ("git unavailable…") or when the frozen
baseline commit `25726a1` is absent (e.g. a shallow clone: CI uses `actions/checkout@v4`, depth 1).
Reproduced: without git on PATH → `OK (skipped=1)`; with git → the test runs and passes. Not a product or
harness defect. Local runs with git on PATH report 0 skipped.

## Carry-forward (no Phase 3 change)

1. **Same-setup post-close re-entry — OWNER POLICY DECISION.** Same-setup post-close re-entry remains
   inherited V1 behavior. Changing it requires an explicit owner trading-policy decision because it changes
   trade occurrence. Not a Phase 3 defect.
2. **Invalidation-window aging — pre-existing, documented.** Invalidation is the extreme swing in the
   provider window; if that swing ages out, the invalidation (stop) and therefore the setup identity change.
3. **SETUP_* journal payloads** stay empty (V1; `storage/database.py` hash-pinned). Traceability is
   proven through the review report, execution record and EXECUTION_DECISION journal (E2E).

Tests (P3.2): Phase 3 focused 29/29 (`test_setup_validator` 4, `test_setup_validator_v2` 20,
`test_setup_validator_regression` 2, `test_phase3_e2e` 3); Phase 2 targeted 143/143; adversarial
mutations 10/10 killed. Confidence: NOT IMPLEMENTED (rationale above). REAL DISABLED; NAS100 OFF;
`v2_position_catch_up` OFF; System Health inactive; schema 3.

**Phase 3: READY FOR INDEPENDENT CERTIFICATION** (not certified by the author).
