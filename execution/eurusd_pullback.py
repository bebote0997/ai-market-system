"""EURUSD H1 pullback limit (OANDA practice demo only). Pure functions: no network, no clock read, no broker.

The engine still chooses the side, the stop and the target. This module only decides:

* whether that side agrees with the closed H1 EMA(200),
* the limit price (signal ± one Wilder ATR(14) on closed H1 bars),
* when the unfilled limit expires,
* how a filled position is scaled out (1/3 at +0.5R, 1/3 at +1R with the stop to breakeven, then a 1R trail).

R is the engine's risk, ``abs(signal − stop)``. Profit levels are measured from the fill, because that is the
price the position actually has. Stop and target prices are never moved onto the fill.

Wilder ATR is the usual ATR(14): the first value is the simple average of the first 14 true ranges, then
``(previous × 13 + true range) / 14``. EMA(200) is seeded with the simple average of the first 200 closes.

A row is managed by these steps only when the router tagged it with ``STRATEGY``. Rows opened before that tag
exist keep the previous manager, so a deploy does not touch an open position.
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from execution import trade_duration as td


STRATEGY = "EURUSD_H1_PULLBACK"
SYMBOL = "EURUSD"
PULLBACK_ENV = "AI_FLOOR_EURUSD_LIMIT_PULLBACK"
ATR_PERIOD = 14
EMA_PERIOD = 200
LIMIT_TTL = timedelta(hours=24)
HALF_R = Decimal("0.5")
ONE_R = Decimal("1")
# Rounding slack for "filled at the limit". A fill this far through the wrong side is slippage, not the level.
FILL_SLACK_PIPS = Decimal("0.2")
_PRICE = Decimal("0.00001")


def pullback_flag(env):
    """Demo default ON. Only the exact value ``0`` restores immediate market entries (the rollback)."""
    return (env or {}).get(PULLBACK_ENV) != "0"


def round_fx(price):
    """Same 5-decimal half-up grid as ``execution.oanda_client.round_price`` (EURUSD)."""
    if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
        raise ValueError("price must be a finite positive Decimal")
    return price.quantize(_PRICE, rounding=ROUND_HALF_UP)


def _decimal(value):
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() else None


def bars_from_frame(frame):
    """Oldest-first ``(high, low, close)`` Decimals from a snapshot frame or a sequence of triples.

    ``None`` when the frame is missing or any bar is unusable (the caller skips the trade; nothing is invented).
    An empty frame is ``()``.
    """
    if frame is None:
        return None
    if isinstance(frame, (list, tuple)):
        return _triples(frame)
    try:
        ordered = frame.sort_index() if hasattr(frame, "sort_index") else frame
        triples = list(zip(ordered["High"], ordered["Low"], ordered["Close"]))
    except (TypeError, KeyError, AttributeError, ValueError):
        return None
    return _triples(triples)


def _triples(rows):
    out = []
    for row in rows:
        try:
            high, low, close = (_decimal(row[0]), _decimal(row[1]), _decimal(row[2]))
        except (TypeError, IndexError, KeyError):
            return None
        if None in (high, low, close) or min(high, low, close) <= 0:
            return None
        if high < low or high < max(low, close) or low > min(high, close):
            return None
        out.append((high, low, close))
    return tuple(out)


def ema(closes, period=EMA_PERIOD):
    """EMA of ``closes`` (oldest first). ``None`` when fewer than ``period`` values are present."""
    if closes is None or len(closes) < period or period < 1:
        return None
    seed = sum(closes[:period], Decimal(0)) / Decimal(period)
    weight = Decimal(2) / Decimal(period + 1)
    value = seed
    for price in closes[period:]:
        value = price * weight + value * (1 - weight)
    return value


def true_ranges(bars):
    """True range of each bar after the first (needs the previous close). Oldest first."""
    ranges = []
    previous = None
    for high, low, close in bars:
        if previous is None:
            previous = close
            continue
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
        previous = close
    return ranges


def wilder_atr(bars, period=ATR_PERIOD):
    """Wilder ATR. ``None`` until ``period`` true ranges exist (``period + 1`` bars)."""
    ranges = true_ranges(bars)
    if len(ranges) < period or period < 1:
        return None
    value = sum(ranges[:period], Decimal(0)) / Decimal(period)
    smooth = Decimal(period - 1)
    for true_range in ranges[period:]:
        value = (value * smooth + true_range) / Decimal(period)
    return value


def trend_side(price, ema_value):
    """``LONG`` when price is strictly above the EMA, ``SHORT`` when strictly below, else ``None``."""
    if price > ema_value:
        return "LONG"
    if price < ema_value:
        return "SHORT"
    return None


def limit_price(side, signal, atr_value):
    """Buy limit below the signal, sell limit above it, by one ATR."""
    if side == "LONG":
        return signal - atr_value
    if side == "SHORT":
        return signal + atr_value
    raise ValueError("side must be LONG or SHORT")


def geometry_ok(side, limit, stop, target):
    """The limit has to sit strictly between the engine's stop and target. Otherwise the order is not sent."""
    if side == "LONG":
        return stop < limit < target
    if side == "SHORT":
        return target < limit < stop
    return False


def limit_expiry(submitted):
    """24 hours after ``submitted``, capped by the next Friday entry ban (16:45 America/New_York).

    A resting limit must not be able to fill while the demo already refuses new entries. When ``submitted`` is
    already inside that ban the expiry is ``submitted`` itself (the caller does not place the order).
    """
    if not isinstance(submitted, datetime) or submitted.tzinfo is None or submitted.utcoffset() is None:
        raise ValueError("timezone-aware datetime required")
    horizon = submitted.astimezone(timezone.utc) + LIMIT_TTL
    if td.entries_blocked(submitted):
        return submitted.astimezone(timezone.utc)
    cap = td.friday_flatten_after(submitted)
    return horizon if horizon <= cap else cap


def gtd_timestamp(at):
    """RFC3339 UTC timestamp for an OANDA GTD order."""
    utc = at.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond:06d}000Z"


@dataclass(frozen=True)
class PullbackPlan:
    limit: Decimal
    atr: Decimal
    ema: Decimal
    price: Decimal
    risk: Decimal


def plan_pullback(side, signal, stop, target, bars, *, rounder=round_fx):
    """``PullbackPlan`` or a short reason string. Never raises on bad market data (that reason is ``h1_no_data``)."""
    if side not in ("LONG", "SHORT"):
        return "side_invalid"
    for name, value in (("signal", signal), ("stop", stop), ("target", target)):
        if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
            return f"{name}_invalid"
    parsed = _triples(bars) if _is_bars(bars) else bars_from_frame(bars)
    if not parsed or len(parsed) < EMA_PERIOD:
        return "h1_no_data"
    closes = tuple(bar[2] for bar in parsed)
    ema_value = ema(closes)
    atr_value = wilder_atr(parsed)
    if ema_value is None or atr_value is None or atr_value <= 0:
        return "h1_no_data"
    price = closes[-1]
    allowed = trend_side(price, ema_value)
    if allowed is None:
        return "trend_flat"
    if allowed != side:
        return "trend_filter"
    raw = limit_price(side, signal, atr_value)
    try:
        limit = rounder(raw)
    except (ValueError, ArithmeticError, TypeError):
        return "limit_invalid"
    if not geometry_ok(side, limit, stop, target):
        return "limit_through_stop"
    return PullbackPlan(limit, atr_value, ema_value, price, abs(signal - stop))


def _is_bars(value):
    return isinstance(value, tuple) and bool(value) and isinstance(value[0], tuple) and len(value[0]) == 3


def _level(side, fill, risk, multiple):
    delta = risk * multiple
    return fill + delta if side == "LONG" else fill - delta


def _reached(side, price, level):
    return price >= level if side == "LONG" else price <= level


def trail_stop(side, fill, risk, price):
    """Stop one R behind ``price``, never worse than breakeven (the fill)."""
    if side == "LONG":
        return max(price - risk, fill)
    if side == "SHORT":
        return min(price + risk, fill)
    raise ValueError("side must be LONG or SHORT")


def _tighter(side, new_stop, current):
    if current is None:
        return True
    return new_stop > current if side == "LONG" else new_stop < current


@dataclass(frozen=True)
class ManageStep:
    """One management action, in the order it must be sent.

    ``kind`` is ``PARTIAL`` (close ``units``, ``remaining`` stay open), ``STOP`` (modify the stop) or ``MARK``
    (record ``stage`` only; the stop is already where it should be). ``journals`` are the event kinds to record
    after the broker accepts the action.
    """
    kind: str
    stage: str
    label: str
    journals: tuple
    units: int = None
    remaining: int = None
    stop: Decimal = None


def _can_partial(units, remaining):
    qty = units // 3
    return qty >= 1 and remaining - qty >= 1, qty


def management_steps(*, side, fill, risk, units, stage, remaining, price, stop):
    """Ordered partial / breakeven / trail steps for the current stage and price. Empty when nothing is due.

    A price that gaps through both +0.5R and +1R returns both partials, then the stop, in that order. The caller
    applies them until one fails and calls again from the stage it managed to save.
    """
    if (side not in ("LONG", "SHORT") or not isinstance(fill, Decimal) or not isinstance(risk, Decimal)
            or not isinstance(price, Decimal) or risk <= 0 or type(units) is not int or units < 1):
        return ()
    stage = stage or "NONE"
    if stage == "TRAIL":
        new_stop = trail_stop(side, fill, risk, price)
        if _tighter(side, new_stop, stop):
            return (ManageStep("STOP", "TRAIL", "TRAIL", ("TRAIL",), remaining=remaining, stop=new_stop),)
        return ()
    left = units if remaining is None else int(remaining)
    steps = []
    half = _level(side, fill, risk, HALF_R)
    one = _level(side, fill, risk, ONE_R)
    if stage == "NONE" and _reached(side, price, half):
        ok, qty = _can_partial(units, left)
        if ok:
            left -= qty
            stage = "HALF"
            steps.append(ManageStep("PARTIAL", "HALF", "0.5R", ("PARTIAL_CLOSE",), units=qty, remaining=left))
    if stage == "HALF" and _reached(side, price, one):
        ok, qty = _can_partial(units, left)
        if ok:
            left -= qty
            stage = "ONE"
            steps.append(ManageStep("PARTIAL", "ONE", "1R", ("PARTIAL_CLOSE",), units=qty, remaining=left))
    ready = _reached(side, price, one) and (
        stage == "ONE" or (stage == "NONE" and units // 3 < 1)
        or (stage == "HALF" and not _can_partial(units, left)[0]))
    if not ready:
        return tuple(steps)
    new_stop = trail_stop(side, fill, risk, price)
    beyond = new_stop != fill and ((side == "LONG" and new_stop > fill) or (side == "SHORT" and new_stop < fill))
    journals = ("BREAKEVEN", "TRAIL") if beyond else ("BREAKEVEN",)
    kind = "STOP" if _tighter(side, new_stop, stop) else "MARK"
    steps.append(ManageStep(kind, "TRAIL", "BE", journals, remaining=left, stop=new_stop if kind == "STOP" else None))
    return tuple(steps)


def limit_fill_verdict(side, limit, fill, *, pip, slack_pips=FILL_SLACK_PIPS):
    """``pending`` when there is no fill yet (a resting limit is not a bad fill).

    ``accepted`` when the fill is at the limit or better, within ``slack_pips``. ``worse`` when the fill is through
    the limit on the expensive side. The stop and target are not moved in any of these cases.
    """
    if fill is None:
        return "pending"
    if side not in ("LONG", "SHORT") or pip is None or pip <= 0:
        raise ValueError("side and pip are required")
    slack = slack_pips * pip
    if side == "LONG":
        return "accepted" if fill <= limit + slack else "worse"
    return "accepted" if fill >= limit - slack else "worse"


def market_anchor(entry, stop, target, quote_fill, *, pip, max_pips, rounder):
    """DEC-FILL-ANCHOR-2 for a MARKET order: translate entry/stop/target onto the quote, or ``None`` when the gap
    is wider than ``max_pips`` (the caller refuses the entry). Raises when a price cannot be rounded."""
    fill = rounder(quote_fill)
    delta = fill - entry
    if abs(delta / pip) > max_pips:
        return None
    return fill, rounder(stop + delta), rounder(target + delta)


def anchor_outcome(*, kind, pending, side, entry, stop, target, quote_fill, limit, fill, pip, max_pips, rounder):
    """How the fill-anchor rule treats one order.

    Market orders keep the translation and the max-pip refusal. Limit orders keep the engine's stop and target.
    A pending limit is ``pending``, not a refusal and not an out-of-policy fill. A limit fill at the intended
    level (or better) is ``accepted``.
    """
    levels = (entry, stop, target)
    if kind == "LIMIT":
        if pending or fill is None:
            return "pending", levels
        return limit_fill_verdict(side, limit, fill, pip=pip), levels
    if kind != "MARKET":
        raise ValueError("kind must be MARKET or LIMIT")
    try:
        anchored = market_anchor(entry, stop, target, quote_fill, pip=pip, max_pips=max_pips, rounder=rounder)
    except (ValueError, ArithmeticError, TypeError):
        return "skipped", levels
    if anchored is None:
        return "refused", None
    return "anchored", anchored
