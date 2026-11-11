"""Signed REST calls. The HTTP function is injected so tests never open a socket.

Discovery is cached. Mutating calls reuse one idempotency key and are not sent twice.
HTTP 429 is not retried (the troubleshooting page says REQUEST_RATE_LIMIT is not retried automatically).
The limits JSON shape is not published on the pages we could read, so the Floor also keeps a minimum gap
between calls (``AI_FLOOR_EXNESS_MIN_INTERVAL_MS``, default 100). TODO: parse
``GET /v1/configuration/accounts/{account_id}/limits`` once its field names are confirmed.
"""
from datetime import datetime, timezone
import json
from urllib.parse import urlsplit

from execution.exness_sign import headers


MIN_INTERVAL_ENV = "AI_FLOOR_EXNESS_MIN_INTERVAL_MS"
DEFAULT_MIN_INTERVAL_MS = 100


def _interval_ms(env):
    raw = str(env.get(MIN_INTERVAL_ENV) or "").strip()
    if not raw:
        return DEFAULT_MIN_INTERVAL_MS
    if not raw.isdigit() or not 1 <= int(raw) <= 60_000:
        return None
    return int(raw)


def _host_ok(url):
    parts = urlsplit(url if isinstance(url, str) else "")
    return parts.scheme == "https" and bool(parts.hostname) and not parts.username and not parts.password and not parts.query and not parts.fragment


class ExnessClient:
    def __init__(self, env, *, caller, sign, clock=None, now_ms=None):
        self._env = env
        self._caller = caller
        self._sign = sign
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._now_ms = now_ms or (lambda: int(self._clock().timestamp() * 1000))
        self._interval = _interval_ms(env)
        self.access_point = None
        self._last_ms = None
        self._blocked_until_ms = None
        self._sent = {}
        key = env.get("EXN_API_KEY")
        account = env.get("EXN_ACCOUNT_ID")
        gateway = env.get("EXN_DISCOVERY_BASE_URL")
        if not isinstance(key, str) or not key.strip() or not isinstance(account, str) or not account.strip().isdigit():
            raise ValueError("EXN_API_KEY and numeric EXN_ACCOUNT_ID are required")
        if not _host_ok((gateway or "").strip().rstrip("/")):
            raise ValueError("EXN_DISCOVERY_BASE_URL must be an https URL without userinfo")
        if self._interval is None:
            raise ValueError("AI_FLOOR_EXNESS_MIN_INTERVAL_MS is invalid")
        self.api_key = key.strip()
        self.account_id = account.strip()
        self.gateway = gateway.strip().rstrip("/")

    def _request(self, base, method, path, body=None, idempotency_key=""):
        now = self._now_ms()
        if self._blocked_until_ms is not None and now < self._blocked_until_ms:
            return 429, {"error_message": "REQUEST_RATE_LIMIT"}, {}, False
        if self._last_ms is not None and now - self._last_ms < self._interval:
            return 429, {"error_message": "floor_min_interval"}, {}, False
        raw = b"" if body is None else json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        signed = headers(self.api_key, idempotency_key, now, method, path, raw, self._sign)
        if body is not None:
            signed["Content-Type"] = "application/json"
        self._last_ms = now
        status, payload, response_headers = self._caller(method, base + path, signed, raw)
        if status == 429:
            retry = response_headers.get("Retry-After") if isinstance(response_headers, dict) else None
            wait = int(retry) if isinstance(retry, str) and retry.isdigit() else 1
            self._blocked_until_ms = now + wait * 1000
        return status, payload if isinstance(payload, dict) else {}, response_headers or {}, True

    def discover(self):
        if self.access_point:
            return self.access_point
        path = f"/v1/trading/access-point?account_id={self.account_id}"
        status, payload, _headers, sent = self._request(self.gateway, "GET", path, None, "")
        if not sent:
            raise RuntimeError("rate_limited")
        point = payload.get("access_point") if status == 200 else None
        if not _host_ok(point):
            raise RuntimeError("access_point_unavailable")
        self.access_point = point.rstrip("/")
        return self.access_point

    def get_json(self, path):
        base = self.discover()
        status, payload, _headers, sent = self._request(base, "GET", path, None, "")
        if not sent:
            raise RuntimeError("rate_limited")
        if status != 200:
            raise RuntimeError(f"read_failed:{status}")
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
