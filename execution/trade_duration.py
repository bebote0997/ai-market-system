"""Pre-demo trade-duration rule for open PAPER positions. Pure functions; the runtime owns every write.

- Day-1 NY close (the first 17:00 America/New_York after the fill, weekdays only, DST-correct via zoneinfo):
  close if the unrealized result is below +0.5R, otherwise keep the position with its stop moved to entry.
- Day 2: close early on an adverse market-structure change (a confirmed closed 15m BOS against the position
  after the Day-1 close).
- Day-2 NY close: force-close.
- Friday 17:00 NY: every open position is closed, whatever its day.

R is always measured against the ORIGINAL stop distance (``initial_stop`` once the stop was moved).
Nothing here reads a wall clock: callers pass the cycle time.
"""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
NY_CLOSE = time(17, 0)
FRIDAY = 4
MIN_KEEP_R = Decimal("0.5")
STAGE_DAY2 = "DAY2_BREAKEVEN"

CLOSE_DAY1_BELOW_HALF_R = "TIME_EXIT_DAY1_BELOW_0_5R"
CLOSE_DAY2_STRUCTURE = "TIME_EXIT_DAY2_STRUCTURE_CHANGE"
CLOSE_DAY2_NY_CLOSE = "TIME_EXIT_DAY2_NY_CLOSE"
CLOSE_FRIDAY = "TIME_EXIT_FRIDAY_NY_CLOSE"
CLOSE_REASONS = (CLOSE_DAY1_BELOW_HALF_R, CLOSE_DAY2_STRUCTURE, CLOSE_DAY2_NY_CLOSE, CLOSE_FRIDAY)
STOP_MOVED_TO_ENTRY = "STOP_MOVED_TO_ENTRY"


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def ny_close_after(at):
    """The first weekday 17:00 America/New_York strictly after ``at`` (UTC)."""
    if not _aware(at):
        raise ValueError("timezone-aware datetime required")
    day = at.astimezone(NEW_YORK).date()
    while True:
        candidate = datetime.combine(day, NY_CLOSE, tzinfo=NEW_YORK)
        if candidate.weekday() < 5 and candidate > at:
            return candidate.astimezone(timezone.utc)
        day += timedelta(days=1)


def day1_close(position):
    return ny_close_after(position.opened_at)


def day2_close(position):
    return ny_close_after(day1_close(position))


def next_boundary(position):
    """The next NY close at which the rule acts on ``position``."""
    return day1_close(position) if position.duration_stage is None else day2_close(position)


def is_friday_close(boundary):
    return boundary.astimezone(NEW_YORK).weekday() == FRIDAY


def unrealized_r(position, price):
    """Unrealized result in R of the original stop distance (Decimal), or None if that distance is not positive."""
    entry = Decimal(str(position.entry_price))
    stop = Decimal(str(position.initial_stop if position.initial_stop is not None else position.stop))
    risk = (entry - stop) if position.side == "LONG" else (stop - entry)
    if risk <= 0:
        return None
    price = Decimal(str(price))
    return ((price - entry) if position.side == "LONG" else (entry - price)) / risk


def boundary_decision(position, boundary, price):
    """('CLOSE', reason) or ('BREAKEVEN', None) for ``position`` at the NY close ``boundary`` and ``price``."""
    if is_friday_close(boundary):
        return "CLOSE", CLOSE_FRIDAY
    if position.duration_stage is not None:
        return "CLOSE", CLOSE_DAY2_NY_CLOSE
    r = unrealized_r(position, price)
    if r is None or r < MIN_KEEP_R:  # an unmeasurable R never keeps a position open
        return "CLOSE", CLOSE_DAY1_BELOW_HALF_R
    return "BREAKEVEN", None


def move_stop_to_entry(position):
    """Day-1 keep: remember the original stop, move the stop to the fill price and enter Day 2."""
    position.initial_stop = position.stop
    position.stop = position.entry_price
    position.duration_stage = STAGE_DAY2


def price_bar(bars, position, boundary):
    """The closed 5m bar that prices ``boundary``: the last one ending in (opened_at, boundary]; else, when the
    session had no such bar, the first closed bar ending after ``boundary`` (closing late beats never closing).
    ``bars`` are MarketBars; returns None when no bar qualifies (NO_DATA)."""
    closed = sorted((bar for bar in bars if bar.is_closed is True and bar.end > position.opened_at),
                    key=lambda bar: bar.start)
    before = [bar for bar in closed if bar.end <= boundary]
    if before:
        return before[-1]
    return next(iter(closed), None)


def adverse_structure(position, frame_15m, as_of):
    """The confirmed closed 15m BOS against ``position`` after its Day-1 close, or None."""
    if position.duration_stage is None:
        return None
    from agents.structure_agent import analizar_estructura
    report = analizar_estructura(frame_15m, position.symbol, "15m", "trade_duration", as_of)
    if report.status != "OK" or not report.evidence:
        return None
    bos = report.evidence[0].get("bos")
    against = "bearish" if position.side == "LONG" else "bullish"
    if not bos or bos.get("direction") != against:
        return None
    broken_at = bos["break_timestamp"].to_pydatetime()
    if broken_at <= day1_close(position) or broken_at + timedelta(minutes=15) > as_of:
        return None
    return bos


__all__ = ["CLOSE_DAY1_BELOW_HALF_R", "CLOSE_DAY2_NY_CLOSE", "CLOSE_DAY2_STRUCTURE", "CLOSE_FRIDAY",
           "CLOSE_REASONS", "MIN_KEEP_R", "STAGE_DAY2", "STOP_MOVED_TO_ENTRY", "adverse_structure",
           "boundary_decision", "day1_close", "day2_close", "is_friday_close", "move_stop_to_entry",
           "next_boundary", "ny_close_after", "price_bar", "unrealized_r"]
