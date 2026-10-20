"""1-year EURUSD backtest: V2_F3 (production fixed 3R + trade-duration rules) vs V2_I2 / V2_I2_15 (intraday). REPLAY ONLY.

Nothing here is wired into the runtime: production floor/orchestrator defaults, RuntimeConfig, fingerprint, planners and
env are untouched. Two phases:

1. Signals (slow, parallel, cached): at every runtime-like slot ``t`` (15 min, LONDON/NEW_YORK) the snapshot holds only
   bars closed by ``t`` (``start + duration <= t``, last 500 per timeframe, ``replay.engine.snapshot_at``). The
   production floor runs ONCE per slot with the production V2_F3 planner and risk config (AI excluded,
   NoMacroDataProvider); the same VALID_SETUP feeds the replay-only I2 planner (``plan_i2``), so every policy sees the
   identical scout evidence.
2. Simulation (fast, per policy): one position at a time, one entry per production setup_id, fill at the open of the
   5m bar starting at ``t`` + half spread + slippage, SL/TP on the bid (LONG) / ask (SHORT), SL first on a same-bar
   double hit, gaps through the stop fill at the bar open, time exits per policy, 2R/day halt (DEC-5.5a, 1 % risk).

Run: ``python -m replay.intraday_backtest --data /workspace/backtest/data --out /workspace/backtest``.
Market data and outputs live outside the repository and are never committed.
"""
import argparse
import json
import logging
import math
import os
import time as wall
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from execution import trade_duration as td

LOG = logging.getLogger("replay.intraday_backtest")
SYMBOL = "EURUSD"
PIP = 0.0001
SLIPPAGE_PIPS = 0.2
DEFAULT_SPREAD_PIPS = 1.0  # only when bid/ask are missing
BUFFER_ATR = 0.2
MIN_SL_PIPS, MAX_SL_PIPS = 15.0, 30.0
DAILY_HALT_R = 2.0  # DEC-5.5a: 2 % daily loss at 1 % risk per trade
F3_FILL_RR_FLOOR = 2.5  # DEC-4.7 fill-time tolerance of the production fixed-3R paper broker (core.rr_contract)
LONDON = ZoneInfo("Europe/London")
NEW_YORK = td.NEW_YORK
SESSION_CLOSE = time(17, 0)
FIVE = timedelta(minutes=5)
FIFTEEN = timedelta(minutes=15)
EQUITY = 10000.0
VARIANTS = {"V2_F3": None, "V2_I2": 2.0, "V2_I2_15": 1.5}
I2_TARGETS = {"V2_I2": 2.0, "V2_I2_15": 1.5}

TP, SL, BE, TIME, FRIDAY, END = "TP", "SL", "BE", "TIME", "FRIDAY", "OPEN_AT_END"


# ---------------------------------------------------------------- data
def load_csv_frames(data_dir, symbol=SYMBOL):
    """Frames shaped like ``replay.engine.load_frames`` (+ BidClose/AskClose on 5m). Rows are bar START times (UTC)."""
    frames = {}
    for timeframe in ("1h", "15m", "5m"):
        raw = pd.read_csv(os.path.join(data_dir, f"{symbol}_{timeframe}.csv"))
        index = pd.DatetimeIndex(pd.to_datetime(raw["start"], utc=True))
        frame = pd.DataFrame({c: raw[c].astype(float).to_numpy() for c in ("Open", "High", "Low", "Close")}, index=index)
        frame["symbol"], frame["is_closed"] = symbol, True
        if timeframe == "5m":
            for column in ("BidClose", "AskClose"):
                if column in raw.columns:
                    frame[column] = raw[column].astype(float).to_numpy()
        frames[timeframe] = frame[~frame.index.duplicated()].sort_index()
    return frames


def median_spread_pips(frame_5m):
    """Median (AskClose - BidClose) in pips, or None when bid/ask are unavailable."""
    if not {"BidClose", "AskClose"} <= set(frame_5m.columns):
        return None
    spread = (frame_5m["AskClose"] - frame_5m["BidClose"]).dropna()
    spread = spread[spread > 0]
    return None if spread.empty else float(spread.median() / PIP)


def snapshot_at(frames, slot):
    """``replay.engine.snapshot_at`` on the OHLC columns only (bid/ask never reach the floor)."""
    from replay.engine import snapshot_at as engine_snapshot
    columns = ["Open", "High", "Low", "Close", "symbol", "is_closed"]
    return engine_snapshot({tf: frame[columns] for tf, frame in frames.items()}, slot)


# ---------------------------------------------------------------- I2 planner (replay only)
def _round(value, side_down):
    from core.rr_contract import normalize
    return normalize(value, Decimal("0.00001"), ROUND_FLOOR if side_down else ROUND_CEILING)


def _confirmed_swings(frame):
    """Confirmed swings (pivot + the following closed bar) of a closed-bar frame: production structure-scout rule."""
    from agents.structure_agent import _closed_frame, _swings
    closed = _closed_frame(frame)
    return ([], []) if closed is None or len(closed) < 3 else _swings(closed)


def i2_stop(side, entry, frame_15m, frame_5m, atr):
    """(stop, source) or (None, skip_reason). LONG: most recent confirmed 15m swing low below entry - 0.2 ATR(1h);
    < 15 pips -> widened to 15; > 30 pips -> most recent confirmed 5m swing giving 15..30 pips after buffer, else skip."""
    if atr is None or not math.isfinite(atr) or atr <= 0:
        return None, "atr_unavailable"
    buffer = BUFFER_ATR * atr
    sign = 1 if side == "LONG" else -1

    def candidates(frame):
        highs, lows = _confirmed_swings(frame)
        swings = lows if side == "LONG" else highs
        for swing in reversed(swings):  # most recent confirmation first
            price = swing["price"]
            if (side == "LONG" and price < entry) or (side == "SHORT" and price > entry):
                yield price - sign * buffer

    first = next(candidates(frame_15m), None)
    if first is None:
        return None, "no_15m_swing_beyond_entry"
    distance = sign * (entry - first) / PIP
    if distance < MIN_SL_PIPS:
        return entry - sign * MIN_SL_PIPS * PIP, "15m_swing_widened_to_15"
    if distance <= MAX_SL_PIPS:
        return first, "15m_swing"
    for stop in candidates(frame_5m):
        if MIN_SL_PIPS <= sign * (entry - stop) / PIP <= MAX_SL_PIPS:
            return stop, "5m_swing"
    return None, "sl_wider_than_30_pips"


def plan_i2(setup, snapshot, as_of, k, instrument, symbol=SYMBOL):
    """(plan dict or None, reason). Entry = production entry authority (last closed 5m close); TP = entry +/- k x risk
    (TP rounded away from entry so the planned R:R is >= k); risk check = production ``evaluar_trade_plan``."""
    from agents.target_planner import _entry
    from core.contracts import TradePlan
    from riesgo import crear_configuracion_riesgo_v2, evaluar_trade_plan
    from tecnico import calcular_atr
    if setup is None or setup.status != "VALID_SETUP" or setup.side not in ("LONG", "SHORT"):
        return None, "not_a_valid_setup"
    side = setup.side
    entry_raw = _entry({"data": snapshot["5m"]}, as_of)
    if entry_raw is None:
        return None, "entry_unavailable"
    stop_raw, source = i2_stop(side, float(entry_raw), snapshot["15m"], snapshot["5m"], calcular_atr(snapshot["1h"], 14))
    if stop_raw is None:
        return None, source
    long = side == "LONG"
    entry = _round(Decimal(str(entry_raw)), not long)
    stop = _round(Decimal(repr(stop_raw)), long)
    risk = abs(entry - stop)
    if risk <= 0:
        return None, "invalid_geometry"
    target = _round(entry + Decimal(str(k)) * risk if long else entry - Decimal(str(k)) * risk, not long)
    rr = float(abs(target - entry) / risk)
    plan = TradePlan("1.0", symbol, side, "5m", float(entry), float(stop), float(target), rr, invalidation=str(stop),
                     evidence=setup.evidence, run_id=setup.run_id, as_of=as_of, policy_version=f"REPLAY_I2_{k}R")
    config = dict(crear_configuracion_riesgo_v2(), ratio_minimo=float(k))
    decision = evaluar_trade_plan(plan, EQUITY, instrument, config)
    if decision.status != "APPROVED":
        return None, f"risk_{decision.reason}"
    return {"entry": float(entry), "stop": float(stop), "target": float(target), "sl_source": source,
            "units": decision.quantity}, "PLAN_READY"


# ---------------------------------------------------------------- signals (phase 1)
def decision_slots(start, end, cadence_minutes=15):
    from replay.engine import decision_slots as engine_slots
    return list(engine_slots(None, start, end, cadence_minutes))


_FRAMES = None


def _init_worker(data_dir):
    global _FRAMES
    logging.disable(logging.WARNING)
    _FRAMES = load_csv_frames(data_dir)


def signal_at(frames, slot):
    """One production floor run (V2_F3 planner + production F3 risk config) and the I2 plans from the same setup."""
    from core.rr_contract import POLICY_V2_F3
    from data.macro_news import NoMacroDataProvider
    from floor.orchestrator import run as run_floor
    from replay.engine import MAX_AGE
    from riesgo import crear_configuracion_riesgo_v2
    from runtime.gates import fresh_snapshot
    from runtime.paper_contracts import paper_instruments
    record = {"slot": slot.isoformat()}
    snapshot = snapshot_at(frames, slot)
    fresh, why, _ = fresh_snapshot(snapshot, SYMBOL, slot, MAX_AGE)
    if not fresh:
        record["status"] = why
        return record
    instrument = paper_instruments()[SYMBOL]
    config = dict(crear_configuracion_riesgo_v2(), rr_policy=POLICY_V2_F3, ratio_minimo=3.0, ratio_maximo=3.0)
    report = run_floor(snapshot, slot, SYMBOL, NoMacroDataProvider(), instrument, config, equity=EQUITY,
                       run_id=f"bt:{SYMBOL}:{slot.isoformat()}", planner_policy=POLICY_V2_F3)
    setup = report.setup_assessment
    record.update(status=setup.status, setup_id=(setup.explanation or {}).get("setup_id"), side=setup.side)
    if setup.status != "VALID_SETUP":
        return record
    plan = report.trade_plan
    if report.final_status == "PLAN_READY":
        record["V2_F3"] = {"entry": plan.entry, "stop": plan.stop, "target": plan.target,
                           "units": report.risk_decision.quantity}
    else:
        reason = (report.target_decision or {}).get("reason") if plan is None else None
        record["V2_F3_skip"] = (reason or (report.risk_decision.reason if report.risk_decision else report.final_status))
    for variant, k in I2_TARGETS.items():
        result, reason = plan_i2(setup, snapshot, slot, k, instrument)
        if result is None:
            record[f"{variant}_skip"] = reason
        else:
            record[variant] = result
    return record


def _signal_chunk(slots):
    return [signal_at(_FRAMES, slot) for slot in slots]


def generate_signals(data_dir, slots, workers, log, chunk=96):
    chunks = [slots[i:i + chunk] for i in range(0, len(slots), chunk)]
    out, started = [], wall.time()
    with ProcessPoolExecutor(workers, initializer=_init_worker, initargs=(data_dir,)) as pool:
        for done, records in enumerate(pool.map(_signal_chunk, chunks), 1):
            out.extend(records)
            elapsed = wall.time() - started
            log(f"signals {done}/{len(chunks)} chunks ({len(out)} slots) up to {records[-1]['slot']} "
                f"elapsed {elapsed:.0f}s eta {elapsed / done * (len(chunks) - done):.0f}s")
    return out


# ---------------------------------------------------------------- time exits
def _at(day, zone, clock=SESSION_CLOSE):
    return datetime.combine(day, clock, tzinfo=zone).astimezone(timezone.utc)


def friday_close_after(at):
    """The first Friday 17:00 America/New_York at or after ``at``."""
    day = at.astimezone(NEW_YORK).date()
    while True:
        candidate = _at(day, NEW_YORK)
        if candidate.astimezone(NEW_YORK).weekday() == td.FRIDAY and candidate >= at:
            return candidate
        day += timedelta(days=1)


def london_open(at):
    local = at.astimezone(LONDON)
    return local.weekday() < 5 and 8 <= local.hour < 17


def i2_exit(entry_at):
    """(close_at, reason): end of the 2nd session counting the entry session; Friday 17:00 NY overrides.
    London open at entry (incl. overlap) -> 17:00 NY that day; NY only -> 17:00 London of the next weekday."""
    if not (isinstance(entry_at, datetime) and entry_at.tzinfo is not None):
        raise ValueError("timezone-aware datetime required")
    if london_open(entry_at):
        close = _at(entry_at.astimezone(NEW_YORK).date(), NEW_YORK)
    else:
        local = entry_at.astimezone(NEW_YORK)
        if local.weekday() >= 5 or not 8 <= local.hour < 17:
            raise ValueError("entry outside the London/New York sessions")
        day = entry_at.astimezone(LONDON).date() + timedelta(days=1)
        while day.weekday() >= 5:
            day += timedelta(days=1)
        close = _at(day, LONDON)
    friday = friday_close_after(entry_at)
    return (friday, FRIDAY) if friday <= close else (close, TIME)


# ---------------------------------------------------------------- trade simulation (phase 2)
@dataclass
class Market:
    starts: np.ndarray  # int64 ns, bar start
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    half_spread: float
    slippage: float
    frame_15m: pd.DataFrame = None

    @classmethod
    def from_frames(cls, frames, spread_pips, slippage_pips=SLIPPAGE_PIPS):
        f = frames["5m"]
        return cls(f.index.as_unit("ns").asi8.copy(), f["Open"].to_numpy(), f["High"].to_numpy(), f["Low"].to_numpy(),
                   f["Close"].to_numpy(), spread_pips * PIP / 2, slippage_pips * PIP, frames.get("15m"))

    def stamp(self, i):
        return datetime.fromtimestamp(self.starts[i] / 1e9, timezone.utc)


def _ns(at):
    return int(pd.Timestamp(at).value)


_STRUCTURE_MEMO = {}


def _memo_structure(function):
    """The structure scout is a pure function of (window, as_of); the window here is fixed by ``as_of``."""
    def wrapped(frame, symbol, timeframe, run_id, as_of):
        key = (symbol, timeframe, as_of, len(frame), frame.index[-1] if len(frame) else None)
        if key not in _STRUCTURE_MEMO:
            _STRUCTURE_MEMO[key] = function(frame, symbol, timeframe, run_id, as_of)
        return _STRUCTURE_MEMO[key]
    return wrapped


def day2_structure_exit(position, frame_15m, day1, day2):
    """First 15m bar end in (day1, day2) at which ``td.adverse_structure`` reports an adverse BOS, each check seeing
    only the last 500 15m bars closed by then (runtime window). None if never."""
    from unittest.mock import patch

    import agents.structure_agent as structure_agent
    if frame_15m is None:
        return None
    columns = ["Open", "High", "Low", "Close", "symbol", "is_closed"]
    frame_15m = frame_15m[[c for c in columns if c in frame_15m.columns]]
    ends = frame_15m.index + FIFTEEN
    with patch.object(structure_agent, "analizar_estructura", _memo_structure(structure_agent.analizar_estructura)):
        for end in ends[(ends > day1) & (ends < day2)]:
            moment = end.to_pydatetime()
            window = frame_15m.loc[ends <= end].iloc[-500:]
            if td.adverse_structure(position, window, moment) is not None:
                return moment
    return None


def simulate_trade(market, slot, side, stop, target, policy, fill_rr_floor=None):
    """Dict with fill/exit/R, or {'rejected': reason}. ``policy`` is 'F3' (trade_duration rules) or 'I2' (hard close)."""
    i = int(np.searchsorted(market.starts, _ns(slot)))
    if i >= len(market.starts):
        return {"rejected": "no_fill_data"}
    long = side == "LONG"
    sign = 1 if long else -1
    hs, slip = market.half_spread, market.slippage
    mid_fill = float(market.open[i])
    fill = mid_fill + sign * (hs + slip)
    risk = sign * (fill - stop)
    reward = sign * (target - fill)
    if risk <= 0 or reward <= 0:
        return {"rejected": "fill_beyond_levels"}
    if fill_rr_floor is not None and reward / risk < fill_rr_floor:
        return {"rejected": "fill_rr_below_2_5"}
    opened = market.stamp(i)
    position = SimpleNamespace(symbol=SYMBOL, side=side, entry_price=fill, stop=stop, initial_stop=None,
                               duration_stage=None, opened_at=opened)
    if policy == "I2":
        boundary, boundary_reason = i2_exit(slot)  # session of the signal (the fill bar normally starts at it)
        if opened >= boundary:
            return {"rejected": "fill_after_session_close"}
    else:
        boundary = td.day1_close(position)
    structure_at = None
    current_stop = stop

    def done(j, exit_at, price, reason, detail=None):
        net = sign * (price - fill) / risk
        return {"fill_at": opened, "fill": fill, "mid_fill": mid_fill, "stop": stop, "target": target,
                "exit_at": exit_at, "exit": price, "reason": reason, "detail": detail or reason, "r": net,
                "risk_pips": risk / PIP, "tp_pips": reward / PIP, "exit_index": j}

    def market_exit(j, at, reason, detail):
        bid_ask = float(market.close[j]) - sign * hs  # exit on bid (LONG) / ask (SHORT)
        return done(j, at, bid_ask - sign * slip, reason, detail)

    j = i
    while j < len(market.starts):
        bar_start = market.stamp(j)
        bar_end = bar_start + FIVE
        stop_reason = BE if current_stop == fill else SL
        if structure_at is not None and bar_end > structure_at:
            return market_exit(j - 1, structure_at, TIME, td.CLOSE_DAY2_STRUCTURE)
        if bar_end > boundary:
            price_index = max(j - 1, i)
            if policy == "I2":
                return market_exit(price_index, boundary, boundary_reason, f"I2_{boundary_reason}")
            action, reason = td.boundary_decision(position, boundary, float(market.close[price_index]))
            if action == "CLOSE":
                return market_exit(price_index, boundary, FRIDAY if reason == td.CLOSE_FRIDAY else TIME, reason)
            day1 = boundary
            td.move_stop_to_entry(position)
            current_stop = fill
            boundary = td.day2_close(position)
            structure_at = day2_structure_exit(position, market.frame_15m, day1, boundary)
            continue  # re-evaluate this bar with the Day-2 stop / boundary
        o = float(market.open[j]) - sign * hs
        hi = float(market.high[j]) - sign * hs
        lo = float(market.low[j]) - sign * hs
        adverse, favourable = (lo, hi) if long else (hi, lo)
        if j > i and sign * (o - current_stop) <= 0:
            return done(j, bar_start, o - sign * slip, stop_reason, "GAP_" + stop_reason)
        if j > i and sign * (o - target) >= 0:
            return done(j, bar_start, o, TP, "GAP_TP")
        if sign * (adverse - current_stop) <= 0:  # SL first on a same-bar double hit (conservative)
            return done(j, bar_end, current_stop - sign * slip, stop_reason)
        if sign * (favourable - target) >= 0:
            return done(j, bar_end, target, TP)
        j += 1
    last = len(market.starts) - 1
    return market_exit(last, market.stamp(last) + FIVE, END, END)


def day_key(at):
    """Trading day (17:00 -> 17:00 America/New_York) labelled by the NY date of its close."""
    local = at.astimezone(NEW_YORK)
    return (local.date() + timedelta(days=1)) if local.time() >= SESSION_CLOSE else local.date()


def run_policy(signals, market, variant):
    """Sequential portfolio walk of one policy over the cached signals: (trades, skips Counter, halts list)."""
    policy = "F3" if variant == "V2_F3" else "I2"
    trades, skips, unique_skips, halts = [], Counter(), Counter(), []
    used, skipped_ids, open_until = set(), set(), None
    day_r = defaultdict(float)
    halted = set()
    for record in signals:
        if record.get("status") != "VALID_SETUP":
            continue
        slot = datetime.fromisoformat(record["slot"])
        setup_id = record.get("setup_id") or record["slot"]
        if open_until is not None and slot < open_until:
            skips["position_open"] += 1
            continue
        if setup_id in used:
            skips["setup_already_traded"] += 1
            continue
        if day_key(slot) in halted:
            skips["daily_loss_halt"] += 1
            continue
        plan = record.get(variant)
        if plan is None:
            reason = record.get(f"{variant}_skip") or "plan_unavailable"
            skips[reason] += 1
            if (setup_id, reason) not in skipped_ids:
                skipped_ids.add((setup_id, reason))
                unique_skips[reason] += 1
            continue
        result = simulate_trade(market, slot, record["side"], plan["stop"], plan["target"], policy,
                                F3_FILL_RR_FLOOR if policy == "F3" else None)
        if "rejected" in result:
            skips[result["rejected"]] += 1
            if (setup_id, result["rejected"]) not in skipped_ids:
                skipped_ids.add((setup_id, result["rejected"]))
                unique_skips[result["rejected"]] += 1
            continue
        used.add(setup_id)
        open_until = result["exit_at"]
        result.update(variant=variant, signal_at=slot, setup_id=setup_id, side=record["side"],
                      planned_entry=plan["entry"], units=plan.get("units"), sl_source=plan.get("sl_source", "invalidation"))
        trades.append(result)
        key = day_key(result["exit_at"])
        day_r[key] += result["r"]
        if day_r[key] <= -DAILY_HALT_R and key not in halted:
            halted.add(key)
            halts.append((key, day_r[key]))
    return trades, skips, unique_skips, halts


# ---------------------------------------------------------------- metrics
def max_drawdown(rs):
    equity = np.concatenate([[0.0], np.cumsum(rs)])
    peak = np.maximum.accumulate(equity)
    return float((peak - equity).max()) if len(equity) else 0.0


def longest_losing_streak(rs):
    best = run = 0
    for r in rs:
        run = run + 1 if r < 0 else 0
        best = max(best, run)
    return best


def bootstrap_lower(rs, quantile=0.10, n=10000, seed=7):
    """Lower bound of the two-sided 80 % percentile bootstrap CI of the mean R."""
    if len(rs) < 2:
        return None
    rng = np.random.default_rng(seed)
    means = rng.choice(np.asarray(rs), size=(n, len(rs)), replace=True).mean(axis=1)
    return float(np.quantile(means, quantile))


def pct(values, q):
    return float(np.percentile(values, q)) if len(values) else float("nan")


def metrics(trades, months):
    rs = np.array([t["r"] for t in trades])
    hold = np.array([(t["exit_at"] - t["fill_at"]).total_seconds() / 3600 for t in trades])
    gross = np.array([t["gross_r"] for t in trades]) if trades else np.array([])
    reasons = Counter(t["reason"] for t in trades)
    n = len(trades)
    return {
        "trades": n, "trades_per_month": n / months if months else float("nan"),
        "sl_pips": [pct([t["risk_pips"] for t in trades], q) for q in (50, 10, 90)],
        "tp_pips": [pct([t["tp_pips"] for t in trades], q) for q in (50, 10, 90)],
        "hold_h": [pct(hold, 50), pct(hold, 90)],
        "win_rate": float((rs > 0).mean()) if n else float("nan"),
        "avg_r": float(rs.mean()) if n else float("nan"), "total_r": float(rs.sum()),
        "avg_gross_r": float(gross.mean()) if n else float("nan"),
        "avg_cost_r": float((gross - rs).mean()) if n else float("nan"),
        "max_dd_r": max_drawdown(rs), "losing_streak": longest_losing_streak(rs),
        "worst_r": float(rs.min()) if n else float("nan"),
        "ci80_low": bootstrap_lower(rs),
        "exits": {k: reasons.get(k, 0) / n * 100 if n else 0.0 for k in (TP, SL, TIME, FRIDAY, BE, END)},
    }


def add_gross(trades, market):
    """Mid-price result (no spread / slippage) of each trade, to measure the modeled cost in R."""
    for t in trades:
        sign = 1 if t["side"] == "LONG" else -1
        if t["reason"] == TP:
            mid_exit = t["exit"] + sign * market.half_spread
        elif t["reason"] in (SL, BE, TIME, FRIDAY, END):
            mid_exit = t["exit"] + sign * (market.half_spread + market.slippage)
        else:
            mid_exit = t["exit"]
        t["gross_r"] = sign * (mid_exit - t["mid_fill"]) / t["risk_pips"] / PIP


def monthly_table(trades_by_variant):
    rows = defaultdict(dict)
    for variant, trades in trades_by_variant.items():
        for t in trades:
            month = t["exit_at"].astimezone(NEW_YORK).strftime("%Y-%m")
            rows[month][variant] = rows[month].get(variant, 0.0) + t["r"]
    return dict(sorted(rows.items()))


def gates(m, spread_pips):
    """G1-G11 (plans/real_trading_readiness_plan.md section 1, approved as DEC-LIVE-1): backtest-computable subset."""
    def verdict(ok):
        return "PASS" if ok else "FAIL"
    cost_share = (m["avg_cost_r"] / m["avg_gross_r"]) if m["avg_gross_r"] and m["avg_gross_r"] > 0 else float("inf")
    return [
        ("G1 duration >= 30 days live", "N/A (live demo only)"),
        ("G2 >= 30 closed trades (in 30 days)", f"{verdict(m['trades_per_month'] >= 30)} (~{m['trades_per_month']:.1f}/month)"),
        ("G3 mean R > +0.15R", f"{verdict(m['avg_r'] > 0.15)} ({m['avg_r']:+.3f}R)"),
        ("G3 bootstrap 80% CI lower > 0", f"{verdict(m['ci80_low'] is not None and m['ci80_low'] > 0)} "
                                          f"({m['ci80_low'] if m['ci80_low'] is None else round(m['ci80_low'], 3)}R)"),
        ("G3 worst trade >= -1.2R", f"{verdict(m['worst_r'] >= -1.2)} ({m['worst_r']:+.2f}R)"),
        ("G4 max DD <= 8R", f"{verdict(m['max_dd_r'] <= 8)} ({m['max_dd_r']:.2f}R)"),
        ("G4 never trip the 5% account halt (<= 5R at 1%)", f"{verdict(m['max_dd_r'] < 5)} ({m['max_dd_r']:.2f}R)"),
        ("G5 entry slippage <= 0.5 pip", f"PASS by construction (modeled {SLIPPAGE_PIPS} pip)"),
        ("G5 entry spread <= 1.5 pip", f"{verdict(spread_pips <= 1.5)} (modeled {spread_pips:.2f} pip)"),
        ("G5 mean total cost <= 0.10R", f"{verdict(m['avg_cost_r'] <= 0.10)} ({m['avg_cost_r']:.3f}R)"),
        ("G5 costs <= 25% of gross expectancy", f"{verdict(cost_share <= 0.25)} "
                                                f"({'n/a, gross <= 0' if math.isinf(cost_share) else f'{cost_share:.0%}'})"),
        ("G6 broker rejects/errors", "N/A (live)"), ("G7 reconciliation", "N/A (live)"),
        ("G8 drift", "N/A (live)"),
        ("G9 no position over the weekend", "PASS by construction (Friday 17:00 NY close)"),
        ("G10 emergency drill", "N/A (operational)"), ("G11 stable operation", "N/A (operational)"),
    ]


# ---------------------------------------------------------------- outputs
def write_trades_csv(path, trades):
    columns = ["variant", "setup_id", "side", "signal_at", "fill_at", "planned_entry", "fill", "stop", "target",
               "sl_source", "risk_pips", "tp_pips", "units", "exit_at", "exit", "reason", "detail", "r", "gross_r"]
    pd.DataFrame([{c: t.get(c) for c in columns} for t in trades], columns=columns).to_csv(path, index=False)


def plot_equity(path, trades_by_variant):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = {"V2_F3": "#2a78d6", "V2_I2": "#eb6834", "V2_I2_15": "#1baf7a"}  # categorical slots 1-3, fixed order
    ink, muted, surface = "#0b0b0b", "#52514e", "#fcfcfb"
    fig, ax = plt.subplots(figsize=(10, 5), dpi=120, facecolor=surface)
    ax.set_facecolor(surface)
    for variant, trades in trades_by_variant.items():
        if not trades:
            continue
        xs = [trades[0]["fill_at"]] + [t["exit_at"] for t in trades]
        ys = np.concatenate([[0.0], np.cumsum([t["r"] for t in trades])])
        ax.step(xs, ys, where="post", color=colors.get(variant), linewidth=2, label=variant)
        ax.annotate(f"{variant} {ys[-1]:+.1f}R", (xs[-1], ys[-1]), xytext=(6, 0), textcoords="offset points",
                    va="center", fontsize=9, color=ink)
    ax.axhline(0, color=muted, linewidth=0.8)
    ax.set_title("EURUSD 1-year backtest - cumulative R, net of modeled spread + slippage", loc="left", color=ink)
    ax.set_ylabel("Cumulative R", color=muted)
    ax.tick_params(colors=muted)
    ax.grid(axis="y", color="#e8e8e6", linewidth=0.8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#c9c8c4")
    if any(trades_by_variant.values()):
        ax.legend(frameon=False, loc="lower left", labelcolor=ink)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(path, facecolor=surface)
    plt.close(fig)


def _fmt3(values, digits=1):
    return " / ".join(f"{v:.{digits}f}" for v in values)


def write_report(path, ctx):
    r = ctx["results"]
    variants = list(r)
    lines = [
        "# EURUSD 1-year backtest — V2_F3 (current) vs V2_I2 / V2_I2_15 (intraday)", "",
        f"Generated {ctx['generated']} by `python -m replay.intraday_backtest` (branch `feat/backtest-v2-i2`). "
        "REPLAY ONLY — production unchanged.", "",
        f"- Data: OANDA practice mid candles {SYMBOL} 5m/15m/1h, {ctx['data_start']} → {ctx['data_end']} "
        f"(UTC bar starts, complete bars). Decision slots: {ctx['slots']} (15 min, LONDON/NEW_YORK), "
        f"fresh snapshots {ctx['fresh']}, VALID_SETUP slots {ctx['valid']} ({ctx['unique_setups']} distinct setup_id).",
        f"- Spread: median (AskClose−BidClose) of 5m bars = **{ctx['spread']:.2f} pip** (mean {ctx['spread_mean']:.2f}); "
        f"modeled constant spread {ctx['spread']:.2f} pip (half on each side), slippage {SLIPPAGE_PIPS} pip adverse on "
        "entry, stop and time exits, 0 on TP.",
        f"- Period length used for /month: {ctx['months']:.2f} months.", "",
        "## Results (net R, 1R = initial risk fill→SL)", "",
        "| Metric | " + " | ".join(variants) + " |", "|---|" + "---|" * len(variants),
    ]

    def row(label, fn):
        lines.append(f"| {label} | " + " | ".join(fn(r[v]["m"]) for v in variants) + " |")
    row("Trades", lambda m: str(m["trades"]))
    row("Trades / month", lambda m: f"{m['trades_per_month']:.1f}")
    row("SL distance pips (med / p10 / p90)", lambda m: _fmt3(m["sl_pips"]))
    row("TP distance pips (med / p10 / p90)", lambda m: _fmt3(m["tp_pips"]))
    row("Holding hours (med / p90)", lambda m: _fmt3(m["hold_h"]))
    row("Win rate", lambda m: f"{m['win_rate']:.1%}")
    row("Avg R (expectancy)", lambda m: f"{m['avg_r']:+.3f}")
    row("Total R", lambda m: f"{m['total_r']:+.2f}")
    row("Avg gross R (mid, no costs)", lambda m: f"{m['avg_gross_r']:+.3f}")
    row("Avg cost R", lambda m: f"{m['avg_cost_r']:.3f}")
    row("Max drawdown R", lambda m: f"{m['max_dd_r']:.2f}")
    row("Longest losing streak", lambda m: str(m["losing_streak"]))
    row("Worst trade R", lambda m: f"{m['worst_r']:+.2f}")
    row("Bootstrap 80% CI lower (mean R)", lambda m: "n/a" if m["ci80_low"] is None else f"{m['ci80_low']:+.3f}")
    for key in (TP, SL, TIME, FRIDAY, BE, END):
        row(f"% exits {key}", lambda m, key=key: f"{m['exits'][key]:.1f}%")
    lines.append("| Daily 2R halt triggers | " + " | ".join(
        (", ".join(f"{d} ({x:+.2f}R)" for d, x in r[v]["halts"]) or "never") for v in variants) + " |")
    lines += ["", "Units: risk sized by the production Risk engine (1 % of USD 10 000, capped by `maximo_capital_pct` "
              "= 100 % notional, i.e. ~8 500 units at 1.17); median approved units per variant: " +
              ", ".join(f"{v} {r[v]['units']:.0f}" for v in variants) + ". Because of that notional cap the real "
              "money risk per trade is far below 1 % for EURUSD; every figure here is in R.", ""]
    lines += ["## Spread sensitivity (same signals, spread 1.0 pip)", "",
              "| Variant | Trades | Avg R | Total R | Max DD R |", "|---|---|---|---|---|"]
    for v in variants:
        m = r[v]["m_sens"]
        lines.append(f"| {v} | {m['trades']} | {m['avg_r']:+.3f} | {m['total_r']:+.2f} | {m['max_dd_r']:.2f} |")
    lines += ["", "## Monthly R (by exit month, New York)", "", "| Month | " + " | ".join(variants) + " |",
              "|---|" + "---|" * len(variants)]
    for month, values in ctx["monthly"].items():
        lines.append(f"| {month} | " + " | ".join(f"{values.get(v, 0.0):+.2f}" for v in variants) + " |")
    lines += ["", "## Skips by reason", "",
              "Slot-level counts (a VALID_SETUP slot evaluated while flat / blocked); in brackets the number of "
              "distinct setup_id per reason.", ""]
    for v in variants:
        lines.append(f"**{v}**: " + ", ".join(f"{k} {n} [{r[v]['unique_skips'].get(k, '-')}]"
                                              for k, n in r[v]["skips"].most_common()))
        if v != "V2_F3":
            sources = Counter(t["sl_source"] for t in r[v]["trades"])
            lines.append("  SL source of executed trades: " + ", ".join(f"{k} {n}" for k, n in sources.most_common()))
        lines.append("")
    lines += ["## Gates (DEC-LIVE-1)", "",
              "Thresholds: `plans/real_trading_readiness_plan.md` §1 (workspace plan, approved as DEC-LIVE-1 in "
              "`docs/decisions.md`, 2026-10-17; the table itself is not in the repo). They are demo→live gates over "
              "a 30-day OANDA demo; here they are applied to the backtest only as a screen.", "",
              "| Gate | " + " | ".join(variants) + " |", "|---|" + "---|" * len(variants)]
    gate_rows = {v: gates(r[v]["m"], ctx["spread"]) for v in variants}
    for index, (label, _) in enumerate(gate_rows[variants[0]]):
        lines.append(f"| {label} | " + " | ".join(gate_rows[v][index][1] for v in variants) + " |")
    lines += ["", "## Method", "",
              "- Signals: production floor (structure + liquidity scouts → Setup Validator → planner → Risk), AI "
              "excluded, `NoMacroDataProvider`, freshness gate `runtime.gates.fresh_snapshot` with the replay "
              "MAX_AGE; snapshot = last 500 bars closed by the slot per timeframe (`replay.engine.snapshot_at`).",
              "- V2_F3: production `plan_fixed_3r` (SL = setup.invalidation, TP 3R) + production F3 risk config; "
              "fill gate of the production paper broker (fill-time R:R ≥ 2.5, DEC-4.7). Time exits from "
              "`execution/trade_duration`: Day-1 NY 17:00 (`boundary_decision` on the mid close of the last 5m bar; "
              "< +0.5R close else stop → entry), Day-2 adverse closed-15m BOS (`adverse_structure`, 500-bar 15m "
              "window per check) or Day-2 NY close, Friday 17:00 NY.",
              "- V2_I2 / V2_I2_15: same VALID_SETUP; SL = most recent confirmed 15m swing beyond the entry ± 0.2×ATR14(1h, "
              "`tecnico.calcular_atr`); < 15 pips → 15; > 30 → most recent confirmed 5m swing giving 15–30 pips, "
              "else skip. TP = entry ± k×risk (k = 2 / 1.5, grid-rounded away from entry). Risk: production "
              "`evaluar_trade_plan` (V1 branch with ratio_minimo = k). No fill gate. Hard close at the end of the "
              "2nd session (London-open entry → 17:00 NY same day; NY-only entry → next weekday 17:00 London); "
              "Friday 17:00 NY overrides (labelled FRIDAY).",
              "- Execution: fill at the open of the 5m bar starting at the slot ± (half spread + 0.2 pip). SL/TP on "
              "5m bars from the fill bar on, on the bid (LONG) / ask (SHORT) = mid ∓ half spread; same-bar SL+TP → "
              "SL; gap through the stop → bar open − slippage; gap through TP → bar open. Time exits at the close "
              "of the last 5m bar ending at the boundary (bid/ask − slippage). BE = stop moved to entry and hit.",
              "- Portfolio: one position per policy, signals skipped while open; one entry per production setup_id "
              "(a fill-gate rejection does not consume it). Daily halt: realised R of the 17:00→17:00 NY day ≤ −2R "
              "blocks new entries until the next day (open-P&L flatten and the pre-entry remaining-budget rule of "
              "DEC-5.5a are not modeled).", "",
              "## Limitations", "",
              "- OANDA practice **mid** candles; bid/ask only as 5m closes → a constant median spread is modeled. "
              "Real spreads widen around 17:00 NY rollover and news; time exits at 17:00 NY are therefore optimistic.",
              "- No AI reviewers and no macro/news filter (NoMacroDataProvider): live signals can differ; no news "
              "blackout, so trades through high-impact releases are included at candle prices.",
              "- 500-bar windows as the runtime provider; scouts see nothing older.",
              "- Single instrument (EURUSD), one year, one regime; sample sizes in the table — the bootstrap CI shows "
              "how wide the uncertainty is. No parameter was tuned on this data except the owner-specified ones.",
              "- 5m bar granularity: intrabar ordering unknown (same-bar SL+TP assumed SL), fills at bar opens, "
              "15-min decision cadence.",
              "- F3 Day-2 structure check evaluated on each closed 15m bar; the runtime acts on its own cycle "
              "cadence. Positions still open at the data end are closed at the last close (OPEN_AT_END).",
              "- Production risk sizing caps notional at 100 % of equity, so actual money risk per EURUSD trade is "
              "well below 1 %; results are reported in R.",
              "- Daily-loss rule simplified to a realised −2R halt (see Method).", ""]
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


# ---------------------------------------------------------------- main
def evaluate(signals, frames, spread_pips):
    market = Market.from_frames(frames, spread_pips)
    out = {}
    for variant in VARIANTS:
        trades, skips, unique_skips, halts = run_policy(signals, market, variant)
        add_gross(trades, market)
        out[variant] = (trades, skips, unique_skips, halts)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    parser.add_argument("--refresh-signals", action="store_true", help="ignore the cached signals.jsonl")
    args = parser.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    log_path = os.path.join(args.out, "run.log")

    def log(message):
        line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {message}"
        print(line, flush=True)
        with open(log_path, "a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    frames = load_csv_frames(args.data)
    first, last = frames["5m"].index[0].to_pydatetime(), frames["5m"].index[-1].to_pydatetime() + FIVE
    from data.market_evidence import TIMEFRAMES
    from replay.engine import WINDOW
    warm = max(f.index[WINDOW - 1] + TIMEFRAMES[tf] for tf, f in frames.items()).to_pydatetime()  # full windows
    start = datetime.fromisoformat(args.start) if args.start else warm
    end = datetime.fromisoformat(args.end) if args.end else last
    slots = decision_slots(start, end)
    cache = os.path.join(args.out, "signals.jsonl")
    signals = None
    if os.path.exists(cache) and not args.refresh_signals:
        with open(cache, encoding="utf-8") as handle:
            signals = [json.loads(line) for line in handle]
        if [s["slot"] for s in signals] != [s.isoformat() for s in slots]:
            log("signals cache does not match the slot range; regenerating")
            signals = None
    if signals is None:
        log(f"generating signals for {len(slots)} slots {start} -> {end} with {args.workers} workers")
        signals = generate_signals(args.data, slots, args.workers, log)
        with open(cache, "w", encoding="utf-8") as handle:
            for record in signals:
                handle.write(json.dumps(record, sort_keys=True) + "\n")
    spread = median_spread_pips(frames["5m"])
    spread_mean = float(((frames["5m"]["AskClose"] - frames["5m"]["BidClose"]) / PIP).mean()) if spread else float("nan")
    spread = spread if spread is not None else DEFAULT_SPREAD_PIPS
    log(f"simulating (spread {spread:.2f} pip)")
    base, sens = evaluate(signals, frames, spread), evaluate(signals, frames, DEFAULT_SPREAD_PIPS)
    months = (end - start).total_seconds() / 86400 / (365.25 / 12)
    results = {}
    for variant in VARIANTS:
        trades, skips, unique_skips, halts = base[variant]
        units = [t["units"] for t in trades if t.get("units")]
        results[variant] = {"trades": trades, "skips": skips, "unique_skips": unique_skips, "halts": halts,
                            "m": metrics(trades, months), "m_sens": metrics(sens[variant][0], months),
                            "units": float(np.median(units)) if units else float("nan")}
        write_trades_csv(os.path.join(args.out, f"trades_{variant}.csv"), trades)
        m = results[variant]["m"]
        log(f"{variant}: trades {m['trades']} avgR {m['avg_r']:+.3f} totalR {m['total_r']:+.2f} DD {m['max_dd_r']:.2f}")
    valid = [s for s in signals if s.get("status") == "VALID_SETUP"]
    ctx = {"results": results, "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "data_start": first.isoformat(), "data_end": last.isoformat(), "slots": len(signals),
           "fresh": sum(1 for s in signals if s.get("status") not in ("STALE_DATA", "NO_DATA")),
           "valid": len(valid), "unique_setups": len({s.get("setup_id") for s in valid}),
           "spread": spread, "spread_mean": spread_mean, "months": months,
           "monthly": monthly_table({v: results[v]["trades"] for v in VARIANTS})}
    plot_equity(os.path.join(args.out, "equity_r.png"), {v: results[v]["trades"] for v in VARIANTS})
    write_report(os.path.join(args.out, "report.md"), ctx)
    log("done")
    return ctx


if __name__ == "__main__":
    main()
