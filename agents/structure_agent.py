"""Deterministic structure scout.

A swing is confirmed only after the following closed candle exists. Therefore
reports never use an unfinished candle and never inspect data after the report
cutoff supplied by the caller.
"""
from datetime import datetime, timezone
import math

import pandas as pd

from core.contracts import AgentMessage
from core.timeframes import AUTHORIZED_TIMEFRAMES


REQUIRED_COLUMNS = ("Open", "High", "Low", "Close")


def _closed_frame(datos, as_of=None):
    if not isinstance(datos, pd.DataFrame) or datos.empty:
        return None
    if not isinstance(datos.index, pd.DatetimeIndex):
        return None
    if datos.index.has_duplicates:
        return None
    if not all(column in datos.columns for column in REQUIRED_COLUMNS):
        return None
    frame = datos.sort_index().copy()
    if as_of is not None:
        frame = frame[frame.index <= as_of]
    if "is_closed" in frame.columns:
        frame = frame[frame["is_closed"] == True]  # noqa: E712
    frame = frame[list(REQUIRED_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    frame = frame.replace([math.inf, -math.inf], math.nan).dropna()
    return frame if not frame.empty else None


def _swings(frame):
    highs = []
    lows = []
    for index in range(1, len(frame) - 1):
        high = frame["High"].iloc[index]
        low = frame["Low"].iloc[index]
        if high > frame["High"].iloc[index - 1] and high > frame["High"].iloc[index + 1]:
            highs.append({"timestamp": frame.index[index + 1], "pivot_timestamp": frame.index[index], "price": float(high)})
        if low < frame["Low"].iloc[index - 1] and low < frame["Low"].iloc[index + 1]:
            lows.append({"timestamp": frame.index[index + 1], "pivot_timestamp": frame.index[index], "price": float(low)})
    return highs, lows


def _classify(highs, lows):
    labels = []
    if len(highs) >= 2:
        labels.append("HH" if highs[-1]["price"] > highs[-2]["price"] else "LH")
    if len(lows) >= 2:
        labels.append("HL" if lows[-1]["price"] > lows[-2]["price"] else "LL")
    if "HH" in labels and "HL" in labels:
        return "bullish"
    if "LH" in labels and "LL" in labels:
        return "bearish"
    return "neutral"


def analizar_estructura(datos, symbol="UNKNOWN", timeframe="unknown", run_id="structure", as_of=None):
    timestamp = datetime.now(timezone.utc)
    if timeframe not in AUTHORIZED_TIMEFRAMES:
        return AgentMessage("1.0", run_id, timestamp, symbol, timeframe, "structure", "ERROR", warnings=("unsupported_timeframe",))
    frame = _closed_frame(datos, as_of)
    if frame is None:
        return AgentMessage("1.0", run_id, timestamp, symbol, timeframe, "structure", "NO_DATA", data_quality={"closed_bars": 0})
    if len(frame) < 3:
        return AgentMessage("1.0", run_id, timestamp, symbol, timeframe, "structure", "PARTIAL", data_quality={"closed_bars": len(frame)}, warnings=("insufficient_bars",))

    highs, lows = _swings(frame)
    evidence = tuple({"timestamp": frame.index[-1], "type": "closed_bar"}.items())
    displacement = None
    last = frame.iloc[-1]
    candle_range = float(last["High"] - last["Low"])
    if candle_range > 0:
        body = abs(float(last["Close"] - last["Open"]))
        if body / candle_range >= 0.6:
            displacement = {"type": "HEURISTIC", "direction": "bullish" if last["Close"] > last["Open"] else "bearish", "ratio": body / candle_range}

    support = min((item["price"] for item in lows), default=None)
    resistance = max((item["price"] for item in highs), default=None)
    report = {
        "symbol": symbol,
        "timeframe": timeframe,
        "bias": _classify(highs, lows),
        "structure_state": _classify(highs, lows),
        "swings": {"highs": highs, "lows": lows},
        "levels": {"support": support, "resistance": resistance},
        "displacement": displacement,
        "retracement": None,
        "evidence": [dict(evidence)],
        "warnings": [],
        "data_quality": {"closed_bars": len(frame), "definition": "swings require one closed confirmation bar"},
    }
    return AgentMessage("1.0", run_id, timestamp, symbol, timeframe, "structure", "OK", evidence=(report,), data_quality=report["data_quality"])


def analizar_estructura_multitimeframe(datos_por_timeframe, symbol="UNKNOWN", run_id="structure", as_of=None):
    if not isinstance(datos_por_timeframe, dict):
        return AgentMessage("1.0", run_id, datetime.now(timezone.utc), symbol, "multi", "structure", "NO_DATA")
    reports = {tf: analizar_estructura(datos_por_timeframe[tf], symbol, tf, run_id, as_of) for tf in AUTHORIZED_TIMEFRAMES if tf in datos_por_timeframe}
    status = "OK" if len(reports) == len(AUTHORIZED_TIMEFRAMES) else ("PARTIAL" if reports else "NO_DATA")
    return AgentMessage("1.0", run_id, datetime.now(timezone.utc), symbol, "multi", "structure", status, evidence=tuple(reports.items()), data_quality={"timeframes": tuple(reports)})
