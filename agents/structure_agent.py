"""Deterministic structure scout.

A swing is confirmed only after the following closed candle exists. Therefore
reports never use an unfinished candle and never inspect data after the report
cutoff supplied by the caller.
"""
from datetime import datetime, timezone
import math

import pandas as pd

from core.contracts import AgentMessage
from core.timeframes import AUTHORIZED_TIMEFRAMES, mask_hasta_as_of


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
        mask = mask_hasta_as_of(frame.index, as_of)
        if mask is None:
            return None
        frame = frame[mask]
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


def _break_of_structure(frame, highs, lows):
    candidates = []
    for swing in reversed(highs):
        confirmations = frame.index[frame.index > swing["timestamp"]]
        for timestamp in confirmations:
            close = frame.loc[timestamp, "Close"]
            if close > swing["price"]:
                candidates.append({
                    "type": "BOS",
                    "direction": "bullish",
                    "broken_level": swing["price"],
                    "swing_timestamp": swing["pivot_timestamp"],
                    "confirmation_timestamp": swing["timestamp"],
                    "break_timestamp": timestamp,
                    "break_close": float(close),
                })
                break
    for swing in reversed(lows):
        confirmations = frame.index[frame.index > swing["timestamp"]]
        for timestamp in confirmations:
            close = frame.loc[timestamp, "Close"]
            if close < swing["price"]:
                candidates.append({
                    "type": "BOS",
                    "direction": "bearish",
                    "broken_level": swing["price"],
                    "swing_timestamp": swing["pivot_timestamp"],
                    "confirmation_timestamp": swing["timestamp"],
                    "break_timestamp": timestamp,
                    "break_close": float(close),
                })
                break
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["break_timestamp"])


def _retracement(frame, highs, lows):
    if len(highs) < 2 or len(lows) < 2:
        return None
    bullish_highs = [high for index, high in enumerate(highs[1:], 1) if high["price"] > highs[index - 1]["price"]]
    bullish_lows = [low for index, low in enumerate(lows[1:], 1) if low["price"] > lows[index - 1]["price"]]
    if bullish_highs and bullish_lows:
        impulse_high = bullish_highs[-1]
        protected_low = bullish_lows[-1]
        close = float(frame["Close"].iloc[-1])
        if protected_low["price"] < close < impulse_high["price"]:
            return {
                "direction": "bullish",
                "classification": "HEURISTIC",
                "impulse_high": impulse_high["price"],
                "protected_low": protected_low["price"],
                "current_close": close,
                "impulse_timestamp": impulse_high["timestamp"],
                "protected_timestamp": protected_low["timestamp"],
                "current_timestamp": frame.index[-1],
            }
    bearish_highs = [high for index, high in enumerate(highs[1:], 1) if high["price"] < highs[index - 1]["price"]]
    bearish_lows = [low for index, low in enumerate(lows[1:], 1) if low["price"] < lows[index - 1]["price"]]
    if bearish_highs and bearish_lows:
        impulse_low = bearish_lows[-1]
        protected_high = bearish_highs[-1]
        close = float(frame["Close"].iloc[-1])
        if impulse_low["price"] < close < protected_high["price"]:
            return {
                "direction": "bearish",
                "classification": "HEURISTIC",
                "impulse_low": impulse_low["price"],
                "protected_high": protected_high["price"],
                "current_close": close,
                "impulse_timestamp": impulse_low["timestamp"],
                "protected_timestamp": protected_high["timestamp"],
                "current_timestamp": frame.index[-1],
            }
    return None


def analizar_estructura(datos, symbol="UNKNOWN", timeframe="unknown", run_id="structure", as_of=None):
    timestamp = as_of if isinstance(as_of, datetime) and as_of.tzinfo is not None else datetime.now(timezone.utc)
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
        "retracement": _retracement(frame, highs, lows),
        "bos": _break_of_structure(frame, highs, lows),
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
