# V2 Phase 1 — Observability & Health Foundation

Status: **IMPLEMENTATION COMPLETE — PENDING FINAL CERTIFICATION**
Branch: `v2/phase1-system-health` · Base: `main` @ `2075e7fcc84ad6a22936ad5dfb7188d14776fbca`

Mode: PAPER / DEMO only. REAL EXECUTION: DISABLED. NAS100: OFF. Phase 2 not started.

This file records owner-approved requirement placement and the final hardening batch so the
decisions can be transferred to the Master Roadmap & Checklist (Word) after certification. It does
not replace or reproduce that document, and it does not certify Phase 1.

## Owner-approved requirement placement

No requirement is deleted or weakened; only responsibility is assigned.

| Requirement | Placement | Status in Phase 1 |
| --- | --- | --- |
| H04 — liveness independent of progress | Phase 1 | Implemented with code/test evidence (below) |
| H02 — process all new bars chronologically and idempotently, with catch-up/restart correctness | **Mandatory Phase 2** (Market Evidence Engine V2) | Not implemented; not complete |
| H15 — deterministic builds | **Mandatory transversal V2** requirement, before the first applicable V2 production/deployment gate | Not implemented by Phase 1 |
| Deploy / build / config / run traceability | **Mandatory transversal V2** requirement, before the first applicable V2 deploy and E2E certification | Not implemented by Phase 1 |
| F01-T13 — Email | Phase 1: health **representation only**. Full Email Notification Engine: **mandatory Phase 9** | Represented truthfully (`UNKNOWN` / `NOT_IMPLEMENTED` / `NOT_CONFIGURED` when supported by evidence); Phase 1 sends no email |

### H04 evidence

- Code: `runtime/system_health.py` keeps heartbeat (`heartbeat_at`) separate from pipeline progress
  (`progress_at`, `progress_stage`, `progress_ref`), and `SystemHealth.project` reports separate
  `liveness` and `progress` views. Alive but not advancing projects `STALE`; an explicitly
  `NOT_ALIVE` heartbeat projects `STALE` even when recent progress exists. Neither is ever `HEALTHY`.
- Tests: `test_system_health.py` (`test_t17_liveness_is_separate_from_progress`,
  `test_t17_dead_heartbeat_and_unknown`), `test_health_probes.py`
  (`test_t01_alive_and_progressing_vs_alive_but_stalled`) and `test_phase1_hardening.py`
  (`LivenessInvariantTests`, cases A–E).

## Blocker before SYSTEM HEALTH production/runtime activation

SYSTEM HEALTH persistence uses synchronous SQLite (the isolated sidecar, `storage/health_store.py`).
The health sink is **not** installed in the production trading hot path; with no sink installed
every hook is a no-op.

**BLOCKER BEFORE SYSTEM HEALTH PRODUCTION/RUNTIME ACTIVATION:** before the health sink is installed
into the production/runtime trading hot path, the latency and blocking behavior of synchronous
SQLite writes must be evaluated and either mitigated or explicitly accepted through the appropriate
gate. This does not block certification of the isolated Phase 1 implementation.

## Final hardening batch

Starting HEAD: `0a9687b9a78b8466903f29a667dcefc130d3116b`. Observability-only fixes:

1. **Heartbeat `NOT_ALIVE` never `HEALTHY`.** `SystemHealth.project` downgrades `HEALTHY` to
   `STALE` when liveness is `NOT_ALIVE`. Unknown liveness (no heartbeat recorded) is reported as
   `UNKNOWN` and changes nothing; `DEGRADED`/`FAILED` are never upgraded.
2. **Real OpenAI timeout → `TIMEOUT`.** `OpenAIProvider` already mapped transport timeouts to the
   generic `CONNECTION_ERROR`. The raised `OpenAIProviderError` now also carries `timed_out=True`
   when the transport failure was a `TimeoutError` (read) or `URLError(reason=TimeoutError)`
   (connect). Its type, `kind`, message, retries, timeout and `last_failure` are unchanged. The
   health classifier maps `CONNECTION_ERROR` with that evidence to `TIMEOUT` and preserves
   `CONNECTION_ERROR` as the legacy name; genuine connection failures stay `CONNECTION_ERROR`.
3. **`NOT_CONFIGURED` is not `UNKNOWN_PROVIDER_FAILURE`.** `SystemHealth.record_error` records a
   `NOT_CONFIGURED` kind as an `UNKNOWN` state with reason `NOT_CONFIGURED` (never an error, never
   `HEALTHY`, not counted in `consecutive_errors`). `observe_ai_provider` no longer projects an
   unconfigured provider as `HEALTHY`. Real unclassified provider failures remain
   `UNKNOWN_PROVIDER_FAILURE`.

Known limitation: `observe_ai_provider` reads only the provider's `last_failure` name, so a timeout
observed through that pattern-B observer remains `CONNECTION_ERROR`. The live hook path
(`SystemHealthSink.provider_call_observed`) receives the exception and classifies `TIMEOUT`.

Unchanged: `storage/database.py`, trading DB schema 3, strategy, setup validation, planner, Risk
Engine, sizing, SL/TP, Paper Broker, order/position logic, market data, freshness, prompts, provider
and model selection, sessions, cadence, scheduler, REAL execution configuration and NAS100.
