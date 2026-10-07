# V2 Phase 6 — Multi-Setup / Position Conflict Engine (P6.0 audit, characterization & owner-decision package)

Baseline `main` `b3e93dfcff06233a7f0d22748d626e9a4b8d57bf` (Phase 5 merged, PR #10). Branch
`v2/phase6-multi-setup-conflict`. PAPER only · REAL DISABLED · NAS100 OFF · trading DB schema 3 · runtime activation
NOT AUTHORIZED · no deploy. P6.0 is audit only: no runtime behavior is changed. F06-T01..T14 are **not** certified.

Evidence: `test_phase6_conflict_characterization.py` (12 tests, current behavior) and `replay/conflict_audit.py`
(offline, observational).

## A. Current behavior map (V1 runtime, `runtime/service.py`)

Per symbol and slot, one cycle holds the per-symbol `symbol_locks` row (`Store.claim_slot`). `run_id = uuid5(slot_key)`,
so a retry of the same slot returns `DUPLICATE`.

| # | Stage | Code | Runs when a same-symbol position / pending exists? |
|---|---|---|---|
| 1 | Market data + freshness gate | `service.py:254-290` | yes |
| 2 | **Existing-position management** (V1 `TradeManager.process_bar` via the guarded write, or B2.3B catch-up when the flag is ON) | `service.py:299-313` | yes, **before** any analysis, persisted on its own |
| 3 | Deterministic floor: structure, liquidity, macro → Setup Validator → Trade Planner → V1 Risk (`evaluar_trade_plan`, no portfolio input) | `service.py:316-319`, `floor/orchestrator.py` | **yes, in full** |
| 4 | AI review (veto only: `AI_CAUTION`) | `service.py:331-343`, `ai/orchestrator.py:39-50` | yes, in full |
| 5 | Execution-time freshness/session recheck | `service.py:346-363` | yes |
| 6 | Pending-order progression (V1: only if AI healthy and final status not AI_CAUTION/ERROR/RISK_REJECTED; flag ON: B2.2 `CurrentCycleGate`) | `service.py:366-394` | yes, **gated by this cycle's analysis** |
| 7 | **Decision point**: `eligible` and no same-symbol PENDING and symbol not in `open_positions` → submit; otherwise `PENDING_ORDER` (precedence) or `EXISTING_POSITION` | `service.py:407`, `:437-444` | this is where `SKIPPED / EXISTING_POSITION` is set |
| 8 | Guarded submit with stale recheck (same-symbol exposure / equity changed → `STALE_PAPER_STATE`) | `service.py:411-431` | not reached |
| 9 | Execution record (`record_execution`: setup_id, blocking ids) + snapshot | `service.py:447-454`, `storage/database.py:226-239` | yes |

Findings: (1) V1 **already analyzes** every new setup when exposure exists. Setup, planner, V1 Risk and AI all run,
and only submission is skipped (characterized: an opposite SHORT setup reached `PLAN_READY` and was skipped as
`EXISTING_POSITION`; the LONG was untouched). (2) The skip is **direction-blind** and **setup-blind**. (3) At the
decision point a PENDING order normally no longer exists: step 6 progresses it on the next cycle's bar before
step 7. `PENDING_ORDER` appears only when progression was blocked (AI caution/unhealthy, stale, P1 no gate).
(4) Phase 4 fixed-3R and Phase 5 Risk V2 are **not** on the runtime path (library and replay only).

## B. Data-model audit

| Entity | Authoritative fields today | Missing for Phase 6 |
|---|---|---|
| Open position (`PaperPosition`) | symbol, side, quantity, planned_entry, entry_price (fill), stop, target, opened_at, run_id, origin_order_id, last_price, last_processed_at | setup_id, policy identity (only on the origin order) |
| Pending order (`PaperOrder`) | symbol, side, quantity, planned_entry, stop, target, as_of, run_id, status, `risk_policy_version` (P5.1C, optional) | setup_id |
| New setup (`SetupAssessment` + V2 explanation) | setup_id, identity inputs (symbol, side, invalidation, 15m anchor), invalidation, evidence refs (bar starts), timestamps; entry/target from the plan | thesis / leg identity (does not exist) |

The only durable `setup_id` link to an existing exposure is `run_id → review_reports.execution.setup_id`. That is an
audit record, documented as "never used as execution gates" (`runtime/observability.py`).

**Multiple same-symbol positions are NOT structurally safe** (characterized):
- `PaperAccount.open_positions` is a dict **keyed by symbol**.
- `PaperBroker.process_next_bar` writes `open_positions[symbol] = position`, so a second same-symbol fill
  **overwrites** the first in memory.
- `PaperBroker.submit_plan` refuses an open symbol but has **no pending-per-symbol guard**. The runtime and Risk V2
  provide that guard.
- `Store.load_paper` **raises** `duplicate open paper position` on two OPEN rows for one symbol. Runtime start
  would fail.
- `TradeManager.process_bar` and `position_catch_up` iterate and delete by symbol key (`.get(symbol)`).
- `ui/state.py`, `symbol_locks` and `ui_snapshots` are per symbol.

The SQL tables have no per-symbol uniqueness. The limit lives in Python. **Hedging (LONG + SHORT on one symbol) and
netting are both unrepresentable today.** Same-symbol multi-position would need a redesign of account keying,
broker fill, TradeManager, catch-up, recovery, UI and the Risk V2 symbol rule. It is not a patch.

Schema 3 suffices for the recommended options (C, J). A durable `setup_id` on orders/positions would be an additive
optional field, the same technique as `risk_policy_version`: omitted when None, legacy bytes unchanged.

## C. setup_id audit (Phase 3, `agents/setup_validator.setup_identity`)

`setup_id = uuid5(json{symbol, side, invalidation, anchor})` with anchor = latest 15m BOS (break bar, broken level,
direction), else retracement (impulse/protected timestamps and prices).

- Proves: the exact same opportunity snapshot. Retry, restart or re-run with a different run_id gives the same id
  (tested). Direction is part of identity.
- Does **not** prove thesis continuity. Any change of anchor, invalidation, side or symbol gives a new id (tested).
  Entry, SL and TP are not inputs.
- `invalidation` is the **minimum swing low (LONG) / maximum swing high (SHORT) of the whole 15m window**
  (`structure_agent.py:164-165`), and the anchor is the **latest** BOS. A rolling window or one new break bar changes
  the id while the market thesis may be unchanged (tested: dropping old bars changes support 98.0 → other).
- Replay (1,940 VALID records): EURUSD 529 distinct ids / 949 records, XAUUSD 546 / 991. Median consecutive run of
  one id = 1 (max 9–10). id changes by proven cause: anchor-only (same side + invalidation) 660, side 290,
  invalidation 123. One id never maps to two (side, invalidation) pairs.
- Conclusion: setup_id is sufficient for **technical/opportunity duplicates** only, never for SAME THESIS.

## D. Conflict taxonomy (from evidence; no new definitions adopted)

| Category | Data available | Missing | Deterministic today? | Proposed minimum rule | Owner decision |
|---|---|---|---|---|---|
| DUPLICATE SETUP | setup_id (new), exposure run_id → review setup_id | setup_id on order/position | yes, via audit link (not an execution input) | same symbol + same setup_id as live exposure | DEC-6.1 |
| SAME THESIS | side, invalidation, setup_id | thesis / leg identity | **no** | none reliable (setup_id too volatile; invalidation is a window extreme) | DEC-6.2 |
| SAME TREND / NEW SETUP | side, 1h bias (in scouts, not persisted with exposure) | persisted bias of the exposure | partially (same side + different id) | "same direction, different setup_id" | DEC-6.3 |
| NEW STRUCTURE | anchor in identity inputs of the new setup | the exposure's anchor (not persisted) | no | — | DEC-6.2 |
| TREND CHANGE | 1h bias of the new setup | exposure's bias | no | — | DEC-6.2 |
| OPPOSITE SETUP | sides of both | — | **yes** | new side ≠ exposure side | DEC-6.5 |
| POSITION CONFLICT | symbol, exposure kind OPEN/PENDING | — | **yes** | same symbol with OPEN or PENDING exposure | DEC-6.4/6.7 |

## E. Duplicate / idempotency mechanisms (F06-T01)

| Mechanism | Protects | Location |
|---|---|---|
| slot_key + `run_id = uuid5(slot_key)` + `claim_slot` | same cycle twice → `DUPLICATE` | `service.py:228-232` |
| `symbol_locks` | one RUNNING cycle per symbol | `database.py:130-146` |
| `PaperBroker.submit_plan` run_id dedupe | same run → same order | `paper_broker.py` |
| `reserve_and_submit` run_id dedupe | `DUPLICATE_RUN` | `risk_reservation.py` |
| B2.3A whole-state CAS | stale writers | `save_paper(expected_state)` |
| journal dedupe (pending gate) | duplicate PENDING_NOT_EVALUATED rows | `pending_order_gate.py` |

These cover **technical** duplicates. F06-T01 adds only the **economic** check: a new run whose setup_id equals the
setup_id of live same-symbol exposure (21 occurrences in replay). The two notions must stay separate.

## F. Risk Engine V2 integration

Gate 9 of `execution/risk_engine_v2.evaluate`: `SYMBOL_EXPOSURE_LIMIT` when the count of open positions + PENDING
orders on the symbol ≥ `RiskPolicy.max_positions_or_pending_per_symbol` (= 1 in `V2_P5_RISK_1`). It is
direction-blind and inside the engine (characterized). A second same-symbol operation therefore needs a **new
registered policy version** (P5.1C: fill semantics are bound to the version). It must not be a mutation of
`V2_P5_RISK_1`, and it presupposes DEC-6.4 B. Every other protection (1%, conservative equity, 2.30% aggregate, 8/7
reservation, drawdown, policy identity, CAS, idempotency) applies unchanged by calling `evaluate` /
`reserve_and_submit`. Phase 6 must never re-implement them.

## G. Concurrency / crash

- Same symbol: runtime cycles are serialized by `symbol_locks`. Two setups of one symbol never decide concurrently
  in the runtime. Outside the runtime (`reserve_and_submit`) the whole-state CAS plus the engine symbol rule already
  admit one of two concurrent same-symbol submissions (Phase 5 race tests, real processes).
- Cross symbol: concurrent cycles. Aggregate risk is protected by the whole-state CAS (loser re-evaluates). Proven in
  Phase 5.
- Position closes / pending fills during evaluation: any PAPER change makes the CAS fail. The submit path then
  re-checks, giving `STALE_PAPER_STATE` (V1) or a fresh re-evaluation (Risk V2). A conflict decision computed on a
  stale view can never be persisted with an order.
- Crash before or after the conflict-decision save: the decision must be written in the **same** guarded transaction
  as any order (Phase 5 pattern), or as a pure journal row with no economic effect. A separate cross-DB "conflict
  store" would be fake atomicity and is rejected.
- Real multi-process tests are required for: same-symbol pair under `symbol_locks`; cross-symbol reservation race;
  close/fill during evaluation; crash before/after commit; restart/retry idempotency.

## H. Existing-position management independence (§21)

Safe today: management (step 2) runs and persists **before** analysis. A failure of the new-setup analysis returns
`ERROR` after management is saved (characterized).

**Coupling**: pending-order progression (step 6) depends on this cycle's analysis and AI health. A failed analysis
leaves the previous pending order unprogressed (characterized; certified Phase 2 P1 design). Phase 6 rule: the
conflict engine must run **after** step 6 (at step 7) and must never feed `final_status`, `ai_healthy` or exceptions
back into steps 2/6.

## I. AI authority

AI is veto-only (`AI_CAUTION`). It receives a read-only account view (`ai/runtime.py`). The trade reviewer cannot
change entry/SL/TP/quantity (`ai/agents/trade_reviewer_ai.py`). No AI can close, cancel, reverse, classify conflicts
or authorize a second position. Phase 6 keeps it so (no decision needed unless the Owner wants to expand it).

## J. Observability (minimum Phase 6 decision record; not implemented)

Journal event `CONFLICT_DECISION` (and the additive `review_reports.execution.conflict` object; schema 3), containing:
run_id, setup_id, symbol, new_direction, as_of, evidence refs; exposures[] (kind OPEN/PENDING, position/order id,
origin run_id, origin setup_id or `UNKNOWN`, direction, entry/fill, SL, TP, quantity, risk_policy_version);
classification; reason; existing / proposed / combined risk and portfolio risk before/after (from the Risk V2 record);
risk_decision; execution_decision; blocking ids; policy versions; `existing_management_continued` (true when step 2
completed this cycle).

Reason codes: `NO_CONFLICT`, `DUPLICATE_SETUP`, `SAME_DIRECTION_EXPOSURE`, `OPPOSITE_DIRECTION_EXPOSURE`,
`PENDING_ORDER_EXPOSURE`, `UNKNOWN_EXPOSURE_ORIGIN`, plus the unchanged Risk V2 reasons.

## K. Historical characterization (observational; `replay/conflict_audit.py`)

Chronological DEC-5.7 single-account pass (production Risk V2 + broker), state at each of 1,940 VALID records:

| Same-symbol state at the new VALID setup | Count |
|---|---|
| OPEN · same direction · same setup_id · same invalidation | 21 |
| OPEN · same direction · different setup_id · same invalidation (anchor-only change) | 51 |
| OPEN · same direction · different setup_id · different invalidation | 969 |
| OPEN · opposite direction · different setup_id · different invalidation | 882 |
| PENDING (any) | 0 (resolved before the decision at a 15m cadence) |
| no same-symbol exposure (2 flat, 15 other symbol exposed) | 17 |

SAME THESIS, NEW STRUCTURE and TREND CHANGE are **NOT DETERMINABLE FROM CURRENT EVIDENCE**. The records carry no
anchor, swings or 1h bias for the exposure.

## L. Owner decisions

| ID | Needed? | Options | Recommendation |
|---|---|---|---|
| DEC-6.1 Duplicate vs retry | yes | A: economic duplicate = same symbol + same setup_id as live exposure, setup_id read via run_id → review (audit record becomes an input). B: same rule, with an additive optional `setup_id` on `PaperOrder`/`PaperPosition` (schema 3, legacy = UNKNOWN). C: no economic duplicate check (one-per-symbol already blocks). | **B**. Durable, explicit, mirrors P5.1C; unknown origin → `UNKNOWN_EXPOSURE_ORIGIN`, never inferred. |
| DEC-6.2 SAME THESIS | yes | A: no SAME THESIS class in P6.1 (classify only DUPLICATE / SAME_DIRECTION / OPPOSITE). B: thesis = (symbol, side, invalidation). Cheap, but invalidation is a window extreme (51 vs 969 split). C: new versioned thesis contract from structure (Phase 3 change). | **A**. Evidence cannot support B/C; C is a separate Phase 3 decision. |
| DEC-6.3 Same direction / new setup | yes | A: analyze + record `SAME_DIRECTION_EXPOSURE` + block (current economics). B: allow if Risk V2 passes (pyramiding; requires DEC-6.4 B). C: allow only with independent SL/TP and a new risk version. | **A** |
| DEC-6.4 Multiple same-symbol positions | yes | A: no, keep one open-or-pending per symbol. B: yes, after a structural redesign (account keying, broker fill, TradeManager, catch-up, recovery guard, UI, Risk version), as its own batch. | **A** for P6.1. B is not safe without redesign (finding H-6.1). |
| DEC-6.5 Opposite setup | yes | A: analyze + record `OPPOSITE_DIRECTION_EXPOSURE` + block; never close. B: hedge (needs 6.4 B + 6.6 B). C: close-and-reverse: **excluded** by invariant. | **A** |
| DEC-6.6 Hedging in PAPER | yes | A: not allowed. B: allowed as independent positions (needs 6.4 B). | **A** |
| DEC-6.7 Pending conflicts | yes | A: preserve the pending order, block the new setup (current). B: replace/cancel (new cancellation authority + reservation release). C: multiple pending (needs 6.4 B). | **A** |
| DEC-6.8 Risk V2 symbol rule | conditional | If DEC-6.4 = A: **NO OWNER DECISION REQUIRED** (rule unchanged). If B: a new registered policy version with a per-symbol limit > 1. | follows 6.4 |
| DEC-6.9 Conflict persistence | yes | A: `CONFLICT_DECISION` journal row + additive review field, written in the same guarded transaction as any order (schema 3). B: new table (schema bump). | **A** |
| DEC-6.10 CF-P5-OPENRISK | conditional | With 6.4 = A no same-symbol add-on exists: **NO OWNER DECISION REQUIRED** in Phase 6; the carry-forward stays. With pyramiding (6.3 B) it becomes mandatory. | keep carry-forward |
| DEC-6.11 Integration target (added) | yes | A: conflict engine as a pure library + replay + optional `reserve_and_submit` hook; V1 runtime unchanged. B: wire into the runtime behind flag `v2_conflict_engine` (OFF by default) at step 7 only. | **A** for P6.1; B only with an explicit runtime authorization. |

## M. Findings

- CRITICAL: none.
- **H-6.1 (HIGH, latent)**: same-symbol multi-position is unsafe (fill overwrite; no pending guard in the broker;
  `load_paper` raises; symbol-keyed managers). It is unreachable today only because of the runtime and Risk V2
  one-per-symbol guards. Those guards must not be relaxed without the DEC-6.4 B redesign.
- **H-6.2 (HIGH, design constraint)**: pending progression is coupled to the current cycle's analysis/AI. A Phase 6
  component placed before step 6, or one altering `final_status`/AI health, would freeze existing pending orders.
- M-6.1: setup_id is opportunity identity, volatile; it is not thesis identity.
- M-6.2: orders/positions carry no setup_id; only the audit link exists.
- M-6.3: the V1 skip and the Risk V2 symbol rule are direction-blind (opposite setups not distinguished in records).
- M-6.4: if Risk V2 were wired into the runtime, every unstamped V1 PENDING order would block NEW V2 risk
  (`UNKNOWN_PENDING_RISK_POLICY`). This is a transition concern.
- L-6.1: replay cannot observe PENDING conflicts at decision time (resolved first).
- L-6.2: CF-P5-OPENRISK unchanged; NAS100 environment configurability unchanged (OFF; Risk V2 fails closed).

## N. P6.1 plan (after decisions; recommended options)

- Batch A (pure core): `execution/conflict_engine.py`, a pure `classify(new_setup, plan, account, orders,
  origin_setup_ids)` → classification + reasons + record. Additive optional `setup_id` on orders/positions (DEC-6.1 B).
  Risk V2 called unchanged.
- Batch B (atomic path): conflict decision + Risk V2 + reservation in one guarded save (extend `reserve_and_submit`
  through composition, not duplication); `CONFLICT_DECISION` journal; idempotency.
- Batch C: replay re-run with classifications, real multi-process tests, regression, docs.
- Runtime wiring (DEC-6.11 B) only if separately authorized, behind an OFF flag at step 7.

## O. Test plan (P6.1)

Targeted / adversarial: duplicate retry same setup_id; different run same setup_id (DUPLICATE_SETUP); same direction
new setup; opposite direction; OPEN + PENDING; unknown-origin exposure; Risk approve/reject; 2.30% aggregate
boundary; 5% drawdown boundary; unknown pending policy; no automatic close; no SL/TP mutation; no duplicate economic
exposure; schema 3; REAL disabled; NAS100 off.

**Real multi-process**: two setups same symbol (symbol lock / CAS); cross-symbol reservations; position closes during
evaluation; pending fills during evaluation; crash before/after the conflict save; restart/retry; stale writer.

Runtime regression (flag OFF): V1 `EXISTING_POSITION` / `PENDING_ORDER` outcomes byte-equivalent; management continues
when a new setup is rejected or AI fails; pending progression unchanged.

Regression: Phase 2 (evidence chronology, exactly once, P1), Phase 3 (setup identity), Phase 4 (fixed 3R, 2.5R
fill), Phase 5 (Risk V2, 2.30%, 5% DD, reservations, policy identity, 8/7). Full suite once.

## P. Feature flag / rollback

With DEC-6.11 A nothing in the runtime changes. If B is authorized later: flag `v2_conflict_engine` OFF by default.
OFF restores today's `EXISTING_POSITION → SKIP` exactly. The only durable traces are additive fields and journal
rows, so rollback needs no migration and leaves economics intact.
