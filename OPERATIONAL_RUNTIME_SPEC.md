# Operational Runtime — Fase 6C

## Lifecycle and launch

From the repository root, run `python -m runtime --recover-only` to initialize and inspect durable state, `python -m runtime --once XAUUSD` for one PAPER cycle, or `AI_FLOOR_SCHEDULER=1 python -m runtime` for the continuous scheduler. By default the scheduler is disabled. On Windows PowerShell, set `$env:AI_FLOOR_SCHEDULER='1'` before launching. Stop with Ctrl+C. Streamlit is a separate read-only process (`streamlit run ui/app.py`). No market provider is bundled; without an injected `MarketDataProvider`, cycles end `NO_DATA`. No demo fixture is used by the runtime.

Configuration is `RuntimeConfig` (`runtime/config.py`). `AI_FLOOR_DB_PATH` overrides the default relative `data/runtime/trading_floor.db`; `AI_FLOOR_GIT_COMMIT` can record a reproducibility identifier. Symbols are limited to XAUUSD, NAS100, EURUSD. Cadence defaults to 15 minutes, risk remains capped by the existing deterministic Risk Engine, and execution is PAPER only. Instrument mechanics must be injected as real `InstrumentSpec` values; unknown multipliers are never filled in.

## Storage and schema

`storage/database.py` uses Python `sqlite3` with a single schema version (1). New databases are created transactionally. Existing version-1 databases are validated and preserved; unknown, unversioned, or incomplete schemas fail explicitly. No destructive migration exists. Tables: `runs`, `agent_decisions`, `setups`, `risk_decisions`, `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `closed_trades`, append-only `journal`, `system_state`, `ui_snapshots`, `run_metadata`, and `symbol_locks`. Paper account and broker events commit together. Timestamps are timezone-aware UTC ISO strings. Explicit typed fields and bounded JSON are used; pickle is absent. Provider metadata keys resembling secrets are filtered. Operational DB files are ignored by Git. Tests create unique disposable SQLite files.

`run_metadata` holds config fingerprint, optional git commit, nullable experiment ID, starting equity, and schema version. Prompt versions and provider/model metadata are stored with run/agent records. This prepares reproducibility without starting the official 14-day experiment.

## Schedule, sessions, and idempotency

`Scheduler.tick()` uses an injected clock and floors UTC to a configured 15-minute slot. Analysis windows are weekdays 08:00–17:00 local time in `Europe/London` and `America/New_York`. Either enabled window permits a cycle; both active means overlap. These are analysis windows, not exchange trading hours. `zoneinfo` handles DST. Disabled scheduling records heartbeat but starts no cycle. On downtime, the scheduler records the count of missed slots and only considers the current slot; it never replays a backlog as a backtest.

Each `symbol + UTC slot + cadence` has a unique durable key. SQLite `BEGIN IMMEDIATE` plus `runs.slot_key` and `symbol_locks.symbol` prevent duplicate and overlapping cycles. Paper persistence rechecks slot ownership inside its transaction. Startup recovery marks only aged unfinished runs failed and releases their locks; a recent running slot remains owned and blocks overlap. Completed or failed keys cannot be replayed.

## Freshness and final paper policy

`MarketDataProvider.load_snapshot(symbol, as_of)` supplies 1h/15m/5m pandas frames. Every frame needs timezone-aware timestamps, exact symbol tagging, explicit `is_closed=True`, no future bars, and a recent last bar (default limits: 2h/30m/10m). Missing, forming, future, wrong-symbol, stale, or invalid OHLC data fail closed before analysis or paper progression. A stale snapshot produces `STALE_DATA` and journal events. The existing deterministic scouts still enforce no-lookahead.

`paper_policy` allows a new PAPER order only when `AIFloorReport.final_status == PLAN_READY`, `RiskDecision.status == APPROVED`, a TradePlan exists, all five AI reviews have a valid `OK`/`PARTIAL` status, and data is CURRENT. `AI_CAUTION` blocks even when the underlying RiskDecision is APPROVED. The Paper Broker performs its existing independent risk, contract, closed-bar, fill, and state checks. Scheduler, persistence, and UI have no authorization authority. Pending orders may fill on a later closed bar only after a healthy current AI review; existing open positions continue deterministic risk management when AI is unavailable.

## Recovery, transactions, and observability

Startup logs `RECOVERY_STARTED`/`RECOVERY_COMPLETED`, marks stale unfinished runs `FAILED`, and checks durable account/order/position consistency. Orphan paper state fails startup with `STATE_INCONSISTENCY`; it is never reset to starting equity. PaperAccount, orders, fills, open positions, closed trades, and their timestamps survive restart. A crash before committed paper state leaves no durable new order; the slot remains non-replayable and is later marked failed. Broker state and journal events commit together. A process failure produces a durable `RUN_FAILED` event and no new paper order.

The journal is append-oriented with run, data, analysis, setup, plan, risk, paper, recovery, and failure events. Structured Python logs include component, symbol, slot, event, and status without prompt bodies or secrets. `runtime/health.py` distinguishes heartbeat from data freshness and exposes database/schema, scheduler, provider mode, last run/success, and PAPER broker state. A heartbeat does not imply fresh market data.

## UI boundary

With SAMPLE / DEMO off, Streamlit opens the configured DB in SQLite read-only mode, reads persisted `ui_snapshots`, durable journal, and health. It never calls orchestrators, providers, scheduler, or broker. Missing DB returns `NO_DATA`/`NOT CONFIGURED`. Persisted CURRENT snapshots become visibly stale on later reads. SAMPLE / DEMO remains isolated from operational storage. The EXPERIMENT page remains `EXPERIMENT NOT STARTED`.
