"""Trade-duration rule for open PAPER positions and routed demo trades (DEC-HOLD-WEEK-1). Pure functions; the runtime
and the broker-demo router own every write.

- Max hold one week: a position is force-closed at the EARLIER of
  (a) the first Friday FRIDAY_FLATTEN_NY (16:45 America/New_York, pre-weekend) after its fill, and
  (b) MAX_HOLD_TRADING_DAYS (5) trading days after its fill (same NY wall-clock time; trading day = Monday-Friday
      in New York, exchange holidays included: no holiday calendar).
  With the defaults (a) always comes first; (b) only binds when configured below 5. Friday wins a tie.
- No position may live through the weekend: from Friday FRIDAY_FLATTEN_NY until the market reopens (Sunday 17:00 NY)
  new entries are refused (``entries_blocked``) and a position filled in that window anyway (a late fill) is closed at
  its first managed moment (its boundary is its own fill time).
- Pending (unfilled) orders: cancelled at the same boundary computed from their submission (``order_cancel_boundary``).
- Legacy Day-1 check (``TIME_EXIT_DAY1_BELOW_0_5R``), OFF by default (``AI_FLOOR_TIME_EXIT_DAY1=1`` turns it on): at
  the first weekday 17:00 NY after the fill, close below +0.5R, else move the stop to entry; the week rule then
  applies. The former Day-2 close and Day-2 adverse-structure exit are removed (superseded by the week rule).

Config (optional per-key env override, read once at import; an invalid value fails closed to the default):
``AI_FLOOR_MAX_HOLD_TRADING_DAYS`` (int 1..20, default 5), ``AI_FLOOR_FRIDAY_FLATTEN_NY`` ("HH:MM" in [13:00, 16:55],
default "16:45"), ``AI_FLOOR_TIME_EXIT_DAY1`` ("1" = on). R is always measured against the ORIGINAL stop distance
(``initial_stop`` once the stop was moved). Nothing here reads a wall clock: callers pass the cycle time.
"""
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
import logging
import os
import re
from zoneinfo import ZoneInfo

NEW_YORK = ZoneInfo("America/New_York")
NY_CLOSE = time(17, 0)
FRIDAY = 4
SUNDAY = 6
MIN_KEEP_R = Decimal("0.5")
STAGE_DAY2 = "DAY2_BREAKEVEN"  # stop moved to entry by the (optional) Day-1 check

MAX_HOLD_ENV = "AI_FLOOR_MAX_HOLD_TRADING_DAYS"
FRIDAY_FLATTEN_ENV = "AI_FLOOR_FRIDAY_FLATTEN_NY"
DAY1_RULE_ENV = "AI_FLOOR_TIME_EXIT_DAY1"
DEFAULT_MAX_HOLD_TRADING_DAYS = 5
DEFAULT_FRIDAY_FLATTEN_NY = "16:45"

CLOSE_DAY1_BELOW_HALF_R = "TIME_EXIT_DAY1_BELOW_0_5R"
CLOSE_FRIDAY = "TIME_EXIT_FRIDAY_NY_CLOSE"  # now at FRIDAY_FLATTEN_NY (16:45 NY), before the 17:00 weekend halt
CLOSE_MAX_HOLD = "TIME_EXIT_MAX_HOLD"
# Retired by DEC-HOLD-WEEK-1; kept so historical rows keep their labels.
CLOSE_DAY2_STRUCTURE = "TIME_EXIT_DAY2_STRUCTURE_CHANGE"
CLOSE_DAY2_NY_CLOSE = "TIME_EXIT_DAY2_NY_CLOSE"
CLOSE_REASONS = (CLOSE_DAY1_BELOW_HALF_R, CLOSE_FRIDAY, CLOSE_MAX_HOLD, CLOSE_DAY2_STRUCTURE, CLOSE_DAY2_NY_CLOSE)
STOP_MOVED_TO_ENTRY = "STOP_MOVED_TO_ENTRY"
CANCEL_FRIDAY = "TIME_CANCEL_FRIDAY_NY_CLOSE"
CANCEL_MAX_HOLD = "TIME_CANCEL_MAX_HOLD"
CANCEL_DAY2_NY_CLOSE = "TIME_CANCEL_DAY2_NY_CLOSE"  # retired; historical rows only
CANCEL_REASONS = (CANCEL_FRIDAY, CANCEL_MAX_HOLD, CANCEL_DAY2_NY_CLOSE)
_CANCEL_FOR = {CLOSE_FRIDAY: CANCEL_FRIDAY, CLOSE_MAX_HOLD: CANCEL_MAX_HOLD}


def _warn(name):
    logging.getLogger("ai_floor.runtime").warning("TRADE_DURATION_CONFIG_INVALID: %s; default used", name)


def max_hold_from_env(environ):
    raw = str(environ.get(MAX_HOLD_ENV) or "").strip()
    if not raw:
        return DEFAULT_MAX_HOLD_TRADING_DAYS
    if raw.isdigit() and 1 <= int(raw) <= 20:
        return int(raw)
    _warn(MAX_HOLD_ENV)
    return DEFAULT_MAX_HOLD_TRADING_DAYS


def friday_flatten_from_env(environ):
    raw = str(environ.get(FRIDAY_FLATTEN_ENV) or "").strip() or DEFAULT_FRIDAY_FLATTEN_NY
    match = re.fullmatch(r"(\d{2}):(\d{2})", raw)
    value = time(int(match[1]), int(match[2])) if match and int(match[1]) < 24 and int(match[2]) < 60 else None
    if value is None or not time(13, 0) <= value <= time(16, 55):
        _warn(FRIDAY_FLATTEN_ENV)
        return time(16, 45)
    return value


MAX_HOLD_TRADING_DAYS = max_hold_from_env(os.environ)
FRIDAY_FLATTEN_NY = friday_flatten_from_env(os.environ)
DAY1_RULE = os.environ.get(DAY1_RULE_ENV) == "1"
# Minutes between the Friday flatten and the 17:00 NY weekend halt (the broker pre-flatten default window).
FRIDAY_FLATTEN_LEAD_MINUTES = (NY_CLOSE.hour * 60) - (FRIDAY_FLATTEN_NY.hour * 60 + FRIDAY_FLATTEN_NY.minute)


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _local(at):
    if not _aware(at):
        raise ValueError("timezone-aware datetime required")
    return at.astimezone(NEW_YORK)


def ny_close_after(at):
    """The first weekday 17:00 America/New_York strictly after ``at`` (UTC)."""
    day = _local(at).date()
    while True:
        candidate = datetime.combine(day, NY_CLOSE, tzinfo=NEW_YORK)
        if candidate.weekday() < 5 and candidate > at:
            return candidate.astimezone(timezone.utc)
        day += timedelta(days=1)


def _friday_flatten(day):
    return datetime.combine(day, FRIDAY_FLATTEN_NY, tzinfo=NEW_YORK)


def entries_blocked(at):
    """True from Friday FRIDAY_FLATTEN_NY until the market reopens on Sunday 17:00 NY (DST-correct)."""
    local = _local(at)
    weekday = local.weekday()
    if weekday == FRIDAY:
        return local >= _friday_flatten(local.date())
    return weekday == FRIDAY + 1 or (weekday == SUNDAY and local.time() < NY_CLOSE)


def friday_flatten_after(at):
    """The first Friday FRIDAY_FLATTEN_NY strictly after ``at`` (UTC)."""
    day = _local(at).date()
    while True:
        candidate = _friday_flatten(day)
        if candidate.weekday() == FRIDAY and candidate > at:
            return candidate.astimezone(timezone.utc)
        day += timedelta(days=1)


def add_trading_days(at, days):
    """``at`` moved forward by ``days`` New York weekdays (Monday-Friday; holidays count), same NY wall-clock time."""
    local = _local(at)
    day, counted = local.date(), 0
    while counted < days:
        day += timedelta(days=1)
        if day.weekday() < 5:
            counted += 1
    return datetime.combine(day, local.time(), tzinfo=NEW_YORK).astimezone(timezone.utc)


def hold_boundary(opened_at):
    """(boundary, close reason) of the week rule for a fill at ``opened_at``: the earlier of the next Friday flatten
    and MAX_HOLD_TRADING_DAYS trading days later (Friday wins a tie). A fill inside the weekend window is due at once."""
    if entries_blocked(opened_at):
        return opened_at.astimezone(timezone.utc), CLOSE_FRIDAY
    friday = friday_flatten_after(opened_at)
    days = add_trading_days(opened_at, MAX_HOLD_TRADING_DAYS)
    return (friday, CLOSE_FRIDAY) if friday <= days else (days, CLOSE_MAX_HOLD)


def day1_close(position):
    return ny_close_after(position.opened_at)


def next_boundary(position):
    """The next moment at which the rule acts on ``position``: the week-rule boundary, or the earlier Day-1 NY close
    while the optional Day-1 check is ON and has not acted yet."""
    hold, _ = hold_boundary(position.opened_at)
    if DAY1_RULE and position.duration_stage is None:
        return min(day1_close(position), hold)
    return hold


def is_friday_close(boundary):
    return boundary.astimezone(NEW_YORK).weekday() == FRIDAY


def order_cancel_boundary(order):
    """(boundary, reason) at which the still-PENDING ``order`` is cancelled: the week-rule boundary of its submission
    (``order.as_of``), so no order can fill over the weekend or outlive the max hold."""
    boundary, reason = hold_boundary(order.as_of)
    return boundary, _CANCEL_FOR[reason]


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
    """('CLOSE', reason) or ('BREAKEVEN', None) for ``position`` at its due ``boundary`` and ``price``: the week rule
    always closes; the optional Day-1 check closes below +0.5R of the original stop distance, else breakeven."""
    hold, reason = hold_boundary(position.opened_at)
    if boundary >= hold or not DAY1_RULE or position.duration_stage is not None:
        return "CLOSE", reason
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


__all__ = ["CANCEL_DAY2_NY_CLOSE", "CANCEL_FRIDAY", "CANCEL_MAX_HOLD", "CANCEL_REASONS", "CLOSE_DAY1_BELOW_HALF_R",
           "CLOSE_DAY2_NY_CLOSE", "CLOSE_DAY2_STRUCTURE", "CLOSE_FRIDAY", "CLOSE_MAX_HOLD", "CLOSE_REASONS", "DAY1_RULE",
           "FRIDAY_FLATTEN_LEAD_MINUTES", "FRIDAY_FLATTEN_NY", "MAX_HOLD_TRADING_DAYS", "MIN_KEEP_R", "STAGE_DAY2",
           "STOP_MOVED_TO_ENTRY", "add_trading_days", "boundary_decision", "day1_close", "entries_blocked",
           "friday_flatten_after", "hold_boundary", "is_friday_close", "move_stop_to_entry", "next_boundary",
           "ny_close_after", "order_cancel_boundary", "price_bar", "unrealized_r"]
