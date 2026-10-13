# V2 Phase 4 — Trade Planner + Target/R:R Engine

## Current state (final Phase 4 policy)

Status: **independent Phase 4 certification PASS** on candidate `d012cd4ea61c0746b642779a995dd69150723943`; not
yet merged. Base: `main` @ `b8b7493d91b37156e653d8ce7a8850a9abd82fb0` (Phase 3 merged). Branch
`v2/phase4-trade-planner`.

- **DEC-4.6 — V2 planning policy = FIXED 3R.** Planned R:R is exactly **3.00R**.
- **DEC-4.7 — minimum acceptable actual fill-time R:R = 2.50.** This is an execution tolerance only; V2 is **not** a
  2.5R strategy. A fill with actual R:R ≥ 2.50 executes as a fixed-3R plan that experienced execution displacement.
- The certified Phase 3 structural invalidation remains the **SL authority**; the SL is immutable after planning and
  is never derived from a ratio.
- TP = entry ± 3 × risk, calculated at planning and **immutable** afterwards (never moved or re-anchored at the fill).
- Risk independently recomputes the planned geometry (a declared R:R never overrides it); the Paper Broker
  independently recomputes the actual fill-time geometry (actual fill + frozen SL/TP).
- Structural target/obstacle evidence (swings, prior-day levels, liquidity) is **observational only**.
- Production code changed in Phase 4 (opt-in paths; V1 behavior unchanged by default): `core/rr_contract.py`,
  `agents/target_planner.py`, `floor/orchestrator.py`, `riesgo.py`, `ai/provider.py`,
  `ai/agents/trade_reviewer_ai.py`, `execution/paper_broker.py`, `core/contracts.py`, `runtime/review.py`; offline
  `replay/` package; `.gitignore` (git-ignores `data/replay/`). Phase 3 (Setup Validator, setup_id) unchanged.
- P4.1A (target-policy lab) remains historical research evidence. P4.1B (invalidation lab) was aborted when DEC-4.6
  superseded it; it changed neither Phase 3 nor production policy.
- **Phase 3: CERTIFIED / CLOSED.** **Phase 4 runtime: OFF** (no runtime path selects a Phase 4 policy). REAL
  EXECUTION DISABLED; NAS100 OFF; System Health INACTIVE; trading DB schema 3. No deploy has occurred.

The chronological record follows unchanged: P4.0 design & owner decision package, P4.1, P4.1A, DEC-4.6, DEC-4.7.

---

## P4.0 — design & owner decision package (historical)

As written at P4.0: P4.0 changed no production code; PAPER only; REAL DISABLED; NAS100 OFF; V2 runtime activation
NOT AUTHORIZED; System Health INACTIVE; trading DB schema 3.

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

---

## P4.1 — Implementation candidate (2026-10-06)

Status line: **P4.1 IMPLEMENTED — READY FOR INDEPENDENT REVIEW. P4.1 implementation ≠ Phase 4 certification**
(F04-T18 not certified). Policy D is reachable only through an explicit `planner_policy=POLICY_V2_D` argument
(tests/replay); the runtime has no path to it (flag OFF; runtime activation NOT AUTHORIZED).

### Owner decisions recorded

- **DEC-4.1 — Policy D (approved with modification).** Entry and invalidation from the certified authorities,
  frozen; first genuine structural target in the trade direction; exact gross R:R; eligible only for
  `2.0 <= R:R <= 5.0`; `< 2` → `RR_BELOW_FLOOR` (no plan); `> 5` → `OUT_OF_POLICY_EXTENDED_TARGET`
  (target and ratio kept as observation, never an order); no 5R synthesis; no ratio rounding to a band.
- **DEC-4.2 — single R:R authority (approved).** `core/rr_contract.py` used by the Phase 4 planner, Risk,
  the deterministic AI gate and the Paper Broker fill gate. Risk recomputes R:R from levels for **every**
  plan (V1 included) and rejects a declared/recomputed mismatch beyond `1e-9` relative (float noise only):
  the P4.0 MEDIUM finding (declared 3R / real 1R APPROVED) is fixed.
- **DEC-4.3 — replay data (approved conceptually, no purchase).** Separate Replay Store; acquisition via the
  existing Twelve Data key, plan `basic` (8 req/min, 800/day, no payment).
- **DEC-4.4 — gross R:R only.** `cost_model = NO_COST_MODEL`, `net_rr = effective_rr = UNAVAILABLE`. The legacy
  `PaperOrder.cost_rate = 0.0` is a compatibility value, not evidence of zero cost.

### Implementation

- `core/rr_contract.py`: `geometry` (LONG `stop < entry < target`, SHORT `target < entry < stop`, finite > 0;
  risk/reward as defined in DEC-4.2), exact `Decimal` ratio, `classify` (floor/ceiling inclusive), analytics
  `band`, `declared_matches`, directed `normalize` (quantized to the instrument increment).
- `agents/target_planner.py` (`plan_policy_d`): result codes `PLAN_READY`, `NO_VALID_TARGET`, `RR_BELOW_FLOOR`,
  `OUT_OF_POLICY_EXTENDED_TARGET`, `INVALID_GEOMETRY`, `INSUFFICIENT_TARGET_EVIDENCE`, `UNSUPPORTED_PRECISION`,
  `NOT_A_VALID_SETUP` (plan viability; Setup statuses untouched; the floor report keeps `PLAN_UNAVAILABLE`).
  - **Eligible target authority:** confirmed 15m/1h swing highs (LONG) / lows (SHORT) from the structure scout,
    only for timeframes whose payload lineage matches and whose Phase 3 evidence reference is `VALID`, confirmed
    at or before `as_of`, strictly beyond the entry. Equal-high/low liquidity: recorded as `HEURISTIC`, never
    selected. Volatility/sweeps: not used (`volatility = UNAVAILABLE`).
  - **First structural obstacle:** LONG nearest eligible level above entry; SHORT nearest below. Never skipped
    (1.7R then 3.5R → `RR_BELOW_FLOOR`). Same-price levels from several timeframes are merged (`sources`).
  - **Precision:** prices are normalized **before** comparison, with directed rounding that can only lower
    R:R (LONG entry↑ stop↓ target↓; SHORT entry↓ stop↑ target↑), quantized to the increment (XAU 0.01,
    EUR 0.00001); the ratio is exact on those values (no `1.0939999999999996`-style artifacts).
  - **Trace (`target_decision`, additive in the review report):** policy version, status/reason, setup_id,
    run_id, symbol, side, raw and normalized entry/stop, increment, every candidate (source, timeframe, authority,
    raw/normalized price, confirmation/pivot time, eligibility, reason), selected target + sources, exact gross
    R:R, band, risk/reward, cost-model status, `decision_id`.
  - **Idempotency:** `decision_id = uuid5(policy, setup_id, symbol, side, entry, stop, increment, candidates)`;
    run_id excluded; same across retry and a new process; a moved target gives a new id. `setup_id` untouched.
- `floor/orchestrator.run(..., planner_policy=POLICY_V1)`: default V1 (unchanged); unknown policy refused.
- `riesgo.py`: `crear_configuracion_riesgo_phase4()` (V2 sizing, `rr_policy=POLICY_V2_D`). Policy from Risk's
  own configuration (never from the plan): V1 keeps the `>= 3` declared check, then the recompute/mismatch
  check; Phase 4 requires `plan.policy_version == POLICY_V2_D`, recomputes, and accepts only 2R–5R
  (`rr_below_minimum` / `rr_above_maximum` / `rr_declared_mismatch` / `rr_policy_mismatch`). Sizing unchanged.
- AI: the trade-review request carries `rr_policy` only for Phase 4 plans (V1 requests byte-identical); the
  deterministic provider applies the V1 `< 3` rule to V1 plans and the 2R–5R contract to Phase 4 plans. AI
  returns a recommendation only; it cannot select, alter or round levels.
- Paper Broker: `PaperBroker(..., rr_policy=None)`; `None` keeps the frozen V1 expression. With `POLICY_V2_D`
  the fill gate recomputes R:R at the actual fill price with the contract: same-price fill as planned;
  favorable fill raises R:R (rejected if it exceeds 5R — out of policy); adverse fill lowers it (rejected
  below 2R); a gap through the stop is invalid geometry. SL/TP are never moved to rescue a fill.

### Replay foundation (F04-T15) and comparison (F04-T16)

`replay/` (offline; never imported by the runtime): `store.py` (separate Replay Store in the Phase 2 evidence
format; file name must contain `replay`; trading DB refused), `acquire.py` (backward paging, closed bars only,
chronological ingest), `engine.py` (lookahead-free: a decision at `t` sees only bars with `start + duration <= t`,
500-bar windows as the runtime; both policies run the production floor on the identical snapshot; fill at the
next 5m open with the policy's gate; SL/TP walk with V1 TradeManager precedence; independent per-decision
simulation; no trading DB, no PaperBroker, no AI), `compare.py` (plans, rejections by reason,
`NO_VALID_TARGET` / `RR_BELOW_FLOOR` / `OUT_OF_POLICY_EXTENDED_TARGET`, R:R distribution and bands, target
sources, fills, outcomes, reach/stop rates, gross R total/expectancy, max drawdown in R, session and symbol
distribution, data sufficiency; `historical_probability` only if ≥ 100 closed per symbol and ≥ 30 per band;
net/effective `UNAVAILABLE`).

### Real-data replay observation

**Observation only — not a historical performance or probability claim.** Replay Store
`data/replay/replay_twelve_data_12m.db` (git-ignored, never committed): Twelve Data `basic` plan (existing key,
no payment; 8 req/min, 800/day; ~44 requests used), XAU/USD and EUR/USD, 1h/15m/5m, 2025-10-06 → 2026-10-06:
XAU 8,406 / 33,606 / 100,799 bars, EUR 8,067 / 32,249 / 96,704 bars; GAP anomalies 14–46 per stream (recorded,
nothing invented). Data note: the provider includes weekend quotes (~1.9–2.1k 1h bars per symbol); no
decision is made on weekends (session calendar), but weekend bars sit inside the scouts' 500-bar windows exactly
as in the live runtime with the same provider. Licensing: provider terms; stored locally for internal research.

Run: decisions 2025-11-03 → 2026-10-05 (≥ 500 1h bars warm-up), **hourly cadence** (sampled: the unchanged
production scouts cost ~2.4 s per run; the scouts run once per slot and are shared by both policies), runtime
session gates, real scouts and Setup Validator, AI excluded, independent per-decision simulation, gross only.
6,661 slots per policy; identical setups across policies: **yes**; 491 VALID_SETUP (XAU 262, EUR 229).

| Metric | Policy 0 (V1) | Policy D |
|---|---|---|
| plans ready | 491 | **0** |
| rejected | — | RR_BELOW_FLOOR 462, NO_VALID_TARGET 29 |
| out-of-policy extended | — | 0 |
| fills / fill-rejected | 275 / **216 (44%)** | — |
| outcomes (closed) | STOP 187, TARGET 69, OPEN 19 | — |
| target reach / stop rate | 27.0% / 73.0% | — |
| gross expectancy / total / max DD | +0.083R / +21.3R / 68.5R | — |
| data sufficiency (≥100/symbol, ≥30/band) | met (128/128 closed; band 3_TO_4: 256) | not met |
| historical probability | ELIGIBLE_FOR_REVIEW (not computed) | UNAVAILABLE / INSUFFICIENT_EVIDENCE |

**Why Policy D produced no plan (diagnosis on all 491 setups):** the first genuine structural target gives
gross R:R median **0.011**, max 0.58 (XAU) / 0.58 (EUR) — never ≥ 2. Reward is tiny (a confirmed 15m/1h swing
almost always sits just beyond the entry: median 0.029% XAU, 0.006% EUR) and risk is large (the certified
invalidation is the window-extreme swing: median 2.55% XAU, 0.63% EUR). Informational only (not the certified
authority, not proposed): with the nearest protective 15m swing as stop, 26/262 (XAU) and 21/229 (EUR) would fall
within 2R–5R. **Policy D as approved is effectively a no-trade policy on this data; it is implemented exactly and
not changed.** The V1 fill gate rejects 44% of approved V1 plans (any adverse next-open gap breaks the fixed 3R).

### Tests

`test_phase4_target_planner.py` (Policy D matrix for EURUSD/XAUUSD LONG/SHORT at 1.5/2.0/2.4/3.0/3.6/4.0/
4.9/5.0/5.5R, just-below-2R on the grid, first obstacle never skipped, confluence, no target, insufficient
evidence, wrong-symbol / future / untrusted-reference evidence, heuristic liquidity never selected, NaN/Inf/
geometry/precision, rounding never manufactures 2R, exact 3/4/5R boundaries, frozen stop, idempotency incl.
new process, setup_id untouched, Risk 2R–5R and 10 adversarial inputs, AI gate, broker fill gate, flag OFF),
`test_phase4_replay.py` (identical setups across policies, outcomes, no lookahead incl. forming bar, no trading
DB / orders, separate store, simulation gates, real scouts end-to-end), `test_phase4_v1_planner_oracle.py`
(V1 economics; the declared-R:R row updated for DEC-4.2). Accepted isolation test
`test_market_evidence.test_not_wired_into_the_runtime` now allows the offline `replay/` package and asserts
no runtime module imports it.

### Status

F04-T01..T14: IMPLEMENTED (T05 volatility and T12 historical probability implemented as explicit
`UNAVAILABLE`; T10 session as replay metadata only). F04-T15: IMPLEMENTED (foundation + 12-month real-data run, hourly cadence).
F04-T16: IMPLEMENTED (gross only). F04-T17: IMPLEMENTED. F04-T18: NOT CERTIFIED.

### P4.1 findings and remaining owner decision

- **HIGH (policy outcome, not a code defect):** DEC-4.1 Policy D yields 0 eligible plans in 12 months of real data
  (first genuine target ≪ 2R because the certified invalidation is the window-extreme swing and nearby confirmed
  swings sit just beyond entry). Owner decision required before any further Phase 4 economics (candidate-target
  authority and/or invalidation authority — the latter belongs to the certified Setup Validator).
- MEDIUM: V1 fill gate rejects 44% of approved V1 plans (strict `>= 3` at the next open with a fixed 3R target).
- LOW: Twelve Data includes weekend quotes; GAP anomalies recorded. LOW: replay uses hourly sampled cadence
  (production scouts ~2.4 s/run).

---

## P4.1A — Target Policy Lab (DEC-4.5 option a, offline) — 2026-10-06

Status: **READY FOR OWNER DECISION.** Offline experiment only: no production planner/Risk/AI/broker change, no
runtime wiring, Phase 3 invalidation and setup_id untouched, trading DB untouched.

**Pre-registration.** Every variant, the discovery/holdout split and the mechanical selection rule are frozen in
the `replay/lab.py` docstring, sha256 `81e30c9c02135d048ea7d95c113128edb42a03d310cf238d3869a14e3108c114`, recorded
2026-10-06T12:37:47Z before any lab result; the file is unchanged since. No variant was modified after results.

Level classes: TARGET CANDIDATE (variant rule) · CONTEXT LEVEL (all other structure incl. internal 15m swings) ·
LIQUIDITY REFERENCE (heuristic equal levels, never a target) · INVALIDATION AUTHORITY (Phase 3 only) · BLOCKING
OBSTACLE (only a nearer target candidate). Common: certified entry and invalidation frozen and normalized as in
Policy D; nearest candidate beyond entry (never by ratio or outcome); exact gross R:R; tradeable iff 2R–5R; > 5R
observational only.

- **D0** current Policy D (production function, unchanged).
- **D1** unswept confirmed 1h swing (external 1h structure).
- **D2** prior completed weekday (UTC) high/low, unswept since that day ended.
- **D3** D1 + D2, nearest.
- **D4** external liquidity: **NOT TESTABLE / INSUFFICIENT EVIDENCE** (only the heuristic equal-level label exists).

**Replay.** Same 12-month store, **15-minute runtime cadence** (no cross-slot caching: scout reports carry the slot's
run_id/timestamp required by the Setup Validator lineage check; achieved by parallel compute; resumable per-chunk
checkpoints). 26,641 decision slots (discovery 17,121 / holdout 9,520); 1,941 VALID_SETUP (1,222 / 719). Discovery
2025-11-03 ≤ t < 2026-06-06; holdout 2026-06-06 ≤ t ≤ 2026-10-05. Weekend quotes never create decisions.

**Selection (discovery only; feasible = ≥ 30 tradeable, ≥ 10 per symbol, ≥ 2 R:R bands; order D1, D2, D3):**
D1 4 (XAU 4, EUR 0) infeasible · **D2 32 (XAU 17, EUR 15, 3 bands) feasible → chosen** · D3 4 infeasible.

| | D0 | D1 | D2 | D3 |
|---|---|---|---|---|
| Discovery: target available / NO_VALID_TARGET | 1150 / 71 | 1089 / 132 | 501 / 720 | 1089 / 132 |
| Discovery: <2 / 2–3 / 3–4 / 4–5 / >5 | 1150/0/0/0/0 | 1085/2/1/1/0 | 466/19/7/6/3 | 1085/2/1/1/0 |
| Discovery tradeable (% of valid) | 0 (0%) | 4 (0.33%) | **32 (2.62%)** | 4 (0.33%) |
| Discovery R:R median (all with a target) | 0.010 | 0.179 | 0.479 | 0.179 |
| **Holdout (D2 only, frozen)** tradeable | — | — | **21 (2.92%)**, XAU 8 / EUR 13 | — |
| Full 12 m tradeable (descriptive) | 0 | 7 | 53 (XAU 25 / EUR 28) | 5 |

D2 detail (full, descriptive): tradeable R:R min 2.02 / median 2.41 / max 4.86; bands 2–3: 38, 3–4: 8, 4–5: 7;
> 5R observational: 7; sources prior-day low 34 / high 19; sessions London 20, New York 18, overlap 15; all 53
APPROVED by the unchanged Phase 4 Risk. Distances (all valid setups): stop median 1.40% of entry; first target
median 0.013% (D0), 0.22% (D1), 0.51% (D2).

**Observational outcomes (gross, no costs; never a selection input; probabilities UNAVAILABLE — samples below
100/symbol and 30/band):** D2 discovery 2 target / 30 stop (−0.74R per trade, −23.5R); D2 holdout 3 / 17 / 1
unresolved (−0.45R, −9.0R). D1 full 1 / 4 / 2.

**V1 benchmark (15-minute, unchanged, AI excluded):** 1,940 plans; fill-rejected 47.6% (discovery 44.6%,
holdout 52.9%); closed target rate 31.0%; gross expectancy +0.24R (discovery +0.38R, holdout −0.05R); max drawdown
220.9R.

**Interpretation.** Under every tested target definition the binding constraint is the certified invalidation
distance (median 1.40% of entry): genuine structural targets sit far closer, so 2R–5R is reached in at most ~2.7%
of valid setups (D2), and D2's observational outcomes are stop-dominated in both discovery and holdout.

---

## DEC-4.6 — FIXED 3R V2 POLICY (2026-10-06)

**Owner decision.** The V2 Trade Planner target policy is **fixed 1:3 R:R**, chosen for simplicity, deterministic
trade management and comparability across the planned 60-day V2 PAPER period. This does not claim variable R:R is
universally inferior. DEC-4.6 supersedes the proposed P4.1B invalidation lab: it was stopped before completion,
none of its results were used, and its files were not committed. **Phase 3 stays certified, closed and merged;**
Setup Validator and setup_id semantics are unchanged.

**Research record (unchanged above).** Structural target policies D0–D4 were researched (P4.1, P4.1A). D2
(prior completed-day high/low) was the only meaningful non-zero candidate: 53 / 1,941 valid setups (2.73%), with
insufficient sample and stop-dominated observational outcomes. The Owner therefore did not adopt structural levels
as target authority for V2 at this time. This does not mean 2R, 4R or 5R are inherently invalid.

**Contract (implemented; explicit opt-in `planner_policy=POLICY_V2_F3`, no runtime path).**
VALID_SETUP → certified structural invalidation = SL → reference entry → TP = entry ± 3 × risk → Risk → AI →
PAPER fill validation. The SL is never derived from a ratio and never moved; TP is never relocated.
- `agents/target_planner.plan_fixed_3r`: entry and SL are normalized with the Policy D conservative rounding
  (LONG entry up / SL down; SHORT entry down / SL up); risk is a grid multiple, so TP is exact on the grid and the
  planned R:R is exactly 3 (Decimal). Invalid geometry (zero/negative risk, NaN, ±Inf, wrong side, SHORT TP ≤ 0)
  fails closed. Trace: setup_id, run_id, symbol, side, raw and normalized entry/SL, increment, risk, reward, TP,
  planned and declared R:R, cost-model status, deterministic `decision_id` (run_id excluded).
- Structural levels (15m/1h swings beyond entry) are recorded as `observational_context` (level, R:R at the level,
  whether TP lies beyond it). They never move TP/SL and never reject a plan.
- `core/rr_contract`: `POLICY_V2_F3` planned limits exactly 3; Risk (`crear_configuracion_riesgo_fixed_3r`)
  recomputes from levels and rejects declared/recomputed mismatch, anything not exactly 3R, and policy mismatch.
- AI deterministic gate: the fixed-3R plan follows the contract (the legacy `< 3` rule cannot reject it); AI cannot
  change entry, SL, TP or R:R.
- Paper Broker (`rr_policy=POLICY_V2_F3`): recomputes the actual geometry at the fill with the frozen SL/TP and
  records it (`fill_geometry`: fill price, SL, TP, actual risk, reward, R:R) on ORDER_FILLED / ORDER_REJECTED; it
  never repairs levels. **Fill acceptance is PROVISIONAL:** the existing V1 rule (actual R:R ≥ 3, no upper bound)
  is inherited unchanged pending DEC-4.7; no tolerance was invented.
- Gross only (DEC-4.4): net/effective UNAVAILABLE; no cost model is not zero cost.

### Fill-geometry audit (evidence for DEC-4.7)

`replay/fill_audit.py` rebuilt the planned geometry of all 1,940 V1 plan slots from P4.1A (identical VALID setups;
no scout rerun) and filled them with frozen SL/TP under two models:
**NEXT_BAR** (open of the 5m bar starting at t — the P4.1/P4.1A replay model) and **NEXT_CYCLE** (open of the bar
starting at t + 10 min — the current-cycle bar of the next 15-minute runtime cycle, where the runtime and the P1 gate
actually evaluate a pending order).

| Model / policy | Fill-rejected | Rejected fills that were adverse | Displacement (R) p05 / median / p95 | Median actual R:R |
|---|---|---|---|---|
| NEXT_BAR · V1 | 924 / 1,940 (47.63%) — reproduces P4.1A | 924 / 924 | −0.011 / 0.000 / +0.011 | 3.000 |
| NEXT_BAR · V2 fixed 3R | 913 (47.06%) | 913 / 913 | −0.011 / 0.000 / +0.011 | 3.000 |
| NEXT_CYCLE · V1 | 919 (47.37%) | 918 / 919 (1 gap through SL) | −0.085 / −0.002 / +0.089 | 3.006 |
| NEXT_CYCLE · V2 fixed 3R | 919 (47.37%) | 918 / 919 | −0.085 / −0.002 / +0.089 | 3.007 |

(Displacement > 0 = adverse; holdout rejection 52.6–52.9% NEXT_BAR, 48.7% NEXT_CYCLE.) Planned R:R is exactly 3.0
for all V2 plans (V1: 3.0 within float noise).

**Findings.**
1. Planned entry = last closed 5m close at t (not an executable price); the PAPER fill is the next evaluated bar's
   open, so it always differs by the market move between them.
2. The rejection is decided by **sign, not size**: with a fixed TP, any adverse displacement — median 0.0023R
   (NEXT_BAR) / 0.025R (NEXT_CYCLE) — makes actual R:R < 3, so the strict `≥ 3` rule rejects every adverse fill and
   accepts every favorable one. About half of fills are adverse, hence ~47–53% rejections. It is a coin-flip filter,
   not protection against meaningful geometry degradation, and it biases accepted trades toward favorable fills.
3. The replay's NEXT_BAR model understates the runtime: the runtime fills ~10 minutes later (NEXT_CYCLE), with ~10×
   larger displacement (p95 0.089R → actual R:R ≈ 2.67 at the 95th adverse percentile), yet a similar rejection rate.
4. Next-bar-open mechanics plus the fixed-TP strict rule are the primary cause; the 47.6% is an artifact of the PAPER
   fill model rather than a protective signal.

Observational, gross, AI excluded, no costs: NEXT_CYCLE V2 fixed 3R — 1,021 filled; target 276 / stop 674 / open 71;
gross expectancy +0.21R (holdout −0.01R). Not a performance claim; probabilities UNAVAILABLE.

**Owner decision required — DEC-4.7 (fill policy).** Options for the owner (no recommendation of a numeric
tolerance is made here): (a) keep strict `≥ 3` at the fill (status quo; ~47–53% rejected, favorable-fill bias);
(b) accept a fill when its actual R:R is at least an owner-chosen floor (evidence above gives the displacement
distribution); (c) change the PAPER execution model to a limit order at the planned entry (fill only when price
trades at/through it, so actual R:R ≥ 3 by construction; unfilled orders expire) — an execution-mechanics change;
(d) re-anchoring TP at the fill is excluded by DEC-4.6 (TP immutable).

---

## DEC-4.7 — FIXED-3R FILL EXECUTION POLICY (2026-10-06)

**Owner decision (option b).** TARGET POLICY stays **fixed 3.00R** (DEC-4.6). EXECUTION FLOOR: a fixed-3R plan
executes when its **actual fill-time R:R ≥ 2.50**, all other existing execution gates passing. 2.50 is an
owner-approved execution tolerance, not a statistically optimized value and not a "2.5R strategy": the plan remains a
fixed-3R plan that experienced execution displacement. The floor is frozen for the 60-day PAPER period unless a
future Owner Decision changes it; there is no automatic adaptation.

**Contract.** Planning: certified structural SL frozen → reference entry → TP exactly 3R → Risk verifies exact
planned 3R (declared R:R never overrides geometry). Fill: actual fill + frozen SL + frozen TP → `actual_fill_rr`
recomputed with the single contract (`FILL_LIMITS[POLICY_V2_F3] = (2.50, no upper bound)`); accept ≥ 2.50, reject
< 2.50 (`fill_rr_below_minimum`); a fill at/through the SL (or beyond the TP) is invalid geometry
(`fill_invalid_geometry`); the existing equity/real-risk gates are unchanged (`post_fill_risk_or_geometry`). Favorable
fills (> 3R) are recorded truthfully, never normalized back. SL/TP are never moved, re-anchored or repaired; no
limit-order conversion. V1 (`rr_policy=None`) keeps its frozen strict fill expression and event details.

**60-day PAPER telemetry (broker `fill_geometry`, on ORDER_FILLED and ORDER_REJECTED):** policy, planned entry, actual
fill, SL, TP, planned R:R, actual fill R:R, floor, actual risk/reward, displacement in price and in R, direction
(FAVORABLE/EQUAL/ADVERSE), rejection reason; symbol/run_id/order_id on the event; setup_id via the execution record;
session via the run. Phase 4 runtime remains OFF, so this is ready at the broker level for when the path is wired.

### Replay comparison — strict ≥ 3 vs DEC-4.7 ≥ 2.50 (same 1,940 fixed-3R plans; planned R:R exactly 3.00 for all)

| Fill model | Rule | Fills | Rejections | Favorable / equal / adverse accepted | Actual R:R < 2.5 / 2.5–3 / ≥ 3 | Gap through SL |
|---|---|---|---|---|---|---|
| NEXT_CYCLE (runtime) | strict ≥ 3 | 1,021 | 919 (47.37%) | 1,003 / 18 / 0 | 29 / 889 / 1,021 | 1 |
| NEXT_CYCLE (runtime) | **DEC-4.7 ≥ 2.50** | **1,910** | **30 (1.55%)** | 1,003 / 18 / 889 | 29 / 889 / 1,021 | 1 |
| NEXT_BAR (replay) | strict ≥ 3 | 1,027 | 913 (47.06%) | 885 / 142 / 0 | 1 / 912 / 1,027 | 0 |
| NEXT_BAR (replay) | **DEC-4.7 ≥ 2.50** | **1,939** | **1 (0.05%)** | 885 / 142 / 912 | 1 / 912 / 1,027 | 0 |

NEXT_CYCLE actual fill R:R: p01 2.457 · p05 2.674 · p25 2.903 · median 3.007 · p75 3.105 · p95 3.370.
Discovery / holdout (NEXT_CYCLE, DEC-4.7): 1,211 / 699 fills; rejections 10 (0.82%) / 20 (2.78%).
V1 strict reproduces P4.1A (924 NEXT_BAR; 919 NEXT_CYCLE).

Observational only (gross, no costs, AI excluded; never an input to DEC-4.7): NEXT_CYCLE DEC-4.7 — target 541 /
stop 1,232 / open 137, +0.22R per closed trade (discovery positive, holdout −0.01R); strict ≥ 3 — +0.21R. Net
UNAVAILABLE; historical probability not computed.
