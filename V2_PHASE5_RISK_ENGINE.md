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
