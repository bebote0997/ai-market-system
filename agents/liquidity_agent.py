"""Deterministic liquidity scout.

Equal-level detection is explicitly marked HEURISTIC: adjacent extrema are
considered equal when they differ by at most 0.1% of the reference price.
"""
from datetime import datetime, timezone
import math

import pandas as pd

from core.contracts import AgentMessage
from core.timeframes import AUTHORIZED_TIMEFRAMES


def _closed_frame(datos, as_of=None):
    if not isinstance(datos, pd.DataFrame) or datos.empty or not isinstance(datos.index, pd.DatetimeIndex):
        return None
    if datos.index.has_duplicates:
        return None
    if not all(column in datos.columns for column in ("High", "Low", "Close")):
        return None
    frame = datos.sort_index().copy()
    if as_of is not None:
        frame = frame[frame.index <= as_of]
    if "is_closed" in frame.columns:
        frame = frame[frame["is_closed"] == True]  # noqa: E712
    frame = frame[["High", "Low", "Close"]].apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([math.inf, -math.inf], math.nan).dropna()
    return frame if not frame.empty else None


def _equal_levels(values, timestamps):
    levels = []
    for index in range(1, len(values)):
        reference = float(values[index - 1])
        current = float(values[index])
        tolerance = abs(reference) * 0.001
        if abs(current - reference) <= tolerance:
            levels.append({"price": current, "timestamps": [timestamps[index - 1], timestamps[index]], "classification": "HEURISTIC"})
    return levels


def _confirmed_pivots(frame):
    highs = []
    lows = []
    for index in range(1, len(frame) - 1):
        if frame["High"].iloc[index] > frame["High"].iloc[index - 1] and frame["High"].iloc[index] > frame["High"].iloc[index + 1]:
            highs.append((float(frame["High"].iloc[index]), frame.index[index]))
        if frame["Low"].iloc[index] < frame["Low"].iloc[index - 1] and frame["Low"].iloc[index] < frame["Low"].iloc[index + 1]:
            lows.append((float(frame["Low"].iloc[index]), frame.index[index]))
    return highs, lows


def analizar_liquidez(datos, symbol="UNKNOWN", timeframe="unknown", run_id="liquidity", as_of=None):
    timestamp = datetime.now(timezone.utc)
    if timeframe not in AUTHORIZED_TIMEFRAMES:
        return AgentMessage("1.0", run_id, timestamp, symbol, timeframe, "liquidity", "ERROR", warnings=("unsupported_timeframe",))
    frame = _closed_frame(datos, as_of)
    if frame is None:
        return AgentMessage("1.0", run_id, timestamp, symbol, timeframe, "liquidity", "NO_DATA", data_quality={"closed_bars": 0})
    if len(frame) < 3:
        return AgentMessage("1.0", run_id, timestamp, symbol, timeframe, "liquidity", "PARTIAL", data_quality={"closed_bars": len(frame)}, warnings=("insufficient_bars",))

    pivot_highs, pivot_lows = _confirmed_pivots(frame)
    equal_highs = _equal_levels([value for value, _ in pivot_highs], [timestamp for _, timestamp in pivot_highs])
    equal_lows = _equal_levels([value for value, _ in pivot_lows], [timestamp for _, timestamp in pivot_lows])
    previous_high = max(frame["High"].iloc[:-1])
    previous_low = min(frame["Low"].iloc[:-1])
    last = frame.iloc[-1]
    sweeps = []
    if last["High"] > previous_high and last["Close"] < previous_high:
        sweeps.append({"type": "high", "timestamp": frame.index[-1], "price": float(last["High"])})
    if last["Low"] < previous_low and last["Close"] > previous_low:
        sweeps.append({"type": "low", "timestamp": frame.index[-1], "price": float(last["Low"])})
    report = {
        "symbol": symbol,
        "timeframe": timeframe,
        "liquidity_above": [{"price": item["price"], "type": "equal_high", "classification": "HEURISTIC"} for item in equal_highs],
        "liquidity_below": [{"price": item["price"], "type": "equal_low", "classification": "HEURISTIC"} for item in equal_lows],
        "sweeps": sweeps,
        "equal_highs": equal_highs,
        "equal_lows": equal_lows,
        "order_blocks": [],
        "evidence": [{"timestamp": frame.index[-1], "closed_bars": len(frame)}],
        "warnings": [],
        "data_quality": {"closed_bars": len(frame), "equal_level_tolerance": 0.001},
    }
    return AgentMessage("1.0", run_id, timestamp, symbol, timeframe, "liquidity", "OK", evidence=(report,), data_quality=report["data_quality"])


def analizar_liquidez_multitimeframe(datos_por_timeframe, symbol="UNKNOWN", run_id="liquidity", as_of=None):
    if not isinstance(datos_por_timeframe, dict):
        return AgentMessage("1.0", run_id, datetime.now(timezone.utc), symbol, "multi", "liquidity", "NO_DATA")
    reports = {tf: analizar_liquidez(datos_por_timeframe[tf], symbol, tf, run_id, as_of) for tf in AUTHORIZED_TIMEFRAMES if tf in datos_por_timeframe}
    status = "OK" if len(reports) == len(AUTHORIZED_TIMEFRAMES) else ("PARTIAL" if reports else "NO_DATA")
    return AgentMessage("1.0", run_id, datetime.now(timezone.utc), symbol, "multi", "liquidity", status, evidence=tuple(reports.items()), data_quality={"timeframes": tuple(reports)})
