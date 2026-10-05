# V2 Phase 2 — Market Evidence Engine V2

Status: **PHASE 2 — IN PROGRESS / NOT CERTIFIED** (Batch 1 complete; later batches pending)
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
| `test_market_evidence.py` | 39 focused tests (identity, order, idempotency, catch-up, closed bars, real-process crash/restart, gaps, multi-stream, NAS100, persistence, safety) |

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
6. **Skipped bars.** Yes. With 15m cadence, two of every three 5m bars never reach
   `TradeManager.process_bar` / `process_next_bar`; missed slots skip more (the H02 V1 limitation,
   `V1_POSTMORTEM.md`).
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
- **D. Owner decisions:** listed below.

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
  processes killed by `os._exit` before the first commit, after a partial catch-up, and repeatedly.
- **Gaps.** A bar later than `watermark + duration` is committed and a `GAP` anomaly is recorded
  (`after`, `before`, `missing_intervals`). No candle is fabricated; `IngestResult.contiguous` is
  False whenever a gap was recorded.
- **Late / revised bars.** An uncommitted bar older than the watermark is recorded as `LATE`
  (not committed). A committed bar re-presented with different content is recorded as `REVISION`;
  committed evidence is never overwritten.

## Persistence decision

Durable evidence is required (restart correctness cannot rely on memory). Options considered:

1. Migrate `trading_floor.db` — rejected: schema 3 is frozen for V1 rollback and the experiment.
2. **Isolated sidecar** — chosen: `storage/evidence_store.py`, own `application_id` 0x56324D45
   ("V2ME"), schema version 1, table-set and `quick_check` validation, refuses the trading DB, the
   Phase 1 health sidecar and any foreign SQLite file without writing, read-only mode, and never
   creates a new file under the trading DB name `trading_floor.db`.

This choice has no trading implication in Batch 1 because nothing consumes the evidence. It does not
settle integration-time persistence (owner decision 5).

## Known limitations

- H02 is **PARTIAL**: evidence processing is complete and proven; applying every intermediate bar to
  5m position management, SL/TP, pending orders and fills is not implemented (runtime integration).
- Not wired into the runtime; V1 still consumes only the latest 5m bar.
- Gaps are recorded but not classified (no market calendar); weekend/daily-break gaps appear as GAP.
- First ingestion of a stream commits the whole closed provider window (no bootstrap watermark).
- Synchronous SQLite like Phase 1; latency is irrelevant while not wired, but must be evaluated
  before runtime activation.

## Owner decisions required

1. **Catch-up trading semantics (H02 integration).** Which downstream effects apply to recovered
   intermediate bars: 5m position management/SL/TP and pending fills (in scope per H02), versus
   setup/AI/planning, which this batch does not run per historical bar. Includes PnL comparability
   with the frozen V1 experiment.
2. **Bootstrap.** Which bars count as "new" for downstream processing at the first start of a stream
   (evidence commits the whole closed window).
3. **Late and revised bars.** Whether `LATE`/`REVISION` evidence may ever affect downstream
   processing; currently recorded only.
4. **Expected-gap calendar.** Classification of market closures (weekends, daily breaks, holidays)
   versus provider gaps.
5. **Integration persistence/atomicity.** Bar-level effects on PAPER state (trading DB) and the
   evidence watermark (sidecar) live in different databases; whether the existing per-position
   `last_processed_at` remains the authority for effects, or another design is approved, must be
   decided before integration.

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

## Safety evidence

- No changes to `runtime/`, `execution/`, `riesgo`, `floor/`, `agents/`, `ai/`, providers, config,
  `storage/database.py` or any existing test.
- AST test: engine and store import no trading, network, clock or randomness module and call no
  trading function; repository test: no non-test module imports them.
- Trading DB byte-identical across evidence processing; schema 3; no evidence tables.
- REAL EXECUTION: DISABLED. NAS100: OFF (engine refuses NAS100 by default).
