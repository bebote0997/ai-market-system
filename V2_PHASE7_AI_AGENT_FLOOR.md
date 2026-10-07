# V2 Phase 7 — AI Agent Floor (P7.0 audit, characterization & design gate)

Baseline `main` `405df6a8b18172cd94da23b9e48380a4405ba8cd` (Phase 6 merged, PR #11). Branch `v2/phase7-ai-agent-floor`.
PAPER only · REAL DISABLED · NAS100 OFF · trading DB schema 3 · no runtime activation · no deploy.
P7.0 is read-only. Its only additions are this document and `test_phase7_ai_characterization.py` (15 tests). No
production behavior changed.

> **Naming warning.** `AUDIT_PHASE7.md`, `PHASE7_CERTIFICATION.md` and `runtime/certify_phase7.py` are **legacy V1**
> artifacts (V1 "Fase 7 — Demo Runner PAPER", 2026-09-17). They are not certification of V2 Phase 7 and are not
> modified or reinterpreted here.

## A. Current architecture map

```
market evidence (provider snapshot) -> freshness gate (runtime/gates.fresh_snapshot)
 -> existing-position management (TradeManager / B2.3B catch-up)               [runtime/service.py:299-313]
 -> deterministic floor: structure, liquidity, macro -> Setup Validator -> Trade Planner -> V1 Risk
                                                                               [floor/orchestrator.run]
 -> AI floor (ai/orchestrator.run), sequential, one provider instance:
      structure_ai -> liquidity_ai -> macro_ai -> setup_reviewer_ai -> trade_reviewer_ai (only if a plan exists)
      each: agents/<x>.build_request (deterministic evidence + positional evidence_ids)
            -> ai/runtime.call_agent: skip if no evidence (NO_DATA, no call)
               -> provider.generate (OpenAIProvider: Responses API, strict json_schema with enums for
                  run_id/as_of/symbol/agent/recommendation/evidence ids; retries; typed OpenAIProviderError)
               -> exception -> AIResponse(ERROR, warnings=("provider_exception:<ExceptionClass>",))
               -> validate_ai_response (identity, enums, confidence, evidence-id grounding) -> ERROR on failure
               -> in-memory AuditLog entry (validation reason, provider/model)
 -> _final_status: base deterministic status; PLAN_READY -> AI_CAUTION if setup review DISAGREE or
    trade review REJECT_RECOMMENDATION
 -> Store.save_reports (runs, agent_decisions, setups, risk_decisions, review_reports) in one transaction
 -> PROVIDER_FAILURE journal event if any agent ERROR; system_state ai_provider
 -> execution-time freshness/session recheck -> pending progression (requires the 4 non-trade agents OK/PARTIAL
    and status not AI_CAUTION/ERROR/RISK_REJECTED) -> paper_policy (all 5 reviews OK/PARTIAL, PLAN_READY,
    risk APPROVED) -> submit or SKIPPED with reason
```

The Phase 4 fixed-3R planner, Phase 5 Risk V2 and the Phase 6 conflict path are library-only. They are not on this
runtime path.

## B. Agent contract matrix

| | structure_ai | liquidity_ai | macro_ai | setup_reviewer_ai | trade_reviewer_ai |
|---|---|---|---|---|---|
| Input | 1h/15m/5m structure payloads (bias, state, bos, retracement) | 1h/15m/5m liquidity payloads | accepted macro events / news items | setup status+side; the 3 specialist biases/statuses | trade plan levels + R:R |
| Evidence ids | `structure_<tf>` | `liquidity_<tf>` | `macro_event_<i>`, `news_item_<i>` | `setup_status`, `ai_<x>_bias` | plan fields |
| Allowed recommendation | none (advisory interpretation) | none | none | AGREE / CAUTION / DISAGREE / INSUFFICIENT_DATA | ACCEPT / CAUTION / REJECT_RECOMMENDATION |
| Prompt / version | `STRUCTURE_PROMPT` 1.0 | `LIQUIDITY_PROMPT` 1.0 | `MACRO_PROMPT` 1.0 | `SETUP_REVIEW_PROMPT` 1.0 | `TRADE_REVIEW_PROMPT` 1.0 |
| No evidence | NO_DATA, no call | NO_DATA, no call | NO_DATA, no call (also when there are simply no events) | always has evidence | always has evidence |

Shared by all: model `OPENAI_MODEL` (default `gpt-5.6-terra`), `store: false`, strict JSON schema (16 required fields,
`additionalProperties: false`; `run_id`/`as_of`/`symbol`/`agent_name`/`schema_version` pinned by enum, recommendation
enum, evidence arrays limited to the supplied ids), timeout `OPENAI_TIMEOUT_SECONDS` (30 s), retries 2.

Failure: typed `OpenAIProviderError` → ERROR response. Fallback: none (no deterministic substitute). Status mapping:
model-declared OK/PARTIAL/NO_DATA/ERROR, overridden to ERROR on any validation failure.

Persistence: `agent_decisions` (status, bias, confidence when OK/PARTIAL, recommendation, reasoning summary, evidence,
warnings, whitelisted metadata) and `review_reports.agents` (also the validation reason). Usage: returned in
`model_metadata`, **dropped** by the persistence whitelist. Latency: only the inactive System Health hook. Cost: none.

Malformed or partially valid responses: any shape, enum, identity or grounding defect turns the whole agent into
ERROR. Partial content never passes (characterized).

Binding to evidence: every call is bound to its own request (identity enums + id grounding). The ids are positional
labels, not content hashes, and free text (observations, reasoning, bias vs evidence) is **not** validated. A response
with invented prices in prose passes (characterized). That text is advisory: no deterministic gate reads it.

## C. Provider adapter audit (`ai/openai_provider.py`)

| Condition | kind | attempts (retries=2) | Notes |
|---|---|---|---|
| 401 / 403 | AUTH_ERROR | 1 | |
| 404 | MODEL_UNAVAILABLE | 1 | |
| 429 (no quota code) | RATE_LIMITED | 3 | backoff 0.4·2^n s + ≤0.1 jitter, or Retry-After ≤ 15 s |
| 429 with Retry-After > 15 s | RATE_LIMITED | 1 | stops immediately |
| 429/any with `insufficient_quota` type or code, `billing_hard_limit_reached`, `credit_balance_exhausted` | QUOTA_EXHAUSTED | 1 | body read, only type/code kept (sanitized) |
| 408, 409, 5xx | PROVIDER_ERROR | 3 | |
| other 4xx | PROVIDER_ERROR | 1 | |
| socket timeout | CONNECTION_ERROR (`timed_out=True`) | 3 | |
| connection failure | CONNECTION_ERROR | 3 | |
| response status ≠ completed | INCOMPLETE_RESPONSE | 1 | |
| malformed JSON / shape | INVALID_STRUCTURED_RESPONSE | 1 | |
| no API key | NOT_CONFIGURED | 0 | |

`provider.health`: NOT_CONFIGURED / ERROR (last call failed) / READY (last success had usage) / DEGRADED (no
successful call yet, **or** a success without usage). Recovery is automatic: a later success clears `last_failure`,
with no restart (characterized).

## D. Error taxonomy (what is technically distinguishable)

| Requested class | Distinguishable today? | Source |
|---|---|---|
| AUTH_ERROR | yes | HTTP 401/403 |
| RATE_LIMITED | yes | HTTP 429 without a quota code |
| QUOTA_EXHAUSTED / INSUFFICIENT_QUOTA | **yes, when the API returns the error type/code** (`insufficient_quota`, `billing_hard_limit_reached`, `credit_balance_exhausted`) | error body |
| TIMEOUT | yes, via `timed_out` (kind stays CONNECTION_ERROR; System Health refines to TIMEOUT) | exception |
| CONNECTION_ERROR | yes | URLError / ConnectionError |
| INVALID_RESPONSE | yes (INCOMPLETE_RESPONSE, INVALID_STRUCTURED_RESPONSE) | parser |
| INVALID_SCHEMA | yes, at `validate_ai_response` (invalid_* reasons) | validator |
| UNGROUNDED_RESPONSE | yes (`ungrounded_*_evidence`) | validator |
| PROVIDER_FAILURE | generic journal event; no kind | runtime |
| UNKNOWN_PROVIDER_ERROR | PROVIDER_ERROR (5xx/other) | adapter |

**Critical gap:** `call_agent` collapses every provider kind into `provider_exception:OpenAIProviderError`. The kind
survives only in `provider.last_failure` (overwritten by the next call) and in the System Health hook, whose sink is
**never installed** in the runtime. Durable records therefore cannot distinguish the September incident class
(quota) from rate limiting, auth or timeouts (characterized).

## E. Authority matrix

| Authority | Owner | AI role |
|---|---|---|
| Setup status / direction | Setup Validator (deterministic) | none (setup reviewer comments only) |
| Entry / SL / TP | Trade Planner (deterministic) | none (trade reviewer cannot change levels) |
| Quantity / risk | Risk Engine (V1 runtime; V2 library) | none |
| Conflict (same-symbol exposure) | V1 submit check; Phase 6 conflict engine (library) | none |
| Freshness / session gates | runtime/gates | none |
| Execution eligibility | `paper_policy` + runtime submit | **veto only**: DISAGREE / REJECT_RECOMMENDATION → AI_CAUTION; any agent not OK/PARTIAL → not eligible |
| Order submission | PaperBroker via runtime guarded write | none (AI never receives the broker; it gets a deep-copied account snapshot) |
| REAL execution / NAS100 | configuration + absence of a real broker / PAPER contract | none |

Characterized: forged AGREE/ACCEPT with confidence 1.0 never upgrades NO_SETUP, WATCH or RISK_REJECTED. The trade plan
and risk decision objects pass through by identity. Only DISAGREE / REJECT_RECOMMENDATION veto; CAUTION and
INSUFFICIENT_DATA do not. **No authority violation found.**

## F. Fail-closed analysis

| Situation | Current result | Execution |
|---|---|---|
| one agent fails | that agent ERROR; `final_status` stays PLAN_READY | blocked (`paper_policy`) |
| multiple / all agents fail (any kind) | all ERROR; `final_status` PLAN_READY; PROVIDER_FAILURE event | blocked; pending progression also blocked |
| malformed / schema / grounding failure | agent ERROR | blocked |
| legitimate macro NO_DATA (no events) | ai_macro NO_DATA | **blocked** (NO_DATA ≠ OK/PARTIAL), pending progression blocked |
| usage missing | accepted; health DEGRADED | unaffected |
| provider recovers | next call READY | normal |

Principle holds: AI or provider uncertainty never increases authority. The weaknesses are truthfulness and visibility.
The aggregate label stays `PLAN_READY` during a total outage (execution reason `POLICY_REVIEW_UNAVAILABLE`).
Recommended minimum V2 policy: no new final state. Keep the execution gate; add a non-authoritative, typed AI
availability field and typed reasons (DEC-7.1, DEC-7.6).

## G. Retry / timeout analysis

- Retries are per agent, bounded (≤ 3 attempts) with no cross-agent coordination. A rate-limit outage costs 5 agents ×
  3 attempts = **15 requests per cycle** (characterized). A quota outage costs 5 requests per cycle (no retry), every
  cycle, on every symbol.
- Worst-case latency per cycle (sequential): 5 × (3 × 30 s + backoff) ≈ 7.6 min. With Retry-After ≤ 15 s, ≈ 10 min.
  That is inside the 15-minute cadence but late enough that the execution-time freshness recheck may block the cycle.
- No retry storm (attempts are bounded), no duplicate journal decisions (one save per cycle), no duplicate economic
  action (slot claim + guarded writes). Token duplication: each retry after a server-side failure may consume tokens;
  there is no idempotency key.
- Proposed (P7.1, DEC-7.4/7.5): per-cycle short-circuit after a non-transient kind (QUOTA_EXHAUSTED, AUTH_ERROR,
  MODEL_UNAVAILABLE, NOT_CONFIGURED). The remaining agents are marked ERROR with the same kind and not called. Also a
  per-cycle AI time budget, after which remaining agents are skipped as ERROR.

## H. Token / cost / balance observability

| Signal | Status |
|---|---|
| input / output / total tokens | **directly observable** (`usage`), returned, **not persisted** |
| cached / reasoning token details | directly observable in `usage.*_details` when returned; not read today |
| response id (`id`) / `x-request-id` header | directly observable; **not captured** |
| latency | measurable locally; only the inactive health hook |
| HTTP status, error type/code | observable on errors; type/code sanitized; not persisted |
| rate-limit headers (`x-ratelimit-*`) | observable in headers; not captured |
| cost | **derived** = tokens × an Owner-configured price table (prices are not in the API response) |
| remaining runway | **derived/estimated** from configured budget − estimated spend |
| prepaid account balance | **NOT AVAILABLE through the current provider path** (the Responses API exposes no balance; no billing credential is or will be used) |

Warning mechanism (proposal, DEC-7.3/7.9): typed QUOTA_EXHAUSTED (immediate), N consecutive failed cycles (configurable),
estimated daily/monthly spend over soft thresholds, and missing usage. Delivered through the existing notification
path, OFF by default. Nothing persisted contains a key, header or response body.

## I. Provider-health state machine

Existing (V2 Phase 1 `runtime/system_health.py`, sink **not installed** = inactive): HEALTHY / DEGRADED / FAILED /
UNKNOWN. Error types RATE_LIMITED, BILLING_OR_QUOTA, TIMEOUT, AUTH_FAILURE and others; DEGRADED → FAILED after N
consecutive errors; a success resets to HEALTHY; NOT_CONFIGURED is UNKNOWN, never an error.

Recommended V2 mapping (READY = HEALTHY): healthy → RATE_LIMITED = DEGRADED (FAILED after N); healthy → QUOTA/AUTH =
FAILED immediately (non-transient); healthy → TIMEOUT = DEGRADED; failed → first success = HEALTHY plus a RECOVERY event
with first-known-good time and incident duration. No restart is needed (the provider is stateless). Activation of the
sink needs runtime authorization (DEC-7.8).

## J. Persistence / observability model (proposed per-call record)

`AI_CALL` journal event (trading DB, schema 3, no DDL), one per attempt-group, written with the cycle's reports:
run_id, setup_id (when VALID), agent, provider, model, prompt/contract version, evidence fingerprint (sha256 of the
canonical request evidence), request/response timestamps, latency, outcome (OK / NO_DATA_SKIPPED / ERROR), error kind,
HTTP status, attempts, input/output/total tokens, response id, health before/after. Never: API key, Authorization
header, request/response bodies beyond the existing whitelisted fields. Write failure is logged and ignored:
observability never changes authority (same rule as `_audit_safely`).

## K. Concurrency / idempotency

Agents run sequentially in one cycle. Cycles are serialized per symbol (`symbol_locks`); `run_id = uuid5(slot_key)`, so
a retried slot returns DUPLICATE before any AI call. Cross-symbol cycles may call the provider concurrently (shared
quota). Durable AI decisions: `agent_decisions` is keyed by (slot_key, agent) and written once per cycle inside
`save_reports`. No contradictory duplicates are possible. Proposed AI-call identity: (run_id, agent, attempt,
evidence fingerprint, model, prompt version). run_id and setup_id semantics are unchanged.

## L. Restart / recovery

| Crash point | Current effect | Recommendation |
|---|---|---|
| before provider call | run stays RUNNING → `recover()` marks FAILED / interrupted_run | none |
| during call / after response, before `save_reports` | tokens consumed, **no durable trace**; run FAILED on recovery | AI_CALL events written incrementally (best effort) so spend is visible |
| after `save_reports`, before execution | reports durable; run FAILED; no order | none (next slot is a new run) |
| after AI decision, before Conflict/Risk | AI is upstream of every economic writer; guarded writes + slot claim prevent duplicates | none |

## M. Historical incident mapping (September 2026, V1_POSTMORTEM.md §4)

Observed: balance -$0.05; structure/liquidity/macro/setup_reviewer ERROR (PROVIDER_FAILURE); recovery after recharge
with no code or config change (first good cycle ≈ 2026-09-30 10:00 UTC; 88 consecutive completed cycles).

With the current code an identical incident would:
- classify each call as QUOTA_EXHAUSTED, **if** the API body carries the quota type/code;
- spend 1 attempt per agent;
- block execution and pending progression;
- record PROVIDER_FAILURE and `provider_exception:OpenAIProviderError`, **without the kind**;
- recover automatically.

The postmortem's root-cause hypothesis still could not be confirmed from durable records. That is the core V2 Phase 7
gap. The 2026-10-02 RATE_LIMITED event was on the market-data path and must not be attributed to AI.

## N. Risks

- CRITICAL: none.
- **HIGH**
  - **H-7.1 (operational / observability):** typed AI provider failure (quota vs rate limit vs auth vs timeout) is
    not persisted and not surfaced; the component that classifies it (System Health) is inactive. A repeat of the
    September incident would again be undiagnosable from durable state.
- **MEDIUM**
  - M-7.1: token usage dropped by the persistence whitelist; no cost record.
  - M-7.2: `final_status` stays PLAN_READY under a total AI outage (truthfulness of the aggregate label).
  - M-7.3: an outage costs up to 15 requests and ≈ 7.6–10 min per cycle; no circuit breaker.
  - M-7.4: legitimate macro NO_DATA (no events) blocks execution and pending progression (existing V1 policy; needs an
    explicit Owner policy, handoff P2).
  - M-7.5: an AI outage freezes pending-order progression (coupling H-6.2 / P1); positions are still managed.
  - M-7.6: no request id / rate-limit header capture; a crash between call and persistence leaves no trace of spend.
- **LOW**
  - L-7.1: positional evidence ids; free text and bias are unvalidated (advisory only).
  - L-7.2: `health` reports DEGRADED after a success without usage.
  - L-7.3: the System Health TIMEOUT refinement is not reflected in the provider kind.

## O. Owner decisions

| ID | Problem / evidence | Options | Recommendation | Safety / compatibility |
|---|---|---|---|---|
| DEC-7.1 | Kind lost in `call_agent` (H-7.1) | A: carry typed kind into the response warning (`provider_error:<KIND>`) and the audit entry · B: keep | **A** | no authority change; warning text changes (tests pinning the old text updated explicitly) |
| DEC-7.2 | No durable per-call record | A: `AI_CALL` journal events (schema 3) · B: new table (schema bump) · C: none | **A** | additive; schema 3 |
| DEC-7.3 | Tokens / cost invisible (M-7.1) | A: persist tokens in AI_CALL + derived cost from an Owner price table + soft budgets · B: tokens only · C: none | **A** (prices supplied by the Owner; estimates labelled as estimates) | no balance API; no billing credential |
| DEC-7.4 | Outage multiplies calls (M-7.3) | A: per-cycle short-circuit on non-transient kinds · B: + cross-cycle breaker with cool-down · C: none | **A** (B later, with evidence) | fewer calls; same fail-closed result |
| DEC-7.5 | Cycle latency under outage | A: per-cycle AI time budget (e.g. 120 s, configurable) · B: keep | **A** | remaining agents ERROR = still blocked |
| DEC-7.6 | Aggregate label under outage (M-7.2) | A: keep `final_status`; add non-authoritative `ai_availability` (ALL_OK / PARTIAL_FAILURE / TOTAL_FAILURE + kinds) · B: new final state | **A** (no new state) | gate unchanged |
| DEC-7.7 | Partial availability policy (M-7.4) | A: keep strict (every agent OK/PARTIAL) · B: accept macro NO_DATA when the deterministic macro report is legitimately empty | **A in P7.1** (B is an economic/strategy decision; separate) | B would increase execution authority, so it needs explicit approval |
| DEC-7.8 | System Health inactive | A: library + tests only (no activation) · B: install the sink behind an OFF flag | **A**; B only with runtime authorization | runtime OFF |
| DEC-7.9 | Alerts | A: typed alerts (quota immediate, N consecutive failures, soft budget) through the existing notification path, OFF by default · B: none | **A** (OFF) | no secrets in messages |
| DEC-7.10 | Grounding strength (L-7.1) | A: keep positional ids (advisory) · B: content-bound evidence fingerprints | **A** (record the fingerprint in AI_CALL only) | none |
| DEC-7.11 | Pending progression frozen during an AI outage (M-7.5) | A: keep (certified P1) · B: decouple | **A** | B would change certified Phase 2 behavior |

## P. Proposed P7.1 implementation plan (after decisions; recommended options)

- Batch A (typed outcomes): typed kind propagation (DEC-7.1); `ai_availability` (DEC-7.6); health quirk fix behind the
  existing property (L-7.2) only if approved.
- Batch B (observability): `AI_CALL` journal record with tokens, latency, response id, attempts, evidence fingerprint
  (DEC-7.2/7.3); derived cost with an Owner price table; written best-effort, never affecting authority.
- Batch C (resilience): per-cycle short-circuit (DEC-7.4) and AI time budget (DEC-7.5); System Health mapping and
  recovery event (library); alerts OFF (DEC-7.9).
- Every batch: no runtime activation, no economic change, characterization tests updated only where an approved
  decision supersedes them.

## Q. Certification gates for P7.2

1. Typed kind for every class in D is durable per call and per run; quota is distinguishable from rate limit, auth
   and timeout.
2. No AI path increases authority: the AuthorityTests matrix and the full fail-closed table pass.
3. An outage costs ≤ the agreed calls per cycle and ≤ the time budget; recovery is automatic and recorded.
4. Tokens are persisted; cost is labelled estimated; no balance claims; no secret in any record (grep + tests).
5. Observability failure cannot change any decision (fault injection).
6. Replay of a synthetic quota incident (offline) reproduces the September signature and recovery.
7. Phase 2–6 regressions and the full suite green; REAL disabled; NAS100 off; schema 3; runtime OFF.

---

# P7.1 — Controlled implementation (DEC-7.1 → DEC-7.11 approved)

Owner interpretations: **DEC-7.7 strict** (macro_ai NO_DATA keeps blocking execution) and **DEC-7.11** (Phase 2
pending progression unchanged). AI stays veto-only. No runtime activation, no economic change.

## Batch A — typed outcomes + ai_availability

- `ai/outcomes.py`: typed per-call outcomes. Provider: AUTH_ERROR, RATE_LIMITED, QUOTA_EXHAUSTED,
  MODEL_UNAVAILABLE (HTTP 404), TIMEOUT (CONNECTION_ERROR with `timed_out`), CONNECTION_ERROR, INVALID_RESPONSE
  (INCOMPLETE / INVALID_STRUCTURED), PROVIDER_FAILURE (5xx, other 4xx), NOT_CONFIGURED, UNKNOWN_PROVIDER_ERROR (any
  non-provider exception). Validator: INVALID_SCHEMA, UNGROUNDED_RESPONSE. Model: MODEL_REPORTED_ERROR (schema-valid
  response with status ERROR). Plus OK and NO_DATA. Sets: NON_TRANSIENT = {AUTH_ERROR, QUOTA_EXHAUSTED,
  MODEL_UNAVAILABLE, NOT_CONFIGURED}; TRANSIENT = {RATE_LIMITED, TIMEOUT, CONNECTION_ERROR, PROVIDER_FAILURE}.
- `ai/runtime.call_agent` (DEC-7.1): every ERROR response keeps its existing first warning (System Health reads
  `warnings[0]`) and gets `ai_outcome:<OUTCOME>` appended. The AuditLog entry records `outcome`, sanitized
  `error_kind` and `http_status` (never a body or key). Typed errors are therefore durable through the existing
  `agent_decisions.warnings` and `review_reports.agents[].outcome`.
- `ai_availability` (DEC-7.6) on `AIFloorReport` and in `review_reports`: `{version V2_P7_AI_AVAILABILITY_1,
  authoritative: false, state ALL_AVAILABLE | PARTIAL_FAILURE | TOTAL_FAILURE | NO_AGENTS, agents, failed_agents,
  failure_outcomes, no_data_agents}`. It is computed after `final_status` and read by no gate (source-checked).
  `final_status` semantics are unchanged.
- Superseded P7.0 characterization (explicit): the "kind collapse" test now pins typed propagation, and the grounding
  test expects the appended typed warning.
- Tests: `test_phase7_ai_outcomes.py` (9). Full suite 969/969.

Batch A SHA: `2ab5e79a03a3192b813cb0e9ec295e9547976736`.

## Batch B — durable AI_CALL observability, usage, estimated cost

- `ai/openai_provider.py`: sanitized `last_call` per `generate()`. It holds `requested_at`, attempts (`attempt`,
  `http_status`, typed `kind`, `latency_ms`) and the response id (`resp_…`, identifier-shaped only). No key, header,
  prompt or body. Behavior and retries are unchanged.
- `ai/runtime.call_agent`: the AuditLog entry carries `call` (`called`, request/response timestamps, latency, provider
  attempts, response id) and `evidence_fingerprint`.
- Evidence fingerprint `V2_P7_EVIDENCE_1` (DEC-7.10) = sha256 of canonical JSON {symbol, agent, role, supplied
  deterministic evidence}. No run_id, timestamps or prompt. Positional evidence ids are unchanged.
- `ai/call_audit.py` (DEC-7.2/7.3):
  - **AI_CALL record `V2_P7_AI_CALL_1`**: run_id, setup_id, symbol, call_sequence, agent, provider, model,
    prompt_version, evidence_fingerprint, evidence_ids, called, requested_at, responded_at, latency_ms, attempts,
    retries, attempt_log, response_id, status, outcome, error_kind, http_status, validation, usage
    {input, output, total} or null, usage_reported, cost, health_before, health_after.
  - `persist()` writes one cycle's rows in ONE transaction (all or nothing) and skips an existing
    (run_id, call_sequence), so retries and restarts never duplicate.
  - Key names avoid the substring "token" because `safe_json` deliberately strips such keys as potential secrets.
    That filter is not weakened.
- Cost: `PricingTable` (Owner-configured USD per 1M input/output units) → `{"basis": "ESTIMATED", "currency",
  "amount", "pricing_source": "OWNER_CONFIGURED_TABLE"}`. Otherwise `PRICE_NOT_CONFIGURED` or `USAGE_UNAVAILABLE`
  (usage is never invented). No balance, credit or funds concept exists anywhere.
- Soft budgets (`SoftBudget`: cycle/daily estimated cost, cycle/daily usage total): crossing a threshold writes one
  `AI_SOFT_BUDGET` row per scope (`effect: OBSERVATION_ONLY`). Nothing blocks or allows anything.
- Default-durable usage: `review_reports.agents[].usage` (provider-reported or null), with no flag.
- Runtime hook: `RuntimeConfig.v2_ai_call_audit` (**OFF**; `from_env` never sets it; fingerprint unchanged while
  OFF). When ON, the AI_CALL rows and soft budget are written after `save_reports` through `_audit_safely`, so a
  failure is logged and the cycle is unchanged. The OFF/ON decision and orders are identical (tested). A missing
  record is never read as approval.
- Crash windows: before or during the provider call, or after the response but before `save_reports`, the run is
  recovered as FAILED (existing `recover()`) with no AI_CALL rows. During `persist`, the transaction rolls back
  (tested). After `persist`, rows are durable and a re-persist writes nothing. In no case is economic authority
  ambiguous: AI output is consumed only within the same cycle.
- Tests: `test_phase7_ai_call_audit.py` (11). Full suite 980/980.

Batch B SHA: `41d7f92c52e86252086f905bdf599dc21e870926`.

## Batch C — resilience, health/recovery, alerts (OFF)

- `ai/provider_health.py` — `ProviderHealthTracker`, in-process and cross-cycle, so no restart is needed. States
  READY / DEGRADED / FAILED / UNKNOWN:
  - UNKNOWN until the first provider evidence.
  - Any provider answer (OK, model NO_DATA, MODEL_REPORTED_ERROR) → READY. If the previous state was DEGRADED or
    FAILED, a `PROVIDER_RECOVERED` event is emitted with incident start, first-known-good time and duration.
  - NON_TRANSIENT (QUOTA_EXHAUSTED, AUTH_ERROR, MODEL_UNAVAILABLE, NOT_CONFIGURED) → FAILED immediately.
  - Transient or invalid provider output → DEGRADED, then FAILED after 3 consecutive.
  - Skipped calls (SHORT_CIRCUITED, AI_TIME_BUDGET_EXHAUSTED) cause no transition.
  - Events are emitted only on state changes. Content rejected by the validator (INVALID_SCHEMA,
    UNGROUNDED_RESPONSE) is agent health, not provider health.
- `ai/resilience.py` — `AICycleGuard` + `GuardedProvider`, one guard per cycle:
  - **Short-circuit (DEC-7.4):** after a NON_TRANSIENT failure, every later agent of the same cycle is not called.
    Its outcome is `SHORT_CIRCUITED` with `short_circuit_cause`. A quota outage costs **1 request per cycle**
    instead of 5. The next cycle has a new guard, so nothing is disabled across cycles and recovery is detected on
    the next call.
  - **Time budget (DEC-7.5, default 120 s):** an agent starting after the deadline is not called
    (`AI_TIME_BUDGET_EXHAUSTED`). The OpenAI adapter's optional `deadline` caps each attempt's timeout at the
    remaining budget and starts no retry that could not begin before the deadline; the outcome stays truthful
    (e.g. TIMEOUT).
  - Every guard outcome is an agent ERROR, so it fails closed exactly like any provider failure. It can only remove
    execution eligibility.
- Retry behavior (unchanged by default): up to 3 attempts (retries=2) for 408/409/429-without-quota/5xx/timeout/
  connection errors, backoff 0.4·2^n s + ≤ 0.1 s jitter or Retry-After ≤ 15 s. Immediate stop on quota/auth/404/other
  4xx/Retry-After > 15 s/budget cut.
- **Maximum theoretical AI latency per cycle:**
  - unguarded (flag OFF): 5 agents × (3 × 30 s + ≤ 30 s of waits) ≈ 600 s;
  - guarded: ≤ the cycle budget (120 s default), because attempts are capped by the remaining budget and later
    agents are not called (tested with an injectable clock).
- `ai/alerts.py` — `AIProviderAlerts(sink=None, enabled=False)` (DEC-7.9). It turns PROVIDER_FAILED /
  PROVIDER_RECOVERED transitions into `NotificationEvent`s for the existing isolated sinks. Only an explicit
  `enabled=True` with a sink delivers. Each (event, incident) is delivered at most once; repeated identical failures
  produce no transition and so no alert. Delivery errors are swallowed. Messages carry typed outcomes and times only
  (no key, header, body or balance). The test suite uses `FakeNotificationSink` only.
- Runtime hook: `RuntimeConfig.v2_ai_resilience` (**OFF**; never from env; fingerprint unchanged while OFF). When ON,
  the cycle's provider is wrapped in a new guard. Health transitions are journaled as `AI_PROVIDER_FAILED` /
  `AI_PROVIDER_DEGRADED` / `AI_PROVIDER_RECOVERED` (source `ai_provider_health`) through `_audit_safely`. Alerts are
  delivered only if an `ai_alerts` object is passed (default None = OFF). OFF vs ON over two real cycles (quota, then
  recharge) gives identical decisions; ON makes 1 provider request instead of 5 in the quota cycle and journals
  FAILED → RECOVERED.
- Tests: `test_phase7_ai_resilience.py` (12). Full suite 992/992.

## P7.1 summary

| Item | Value |
|---|---|
| Starting SHA (P7.0) | `6f4f68c929df28fa5863d0a689bb5e38d6e6967b` |
| Batch A | `2ab5e79a03a3192b813cb0e9ec295e9547976736` |
| Batch B | `41d7f92c52e86252086f905bdf599dc21e870926` |
| Batch C / final | the commit that adds this section (see the final report) |
| Tests | 960 (P7.0) → 969 (A) → 980 (B) → 992 (C), 0 failures / errors / skips |
| Economic behavior | unchanged (execution, core, agents, floor, riesgo, storage/database.py, runtime/gates.py untouched) |
| AI authority | unchanged: veto only; `ai_availability`, AI_CALL, health and alerts are read by no gate |
| Schema | 3 (journal rows only) |
| Runtime activation | none: `v2_ai_call_audit` and `v2_ai_resilience` OFF and not settable from env; alerts OFF |

Always-on, observability-only changes (approved DEC-7.1/7.6/7.3):
- the appended `ai_outcome:<KIND>` warning;
- AuditLog typed fields;
- `ai_availability` in the AI report and review;
- per-agent `usage` and `outcome` in the review;
- the provider's sanitized `last_call`.

Cost-estimation limitations: amounts are estimates from an Owner-supplied price table. They exclude any discounts,
cached-input pricing, taxes or provider-side adjustments. No prepaid balance is available through this provider path,
and none is claimed.

Remaining risks (for P7.2):
- MEDIUM: the AI_CALL durability and resilience benefits require future runtime activation of the two flags
  (separate authorization).
- MEDIUM (unchanged, Owner policy): macro NO_DATA strictness (DEC-7.7) and pending progression frozen during an AI
  outage (DEC-7.11).
- LOW: health state is in-process, so it resets to UNKNOWN on process restart (recovery after a restart is reported
  as a first success, not a recovery).
- LOW: free-text grounding unchanged (DEC-7.10).

## P7.2 certification procedure (independent reviewer)

1. Checkout the final P7.1 SHA. Confirm `git diff 405df6a -- execution core agents floor riesgo.py
   storage/database.py runtime/gates.py render.yaml` is empty, and that `runtime/config.py` defaults and `from_env`
   leave both flags OFF.
2. Run the full offline suite (no network). Focus files: `test_phase7_ai_characterization.py`,
   `test_phase7_ai_outcomes.py`, `test_phase7_ai_call_audit.py`, `test_phase7_ai_resilience.py`.
3. Veto-only: AuthorityTests (forged AGREE/ACCEPT never upgrade NO_SETUP/WATCH/RISK_REJECTED; plan and risk objects
   pass through by identity), `test_availability_is_never_authoritative`,
   `test_ai_does_not_reach_conflict_or_risk_authorities`.
4. Typed errors durable: `test_call_agent_preserves_typed_provider_kinds`, `test_review_payload_carries_typed_outcomes`,
   `test_typed_failure_survives_into_the_record`.
5. Tokens durable: `test_records_usage_attempts_ids_and_cost_labels`, `test_persist_is_atomic_idempotent_and_redacted`,
   review `usage` in `test_off_vs_on_same_decision_and_durable_records`.
6. Short-circuit / bounded retries / budget / recovery: `CycleGuardTests`,
   `test_off_vs_on_same_decisions_fewer_calls_and_recovery_events`.
7. Alerts OFF and deduplicated; no secret anywhere: `AlertTests`, redaction asserts.
8. Re-verify REAL disabled, NAS100 off, schema 3, no deploy, and that `runtime/service.py` changes run only under the
   OFF flags plus `_audit_safely`.
