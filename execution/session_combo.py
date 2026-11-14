"""London Asian-range breakout (USDJPY) and London 16:00 fix fade (GBPUSD, then EURUSD).

Off unless ``AI_FLOOR_SESSION_COMBO`` is exactly ``1`` and ``AI_FLOOR_BROKER`` is exactly ``exness``.
The flag never sends an order by itself: ``ExnessBroker`` still requires the real-execution double key.

Stop entries are emulated. The Exness pending-order page names types ``limit``, ``stop`` and ``sltp`` but not the
JSON field, so this module does not send a broker stop. It arms both prices and sends a market order only when
the bar that is open at ``at`` crosses one side and the live quote is through that side. A bar that already
closed does not open a trade. A bar that crosses both sides skips the day.

London entries are allowed only while ``at`` is in ``[02:00, 05:00)`` America/New_York. The fix entry is allowed
only in the first five minutes of the 16:00 Europe/London bar. Each leg is stored in the broker flag store
(``combo_lon_YYYY-MM-DD`` / ``combo_fix_YYYY-MM-DD``) so a restart, a time exit, or a stop cannot send that
day's trade again.

The fill is the live ask (LONG) or bid (SHORT). Stop stays at the other side of the range (London) or 30 pips
from that fill (fix). Take-profit, when there is one, is twice the actual fill-to-stop distance. If the minimum
lot would risk more than the lesser of 1% and $5 at that distance, the trade is not sent.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
import json
import logging
from zoneinfo import ZoneInfo

from execution.exness_sizing import RISK_FRACTION_CAP, account_risk_cap, capped_lots


LOG = logging.getLogger("ai_floor.session_combo")
NY = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
FLAG = "AI_FLOOR_SESSION_COMBO"
BROKER_FLAG = "AI_FLOOR_BROKER"
FIX_PIPS = Decimal("15")
FIX_SL_PIPS = Decimal("30")
FIX_HOLD = timedelta(minutes=30)
FIX_TOLERANCE = timedelta(minutes=5)
PIP = {"USDJPY": Decimal("0.01"), "EURUSD": Decimal("0.0001"), "GBPUSD": Decimal("0.0001")}
LONDON_SYMBOL = "USDJPY"
FIX_SYMBOLS = ("GBPUSD", "EURUSD")
_FIELDS = ("status", "client_id", "exit_at", "exited", "symbol", "side", "logged_range",
           "position_id", "volume", "opened_at")
_MATCH_SECONDS = 120
_TERMINAL = ("filled", "skipped", "cancelled", "done", "submitted")
# The 7-hour window is 84 M5 bars. The first and the last alone are not a range.
MIN_RANGE_BARS = 3


def combo_enabled(env):
    return env.get(FLAG) == "1" and str(env.get(BROKER_FLAG) or "").strip().lower() == "exness"


def entry_window(at):
    """True while an entry can still be on time: London ``[02:00, 05:00)`` New York, or the fix's first 5 minutes."""
    if not _aware(at):
        return False
    ny = _ny(at)
    if ny.weekday() < 5 and time(2, 0) <= ny.time() < time(5, 0):
        return True
    lon = _lon(at)
    return lon.weekday() < 5 and time(16, 0) <= lon.time() < time(16, 5)


def _aware(at):
    return isinstance(at, datetime) and at.tzinfo is not None and at.utcoffset() is not None


class M5Bar:
    def __init__(self, open_time, open_, high, low, close, symbol):
        if not _aware(open_time):
            raise ValueError("bar open_time must be timezone-aware")
        if symbol not in PIP:
            raise ValueError("symbol")
        self.open_time = open_time
        self.open = open_
        self.high = high
        self.low = low
        self.close = close
        self.symbol = symbol


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


def _floor_m5(at, zone):
    local = at.astimezone(zone).replace(second=0, microsecond=0)
    return local.replace(minute=(local.minute // 5) * 5)


def asian_range(bars, day, symbol=LONDON_SYMBOL):
    """``(high, low, bars, reason)``. Missing the 19:00 bar, the 01:55 bar, or all but those two is incomplete.

    Incomplete is not final. The runner keeps asking until 05:00 NY, then skips.
    """
    start, end = range_window(day)
    chosen = [bar for bar in bars if bar.symbol == symbol and start <= _ny(bar.open_time) < end]
    if not chosen:
        return None, None, (), "range_incomplete"
    last_open = end - timedelta(minutes=5)
    if len(chosen) < MIN_RANGE_BARS:
        return None, None, tuple(chosen), "range_incomplete"
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


def london_fill(bars, day, high, low, symbol=LONDON_SYMBOL):
    """``(None, reason)`` when a 02:00-05:00 bar spans both sides. Never returns a historical entry."""
    start = datetime.combine(day, time(2, 0), tzinfo=NY)
    end = datetime.combine(day, time(5, 0), tzinfo=NY)
    for bar in bars:
        if bar.symbol != symbol:
            continue
        opened = _ny(bar.open_time)
        if start <= opened < end and spans_both(bar, high, low):
            return None, "both_sides_one_bar"
    return None, None


def live_cross(bar, high, low):
    """Side touched by the bar that is open now, or a skip when that bar spans both."""
    if bar is None:
        return None, None
    if spans_both(bar, high, low):
        return None, "both_sides_one_bar"
    if bar.high >= high:
        return "LONG", None
    if bar.low <= low:
        return "SHORT", None
    return None, None


def fix_move(bars, symbol, at):
    """``(side, move)`` inside the 16:00 entry window, else ``incomplete`` / ``below`` / ``closed``."""
    pip = pip_size(symbol)
    if pip is None or not _aware(at):
        return "closed"
    day = _lon(at).date()
    start_at = datetime.combine(day, time(15, 45), tzinfo=LONDON)
    last_at = datetime.combine(day, time(15, 55), tzinfo=LONDON)
    entry_at = datetime.combine(day, time(16, 0), tzinfo=LONDON)
    if not entry_at <= _lon(at) < entry_at + FIX_TOLERANCE:
        return "closed"
    own = [bar for bar in bars if bar.symbol == symbol]
    opened = next((bar for bar in own if _lon(bar.open_time) == start_at), None)
    closed = next((bar for bar in own if _lon(bar.open_time) == last_at), None)
    if opened is None or closed is None:
        return "incomplete"
    move = (closed.close - opened.open) / pip
    if abs(move) < FIX_PIPS:
        return "below"
    return ("SHORT" if move > 0 else "LONG"), move


def fix_signal(bars, symbol, at):
    """``(side, move, entry_at)`` or None. Entry is allowed only in ``[16:00, 16:05)`` London."""
    outcome = fix_move(bars, symbol, at)
    if not isinstance(outcome, tuple):
        return None
    side, move = outcome
    entry_at = datetime.combine(_lon(at).date(), time(16, 0), tzinfo=LONDON)
    return side, move, entry_at


def size_for_cap(equity, balance, entry, stop, spec, currency="USD", env=None):
    """Budget is the lesser of 1% of equity, 1% of balance, and the currency cap. ``entry`` is the fill."""
    cap = account_risk_cap(currency, env)
    if cap is None:
        return Decimal(0), "currency_unsupported"
    return capped_lots(equity, balance, entry, stop, spec, usd_cap=cap)


def _log(event, **detail):
    parts = " ".join(f"{key}={value}" for key, value in detail.items() if value is not None)
    LOG.info("component=session_combo event=%s%s%s", event, " " if parts else "", parts)


def _blank():
    return {"status": "pending", "client_id": None, "exit_at": None, "exited": False,
            "symbol": None, "side": None, "logged_range": False, "position_id": None,
            "volume": None, "opened_at": None}


def london_key(day):
    return f"combo_lon_{day.isoformat()}"


def fix_key(day):
    return f"combo_fix_{day.isoformat()}"


class SessionCombo:
    def __init__(self, broker, store=None):
        self._broker = broker
        self._store = store if store is not None else broker.flag_store
        self._phase = {}
        self._logged_bars = False

    def tick(self, at, bars):
        if not _aware(at):
            return
        self._load_recent(at)
        self._reconcile_submitted()
        self._exits(at)
        if not bars and entry_window(at):
            if not self._logged_bars:
                self._logged_bars = True
                _log("SESSION_COMBO_NO_BARS", reason="m5_unavailable")
            return
        if self._position_open() or not self._fresh():
            return
        self._london(at, bars)
        if self._position_open():
            return
        self._fix(at, bars)

    def _load_recent(self, at):
        ny_day = _ny(at).date()
        lon_day = _lon(at).date()
        for key in (london_key(ny_day), london_key(ny_day - timedelta(days=1)),
                    fix_key(lon_day), fix_key(lon_day - timedelta(days=1))):
            self._leg(key)

    def _leg(self, key):
        if key in self._phase:
            return self._phase[key]
        state = _blank()
        raw = self._read_flag(key)
        if raw:
            try:
                parsed = json.loads(raw)
            except (TypeError, ValueError):
                parsed = None
            if not isinstance(parsed, dict) or parsed.get("status") not in (
                    "pending", "armed", "submitted", "filled", "skipped", "cancelled", "done"):
                state["status"] = "done"
                _log("SIZE_SKIP", reason="state_unreadable")
                self._phase[key] = state
                self._put(key, state)
                return state
            for field in _FIELDS:
                if field in parsed:
                    state[field] = parsed[field]
            if isinstance(state["exit_at"], str):
                state["exit_at"] = datetime.fromisoformat(state["exit_at"])
            if isinstance(state["opened_at"], str):
                state["opened_at"] = datetime.fromisoformat(state["opened_at"])
            state["exited"] = bool(state["exited"])
            state["logged_range"] = bool(state["logged_range"])
        self._phase[key] = state
        return state

    def _read_flag(self, key):
        try:
            return self._store.get_flag(key)
        except Exception:  # noqa: BLE001
            return None

    def _put(self, key, state):
        payload = dict(state)
        for field in ("exit_at", "opened_at"):
            if isinstance(payload.get(field), datetime):
                payload[field] = payload[field].isoformat()
        self._store.set_flag(key, json.dumps(payload, sort_keys=True))

    def _reconcile_submitted(self):
        for key, state in list(self._phase.items()):
            if state["status"] != "submitted":
                continue
            try:
                kind, row = self._locate(state)
            except Exception:  # noqa: BLE001
                continue
            if kind == "held":
                self._adopt(state, row)
                state["status"] = "filled"
                self._put(key, state)
                continue
            if kind != "gone":
                state["status"] = "done"
                state["exited"] = True
                self._put(key, state)
                _log("SIZE_SKIP", symbol=state.get("symbol"), reason="restart_unconfirmed")
                continue
            state["status"] = "done"
            state["exited"] = True
            self._put(key, state)
            _log("SIZE_SKIP", symbol=state.get("symbol"), reason="restart_unconfirmed")

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
        if local.weekday() >= 5:
            return
        day = local.date()
        key = london_key(day)
        state = self._leg(key)
        if state["status"] in _TERMINAL:
            return
        high, low, window, reason = asian_range(bars, day)
        if reason == "range_incomplete" and local.time() < time(5, 0):
            return
        if reason:
            _log("LONDON_OCO_SKIPPED", symbol=LONDON_SYMBOL, reason=reason)
            state["status"] = "skipped"
            self._put(key, state)
            return
        if not state["logged_range"]:
            _log("ASIAN_RANGE", symbol=LONDON_SYMBOL, high=format(high, "f"), low=format(low, "f"))
            state["logged_range"] = True
            self._put(key, state)
        if any(spans_both(bar, high, low) for bar in window):
            _log("LONDON_OCO_SKIPPED", symbol=LONDON_SYMBOL, reason="both_sides_one_bar")
            state["status"] = "skipped"
            self._put(key, state)
            return
        if local.time() < time(2, 0):
            return
        _side, skip = london_fill(bars, day, high, low)
        if skip:
            event = "LONDON_OCO_CANCELLED" if state["status"] == "armed" else "LONDON_OCO_SKIPPED"
            _log(event, symbol=LONDON_SYMBOL, reason=skip)
            state["status"] = "skipped"
            self._put(key, state)
            return
        if local.time() >= time(5, 0):
            _log("LONDON_OCO_CANCELLED", symbol=LONDON_SYMBOL, reason="unfilled_0500")
            state["status"] = "cancelled"
            self._put(key, state)
            return
        if state["status"] == "pending":
            _log("LONDON_OCO_PLACED", symbol=LONDON_SYMBOL, high=format(high, "f"), low=format(low, "f"),
                 mode="tick_cross")
            state["status"] = "armed"
            self._put(key, state)
        live = next((bar for bar in bars if bar.symbol == LONDON_SYMBOL and _ny(bar.open_time) == _floor_m5(at, NY)),
                    None)
        side, live_skip = live_cross(live, high, low)
        if live_skip:
            event = "LONDON_OCO_CANCELLED" if state["status"] == "armed" else "LONDON_OCO_SKIPPED"
            _log(event, symbol=LONDON_SYMBOL, reason=live_skip)
            state["status"] = "skipped"
            self._put(key, state)
            return
        if side is None:
            return
        fill = self._fill_price(LONDON_SYMBOL, side)
        if fill is None:
            _log("SIZE_SKIP", symbol=LONDON_SYMBOL, reason="quote_missing")
            return
        if (side == "LONG" and fill < high) or (side == "SHORT" and fill > low):
            return
        stop = low if side == "LONG" else high
        distance = fill - stop if side == "LONG" else stop - fill
        if distance <= 0:
            _log("SIZE_SKIP", symbol=LONDON_SYMBOL, reason="entry_through_stop")
            return
        target = fill + distance * 2 if side == "LONG" else fill - distance * 2
        client = f"combo-lon-{day.isoformat()}-{side}"
        exit_at = datetime.combine(day, time(11, 0), tzinfo=NY)
        self._commit(key, state, day, LONDON_SYMBOL, side, fill, stop, target, client, exit_at, at)

    def _fix(self, at, bars):
        local = _lon(at)
        if local.weekday() >= 5 or local.time() < time(16, 0):
            return
        day = local.date()
        key = fix_key(day)
        state = self._leg(key)
        if state["status"] in _TERMINAL:
            return
        if _lon(at) >= datetime.combine(day, time(16, 0), tzinfo=LONDON) + FIX_TOLERANCE:
            _log("FIX_SKIPPED", reason="window_closed")
            state["status"] = "done"
            self._put(key, state)
            return
        waiting = False
        signaled = False
        for symbol in FIX_SYMBOLS:
            if self._position_open():
                return
            outcome = fix_move(bars, symbol, at)
            if outcome == "incomplete":
                waiting = True
                break
            if outcome == "below":
                continue
            side, move = outcome
            signaled = True
            _log("FIX_SIGNAL", symbol=symbol, side=side, move_pips=format(move, "f"))
            fill = self._fill_price(symbol, side)
            if fill is None:
                _log("SIZE_SKIP", symbol=symbol, reason="quote_missing")
                return
            pip = pip_size(symbol)
            stop = fill - FIX_SL_PIPS * pip if side == "LONG" else fill + FIX_SL_PIPS * pip
            client = f"combo-fix-{day.isoformat()}-{symbol}"
            if self._commit(key, state, day, symbol, side, fill, stop, None, client, at + FIX_HOLD, at):
                _log("FIX_ENTRY", symbol=symbol, side=side)
                return
        if waiting or signaled:
            return
        _log("FIX_SKIPPED", reason="below_threshold")
        state["status"] = "done"
        self._put(key, state)

    def _fill_price(self, symbol, side):
        try:
            book = self._broker._read()
        except Exception:  # noqa: BLE001
            return None
        slot = book.quotes.quote(symbol)
        if slot is None or slot.bid is None or slot.ask is None or slot.tick_at is None:
            return None
        return slot.ask if side == "LONG" else slot.bid

    def _commit(self, key, state, day, symbol, side, entry, stop, target, client_id, exit_at, opened_at):
        if state["status"] in _TERMINAL:
            return False
        state.update(status="submitted", client_id=client_id, exit_at=exit_at, symbol=symbol, side=side,
                     exited=False, opened_at=opened_at, position_id=None)
        self._put(key, state)
        result, lots = self._send(day, symbol, side, entry, stop, target, client_id)
        if lots is not None:
            state["volume"] = format(lots, "f")
        if getattr(result, "status", None) == "ACCEPTED":
            self._adopt(state, None, ticket=getattr(result, "broker_ref", None), lots=lots, opened_at=opened_at)
            state["status"] = "filled"
            self._put(key, state)
            return True
        try:
            kind, row = self._locate(state)
        except Exception:  # noqa: BLE001
            kind, row = "unknown", None
        if kind == "held":
            self._adopt(state, row)
            state["status"] = "filled"
            self._put(key, state)
            return True
        state.update(status="armed", client_id=None, exit_at=None, symbol=None, side=None, position_id=None,
                     volume=None, opened_at=None)
        self._put(key, state)
        return False

    def _send(self, day, symbol, side, entry, stop, target, client_id):
        try:
            book = self._broker._read()
        except Exception:  # noqa: BLE001
            _log("SIZE_SKIP", symbol=symbol, reason="book_unread")
            return None, None
        broker_env = getattr(self._broker, "_env", None)
        if book.balance is None or account_risk_cap(book.balance.currency, broker_env) is None:
            _log("SIZE_SKIP", symbol=symbol, reason="currency_unsupported")
            return None, None
        lots, reason = size_for_cap(book.balance.equity, book.balance.balance, entry, stop, book.specs.get(symbol),
                                    book.balance.currency, env=broker_env)
        if reason:
            _log("SIZE_SKIP", symbol=symbol, reason=reason)
            return None, None
        result = self._broker.open_market(symbol=symbol, side=side, quantity=lots, entry=entry, stop=stop,
                                           target=target, client_id=client_id,
                                           source_run_id=f"session-combo-{day}", risk_fraction=RISK_FRACTION_CAP)
        if getattr(result, "status", None) != "ACCEPTED":
            _log("SIZE_SKIP", symbol=symbol, reason=getattr(result, "reason", None) or "not_accepted")
            return result, lots
        return result, lots

    def _adopt(self, state, row, ticket=None, lots=None, opened_at=None):
        if row is not None:
            state["position_id"] = row.ticket
            state["symbol"] = row.symbol
            state["side"] = row.side
            state["volume"] = format(row.volume, "f")
            if getattr(row, "opened_at", None) is not None:
                state["opened_at"] = row.opened_at
            return
        if ticket:
            state["position_id"] = ticket
        if lots is not None:
            state["volume"] = format(lots, "f")
        if opened_at is not None and state.get("opened_at") is None:
            state["opened_at"] = opened_at
        if not state.get("position_id"):
            return
        try:
            trades = self._broker.open_trades()
        except Exception:  # noqa: BLE001
            return
        for item in trades:
            if item.ticket == state["position_id"]:
                self._adopt(state, item)
                return

    def _locate(self, state):
        """``(held, row)``, ``(gone, None)``, ``(unknown, None)`` or ``(ambiguous, None)``.

        A known position id that a successful read does not show is gone. No id and no single
        symbol/side/volume/time match is not proof the position is gone.
        """
        trades = self._broker.open_trades()
        ticket = state.get("position_id")
        if ticket:
            found = next((row for row in trades if row.ticket == ticket), None)
            return ("held", found) if found is not None else ("gone", None)
        client = state.get("client_id")
        if client:
            by_comment = next((row for row in trades if row.comment == client), None)
            if by_comment is not None:
                return "held", by_comment
        symbol, side = state.get("symbol"), state.get("side")
        if not symbol or side not in ("LONG", "SHORT"):
            return "unknown", None
        matches = [row for row in trades if row.symbol == symbol and row.side == side]
        volume = state.get("volume")
        if volume:
            wanted = Decimal(str(volume))
            matches = [row for row in matches if row.volume == wanted]
        opened = state.get("opened_at")
        if isinstance(opened, datetime):
            timed = []
            for row in matches:
                stamp = getattr(row, "opened_at", None)
                if stamp is None or abs((stamp - opened).total_seconds()) <= _MATCH_SECONDS:
                    timed.append(row)
            matches = timed
        if len(matches) == 1:
            return "held", matches[0]
        if len(matches) > 1:
            return "ambiguous", None
        return "unknown", None

    def _exits(self, at):
        for key, state in list(self._phase.items()):
            if state.get("exited") or state.get("status") != "filled":
                continue
            try:
                kind, row = self._locate(state)
            except Exception:  # noqa: BLE001
                continue
            if kind == "held":
                self._adopt(state, row)
                self._put(key, state)
            elif kind == "gone":
                state["exited"] = True
                state["status"] = "done"
                self._put(key, state)
                continue
            else:
                continue
            exit_at = state.get("exit_at")
            if not isinstance(exit_at, datetime) or at < exit_at:
                continue
            ticket = state.get("position_id")
            if not ticket:
                continue
            client = state.get("client_id")
            reason = "london_1100" if str(client or "").startswith("combo-lon-") else "fix_30m"
            result = self._broker.close_market(ticket, reason=reason, client_id=f"{client}-exit")
            _log("TIME_EXIT", symbol=state.get("symbol"), reason=reason, status=getattr(result, "status", None))
            if getattr(result, "status", None) not in ("ACCEPTED", "REJECTED"):
                continue
            try:
                still = any(item.ticket == ticket for item in self._broker.open_trades())
            except Exception:  # noqa: BLE001
                continue
            if still:
                continue
            state["exited"] = True
            state["status"] = "done"
            self._put(key, state)
