# V2 Phase 2 — Market Evidence Engine V2

Status: **PHASE 2 — IN PROGRESS / NOT CERTIFIED** (Batch 1 accepted; B2.1 accepted; B2.2
READY FOR INDEPENDENT REVIEW, not accepted; B2.3 not started). H02: **PARTIAL**.
Branch: `v2/phase2-market-evidence` · Base: `main` @ `1f3362f47cf2439f7d1c6df844a2a9f7656dee1b`

Mode: PAPER / DEMO only. REAL EXECUTION: DISABLED. NAS100: OFF. Not wired into the runtime.

This file records Phase 2 scope, design, evidence and open decisions so they can be transferred to
the Master Roadmap & Checklist (Word). It does not replace that document and does not certify
Phase 2.

## Batch 1 — Evidence foundation + H02 evidence processing

Scope: an isolated market-evidence layer that commits every newly available closed bar of each
(symbol, timeframe) stream in chronological order, exactly once, with correct catch-up and
restart/recovery. No trading behavior changes; no runtime wiring.

Files:

| File | Role |
| --- | --- |
| `data/market_evidence.py` | `MarketBar` contract, `bars_from_frame` adapter for the V1 provider frame, `MarketEvidenceEngine` (ingest, catch-up, anomalies, reads) |
| `storage/evidence_store.py` | Isolated MARKET EVIDENCE SQLite sidecar (`EvidenceStore`) |
| `test_market_evidence.py` | 47 focused tests (identity, order, idempotency, catch-up, closed bars, real-process crash/restart, gaps, multi-stream, NAS100, persistence and schema contract, safety) |

## V1 market path audit (frozen behavior, unchanged by Batch 1)

Path: `runtime.scheduler.Scheduler.tick` (15-minute slot, `SLOTS_MISSED` journal event only) →
`runtime.service.OperationalRuntime.run_cycle` → `market_provider.load_snapshot(symbol, slot)`
(`data/twelve_data_provider.py` default, `data/massive_provider.py`) → `runtime.gates.fresh_snapshot`
(at the slot and again at wall time) → `TradeManager.process_bar(latest 5m bar)` → `run_floor(snapshot)`
→ AI → execution-time freshness recheck → `PaperBroker.process_next_bar(order, latest 5m bar)` /
`submit_plan`.

1. **Requests.** Every cycle refetches a full rolling window per timeframe (Twelve Data
   `outputsize=500`, cached per expected bar; Massive 14 days ending at `as_of`).
2. **Symbols.** `MARKETS = (XAUUSD, NAS100, EURUSD)`; enabled by default: XAUUSD, EURUSD.
3. **Timeframes.** `{"1h","15m","5m"}`; each a pandas DataFrame indexed by a tz-aware UTC
   `DatetimeIndex` of bar **start** times; columns `Open/High/Low/Close/volume/symbol/timeframe/
   provider/provider_symbol/is_closed`.
4. **Latest bar.** `frame.index.max()`.
5. **Only latest consumed?** For trading effects, yes: position management and pending fills see
   only the latest 5m bar (`runtime/service.py`, `bar = ... snapshot["5m"].index.max()`). The
   deterministic analysis (`run_floor`) receives the whole window.
6. **Skipped bars.** Yes. Under the normal continuous 15-minute cadence, each cycle passes only
   the latest closed 5m bar to `TradeManager.process_bar` / `process_next_bar`, so the two
   intermediate 5m bars of each 15-minute interval can be omitted from position/order processing.
   Freshness gates, failed runs and missed slots (`SLOTS_MISSED`) can cause additional omissions
   (the H02 V1 limitation, `V1_POSTMORTEM.md`).
7. **Duplicates.** Twelve Data: a duplicate timestamp in one response raises `INVALID_BAR`;
   Massive: `DUPLICATE_OR_UNSORTED`; `fresh_snapshot` rejects `has_duplicates` as `NO_DATA`.
8. **Timestamps.** Normalized to aware UTC; naive provider stamps are treated as UTC.
9. **Ordering.** Twelve Data `sort_index()`; Massive requires strictly ascending results.
10. **Downtime/restart.** No catch-up: the next tick runs the current slot only and logs
    `SLOTS_MISSED`.
11. **Accumulated bars.** Present in the window for analysis, but only the latest affects
    positions/pending orders.
12. **Overlapping windows.** By design every cycle; no cross-cycle per-bar state.
13. **Repeated bar.** Position effects are guarded by `PaperPosition.last_processed_at`
    (bars `<=` it are ignored); pending fills need `bar.timestamp > order.as_of`.
14. **Out of order.** Twelve Data sorts; Massive rejects.
15. **Persistence.** No per-bar market evidence. Only `DATA_CHECK` journal state, UI snapshots and
    run records.
16. **Watermarks.** Only `system_state.scheduler_slot` and per-position `last_processed_at`.
17. **Partial processing.** A run claims its slot; a crash marks it FAILED; no bar-level progress.
18. **Freshness.** `fresh_snapshot` with `max_age_seconds = 1h:7200, 15m:1800, 5m:600`.
19. **Closed vs forming.** Explicit: both providers drop a bar when `start + duration > as_of` and
    mark every emitted bar `is_closed=True`; `fresh_snapshot` requires all `is_closed`.
20. **Trading-relevant.** The latest 5m bar (SL/TP, fills) and the snapshot passed to `run_floor`.

## Classification

- **A. Frozen (unchanged):** providers, request cadence, symbols, sessions, timeframes, freshness
  thresholds, closed-bar rule, `fresh_snapshot`, setup/AI/risk/paper/execution, trading DB schema 3.
- **B. Approved V2 requirement:** H02 (`V2_HANDOFF_REQUIREMENTS.md`): process all new bars
  chronologically and idempotently, with catch-up and retry/restart without omitted or duplicate
  effects, **including 5m position management, SL/TP, pending orders and fills**.
- **C. Implementation choices (Batch 1, reversible, no trading effect):** semantic identity; one
  bar per transaction; sidecar store; anomaly recording (GAP/LATE/REVISION).
- **D. Owner decisions:** Decisions A–E approved (below); the Batch 2 trading-side idempotency gate
  remains open.

## Architecture and models

- **Contract.** `MarketBar(symbol, timeframe, start, open, high, low, close, volume, provider,
  is_closed)`; `start` is the V1 bar-start timestamp normalized to UTC and serialized with
  `storage.codec.utc` (the existing convention). Validation at the evidence boundary only:
  aware timestamp, supported timeframe, finite positive consistent OHLC, non-negative volume,
  strict bool `is_closed`, stream membership. No strategy concepts.
- **Closed-bar rule (inherited, not new).** A bar is evidence only if `is_closed` is True and
  `start + duration <= as_of`, the exact V1 provider rule. Only the closed chronological
  prefix is committed; a forming bar and every later bar are `held` and remain eligible.
- **Identity / idempotency.** Primary key `(symbol, timeframe, bar_start)`. Provider is evidence,
  not identity. An identical re-presentation is a `DUPLICATE` (by market-content digest of OHLCV);
  conflicting duplicates within one presentation reject the whole presentation.
- **Ordering.** Per stream by `bar_start`; input order (sequential, reversed, shuffled) is
  irrelevant. No cross-symbol/cross-timeframe priority; snapshot timeframes are iterated in the
  fixed order 1h, 15m, 5m purely for determinism. Reads order by the persisted `stream_seq`, which
  equals chronological order because commits are strictly chronological.
- **Watermark.** The committed bar with the highest `stream_seq`. Derived from durable data, never
  from process memory.
- **Recovery model.** One bar per SQLite transaction (identity check, watermark check, gap record,
  insert). A crash keeps every bar committed before it and loses none after it; the next
  presentation of the (overlapping) provider window commits the remainder. Proven with real child
  processes killed by `os._exit`: inside the transaction that has just INSERTed a genuinely new bar
  but before COMMIT (first new bar and a middle bar; the uncommitted bar stays eligible and is
  committed exactly once on restart), in a duplicate-check transaction before any new insert,
  after a partial catch-up, and repeatedly.
- **Gaps.** A bar later than `watermark + duration` is committed and a `GAP` anomaly is recorded
  (`after`, `before`, `missing_intervals`). No candle is fabricated.
- **Ingestion continuity is not market-history completeness.** `IngestResult.contiguous` is True
  only when that ingestion recorded no `GAP` relative to the committed watermark. It says nothing
  about market gaps before the first committed bar of a stream, inside a provider window that was
  never presented, or in the provider's own history; it does not prove that no market bars are
  missing.
- **Late / revised bars.** An uncommitted bar older than the watermark is recorded as `LATE`
  (not committed). A committed bar re-presented with different content is recorded as `REVISION`;
  committed evidence is never overwritten.

## Persistence decision

Durable evidence is required (restart correctness cannot rely on memory). Options considered:

1. Migrate `trading_floor.db` — rejected: schema 3 is frozen for V1 rollback and the experiment.
2. **Isolated sidecar** — chosen: `storage/evidence_store.py`, own `application_id` 0x56324D45
   ("V2ME"), schema version 1, read-only mode. Protections, all checked before anything is written:
   - **Trading DB by name/identity.** Any path whose name is `trading_floor.db` — case-insensitively,
     after resolving links and `..`, ignoring Windows trailing dots/spaces and an alternate-data-stream
     suffix — is refused before a connection is opened, whether the file is missing, empty or
     initialized (an empty trading DB has no `application_id` yet). An existing file that is the same
     file (e.g. a hard link) as a sibling trading DB is refused too; an initialized trading DB under
     any other name is refused by `application_id`/table set. Refused files stay byte-for-byte
     unchanged.
   - **Validated target == SQLite target.** The path is resolved once; that absolute target is what
     is validated and exactly what SQLite opens. Read-write opens it as a plain filename (no URI
     parsing); read-only uses a percent-encoded file URI (`Path.as_uri`), so `#`, `?`, `%`, spaces
     and non-ASCII stay part of the filename (e.g. `trading_floor.db#evidence` can never reach
     `trading_floor.db` as a URI fragment). Before anything is written, SQLite's own
     `PRAGMA database_list` must report the same target, otherwise the store is refused. A filename
     the platform cannot represent (e.g. `?` on Windows) fails closed.
   - **Foreign databases.** The Phase 1 health sidecar and any other SQLite file are refused.
   - **Schema contract (effective behavior).** A database that identifies itself as a sidecar
     (`application_id`, version, table names) is accepted only when no schema object can make a
     canonical write behave differently. Strategy: SQLite metadata wherever it exists, plus the
     smallest extra inspection for what metadata does not expose.
     - Metadata (`PRAGMA table_xinfo`, `index_list`, `index_xinfo`, `foreign_key_list`,
       `sqlite_master`): every required column with its type affinity, NOT NULL, primary-key
       membership and no hidden/generated column; exactly the required UNIQUE/PRIMARY KEY column
       sets (`(symbol,timeframe,bar_start)`, `(symbol,timeframe,stream_seq)`, `anomaly_id`), each
       non-partial with BINARY collation, and no other UNIQUE index (partial or not); no partial or
       expression non-unique index (their predicates/expressions are evaluated, and can fail, on
       write); no foreign key; no trigger.
     - Keyword scan of each table's CREATE statement after removing string literals, quoted
       identifiers and comments (never whole-string equality): `CHECK`, `ON CONFLICT`,
       `REFERENCES`, generated/`VIRTUAL`/`STORED` columns, non-BINARY `COLLATE`, `STRICT`,
       `WITHOUT ROWID` and `AUTOINCREMENT` refuse the store (fail closed: the last three are not
       used by the canonical schema).
     - Accepted harmless differences: SQL formatting/case, column and key order, same-affinity type
       spellings, explicit `COLLATE BINARY`, `DEFAULT` values, comments, and plain non-unique
       column indexes.
     A mismatch is refused on open, before any ingestion, and the file is never repaired.
   - `quick_check` integrity validation.

This choice has no trading implication in Batch 1 because nothing consumes the evidence. Authority
between the two databases is settled by Decision B; trading-side idempotency remains a Batch 2 gate.

## Known limitations

- H02 is **PARTIAL**: evidence processing is complete and proven; applying every intermediate bar to
  5m position management, SL/TP, pending orders and fills is not implemented (runtime integration).
- Not wired into the runtime; V1 still consumes only the latest 5m bar.
- Gaps are recorded but not classified (no market calendar, Decision E); weekend/daily-break gaps
  appear as GAP.
- First ingestion of a stream commits the whole closed provider window as evidence. Which of those
  bars drive trading economics is governed by Decision C (not implemented in Batch 1).
- Synchronous SQLite like Phase 1; latency is irrelevant while not wired, but must be evaluated
  before runtime activation.

## Owner-approved decisions A–E

These replace the earlier "owner decisions required" list. None is implemented by Batch 1, which
remains isolated evidence processing.

- **A — Catch-up trading semantics.** Recovered CLOSED 5m bars may later be applied chronologically
  to deterministic PAPER management of ALREADY-EXISTING trading state where H02 requires it:
  open-position management, SL/TP, and existing pending-order/fill mechanics. Catch-up must NOT
  retrospectively rerun setup generation, AI analysis/review, the Risk Engine or Trade Planner for
  new historical trades, and must not create retroactive trades. Catch-up is not a backtest and not
  historical trade generation.
- **B — Authorities.** The Evidence Store is authoritative for WHAT MARKET EVIDENCE WAS INGESTED.
  The trading DB is authoritative for WHAT TRADING EFFECTS WERE APPLIED. There is no cross-SQLite
  transaction and none may be faked. Before Batch 2 wiring, trading-side restart/idempotency must be
  proven for every economic effect; pending-order per-bar recovery is currently NOT PROVEN (gate
  below).
- **C — Bootstrap.** If existing PAPER trading state requires catch-up, the required closed 5m bars
  are determined from persisted trading progress. If no trading state requires historical catch-up,
  start from the latest eligible CLOSED evidence. The full provider lookback window is never replayed
  through trading economics.
- **D — Late / revision.** `LATE` and `REVISION` remain persisted and observational only. They never
  automatically rewrite prior trading effects; any retroactive economic correction requires a future
  OWNER DECISION.
- **E — Market calendar.** No new market calendar is invented now. Weekend/daily-break/holiday
  distinction stays deferred unless an existing authoritative component can provide it without
  changing trading semantics.

## Batch 2 integration gate — trading-side idempotency (HIGH, OPEN)

Status: **PARTIAL — mandatory gate before any Batch 2 runtime wiring.** Not solved in Batch 1, by
design.

- Existing V1 transactions recovered several crash scenarios without duplicate fills, closes or PnL
  (independent review).
- Open positions have durable per-bar progress: `PaperPosition.last_processed_at`
  (`execution/trade_manager.py` ignores bars at or before it).
- Pending orders have no equivalent durable per-bar progress: `PaperBroker.process_next_bar`
  resolves a `PENDING` order against the bar it is given (fill, cancel or reject); nothing records
  which bars an order has already been evaluated against. Pending-order eligibility also depends
  on runtime gates (AI, freshness, session) that are not proven recoverable per historical bar
  (reconfirmed by the re-review of `dd9459f`).
- Runs recover by scheduler slot (`claim_slot`), not by bar.
- Therefore the Evidence Store watermark cannot prove that the economic effects of a bar were applied
  (Decision B). Before wiring: prove restart/idempotency for every economic effect under bar-by-bar
  catch-up, including pending orders.
- Not done in Batch 1 and not to be done casually: no trading DB schema migration, no ad-hoc
  pending-order progress, no change to order eligibility, fill semantics or the Paper Broker, no
  historical `run_cycle`, no cross-SQLite pseudo-atomicity.

## B2.1 — Position catch-up core (isolated; not wired)

Purpose: every committed closed 5m bar after an EXISTING open PAPER position's durable watermark
reaches the unchanged `TradeManager.process_bar` exactly once, oldest first.
`execution/position_catch_up.py` (`catch_up_position`); tests `test_position_catch_up.py`.

- **Watermark.** `PaperPosition.last_processed_at`, else `opened_at` (trading DB, durable).
- **Input.** `MarketEvidenceEngine.committed(symbol, "5m", after=watermark)`: committed, closed,
  strictly chronological (verified; a violation fails closed), and closed by the caller's `as_of`.
  LATE, REVISION, forming and gap bars are never committed, so they never reach economics.
- **Exactly-once invariant (per position, bar).** Each bar is applied to PAPER state freshly loaded
  from the trading DB and persisted with one `Store.save_paper` call, i.e. one trading-DB transaction
  holding the position (including `last_processed_at`), any closed trade and position status, account
  realized/unrealized PnL and equity, and the journal. Either nothing from the bar commits and it
  stays eligible, or everything commits together. The next bar is chosen from durable state only.
  The P1 fix below revalidates the pre-computation state under that transaction's write lock;
  `TradeManager`'s in-memory watermark check alone cannot exclude another writer.
- **Close.** A closing bar ends catch-up; later bars produce no effect (the position is no longer open).
- **Crash/restart (real child processes, `os._exit`).** Crash before T2 → resumes T2; crash inside
  T2's transaction → T2 rolled back, resumes T2; crash after T2 commits → resumes T3; repeated
  crashes converge to the uninterrupted state (identical equity, one close, one PnL delta).
- **Fail closed.** Evidence read failure or contract violation raises `CatchUpEvidenceError` before
  any trading write; no newest-bar fallback; a later healthy call resumes at the right bar. A trading
  save failure leaves the Evidence Store byte-identical (it is never written) and the bar eligible.
- **No decision pipeline.** Never calls Setup, AI, Risk, the Trade Planner, `submit_plan` or
  `process_next_bar`; pending orders are untouched (spies + AST tests). No open position → `NO_OP`
  (bootstrap does not replay the provider lookback).
- **Unchanged.** `execution/trade_manager.py`, `execution/paper_broker.py`,
  runtime, providers, Risk, AI; trading DB schema 3; no cross-database transaction.
  `storage/database.py` has only the opt-in, schema-neutral P1 comparison guard described below.

### B2.1 P1 — concurrency correction (2026-10-05)

- Checkpoint: clean `v2/phase2-market-evidence`, local/fetched remote
  `8c99d7dfd1b726443c85b4ebc5ccb0004dec7579`. Certified main and accepted Batch 1 unchanged.
- Reproduced before production edits with independent SQLite connections: A computes a stop
  close, pauses before save; B computes/commits the same bar; A saves its stale result. Old code
  persists two closed trades, two POSITION_CLOSED events, realized PnL -10 but trade sum -20.
  The preserved normal-progress regression also fails on old code (both report applying the bar).
- Mechanism: `Store.paper_state` captures immutable encoded account/positions/closed trades/orders/
  fills before computation. `save_paper(expected_state=...)` reloads and compares those objects
  **inside its existing BEGIN IMMEDIATE, before any economic or journal write**. SQLite serializes
  competing processes. A winning commit changes the durable position watermark or removes the
  open position; the later comparison fails and writes nothing. The full comparison also prevents
  lost updates to account equity from another symbol. No schema change, Python lock, new database
  authority, or cross-database transaction. Legacy callers omit the optional guard and behave as before.
- Loser: `STALE`, with only previously committed bars (if any) in `applied`; the rejected bar has
  no effects. A new call reloads durable state; no automatic spin/retry under contention.
- Focused: 28/28 pass (19 original B2.1 tests unchanged + 9 new concurrency/isolation tests).
  Real child processes prove A/B/A for progress and close, three fresh-process retries, and a writer
  terminated with uncommitted close effects/write lock while another writer attempts the transaction.
  Exactly one close/event, realized -10, equity 9990; progress watermark once, equity 10006.
  Trading rollback preserves evidence bytes; evidence rollback preserves committed trading bytes.
  Existing single-writer crash, pending-order and forbidden-pipeline tests pass.
- Mutations: exactly 3, scratch copies only, all detected by assertion failures: remove transactional
  revalidation; ignore `last_processed_at` in the comparison (unchanged-price bar); duplicate close journal.
- Full local suite run **once**: 684 tests, 683 pass, 1 obsolete whole-file byte-identity assertion
  fails, 0 skips. The assertion was narrowed solely for the authorized opt-in guard; the original
  SHA-256 still checks every remaining byte. Both compatibility tests then pass (2/2), including
  actual frozen V1 code from Git history. No production changes after the full run; no full rerun.
  Additional frozen V1 read-only/read-write check on a DB containing a guarded close confirms
  schema 3, one trade/event, reconciled PnL and equity. Linux push CI must be checked on the final SHA.
- Windows-only test launcher adjustment outside the repository: temporary directories use inherited
  permissions because this sandbox cannot reopen Python 3.13 mode-0700 directories. No test skipped.
- Scope: two production files, new `test_position_catch_up_concurrency.py`, the narrow compatibility
  assertion in `test_system_health.py`, and these two documentation files. All trading semantics
  outside exclusion are unchanged; single-writer results remain comparable. The erroneous duplicate
  multi-writer history is not a valid baseline and is not repaired/migrated by this fix.
- **READY FOR INDEPENDENT RE-REVIEW only.** No acceptance/certification, PR, merge, deploy, runtime
  wiring or activation. REAL DISABLED; NAS100 OFF; H02 PARTIAL; B2.2/B2.3 NOT STARTED.

### B2.2 — cycle-gated pending orders (isolated, 2026-10-05)

**Owner decision P1: APPROVED.** NO CURRENT CYCLE GATE = NO PENDING-ORDER EVALUATION. B2.1 was
accepted at `8e880bf` before this batch.

`execution/pending_order_gate.py` → `gate_pending_orders(store, evidence, *, account_id, symbol,
as_of, gate=None, instrument=None, owner_key=None)`. Not wired into the runtime.

- `CurrentCycleGate` carries the outcome of the EXISTING V1 gates for one real cycle — paper_enabled,
  execution-time freshness, execution-time session (incl. the V1 diagnostic override), AI health and
  final status not in {AI_CAUTION, ERROR, RISK_REJECTED} — plus that cycle's own closed 5m bar (the
  dict V1 hands to `process_next_bar`). It does not compute or relax any gate; flags must be `True`.
  A failed or crashed cycle supplies `None` or a non-passing gate.
- Current = passed, same symbol, aware bar timestamp, and not older than the newest committed closed
  5m bar. An earlier cycle's approval is therefore never carried forward to bars it did not see.
- Only the gate's own bar is ever handed to the unchanged `PaperBroker.process_next_bar`, and only if
  strictly after `order.as_of` (V1 rule; bars at/before `as_of` are never evaluated, so recovery cannot
  cancel an order with an old bar). Approval cannot travel backward: no T4 fill at T1–T3 prices.
- Every other committed closed 5m bar after `order.as_of` (intermediate bars, or all bars without a
  current gate) is journaled once in the existing `journal` table: `event_type=PENDING_NOT_EVALUATED`,
  `symbol`, `source=order_id`, `timestamp=bar start`, `run_id=order.run_id`, payload
  `{order_id, bar_start, reason: NO_CURRENT_CYCLE_GATE}`. Inserted under one BEGIN IMMEDIATE that
  re-reads which orders are still PENDING and skips keys already present: retries, restarts and
  duplicate evidence add no rows. No economic effect; no schema change.
- Exactly-once evaluation: durable reload, B2.1 `expected_state` captured, one `save_paper` guarded
  under BEGIN IMMEDIATE; a concurrent winner makes the call `STALE` with nothing written.
- Evidence read-only; read failure or contract violation fails closed (`PendingGateEvidenceError`).
- Never runs Setup, AI, setup reviewer, Risk, Trade Planner, `submit_plan` or `TradeManager`
  (spies + AST: the single `process_next_bar` call takes `gate.bar`).
- Tests: `test_pending_order_gate.py` 24/24 (required items 1–20 plus stale-gate, truthy flags,
  other symbol/malformed bar, evidence failure, not-yet-closed bars, two-writer STALE). B2.1 suites
  28/28 unchanged. Mutations 4/4 killed: intermediate bar to `process_next_bar`; previous gate carried
  forward; T4 gate evaluating T1 price; `order.as_of` guard removed. Full suite once: 708 pass /
  0 fail / 0 skipped.
- Scope: new module + new test file + these two docs. Protected modules, runtime, Risk, AI, providers,
  scheduler, freshness, sessions, strategy, Paper Broker economics and schema 3 unchanged.
- **READY FOR INDEPENDENT REVIEW only.** Not accepted. No PR/merge/deploy/runtime wiring.

Remaining gates:
- **B2.3 — runtime activation: NOT STARTED.** Requires explicit owner authorization: per-bar SL/TP
  changes PAPER economics versus V1 and the frozen experiment. Activation gates recorded for B2.3:
  (1) the B2.1 stale-state guard is opt-in — before catch-up and the runtime operate together, every
  relevant PAPER writer (runtime `save_paper` calls included) must participate safely in stale-state
  protection; (2) the runtime must build `CurrentCycleGate` from its real gate values at the existing
  pending-order point and replace its direct `process_next_bar` loop, without relaxing any gate.

## Phase 1 protection

SYSTEM HEALTH remains isolated and inactive; its two activation blockers (synchronous SQLite
latency; AI-provider observation consistency) and the open H04 supervisor/recovery/shutdown work are
untouched and remain open.

## Test evidence

- Baseline before changes: 609/609 pass, 0 skipped (`main` @ `1f3362f`).
- Focused: `test_market_evidence.py` 39/39 pass.
- Mutation testing (scratch only, in-memory source mutation; no file modified), every mutation
  caught: newest-bar-only (24 failing tests), reversed chronology (25), duplicate processing (16),
  in-memory-only recovery (13), skipped middle bar (19), duplicate commit after restart (6),
  forming bar committed (2).
- Full suite after Batch 1: 648/648 pass, 0 failed, 0 errors, 0 skipped.

### Batch 1 review fixes (M1–M3)

- **M1:** trading DB refused by name (case-insensitive, normalized) and identity, including an
  existing empty `trading_floor.db` / `TRADING_FLOOR.DB`, before any write
  (`test_m1_trading_db_refused_by_name_and_identity_before_any_write`).
- **M2:** schema contract validated on open
  (`test_m2_self_identified_store_with_wrong_schema_is_refused_on_open_unchanged`,
  `test_m2_semantically_equivalent_schema_opens_and_ingests_normally`).
- **M3:** the former `test_process_crash_before_first_commit_loses_nothing` crashed in the
  duplicate-T0 transaction before any new INSERT; it is renamed
  `test_process_crash_in_duplicate_check_transaction_loses_nothing`. The real pre-commit window is
  covered by `test_process_crash_after_real_insert_before_commit_then_restart`.
- Focused: `test_market_evidence.py` 43/43 pass; full suite figures in `CHANGELOG_AGENT.md`.

### Batch 1 final store hardening (re-review of `dd9459f`; M3 closed)

- **M1 (URI/path alias):** `test_m1_uri_significant_path_never_reaches_the_protected_trading_db`
  reproduces the independent `trading_floor.db#evidence` bypass (plus `#`, `?`, `%`, space,
  non-ASCII and `nested/../` forms) and proves the empty protected file keeps its size and SHA-256
  with no journal/WAL/SHM or side file; `test_m1_legitimate_uri_significant_filenames_open_their_own_file`
  proves such names are real, distinct files in read-write and read-only mode.
- **M2 (effective schema):** `test_m2_behavior_changing_constraints_are_refused_on_open_unchanged`
  (partial UNIQUE on anomalies — the 2-detected/1-persisted GAP case — partial/expression indexes,
  CHECK, FOREIGN KEY, trigger, collation, extra UNIQUE, ON CONFLICT, generated column, affinity,
  STRICT, WITHOUT ROWID); `test_m2_canonical_schema_opens_and_persists_every_detected_anomaly`;
  `test_m2_semantically_equivalent_schema_opens_and_ingests_normally`.
- Focused: `test_market_evidence.py` 47/47 pass.

## Batch 1 safety evidence (historical; B2.1 changes listed above)

- No changes to `runtime/`, `execution/`, `riesgo`, `floor/`, `agents/`, `ai/`, providers, config,
  `storage/database.py` or any existing test.
- AST test: engine and store import no trading, network, clock or randomness module and call no
  trading function; repository test: no non-test module imports them.
- Trading DB byte-identical across evidence processing; schema 3; no evidence tables.
- REAL EXECUTION: DISABLED. NAS100: OFF (engine refuses NAS100 by default).
