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
from threading import Event, RLock, Thread
from urllib.parse import urlencode

from execution.exness_stream import backoff_seconds, subscribe_ticks, ticks_path
from execution.session_combo import M5Bar


LOG = logging.getLogger("ai_floor.exness_m5")
# The socket library logs the request URL. That URL contains the account id, so it stays quiet.
_WS_LOG = logging.getLogger("ai_floor.exness_ticks")
_WS_LOG.setLevel(logging.CRITICAL)
FLAG = "AI_FLOOR_EXNESS_M5_FEED"
BROKER_FLAG = "AI_FLOOR_BROKER"
FEED_SYMBOLS = ("USDJPY", "GBPUSD", "EURUSD")
CANDLES_PATH = "/v1/market-data/accounts/{account_id}/candles"
# Floor policy. The portal says reconnect with backoff and does not publish a heartbeat frame.
HEARTBEAT_SILENCE_SECONDS = 15
# Accepting the socket is not a success. The attempt counter clears only after a parsed tick has
# been on the wire this long, so accept-then-close cannot sign a new handshake every second.
STREAM_STABLE_SECONDS = 5
BAR = timedelta(minutes=5)
# The candle page rejects a from/to span longer than the account candle limit. 900 M5 bars is 75 hours.
MAX_CANDLE_BARS = 900
# The official TypeScript example offers this subprotocol and turns per-message deflate off.
WS_PROTOCOL = "exness-ws-protocol"


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


def candle_spans(start, end):
    """``(start, end)`` pairs, each short enough for one candle GET."""
    if start >= end:
        return ()
    step = BAR * MAX_CANDLE_BARS
    spans, cursor = [], start
    while cursor < end:
        nxt = min(end, cursor + step)
        spans.append((cursor, nxt))
        cursor = nxt
    return tuple(spans)


def candle_request_path(account_id, symbol, start, end):
    """Path plus query, encoded once. The signature uses this exact string."""
    return candles_path(account_id) + "?" + urlencode(candle_query(symbol, start, end))


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
    if isinstance(payload, list):
        payload = {"candles": payload}
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
        self._lock = RLock()

    def on_tick(self, symbol, bid, ask, at):
        with self._lock:
            if symbol not in self.aggs:
                return []
            self.quotes.note_tick(bid, ask, at, symbol=symbol)
            return self.aggs[symbol].on_tick(bid, at)

    def close_elapsed(self, at):
        with self._lock:
            emitted = []
            for agg in self.aggs.values():
                emitted.extend(agg.close_elapsed(at))
            return emitted

    def bars(self):
        with self._lock:
            out = []
            for agg in self.aggs.values():
                out.extend(agg.bars())
            return tuple(out)

    def mark_disconnected(self):
        with self._lock:
            self.quotes.mark_disconnected()

    def insert_bars(self, bars):
        with self._lock:
            added = []
            for bar in bars:
                agg = self.aggs.get(getattr(bar, "symbol", None))
                if agg is None:
                    continue
                added.extend(agg.insert([bar]))
            return tuple(added)

    def accept_candles(self, symbol, payload):
        with self._lock:
            agg = self.aggs.get(symbol)
            if agg is None or payload is None:
                return ()
            bars, reason = parse_candle_payload(payload, symbol)
            if reason:
                _log("M5_GAP", symbol=symbol, reason=reason)
                return ()
            return tuple(agg.insert(bars))


class TickStream:
    """Subscribe, parse, heartbeat and reconnect.

    ``on_open`` returns the documented subscribe message. ``LiveTickRunner`` opens the socket.
    """

    def __init__(self, feed, *, clock=None):
        self.feed = feed
        self._clock = clock
        self.attempt = 0
        self.connected = False
        self.last_frame_at = None
        self._opened_at = None
        self._saw_tick = False

    def subscribe_message(self):
        return subscribe_ticks(tuple(self.feed.aggs))

    def handshake_path(self, account_id):
        return ticks_path(account_id)

    def on_open(self, at):
        # Do not clear ``attempt`` here. The server can accept the socket and close it before a frame.
        # Resetting on that accept made every reconnect wait 1 s and sign a new handshake.
        self.connected = True
        self._opened_at = at
        self._saw_tick = False
        self.last_frame_at = at
        message = self.subscribe_message()
        _log("STREAM_SUBSCRIBE", symbols=",".join(message["subscribe"]["instruments"]))
        return message

    def on_message(self, message, at):
        self.last_frame_at = at
        parsed = parse_tick_frame(message)
        if parsed is None:
            return []
        self._saw_tick = True
        self._note_stable(at)
        symbol, bid, ask, tick_at = parsed
        return self.feed.on_tick(symbol, bid, ask, tick_at)

    def _note_stable(self, at):
        opened = self._opened_at
        if not self._saw_tick or opened is None:
            return
        if (at - opened).total_seconds() >= STREAM_STABLE_SECONDS:
            self.attempt = 0

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
        self._note_stable(at)
        if self.silent(at):
            return self.on_drop(at)
        return None

    def reconnect(self, at, payloads=None):
        message = self.on_open(at)
        for symbol, payload in (payloads or {}).items():
            self.feed.accept_candles(symbol, payload)
        return message


class _WsSocket:
    """The small surface ``LiveTickRunner`` needs from ``websockets.sync``."""

    def __init__(self, ws):
        self._ws = ws

    def send(self, data):
        self._ws.send(data)

    def recv(self, timeout):
        return self._ws.recv(timeout=timeout)

    def close(self):
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001 - the runner already treats the socket as gone
            return


def connect_ticks(url, headers):
    """Open one signed ticks socket. No order is sent. The URL is not logged."""
    from websockets.sync.client import connect
    ws = connect(url, additional_headers=dict(headers), subprotocols=[WS_PROTOCOL], compression=None,
                 proxy=None, open_timeout=10, close_timeout=2, ping_interval=None, ping_timeout=None,
                 legacy=True, max_size=1_000_000, user_agent_header=None, logger=_WS_LOG)
    return _WsSocket(ws)


class LiveTickRunner:
    """The process thread that keeps the quote socket open while the Exness path is in use.

    ``connect`` and ``handshake`` are injected. ``handshake`` returns ``(url, headers)`` for a freshly
    signed GET of the ticks path. A dropped socket backs off, signs a new handshake, and subscribes again.
    """

    def __init__(self, stream, connect, handshake, *, clock, sleep=None, recv_timeout=1.0):
        self.stream = stream
        self._connect = connect
        self._handshake = handshake
        self._clock = clock
        self._sleep = sleep
        self._recv_timeout = recv_timeout
        self._stop = Event()
        self._thread = None

    def start(self):
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = Thread(target=self._loop, name="exness-ticks", daemon=True)
        self._thread.start()

    def stop(self):
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2)

    def _pause(self, seconds):
        if self._sleep is not None:
            self._sleep(seconds)
            return
        self._stop.wait(timeout=seconds)

    def _loop(self):
        while not self._stop.is_set():
            self.serve_once()

    def serve_once(self):
        sock = None
        try:
            _url, headers = self._handshake()
            sock = self._connect(_url, headers)
            at = self._clock()
            message = self.stream.on_open(at)
            sock.send(json.dumps(message, separators=(",", ":")))
            while not self._stop.is_set():
                try:
                    raw = sock.recv(self._recv_timeout)
                except TimeoutError:
                    raw = None
                at = self._clock()
                if raw is None:
                    delay = self.stream.poll(at)
                    if delay is not None:
                        self._pause(delay)
                        break
                    continue
                if isinstance(raw, bytes):
                    try:
                        raw = raw.decode("utf-8")
                    except UnicodeError:
                        continue
                self.stream.on_message(raw, at)
        except Exception as exc:  # noqa: BLE001 - reconnect; the message can contain the host
            delay = self.stream.on_drop(self._clock())
            _log("STREAM_ERROR", error_type=type(exc).__name__)
            self._pause(delay)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:  # noqa: BLE001
                    pass
