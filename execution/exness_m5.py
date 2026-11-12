"""M5 bars for the Exness session combo, built from ticks. Off unless ``AI_FLOOR_EXNESS_M5_FEED`` is ``1``.

The ticks WebSocket path and the subscribe message are documented. The tick frame and the candle JSON
are not, so parsing lives here and accepts only the provisional fixtures in this file. A frame or payload
that does not match is ignored or marked as a gap. Nothing here sends an order.

Bars are bid OHLC on UTC 5-minute boundaries. A bar closes when the next boundary starts, or when the
clock reaches that boundary, so the 01:55 bar exists at the 02:00 decision.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging

from execution.exness_stream import backoff_seconds, subscribe_ticks, ticks_path
from execution.session_combo import M5Bar


LOG = logging.getLogger("ai_floor.exness_m5")
FLAG = "AI_FLOOR_EXNESS_M5_FEED"
BROKER_FLAG = "AI_FLOOR_BROKER"
FEED_SYMBOLS = ("USDJPY", "GBPUSD", "EURUSD")
CANDLES_PATH = "/v1/market-data/accounts/{account_id}/candles"
# Floor policy. The portal says reconnect with backoff and does not publish a heartbeat frame.
HEARTBEAT_SILENCE_SECONDS = 15
BAR = timedelta(minutes=5)


def m5_feed_enabled(env):
    return env.get(FLAG) == "1" and str(env.get(BROKER_FLAG) or "").strip().lower() == "exness"


def floor_m5_utc(at):
    utc = at.astimezone(timezone.utc).replace(second=0, microsecond=0)
    return utc.replace(minute=(utc.minute // 5) * 5)


def candles_path(account_id):
    if not isinstance(account_id, str) or not account_id.isdigit():
        raise ValueError("account_id must be an unsigned integer string")
    return CANDLES_PATH.format(account_id=account_id)


def candle_query(symbol, start, end):
    """Live query: ``instrument``, ``timeframe=M5``, ``price_type=bid``, ``from`` inclusive, ``to`` exclusive."""
    return {"instrument": symbol, "timeframe": "M5", "price_type": "bid",
            "from": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "to": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def _price(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        value = str(value)
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() and out > 0 else None


def _stamp(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            return None
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str):
        return None
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if at.tzinfo is None or at.utcoffset() is None:
        return None
    return at


def parse_tick_frame(message):
    """``(symbol, bid, ask, at)`` or None.

    Live ticks are ``{instrument, bid, ask, timestamp}`` with numeric prices. ``time`` and decimal
    strings are still accepted. A subscribe acknowledgement is ignored.
    """
    if isinstance(message, str):
        try:
            message = json.loads(message)
        except ValueError:
            return None
    if not isinstance(message, dict):
        return None
    symbol = message.get("instrument")
    stamp = message.get("timestamp") if message.get("timestamp") is not None else message.get("time")
    bid, ask, at = _price(message.get("bid")), _price(message.get("ask")), _stamp(stamp)
    if not isinstance(symbol, str) or bid is None or ask is None or at is None or ask < bid:
        return None
    return symbol, bid, ask, at


def parse_candle_payload(payload, symbol):
    """``(bars, reason)``.

    Live candles are ``{time, open, high, low, close, volume}``. ``time`` is an ISO timestamp with an
    offset. Prices may be JSON numbers. ``open_time`` is still accepted.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return (), "schema_unknown"
    rows = payload.get("candles") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return (), "schema_unknown"
    bars = []
    for row in rows:
        if not isinstance(row, dict):
            return (), "schema_unknown"
        opened = _stamp(row.get("time") if row.get("time") is not None else row.get("open_time"))
        prices = [_price(row.get(name)) for name in ("open", "high", "low", "close")]
        if opened is None or any(price is None for price in prices):
            return (), "schema_unknown"
        open_, high, low, close = prices
        if high < max(open_, close, low) or low > min(open_, close, high):
            return (), "schema_unknown"
        bars.append(M5Bar(floor_m5_utc(opened), open_, high, low, close, symbol))
    bars.sort(key=lambda bar: bar.open_time)
    return tuple(bars), None


def _log(event, **detail):
    parts = " ".join(f"{key}={value}" for key, value in detail.items() if value is not None)
    LOG.info("component=exness_m5 event=%s%s%s", event, " " if parts else "", parts)


class M5Aggregator:
    def __init__(self, symbol):
        self.symbol = symbol
        self.closed = []
        self.gaps = []
        self._bar = None

    def on_tick(self, bid, at):
        opened = floor_m5_utc(at)
        if self._bar is None:
            self._start(opened, bid)
            return []
        if opened == self._bar["open"]:
            self._widen(bid)
            return []
        if opened < self._bar["open"]:
            return []
        emitted = self._emit()
        cursor = emitted.open_time + BAR
        while cursor < opened:
            self._gap(cursor)
            cursor += BAR
        self._start(opened, bid)
        return [emitted]

    def close_elapsed(self, at):
        if self._bar is None or at < self._bar["open"] + BAR:
            return []
        emitted = self._emit()
        cursor = emitted.open_time + BAR
        limit = floor_m5_utc(at)
        while cursor < limit:
            self._gap(cursor)
            cursor += BAR
        self._bar = None
        return [emitted]

    def insert(self, bars):
        have = {bar.open_time for bar in self.closed}
        added = []
        for bar in bars:
            if bar.symbol != self.symbol or bar.open_time in have:
                continue
            if self._bar is not None and bar.open_time == self._bar["open"]:
                continue
            self.closed.append(bar)
            have.add(bar.open_time)
            added.append(bar)
        self.closed.sort(key=lambda bar: bar.open_time)
        self.gaps = [gap for gap in self.gaps if gap not in have]
        return added

    def bars(self):
        out = list(self.closed)
        if self._bar is not None:
            out.append(self._as_bar())
        return out

    def _start(self, opened, bid):
        self._bar = {"open": opened, "high": bid, "low": bid, "close": bid, "open_px": bid}

    def _widen(self, bid):
        self._bar["high"] = max(self._bar["high"], bid)
        self._bar["low"] = min(self._bar["low"], bid)
        self._bar["close"] = bid

    def _emit(self):
        bar = self._as_bar()
        self.closed.append(bar)
        self._bar = None
        _log("M5_BAR", symbol=self.symbol, open_time=bar.open_time.isoformat())
        return bar

    def _as_bar(self):
        row = self._bar
        return M5Bar(row["open"], row["open_px"], row["high"], row["low"], row["close"], self.symbol)

    def _gap(self, opened):
        if opened not in self.gaps:
            self.gaps.append(opened)
            _log("M5_GAP", symbol=self.symbol, open_time=opened.isoformat(), reason="no_ticks")


class M5Feed:
    """Per-symbol quotes on the broker's book, plus closed and forming M5 bars."""

    def __init__(self, quotes, symbols=FEED_SYMBOLS):
        self.quotes = quotes
        self.aggs = {symbol: M5Aggregator(symbol) for symbol in symbols}

    def on_tick(self, symbol, bid, ask, at):
        if symbol not in self.aggs:
            return []
        self.quotes.note_tick(bid, ask, at, symbol=symbol)
        return self.aggs[symbol].on_tick(bid, at)

    def close_elapsed(self, at):
        emitted = []
        for agg in self.aggs.values():
            emitted.extend(agg.close_elapsed(at))
        return emitted

    def bars(self):
        out = []
        for agg in self.aggs.values():
            out.extend(agg.bars())
        return tuple(out)

    def mark_disconnected(self):
        self.quotes.mark_disconnected()

    def accept_candles(self, symbol, payload):
        agg = self.aggs.get(symbol)
        if agg is None:
            return ()
        bars, reason = parse_candle_payload(payload, symbol)
        if reason:
            _log("M5_GAP", symbol=symbol, reason=reason)
            return ()
        return tuple(agg.insert(bars))


class TickStream:
    """Subscribe, parse, heartbeat and reconnect. The socket itself is injected.

    A live socket is not opened here: the portal's handshake is a signed GET, and this repo does not
    pin a WebSocket library. ``on_open`` returns the documented subscribe message.
    """

    def __init__(self, feed, *, clock=None):
        self.feed = feed
        self._clock = clock
        self.attempt = 0
        self.connected = False
        self.last_frame_at = None

    def subscribe_message(self):
        return subscribe_ticks(tuple(self.feed.aggs))

    def handshake_path(self, account_id):
        return ticks_path(account_id)

    def on_open(self, at):
        self.connected = True
        self.attempt = 0
        self.last_frame_at = at
        message = self.subscribe_message()
        _log("STREAM_SUBSCRIBE", symbols=",".join(message["subscribe"]["instruments"]))
        return message

    def on_message(self, message, at):
        self.last_frame_at = at
        parsed = parse_tick_frame(message)
        if parsed is None:
            return []
        symbol, bid, ask, tick_at = parsed
        return self.feed.on_tick(symbol, bid, ask, tick_at)

    def on_drop(self, at):
        del at
        self.connected = False
        self.feed.mark_disconnected()
        delay = backoff_seconds(self.attempt)
        self.attempt += 1
        _log("STREAM_DROP", backoff_seconds=delay)
        return delay

    def silent(self, at):
        if not self.connected or self.last_frame_at is None:
            return False
        return (at - self.last_frame_at).total_seconds() > HEARTBEAT_SILENCE_SECONDS

    def poll(self, at):
        if self.silent(at):
            return self.on_drop(at)
        return None

    def reconnect(self, at, payloads=None):
        message = self.on_open(at)
        for symbol, payload in (payloads or {}).items():
            self.feed.accept_candles(symbol, payload)
        return message
