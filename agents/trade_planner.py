import math
from datetime import datetime, timezone

import pandas as pd

from core.contracts import SetupAssessment, TradePlan


def crear_trade_plan(setup, datos_5m, symbol, run_id, as_of):
    if not isinstance(setup, SetupAssessment) or setup.status != "VALID_SETUP":
        return None
    if not isinstance(datos_5m, dict) or not datos_5m:
        return None
    frame = datos_5m.get("data")
    if frame is None or frame.empty or not isinstance(frame.index, type(frame.index)):
        return None
    frame = frame.sort_index()
    cutoff = pd.Timestamp(as_of)
    if frame.index.tz is None and cutoff.tzinfo is not None:
        cutoff = cutoff.tz_localize(None)
    elif frame.index.tz is not None and cutoff.tzinfo is None:
        cutoff = cutoff.tz_localize("UTC")
    frame = frame[frame.index <= cutoff]
    if frame.empty or "Close" not in frame.columns:
        return None
    entry = frame["Close"].iloc[-1]
    if entry is None or not math.isfinite(float(entry)) or float(entry) <= 0:
        return None
    entry = float(entry)
    stop = setup.invalidation
    if stop is None or not math.isfinite(float(stop)):
        return None
    stop = float(stop)
    if setup.side == "LONG":
        if not stop < entry:
            return None
        target = entry + 3 * (entry - stop)
    elif setup.side == "SHORT":
        if not stop > entry:
            return None
        target = entry - 3 * (stop - entry)
    else:
        return None
    return TradePlan("1.0", symbol, setup.side, "5m", entry, stop, target, 3.0, evidence=setup.evidence, invalidation=str(stop))
