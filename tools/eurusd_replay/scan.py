"""Decision-slot scan. Calls the real setup validator, FX planner, and H1 EMA(200).

Structure and liquidity use ``tools.eurusd_replay.scouts`` (audited against the real scouts). Macro is the
real agent over an empty calendar: the candle bundle has no news, so the high-impact filter
never fires. AI reviewers are not called.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta
import json

import numpy as np
import pandas as pd

from agents.macro_news_agent import analizar_macro_news
from agents.setup_validator import evaluar_setup
from agents.target_planner import plan_fx_fixed_rr
from tools.eurusd_replay.scouts import equivalent, frame_from_csv_window, liquidity_message, structure_message
from core.contracts import AgentMessage
from data.macro_news import NoMacroDataProvider

# Same bar lengths as the live providers. Kept local so this folder does not import the evidence engine.
BAR = {"1h": timedelta(hours=1), "15m": timedelta(minutes=15), "5m": timedelta(minutes=5)}
from execution.eurusd_pullback import bars_from_frame, ema, trend_side
from replay.engine import MAX_AGE, WINDOW, decision_slots
from runtime.paper_contracts import paper_instruments

SYMBOL = "EURUSD"
AUDIT_EVERY = 200


@dataclass(frozen=True)
class Signal:
    """One PLAN_READY slot. ``relaxed`` is true only when 5m was neutral and the C rule was required."""
    at: datetime
    side: str
    entry: str
    stop: str
    target: str
    structural: str
    trend: str | None
    setup_id: str | None
    relaxed: bool

    def dump(self):
        return {"at": self.at.isoformat(), "side": self.side, "entry": self.entry, "stop": self.stop,
                "target": self.target, "structural": self.structural, "trend": self.trend,
                "setup_id": self.setup_id, "relaxed": self.relaxed}


def load_frames(data_dir):
    frames = {}
    for timeframe, name in (("5m", "EURUSD_5m.csv"), ("15m", "EURUSD_15m.csv"), ("1h", "EURUSD_1h.csv")):
        frame = pd.read_csv(f"{data_dir.rstrip('/')}/{name}")
        frame["start"] = pd.to_datetime(frame["start"], utc=True)
        frame = frame.set_index("start").sort_index()
        if frame.index.has_duplicates:
            raise ValueError(f"duplicate {timeframe} timestamps")
        prices = frame[["Open", "High", "Low", "Close", "BidClose", "AskClose"]]
        if not np.isfinite(prices.to_numpy(dtype=np.float64)).all():
            raise ValueError(f"non-finite price in {timeframe}")
        frames[timeframe] = frame
    return frames


def fresh_session_slots(frames):
    """Session slots whose last closed bar of every timeframe is inside the live freshness limits."""
    start = frames["5m"].index[0].to_pydatetime()
    end = frames["5m"].index[-1].to_pydatetime()
    age = dict(MAX_AGE)
    fresh = []
    for slot in decision_slots({}, start, end):
        ok = True
        for timeframe, duration in BAR.items():
            index = frames[timeframe].index
            lo, hi = closed_bounds(index, duration, slot)
            if not _fresh(index, lo, hi, slot, age[timeframe]):
                ok = False
                break
        if ok:
            fresh.append(slot)
    return fresh


def closed_bounds(index, duration, slot):
    """``[start, end)`` positional slice of bars whose close (`start + duration`) is at or before ``slot``.

    Pandas 3 stores timestamps in microseconds, so this compares the index itself instead of raw integers.
    """
    ends = index + pd.Timedelta(duration)
    end = int(ends.searchsorted(pd.Timestamp(slot), side="right"))
    start = max(0, end - WINDOW)
    return start, end


def _fresh(index, start, end, slot, max_age):
    if end <= start:
        return False
    last = index[end - 1]
    age = (pd.Timestamp(slot) - last).total_seconds()
    return 0 <= age <= max_age


def _view(arrays, start, end):
    return (arrays["high"][start:end], arrays["low"][start:end], arrays["open"][start:end],
            arrays["close"][start:end], list(arrays["index"][start:end]))


def _could_align(structure):
    """1h and 15m share a direction, and 5m is not the opposite. Legacy and C both need at least this."""
    h1 = structure["1h"].evidence[0]["bias"] if structure["1h"].status == "OK" else None
    m15 = structure["15m"].evidence[0]["structure_state"] if structure["15m"].status == "OK" else None
    m5 = structure["5m"].evidence[0]["structure_state"] if structure["5m"].status == "OK" else None
    if h1 not in ("bullish", "bearish") or h1 != m15:
        return False
    opposite = "bearish" if h1 == "bullish" else "bullish"
    return m5 != opposite


def _relax_5m(structure):
    """Copy of the reports where a neutral 5m state is set to the 1h/15m side. Opposite 5m is left alone."""
    payload = dict(structure["5m"].evidence[0])
    side = structure["1h"].evidence[0]["bias"]
    if payload.get("structure_state") != "neutral":
        return None
    payload["structure_state"] = side
    return {**structure, "5m": AgentMessage(
        structure["5m"].schema_version, structure["5m"].run_id, structure["5m"].timestamp, structure["5m"].symbol,
        "5m", "structure", "OK", evidence=(payload,), data_quality=structure["5m"].data_quality)}


def _trend(frame_slice):
    parsed = bars_from_frame(frame_slice)
    if not parsed:
        return None
    closes = tuple(bar[2] for bar in parsed)
    value = ema(closes)
    if value is None:
        return None
    return trend_side(closes[-1], value)


def _emit(slot, assessment, plan, decision, trend, relaxed):
    structural = decision.get("structural_stop")
    if structural is None:
        structural = str(plan.stop)
    setup_id = (assessment.explanation or {}).get("setup_id")
    return Signal(slot, plan.side, str(plan.entry), str(plan.stop), str(plan.target), str(structural),
                  trend, setup_id, relaxed)


def scan(frames, *, audit=True, progress=None):
    """PLAN_READY signals. ``audit`` calls the real scouts on every 200th candidate window."""
    arrays = {}
    for timeframe, frame in frames.items():
        arrays[timeframe] = {
            "open": frame["Open"].to_numpy(dtype=np.float64),
            "high": frame["High"].to_numpy(dtype=np.float64),
            "low": frame["Low"].to_numpy(dtype=np.float64),
            "close": frame["Close"].to_numpy(dtype=np.float64),
            "index": frame.index,
        }
    start, end = frames["5m"].index[0].to_pydatetime(), frames["5m"].index[-1].to_pydatetime()
    instrument = paper_instruments()[SYMBOL]
    macro_provider = NoMacroDataProvider()
    age = dict(MAX_AGE)
    signals, audits, mismatches, candidates = [], 0, 0, 0
    slots = 0
    for slot in decision_slots({}, start, end):
        slots += 1
        if progress and slots % 5000 == 0:
            progress(f"slots={slots} signals={len(signals)}")
        bounds = {}
        fresh = True
        for timeframe, duration in BAR.items():
            lo, hi = closed_bounds(arrays[timeframe]["index"], duration, slot)
            bounds[timeframe] = (lo, hi)
            if not _fresh(arrays[timeframe]["index"], lo, hi, slot, age[timeframe]):
                fresh = False
                break
        if not fresh:
            continue
        run_id = f"replay:{slot.isoformat()}"
        structure = {}
        for timeframe in ("1h", "15m", "5m"):
            lo, hi = bounds[timeframe]
            high, low, open_, close, index = _view(arrays[timeframe], lo, hi)
            structure[timeframe] = structure_message(high, low, open_, close, index, SYMBOL, timeframe, run_id, slot)
        if not _could_align(structure):
            continue
        candidates += 1
        if audit and candidates % AUDIT_EVERY == 1:
            audits += 1
            for timeframe in ("1h", "15m", "5m"):
                lo, hi = bounds[timeframe]
                window = frame_from_csv_window(frames[timeframe].iloc[lo:hi])
                if not equivalent(window, SYMBOL, timeframe, slot):
                    mismatches += 1
                    raise RuntimeError(f"scout transcription diverged at {slot.isoformat()} {timeframe}")
        liquidity = {}
        for timeframe in ("1h", "15m", "5m"):
            lo, hi = bounds[timeframe]
            high, low, open_, close, index = _view(arrays[timeframe], lo, hi)
            liquidity[timeframe] = liquidity_message(high, low, close, index, SYMBOL, timeframe, run_id, slot)
        macro = analizar_macro_news(macro_provider, SYMBOL, slot, run_id)
        legacy = evaluar_setup(structure, liquidity, macro, SYMBOL, run_id, slot)
        chosen, relaxed = legacy, False
        if legacy.status != "VALID_SETUP":
            relaxed_reports = _relax_5m(structure)
            if relaxed_reports is None:
                continue
            chosen = evaluar_setup(relaxed_reports, liquidity, macro, SYMBOL, run_id, slot)
            if chosen.status != "VALID_SETUP":
                continue
            relaxed = True
        m5_lo, m5_hi = bounds["5m"]
        m5 = frame_from_csv_window(frames["5m"].iloc[m5_lo:m5_hi])
        plan, decision = plan_fx_fixed_rr(chosen, {"data": m5}, SYMBOL, run_id, slot, instrument)
        if plan is None:
            continue
        h_lo, h_hi = bounds["1h"]
        trend = _trend(frames["1h"].iloc[h_lo:h_hi])
        signals.append(_emit(slot, chosen, plan, decision, trend, relaxed))
    return signals, {"slots": slots, "candidates": candidates, "signals": len(signals),
                     "scout_audits": audits, "scout_mismatches": mismatches}


def write_signals(path, signals, meta):
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(json.dumps({"meta": meta}) + "\n")
        for signal in signals:
            handle.write(json.dumps(signal.dump()) + "\n")


def read_signals(path):
    signals, meta = [], {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            if "meta" in row:
                meta = row["meta"]
                continue
            signals.append(Signal(
                datetime.fromisoformat(row["at"]), row["side"], row["entry"], row["stop"], row["target"],
                row["structural"], row["trend"], row["setup_id"], bool(row["relaxed"])))
    return signals, meta
