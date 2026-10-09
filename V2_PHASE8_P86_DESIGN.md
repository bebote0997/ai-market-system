# V2 Phase 8 / P8.6 — Selective activation and experiment continuity (DESIGN ONLY, revision P8.6F)

- **Baseline:** `f2f87be26822b130445ebcd47e31b14f20cca69d` (branch `v2/phase8-execution`).
- **Scope:** documentation only. No code, DB, flag, Render, deployment or segment change. Nothing is certified.
- **Safety baseline:** PAPER only, REAL OFF, NAS100 OFF.
- **Phase 8 status:** BLOCKED. HIGH-8.1 is OPEN for the operational runtime.
- **Revision P8.6F** corrects the P8.6 design after the Copilot P8.6R audit.
  - The P8.6R report was **not attached** to the prompt. H-1, H-2 and M-1…M-7 are addressed as the P8.6F prompt
    describes them.
  - H-1 and H-2 are interpreted as below. If the report says otherwise, the Owner should flag it.
- **Decision status:** every DEC-8.13…DEC-8.22 below is a **PROPOSAL**. None is approved.
- **Revision P8.6F2** (targeted) corrects the remaining findings of the Copilot P8.6R2 audit: H-1, H-2, M-2, M-5, the
  experiment-status model and the G-8.INT counting rules.
  - The P8.6R2 report was **not attached**; the findings are taken from the P8.6F2 prompt.
  - Sections not listed in section 11 are **unchanged**, including the PASS sections and the validated journal
    metadata rules of section 4.
- **Revision P8.6F3** (targeted) corrects the Copilot **P8.6R3** findings. This time the complete reports P8.5R2,
  P8.6R, P8.6R2 and P8.6R3 were received and reconciled (section 14).
  - Changed: 1.9 (new, shared equity), 2.3 (EX attestation), 3.3 and 3.5 (replaced), 3.8 (replaced), 5 (G1, G14), 7, 8,
    9 and 13 (replaced), 14–16 (new), plus four carry-over clarifications (1.4 L-2, 1.5 M-3, 3.6 M-7, DEC-8.15 M-4).
  - **Every Owner decision is PENDING.**
- **Revision P8.6F4** (targeted) corrects the Copilot **P8.6R4** findings. The complete P8.6R4 report was received
  and read.
  - **Grounding correction (important).** The operational cycle does **not** use `execution/risk_engine_v2.py`. It
    sizes through the V1 floor: `floor.orchestrator.run` → `riesgo.evaluar_trade_plan(plan, equity, instrument,
    risk_config)`, then `runtime.paper_contracts.apply_paper_quantity_increment`.
  - V2's `reserve_and_submit` / `RISK_V2_DECISION` are not wired into `runtime/service.py`.
  - P8.6F3 §1.9.1 (and the evidence cited in P8.6R4) described V2. §1.9 is corrected below; its conclusion stands.
  - Changed: 0 (grounding rows), 1.9 (replaced), 3.3 / 3.5 / 3.8 (replaced; 3.5 also restores the M1–M10 table that
    P8.6F3 dropped by mistake), 3.4 (one precondition), 3.11 (new), 5 (G14 / G15 and 5.1, new), 7, 8, 9, 13, 14.4,
    15, 16.
- **Revision P8.6F5** (targeted) corrects the Copilot **P8.6R5** findings. The complete P8.6R5 report was received
  and read, including its GO/NO-GO table and final verdict.
  - H-1 and H-2 (PASS in design) are **not reopened**.
  - Changed: 0 (grounding rows), 1.9 (replaced: the V1 fill gate), 3.8 (R-HALT replaced, I-R1 restated), 3.11.1 (new:
    the A2 contract), 5 (G14 / G15 rows), 5.1 (replaced: REX contract, atomicity alternatives, paper-state hash and
    stages), 7, 8 (replaced: three decision levels, acyclic), 9, 13, 14.1 (R3-5), 14.5 (new), 15, 16.
  - **A design PASS is not an implementation certification.** Items that need code are marked
    `DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED`.
- **Revision P8.6F6** (targeted) corrects the four open **P8.6R6** findings: R6-1 AI reproduction, R6-2 the B
  evidence-gap rule, R6-3 the R-HALT race, R6-4 I-R1. The complete P8.6R6 report was received and read.
  - **Not reopened:** H-1, H-2, the V1 sizing and fill gate, R5-7, R5-8, the A2 concept.
  - Changed: 0 (grounding rows), 5.1.2.1 / 5.1.2.2 (new), 5.1.3.1 (new), 5.1.4 / 5.1.5 (replaced), 5.1.9 (new), 5
    (G14 / G15 rows), 3.8 (R-HALT replaced, I-R1 replaced by I-R1a–d), 8 (rows), 9, 13, 14.6 (new), 15, 16.
  - Everything here is **specified, not implemented**.
- **Revision P8.6F7** (targeted) corrects the four **P8.6R7** findings: R7-1 complete economic coverage, R7-2 the
  physical post-halt effects, R7-3 the quantity adapter and AI provenance, R7-4 post-halt persistence. The complete
  P8.6R7 report was received and read.
  - **Not reopened:** H-1, H-2, the V1 gate, the A2 concept, governance, the freeze.
  - Changed: 0 (grounding rows), 5.1.2.3 (new), 5.1.3.1 (replaced), 5.1.4 step 2, 5.1.5 (replaced), 5 (G15 row), 3.8
    (new P8.6F7 block; the halt invariants replaced), 8 (DEC-8.17b new; DEC-8.21b), 9, 13, 14.7 (new), 15, 16.
  - Status labels used: **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED / OWNER DECISION PENDING / OPEN**.
- **Revision P8.6F8** (targeted) corrects the three **P8.6R8** findings: R8-1 the account genesis and period boundary,
  R8-2 the Policy B guarantee, R8-3 the anchor cadence. It also records the LOW finding (G14 = deterministic replay
  only). The complete P8.6R8 report was received and read.
  - **Not reopened:** H-1, H-2, F1b and AI provenance, the post-halt persistence policy, EDG, the V1 gate, A2,
    freeze and governance. The only edits to them are the concrete dependencies listed in 14.8.
  - Changed: 0 (grounding rows), 5.1.3.1 (items 2, 3, 5), 5.1.3.2 / 5.1.3.3 (new), 5.1.4 (scope sentence), 3.8 (stop
    policy table, recovery paragraph, I-R1b / I-R1d / I-R13, NR25), 8 (rows), 9, 13, 14.8 (new), 15, 16.
- **Revision P8.6F9** (final targeted) corrects the **P8.6R9** findings: R9-1 HIGH (the OAR-G → E0 window, GEN-2 /
  R-GEN-1) and R9-2 LOW (R-HALT terminology). The complete P8.6R9 report was received and read.
  - Changed: 5.1.3.2 (G4 row, GEN-2, GEN-5, R-GEN-1 replaced, the new sealed-genesis gate, invariants and tests); 3.8
    (terminology unified on Alternative 1 / 2); 13 (items 34–36); 14.9 (new); 15; 16.
  - Nothing else was reopened.
- **Revision P8.6F10** (targeted) corrects the **P8.6R10** HIGH: there was no unconditional fail-closed barrier when the
  sealed-genesis configuration is missing. The complete P8.6R10 report was received and read.
  - Changed: 0 (one grounding row); 5.1.3.2 (G1 row, the R-GEN-1 sealed-mode bullet, the gate placement, one
    failure-policy row, the new start-mode contract, startup order, failure policy, invariants and tests); 13 (items
    37–41); 14.10 (new); 15; 16.
  - Nothing else was reopened.

### Interpretation of the HIGH findings

- **H-1 — continuity weakened an existing check.** P8.6 proposed checking resumability only against the current
  segment's `run_metadata` rows. Today EVERY row must match (freeze SHA, fingerprint, starting equity), so that
  proposal dropped validation of historical rows. P8.6 also proposed writing the segment's opening equity as "starting
  equity", which collides with `run_metadata.starting_equity` (the configured account equity, checked by the
  preflight).
- **H-2 — no immutable, verifiable segment chain.** P8.6 had several gaps:
  - S1 was "implicit" through NULL `experiment_id`;
  - the current segment was a mutable state key;
  - the seal used "the last persisted mark" with no age bound;
  - there was no way to detect later modification of sealed history;
  - compatibility with the immutable freeze values (`experiment_*` keys, `EXPERIMENT_BASELINE_SHA`) was not defined.

## 0. Grounding (verified at the baseline)

| Fact | Where |
|---|---|
| The global flag is a bool, OFF by default. OFF keeps the V1 fingerprint byte-identical; ON adds `v2_position_catch_up`. | `runtime/config.py:30,61-75` |
| The flag acts at three cycle sites: ingestion and REVISION journaling, position management, and pending progression. | `runtime/service.py:319,356,429` |
| The legacy newest-bar path advances `last_processed_at`, so a symbol added to the scope later is not retroactive. | `execution/trade_manager.py:36-69` |
| The experiment start is immutable once written: `start_experiment_if_unstarted` writes `experiment_started`, `experiment_started_at_utc`, `experiment_baseline_sha`, `experiment_freeze_sha` and one `EXPERIMENT_STARTED` event, after the first durable claim. Repeated starts keep the original values. | `storage/database.py:190-205`, `OPERATIONAL_RUNTIME_SPEC.md:17` |
| The cloud runner hard-codes `EXPERIMENT_BASELINE_SHA = f5032ba…`; the freeze SHA comes from `AI_FLOOR_GIT_COMMIT`. | `runtime/cloud_runner.py:15,24-27` |
| `run_metadata(slot_key, git_commit, config_fingerprint, experiment_id NULL, starting_equity = configured account equity, schema_version)`. A started experiment is resumable only if EVERY row equals (freeze SHA, current fingerprint, configured starting equity). | `storage/database.py:59,185-188`, `runtime/cloud.py:82-100` |
| **There is no durable "ended" marker.** The 14-day end can only be derived from `experiment_started_at_utc`, and nothing in the code stops runs after day 14. | code search (no end state) |
| `PaperAccount` has no mark timestamp. The mark of an open position is its `last_price` at `last_processed_at` (a bar start). | `execution/contracts.py`, `execution/trade_manager.py:68-69` |
| LOW-1 reproduced on temporary copies: a directly inserted review row with decision `RESOLVED` makes the gate CLEAR for a material anomaly. | `runtime/revision_review.py:91-97,181-184` |
| **(P8.6F4)** The runtime risk path is V1. Sizing: `quantity = min(equity × 1 % / (abs(entry − stop) × m), equity × 100 % / (entry × m))` in float, with `equity = broker.account.equity` (marked, account-wide), then a floor to the PAPER increment. There is no drawdown gate, no aggregate limit and no pending reservation in this path. | `runtime/service.py:366-371`, `riesgo.py:128-185`, `runtime/paper_contracts.py:19` |
| **(P8.6F4)** Runtime admission is in the service, in this order: `paper_policy(ai)` eligibility; not `paper_enabled`; `paper_blocked`; one PENDING order or open position per symbol; at submit, a re-check inside the guarded write (no new pending or position, and `equity == decision_equity`, else `STALE_PAPER_STATE`); `submit_plan` may decline (`BROKER_DECLINED_PLAN`); otherwise `AI_CAUTION`, `PENDING_ORDER` or `EXISTING_POSITION` | `runtime/service.py:455-509` |
| **(P8.6F4)** `risk_decisions` (table) stores status, reason, equity_at_decision, risk_fraction, quantity, capital_at_risk, entry, SL, TP and multiplier, only when the floor produced a `risk_decision`. It holds no configuration, increment, pre-increment quantity, state or trace | `storage/database.py:176-178` |
| **(P8.6F4)** `Scheduler.tick` writes a heartbeat, sets `scheduler_slot`, checks the session, then calls `run_cycle(symbol, slot)` **sequentially** for `config.enabled_symbols`, in tuple order. Every tick calls every symbol; once a slot is claimed, later ticks return `DUPLICATE` with no economic work | `runtime/scheduler.py:37-54` |
| **(P8.6F4)** `journal.id` is `INTEGER PRIMARY KEY AUTOINCREMENT`. SQLite assigns `max(MAX(id), sqlite_sequence.seq) + 1`; an explicit `id` is allowed and raises `seq` to it. The code never updates or deletes journal rows, and a rolled-back insert also rolls back `seq` | `storage/database.py:53` |
| **(P8.6F4)** `load_paper(account_id)` filters only `paper_accounts` by account; orders, fills and closed trades are loaded **for all accounts** | `storage/database.py:481-492` |
| **(P8.6F4)** The cloud loop is `while not stop: runner.tick(); runner.daily_summary(now); sleep 30 × 1 s (checking stop)`. SIGTERM only sets `stop` | `runtime/cloud_runner.py:36-64` |
| **(P8.6F5)** The runtime broker is `PaperBroker(account, instrument)` with `rr_policy=None`, so unstamped runtime orders use the **frozen V1 fill gate**. A fill is rejected (`ORDER_REJECTED {"reason": "post_fill_risk_or_geometry"}`) if equity is invalid, `risk_per_unit ≤ 0`, `reward ≤ 0`, `reward / risk_per_unit < 3`, `real_risk > current_equity × 0.01` **or** `real_risk > order.equity_at_submission × 0.01`, with `real_risk = (fill_open − SL) × qty × m` (LONG). No numeric telemetry is journaled on V1 rejections | `runtime/service.py:127-132`, `execution/paper_broker.py:78-80,137-200` |
| **(P8.6F5)** `submit_plan` stores `equity_at_submission = float(account.equity)` at submit time. Inside the runtime's guarded write this equals `decision_equity` (or the submit is refused as `STALE_PAPER_STATE`). It returns `None` (→ `BROKER_DECLINED_PLAN`) on any of: as_of not aware; `final_status ≠ PLAN_READY`; plan or decision missing or not APPROVED; run, symbol or side mismatch; plan as_of not aware; invalid quantity or prices; instrument or multiplier invalid; instrument symbol mismatch; invalid equity; an open position on the symbol. A duplicate `run_id` returns the existing order | `execution/paper_broker.py:47-76` |
| **(P8.6F5)** `paper_policy(ai)` is true only if `data_state == "CURRENT"`, `final_status == "PLAN_READY"`, `risk_decision.status == APPROVED`, `trade_plan` is present, and all five AI responses (structure, liquidity, macro, setup review, trade review) are present with status OK or PARTIAL. `ai_healthy` uses the first four. Legacy pending progression runs only if `paper_enabled ∧ paper_blocked is None ∧ ai_healthy ∧ final_status ∉ {AI_CAUTION, ERROR, RISK_REJECTED}`, and fills only on this cycle's newest 5m bar with `bar.timestamp > order.as_of`. Under catch-up, the same values feed `CurrentCycleGate` (B2.2) | `runtime/gates.py:42-53`, `runtime/service.py:426-457` |
| **(P8.6F5)** `Store.paper_state()` returns a **tuple of `paper_encode` strings** (account; sorted open positions; sorted closed trades; sorted orders; sorted fills), compared by equality in `save_paper`. **It is not a hash** | `storage/database.py:410-420` |
| **(P8.6F5)** Catch-up does one load, compute and `save_paper` **per bar** (`execution/position_catch_up.py:102-109`); the legacy path does one guarded write | — |
| **(P8.6F5)** Cycle order after the claim: ingestion (flag ON) → freshness → position management (legacy or catch-up) → `decision_equity = account.equity` → V1 floor (sizing) → AI → execution freshness and session re-check → `ai_healthy` → pending progression → broker reload → `paper_policy` and admission → submit (guarded write) | `runtime/service.py:276-515` |
| **(P8.6F5)** The DB path comes from `AI_FLOOR_DB_PATH` (default `data/runtime/trading_floor.db`) and is **not** part of the configuration fingerprint | `runtime/config.py:15,61-75,87` |
| **(P8.6F6)** `ai.final_status = _final_status(det, ai_setup_review, ai_trade_review)`, applied in this order: (1) `"equity_invalid" ∈ det.warnings` → `NO_DATA`; (2) `det.final_status ∉ VALID_FLOOR_STATUSES` → `ERROR`; (3) `det.final_status == PLAN_READY` and (`setup_review` present with `recommendation == "DISAGREE"`, or `trade_review` present with `recommendation == "REJECT_RECOMMENDATION"`) → `AI_CAUTION`; (4) otherwise `det.final_status`. The response **status is not consulted** in rule 3 | `ai/orchestrator.py:40-51` |
| **(P8.6F6)** The floor's deterministic status, in this order: invalid equity → `NO_SETUP` with warning `equity_invalid`; setup `NO_SETUP` / `WATCH` → that status; plan `None` → `PLAN_UNAVAILABLE` (which is **not** in `VALID_FLOOR_STATUSES`, so the AI status becomes `ERROR`); risk APPROVED → `PLAN_READY`, otherwise `RISK_REJECTED`. `ai_trade_review` exists only if a plan exists | `floor/orchestrator.py:14-46`, `ai/orchestrator.py:54-71`, `ai/contracts.py:20-22` |
| **(P8.6F6)** `AIResponse`: `status ∈ {OK, PARTIAL, NO_DATA, ERROR}`; `recommendation` optional, from `{AGREE, CAUTION, DISAGREE, INSUFFICIENT_DATA, ACCEPT, REJECT_RECOMMENDATION}` (validated) | `ai/contracts.py:12-18,42-60,100-125` |
| **(P8.6F6)** `PaperOrder` fields: `schema_version`, `order_id`, `run_id`, `symbol`, `side`, `quantity`, `planned_entry`, `stop`, `target`, `contract_multiplier`, `equity_at_submission`, `cost_rate`, `as_of`, `status`, `risk_policy_version`, `setup_id` | `execution/contracts.py:35-56` |
| **(P8.6F6)** The runtime is single-threaded for economic work. Python signal handlers run in the main thread **between bytecodes**, delayed until a running C call (e.g. an SQLite statement) returns. The cloud handler only assigns `stop = True` | `runtime/cloud_runner.py:36-42` |
| **(P8.6F7)** `load_paper` loads only `paper_positions` with status OPEN, so `paper_state()` (the CAS tuple) and `psh` **do not cover CLOSED `paper_positions` rows**. `save_paper` updates a closed position's row to CLOSED. Economic tables: `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `closed_trades` (each `(id TEXT PRIMARY KEY, payload TEXT)`) | `storage/database.py:48-52,410-420,437-449,481-492` |
| **(P8.6F7)** `OperationalRuntime.__init__` runs `recover()` and then, if the PAPER account is missing, a **startup reconciliation `save_paper`** (an economic write outside any cycle) | `runtime/service.py:89-110` |
| **(P8.6F8)** Account bootstrap. On **every** `OperationalRuntime` construction, if `load_paper` finds no account: if any `paper_orders` row or any `paper_positions` row exists → `STATE_INCONSISTENCY` event, `RuntimeError("paper state without account")` (fail-closed). Otherwise it creates `PaperAccount("1.0", account_id, starting_equity, starting_equity, starting_equity)` through `save_paper(expected_state=paper_state(None, orders, fills))`. The check does **not** look at `paper_fills` or `closed_trades`. The creation writes **no journal event**, and its payload has no timestamp (a deterministic function of `account_id`, `starting_equity` and the encoder) | `runtime/service.py:90-104` |
| **(P8.6F8)** `DemoRunner.__init__` constructs the runtime (so the account may be created) **before** `preflight`, the `runner` state, and `start_experiment_if_unstarted`. The cloud runner then ticks immediately | `runtime/demo_runner.py:69-90`, `runtime/cloud_runner.py:50-58` |
| **(P8.6F10)** `Store(path)` **creates** the parent directory, the DB file and the schema when they are missing (`mkdir`, `sqlite3.connect`, `_migrate`). `cloud_preflight` opens `Store(path)`, so it can create a DB. `DemoRunner` runs its in-process preflight **after** constructing `OperationalRuntime`. Neither preflight checks `AI_FLOOR_GENESIS_ANCHOR` or `allowed_signers` | `storage/database.py:65-80,100-113`, `runtime/cloud.py:73-76`, `runtime/demo_runner.py:69-83` |
| **(P8.6F8)** `Store.save_paper` opens its own transaction (`with self.transaction():` → `BEGIN IMMEDIATE`) **inside** the call. A caller-side check therefore always precedes `BEGIN` by some Python frames | `storage/database.py:118-125,422-430` |
| **(P8.6F7)** Quantity adapter `apply_paper_quantity_increment(report, instrument)` runs after `run_floor` and before the AI. In order: (1) no decision or not APPROVED → unchanged; (2) invalid increment → `ValueError`; (3) invalid quantity → `ValueError`; (4) `units = floor(Decimal(str(q)) / Decimal(str(step)))` and `rounded = float(units × Decimal(str(step)))`; (5) `rounded ≤ 0` → `final_status = RISK_REJECTED`, decision REJECTED, `quantity = 0.0`, `capital_at_risk = 0.0`, reason `paper_quantity_below_increment`; (6) `rounded > q` → `ValueError`; (7) `rounded == q` → unchanged; (8) otherwise `quantity = rounded`, `capital_at_risk = cap × (rounded / q)`, warning `paper_quantity_rounded_down` | `runtime/paper_contracts.py:19-52`, `runtime/service.py:368-371` |
| **(P8.6F7)** AI responses: `call_agent` validates each response with `validate_ai_response(response, request)` (type, `schema_version`, `run_id`, `symbol`, `agent_name`, `as_of`, status, bias, recommendation, confidence, grounded evidence). On a failure or a provider exception it **substitutes** an `ERROR` response built from the **request** identity with `recommendation = None` and the reason in its warnings. With no usable evidence the call is skipped and a `NO_DATA` response is substituted. The audit entry records the validation reason, outcome and evidence fingerprint | `ai/runtime.py:15-127`, `ai/contracts.py:91-125` |
| **(P8.6F7)** Persistence between the floor and the pending stage: `set_state(macro_provider)`, the macro `PROVIDER_FAILURE` event, `record_macro_awareness`, `_record_ai_health` events, **`save_reports`** (runs row, agent_decisions, setups, risk_decisions, review_reports), `record_analysis_events`, `_record_ai_calls`, `set_state(ai_provider)`, the AI `PROVIDER_FAILURE` event; then the execution-gate event and `record_execution`, `save_snapshot`, `finish`. `OperationalRuntime.close()` calls `store.heartbeat(clock, "STOPPED")`, which writes **`heartbeat` and `scheduler`**; `DemoRunner.close()` writes `runner`. `health_hooks.emit` is a no-op unless a sink is installed (no runner installs one) | `runtime/service.py:332-425,505-520,121-123`, `storage/database.py:282-285`, `runtime/demo_runner.py:165-168`, `runtime/health_hooks.py:11-33` |

---

## 1. Per-symbol catch-up contract

Unchanged from P8.6 except where marked **(P8.6F)**.

### 1.1 Environment and grammar

| `AI_FLOOR_V2_POSITION_CATCH_UP` | `AI_FLOOR_V2_POSITION_CATCH_UP_SYMBOLS` | Result |
|---|---|---|
| unset / `0` | unset | OFF; V1 behaviour, fingerprint and preflight report byte-identical |
| unset / `0` | set (even empty) | ValueError (proposed DEC-8.13) |
| `1` | unset / empty | ValueError |
| `1` | valid list | ON for exactly the listed symbols |
| other | — | ValueError (unchanged R1) |

- **Tokens:** split on `,`; every token must match `^[A-Z0-9]+$` exactly.
- **Rejected:** duplicates, `NAS100`, symbols not in `SUPPORTED_SYMBOLS` or not in `enabled_symbols`.
- **Canonical form:** a tuple in `enabled_symbols` order.
- **Evidence path:** must be set and separate (unchanged).

### 1.2 Configuration

- **Field:** `RuntimeConfig.v2_position_catch_up_symbols: tuple = ()`.
- **Invariants (`__post_init__`):**
  - the flag is ON ⇔ the scope is non-empty;
  - the scope is a subset of `enabled_symbols`;
  - no NAS100, no duplicates, canonical order.
- **Predicate:** `catch_up_applies(symbol)` is the only branch condition in the runtime.
- **`enabled_symbols`:** stays `("XAUUSD", "EURUSD")`.
- **Fingerprint:**
  - OFF: byte-identical;
  - ON: adds `v2_position_catch_up: true` and `v2_position_catch_up_symbols: [canonical scope]`.

### 1.3 Runtime per symbol (one runtime, one DB, sequential per-symbol cycles)

| Step | In scope | Enabled, out of scope |
|---|---|---|
| Evidence ingestion | as today; failure → `paper_blocked` | **observe-only** (1.5) |
| REVISION journaling | as today | recorded, tagged `managed_by: NEWEST_BAR`, `material: false` (1.6) |
| Position management | chronological catch-up from the watermark | legacy newest bar (byte-identical to OFF) |
| Pending progression | B2.2 gate on the committed bar | legacy |
| Entries / Risk Engine | unchanged | unchanged |

### 1.4 Preflight and preview

- **Preflight:** `catch_up_scope_valid`. The scope is reported in a report field and the `catch_up_scope` state key
  (ON only; the OFF report is unchanged).
- **Preview:** takes `--catch-up-symbols`, which **replaces** `--first-scope` (P8.6R L-2). Each position gets `path = CATCH_UP | NEWEST_BAR`; NEWEST_BAR positions
  carry `ongoing_high_8_1_exposure: true`.
- **Single authority:** the `flock`, `AI_FLOOR_INSTANCE_COUNT=1` and the slot claims. Running a second runtime on the
  same DB is forbidden.

### 1.5 (P8.6F, M-3) Observe-only fault isolation for out-of-scope symbols

1. **Failure handling:** the observe call is wrapped like `_audit_safely`.
   - Any `Exception` → one WARNING event `EVIDENCE_OBSERVE_FAILED` with `error_type` only (no message, no secret).
     The cycle continues on the legacy path with the same inputs as flag OFF.
   - `BaseException`s (KeyboardInterrupt, SystemExit) are not swallowed.
2. **Bounded latency:** the observe ingestion uses a dedicated short busy timeout (proposed ≤ 500 ms) instead of the
   10 s store default.
   - If the evidence DB is locked, the observation is skipped with `EVIDENCE_OBSERVE_SKIPPED_BUSY`. It must never
     delay the execution-freshness check of the cycle.
   - The elapsed time is recorded.
3. **No shared mutable state with the in-scope path:** observe ingestion happens inside the out-of-scope symbol's own
   cycle. It never touches `_last_ingest` or the catch-up objects of another symbol.
4. **Shared-file damage** (corrupt evidence DB, disk full) is NOT isolated, by design. The in-scope symbol then fails
   closed (`paper_blocked`), which is the existing, correct behaviour.
5. **Health signal:** after K consecutive observe failures for a symbol (proposed K = 4, one hour), the
   `evidence_observe` health state becomes `DEGRADED`. It never changes trading behaviour or the scope.
6. **Tests:**
   - injected exception;
   - locked evidence DB (timing bound);
   - corrupt evidence DB (in-scope fails closed, out-of-scope unchanged);
   - in each case, EURUSD **trading decisions and economic state** (orders, fills, positions, closed trades, account
    fields, watermarks) are identical to a flag-OFF twin. The DB as a whole is not compared, because it intentionally
    gains warning and health rows (P8.6R2 M-3).

### 1.6 (P8.6F, M-4) Exact scope of the revision gate

- **Coverage:** ALL `REVISION` anomalies in the Evidence DB, all enabled symbols, all timeframes, all time. Coverage
  is never filtered by scope or date.
- **Materiality:**
  - for in-scope symbols, the rule is unchanged;
  - for a symbol out of scope **at the time the anomaly is recorded**, the record is `material: false` with
    `managed_by: NEWEST_BAR`, because its economics never read committed evidence;
  - the record keeps that value when the symbol later joins the scope (no re-classification of history);
  - anomalies recorded after it joins are classified normally.
- **New blocker `ANOMALY_FOR_DISABLED_SYMBOL`:** an anomaly whose symbol is not in `enabled_symbols` (e.g. NAS100) →
  BLOCKED, with an Owner investigation.
- **When the gate is evaluated:** before any activation, before any segment transition (3.4), and as a G-8.INT
  criterion (5).

### 1.7 (P8.6F2, M-2) Evidence completeness and freshness before a symbol joins the scope

#### 1.7.1 Authoritative as-of

- **Runtime:** the cycle's slot (`runs.as_of`), never the wall clock (unchanged).
- **Preflight / scope-expansion check:** an explicit `as_of` = `slot_at(planned first cycle time)`, written to the
  report. An `as_of` later than the check's clock → fail (no future as-of).

#### 1.7.2 Market calendar

No calendar is invented (AGENTS.md: no invented data). There are two modes:

- **STRICT** (the default, and the only mode until an approved calendar exists): the expected bars are every 5m start
  `t` with `watermark < t` and `t + 5 min ≤ as_of`, around the clock.
  - Weekend, daily-break and holiday closures show up as missing bars, so completeness cannot be demonstrated across
    them.
- **CALENDAR:** only with an Owner-approved, versioned, per-symbol calendar artifact sourced from provider
  documentation.
  - Its SHA-256 is part of the configuration fingerprint.
  - The expected bars are the 5m starts inside its open intervals.
  - A calendar whose validity range does not cover the whole window → STRICT for that window.

#### 1.7.3 Definitions for an open position `p` of symbol `X`

- **Window:** `W(p) = (watermark_p, as_of]`.
- **`complete(p)`** holds only if all of the following hold:
  - every expected bar in `W(p)` is committed in the 5m evidence of `X`;
  - no `GAP` anomaly of `X` lies inside `W(p)`;
  - no `LATE` anomaly lies inside `W(p)` for a bar that is not committed.
- **Trailing gap:** the newest expected bar at `as_of` must be committed. Otherwise `TRAILING_GAP` (stale): not
  complete, even with no open positions.
- **Stale data:**
  - evidence rows carry no ingestion timestamp, so staleness is defined only by the trailing bar against `as_of`;
  - the cycle's existing freshness check (5m: 600 s) is unchanged.
- **Session boundaries:**
  - cycles and ingestion run only in session slots;
  - off-session bars reach evidence only if a later in-session snapshot contains them;
  - the provider's 5m lookback depth is **not assumed** (section 13); a window spanning off-session time is complete
    only if those bars were actually committed.

#### 1.7.4 Scope-expansion rule (fail-closed)

- `X` may join the scope only if `complete(p)` holds for every open position of `X`, and the trailing bar of `X` is
  committed at `as_of`.
- Otherwise `catch_up_evidence_contiguous = False` → NOT_READY.
- No backfill, no synthetic bar, no override.
- In STRICT mode, a position held across a closure blocks the expansion until it closes on the legacy path.

#### 1.7.5 Invariants

- **I-E1:** expansion READY ⇒ for every open position `p` of `X`, expected(`W(p)`) ⊆ committed(`X`, 5m).
- **I-E2:** expansion READY ⇒ the newest expected bar at `as_of` is committed.
- **I-E3:** expansion READY ⇒ no GAP anomaly, and no uncommitted LATE anomaly, inside any `W(p)`.
- **I-E4:** the mode is STRICT unless a valid calendar covers the window.

#### 1.7.6 Negative tests

- **NE1:** a missing interior bar → NOT_READY.
- **NE2:** a missing trailing bar → NOT_READY, also with no open position.
- **NE3:** a GAP anomaly in the window → NOT_READY.
- **NE4:** a window across a weekend in STRICT mode → NOT_READY.
- **NE5:** a calendar whose validity ends inside the window → STRICT → NOT_READY.
- **NE6:** an `as_of` in the future → fail.
- **NE7 (positive):** a fully committed window → READY.

### 1.8 EURUSD with HIGH-8.1 open

- **Mechanics:** with a 15-minute cycle, two of every three 5m bars are never checked for SL/TP. Touches are missed or
  applied late at another price.
- **Bias:** in both directions; holding times are distorted; the shared account propagates the effect to other
  symbols' sizing.
- **Conclusion:** EURUSD results are not certifiable while out of scope, and account-level metrics are contaminated.
- **Proposed DEC-8.16:** EURUSD keeps trading on the legacy path. Its trades, and the account-level metrics of any
  segment where it is out of scope, are tagged `HIGH_8_1_AFFECTED` and excluded from certified metrics. EURUSD joins
  the scope after the XAUUSD gate and 1.7 are met.
- **(P8.6F3) Superseded by 1.9:** shared equity makes XAUUSD sizing depend on EURUSD in a mixed-path segment (P8.6R3
  HIGH). The proposal above therefore no longer implies any XAUUSD certification. Mixed-path segments are
  `TECHNICAL_ONLY`; see 1.9.3 and DEC-8.16.

---

### 1.9 (P8.6F5) Shared equity: sizing, the V1 fill gate, the multi-symbol sequence and the certification policy

#### 1.9.1 Coupling in the operational (V1) path (corrected again; P8.6R5 HIGH)

| Coupling | Code | Effect of EURUSD on XAUUSD |
|---|---|---|
| Account equity | `TradeManager`: `equity = starting + realized + unrealized`, account-wide | EURUSD closes and marks move the single equity value |
| **Sizing at decision** | `evaluar_trade_plan(plan, capital_actual = decision_equity)`; `qty = min(E × 1 % / (abs(entry − SL) × m), E × 100 % / (entry × m))`, then the increment floor | The XAUUSD quantity is proportional to `decision_equity`, which includes EURUSD PnL |
| **Equity at submission** | `order.equity_at_submission = account.equity` at submit (equal to `decision_equity`, else `STALE_PAPER_STATE`) | Frozen into the order |
| **Fill admission (V1 gate)** | `real_risk > current_equity × 1 %` **or** `real_risk > equity_at_submission × 1 %` → REJECTED; also the fill R:R < 3 and the geometry checks | A EURUSD loss between the XAUUSD submission and its fill lowers `current_equity`. That can **reject the XAUUSD fill**, even with the same fill price. A EURUSD gain cannot rescue a fill above the submission-equity bound |
| **Fill timing** | A pending XAUUSD order is evaluated only in a later XAUUSD cycle, on that cycle's newest 5m bar (legacy) or through B2.2 (catch-up), after that cycle's management step | `current_equity` at fill includes EURUSD as of EURUSD's previous cycle (1.9.4) |
| Admission at submit | The service gates, and `equity == decision_equity` inside the guarded write | A concurrent equity change refuses the submission |
| Not in the runtime | V2 conservative basis, drawdown gate, aggregate limit, pending reservations, the V2 fill money check | Not applicable to stamped-free V1 runtime orders |
| HIGH-8.1 on EURUSD | newest-bar management | Biased EURUSD outcomes feed the sizing **and** the fill gate |

**Conclusion:**
- XAUUSD economics in a mixed-path segment depend on biased EURUSD outcomes at **two points**: entry sizing and fill
  admission. Mixed-path segments stay `TECHNICAL_ONLY`.
- G14 and G15 must reproduce both points (5.1).
- P8.6F4's statement that the coupling is "through sizing only" is **withdrawn**.

#### 1.9.2 Alternatives (unchanged in substance)

- **A** (both symbols under catch-up, one account) is the only configuration eligible for economic certification. It
  is necessary, not sufficient: G14 and G15 must also pass on REX evidence (5.1).
- **B** (XAUUSD only) is `TECHNICAL_ONLY`.
- Neither changes `enabled_symbols`.

#### 1.9.3 Certification policy (proposal; DEC-8.16 PENDING)

1. Mixed-path segments are never economically certifiable.
2. B is optional, as a `TECHNICAL_ONLY` stage.
3. A is necessary but not sufficient; certification requires G14 (entry sizing **and** fill admission) and G15 (stage
   replay).
4. Excluding a symbol from trading needs its own freeze decision.

**Invariants**
- **I-S1:** a segment with any enabled symbol out of scope has `certification_eligibility = TECHNICAL_ONLY`.
- **I-S2:** a day on which any enabled symbol was out of scope is not counted.
- **I-S3:** every counted run passes G14.
- **I-S4:** the counted window passes G15.
- **I-S5 (new):** every pending-order evaluation (fill, reject or cancel) in a counted window reproduces from REX
  inputs.

**Negative tests**
- **NS1–NS6:** unchanged.
- **NS7:** a XAUUSD fill rejected because EURUSD lowered `current_equity` → the oracle reproduces the rejection and
  its clause.
- **NS8:** a fill accepted against `current_equity` but above `equity_at_submission × 1 %` → reproduced as a
  rejection.

#### 1.9.4 Multi-symbol sequence (unchanged; fill timing added)

- **Order:** for each slot, the cycles run in `enabled_symbols` order, sequentially.
- **XAUUSD at `t` sees EURUSD as of EURUSD's cycle at `t − 15 min`, both when sizing and when evaluating its pending
  fill:**
  - **sizing:** `decision_equity` is read after XAUUSD's own management step;
  - **pending fill:** `current_equity` is read in the pending-progression write, after XAUUSD's own management.
- **EURUSD at `t` sees XAUUSD after XAUUSD's full cycle at `t`.**
- No change to the operational order is proposed; G15 replays it stage by stage (5.1.6).

## 2. (P8.6F2) Experiment status — four independent axes, read-only

**No production query.** The procedure runs only on an Owner-made copy: SQLite backup API (or a WAL checkpoint),
opened `mode=ro`, with its SHA-256 recorded before and after. `copy_time` is the moment the copy was taken, stated by
the Owner.

### 2.1 Inputs

- The copy and its SHA-256.
- `copy_time` and `now_utc`, stated explicitly.
- The expected freeze values from `EXPERIMENT_FREEZE.md`: baseline `f5032ba…`, equity 10000, symbols
  XAUUSD/EURUSD, schema 3.
- The Owner-declared deployed SHA (`AI_FLOOR_GIT_COMMIT`), if any.
- **New:** the Owner's execution attestation (2.3 EX).

### 2.2 Read-only checks (all queries are SELECTs)

| ID | Check |
|---|---|
| Q1 | `PRAGMA integrity_check` = ok; `schema_info.version` = 3 |
| Q2 | `system_state` values: `experiment_started`, `experiment_started_at_utc`, `experiment_baseline_sha`, `experiment_freeze_sha`, `runner`, `heartbeat`, `last_run`, `last_success`, `scheduler`, `enabled_symbols` |
| Q3 | `journal` rows with `event_type='EXPERIMENT_STARTED'`: count, ids, payload |
| Q4 | `run_metadata`: distinct (git_commit, config_fingerprint, experiment_id, starting_equity, schema_version) with counts |
| Q5 | `runs`: count by status; runs with status `RUNNING` |
| Q6 | `paper_accounts` for `paper-main`; open positions with `last_processed_at`; pending orders |
| Q7 | the history mapping of 3.5 (every rule M1–M10) |
| Q8 | if an Evidence DB copy is given: REVISION anomalies and the gate result (read-only) |

### 2.3 Axes (each evaluated independently)

**FS — Formal start**
- **STARTED** if all of the following hold:
  - `experiment_started='1'`;
  - `experiment_started_at_utc` is a valid UTC timestamp ≤ `copy_time`;
  - the baseline and freeze SHAs match the SHA pattern;
  - there is **exactly one** `EXPERIMENT_STARTED` event, with payload SHAs equal to the keys;
  - the baseline is `f5032ba…` (or the S1 genesis of a verified chain).
- **NOT_STARTED** if `experiment_started` is absent or ≠ '1' **and** there are zero `EXPERIMENT_STARTED` events.
- **START_INCONSISTENT** otherwise.

**EX — Execution at `copy_time`**

A DB copy can never prove a process is stopped.
- **RUNNING:** `runner='RUNNING'` and `heartbeat` (or `last_run`) within 2 cadences (30 min) before `copy_time`.
- **STOPPED:** requires **both** of the following:
  1. **(P8.6F3)** A valid **execution attestation** (3.8 step H5). It must have:
     - a trusted signer (the Owner key in `allowed_signers`, 3.3.6);
     - an attestation time (UTC);
     - a scope (service, environment, DB path, copy SHA);
     - the H1–H4 observations;
     - freshness: the copy must be taken **after** the attestation time and within 60 minutes of it, and no
       heartbeat, run or journal activity may postdate the attestation in the copy.
  2. DB evidence consistent with it:
     - no heartbeat or `last_run` within 2 cadences of `copy_time`;
     - every run still in status `RUNNING` is listed for `recover()`.
- **UNKNOWN:** anything else, including a stale heartbeat without an attestation, an attestation contradicted by a
  recent heartbeat, or missing keys.

**PD — Period** (STARTED only; per segment)
- **PERIOD_ELAPSED** if `now_utc ≥ segment.started_at + 14 d` (S1: `experiment_started_at_utc`); otherwise
  **NOT_ELAPSED**.
- PERIOD_ELAPSED does **not** mean complete, valid or ended.
- **Result window:** `[started_at, started_at + 14 d)`. Runs after it are mapped to the segment but flagged
  `RUNS_AFTER_PERIOD` and kept out of the result window.

**RS — Results**
- **RESULT_CERTIFIED** only if an explicit append-only record exists: a future `EXPERIMENT_RESULT_CERTIFIED` event
  with `segment_id`, `segment_hash`, an independent-review reference and an Owner-signature reference, verifying
  against the anchored chain (3.3).
- No such record type exists today, so every experiment is **UNCERTIFIED**.
- Never inferred from elapsed days, run counts or PnL.

**INTEGRITY overlay:** OK, or INCONSISTENT (3.5 M10 or any 3.3.6 check fails). INCONSISTENT dominates every axis.

### 2.4 Actions by combination

| Combination | Proceed |
|---|---|
| INTEGRITY = INCONSISTENT, or FS = START_INCONSISTENT | **STOP**. Report the failing checks; no start, resume, transition or activation; nothing repaired automatically |
| EX = RUNNING | No action on the DB. The Owner performs EMERGENCY_HALT or an orderly stop (3.8), takes a new copy and re-classifies |
| EX = UNKNOWN | No start or transition. The Owner provides the attestation or stops the runner, then re-classifies |
| FS = NOT_STARTED, EX = STOPPED | No segmentation. Freeze amendment, then the existing start mechanism. PRE_START rows are quarantined (3.5 M8) |
| FS = STARTED, EX = STOPPED, PD = NOT_ELAPSED | Resume under the same configuration (if allowed by 3.8), or a transition (3.4) |
| FS = STARTED, EX = STOPPED, PD = PERIOD_ELAPSED | The period elapsed; the result is UNCERTIFIED. The Owner chooses between (a) a closing seal (`kind=CLOSE`) and a new experiment under a new freeze document, and (b) staying stopped. Result certification is a separate Owner and independent-review process |

The P8.6F states map as follows:
- NOT_STARTED → FS = NOT_STARTED;
- STARTED → FS = STARTED, EX = STOPPED;
- ACTIVE → EX = RUNNING;
- ENDED → **removed** (replaced by PD + RS);
- INCONSISTENT → INTEGRITY / FS.

## 3. (P8.6F, H-1/H-2) Experiment continuity — corrected design

### 3.1 Principles

1. **No existing check is weakened.** Every historical `run_metadata` row stays validated, against the immutable
   record of the segment it belongs to.
2. **Immutable original values.** The original `experiment_*` state keys and the `EXPERIMENT_STARTED` event are never
   modified. They form the genesis of the chain.
3. **Append-only records.** Segment records are journal events, never updated or deleted, linked by a hash chain and
   anchored outside the DB.
4. **Opening equity is separate.** `run_metadata.starting_equity` keeps its current meaning: the configured account
   equity, 10000, checked as today. Segment opening and closing equity live in the segment records.
5. No implicit close, transfer, cancel, re-price or SL/TP change at any boundary.

### 3.2 Segment identity (immutable)

- **`segment_index`:** 1, 2, 3, …
- **`segment_id`:** `"<experiment_started_at_utc compact>-S<index>"`. It derives from the immutable start, never from
  a mutable key.
- **Segment record:** a canonical JSON object (sorted keys, no NaN) with:
  - `segment_id`, `segment_index`, `previous_segment_hash` (null for S1);
  - `code_sha` (the deployed `AI_FLOOR_GIT_COMMIT`), `freeze_document_sha` (the commit of the freeze document or
    amendment governing it), `baseline_sha`;
  - `config_fingerprint`, `catch_up_scope`;
  - `configured_starting_equity` (10000);
  - `opening_equity`, `opening_snapshot` (positions and orders), `started_at_utc`.
- **`segment_hash`:** `sha256(canonical record)`.
- **S1 genesis** is materialized **at the first transition**, from values that already exist and are immutable:
  - `experiment_started_at_utc` and the baseline/freeze SHAs;
  - the fingerprint of the first `run_metadata` row of the experiment;
  - opening equity = configured 10000;
  - an empty opening snapshot (the experiment starts flat), to be verified by Q7.

  Nothing existing is rewritten. If any of these values is inconsistent, the state is INCONSISTENT and there is no
  genesis.
- **Current segment:** derived only from the chain (the last `SEGMENT_STARTED` without a following `SEGMENT_SEALED`).
  There is **no mutable "current segment" key**; a state key may exist as a cache, but it is always re-verified
  against the chain.

### 3.3 (P8.6F4, H-2) Deterministic segment integrity

#### 3.3.1 Hash and the self-hash rule (unchanged from P8.6F3)

- **Algorithm:** SHA-256, lowercase hex; `H(prefix, x) = SHA256(prefix + "\n" + x)`.
- **Prefixes:** `V2SEG/RECORD/1`, `V2SEG/SEAL/1`, `V2SEG/DIGEST/1`, `V2SEG/TABLE/1`, `V2SEG/PROPOSAL/1`.
- **Self-hash rule:** an object storing its own hash in field `f` is hashed without `f`. This applies to the proposal
  `Q` (`proposal_hash`), the seal payload `P_n` (`seal_hash`) and the segment record `R_n` (`segment_hash`).

#### 3.3.2 Canonical serialization (unchanged)

CJ = `json.dumps(sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)` in UTF-8, with:
- REAL as `float.hex`;
- TEXT as stored;
- BLOB as hex;
- NULL as `null`.

A row is `CJ({column: value})` over **all** columns, including `id`.

#### 3.3.3 (P8.6R4 MEDIUM) Journal identifiers and coverage without gaps

**Boundary base `B_n`:**
- read inside the COMMIT transaction as `MAX(journal.id)`;
- the `sqlite_sequence` row for `journal` must exist and `seq` must equal `B_n`;
- otherwise INCONSISTENT. The code never deletes journal rows and a rolled-back insert rolls back `seq`, so
  `seq ≠ MAX(id)` means deletion or manual editing;
- the "next id = MAX + 1" assumption is never used.

**Explicit identifiers:** the three transition rows are inserted with **explicit** `id` values, and every value is
fixed in `Q`:

| Row | id | timestamp | run_id | symbol | source | event_type | severity | payload |
|---|---|---|---|---|---|---|---|---|
| SNAP | `B_n + 1` | `sealed_at_utc` | NULL | NULL | `experiment_segment` | `SEGMENT_REVIEW_SNAPSHOT` | `INFO` | CJ of the segment runs' `review_reports` rows |
| SEAL | `B_n + 2` | `sealed_at_utc` | NULL | NULL | `experiment_segment` | `EXPERIMENT_SEGMENT_SEALED` | `INFO` | `P_n` + `seal_hash` |
| START | `B_n + 3` | `sealed_at_utc` | NULL | NULL | `experiment_segment` | `EXPERIMENT_SEGMENT_STARTED` | `INFO` | `R_{n+1}` + `segment_hash` |

- Each insert must change exactly one row.
- After the three inserts, `seq` must equal `B_n + 3`.
- Any deviation → ROLLBACK, INCONSISTENT.
- No other writer can interleave: COMMIT holds `BEGIN IMMEDIATE`, the runner is stopped, and the tool holds the
  `flock`.

**Partition of journal ids** (every id in exactly one covered set):
- `[1, E0 − 1]` → the **PRE_START digest** (full rows).
- Sealed `S_n`:
  - **T1** = `[s_n, B_n]`, full rows;
  - **T14** = the SNAP row `B_n + 1`, the **full row with all 8 columns**.
  - `s_1 = E0`, and `s_{n+1} = B_n + 2`: the SEAL and START rows are the first two rows of `S_{n+1}`'s T1, so both are
    covered as full rows by the next seal.
- The current (unsealed) segment is covered when it is sealed. Until then, the SEAL and START rows of its opening
  transition are protected by `seal_hash`, `segment_hash`, the fixed envelope check (V2) and the OAR (3.3.5).

**T14 at PREPARE:** because `B_n`, `sealed_at_utc`, the envelope constants and the `review_reports` content are fixed
in `Q`, the SNAP row is fully determined at PREPARE, and its T14 digest is part of `Q`.

**Membership tables T1–T14:** unchanged from P8.6F3, except T1 (range above) and T14 (full row).

| # | Table | Members of `S_n` | Order |
|---|---|---|---|
| T1 | journal | `s_n ≤ id ≤ B_n` | id |
| T2 | runs | M2 → `S_n`, terminal | slot_key |
| T3 | run_metadata | M3 → `S_n` | slot_key |
| T4–T6 | agent_decisions, setups, risk_decisions | slot_key of a T2 member | CJ bytes / slot_key |
| T7 | review_reports | **removed** (live view) | — |
| T8 | paper_orders | terminal event (E2) → `S_n` | order_id |
| T9 | paper_fills | `ORDER_FILLED` (E1) → `S_n` | fill_id |
| T10 | paper_positions | CLOSED and close event (E4) → `S_n` | position_id |
| T11 | closed_trades | `POSITION_CLOSED` (E3) → `S_n` | trade_id |
| T12 | notification_events | `journal_id` in T1 | event_id |
| T13 | macro_awareness | `first_run_id` (E6) of a T2 member | (event_id, symbol) |
| T14 | journal row `B_n + 1` | all 8 columns | — |

**Not protected (explicit):**
- paper_accounts, open positions and non-terminal orders (covered by the snapshot and the next seal's equality);
- live `review_reports`;
- system_state (verified by V1);
- ui_snapshots, symbol_locks, notification_deliveries.

#### 3.3.4 Records, proposal and chain (DAG unchanged; identifiers added)

- **`seal_core_n`:** as in P8.6F3, plus `B_n`, the full expected SNAP row (or its T14 digest), and the T1–T14
  digests.
- **`start_core_{n+1}`:** as in P8.6F3, plus `account_id` (3.11).
- **Proposal:** `Q = {version: 2, seal_core_n, start_core_{n+1}, state_fingerprint}` and
  `proposal_hash = H(PROPOSAL, CJ(Q))`.
- **Then, in order:** `P_n = seal_core_n + proposal_hash` → `seal_hash_n`; then `R_{n+1} = start_core_{n+1} +
  {proposal_hash, amendment_sha, previous_seal_hash, previous_segment_hash}` → `segment_hash_{n+1}`.
- There is no cycle; Q contains no id-dependent value that is unknown at PREPARE.

#### 3.3.5 Lifecycle PREPARE → SIGN → COMMIT → ANCHOR

1. **PREPARE** (read-only; EX = STOPPED with a valid H5; INTEGRITY = OK; `flock`):
   - read `B_n = MAX(journal.id)` and require `sqlite_sequence.seq = B_n`;
   - build Q, including the full SNAP row and all digests, and `proposal_hash`;
   - re-verify every earlier sealed digest with the code under PREPARE (I-H6). No write.
2. **SIGN:** a signed amendment commit containing `proposal_hash`.
3. **COMMIT** (`BEGIN IMMEDIATE`):
   - re-read `MAX(id)` and `seq`; both must equal `Q.B_n`;
   - rebuild Q and require byte equality;
   - verify the amendment signature;
   - require that ids `B_n + 1 … + 3` are unused;
   - insert SNAP, SEAL and START with explicit ids and the exact envelope values;
   - require one changed row per insert, and `seq = B_n + 3` afterwards;
   - COMMIT, or ROLLBACK and INCONSISTENT.
4. **ANCHOR:** the OAR (unchanged from P8.6F3): an SSH-signed record, two Owner-controlled locations outside the DB
   host and outside Git hosting, the `allowed_signers` trust root, and the `AI_FLOOR_SEGMENT_HASH` gate.

**Crash recovery:** as in P8.6F3, plus one row.

| Interrupted at | Observable state | Recovery |
|---|---|---|
| any step | `seq ≠ MAX(id)` | INCONSISTENT and STOP. Never "repaired" by resetting `seq` |

#### 3.3.6 Verification (V1–V6 as in P8.6F3; V2 and V4 extended)

- **V2:** for each transition, the rows `B_n + 1/2/3` exist with exactly the fixed envelope values above, and no other
  journal row has an id in `(B_n, B_n + 3]`. At verification time, `sqlite_sequence.seq` equals `MAX(journal.id)`.
- **V4:** T1 is recomputed over `[s_n, B_n]`, T14 over the full SNAP row, and the PRE_START digest over
  `[1, E0 − 1]`. Every journal id up to the last sealed `B_n + 1` belongs to exactly one digest; SEAL and START rows of
  sealed segments are inside the next segment's T1.

#### 3.3.7 Stability, invariants and negative tests

The digest-stability argument of P8.6F3 is unchanged: T14 is a journal row, and journal rows are never updated.

**Invariants**
- **I-H1…I-H6:** unchanged from P8.6F3.
- **I-H7:** at PREPARE and COMMIT, `sqlite_sequence('journal').seq = MAX(journal.id) = B_n`.
- **I-H8:** the journal ids up to the last sealed `B_n + 1` form a partition: PRE_START, then T1 and T14 of each sealed
  segment, with no gap and no overlap.
- **I-H9:** the SNAP, SEAL and START rows carry exactly the fixed envelope values.
- **I-H10:** after COMMIT, `seq = B_n + 3`.

**Negative tests (new)**
- **NH19–NH26:** tamper with one T14 column each (`id`, `timestamp`, `run_id`, `symbol`, `source`, `event_type`,
  `severity`, `payload`) → V4 FAIL.
- **NH27:** tamper with any envelope field of a SEAL row → V2 FAIL (and V4 once the next segment is sealed).
- **NH28:** the same for a START row.
- **NH29:** `seq > MAX(id)` (the top journal row deleted) → PREPARE refuses, INCONSISTENT.
- **NH30:** the `sqlite_sequence` row for `journal` is missing → INCONSISTENT.
- **NH31:** an explicit id already used → the insert fails, ROLLBACK, INCONSISTENT.
- **NH32:** a journal insert between PREPARE and COMMIT → `B_n` mismatch → refused.
- **NH33:** a row inserted into a sealed id range (e.g. into a gap id) → V4 count/digest FAIL.
- **NH34:** a non-adjacent SNAP/SEAL/START (id gap) → V2 FAIL.
- **NH35 (positive):** `seq` was raised legitimately by explicit-id inserts of earlier transitions; `B_n` is still
  consistent.

### 3.4 Transition preconditions (all required; otherwise STOP)

1. **Status:** FS = STARTED, EX = STOPPED, INTEGRITY = OK (2.3). The `flock` is held by the transition tool.
2. **Clean runs:** `recover()` completed; no run is in status `RUNNING`; no unexplained `ERROR` run since the last
   completed slot.
3. **Revision gate** CLEAR (1.6), including the section 4 validation.
4. **Fresh mark:** within the maximum age (3.6) at the proposal's `sealed_at`, or a flat book.
5. **Pending orders:** per proposed DEC-8.19 (zero by default).
6. **Signed lifecycle:** the PREPARE → SIGN → COMMIT → ANCHOR lifecycle of 3.3.5 is followed exactly; no step is
   skipped or merged.
7. **(P8.6F4) Journal sequence:** `sqlite_sequence('journal').seq = MAX(journal.id)` (3.3.3, I-H7).
8. **(P8.6F4) Continuity policy:** the boundary follows the continuity alternative chosen under DEC-8.20 (3.11).
   Under A2, no segment transition is used at all.

### 3.5 (P8.6F3, H-1) Complete history mapping, with closing-event attribution

**Ordering basis:** the monotonic `journal.id`; never timestamps (unchanged).

- **Anchor ids (P8.6F4):** `E0` is the unique `EXPERIMENT_STARTED`. For each transition, SNAP, SEAL and START are
  the explicit ids `B_n + 1/2/3` (3.3.3), and `s_{n+1} = B_n + 2`.
- **Two identities (P8.6R3):**
  - `origin_run_id` is the row's own `run_id` field, i.e. the run that **opened or created** the entity. It is
    preserved, never rewritten, and used for strategy attribution.
  - `event_segment` is the segment of the journal event that **created or terminated** the row. It is used for
    segment membership and economic attribution.

**Event links (verified in the code):** the economic journal events store the entity id in `journal.source`. Their
`journal.run_id` is the **originating** run, also for closes.

| Event | `source` | Written by |
|---|---|---|
| `ORDER_SUBMITTED` | `order_id` | `PaperBroker.submit_plan` |
| `ORDER_CANCELLED` / `ORDER_REJECTED` | `order_id` | `PaperBroker` |
| `ORDER_FILLED` | `fill_id` | `PaperBroker` |
| `POSITION_OPENED` | `position_id` | `PaperBroker` |
| `STOP_HIT` / `TARGET_HIT` (optional) and `POSITION_CLOSED` | `trade_id` | `TradeManager` |

#### Mapping rules (P8.6F4: the M table, restored and updated for the 3.3.3 identifiers)

`review_reports` is handled by 3.3.3 (T14) and is not mapped for integrity.

| Rule | Row | Category |
|---|---|---|
| M1 | journal row `j` | `j < E0` → PRE_START; sealed `S_n`: `s_n ≤ j ≤ B_n + 1` (T1 plus SNAP); `s_1 = E0`, `s_{n+1} = B_n + 2`; `j ≥ s_k` for the unsealed `S_k` → `S_k`; anything else → UNMAPPED |
| M2 | runs row `r` | the category of its unique `RUN_STARTED` journal row (`payload.slot_key = r.slot_key`); 0 or ≥ 2 → UNMAPPED |
| M3 | run_metadata `m` | the category of `runs(m.slot_key)`; no runs row → UNMAPPED. `experiment_id`: NULL for PRE_START; NULL or the S1 id for S1; equal to `segment_id` for `S_{n≥2}`; otherwise CONFLICT |
| M4 | runs row without run_metadata | keeps its M2 category, flagged `METADATA_MISSING_NONECONOMIC`, only if no economic row or economic event has its `run_id`; otherwise CONFLICT |
| M5 | agent_decisions, setups, risk_decisions (slot_key); macro_awareness (`first_run_id` via E6) | the category of that run; unknown → UNMAPPED |
| M6 | economic rows | **replaced by E1–E7 below** |
| M7 | notification_events | via `journal_id` (M1) |
| M8 | PRE_START rows | quarantined; counts and the PRE_START digest stored in the genesis; economic PRE_START rows of `paper-main` → CONFLICT |
| M9 | segment events | exactly one `EXPERIMENT_STARTED`; SNAP/SEAL/START triples with the 3.3.3 ids and envelopes; unique `segment_index`. Any duplicate, missing or misplaced segment event → CONFLICT |
| M10 | fail-closed | any UNMAPPED or CONFLICT → INTEGRITY = INCONSISTENT: not resumable, no transition, metrics refused, offending rows listed |

| Rule | Row | Mapping |
|---|---|---|
| E1 | paper_fills `f` | exactly one `ORDER_FILLED` with `source = f.fill_id` → its M1 segment |
| E2 | paper_orders `o` | **creation:** exactly one `ORDER_SUBMITTED` with `source = o.order_id`. **Terminal:** FILLED → the E1 event of the fill whose `order_id = o.order_id` (exactly one); CANCELLED / REJECTED → exactly one `ORDER_CANCELLED` / `ORDER_REJECTED` with `source = o.order_id`. A terminal order is a member of its terminal segment; a non-terminal order at seal goes to the closing snapshot |
| E3 | closed_trades `t` | exactly one `POSITION_CLOSED` with `source = t.trade_id` → `close_segment(t)`; at most one `STOP_HIT` / `TARGET_HIT` with the same source, in the same segment |
| E4 | paper_positions `p` | **open:** exactly one `POSITION_OPENED` with `source = p.position_id` → `open_segment(p)`. **Close:** if `status = CLOSED`, exactly one closed_trades row with `position_id = p.position_id`; `close_segment(p) = close_segment(that trade)`. A CLOSED position is a member of its close segment |
| E5 | carried entities | `open_segment(p) < close_segment(p)` → the trade and the position are `CARRIED_OVER` in the close segment. Realized PnL is attributed to the close segment's `CARRIED_OVER` line; the strategy decision stays attributed to `origin_run_id`. The sealed open segment is never rewritten: it holds the position only in `closing_snapshot`, at the sealed mark |
| E6 | every `run_id` lookup (economic rows, events, macro_awareness) | `runs.run_id` is not UNIQUE in the schema, so the verifier requires exactly one runs row per referenced `run_id`. 0 → UNMAPPED; ≥ 2 → CONFLICT. The event's `journal.run_id` must equal the entity's `run_id`, else CONFLICT |
| E7 | uniqueness and orphans | an entity with zero or ≥ 2 creation or closing events → CONFLICT. An economic event whose `source` matches no row (e.g. a `POSITION_CLOSED` without a closed trade) → CONFLICT. Event and row symbols must agree |

**Original economic records are preserved:**
- no rule rewrites, moves or deletes a row;
- attribution is computed by the verifier and the metrics tools from immutable events;
- the live tables keep their current runtime semantics.

#### Invariants

Unchanged:
- **I-M1:** every row of the mapped tables has exactly one category in {PRE_START, S_1..S_k}.
- **I-M2:** FS = STARTED ⇒ |EXPERIMENT_STARTED| = 1.
- **I-M3 (P8.6F4):** the SNAP, SEAL and START ids of each transition are the explicit values `B_n + 1`, `+ 2` and
  `+ 3`, with `B_n = MAX(journal.id) = sqlite_sequence.seq` at COMMIT (3.3.3).
- **I-M4:** for every runs row `r`, |RUN_STARTED(`r.slot_key`)| = 1.
- **I-M5:** for every `m` in run_metadata, `runs(m.slot_key)` exists, `cat(m) = cat(runs)` and the `experiment_id`
  rule (M3) holds.
- **I-M6:** for every `m` in `S_n`, (git_commit, fingerprint, starting_equity) = (`code_sha_n`, `fingerprint_n`,
  10000.0).
- **I-M8:** there are no PRE_START economic rows for `paper-main`.

New and changed:
- **I-M7 (strengthened):** every referenced `run_id` resolves to exactly one runs row.
- **I-M9:** every economic row has exactly one creation event; every terminal row has exactly one terminal event.
- **I-M10:** for every closed trade `t`, `close_segment(t)` is the M1 segment of its unique `POSITION_CLOSED`,
  independently of `t.run_id`.
- **I-M11:** for every position `p`, `open_segment(p) ≤ close_segment(p)`, and CARRIED_OVER ⇔ the inequality is
  strict.
- **I-M12:** no economic event is an orphan.

#### Negative tests

- **NM1–NM13:** unchanged.
- **NM14:** a trade with two `POSITION_CLOSED` events → INCONSISTENT.
- **NM15:** a `POSITION_CLOSED` whose trade row is missing → INCONSISTENT.
- **NM16:** two runs rows sharing one `run_id` → INCONSISTENT.
- **NM17:** a fill whose `ORDER_FILLED` `run_id` differs from the fill's → INCONSISTENT.
- **NM18 (positive):** a position opened in `S_1` and closed in `S_2` → the trade is a member of `S_2`, labelled
  CARRIED_OVER, with `origin_run_id` in `S_1`, and the `S_1` digest is unchanged.
- **NM19:** a CLOSED position with no closed trade → INCONSISTENT.
- **NM20 (positive):** a pending order from `S_1` filled in `S_2` → the order and fill are members of `S_2` and
  CARRIED_OVER (only under the DEC-8.19 CARRY alternative).
- **NM21:** a STOP_HIT in a different segment from its POSITION_CLOSED → INCONSISTENT.

### 3.6 (M-7) Equity at seal and maximum mark age

- **Persisted state only:** the seal uses only what is already persisted. It never fetches a price and never invents
  a mark.
- **`closing_equity`:** cash + Σ unrealized at each open position's `last_price`. It must equal the stored account
  equity exactly; otherwise INCONSISTENT.
- **Mark age** per open position: `sealed_at_utc − (last_processed_at + 5 min)`, the end of the bar that produced the
  mark.
  - **Proposed maximum: 20 minutes**, i.e. one 15-minute cadence plus one 5m bar. It requires sealing right after a
    completed in-session cycle.
- **Mark older than the maximum:** the transition is BLOCKED. There is no stale-mark override by default (proposed
  DEC-8.19). Outside sessions (weekend) this means a transition needs a flat book, or must wait until the first
  in-session cycle has run and the runner has stopped again.
- **Flat book:** the mark age does not apply; `closing_equity` = cash.
- **(P8.6F3, P8.6R2 M-7) Recorded fields and tests:**
  - the seal records, for every open position, `last_processed_at`, the bar end used as mark time, `last_price`, and
    the computed age;
  - tests cover an age exactly at 20 minutes (accepted), 20 minutes + 1 s (blocked), a mark spanning a DST change in
    London and in New York (UTC arithmetic only), and a non-UTC input timestamp (rejected).


### 3.7 New baseline, freeze compatibility and the 14-day counter

- **Every segment after S1 is governed by a signed freeze amendment** (proposed file:
  `EXPERIMENT_FREEZE_AMENDMENT_S<n>.md`). It records:
  - the defect that justifies it (HIGH-8.1), the code SHA and the fingerprint per scope;
  - the unchanged economics table (strategy, risk, prompts, providers, costs, fills);
  - the treatment of open positions and pending orders;
  - the anchor hashes (3.3);
  - the Owner's signature and date.
- **Unchanged:** `EXPERIMENT_FREEZE.md` and its baseline `f5032ba…`.
- **Code impact (to implement later; not in this design):**
  - the hard-coded `EXPERIMENT_BASELINE_SHA` in `runtime/cloud_runner.py` becomes chain-aware: S1 keeps `f5032ba…`;
    later segments take the baseline from their amendment;
  - the original `experiment_*` keys stay immutable and are verified against the genesis.
- **14-day counter (proposed DEC-8.20):**
  - each segment has its own counter, starting at its `started_at_utc` (the first durable claim after the
    transition) and ending at +14 d;
  - days of different segments are **never added together**;
  - a segment shorter than 14 days is reported as `INCOMPLETE` and is not a valid experiment result;
  - an emergency rollback (3.8) ends the current segment as `INCOMPLETE`.

### 3.8 (P8.6F3, M-5) Emergency halt: control-plane procedure

**Code facts (baseline):**
- `cloud_runner.main` raises **before opening the DB** unless `RENDER=true` and `AI_FLOOR_CLOUD_RUNNER=1`.
- The SIGTERM/SIGINT handler only sets `stop`. The current `tick()` processes the enabled symbols sequentially to
  completion, then `daily_summary` runs, then the loop exits and `DemoRunner.close()` writes `runner=STOPPED`.
- Each economic save is one atomic CAS transaction.
- A kill mid-cycle leaves the run `RUNNING` with its symbol lock. The next authorized start recovers it
  (`recovery_stale_after_seconds=0` → `FAILED` / `interrupted_run`).
- **Not in the code:** the hosting supervisor's restart behaviour. The procedure does not rely on it.

**Procedure H1–H7** (Owner and operator; no DB writes):

| Step | Action | Evidence recorded |
|---|---|---|
| H1 Disable restart authority **first** | Set `AI_FLOOR_CLOUD_RUNNER=0` and `AI_FLOOR_SCHEDULER=0` in the service environment. Any restart, by the supervisor or by an environment-triggered redeploy, then exits in `main()` before opening the DB | env values, time |
| H2 Control-plane stop | Suspend the service, or scale it to zero instances, through the hosting control plane | action id, time, status shown |
| H3 In-flight tick | Allow the graceful stop: the current tick finishes all its symbols (atomic saves). If the platform force-kills it, accept the `RUNNING` run; it is recovered only at the next **authorized** start. **No manual DB repair** | last journal id and run statuses at stop, from a later copy |
| H4 Termination verification (all required) | (a) the control plane shows the service suspended or at 0 instances. (b) Two observations ≥ 2 cadences (30 min) apart show no new heartbeat, `last_run`, runs or journal rows. (c) Where the disk can be reached, a one-shot lock probe takes `LOCK_EX \| LOCK_NB` on `<db>.runner.lock` and releases it at once; success means no runner holds it. This is a future read-only tool; if it is unavailable, the attestation states so. (d) No run started after the H2 time | observations with timestamps |
| H5 Durable execution attestation | A CJ document `{signer key fingerprint, attestation_time_utc, scope {service, environment, DB path, copy SHA if any}, H1–H4 results with times, "restart authority disabled; no runner process", freshness rules}`, signed with `ssh-keygen -Y sign -n v2-execution-attestation` and retained like an OAR (two Owner-controlled locations) | the signature file |
| H6 Pending orders and open positions | Untouched. Their ids are listed in the H5 record. No cancel, close, modify, re-price or manual price fetch | ids, counts |
| H7 Fail-closed restart policy | A restart is allowed only when **all** hold: a signed Owner restart decision; a fresh copy with FS = STARTED, INTEGRITY = OK and EX = STOPPED under a valid H5; preflight READY, including `segment_anchor_matches`; the revision gate per policy; the trigger cause resolved and documented. Then resume the service with `AI_FLOOR_CLOUD_RUNNER=0` (it exits) and set `AI_FLOOR_CLOUD_RUNNER=1` **last**. If any item is missing, stay halted. **There is no automatic restart path** | restart decision, preflight output |

**Economic effects of a halt (disclosed, not corrected):**
- A pending order fills at the open of the next processed eligible bar after the restart, which can be much later
  (freeze rule). With the flag ON, skipped bars are journaled `PENDING_NOT_EVALUATED`.
- In-scope positions catch up chronologically from their watermark. Legacy-path positions evaluate only the newest bar,
  which extends HIGH-8.1 exposure.
- The halt duration is reported in the next seal.

**Operator actions after a halt** (unchanged from P8.6F2, with H7 as the only restart path). In every case, the
operator may not bypass a gate, edit the DB or insert journal rows.

| Situation | Permitted actions | Additional prohibition |
|---|---|---|
| Runner halted | Stay halted (the default); resume the same segment through H7 if the trigger is outside the economic path; or a ROLLBACK transition (3.3.5) | Restarting with another configuration without a transition |
| Pending orders exist | Stay halted; resume through H7 so the orders progress; or a new signed decision selecting the CARRY alternative (DEC-8.19) | Cancelling or modifying orders |
| Revision gate blocked | Owner `record_review`; stay halted until CLEAR | Manual journal rows; changing evidence |
| Equity marks stale | Stay halted; resume through H7 only if the trigger is outside the management path; otherwise escalate to a new Owner decision | Fetching or editing prices; sealing with a stale mark |
| Rollback preconditions fail | Stay halted; the report names each failing precondition | Waiving a precondition |

**Halt triggers** (proposed, DEC-8.17): unchanged from P8.6F2.

**(P8.6F4, P8.6R4 M-5) Exactly what PAPER activity can occur after the halt is requested.** Let `T_h` be the moment
SIGTERM reaches the process.

| Where the process is at `T_h` | Activity after `T_h` (current code) |
|---|---|
| Sleeping between ticks (`sleep 1 s` steps) | None economic. The loop exits within ≤ 1 s, then `runner.close()` writes `runner=STOPPED` |
| In `daily_summary` | Completes the summary write and notification attempt (non-economic), then exits |
| In `tick()`, slot already claimed for every symbol (`DUPLICATE` claims) | Heartbeat and `DUPLICATE` returns only; non-economic |
| In `tick()`, inside the cycle of symbol `k` of a new slot | **Economic.** The rest of cycle `k` (position management and closes; floor and AI calls; pending progression and fills; a possible new order) **and the full cycles of every later symbol in `enabled_symbols` for that slot**, then `daily_summary`, then exit. Worst case: XAUUSD is interrupted at the start, so the whole XAUUSD and EURUSD cycles run, including AI time |
| A force-kill (SIGKILL) after a platform grace period, mid-cycle | Activity up to the kill. Each economic save is atomic (all-or-nothing); the run stays `RUNNING` and is recovered at the next authorized start |

**Conclusion.** With the current runner, the guarantee "no new PAPER operation starts after the authorized stop point"
**does not hold**. M-5 stays **OPEN (partial mitigation)**.

**Operational mitigation (current code; partial, depends on operator timing):** request the halt only when every
enabled symbol of the current slot has `RUN_COMPLETED` (or another terminal status) and the next slot boundary is
more than 2 minutes away. In that window, ticks only make `DUPLICATE` claims, so no economic activity follows `T_h`.
This is a procedure, not a guarantee, and it does not cover an emergency at an arbitrary time.

**(P8.6F6) Future runner requirements R-HALT, with linearization** (documented only; **not implemented**).
- Introduced only in a new baseline; never hot-patched into a frozen period.
- No economic rule changes.
- This **replaces** the P8.6F5 CP text, which had a check-then-act window (P8.6R6 MEDIUM).

**Three distinct instants** (never interchanged):

| Symbol | Meaning |
|---|---|
| `T_sig` | The OS delivers SIGTERM / SIGINT to the process. Python defers the handler until the main thread is between bytecodes. A running C call, e.g. an SQLite statement up to its 10 s busy timeout, finishes first |
| **`T_h`** | **Barrier instant:** the handler executes the single atomic store `gate.requested_at = (monotonic_ns, utc)`. This is the halt **request** as seen by the process, and the only reference instant for the economic prohibition |
| `T_ack` | **Durable acceptance:** the commit of the non-economic halt bookkeeping (`HALT_OBSERVED`, run `HALTED`). Always `T_h < T_ack` |

**Economic gate and linearization point**
- **The gate object:** every economic write goes through `gate.admit(kind)`, called on the main thread **immediately
  before the write's state load**. Its first statement is a single atomic read `r = gate.requested_at` (a reference
  read in CPython).
- **The linearization point `L(W)`** is that read:
  - `r is None` → the write is **admitted**: it runs load → compute → `BEGIN IMMEDIATE` → commit or rollback to
    completion, and is never interrupted by the handler;
  - otherwise → **refused**, and the run proceeds to the bookkeeping.
- **The start of an economic write is defined as `L(W)`.** "Start of apply" is no longer used. There is therefore no
  window between the check and the act: every effect after `L(W)` belongs to an admitted write.
- **Ordering:** handler and main thread share one thread, so the store at `T_h` and every read are totally ordered.
  W is admitted ⇔ `L(W)` precedes `T_h`.
- **The handler only assigns.** No lock, no I/O, no DB access, no exception. SIGINT must use the same custom handler
  (no `KeyboardInterrupt`).
- **No deadlocks:**
  - `admit` takes no lock and holds no SQLite lock;
  - the read happens before `BEGIN IMMEDIATE`;
  - the handler never waits.
- **SQLite consistency:** an admitted write's transaction commits or rolls back atomically as today. A signal arriving
  mid-transaction only sets the flag.
- **Threads:** economic writes are asserted to be on the main thread. AI time-budget threads, if any, never write
  economics.
- **Retries:** the second attempt of a STALE guarded write is a **new** write, with a new `L(W)`.

**Coverage of operations** (each line is an `admit` call site, at the start of a write, before its load):

| # | Operation | Admit point |
|---|---|---|
| 1 | Legacy position management | Entry of the guarded `TradeManager` write. Closes on that bar are inside the write |
| 2 | Catch-up | **Per bar**, before each `_load` in `catch_up_position` (a gate passed in). A refusal stops the loop at the last committed bar |
| 3 | Pending orders and fills | Entry of the legacy guarded `progress_pending` write, or of `gate_pending_orders` |
| 4 | Submission | Entry of the submit guarded write |
| 5 | Closes | Occur only inside management or catch-up writes (rows 1–2) |
| 6 | Sequential symbols | Before each `run_cycle` claim in `Scheduler.tick`, a non-economic check of `requested_at`: no new claim once it is set |
| 7 | SQLite transactions | Never interrupted; only admitted writes open them |
| 8 | Signals and restarts | SIGTERM / SIGINT → `T_h`. SIGKILL → no bookkeeping, and recovery as below. A restart starts a new process with `requested_at = None`, but restart authority is disabled first (H1), so `main()` exits before any DB access |

**Operations already started, and operations waiting to start**
- **Admitted before `T_h`:** completes; at most one, because processing is sequential (I-R9).
- **Waiting** (any later `admit`): refused.
- **Non-economic stages** (AI calls, floor computation) may still run until the next admit point. Their results are
  never written economically.

**Halt bookkeeping** (authorized non-economic writes, after the last admitted write):
- **One transaction:** the current run's `runs` row (`status = COMPLETED`, `final_status = HALTED`, `completed_at`)
  plus the journal `HALT_OBSERVED {t_h_utc, t_h_monotonic, refused_kind, last_admitted_write_seq}`.
- **Then** `system_state.runner = STOPPED`.
- **Nothing else.** **(Superseded by the P8.6F7 post-halt persistence policy below: the allowlist is H plus P, i.e. this transaction plus `heartbeat`, `scheduler` and `runner`.)**

**Recovery when the process dies before `T_ack`:**
- the next authorized start's `recover()` is unchanged (`FAILED / interrupted_run`, symbol locks released,
  `RECOVERY` events), and is non-economic;
- the verifier classifies `HALT_INTERRUPTED` from an external H5 / HALT note `T_h` and the REX admit sequence;
- nothing is written for that classification.

**Proof obligation:**
- every admitted write records `L(W)` (a monotonic sequence and a time) in its REX entry;
- `HALT_OBSERVED` records `T_h`;
- the verifier checks `L(W) < T_h` for every write of that process.

**Race tests** (on the future implementation; POSIX CI):
- deterministic fault injection sets `requested_at` (a) just before `L(W)`, (b) just after `L(W)` before the load,
  (c) between load and `BEGIN`, (d) inside the transaction, (e) after the commit before the next admit, (f) between
  catch-up bars, (g) between symbols;
- plus real `os.kill(getpid(), SIGTERM)` tests at those points, and a randomized property test.

**M-5 stays OPEN** until this is implemented and independently tested.

**(P8.6F7, P8.6R7) Physical effects after the signal: what is and is not guaranteed**

The admission boundary (`L(W)` versus `T_h`) is an **ordering of admissions**. It is **not** a guarantee of "no
PAPER effect after the stop request".

| Interval | What can still happen (current design) |
|---|---|
| `T_sig` → `T_h` | Anything the process does until the handler runs, bounded by the longest uninterruptible C call: at most the SQLite busy timeout (10 s), plus statement time. Admissions in this interval are normal admissions |
| `T_h` → in-flight completion | At most one write admitted before `T_h` (I-R9) may load, compute, `BEGIN`, and **commit after `T_h`** |
| After the in-flight write and the bookkeeping (`T_ack`) | No economic effect |

**Stop policy (P8.6F8, P8.6R8 MEDIUM; Owner decision PENDING, DEC-8.17b)**

**Code fact:** `save_paper` opens `BEGIN IMMEDIATE` internally. Any caller-side check precedes `BEGIN` by some Python
frames, so the handler can run between them.

**The P8.6F7 claims are withdrawn:** "at most the one transaction already open at `T_h` may commit" and "no economic
write opens a transaction after `T_h`, except the single one already past `pre_begin`" (historical P8.6F7 terminology: "Policy B" and "pre-begin" are superseded by Alternative 1 / 2 and "second check before invoking `save_paper`"). A write whose last caller-side
check preceded `T_h` may **open** its transaction **after** `T_h` and commit.

| Aspect | **Alternative 1: admission contract (caller-side reads)** | **Alternative 2: in-transaction barrier** |
|---|---|---|
| Mechanism | Read 1 at `L(W)` (admission, before the load). **Read 2 (`pre_save`)** immediately before the `save_paper(...)` call. If the flag is set at read 2, skip the call: no transaction, no effect | A flag read **inside** the transaction, right after `BEGIN IMMEDIATE` and before the first economic statement. If set → `ROLLBACK`, no effect |
| Exact guarantee | No economic write is admitted (read 1) or calls `save_paper` (read 2) after `T_h`. **Residual:** at most **one** write whose read 2 preceded `T_h`. It may execute `BEGIN IMMEDIATE` after `T_h` (the window is the Python frames between read 2 and `BEGIN`, plus any SQLite lock wait, ≤ 10 s busy timeout), then commit after `T_h` | No transaction whose in-transaction read observed the halt commits. **Residual:** at most one transaction whose in-transaction read preceded `T_h`; it commits after `T_h`. A commit is never interrupted. **No alternative gives zero effect after `T_h`** |
| Dependency | Callers only (service guarded writes, catch-up per bar, pending gate, submit). **No change to `storage/database.py`** | Requires changing `Store.transaction()` / `save_paper` in the **hash-pinned `storage/database.py`** (or a `Store` subclass with equivalent behavioural risk). Needs the **additional explicit authorization** to modify the pinned module, plus its own review |
| Residual size | One write: the frames between read 2 and `BEGIN`, plus its transaction | One transaction from just after `BEGIN` |
| Evidence to certify | REX per write: `L(W)` and the read 2 sequence and time, plus the commit time; `HALT_OBSERVED`: `T_h`. The verifier checks `read2 < T_h` for every committed EW, and that at most one committed EW has commit > `T_h` | Plus the in-transaction read time; the verifier checks `in_tx_read < T_h` for every committed EW |

**Recommendation:** **Alternative 1.** It is the simplest, keeps PAPER integrity (every committed write is atomic and
evidenced) and needs no pinned-module change. Alternative 2 only shrinks the residual from "a few frames plus the
transaction" to "the transaction", at the cost of modifying `storage/database.py`.

**Owner acceptance conditions for Alternative 1** (DEC-8.17b):
- (a) the residual is at most one economic write per process after `T_h`;
- (b) its effect is atomic, evidenced in REX, and listed in the halt report;
- (c) an operational bound: the halt is complete when `T_stop` (that write's commit or rollback) is recorded, and
  `T_ack ≥ T_stop`.

**Accurate wording (both alternatives):** *after `T_h`, no new economic write is admitted. At most one economic write
whose second check before invoking `save_paper` preceded `T_h` may begin its transaction and commit after `T_h`.
After `T_stop`, nothing
economic happens.* The design never claims zero economic effect after `T_sig` or `T_h`.

**Post-halt persistence policy (P8.6R7 MEDIUM; aligned with every real persistence site, 0 grounding)**
- **Mechanism:** every persistence call site in `run_cycle`, `Scheduler.tick` and the close path is guarded by
  `gate.allow(category)` (the same single atomic read of `requested_at`). After `T_h` the rule below applies.
- **Admitted before `T_h`:** a persistence call whose own read preceded `T_h` completes (it is the same admission rule
  as for economic writes; non-economic ones are harmless but are classified as "admitted before `T_h`").

| Category | Persistence sites | After `T_h` |
|---|---|---|
| E. Economic | management guarded write; catch-up per-bar save; `progress_pending` / `gate_pending_orders`; submit; account creation in `__init__` (impossible in sealed mode after OAR-G, R-GEN-1; without sealed mode, any creation after `E0` → `CERTIFICATION_INVALID`, GEN-3) | **Refused** for new writes: admission check at `L(W)`; under Alternative 1, also the **second check before invoking `save_paper`**; under Alternative 2, the in-transaction check. A write admitted before `T_h` may still begin its transaction and commit after `T_h` (the accepted residual) |
| D. Decision records | `save_reports` (runs final status, agent_decisions, setups, risk_decisions, review_reports), `record_execution`, `record_analysis_events`, `record_macro_awareness` | **Suppressed.** In-memory results (e.g. AI calls that finish) are discarded, not persisted |
| O. Observability | `store.event(...)` events (market_data, macro / AI `PROVIDER_FAILURE`, execution gate, policy, market_evidence, `EVIDENCE_REVISION`, AI health, `_record_ai_calls`), `set_state` of provider keys, `save_snapshot`, evidence-store ingestion (separate DB), the health hooks (no-op) | **Suppressed** |
| L. Run lifecycle | `claim_slot` (a new run); `save_run_metadata`; `finish` | `claim_slot` **refused** (no new run). `finish` is **replaced** by the halt bookkeeping |
| H. Halt bookkeeping | one transaction: the current run's `runs` status fields (`COMPLETED`, `final_status = HALTED`, `completed_at`) plus one `HALT_OBSERVED` journal row | **Allowed** (after `T_stop`, under either alternative) |
| P. Process shutdown | `OperationalRuntime.close()` → `store.heartbeat(clock, "STOPPED")` (writes `heartbeat` and `scheduler`); `DemoRunner.close()` → `runner = STOPPED` | **Allowed**, exactly these three `system_state` keys |
| S. Summaries and notifications | `daily_summary` (summary rows, notification events and deliveries) | **Suppressed** |

- **Resolution of "AI may finish" vs "nothing persisted":** AI calls in flight may finish **in memory**. Their outputs
  reach no table, because every D and O persistence site is suppressed after `T_h`.
- **No economic write can be bookkeeping:** the H and P allowlist touches only `runs` (status fields), `journal`
  (`event_type = HALT_OBSERVED` only) and `system_state` (`heartbeat`, `scheduler`, `runner`). None of these is an
  economic table (EDG).
- **Supersession:** this supersedes the P8.6F6 "Halt bookkeeping" list, which omitted `heartbeat` and `scheduler`.
- **(P4a implementation, Owner decisions 2026-10-09) Post-halt allowlist as implemented: H, P and a RESTRICTED R.**
  - **D-1:** the H transaction also deletes the `symbol_locks` row of the halted run only (same `slot_key` and
    symbol), in the same transaction; a documented exception to the original I-R1c list. A failed H transaction
    commits nothing and the halt is not confirmed (`HALT_UNCONFIRMED`).
  - **R (conditioned ratification of DEC-8.17b (b)):** after `T_h` only REX evidence of the halted run of this process:
    one `REX_WRITE` per admitted write whose read 2 preceded `T_h`, and one `REX_RUN` of the run in flight at `T_h`.
    Never `REX_FAILURE`, another run, another process, a new run or a duplicate (`HaltGate.allow_evidence` at write
    time; the verifier flags `POST_HALT_WRITE` / `POST_HALT_EVIDENCE_NOT_AUTHORIZED`). R never authorizes an economic
    write.
  - **STARTUP (MEDIUM-1):** every process-start write (schema creation, `recover()`, the startup `system_state` keys,
    the preflight write probe, `EXPERIMENT_STARTED`, notification capture) is a STARTUP site, refused after `T_h`; the
    constructor stops without writing. On POSIX the halt signals are deferred across each check + write, so a signal
    never lands between them (its handler, i.e. `T_h`, runs right after the write). A halt during start records no
    `HALT_OBSERVED`; H1 and H5 remain the barrier for that case.

**Recovery (P8.6F8):**
- A later authorized start may write only `recover()`'s non-economic rows.
- Account creation after `E0` is **never** a legitimate recovery: it is `CERTIFICATION_INVALID` (5.1.3.2 GEN-3 /
  GEN-4). The P8.6F7 "evidenced startup reconciliation" is withdrawn.

**Halt invariants (P8.6F7; replace the P8.6F6 versions)**
- **I-R1a (operator):** H1–H7 perform no DB write.
- **I-R1b (economic admission, P8.6F8):** no economic write is **admitted** after `T_h` (`L(W) < T_h` for every
  committed EW of that process). Under Alternative 1, additionally `read2 < T_h` for every committed EW. At most one
  committed EW has its `BEGIN` and commit after `T_h`.
- **I-R1c (post-`T_h` allowlist):** after `T_h`, the runtime's writes are only:
  - (i) writes admitted before `T_h`;
  - (ii) the H bookkeeping transaction (`runs` status fields of the current run, plus one `HALT_OBSERVED` row);
  - (iii) the P shutdown keys (`heartbeat`, `scheduler`, `runner`).

  No D, O, S or L write except these.
- **I-R1d (recovery, P8.6F8):** a later process writes only `recover()`'s non-economic rows. Any account creation
  after `E0` → `CERTIFICATION_INVALID` (GEN-3).
- **No-zero-effect statement:** the invariants do **not** assert "no economic effect after `T_sig` / `T_h`"; the
  residual is at most one in-flight write (Alternative 1 or 2 of the P8.6F8 stop policy).
- **I-R7:** = I-R1b.
- **I-R9:** at most one admitted economic write is unfinished at `T_h`.
- **I-R10:** after a halt, the catch-up watermark equals the last committed bar.
- **I-R11:** the handler performs no I/O, takes no lock and raises nothing.
- **I-R12:** every STALE retry has its own admission.
- **I-R13 (P8.6F8):** `T_ack ≥ T_stop`. Alternative 1: every committed EW has `read2 < T_h`, and at most one has a
  commit > `T_h`. Alternative 2: every committed EW has `in_tx_read < T_h`.
- **I-R14:** every persistence site in the cycle and close path is classified (E / D / O / L / H / P / S) and guarded.
  An unclassified site is a design defect.

**Negative tests (P8.6F6)**
- **NR11** (current code): a halt during the XAUUSD cycle → EURUSD still runs (documents the gap).
- **NR12:** a halt before the submit admit → no order; run `HALTED`.
- **NR13:** a halt between symbols → no EURUSD claim.
- **NR14:** a halt inside a transaction → the write commits; bookkeeping after it; `L(W) < T_h`.
- **NR15:** a halt between catch-up bars → no further bar.
- **NR16:** a halt before the management admit → no management write.
- **NR17:** a halt right after the claim → `HALTED`, no economic write.
- **NR18:** a kill before `T_ack` → `recover()` marks `FAILED`; the verifier says `HALT_INTERRUPTED` only with a
  matching external `T_h`.
- **NR19:** randomized signal timing → I-R1b, I-R9 and I-R12 hold.
- **NR20:** injection (b), just after `L(W)` → the write completes and is classified as admitted before `T_h`. The
  former race is no longer a violation, by definition.
- **NR21:** the handler attempts DB access → test failure (I-R11).
- **NR22:** a STALE retry after `T_h` → refused.
- **NR23:** a post-`T_h` write to any row outside the I-R1c list → FAIL.

**Negative tests (P8.6F7)**
- **NR24:** a signal right after `L(W)` with admission only (P8.6F7 Policy A, superseded by Alternative 1) → the write commits after `T_h`; reported as the accepted
  residual, **not** as "no effect".
- **NR25 (P8.6F8):** Alternative 1: a signal before read 2 → no `save_paper` call, no DB change. A signal **between
  read 2 and `BEGIN`** → the transaction **opens after `T_h`** and commits; it is reported as the single accepted
  residual. `T_ack ≥ T_stop`.
- **NR30:** two economic writes committing after `T_h` → FAIL (I-R13).
- **NR31:** Alternative 2 (only if authorized): a signal between `BEGIN` and the in-transaction read → ROLLBACK, no
  effect.
- **NR26:** the signal during the AI stage → AI finishes in memory; no `save_reports`, analysis events, AI call audit
  or provider-state write after `T_h`.
- **NR27:** a post-`T_h` write outside the I-R1c allowlist (e.g. `save_snapshot`, a `DATA_CHECK` event,
  `daily_summary`) → FAIL.
- **NR28:** the close path writes exactly `heartbeat`, `scheduler` and `runner`, and nothing else.
- **NR29:** a new persistence site added without a category → a static check fails (I-R14).

**Invariants**
- **I-R1:** **superseded by I-R1a–I-R1d (P8.6F6, above).** The unqualified wording "a halt changes no DB row" is withdrawn.
- **I-R2:** no transition commits while any 3.4 precondition is false.
- **I-R3:** a resume needs `segment_anchor_matches` and a READY preflight.
- **I-R4:** with `AI_FLOOR_CLOUD_RUNNER ≠ 1`, `cloud_runner.main` exits before any DB access (testable on the existing
  code).
- **I-R5:** an attestation older than 60 minutes before the copy, or predating any activity in the copy → EX =
  UNKNOWN.
- **I-R6:** no step writes `runner`, `heartbeat` or run status by hand.

**Negative tests**
- **NR1–NR5:** unchanged.
- **NR6:** start `cloud_runner.main` with `AI_FLOOR_CLOUD_RUNNER=0` → it raises before the DB is opened (no file
  created or touched).
- **NR7:** SIGTERM during a tick → the tick completes; no partial economic state; `runner=STOPPED`.
- **NR8:** a kill mid-cycle → the run stays `RUNNING`, its economic transaction is all-or-nothing, and the next
  authorized start marks it `FAILED` / `interrupted_run`.
- **NR9:** an attestation with a heartbeat after its time → UNKNOWN.
- **NR10:** an attestation signed by a key outside `allowed_signers` → UNKNOWN.

### 3.9 Open positions and pending orders at a boundary

- **Open positions** (proposed DEC-8.19, option CARRY):
  - carried unchanged, with their watermark;
  - `CARRIED_OVER` in the new segment. Their realized PnL is reported on a separate line and excluded from the new
    segment's strategy metrics (win rate, R multiple, hit rate);
  - their mark-to-market change belongs to the segment in which it occurs.
  - **Alternative FLAT:** wait until positions close naturally; never force a close.
- **Pending orders:** zero at a transition (default proposal). Otherwise the transition waits; orders are never
  auto-cancelled (there is no TIF, MEDIUM-8.2). The alternative is CARRY with the same `CARRIED_OVER` labelling.

### 3.10 Results separation

- **Segment metrics** are computed only from that segment's history (`run_metadata.experiment_id`, the time window
  and the digest scope).
- **Equity:** the curve is continuous; each segment reports `closing_equity − opening_equity` broken down into new
  trades, `CARRIED_OVER` and `HIGH_8_1_AFFECTED`.

---

### 3.11 (P8.6F4, P8.6R4 MEDIUM) Freeze and continuity: a flat new period versus inherited continuity

The freeze fixes, among other things, **initial equity 10000 USD**, account `paper-main` and the economic model, and
requires a new baseline and period when results or economic decisions change. Activating catch-up changes execution,
so a new baseline is required in every alternative.

| Aspect | **A1. New period, same account, flat** | **A2. New period, new PAPER account at 10000, flat (new trading DB)** | **B. Continuity: inherited equity and positions, separate attribution** |
|---|---|---|---|
| Precondition | `paper-main` has no open position and no pending order | The **old** account is flat (positions are never abandoned); then a new trading DB with a fresh account at 10000 | None beyond 3.4 |
| Opening equity | The current realized equity (≠ 10000 in general) | **10000**, as frozen | The carried, marked equity (≠ 10000) |
| Freeze conformity | Initial equity deviates; needs an amendment of the equity parameter | **Matches** the frozen economic table; amendment only for code, fingerprint, scope and DB path | Initial equity and carried positions deviate; needs an amendment |
| Certification | Clean trade history; equity base differs from the frozen one | **Cleanest**: all economics from 10000 under one SHA and fingerprint | Carried trades excluded (`CARRIED_OVER`); account-level metrics mix pre-segment marks |
| 14-day period | Per segment, from its start | Standard mechanism in the new DB: `start_experiment_if_unstarted` at its first start (FS = NOT_STARTED there) | Per segment; the segment's result window excludes carried economics |
| Catch-up closes | None inherited | None inherited | A carried position is processed from its watermark. Bars between the watermark and the segment start are processed **in the new segment**, so a close can occur on a bar earlier than `started_at`. Such closes are flagged `PRE_SEGMENT_BAR_CLOSE`, attributed to `CARRIED_OVER`, and excluded from strategy metrics and the 14-day result window; they stay in account equity |
| History | Same DB; needs the segment chain (3.3) | The old DB is untouched, archived read-only; its SHA-256 and final state are anchored with an OAR. **No segment chain or cross-segment mapping needed** | Same DB; needs the full segment chain and the E-rules |
| Code facts | — | `load_paper` is not account-scoped for orders, fills and trades, so a second account in the **same** DB would mix them. **A2 therefore requires a new DB file**, not a new account id in the same DB | — |
| Reaching flat | Needs a natural flat moment | Needs a natural flat moment in the old DB | Not needed |
| Implementation surface | Segments (3.3, 3.5) | The smallest: archive plus OAR; the segment machinery becomes optional | The largest |

**Reaching a flat account without code changes:**
- The runtime cannot run "manage-only, no new entries" today. A dry run (`paper_enabled=False`) also stops position
  management, so it is not usable.
- **Opportunistic flat halt:** watch Owner-made copies (read-only); when the account is flat after a slot completed,
  apply the 3.8 halt in the safe window; re-check flatness on a post-halt copy with a valid H5.
- If it is not flat (a new order happened), resume through H7 and retry later.
- A bounded wait (proposed: 10 counted session days) after which the Owner decides.
- A "wind-down mode" (manage only, no new entries) would be a future runtime change (R-WIND-1, documented only).

**Recommendation (DEC-8.19 and DEC-8.20 remain PENDING):**
- **A2.** It is the only alternative that keeps the frozen economic table (equity 10000, one account, one
  configuration) for the certified period, needs no carried attribution, and removes the need for the segment
  machinery and its H-2 surface.
- B is acceptable only for `TECHNICAL_ONLY` segments, or if the Owner explicitly prefers continuity and accepts the
  `PRE_SEGMENT_BAR_CLOSE` and `CARRIED_OVER` exclusions.
- A1 has no advantage over A2 except keeping one DB file.

**Invariants**
- **I-C1:** A2 ⇒ the old DB's SHA-256 at archive equals the OAR value, and the old DB is never opened for writing
  again.
- **I-C2:** A2 ⇒ the new DB's account starts at 10000 with zero positions and orders.
- **I-C3:** B ⇒ every close of a carried position on a bar before `started_at` is flagged `PRE_SEGMENT_BAR_CLOSE`.
- **I-C4:** a transition or archive is never performed on a non-flat account under A1 or A2.

**Negative tests**
- **NC1:** an A2 archive attempt with an open position → refused.
- **NC2:** the archived DB modified after the OAR → hash mismatch.
- **NC3:** B with a carried close on a pre-start bar without the flag → FAIL.
- **NC4:** A2 with a second account in the same DB → refused (code-fact check).

#### 3.11.1 (P8.6F5, P8.6R5 MEDIUM) A2 contract: new period identity, archive and anchor

A2 remains the **preferred alternative for evaluation**, and is **NO-GO**. Nothing below is created, archived or
modified now.

**1. Period identity and baseline.** A2 follows the existing two-commit pattern (`EXPERIMENT_BASELINE_SHA` =
`f5032ba…` as the code baseline; the freeze commit = `AI_FLOOR_GIT_COMMIT`).
- **Code baseline commit `X_P2`:** contains every approved code change (catch-up scope, REX, R-HALT, and the
  preflight checks below).
- **Freeze-amendment commit `Y_P2`:** its parent is `X_P2`. Its diff from `X_P2` touches **only** two things: (a)
  `runtime/cloud_runner.py`, where `EXPERIMENT_BASELINE_SHA = X_P2`; (b) the new document `EXPERIMENT_FREEZE_P2.md`.
  This is verifiable with `git diff --stat X_P2 Y_P2`. The commit is signed (DEC-8.20).
- **Deployment:** `Y_P2`, with `AI_FLOOR_GIT_COMMIT = Y_P2`.
- **Recording:** in the **new** DB, `start_experiment_if_unstarted` records baseline `X_P2` and freeze `Y_P2` at the
  first start (FS = NOT_STARTED there).
- **Period identity:** `period_id = "P2-" + experiment_started_at_utc` (compact) of the new DB, plus (`X_P2`,
  `Y_P2`). No segment mechanism is used.
- **`EXPERIMENT_FREEZE.md` (P1) stays unchanged.** `EXPERIMENT_FREEZE_P2.md` records:
  - the justification (HIGH-8.1 fixed);
  - the frozen economic table, identical to P1, including **10000 USD**;
  - the scope;
  - the new DB and Evidence Store paths;
  - the archive anchor of P1 (item 4);
  - the flat proof (item 6).

**2. New Trading DB.**
- **Path:** a new path, e.g. `…/data/runtime/trading_floor_p2.db`, set through `AI_FLOOR_DB_PATH`.
- **Pinning:** `db_path` is **not** in the fingerprint, so `EXPERIMENT_FREEZE_P2.md` pins it, and a new preflight
  check requires `realpath(config.db_path)` to equal the pinned path.
- **Freshness:** the file must not exist before the first P2 start.

**3. Separate Evidence Store.**
- **Path:** a new path, e.g. `…/market_evidence_p2.db`.
- **Distinctness:** it must be distinct from both Trading DBs, by **realpath and by (`st_dev`, `st_ino`)**.
- **Freshness:** it is created fresh for P2. No P1 evidence store is reused (the flag was never ON operationally).

**4. Archive of the P1 Trading DB.** All of the following happen after a verified halt (H5) and the flat proof:
- **Consistent copy:** use the SQLite **backup API from a `mode=ro` connection**, which includes committed WAL
  content. Never a raw file copy of a WAL-mode DB, and never a checkpoint on the source.
- **Check the copy:** `PRAGMA integrity_check` = ok; schema version 3.
- **Hash:** the SHA-256 of the copy.
- **Archive anchor (OAR-A)**, CJ:
  - `kind: "TRADING_DB_ARCHIVE"`;
  - the P1 identity: `experiment_started_at_utc`, baseline and freeze SHAs (or `NOT_STARTED`);
  - `backup_sha256`, `integrity_check`, `schema_version`;
  - row counts per table; `MAX(journal.id)` and `sqlite_sequence.seq`;
  - the hash and equity of the final account payload;
  - `open_positions = 0`, `pending_orders = 0`, `running_runs = 0`;
  - the source realpath, `st_dev`, `st_ino`;
  - `archive_time_utc`, the hash of the H5 attestation.

  It is signed with `ssh-keygen -Y sign -n v2-archive-anchor` and retained in two Owner-controlled locations, like
  the OAR.
- **The source file** stays at its P1 path, read-only (filesystem permission). The P2 configuration never points to
  it.

**5. Preventing aliases, hardlinks and reuse.** These are new preflight checks in `X_P2`, fail-closed. For the P2
Trading DB, the P2 Evidence Store and the archived P1 file:
- pairwise-distinct realpaths and (`st_dev`, `st_ino`);
- `st_nlink == 1`;
- no symlink in any path component (`realpath == abspath`);
- the P2 Trading DB is not the pinned P1 path;
- the archived file's SHA-256 still equals OAR-A whenever P2 starts. Its WAL and SHM files must be absent or empty;
  otherwise NOT_READY.

**6. Flat proof.**
- **On the archive copy:** zero `paper_positions` with status OPEN; zero `paper_orders` with status PENDING; zero
  `runs` in RUNNING.
- **Attestation:** EX = STOPPED with a valid H5 whose time precedes the copy.
- **Recording:** the counts are part of OAR-A.

**7. Conditions to start the P2 14-day period.**
- **Decisions:** DEC-8.20 (= A2) and DEC-8.19 approved at the policy level; implementation authorization for `X_P2`;
  operational authorization (8.3) for the start.
- **Artefacts:** `Y_P2` signed and deployed; OAR-A retained in two locations; the P2 Trading DB and Evidence Store
  absent.
- **Preflight:** READY, including the item 2 and item 5 checks.
- **Counter:** the 14-day counter starts at P2's `EXPERIMENT_STARTED`, by the existing mechanism.

**Invariants**
- **I-C5:** the realpaths and (`st_dev`, `st_ino`) of the P2 Trading DB, the P2 Evidence Store and the P1 archive
  are pairwise distinct, each with `st_nlink = 1`.
- **I-C6:** `git diff X_P2 Y_P2` touches only the baseline constant and `EXPERIMENT_FREEZE_P2.md`.
- **I-C7:** the P1 archive hash equals OAR-A at every P2 start.
- **I-C8:** the P2 DB's first `EXPERIMENT_STARTED` records (`X_P2`, `Y_P2`) and an account at 10000.

**Negative tests**
- **NC5:** a P2 path through a symlink → NOT_READY.
- **NC6:** a hardlink (`nlink > 1`) → NOT_READY.
- **NC7:** the archive modified after OAR-A → NOT_READY.
- **NC8:** the P2 DB already exists at the first start → refused.
- **NC9:** the Evidence Store path equals the P1 or P2 Trading DB, including by inode → NOT_READY.
- **NC10:** `Y_P2` touches other code → the amendment is invalid.
- **NC11:** a raw copy of a WAL-mode DB without the backup API → hash or integrity mismatch is detected.
- **NC12:** an OPEN position in the archive copy → archive refused.


## 4. (P8.6F, M-1) LOW-1 hardening with exact `previous_decision` semantics

### 4.1 `previous_decision`

The literal `decision` value of the **immediately preceding review row for the same `anomaly_id`** in journal-id order
(valid or not), or `null` for the first row. This is what `record_review` already writes today: the latest raw
decision, read inside its write transaction.

### 4.2 Validity

A review row is VALID only if all of the following hold:
- `source == "revision_review"` and `event_type == EVIDENCE_REVISION_REVIEWED`;
- `decision ∈ DECISIONS`;
- `review_key == anomaly_id`, referring to a recorded `EVIDENCE_REVISION` (otherwise `REVIEW_WITHOUT_RECORD`);
- `reviewer` is a non-blank string;
- `final == (decision ∈ FINAL_DECISIONS)`;
- `previous_decision` equals 4.1 for that row.

### 4.3 Effective state

- The **latest** row per anomaly decides. If it is invalid, the state is `INVALID_DECISION` → blocker
  `INVALID_REVIEW_RECORD`. There is never a fallback to an earlier row.
- An inserted, removed or reordered row makes the next row's `previous_decision` wrong. If that is the latest row, the
  gate blocks.
- **Historical invalid rows** (not latest) are listed in `historical_invalid_reviews` (warning), with the anomaly and
  the journal ids, so the Owner sees the tampering point.

### 4.4 Remediation

- A new valid `record_review` row. Its `previous_decision` is the invalid row's literal decision, so the chain stays
  continuous and the history is never rewritten.

### 4.5 Classification rows

- An `EVIDENCE_REVISION` row counts only if its symbol, timeframe and bar_start equal those of the evidence anomaly
  with that `anomaly_id`. Otherwise → `CLASSIFICATION_MISMATCH`.

### 4.6 Tests

- an unknown decision → BLOCKED;
- missing `reviewer` or `final` → BLOCKED; inconsistent `final` → BLOCKED;
- a wrong `source` → BLOCKED;
- an inserted middle row → the next row is invalid → BLOCKED if it is the latest;
- invalid then valid-final → CLEAR, with the invalid row listed;
- valid-final then invalid → BLOCKED;
- a review for an unknown key → BLOCKED;
- a classification with a mismatched bar → BLOCKED;
- the history row count is unchanged in all cases.

### 4.7 Limit

Well-formed forgery by someone with DB write access remains possible. The mitigation is detection (3.3, 3.5) and access
control.

---

## 5. (P8.6F, M-6) G-8.INT — measurable criteria (proposed, DEC-8.17)

Phase 8 can be certified, and HIGH-8.1 closed for the runtime, only when ALL of these hold for the exact SHA and
fingerprint under review.

| # | Criterion | Threshold (proposal) |
|---|---|---|
| G1 | Scope (P8.6F3) | alternative **A**: every enabled symbol in scope for the whole counted window. Mixed-path (B) segments are `TECHNICAL_ONLY` and never count (1.9.3). Excluding a symbol from trading needs its own signed freeze decision |
| G2 | Simulation on copies (section 6) | PASS, zero STOP conditions |
| G3 | Live PAPER duration per symbol | ≥ 10 **counted session days** (definition below) in one segment, under one SHA and fingerprint |
| G4 | Managed exposure | ≥ 200 cycles per symbol, inside counted session days, with ≥ 1 open position under catch-up |
| G5 | Close reconciliation | ≥ 5 live closes per symbol, 100 % equal to the independent oracle over committed evidence: same bar_start, same price (exact Decimal), same reason, net PnL equal to 1e-8. Window extension below |
| G6 | Duplicates | 0 duplicate closes, fills or claims |
| G7 | Evidence availability | `EVIDENCE_UNAVAILABLE` in ≤ 1 % of in-scope cycles. Every occurrence has a recorded cause and is followed by a correct catch-up (in G5); 0 unexplained |
| G8 | Evidence gaps | 0 unexplained `GAP` anomalies inside any open position's window |
| G9 | Revision gate | CLEAR (including section 4) at the end of the window |
| G10 | Runtime health | 0 `ERROR` runs; 0 INCONSISTENT verifications; 0 emergency halts in the window (a halt restarts G3/G4) |
| G11 | Chain integrity | the segment chain and digests verify (3.3, 3.5) and match the external anchors |
| G12 | Safety | PAPER only, `REAL_EXECUTION_ENABLED=False`, NAS100 OFF, schema 3, on every run |
| G13 | Review | independent review PASS on the SHA, the evidence bundle and these measurements |
| G14 | Decision reproduction (P8.6F6) | **Every run of the period** reproduces independently from REX (5.1.4), including **F1 and A1–A4** (the floor and AI final status from recommendations), P1 / H1, V1 sizing, admission, every pending-order fill-gate trace from full order values, and every not-evaluated path. A stored status is never sufficient |
| G15 | Whole-period chain (P8.6F7) | One unbroken **EDG** chain (all rows of the five economic tables, including CLOSED positions) from `edg_start` to the current EDG over **all** writes (including the startup reconciliation), with event coverage (5.1.3.1). Any UEW → `CERTIFICATION_INVALID` (R-B1). Missing external evidence → **NOT VERIFIED**. Only **VERIFIED** passes |

**(P8.6F2) Counted session day.** A UTC date `D` counts for symbol `X` only if all of the following hold:
1. `D` is Monday–Friday.
2. The scheduled slots are `S(D)`: every 15-minute slot `t` of `D` with `session_names(t) ∩ config.sessions ≠ ∅`, the
   scheduler's own definition (London or New York, 08:00–17:00 local, ZoneInfo / DST). The number of slots varies
   with DST.
3. All of `S(D)` lies inside one segment with the SHA and fingerprint under review, and no EMERGENCY_HALT occurred on
   `D`.
4. `X` was in scope for the whole of `D`.
5. For `X`, ≥ 95 % of `S(D)` have a run with status `COMPLETED` and a final status other than `ERROR`, and there are
   0 `ERROR` runs.

- A day that fails a condition is simply not counted.
- A halt resets the G3, G4 and G5 counters (G10).
- Holidays are not special-cased; a day with mostly `NO_DATA` fails item 5.

**(P8.6F2) Minimum-close extension.**
- If fewer than 5 reconciled closes exist for `X` after 10 counted days, the window extends one counted day at a time
  until 5 closes or **20 counted days**.
- At 20 days with fewer than 5 closes, G5 is **NOT MET**. There is no automatic pass and no substitution by the
  simulation, unless a separate Owner decision and an independent review accept it.
- G4 counts only cycles inside counted days.

### 5.1 (P8.6F5) G14 / G15 evidence contract: the REX

**Status:** a design for a **future** runtime addition; not implemented. It is introduced only in a new baseline
(3.11.1 `X_P2`) under implementation authorization (8.2). Until it is implemented and independently reviewed, G14 and
G15 cannot pass (`DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED` at best).

#### 5.1.1 Paper-state hash and stages (P8.6R5 MEDIUM)

**`psh(state)`:**
- `SHA256("V2PAPER/STATE/1\n" + CJ(list(Store.paper_state(account, orders, fills))))`;
- the tuple elements are the `paper_encode` strings exactly as `paper_state` builds them, in the same order;
- CJ as in 3.3.2.

`Store.paper_state()` itself stays an equality tuple. `psh` is a **new**, separately defined digest of it.
- **Stability:** `psh` is stable only while `paper_encode` is unchanged. G15 is therefore evaluated **within one code
  SHA** (one period), consistent with I-H6.
- **Scope:** `load_paper` is not account-scoped for orders, fills and trades, so `psh` covers exactly what the CAS
  covers.

**Stages of a cycle**, in code order. `W` marks an economic write; each write records `psh_before` (the expected
state it loaded) and `psh_after` (the state it persisted, or "not saved").

| Stage | Name | Write | Notes |
|---|---|---|---|
| ST0 | Claim | — | `RUN_STARTED` |
| ST1 | Ingestion (flag ON) | — (evidence DB only) | |
| ST2L | Legacy management | W, one guarded write | TradeManager on this cycle's newest 5m bar |
| ST2C.k | Catch-up bar `k` | W per bar | one per committed bar applied |
| ST3 | Sizing read | — | `decision_equity` = account equity read after ST2 |
| ST4 | V1 floor and risk | — | |
| ST5 | AI | — | |
| ST6 | Execution re-check | — | freshness, session |
| ST7 | Pending progression | W, the guarded write or the B2.2 gate | the V1 fill gate per order |
| ST8 | Broker reload and admission | — | `paper_policy`, the service gates |
| ST9 | Submission | W, a guarded write | the `equity == decision_equity` re-check |
| ST10 | Finish | — | final status |

**Retries:** a guarded write that is STALE on its first attempt has two attempts. Each attempt records its own
`psh_before`, and only the persisted attempt has a `psh_after`.

#### 5.1.2 REX structure: observed inputs → computed decisions → economic effects → persisted evidence

One REX per run, with an entry per stage reached.

| Layer | Content |
|---|---|
| **Observed inputs** | **Identity:** `run_id`, `slot_key`, `slot`, `symbol`, `code_sha`, `config_fingerprint`, `enabled_symbols`, `symbol_index`, `scheduler_cycle`, `v2_position_catch_up`, `catch_up_scope`. **Market:** the newest 5m bar used by ST2L / ST7 (timestamp and OHLC) and its snapshot freshness, or the committed evidence bar references (`bar_start`, `digest`) for ST2C and B2.2. **Clock and session:** `execution_at`, `execution_fresh`, `execution_state`, `session_open`, `diagnostic_outside_session`. **AI:** for each of `ai_structure`, `ai_liquidity`, `ai_macro`, `ai_setup_review`, `ai_trade_review`: present / absent, `agent_name`, `status`. Plus `ai.final_status`. **V1 floor output:** setup status and side; the full `trade_plan`; the full `risk_decision` (status, reason, quantity, capital_at_risk, entry, SL, TP, `equity_at_decision`, risk_fraction, multiplier). **Configuration:** the full `risk_config` and its hash; the instrument (multiplier, `quantity_increment`) and its hash; `paper_enabled`; `data_state` as passed to `paper_policy` (default `CURRENT`). **State:** `psh` at every stage boundary, and the decoded account fields (equity, cash, starting, realized, unrealized) at ST3, ST7 and ST9 |
| **Computed decisions** | Recomputed by the oracle; recorded by the runtime for comparison: the **ST2** management outcome per bar (`no_hit`, or stop / target with exit price); **ST3** `decision_equity`; **ST4** the V1 gate trace and sizing intermediates (`risk_unitario`, `risk_money`, `capital_max`, `quantity_risk`, `quantity_capital`, raw qty, floored qty); **ST7** `ai_healthy` with its four inputs, the legacy eligibility clauses (`paper_enabled`, `paper_blocked`, `ai_healthy`, `final_status`) or the `CurrentCycleGate` fields and the B2.2 result; **per pending order:** the `bar.timestamp > as_of` result, then the V1 fill-gate inputs (`fill_price`, `risk_per_unit`, `reward`, `reward / risk_per_unit`, `real_risk`, `current_equity`, `equity_at_submission`) and the outcome of **each clause** in order, giving FILLED / REJECTED (clause) / CANCELLED (reason); **ST8** `paper_policy` with each clause, then the service admission clauses in order; **ST9** the submit re-check clauses (pending on symbol, open on symbol, `equity == decision_equity`) and the `submit_plan` refusal condition, if any (the 0 grounding list) |
| **Economic effects** | Per write: `psh_before`, `psh_after`, the attempt number, the created or changed entity ids (`order_id`, `fill_id`, `position_id`, `trade_id`), and the journal ids of the events produced |
| **Persisted evidence** | The REX rows themselves (5.1.3), their journal ids, and `rex_digest = H("V2REX/1", CJ(rex without rex_digest))` |

**Paths without a risk decision.** Every early exit records the stage reached and the inputs of the condition that
stopped it. The oracle re-evaluates that condition from them.
- **Data:** `NO_DATA` (provider error kind); `STALE_DATA` / `DATA_UNAVAILABLE` (the freshness inputs and the
  provider health state).
- **Session:** the session gate (slot, clock, sessions).
- **Floor:** equity invalid in the floor (`decision_equity`); no setup or plan (setup status); risk REJECTED (the
  ST4 trace).
- **AI:** AI not `PLAN_READY` (the AI statuses).
- **Execution re-check:** blocked (the ST6 inputs).
- **Admission:** `AI_CAUTION`, `PENDING_ORDER`, `EXISTING_POSITION`, `PAPER_DIAGNOSTIC_BLOCKED`, `paper_blocked`
  (ST8 inputs).

A run that ends `ERROR` / `FAILED` records the last stage reached, when possible; it counts against G10 in any case.

#### 5.1.2.1 (P8.6F6, P8.6R6 HIGH) AI and floor decision reproduction: required REX inputs

`psh` and a stored `final_status` are **never** accepted as evidence of a decision. The oracle recomputes the decision
from the fields below and compares.

| Group | Required fields (all mandatory; absence → evidence INVALID) |
|---|---|
| Deterministic floor result (before the AI) | `det.final_status`; the full `det.warnings` tuple; setup `status`, `side`, `warnings`; `trade_plan` present / absent plus all plan fields; the full `risk_decision` or null; `decision_equity` |
| Setup reviewer | present / absent; `agent_name`; `status`; `recommendation` (null allowed); `bias`; `confidence`; `warnings` |
| Trade reviewer | present / absent (present only if a plan exists); `agent_name`; `status`; `recommendation` (null allowed); `bias`; `confidence`; `warnings` |
| Structure, liquidity and macro AI | present / absent; `agent_name`; `status`; `recommendation`; `bias`; `confidence` |
| Recorded output | `ai.final_status` (for comparison only) |
| Rule identity | `ai_rules_version`, plus the SHA-256 of `ai/orchestrator.py`, `ai/contracts.py`, `runtime/gates.py`, `floor/orchestrator.py` and `execution/paper_broker.py` at `code_sha`; the constant sets `VALID_FLOOR_STATUSES`, `VALID_AI_STATUSES`, `VALID_RECOMMENDATIONS` and the two trigger values (`DISAGREE`, `REJECT_RECOMMENDATION`) |

**Oracle rules** (literal transcription of the baseline code, in this exact order):
- **F1** (floor status): reproduce `det.final_status` from the setup status, plan presence and risk status, in the
  order of the 0 grounding row. Setup detection and planning themselves are deterministic modules whose market inputs
  are not persisted; **G14 starts from the recorded setup and plan**, and that boundary is stated in the evidence.
- **A1:** `"equity_invalid" ∈ det.warnings` → `NO_DATA`.
- **A2:** `det.final_status ∉ VALID_FLOOR_STATUSES` → `ERROR`. This includes `PLAN_UNAVAILABLE`.
- **A3:** `det.final_status == PLAN_READY` and (setup reviewer present ∧ `recommendation == DISAGREE`, or trade
  reviewer present ∧ `recommendation == REJECT_RECOMMENDATION`) → `AI_CAUTION`. **The status of those responses is
  ignored**, as in the code.
- **A4:** otherwise `det.final_status`.
- **P1** (`paper_policy`): `data_state == CURRENT` (the runtime passes the default) ∧ `final_status == PLAN_READY` ∧
  `risk_decision.status == APPROVED` ∧ a plan is present ∧ all five responses are present with status ∈ {OK,
  PARTIAL}.
- **H1** (`ai_healthy`): the first four responses are present with status ∈ {OK, PARTIAL}.

**Missing-value interpretation** (versioned in `ai_rules_version`; literal to the code):
- an absent reviewer never triggers A3;
- `recommendation = null` never triggers A3;
- an absent response makes P1 and H1 false;
- a recommendation outside `VALID_RECOMMENDATIONS` in a REX → evidence INVALID (it cannot come from the validated
  runtime);
- an empty `det.warnings` is valid; an absent `warnings` field → INVALID.

#### 5.1.2.2 (P8.6F6) Pending-order inputs for the fill gate (full values, not hashes)

For ST7, the REX records the following:
- **Every PENDING order of the symbol, before evaluation:** all `PaperOrder` fields (0 grounding row), in **iteration
  order**. `load_paper` has no `ORDER BY`, so the order is recorded rather than assumed. Admission allows at most one
  pending order per symbol, so more than one → recorded and flagged.
- **The bar used:** `symbol`, `timestamp`, `open`, `high`, `low`, `close`, `is_closed`. Under catch-up, also the
  committed evidence `bar_start` and `digest`.
- **Broker inputs:** `rr_policy` (None in the runtime), `current_equity` (`account.equity` at the evaluation),
  `account` validity.
- **Per order:** the clause trace in code order:
  1. status ≠ PENDING → skip;
  2. side / multiplier valid;
  3. bar validity;
  4. `timestamp > as_of`;
  5. `fill_price`, `risk_per_unit`, `reward`, `real_risk`;
  6. the stamped policy (`risk_policy_version`; None → V1 gate);
  7. the V1 clauses: equity valid, `equity_at_submission` valid, `risk_per_unit > 0`, `reward > 0`,
     `reward / risk_per_unit ≥ 3`, `real_risk ≤ current_equity × 0.01`, `real_risk ≤ equity_at_submission × 0.01`;
  8. the resulting status and the created `fill_id` / `position_id`.
- **Missing values** follow the code: `equity_at_submission = None` → the "equity valid" clause is false → REJECTED.
  A field absent from the REX → evidence INVALID; never defaulted by the oracle.

#### 5.1.2.3 (P8.6F7, P8.6R7 MEDIUM) Quantity adapter and AI response provenance

**The adapter stage (F1b), between F1 and A1:**
- **REX record:**
  - the floor report **before** the adapter: `final_status`, the full `risk_decision`, warnings;
  - the instrument `quantity_increment`;
  - the adapter outcome: one of `UNCHANGED_NOT_APPROVED`, `UNCHANGED_EXACT`, `ROUNDED_DOWN`, `REJECTED_BELOW_INCREMENT`,
    `ERROR_INVALID_INCREMENT`, `ERROR_INVALID_QUANTITY`, `ERROR_ROUNDING_INCREASE`;
  - `units`, `rounded`, the quantity and `capital_at_risk` before and after, the added warning;
  - the report **after** the adapter, i.e. the effective deterministic report received by the AI (`final_status` and
    `risk_decision`).
- **Oracle:** transcribes rules (1)–(8) of the 0 grounding row literally, with **Decimal** semantics
  (`Decimal(str(float))`, `ROUND_FLOOR`) and the float conversions exactly as in the code. The 5.1.4 step 3 floor
  reproduction uses this transcription, not an IEEE floor.
- **An adapter `ValueError`** ends the cycle `ERROR` (counted against G10). The REX records the stage reached.
- **A1–A4 are applied to the post-adapter report.** For example, `RISK_REJECTED` produced by the adapter passes
  through A4 unchanged.
- **Rule identity** (extends the 5.1.2.1 row): add the SHA-256 of `runtime/paper_contracts.py` and `ai/runtime.py`.

**AI response provenance (all five responses):**
- **Per response, the REX records:**
  - **response identity:** `schema_version`, `run_id`, `symbol`, `agent_name`, `as_of`;
  - **request identity:** `run_id`, `symbol`, `agent_name`, `as_of`, `prompt_version`, the evidence-id list and the
    `evidence_fingerprint`;
  - **validation:** the validation reason (`ok`, or the failure reason) and the outcome;
  - **substitution:** `substituted` (true when the response is a runtime substitute: invalid response → `ERROR`; no
    evidence → `NO_DATA`; provider exception → `ERROR`);
  - **content:** status, recommendation, bias, confidence, warnings.
- **Provenance rules** (the oracle rejects otherwise → evidence INVALID):
  - **V-P1:** response `run_id` = request `run_id` = the cycle `run_id`; response `symbol` = request `symbol` = the
    cycle symbol; response `as_of` = request `as_of` = the cycle slot; response `agent_name` = request
    `agent_name`; `schema_version` = `AI_SCHEMA_VERSION`.
  - **V-P2:** if `substituted`, then status ∈ {ERROR, NO_DATA} and `recommendation = null`. If not substituted, the
    validation reason is `ok`.
  - **V-P3:** A1–A4, P1 and H1 use only responses that satisfy V-P1 and V-P2. A response reused from another run,
    symbol or timestamp cannot satisfy V-P1.
- **Limit:** the REX proves the identity binding the runtime enforced and recorded. It cannot prove what the external
  provider "really" returned beyond the recorded, validated content. That is outside the trust root (5.1.3.1).

#### 5.1.3 Atomicity: the two alternatives (P8.6R5 HIGH; Owner decision PENDING)

The P8.6F4 text required **both** "REX in the same transaction as the order" **and** "a REX failure never changes the
trading outcome". Those are contradictory, and the P8.6F4 version is **withdrawn**.

| Aspect | **A. Mandatory evidence (atomic)** | **B. Decoupled observability** |
|---|---|---|
| Rule | Each economic write appends its REX stage entry to the broker journal, so it is inserted **inside the same `save_paper` transaction**. If the REX cannot be built or inserted, the transaction rolls back and the economic effect is not committed | The economic write commits exactly as in V1. The REX stage entry is written **immediately after**, in its own transaction, carrying `psh_after` and the write's journal ids |
| REX write failure | The write fails, the cycle fails closed (`ERROR` for that run) and no economic effect happens | The economic effect persists; that run is **non-certifiable** (G14 FAIL) and an alert is raised |
| Rollback | All-or-nothing per write | Economic: as V1. REX: its own transaction |
| Retries | As in today's guarded write: a re-load and re-compute; REX is rebuilt | A REX write is retried only within the same process, with identical content (idempotent by (`run_id`, stage, attempt)). **Backfilling a REX later is forbidden**: inputs observed after the fact cannot be proven |
| Crash between steps | Impossible (one transaction) | An economic effect without a REX → the run is non-certifiable; detected by G14 coverage and the G15 chain |
| DB consistency | Every economic effect has evidence | Some economic effects may lack evidence (detected, never certified) |
| Compatibility with PAPER V1 | **Changes failure semantics**: a new failure source inside the economic transaction (serialization, size or encoding errors would block trading). A behaviour change requiring a new baseline and economic review | **Economically inert**: V1 economic behaviour unchanged under every failure; only certifiability changes |
| Economic impact | Possible: a persistent REX defect halts trading (liveness risk) | None |
| Adversarial tests | NG-A1: a REX encode failure → no order and no fill (rollback). NG-A2: a crash mid-transaction → neither. NG-A3: a REX too large → the cycle errors, the state is unchanged | NG-B1: a REX insert failure → the order persists and the run is flagged non-certifiable. NG-B2: a crash after the economic commit → G14 FAIL for that run. NG-B3: a backfilled REX (wrong time or journal order) → rejected by G15. NG-B4: a REX whose `psh_after` ≠ the next write's `psh_before` → FAIL |

**Recommendation (for Owner approval; not assumed): B.**
- It keeps V1 PAPER economics byte-identical under every failure mode, which matters under a freeze.
- It keeps certification **fail-closed**: a missing or late REX makes the run, and therefore the window,
  non-certifiable. It never makes it falsely certifiable.
- A strengthens evidence completeness at the cost of a new economic failure mode. It should be chosen only if the
  Owner prefers evidence completeness over V1 behavioural identity.
- NG7 (P8.6F4) is replaced by NG-B1.
- **(P8.6F6) Superseded scope for B:** wherever this subsection or NG-B1 / NG-B2 says that a missing REX makes "the run" or "the window" non-certifiable, the **B-STRICT rule (5.1.3.1) applies instead**. Any unevidenced economic write makes the **whole period or segment** permanently `CERTIFICATION_INVALID`; a run without REX and without an economic write is handled by R-B3.

#### 5.1.3.1 (P8.6F7, P8.6R7 HIGH) Alternative B: evidence-gap rule on the complete economic state (part of DEC-8.21b, PENDING)

This replaces the P8.6F6 version, whose "complete detection" claim relied on `psh` and is **withdrawn**.

**1. Six distinct notions**

| Notion | Definition | Role |
|---|---|---|
| CAS state | `Store.paper_state()` (account, OPEN positions, closed trades, orders, fills) and its digest `psh` (5.1.1) | Concurrency only (whole-state comparison). **Not** a completeness proof: it omits CLOSED `paper_positions` rows |
| **Complete persisted economic state (EDG)** | Every row of `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions` (**all statuses**), `closed_trades` | The basis of the evidence chain |
| Economic events | The journal events listed below | Event coverage |
| Operation history | REX write entries (`L(W)`, inputs, decisions, `edg_before` / `edg_after`, `psh_before` / `psh_after`, journal ids) | Decision reproduction (G14) and chain (G15) |
| Final-snapshot integrity | The DB copy's current EDG = the last `edg_after` | Detects persistent divergence at observation time |
| Limits | What snapshots cannot show (item 4) | Explicit |

**2. EDG: the complete economic digest (`V2ECON/STATE/1`)**
- **Tables, in this fixed order:** `paper_accounts`, `paper_orders`, `paper_fills`, `paper_positions`, `closed_trades`.
  The table list is versioned (`edg_version = 1`). A schema change needs a new version and a new baseline.
- **Rows:** all rows of each table, ordered by primary key (bytewise UTF-8). Each row is serialized as `CJ({column:
  value})` over all columns (`id` and `payload` TEXT as stored, never re-parsed). CJ as in 3.3.2.
- **Digests:**
  - `t = H("V2ECON/TABLE/1", name + "\n" + count + "\n" + "\n".join(rows))`;
  - `edg = H("V2ECON/STATE/1", CJ([[name, count, t] for the 5 tables]))`.
- **Historical rows:** closed positions, terminal orders, fills and closed trades are **included**. They are part of
  the persisted economic state, so any persistent modification to them changes `edg`.
- **Not economic state** (explicitly): `review_reports` (a live derived view), `risk_decisions`, `setups` and
  `agent_decisions` (decision records, covered by REX and G14), `system_state`, `ui_snapshots`, `symbol_locks`.
- **REX write entries** record `edg_before` and `edg_after` (computed inside the write path; the PAPER tables are
  small) in addition to `psh`.

**Economic writes (EW) covered:**
- every committed transaction that changes `edg`;
- management writes (legacy and every catch-up bar);
- pending progression (legacy and `gate_pending_orders`);
- submissions;
- **any account creation by `OperationalRuntime.__init__` after the period's formal start.** It is never legitimate:
  see 5.1.3.2. The initial creation **before** the period is genesis, covered by the genesis anchor, not by REX.

**Economic events:** `ORDER_SUBMITTED`, `ORDER_FILLED`, `ORDER_REJECTED`, `ORDER_CANCELLED`, `POSITION_OPENED`,
`STOP_HIT`, `TARGET_HIT`, `POSITION_CLOSED`.

**3. Trust root (explicit)**
1. The code at the period's baseline SHA is the **only** writer of the trading DB.
2. Single-writer enforcement: the `flock`, `AI_FLOOR_INSTANCE_COUNT=1`, and **no other process or person with write
   access**. This is **external** evidence: an Owner-signed single-writer attestation for the period, covering
   hosting access control, the absence of DB shells or manual sessions, and deploy history.
3. **Genesis anchor (OAR-G)** with `edg_start = edg_genesis`, signed **before** the period starts (5.1.3.2).
4. **Chain-head anchors** on a fixed **time** cadence: one per UTC calendar day of the period, counted or excluded, and
   at most 26 h apart, plus initial and final anchors (5.1.3.3). They bound the window in which a consistent rewrite
   of DB and REX could go undetected. They do **not** detect an alteration made and reverted inside an interval.

**4. What can and cannot be shown**

| Category | Content |
|---|---|
| **Provable from SQLite evidence** (a DB copy plus REX) | an unbroken EDG chain (`edg_before = edg_after` of the previous EW); every economic event referenced by exactly one evidenced EW; every run covered; the final EDG equal to the last `edg_after`; G14 reproduction; no **persistent** divergence of any economic row at the observation time |
| **Requires external evidence** | the absence of foreign writers (item 3.2); the anchored `edg_start` / `psh_start` (3.3); the chain-head anchors (3.4); the H5 attestation for halts; the code SHA actually deployed |
| **Not provable retrospectively, by any snapshot** | an alteration made and **reverted** between two observations (an anchor or a copy); a consistent rewrite of the economic rows **and** the REX by an actor with DB write access within one anchor interval; anything before `edg_start` |

**5. Classification of a period**

| Result | Conditions |
|---|---|
| **CERTIFICATION_INVALID** (permanent; R-B6) | any UEW detected: EDG chain break; orphan economic event; a run with an EW and incomplete REX; final EDG ≠ last `edg_after`; an anchor mismatch; a backfilled REX (R-B4); NWR days above the parameter (R-B3) |
| **NOT VERIFIED** (not certified, not invalid; permanent for the period) | the SQLite-evidence checks pass but external evidence is missing or incomplete: no single-writer attestation; no OAR-G; an **absent or late chain-head anchor** (5.1.3.3); an unverifiable signature or a broken anchor chain linkage. A missing interval cannot be repaired retroactively |
| **VERIFIED** (an input to G-8.INT, not a certification by itself) | all SQLite-evidence checks pass **and** all external evidence is present and verifies |

**Rules R-B1…R-B6** (unchanged in intent; now on EDG):
- **R-B1:** any UEW → `CERTIFICATION_INVALID`, whatever the day or run status.
- **R-B2:** day exclusion never removes a write, event or run from coverage.
- **R-B3:** NWR → its day is not counted; above the DEC-8.21b parameter (2 per period) → `CERTIFICATION_INVALID`.
- **R-B4:** no retroactive REX.
- **R-B5:** recovery only through `TECHNICAL_ONLY` continuation, or a new period.
- **R-B6:** `CERTIFICATION_INVALID` is permanent.

**Statement of limitation:**
- With SQLite evidence alone, B-STRICT detects every **persistent** unevidenced change to the economic state and every
  orphan economic event. It **cannot** prove the absence of transient, reverted alterations or of a consistent
  rewrite between anchors.
- The verifiable condition proposed for future certification is "VERIFIED" as defined above: the SQLite evidence plus
  the single-writer attestation plus daily signed chain-head anchors.

**B-QUARANTINE (alternative):** assessed in P8.6F6. Still not recommended; with EDG, its reproducibility argument
would also have to cover CLOSED-position rows.

**Invariants**
- **I-G6 (revised):** the evidenced EWs form one unbroken **EDG** chain from `edg_start` to the current `edg`.
- **I-G7:** every economic event is referenced by exactly one evidenced EW.
- **I-G8:** UEW ≥ 1 ⇒ `CERTIFICATION_INVALID`, permanently.
- **I-G9:** NWR days ≤ the parameter.
- **I-G10:** VERIFIED ⇒ the single-writer attestation, `edg_start` and every daily chain-head anchor are present and
  verify.
- **I-G11:** EDG covers all rows of the five economic tables, including CLOSED positions.

#### 5.1.3.2 (P8.6F8, P8.6R8 HIGH) Genesis of the PAPER account and the period boundary

**Principle:** the initial, legitimate creation of the account is **genesis**. It is anchored **before** the period
starts and is not part of the period's REX chain. **Every** later account creation or reconstruction during a started
period invalidates certification; no exception exists.

**Sequence** (A2 period P2; the same logic applies to any new period):

| Step | Instant | What happens | Evidence |
|---|---|---|---|
| G0 | DB creation | `Store` creates schema 3; no economic rows | — |
| G1 | **Genesis write** | **GENESIS_PREPARATION mode only** (P8.6F10 start-mode contract below), through the dedicated genesis tool under an Owner-signed authorization (operational act OP-7). It creates the new DB file **exclusively** and the single `paper_accounts` row at `starting_equity = 10000`, with no orders, fills, positions or trades. No runtime is constructed and no cycle runs | `GENESIS_PREPARED` journal event written by the tool, with the hash of the authorization |
| G2 | **`edg_genesis`** | The Owner takes a read-only (backup-API) copy. `edg_genesis` is computed, and must equal the **expected** value recomputed independently from (`account_id`, 10000, the encoder at `X_P2`). The copy must also show `experiment_started` absent, zero runs and zero economic journal events | — |
| G3 | **OAR-G** | Signed genesis anchor (`ssh-keygen -Y sign -n v2-genesis-anchor`): `{period_id_pending, db_realpath, st_dev, st_ino, edg_genesis, psh_genesis, MAX(journal.id), sqlite_sequence, schema_version, X_P2, Y_P2, copy_sha256, created_at_utc}`. Retained in two Owner-controlled locations | OAR-G |
| G4 | **Formal start** | First authorized `DemoRunner` start, **in sealed mode** (the sealed-genesis gate below). The gate passes **before** any account-creation branch can run; the constructor can never create the account. `start_experiment_if_unstarted` then writes the start keys and `EXPERIMENT_STARTED` (non-economic, journal id `E0`) | `GENESIS_SEAL_CHECK` (PASS) and then journal `E0` |
| G5 | Period | `edg_start := edg_genesis`. The REX chain begins at the first evidenced EW, whose `edg_before` must equal `edg_genesis` | REX |

**Rules**
- **GEN-1:** `edg_start` is **defined** as `edg_genesis` from OAR-G. Without OAR-G, the period is at best **NOT
  VERIFIED**.
- **GEN-2 (genesis → activation, P8.6F9):** after OAR-G is sealed, **no runtime path may create or recreate the PAPER
  account**. The only legitimate way to reach `E0` is a sealed start that passes the gate below. The verifier checks:
  - (a) no economic journal event with id < `E0`;
  - (b) no runs row mapped before `E0` with an economic row (M8);
  - (c) a `GENESIS_SEAL_CHECK` with result PASS, `edg_observed = edg_genesis`, written **before** `E0` by the same
    process;
  - (d) the first REX `edg_before = edg_genesis`, or, with no REX yet, the current EDG equals `edg_genesis`.

  - **(a), (b) or (d) failing, or a FAIL seal check** → `CERTIFICATION_INVALID` (a genesis failure before `E0` requires
    a **new genesis**, 5.1.3.2 failure policy).
  - **(c) absent** (no sealed-start evidence) → **NOT VERIFIED**.
  - **Withdrawn (P8.6F8 overclaim):** these checks do **not** prove that no change happened between OAR-G and the seal
    check. A deletion of the account followed by an **identical restoration** by an external actor leaves the same EDG,
    and **no hash comparison detects it**. Excluding such actors is part of the trust root (single-writer attestation,
    5.1.3.1 item 3.2). What the design guarantees is narrower: **the runtime itself can never perform that
    recreation**, because sealed mode removes the creation path.
- **GEN-3 (account missing during a started period):** if `paper_accounts` lacks the account after `E0`, the
  disappearance is itself an unevidenced change of EDG → `CERTIFICATION_INVALID`.
  - With the current code, a later runtime construction on a flat book would **recreate** the account at 10000
    silently. That recreation is a second UEW. It can never be legitimized: a REX entry would only document it.
  - Current code with positions or orders present: the runtime fails closed (`paper state without account`). The
    period is invalid in either case.
- **GEN-4 (later startup reconciliation):** the P8.6F7 notion of an "evidenced `STARTUP_RECONCILIATION`" is
  **withdrawn**. After `E0`, any account creation by `__init__` → `CERTIFICATION_INVALID`.
- **GEN-5 (detection, P8.6F9):** detection is limited to **persistent** divergence observed at a check:
  - after `E0`, an EDG change without an evidenced EW (I-G6);
  - before `E0`, an EDG ≠ `edg_genesis` at the sealed start (the seal check);
  - a substituted DB file (OAR-G `realpath`, `st_dev`, `st_ino`).

  A deletion followed by an identical restoration between two observations is **not** detectable (stated limit).
  Prevention of runtime-initiated recreation comes from sealed mode (R-GEN-1), not from detection.

**Future requirement R-GEN-1 (P8.6F9; replaces the P8.6F8 text).** A runtime change; documented only, not
implemented; new baseline only; separate authorization DEC-8.22-i.

- **Fail-closed from the moment OAR-G is sealed, not from `E0`.**
- **Sealed mode** is set by **external** configuration: `AI_FLOOR_GENESIS_ANCHOR` (the path to the OAR-G file and its
  signature) together with an `allowed_signers` path, pinned in `EXPERIMENT_FREEZE_P2.md`.
  - **(P8.6F10)** In the new baseline, sealed mode is **the only mode of the normal runtime**, not an option.
  - When the configuration is absent or invalid, the normal entry points **refuse to start**. Absence never enables
    creation (start-mode contract below).
- **In sealed mode, `OperationalRuntime.__init__` has no account-creation path.** If the account is missing it raises
  (`genesis_sealed_account_missing`) and writes nothing economic.
- **The authority is not the DB.** A DB-resident marker could itself be deleted, so `experiment_started` and any DB key
  are only corroborating evidence.

**Sealed-genesis gate.** As corrected by P8.6F10:
- **Step 1** (OAR-G and DB file identity) runs **before any DB open**, in the startup sequence below.
- **Steps 2–5** run inside `OperationalRuntime.__init__` after `recover()` (non-economic) and before any economic
  write.
- The constructor has **no** account branch at all.

1. **Identify a valid OAR-G:**
   - the signature verifies with `ssh-keygen -Y verify -n v2-genesis-anchor` against the pinned `allowed_signers`;
   - its `X_P2` / `Y_P2` equal the deployed baseline and freeze commit;
   - its `db_realpath`, `st_dev` and `st_ino` equal the configured DB file (and `st_nlink = 1`);
   - otherwise → refuse.
2. **Compute EDG** on the store connection inside the constructor, before any economic write.
3. **No write window between check and construction:**
   - the check **is part of** the constructor, in the same process and the same connection, under the `flock`;
   - no economic write precedes it;
   - the constructor cannot create the account afterwards (no creation path in sealed mode);
   - external writers are excluded only by the single-writer trust root, which is **not provable** from SQLite.
4. **Expected value:**
   - **before `E0`** (`experiment_started` absent): EDG must equal `edg_genesis`;
   - **after `E0`** (a restart): EDG must equal the **chain head**, i.e. the last REX `edg_after` (or `edg_genesis` if
     there is no REX yet). The chain head is read from the REX rows and cross-checked against the latest chain-head
     anchor when one is configured.
5. **Result recording:** one non-economic journal event `GENESIS_SEAL_CHECK {oar_g_hash, expected, edg_observed,
   experiment_started, result}`, written before any economic write of that process. On FAIL, the constructor raises
   after writing it.

**Failure policy**

| Situation | Before `E0` | After `E0` (restart) |
|---|---|---|
| Account absent | Refuse to start; **genesis INVALID**. The Owner discards the DB, voids OAR-G with a signed `VOID` record, and redoes G0–G3 (a new genesis) | Refuse to start; **period `CERTIFICATION_INVALID`** (GEN-3) |
| EDG ≠ expected | Refuse; genesis INVALID; a new genesis | Refuse; period `CERTIFICATION_INVALID` |
| OAR-G invalid, missing, or for another file | Refuse; no start | Refuse; the period stays stopped; NOT VERIFIED until resolved; INVALID if the file was substituted |
| Sealed mode not configured for a start of the period | **Superseded by P8.6F10:** the normal entry point refuses before any DB open (start-mode failure policy below). The preflight is a second barrier, never the only one | Same refusal. Any process start that nevertheless lacks a PASS `GENESIS_SEAL_CHECK` → **NOT VERIFIED** |

**Evidence for classification**
- **`GENESIS_SEAL_CHECK` per process start:** process starts are marked by `RECOVERY_STARTED`, which `recover()`
  writes on every construction. Each `RECOVERY_STARTED` in the period must be followed by a PASS `GENESIS_SEAL_CHECK`
  before the next economic event:
  - a FAIL → INVALID, with the start refused;
  - a missing check → NOT VERIFIED.
- **Other evidence:** the OAR-G file, `allowed_signers`, the DB copy, the REX chain and the anchors.

**Dependencies**
- The external configuration (the env and pinned paths in `Y_P2`).
- A constructor change: the creation branch is removed in sealed mode and the gate placed before it. This is
  `runtime/service.py`, **not** the pinned `storage/database.py`; EDG is computed with read queries.
- The existing `flock` order in `cloud_runner`.
- The single-writer attestation.
- Tests on the implementation (below).

**Observation (not changed):** the bootstrap check ignores `paper_fills` and `closed_trades`. Under GEN-3 that does not
matter, because any missing account after `E0` invalidates the period.

**Compatibility:**
- **A2 (3.11.1):** G1–G3 occur after `Y_P2` is deployed with the runner OFF and before OP-4.
- **OAR:** OAR-G uses the same signing, retention and `allowed_signers` as OAR / OAR-A.
- **EDG / REX:** the chain starts at `edg_genesis`.
- **Freeze:** genesis equity 10000 = the frozen value.

**Invariants**
- **I-G12:** `edg_start = edg_genesis` (OAR-G) = the recomputed expected genesis.
- **I-G13:** no economic change between OAR-G and the first evidenced EW.
- **I-G14:** after `E0`, `paper_accounts` contains the account in every observation; its absence or a recreation →
  `CERTIFICATION_INVALID`.

**Negative tests**
- **NG37:** the account row is deleted on a flat book after `E0`, then the runtime restarts and recreates it at 10000
  → the EDG chain breaks → `CERTIFICATION_INVALID`, even if a REX entry claims `STARTUP_RECONCILIATION`.
- **NG38:** an order is inserted between OAR-G and `E0` → GEN-2 fails → INVALID.
- **NG39:** no OAR-G, everything else passing → NOT VERIFIED.
- **NG40:** OAR-G `edg_genesis` ≠ the recomputed expected genesis → INVALID.
- **NG41:** a DB file substituted after OAR-G (different `st_ino`) → INVALID.

**(P8.6F10, P8.6R10 HIGH) Start-mode contract: GENESIS_PREPARATION vs SEALED_RUNTIME.** These are future
requirements for the new baseline; the current code does **not** satisfy them.

| | **GENESIS_PREPARATION** | **SEALED_RUNTIME** |
|---|---|---|
| When | Only **before** OAR-G, once per period | **Always** after OAR-G; the only mode of every normal entry point (`cloud_runner`, `DemoRunner`, `OperationalRuntime`, manual or diagnostic cycles) |
| How it is selected | Only by explicitly invoking a **separate** genesis tool (e.g. `python -m runtime.genesis_prepare --authorization <file> --allowed-signers <file> --db <new path>`). Normal entry points never import it; no environment variable or default can select it | Hard-wired: the normal entry points have no mode parameter. A start-mode environment variable requesting preparation is **rejected** at startup, never honoured |
| Authority | An **Owner-signed genesis authorization** (`ssh-keygen -Y sign -n v2-genesis-authorization`): `{period_id, target db_realpath, X_P2, Y_P2, starting_equity = 10000, account_id, not_after_utc}`, verified against `allowed_signers` | OAR-G plus `allowed_signers`, pinned in `EXPERIMENT_FREEZE_P2.md` (external configuration) |
| Account creation | Creates the DB file **exclusively** (it must not exist: create-exclusive, so an existing file → refuse) and exactly one account at 10000. Writes `GENESIS_PREPARED {authorization_hash}`. No experiment start, no runtime, no cycle | **None.** The account-creation branch does not exist in this mode |
| Ends | Before OAR-G is sealed (G2–G3 happen after the tool exits) | — |

**Trust root and missing configuration**
- **The default is refusal, not creation.** The normal runtime has no creation path, so it never needs to "know"
  whether OAR-G was sealed; a missing or omitted file can only cause a refusal.
- **Creation requires two things:** (a) the Owner's signed authorization, verified against the Owner key (the same
  `allowed_signers` trust root as every anchor), and (b) a **non-existent** target file (create-exclusive).
- **Re-running preparation after OAR-G:**
  - on the sealed file, it is refused because the file exists;
  - if the file is deleted and the tool re-run with a still-valid authorization, it creates a **different** file
    (new inode and new `GENESIS_PREPARED`). SEALED_RUNTIME then refuses that file (an OAR-G identity mismatch), and the
    original period's evidence shows a substituted file.
- **Authorization lifetime:** short (`not_after_utc`, e.g. 24 h) and bound to one target path.
- **What the trust root cannot prevent:** an actor who holds the Owner's signing key, or who can write the DB directly
  (single-writer assumption, 5.1.3.1).

**Startup order** (SEALED_RUNTIME; a future refactor, R-GEN-1a…e)
1. **Mode:** the entry point is a normal one, so the mode is SEALED_RUNTIME. A start-mode variable asking for
   preparation → refuse.
2. **Existing guards:** `cloud_runner` environment guards (`RENDER`, `AI_FLOOR_CLOUD_RUNNER`), as today.
3. **Lock:** acquire the `flock`.
4. **Verify the external configuration before any DB open:**
   - `AI_FLOOR_GENESIS_ANCHOR` and `allowed_signers` are present and readable;
   - the OAR-G signature verifies;
   - its `X_P2` / `Y_P2` equal the deployed constant and `AI_FLOOR_GIT_COMMIT`;
   - the DB path equals the pinned path;
   - the file **exists** and its `realpath`, `st_dev`, `st_ino` and `st_nlink = 1` match OAR-G.

   Any failure → refuse; **no DB file is opened or created.** This also removes the P8.6R10 observation that
   `recover()` writes before identity is validated.
5. **Construct the runtime** (no creation branch). `Store` opens the existing file; schema 3, so no migration
   writes. Then `recover()`, which writes only non-economic rows.
6. **Gate steps 2–5:** account present; EDG equals `edg_genesis` (before `E0`) or the chain head (after `E0`);
   `GENESIS_SEAL_CHECK` written. On failure → raise.
7. **Formal start:** `DemoRunner` preflight, the `runner` state, then `start_experiment_if_unstarted` (`E0`), only
   after steps 1–6 passed. The preflight (also read-only and non-creating, R-GEN-1d) **reinforces but never replaces**
   steps 4–6.

**Start-mode failure policy**

| Case | Behaviour | Certification classification | Evidence |
|---|---|---|---|
| Mode absent | Not possible on normal entry points (always SEALED_RUNTIME) | — | code inventory test |
| Mode variable requests preparation on a normal entry point | Refuse before any DB open | No start; nothing to certify | process error log; no `RECOVERY_STARTED` in the journal |
| Sealed configuration absent (env or `allowed_signers`) | Refuse before any DB open | No start. If the period had started: it stays stopped, with no new entries | as above |
| OAR-G signature invalid, or OAR-G missing | Refuse before any DB open | as above; NOT VERIFIED if a start without a check is ever found | signature-check log |
| DB file wrong (path, inode, nlink, symlink) | Refuse before any DB open | Before `E0`: genesis INVALID. After `E0`: a substituted file → period `CERTIFICATION_INVALID` | identity values vs OAR-G |
| Account absent | Refuse at gate step 6 (after the non-economic `recover()`) | Before `E0`: genesis INVALID → a new genesis. After `E0`: `CERTIFICATION_INVALID` | `GENESIS_SEAL_CHECK` FAIL |
| EDG inconsistent | Refuse at step 6 | as above | `GENESIS_SEAL_CHECK` FAIL with expected and observed values |
| Preparation requested after OAR-G | The genesis tool refuses (the file exists). On a deleted file it creates a new file, which SEALED_RUNTIME refuses by identity | The original period: substituted file → INVALID | tool log; `GENESIS_PREPARED` on the new file; OAR-G mismatch |
| Restart after `E0` | Steps 1–7 again; step 6 compares with the chain head | Pass → continue. Fail → INVALID | `GENESIS_SEAL_CHECK` per `RECOVERY_STARTED` |

No row claims infallible detection of external tampering. An identical restoration by an actor with DB access remains
undetectable (NG48).

**Implementation requirements (future; new baseline; DEC-8.22-i, extended)**
- **R-GEN-1a:** remove the account-creation branch from `OperationalRuntime.__init__`. A missing account always
  raises.
- **R-GEN-1b:** a startup function performing steps 1–4 before any `Store` open, called by every normal entry point.
  The `OperationalRuntime` constructor requires the verified startup context it produces, so it cannot be built
  without passing steps 1–4.
- **R-GEN-1c:** the separate genesis tool (GENESIS_PREPARATION) with authorization verification and create-exclusive.
- **R-GEN-1d:** the preflights open the DB read-only and never create it.
- **R-GEN-1e:** an inventory test: no normal-entry module imports the genesis tool, and no `PaperAccount` creation
  call exists outside it.

Existing tests that rely on implicit account creation will need fixtures that use the genesis tool. This is an
implementation impact, not a design change.

**Invariants (P8.6F10)**
- **I-G20:** no account-creation code path is reachable from any normal entry point.
- **I-G21:** in SEALED_RUNTIME, steps 1–4 complete before any DB file is opened or created.
- **I-G22:** account creation happens only in GENESIS_PREPARATION, with a valid, unexpired, Owner-signed
  authorization, on a file that did not exist.

**Negative tests (P8.6F10; proposed, not executed)**
- **NG54:** a normal start with the sealed configuration absent → refused before the DB open; no file created; no
  account.
- **NG55:** a normal start against a DB without an account and with a **valid** sealed configuration → refused at
  step 6. A probe asserts that the account-creation code is never reached (none exists in SEALED_RUNTIME).
- **NG56:** a preflight that would pass (or was skipped) with the sealed configuration missing → the constructor still
  refuses (the preflight is not the only barrier).
- **NG57:** a fresh process with no flags → GENESIS_PREPARATION is not activated; no file is created.
- **NG58:** a start-mode variable set to preparation on `cloud_runner` → refused (no silent fallback).
- **NG59:** a manipulated configuration (OAR-G swapped, `allowed_signers` edited to another key, `X_P2` mismatch)
  → refused; no creation.
- **NG60:** a start after OAR-G with the env unset → fail closed before the DB open.
- **NG61:** the genesis tool with an expired or unsigned authorization, or against an existing file → refused.

**Invariants (P8.6F9, OAR-G → `E0` window)**
- **I-G17:** in sealed mode, `OperationalRuntime.__init__` never writes `paper_accounts`.
- **I-G18:** every process start in the period writes a PASS `GENESIS_SEAL_CHECK` before any economic write.
- **I-G19:** a sealed start before `E0` observes EDG = `edg_genesis`; after `E0`, EDG = the chain head.

**Negative tests (P8.6F9)**
- **NG47:** the account is deleted after OAR-G; then a sealed start → refused, no account created, `GENESIS_SEAL_CHECK`
  FAIL → genesis INVALID.
- **NG48:** the account is deleted and restored identically before the seal check → **not detected**. This documents
  the limit; it is never reported as detection.
- **NG49:** a period start without sealed mode (env unset) → preflight NOT_READY. If such a start happened anyway →
  NOT VERIFIED.
- **NG50:** an order inserted between OAR-G and the sealed start → EDG ≠ `edg_genesis` → refused, INVALID.
- **NG51:** a restart after `E0` with EDG ≠ the chain head → refused; period INVALID.
- **NG52:** a `RECOVERY_STARTED` followed by an economic event with no PASS seal check in between → NOT VERIFIED.
- **NG53:** an OAR-G for another DB inode, or with an invalid signature → refused.

#### 5.1.3.3 (P8.6F8, P8.6R8 MEDIUM) Chain-head anchor cadence and failures

This replaces "at least once per counted day". The cadence depends on **time**, never on day counting.

- **Schedule.** Anchors are tied to UTC calendar days, independent of counting:
  - an **initial anchor** = OAR-G (5.1.3.2);
  - **one anchor per UTC calendar day** from the start date to the end date, including weekends, holidays,
    non-counted and excluded days, and days with no operations;
  - a **final anchor** at period close or archive (OAR-A under A2).
- **Maximum gap:** consecutive anchors' `anchored_at_utc` must be ≤ **26 h** apart (24 h plus a 2 h operational
  tolerance), and each UTC day `d` must have an anchor with `anchored_at_utc ∈ [d 00:00, d+1 02:00)`.
- **Content:**
  - `{period_id, anchor_seq, anchored_at_utc, previous_anchor_hash, edg, psh, MAX(journal.id), sqlite_sequence.seq,
    last_rex_seq, last_rex_digest, copy_sha256}`;
  - the copy is taken with the backup API from `mode=ro` (an operational act, OP-6);
  - anchors are hash-chained through `previous_anchor_hash`.
- **Signing, custody and checks:**
  - signed with `ssh-keygen -Y sign -n v2-chain-anchor`, retained in two Owner-controlled locations; the repository
    copy is a convenience only;
  - the verifier checks:
    - each signature against `allowed_signers`;
    - the `previous_anchor_hash` linkage;
    - monotonic `anchor_seq` and `MAX(journal.id)`;
    - that each anchor's `edg` / `psh` equal the replayed state at `last_rex_seq`;
    - the gap rule.
- **Days without operations:** an anchor is still required; it shows `edg` unchanged.
- **Interruptions and connectivity:** if the Owner cannot take a copy (hosting unreachable, outage), the anchor is
  **absent** or **late**.

**Consequences (verifiable)**

| Condition | Result |
|---|---|
| Anchor content inconsistent with the replayed chain (`edg`, `psh`, journal or REX position), or broken `previous_anchor_hash` linkage | **CERTIFICATION_INVALID** |
| An anchor absent for a UTC day, or a gap > 26 h, with everything else consistent | **NOT VERIFIED** (permanent; a later anchor cannot attest the past interval) |
| Every anchor present, on time, valid and consistent, plus the other VERIFIED conditions | eligible for **VERIFIED** |

**Limit (unchanged):** daily anchors bound the persistent-rewrite window to ≤ 26 h. They do **not** detect an
alteration made and reverted within one interval.

**Invariants**
- **I-G15:** for every UTC day of the period there is an anchor in its window, and every consecutive gap is ≤ 26 h.
- **I-G16:** the anchor chain linkage is unbroken and consistent with the replay.

**Negative tests**
- **NG42:** a Saturday (not counted) with no anchor → NOT VERIFIED.
- **NG43:** a 27 h gap → NOT VERIFIED.
- **NG44:** an anchor whose `edg` differs from the replay at its `last_rex_seq` → INVALID.
- **NG45:** a broken `previous_anchor_hash` → INVALID.
- **NG46:** a row altered and restored between two on-time anchors → not detected; documents the limit, never
  reported as detection.

#### 5.1.4 G14: independent reproduction (P8.6F6)

The oracle neither imports nor reuses runtime modules, and is reviewed independently. **Every** run of the period is
checked, not only counted days (R-B2).

**Scope of G14 (P8.6F8, P8.6R8 LOW):**
- G14 is a **deterministic replay of the runtime's rules over persisted, validated data**: the floor and adapter
  results, the recorded AI responses and their provenance, the configuration and the state.
- It does **not** reproduce the provider's inference.
- It does **not** prove what an external model returned beyond the validated content stored in REX.
- "AI reproduction" in this document always means this rule replay.

1. **Coverage:** run coverage (5.1.3.1). An NWR is handled by R-B3.
2. **F1, F1b, V-P1–V-P3, then A1–A4:** recompute the floor status (F1), the adapter transition (F1b, 5.1.2.3), the
   response provenance, and `ai.final_status` on the post-adapter report. A stored value alone is never accepted.
3. **ST4:** IEEE-754 V1 sizing in the exact operation order, then the increment floor; compare `float.hex` exactly;
   the gate order gives status and reason.
4. **ST7:** recompute H1 (`ai_healthy`) and the eligibility clauses, then each pending order's clause trace from
   5.1.2.2 (full values).
5. **ST8 / ST9:** recompute P1 (`paper_policy`), the admission clauses, the submit re-check and the `submit_plan`
   refusal condition.
6. **Not-evaluated paths:** re-evaluate the stopping condition.
7. **Result:** any mismatch or INVALID evidence → that run fails G14. If the run made an EW, the write is a UEW
   (R-B1).

#### 5.1.5 G15: stage replay over the whole period (P8.6F7)

- **Chain:** the EDG chain of 5.1.3.1 (I-G6) spans the **entire period** from `edg_start`. The `psh` chain is also
  checked, for CAS consistency. A write not covered by evidence → UEW → R-B1. Excluding a day is never an option for
  writes (R-B2).
- **Reads:** non-write stages are checked against the state at their boundary (`decision_equity`, and
  `current_equity` at ST7).
- **Order:** run and stage order equals the execution order. Scheduler order and `scheduler_cycle` affect only
  counting.
- **Result:** G15 yields VERIFIED, NOT VERIFIED or `CERTIFICATION_INVALID` (5.1.3.1 item 5). Only VERIFIED can feed
  G-8.INT.

#### 5.1.6 Replay procedure (the oracle)

1. Start from the state at the window start (`psh` checked).
2. For each slot, for each symbol in order:
   - apply ST2 with the 3.x oracle (per bar for catch-up; the newest bar for legacy) and check `psh_after`;
   - apply ST4 and ST7–ST9 with the reproduced decisions, and check each `psh_after`.
3. The final state must equal the recorded state.

The replay reproduces the **real sequence**, not only final results.

#### 5.1.7 If the V2 engine is ever wired in

Re-specify 5.1, adding V2 inputs (per-position open risk, pending reservations, the stamped policy, the V2 fill money
check), and re-audit.

#### 5.1.8 Invariants and negative tests

**Invariants**
- **I-G1:** a complete REX for every counted run.
- **I-G2:** the oracle equals the REX at every decision.
- **I-G3:** the `psh` chain is unbroken across all economic writes.
- **I-G4:** under alternative B, a REX failure never changes an economic row; under A, it rolls back the whole
  write.
- **I-G5:** `psh` recomputed from the DB at any write boundary equals the recorded value.

**Negative tests**
- **NG1–NG6, NG8, NG9:** as in P8.6F4.
- **NG7:** replaced by NG-B1 (alternative B) or NG-A1 (alternative A).
- **NG10:** an AI response status altered in the REX → the `paper_policy` reproduction mismatches → FAIL.
- **NG11:** `equity_at_submission` altered → the fill-gate reproduction mismatches → FAIL.
- **NG12:** a missing catch-up bar stage entry → G15 FAIL.
- **NG13:** `psh` computed over a re-ordered tuple → mismatch.
- **NG14:** a STALE first attempt → two attempt entries, only the second has `psh_after`.

#### 5.1.9 (P8.6F6) New negative tests (AI reproduction and the evidence gap)

**AI and floor reproduction**
- **NG15:** two REX with identical AI statuses (all OK) but setup recommendation `DISAGREE` vs `AGREE` → the oracle
  derives `AI_CAUTION` vs `PLAN_READY`. A REX whose stored status contradicts the derivation → FAIL.
- **NG16:** same statuses, trade review `REJECT_RECOMMENDATION` vs `ACCEPT` → `AI_CAUTION` vs `PLAN_READY`.
- **NG17:** setup review status `ERROR` with `recommendation = DISAGREE` → `AI_CAUTION` (status ignored by A3).
- **NG18:** `det.final_status = PLAN_UNAVAILABLE` → `ERROR` (A2).
- **NG19:** `equity_invalid` in `det.warnings` → `NO_DATA` (A1), even with a plan.
- **NG20:** the REX lacks a reviewer's `recommendation` field while its stored `final_status` matches → evidence
  INVALID (not accepted).

**Pending-order inputs**
- **NG21:** the REX for a pending order lacks `equity_at_submission` while `psh` matches → INVALID.
- **NG22:** pending-order evidence given only as `psh` → INVALID.
- **NG23:** two pending orders for one symbol → recorded in iteration order and flagged.

**Evidence gap (B-STRICT)**
- **NG24:** a crash after a fill commit and before its REX, on a day later excluded → `CERTIFICATION_INVALID`
  (R-B1 / R-B2).
- **NG25:** a crash after a management write with no journal event (only `last_price` moved), and no further writes →
  the current `psh` ≠ the last `psh_after` → `CERTIFICATION_INVALID`.
- **NG26:** an orphan `POSITION_CLOSED` not listed in any REX → `CERTIFICATION_INVALID`.
- **NG27:** a `FAILED` / `interrupted_run` with an EW and no REX → `CERTIFICATION_INVALID` (status does not exempt).
- **NG28:** a REX written after a later EW (backfill) → INVALID (R-B4).
- **NG29:** 3 NWR days with the parameter at 2 → `CERTIFICATION_INVALID`; 2 NWR days → those days are not counted
  and the period may still certify.
- **NG30 (positive):** a manual cycle with a complete REX → the chain is intact and the day is not counted.

## 6. Simulation procedure (later rehearsal on copies; no operational DB access)

### 6.1 Inputs

Each input has its SHA-256 recorded:
- Owner-made copies of the trading DB and the Evidence DB (backup API);
- a bar export for XAUUSD and EURUSD covering every open position from `opened_at` to the last rehearsed slot;
- the code SHA under test;
- the fingerprints per scope;
- a frozen clock and slot list.

### 6.2 Preparation

(P8.6F2) Run the status classification (2) on the copy. Proceed only if FS ∈ {NOT_STARTED, STARTED}, EX = STOPPED
(attested) and INTEGRITY = OK; otherwise STOP or act per 2.4.

### 6.3 Twins in a private temp directory

- **A:** scope XAUUSD;
- **B:** flag OFF (reference);
- **C:** scope XAUUSD,EURUSD.

The originals are hashed before and after.

### 6.4 Steps

1. Run the preview with `--catch-up-symbols XAUUSD`.
2. If the state is STARTED: transition **dry run** on A. Check the payloads, the chain links and the mark age.
3. Replay the slots on A, B and C (deterministic AI stub).
4. Duplicate a slot; kill mid-cycle and `recover()`.
5. Inject the observe-only faults on EURUSD in A (1.5).
6. Run the revision gate on each twin, plus the section 4 adversarial rows.
7. EMERGENCY_HALT, then a ROLLBACK dry run on A.
8. Run the chain and digest verification after every transition dry run.
9. Re-hash the originals.

### 6.5 Oracles

- **Catch-up symbols:** the independent Decimal oracle over the export.
- **EURUSD on A:** management of existing positions identical to B (events, closes, PnL, watermark). New entries may
  differ in size, because the shared equity changes earlier.
- **Boundaries:** `opening_* == closing_*`; no close, cancel, transfer or re-price.

### 6.6 STOP conditions (any → stop and record)

- An original's hash changes, or a write happens outside the temp directory.
- Any oracle mismatch; any EURUSD management divergence between A and B.
- A boundary changes economics.
- A mark exceeds the maximum age and the seal is accepted anyway.
- The chain or digest verification fails, or passes on a deliberately tampered twin.
- A revision gate CLEAR with an unclassified, unresolved, escalated or invalid anomaly.
- A READY preflight with an invalid scope or a non-contiguous evidence window.
- NAS100 appears; REAL is True; schema ≠ 3.
- An unexpected exception or `ERROR`.
- Two runtimes acting on one twin.

### 6.7 Exit

A PASS/FAIL record with the SHA, input hashes, outputs and timings. PASS satisfies G2 only.

---

## 7. Risk matrix

Likelihood (L) and Impact (I): H/M/L.

| # | Risk | L | I | Treatment | Residual |
|---|---|---|---|---|---|
| R-1 | EURUSD results biased while out of scope (HIGH-8.1) | H | H | `HIGH_8_1_AFFECTED` tagging, exclusion from certified metrics, G1 | M: account-level contamination until EURUSD joins |
| R-2 | Shared equity: catch-up closes change other symbols' sizing | H | M | expected; the simulation compares management only | L |
| R-3 | Continuity weakens a historical check (H-1) | L after fix | H | per-segment full validation (3.5) | L |
| R-4 | Sealed history modified, or the chain forged (H-2) | L | H | digest, hash chain, external anchor, access control | M: no SQLite-level append-only guarantee |
| R-5 | Seal at a stale mark | M | M | 20-minute maximum, else BLOCKED (3.6) | L |
| R-6 | Execution state misread (a running process taken for stopped) | M | H | (P8.6F2) independent axes; STOPPED needs an Owner attestation plus DB evidence; otherwise UNKNOWN → no action | L |
| R-7 | Pending orders without TIF block a transition indefinitely | M | M | DEC-8.19; never auto-cancel | M |
| R-8 | Observe-only fault leaks into the cycle (latency or lock) | M | M | 1.5 isolation, short busy timeout | L |
| R-9 | EURUSD evidence gaps prevent a later joining | M | M | 1.7 contiguity check; no backfill | M |
| R-10 | Emergency needs speed but transitions need ceremony | M | H | two-stage: immediate HALT, then ROLLBACK | M: out-of-scope symbols unmanaged while halted |
| R-11 | Runs continue after day 14 (no end marker) | M | M | (P8.6F2) PD = PERIOD_ELAPSED never means ended or certified; `RUNS_AFTER_PERIOD` kept outside the result window; `CLOSE` seal only by an Owner decision | M until an end marker exists (future work) |
| R-12 | LOW-1 and well-formed forgery in the review journal | L | M | section 4 validation plus detection | L/M |
| R-13 | Local `demo_runner` without `flock` | L | H | never point it at a cloud-reachable DB | L |
| R-14 | Inventory tests constrain module names | M | L | scope logic in `runtime/config.py` / `runtime/service.py` | L |
| R-15 | Source reports not attached (P8.6F, P8.6F2) | — | — | **Resolved in P8.6F3**: all four reports received and reconciled (section 14) | — |
| R-16 | The Owner's signing key is compromised or lost | L | H | registered key (DEC-8.20); a secondary external anchor; key rotation only through a signed amendment | M |
| R-17 | STRICT completeness blocks scope expansion for long periods (weekends, breaks) | H | L | intended fail-closed; an optional approved calendar (DEC-8.14) | L |
| R-18 | Halt deadlock (management-path trigger plus stale marks) | L | M | the intended safe state in PAPER; exit only through a new Owner decision | L |
| R-19 | History-mapping assumptions (RUN_STARTED uniqueness, payload `run_id` on every economic row, `ClosedTrade.run_id` semantics, AUTOINCREMENT contiguity) | M | H | listed for independent validation (section 13); any violation → INCONSISTENT (fail-closed) | L |
| R-20 | The P8.6R2 report was not attached | — | — | **Resolved in P8.6F3** (section 14) | — |
| R-21 | Mixed-path results contaminate XAUUSD **sizing (`decision_equity`) and fill admission (the V1 fill gate on `current_equity` and `equity_at_submission`)** through shared equity (P8.6R3 HIGH, P8.6R5 HIGH) | H | H | 1.9.1 / 1.9.3: mixed-path `TECHNICAL_ONLY`; certification only under A with G14 (including the fill gate) and G15 | L |
| R-22 | Carried closes mutate sealed history (P8.6R3 HIGH) | H before fix | H | E-rules, T7 removed, T14 snapshot, 3.3.7 argument, I-H6 | L, pending implementation and re-audit |
| R-23 | Proposal-hash cycle (P8.6R3 HIGH) | — | H | self-hash rule and DAG (3.3.1, 3.3.4) | L |
| R-24 | Halt/restart race: in-flight tick, supervisor restart (P8.6R3 MEDIUM) | M | M | H1 disables restart authority first; H4 verification; H7 fail-closed | L/M: supervisor behaviour is external |
| R-25 | Anchor trust boundary (P8.6R3 MEDIUM) | M | H | OAR signed by an SSH key, two Owner-controlled locations, `allowed_signers` trust root | M: key custody |
| R-26 | A new SHA re-encodes sealed rows differently | M | H | I-H6 checked at PREPARE; NH16 | L |
| R-27 | The design reasoned from the V2 risk engine, which is not wired in (found in P8.6F4) | — | H | 0 grounding rows; 1.9.1 corrected; G14 targets V1 | L |
| R-28 | G14 / G15 impossible without the REX runtime addition | H | H | 5.1 requirement; certification blocked until implemented and reviewed | M |
| R-29 | `sqlite_sequence` anomalies | L | H | explicit ids, seq = MAX checks, fail-closed (3.3.3) | L |
| R-30 | Economic activity after a halt request (whole tick completes) | M | M | operational timing mitigation (partial); R-HALT (CP1–CP9, bookkeeping and recovery; P8.6F5) future requirement, new baseline only; M-5 OPEN | M until R-HALT |
| R-31 | `load_paper` not account-scoped | M | H | A2 uses a new DB file (3.11); NC4 | L |
| R-32 | A flat account cannot be reached deterministically | M | M | opportunistic flat halt; bounded wait; R-WIND-1 future option | M |
| R-33 | REX atomicity choice: A adds an economic failure mode; B allows uncertifiable runs | M | M | 5.1.3 comparison; recommendation B; Owner decision | L/M |
| R-34 | Runtime changes (REX, R-HALT, R-WIND) inside a frozen period | M | H | only in a new baseline (`X_P2` / `Y_P2`); never hot-patched (8.2) | L |
| R-35 | A2 path aliasing or reuse of the P1 DB | L | H | realpath, inode and nlink checks, a pinned path, OAR-A (3.11.1) | L |
| R-36 | The REX payload grows with state (account, positions, orders at each stage) | M | L | `psh` plus decoded fields only at ST3 / ST7 / ST9; size bound tested (NG-A3 / NG-B) | L |

---

## 8. Owner decisions in three levels (ALL PENDING; none approved)

Approving a policy does not authorize implementation, and implementation does not authorize operation (P8.6R5).

### 8.1 Policy decisions (DEC-8.13 … DEC-8.21)

The dependency graph is **acyclic**. P8.6F5 breaks the DEC-8.16 ↔ DEC-8.17 cycle: both are independent policies and
may be approved together.

| ID | Policy proposal | Status | P8.6R5 readiness | Depends on (acyclic) | If approved / rejected |
|---|---|---|---|---|---|
| DEC-8.13 | Scope variable set while OFF → error | **PENDING** | ready | — | no dormant configuration / an unnoticed scope |
| DEC-8.14 | Observe-only ingestion; STRICT completeness; CALENDAR unavailable; **explicit acceptance** of STRICT blocking and of the unknown lookback and entitlement; expansion blocked until evidence is demonstrated | **PENDING** | conditional on the acceptance | DEC-8.13 | fail-closed expansion / no out-of-scope ingestion |
| DEC-8.15 | Global conservative revision gate | **PENDING** | ready | DEC-8.14 | complete coverage / gaps |
| DEC-8.16 | **Certification eligibility policy only:** mixed-path segments are `TECHNICAL_ONLY`; B optional technical; A necessary. Economic certification is granted only if G-8.INT (DEC-8.17) passes | **PENDING** | the TECHNICAL_ONLY policy is adequate | — | no certification of contaminated economics / none |
| DEC-8.17 | **Measurement policy only:** G-8.INT G1–G15 as defined (G14 including the V1 fill gate; G15 stage replay); the REX as the evidence source (5.1); multi-symbol semantics certified as-is (1.9.4); halt triggers; **explicit acceptance or rejection of the post-halt residual activity while R-HALT does not exist** | **PENDING** | was blocked by the REX gap, the cycle and M-5; now the REX contract is completed (5.1); M-5 stays OPEN | — | objective criteria / certification limited to exit mechanics |
| DEC-8.17b | **Halt stop policy (3.8, P8.6F8):** **Alternative 1 (recommended): admission contract** with caller-side reads at `L(W)` and `pre_save`. Residual: at most one write may open its transaction and commit after `T_h`; acceptance conditions (a)–(c). Or Alternative 2: an in-transaction barrier, which needs explicit authorization to modify the pinned `storage/database.py`. Plus the post-halt persistence policy. **Explicit acceptance of the residual is required** | **PENDING** | P8.6R8: the guarantee is corrected (to re-check) | DEC-8.17 | Alternative 1: simplest, no pinned change, residual of one write / Alternative 2: a smaller residual, pinned change |
| DEC-8.18 | Status-verification procedure (axes, H5) | **PENDING** | procedure approvable; classification needs a current copy and H5 | — | verified starting point / none |
| DEC-8.20 | **Continuity alternative: A1 / A2 / B. Recommended A2** with the 3.11.1 contract | **PENDING** | P8.6R6: **conceptual GO conditional** (no transition or operation); the Owner must explicitly accept the new period, new DB, 10000 initial equity, flat account, P1 archive and signed P2 freeze | DEC-8.18 | frozen economics preserved, segments optional / continuity with exclusions |
| DEC-8.19 | Boundary handling: none under A2 (flat precondition); the B rules otherwise | **PENDING** | blocked until DEC-8.20 | DEC-8.20 | — |
| DEC-8.21 | LOW-1 per section 4 | **PENDING** | ready | — | closes LOW-1 / open |
| DEC-8.21b | REX atomicity alternative (5.1.3): **recommended B with the B-STRICT evidence-gap rule (5.1.3.1: R-B1…R-B6 on the complete economic digest EDG; parameter: at most 2 NWR days per period; VERIFIED requires an Owner single-writer attestation, **OAR-G genesis (5.1.3.2)** and **chain-head anchors per UTC day with gaps ≤ 26 h (5.1.3.3)**; otherwise NOT VERIFIED; any account creation after `E0` → INVALID)**. Alternative B-QUARANTINE documented, not recommended | **PENDING** | P8.6R6: blocked until the B rule was corrected (now 5.1.3.1; to re-audit) | DEC-8.17 | B-STRICT: economically inert; any unevidenced write invalidates the whole period. A: complete evidence, a new failure mode |

DEC-8.21b is a sub-decision recorded under the existing numbering, so no new top-level ID is introduced.

### 8.2 Implementation authorizations (DEC-8.22, split into items; each PENDING and separate)

| Item | Scope | Requires (policy) | Additional condition |
|---|---|---|---|
| DEC-8.22-a | LOW-1 hardening | DEC-8.21 | adversarial tests |
| DEC-8.22-b | Per-symbol catch-up mechanism (technical only) | DEC-8.13, 8.14, 8.16 | no certification eligibility, no activation |
| DEC-8.22-c | Read-only state verifier | DEC-8.18 | Owner copies only |
| DEC-8.22-d | REX (5.1) | DEC-8.17, DEC-8.21b | a P8.6R6 re-audit of 5.1; **only in a new baseline** |
| DEC-8.22-e | R-HALT (3.8) | DEC-8.17 | a P8.6R6 re-audit; **only in a new baseline** |
| DEC-8.22-f | A2 preflight checks and archive tool (3.11.1) | DEC-8.20 = A2 | a P8.6R6 re-audit |
| DEC-8.22-g | Segment machinery (3.3 / 3.5) | DEC-8.20 = A1 or B | not needed under A2 |
| DEC-8.22-h | R-WIND (wind-down) | a separate policy decision, not requested now | **never inside a frozen period**; only with a pause and an approved new baseline |
| DEC-8.22-i | R-GEN-1: refuse account creation after `E0` (5.1.3.2) | DEC-8.21b | a runtime change; a new baseline only |
| DEC-8.22-j | Alternative 2 halt barrier (only if chosen under DEC-8.17b) | DEC-8.17b = Alternative 2 | **additional explicit authorization to modify the hash-pinned `storage/database.py`** |

Commits only on dedicated branches. No item authorizes operation.

### 8.3 Operational authorizations (each a separate, explicit Owner act; none requested now)

| Item | Act |
|---|---|
| OP-1 | Request an Owner-made copy and run the read-only verifier |
| OP-2 | Perform an EMERGENCY_HALT or an orderly halt (H1–H7) |
| OP-3 | Archive the P1 DB and sign OAR-A (A2) |
| OP-4 | Deploy `Y_P2` and start the P2 period |
| OP-5 | Activate any flag, Render change or deploy |
| OP-6 | Take read-only (backup-API) copies for the daily chain-head anchors (5.1.3.3) and sign them |
| OP-7 | Genesis preparation of a new period's DB (5.1.3.2 G1–G3), runner and scheduler OFF |

None of OP-1…OP-5 is authorized by any decision above.

### 8.4 Owner decisions recorded 2026-10-08 (supersede the PENDING status of these rows only)

| ID | Status |
|---|---|
| DEC-8.17 | **APPROVED POLICY:** the original §8.1 definition, i.e. the G-8.INT measurement policy (G1–G15, REX as the evidence source, 1.9.4 semantics certified as-is, halt triggers, acceptance of the post-halt residual). *(Corrected 2026-10-08: it was earlier mis-recorded as the emergency stop policy.)* |
| DEC-8.17b | **APPROVED POLICY:** Alternative 1 plus the post-halt persistence policy (3.8). With the 3.8 H1–H7 procedure, it carries the emergency-stop principles (stop new admissions, preserve evidence, review and authorization before resuming). The Owner accepts that an operation admitted before `T_h` may begin or commit after `T_h`; M-5 OPEN |
| DEC-8.13 | **APPROVED POLICY** (2026-10-08) |
| DEC-8.14 | **APPROVED POLICY** (2026-10-08): busy ≤ 500 ms and DEGRADED after 4 consecutive failures, subject to tests |
| DEC-8.15 | **APPROVED POLICY** (2026-10-08) |
| DEC-8.16 | **APPROVED POLICY** (2026-10-08) |
| DEC-8.21 | **APPROVED POLICY** (2026-10-08) |
| DEC-8.20 | **APPROVED POLICY:** A2 (a new USD 10,000 PAPER account), with a P1 archive, a new DB, no inherited positions or orders, an approved baseline and freeze, and a sealed genesis. No XAUUSD / EURUSD economic isolation |
| DEC-8.21b | **PROVISIONAL:** B-STRICT pending operational validation |
| Package plan | **APPROVED (planning only)** |

- DEC-8.18 and DEC-8.19 remain **PENDING**. DEC-8.22-a…j (including a / b) and OP-1…OP-7 remain **NOT AUTHORIZED**.
- Details are in `V2_PHASE8_P86_HANDOFF.md`.

## 9. GO / NO-GO per component (P8.6F8; every decision PENDING)

| Component | Verdict | Conditions |
|---|---|---|
| LOW-1 isolated | **GO, conditional** | DEC-8.21 + DEC-8.22-a; adversarial tests |
| Per-symbol catch-up, isolated | **GO, conditional, technical only** | DEC-8.13 / 8.14 / 8.16 + DEC-8.22-b; no certification eligibility, no operation |
| Read-only verifier | **GO, conditional** | DEC-8.18 + DEC-8.22-c; Owner copies only |
| REX / B-STRICT (5.1) | **NO-GO** | P8.6R9 acceptance of 5.1.3.2 / 5.1.3.3; DEC-8.17, DEC-8.21b, DEC-8.22-d / i; a new baseline only |
| R-HALT (3.8) | **NO-GO** | P8.6R9 acceptance of the corrected stop policy; DEC-8.17b (and DEC-8.22-j if Alternative 2); DEC-8.22-e; a new baseline only; **M-5 OPEN** |
| Segments | **NO-GO** | not needed under A2 |
| A2 conceptual | **GO, conditional (policy approval only)** | DEC-8.20; no transition, archive, genesis or operation |
| A2 archive, preflight, genesis | **NO-GO** | DEC-8.20 = A2, DEC-8.22-f, implementation and review; then OP-2 / OP-3 / OP-7 |
| Simulation | **NO-GO** | dependencies implemented and reviewed |
| Runtime activation | **NO-GO** | HIGH-8.1 OPEN; Phase 8 BLOCKED; Phase 9 not authorized |

## 10. Differences from the P8.6 design

| Topic | P8.6 | P8.6F |
|---|---|---|
| Resumability (H-1) | Checked only the current segment's rows | Every row validated against its own segment's record; NULL rows bound to S1; digests of sealed segments |
| Starting equity (H-1) | Segment opening equity used as "starting equity" | `run_metadata.starting_equity` keeps its meaning (10000); `opening_equity` and `closing_equity` are separate fields |
| Segment identity (H-2) | Implicit S1 via NULL; mutable `experiment_segment_current` key | Immutable records, hashes, a genesis built from the immutable start keys; the current segment derived from the chain |
| Chain verification (H-2) | Seal/start events without links | `seal_hash` → `previous_segment_hash`; `opening == closing` exactly; external anchor in the signed amendment |
| Tamper detection (H-2) | None | `history_digest` per sealed segment, chain recomputation, anchors; INCONSISTENT on mismatch |
| Freeze compatibility | "Amendment needed" | Original keys and `EXPERIMENT_FREEZE.md` immutable; per-segment amendments; chain-aware baseline SHA (future code) |
| 14-day counter | Owner decision only | Per-segment counter; no summing; `INCOMPLETE` segments |
| Mark for the seal (M-7) | "Last persisted mark", no bound | ≤ 20 minutes or flat; equals the stored equity; no override |
| `previous_decision` (M-1) | "Equals the effective decision before that row" | The literal decision of the immediately preceding row for the same anomaly (valid or not); remediation keeps the chain |
| EURUSD evidence (M-2) | Observe-only ingestion | Plus a contiguity check before joining; no backfill; a gap with an open position → STOP |
| Observe-only isolation (M-3) | "Failure → warning" | Exception handling, bounded busy timeout, no shared state, intended fail-closed on shared-file damage, DEGRADED signal, tests |
| Gate scope (M-4) | "All enabled symbols" | Exact: all anomalies, all time; materiality frozen at recording; `ANOMALY_FOR_DISABLED_SYMBOL` |
| Emergency rollback (M-5) | "Rollback = transition" | Two stages: immediate EMERGENCY_HALT, then a ROLLBACK transition with no waived preconditions; triggers listed |
| G-8.INT (M-6) | Qualitative, "N cycles" | G1–G13 with numeric thresholds |
| Experiment state | "Owner confirms" | Read-only classification procedure on a copy with five states and an action per state |
| Decisions | Recommendations | All marked PROPOSED, with dependencies and consequences |

---

## 11. (P8.6F2) Changes by finding

| Finding | Change | Sections |
|---|---|---|
| H-1 complete history mapping | Membership by `journal.id` (never by timestamps); rules M1–M10 for journal, runs, run_metadata, dependents, economic rows, notifications and segment events; PRE_START quarantine with a digest; economic pre-start rows → CONFLICT; missing metadata allowed only if non-economic; duplicate or conflicting segment records → CONFLICT; any unmapped row → INCONSISTENT; per-segment resumability keeps "every row matches"; invariants I-M1…I-M8; tests NM1…NM13 | 3.5 (replaced), 2.2 Q7 |
| H-2 deterministic integrity | SHA-256 with domain prefixes; canonical JSON with REAL as `float.hex`, TEXT as stored, BLOB as hex; fixed membership tables T1–T13 and an explicit unprotected list; per-table and segment digests; seal → start chain with byte-equal boundary values; non-circular PREPARE → SIGN → COMMIT → ANCHOR with a signed amendment over `proposal_hash`; crash recovery per step; idempotent COMMIT; `AI_FLOOR_SEGMENT_HASH` preflight; verification V1–V6 with explicit detection limits (no tamper-proof claim); tests NH1…NH11 | 3.3 (replaced), 3.4 (updated) |
| M-2 evidence freshness | Authoritative as-of; STRICT (around the clock) versus an approved-CALENDAR mode, with no invented calendar; expected bars; trailing gap; staleness; session-boundary rule (lookback not assumed); fail-closed expansion; I-E1…I-E4; NE1…NE7 | 1.7 (replaced) |
| M-5 emergency halt | An operator table for runner halted, pending orders, gate blocked, stale marks and failed rollback preconditions; HALT note in the repository, never in the DB; deadlock = safe state; I-R1…I-R3; NR1…NR5 | 3.8 (replaced) |
| Experiment status | Independent axes FS / EX / PD / RS plus INTEGRITY; STOPPED needs an attestation; PERIOD_ELAPSED ≠ ended or certified; RESULT_CERTIFIED only through an explicit signed record; ENDED removed | 2 (replaced) |
| G-8.INT | Counted-session-day definition (scheduler slots, 95 % COMPLETED, 0 ERROR, one segment, no halt); minimum-close extension 10 → 20 counted days, then NOT MET | 5 (G3–G5 rows plus rules) |
| Preserved | 1.1–1.6, 1.8, 3.1, 3.2, 3.6, 3.7, 3.9, 3.10, section 4 (validated journal metadata rules, unchanged), section 6 (except 6.2), section 10 | — |

Note: section 6.2 now reads "proceed only if FS ∈ {NOT_STARTED, STARTED}, EX = STOPPED (attested), INTEGRITY = OK".

## 12. Testable invariants (index)

| Area | Invariants | Negative / positive tests |
|---|---|---|
| History mapping | I-M1…I-M8 (3.5) | NM1…NM13 |
| Segment integrity | I-H1…I-H5 (3.3.7) | NH1…NH11 |
| Evidence completeness | I-E1…I-E4 (1.7.5) | NE1…NE7 |
| Emergency and rollback | I-R1…I-R3 (3.8) | NR1…NR5 |
| Revision journal (unchanged) | section 4.2–4.5 | section 4.6 |
| Per-symbol contract (unchanged) | section 1 | section B matrix |

## 13. Assumptions and items that still require independent validation (P8.6F3, with the P8.6R3 results)

| # | Item | P8.6R3 result | P8.6F3 treatment | Status |
|---|---|---|---|---|
| 1 | `RUN_STARTED` uniqueness per slot_key | VERIFIED for runtime writes; not DB-enforced | Verifier enforces I-M4 | closed in design; verifier test needed |
| 2 | Economic rows carry an existing `run_id` | VERIFIED for object writes; not DB-enforced | E6 / I-M7 (exactly one runs row; `runs.run_id` not UNIQUE) | closed in design |
| 3 | `ClosedTrade.run_id` semantics and close linkage | **CONTRADICTED** (originating run) | E3 / E4 / E5: close attribution by the unique `POSITION_CLOSED` (`source = trade_id`) | **corrected; to re-audit** |
| 4 | Adjacent segment-event ids | VERIFIED | I-M3, adjusted to the triple | closed in design |
| 5 | `runner` / `heartbeat` prove a stop | **CONTRADICTED** if used alone | H4 + H5 + I-R5 | **corrected; to re-audit** |
| 6 | Provider 5m lookback (default `outputsize` 500) | NOT VERIFIABLE | Operational unknown; accepted explicitly under DEC-8.14; measured in the simulation | open (operational) |
| 7 | Digest determinism (`float.hex`, SQLite REAL) | NOT VERIFIABLE | I-H5, I-H6, NH10, NH16 | open until implemented and tested |
| 8 | Signed-commit verification and Owner key registration | NOT VERIFIABLE | `allowed_signers` trust root, SSH-signed OAR and H5, two retention locations | open until the Owner sets up the key |
| 9 | Operational DB contents (PRE_START rows) | NOT VERIFIABLE | Needs a current Owner copy (DEC-8.18) | open |
| 10 | Design matches the complete P8.6R2 | NOT VERIFIABLE then | All reports now received; section 14 reconciliation | closed by reconciliation; to re-audit |
| 11 | Preserved PASS sections still accepted | NOT VERIFIABLE (design only) | Listed in section 16 for re-audit | open |
| 12 | `save_paper` encoding stability for historical rows | (new in P8.6F3) | I-H6 checked at PREPARE | open until implemented |
| 13 | Hosting supervisor restart behaviour | (P8.6R3 MEDIUM) | Not relied on: H1 makes every restart exit before DB access (I-R4) | closed in design; NR6 test |
| 14 | Which risk engine the runtime uses | (found in P8.6F4) | V1 path, verified in the code (0 grounding) | closed (grounding corrected) |
| 15 | Exact float reproduction of V1 sizing by an independent oracle | (P8.6F4) | Specified operation order (5.1.4); needs implementation tests | open |
| 16 | SQLite explicit-id insert and `sqlite_sequence` behaviour on the deployed SQLite version | (P8.6F4) | 3.3.3 checks; NH29–NH35 | open until tested |
| 17 | REX and R-HALT are implementable without economic change | (P8.6F4) | Observability-only design; NG7, NR14 | open until implemented and reviewed |
| 18 | A flat account is reachable in practice | (P8.6F4) | Opportunistic flat halt; bounded wait | operational, open |
| 19 | V1 fill-gate reproduction (float `real_risk` vs `equity × 0.01`) | (P8.6F5) | 5.1.4 step 3; NS7 / NS8 / NG11 | open until implemented |
| 20 | `psh` stability and REX size | (P8.6F5) | I-G5, R-36 | open until implemented |
| 21 | R-HALT checkpoints reachable without economic change | (P8.6F5) | CP1–CP9, NR15–NR19 | open until implemented |
| 22 | A2 filesystem checks (inode, nlink, symlink) on the hosting disk | (P8.6F5) | 3.11.1 item 5 | open until implemented and tested on the target |
| 23 | The oracle's literal transcription of `_final_status` / `paper_policy` / the fill gate stays in sync with the code | (P8.6F6) | rule versions plus module hashes in REX (5.1.2.1) | open until implemented |
| 24 | CPython atomicity of the reference store and read used by the gate, and handler timing around C calls | (P8.6F6) | I-R11, NR20; POSIX tests | open until implemented and tested |
| 25 | `psh_start` anchoring for the A2 period | (P8.6F6) | 5.1.3.1 detection 1 | open until implemented |
| 26 | EDG computation cost and determinism over the five tables | (P8.6F7) | I-G11, I-H5-style tests | open until implemented |
| 27 | Single-writer attestation and daily chain-head anchors are operationally feasible | (P8.6F7) | 5.1.3.1 item 3 | open (operational) |
| 28 | Every persistence site is guarded and classified (E / D / O / L / H / P / S) | (P8.6F7) | I-R14, NR26–NR29 | open until implemented |
| 29 | The Policy B pre-begin check is reachable at every economic call site without changing `storage/database.py` | (P8.6F7) | 3.8 | **superseded by item 33** (P8.6F8 `read2`) |
| 30 | The expected `edg_genesis` is recomputable from (`account_id`, 10000, encoder) and matches OAR-G | (P8.6F8) | I-G12, NG40 | open until implemented |
| 31 | The genesis preparation (OP-7) can run with the runner and scheduler OFF without any cycle | (P8.6F8) | 5.1.3.2 G1 | open until implemented and tested |
| 32 | Daily anchor copies are operationally feasible within the 26 h bound | (P8.6F8) | 5.1.3.3 | open (operational) |
| 33 | `read2` placement immediately before every `save_paper` call site | (P8.6F8) | I-R13, NR25 / NR30 | open until implemented |
| 34 | Sealed mode removes every account-creation path; the gate runs before any economic write | (P8.6F9) | I-G17 / I-G18, NG47–NG53 | open until implemented |
| 35 | OAR-G verification inside the runtime (signature, file identity, baseline / freeze) | (P8.6F9) | R-GEN-1 gate step 1 | open until implemented |
| 36 | Chain-head lookup for the restart check after `E0` | (P8.6F9) | R-GEN-1 gate step 4 | open until implemented |
| 37 | Startup function (steps 1–4) runs before any `Store` open on every normal entry point | (P8.6F10) | R-GEN-1b, I-G21, NG54 / NG60 | open until implemented |
| 38 | Account-creation branch removed from the runtime; the inventory test | (P8.6F10) | R-GEN-1a / 1e, I-G20, NG55 | open until implemented |
| 39 | Genesis tool: authorization verification and create-exclusive | (P8.6F10) | R-GEN-1c, I-G22, NG61 | open until implemented |
| 40 | Read-only, non-creating preflights | (P8.6F10) | R-GEN-1d | open until implemented |
| 41 | Test-fixture migration away from implicit account creation | (P8.6F10) | implementation impact | open until implemented |

## 14. (P8.6F3) Traceability: every audit finding

### 14.1 P8.6R3

| # | P8.6R3 finding | Severity | Evidence | Correction (section) | Status |
|---|---|---|---|---|---|
| R3-1 | H-1: closes attributed to the opening run; `review_reports` updated after a seal; `runs.run_id` not unique | HIGH | `trade_manager.py:74,81`; `database.py:238,457-468`; `runs` schema | E1–E7, `origin_run_id` versus `event_segment`, T7 removed, T14 snapshot, E6 uniqueness (3.3.3, 3.5) | ADDRESSED IN DESIGN; re-audit |
| R3-2 | H-2: `proposal_hash` self-referential; digest changes with later closes; signed commits are not an independent anchor | HIGH | Design 3.3.4–3.3.5 (P8.6F2) | Self-hash rule and DAG (3.3.1, 3.3.4); stability argument plus I-H6 (3.3.7); OAR with two retention locations (3.3.5) | ADDRESSED IN DESIGN; re-audit |
| R3-3 | Out-of-scope EURUSD contaminates XAUUSD decisions through shared equity | HIGH | `risk_engine_v2` basis, drawdown and aggregate limit (**P8.6F4: evidence corrected; the runtime uses V1 sizing on marked equity, 1.9.1; the conclusion stands**) | 1.9 comparison of A and B; certification policy; G1 and G14; DEC-8.16 / 8.17 | ADDRESSED IN DESIGN; Owner decision PENDING |
| R3-4 | M-2 PASS (fail-closed); CALENDAR must stay unavailable; lookback unknown | MEDIUM (lookback) | `outputsize` 500 default | DEC-8.14 explicit acceptance; item 13.6 | PASS; operational unknown remains |
| R3-5 | M-5 FAIL: SIGTERM does not interrupt a tick; supervisor restart; no termination proof | MEDIUM / FAIL | `cloud_runner.py:20-56`, `scheduler.py` | 3.8 H1–H7, I-R4…I-R6, NR6–NR10; R-HALT (P8.6F5) | **OPEN, partial mitigation** (corrected in P8.6F5; see R4-7, R5-5) |
| R3-6 | EX = STOPPED needs signer identity, timestamp, scope and freshness | — | — | 2.3 EX, 3.8 H5, I-R5 | ADDRESSED IN DESIGN |
| R3-7 | Anchor trust boundary | MEDIUM | — | OAR, `allowed_signers`, key rotation (3.3.5) | ADDRESSED IN DESIGN |
| R3-8 | Section 13 items 1–11 | — | as listed | Section 13 table | see section 13 |
| R3-9 | Decision readiness | — | — | Section 8 (all PENDING) | PENDING |
| R3-10 | GO/NO-GO A–F | — | — | Section 9 aligned (LOW-1 GO conditional, catch-up NO-GO until re-audit, verifier GO conditional, D/E/F NO-GO) | aligned |

### 14.2 Earlier reports (reconciled)

| Finding | Report | Final treatment | Status |
|---|---|---|---|
| LOW-1 forged unknown decision | P8.5R2 | Section 4 (DEC-8.21) | design PASS (P8.6R2); implementation pending |
| LOW-2 non-material unreviewed = warning | P8.5R2 | Documented semantics | closed (accepted) |
| H-1 / H-2 (resumability, chain) | P8.6R | Superseded by the P8.6R2 and P8.6R3 versions above | see R3-1, R3-2 |
| M-1 `previous_decision` | P8.6R, P8.6R2 | Section 4.1 | PASS |
| M-2 evidence completeness | P8.6R, P8.6R2, P8.6R3 | 1.7 | PASS (R3) |
| M-3 observe-only isolation | P8.6R, P8.6R2 | 1.5 test wording corrected (decisions and economics, not the whole DB) | PASS with condition; condition applied |
| M-4 global gate breadth | P8.6R, P8.6R2 | Explicit in DEC-8.15 | PASS; made explicit |
| M-5 halt / rollback | P8.6R, P8.6R2, P8.6R3 | 3.8 | see R3-5 |
| M-6 G-8.INT | P8.6R, P8.6R2 | Section 5 counted days and extension (F2); G1 / G14 (F3); exclusion needs a freeze decision | PASS with clarification; re-audit G1/G14 |
| M-7 stale mark | P8.6R, P8.6R2 | 3.6 recorded fields and boundary / time-zone tests | PASS; tests listed |
| Experiment-state model | P8.6R, P8.6R2, P8.6R3 | Section 2 axes; H5 attestation | improved (R3); attestation spec added |
| `EXPERIMENT_ENDED` event (P8.6R recommendation) | P8.6R vs P8.6R2 | Replaced by PD/RS axes and a `CLOSE` seal kind | P8.6R2 / P8.6R3 accept the axes; an end marker remains future work (R-11) |
| Freeze vs continuous equity | P8.6R | DEC-8.20, explicit options (i) and (ii) | Owner decision PENDING |
| DEC-8.16 vs freeze | P8.6R | Freeze amendment before implementation (DEC-8.20 dependency) | PENDING |
| L-1 unset vs empty | P8.6R | Matrix C2 / C3 | closed |
| L-2 `--catch-up-symbols` replaces `--first-scope` | P8.6R | 1.4 wording restored | closed |
| L-3 placeholder decisions | P8.6R | Section 8 | closed |
| L-4 local runner without `flock` | P8.6R | R-13 rule | accepted risk |

### 14.3 Contradictions resolved in this revision

- **"Map economic rows by `run_id`"** (P8.6F2 M5/M6) contradicted the code (`ClosedTrade.run_id` = the opening run).
  Now: event-based attribution, with `run_id` kept as origin identity.
- **"`review_reports` is history"** (P8.6F2 T7) contradicted the code (rewritten on every save). Now: a live view,
  excluded, with a frozen T14 snapshot.
- **"Signed commit = anchor"** (P8.6F2) contradicted P8.6R2 and P8.6R3. Now: an Owner-retained, SSH-signed OAR.
- **"XAUUSD certifiable in a mixed segment"** (P8.6F, F2 DEC-8.16) contradicted P8.6R3. Now: `TECHNICAL_ONLY`.

### 14.4 (P8.6F4) P8.6R4 traceability

| # | P8.6R4 finding | Severity | Evidence | Correction (section) | Status after P8.6F4 |
|---|---|---|---|---|---|
| R4-1 | H-1: closing-event attribution | PASS in design | — | Unchanged; M table restored (3.5) | **RESOLVED in design**; implementation and tests pending |
| R4-2 | H-2: T14 full-row coverage not specified; T1 ends before T14 | FAIL / MEDIUM | — | 3.3.3: T14 = the full 8-column row; partition of ids; SEAL / START covered by the next T1, plus the fixed envelope (V2) | **ADDRESSED IN DESIGN**; re-audit |
| R4-3 | `hwm + 1/2/3` assumes MAX + 1; `sqlite_sequence` | MEDIUM | `journal` AUTOINCREMENT | 3.3.3 explicit ids; `seq = MAX` checks; I-H7…H10; NH29–NH35 | **ADDRESSED IN DESIGN**; re-audit |
| R4-4 | G14: `risk_decisions` insufficient; no row when no decision | HIGH | `database.py:176-178` | 5.1 REX contract (V1 path), coverage including not-evaluated runs, oracle procedure; **grounding corrected: runtime = V1** | **ADDRESSED IN DESIGN**; needs a runtime addition, so **OPEN for certification** |
| R4-5 | Multi-symbol sequential semantics not reproducible | MEDIUM | `scheduler.py:37-54`, `service.py` | 1.9.4 documented and unchanged; G15 replay (5.1.5) | **ADDRESSED IN DESIGN**; depends on REX |
| R4-6 | DEC-8.20: inherited equity and positions vs flat; catch-up closes on pre-segment bars | MEDIUM | — | 3.11 comparison of A1 / A2 / B; `PRE_SEGMENT_BAR_CLOSE`; recommendation A2 | **OPEN, Owner decision** (DEC-8.19 / 8.20 PENDING) |
| R4-7 | M-5: the graceful halt completes the whole tick | FAIL / MEDIUM | `cloud_runner.py` | 3.8: exact post-halt activity table, operational mitigation, R-HALT-1…5 future requirements | **OPEN (partial mitigation)**; not closed |
| R4-8 | M-2: PASS with an operational limitation | PASS | — | Explicit acceptance in DEC-8.14 | **RESOLVED in design**, given the acceptance |
| R4-9 | Decision readiness | — | — | Section 8 updated; all PENDING | PENDING |
| R4-10 | GO / NO-GO | — | — | Section 9 aligned (catch-up GO conditional for technical implementation only) | aligned |
| R4-11 | Not verifiable (NM / NH / NR tests not run; SQLite determinism; encoder; `allowed_signers`; H5; control plane) | — | — | Section 13 items 6–9, 12, 15–18 | **NOT VERIFIABLE** by a design audit |

**Status summary after P8.6F4**
- **Resolved in design (pending implementation):** H-1; M-2 (with acceptance).
- **Addressed in design, to re-audit:** H-2 (T14 and identifiers); G14 / G15 design (5.1); the multi-symbol semantics.
- **Open:** G14 / G15 for certification (needs the REX runtime addition); M-5 (partial mitigation until R-HALT);
  continuity (Owner decision); HIGH-8.1.
- **Not verifiable:** listed in R4-11.

### 14.5 (P8.6F5) P8.6R5 traceability

| # | P8.6R5 finding | Severity | Code evidence (verified) | Correction (section) | Status after P8.6F5 |
|---|---|---|---|---|---|
| R5-1 | The V1 fill gate couples equity to fill admission | HIGH | `paper_broker.py:137-200` (`policy None`: `real_risk > current_equity × 0.01` or `> equity_at_submission × 0.01`); `service.py:129` | 0 grounding; 1.9.1 (claim withdrawn); 1.9.4 fill timing; G14 includes the fill gate; I-S5, NS7 / NS8; R-21 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED** (depends on REX) |
| R5-2 | REX lacks the AI statuses, `ai_healthy`, `paper_policy` inputs and the broker inputs | HIGH | `gates.py:42-53`; `service.py:426-457`; `paper_broker.py:47-76` | 5.1.2: four layers; all five AI statuses; `ai_healthy` inputs; eligibility clauses; fill-gate inputs; `submit_plan` refusal conditions; not-evaluated paths | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED** |
| R5-3 | Atomicity vs observability contradiction | HIGH | §5.1.2 / NG7 (P8.6F4) | 5.1.3: A vs B comparison; the P8.6F4 text withdrawn; recommend B; DEC-8.21b | **DESIGN RESOLVED, pending Owner choice**; implementation not verified |
| R5-4 | `paper_state` is not a hash; stages undefined | MEDIUM | `database.py:410-420` | 5.1.1: `psh` defined over the tuple; stages ST0–ST10 with per-write before/after; the G15 stage replay (5.1.5–5.1.6) | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED** |
| R5-5 | R-HALT incomplete; I-R1 contradiction; recovery | MEDIUM | `position_catch_up.py:102-109`; `cloud_runner.py` | 3.8: `T_h`, the prohibition point, CP1–CP9 (including per-bar catch-up and management), non-economic bookkeeping, verifier-level `HALT_INTERRUPTED`, I-R1 restated, I-R7…I-R10, NR15–NR19 | **M-5 OPEN** (design improved; implementation and independent tests required) |
| R5-6 | A2 identity, archive and anchor | MEDIUM | `cloud_runner.py:15`; `config.py:87` (`db_path` not in the fingerprint) | 3.11.1: `X_P2` / `Y_P2`, `EXPERIMENT_FREEZE_P2.md`, the pinned DB path, the separate evidence store, a backup-API copy, OAR-A, alias / hardlink / symlink checks, the flat proof, start conditions | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED**; A2 stays **NO-GO** |
| R5-7 | Contradictory traceability (R3-5, R-21) | MEDIUM | — | R3-5 → OPEN partial; R-21 updated | **RESOLVED** (documentation) |
| R5-8 | DEC-8.16 ↔ DEC-8.17 cycle; levels mixed; R-WIND under freeze | MEDIUM | — | 8.1 acyclic policies; 8.2 implementation items; 8.3 operational acts; R-WIND only with a new baseline; DEC-8.21b | **RESOLVED** (governance documentation) |
| R5-9 | H-1 PASS in design | — | — | not reopened | **DESIGN PASS / IMPLEMENTATION NOT VERIFIED** |
| R5-10 | H-2 PASS in design | — | — | not reopened | **DESIGN PASS / IMPLEMENTATION NOT VERIFIED** |
| R5-11 | M-2 PASS conditional | — | — | DEC-8.14 acceptance | conditional on the Owner acceptance |
| R5-12 | G14 / G15 not certifiable | — | — | 5.1 completed | **NOT CERTIFIABLE** until REX is implemented and independently reviewed |
| R5-13 | HIGH-8.1 | — | — | — | **OPEN** |

### 14.6 (P8.6F6) P8.6R6 traceability

| # | P8.6R6 finding | Severity | Code evidence (verified) | Correction (section) | Status after P8.6F6 |
|---|---|---|---|---|---|
| R6-1 | REX cannot reproduce the AI decisions (recommendations, base status); pending-order inputs only hashed | HIGH | `ai/orchestrator.py:40-51,54-71`; `floor/orchestrator.py:14-46`; `ai/contracts.py`; `execution/contracts.py:35-56` | 5.1.2.1 (F1, A1–A4, P1, H1; all response fields; rule versions; missing-value rules); 5.1.2.2 (full order values and clause trace); 5.1.4 steps 2 and 4; NG15–NG23 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED** |
| R6-2 | Option B lets a UEW hide in an excluded day and contaminate later days | HIGH | §5.1.3 / 5.1.5 (P8.6F5) | 5.1.3.1 B-STRICT (R-B1…R-B6); whole-period G15 chain; event and run coverage; B-QUARANTINE assessed; I-G6…I-G9; NG24–NG30; DEC-8.21b updated | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED**; Owner choice PENDING |
| R6-3 | R-HALT check-then-act race | MEDIUM | `cloud_runner.py:36-42`; §3.8 (P8.6F5) | 3.8: `T_sig`, `T_h` and `T_ack` distinguished; the gate with a single-read linearization point `L(W)` as the write start; coverage of 8 operation classes; no locks, no deadlock; race tests NR20–NR23 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED**; **M-5 OPEN** |
| R6-4 | I-R1 contradiction | MEDIUM | §3.8 | I-R1 replaced by I-R1a (operator), I-R1b (economic), I-R1c (enumerated bookkeeping), I-R1d (recovery); the old wording withdrawn | **RESOLVED** (documentation) |

**Status of earlier items (unchanged by P8.6F6):**
- H-1 and H-2: DESIGN PASS / IMPLEMENTATION NOT VERIFIED.
- R5-1: resolved in analysis; its reproduction is now covered by 5.1.2.2.
- R5-2: now with R6-1.
- R5-3: now with R6-2.
- R5-4: partial in design; validation pending.
- R5-5: now with R6-3 and R6-4; M-5 OPEN.
- R5-6: A2 concept GO conditional; implementation NO-GO.
- R5-7 and R5-8: resolved.
- **HIGH-8.1: OPEN.**

### 14.7 (P8.6F7) P8.6R7 traceability

| # | P8.6R7 finding | Severity | Code evidence (verified) | Correction (section) | Status after P8.6F7 |
|---|---|---|---|---|---|
| R7-1 | B-STRICT relied on `psh`, which omits CLOSED `paper_positions` rows; snapshots cannot detect reverted alterations | HIGH | `database.py:410-420,437-449,481-492`; `service.py:89-110` (startup reconciliation) | 5.1.3.1 replaced: six notions separated; EDG over all rows of five tables; the startup reconciliation covered; an explicit trust root; a provable / external / not provable table; INVALID vs NOT VERIFIED vs VERIFIED; the limitation stated; I-G10 / I-G11; G15 row | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED**; the residual limit (reverted alterations, a consistent rewrite between anchors) is **stated, not eliminated**; DEC-8.21b **OWNER DECISION PENDING** |
| R7-2 | The admission boundary does not prevent physical effects after `T_h` | MEDIUM | `cloud_runner.py:36-42`; the 3.8 design | 3.8: interval table `T_sig` / `T_h` / `T_stop` / `T_ack`; Policies A and B; Policy B recommended with a pre-begin check at the caller (no change to the pinned `storage/database.py`); the wording rule (no "zero effect" claim); I-R13; NR24 / NR25 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED**; DEC-8.17b **OWNER DECISION PENDING**; **M-5 OPEN** |
| R7-3 | The quantity adapter is missing from the reproduction; AI response identity is not required | MEDIUM | `paper_contracts.py:19-52`; `service.py:368-371`; `ai/runtime.py:15-127`; `ai/contracts.py:91-125` | 5.1.2.3: F1b adapter stage (8 rules, Decimal semantics, outcomes, module hashes); per-response and per-request identity, validation, `substituted`; V-P1…V-P3; 5.1.4 step 2 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED** |
| R7-4 | I-R1c does not match the real post-signal persistence (reports, events, audit, provider state, heartbeat / scheduler) | MEDIUM | `service.py:332-425,505-520,121-123`; `database.py:282-285`; `demo_runner.py:165-168` | 3.8: the post-halt persistence policy by category; every real site listed; the allowlist is H plus P (`heartbeat`, `scheduler`, `runner`); D / O / S suppressed; AI finishes in memory only; I-R1c and I-R14; NR26–NR29 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED** |

**Status of earlier items:**
- R6-1: now with R7-3.
- R6-2: now with R7-1, with the limit stated.
- R6-3: now with R7-2.
- R6-4: now with R7-4.
- H-1 and H-2: DESIGN PASS / IMPLEMENTATION NOT VERIFIED.
- **M-5: OPEN.**
- **HIGH-8.1: OPEN.**

### 14.8 (P8.6F8) P8.6R8 traceability

| # | P8.6R8 finding | Severity | Code evidence (verified) | Correction | Status after P8.6F8 |
|---|---|---|---|---|---|
| R8-1 | REX coverage at bootstrap has no temporal boundary; account missing during a period | HIGH | `service.py:90-104` (creation conditions, no journal event); `demo_runner.py:69-90` (runtime before the experiment start) | 5.1.3.2: genesis G0–G5, OAR-G, `edg_start = edg_genesis`, GEN-1…GEN-5 (no reconstruction exception), R-GEN-1 as a future requirement; 5.1.3.1 items 2 and 3; 3.8 recovery and I-R1d; I-G12…I-G14; NG37–NG41 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED** |
| R8-2 | Policy B overclaimed: a transaction can open after `T_h` | MEDIUM | `database.py:118-125,422-430` (`BEGIN` inside `save_paper`) | 3.8: the P8.6F7 claims withdrawn; Alternative 1 (caller reads, residual of one write possibly opening after `T_h`) vs Alternative 2 (in-transaction barrier, needs a pinned-module authorization); recommend 1; accurate wording; I-R1b / I-R13; NR25 / NR30 / NR31; DEC-8.17b, DEC-8.22-j | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED**; DEC-8.17b **OWNER DECISION PENDING**; **M-5 OPEN** |
| R8-3 | Anchor cadence tied to counted days | MEDIUM | §5.1.3.1 (P8.6F7) | 5.1.3.3: per UTC day regardless of counting, gaps ≤ 26 h, initial (OAR-G) and final (OAR-A) anchors, hash-chained content, signing and custody, outage → absent or late → NOT VERIFIED, inconsistency → INVALID; the limit restated; I-G15 / I-G16; NG42–NG46 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED**; DEC-8.21b **OWNER DECISION PENDING** |
| R8-LOW | G14 "AI reproduction" scope | LOW | §5.1.2.3 / 5.1.4 | 5.1.4 scope paragraph: a deterministic rule replay over persisted, validated data; not provider inference | **RESOLVED** (documentation) |

**Concrete dependencies edited in accepted areas:**
- EDG (5.1.3.1, items 2, 3 and 5), for R8-1 and R8-3;
- the post-halt policy's recovery paragraph and I-R1d, for R8-1.

Nothing else in H-1, H-2, F1b, the AI provenance, the persistence policy, the V1 gate, A2, freeze or governance was
changed.

### 14.9 (P8.6F9) P8.6R9 traceability

| # | P8.6R9 finding | Severity | Code evidence (verified) | Correction | Status after P8.6F9 |
|---|---|---|---|---|---|
| R9-1 | GEN-2 overclaimed detection in the OAR-G → `E0` window; R-GEN-1 only covered `E0` and later | HIGH | `service.py:90-104` (creation without a journal event); `demo_runner.py:69-90` (runtime before the start); `cloud_runner.py` (`flock` before `DemoRunner`); `database.py:496` (`RECOVERY_STARTED` per construction) | 5.1.3.2: sealed mode by external configuration (OAR-G); no creation path after sealing; the sealed-genesis gate inside the constructor, before any economic write, under the `flock`; the expected value before and after `E0`; the failure policy; `GENESIS_SEAL_CHECK` evidence per process start; the overclaim withdrawn and the restoration limit stated; I-G17…I-G19; NG47–NG53 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED** |
| R9-2 | R-HALT terminology ("Policy B", "pre-begin") | LOW | `database.py:422-430` | 3.8: unified on Alternative 1 / 2 and "second check before invoking `save_paper`"; historical terms labelled; the residual kept explicit | **RESOLVED** (documentation) |

**Historical terminology note:** rows R7-2 and R8-2 (14.7 / 14.8), item 29 (13) and NR24 keep their original wording
as history. The current terms are Alternative 1 / 2 and the second check before invoking `save_paper`.

### 14.10 (P8.6F10) P8.6R10 traceability

| # | P8.6R10 finding | Severity | Code evidence (verified) | Correction | Status after P8.6F10 |
|---|---|---|---|---|---|
| R10-1 (R9-1 residue) | No unconditional fail-closed when the sealed configuration is missing; the preflight runs after the constructor; `cloud_preflight` checks no genesis configuration | HIGH | `service.py:90-104`; `demo_runner.py:69-83`; `cloud.py:73-76`; `database.py:65-80` (`Store` creates a missing DB) | 5.1.3.2: start-mode contract (GENESIS_PREPARATION via a separate signed-authorization tool, create-exclusive; SEALED_RUNTIME hard-wired, no creation branch); the default is refusal; startup order with configuration and identity verification **before any DB open**; start-mode failure policy; R-GEN-1a…e; I-G20…I-G22; NG54–NG61 | **DESIGN RESOLVED / IMPLEMENTATION NOT VERIFIED** |
| R10-obs | `recover()` writes non-economic rows before the identity check | not blocking | `database.py:494-510` | Resolved by step 4: identity is verified before any DB open | **DESIGN RESOLVED** |
| R9-2 | R-HALT terminology | — | — | accepted by P8.6R10 | RESOLVED |

## 15. (P8.6F8) Invariants and negative tests index

| Area | Invariants | Tests |
|---|---|---|
| History mapping | I-M1…I-M12 | NM1…NM21 |
| Segment integrity and identifiers | I-H1…I-H10 | NH1…NH35 |
| Evidence completeness | I-E1…I-E4 | NE1…NE7 |
| Shared equity and certification | I-S1…I-S5 | NS1…NS8 |
| REX, G14 / G15, EDG, genesis, sealed genesis, start modes, anchors | I-G1…I-G22 | NG1…NG61, NG-A1…A3, NG-B1…B4 |
| Continuity and A2 | I-C1…I-C8 | NC1…NC12 |
| Halt | I-R1a…I-R1d, I-R2…I-R14 | NR1…NR31 |
| Mark age | 3.6 | boundary, DST and non-UTC tests |
| Revision journal (unchanged) | section 4.2–4.5 | section 4.6 |
| Per-symbol contract (unchanged) | section 1 | section B matrix |

(NG31–NG36 are as in P8.6F7; NG37–NG46 are in 5.1.3.2 and 5.1.3.3.)

**NG31–NG36 (P8.6F7, retained):**
- **NG31:** a CLOSED `paper_positions` row edited directly → the EDG chain breaks → INVALID.
- **NG32:** an account creation by `__init__` after `E0` → INVALID (P8.6F8: never legitimized by a REX).
- **NG33:** a missing chain-head anchor → NOT VERIFIED.
- **NG34:** a row altered and restored between anchors → not detected (documents the limit).
- **NG35:** the adapter turns APPROVED into `RISK_REJECTED` → F1b and A4 reproduced.
- **NG36:** cross-provenance responses → V-P1 / V-P2 FAIL.

## 16. (P8.6F10) Readiness for P8.6R11

**For the auditor:**
- Compare the start-mode contract, startup order and failure policy (5.1.3.2, P8.6F10) with `runtime/service.py`
  84–110, `runtime/demo_runner.py` 69–90, `runtime/cloud_runner.py`, `runtime/cloud.py` 73–76 and
  `storage/database.py` 65–113.
- Confirm four things:
  1. no normal entry point can create an account in the specified design;
  2. a missing or invalid sealed configuration leads to refusal **before any DB open**;
  3. the preflight is an additional barrier, not the only one;
  4. the current code is described as **not** satisfying the contract (future requirements R-GEN-1a…e).

**Only demonstrable through implementation and independent tests:**
- R-GEN-1a…e and NG54–NG61;
- every item listed in the P8.6F9 readiness section (sealed gate, OAR-G verification, chain head, REX / EDG / `psh`,
  crash detection, `read2`, post-halt suppression, POSIX races, anchors, A2 checks, OP-6 / OP-7, single-writer
  attestation).

**Documentation closure.** If P8.6R11 accepts R10-1, no documentation finding remains open. P8.6 is not certified by
the author.

**Status:** Phase 8 BLOCKED; HIGH-8.1 OPEN; M-5 OPEN; DEC-8.17, DEC-8.17b, DEC-8.20 and DEC-8.21b PENDING;
DEC-8.22 and OP-1…OP-7 not authorized; Phase 9 not authorized.
