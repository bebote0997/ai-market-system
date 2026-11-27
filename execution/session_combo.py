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

An HTTP 200/202 from Exness is ``UNKNOWN`` (the body is an ack, not a fill). That ack is not a skip: the
position is read again until it is visible, and only then is the leg ``filled``. Until that read confirms
it, the leg stays ``submitted`` and nothing is logged as ``SIZE_SKIP`` or unfilled. A position that is open
still gets an ``sltp`` order when its stop (and take-profit, when the leg has one) is missing, even if the
position id was not stored. The 11:00 ET exit closes the live ticket, including when ``combo_lon`` has no id.
If that path did not close, ``_close_london_by_book`` reads the broker book and closes the matching USDJPY.
"""
from datetime import datetime, time, timedelta
from decimal import Decimal
import json
import logging
import time as time_mod
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
           "position_id", "volume", "opened_at", "stop", "target", "sltp_sent",
           "protection_after", "protection_retry_done", "protection_close_done",
           "protection_miss_reads", "entry", "exit_price", "pnl", "exit_reason", "exit_unknown_seen",
           "book_alert")
_MATCH_SECONDS = 120
_TERMINAL = ("filled", "skipped", "cancelled", "done", "submitted")
_FILL_POLLS = 5
_SLTP_ATTEMPTS = 3
# Real pauses between fill re-reads. The first read is immediate; later ones back off.
_FILL_PAUSE_S = (0.2, 0.4, 0.8, 1.6)
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
            "volume": None, "opened_at": None, "stop": None, "target": None, "entry": None,
            "exit_price": None, "pnl": None, "exit_reason": None, "exit_unknown_seen": False, "sltp_sent": None,
            "protection_after": None, "protection_retry_done": False, "protection_close_done": False,
            "protection_miss_reads": 0}


class _SendResult:
    """In-process stand-in when the broker was not called."""

    def __init__(self, status, reason):
        self.status = status
        self.reason = reason
        self.broker_ref = None


def _as_decimal(value):
    if isinstance(value, Decimal):
        return value if value.is_finite() else None
    if value is None or value == "":
        return None
    try:
        out = Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() else None


def _parse_stamp(value):
    """Datetime con zona, o None si viene vacío, naive o ilegible."""
    if isinstance(value, datetime):
        return value if _aware(value) else None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if _aware(parsed) else None


def _row_open_time(row):
    """Hora de apertura de una posición. ``open_time`` vacío no cuenta como hora."""
    for name in ("opened_at", "open_time"):
        raw = row.get(name) if isinstance(row, dict) else getattr(row, name, None)
        if raw is None or raw is False or (isinstance(raw, str) and not raw.strip()):
            continue
        return _parse_stamp(raw)
    return None


def london_book_count(state, positions, linked, at, asked=()):
    """USDJPY que coinciden con la pata de Londres.

    Un ``open_time`` vacío o ausente no entra en la ventana: no se cuenta ni tumba el ciclo.
    Respaldo: sin hora, el ticket solo cuenta si una orden ``sltp`` lo liga.
    """
    if not _aware(at) or not isinstance(state, dict):
        return []
    local = _ny(at)
    side = state.get("side")
    if side not in ("LONG", "SHORT"):
        return []
    window_start = datetime.combine(local.date(), time(2, 0), tzinfo=NY)
    window_end = datetime.combine(local.date(), time(5, 0), tzinfo=NY)
    opened_flag = _parse_stamp(state.get("opened_at"))
    linked = {str(item) for item in linked or ()}
    asked = {str(item) for item in asked or ()}
    matches = []
    for row in positions or ():
        symbol = row.get("symbol") if isinstance(row, dict) else getattr(row, "symbol", None)
        row_side = row.get("side") if isinstance(row, dict) else getattr(row, "side", None)
        ticket = row.get("ticket") if isinstance(row, dict) else getattr(row, "ticket", None)
        if symbol != LONDON_SYMBOL or row_side != side or str(ticket) in asked:
            continue
        comment = row.get("comment") if isinstance(row, dict) else getattr(row, "comment", None)
        if _london_exit_due(comment, at):
            continue
        opened = _row_open_time(row)
        in_window = opened is not None and window_start <= opened.astimezone(NY) < window_end
        near_flag = False
        if opened is not None and opened_flag is not None:
            try:
                near_flag = abs((opened - opened_flag).total_seconds()) <= _MATCH_SECONDS
            except (TypeError, OverflowError, ValueError):
                near_flag = False
        by_link = str(ticket) in linked
        if (in_window and (near_flag or by_link)) or (opened is None and by_link):
            matches.append(row)
    return matches


def _london_exit_due(comment, at):
    """True when ``comment`` is ``combo-lon-YYYY-MM-DD-...`` and ``at`` is at or after 11:00 ET that day."""
    text = str(comment or "")
    marker = "combo-lon-"
    if not text.startswith(marker):
        return False
    try:
        day = datetime.strptime(text[len(marker):len(marker) + 10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return at >= datetime.combine(day, time(11, 0), tzinfo=NY)


def london_key(day):
    return f"combo_lon_{day.isoformat()}"


def fix_key(day):
    return f"combo_fix_{day.isoformat()}"


class SessionCombo:
    def __init__(self, broker, store=None, sleep=None):
        self._broker = broker
        self._store = store if store is not None else broker.flag_store
        self._phase = {}
        self._logged_bars = False
        self._sltp_attempts = {}
        self._sltp_sent = set()
        self._asked_usdjpy = set()
        self._sleep = time_mod.sleep if sleep is None else sleep

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
            state["exit_at"] = _parse_stamp(state.get("exit_at"))
            state["opened_at"] = _parse_stamp(state.get("opened_at"))
            state["protection_after"] = _parse_stamp(state.get("protection_after"))
            state["exited"] = bool(state["exited"])
            state["logged_range"] = bool(state["logged_range"])
            state["protection_retry_done"] = bool(state.get("protection_retry_done"))
            state["protection_close_done"] = bool(state.get("protection_close_done"))
            sent = state.get("sltp_sent")
            if sent:
                self._sltp_sent.add(sent)
        self._phase[key] = state
        return state

    def _read_flag(self, key):
        try:
            return self._store.get_flag(key)
        except Exception:  # noqa: BLE001
            return None

    def _put(self, key, state):
        payload = dict(state)
        for field in ("exit_at", "opened_at", "protection_after"):
            if isinstance(payload.get(field), datetime):
                payload[field] = payload[field].isoformat()
        self._store.set_flag(key, json.dumps(payload, sort_keys=True))

    def _persist_exit(self, key, state, ticket=None):
        """Store deal entry, exit_price and pnl when missing. A failed GET does not undo the close."""
        try:
            from execution.combo_history import remember_exit
            remember_exit(state, ticket, key=key, broker=self._broker)
        except Exception:  # noqa: BLE001
            _log("HISTORY_BACKFILL", status="failed", reason="read_failed")
        self._put(key, state)

    def _reconcile_submitted(self):
        for key, state in list(self._phase.items()):
            if state["status"] != "submitted":
                continue
            try:
                kind, row = self._locate(state)
            except Exception:  # noqa: BLE001
                continue
            if kind == "held":
                self._settle_fill(state, row)
                state["status"] = "filled"
                self._put(key, state)
                continue
            if kind == "gone":
                state["status"] = "done"
                state["exited"] = True
                self._persist_exit(key, state, state.get("position_id"))
                continue
            # Not confirmed. Stay submitted: do not SIZE_SKIP and do not mark the leg unfilled.

    def _fresh(self):
        try:
            return bool(self._broker.note_link())
        except Exception:  # noqa: BLE001
            return False

    def _position_open(self):
        """True when a position or a resting entry is open. A leftover ``sltp`` order is not an entry."""
        try:
            if self._broker.open_trades():
                return True
            return any(getattr(order, "kind", "") != "sltp" for order in self._broker.pending_orders())
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
                     exited=False, opened_at=opened_at, position_id=None, stop=format(stop, "f"),
                     entry=format(entry, "f"),
                     target=None if target is None else format(target, "f"))
        self._put(key, state)
        result, lots = self._send(day, symbol, side, entry, stop, target, client_id)
        if lots is not None:
            state["volume"] = format(lots, "f")
        status = getattr(result, "status", None)
        if status == "ACCEPTED":
            self._settle_fill(state, None, ticket=getattr(result, "broker_ref", None), lots=lots, opened_at=opened_at)
            state["status"] = "filled"
            self._put(key, state)
            return True
        if status == "UNKNOWN" and getattr(result, "reason", None) == "book_unread":
            # The read failed before any order. Stay armed so the next tick can send.
            state.update(status="armed", client_id=None, exit_at=None, symbol=None, side=None, position_id=None,
                         volume=None, opened_at=None, stop=None, target=None)
            self._put(key, state)
            return False
        if status == "UNKNOWN":
            kind, row = self._await_held(state)
            if kind == "held":
                self._settle_fill(state, row, lots=lots, opened_at=opened_at)
                state["status"] = "filled"
                self._put(key, state)
                return True
            # Ack without a visible position yet. Keep the submitted leg and do not call it unfilled.
            self._put(key, state)
            return False
        try:
            kind, row = self._locate(state)
        except Exception:  # noqa: BLE001
            kind, row = "unknown", None
        if kind == "held":
            self._settle_fill(state, row, lots=lots, opened_at=opened_at)
            state["status"] = "filled"
            self._put(key, state)
            return True
        state.update(status="armed", client_id=None, exit_at=None, symbol=None, side=None, position_id=None,
                     volume=None, opened_at=None, stop=None, target=None)
        self._put(key, state)
        return False

    def _send(self, day, symbol, side, entry, stop, target, client_id):
        try:
            book = self._broker._read()
        except Exception:  # noqa: BLE001 - the order was not sent; the leg stays retryable
            _log("ORDER_UNREAD", symbol=symbol, status="UNKNOWN", reason="book_unread")
            return _SendResult("UNKNOWN", "book_unread"), None
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
        if getattr(result, "status", None) == "REJECTED":
            _log("SIZE_SKIP", symbol=symbol, reason=getattr(result, "reason", None) or "not_accepted")
        return result, lots

    def _adopt(self, state, row, ticket=None, lots=None, opened_at=None):
        if row is not None:
            state["position_id"] = row.ticket
            state["symbol"] = row.symbol
            state["side"] = row.side
            state["volume"] = format(row.volume, "f")
            if getattr(row, "opened_at", None) is not None:
                state["opened_at"] = row.opened_at
            return row
        if ticket:
            state["position_id"] = ticket
        if lots is not None:
            state["volume"] = format(lots, "f")
        if opened_at is not None and state.get("opened_at") is None:
            state["opened_at"] = opened_at
        if not state.get("position_id"):
            return None
        try:
            trades = self._positions()
        except Exception:  # noqa: BLE001
            return None
        for item in trades:
            if item.ticket == state["position_id"]:
                return self._adopt(state, item)
        return None

    def _positions(self, fresh=False):
        """Open positions. ``fresh`` bypasses the manage-cycle cache after an ack that just cleared it."""
        if fresh:
            read = getattr(self._broker, "_read", None)
            if read is not None:
                return list(read(fresh=True).positions)
        return list(self._broker.open_trades())

    def _await_held(self, state):
        """Re-read until the position is visible. ``gone`` is only returned when a stored id is absent.

        Reads after the first one wait (0.2 s, then 0.4, 0.8, 1.6). A tight loop was reading the same
        snapshot before the ack became a position.
        """
        last = ("unknown", None)
        for attempt in range(_FILL_POLLS):
            if attempt:
                self._sleep(_FILL_PAUSE_S[attempt - 1])
            try:
                kind, row = self._locate(state, fresh=True)
            except Exception:  # noqa: BLE001
                return "unknown", None
            if kind in ("held", "gone"):
                return kind, row
            last = (kind, row)
        return last

    def _settle_fill(self, state, row, ticket=None, lots=None, opened_at=None):
        bound = row
        try:
            bound = self._adopt(state, row, ticket=ticket, lots=lots, opened_at=opened_at) or row
        except Exception:  # noqa: BLE001 - protection does not wait for the flag write
            if row is not None and not state.get("position_id"):
                state["position_id"] = getattr(row, "ticket", None)
            bound = row
        price = getattr(bound, "price", None)
        if isinstance(price, Decimal) and price.is_finite():
            state["entry"] = format(price, "f")
        self._ensure_protection(state, bound)

    def _ensure_protection(self, state, row):
        """Place the ``sltp`` order when the open position has no stop, or no target the leg required."""
        stop = _as_decimal(state.get("stop"))
        target = _as_decimal(state.get("target"))
        ticket = getattr(row, "ticket", None) if row is not None else state.get("position_id")
        if not ticket or stop is None:
            return
        seen_stop = getattr(row, "stop", None) if row is not None else None
        seen_target = getattr(row, "target", None) if row is not None else None
        if seen_stop is not None and (target is None or seen_target is not None):
            return
        if self._sltp_remembered(ticket) or self._sltp_attempts.get(ticket, 0) >= _SLTP_ATTEMPTS:
            return
        attempt = self._sltp_attempts.get(ticket, 0) + 1
        self._sltp_attempts[ticket] = attempt
        try:
            result = self._broker.replace_protection(ticket, stop, target, client_id=f"sltp-{ticket}-{attempt}")
        except Exception:  # noqa: BLE001
            return
        if getattr(result, "status", None) != "REJECTED":
            self._remember_sltp(state, ticket)

    def _sltp_remembered(self, ticket):
        """True when this process, or a previous one, already accepted an ``sltp`` for ``ticket``."""
        if not ticket or ticket in self._sltp_sent:
            return bool(ticket and ticket in self._sltp_sent)
        try:
            remembered = self._store.get_flag(f"sltp_sent_{ticket}") == "1"
        except Exception:  # noqa: BLE001
            return False
        if remembered:
            self._sltp_sent.add(ticket)
        return remembered

    def _remember_sltp(self, state, ticket):
        self._sltp_sent.add(ticket)
        state["sltp_sent"] = ticket
        if state.get("protection_after") is None and isinstance(state.get("opened_at"), datetime):
            state["protection_after"] = state["opened_at"]
        try:
            self._store.set_flag(f"sltp_sent_{ticket}", "1")
        except Exception:  # noqa: BLE001 - the leg flag still carries sltp_sent
            pass

    def _orders_snapshot_ok(self):
        """True only when this read's book carries a real ``orders`` list. A missing field is not 'no stop'."""
        try:
            book = self._broker._read(fresh=True)
        except Exception:  # noqa: BLE001
            return False
        if getattr(book, "orders_present", True) is False:
            return False
        return isinstance(getattr(book, "orders", None), (list, tuple))

    def _alert_once(self, key, state, kind, reason, symbol=None):
        """La misma alerta de libro no se repite en cada ciclo para una pata vieja."""
        token = f"{kind}:{reason}"
        if state.get("book_alert") == token:
            return
        self._high_alert(kind, reason, symbol)
        state["book_alert"] = token
        try:
            self._put(key, state)
        except Exception:  # noqa: BLE001
            pass

    def _retire_flat_london(self, key, state, rows):
        """El broker ya no tiene el USDJPY: marca la pata salida y guarda el P/L si el deal se cruza."""
        from execution.combo_history import settle_flat_leg
        if not settle_flat_leg(state, key, self._broker, rows=rows):
            return
        try:
            self._put(key, state)
        except Exception:  # noqa: BLE001
            pass

    def _high_alert(self, kind, reason, symbol=None):
        """Immediate operator alert. The broker sink delivers it when alerts are on."""
        _log(kind, severity="HIGH", reason=reason, symbol=symbol)
        alert = getattr(self._broker, "_alert", None)
        if not callable(alert):
            return
        detail = {"reason": reason, "severity": "HIGH"}
        if symbol:
            detail["symbol"] = symbol
        try:
            alert(kind, detail)
        except Exception:  # noqa: BLE001 - an alert never changes the close
            _log("ALERT_FAILED", reason=kind)

    def _protect_followup(self, key, state, row, at, asked):
        """One replace on a later manage if the stop is still missing.

        Closing for ``protection_missing`` needs two consecutive reads that both show no stop and a
        valid ``orders`` list. A snapshot with no ``orders`` field resets that streak and never closes.
        The check does not run on the manage that sent the first sltp (``at <= protection_after``).
        """
        if state.get("protection_close_done"):
            return
        if getattr(row, "stop", None) is not None:
            state["protection_miss_reads"] = 0
            return
        sent = state.get("protection_after")
        if not isinstance(sent, datetime) or not isinstance(at, datetime) or at <= sent:
            return
        if not self._orders_snapshot_ok():
            state["protection_miss_reads"] = 0
            _log("PROTECTION_ORDERS_ABSENT", symbol=state.get("symbol"))
            return
        misses = int(state.get("protection_miss_reads") or 0) + 1
        state["protection_miss_reads"] = misses
        ticket = getattr(row, "ticket", None)
        if not ticket:
            return
        if not state.get("protection_retry_done"):
            stop = _as_decimal(state.get("stop"))
            if stop is None:
                return
            _log("PROTECTION_MISSING", symbol=state.get("symbol"))
            try:
                result = self._broker.replace_protection(
                    ticket, stop, _as_decimal(state.get("target")), client_id=f"sltp-retry-{ticket}")
            except Exception:  # noqa: BLE001
                result = None
            state["protection_retry_done"] = True
            _log("PROTECTION_REPLACE", symbol=state.get("symbol"), status=getattr(result, "status", None),
                 reason=getattr(result, "reason", None))
            return
        if misses < 2:
            return
        done = self._finish_close(ticket, "protection_missing", f"protect-{ticket}", state.get("symbol"), asked)
        _log("PROTECTION_CLOSE", symbol=state.get("symbol"), status="closed" if done else "open",
             reason="protection_missing")
        if not done:
            self._high_alert("EXNESS_PROTECTION_CLOSE_FAILED", "protection_missing")
            return
        state["protection_close_done"] = True
        state["exited"] = True
        state["status"] = "done"
        state["exit_reason"] = "protection_missing"
        self._persist_exit(key, state, state.get("position_id"))

    def _locate(self, state, fresh=False):
        """``(held, row)``, ``(gone, None)``, ``(unknown, None)`` or ``(ambiguous, None)``.

        A known position id that a successful read does not show is gone. No id and no single
        symbol/side/volume/time match is not proof the position is gone.
        """
        trades = self._positions(fresh=fresh)
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
        asked = set()
        self._asked_usdjpy = set()
        for key, state in list(self._phase.items()):
            if state.get("exited") or state.get("status") not in ("filled", "submitted"):
                continue
            exit_at = state.get("exit_at")
            due = isinstance(exit_at, datetime) and at >= exit_at
            try:
                kind, row = self._locate(state)
            except Exception:  # noqa: BLE001
                kind, row = "unknown", None
            if kind == "held" and row is not None:
                try:
                    self._adopt(state, row)
                except Exception:  # noqa: BLE001
                    if not state.get("position_id"):
                        state["position_id"] = row.ticket
                self._ensure_protection(state, row)
                self._protect_followup(key, state, row, at, asked)
                if state.get("exited"):
                    continue
                try:
                    self._put(key, state)
                except Exception:  # noqa: BLE001
                    pass
            elif kind == "gone":
                state["exited"] = True
                state["status"] = "done"
                self._put(key, state)
                continue
            elif not due:
                continue
            if not due:
                continue
            ticket = getattr(row, "ticket", None) if row is not None else None
            if not ticket and kind != "gone":
                ticket = state.get("position_id")
            if not ticket:
                continue
            self._close_leg(key, state, ticket, row, asked, at)
        self._close_london_without_flag(at, asked)
        self._close_london_by_book(at, asked)

    def _exit_reason(self, key, state, row):
        client = state.get("client_id") or getattr(row, "comment", None) or ""
        if str(client).startswith("combo-lon-") or str(key).startswith("combo_lon_"):
            return "london_1100"
        return "fix_30m"

    def _close_leg(self, key, state, ticket, row, asked, at):
        reason = self._exit_reason(key, state, row)
        if self._finish_close(ticket, reason, state.get("client_id"), state.get("symbol"), asked):
            state["exited"] = True
            state["status"] = "done"
            state["exit_reason"] = reason
            if not state.get("position_id"):
                state["position_id"] = ticket
            self._persist_exit(key, state, ticket)
            return
        if self._exit_failure_should_alert(state, at):
            self._high_alert("EXNESS_EXIT_FAILED", getattr(self, "_close_status", None) or "close_failed",
                             state.get("symbol"))

    def _exit_failure_should_alert(self, state, at):
        """No alert on the first UNKNOWN/202. Alert on REJECTED, an exception, or a later tick still open."""
        kind = getattr(self, "_close_kind", None)
        if kind in ("rejected", "exception"):
            return True
        if kind != "unknown":
            return False
        seen = bool(state.get("exit_unknown_seen"))
        state["exit_unknown_seen"] = True
        exit_at = state.get("exit_at")
        if not seen or not isinstance(at, datetime) or not isinstance(exit_at, datetime):
            return False
        return at >= exit_at + timedelta(minutes=2)

    def _finish_close(self, ticket, reason, client, symbol, asked):
        """Close ``ticket`` once this tick. True when a later read no longer shows it.

        HTTP 202 is ``UNKNOWN`` on the transport. That ack is not left as the log status: a re-read that
        no longer shows the ticket is logged ``closed`` (the 18 Nov London exit was a real close logged
        ``UNKNOWN`` because the 202 returned before the position disappeared).
        """
        if not ticket or ticket in asked:
            self._close_status = "already_asked"
            self._close_kind = "already_asked"
            return False
        asked.add(ticket)
        if symbol == LONDON_SYMBOL:
            self._asked_usdjpy.add(ticket)
        try:
            result = self._broker.close_market(ticket, reason=reason, client_id=f"{client}-exit")
        except Exception:  # noqa: BLE001
            self._close_status = "close_failed"
            self._close_kind = "exception"
            _log("TIME_EXIT", symbol=symbol, reason=reason, status="UNKNOWN")
            return False
        status = getattr(result, "status", None)
        self._close_status = getattr(result, "reason", None) or status or "close_failed"
        if status == "REJECTED":
            self._close_kind = "rejected"
            _log("TIME_EXIT", symbol=symbol, reason=reason, status="REJECTED")
            return False
        self._close_kind = "unknown"
        try:
            gone = not any(item.ticket == ticket for item in self._positions(fresh=True))
        except Exception:  # noqa: BLE001
            _log("TIME_EXIT", symbol=symbol, reason=reason, status=status or "UNKNOWN")
            return False
        if gone:
            _log("TIME_EXIT", symbol=symbol, reason=reason, status="closed")
            return True
        _log("TIME_EXIT", symbol=symbol, reason=reason, status=status or "UNKNOWN")
        return False

    def _close_london_without_flag(self, at, asked):
        """11:00 ET closes the live ``combo-lon-`` ticket even when the day flag has no position id."""
        try:
            trades = self._positions()
        except Exception:  # noqa: BLE001
            return
        for row in trades:
            if not _london_exit_due(getattr(row, "comment", None), at):
                continue
            if not self._finish_close(row.ticket, "london_1100", row.comment, row.symbol, asked):
                continue
            self._mark_london_done(row.comment, row.ticket)

    def _close_london_by_book(self, at, asked):
        """11:00 ET fallback. Close nothing when the day is already done.

        Otherwise close the one USDJPY whose side matches the flag, whose open time is inside
        02:00-05:00 ET, and which is within two minutes of the flag or linked by an ``sltp`` order.
        Zero matches or more than one: a HIGH alert, and no close. If the stored ticket, or a
        USDJPY ticket already asked this tick, is still open, do not close another one. If that
        ticket is already gone, return with no alert and no close.
        """
        if not _aware(at):
            return
        local = _ny(at)
        if local.weekday() >= 5 or local.time() < time(11, 0):
            return
        key = london_key(local.date())
        state = self._leg(key)
        if state.get("exited") or state.get("status") == "done":
            return
        if state.get("status") not in ("filled", "submitted"):
            return
        if state.get("side") not in ("LONG", "SHORT"):
            self._alert_once(key, state, "EXNESS_LONDON_BOOK", "london_book_count")
            return
        try:
            book = self._broker._read()
        except Exception:  # noqa: BLE001
            self._high_alert("EXNESS_LONDON_BOOK", "book_unread")
            return
        # USDJPY tickets only. A Fix-leg ticket in ``asked`` does not block London.
        # Still open: do not close a different position. Already gone: no alert and no close;
        # the next tick marks the day done.
        usdjpy_open = {row.ticket for row in book.positions if row.symbol == LONDON_SYMBOL}
        watched = set(getattr(self, "_asked_usdjpy", ()))
        stored = state.get("position_id")
        if stored:
            watched.add(stored)
        if any(ticket not in usdjpy_open for ticket in watched):
            return
        if any(ticket in usdjpy_open for ticket in watched):
            return
        same_side = [row for row in book.positions
                     if row.symbol == LONDON_SYMBOL and row.side == state.get("side")]
        if state.get("status") == "filled" and not same_side:
            self._retire_flat_london(key, state, book.positions)
            return
        linked = set()
        for item in getattr(book, "sltp_ids", ()) or ():
            if isinstance(item, (tuple, list)) and len(item) >= 2:
                linked.add(str(item[1]))
        matches = london_book_count(state, book.positions, linked, at, asked)
        if len(matches) != 1:
            self._alert_once(key, state, "EXNESS_LONDON_BOOK", "london_book_count")
            return
        row = matches[0]
        client = state.get("client_id") or f"combo-lon-{local.date().isoformat()}"
        if not self._finish_close(row.ticket, "london_1100", client, row.symbol, asked):
            return
        state["exited"] = True
        state["status"] = "done"
        state["exit_reason"] = "london_1100"
        state["position_id"] = row.ticket
        self._persist_exit(key, state, row.ticket)

    def _mark_london_done(self, comment, ticket):
        try:
            day = datetime.strptime(str(comment)[len("combo-lon-"):len("combo-lon-") + 10], "%Y-%m-%d").date()
        except ValueError:
            return
        key = london_key(day)
        state = self._leg(key)
        if state.get("exited"):
            return
        state["exited"] = True
        state["status"] = "done"
        state["exit_reason"] = "london_1100"
        if not state.get("position_id"):
            state["position_id"] = ticket
        self._persist_exit(key, state, ticket)
