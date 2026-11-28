"""Status copy and FX weekend hours for the Posiciones chart. Read-only.

The FX cash session is closed from Friday 17:00 America/New_York through Sunday
just before 17:00 (DST-aware). That is the same weekend window as
``execution.friday_close.friday_window`` ``closed``. Nothing here sends an order
or changes a brake.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from execution.friday_close import CLOSED, friday_window

ET = ZoneInfo("America/New_York")


def fx_market_closed(now):
    """True from Friday 5:00 PM ET inclusive until Sunday 5:00 PM ET exclusive."""
    return friday_window(now) == CLOSED


def _as_datetime(value):
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone-aware datetime required")
        return value
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        seconds = value / 1000.0 if value > 1e12 else float(value)
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_et_time(value):
    """``4:50 PM ET`` in America/New_York. Empty if the instant cannot be read."""
    moment = _as_datetime(value)
    if moment is None:
        return ""
    local = moment.astimezone(ET)
    hour = local.hour % 12 or 12
    suffix = "AM" if local.hour < 12 else "PM"
    return f"{hour}:{local.minute:02d} {suffix} ET"


def last_candle_time(candles):
    if not candles:
        return None
    last = candles[-1]
    if isinstance(last, dict):
        return last.get("time")
    return getattr(last, "time", None)


def chart_status(*, candles, stale, last_update, now):
    """Panel status line. ``sin datos`` only when there are no candles."""
    if not candles:
        return "sin datos"
    candle_clock = format_et_time(last_candle_time(candles))
    if fx_market_closed(now):
        return f"mercado cerrado · última vela {candle_clock}" if candle_clock else "mercado cerrado"
    if stale:
        return f"sin conexión · última vela {candle_clock}" if candle_clock else "sin conexión"
    read = format_et_time(last_update) or candle_clock
    return f"última lectura {read}" if read else "última lectura"
