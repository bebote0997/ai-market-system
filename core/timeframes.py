AUTHORIZED_TIMEFRAMES = ("1h", "15m", "5m")
DEFAULT_TIMEZONE = "UTC"


def validar_timeframe(timeframe):
    return isinstance(timeframe, str) and timeframe in AUTHORIZED_TIMEFRAMES
