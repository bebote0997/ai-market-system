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
