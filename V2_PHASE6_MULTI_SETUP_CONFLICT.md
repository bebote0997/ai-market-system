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

---

# P6.1 — Implementation (DEC-6.1 → DEC-6.11 approved)

Status: implemented, **pending independent review** (author: Claude; not self-certified). Library / composable path /
replay only (DEC-6.11 A): `runtime/service.py` and `runtime/config.py` are unchanged; there is no Phase 6 flag and
no runtime wiring. PAPER only · REAL DISABLED · NAS100 OFF · schema 3.

Approved rule: **analysis may continue; new same-symbol economic exposure may not.**

## P1. Decisions as implemented

| Decision | Implementation |
|---|---|
| DEC-6.1 B | `PaperOrder.setup_id`, `PaperPosition.setup_id`: optional, default None (UNKNOWN). `storage/codec.OPTIONAL_IDENTITY_FIELDS` omits them when None, so legacy bytes are unchanged; schema 3, no DDL. Only the Phase 6 path stamps an order. The broker fill copies the order's id to the position; it is never re-derived or inferred from geometry. |
| DEC-6.2 A | No SAME_THESIS / NEW_STRUCTURE / TREND_CHANGE class. A different setup_id never means "different thesis". |
| DEC-6.3 A | Same-direction exposure → `SAME_DIRECTION_NEW_SETUP` → BLOCK. No pyramiding. |
| DEC-6.4 A | One open-or-pending exposure per symbol is unchanged; `PaperAccount` is not redesigned. |
| DEC-6.5 A / 6.6 A | Opposite exposure → `OPPOSITE_SETUP` → BLOCK. Never close, reverse, net, hedge, cancel, resize or move SL/TP. |
| DEC-6.7 A | Pending exposure is preserved (not cancelled, replaced, modified or re-stamped); the new setup is blocked. |
| DEC-6.8 | Phase 5 `SYMBOL_EXPOSURE_LIMIT` is unchanged and still authoritative (defense in depth behind the conflict engine). |
| DEC-6.9 A | `CONFLICT_DECISION` journal rows in the trading DB, written atomically with the economic outcome (P4). |
| DEC-6.10 | CF-P5-OPENRISK unchanged (entry-basis loss to stop). |
| DEC-6.11 A | Pure `execution/conflict_engine.py` + composable `execution/conflict_path.py` + `replay/conflict_audit.py`. |

## P2. Conflict contract (`execution/conflict_engine.py`, policy `V2_P6_CONFLICT_1`)

`classify(symbol, direction, setup_id, account, orders, entry, stop, target, as_of)` → frozen `ConflictDecision`:
status ALLOW/BLOCK, classification, conflict_kind (`EXISTING_POSITION_CONFLICT` / `EXISTING_PENDING_CONFLICT`),
reason_code, economic_action (`CONTINUE_TO_RISK` / `BLOCK_NEW_EXPOSURE`), requires_risk_evaluation, new candidate
fields, every same-symbol exposure (kind, id, run_id, direction, setup_id or UNKNOWN, entry, SL, TP, quantity) and the
blocking entity (open position first, else the first PENDING order by id).

| Precedence | Classification | Reason code |
|---|---|---|
| invalid direction / no candidate setup_id | INVALID_CANDIDATE | INVALID_DIRECTION / MISSING_NEW_SETUP_ID |
| no same-symbol exposure | NO_CONFLICT (≠ risk approval) | NO_CONFLICT |
| exposure setup_id unknown | UNKNOWN_SETUP_ID | UNKNOWN_EXISTING_SETUP_ID |
| same setup_id | DUPLICATE_SETUP | DUPLICATE_SETUP |
| same direction | SAME_DIRECTION_NEW_SETUP | SAME_DIRECTION_POSITION_CONFLICT / SAME_DIRECTION_PENDING_CONFLICT |
| opposite direction | OPPOSITE_SETUP | OPPOSITE_POSITION_CONFLICT / OPPOSITE_PENDING_CONFLICT |

Path reasons: `RESERVED/approved`, `RISK_REJECTED/<Risk V2 reason>` (e.g. `PORTFOLIO_RISK_LIMIT`,
`ACCOUNT_DRAWDOWN_LIMIT`, `SYMBOL_EXPOSURE_LIMIT`, `UNKNOWN_PENDING_RISK_POLICY`), `DUPLICATE_RUN`
(`run_id_already_submitted` / `run_id_already_decided`), `STALE/STALE_STATE`, `NOT_SUBMITTED/<candidate reason>`.
Authority: reads only. It has no write, close, cancel or SL/TP assignment (source-checked by tests); AI has no access.

## P3. Durable identity lifecycle

Setup (Phase 3 `setup_identity` of the VALID assessment; must equal the explanation's id when present) → order
(stamped in the guarded save, with `risk_policy_version`) → fill (position copies `order.setup_id`) → restart
(decoded unchanged). Legacy/V1/Phase 5-path objects keep `None` → `UNKNOWN_EXISTING_SETUP_ID`. They are never
re-stamped, including after fills and restarts.

## P4. Atomicity (`execution/conflict_path.submit_with_conflict_control`)

Fresh load + B2.3A comparison value → idempotency (an order for the run_id, or a decisive decision row) → classify →
- BLOCK: one `CONFLICT_DECISION` row in a BEGIN IMMEDIATE transaction that first re-verifies the whole PAPER state and
  the absence of a decisive row for the run_id (`commit_decision_rows`). No order, no economic write.
- ALLOW → Phase 5 `prepare_reservation` (the Phase 5 evaluation and order build, extracted unchanged;
  `reserve_and_submit` now delegates to it with identical behavior):
  - REJECTED: `RISK_V2_DECISION` + `CONFLICT_DECISION` rows in one state-verified transaction.
  - APPROVED: order + `RISK_V2_DECISION` + `CONFLICT_DECISION(RESERVED)` in **one** `save_paper(expected_state)`.
- Any state change → nothing written → reload, recompute conflict **and** Risk, once. After a second conflict, see
  P10 (P6.1D). A stale ALLOW or stale BLOCK never leaves a decisive trace.

## P5. Existing management and pending progression (H-6.1 / H-6.2)

- Nothing in the runtime changed. Management (step 2) and pending progression (step 6) are untouched.
- A blocked or rejected Phase 6 decision writes no economic state (whole PAPER state byte-identical; tested), so it
  cannot interfere: after an opposite-setup block, SL and TP hits still close the position through `TradeManager`, and
  a blocked candidate leaves an existing (even legacy) pending order free to fill (tested).
- Future runtime wiring (separate authorization) must call the path only at step 7, after step 6, without feeding
  `final_status` / AI health. At that point it cannot freeze pending progression. No HIGH blocker.

## P6. Concurrency (real processes, `test_phase6_conflict_concurrency.py`)

A same setup_id → one order, loser `DUPLICATE_SETUP`; B/C different ids same direction → loser
`SAME_DIRECTION_PENDING_CONFLICT`; D opposite → loser `OPPOSITE_PENDING_CONFLICT`, no hedge; E cross-symbol → both
reserved, or `PORTFOLIO_RISK_LIMIT` under a 1.5% test cap; F position closes mid-evaluation → stale BLOCK discarded,
recomputed reservation; G pending fills mid-evaluation → recomputed `SAME_DIRECTION_POSITION_CONFLICT`, setup_id
inherited; H crash before commit → nothing durable, retry reserves; I/J crash after commit → durable order + setup_id,
retries `DUPLICATE_RUN`, new run `DUPLICATE_SETUP`; K legacy None stays UNKNOWN; L unknown Phase 5 pending policy
still fails closed.

## P7. Replay (`replay/conflict_audit.py`, observational)

The P6.0 identity view and all six exposure-state counts are reproduced exactly. Engine classification of the 1,940
VALID records: DUPLICATE_SETUP 21 · SAME_DIRECTION_POSITION_CONFLICT 1,020 (= 51 + 969) · OPPOSITE_POSITION_CONFLICT
882 · NO_CONFLICT 17 · pending 0. No thesis, structure or trend labels.

## P8. F06 mapping (evidence, not certification)

| Task | Status / evidence |
|---|---|
| F06-T01 Duplicate setup | implemented: `DUPLICATE_SETUP` on durable setup_id; technical retry = `DUPLICATE_RUN` (separate) |
| F06-T02 Same thesis | **N/A, not determinable from current evidence** (DEC-6.2 A) |
| F06-T03 Same trend / new setup | deterministic subset only: same direction, different setup_id (no trend classification) |
| F06-T04 New structure | **N/A, not determinable** |
| F06-T05 Trend change | **N/A, not determinable** |
| F06-T06 Opposite setup | implemented: `OPPOSITE_SETUP`, block, no close |
| F06-T07 Position conflict | implemented: `EXISTING_POSITION_CONFLICT` / `EXISTING_PENDING_CONFLICT` |
| F06-T08 Existing vs new | classification + trace of both entities |
| F06-T09 Direction + setup_id | both in the decision and the record |
| F06-T10 Entry/SL/TP | recorded for both; never mutated (tests) |
| F06-T11 Existing + new risk | Risk V2 record: portfolio before, proposed reservation |
| F06-T12 Combined risk | portfolio after vs the 2.30% limit |
| F06-T13 Risk revalidation | every economic path goes through Risk V2; a forced wrong ALLOW is still rejected by `SYMBOL_EXPOSURE_LIMIT` |
| F06-T14 Contrary signal | analyzed and blocked; existing position untouched and still managed |

## P9. Limitations and rollback

Not supported (by policy): SAME_THESIS, NEW_STRUCTURE, TREND_CHANGE, more than one position per symbol, hedging,
netting. The V1 runtime still decides `EXISTING_POSITION` / `PENDING_ORDER` exactly as before. Rollback: stop calling
the path. The only durable traces are additive optional fields (ignored by V1, omitted when None) and journal rows; no
migration, no state rewrite.
Code-downgrade limitation (MEDIUM, documented): pre-P6.1 code rejects payloads containing `setup_id` (strict
`paper_decode`), exactly like pre-P5.1C code and `risk_policy_version`. Rolling the code back over a DB that already
holds stamped Phase 6 rows is therefore not supported. The supported rollback is to stop calling the path (current
state: never called by the runtime).

---

# P6.1D — P6-STALE-01 correction (independent P6.2 review, HIGH)

Status: corrected, **pending independent delta review** (author: Claude; not self-certified).

## P10. Root cause and correction

Cause (7648eb9 `submit_with_conflict_control`): after the second lost CAS, the path built the STALE row as
`{**record, ...}`. That copied the last **provisional** record, including the `order_id` of an order built in memory
but never committed. It then wrote the row with an **unguarded** `store.event`, without reading the durable outcome of
the same run_id. If another process had meanwhile committed `RESERVED / SUBMITTED` (or BLOCKED / RISK_REJECTED) for
that run, the journal ended with a contradictory `STALE_STATE / NOT_SUBMITTED` row naming a nonexistent order.

Correction (orchestration only; Phase 5, storage and runtime unchanged):
1. **Authoritative durable outcome wins.** After a double stale, `_after_double_stale` reads, under one BEGIN
   IMMEDIATE, whether the run_id has a committed order or a decisive `CONFLICT_DECISION`. If so, nothing is written
   and the result is `DUPLICATE_RUN` (`run_id_already_submitted` with the committed order, or
   `run_id_already_decided` with the decisive record).
2. **Legitimate stale is non-decisive and sanitized.** With no durable outcome, at most one `CONFLICT_ATTEMPT_STALE`
   row per run_id is written in that same transaction. It is a different event type from `CONFLICT_DECISION`, carries
   `decisive: false`, `economic_action: NONE`, and is built from scratch. It has no `order_id`, `decision`,
   `execution_status`, risk or reservation field. Idempotency ignores it, so a later retry or restart of the run
   decides normally.
3. **Same class, other exits.** `commit_decision_rows` (BLOCKED / RISK_REJECTED) now also refuses when an order for the
   run already exists, besides a decisive row or a changed state. The RESERVED path re-reads decisive rows of the run
   just before its guarded save (removed in P6.1E: superseded by the immutable run claim, P11-P13). Every `DUPLICATE` outcome now reconciles to the durable order/decision.

Terminal-path audit:

| Exit | Write | Guard |
|---|---|---|
| NOT_SUBMITTED | none | — |
| DUPLICATE_RUN | none | — |
| BLOCKED | one decision row | state CAS + no decisive row + no run order, one transaction |
| RISK_REJECTED | risk + decision rows | same |
| RESERVED | order + risk + decision rows | `save_paper(expected_state)` |
| STALE | ≤ 1 sanitized observation | no run order + no decisive row + no prior observation, one transaction |
| exception | none (transactions roll back) | — |

Journal invariants (asserted for every case of `test_phase6_stale_regression.py`): per run_id ≤ 1 order and
≤ 1 decisive decision; no `CONFLICT_DECISION` with `STALE_STATE`; every row naming an order_id references a committed
order of that run; BLOCKED / RISK_REJECTED coexist with no order; observations are non-decisive, carry no economic
identifier, and number ≤ 1.

Old-candidate proof: the permanent regression (5 real-process tests) fails 5/5 on 7648eb9. Case A shows exactly the
reported contradiction: `RESERVED / SUBMITTED order=12e2…` (real) followed by `STALE_STATE / NOT_SUBMITTED
order=7537…` (never existed). It passes 5/5 after the correction.

Residual (separately reported in P6.1D; **superseded by P6.1E / P11**): the RESERVED path's `save_paper` (in the
hash-pinned `storage/database.py`) cannot atomically re-check same-run journal rows. A same-run BLOCKED/RISK_REJECTED row and a
RESERVED order could coexist only if two callers used the SAME run_id with DIFFERENT inputs (report or policy) on the
same PAPER state. Equal inputs on an equal state give the same deterministic decision. The pre-save re-read narrows
the window. The runtime's run_id is one per slot (`claim_slot`), and the path is not wired into the runtime.

---

# P6.1E — Same-run candidate identity & atomic idempotency (independent review, HIGH)

Status: corrected, **pending independent delta review** (author: Claude; not self-certified).

## P11. Root cause

`save_paper(expected_state)` compares only the PAPER economic state. A BLOCKED or RISK_REJECTED decision is
journal-only: it changes no PAPER state. Caller A (run `shared`, EURUSD, NO_CONFLICT) paused before its save, and
caller B (same run, a different XAUUSD candidate) committed `XAUUSD BLOCKED`. A's CAS still matched, so A committed
`EURUSD RESERVED`. That left two candidate truths for one run_id. P6.1D's pre-save re-read only narrowed this window,
and it is removed.

## P12. Candidate identity contract (`execution/candidate_identity.py`)

Fingerprint `V2_P6_CANDIDATE_1` = sha256 of canonical JSON (sorted keys, `,`/`:` separators, ASCII) over:
account_id · symbol · setup_id (Phase 3) · side · planner policy · planned entry / SL / TP and declared R:R ·
plan as_of (UTC ISO; it becomes the order's as_of) · conflict policy version · full risk policy record (versioned
limits and fill semantics) · instrument contract (symbol, price/quantity increments, multiplier).

Numbers are canonical decimals: `Decimal(repr(float))`, normalized, fixed notation, so 2650 == 2650.0. Excluded:
run_id (the key), quantity (a Risk output), timestamps of decisions, warnings, scout evidence. No `repr()`/`hash()` of
objects and no unordered serialization.

## P13. Durable claim

`RUN_CANDIDATE_CLAIM` journal row, same trading DB, schema 3, no DDL. Acquired by check-then-insert inside ONE
`BEGIN IMMEDIATE` (`claim_run`), so it is serialized across processes and connections. It is never updated or deleted,
and the first row is authoritative (only one can exist). Payload: fingerprint version, fingerprint, canonical fields,
`economic_action: NONE`, `decisive: false`. It is not a conflict decision, Risk approval, reservation, submission or
rejection.

| Claim state for the run | Requested fingerprint | Result |
|---|---|---|
| none, no history | any | `CLAIMED` → normal path |
| none, but an order or decisive row exists (pre-claim / Phase 5 API) | any | `NOT_SUBMITTED / RUN_ID_IDENTITY_UNKNOWN`; no claim invented |
| exists | equal | `SAME` → idempotent continuation / reconciliation |
| exists | different | `NOT_SUBMITTED / RUN_ID_CANDIDATE_MISMATCH`; nothing written; claim untouched |

Why no TOCTOU remains: the claim is immutable once written, so a caller that verified "the claim is mine" can never
be contradicted by a different candidate. Every other writer of that run_id has the same fingerprint, so the same
deterministic outcome on the same PAPER state, and a CAS failure on any other state. Defense in depth:
`commit_decision_rows` re-verifies the claim inside its own transaction. Reconciliation (`_reconciled`) refuses
(`RUN_ID_CANDIDATE_MISMATCH`) an order or decisive row that does not match the claim (INV-6.6/6.7). Decision and stale
rows carry `candidate_fingerprint`.

Crash after claim: the claim alone has no economic effect. The same candidate resumes normally; a different one stays
refused (real-process test). `CONFLICT_ATTEMPT_STALE` observations remain non-decisive and are not identity barriers.
P6-STALE-01 stays closed: `test_phase6_stale_regression.py` is unchanged and passes.

Legacy: runs with decisive rows or orders created before claims existed (P6.1/P6.1D, or the identity-unaware Phase 5
`reserve_and_submit`) get no retroactive claim. The Phase 6 path refuses them (`RUN_ID_IDENTITY_UNKNOWN`) and never
rewrites history.

## P14. Invariants and evidence

INV-6.1 one run_id → one candidate · 6.2 different candidate fails closed · 6.3 same candidate converges · 6.4 a claim
is not approval · 6.5 claim-only crash recoverable · 6.6 decisive outcome matches the claim · 6.7 committed order
matches the claim · 6.8 P6-STALE-01 closed · 6.9 no second same-symbol exposure · 6.10 Risk V2 mandatory after
NO_CONFLICT.

- `test_phase6_candidate_identity.py`: fingerprint canonicalization and sensitivity (symbol, setup_id, direction,
  entry, SL, TP, declared R:R, as_of, multiple fields, policy, instrument, account); sequential collisions after
  BLOCKED, RISK_REJECTED, RESERVED and claim-only; policy mismatch; stale observation not a barrier; legacy history
  fails closed; a foreign order under a claimed run is never adopted.
- `test_phase6_candidate_race.py` (real processes): the exact review reproduction; a concurrent matrix (different
  symbol, setup_id, direction, geometry); identical-candidate convergence; claim-only crash + restart. On 6bcf7cc it
  fails 7/7 subtests. The exact case shows `[('XAUUSD', 'BLOCKED'), ('EURUSD', 'RESERVED')]` for one run, and the
  matrix shows the second symptom: the loser adopting a different candidate's order as DUPLICATE_RUN. It passes on the
  corrected HEAD.

The rollback MEDIUM (pre-P6.1 code cannot decode stamped `setup_id`) remains **unresolved, pending independent
adjudication**. The new claim rows are journal rows: older code ignores them.
