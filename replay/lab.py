"""V2 Phase 4 / P4.1A (DEC-4.5 option a): OFFLINE target-policy lab. Never wired into production.

PRE-REGISTRATION (frozen before any lab result was computed; any change requires a new variant ID)
=================================================================================================
Common to every variant (identical to Policy D except the TARGET CANDIDATE set):
  entry  = certified V1 entry (last closed 5m close <= t), normalized LONG up / SHORT down;
  stop   = certified Phase 3 invalidation (setup.invalidation), normalized LONG down / SHORT up — never moved;
  target = the NEAREST legitimate TARGET CANDIDATE strictly beyond entry in the trade direction (never chosen by
           ratio or outcome), normalized toward entry (LONG down / SHORT up);
  ratio  = exact gross R:R via core.rr_contract; tradeable iff 2.0 <= R:R <= 5.0; < 2 REJECT (RR_BELOW_FLOOR);
           > 5 OUT_OF_POLICY_EXTENDED_TARGET (observational only). No 5R synthesis, no band rounding.
Level classes: TARGET CANDIDATE (variant rule below) · CONTEXT LEVEL (every other structural level, incl. all
internal 15m swings) · LIQUIDITY REFERENCE (equal highs/lows: heuristic, never a target) · INVALIDATION
AUTHORITY (Phase 3 only) · BLOCKING OBSTACLE (only a nearer TARGET CANDIDATE blocks a farther one).
Only evidence available at t: snapshot bars with start + duration <= t; scout swings confirmed <= t.

D0  Current Policy D, unchanged (agents.target_planner.plan_policy_d): first eligible 15m/1h confirmed swing.
D1  EXTERNAL 1H STRUCTURE. Candidates: confirmed 1h swing highs (LONG) / lows (SHORT) from the certified
    structure scout (setup.evidence 1h swings, confirmation <= t) that are still UNSWEPT at t: no closed 1h bar
    after the pivot bar, and no closed 5m bar after the last closed 1h bar, traded at or beyond the level
    (high >= level LONG / low <= level SHORT). Unswept = the level still bounds the 1h range ("external").
D2  PRIOR COMPLETED TRADING DAY. Trading day = UTC calendar date Monday-Friday (Saturday/Sunday provider quotes
    are never a "prior day"; documented rule, not a market calendar). Prior day P = the latest weekday date
    < date(t) with closed 1h bars. Candidate: P's high (LONG) / low (SHORT) from its 1h bars, only if UNSWEPT
    since P ended (all closed 1h bars after P and 5m bars after the last 1h bar, weekend quotes included).
D3  D1 candidates + D2 candidate; nearest wins.
D4  EXTERNAL LIQUIDITY: the only liquidity evidence is the equal-high/low HEURISTIC label -> NOT TESTABLE /
    INSUFFICIENT EVIDENCE (not promoted to authority, not run).

Split (decision timestamps, UTC; data 2025-10-06..2026-10-06; decisions start after 500 1h bars of warm-up):
  DISCOVERY 2025-11-03 00:00 <= t < 2026-06-06 00:00   (first 8 months of the 12-month dataset)
  HOLDOUT   2026-06-06 00:00 <= t <= 2026-10-05 00:00  (final 4 months)
Selection rule (applied to DISCOVERY only, mechanical): a variant is FEASIBLE iff tradeable >= 30 overall,
>= 10 per symbol, and its tradeable plans span >= 2 R:R bands. Choose the first feasible variant in simplicity
order D1, D2, D3 (fewest candidate classes first). The chosen variant is then reported on HOLDOUT once,
unchanged. Outcomes/PnL are never a selection input.
"""

import pandas as pd

from agents.target_planner import _entry
from core.rr_contract import (POLICY_V2_D, ROUND_CEILING, ROUND_FLOOR, WITHIN_POLICY, band, classify, geometry,
                              normalize, to_decimal)

VARIANTS = ("D1", "D2", "D3")
DISCOVERY_END = pd.Timestamp("2026-06-06T00:00:00Z")
HOUR = pd.Timedelta(hours=1)
FIVE = pd.Timedelta(minutes=5)


def _beyond(side, level, entry):
    return level > entry if side == "LONG" else level < entry


def _swept(side, level, bars):
    if bars is None or bars.empty:
        return False
    return bool((bars["High"] >= level).any()) if side == "LONG" else bool((bars["Low"] <= level).any())


def _after(snapshot, since):
    """Closed bars strictly after ``since``: 1h bars starting at/after ``since`` plus 5m bars after the last 1h."""
    one_h = snapshot["1h"]
    later_1h = one_h.loc[one_h.index >= since]
    last_1h_end = one_h.index[-1] + HOUR if len(one_h) else since
    five = snapshot["5m"]
    later_5m = five.loc[five.index >= max(since, last_1h_end)]
    return pd.concat([later_1h[["High", "Low"]], later_5m[["High", "Low"]]])


def d1_candidates(setup, snapshot, slot, side):
    structure = setup.evidence[0].get("1h") if setup.evidence else None
    swings = (structure or {}).get("swings") or {}
    out = []
    for swing in swings.get("highs" if side == "LONG" else "lows") or ():
        price, confirmed, pivot = swing.get("price"), swing.get("timestamp"), swing.get("pivot_timestamp")
        if to_decimal(price) is None or confirmed is None or pivot is None:
            continue
        confirmed, pivot = pd.Timestamp(confirmed), pd.Timestamp(pivot)
        if confirmed.tzinfo is None or confirmed > slot:
            continue  # future (or unverifiable) confirmation: never a candidate
        if _swept(side, float(price), _after(snapshot, pivot + HOUR)):
            continue
        out.append({"source": "external_1h_swing", "price": float(price), "pivot_at": pivot.isoformat()})
    return out


def prior_day(snapshot, slot):
    """(date, high, low) of the latest completed weekday before date(slot), from closed 1h bars only."""
    one_h = snapshot["1h"]
    today = pd.Timestamp(slot).tz_convert("UTC").normalize()
    days = sorted({d for d in one_h.index.normalize() if d < today and d.dayofweek < 5})
    if not days:
        return None
    day = days[-1]
    bars = one_h.loc[(one_h.index >= day) & (one_h.index < day + pd.Timedelta(days=1))]
    return day, float(bars["High"].max()), float(bars["Low"].min())


def d2_candidates(setup, snapshot, slot, side):
    found = prior_day(snapshot, slot)
    if found is None:
        return []
    day, high, low = found
    level = high if side == "LONG" else low
    if _swept(side, level, _after(snapshot, day + pd.Timedelta(days=1))):
        return []
    return [{"source": "prior_day_high" if side == "LONG" else "prior_day_low", "price": level,
             "day": day.date().isoformat()}]


def candidates(variant, setup, snapshot, slot, side):
    if variant == "D1":
        return d1_candidates(setup, snapshot, slot, side)
    if variant == "D2":
        return d2_candidates(setup, snapshot, slot, side)
    if variant == "D3":
        return d1_candidates(setup, snapshot, slot, side) + d2_candidates(setup, snapshot, slot, side)
    raise ValueError("unknown lab variant")


def evaluate(variant, setup, snapshot, slot, instrument):
    """Lab target decision for one VALID setup. Same frozen entry/stop as Policy D; only candidates differ."""
    side, increment = setup.side, instrument.price_increment
    entry_raw = _entry({"data": snapshot["5m"]}, slot)
    entry = normalize(entry_raw, increment, ROUND_CEILING if side == "LONG" else ROUND_FLOOR) if entry_raw else None
    stop = normalize(setup.invalidation, increment, ROUND_FLOOR if side == "LONG" else ROUND_CEILING)
    result = {"variant": variant, "entry": None if entry is None else str(entry),
              "stop": None if stop is None else str(stop), "status": None, "target": None, "source": None,
              "rr": None, "band": None, "candidates": 0}
    if entry is None or stop is None or not _beyond("SHORT" if side == "LONG" else "LONG", stop, entry):
        return {**result, "status": "INVALID_GEOMETRY"}
    rounding = ROUND_FLOOR if side == "LONG" else ROUND_CEILING
    pool = []
    for c in candidates(variant, setup, snapshot, slot, side):
        target = normalize(c["price"], increment, rounding)
        if target is not None and _beyond(side, target, entry):
            pool.append((abs(target - entry), c["source"], target, c))
    result["candidates"] = len(pool)
    if not pool:
        return {**result, "status": "NO_VALID_TARGET"}
    _, source, target, _ = min(pool, key=lambda item: (item[0], item[1]))  # nearest; never by ratio
    shape, _ = geometry(side, entry, stop, target)
    outcome = classify(shape.rr, POLICY_V2_D)
    status = "TRADEABLE" if outcome == WITHIN_POLICY else outcome
    return {**result, "status": status, "target": str(target), "source": source, "rr": str(shape.rr),
            "band": band(shape.rr), "risk": str(shape.risk), "reward": str(shape.reward)}


__all__ = ["DISCOVERY_END", "VARIANTS", "candidates", "evaluate", "prior_day"]
