import math
from datetime import datetime, timezone

import pandas as pd

from core.contracts import SetupAssessment, TradePlan
from core.timeframes import mask_hasta_as_of


def crear_trade_plan(setup, datos_5m, symbol, run_id, as_of):
    if not isinstance(setup, SetupAssessment) or setup.status != "VALID_SETUP":
        return None
    if not isinstance(datos_5m, dict) or not datos_5m:
        return None
    frame = datos_5m.get("data")
    if frame is None or frame.empty or not isinstance(frame.index, type(frame.index)):
        return None
    frame = frame.sort_index()
    mask = mask_hasta_as_of(frame.index, as_of)
    if mask is None:
        return None
    if "is_closed" in frame.columns:
        frame = frame.loc[mask & (frame["is_closed"] == True)]  # noqa: E712
    else:
        frame = frame.loc[mask]
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
    return TradePlan("1.0", symbol, setup.side, "5m", entry, stop, target, 3.0, evidence=setup.evidence, invalidation=str(stop), run_id=run_id, as_of=as_of)
