# AI TRADING FLOOR V2 — HANDOFF REQUIREMENTS

Source: V1 final postmortem  
Status: **V2 planning input**  
Safety default: **PAPER ONLY; REAL EXECUTION DISABLED**

NAS100: **OFF** unless and until a separately authorized phase explicitly
changes that state.

## Purpose

Convert V1 evidence into explicit V2 requirements. These requirements supplement the V2 Master Roadmap & Checklist and must be mapped to the appropriate phase before implementation.

## P0 — Non-negotiable safety and governance

- [ ] REAL execution remains disabled by default and cannot be enabled implicitly by configuration drift.
- [ ] No V2 phase advances without documented acceptance criteria and certification evidence.
- [ ] Every change to main uses PR + CI + independent review according to GOVERNANCE.md.
- [ ] Render auto-deploy stays off unless separately authorized.
- [ ] V1 baseline/database/evidence remain immutable historical artifacts.
- [ ] PAPER and any future REAL execution path must be structurally separated and auditable.
- [ ] Carry the following V1 findings into V2 planning and certification; retain their source evidence and do not mark them closed without explicit acceptance evidence:
  - [ ] H02 — **V2 REQUIREMENT — OWNER APPROVED:** process ALL newly available bars since the last processed bar, chronologically and idempotently, including 5m position management, SL/TP, pending orders and fills. Never silently skip intermediate bars when several arrive between cycles. Acceptance must demonstrate catch-up and retry/restart without omitted or duplicate effects; retain the historical-outcome limitation in V1_POSTMORTEM.md.
  - [ ] H03 — **pending-order lifecycle:** persist the originating setup, creation time, lifecycle state and transition reason. Define TTL/expiration and cancel when the originating setup becomes invalid. An opposite thesis/setup must trigger cancellation or reassessment before any fill; the choice and eligibility after reassessment are **OWNER DECISION REQUIRED**. TTL values, time basis, expiration boundaries and any other unapproved lifecycle policy are **OWNER DECISION REQUIRED**, not invented defaults. Before a pending order may fill, require current-cycle AI/review evidence, current data/session eligibility and deterministic risk authorization; an old approval cannot substitute for the current-cycle gate. Missing, failed or cautionary review must not permit a fill. Exact V2 review/status policy, including whether PARTIAL is acceptable, remains **OWNER DECISION REQUIRED**. Persist cancellation/expiration/fill decisions atomically and idempotently: retries, restarts and competing cancel/fill transitions must not create duplicate fills/exposure or resurrect a terminal order. Acceptance must cover expiration, invalidated setups, opposite setups, blocked review and repeated/concurrent transitions under the approved policies.
  - [ ] H04 — **V2 REQUIREMENT — OWNER APPROVED:** liveness must be independent of progress. Demonstrate separately process-alive/heartbeat status and actual pipeline progress (last completed stage/run/bar and its timestamp); an active heartbeat is not evidence that the pipeline is advancing. Acceptance must detect a stalled pipeline while heartbeat remains active, and cover bounded supervisor/recovery/shutdown behavior.
  - [ ] H15 — **V2 REQUIREMENT — OWNER APPROVED:** deterministic build. The same relevant source, configuration and inputs must produce a reproducible, traceable build/release. Pin Python and transitive dependencies; preserve artifact identity and provenance, including source/configuration fingerprints. Acceptance must compare repeated builds from the same inputs and record their identities; do not equate code-level recovery with reproduction of a retained Render artifact.
  - [ ] ISSUE-004 — per-run deployed-SHA traceability, distinct from the experiment freeze identity.
- [ ] Define portfolio-level aggregate exposure/risk limits before a new PAPER execution phase.
- [ ] Define how simultaneous/multiple setups interact with existing open positions and pending orders, including duplicate exposure and any explicitly authorized pyramiding policy; test the decision and recovery paths.

Source evidence: the Phase 0 package `PHASE0_OWNER_CLOSURE_EVIDENCIA_2026-10-02.zip`, member `PHASE0_OWNER_DISPOSITIONS_2026-10-02.md`, section ISSUE-007 / F00-T12, explicitly records H02/H04/H15 as owner-approved requirements. `PHASE0_FINAL_CLOSURE_Evidencia_2026-10-02.zip`, member `PHASE0_FINAL_CLOSURE_REPORT_2026-10-02.md`, sections 1 and 6, records their accepted V1 limitations and mandatory V2 redesign. These are retained Phase 0 evidence artifacts, not files claimed to exist in this Git checkout.

H03's explicit V2 scope above follows the owner's PR #5 final-review correction. Repository evidence for the existing V1 behavior is [runtime/service.py](runtime/service.py) (`OperationalRuntime` pending-order gate using current-cycle AI health and freshness/session checks) and [execution/paper_broker.py](execution/paper_broker.py) (`PaperBroker.process_next_bar`, PENDING/FILLED/CANCELLED/REJECTED transitions), plus [AUDIT_FREEZE.md](AUDIT_FREEZE.md) (freshness and pending-fill evidence). These sources establish the baseline behavior, not an approved TTL or a completed V2 lifecycle. The retained Phase 0 report says H03 was not reconstructed from incomplete review numbering; no unavailable red-team text or acceptance criteria are attributed to it.

## P1 — Provider cost, usage and quota observability

Derived directly from the V1 OpenAI balance incident.

- [ ] Record AI calls by run_id, agent, provider, model, symbol and timestamp.
- [ ] Record input/output/cached tokens when the provider exposes them.
- [ ] Estimate cost per call, run, day, agent and symbol.
- [ ] Dashboard: daily/weekly/monthly AI spend.
- [ ] Dashboard: calls and tokens per agent.
- [ ] Configurable soft-budget thresholds.
- [ ] Notification-channel warnings at configurable usage/cost thresholds (email primary; Slack supplemental).
- [ ] Estimate remaining runway when enough usage data exists.
- [ ] Distinguish known billing/quota exhaustion from generic provider failure.
- [ ] Never infer an exact provider account balance unless an authoritative provider API supplies it.

## P2 — Provider resilience and failure taxonomy

- [ ] Typed outcomes: RATE_LIMITED, BILLING_OR_QUOTA, TIMEOUT, AUTH_FAILURE, PROVIDER_5XX, INVALID_RESPONSE, UNKNOWN_PROVIDER_FAILURE.
- [ ] Bounded exponential backoff with jitter where safe.
- [ ] Per-agent timeout budgets.
- [ ] Circuit-breaker or equivalent suppression for repeated provider failure.
- [ ] Recovery event with first-known-good timestamp.
- [ ] Incident duration persisted.
- [ ] Provider failure must never bypass risk or execution gates.
- [ ] Partial agent availability must have explicit deterministic policy.

## P3 — Notifications V2

Email is the **primary notification channel**. Slack is supplemental and must
not be described as the primary channel.

- [ ] Define email delivery, failure handling, deduplication and audit evidence.
- [ ] Do not let notification failure bypass risk/execution gates or create orders.

### Notification-channel anti-noise policy (email primary; Slack supplemental)

- [ ] Do not send routine WATCH/NO_SETUP every cycle.
- [ ] Do not repeat identical EXISTING_POSITION blocks unnecessarily.
- [ ] Immediate messages reserved for actionable state changes, failures, executions and risk events.
- [ ] Daily summary contains cycle totals, VALID_SETUP, executions, rejections, provider incidents, PnL and cost/usage.
- [ ] Incident start and recovery are paired.
- [ ] Severity levels standardized.

## P4 — End-to-end observability

Every run should be traceable through:

Market Data -> Structure -> Liquidity -> Macro -> Setup Reviewer -> VALID_SETUP -> Plan -> Risk -> Execution -> Position -> Journal -> Notification channel / Dashboard.

Required fields where applicable:

- [ ] run_id
- [ ] setup_id
- [ ] symbol
- [ ] direction
- [ ] market-data freshness
- [ ] agent status/reason
- [ ] setup status
- [ ] plan status
- [ ] risk status/reason
- [ ] execution_status
- [ ] execution_reason
- [ ] blocking_position_id
- [ ] blocking_order_id
- [ ] position_id
- [ ] order_id
- [ ] provider/model usage and estimated cost
- [ ] timestamps and latency per stage

## P5 — Strategy-rule formalization

Translate the owner's trading process into deterministic/inspectable inputs without allowing AI prose alone to authorize execution.

- [ ] Session news/context review.
- [ ] Previous important highs/lows as liquidity references.
- [ ] Trend identification.
- [ ] Multi-timeframe evidence: 1H / 15M / 5M.
- [ ] London / New York session context.
- [ ] Every strategy signal stores its evidence/provenance.
- [ ] Strategy changes are versioned so PAPER results remain comparable.
- [ ] **OWNER DECISION REQUIRED — NOT MANDATORY:** whether to use FVG/imbalance, Fibonacci, POI, or any extra confirmation, and if approved, their definitions and role. Do not treat these as V2 acceptance requirements before that decision.

## P6 — Position management V2

Position-management requirements must be designed and tested against aggregate
exposure, multiple setups, and existing positions/pending orders. Preserve the
protection against **UNAUTHORIZED additional exposure** and deterministic risk
authorization.

V1: `EXISTING_POSITION -> SKIP`.

V2: `EXISTING_POSITION -> ANALYZE NEW SETUP`.

Analysis does not automatically authorize opening another position, pyramiding,
reversal, hedging or increased exposure. The action after analysis depends on
approved V2 rules. Same-direction addition, pyramiding, opposite-direction setup
handling, hedging, reversal and maximum concurrent exposure are each
**OWNER DECISION REQUIRED** before implementation/certification; no policy is selected here.

- [ ] Define position lifecycle, aggregate exposure handling, and interaction with new setups while positions or pending orders exist.
- [ ] Persist every stop/target modification and reason.
- [ ] Test gaps, partial fills, rejected modifications and restart recovery.

**OWNER DECISION REQUIRED — NOT MANDATORY:** whether to adopt a four-tranche/four-leg
model, its target profile (including any 1R / 2R / 2R / 3R distribution), a
break-even move, or trailing/stop-advancement rules. These are unapproved design
options, not V2 acceptance criteria unless the owner explicitly selects them.

## P7 — Data integrity, replay and testing

- [ ] Deterministic market-data fixtures.
- [ ] Replay representative V1 runs.
- [ ] Tests for CURRENT/STALE/NO_DATA behavior.
- [ ] Tests for VALID_SETUP and no-setup boundaries.
- [ ] Tests for analysis of new setups with existing positions and prevention of UNAUTHORIZED additional exposure under approved V2 policies; do not require V1's automatic EXISTING_POSITION skip.
- [ ] Tests for provider outage/recovery.
- [ ] Tests for rate limiting.
- [ ] Tests for quota/billing classification.
- [ ] Tests for process restart with open positions.
- [ ] SQLite integrity and migration tests.
- [ ] Backup/restore rehearsal before final PAPER certification.

## P8 — Dashboard V2

- [ ] System health.
- [ ] Current session and market-data freshness.
- [ ] Latest run by symbol.
- [ ] Agent health/status.
- [ ] Open PAPER positions and lifecycle.
- [ ] Equity / realized / unrealized PnL.
- [ ] VALID_SETUP and risk decisions.
- [ ] Provider usage/cost/quota health.
- [ ] Incident timeline.
- [ ] Search by run_id/setup_id/position_id.
- [ ] Clear banner: PAPER / DEMO; REAL DISABLED.

## P9 — V2 PAPER certification gate

Before a new unattended V2 PAPER demo:

- [ ] Full automated test suite passes.
- [ ] Independent review complete.
- [ ] Exact deploy SHA recorded.
- [ ] Persistent-disk migration/backup verified.
- [ ] REAL execution independently verified disabled.
- [ ] Provider alerts tested using controlled fixtures/mocks where possible.
- [ ] Email delivery, deduplication, delivery status/audit evidence and delivery failure handling verified; failures must not create orders or bypass risk/execution gates.
- [ ] Notification-channel anti-noise behavior verified, including supplemental Slack where configured.
- [ ] Verify any fallback/retry mechanism against an explicitly approved policy. Slack support alone does not authorize automatic email-to-Slack fallback; no approved fallback mechanism is identified in the cited Phase 0 dispositions, so unspecified routing/retry policy remains **OWNER DECISION REQUIRED**.
- [ ] Position lifecycle verified end-to-end.
- [ ] Restart recovery verified with open PAPER state.
- [ ] Natural-cycle soak test completed.
- [ ] Final go/no-go certification recorded.

## Deferred — REAL execution

REAL trading is explicitly outside the current V2 implementation authorization. Any future proposal requires a separate architecture, threat/risk review, broker adapter certification, kill switch, exposure limits, authorization workflow, reconciliation, and a new explicit approval.
