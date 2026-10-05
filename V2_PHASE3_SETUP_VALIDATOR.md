# V2 Phase 3 — Setup Validator V2

Status: **PHASE 3 — IN PROGRESS / NOT CERTIFIED.** P3.1 (core) READY FOR INDEPENDENT REVIEW;
F03-T09 and F03-T11 NOT STARTED (P3.2). Branch `v2/phase3-setup-validator` · Base: `main` @
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
- `evidence_refs` (F03-T04): per scout and timeframe, its status and last closed bar start — the Phase 2
  Market Evidence identity (symbol, timeframe, bar_start) — plus macro status and event ids. References,
  not copies; no second market-evidence authority; the Evidence Store is not read.
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
