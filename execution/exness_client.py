"""Signed REST calls. The HTTP function is injected so tests never open a socket.

Discovery is cached. Mutating calls reuse one idempotency key and are not sent twice.
HTTP 429 is not retried (the troubleshooting page says REQUEST_RATE_LIMIT is not retried automatically).
The limits JSON shape is not published on the pages we could read, so the Floor also keeps a minimum gap
between calls (``AI_FLOOR_EXNESS_MIN_INTERVAL_MS``, default 100). A call inside that gap waits.
It is not rejected, and the wait is not a retry of HTTP 429. TODO: parse
``GET /v1/configuration/accounts/{account_id}/limits`` once its field names are confirmed.
"""
from collections import deque
from datetime import datetime, timezone
import json
import re
from threading import Lock
import time
from urllib.parse import urlsplit

from execution.exness_sign import headers


MIN_INTERVAL_ENV = "AI_FLOOR_EXNESS_MIN_INTERVAL_MS"
DEFAULT_MIN_INTERVAL_MS = 100
# Per-operation REST limits read from GET /v1/configuration/accounts/{account_id}/limits on 2026-11-15
# (every window is 1 s). The 100 ms gap alone allows 10 calls a second, so three candle GETs in one manage
# hit the 2/s candle limit: the third one (EURUSD) got HTTP 429 REQUEST_RATE_LIMIT and the 1 s block that
# follows failed the account read. A call waits until its operation has room; it is never retried.
OPERATION_WINDOW_MS = 1050  # 1 s window plus a margin for clock jitter between the Floor and the server
# The 2/s operations (candles, snapshot, instruments, limits) get 150 ms of margin for a server clock offset.
TWO_PER_SECOND_WINDOW_MS = 1150
MAX_WAIT_STEP_MS = TWO_PER_SECOND_WINDOW_MS  # one sleep never exceeds one window; the slot is re-checked after it
_OPERATION_LIMITS = (
    (re.compile(r"^/v1/market-data/accounts/[0-9]+/candles(?:\?|$)"), "candles", 2),
    (re.compile(r"^/v1/trading/accounts/[0-9]+/snapshot(?:\?|$)"), "snapshot", 2),
    (re.compile(r"^/v1/configuration/accounts/[0-9]+/instruments(?:\?|$)"), "instruments", 2),
    (re.compile(r"^/v1/configuration/accounts/[0-9]+/instruments/[^/?]+/conditions(?:\?|$)"), "conditions", 4),
    (re.compile(r"^/v1/configuration/accounts/[0-9]+/account(?:\?|$)"), "account", 4),
    (re.compile(r"^/v1/configuration/accounts/[0-9]+/limits(?:\?|$)"), "limits", 2),
)


def operation_limit(method, path):
    """``(name, calls_per_window)`` for a rate-limited GET, else None."""
    if str(method).upper() != "GET" or not isinstance(path, str):
        return None
    for pattern, name, limit in _OPERATION_LIMITS:
        if pattern.match(path):
            return name, limit
    return None


def operation_name(path):
    """Short endpoint name for logs (``candles``, ``snapshot``...). Never the path, host or account."""
    found = operation_limit("GET", path)
    if found is not None:
        return found[0]
    if isinstance(path, str) and path.startswith("/v1/trading/access-point"):
        return "access_point"
    return "other"


_CODE = re.compile(r"^[A-Za-z0-9_]{1,40}$")


def _error_code(payload):
    """``3015:REQUEST_RATE_LIMIT`` from an Exness error body; ``none`` for anything unexpected."""
    payload = payload if isinstance(payload, dict) else {}
    code, message = payload.get("code"), payload.get("error_message")
    code = str(code) if isinstance(code, int) and not isinstance(code, bool) else "none"
    message = message if isinstance(message, str) and _CODE.match(message) else "none"
    return f"{code}:{message}"


_SAFE_DETAIL = re.compile(r"^[A-Za-z_]{1,40}(?::[A-Za-z0-9_]{1,40}){0,7}$")


def safe_detail(exc):
    """The message of a Floor-built read error (``read_failed:429:3015:REQUEST_RATE_LIMIT:candles``), else ``none``.

    Only colon-separated words and numbers pass, so a host, a URL, a key or free text is never logged.
    """
    try:
        text = str(exc)
    except Exception:  # noqa: BLE001
        return "none"
    return text if len(text) <= 200 and _SAFE_DETAIL.match(text) else "none"


def _interval_ms(env):
    raw = str(env.get(MIN_INTERVAL_ENV) or "").strip()
    if not raw:
        return DEFAULT_MIN_INTERVAL_MS
    if not raw.isdigit() or not 1 <= int(raw) <= 60_000:
        return None
    return int(raw)


def normalize_gateway(raw):
    """Bare hostnames from Key Management become https URLs. http and userinfo stay invalid."""
    text = raw.strip().rstrip("/") if isinstance(raw, str) else ""
    if text and "://" not in text:
        text = "https://" + text
    return text


def _host_ok(url):
    parts = urlsplit(url if isinstance(url, str) else "")
    return parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password and not parts.query and not parts.fragment


def _local_reason(payload):
    """Why the Floor did not send: ``local_block`` after a server 429, ``local_stall`` when the clock stood still."""
    message = payload.get("error_message") if isinstance(payload, dict) else None
    return "local_block" if message == "REQUEST_RATE_LIMIT" else "local_stall"


class ExnessClient:
    def __init__(self, env, *, caller, sign, clock=None, now_ms=None, sleep=None, monotonic_ms=None):
        self._env = env
        self._caller = caller
        self._sign = sign
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        # Wall-clock milliseconds go into the signature (EXN-TIMESTAMP) only.
        self._now_ms = now_ms or (lambda: int(self._clock().timestamp() * 1000))
        # The rate limiter measures gaps on a monotonic clock, so a wall-clock step cannot open or close a window.
        # A test that injects ``now_ms`` and no monotonic clock drives both from the same fake time.
        if monotonic_ms is not None:
            self._mono_ms = monotonic_ms
        elif now_ms is not None:
            self._mono_ms = now_ms
        else:
            self._mono_ms = lambda: int(time.monotonic() * 1000)
        self._sleep = sleep or time.sleep
        self._interval = _interval_ms(env)
        self.access_point = None
        self._last_ms = None
        self._blocked_until_ms = None
        self._sent = {}
        self._recent = {}
        self._lock = Lock()
        key = env.get("EXN_API_KEY")
        account = env.get("EXN_ACCOUNT_ID")
        gateway = normalize_gateway(env.get("EXN_DISCOVERY_BASE_URL"))
        if not isinstance(key, str) or not key.strip() or not isinstance(account, str) or not account.strip().isdigit():
            raise ValueError("EXN_API_KEY and numeric EXN_ACCOUNT_ID are required")
        if not _host_ok(gateway):
            raise ValueError("EXN_DISCOVERY_BASE_URL must be an https URL without userinfo")
        if self._interval is None:
            raise ValueError("AI_FLOOR_EXNESS_MIN_INTERVAL_MS is invalid")
        self.api_key = key.strip()
        self.account_id = account.strip()
        self.gateway = gateway

    def _request(self, base, method, path, body=None, idempotency_key=""):
        state = self._acquire_slot(operation_limit(method, path))
        if state == "blocked":
            return 429, {"error_message": "REQUEST_RATE_LIMIT"}, {}, False
        if state != "ready":
            return 429, {"error_message": "floor_min_interval"}, {}, False
        raw = b"" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        signed = headers(self.api_key, idempotency_key, self._now_ms(), method, path, raw, self._sign)
        if body is not None:
            signed["Content-Type"] = "application/json"
        operation = operation_limit(method, path)
        try:
            status, payload, response_headers = self._caller(method, base + path, signed, raw)
        finally:
            self._mark_done(operation)
        if status == 429:
            retry = response_headers.get("Retry-After") if isinstance(response_headers, dict) else None
            wait = int(retry) if isinstance(retry, str) and retry.isdigit() else 1
            with self._lock:
                self._blocked_until_ms = self._mono_ms() + wait * 1000
        return status, payload if isinstance(payload, dict) else {}, response_headers or {}, True

    def signed_headers(self, method, path):
        """Headers for a signed GET that is not sent through the HTTP caller.

        The ticks WebSocket handshake is one of these. The path is the exact path the socket opens.
        """
        if method.upper() != "GET":
            raise ValueError("signed_headers is only for GET")
        if self._acquire_slot() != "ready":
            raise RuntimeError("rate_limited")
        return headers(self.api_key, "", self._now_ms(), "GET", path, b"", self._sign)

    def _mark_done(self, operation):
        """Move this call's window stamp from the send time to the response time.

        The server counts a call when the request reaches it, which can be hundreds of milliseconds after the
        send (each call opens a new TLS connection). The response time is never earlier than that arrival, so
        a window measured from it cannot let two calls land inside one server second. Live evidence after
        deploying the send-time window: EURUSD candles still got HTTP 429 at 23:42:45Z, 23:45:58Z, 23:46:30Z.
        """
        if operation is None:
            return
        name, _limit = operation
        with self._lock:
            recent = self._recent.get(name)
            if recent:
                recent[-1] = max(recent[-1], self._mono_ms())

    def _slot_wait_locked(self, operation):
        """``("blocked"|"ready"|"wait", wait_ms)``. Called with the lock held; never sleeps."""
        now = self._mono_ms()
        if self._blocked_until_ms is not None and now < self._blocked_until_ms:
            return "blocked", 0
        elapsed = None if self._last_ms is None else now - self._last_ms
        wait = 0 if elapsed is None or elapsed >= self._interval else self._interval - elapsed
        recent = None
        if operation is not None:
            name, limit = operation
            recent = self._recent.setdefault(name, deque(maxlen=limit))
            if len(recent) >= limit:
                window = TWO_PER_SECOND_WINDOW_MS if limit <= 2 else OPERATION_WINDOW_MS
                wait = max(wait, window - (now - recent[0]))
        if wait <= 0:
            self._last_ms = now
            if recent is not None:
                recent.append(now)
            return "ready", 0
        return "wait", min(wait, MAX_WAIT_STEP_MS)

    def _acquire_slot(self, operation=None):
        """``"ready"`` once the local gap and the operation window allow a call.

        The wait is computed under the lock and slept outside it, then the slot is checked again, so the tick
        handshake is never stuck behind a sleeping candle call. A server 429 returns ``"blocked"`` at once:
        REQUEST_RATE_LIMIT is not retried, and an order that gets it stays REJECTED.
        """
        while True:
            with self._lock:
                state, wait = self._slot_wait_locked(operation)
                before = self._mono_ms()
            if state != "wait":
                return state
            self._sleep(wait / 1000)
            if self._mono_ms() <= before:
                return "stalled"

    def discover(self):
        if self.access_point:
            return self.access_point
        path = f"/v1/trading/access-point?account_id={self.account_id}"
        status, payload, _headers, sent = self._request(self.gateway, "GET", path, None, "")
        if not sent:
            raise RuntimeError(f"rate_limited:{_local_reason(payload)}:access_point")
        point = payload.get("access_point") if status == 200 else None
        if not _host_ok(point):
            raise RuntimeError(f"access_point_unavailable:{status}:{_error_code(payload)}")
        self.access_point = point.rstrip("/")
        return self.access_point

    def get_json(self, path):
        base = self.discover()
        status, payload, _headers, sent = self._request(base, "GET", path, None, "")
        if not sent:
            raise RuntimeError(f"rate_limited:{_local_reason(payload)}:{operation_name(path)}")
        if status != 200:
            raise RuntimeError(f"read_failed:{status}:{_error_code(payload)}:{operation_name(path)}")
        return payload

    def mutate(self, method, path, body, idempotency_key):
        if not isinstance(idempotency_key, str) or not idempotency_key:
            raise ValueError("mutating requests require a non-empty idempotency key")
        prior = self._sent.get(idempotency_key)
        if prior is not None:
            return prior
        base = self.discover()
        try:
            status, payload, _headers, sent = self._request(base, method, path, body, idempotency_key)
        except Exception:
            # The request may have reached the server. Remember the key so a retry reuses it.
            result = {"status": 0, "payload": {}, "uncertain": True}
            self._sent[idempotency_key] = result
            return result
        if not sent:
            return {"status": status, "payload": payload, "uncertain": False}
        result = {"status": status, "payload": payload, "uncertain": status >= 500 or status == 0}
        self._sent[idempotency_key] = result
        return result
