# V2 Phase 8 — Execution / Fill / Trade Manager (P8.0 audit, characterization & certification plan)

Baseline `main` `2753130d22abc33e12caf766e8ed5dfba17a8b70` (Phase 7 merged, PR #12; CI 37671422429 success). Branch
`v2/phase8-execution`. PAPER only · REAL DISABLED · NAS100 OFF · schema 3 · `v2_ai_call_audit` / `v2_ai_resilience` /
`v2_position_catch_up` OFF · no deploy · no operational DB touched. P8.0 is audit only: no production code changed.
The only addition is `test_phase8_execution_characterization.py` (4 tests). No F08 task is marked CERTIFIED.

## A. Baseline

`origin/main` = `2753130` (contains Phase 7 final `8391ea3`); CI success; clean worktree; full suite 1001/1001 at
baseline (1005 with the 4 new tests). Phase 8 depends on:
- Phase 2: guarded writers, catch-up, P1 pending gate;
- Phase 4: fill geometry;
- Phase 5: Risk V2 and reservations;
- Phase 6: conflict path and setup_id;
- Phase 7: AI fail-closed.

## B. Execution architecture map (runtime path as it runs today)

```
Scheduler.tick (runtime/scheduler.py; cloud_runner / __main__ loop)
 -> per enabled symbol: OperationalRuntime.run_cycle(symbol, slot)               [runtime/service.py]
    claim_slot (runs.slot_key UNIQUE + symbol_locks)       -> DUPLICATE if claimed / locked
    market data -> fresh_snapshot (slot + wall clock)      -> NO_DATA / STALE_DATA, no order
    position management: TradeManager.process_bar(newest closed 5m bar) via _guarded_paper_write
        (flag ON instead: Evidence ingest + catch_up_position over every committed bar)
    floor (Setup -> Planner -> V1 Risk) -> AI (veto) -> save_reports
    execution-time freshness/session recheck
    pending progression: PaperBroker.process_next_bar(order, current bar) if AI healthy (P1 gate when flag ON)
    submit: paper_policy + no same-symbol PENDING/position + equity unchanged -> PaperBroker.submit_plan
        inside _guarded_paper_write (B2.3A whole-state CAS, 1 retry, STALE_PAPER_STATE)
    record_execution (SUBMITTED / SKIPPED + reason) -> finish (releases symbol lock)
Persistence: storage/database.Store (schema 3): paper_accounts / orders / fills / positions / closed_trades, journal,
    runs, review_reports. save_paper(expected_state) = BEGIN IMMEDIATE compare-and-swap.
Recovery: Store.recover (startup) -> RUNNING runs older than stale_after -> FAILED + lock release + review;
    OperationalRuntime.__init__ refuses orphan / inconsistent positions and orders without an account.
```

Library-only (not on the runtime path):
- fixed-3R planner (Phase 4);
- Risk V2 `evaluate` / `reserve_and_submit` (Phase 5);
- `submit_with_conflict_control` (Phase 6).

## C. F08 matrix

| ID | Expected | Implementation | Existing evidence | Gap / risk | Needed |
|---|---|---|---|---|---|
| T01 SUBMITTED | an approved plan becomes exactly one order, traceably | `submit_plan` creates PENDING + `ORDER_SUBMITTED`; `record_execution` SUBMITTED + order_id | test_execution_observability (submitted then EXISTING_POSITION), test_stale_safe_writers (d/e/f/g/h) | "SUBMITTED" is a journal/execution state, not an order status (LOW, document) | none |
| T02 PENDING | order waits for the next eligible bar | PENDING; progressed by `process_next_bar` on a current-cycle bar after `as_of` | test_execution state machine; test_pending_order_gate; test_runtime_pending_gate | **no time-in-force**: during an AI outage it stays PENDING and can fill much later (characterized: 75 min) (MEDIUM-8.2) | DEC-8.2 |
| T03 FILLED | fill at the next bar open, gates rechecked | `process_next_bar` (V1 R:R ≥ 3 + 1% at fill; V2 policies via rr_policy) | test_execution fill/equity/RR; Phase 4/5 fill tests | — | adversarial fill matrix in P8.1A |
| T04 REJECTED | fill-time gate failure → REJECTED with reason, no position | broker reasons `post_fill_risk_or_geometry` etc. | test_execution post-fill rejects; Phase 4/5 boundary tests | — | none |
| T05 POSITION OPENED | one position per fill, quantity preserved | `POSITION_OPENED`; position keyed by symbol | test_execution quantity/idempotency | one per symbol by policy (DEC-6.4) | none |
| T06 POSITION CLOSED | exact PnL once, equity updated | TradeManager close → ClosedTrade, realized PnL, cost once | test_execution PnL/cost once; test_demo_runner restart close without double accounting | — | none |
| T07 SL | stop applied on the bar it is touched | TradeManager precedence gap-stop > gap-target > stop > target | test_execution LONG/SHORT stop/gap/same-bar | **default runtime manages only the newest 5m bar per 15-min cycle: a stop touched in an intermediate bar is missed** (pinned: test_runtime_catch_up `test_1_15`, "T2 stop never seen") (**HIGH-8.1**) | DEC-8.1 |
| T08 TP | same for target | same | same | same as T07 (HIGH-8.1) | DEC-8.1 |
| T09 Restart | state survives; no duplicate | durable paper tables; recover(); startup consistency checks | test_runtime restart; test_demo_runner restart; **new**: orphan / qty-mismatch startup refusal | — | none |
| T10 Duplicate protection | no double order/fill/close | slot claim, run_id dedupe in submit, CAS, catch-up watermark | test_stale_safe_writers; test_runtime_pending_gate 8/10; Phase 5/6 races | — | restart-duplicate real-process test in P8.1A |
| T11 Multiple positions | multi-symbol exposure is consistent | dict per symbol; aggregate equity | test_execution multi-symbol equity; Phase 5/6 tests | same-symbol multi-position unsupported by policy (Phase 6) | none |
| T12 Simultaneous setups | no Conflict Engine bypass, no double exposure | symbol_locks per symbol; CAS across symbols; Phase 6 path (library) | test_stale_safe_writers i (xau/eur writers); Phase 6 races | — | none |
| T13 Stale data | no order on stale/forming data | fresh_snapshot at slot and wall clock; execution-time recheck | test_runtime freshness; test_demo_runner stale/forming | — | none |
| T14 Provider failure | market or AI failure grants nothing | provider errors → NO_DATA; AI not OK/PARTIAL → not eligible | test_runtime provider crash; test_demo_runner AI failure; Phase 7 suites | — | none |
| T15 Database failure | no phantom/duplicate order; truthful state | all PAPER writes in one transaction; rollback on error | test_stale_safe_writers c/j; test_runtime_catch_up 10; **new**: `finish()` failure | a DB failure inside `finish()` escapes `run_cycle` (fail-stop) and the symbol lock blocks the symbol in-process until a restart recovers it (MEDIUM-8.3) | DEC-8.3 |
| T16 Scheduler restart | no backfill, no double cycle | missed slots counted (`SLOTS_MISSED`), never replayed; slot uniqueness; cloud runner lifetime lock + recover(stale=0) | test_runtime disabled scheduler / duplicate tick; test_cloud_resume | local CLI recovers only runs older than 120 s | none (document) |
| T17 State recovery | interrupted cycles closed truthfully; economics intact | recover() marks FAILED + review; startup refuses inconsistent state | test_demo_runner recovery; test_runtime claim/recovery | — | none |

## D. Findings

- CRITICAL: none.
- **HIGH**
  - **HIGH-8.1:** in the default runtime (flag OFF) SL/TP are evaluated only on the newest closed 5m bar of each
    15-minute cycle; intermediate-bar touches are missed. This is pinned in Phase 2. The certified remedy (B2.3B/C
    chronological catch-up over committed evidence) exists behind `v2_position_catch_up` (OFF). F08-T07/T08 cannot be
    certified for the default path without a decision.
- **MEDIUM**
  - **MEDIUM-8.2:** PENDING orders have no time-in-force. During an AI outage they stay PENDING (by the certified
    Phase 2 / DEC-7.11 policy) and may fill hours later. The V1 fill-time R:R/1% gates still protect geometry and risk.
  - **MEDIUM-8.3:** a DB failure inside `finish()` escapes `run_cycle` (fail-stop). The symbol lock then returns
    DUPLICATE for that symbol in-process until a restart recovers it (cloud runner: stale = 0; local CLI: runs older
    than 120 s). No economic harm.
  - **MEDIUM-8.4 (Phase 6 compatibility gate, carried):** the strict `paper_decode` rejects unknown fields. Rows stamped
    by the Phase 5/6 paths (`risk_policy_version`, `setup_id`) cannot be read by code older than P5.1C/P6.1. No
    runtime row is stamped today (those paths are not wired), so there is no current exposure. It becomes blocking
    when they are wired.
- **LOW**
  - "SUBMITTED" is an execution/journal state, not an order status.
  - The startup orphan check was untested (now characterized).
  - The local CLI 120 s recovery threshold.

## E. Reusable without change

PaperBroker state machine and fill gates; TradeManager (precedence, PnL, cost once); B2.3A guarded writers; Store CAS,
recover(), schema 3; slot claim / symbol locks; scheduler no-backfill; Phase 2 catch-up + P1 pending gate; Phase 4/5
fill policies; Phase 5 Risk V2 + reservations; Phase 6 conflict path; Phase 7 AI fail-closed. Existing tests (≈ 120
across test_execution, test_runtime, test_demo_runner, test_stale_safe_writers, test_runtime_pending_gate,
test_runtime_catch_up, test_cloud_resume, Phase 5/6/7 suites) cover most of F08.

## F. Minimal changes (only after decisions)

- DEC-8.1 A: no production change. P8.2 certifies T07/T08 on the catch-up path through tests with the flag ON
  (activation remains separate).
- DEC-8.2 B: a pending time-in-force (cancel after N closed bars / cycles), a small broker/runtime change that only
  removes fills.
- DEC-8.3 B: release the symbol lock / mark FAILED in an independent best-effort transaction when `finish()` fails; or
  accept fail-stop.
- DEC-8.4 B: a documented rollback procedure plus a test that pre-P6.1 code fails closed (not silently) on stamped
  rows; or a forward-compatible decoder.

## G. P8.1 batches

| Batch | Objective | Files | Risk | Tests | PASS/FAIL | Depends |
|---|---|---|---|---|---|---|
| P8.1A | adversarial certification tests, no production change | new `test_phase8_*` | none | restart-duplicate (real processes), DB-failure injection matrix (save_paper / record_execution / journal / finish), fill matrix, scheduler restart, simultaneous setups across processes | all pass, 0 production diff | P8.0 accepted |
| P8.1B | T07/T08 on the catch-up path (if DEC-8.1 A) | tests only (flag ON in test config) | none | every-bar SL/TP, gap ordering, cadence jumps, restart exactly-once with catch-up | pass; flag still OFF by default | DEC-8.1 |
| P8.1C | pending time-in-force (if DEC-8.2 B) | `execution/paper_broker.py` or `runtime/service.py` | economic (removes fills) | expiry boundary, outage, restart, no duplicate cancel | pass; V1 fill gate unchanged | DEC-8.2 |
| P8.1D | finish-failure liveness (if DEC-8.3 B) | `runtime/service.py` | low | DB failure in finish → lock released / run FAILED; no order | pass | DEC-8.3 |
| P8.1E | compatibility / rollback gate (DEC-8.4) | doc + tests (decoder only if B) | low | old-code-on-new-rows fails closed; restore from copy is idempotent | pass | DEC-8.4 |

Each batch: one commit, exact SHA, CI run id, full suite once, `git diff --check`. Proceed only when the batch PASSes.

## H. P8.2 certification gate (independent reviewer, e.g. Copilot)

Exact SHA; independent audit of the F08 matrix; reviewer-written adversarial tests (restart duplicate, DB failure,
SL/TP intermediate bars on the certified path, pending lifetime per DEC-8.2, simultaneous setups); full regression; CI
PASS; zero open CRITICAL/HIGH affecting certification (HIGH-8.1 resolved by DEC-8.1); PAPER/REAL/NAS100/schema
confirmed; evidence of recovery and idempotency; PASS/FAIL report. The author does not certify.

## I. Rollback and compatibility

| Item | Status |
|---|---|
| Schema / migrations | 3; migration from 1/2 tested; incompatible schema fails without destroying data |
| Code ↔ DB | current code reads all legacy rows (identity fields default None); older code cannot read Phase 5/6-stamped rows (MEDIUM-8.4); no such runtime rows exist today |
| Pending orders / fills | survive restart (tests); progressed only on a current-cycle bar; idempotent fill |
| Open positions | survive restart; startup consistency checks; catch-up watermark prevents re-processing |
| Isolated restore | restore a copy, open read-only or with recover(); never against the operational DB in P8 |
| Idempotency after restore | slot claim + run_id + CAS + watermark; tested for restart, not yet for a restored copy (P8.1E) |

## J. Owner decisions

| ID | Problem | Options | Recommendation |
|---|---|---|---|
| DEC-8.1 | T07/T08 vs newest-bar-only default (HIGH-8.1) | A: certify T07/T08 on the certified catch-up path in tests (activation stays separate) · B: accept the limitation for the V1 path · C: change the V1 path | **A** |
| DEC-8.2 | pending without time-in-force | A: keep (document) · B: expire after N closed bars/cycles | **B with small N** (economic: removes late fills); or A if the Owner prefers no economic change in Phase 8 |
| DEC-8.3 | finish() DB failure liveness | A: accept fail-stop (restart recovers) · B: best-effort lock release / FAILED mark | **A** (document), B optional |
| DEC-8.4 | Phase 6 rollback compatibility | A: documented rollback procedure + fail-closed test · B: forward-compatible decoder | **A** |
| DEC-8.5 | certification target | A: certify the V1 runtime path as running + library paths by tests · B: wait for V2 path wiring | **A** |

Recommendation: **READY** for P8.1 once DEC-8.1 … DEC-8.5 are decided (P8.1A can start immediately, since it is
tests only).

---

# P8.1A — Adversarial tests (tests only; flags OFF; no production change)

Owner decisions applied:
- **DEC-8.1:** `v2_position_catch_up` stays OFF in the runtime. HIGH-8.1 stays OPEN for the default runtime. The
  chronological path is tested only in isolation (P8.1B), and an activation gate is required before any DEMO.
- **DEC-8.2:** no pending time-in-force. The late-fill risk is documented (MEDIUM-8.2 accepted as existing policy).
- **DEC-8.3:** investigate finish() failures; propose, do not implement.
- **DEC-8.4:** rollback procedure + fail-closed tests, no runtime change.
- **DEC-8.5:** certification of the current runtime and of isolated V2 components are separate. Nothing that depends
  on an OFF flag is certified as active.

## Tests added

`test_phase8_adversarial.py` (12, in-process) and `test_phase8_process_races.py` (4, real processes). Every scenario
asserts the PAPER invariants: ≤ 1 order per run, no fill without a FILLED order, every open position has a FILLED origin
order with equal quantity, a restart succeeds, and a re-run of the slot is DUPLICATE.

| Area | Scenario | Result |
|---|---|---|
| SQLite failure matrix | injected failure in `save_reports` / submit `save_paper` / `record_execution` / `record_analysis_events` | first two: cycle ERROR, **0 orders**; audit-only: decision stands, **1 order**; next slot after restart never duplicates |
| | `finish()` raising after the order committed | the order stands once. The exception escapes the cycle; the lock is held until restart. Recovery marks the run FAILED / `interrupted_run` while the review keeps `execution: SUBMITTED` (DEC-8.3) |
| | journal failure inside the economic save (fill) | whole fill rolled back atomically; a later cycle fills exactly once |
| Fills / transitions | submit → fill → terminal; restarts | 1 order, 1 fill, 1 position; never refilled; REJECTED/CANCELLED never progressed |
| Scheduler / locks | same-slot tick twice | `["PLAN_READY"]`, then `["DUPLICATE"]`; 1 order |
| | hard interruption (finish never runs) | lock kept at a restart 1 min later (120 s threshold); recovered 15 min later |
| Restart duplication (real processes) | crash **after** the submit commit (exit before record/finish) | 1 PENDING order; restart recovers (run FAILED `interrupted_run`), fills it once; further restarts DUPLICATE |
| | crash **before** the submit COMMIT (exit inside the transaction) | 0 orders, 0 ORDER_SUBMITTED; the next slot submits exactly once |
| Lock race (real processes) | two processes, same slot | one cycle, one run, one order |
| | process B recovers a still-live run of process A (local runtime, no lifetime lock) | B trades; A loses slot ownership → ERROR with no economic write; **exactly 1 order** (ownership is re-checked inside `save_paper`'s transaction) |
| Conflicts | pending + new same-symbol setup; open LONG + opposite SHORT | `PENDING_ORDER`; `EXISTING_POSITION`; 1 order; LONG untouched |
| PAPER safety | REAL constant, NAS100, cloud runner, flags | `REAL_EXECUTION_ENABLED` False; NAS100 not enabled and not in PAPER contracts (cycle raises); no live-broker class; all V2 flags False from env; cloud runner refuses without activation env |
| Rollback (DEC-8.4) | a row with an unknown future field | startup fails closed (`invalid paper payload`); the row is not rewritten |
| | Phase 5/6-stamped rows | read by current code |
| | restore from a WAL-checkpointed copy | the next slot runs once on each copy; reruns DUPLICATE; identical order states |

## DEC-8.3 investigation result

- No phantom or duplicate order at any injected point. Failures before the submit commit leave no order; failures
  after it leave exactly one.
- `finish()` failure: fail-stop. The exception leaves `run_cycle`/`tick`. The cloud runner exits and restarts with
  `recover(stale=0)` under its lifetime lock. The local CLI recovers runs older than 120 s.
- Truthfulness nuance (MEDIUM-8.3, unchanged): after such a recovery the run is `FAILED / ERROR / interrupted_run`,
  while the review's `execution` record (SUBMITTED + order_id) and the journal `ORDER_SUBMITTED` show the order. The
  economic truth is in the order tables and journal. The run status describes the process interruption.
- **Proposed minimal change (not implemented, optional):** none required for correctness. If the Owner wants a
  truthful run label, `recover()` could set `error = "interrupted_after_execution"` when a review execution record
  exists. That touches the hash-pinned `storage/database.py`, so it needs an explicit authorization and a pinned-hash
  update. Recommendation: **accept fail-stop + document (option A).**
- LOW-8.5 (new): when startup fails closed on an undecodable row, `OperationalRuntime.__init__` does not close its
  Store (the connection is released only by garbage collection). Harmless to economics. A one-line fix can be made if
  authorized.

## DEC-8.4 rollback procedure (no runtime change)

1. Stop the single scheduler authority (SIGTERM; the cloud runner lifetime lock guarantees one writer).
2. `PRAGMA wal_checkpoint(FULL)`; copy the DB; `PRAGMA integrity_check` on the copy.
3. Detect forward rows:
   `SELECT COUNT(*) FROM paper_orders WHERE payload LIKE '%"setup_id"%' OR payload LIKE '%"risk_policy_version"%'`
   (same for `paper_positions`).
4. If 0: rolling back to any schema-3 code is compatible. If > 0: the target code must be ≥ P5.1C / P6.1. Older code
   fails closed at startup by design (tested). Never edit or strip rows.
5. Restore = start the chosen code on the copy. `recover()` closes interrupted runs. Run one cycle, verify the
   invariants, and verify a rerun of the slot is DUPLICATE (tested).

## DEC-8.5 certification separation

| Scope | Certifiable now | Basis |
|---|---|---|
| Current runtime (flags OFF): T01–T06, T09–T17 | yes (P8.2) | existing + P8.1A tests |
| Current runtime: T07/T08 | **no**: HIGH-8.1 open (newest bar only) | DEC-8.1 |
| Current runtime: T02 late fills | certify with accepted risk | DEC-8.2 |
| Isolated V2 components (catch-up, P1 gate, Risk V2, conflict path, AI resilience) | as components, flag ON in tests only, never as active runtime behavior | P8.1B |

## Recommendation for P8.1B

Isolated flag-ON certification suite for the chronological path (tests only; `v2_position_catch_up` OFF by default):
- SL/TP touched in intermediate bars, for LONG and SHORT, gap ordering and same-bar precedence;
- cadence jumps and missed cycles;
- restart exactly-once and real-process races with catch-up;
- P1 pending gate interplay;
- Evidence Store failure fail-closed;
- combination with an AI outage;
- equivalence of final P&L versus a bar-by-bar oracle.

Plus a written **activation gate** for the future DEMO:
- the preconditions to flip the flag (separate evidence path, backup, Owner approval);
- a first-cycle verification checklist;
- the rollback (flag OFF) steps.

Its exit criterion is HIGH-8.1 closable for the flag-ON path while it stays explicitly open for the flag-OFF runtime.

---

# P8.1B — Isolated certification of the chronological SL/TP catch-up (tests only)

`v2_position_catch_up` is ON **only** inside `test_phase8_catch_up_certification.py`, on temporary trading and
evidence databases. Every other flag stays OFF; runtime defaults and `from_env` are unchanged (asserted). No
production code changed.

**Independent oracle.** Decimal arithmetic and its own reading of the documented precedence. Per bar, in time order:
open beyond SL → exit at open; open beyond TP → exit at open; SL touched → exit at SL; TP touched → exit at TP; SL
before TP when both are touched in one bar. It does not import or call `TradeManager`. Each run is compared on exit
bar, exit price, reason, net PnL, realized PnL, equity and the number of `POSITION_CLOSED` events.

## Evidence

Shared setup:
- Position opened 13:05, quantity 10, multiplier 1, cost 0.
- Cycle slot 13:30; managed bars B1–B4 = 13:10, 13:15, 13:20, 13:25.
- Flat bar = 100.00 / 100.05 / 99.95 / 100.00.
- LONG: entry 100, SL 95, TP 110. SHORT: entry 100, SL 105, TP 90.
- Persistence for every row = one `POSITION_CLOSED` journal row, a closed trade, and the position row CLOSED.

| # | Scenario | Bar sequence (non-flat bars, O/H/L/C) | Expected (oracle) | Observed | Position | SL/TP | PnL | Result |
|---|---|---|---|---|---|---|---|---|
| 1 | LONG SL touched in an intermediate bar | B2 100/100.5/94/96 | B2 @95 stop | B2 @95 stop | CLOSED | SL | −50 | PASS |
| 2 | LONG TP touched in an intermediate bar | B2 100/111/99.5/108 | B2 @110 target | same | CLOSED | TP | +100 | PASS |
| 3 | SHORT SL touched in an intermediate bar | B3 100/106/99.5/104 | B3 @105 stop | same | CLOSED | SL | −50 | PASS |
| 4 | SHORT TP touched in an intermediate bar | B2 100/100.5/89/91 | B2 @90 target | same | CLOSED | TP | +100 | PASS |
| 5a | LONG gap below SL | B2 93/94/92/93.5 | B2 @93 stop (open) | same | CLOSED | SL (gap) | −70 | PASS |
| 5b | LONG gap above TP | B2 112/113/111.5/112.5 | B2 @112 target (open) | same | CLOSED | TP (gap) | +120 | PASS |
| 5c | SHORT gap above SL | B2 107/108/106.5/107.5 | B2 @107 stop (open) | same | CLOSED | SL (gap) | −70 | PASS |
| 5d | SHORT gap below TP | B2 88/89/87.5/88.5 | B2 @88 target (open) | same | CLOSED | TP (gap) | +120 | PASS |
| 6a | LONG SL and TP in the same bar | B2 100/111/94/100 | B2 @95 stop (existing precedence) | same | CLOSED | SL | −50 | PASS |
| 6b | SHORT SL and TP in the same bar | B3 100/106/89/100 | B3 @105 stop | same | CLOSED | SL | −50 | PASS |
| — | no touch | all flat | open | open | OPEN | — | 0 | PASS |
| 7 | cadence jump (13:30 cycle missed) | 13:40 100/100.4/94.5/95.5; cycles 13:15, 13:45 | each bar 13:10–13:40 processed exactly once; 13:40 @95 stop | same (bar spy) | CLOSED | SL | −50 | PASS |
| 8 | crash BEFORE the close commit (real process, exit inside the transaction) | scenario 1 | nothing committed; restart closes at B2 @95 | same | OPEN → CLOSED | SL | −50 | PASS |
| 9 | crash AFTER the close commit (real process) | scenario 1 | durable close; 2 restarts add nothing | 1 close, 1 realized PnL | CLOSED | SL | −50 | PASS |
| 10 | two processes compete (A computes the close and pauses; B recovers A's stale lock 15 min later and closes) | scenario 1 | one close; A loses ownership → ERROR, no write | same | CLOSED | SL | −50 | PASS |
| 11 | pending order, no position | B2 dip 94 | fills only on the current gated bar (B4); B1–B3 journaled PENDING_NOT_EVALUATED; the B2 dip before the fill is ignored | same | OPEN at B4 | — | — | PASS |
| 12 | evidence persistence failure, then recovery | scenario 1 | cycle 1: EVIDENCE_UNAVAILABLE, no economics; cycle 2 closes at the true bar B2 (not the newest) | same | CLOSED | SL | −50 | PASS |
| 13 | AI provider failure during management | scenario 2 + a VALID setup | close still applied; no new order | same | CLOSED | TP | +100 | PASS |
| 14 | 40 randomized LONG/SHORT 4-bar sequences (seed 20261007; open/high/low/close ±6–7) | random | oracle | equal in all 40 (exit bar, price, reason, PnL, equity, one close) | mixed | mixed | mixed | PASS |
| 15 | same cycle repeated | scenario 1 | DUPLICATE; next slot changes nothing | same | CLOSED | SL | −50 | PASS |
| 16 | full traceability | submit 13:15 (V1 plan 100/90/130) → fill 13:25 gate bar → 13:45 dip → 14:00 catch-up | one run_id across ORDER_SUBMITTED → ORDER_FILLED(fill_id) → POSITION_OPENED(position_id) → POSITION_CLOSED(trade_id); `origin_order_id` and `position_id` linked; close 13:45 @90 stop | same | CLOSED | SL | −10 × qty | PASS |
| 17 | (extra) provider revises an already committed bar | B2 flat first, later revised to a stop touch | first committed bar wins (Phase 2); REVISION anomaly; no close | same | OPEN | — | 0 | PASS (LOW-8.6) |
| B | default runtime (flag OFF), scenarios 1–4 | same data | — | position stays OPEN (newest bar only) | OPEN | missed | — | characterized |

## HIGH-8.1 status

- **Path A (flag ON, isolated tests):** every scenario passes against the independent oracle, including crashes, races,
  cadence jumps, evidence failure, AI failure and 40 randomized sequences. The evidence supports **closing HIGH-8.1
  for the flag-ON path, subject to independent confirmation in P8.2** (the author does not self-certify).
- **Path B (default runtime, flag OFF):** **HIGH-8.1 remains OPEN.** The same data leaves positions open after
  intermediate SL/TP touches (characterized). A PASS on A does not protect B.

## New findings

- **LOW-8.6:** a provider revision of an already committed bar is handled by the certified "first committed wins"
  rule and recorded only in `evidence_anomalies` (REVISION). It is not surfaced in the trading journal or health. A
  stop visible only in revised data is not applied.
- No CRITICAL / HIGH / MEDIUM new.

## Future activation gate for `v2_position_catch_up` (documented only; nothing activated)

1. **Preconditions:**
   - P8.2 independent PASS for path A;
   - an Owner decision to activate;
   - a separate `market_evidence_path` on durable storage (never the trading DB);
   - backups of both DBs;
   - the single scheduler authority stopped.
2. **Data compatibility:** schema 3; Evidence Store schema present or creatable; no stamped forward rows that the
   deployed code cannot read (DEC-8.4 procedure).
3. **Existing positions:** for each open position, record `opened_at`, `last_processed_at`, SL and TP. The first
   flag-ON cycle processes every committed bar after the watermark. Expect immediate closes if intermediate touches
   happened while the flag was OFF. Compute the expected result with the independent oracle **before** activation.
4. **First controlled cycle:** one symbol, PAPER only, at a session slot. Watch for `EVIDENCE_UNAVAILABLE` (must be
   absent), bars processed equal to bars expected, and no duplicate journal rows.
5. **SL/TP validation:** compare the closes and PnL of the first N cycles with the oracle over the committed evidence
   (exact match required).
6. **Logs and persistence:** the evidence watermark advances; positions' `last_processed_at` equals the newest
   committed bar; `PENDING_NOT_EVALUATED` rows only for non-current bars; no REVISION anomalies left unreviewed.
7. **Rollback:** stop the scheduler; set the flag OFF (config only; fingerprint returns to V1); keep the Evidence Store
   (read-only, for audit). Positions stay valid: the V1 path continues from `last_processed_at`. No data rewrite.
8. **Immediate stop criteria:**
   - any oracle mismatch;
   - a duplicate close or realized PnL;
   - `STATE_INCONSISTENCY`;
   - repeated `EVIDENCE_UNAVAILABLE` / `STALE_PAPER_STATE`;
   - a position closed on a bar at or before its `opened_at`;
   - any order not explained by the normal submit path.

## Recommendation for the next batch

P8.1C/D/E are not needed under the current decisions (DEC-8.2 no TIF; DEC-8.3 fail-stop accepted; DEC-8.4 procedure
documented). Recommended next step: **P8.2 independent certification** with the separation of DEC-8.5:
- current runtime T01–T06, T09–T17 (T02 with the accepted late-fill risk);
- T07/T08 certified only for path A as an isolated component;
- HIGH-8.1 open for path B.

Optionally, with authorization: the two one-line LOW fixes (LOW-8.5 Store close on fail-closed startup; DEC-8.3 run
label).

---

# P8.3 — HIGH-8.1 closure preparation and final-certification plan (no activation)

## 1. Independent P8.2 verdict: recorded scope

The full P8.2 report was **not available to this agent**: it is not on the branch, in a PR, in commit comments or in
the docs. Only what the Owner's P8.3 brief states is recorded: HEAD `0744868573e1e3e76fa070eefeaa9781f03e70ed` is
"partially certified" by Copilot P8.2. The exact PASS/FAIL per F08 task and any findings of that review must be
attached to this document by the Owner. Nothing below assumes them.

## A. Final diagnosis of HIGH-8.1

- **Path B (operational runtime, `v2_position_catch_up` OFF): OPEN.** Each 15-minute cycle passes only the newest
  closed 5m bar to `TradeManager`. SL/TP touches in the other two bars are never evaluated (pinned since Phase 2;
  re-characterized in P8.1B and P8.3).
- **Path A (catch-up ON, isolated):** the P8.1B evidence (independent oracle, crashes, races, 40 randomized cases)
  supports closure as a component, subject to the independent review.
- **New activation facts (P8.3, tested on temporary DBs):**
  1. **No retroactive correction.** The catch-up watermark is `last_processed_at`, which the flag-OFF path advances
     to the newest bar every cycle. Touches missed **before** activation sit behind the watermark and are never
     revisited (test a). Activation stops new misses; it does not repair past ones.
  2. Touches **between** the last flag-OFF cycle and the first flag-ON cycle are closed by the first activation cycle,
     at the true bar and price (test b). With the flag OFF the same data is missed (test b2).
  3. Pending orders across activation: filled only on the current gated bar; bars strictly after `as_of` journaled
     `PENDING_NOT_EVALUATED` (test c).
  4. Provider revisions after activation are detectable (REVISION anomaly) and not applied (test d).
  5. Rollback to flag OFF is clean: no duplicate processing; the Evidence Store is left intact. From then on, misses
     return (test e).
  6. **The activation route does not exist yet:** `RuntimeConfig.from_env()` never reads the flag or an evidence path,
     and `cloud_preflight` does not validate an evidence store on the durable mount. Activating on Render therefore
     needs an authorized production change.

## B. Minimal resolution plan

| Step | Change | Type | Authorization |
|---|---|---|---|
| R1 | `from_env`: read `AI_FLOOR_V2_POSITION_CATCH_UP` (`"1"` only) and `AI_FLOOR_MARKET_EVIDENCE_PATH`; default OFF; existing validation keeps rejecting a missing or identical path | production (config only) | **DEC-8.6** |
| R2 | `cloud_preflight`: when the flag is ON, require the evidence path on the durable mount, distinct from the trading DB, writable, schema-valid | production (preflight only) | DEC-8.6 |
| R3 | read-only **activation preview** tool: from copies of the trading DB and the evidence/provider bars, list open positions, watermarks, pending orders, oracle-expected closes after the watermark, historic divergence before it, and REVISION anomalies | offline tool (not runtime) | DEC-8.6 |
| R4 | surface REVISION anomalies in health/journal (LOW-8.6) | production (observability) | DEC-8.8 B |
| R5 | activation itself (Render env + restart) | operational | separate Owner GO |

No economic rule, strategy, Risk, AI or Conflict behavior changes in R1–R4.

## C. Activation risks

| Risk | Effect | Mitigation |
|---|---|---|
| historic divergence (touches missed before activation) | positions that "should" be closed stay open; PnL differs from a bar-by-bar view | R3 preview reports them; DEC-8.7 decides the treatment; never auto-closed |
| first-cycle retroactive closes (touches since the last flag-OFF cycle) | immediate closes at the activation cycle | expected, oracle-predicted by R3 before activation |
| evidence store on non-durable disk | catch-up loses its record at restart | R2 preflight blocks it |
| REVISION anomalies | provider corrections silently ignored | E below; DEC-8.8 |
| evidence failure | `EVIDENCE_UNAVAILABLE` blocks PAPER economics for the symbol | fail-closed by design; stop criterion if repeated |
| larger cycle work (ingest + catch-up) | longer cycle | measured in the controlled first cycle |
| AI outage freezes pending orders | unchanged policy (DEC-7.11) | none (policy) |
| rollback | misses resume | documented; flag OFF only, no data rewrite |

## D. Isolated simulation procedure (before any activation)

1. Obtain a **copy** of the operational trading DB (never the original; Owner-provided). Checkpoint WAL, then run
   `integrity_check`.
2. Obtain the 5m bars (provider export or Evidence Store copy) covering every open position since `opened_at`.
3. Run R3 (or, until R3 exists, the procedure of `test_phase8_activation_simulation.py`) on the copy:
   - per open position: watermark, oracle result over **[opened_at, now]** (full history) and over
     **(watermark, now]** (what activation will do);
   - per pending order: `as_of`, expected gate bar;
   - REVISION anomalies.
4. Execute one flag-ON cycle **on the copy** with the real runtime (temporary evidence path).
5. Reconcile: closes, exit bars, prices, realized PnL and equity must equal the oracle for (watermark, now]. Any
   full-history divergence is reported for DEC-8.7.
6. Repeat the cycle (must be DUPLICATE), then one more slot (no duplicate close), then rollback to OFF on the copy
   (clean).
7. Record SHA, inputs (hashes), outputs and PASS/FAIL. Covered today by `test_phase8_activation_simulation.py`
   (6 tests) on synthetic data.

## E. REVISION anomalies

- Detection (read-only): `SELECT symbol, timeframe, bar_start, kind, payload FROM evidence_anomalies WHERE
  kind='REVISION'` (helper `revisions()` in the simulation tests).
- Review before DEMO, for each revision:
  - does the revised bar cross an open or historic position's SL/TP that the committed bar did not?
  - classify it as benign (no level crossed) or material (a level crossed);
  - record the decision.
- Gate: zero **unreviewed** revisions, and zero material revisions without an Owner decision. The certified rule
  ("first committed wins") is kept; nothing is rewritten.
- Optional R4: journal/health surfacing so revisions are visible in operations.

## F. Conditions for final Phase 8 certification

Formal certification **cannot** honestly be issued while path B carries an open HIGH on F08-T07/T08, which are core
Phase 8 tasks. Options:
1. Keep Phase 8 **BLOCKED** (recommended). Record the component evidence (T01–T06, T09–T17 on the current runtime;
   T07/T08 for path A) as partial results.
2. Certify Phase 8 **only after** R1–R3 + DEC-8.7/8.8 + a successful controlled activation (D on a copy, then the
   operational first cycle under the P8.1B gate) and a new independent review.

Criteria for the final independent review:
- exact SHA;
- F08 matrix re-run;
- reviewer-written adversarial tests on the activated configuration;
- activation-preview evidence on an operational copy;
- first-cycle reconciliation equal to the oracle;
- zero unreviewed REVISIONs;
- full suite and CI PASS;
- PAPER, REAL OFF, NAS100 OFF, schema 3;
- no open CRITICAL/HIGH;
- PASS/FAIL report.

## G. Owner decisions pending

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| DEC-8.6 | authorize R1–R3 (activation route + preflight + preview tool; flag still OFF) | A authorize · B defer | **A** |
| DEC-8.7 | historic divergence before activation | A report only, never auto-close · B Owner-run manual reconciliation (outside the automated system) | **A** |
| DEC-8.8 | REVISION handling | A review gate only · B gate + R4 surfacing | **B** |
| DEC-8.9 | Phase 8 status until activation | A BLOCKED with partial component evidence · B certify with open HIGH | **A** |
| DEC-8.10 | activation scope | A one symbol first (XAUUSD), then EURUSD · B both | **A** |
| — | attach the P8.2 independent report | — | required |

## H. Recommendation

- **Activation: NO-GO now.** No route exists without a production change; the P8.2 report is not attached;
  historic-divergence and REVISION reviews on an operational copy have not been done.
- **GO** for DEC-8.6 (R1–R3 implementation with the flag still OFF) as the next batch, followed by D on an
  Owner-provided DB copy.
- **Phase 8 status: BLOCKED** (HIGH-8.1 OPEN on the operational runtime; path A evidence recorded).

---

# P8.2 independent verdict (Copilot), recorded as received from the Owner

Candidate `0744868573e1e3e76fa070eefeaa9781f03e70ed`; CI 37720604915 SUCCESS; no production change since `main`.

| Area | Verdict |
|---|---|
| T01, T03–T06, T09–T11, T13, T14, T16, T17 | PASS |
| T02 | PASS with accepted risk (no time-in-force) |
| T12 | PASS with scope (CAS/locks; not a certification of a runtime-wired Conflict Engine) |
| T15 | PASS with accepted fail-stop |
| T07 / T08 | **Path A (isolated catch-up) PASS; Path B (flag-OFF runtime) FAIL** |
| HIGH-8.1 | closed only for the catch-up component; **OPEN for Path B** |
| MEDIUM | 8.2 (pending without TIF), 8.3 (fail-stop + run/review mismatch), 8.4 (rollback to old code) |
| LOW | 8.5 (Store not closed on failed constructor); 8.6 (REVISION visibility: a **mandatory pre-DEMO condition**) |
| Global | **BLOCKED** for an integral runtime certification or future DEMO |

**Correction to earlier author reports.** CI and the reviewer's run show **1035 tests with 1 skip**. The skipped test
(`test_system_health … frozen_v1_baseline_code_opens_trading_db_after_sidecar_use`) needs `git` on `PATH`; it passed
when run separately. The author's local runs had `git` on `PATH` and reported 1035 OK. Both are true; "0 skipped" holds
only for that local environment.

# P8.4 — Safe activation preparation (no activation). Owner decisions DEC-8.6 … DEC-8.10 approved.

## Batch 1 — R1 configuration + R2 preflight

- **R1** (`runtime/config.py`, `from_env`): `v2_position_catch_up` is enabled **only** by the exact value
  `AI_FLOOR_V2_POSITION_CATCH_UP="1"`, together with `AI_FLOOR_MARKET_EVIDENCE_PATH`.
  - Unset or `"0"` → OFF.
  - Any other value (`true`, `yes`, `2`, `" 1"`, …) → `ValueError`, so startup fails closed instead of guessing.
  - `"1"` without an evidence path, or with a path equal to the trading DB → `ValueError` (existing validation).
  - An evidence path alone never enables anything.
  - The AI flags still have no env route.
  - The default remains OFF. The fingerprint changes when ON, so a running experiment's cloud preflight reports
    configuration drift (`experiment_resumable` False). That is an additional barrier against accidental activation.
- **R2** (`runtime.config.catch_up_storage_checks`, used by `cloud_preflight` and the local doctor `preflight`). It adds
  checks **only when the flag is ON**, so the OFF reports are byte-identical:
  - `catch_up_evidence_path_set`;
  - `catch_up_evidence_separate` (different path and file name from the trading DB);
  - `catch_up_evidence_durable` (on the durable mount);
  - `catch_up_evidence_writable_schema`: an existing store opens with the evidence schema and accepts a write probe;
    an absent store needs a writable parent and is **never created by the preflight**.
  - Any False → `NOT_READY`.
  - The check lives in `runtime/config.py`, so the certified Phase 2 inventory tests (only `runtime/config.py` and
    `runtime/service.py` reference the evidence/catch-up modules) still hold unchanged.
- **No silent degradation**: with the flag ON and an unusable Evidence Store, the cycle journals
  `EVIDENCE_UNAVAILABLE` and makes **no** `TradeManager` call. There is no newest-bar fallback (tested).
- Tests: `test_phase8_activation_route.py` (10): defaults, exact-value parsing, ambiguous values fail closed, the
  preflight matrix (absent/existing/garbage/outside mount/missing parent/same name/not mounted), cloud and local
  NOT_READY, no fallback, and an env-built config running only in a temporary directory with a clean restart
  (DUPLICATE). Full suite 1051.

Batch 1 SHA: `4fe10bdd3a9017a2af2775c52c4e72d091db73b5` (CI 37779990370 success).

## Batch 2 — R3 read-only activation preview (`replay/activation_preview.py`)

- **Read-only by construction.**
  - Each source (trading DB, optional Evidence DB) is copied into a private temporary directory with SQLite's backup
    API from a `mode=ro` connection; only the copies are read (`Store(..., readonly=True)`).
  - Source files (and `-wal`/`-shm`) are hashed before and after; the report carries `source_unchanged`.
  - The CLI refuses to write its report over a source.
  - The module contains no write SQL and never calls `TradeManager`, `process_bar` or `save_paper`
    (source-checked).
- **Report `V2_P8_ACTIVATION_PREVIEW_1`:**
  - per open position: watermark, `historic_missed` (DEC-8.7: REPORT ONLY; activation never revisits those bars),
    `catch_up_bars` (what the first flag-ON cycle processes), `expected_close` (bar, price, reason, net PnL),
    `coverage_gaps`, `in_first_activation_scope` (DEC-8.10: XAUUSD);
  - `expected_realized_after_first_cycle`;
  - pending orders (gate bar, bars that would be journaled not evaluated);
  - REVISION anomalies;
  - risks: HISTORIC_DIVERGENCE, IMMEDIATE_CLOSE_EXPECTED, MISSING_BARS, NO_BAR_DATA,
    OUTSIDE_FIRST_ACTIVATION_SCOPE, PENDING_ORDERS_PRESENT, REVISION_PRESENT.
- **Bars** come from an Evidence DB copy and/or a JSON export
  (`[{symbol, bar_start, open, high, low, close}]`) for periods the Evidence Store never saw (e.g. positions opened
  while the flag was OFF).
- **Independent oracle** (Decimal, own precedence, includes the position's cost): the preview's prediction equals the
  real first flag-ON cycle run on a separate copy (exit bar, price, reason, net PnL, realized PnL), and the pending
  order's predicted gate bar and not-evaluated bars equal the runtime's (tested).
- **Usage:** `python -m replay.activation_preview --trading-db COPY.db --as-of 2026-…Z [--evidence-db EV_COPY.db]
  [--bars-json bars.json] [--first-scope XAUUSD] --out report.json` (exit 0 = sources unchanged).
- Tests: `test_phase8_activation_preview.py` (5). Full suite 1056.

Batch 2 SHA: `fc5f8b6ddbbedecdab3f0b49d6629062cf00089a` (CI 37780913252 success).

## Batch 3 — R4 REVISION visibility + pre-DEMO gate (`runtime/revision_review.py`)

- **Unchanged certified rule:** the first committed bar stays authoritative; nothing is rewritten, moved, closed or
  opened.
- **Runtime hook** (flag ON path only, after a successful ingestion, through `_audit_safely`, so a failure never
  changes the cycle):
  - each revision reported by this cycle's ingestion is classified by comparing the presented snapshot bar with the
    committed bar;
  - **MATERIAL** = the presented 5m bar crosses an open position's SL or TP (open beyond or high/low touch) that the
    committed bar did not, for a position opened before the bar; 1h/15m revisions are non-material (they never feed
    position management);
  - one `EVIDENCE_REVISION` journal row per (symbol, timeframe, bar_start, presented OHLC), deduplicated across cycles
    and restarts (WARNING if material);
  - system_state `evidence_revisions` = {recorded, unreviewed, material_unreviewed} for health views.
- **Owner review:** `python -m runtime.revision_review review --trading-db … --key <review_key> --decision
  ACCEPTED_FIRST_COMMITTED|ESCALATED --reviewer …` writes `EVIDENCE_REVISION_REVIEWED`. Run it against the
  operational DB only during the Owner's review.
- **Pre-DEMO gate** (read-only, `mode=ro`): `python -m runtime.revision_review gate --trading-db … --evidence-db …`
  - **BLOCKED** (exit 3) while any material revision is unreviewed, or any REVISION anomaly of the evidence DB has no
    journal classification (UNCLASSIFIED; e.g. recorded before R4 or after a classification failure);
  - non-material unreviewed revisions are listed as warnings;
  - CLEAR = exit 0.
- **Module boundary:** it reads `evidence_anomalies` with plain SQLite and receives the evidence engine object from the
  runtime, so the certified Phase 2 inventory tests are unchanged.
- **Superseded characterization (explicit):** P8.1B scenario 17 pinned LOW-8.6 ("revision not in the trading
  journal"). It now asserts the `EVIDENCE_REVISION` row. Its "first committed wins / position stays open" assertions
  are unchanged.
- Tests: `test_phase8_revision_review.py` (6): benign → visible, gate CLEAR with a warning; material → BLOCKED until
  reviewed, positions untouched; no duplicates across cycles/restart; classification failure → UNCLASSIFIED blocks and
  the cycle is unchanged; gate read-only (hashes) and CLI exit codes; flag OFF records nothing. Full suite 1062.

## P8.4 status

| Item | Status |
|---|---|
| R1 route | done; OFF by default; exact `"1"` + separate evidence path; ambiguous values fail closed |
| R2 preflight | done; fail-closed Evidence Store checks only when ON; no fallback |
| R3 preview | done; read-only on copies; predictions equal a real first cycle |
| R4 REVISION | done; journal + health state + review + pre-DEMO gate |
| Activation | **not performed**; no Render change; no deploy |
| HIGH-8.1 | **OPEN** for the operational runtime (flag OFF); closed only for the isolated catch-up component (P8.2) |
| Phase 8 | **BLOCKED** (DEC-8.9) |

## Open risks

1. **DEC-8.10 vs the current runtime shape.** The flag is global over `enabled_symbols`, and `cloud_preflight`
   requires `enabled_symbols == ("XAUUSD", "EURUSD")`. An XAUUSD-only first activation therefore needs a design
   decision: either run with `enabled_symbols=("XAUUSD",)` (a preflight change plus configuration drift) or a
   per-symbol catch-up scope. Not implemented.
2. **Experiment continuity.** With the flag ON the config fingerprint changes, so a running experiment's cloud
   preflight reports drift (`experiment_resumable` False). Activation needs an Owner decision on the experiment
   (new experiment or controlled continuity).
3. **Historic divergence** (DEC-8.7) is reported, never corrected. Positions may stay open although a pre-activation
   bar touched SL/TP.
4. REVISION materiality is evaluated against currently open positions at the time of ingestion only.
5. Unchanged: MEDIUM-8.2 (no TIF), MEDIUM-8.3 (fail-stop run label), MEDIUM-8.4 (old code on stamped rows), LOW-8.5
   (Store not closed on fail-closed startup).
6. The R1–R4 runtime paths run only when the flag is ON, so they are exercised only by isolated tests until activation.

## Procedure — later simulation with a copied operational DB (no activation)

1. Owner provides **copies** of `trading_floor.db` and, if any exists, the Evidence DB. Copy after a WAL checkpoint, or
   use the preview's backup-API copy. Record their SHA-256.
2. Export closed 5m bars (provider) covering every open position from `opened_at` to the chosen `as_of`, as
   `[{symbol, bar_start, open, high, low, close}]`.
3. `python -m replay.activation_preview --trading-db COPY.db [--evidence-db EV_COPY.db] --bars-json bars.json
   --as-of <slot> --first-scope XAUUSD --out preview.json`. The exit code must be 0 (`source_unchanged`).
4. Review `historic_missed` (DEC-8.7, report only), `expected_close`, `MISSING_BARS`, pending gate bars and REVISIONs.
5. On a **second** copy: build a test config with `v2_position_catch_up=True` and temporary evidence/trading paths; run
   one cycle with a bars provider replaying the export. Reconcile closes, prices, net PnL and realized PnL exactly with
   `preview.json`. Rerun the slot (DUPLICATE), run one more slot (no duplicate close), then roll back to flag OFF on
   the copy.
6. `python -m runtime.revision_review gate --trading-db COPY2.db --evidence-db EV2.db` → the result must be CLEAR, or
   every item must be reviewed.
7. Record SHA, input hashes, outputs and PASS/FAIL. Any mismatch is a stop criterion (P8.1B gate).

## Next independent review required (P8.5, e.g. Copilot)

Review the exact final SHA of this batch series:
- R1/R2 route and preflight (no accidental activation, fail-closed matrix, OFF reports unchanged, inventory tests
  intact);
- R3 read-only guarantees and oracle agreement with real cycles;
- R4 classification, dedup, review and the gate (BLOCKED/CLEAR semantics);
- the superseded LOW-8.6 assertion;
- full suite with the environmental skip explained; CI;
- PAPER, REAL OFF, NAS100 OFF, schema 3;
- confirmation that HIGH-8.1 stays OPEN for the operational runtime and Phase 8 stays BLOCKED.

A later, separate review after an Owner-approved controlled activation would be the only path to closing HIGH-8.1
for the runtime.

# P8.4F — Fixes for the P8.5 independent review (Copilot) blockers

Baseline `0f6752782e505ff8b81204976109c7fe3fb1beaf`. Only the three confirmed defects were changed; no strategy, Risk
Engine, AI, schema or flag-default change. PAPER only, REAL OFF, NAS100 OFF, schema 3, flag OFF by default, nothing
activated. The P8.5 report itself was not attached to the P8.4F prompt; the fixes follow the summary of findings in
that prompt.

| Fix | Finding | Commit |
|---|---|---|
| FIX1 | R2 / HIGH: the Evidence Store preflight could approve a read-only store | `70ff008` |
| FIX2 | R3 / MEDIUM: the preview could assign a pending order a bar at or before its as_of | `3d59ecc` |
| FIX3 | R4 / HIGH: the revision gate could say CLEAR without reviewing every anomaly | `e2ab0c5` |

## FIX1 — persistent write probe (`runtime/config.py::catch_up_storage_checks`)

- **Root cause:** `CREATE TEMP TABLE` writes to SQLite's temp database, which works on a read-only main database, so
  `catch_up_evidence_writable_schema` was true for a read-only Evidence Store.
- **Fix:** `BEGIN IMMEDIATE` (takes the write lock), `CREATE TABLE main.…` + `INSERT`, always `ROLLBACK`; then the
  schema must be unchanged (no residue) and the file and its directory must be writable. Any error → false (fail
  closed).
- **Test:** `test_read_only_evidence_store_fails_and_probe_leaves_no_residue`. Before (baseline): `True is not False`.
  After: read-only → false, writable → true, no residue.

## FIX2 — pending gate eligibility (`replay/activation_preview.py`)

- **Root cause:** the preview reported the newest closed bar as the gate bar without the runtime's rule that a bar may
  progress an order only if it starts strictly after the order's as_of.
- **Fix:** the gate bar is reported only when it is eligible; otherwise `gate_bar = null`,
  `expected_status = STAYS_PENDING_NO_ELIGIBLE_BAR`. New field `expected_status` (`EVALUATED_ON_GATE_BAR` or the
  above).
- **Test:** `test_pending_order_never_assigned_a_bar_at_or_before_its_as_of`: an order with as_of 13:30 previewed at the
  13:30 slot; then a real runtime cycle runs on an isolated copy. Before: the preview gave gate 13:25 while the runtime
  left the order PENDING with 0 fills. After: the preview reports no gate and STAYS_PENDING, which agrees with the
  runtime. The existing test (a later slot, where the gate equals the runtime's) is unchanged.

## FIX3 — revision gate coverage by anomaly identity (`runtime/revision_review.py`, `runtime/service.py`)

- **Root causes:**
  - `demo_gate(trading_db, evidence_db=None)` skipped the coverage check without the Evidence DB, so it returned
    CLEAR, and the CLI exited 0.
  - Coverage was matched by (symbol, timeframe, bar_start). A second, different revision of an already-classified
    bar therefore counted as classified.
- **Fix:**
  - **Identity:** each journal record carries the Evidence DB `anomaly_id`. The runtime resolves it from the presented
    bar digest (`presented_digest` of the REVISION anomaly). `review_key` = `anomaly_id`.
  - **Unidentifiable revisions** are never recorded under a guessed identity, so they remain UNCLASSIFIED.
  - **Dedup and reviews** are keyed by `anomaly_id`. Decisions are durable journal rows.
  - **Gate:** the Evidence DB is required (`--evidence-db` is mandatory in the CLI). The gate is BLOCKED on
    `EVIDENCE_DB_MISSING`, `EVIDENCE_DB_UNREADABLE`, `UNCLASSIFIED`, `CLASSIFICATION_WITHOUT_ANOMALY` (a record
    whose anomaly is not in the given Evidence DB) or `MATERIAL_UNREVIEWED`. It is CLEAR only when every REVISION
    anomaly is classified by id and no material one is unreviewed.
  - **Legacy rows** without `anomaly_id` never count as coverage.
  - **Report fields:** the report adds `blockers`, `anomalies`, `classified` and `orphan_classifications`.
- **Superseded (explicit):** the P8.4 assertion `demo_gate(db)["status"] == "CLEAR"` in
  `test_flag_off_never_records_revisions`. It now expects BLOCKED / `EVIDENCE_DB_MISSING`.
- **Tests (`RevisionGateCoverageTests`, 5 new):**
  - false CLEAR #1: missing Evidence DB → BLOCKED; the gate never creates the file; CLI exit 3; CLI without
    `--evidence-db` is rejected;
  - false CLEAR #2: second revision of a classified bar → BLOCKED, with the exact unclassified anomaly_id;
  - two material revisions of one bar: separate records; re-presentation is deduplicated; a partial review still
    blocks; full review → CLEAR, durable across a later cycle;
  - a classification without a matching anomaly → BLOCKED;
  - unidentifiable revision → no record.
- **Before/after:**
  - Before (baseline, direct reproduction): gate without the Evidence DB = CLEAR (CLI exit 0) while one anomaly was
    unclassified; 2 anomalies with 1 classified = CLEAR.
  - After: both BLOCKED.

## Suite

- After FIX1: 1063 OK; after FIX2: 1064 OK; after FIX3: 1069 OK (local).
- CI reports the known environmental skip (`test_system_health` needs git on PATH).
- `git diff --check` clean.

## Remaining risks

1. **Same class as FIX1, not changed (out of scope):** the trading DB `db_writable_schema` checks in `cloud_preflight`
   and `demo_runner.preflight` still use a TEMP-table probe. They can report a read-only trading DB as writable.
   Recommended as a separate, authorized fix.
2. FIX3 identity depends on the evidence engine's `presented_digest` payload field and `bars_from_frame` producing the
   same digest as ingestion (the same function is used for both). If the lookup fails, the anomaly stays UNCLASSIFIED
   and the gate stays BLOCKED (fail closed, never CLEAR).
3. `ESCALATED` still counts as reviewed for the gate (unchanged P8.4 semantics; an Owner policy question).
4. Every P8.4 open risk is unchanged:
   - DEC-8.10 vs the global flag;
   - experiment continuity;
   - historic divergence is report-only;
   - materiality is evaluated against the positions open at ingestion;
   - MEDIUM-8.2/8.3/8.4 and LOW-8.5.
5. HIGH-8.1 stays **OPEN** for the operational runtime; Phase 8 stays **BLOCKED**; nothing is certified.

## Proposal (NOT implemented) — per-symbol catch-up configuration

- **Problem:**
  - DEC-8.10 wants XAUUSD first, but `AI_FLOOR_V2_POSITION_CATCH_UP` is global over `enabled_symbols`.
  - `cloud_preflight` requires `enabled_symbols == ("XAUUSD", "EURUSD")`.
- **Proposal:**
  - **Variable:** `AI_FLOOR_V2_POSITION_CATCH_UP_SYMBOLS`, an explicit comma list such as `XAUUSD`.
    - It is valid only with the flag at `"1"`.
    - Every entry must be in `enabled_symbols`.
    - NAS100 is rejected.
    - Empty, duplicate, unknown or whitespace-ambiguous values fail closed.
    - The flag ON without the list is either an error or means all enabled symbols; this is an Owner decision.
  - **Runtime:** the catch-up and pending gate apply only to listed symbols. Other symbols keep the newest-bar path,
    unchanged.
  - **Evidence:** evidence ingestion stays for all enabled symbols, so watermarks exist when a symbol is added later.
  - **Preflight:** the configured scope is reported. `enabled_symbols` stays `("XAUUSD", "EURUSD")`, so no preflight
    weakening is needed.
  - **Fingerprint:** the scope is part of the config fingerprint (experiment drift is explicit).
  - **Preview:** `--first-scope` is checked against the configured scope.
  - **Tests:**
    - the scope matrix;
    - EURUSD is unchanged while XAUUSD catches up;
    - adding a symbol later starts from its own durable watermark (DEC-8.7, no retroactive correction);
    - rollback, by removing a symbol or the flag.
- **Requires Owner authorization:** a decision on the default meaning of flag ON without a list, and a new independent
  review.

## Next independent review required (P8.5 re-review)

Review final SHA of P8.4F:
- the three fixes and their adversarial tests;
- the explicit supersession;
- that no other behaviour changed (flag OFF path untouched, inventory tests intact);
- full suite and CI.

Then decide whether P8.4 R1–R4 can be accepted. HIGH-8.1 for the runtime still needs a later Owner-approved controlled
activation and its own review.

# P8.4G — Fixes for the two HIGH findings of the Copilot P8.5R re-review

Baseline `b5a8c3ca86f2788cc0f17e0f4dce2c8fdceed3b7`. Owner decisions DEC-8.11 (trading DB persistent-write check) and
DEC-8.12 (ESCALATED is not final) approved. The P8.5R report was referenced as attached but was not present in the
prompt; the fixes follow the findings as stated in the P8.4G prompt.

| Fix | Finding | Commit |
|---|---|---|
| FIX A | P8.5R HIGH: the trading DB preflights accepted a read-only DB (TEMP probe) | `1dc82c8` |
| FIX B | P8.5R HIGH: a material ESCALATED anomaly cleared the revision gate | `87dfd62` |

Files: `runtime/config.py`, `runtime/cloud.py`, `runtime/demo_runner.py`, `runtime/revision_review.py` and tests only.
No strategy, Risk Engine, AI, storage, broker or schema change. PAPER only, REAL OFF, NAS100 OFF, flags OFF by
default.

## FIX A — trading DB persistent write (DEC-8.11)

- **Root cause:** `cloud_preflight` and `demo_runner.preflight` probed with `CREATE TEMP TABLE`, which SQLite keeps
  in its temp database. A read-only trading DB still opens (SQLite falls back to read-only) and passes
  `quick_check`, so it was reported writable.
- **Fix:** `runtime/config.py::persistent_write_probe(db, path)`, shared by the three preflights (trading DB, cloud
  and local, plus the Evidence Store check from P8.4F FIX1). It works as follows:
  1. It requires OS write access to the file and its directory.
  2. It takes the write lock (`BEGIN IMMEDIATE`), creates a table on `main` and inserts a row.
  3. It always rolls back, including after an error.
  4. It requires `sqlite_master`, `PRAGMA schema_version` and `PRAGMA user_version` to be unchanged.
  - SQLite errors propagate to the callers, which already map them to `db_writable_schema = False`.
- **Results:**
  - read-only DB, insufficient permissions, a SQLite error during the probe, or a corrupt file → NOT_READY;
  - a valid DB → INFRA_READY / READY as before, with the file byte-identical (no economic, schema or version change,
    no residue).
- **Tests:** `test_phase8_trading_db_preflight.py` (5).
- **Before/after:** before (baseline reproduction), a read-only trading DB gave `db_writable_schema: True`, cloud
  `INFRA_READY` and local `READY`. After: `False` / `NOT_READY` for both.

## FIX B — ESCALATED is not final (DEC-8.12)

- **Root cause:** `demo_gate` treated any `EVIDENCE_REVISION_REVIEWED` row as resolution, whatever its decision.
- **Fix:**
  - Decisions are append-only journal rows: history is never deleted or rewritten. The effective decision of an
    anomaly is its latest row (journal order), keyed by `anomaly_id`.
  - Each review row records `decision`, `final`, `previous_decision`, `reviewer` (required, non-blank) and `note`.
  - The gate is BLOCKED by `MATERIAL_UNREVIEWED` (material, no decision) and by `ESCALATED_UNRESOLVED`, in addition to
    the P8.4F blockers (`EVIDENCE_DB_MISSING`, `EVIDENCE_DB_UNREADABLE`, `UNCLASSIFIED`,
    `CLASSIFICATION_WITHOUT_ANOMALY`).
  - The health summary adds `escalated_unresolved`. `unreviewed` and `material_unreviewed` now count anomalies without
    a final decision.

### Permitted decisions and their consequences

| Decision | Final | Consequence |
|---|---|---|
| `ACCEPTED_FIRST_COMMITTED` | yes | The Owner accepts the certified rule for this anomaly: the first committed bar stays authoritative. The anomaly is resolved for the gate. Evidence and PAPER state are unchanged. |
| `ESCALATED` | no | Needs investigation. The gate is BLOCKED (material or not) until a later final decision. |
| no decision | — | Material: BLOCKED. Non-material: warning only (unchanged). |

- **Re-escalation:** ESCALATED after a final decision re-opens the anomaly; both rows remain.
- **No path to change a committed bar:** no decision changes a committed bar, and none exists to accept the presented
  bar. If an escalation concludes the committed bar was wrong, the gate stays BLOCKED until the Owner authorizes a
  separate procedure.
- **Later anomalies:** a later anomaly of the same bar (partial revision) has its own `anomaly_id`. It never alters
  earlier decisions and blocks on its own until resolved.

### Superseded expectations (explicit)

- `test_gate_is_read_only_and_cli_exit_codes`: ESCALATED → gate exit 0 (P8.4) is now exit 3; a final decision → 0.
- `test_multiple_revisions_of_one_bar_are_reviewed_individually_and_persist`: ESCALATED → CLEAR (P8.4F) is now
  BLOCKED `ESCALATED_UNRESOLVED`; a final decision → CLEAR.
- `test_non_material_revision_is_visible_and_only_a_warning`: the summary adds `escalated_unresolved`.

### Tests and before/after

- **Tests:** `EscalationTests` (6):
  - material ESCALATED → BLOCKED, then final → CLEAR, with an auditable history; the committed bar digest and the
    PAPER account are unchanged;
  - non-material ESCALATED → BLOCKED;
  - re-escalation re-opens and keeps history;
  - a new partial revision does not erase an earlier decision;
  - unclassified still blocks;
  - review validation.
- **Before/after:** before (baseline reproduction), a material anomaly with only ESCALATED gave CLEAR. After: BLOCKED
  `ESCALATED_UNRESOLVED`.

## Suite and verification

- Full suite: 1074 OK after FIX A, 1080 OK after FIX B (local). `git diff --check` clean.
- Diff `b5a8c3c..HEAD`: the preflights, `revision_review` and tests only.
- `REAL_EXECUTION_ENABLED = False`; default `enabled_symbols = (XAUUSD, EURUSD)`; `v2_position_catch_up` and the
  scheduler are OFF by default.

## Open risks

1. `cloud_preflight` still creates a missing trading DB (unchanged pre-existing behaviour; the probe then runs on it).
2. On Windows, directory write permission cannot be reproduced with `chmod`; the test simulates it by patching
   `os.access`.
3. The decision vocabulary is minimal (one final decision). Any outcome other than "first committed stays
   authoritative" has no in-system resolution, by design.
4. P8.4F remaining risks and the P8.4 open risks are unchanged. **HIGH-8.1 stays OPEN** for the operational runtime;
   **Phase 8 stays BLOCKED**; nothing is certified or activated.

## Next independent review required (P8.5R2)

Review the final SHA of P8.4G:
- FIX A on both preflights and the shared probe (including the Evidence Store reuse);
- FIX B semantics and the decision table;
- the three superseded expectations;
- that no economic, strategy or Risk Engine behaviour changed;
- full suite and CI.

# P8.6 — Design phase result, Owner decisions and implementation handoff (2026-10-08; documentation only)

- **Design:** `V2_PHASE8_P86_DESIGN.md` (revision P8.6F10). Copilot P8.6R11 (as relayed by the Owner): **P8.6 DESIGN
  COMPLETE — CONDITIONAL**. This is not implementation or operational certification.
- **Owner decisions:**
  - DEC-8.17 **APPROVED POLICY** (the G-8.INT measurement policy, original §8.1 definition; an earlier "emergency stop"
    label was corrected the same day);
  - DEC-8.17b **APPROVED POLICY** (halt stop policy: Alternative 1 plus the post-halt persistence policy, carrying
    the emergency-stop principles; the residual of one admitted write after `T_h` is accepted);
  - DEC-8.20 **APPROVED POLICY** (A2 with conditions; no XAUUSD / EURUSD economic isolation);
  - DEC-8.21b **PROVISIONAL** (B-STRICT pending operational validation);
  - the package plan **APPROVED for planning only**;
  - DEC-8.13 / 8.14 / 8.15 / 8.16 / 8.21 **APPROVED POLICY** (2026-10-08; DEC-8.14 busy ≤ 500 ms and K = 4 subject to
    tests);
  - DEC-8.18 / 8.19 PENDING;
  - DEC-8.22-a…j and OP-1…OP-7 NOT AUTHORIZED.
- **Handoff:** `V2_PHASE8_P86_HANDOFF.md`: decision register, packages P1–P4 with gates, recommended sequence
  (P1 → P2a → P3 → P4a → P2b → P4b), the P1 prompt (not executed), blockers B-1…B-6.
- **Status unchanged:** Phase 8 BLOCKED; HIGH-8.1 OPEN; M-5 OPEN; G14 / G15 not certified; PAPER only; REAL OFF;
  NAS100 OFF; no Phase 9.

## P4a R-HALT — component-level closure (2026-10-09)

- Certified SHA `d21ce66796f6827ba5db52d175c80195d0f34630` on `v2/phase8-p4a`; CI run `37998764069` SUCCESS.
- History on the branch: `8c4d249` (G15 malformed-input pre-correction), `3b311e3` (R-HALT; Copilot REQUEST CHANGES,
  MEDIUM-1), `f71318b` / `584bcc3` / `caa2c20` (startup barrier and restricted R; CI failures diagnosed through CI
  annotations: OS-level signal masking did not defer the handler), `d21ce66` (Python-level STARTUP deferral; CI
  success).
- Copilot: CERTIFY P4a, subject to LOW-4 (documentary). Owner: DEC-8.17b amendment APPROVED; LOW-1…LOW-3 accepted.
- Status: **P4a CERTIFIED — COMPONENT LEVEL**. LOW-4 resolved documentarily (handoff 2b, design 3.8).
- Unchanged: M-5 OPEN, HIGH-8.1 OPEN, B-STRICT PROVISIONAL, Phase 8 BLOCKED; flags OFF; PAPER only.

## P2b Sealed Genesis — implementation (2026-10-09, pending independent audit)

- Branch `v2/phase8-p2b` from `ce0ebe1`. Owner D-1 (flag `AI_FLOOR_V2_SEALED_GENESIS`, OFF; only-mode switch at the
  P4b cut), D-2 (`ssh-keygen -Y`, ephemeral test keys), D-3 (non-creating preflights, rolled-back probe kept),
  D-4 (DEC-8.22-i and the preflight part of DEC-8.22-f; DEC-8.18 not required, FS/EX/PD/RS not classified).
- X_P2 is not pinned (`runtime.genesis.SEALED_BASELINE_SHA = None`): no sealed start can pass step 1 outside tests
  until the authorized P4b baseline cut.
- Status: IMPLEMENTED — NOT CERTIFIED (Copilot audit pending).

## P4b preparation (2026-10-09, pending independent audit)

- Branch `v2/phase8-p4b` from the certified P2b `fd29dc7` (Copilot CERTIFY P2b; CI `38010674011`).
- LOW-1 fixed (non-creating sealed open). LOW-2 resolved by the contract: I-G18 missing check = NOT VERIFIED, FAIL =
  INVALID (design 5.1.3.2), regression-tested; severities unchanged.
- X_P2 single pin (`cloud_runner.EXPERIMENT_BASELINE_SHA`) so that `Y_P2` can satisfy I-C6.
- A2 tools and the G-8.INT matrix implemented and tested on synthetic periods only; checklist in
  `V2_PHASE8_CERTIFICATION_CHECKLIST.md`. No real A2 / E0. Status: PREPARED — NOT CERTIFIED.
