"""V2 Phase 4 (DEC-4.1): Trade Planner Policy D — invalidation first, first genuine structural target.

Only used when a caller explicitly selects ``POLICY_V2_D`` (default everywhere: V1). Order, never inverted:
VALID_SETUP -> authoritative entry -> authoritative invalidation (frozen stop) -> candidate targets from
existing deterministic evidence (independent of any ratio) -> the FIRST eligible obstacle in the trade
direction -> exact gross R:R from geometry -> 2R..5R policy. The stop is never moved and no target is ever
synthesized; a nearer obstacle is never skipped for a prettier ratio.

Eligible target authority: confirmed 15m and 1h swing highs (LONG) / lows (SHORT) from the structure scout
(deterministic, one closed confirmation bar), from timeframes whose Phase 3 evidence reference is VALID.
Equal-high/low liquidity is recorded as heuristic context and never selected.

Precision (DEC-4.1/4.2): prices are put on the instrument grid with conservative directed rounding before
any comparison — LONG: entry up, stop down, target down; SHORT: entry down, stop up, target up — so
normalization can only lower R:R and can never manufacture eligibility. The ratio is exact (Decimal).
"""
import json
import math
import uuid
from decimal import Decimal

import pandas as pd

from core.contracts import SetupAssessment, TradePlan
from core.rr_contract import (OUT_OF_POLICY_EXTENDED_TARGET, POLICY_V2_D, ROUND_CEILING, ROUND_FLOOR,
                              RR_BELOW_FLOOR, WITHIN_POLICY, band, classify, geometry, normalize, to_decimal)
from core.timeframes import mask_hasta_as_of

POLICY_VERSION = POLICY_V2_D
CANDIDATE_TIMEFRAMES = ("15m", "1h")
# Phase 4 plan result codes (plan viability; distinct from Setup statuses).
PLAN_READY = "PLAN_READY"
NO_VALID_TARGET = "NO_VALID_TARGET"
INVALID_GEOMETRY = "INVALID_GEOMETRY"
INSUFFICIENT_TARGET_EVIDENCE = "INSUFFICIENT_TARGET_EVIDENCE"
UNSUPPORTED_PRECISION = "UNSUPPORTED_PRECISION"
NOT_A_VALID_SETUP = "NOT_A_VALID_SETUP"
COST_MODEL = {"cost_model": "NO_COST_MODEL", "net_rr": "UNAVAILABLE", "effective_rr": "UNAVAILABLE"}


def _entry(datos_5m, as_of):
    """The V1 entry authority, unchanged: last closed 5m close at or before as_of."""
    if not isinstance(datos_5m, dict) or not datos_5m:
        return None
    frame = datos_5m.get("data")
    if frame is None or not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    frame = frame.sort_index()
    mask = mask_hasta_as_of(frame.index, as_of)
    if mask is None:
        return None
    frame = frame.loc[mask & (frame["is_closed"] == True)] if "is_closed" in frame.columns else frame.loc[mask]  # noqa: E712
    if frame.empty or "Close" not in frame.columns:
        return None
    value = frame["Close"].iloc[-1]
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and value > 0 else None


def _iso(value):
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError):
        return None
    return None if stamp is pd.NaT or stamp.tzinfo is None else stamp.tz_convert("UTC").isoformat()


def _structure(setup, timeframe):
    evidence = setup.evidence[0] if setup.evidence and isinstance(setup.evidence[0], dict) else {}
    payload = evidence.get(timeframe)
    return payload if isinstance(payload, dict) else None


def _ref_valid(setup, timeframe):
    refs = (setup.explanation or {}).get("evidence_refs", ())
    return any(r.get("source") == "structure" and r.get("timeframe") == timeframe and r.get("ref_state") == "VALID"
               for r in refs if isinstance(r, dict))


def _candidates(setup, side, entry, increment, as_of):
    """Every structural level considered, eligible or not, plus whether any trustworthy evidence existed."""
    rounding = ROUND_FLOOR if side == "LONG" else ROUND_CEILING
    cutoff = pd.Timestamp(as_of)
    records, trustworthy = [], False
    for timeframe in CANDIDATE_TIMEFRAMES:
        payload = _structure(setup, timeframe)
        if payload is None or not isinstance(payload.get("swings"), dict):
            continue
        lineage_ok = payload.get("symbol") == setup.symbol and payload.get("timeframe") == timeframe
        ref_ok = _ref_valid(setup, timeframe)
        trustworthy = trustworthy or (lineage_ok and ref_ok)
        for swing in payload["swings"].get("highs" if side == "LONG" else "lows") or ():
            record = {"source": f"swing_{'high' if side == 'LONG' else 'low'}_{timeframe}", "timeframe": timeframe,
                      "authority": "DETERMINISTIC_STRUCTURE", "price_raw": None, "target_price": None,
                      "confirmed_at": None, "pivot_at": None, "eligible": False, "reason": None}
            price = to_decimal(swing.get("price")) if isinstance(swing, dict) else None
            confirmed = _iso(swing.get("timestamp")) if isinstance(swing, dict) else None
            record.update(price_raw=None if price is None else str(price), confirmed_at=confirmed,
                          pivot_at=_iso(swing.get("pivot_timestamp")) if isinstance(swing, dict) else None)
            target = None if price is None else normalize(price, increment, rounding)
            record["target_price"] = None if target is None else str(target)
            if not lineage_ok:
                record["reason"] = "lineage_mismatch"
            elif not ref_ok:
                record["reason"] = "evidence_ref_not_valid"
            elif price is None or target is None:
                record["reason"] = "invalid_price"
            elif confirmed is None:
                record["reason"] = "invalid_timestamp"
            elif pd.Timestamp(confirmed) > cutoff:
                record["reason"] = "future_evidence"
            elif (side == "LONG" and not target > entry) or (side == "SHORT" and not target < entry):
                record["reason"] = "not_beyond_entry"
            else:
                record.update(eligible=True, reason="eligible", distance=str(abs(target - entry)))
            records.append(record)
    liquidity = setup.evidence[1] if len(setup.evidence) > 1 and isinstance(setup.evidence[1], dict) else {}
    for timeframe in CANDIDATE_TIMEFRAMES:
        payload = liquidity.get(timeframe) if isinstance(liquidity.get(timeframe), dict) else {}
        for level in payload.get("liquidity_above" if side == "LONG" else "liquidity_below") or ():
            price = to_decimal(level.get("price")) if isinstance(level, dict) else None
            if price is not None and ((side == "LONG" and price > entry) or (side == "SHORT" and price < entry)):
                records.append({"source": f"equal_level_{timeframe}", "timeframe": timeframe,
                                "authority": "HEURISTIC", "price_raw": str(price), "target_price": None,
                                "eligible": False, "reason": "heuristic_not_target_authority"})
    return records, trustworthy


def _decision_id(decision):
    identity = {key: decision.get(key) for key in ("policy_version", "setup_id", "symbol", "side", "entry", "stop",
                                                    "price_increment")}
    identity["candidates"] = sorted((c.get("source"), c.get("price_raw"), c.get("confirmed_at"), c.get("eligible"))
                                    for c in decision["candidates"])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, json.dumps(identity, sort_keys=True, default=str)))


def plan_policy_d(setup, datos_5m, symbol, run_id, as_of, instrument):
    """(TradePlan or None, target_decision). A plan exists only for PLAN_READY (2R <= R:R <= 5R)."""
    decision = {"policy_version": POLICY_VERSION, "status": None, "reason": None, "run_id": run_id,
                "setup_id": (getattr(setup, "explanation", None) or {}).get("setup_id"), "symbol": symbol,
                "side": getattr(setup, "side", None), "entry": None, "stop": None, "entry_raw": None, "stop_raw": None,
                "price_increment": None, "selected": None, "gross_rr": None, "band": None, "candidates": [],
                "volatility": "UNAVAILABLE", "historical_probability": "UNAVAILABLE / INSUFFICIENT_EVIDENCE",
                **COST_MODEL}

    def done(status, reason, plan=None):
        decision.update(status=status, reason=reason)
        decision["decision_id"] = _decision_id(decision)
        return plan, decision

    if not isinstance(setup, SetupAssessment) or setup.status != "VALID_SETUP" or setup.symbol != symbol:
        return done(NOT_A_VALID_SETUP, "setup_not_valid_for_symbol")
    side = setup.side
    increment = getattr(instrument, "price_increment", None)
    if instrument is None or getattr(instrument, "symbol", None) != symbol or to_decimal(increment) is None:
        return done(UNSUPPORTED_PRECISION, "instrument_precision_unavailable")
    decision["price_increment"] = str(to_decimal(increment))
    entry_raw, stop_raw = _entry(datos_5m, as_of), to_decimal(setup.invalidation)
    decision.update(entry_raw=None if entry_raw is None else repr(entry_raw), stop_raw=None if stop_raw is None else str(stop_raw))
    if entry_raw is None or stop_raw is None or side not in ("LONG", "SHORT"):
        return done(INVALID_GEOMETRY, "entry_or_invalidation_unavailable")
    # Invalidation first: entry and stop are fixed here and never revisited (conservative grid rounding).
    entry = normalize(entry_raw, increment, ROUND_CEILING if side == "LONG" else ROUND_FLOOR)
    stop = normalize(stop_raw, increment, ROUND_FLOOR if side == "LONG" else ROUND_CEILING)
    decision.update(entry=None if entry is None else str(entry), stop=None if stop is None else str(stop))
    if entry is None or stop is None or (side == "LONG" and not stop < entry) or (side == "SHORT" and not stop > entry):
        return done(INVALID_GEOMETRY, "invalid_entry_stop_geometry")
    candidates, trustworthy = _candidates(setup, side, entry, increment, as_of)
    decision["candidates"] = candidates
    if not trustworthy:
        return done(INSUFFICIENT_TARGET_EVIDENCE, "no_trustworthy_structural_evidence")
    eligible = [c for c in candidates if c["eligible"]]
    if not eligible:
        return done(NO_VALID_TARGET, "no_structural_target_beyond_entry")
    first = min(eligible, key=lambda c: (abs(Decimal(c["target_price"]) - entry), c["source"]))
    target = Decimal(first["target_price"])
    sources = sorted(c["source"] for c in eligible if c["target_price"] == first["target_price"])
    shape, why = geometry(side, entry, stop, target)
    if shape is None:
        return done(INVALID_GEOMETRY, why)
    outcome = classify(shape.rr, POLICY_VERSION)
    decision.update(selected={**first, "sources": sources}, gross_rr=str(shape.rr), band=band(shape.rr),
                    risk=str(shape.risk), reward=str(shape.reward))
    if outcome == RR_BELOW_FLOOR:
        return done(RR_BELOW_FLOOR, "first_structural_target_below_2r")
    if outcome == OUT_OF_POLICY_EXTENDED_TARGET:
        return done(OUT_OF_POLICY_EXTENDED_TARGET, "first_structural_target_above_5r_observational_only")
    assert outcome == WITHIN_POLICY
    plan = TradePlan("1.0", symbol, side, "5m", float(entry), float(stop), float(target), float(shape.rr),
                     evidence=setup.evidence, invalidation=str(stop), run_id=run_id, as_of=as_of,
                     policy_version=POLICY_VERSION)
    plan, decision = done(PLAN_READY, "first_structural_target_within_2r_5r", plan)
    return plan, decision


__all__ = ["INSUFFICIENT_TARGET_EVIDENCE", "INVALID_GEOMETRY", "NOT_A_VALID_SETUP", "NO_VALID_TARGET", "PLAN_READY",
           "POLICY_VERSION", "UNSUPPORTED_PRECISION", "plan_policy_d"]
