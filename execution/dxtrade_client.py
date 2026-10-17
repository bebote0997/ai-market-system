"""DXtrade REST client, stdlib only (SKELETON, not wired; nothing imports it from runtime/, floor/ or ui/).

* Transport is injectable: ``transport(method, url, headers, body, timeout) -> (status, headers, body_bytes)``;
  it raises ``TimeoutError`` on timeouts and ``OSError`` on connection failures. Tests always inject a fake; the
  default urllib transport is only for a future, owner-authorised activation.
* Session token lives only in memory and is never logged, repr'd or put in exception messages; password, username,
  domain and account code are scrubbed the same way. The base URL must be https without userinfo/query/fragment and
  only its sanitised endpoint is shown.
* Rate limiter (injectable clock/sleep): 1 login/s, 2 data req/s, 1 trading req/s.
* 429 -> bounded exponential backoff, then ``BrokerRateLimited`` (never a tight loop).
* 401 -> one re-login and one retry; 404 with code 2 -> ``BrokerApiNotPermitted``; 412 -> ``BrokerPreconditionFailed``;
  timeout -> ``BrokerTimeout`` (callers map trading timeouts to UNKNOWN; never retried blindly).

Endpoint paths and payload shapes follow the public DXtrade REST docs and are UNCONFIRMED until the read-only spike
(plan §3 Fase 0 / §7).
"""
from decimal import Decimal
import json
import logging
import time
from urllib.parse import quote

from execution.broker_config import base_url_problem, display_base_url, mask_account
from execution.broker_port import (BrokerApiNotPermitted, BrokerAuthError, BrokerPreconditionFailed, BrokerRateLimited,
                                   BrokerRejected, BrokerTimeout, BrokerUnavailable)


LOG = logging.getLogger("ai_floor.broker.dxtrade")
LOGIN, DATA, TRADING = "login", "data", "trading"
MIN_INTERVAL_SECONDS = {LOGIN: 1.0, DATA: 0.5, TRADING: 1.0}
ORDER_TYPES = ("MARKET", "LIMIT", "STOP")


def urllib_transport(method, url, headers, body, timeout):
    """Default network transport. Never used by tests."""
    import socket
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout) as response:  # noqa: S310 - https enforced by DxtradeClient
            return response.status, dict(response.headers), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers or {}), error.read() or b""
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise TimeoutError("request timed out") from None
        raise OSError("connection failed") from None


class RateLimiter:
    """Minimum spacing per category; waits with the injected sleep, never busy-loops."""

    def __init__(self, clock, sleep, intervals=None):
        self._clock, self._sleep = clock, sleep
        self._intervals = dict(intervals or MIN_INTERVAL_SECONDS)
        self._next = {}

    def acquire(self, category):
        now = self._clock()
        wait = self._next.get(category, now) - now
        if wait > 0:
            self._sleep(wait)
            now = self._clock()
        self._next[category] = max(now, self._next.get(category, now)) + self._intervals[category]


class DxtradeClient:
    def __init__(self, base_url, username, password, domain, *, transport=None, clock=None, sleep=None,
                 timeout=10.0, max_429_retries=3, backoff_base=1.0, backoff_cap=8.0):
        problem = base_url_problem(base_url)
        if problem:
            raise ValueError(f"DXtrade {problem}")  # Never echoes the URL (it could carry credentials).
        self._base = base_url if base_url.endswith("/") else base_url + "/"
        self._username, self._password, self._domain = username, password, domain
        self._transport = transport or urllib_transport
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._limiter = RateLimiter(self._clock, self._sleep)
        self._timeout = timeout
        self._max_429 = max(0, int(max_429_retries))
        self._backoff_base, self._backoff_cap = float(backoff_base), float(backoff_cap)
        self._token = None
        self._masked = {}
        self.last_ok_at = None
        self.last_error = None

    def __repr__(self):
        return f"DxtradeClient(base_url={display_base_url(self._base)!r}, session_active={self.session_active})"

    __str__ = __repr__

    @property
    def session_active(self):
        return self._token is not None

    def register_account(self, account_code):
        """Account codes appear in URL paths; remember them so logs/errors show only the masked form."""
        self._masked[account_code] = mask_account(account_code)

    def scrub(self, text):
        text = str(text)
        for secret in (self._password, self._token, self._username, self._domain):
            if secret:
                text = text.replace(secret, "***")
        for code, masked in self._masked.items():
            text = text.replace(quote(code, safe=""), masked).replace(code, masked)
        return text

    # -- session -------------------------------------------------------------------------------------------------
    def login(self):
        body = {"username": self._username, "domain": self._domain, "password": self._password}
        payload = self._call(LOGIN, "POST", "login", body, auth=False)
        token = payload.get("sessionToken") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise BrokerAuthError("login answer without session token")
        self._token = token
        LOG.info("dxtrade login ok")

    def ping(self):
        return self._call(DATA, "POST", "ping")

    def logout(self):
        if self._token is None:
            return
        try:
            self._call(DATA, "POST", "logout")
        finally:
            self._token = None

    # -- account endpoints ---------------------------------------------------------------------------------------
    def _account(self, account_code, suffix):
        return f"accounts/{quote(account_code, safe='')}/{suffix}"

    def get_positions(self, account_code):
        return self._call(DATA, "GET", self._account(account_code, "positions"))

    def get_orders(self, account_code):
        return self._call(DATA, "GET", self._account(account_code, "orders"))

    def get_metrics(self, account_code):
        return self._call(DATA, "GET", self._account(account_code, "metrics"))

    def place_order(self, account_code, order):
        return self._call(TRADING, "POST", self._account(account_code, "orders"), order)

    def place_group(self, account_code, orders, contingency="IF_THEN"):
        group = {"orders": list(orders), "contingencyType": contingency}
        return self._call(TRADING, "POST", self._account(account_code, "orders/group"), group)

    def modify_order(self, account_code, order, version):
        return self._call(TRADING, "PUT", self._account(account_code, "orders"), order,
                          extra_headers={"If-Match": str(version)})

    @staticmethod
    def build_order(order_type, *, order_code, instrument, side, quantity, position_effect, price=None,
                    position_code=None, tif="GTC"):
        if order_type not in ORDER_TYPES:
            raise ValueError(f"order type must be one of {ORDER_TYPES}")
        if side not in ("BUY", "SELL") or position_effect not in ("OPEN", "CLOSE"):
            raise ValueError("invalid side or positionEffect")
        if not isinstance(quantity, Decimal) or quantity <= 0:
            raise ValueError("quantity must be a positive Decimal")
        order = {"orderCode": order_code, "type": order_type, "instrument": instrument, "quantity": str(quantity),
                 "positionEffect": position_effect, "side": side, "tif": tif}
        if order_type == "LIMIT":
            order["limitPrice"] = str(price)
        elif order_type == "STOP":
            order["stopPrice"] = str(price)
        if order_type != "MARKET" and (not isinstance(price, Decimal) or price <= 0):
            raise ValueError("LIMIT/STOP orders need a positive Decimal price")
        if position_code is not None:
            order["positionCode"] = position_code
        return order

    # -- transport ------------------------------------------------------------------------------------------------
    def _call(self, category, method, path, body=None, *, auth=True, extra_headers=None):
        if auth and self._token is None:
            self.login()
        relogged = False
        attempts_429 = 0
        while True:
            status, headers, raw = self._send(category, method, path, body, auth, extra_headers)
            if status == 401 and auth and not relogged:
                LOG.warning("dxtrade session rejected; one re-login")
                self._token = None
                relogged = True
                self.login()
                continue
            if status == 429:
                if attempts_429 >= self._max_429:
                    raise self._fail(BrokerRateLimited("rate limited after bounded backoff"))
                delay = self._retry_after(headers)
                if delay is None:
                    delay = self._backoff_base * (2 ** attempts_429)
                delay = min(max(delay, self._backoff_base), self._backoff_cap)
                attempts_429 += 1
                LOG.warning("dxtrade 429 on %s %s; backoff %.1fs (%d/%d)", method, self.scrub(path), delay,
                            attempts_429, self._max_429)
                self._sleep(delay)
                continue
            return self._decode(status, raw, method, path)

    def _send(self, category, method, path, body, auth, extra_headers):
        self._limiter.acquire(category)
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if auth:
            headers["Authorization"] = f"DXAPI {self._token}"
        headers.update(extra_headers or {})
        LOG.debug("dxtrade %s %s", method, self.scrub(path))
        try:
            return self._transport(method, self._base + path, headers, data, self._timeout)
        except TimeoutError:
            raise self._fail(BrokerTimeout(f"timeout on {method} {self.scrub(path)}")) from None
        except OSError:
            raise self._fail(BrokerUnavailable(f"transport failure on {method} {self.scrub(path)}")) from None

    @staticmethod
    def _retry_after(headers):
        for key, value in (headers or {}).items():
            if str(key).lower() == "retry-after":
                try:
                    return float(value)
                except (TypeError, ValueError):
                    return None
        return None

    def _decode(self, status, raw, method, path):
        try:
            payload = json.loads(raw.decode("utf-8"), parse_float=Decimal) if raw else {}
        except (UnicodeDecodeError, ValueError):
            payload = None
        code = payload.get("errorCode", payload.get("code")) if isinstance(payload, dict) else None
        where = f"{method} {self.scrub(path)}"
        if 200 <= status < 300:
            if payload is None:
                raise self._fail(BrokerUnavailable(f"unparseable answer on {where}"))
            self.last_ok_at, self.last_error = self._clock(), None
            return payload
        detail = ""
        if isinstance(payload, dict) and payload.get("description"):
            detail = ": " + self.scrub(str(payload["description"]))[:200]
        if status == 401:
            self._token = None
            raise self._fail(BrokerAuthError(f"authentication refused on {where}"))
        if status == 404 and str(code) == "2":
            raise self._fail(BrokerApiNotPermitted(f"API not permitted for this user ({where})"))
        if status == 412:
            raise self._fail(BrokerPreconditionFailed(f"precondition failed on {where}"))
        if status >= 500:
            raise self._fail(BrokerUnavailable(f"HTTP {status} on {where}"))
        raise self._fail(BrokerRejected(f"HTTP {status} on {where}{detail}", status=status, code=code))

    def _fail(self, error):
        self.last_error = type(error).__name__
        return error
