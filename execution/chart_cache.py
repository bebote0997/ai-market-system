"""Read-only M5 cache for the Liquid Glass chart. It does not send orders.

Closed candles come from one ``GET /v1/market-data/accounts/{id}/candles``
(``timeframe=M5``, ``price_type=bid``) at most every ``GAP_SECONDS``. The call
goes through the same ``ExnessClient`` as the cycle, so the 2/s candle window
is shared. If that window is already full, this refresh does not send. The
candle that is still open is built from ticks already on the quote book.
When the quote feed is down, ``last_update`` stays where it was and ``stale``
is true. Missing numbers stay null.

``AI_FLOOR_CHART_CACHE=0`` turns the GETs off. Any other value, including an
unset variable, leaves the cache on. Nothing here changes order size or the
entry windows.
"""
from datetime import timezone
from decimal import Decimal
import json
import logging
from threading import RLock

from execution.exness_client import (TWO_PER_SECOND_WINDOW_MS, operation_limit, safe_detail)
from execution.exness_m5 import BAR, candle_request_path, floor_m5_utc, parse_candle_payload


LOG = logging.getLogger("ai_floor.chart_cache")
FLAG_ENV = "AI_FLOOR_CHART_CACHE"
FLAG = "chart_feed"
SYMBOLS = ("USDJPY", "EURUSD")
CLOSED_BARS = 150
GAP_SECONDS = 30  # inside the 30–60 s budget: one candle GET, then the other symbol
_CANDLE_KEYS = ("time", "open", "high", "low", "close")
_POSITION_KEYS = ("entry", "stop", "target", "side", "floating")


def chart_cache_enabled(env):
    """On unless the variable is exactly ``0``. Unset stays on and does not trade."""
    try:
        raw = env.get(FLAG_ENV)
    except Exception:  # noqa: BLE001 - a broken mapping does not turn the reads into orders
        return True
    return not (isinstance(raw, str) and raw.strip() == "0")


def _aware(at):
    return at is not None and getattr(at, "tzinfo", None) is not None and at.utcoffset() is not None


def _number_text(value, *, positive=False):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        value = str(value)
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        return None
    if not number.is_finite():
        return None
    if positive and number <= 0:
        return None
    return format(number, "f")


def _same_id(left, right):
    if isinstance(left, bool) or isinstance(right, bool) or left is None or right is None:
        return False
    text = str(left).strip()
    return bool(text) and text == str(right).strip()


def position_from_desk(symbol, desk):
    """Entry, side and floating P/L from the position. SL/TP only from its ``sltp`` order.

    A stop or target written on the position itself is the client entry and is ignored.
    """
    if not isinstance(desk, dict):
        return None
    orders = desk.get("orders") if isinstance(desk.get("orders"), list) else []
    for item in desk.get("positions") or []:
        if not isinstance(item, dict) or str(item.get("instrument") or "").strip() != symbol:
            continue
        stop = target = None
        for order in orders:
            if not isinstance(order, dict):
                continue
            if str(order.get("type") or "").strip().lower() != "sltp":
                continue
            if not _same_id(order.get("position_id"), item.get("position_id")):
                continue
            found_stop = _number_text(order.get("stop_loss_price"), positive=True)
            found_target = _number_text(order.get("take_profit_price"), positive=True)
            if found_stop is not None:
                stop = found_stop
            if found_target is not None:
                target = found_target
        side = item.get("side") if item.get("side") in ("LONG", "SHORT") else None
        return {
            "entry": _number_text(item.get("open_price"), positive=True),
            "stop": stop,
            "target": target,
            "side": side,
            "floating": _number_text(item.get("profit")),
        }
    return None


def _candle_room(client):
    """False when this process already holds the 2/s candle window. Never sleeps."""
    recent = getattr(client, "_recent", None)
    mono = getattr(client, "_mono_ms", None)
    if not isinstance(recent, dict) or not callable(mono):
        return True
    found = operation_limit("GET", "/v1/market-data/accounts/0/candles")
    if found is None:
        return True
    name, limit = found
    stamps = recent.get(name)
    if not stamps or len(stamps) < limit:
        return True
    window = TWO_PER_SECOND_WINDOW_MS if limit <= 2 else 0
    try:
        return mono() - stamps[0] >= window
    except TypeError:
        return False


def _empty(symbol):
    return {"symbol": symbol, "candles": [], "bid": None, "ask": None, "position": None,
            "last_update": None, "stale": True}


class ChartCache:
    def __init__(self, *, interval=GAP_SECONDS):
        if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval < 0:
            raise ValueError("interval")
        self.interval = float(interval)
        self._closed = {symbol: [] for symbol in SYMBOLS}
        self._forming = {}
        self._bid = {}
        self._ask = {}
        self._position = {symbol: None for symbol in SYMBOLS}
        self._last_update = {}
        self._stale = {symbol: True for symbol in SYMBOLS}
        self._last_get = None
        self._turn = 0
        self._lock = RLock()

    def view(self, symbol):
        if symbol not in SYMBOLS:
            return _empty(symbol)
        with self._lock:
            candles = [dict(row) for row in self._closed[symbol]]
            forming = self._forming.get(symbol)
            if forming is not None and all(row["time"] != forming["time"] for row in candles):
                candles.append(dict(forming))
            position = self._position.get(symbol)
            return {
                "symbol": symbol,
                "candles": candles,
                "bid": self._bid.get(symbol),
                "ask": self._ask.get(symbol),
                "position": None if position is None else {key: position[key] for key in _POSITION_KEYS},
                "last_update": self._last_update.get(symbol),
                "stale": bool(self._stale.get(symbol, True)),
            }

    def publish(self, store):
        body = {"symbols": {symbol: self.view(symbol) for symbol in SYMBOLS}}
        store.set_flag(FLAG, json.dumps(body, separators=(",", ":"), ensure_ascii=True))

    def refresh(self, at, *, client=None, quotes=None, desk=None):
        if not _aware(at):
            return
        with self._lock:
            self._observe(at, quotes)
            if isinstance(desk, dict):
                for symbol in SYMBOLS:
                    self._position[symbol] = position_from_desk(symbol, desk)
            self._pull(at, client)

    def _observe(self, at, quotes):
        boundary = floor_m5_utc(at)
        connected = quotes is not None and getattr(quotes, "connected", False) is True
        for symbol in SYMBOLS:
            self._finalize(symbol, boundary)
            slot = quotes.quote(symbol) if connected else None
            if slot is None or slot.tick_at is None or slot.bid is None or slot.ask is None or slot.ask < slot.bid:
                self._stale[symbol] = True
                continue
            bid, ask = _number_text(slot.bid, positive=True), _number_text(slot.ask, positive=True)
            if bid is None or ask is None:
                self._stale[symbol] = True
                continue
            self._bid[symbol] = bid
            self._ask[symbol] = ask
            self._stale[symbol] = False
            self._last_update[symbol] = at.astimezone(timezone.utc).isoformat()
            if _aware(slot.tick_at):
                self._on_tick(symbol, bid, slot.tick_at)
            self._finalize(symbol, boundary)

    def _on_tick(self, symbol, bid, tick_at):
        key = floor_m5_utc(tick_at).isoformat()
        current = self._forming.get(symbol)
        if current is None or current["time"] != key:
            self._forming[symbol] = {"time": key, "open": bid, "high": bid, "low": bid, "close": bid}
            return
        current["high"] = format(max(Decimal(current["high"]), Decimal(bid)), "f")
        current["low"] = format(min(Decimal(current["low"]), Decimal(bid)), "f")
        current["close"] = bid

    def _finalize(self, symbol, boundary):
        current = self._forming.get(symbol)
        if current is None or not current["time"] < boundary.isoformat():
            return
        self._push_closed(symbol, current)
        self._forming.pop(symbol, None)

    def _push_closed(self, symbol, row):
        closed = [item for item in self._closed[symbol] if item["time"] != row["time"]]
        closed.append({key: row[key] for key in _CANDLE_KEYS})
        closed.sort(key=lambda item: item["time"])
        self._closed[symbol] = closed[-CLOSED_BARS:]

    def _due(self, at):
        last = self._last_get
        if last is None:
            return True
        try:
            gap = (at - last).total_seconds()
        except TypeError:
            return False
        return gap >= self.interval

    def _pull(self, at, client):
        if client is None or not self._due(at) or not _candle_room(client):
            return
        # Closed M5 bars are still fetched when the quote socket is down (weekend, or
        # a deploy after the close). Only the in-progress bar needs live ticks.
        live = [symbol for symbol in SYMBOLS if not self._stale.get(symbol, True)]
        roster = live or list(SYMBOLS)
        symbol = roster[self._turn % len(roster)]
        account = getattr(client, "account_id", None)
        if not isinstance(account, str) or not account.isdigit():
            return
        boundary = floor_m5_utc(at)
        path = candle_request_path(account, symbol, boundary - BAR * CLOSED_BARS, boundary)
        self._last_get = at
        self._turn += 1
        try:
            payload = client.get_json(path)
        except Exception as exc:  # noqa: BLE001 - the host stays out of the log; candles stay as they were
            LOG.warning("component=chart_cache event=CHART_GAP symbol=%s detail=%s", symbol, safe_detail(exc))
            return
        bars, reason = parse_candle_payload(payload, symbol)
        if reason:
            LOG.info("component=chart_cache event=CHART_GAP symbol=%s reason=%s", symbol, reason)
            return
        self._merge(symbol, bars, boundary)
        if not self._last_update.get(symbol):
            self._last_update[symbol] = at.astimezone(timezone.utc).isoformat()
        LOG.info("component=chart_cache event=CHART_CACHE symbol=%s candles=%s stale=%s",
                 symbol, len(self._closed[symbol]), self._stale.get(symbol, True))

    def _merge(self, symbol, bars, boundary):
        limit = boundary.isoformat()
        have = {row["time"]: row for row in self._closed[symbol]}
        for bar in bars:
            if bar.open_time >= boundary:
                continue
            stamp = bar.open_time.isoformat()
            have[stamp] = {"time": stamp, "open": format(bar.open, "f"), "high": format(bar.high, "f"),
                           "low": format(bar.low, "f"), "close": format(bar.close, "f")}
        ordered = [have[key] for key in sorted(have) if key < limit]
        self._closed[symbol] = ordered[-CLOSED_BARS:]
