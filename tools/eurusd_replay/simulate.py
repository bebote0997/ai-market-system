"""Bar-by-bar book for one replay configuration.

Pullback prices, expiry, partials, the EMA step machine, the session clock, the week-hold,
and the demo sizer are the live functions. A+B (4 hours, 15 pips, same-side replace, EMA
priority, 5-pip or market) is applied here, because that behaviour is another branch's
change to ``execution/eurusd_pullback.py`` and this replay does not edit that file.
"""
from dataclasses import dataclass, field, replace
from datetime import timedelta
from decimal import Decimal, ROUND_FLOOR
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from execution.broker_demo_router import FILL_ANCHOR_MAX_PIPS, broker_units
from execution.broker_demo_state import DEFAULT_MAX_UNITS
from execution.ema_cross import ENTRY_HOUR_END, ENTRY_HOUR_START, levels_from_fill, step
from execution import ema_cross
from execution import eurusd_pullback as pullback
from execution.eurusd_pullback import limit_expiry, limit_levels, limit_price, management_steps, market_anchor, round_fx
from core import fx_policy
from execution import trade_duration as td

PIP = fx_policy.pip_size("EURUSD")
NY = ZoneInfo("America/New_York")
START_EQUITY = Decimal("100000")
FIVE = timedelta(minutes=5)
CANCEL_PIPS = Decimal("15")


@dataclass(frozen=True)
class Config:
    name: str
    entry: str                 # "limit" or "market"
    offset_pips: Decimal
    ttl: timedelta
    runaway_pips: Decimal | None
    replace_same_side: bool
    ema_priority: bool
    relaxed_alignment: bool
    ema_be_r: Decimal | None
    ema_window: str            # "utc" or "et_7_11"
    pullback_enabled: bool = True
    ema_enabled: bool = True


def legacy():
    return Config("0_legacy", "limit", pullback.LIMIT_OFFSET_PIPS, pullback.LIMIT_TTL, None,
                  False, False, False, None, "utc")


def ab_limit():
    return Config("1_ab_5pip", "limit", Decimal("5"), timedelta(hours=4), CANCEL_PIPS,
                  True, True, False, None, "utc")


def ab_market():
    return Config("1_ab_market", "market", Decimal("0"), timedelta(0), None,
                  False, True, False, None, "utc")


def with_flags(base, *, name, relaxed=False, be=None, window=None, pullback=None, ema=None):
    return replace(base, name=name, relaxed_alignment=relaxed,
                   ema_be_r=base.ema_be_r if be is None else be,
                   ema_window=window or base.ema_window,
                   pullback_enabled=base.pullback_enabled if pullback is None else pullback,
                   ema_enabled=base.ema_enabled if ema is None else ema)


@dataclass
class Position:
    strategy: str
    side: str
    units: int
    left: int
    fill: Decimal
    anchor: Decimal
    stop: Decimal
    target: Decimal
    risk: Decimal
    risk_money: Decimal
    realized: Decimal
    stage: str
    opened_at: object
    hold_at: object
    setup_id: str | None


@dataclass
class Limit:
    side: str
    limit: Decimal
    stop: Decimal
    target: Decimal
    risk: Decimal
    signal: Decimal
    units: int
    risk_money: Decimal
    placed_at: object
    expiry: object
    setup_id: str | None


@dataclass
class Book:
    config: Config
    cash: Decimal = START_EQUITY
    position: Position | None = None
    limit: Limit | None = None
    ema_pending: object = None
    ema_done: set = field(default_factory=set)
    trades: list = field(default_factory=list)
    placed: int = 0
    filled: int = 0
    expired: int = 0
    cancelled_run: int = 0
    replaced: int = 0
    cancelled_ema: int = 0
    market_sent: int = 0
    market_refused: int = 0
    capped: int = 0
    limit_prices: list = field(default_factory=list)
    peak_equity: Decimal = START_EQUITY
    max_dd_pct: float = 0.0
    r_closed: Decimal = Decimal(0)
    peak_r: Decimal = Decimal(0)
    max_dd_r: Decimal = Decimal(0)


def _window_et(at):
    local = at.astimezone(NY)
    return local.weekday() < 5 and 7 <= local.hour < 11


def _expiry(at, ttl):
    previous = pullback.LIMIT_TTL
    pullback.LIMIT_TTL = ttl
    try:
        return limit_expiry(at)
    finally:
        pullback.LIMIT_TTL = previous


def _units(equity, entry, stop):
    decision = SimpleNamespace(risk_fraction=Decimal("0.01"))
    units = broker_units(equity, decision, entry, stop, DEFAULT_MAX_UNITS)
    capped = size_would_exceed(equity, entry, stop, units)
    return units, capped


def size_would_exceed(equity, entry, stop, units):
    raw = int((equity * Decimal("0.01") / abs(entry - stop)).to_integral_value(rounding=ROUND_FLOOR))
    return raw > units


def _quote(bar):
    spread = bar["ask"] - bar["bid"]
    if spread < 0 or spread > 0.01:
        return None
    half = spread / 2
    return {
        "bid_open": bar["open"] - half, "ask_open": bar["open"] + half,
        "bid_high": bar["high"] - half, "ask_high": bar["high"] + half,
        "bid_low": bar["low"] - half, "ask_low": bar["low"] + half,
        "bid": bar["bid"], "ask": bar["ask"],
        "high": bar["high"], "low": bar["low"],
    }


def _pnl(side, entry, exit_price, units):
    signed = (exit_price - entry) if side == "LONG" else (entry - exit_price)
    return signed * units


def _mark(book, quote):
    position = book.position
    if position is None or quote is None:
        unrealized = Decimal(0)
        open_r = Decimal(0)
    else:
        mark = quote["bid"] if position.side == "LONG" else quote["ask"]
        unrealized = _pnl(position.side, position.fill, mark, position.left)
        open_r = (position.realized + unrealized) / position.risk_money
    equity = book.cash + unrealized
    if equity > book.peak_equity:
        book.peak_equity = equity
    if book.peak_equity > 0:
        drawdown = float((book.peak_equity - equity) / book.peak_equity)
        if drawdown > book.max_dd_pct:
            book.max_dd_pct = drawdown
    r_equity = book.r_closed + open_r
    if r_equity > book.peak_r:
        book.peak_r = r_equity
    r_dd = book.peak_r - r_equity
    if r_dd > book.max_dd_r:
        book.max_dd_r = r_dd


def _close_position(book, exit_price, at, reason):
    position = book.position
    realized = position.realized + _pnl(position.side, position.fill, exit_price, position.left)
    trade_r = realized / position.risk_money
    book.cash += _pnl(position.side, position.fill, exit_price, position.left)
    book.r_closed += trade_r
    book.trades.append({
        "strategy": position.strategy, "side": position.side, "entry_at": position.opened_at, "exit_at": at,
        "r": float(trade_r), "pnl": float(realized), "reason": reason, "units": position.units,
        "fill": float(position.fill),
    })
    book.position = None


def _stop_target(side, stop, target, quote):
    """Broker stop and target. Stop wins when both are inside the bar. Gaps fill at the open quote."""
    if side == "LONG":
        if quote["bid_open"] <= stop:
            return "STOP", quote["bid_open"]
        if quote["bid_open"] >= target:
            return "TARGET", quote["bid_open"]
        if quote["bid_low"] <= stop:
            return "STOP", stop
        if quote["bid_high"] >= target:
            return "TARGET", target
    else:
        if quote["ask_open"] >= stop:
            return "STOP", quote["ask_open"]
        if quote["ask_open"] <= target:
            return "TARGET", quote["ask_open"]
        if quote["ask_high"] >= stop:
            return "STOP", stop
        if quote["ask_low"] <= target:
            return "TARGET", target
    return None, None


def _try_fill_limit(book, quote, bar_end):
    order = book.limit
    if order is None or quote is None:
        return
    if bar_end > order.expiry:
        book.limit = None
        book.expired += 1
        return
    runaway = book.config.runaway_pips
    if order.side == "LONG":
        gap = quote["ask_open"] <= order.limit
        touched = quote["ask_low"] <= order.limit
        ran = runaway is not None and quote["high"] >= order.signal + runaway * PIP
        fill_price = quote["ask_open"] if gap else order.limit
    else:
        gap = quote["bid_open"] >= order.limit
        touched = quote["bid_high"] >= order.limit
        ran = runaway is not None and quote["low"] <= order.signal - runaway * PIP
        fill_price = quote["bid_open"] if gap else order.limit
    if ran and not gap:
        book.limit = None
        book.cancelled_run += 1
        return
    if not (gap or touched):
        return
    book.limit = None
    book.filled += 1
    _open_position(book, "pullback", order.side, order.units, fill_price, order.limit, order.stop, order.target,
                   order.risk, order.risk_money, bar_end, order.setup_id)
    reason, price = _stop_target(order.side, book.position.stop, book.position.target, quote)
    if reason is not None:
        _close_position(book, price, bar_end, reason)


def _open_position(book, strategy, side, units, fill, anchor, stop, target, risk, risk_money, at, setup_id):
    hold_at, _reason = td.hold_boundary(at)
    book.position = Position(strategy, side, units, units, fill, anchor, stop, target, risk, risk_money,
                             Decimal(0), "NONE", at, hold_at, setup_id)


def _manage_pullback(book, quote, at):
    position = book.position
    if position is None or position.strategy != "pullback" or quote is None:
        return
    price = quote["bid"] if position.side == "LONG" else quote["ask"]
    steps = management_steps(side=position.side, anchor=position.anchor, risk=position.risk, units=position.units,
                             stage=position.stage, remaining=position.left, price=price, stop=position.stop)
    for managed in steps:
        if managed.kind == "PARTIAL":
            exit_price = price
            book.cash += _pnl(position.side, position.fill, exit_price, managed.units)
            position.realized += _pnl(position.side, position.fill, exit_price, managed.units)
            position.left = managed.remaining
            position.stage = managed.stage
        elif managed.kind == "STOP" and managed.stop is not None:
            position.stop = round_fx(managed.stop)
            position.stage = managed.stage
        elif managed.kind == "MARK":
            position.stage = managed.stage


def _arm_ema_be(book, quote):
    position = book.position
    multiple = book.config.ema_be_r
    if position is None or position.strategy != "ema" or multiple is None or quote is None:
        return
    price = quote["bid"] if position.side == "LONG" else quote["ask"]
    reached = price >= position.fill + multiple * position.risk if position.side == "LONG" else price <= position.fill - multiple * position.risk
    if not reached:
        return
    breakeven = position.fill
    tighter = breakeven > position.stop if position.side == "LONG" else breakeven < position.stop
    if tighter:
        position.stop = breakeven


def _place_limit(book, signal, at):
    if signal.trend != signal.side:
        return
    offset = book.config.offset_pips * PIP
    structural = Decimal(signal.structural)
    try:
        limit = round_fx(limit_price(signal.side, Decimal(signal.entry), offset))
        levels = limit_levels(signal.side, limit, structural)
    except (ValueError, ArithmeticError):
        return
    if isinstance(levels, str):
        return
    stop, target, _action, _pips = levels
    risk = abs(limit - stop)
    units, capped = _units(book.cash, limit, stop)
    if units < 1:
        return
    if capped:
        book.capped += 1
    book.limit = Limit(signal.side, limit, stop, target, risk, Decimal(signal.entry), units, risk * units,
                       at, _expiry(at, book.config.ttl), signal.setup_id)
    book.limit_prices.append(limit)
    book.placed += 1


def _place_market(book, signal, quote, at):
    if quote is None:
        book.market_refused += 1
        return
    quote_fill = quote["ask"] if signal.side == "LONG" else quote["bid"]
    try:
        anchored = market_anchor(Decimal(signal.entry), Decimal(signal.stop), Decimal(signal.target), quote_fill,
                                 pip=PIP, max_pips=FILL_ANCHOR_MAX_PIPS, rounder=round_fx)
    except (ValueError, ArithmeticError):
        book.market_refused += 1
        return
    if anchored is None:
        book.market_refused += 1
        return
    fill, stop, target = anchored
    units, capped = _units(book.cash, fill, stop)
    if units < 1:
        book.market_refused += 1
        return
    if capped:
        book.capped += 1
    book.market_sent += 1
    _open_position(book, "pullback_market", signal.side, units, fill, fill, stop, target, abs(fill - stop),
                   abs(fill - stop) * units, at, signal.setup_id)


def _enter_ema(book, signal, quote, at):
    if quote is None:
        return False
    fill_quote = quote["ask"] if signal.side == "LONG" else quote["bid"]
    try:
        fill, stop, target = levels_from_fill(signal.side, fill_quote)
    except (ValueError, ArithmeticError):
        return False
    units, capped = _units(book.cash, fill, stop)
    if units < 1:
        return False
    if capped:
        book.capped += 1
    book.ema_done.add(signal.signal_id)
    book.ema_pending = None
    _open_position(book, "ema", signal.side, units, fill, fill, stop, target, abs(fill - stop),
                   abs(fill - stop) * units, at, None)
    return True


def _ema_step(book, at, m15, h1, flat):
    decision = step(book.ema_pending, at=at, m15=m15, h1=h1, news_reason=None, session_open=True,
                    flat=flat, terminal_ids=book.ema_done)
    if decision.action == "ARM" and decision.signal is not None:
        book.ema_pending = decision.signal
    elif decision.action in ("CANCEL", "EXPIRE") and decision.signal is not None:
        book.ema_done.add(decision.signal.signal_id)
        book.ema_pending = None
    return decision


def _flatten_due(book, quote, bar_start, bar_end):
    position = book.position
    if position is None:
        return
    if bar_start >= position.hold_at and quote is not None:
        price = quote["bid_open"] if position.side == "LONG" else quote["ask_open"]
        _close_position(book, price, bar_start, "HOLD")
        return
    if quote is None:
        return
    reason, price = _stop_target(position.side, position.stop, position.target, quote)
    if reason is not None:
        _close_position(book, price, bar_end, reason)
        return
    if bar_end >= position.hold_at:
        price = quote["bid"] if position.side == "LONG" else quote["ask"]
        _close_position(book, price, bar_end, "HOLD")


def _reversal(book, signal, quote, at):
    position = book.position
    if position is None or position.strategy != "pullback" or signal.side == position.side or quote is None:
        return False
    in_profit = quote["bid"] > position.fill if position.side == "LONG" else quote["ask"] < position.fill
    if not in_profit:
        return False
    price = quote["bid"] if position.side == "LONG" else quote["ask"]
    _close_position(book, price, at, "REVERSAL")
    return True


def _pullback(book, signal, quote, at):
    """Place or keep the pullback order. True when the book stays busy afterwards."""
    if book.position is not None:
        if not (book.position.strategy == "pullback" and signal.side != book.position.side and _reversal(book, signal, quote, at)):
            return True
    if book.limit is not None:
        replace = (book.config.replace_same_side and signal.side == book.limit.side
                   and signal.setup_id and signal.setup_id != book.limit.setup_id)
        if not replace:
            return True
        book.limit = None
        book.replaced += 1
    if book.config.entry == "market":
        _place_market(book, signal, quote, at)
        return book.position is not None
    placed = book.placed
    _place_limit(book, signal, at)
    return book.placed > placed


def _decide(book, signal, quote, at, m15, h1):
    blocked = td.entries_blocked(at)
    ema_on = book.config.ema_enabled
    if not book.config.pullback_enabled:
        signal = None
    ema_handled = False
    if ema_on and book.config.ema_priority and book.position is None and not blocked:
        ema_handled = True
        decision = _ema_step(book, at, m15, h1, flat=True)
        if decision.action == "ENTER" and decision.signal is not None:
            if book.limit is not None:
                book.limit = None
                book.cancelled_ema += 1
            if _enter_ema(book, decision.signal, quote, at):
                return
    if not blocked and signal is not None and _pullback(book, signal, quote, at):
        if ema_on and not ema_handled:
            _ema_step(book, at, m15, h1, flat=False)
        return
    if not ema_on or ema_handled:
        return
    flat = book.position is None and book.limit is None and not blocked
    decision = _ema_step(book, at, m15, h1, flat=flat)
    if flat and decision.action == "ENTER" and decision.signal is not None:
        _enter_ema(book, decision.signal, quote, at)


def _pairs_ending(pairs, at, window):
    # pairs are (close_time, close) sorted. Take the last `window` with close_time <= at.
    lo, hi = 0, len(pairs)
    # binary search
    while lo < hi:
        mid = (lo + hi) // 2
        if pairs[mid][0] <= at:
            lo = mid + 1
        else:
            hi = mid
    start = max(0, lo - window)
    return pairs[start:lo]


def run(config, signals, bars, m15_pairs, h1_pairs, fresh_slots):
    """Replay ``config``. ``signals`` are scan records; relaxed ones are used only when the config asks."""
    by_slot = {}
    for signal in signals:
        if signal.relaxed and not config.relaxed_alignment:
            continue
        by_slot[signal.at] = signal
    fresh = set(fresh_slots)
    previous_window = ema_cross.in_entry_window
    if config.ema_window == "et_7_11":
        ema_cross.in_entry_window = _window_et
    elif config.ema_window != "utc":
        raise ValueError(config.ema_window)
    book = Book(config)
    try:
        for bar in bars:
            quote = _quote(bar)
            bar_end = bar["end"]
            _flatten_due(book, quote, bar["start"], bar_end)
            if book.position is None:
                _try_fill_limit(book, quote, bar_end)
            if book.position is not None and book.position.strategy == "ema":
                _arm_ema_be(book, quote)
            _mark(book, quote)
            if bar_end not in fresh:
                continue
            if book.position is not None and book.position.strategy == "pullback":
                _manage_pullback(book, quote, bar_end)
            signal = by_slot.get(bar_end)
            m15 = _pairs_ending(m15_pairs, bar_end, 500)
            h1 = _pairs_ending(h1_pairs, bar_end, 500)
            _decide(book, signal, quote, bar_end, m15, h1)
            _mark(book, quote)
        if book.position is not None:
            last = bars[-1]
            quote = _quote(last)
            if quote is not None:
                price = quote["bid"] if book.position.side == "LONG" else quote["ask"]
                _close_position(book, price, last["end"], "END")
        if book.limit is not None:
            book.limit = None
            book.expired += 1
    finally:
        ema_cross.in_entry_window = previous_window
    return book


def iter_bars(frame):
    index = frame.index
    opens = frame["Open"].to_numpy()
    highs = frame["High"].to_numpy()
    lows = frame["Low"].to_numpy()
    bids = frame["BidClose"].to_numpy()
    asks = frame["AskClose"].to_numpy()
    for i, start in enumerate(index):
        start_at = start.to_pydatetime()
        yield {
            "start": start_at, "end": start_at + FIVE,
            "open": Decimal(str(opens[i])), "high": Decimal(str(highs[i])), "low": Decimal(str(lows[i])),
            "bid": Decimal(str(bids[i])), "ask": Decimal(str(asks[i])),
        }


def close_pairs(frame, duration):
    out = []
    for start, close in zip(frame.index, frame["Close"].to_numpy()):
        out.append((start.to_pydatetime() + duration, Decimal(str(close))))
    return tuple(out)


# Kept so a reader can see the live window the UTC configs call.
_LIVE_WINDOW = (ENTRY_HOUR_START, ENTRY_HOUR_END)
