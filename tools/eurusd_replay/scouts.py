"""Structure and liquidity, transcribed from the deterministic scouts.

``agents.structure_agent`` and ``agents.liquidity_agent`` are the source of the rules. Their
pandas loops are too slow to call on every 15-minute slot of a five-year book. This module
repeats those rules on numpy arrays. ``equivalent`` compares a window with the real scouts;
the scan audits a sample of live windows the same way and stops if they disagree.
"""
from datetime import datetime, timezone

import numpy as np

from agents.liquidity_agent import analizar_liquidez
from agents.structure_agent import analizar_estructura
from core.contracts import AgentMessage


def _swings_structure(high, low, index):
    highs, lows = [], []
    for i in range(1, len(high) - 1):
        if high[i] > high[i - 1] and high[i] > high[i + 1]:
            highs.append({"timestamp": index[i + 1], "pivot_timestamp": index[i], "price": float(high[i])})
        if low[i] < low[i - 1] and low[i] < low[i + 1]:
            lows.append({"timestamp": index[i + 1], "pivot_timestamp": index[i], "price": float(low[i])})
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


def _first_break(index_ns, close, swing, bullish):
    start = int(np.searchsorted(index_ns, swing["timestamp"].value, side="right"))
    level = swing["price"]
    for j in range(start, len(close)):
        price = float(close[j])
        if price > level if bullish else price < level:
            return j
    return None


def _break_of_structure(index, index_ns, close, highs, lows):
    candidates = []
    for bullish, swings in ((True, highs), (False, lows)):
        for swing in reversed(swings):
            found = _first_break(index_ns, close, swing, bullish)
            if found is None:
                continue
            candidates.append({
                "type": "BOS",
                "direction": "bullish" if bullish else "bearish",
                "broken_level": swing["price"],
                "swing_timestamp": swing["pivot_timestamp"],
                "confirmation_timestamp": swing["timestamp"],
                "break_timestamp": index[found],
                "break_close": float(close[found]),
            })
    if not candidates:
        return None
    return max(candidates, key=lambda item: item["break_timestamp"])


def _retracement(highs, lows, close):
    if len(highs) < 2 or len(lows) < 2:
        return None
    last = float(close[-1])
    bullish_highs = [high for i, high in enumerate(highs[1:], 1) if high["price"] > highs[i - 1]["price"]]
    bullish_lows = [low for i, low in enumerate(lows[1:], 1) if low["price"] > lows[i - 1]["price"]]
    if bullish_highs and bullish_lows:
        impulse_high, protected_low = bullish_highs[-1], bullish_lows[-1]
        if protected_low["price"] < last < impulse_high["price"]:
            return {
                "direction": "bullish", "classification": "HEURISTIC",
                "impulse_high": impulse_high["price"], "protected_low": protected_low["price"],
                "current_close": last, "impulse_timestamp": impulse_high["timestamp"],
                "protected_timestamp": protected_low["timestamp"], "current_timestamp": None,
            }
    bearish_highs = [high for i, high in enumerate(highs[1:], 1) if high["price"] < highs[i - 1]["price"]]
    bearish_lows = [low for i, low in enumerate(lows[1:], 1) if low["price"] < lows[i - 1]["price"]]
    if bearish_highs and bearish_lows:
        impulse_low, protected_high = bearish_lows[-1], bearish_highs[-1]
        if impulse_low["price"] < last < protected_high["price"]:
            return {
                "direction": "bearish", "classification": "HEURISTIC",
                "impulse_low": impulse_low["price"], "protected_high": protected_high["price"],
                "current_close": last, "impulse_timestamp": impulse_low["timestamp"],
                "protected_timestamp": protected_high["timestamp"], "current_timestamp": None,
            }
    return None


def _message(kind, symbol, timeframe, run_id, as_of, status, report, warnings=()):
    stamp = as_of if isinstance(as_of, datetime) and as_of.tzinfo is not None else datetime.now(timezone.utc)
    evidence = (report,) if report is not None else ()
    quality = report["data_quality"] if report else {"closed_bars": 0}
    return AgentMessage("1.0", run_id, stamp, symbol, timeframe, kind, status,
                        evidence=evidence, data_quality=quality, warnings=warnings)


def structure_message(high, low, open_, close, index, symbol, timeframe, run_id, as_of):
    """Same decision fields as ``analizar_estructura`` on this closed window."""
    n = len(close)
    if n == 0:
        return _message("structure", symbol, timeframe, run_id, as_of, "NO_DATA", None)
    if n < 3:
        report = {"data_quality": {"closed_bars": n}}
        return _message("structure", symbol, timeframe, run_id, as_of, "PARTIAL", report, ("insufficient_bars",))
    highs, lows = _swings_structure(high, low, index)
    bias = _classify(highs, lows)
    index_ns = np.array([stamp.value for stamp in index], dtype=np.int64)
    retracement = _retracement(highs, lows, close)
    if retracement is not None:
        retracement["current_timestamp"] = index[-1]
    report = {
        "symbol": symbol, "timeframe": timeframe, "bias": bias, "structure_state": bias,
        "levels": {
            "support": min((item["price"] for item in lows), default=None),
            "resistance": max((item["price"] for item in highs), default=None),
        },
        "retracement": retracement,
        "bos": _break_of_structure(index, index_ns, close, highs, lows),
        "evidence": [{"timestamp": index[-1], "type": "closed_bar"}],
        "data_quality": {"closed_bars": n, "definition": "swings require one closed confirmation bar"},
    }
    return _message("structure", symbol, timeframe, run_id, as_of, "OK", report)


def _pivots(high, low, index):
    highs, lows = [], []
    for i in range(1, len(high) - 1):
        if high[i] > high[i - 1] and high[i] > high[i + 1]:
            highs.append((float(high[i]), index[i]))
        if low[i] < low[i - 1] and low[i] < low[i + 1]:
            lows.append((float(low[i]), index[i]))
    return highs, lows


def _equal(pivots):
    levels = []
    for i in range(1, len(pivots)):
        reference, current = pivots[i - 1][0], pivots[i][0]
        if abs(current - reference) <= abs(reference) * 0.001:
            levels.append({"price": current, "timestamps": [pivots[i - 1][1], pivots[i][1]],
                           "classification": "HEURISTIC"})
    return levels


def liquidity_message(high, low, close, index, symbol, timeframe, run_id, as_of):
    """Same decision fields as ``analizar_liquidez`` on this closed window."""
    n = len(close)
    if n == 0:
        return _message("liquidity", symbol, timeframe, run_id, as_of, "NO_DATA", None)
    if n < 3:
        report = {"data_quality": {"closed_bars": n}}
        return _message("liquidity", symbol, timeframe, run_id, as_of, "PARTIAL", report, ("insufficient_bars",))
    pivot_highs, pivot_lows = _pivots(high, low, index)
    equal_highs, equal_lows = _equal(pivot_highs), _equal(pivot_lows)
    previous_high, previous_low = float(np.max(high[:-1])), float(np.min(low[:-1]))
    last_high, last_low, last_close = float(high[-1]), float(low[-1]), float(close[-1])
    sweeps = []
    if last_high > previous_high and last_close < previous_high:
        sweeps.append({"type": "high", "timestamp": index[-1], "price": last_high})
    if last_low < previous_low and last_close > previous_low:
        sweeps.append({"type": "low", "timestamp": index[-1], "price": last_low})
    report = {
        "symbol": symbol, "timeframe": timeframe,
        "liquidity_above": [{"price": item["price"], "type": "equal_high", "classification": "HEURISTIC"}
                            for item in equal_highs],
        "liquidity_below": [{"price": item["price"], "type": "equal_low", "classification": "HEURISTIC"}
                            for item in equal_lows],
        "sweeps": sweeps,
        "data_quality": {"closed_bars": n, "equal_level_tolerance": 0.001},
    }
    return _message("liquidity", symbol, timeframe, run_id, as_of, "OK", report)


def _payload(message):
    if not message.evidence:
        return None
    return message.evidence[0]


def _same(left, right):
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(_same(left[key], right[key]) for key in left)
    if isinstance(left, (list, tuple)) and isinstance(right, (list, tuple)):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    if isinstance(left, float) and isinstance(right, float):
        return left == right or (np.isnan(left) and np.isnan(right))
    try:
        if left == right:
            return True
    except Exception:
        return False
    return str(left) == str(right)


DECISION_STRUCTURE = ("bias", "structure_state", "levels", "retracement", "bos")
DECISION_LIQUIDITY = ("liquidity_above", "liquidity_below", "sweeps")


def equivalent(frame, symbol, timeframe, as_of):
    """True when the transcription and the real scout agree on every field the validator reads."""
    high = frame["High"].to_numpy()
    low = frame["Low"].to_numpy()
    close = frame["Close"].to_numpy()
    open_ = frame["Open"].to_numpy()
    index = list(frame.index)
    real_s = analizar_estructura(frame, symbol, timeframe, "parity", as_of)
    fast_s = structure_message(high, low, open_, close, index, symbol, timeframe, "parity", as_of)
    real_l = analizar_liquidez(frame, symbol, timeframe, "parity", as_of)
    fast_l = liquidity_message(high, low, close, index, symbol, timeframe, "parity", as_of)
    if real_s.status != fast_s.status or real_l.status != fast_l.status:
        return False
    if real_s.status != "OK":
        return True
    left, right = _payload(real_s), _payload(fast_s)
    if any(not _same(left.get(key), right.get(key)) for key in DECISION_STRUCTURE):
        return False
    left, right = _payload(real_l), _payload(fast_l)
    return all(_same(left.get(key), right.get(key)) for key in DECISION_LIQUIDITY)


def frame_from_csv_window(frame):
    """Closed OHLC frame shaped the way the live scouts expect."""
    out = frame[["Open", "High", "Low", "Close"]].copy()
    out["symbol"] = "EURUSD"
    out["is_closed"] = True
    return out
