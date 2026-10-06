"""Chronological, lookahead-free replay of Policy 0 (frozen V1) vs Policy D (Phase 4) on identical evidence.

At each decision slot ``t`` the snapshot holds only committed bars closed by ``t`` (``start + duration <= t``),
the same provider window size as the runtime (500 bars per timeframe). Both policies run the production
floor (scouts -> Setup Validator -> planner -> Risk) on that one snapshot; AI is excluded for both. The
outcome walk uses bars after ``t`` only to simulate the fill (next 5m open, the policy's fill gate) and the
SL/TP management (V1 TradeManager precedence: gap stop, gap target, stop, target). Pure computation: no
trading DB, no PaperBroker, no orders. Each decision is simulated independently (no portfolio/position
interaction — Phase 5/6). Gross only: no cost model (DEC-4.4).
"""
from dataclasses import dataclass, field
from datetime import timedelta
from unittest.mock import patch

import pandas as pd

from core.rr_contract import FILL_LIMITS, POLICY_V1, POLICY_V2_D, WITHIN_POLICY, classify, geometry
from data.macro_news import NoMacroDataProvider
from data.market_evidence import TIMEFRAMES
from floor.orchestrator import run as run_floor
from riesgo import crear_configuracion_riesgo_phase4, crear_configuracion_riesgo_v2
from runtime.gates import fresh_snapshot
from runtime.paper_contracts import paper_instruments
from runtime.scheduler import session_names

WINDOW = 500  # Same default window as the runtime Twelve Data provider (outputsize=500).
MAX_AGE = (("1h", 7200), ("15m", 1800), ("5m", 600))
SESSIONS = ("LONDON", "NEW_YORK")
EQUITY = 10000.0
POLICIES = {POLICY_V1: crear_configuracion_riesgo_v2, POLICY_V2_D: crear_configuracion_riesgo_phase4}


@dataclass(frozen=True)
class Decision:
    policy: str
    symbol: str
    slot: object
    session: tuple
    setup_status: str
    setup_id: object
    final_status: str
    plan_status: str  # PLAN_READY or the Phase 4/V1 reason the plan is unavailable/rejected
    rr: object = None
    band: object = None
    target_source: object = None
    entry: object = None
    stop: object = None
    target: object = None
    side: object = None
    fill: str = "NOT_APPLICABLE"  # FILLED / FILL_REJECTED / NO_FILL_DATA / NOT_APPLICABLE
    fill_price: object = None
    outcome: str = "NOT_APPLICABLE"  # TARGET / STOP / OPEN / NOT_APPLICABLE
    exit_at: object = None
    r_multiple: object = None
    extended_observation: dict = field(default_factory=dict)


def load_frames(engine, symbol):
    """All committed bars of the symbol as frames (chronological); read once, sliced per slot."""
    frames = {}
    for timeframe in ("1h", "15m", "5m"):
        bars = engine.committed(symbol, timeframe)
        frames[timeframe] = pd.DataFrame(
            {"Open": [b.open for b in bars], "High": [b.high for b in bars], "Low": [b.low for b in bars],
             "Close": [b.close for b in bars], "symbol": symbol, "is_closed": True},
            index=pd.DatetimeIndex([b.start for b in bars]))
    return frames


def snapshot_at(frames, slot):
    """Committed bars closed by ``slot`` only (no lookahead), last WINDOW per timeframe."""
    out = {}
    for timeframe, frame in frames.items():
        closed = frame.loc[frame.index + TIMEFRAMES[timeframe] <= slot]
        out[timeframe] = closed.iloc[-WINDOW:]
    return out


def _fill_rejected(policy, side, fill_price, stop, target):
    risk = (fill_price - stop) if side == "LONG" else (stop - fill_price)
    reward = (target - fill_price) if side == "LONG" else (fill_price - target)
    if risk <= 0 or reward <= 0:
        return True
    if policy == POLICY_V1:
        return reward / risk < 3  # Frozen V1 broker expression.
    shape, _ = geometry(side, fill_price, stop, target)
    return shape is None or classify(shape.rr, policy, FILL_LIMITS) != WITHIN_POLICY


def simulate(policy, frame_5m, slot, side, stop, target):
    """(fill, fill_price, outcome, exit_at, r_multiple) using only bars that start at or after ``slot``."""
    after = frame_5m.loc[frame_5m.index >= slot]
    if after.empty:
        return "NO_FILL_DATA", None, "NOT_APPLICABLE", None, None
    fill_price = float(after["Open"].iloc[0])
    if _fill_rejected(policy, side, fill_price, stop, target):
        return "FILL_REJECTED", fill_price, "NOT_APPLICABLE", None, None
    risk = abs(fill_price - stop)
    for stamp, bar in after.iloc[1:].iterrows():  # TradeManager skips the fill bar itself.
        o, h, l = float(bar["Open"]), float(bar["High"]), float(bar["Low"])
        if side == "LONG":
            gap_stop, gap_target, stop_hit, target_hit = o <= stop, o >= target, l <= stop, h >= target
        else:
            gap_stop, gap_target, stop_hit, target_hit = o >= stop, o <= target, h >= stop, l <= target
        exit_price = o if gap_stop or gap_target else stop if stop_hit else target if target_hit else None
        if exit_price is None:
            continue
        outcome = "STOP" if (gap_stop or (not gap_target and stop_hit)) else "TARGET"
        move = (exit_price - fill_price) if side == "LONG" else (fill_price - exit_price)
        return "FILLED", fill_price, outcome, stamp, move / risk
    return "FILLED", fill_price, "OPEN", None, None


def decision_slots(frames, start, end, cadence_minutes=15):
    """Runtime-like slots inside LONDON/NEW_YORK sessions."""
    slot = pd.Timestamp(start).ceil(f"{cadence_minutes}min")
    while slot <= end:
        if set(session_names(slot.to_pydatetime())) & set(SESSIONS):
            yield slot.to_pydatetime()
        slot += timedelta(minutes=cadence_minutes)


def _shared_scouts():
    """Run each scout once per (slot, timeframe) and reuse the identical report for every policy: both
    policies provably see the same evidence, and the (unchanged, slow) production scouts run once."""
    import floor.orchestrator as orchestrator
    cache = {}

    def memo(function, name):
        def wrapped(frame, symbol, timeframe, run_id, as_of):
            key = (name, symbol, timeframe, run_id, as_of)
            if key not in cache:
                cache.clear() if len(cache) > 64 else None
                cache[key] = function(frame, symbol, timeframe, run_id, as_of)
            return cache[key]
        return wrapped
    return (patch.object(orchestrator, "analizar_estructura", memo(orchestrator.analizar_estructura, "structure")),
            patch.object(orchestrator, "analizar_liquidez", memo(orchestrator.analizar_liquidez, "liquidity")))


def replay(engine, symbol, start, end, *, policies=(POLICY_V1, POLICY_V2_D), cadence_minutes=15):
    """``cadence_minutes`` 15 = runtime cadence; a coarser cadence samples decision slots (documented)."""
    structure, liquidity = _shared_scouts()
    with structure, liquidity:
        return _replay(engine, symbol, start, end, policies, cadence_minutes)


def _replay(engine, symbol, start, end, policies, cadence_minutes):
    frames = load_frames(engine, symbol)
    instrument = paper_instruments()[symbol]
    macro = NoMacroDataProvider()
    decisions = []
    for slot in decision_slots(frames, start, end, cadence_minutes):
        snapshot = snapshot_at(frames, slot)
        fresh, _, _ = fresh_snapshot(snapshot, symbol, slot, MAX_AGE)
        if not fresh:
            continue
        for policy in policies:
            report = run_floor(snapshot, slot, symbol, macro, instrument, POLICIES[policy](), equity=EQUITY,
                               run_id=f"replay:{symbol}:{slot.isoformat()}", planner_policy=policy)
            setup, plan, decision = report.setup_assessment, report.trade_plan, report.target_decision or {}
            plan_status = ("PLAN_READY" if report.final_status == "PLAN_READY" else
                           decision.get("status") or report.final_status)
            selected = decision.get("selected") or {}
            record = dict(policy=policy, symbol=symbol, slot=slot, session=tuple(session_names(slot)),
                          setup_status=setup.status, setup_id=(setup.explanation or {}).get("setup_id"),
                          final_status=report.final_status, plan_status=plan_status)
            if decision.get("status") == "OUT_OF_POLICY_EXTENDED_TARGET":
                record["extended_observation"] = {"target": selected.get("target_price"),
                                                  "gross_rr": decision.get("gross_rr")}
            if plan is not None:
                shape, _ = geometry(plan.side, plan.entry, plan.stop, plan.target)
                rr = None if shape is None else shape.rr
                record.update(rr=rr, band=decision.get("band"), side=plan.side, entry=plan.entry, stop=plan.stop,
                              target=plan.target,
                              target_source=selected.get("source") if decision else "manufactured_3r")
                if report.final_status == "PLAN_READY":
                    fill, price, outcome, exit_at, r_multiple = simulate(policy, frames["5m"], slot, plan.side,
                                                                         plan.stop, plan.target)
                    record.update(fill=fill, fill_price=price, outcome=outcome, exit_at=exit_at, r_multiple=r_multiple)
            decisions.append(Decision(**record))
    return decisions
