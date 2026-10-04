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
  - [ ] H02 — 5m bar / Trade Manager sampling gap; address position-management bar coverage and the effect on historical outcomes.
  - [ ] H03 — mandatory V2 carryover from the independent V1 review; preserve the finding and its acceptance criteria from that source review (criteria are not restated here).
  - [ ] H04 — heartbeat / supervisor timing and recovery behavior.
  - [ ] H15 — dependency reproducibility and rollback/build repeatability.
  - [ ] ISSUE-004 — per-run deployed-SHA traceability, distinct from the experiment freeze identity.
- [ ] Define portfolio-level aggregate exposure/risk limits before a new PAPER execution phase.
- [ ] Define how simultaneous/multiple setups interact with existing open positions and pending orders, including duplicate exposure and any explicitly authorized pyramiding policy; test the decision and recovery paths.

## P1 — Provider cost, usage and quota observability

Derived directly from the V1 OpenAI balance incident.

- [ ] Record AI calls by run_id, agent, provider, model, symbol and timestamp.
- [ ] Record input/output/cached tokens when the provider exposes them.
- [ ] Estimate cost per call, run, day, agent and symbol.
- [ ] Dashboard: daily/weekly/monthly AI spend.
- [ ] Dashboard: calls and tokens per agent.
- [ ] Configurable soft-budget thresholds.
- [ ] Slack warnings at configurable usage/cost thresholds.
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

### Slack supplemental anti-noise policy

- [ ] Do not send routine WATCH/NO_SETUP every cycle.
- [ ] Do not repeat identical EXISTING_POSITION blocks unnecessarily.
- [ ] Immediate messages reserved for actionable state changes, failures, executions and risk events.
- [ ] Daily summary contains cycle totals, VALID_SETUP, executions, rejections, provider incidents, PnL and cost/usage.
- [ ] Incident start and recovery are paired.
- [ ] Severity levels standardized.

## P4 — End-to-end observability

Every run should be traceable through:

Market Data -> Structure -> Liquidity -> Macro -> Setup Reviewer -> VALID_SETUP -> Plan -> Risk -> Execution -> Position -> Journal -> Slack/Dashboard.

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
exposure, multiple setups, and existing positions/pending orders. Prevent
unintended duplicate exposure; any pyramiding behavior requires explicit owner
approval and deterministic risk authorization.

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
- [ ] Tests for duplicate-position blocking.
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
- [ ] Slack anti-noise behavior verified.
- [ ] Position lifecycle verified end-to-end.
- [ ] Restart recovery verified with open PAPER state.
- [ ] Natural-cycle soak test completed.
- [ ] Final go/no-go certification recorded.

## Deferred — REAL execution

REAL trading is explicitly outside the current V2 implementation authorization. Any future proposal requires a separate architecture, threat/risk review, broker adapter certification, kill switch, exposure limits, authorization workflow, reconciliation, and a new explicit approval.
