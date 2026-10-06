# V2 Phase 5 — Risk Engine V2 (P5.0 architecture, audit & owner-decision package)

Status: **P5.0 — READY FOR OWNER DECISIONS.** Audit/design only; no production behavior changed.
Base: `main` @ `3edb64804cfc4b1432dbc79aa7ed3d810be4e558` (Phase 4 merged). Branch `v2/phase5-risk-engine`.
PAPER only · REAL DISABLED · NAS100 OFF (default; see finding L3) · runtime activation NOT AUTHORIZED · Phase 4
runtime OFF · System Health INACTIVE · trading DB schema 3 · no deploy · Phase 6 not started.

## B. Current risk authority map

| Authority | File / function | Reads | Writes | Decision | Kind | V1/V2 | Tests |
|---|---|---|---|---|---|---|---|
| Risk sizing & plan validation | `riesgo.evaluar_trade_plan` | plan, equity, instrument, config | — | APPROVED/REJECTED; quantity; capital_at_risk; risk_fraction | AUTHORITATIVE | both (policy by config) | test_risk_v2, test_riesgo, Phase 4 suites, P5 characterization |
| Risk config | `riesgo.crear_configuracion_riesgo_v2/_fixed_3r` | — | — | 1% per trade, 100% notional cap, R:R policy | AUTHORITATIVE (code defaults; not env/RuntimeConfig) | both | P5 characterization |
| PAPER quantity rounding | `runtime/paper_contracts.apply_paper_quantity_increment` | risk decision, increment | report | floor to XAU 0.001 / EUR 1.0; can reject `paper_quantity_below_increment`; never increases | AUTHORITATIVE | both | test_paper_contracts |
| Equity basis | `TradeManager.process_bar` | positions, bar | `equity = starting + realized + unrealized` (marked at last close); `cash` never updated | sizing basis | AUTHORITATIVE state | both | P5 characterization |
| Submission gate | `PaperBroker.submit_plan` + `runtime/service` eligibility | floor report, open positions/orders | order | refuses same-symbol open position; runtime refuses if a PENDING order or position exists for the symbol | AUTHORITATIVE | both | test_execution, test_runtime |
| B2.3A stale-state guard | `runtime/service._guarded_paper_write`, `Store.save_paper(expected_state)` | whole PAPER state | PAPER state | STALE → retry once / fail closed; submit re-checks eligibility and `equity == decision_equity` | AUTHORITATIVE | runtime | test_stale_safe_writers |
| Fill gate | `PaperBroker.process_next_bar` | order, bar, equity | order/position | V1 strict R:R ≥ 3; V2 F3 R:R ≥ 2.50 (DEC-4.7); **money at risk ≤ 1% of current and of submission equity (hard-coded 0.01)** | AUTHORITATIVE | both | test_execution, Phase 4, P5 characterization |
| AI | `ai/provider` (deterministic), trade/setup reviewers | plan summary | — | REJECT/CAUTION → `AI_CAUTION` (no order) | ADVISORY veto; cannot change levels/size | both | AI suites |
| Freshness / session / AI health | `runtime/service` gates | snapshot, clock | — | block cycle / pending progress / submission | AUTHORITATIVE gates | runtime | test_runtime |
| Macro high-impact | `agents/setup_validator` | macro report | — | WATCH (no plan) | AUTHORITATIVE (setup) | both | setup suites |

Not present in production: drawdown, daily/session loss, high-water mark, kill switch, aggregate or per-symbol risk
limits, correlation, position-count limits beyond one-per-symbol (only legacy research modules mention drawdown).

## C. F05 gap matrix

| Task | Current | Gap | Owner decision |
|---|---|---|---|
| T01 maintain protections | per-trade 1%, notional cap, fill gates, one-per-symbol, B2.3A guard | none (characterized) | — |
| T02 individual risk | 1% ceiling; notional cap often binds (EUR) | effective risk varies 0.30–1.00% | DEC-5.1 |
| T03 aggregate risk | none | MISSING | DEC-5.2 |
| T04 exposure per symbol | one open position or pending order per symbol (count) | no risk/notional metric | DEC-5.3 (only if >1 per symbol in Phase 6) |
| T05 total exposure | none | MISSING | DEC-5.2 |
| T06 correlation | none; evidence offline only | DESIGN REQUIRED | DEC-5.6 |
| T07 multiple positions | max one per symbol, ≤ 2 symbols → ≤ 2 open + pending | PARTIAL | DEC-5.4 |
| T08 drawdown | none | MISSING | DEC-5.5 |
| T09 PAPER limits | listed in §J/§K below | PARTIAL | DEC-5.5 |
| T10 sizing | consistent units; fill can raise money risk; cap hard-coded | PARTIAL | DEC-5.7 |
| T11 invalid SL/TP | covered across Setup/Planner/Risk/Broker | CURRENT | — |
| T12 abnormal market | data integrity + session + macro only | PARTIAL / INSUFFICIENT for volatility & spread | DEC-5.8 |

## D. Individual risk & sizing

`quantity = min(E × r / (|entry − SL| × m), E × c / (entry × m))`, `E` = current equity (`starting + realized +
unrealized`), `r` = 1%, `c` = 100% (no leverage), `m` = contract multiplier (1.0 PAPER units, USD per unit). Then
PAPER floor to the increment (never increases). `capital_at_risk = quantity × |entry − SL| × m`. Invalid equity (0,
negative, NaN, ±Inf, bool) → `invalid_numeric_value`. Units: USD price distance × units; no pip conversion.

Worked examples (E = 10,000): EUR LONG 1.08500/1.07800 → notional-bound 9,216.59 → PAPER 9,216 → risk 64.51 (0.65%);
EUR SHORT 1.08500/1.09600 → risk-bound 9,090.91 → 9,090 → 99.99 (1.00%); XAU LONG 2650/2600 → risk-bound 2.0 → 100
(1.00%); XAU SHORT 2650/2660 → notional-bound 3.7736 → 3.773 → 37.73 (0.38%). Tiny SL → notional cap (risk ≈ 0);
wide SL → risk-bound 1%.

Replay evidence (1,940 fixed-3R plans, production sizing): XAU 958/991 risk-bound (median SL 2.86% of price);
EUR 211/949 risk-bound (median SL 0.63%). Approved money risk: p05 0.30% · median 1.00% · max 1.00%.

**Money at risk at the fill.** Adverse displacement raises money at risk proportionally (risk per unit grows from
`|entry − SL|` to `|fill − SL|`, quantity fixed). For risk-bound plans any adverse fill exceeds 1%, and the broker's
hard-coded 1% cap rejects it (`post_fill_risk_or_geometry`). On the same 1,940 plans with the runtime NEXT_CYCLE fill
and DEC-4.7: 1,910 pass the R:R floor, but **523 (27.4%) are then rejected by the money cap** (XAU 421, EUR 102);
1,387 fill. Fill money risk: median 0.97%, p95 1.05%, max 1.14% (before the cap). The DEC-4.7 replay (1.55%) did
not model this existing gate. The cap is independent of the Risk configuration: with Risk at 0.5%, a fill carrying
0.51% passes (cap 1%).

## E. Portfolio / aggregate risk

Available to a Risk V2 (all in the trading DB, schema 3): open positions (symbol, side, quantity, entry, SL, TP,
last price, multiplier — AUTHORITATIVE), pending orders (symbol, side, quantity, planned entry, SL, TP — AUTHORITATIVE),
closed trades and realized PnL (AUTHORITATIVE), unrealized PnL/equity (DERIVABLE, marked at last processed close),
run_id/order_id/position_id (AUTHORITATIVE), setup_id (in the review/execution record — AVAILABLE, not on the
order), session (DERIVABLE from timestamps), policy version (on the plan, not on the order — AMBIGUOUS for pending
orders created by different policies). Today Risk receives only equity: no portfolio input.

Mathematics (repository units): `risk_to_stop(position) = max(0, (p − SL) if LONG else (SL − p)) × quantity × m`
with `p` = entry (initial risk) or last price (current risk to stop); a stop beyond breakeven gives 0, never negative.
`pending_risk(order) = |planned_entry − SL| × quantity × m` (the fill can raise it — see D).
`CURRENT_PORTFOLIO_RISK = Σ open risk_to_stop + Σ pending_risk`; `PROPOSED_TRADE_RISK = capital_at_risk`;
`POST_TRADE_PORTFOLIO_RISK = CURRENT + PROPOSED`; compared with a limit × equity basis (DEC-5.2).
Equity basis options: current equity (includes unrealized gains/losses — pro-cyclical), starting equity (static),
realized balance (`starting + realized`), conservative `min(current, realized balance)`.

## F. Symbol exposure

Only a count limit exists (one open position or pending order per symbol). Quantity is not comparable across
symbols; notional ignores the stop; **risk-to-stop is the comparable, Phase-4-consistent metric** (recommended
authoritative); notional remains a secondary guard only if leverage is introduced. Opposite-direction exposure is
impossible today (one per symbol).

## G. Multi-position readiness

Assumptions that break when Phase 6 allows more setups: `account.open_positions` is keyed by symbol (one position per
symbol — `load_paper` raises on duplicates); the runtime and broker refuse a second order/position per symbol; Risk has
no portfolio input. Phase 5 can add portfolio risk over the existing dict without changing these Phase 6 semantics.

## H. Drawdown

None exists. Deterministic options: account drawdown vs starting equity; peak-to-current (needs a durable
high-water mark — derivable from closed trades only for realized peaks; an equity peak including unrealized needs
persistence); daily realized loss (derivable from `closed_trades.exited_at` by UTC day); equity drawdown. All
thresholds are owner policy (DEC-5.5).

## I. Correlation evidence

Runtime: each floor run sees one symbol; no cross-symbol data at decision time → empirical correlation is
**INSUFFICIENT at runtime**. Offline replay store (12 months): EURUSD/XAUUSD 1h log-return correlation +0.43 (6,190
bars), daily +0.47 (261 days), rolling 20-day 0.13 … 0.68 (median 0.51) — positive and unstable (both quoted against
USD). A threshold-based empirical method would need cross-symbol evidence at decision time, a fixed lookback,
minimum observations, staleness and lookahead rules; none exists in the runtime. Static grouping or no adjustment
is implementable now (DEC-5.6).

## J. Invalid SL/TP — owning layer

LONG SL ≥ entry / SHORT SL ≤ entry / zero distance / SL == entry after rounding → Planner (`INVALID_GEOMETRY`) and
Risk (`invalid_level_order`); TP on wrong side / TP == entry → Planner (fixed 3R cannot produce it except SHORT
TP ≤ 0 → `INVALID_GEOMETRY`) and Risk; NaN/±Inf/None/wrong type/≤ 0 → Planner (`to_decimal`) and Risk
(`invalid_plan` / `invalid_numeric_value`); fill at/through SL → Broker (`fill_invalid_geometry`, V2) /
`post_fill_risk_or_geometry` (V1); invalid bar OHLC → Broker cancels (`invalid_fill_bar`); missing invalidation →
Setup Validator (NO_SETUP). No gap: Risk V2 should keep its existing checks and not duplicate Planner rules.

## K. Abnormal market conditions

| Class | Evidence today | Enforceable deterministically |
|---|---|---|
| Data integrity (stale, missing, future, wrong symbol, provider failure, gaps) | freshness gate, provider health, Phase 2 GAP/LATE (flag OFF) | YES (already enforced; GAP evidence only with the V2 flag) |
| Session / weekend | session calendar | YES (enforced) |
| Macro/news | high-impact active window → WATCH | YES (enforced at setup) |
| Execution displacement | fill geometry, money cap | YES (enforced at fill) |
| Market volatility (ATR/range) | derivable from bars, never certified | INSUFFICIENT EVIDENCE for a threshold |
| Spread | not available | INSUFFICIENT EVIDENCE |

## L. Concurrency / crash / restart

Single process: symbols run sequentially; no race. Cross-process: `symbol_locks` (same symbol) + B2.3A
compare-and-swap over the whole PAPER state (different symbols) — two writers cannot both commit on stale state.
An aggregate limit checked against freshly loaded state and persisted with `expected_state` is therefore atomic with
the reservation (the pending order row) — **no schema change and no cross-database atomicity needed**. Crash before
the order save: nothing reserved, recomputed next cycle; crash after: the pending order is the durable reservation;
restart re-derives everything from the trading DB. Stale equity: submission already requires
`equity == decision_equity`. Missing account/position or corrupt state: existing recovery raises (fail closed).
Risk computation exception: cycle FAILED, no order. Evidence Store unavailable: Phase 2 policy (PAPER economics
blocked when the V2 flag is ON).

## M. Phase 4 compatibility

Planner: exact 3.00R plan; Risk recomputes planned geometry (declared R:R cannot override); Broker recomputes the
actual fill (≥ 2.50, DEC-4.7). Risk never moves entry/SL/TP. Plan-time Risk and fill-time Broker authorities are
distinct. Contradiction: the broker's money cap is a second, hard-coded per-trade risk limit (1%) that is not tied to
the Risk configuration and rejects 27.4% of DEC-4.7-accepted fills (finding H1).

## N. Proposed Risk Engine V2 architecture

TradePlan (Phase 4) → Risk V2 (pure function): (1) existing per-trade checks and sizing (unchanged policy unless
DEC-5.1) → (2) load PAPER state already loaded by the guarded write (no new reads) → (3) open risk + pending risk
(reservations) per symbol and in total → (4) post-trade totals vs DEC-5.2/5.3 limits on the DEC-5.2 equity basis →
(5) drawdown state vs DEC-5.5 → (6) correlation group (DEC-5.6) → APPROVED / RISK_REJECTED with a reason → order
persisted with `expected_state` (the pending order is the reservation). Inputs: plan, PAPER state, equity basis,
policy version. Outputs: decision + decision record. Idempotency: same state + plan + policy → same decision.
Concurrency: B2.3A compare-and-swap. Fail closed on any missing/invalid input. Rollback: policy version flag; V1
path unchanged. Fill-time money risk: one configured limit shared by Risk and Broker (DEC-5.7) instead of the
hard-coded 0.01.

Decision record (additive JSON in the existing review/risk records, schema 3): run_id, setup_id, symbol, side,
planned entry, SL, TP, planned R:R, policy version, risk fraction, equity basis + value, risk budget, quantity,
trade risk, existing symbol risk, existing portfolio risk, pending (reserved) risk, post-trade symbol/portfolio
risk, drawdown state, correlation group, status, reason, timestamp.

## O. Owner decisions — see the final report (DEC-5.1, 5.2, 5.4/5.3, 5.5, 5.6, 5.7, 5.8).

## P. Tests / evidence

`test_phase5_risk_characterization.py` (8 tests: config defaults and no runtime path, four worked examples with
binding constraint and PAPER rounding, float-epsilon above 1% before rounding, invalid equity and extreme stops,
equity basis includes unrealized PnL, Risk has no portfolio input, one-per-symbol refusal, broker hard-coded 1% money
cap at fill and its independence from the Risk configuration). Offline evidence script (not committed): money-at-risk
at fill and correlation from the 12-month replay store.

## R. P5.1 plan (after decisions)

Batch A — portfolio-risk core: risk-to-stop math, pending reservations, aggregate/per-symbol limits, equity basis,
decision record, unified per-trade money limit shared with the broker (DEC-5.1/5.2/5.3/5.4/5.7). Batch B —
drawdown/loss limits and abnormal-market policy (DEC-5.5/5.8), correlation grouping if adopted (DEC-5.6).
Batch C — replay re-run of the fixed-3R cohort with the full gate stack, characterization diff, certification.

---

# P5.1 — Risk Engine V2 implementation (DEC-5.1 → DEC-5.8 approved)

Status: implemented, **pending independent review** (author: Claude; not self-certified). PAPER only · REAL
DISABLED · NAS100 OFF · runtime NOT wired / NOT activated · trading DB schema 3 · Phases 3/4 unchanged.

## S1. Components

| File | Role |
|---|---|
| `core/risk_policy.py` | Single versioned policy `V2_P5_RISK_1` (exact `Fraction`s): per-trade 1/100, notional 1, aggregate 23/1000, drawdown 5/100, planned R:R 3 and fill floor 5/2 read from `core.rr_contract`, fill money factor **derived** `(1+3)/(1+2.5) = 8/7`, max 1 position-or-pending per symbol, correlation OFF, daily loss NONE, volatility/spread UNAVAILABLE. |
| `execution/risk_engine_v2.py` | Pure `evaluate(plan, account, orders, instrument, policy)` → `RiskDecision` + traceability record. No I/O, no market data, no mutation. |
| `execution/risk_reservation.py` | `reserve_and_submit`: fresh durable load → run_id idempotency → evaluate → unchanged `PaperBroker.submit_plan` (fixed 3R) → ONE `save_paper(expected_state)` with the `RISK_V2_DECISION` journal row; one fresh re-evaluation on STALE, then fail closed. The PENDING order row **is** the reservation (no new table, no schema change). |
| `execution/paper_broker.py` | DEC-5.7 for `POLICY_V2_F3` only: the hard-coded `0.01 × equity` fill caps are replaced by `actual fill money risk ≤ planned money risk × 8/7` (planned = order quantity × \|planned entry − SL\| × multiplier). V1 (`rr_policy=None`) and policy D unchanged. |
| `replay/risk_audit.py` | Offline observational audit (standalone + chronological portfolio). |

## S2. Gate order and rejection precedence (first failing gate decides)

1. account state valid (finite equity/realized, positive starting equity) → `INVALID_ACCOUNT_STATE`
2. plan geometry via the single R:R contract: policy `V2_P4_FIXED_3R` (`rr_policy_mismatch`), levels
   (`invalid_numeric_value` / `invalid_level_order` / `invalid_side`), declared R:R matches (`rr_declared_mismatch`),
   exactly 3R (`rr_below_minimum` / `rr_above_maximum`); instrument contract present
   (`instrument_contract_data_unavailable`)
3. conservative equity `C = min(current_equity, starting_equity + realized_pnl)`; `cash` is not an input;
   `C ≤ 0` → `INVALID_ACCOUNT_STATE`
4. drawdown: `current_equity ≤ starting_equity × 0.95` → `ACCOUNT_DRAWDOWN_LIMIT` (new entries only; never closes
   or moves anything; no latch, no high-water mark, no daily or consecutive-loss rule)
5. sizing: `q = floor_increment(min(C × 1% / (D × m), C × 100% / (entry × m)))`; TRUE planned money risk
   `R = q × D × m ≤ C × 1%` (exact arithmetic; the P5.0 float epsilon 100.00000000000001 cannot occur)
   → `PAPER_QUANTITY_BELOW_INCREMENT`
6. open risk `Σ max(0, fill→SL distance) × qty × m` over open positions (persisted fill price, see S3)
7. pending reserved risk `Σ |planned entry − SL| × qty × m × 8/7` over PENDING orders
8. proposed reservation `R × 8/7`
9. symbol rule: an open position or PENDING order on the symbol → `SYMBOL_EXPOSURE_LIMIT`
10. aggregate `open + pending + proposed ≤ C × 2.30%` → `PORTFOLIO_RISK_LIMIT`
11. `APPROVED`

## S3. Authoritative price source for open risk

Open risk uses the **persisted fill price** (`PaperPosition.entry_price`) to the frozen SL, never a market quote.
(a) No market-data dependency or freshness ambiguity at decision time. (b) With the conservative equity basis it
never under-states the remaining downside: a position in profit has its unrealized profit excluded from C, so the
loss to SL relative to C is exactly fill→SL; a position at a loss already reduced C, so fill→SL over-states the
remaining distance (conservative). Favorable fills reduce the reservation. At the worst permitted fill the pending
reservation equals the open risk after the fill, so pending → fill has neither a gap nor a double count
(`test_pending_to_fill_no_gap_no_double_count`).

## S4. Concurrency / restart (DEC-5.4)

Proven with real separate processes (`test_phase5_risk_concurrency.py`):
A cross-symbol race that would exceed the aggregate (test policy 1.5%) → the loser re-evaluates on the winner's
reservation → `PORTFOLIO_RISK_LIMIT`; B race that fits → both reserved (loser after one re-evaluation);
same-symbol race → one admitted; C crash before commit (lock held, then `os._exit`) → nothing reserved, lock
released, retry reserves; D crash after commit → retries return `DUPLICATE_RUN`, still one order and one decision
row; E unrelated stale writer (realized loss crossing −5%) → save refused, re-evaluation → `ACCOUNT_DRAWDOWN_LIMIT`.

## S5. Protection matrix (F05-T01)

| Protection | Rule | Where | Test |
|---|---|---|---|
| Per-trade risk | ≤ 1% of C (ceiling); TRUE risk after floor | engine step 5 | `SizingTests` |
| Leverage | notional ≤ 100% of C | engine step 5 | worked examples (EUR LONG, XAU SHORT) |
| Unrealized profit | never raises capacity | engine step 3 | `test_conservative_equity_basis` |
| Aggregate | open + pending + proposed ≤ 2.30% of C | engine step 10 | `test_aggregate_limit_binds_*`, race A |
| Pending orders | reserved at 8/7 × planned | engine step 7 | `test_open_and_pending_risk_amounts` |
| Symbol exposure | 1 open-or-pending per symbol | engine step 9 + broker open check | `test_symbol_rule_open_and_pending` |
| Drawdown | no new entries at equity ≤ 95% of start | engine step 4 | `test_drawdown_gate_boundary`, race E |
| Fill R:R | actual ≥ 2.50 (DEC-4.7) | broker `_fill_check` | Phase 4 tests, boundary 2660 |
| Fill money | actual ≤ planned × 8/7 (DEC-5.7) | broker `_fill_money_check` | `FillMoneyRuleTests` |
| Gap through SL | rejected (`fill_invalid_geometry`) | broker | `test_gap_through_stop_and_invalid_state` |
| Correlation | OFF (DEC-5.6) | policy record only | `test_single_versioned_policy` |
| Volatility / spread | UNAVAILABLE (DEC-5.8); existing gates kept | policy record only | idem |
| Idempotency | run_id → same order, no second reservation | reservation | `test_retry_is_idempotent`, race D |
| Atomicity | B2.3A whole-state CAS, one retry | reservation | races A–E |
| Invalid SL/TP | owned by planner / R:R contract; Risk only recomputes | engine step 2 | `test_invalid_state_and_geometry_fail_closed` |

## S6. Replay comparison (observational; constants not tuned)

`python -m replay.risk_audit <replay_twelve_data_12m.db> <P4.1A lab_out> <out.json>`: 1,940 fixed-3R plans,
NEXT_CYCLE fill, production engine + broker; old rule = R:R floor + the former 1% equity cap on the same rows.

| Standalone (fresh 10,000 account) | all | discovery | holdout | EURUSD | XAUUSD |
|---|---|---|---|---|---|
| plans / Risk-approved | 1,940 / 1,940 | 1,221 / 1,221 | 719 / 719 | 949 / 949 | 991 / 991 |
| old 1% rule: filled | 1,387 | 860 | 527 | 831 | 556 |
| old: money-cap rejects | 523 | 351 | 172 | 102 | 421 |
| DEC-5.7: filled | 1,910 | 1,211 | 699 | 933 | 977 |
| DEC-5.7: money rejects | 0 | 0 | 0 | 0 | 0 |
| R:R < 2.50 rejects (both rules) | 29 | 10 | 19 | 16 | 13 |
| gap through SL (both rules) | 1 | 0 | 1 | 0 | 1 |
| R:R ≥ 2.50 vs 8/7 disagreements | **0** | 0 | 0 | 0 | 0 |

Planned risk % of equity: median 0.9988, p05 0.2999, max 1.0000 (EURUSD median 0.6344, notional-bound; XAUUSD
0.9994). Actual fill risk % (DEC-5.7 filled): median 0.9684, p95 1.0545, max 1.1397. Actual/planned ratio: median
0.9977, p95 1.0782, max 1.1420 (< 8/7 = 1.142857). The two fill gates are mathematically equivalent for exact 3R
plans (`fill_money_risk_factor` derivation) and agree on every row; the money rule stays as defense in depth
(`test_money_rule_is_independent_defense_in_depth`).

Portfolio pass (chronological, one account, full gate stack, no costs): DEC-5.7 — 17 approved, 1,923
`SYMBOL_EXPOSURE_LIMIT`, 17 filled, 0 `PORTFOLIO_RISK_LIMIT`, 0 `ACCOUNT_DRAWDOWN_LIMIT`, peak post-trade portfolio
risk 2.1677% of C, minimum equity at decisions 9,917.51. Former 1% rule — 26 approved, 10 money-cap rejects, 16
filled, peak 2.1670%. The one-per-symbol rule dominates because fixed-3R trades on far structural stops stay open
for long periods; n is small and PnL is not a performance claim.

## S7. Findings disposition

- H1 (independent hard-coded 1% fill cap): FIXED for fixed 3R by DEC-5.7 (characterization test explicitly updated).
- Missing aggregate / pending / drawdown controls: FIXED (S2).
- Float epsilon above 1% (LOW): FIXED on the V2 path (exact arithmetic). Frozen V1 `riesgo.py` unchanged; its result
  is floored by the PAPER increment before persistence.
- `cash` never updated: DOCUMENTED; not an input of Risk V2.
- NAS100 can be listed in `AI_FLOOR_ENABLED_SYMBOLS`: CARRY-FORWARD safety finding (runtime not redesigned). Risk V2
  fails closed for NAS100 (`instrument_contract_data_unavailable`: no PAPER contract).
- Runtime wiring: NOT DONE (forbidden in P5.1). The frozen V1 runtime keeps V1 Risk; Risk V2 is reachable only via
  `reserve_and_submit` (explicit V2 path), like the Phase 4 opt-in planner.
