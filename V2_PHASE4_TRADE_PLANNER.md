# V2 Phase 4 — Trade Planner + Target/R:R Engine (P4.0 design & owner decision package)

Status: **P4.0 DESIGN COMPLETE — READY FOR OWNER DECISION.** P4.1 implementation NOT STARTED.
Base: `main` @ `b8b7493d91b37156e653d8ce7a8850a9abd82fb0` (Phase 3 merged). Branch `v2/phase4-trade-planner`.
No production code changed; trading behavior unchanged. PAPER only; REAL DISABLED; NAS100 OFF; V2 runtime
activation NOT AUTHORIZED; System Health INACTIVE; trading DB schema 3.

---

## A. Current V1 behavior (from code)

Path: `floor/orchestrator.run` → `agents/setup_validator.evaluar_setup` → `agents/trade_planner.crear_trade_plan`
→ `riesgo.evaluar_trade_plan` → `runtime/paper_contracts.apply_paper_quantity_increment` → AI
(`ai/orchestrator`: setup + trade reviewers) → `runtime/gates.paper_policy` → `PaperBroker.submit_plan` →
next cycle `PaperBroker.process_next_bar` (fill gate) → `TradeManager.process_bar` (SL/TP).

| Element | V1 source | Authority |
|---|---|---|
| Entry | last closed 5m `Close` ≤ as_of (`trade_planner.py:28`) | deterministic |
| Stop | `setup.invalidation` unchanged (`trade_planner.py:32`) | deterministic (Setup) |
| Invalidation | 15m `levels.support` (LONG) / `resistance` (SHORT) = **lowest swing low / highest swing high in the provider window** (`structure_agent.py:164-165`, `setup_validator`) | deterministic (Setup) |
| Target | `entry ± 3 × |entry − stop|` (`trade_planner.py:38-43`) — **manufactured from R, not structure** | deterministic |
| R:R | literal `3.0` in the plan (`trade_planner.py:46`), never computed | deterministic constant |
| Planner rejection | not VALID_SETUP; no/empty/malformed 5m data; NaN/≤0 entry; NaN stop; stop on wrong side (LONG `stop<entry`, SHORT `stop>entry`) → `None` | deterministic |
| Risk | `ratio_minimo = 3.0` vs the **declared** `risk_reward`; level order; size = min(1% equity / (|entry−stop|·mult), 100% equity notional) (`riesgo.py:112-150`) | deterministic authority |
| PAPER units | quantity floored to increment (XAU 0.001, EUR 1.0) (`paper_contracts.py`) | deterministic |
| AI | setup reviewer sees status/side; trade reviewer sees plan incl. `risk_reward`. Default `DeterministicAIProvider` → `REJECT_RECOMMENDATION` if `risk_reward < 3` (`ai/provider.py:113`) → `AI_CAUTION` → no order | AI-assisted veto |
| Fill gate | next bar open; REJECT unless `reward/risk ≥ 3` at the fill price and real risk ≤ 1% equity (`paper_broker.py:86-96`) | deterministic |
| Costs | `cost_rate = 0.0` hard-coded at submission; no spread/slippage/commission model; slippage only implicit (next-bar open) | NOT IMPLEMENTED |
| Precision | `price_increment` XAU 0.01 / EUR 0.00001 exists (`paper_contracts.py`) but **entry/stop/target are not rounded** | NOT IMPLEMENTED |
| Session / volatility / structure / liquidity in planner | none (session only gates the cycle; ATR exists only in the legacy `calcular_plan_riesgo`, unused by V2) | NOT IMPLEMENTED |
| Persisted | plan dict in `setups.plan` JSON, risk decision table, review report, `PLAN_CREATED` / `RISK_*` journal events | — |

Tests protecting V1: `test_trade_planner.py`, `test_risk_v2.py`, `test_execution.py`, and the new oracle below.

## B. V1 regression oracle (frozen economics)

`test_phase4_v1_planner_oracle.py` (non-production) pins exact outputs: EURUSD/XAUUSD LONG/SHORT, precision
edge, tiny SL, extreme SL, all planner rejections (wrong geometry LONG/SHORT, equal stop/entry, missing
invalidation, WATCH, missing/malformed/NaN market data, NaN invalidation), Risk `rr_below_minimum`,
PAPER increments, deterministic AI R:R dependency, and the broker fill gate (fill 0.50 worse → REJECTED).
Examples: XAU LONG 2650/2640 → target 2680, qty 3.7736 (capital-capped) → 3.773; EUR LONG 1.085/1.082 →
target `1.0939999999999996`; XAU tiny SL 2649.99 → target `2650.0300000000007` APPROVED; XAU SL 2400 →
target 3400.

## C. Available evidence for Phase 4

| Input | Where | Class |
|---|---|---|
| Entry (last closed 5m close) | snapshot | AVAILABLE + AUTHORITATIVE |
| Invalidation (window-extreme 15m swing) | `SetupAssessment.invalidation` | AVAILABLE + AUTHORITATIVE (Setup) |
| Confirmed swing highs/lows per tf (price, pivot bar) | `setup.evidence[0][tf]["swings"]` (structure scout) | AVAILABLE + INFORMATIONAL (deterministic, 1-bar confirmation) |
| BOS broken level, retracement impulse/protected | structure payload | AVAILABLE + INFORMATIONAL |
| Equal highs/lows (0.1% tolerance) | `setup.evidence[1][tf]["liquidity_above/below"]` | AVAILABLE + INFORMATIONAL (labelled HEURISTIC) |
| Sweeps (last bar only) | liquidity payload | AVAILABLE BUT INSUFFICIENT as a target source (context) |
| Volatility (ATR/range) | not produced; derivable from snapshot OHLC | AVAILABLE BUT INSUFFICIENT (no certified definition) |
| Session | `runtime/scheduler.session_names` | AVAILABLE + AUTHORITATIVE for gating; no movement model |
| Macro active window | macro report (already a Setup gate) | AVAILABLE + AUTHORITATIVE (gate) |
| Price precision | `paper_instruments()` `price_increment` | AVAILABLE + AUTHORITATIVE (PAPER) |
| Spread / commission / slippage model | — | NOT AVAILABLE |
| Phase 2 Market Evidence (chronological closed bars) | sidecar, only when `v2_position_catch_up` ON (OFF in prod) | AVAILABLE (code) / NOT POPULATED historically |
| Phase 3 evidence refs, setup_id | `SetupAssessment.explanation` | AVAILABLE + INFORMATIONAL / identity |
| Historical trade outcomes | not in the repository | NOT AVAILABLE |

AI prose is never an input.

## D. Invalidation-first pipeline (design)

`plan_target(setup, market, instrument, policy) -> TargetDecision` (pure, deterministic):
1. **Invalidation**: read `setup.invalidation` (never computed here). Validate finite, > 0, correct side.
2. **Freeze**: `stop` is a constant for the rest of the function; no later stage receives a writable stop.
3. **Risk distance**: `R = |entry − stop|` on increment-rounded prices (rule in F).
4. **Candidates**: enumerate structural levels on the reward side **without reference to R or any policy**.
5. **R:R per candidate**: `rr = reward / R` (Decimal arithmetic on rounded prices).
6. **Policy**: filter/select only; it can choose a candidate or reject — it cannot create a price.

Structural guarantees (testable): the policy receives `(candidates, R)` and returns an index or a rejection
code; `plan.stop == rounded(setup.invalidation)` always; candidate generation is identical for every policy
and every floor (mutation: a policy that alters `stop` or synthesizes a target must fail certification).

## E. Structural invalidation

Consume the Phase 3 invalidation **unchanged** (Setup semantics are certified; redefining "invalidation"
would change setups). Validate only: finite, > 0, LONG `stop < entry`, SHORT `stop > entry` after rounding;
otherwise `REJECT invalid_geometry`. Record (informational) the nearest protective swing as metadata: the V1
invalidation is the window extreme, which can be far from the nearest protective swing (distant-SL cases).

## F. SL distance and precision

Repository convention: prices in USD per unit; only `price_increment` (no pip concept). Compute
`risk_price = |entry − stop|`, `risk_ticks = risk_price / price_increment`, `risk_pct = risk_price / entry`;
EURUSD pips (`/0.0001`) for display only. Rounding (conservative, fixes unrounded V1 targets):
stop rounded **away** from entry, target rounded **toward** entry, both to `price_increment` (LONG: stop
floor, target floor; SHORT: stop ceil, target ceil); R:R classified on rounded prices with `Decimal`, so
rounding can only lower R:R. Zero ticks after rounding → `REJECT zero_risk_distance`.

## G. Volatility

No certified volatility exists. Design: optional `atr_5m_14` / `atr_15m_14` from the same snapshot's closed
bars (no lookahead), **informational only** in P4.1 (`target_distance_in_atr`); unavailable → field
`UNAVAILABLE`, never a rejection. Making it gate targets is a strategy change (not proposed).

## H. Available space and obstacles

Available space = distance from entry to the nearest obstacle in the reward direction. Obstacle sources:
confirmed 15m and 1h swing highs (LONG) / lows (SHORT) strictly beyond entry; equal-high/low liquidity
levels beyond entry. Per obstacle: `price, source (swing_15m|swing_1h|equal_level_<tf>), authority
INFORMATIONAL-DETERMINISTIC, timestamp, distance, rr_at_level, blocks_beyond (bool)`. No obstacle evidence →
`OBSTACLE_EVIDENCE_UNAVAILABLE` (the planner must not assume open space). FVG / Fibonacci / POI / extra
confirmations: not introduced.

## I. Liquidity

Equal highs/lows are both a plausible target (resting liquidity) and an obstacle. Eligible as a candidate
only as a structural level of the same list (H); sweeps (already-taken liquidity) are context only.
Heuristic tolerance (0.1%) stays labelled HEURISTIC in metadata.

## J. Session

Metadata only (`session_names(as_of)`); no session multiplier (no evidence). Session already gates the cycle.

## K. Costs

`gross_rr` always. `net_rr = UNAVAILABLE (costs_unknown)` until the owner supplies explicit per-symbol
spread/commission assumptions (decision 4). The fill gate's implicit slippage (next-bar open) remains.

## L. Target candidate record

`{target_price, source, timeframe, evidence_ref (symbol, timeframe, bar/pivot timestamp), distance_price,
distance_ticks, reward, risk, gross_rr (Decimal str), band, blocked_by (obstacle or None), session,
data_quality, eligible (bool), reason}`.

## M. Policy options (owner decision 1)

Candidates are sorted by distance from entry; `O1` = nearest candidate (also the first obstacle).

- **Policy 0 — keep V1**: manufactured 3R target. No change; ignores structure (status quo).
- **Policy A — farthest justified**: target = farthest candidate; classify band. + maximal reward.
  − Ignores nearer obstacles (target "through" structure); most optimistic; highest overfitting risk.
- **Policy B — floor + structure**: target = nearest candidate with `rr ≥ floor` **and no nearer
  blocking obstacle**; if `O1` has `rr < floor` → `REJECT target_blocked_before_floor`.
- **Policy C — ranked**: score eligible candidates (authority 1h > 15m > equal-level; confluence count;
  band 2–5 preferred); pick the top score. + Uses more evidence. − Weights are arbitrary until replay
  calibrates them; hardest to certify; overfitting-prone.
- **Policy D — first obstacle (proposed)**: target = `O1` (the first structural obstacle, the most
  conservative genuine target). Accept iff `rr(O1) ≥ floor` and band policy (N) allows it; otherwise reject.
  No target is ever placed beyond an unbroken obstacle or invented.

| | 0 | A | B | C | D |
|---|---|---|---|---|---|
| Genuine (structural) target | no | yes | yes | yes | yes |
| Can accept 1:2 where V1 trades | — | if floor 2 | if floor 2 | if floor 2 | if floor 2 |
| Changes TP where V1 trades | no | ~always | ~always | ~always | ~always |
| Rejects some V1 trades | no | when no candidate | when `O1 < floor` | when no candidate | when `O1 < floor` or none |
| Required evidence | none | swings | swings | swings+weights | swings |
| Evidence unavailable | — | REJECT | REJECT | REJECT | REJECT |
| Overfitting risk | none | high | low | high | lowest |
| Testability | trivial | good | good | weak | best |

How many V1 plans change: not quantifiable from the repository (no history). Every V1 plan's TP would change
under A–D (V1 TP is never structural); a share of V1 trades would be rejected (no candidate, or `O1 < floor`).
Risk sizing is unaffected (it depends on entry/stop only); `capital_at_risk` unchanged.

**TECHNICAL RECOMMENDATION:** Policy D, floor 1:2 for V2 PAPER experimentation, bands per N, gross R:R only,
evaluated against Policy 0 by the F04-T15 replay before any runtime use. **OWNER APPROVAL REQUIRED.**

## N. R:R bands (on rounded prices, Decimal, `≥` at boundaries)

| Band | Treatment (recommended, owner-approved floor) |
|---|---|
| `< 2` | REJECT `rr_below_floor` |
| `= 2`, `2–3` | ACCEPT if floor = 2; REJECT if floor = 3 |
| `= 3`, `3–4`, `= 4`, `4–5`, `= 5` | ACCEPT (genuine target required) |
| `> 5` | ACCEPT as `EXTENDED` with flag `rr_above_5` (genuine first obstacle; never capped/synthesized) — or REJECT if the owner prefers (decision 1c) |

## O. Historical probability (F04-T12)

Minimum evidence before any probability: ≥ 100 closed PAPER/replay trades per symbol and ≥ 30 per band,
same setup family, same policy version, chronological evidence without lookahead/revisions, stated costs,
out-of-sample split. The repository has **no** such data (no committed history; Phase 2 Evidence Store not
populated in production). Result: `HISTORICAL_PROBABILITY = UNAVAILABLE / INSUFFICIENT_EVIDENCE`. Not a
blocker for P4.1 (honest unavailability is allowed).

## P. Scenarios (design fixtures → P4.1 tests / replay inputs)

R distances: XAU 10.00 (entry 2650, stop 2640 LONG / 2660 SHORT); EUR 0.00100 (entry 1.08500, stop
1.08400 LONG / 1.08600 SHORT). Obstacle `O1` placed at:

| Scenario | XAU LONG O1 | XAU SHORT O1 | EUR LONG O1 | EUR SHORT O1 | D, floor 2 |
|---|---|---|---|---|---|
| ≈1:2 | 2670.00 | 2630.00 | 1.08700 | 1.08300 | ACCEPT 2.0 |
| ≈1:3 | 2680.00 | 2620.00 | 1.08800 | 1.08200 | ACCEPT 3.0 |
| ≈1:4 | 2690.00 | 2610.00 | 1.08900 | 1.08100 | ACCEPT 4.0 |
| ≈1:5 | 2700.00 | 2600.00 | 1.09000 | 1.08000 | ACCEPT 5.0 |
| blocked before 1:2 | 2665.00 | 2635.00 | 1.08650 | 1.08350 | REJECT blocked |
| beyond 1:5 | 2720.00 | 2580.00 | 1.09200 | 1.07800 | EXTENDED 7.0 |
| multiple (2680, 2700) | 2680 chosen | 2620 chosen | 1.08800 | 1.08200 | ACCEPT 3.0 |
| no candidate | — | — | — | — | REJECT no_valid_target |

Plus: very close invalidation (XAU stop 2649.99 → R 1 tick; O1 2652 → 200 R → EXTENDED), very distant
invalidation (stop 2400, O1 2700 → 0.2 → REJECT), missing volatility (informational UNAVAILABLE, no change),
missing liquidity (swings only), missing obstacle evidence (REJECT `obstacle_evidence_unavailable`), session
boundary (metadata only), precision edge (EUR target 1.0870049 → 1.08700 rounds toward entry; R:R recomputed;
`2.00` exactly accepted at floor 2; `1.99999` rejected), costs unavailable (`net_rr UNAVAILABLE`).

## Q. Edge-case / fail-closed matrix

| Case | Result |
|---|---|
| missing entry / missing SL / NaN / ±Inf / price ≤ 0 | REJECT `invalid_numeric` |
| zero risk distance (after rounding) | REJECT `zero_risk_distance` |
| LONG stop ≥ entry / SHORT stop ≤ entry | REJECT `invalid_geometry` |
| stale / future / misaligned evidence (Phase 3 `ref_state` ≠ VALID for 15m/1h structure) | REJECT `target_evidence_invalid` |
| mixed symbols / mixed setup_id in evidence | REJECT `lineage_mismatch` |
| unsupported instrument / unknown precision | REJECT `instrument_precision_unavailable` |
| unknown cost | SAFE DEGRADATION: `net_rr UNAVAILABLE` |
| no target / all candidates on wrong side / target == entry / target on risk side | REJECT `no_valid_target` |
| extreme R:R (> 5) | per band N (flagged EXTENDED) |
| rounding changes classification | classification on rounded prices only (deterministic) |
| volatility missing | SAFE DEGRADATION (informational field UNAVAILABLE) |
| duplicate planning / restart / retry | same inputs + policy version → identical decision and id (R) |

## R. Identity / idempotency

Today a plan has no id (keyed by `run_id`; `setup_id` exists for VALID setups). Proposed:
`target_decision_id = uuid5(setup_id, symbol, side, entry, stop, target|None, policy_version, candidate set
hash)`. Same durable inputs + same policy version → same id; any material change → new id. `setup_id`
semantics untouched.

## S. Downstream impact (fields whose meaning changes)

- `TradePlan.target`: manufactured 3R → genuine structural level. `TradePlan.risk_reward`: constant 3.0 →
  computed gross R:R (rounded prices).
- **Risk Engine** (`ratio_minimo = 3.0`, declared-R:R trust): a floor below 3 is rejected by Risk unless its
  config changes (owner decision 2). Recommended hardening: Risk recomputes R:R from levels.
- **AI** deterministic provider rejects `risk_reward < 3` → `AI_CAUTION` (decision 2).
- **Paper Broker** fill gate hard-codes `≥ 3` at the fill price (`paper_broker.py:92`) → would reject every
  2 ≤ R:R < 3 order (decision 2).
- Observability: new `target_decision` metadata in the review report (additive); `PLAN_CREATED` unchanged.
- Phase 6 position conflicts: nearer targets shorten holding time (informational).

## T. Replay design (F04-T15) — READY, data required

Inputs: chronological closed 1h/15m/5m bars for XAUUSD and EURUSD (Phase 2 Evidence Store format, committed
only, REVISION/LATE excluded). At each slot `t`: rebuild the provider window from evidence `≤ t`; run the
same scouts + Setup Validator once; plan with Policy 0 **and** the candidate policy on the identical setup;
same Risk config; fill at the next committed 5m open with the V1 fill gate per policy floor; manage SL/TP on
every subsequent committed 5m bar with `TradeManager` semantics (V1 same-bar precedence); identical costs
(0, or the owner model for both); AI excluded or the deterministic provider fixed for both; same session
gates. No future bar may influence target selection (candidates from swings confirmed ≤ t). Data: ≥ 12
months of bars per symbol — **not in the repository; acquisition requires owner authorization (decision 3).**

## U. Comparison metrics (F04-T16)

Available from replay: plans, rejections by reason, R:R distribution, target-source distribution, target
reach rate, stop rate, time-to-outcome, ambiguous same-bar rate, gross PnL, expectancy (R and $), max
drawdown, exposure time, session and symbol distribution, data-sufficiency per band. Unavailable without
owner cost model: net PnL / net expectancy. Unavailable without live PAPER history: real fill slippage.

## V. Tests and certification (F04-T17 / F04-T18)

P4.1 tests: oracle unchanged when the new policy is OFF; invalidation-first (stop == rounded invalidation;
mutation: policy alters stop → killed); genuine target only (mutation: synthesized `entry + kR` → killed);
band boundaries on rounded Decimal; every Q row; scenario table P for all four symbol/side pairs;
determinism/identity (same inputs → same id, restart); lineage (Phase 3 refs); no Risk/AI/broker change
unless decision 2 approves it; flag OFF by default. Certification: replay without lookahead, V1 comparison
on identical evidence, PAPER only, REAL disabled, NAS100 off, schema 3 (metadata in existing JSON), rollback
= flag OFF restores Policy 0 byte-identically.

---

## OWNER DECISION PACKAGE

**DECISION 1 — Target/R:R policy for V2 PAPER experimentation**
- OPTIONS: Policy 0 (keep V1 manufactured 3R) · A (farthest) · B (floor + nearest unblocked) · C (ranked) ·
  D (first obstacle + floor). Sub-choices: floor 1:2 or 1:3 (1b); `> 1:5` accept-as-EXTENDED or reject (1c).
- TECHNICAL RECOMMENDATION: **D, floor 1:2, `> 1:5` accepted as EXTENDED**, behind a flag OFF by default,
  replay-compared to Policy 0 before any runtime use.
- WHY: only genuine targets; most conservative structural choice; fewest free parameters; deterministic and
  testable; never touches the stop.
- TRADING BEHAVIOR AFFECTED: TP of essentially every plan; some V1 trades rejected (no target / blocked);
  some plans with 2 ≤ R:R < 3 become possible (only with decision 2).
- EVIDENCE: V1 TP is `entry ± 3R` by construction (oracle); swings/levels available in Setup evidence.
- RISK IF APPROVED: fewer trades, smaller average reward per trade; no history to predict net effect.
- RISK IF DEFERRED: V1 keeps manufactured, sometimes absurd targets (3-cent or +$750 gold targets).

**DECISION 2 — Align the four hard-coded "3" gates with the approved floor**
- OPTIONS: (a) align Risk `ratio_minimo`, deterministic AI `risk_reward < 3` rule and Paper Broker fill gate
  to the policy floor, and make Risk recompute R:R from levels; (b) keep 3 everywhere (then 1:2 never trades).
- TECHNICAL RECOMMENDATION: (a), as an explicit, separately reviewed change.
- WHY: otherwise a floor-2 plan is approved by the planner and silently killed by Risk, AI or the broker;
  Risk currently trusts the declared R:R.
- TRADING BEHAVIOR AFFECTED: Risk/AI/broker acceptance thresholds.
- EVIDENCE: `riesgo.py:135`, `ai/provider.py:113`, `paper_broker.py:92`; oracle `declared3_real1` APPROVED.
- RISK IF APPROVED: changes three certified authorities at once (needs its own certification).
- RISK IF DEFERRED: decision 1 with floor 2 has no effect in practice.

**DECISION 3 — Authorize historical data acquisition for the F04-T15 replay**
- OPTIONS: acquire ≥ 12 months of 1h/15m/5m XAUUSD/EURUSD bars into a separate replay evidence store /
  replay only on data collected from now on / skip replay.
- TECHNICAL RECOMMENDATION: acquire into a separate store (never the trading DB).
- WHY: no history exists in the repository; F04-T12/T15/T16 cannot be evidenced otherwise.
- TRADING BEHAVIOR AFFECTED: none (offline).
- EVIDENCE: no committed data; Evidence Store not populated (flag OFF).
- RISK IF APPROVED: provider quota/cost; data-quality work. RISK IF DEFERRED: policy chosen without evidence.

**DECISION 4 — Cost assumptions for net R:R**
- OPTIONS: gross only (net UNAVAILABLE) / owner-provided per-symbol spread + commission assumptions.
- TECHNICAL RECOMMENDATION: gross only for P4.1; add owner-provided assumptions before certification metrics.
- WHY: no broker cost data exists; inventing costs is forbidden.
- TRADING BEHAVIOR AFFECTED: none if informational; acceptance only if net R:R is later made a gate.
- RISK IF APPROVED: assumption error. RISK IF DEFERRED: net expectancy unavailable in F04-T16.
