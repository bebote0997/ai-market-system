"""Read-only 5-minute OHLC from the OANDA *practice* candles endpoint. No orders, no account id, no live host.

``GET https://api-fxpractice.oanda.com/v3/instruments/{EUR_USD|XAU_USD}/candles`` with the existing
``OANDA_PRACTICE_TOKEN``. Mid prices when OANDA sends them, otherwise bid, otherwise ask. At least three hours
are requested (48 completed M5 candles = four hours). Results are cached in the process for two minutes.
Any failure, including a missing token, returns None so the chart can fall back to stored closes. Never raises.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
import re
import threading
import time
from urllib.parse import parse_qsl, quote, urlencode, urlsplit
from urllib.request import Request, urlopen

HOST = "api-fxpractice.oanda.com"
COUNT = 48  # 48 x 5 min = 4 hours, which covers the 3 hours asked for
GRANULARITY = "M5"
TTL_SECONDS = 120.0
TIMEOUT = 3.0
INSTRUMENTS = {"EURUSD": "EUR_USD", "XAUUSD": "XAU_USD"}
_PATH = re.compile(r"^/v3/instruments/(EUR_USD|XAU_USD)/candles$")
_TO = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")
_CACHE = {}
_LOCK = threading.Lock()
_TRANSPORT = None


def clear_cache():
    with _LOCK:
        _CACHE.clear()


@contextmanager
def override(transport):
    """Tests: replace the HTTP call. No network."""
    global _TRANSPORT
    previous = _TRANSPORT
    _TRANSPORT = transport
    clear_cache()
    try:
        yield
    finally:
        _TRANSPORT = previous
        clear_cache()


def _allowed(url):
    try:
        parts = urlsplit(url)
        query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    if not (parts.scheme == "https" and parts.netloc == HOST and parts.hostname == HOST and parts.port is None
            and "@" not in parts.netloc and not parts.fragment and _PATH.match(parts.path)):
        return False
    keys = [key for key, _value in query]
    values = dict(query)
    to = values.get("to")
    return (len(keys) == len(set(keys)) and set(keys) <= {"price", "granularity", "count", "to"}
            and {"price", "granularity", "count"} <= set(keys)
            and values.get("price") in {"M", "BA", "MBA"} and values.get("granularity") == GRANULARITY
            and values.get("count") == str(COUNT) and (to is None or _TO.fullmatch(to) is not None))


def _http(url, token, timeout):
    if not _allowed(url):
        raise ValueError("refused")
    request = Request(url, headers={"Accept": "application/json", "Authorization": "Bearer " + token}, method="GET")
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - host and path are allow-listed above
        if response.status != 200:
            raise ValueError("http")
        return json.load(response)


def _stamp(value):
    text = str(value).replace("Z", "+00:00")
    if "." in text:
        head, frac = text.split(".", 1)
        digits, rest = [], ""
        for index, char in enumerate(frac):
            if char.isdigit():
                digits.append(char)
            else:
                rest = frac[index:]
                break
        text = head + "." + "".join(digits[:6]) + rest
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("naive")
    return parsed.astimezone(timezone.utc)


def _prices(candle):
    for key in ("mid", "bid", "ask"):
        block = candle.get(key)
        if isinstance(block, dict) and all(name in block for name in ("o", "h", "l", "c")):
            return block
    return None


def _bar(candle):
    if candle.get("complete") is not True:
        return None
    block = _prices(candle)
    if block is None or not candle.get("time"):
        return None
    try:
        open_, high, low, close = (float(block[name]) for name in ("o", "h", "l", "c"))
        stamp = _stamp(candle["time"])
    except (TypeError, ValueError):
        return None
    if not all(value > 0 for value in (open_, high, low, close)):
        return None
    if high < max(open_, close) or low > min(open_, close) or high < low:
        return None
    return {"timestamp": stamp.isoformat(), "open": open_, "high": high, "low": low, "close": close,
            "timeframe": "5m", "source": "oanda_practice"}


def _to_param(as_of):
    if not as_of:
        return None
    try:
        stamp = as_of.astimezone(timezone.utc) if isinstance(as_of, datetime) else _stamp(as_of)
    except (TypeError, ValueError):
        return None
    return stamp.strftime("%Y-%m-%dT%H:%M:%S.000000000Z")


def practice_candles(symbol, as_of=None, *, env=None):
    """Completed M5 OHLC bars up to ``as_of`` (latest if it is omitted), or None. Never raises."""
    instrument = INSTRUMENTS.get(symbol)
    env = os.environ if env is None else env
    token = env.get("OANDA_PRACTICE_TOKEN") if isinstance(env, dict) or hasattr(env, "get") else None
    if instrument is None or not isinstance(token, str) or not token.strip():
        return None
    end = _to_param(as_of)
    if as_of and end is None:
        return None
    key = (instrument, end or "latest")
    now = time.monotonic()
    with _LOCK:
        cached = _CACHE.get(key)
        if cached and now - cached[0] < TTL_SECONDS:
            return cached[1]
    query = {"price": "MBA", "granularity": GRANULARITY, "count": str(COUNT)}
    if end:
        query["to"] = end
    url = f"https://{HOST}/v3/instruments/{quote(instrument, safe='')}/candles?{urlencode(query)}"
    if not _allowed(url):
        return None
    transport = _TRANSPORT or _http
    try:
        payload = transport(url, token.strip(), TIMEOUT)
        candles = payload.get("candles") if isinstance(payload, dict) else None
        bars = [bar for candle in candles or () if (bar := _bar(candle))]
        bars.sort(key=lambda bar: bar["timestamp"])
        if as_of:
            limit = _stamp(as_of).isoformat()
            bars = [bar for bar in bars if bar["timestamp"] <= limit]
        if len(bars) < 2:
            return None
    except Exception:  # noqa: BLE001 - a chart without broker candles falls back to stored closes
        return None
    with _LOCK:
        _CACHE[key] = (now, bars)
    return bars
