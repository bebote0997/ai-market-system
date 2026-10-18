"""Friday close windows for the broker-demo router (OANDA halts EUR_USD at 17:00 America/New_York on Friday).

Pure helpers, DST-correct via zoneinfo; nothing reads a wall clock. The router uses them only when
``AI_FLOOR_FRIDAY_PREFLATTEN == "1"`` (exact); with the flag OFF it never imports this behaviour.

- ``preflatten``: Friday from (17:00 NY - ``minutes``) until 17:00 NY -> close routed trades, refuse entries.
- ``closed``: Friday >= 17:00 NY through Sunday < 17:00 NY -> refuse entries, keep retrying closes.
- ``open``: anything else.
"""
from datetime import datetime, timedelta

from execution.trade_duration import FRIDAY, NEW_YORK, NY_CLOSE

FLAG_ENV = "AI_FLOOR_FRIDAY_PREFLATTEN"
MINUTES_ENV = "AI_FLOOR_FRIDAY_PREFLATTEN_MINUTES"
DEFAULT_MINUTES, MIN_MINUTES, MAX_MINUTES = 30, 5, 240
OPEN, PREFLATTEN, CLOSED = "open", "preflatten", "closed"
ALERT_LEAD = timedelta(minutes=5)  # alert when trades remain from Friday 16:55 NY on
SUNDAY = 6


def preflatten_flag(env):
    """Only the exact value ``1`` turns the Friday pre-flatten on."""
    return env.get(FLAG_ENV) == "1"


def preflatten_minutes(env):
    """Integer minutes in [5, 240]; anything else -> 30."""
    raw = str(env.get(MINUTES_ENV) or "").strip()
    return int(raw) if raw.isdigit() and MIN_MINUTES <= int(raw) <= MAX_MINUTES else DEFAULT_MINUTES


def minutes_from_env(env):
    """The router setting: None (feature OFF) unless the flag is exactly ``1``."""
    return preflatten_minutes(env) if preflatten_flag(env) else None


def _local(now):
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    return now.astimezone(NEW_YORK)


def _friday_close(local):
    return datetime.combine(local.date(), NY_CLOSE, tzinfo=NEW_YORK)


def friday_window(now, minutes=DEFAULT_MINUTES):
    """``open`` / ``preflatten`` / ``closed`` for the aware datetime ``now``."""
    if type(minutes) is not int or not MIN_MINUTES <= minutes <= MAX_MINUTES:
        raise ValueError("minutes must be an int in [5, 240]")
    local = _local(now)
    weekday = local.weekday()
    if weekday == FRIDAY:
        close = _friday_close(local)
        if local >= close:
            return CLOSED
        return PREFLATTEN if local >= close - timedelta(minutes=minutes) else OPEN
    if weekday == FRIDAY + 1 or weekday == SUNDAY and local.time() < NY_CLOSE:
        return CLOSED
    return OPEN


def alert_due(now):
    """True from Friday 16:55 NY until the market reopens (Sunday 17:00 NY)."""
    local = _local(now)
    if local.weekday() == FRIDAY:
        return local >= _friday_close(local) - ALERT_LEAD
    return friday_window(now) == CLOSED
