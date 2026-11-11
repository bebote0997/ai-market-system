"""London Asian-range breakout (USDJPY) and London 16:00 fix fade (GBPUSD, then EURUSD).

Off unless ``AI_FLOOR_SESSION_COMBO`` is exactly ``1`` and ``AI_FLOOR_BROKER`` is exactly ``exness``.
The flag never sends an order by itself: ``ExnessBroker`` still requires the real-execution double key.

Stop entries are emulated. The Exness pending-order page names types ``limit``, ``stop`` and ``sltp`` but not the
JSON field, so this module does not send a broker stop. It arms both prices in memory and, when one M5 bar
crosses exactly one of them, sends a market order with the same stop and target. A bar that crosses both is a skip.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
import logging
from zoneinfo import ZoneInfo

from execution.exness_sizing import RISK_FRACTION_CAP, floor_to_step


LOG = logging.getLogger("ai_floor.session_combo")
NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
FLAG = "AI_FLOOR_SESSION_COMBO"
BROKER_FLAG = "AI_FLOOR_BROKER"
RISK_CAP = Decimal("5")
FIX_PIPS = Decimal("15")
FIX_SL_PIPS = Decimal("30")
FIX_HOLD = timedelta(minutes=30)
PIP = {"USDJPY": Decimal("0.01"), "EURUSD": Decimal("0.0001"), "GBPUSD": Decimal("0.0001")}
LONDON_SYMBOL = "USDJPY"
FIX_SYMBOLS = ("GBPUSD", "EURUSD")


def combo_enabled(env):
    return env.get(FLAG) == "1" and str(env.get(BROKER_FLAG) or "").strip().lower() == "exness"


def _aware(at):
    return isinstance(at, datetime) and at.tzinfo is not None and at.utcoffset() is not None


class M5Bar:
    def __init__(self, open_time, open_, high, low, close):
        if not _aware(open_time):
            raise ValueError("bar open_time must be timezone-aware")
        self.open_time = open_time
        self.open = open_
        self.high = high
        self.low = low
        self.close = close


def pip_size(symbol):
    return PIP.get(symbol)


def range_window(day):
    """``[19:00 NY on the previous date, 02:00 NY on day)``."""
    start = datetime.combine(day - timedelta(days=1), time(19, 0), tzinfo=NY)
    end = datetime.combine(day, time(2, 0), tzinfo=NY)
    return start, end


def _ny(at):
    return at.astimezone(NY)


def _lon(at):
    return at.astimezone(LONDON)


def asian_range(bars, day):
    """``(high, low, bars, reason)``. Missing the 19:00 bar or the 01:55 bar is incomplete."""
    start, end = range_window(day)
    chosen = [bar for bar in bars if start <= _ny(bar.open_time) < end]
    if not chosen:
        return None, None, (), "range_incomplete"
    last_open = end - timedelta(minutes=5)
    if not any(_ny(bar.open_time) == start for bar in chosen):
        return None, None, tuple(chosen), "range_incomplete"
    if not any(_ny(bar.open_time) == last_open for bar in chosen):
        return None, None, tuple(chosen), "range_incomplete"
    high = max(bar.high for bar in chosen)
    low = min(bar.low for bar in chosen)
    if high <= low:
        return None, None, tuple(chosen), "range_flat"
    return high, low, tuple(chosen), None


def spans_both(bar, high, low):
    return bar.high >= high and bar.low <= low


def london_fill(bars, day, high, low):
    """First 02:00-05:00 bar that crosses exactly one side, else a skip reason."""
    start = datetime.combine(day, time(2, 0), tzinfo=NY)
    end = datetime.combine(day, time(5, 0), tzinfo=NY)
    for bar in bars:
        opened = _ny(bar.open_time)
        if not start <= opened < end:
            continue
        if spans_both(bar, high, low):
            return None, "both_sides_one_bar"
        if bar.high >= high:
            return "LONG", None
        if bar.low <= low:
            return "SHORT", None
    return None, None


def fix_signal(bars, symbol, at):
    """Move from the 15:45 London open to the 15:55 close. Entry is the 16:00 bar open, against the move."""
    pip = pip_size(symbol)
    if pip is None or not _aware(at):
        return None
    day = _lon(at).date()
    start_at = datetime.combine(day, time(15, 45), tzinfo=LONDON)
    last_at = datetime.combine(day, time(15, 55), tzinfo=LONDON)
    entry_at = datetime.combine(day, time(16, 0), tzinfo=LONDON)
    if _lon(at) < entry_at:
        return None
    opened = next((bar for bar in bars if _lon(bar.open_time) == start_at), None)
    closed = next((bar for bar in bars if _lon(bar.open_time) == last_at), None)
    entry_bar = next((bar for bar in bars if _lon(bar.open_time) == entry_at), None)
    if opened is None or closed is None or entry_bar is None:
        return None
    move = (closed.close - opened.open) / pip
    if abs(move) < FIX_PIPS:
        return None
    side = "SHORT" if move > 0 else "LONG"
    return side, entry_bar.open, move, entry_at


def size_for_cap(equity, balance, entry, stop, spec):
    """Budget is the lesser of 1% of equity, 1% of balance, and $5. Floor to ``volume_step``."""
    if spec is None or equity is None or balance is None or equity <= 0 or balance <= 0 or entry == stop:
        return Decimal(0), "sizing_invalid"
    budget = min(equity * RISK_FRACTION_CAP, balance * RISK_FRACTION_CAP, RISK_CAP)
    unit = abs(entry - stop) * spec.contract_size * spec.profit_rate
    if unit <= 0:
        return Decimal(0), "sizing_invalid"
    if spec.volume_min * unit > budget:
        return Decimal(0), "min_volume_over_cap"
    lots = floor_to_step(budget / unit, spec.volume_step)
    if lots < spec.volume_min:
        return Decimal(0), "min_volume_over_cap"
    return lots, None


def _log(event, **detail):
    parts = " ".join(f"{key}={value}" for key, value in detail.items() if value is not None)
    LOG.info("component=session_combo event=%s%s%s", event, " " if parts else "", parts)


class SessionCombo:
    def __init__(self, broker):
        self._broker = broker
        self._phase = {}
        self._logged_bars = False

    def tick(self, at, bars):
        if not _aware(at):
            return
        if not bars:
            if not self._logged_bars:
                self._logged_bars = True
                _log("SESSION_COMBO_NO_BARS", reason="m5_unavailable")
            return
        self._exits(at)
        if self._position_open():
            return
        if not self._fresh():
            return
        self._london(at, bars)
        if self._position_open():
            return
        self._fix(at, bars)

    def _fresh(self):
        try:
            return bool(self._broker.note_link())
        except Exception:  # noqa: BLE001
            return False

    def _position_open(self):
        try:
            return bool(self._broker.open_trades() or self._broker.pending_orders())
        except Exception:  # noqa: BLE001
            return True

    def _london(self, at, bars):
        local = _ny(at)
        if local.weekday() >= 5 or local.time() < time(2, 0):
            return
        day = local.date()
        state = self._phase.setdefault(day, {"london": "pending"})
        if state["london"] == "filled":
            return
        if state["london"] in ("skipped", "cancelled", "done"):
            return
        high, low, window, reason = asian_range(bars, day)
        if reason:
            _log("LONDON_OCO_SKIPPED", symbol=LONDON_SYMBOL, reason=reason)
            state["london"] = "skipped"
            return
        if not state.get("logged_range"):
            _log("ASIAN_RANGE", symbol=LONDON_SYMBOL, high=format(high, "f"), low=format(low, "f"))
            state["logged_range"] = True
        if any(spans_both(bar, high, low) for bar in window):
            _log("LONDON_OCO_SKIPPED", symbol=LONDON_SYMBOL, reason="both_sides_one_bar")
            state["london"] = "skipped"
            return
        side, skip = london_fill(bars, day, high, low)
        if skip:
            event = "LONDON_OCO_CANCELLED" if state["london"] == "armed" else "LONDON_OCO_SKIPPED"
            _log(event, symbol=LONDON_SYMBOL, reason=skip)
            state["london"] = "skipped"
            return
        if state["london"] == "pending":
            _log("LONDON_OCO_PLACED", symbol=LONDON_SYMBOL, high=format(high, "f"), low=format(low, "f"),
                 mode="tick_cross")
            state["london"] = "armed"
        if side is None and local.time() >= time(5, 0):
            _log("LONDON_OCO_CANCELLED", symbol=LONDON_SYMBOL, reason="unfilled_0500")
            state["london"] = "cancelled"
            return
        if side is None:
            return
        entry = high if side == "LONG" else low
        stop = low if side == "LONG" else high
        distance = entry - stop if side == "LONG" else stop - entry
        target = entry + distance * 2 if side == "LONG" else entry - distance * 2
        client = f"combo-lon-{day.isoformat()}-{side}"
        if not self._send(day, LONDON_SYMBOL, side, entry, stop, target, client):
            return
        state["london"] = "filled"
        state["client_id"] = client
        state["exit_at"] = datetime.combine(day, time(11, 0), tzinfo=NY)

    def _fix(self, at, bars):
        local = _lon(at)
        if local.weekday() >= 5 or local.time() < time(16, 0):
            return
        key = ("fix", local.date())
        state = self._phase.setdefault(key, {"fix": "pending"})
        if state["fix"] == "filled":
            return
        if state["fix"] != "pending":
            return
        for symbol in FIX_SYMBOLS:
            if self._position_open():
                return
            signal = fix_signal(bars, symbol, at)
            if signal is None:
                continue
            side, entry, move, entry_at = signal
            _log("FIX_SIGNAL", symbol=symbol, side=side, move_pips=format(move, "f"))
            pip = pip_size(symbol)
            stop = entry - FIX_SL_PIPS * pip if side == "LONG" else entry + FIX_SL_PIPS * pip
            client = f"combo-fix-{local.date().isoformat()}-{symbol}"
            if self._send(local.date(), symbol, side, entry, stop, None, client):
                _log("FIX_ENTRY", symbol=symbol, side=side)
                state["fix"] = "filled"
                state["client_id"] = client
                state["exit_at"] = entry_at + FIX_HOLD
                return
        if local.time() >= time(16, 30):
            state["fix"] = "done"

    def _send(self, day, symbol, side, entry, stop, target, client_id):
        try:
            book = self._broker._read()
        except Exception:  # noqa: BLE001
            _log("SIZE_SKIP", symbol=symbol, reason="book_unread")
            return False
        if book.balance is None or book.balance.currency != "USD":
            _log("SIZE_SKIP", symbol=symbol, reason="currency_unsupported")
            return False
        lots, reason = size_for_cap(book.balance.equity, book.balance.balance, entry, stop, book.specs.get(symbol))
        if reason:
            _log("SIZE_SKIP", symbol=symbol, reason=reason)
            return False
        result = self._broker.open_market(symbol=symbol, side=side, quantity=lots, entry=entry, stop=stop,
                                           target=target, client_id=client_id,
                                           source_run_id=f"session-combo-{day}", risk_fraction=RISK_FRACTION_CAP)
        if getattr(result, "status", None) != "ACCEPTED":
            _log("SIZE_SKIP", symbol=symbol, reason=getattr(result, "reason", None) or "not_accepted")
            return False
        return True

    def _exits(self, at):
        for state in self._phase.values():
            exit_at = state.get("exit_at")
            if exit_at is None or state.get("exited") or at < exit_at:
                continue
            client = state.get("client_id")
            try:
                ticket = self._broker.trade_id_for_client(client) if client else None
                trades = self._broker.open_trades()
            except Exception:  # noqa: BLE001
                continue
            if ticket is None and trades:
                ticket = trades[0].ticket
            if ticket is None:
                continue
            symbol = trades[0].symbol if trades else None
            reason = "london_1100" if str(client or "").startswith("combo-lon-") else "fix_30m"
            result = self._broker.close_market(ticket, reason=reason, client_id=f"{client}-exit")
            _log("TIME_EXIT", symbol=symbol, reason=reason, status=getattr(result, "status", None))
            if getattr(result, "status", None) == "ACCEPTED":
                state["exited"] = True
