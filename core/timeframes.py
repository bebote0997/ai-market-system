AUTHORIZED_TIMEFRAMES = ("1h", "15m", "5m")
DEFAULT_TIMEZONE = "UTC"

import pandas as pd


def validar_timeframe(timeframe):
    return isinstance(timeframe, str) and timeframe in AUTHORIZED_TIMEFRAMES


def mask_hasta_as_of(index, as_of):
    """Return a temporal mask; ambiguous naive/aware combinations fail closed."""
    if not isinstance(index, pd.DatetimeIndex) or as_of is None:
        return None
    cutoff = pd.Timestamp(as_of)
    if index.tz is None and cutoff.tzinfo is not None:
        return None
    if index.tz is not None and cutoff.tzinfo is None:
        return None
    if index.tz is not None:
        cutoff = cutoff.tz_convert(index.tz)
    return index <= cutoff
