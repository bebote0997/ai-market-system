"""``GET /chart/<SYMBOL>.json`` for the Liquid Glass panel.

The browser talks only to this process. The body is the cache the cycle already
published (candles, bid/ask, position, ``last_update``, ``stale``). POST, PUT,
PATCH and DELETE answer 405. The same panel password guards the GET: the
``aif_remember`` cookie issued at login. The body has no key, account id or host.
"""
import json
import os
import re
import time

from execution.chart_cache import FLAG, SYMBOLS, _empty
from ui.auth import REMEMBER_COOKIE, password_strong_enough, remember_token_valid, remote_dashboard_enabled


_PATH = re.compile(r"^/chart/([A-Za-z]{6})\.json$")
_CANDLE_KEYS = ("time", "open", "high", "low", "close")
_POSITION_KEYS = ("entry", "stop", "target", "side", "floating")


def _is_cookie(key):
    if isinstance(key, (bytes, bytearray)):
        return key.lower() == b"cookie"
    return str(key).lower() == "cookie"


def _cookie(headers, name):
    for key, value in headers or ():
        if not _is_cookie(key):
            continue
        text = value.decode("latin-1", "ignore") if isinstance(value, (bytes, bytearray)) else str(value)
        for part in text.split(";"):
            if "=" not in part:
                continue
            found, token = part.split("=", 1)
            if found.strip() == name:
                return token.strip()
    return ""


def _header_items(headers):
    if headers is None:
        return ()
    if isinstance(headers, dict):
        return tuple(headers.items())
    return tuple(headers)


def authorized(headers, env, now=None):
    """True for the same cases the panel lets in, using the remember cookie when a password is set."""
    env = os.environ if env is None else env
    try:
        password = env.get("AI_FLOOR_DASHBOARD_PASSWORD")
    except Exception:  # noqa: BLE001
        return False
    if not password:
        return not remote_dashboard_enabled(env)
    if not password_strong_enough(password):
        return False
    token = _cookie(_header_items(headers), REMEMBER_COOKIE)
    return remember_token_valid(token, password, time.time() if now is None else now)


def _clean_candle(row):
    if not isinstance(row, dict):
        return None
    cleaned = {}
    for key in _CANDLE_KEYS:
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
        cleaned[key] = value.strip()
    return cleaned


def _clean_position(row):
    if not isinstance(row, dict):
        return None
    side = row.get("side") if row.get("side") in ("LONG", "SHORT") else None
    body = {"entry": _text(row.get("entry")), "stop": _text(row.get("stop")),
            "target": _text(row.get("target")), "side": side, "floating": _text(row.get("floating"))}
    return {key: body[key] for key in _POSITION_KEYS}


def _text(value):
    if value is None or isinstance(value, bool):
        return None
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or any(ch.isspace() for ch in text):
        return None
    return text


def _clean_view(symbol, row):
    if not isinstance(row, dict):
        return _empty(symbol)
    candles = []
    for item in row.get("candles") or []:
        cleaned = _clean_candle(item)
        if cleaned is not None:
            candles.append(cleaned)
    return {
        "symbol": symbol,
        "candles": candles,
        "bid": _text(row.get("bid")),
        "ask": _text(row.get("ask")),
        "position": _clean_position(row.get("position")),
        "last_update": _text(row.get("last_update")),
        "stale": row.get("stale") is True or row.get("last_update") in (None, ""),
    }


def published_view(env, symbol):
    """The cycle's flag, or an explicit stale empty view. Never calls the broker."""
    from ui.exness_present import _flags
    raw = _flags(env).get(FLAG)
    if not isinstance(raw, str) or not raw.startswith("{"):
        return _empty(symbol)
    try:
        payload = json.loads(raw)
    except ValueError:
        return _empty(symbol)
    symbols = payload.get("symbols") if isinstance(payload, dict) else None
    if not isinstance(symbols, dict):
        return _empty(symbol)
    return _clean_view(symbol, symbols.get(symbol))


def _body(payload):
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")


def answer(method, path, headers, env, *, now=None, cache=None):
    """``(status, body_bytes)`` for ``/chart/...``, or None when the path is not the chart."""
    path = str(path or "").split("?", 1)[0]
    if not path.startswith("/chart/"):
        return None
    if str(method or "").upper() != "GET":
        return 405, _body({"error": "method_not_allowed"})
    if not authorized(headers, env, now):
        return 401, _body({"error": "unauthorized"})
    found = _PATH.match(path)
    symbol = found.group(1).upper() if found is not None else ""
    if symbol not in SYMBOLS:
        return 404, _body({"error": "not_found"})
    view = cache.view(symbol) if cache is not None else published_view(env, symbol)
    return 200, _body(_clean_view(symbol, view))


class ChartFeedMiddleware:
    """Serves the chart path and leaves every other request to the dashboard."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if not path.startswith("/chart/"):
            await self.app(scope, receive, send)
            return
        status, body = answer(scope.get("method"), path, scope.get("headers") or (), os.environ)
        if status is None:
            await self.app(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"cache-control", b"no-store"),
            (b"allow", b"GET"),
            (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": body})
