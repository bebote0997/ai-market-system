"""OANDA v20 REST client for the PRACTICE environment only, stdlib only (DRAFT, not wired; nothing imports it from
runtime/, floor/ or ui/).

* Host guard: the only accepted endpoint is ``https://api-fxpractice.oanda.com/v3``. The constructor AND every request
  refuse anything else (``api-fxtrade.oanda.com``, http, userinfo, ports, trailing dots, case tricks, other hosts) with
  ``BrokerGuardError``. There is deliberately NO flag to reach the live host; enabling live trading would need a new,
  owner-approved, explicit change (not implemented here).
* Transport is injectable: ``transport(method, url, headers, body, timeout) -> (status, headers, body_bytes)``; it
  raises ``TimeoutError`` on timeouts and ``OSError`` on connection failures. The default urllib transport refuses
  redirects. Tests always inject a fake and patch the default to fail.
* The bearer token lives only in memory; it and the account id never appear in repr/logs/exceptions (``scrub``).
* Rate limiter (injectable): <= 10 data req/s, 2 trading req/s. 429 -> bounded exponential backoff (honours
  Retry-After, cap 8 s, max 3 retries) then ``BrokerRateLimited``. 5xx on GET -> at most 2 bounded retries.
  Trading requests (POST/PUT) are NEVER retried on timeout/5xx/transport errors: callers map them to UNKNOWN and
  reconcile. An optional ``pre_send_guard()`` runs right before EVERY transport attempt of a trading request (after
  the limiter wait, inside the 429 loop); a ``BrokerGuardError`` from it propagates and nothing is sent. 401/403 -> ``BrokerAuthError``; other 4xx -> ``BrokerRejected(status, code=errorCode)``; 3xx ->
  ``BrokerUnavailable`` (redirects are never followed).

Only ``EUR_USD`` is accepted as instrument. Streaming is not implemented (``PRACTICE_STREAM_URL`` is informational).
"""
from decimal import ROUND_HALF_UP, Decimal
import json
import logging
import re
import time
from urllib.parse import quote, urlsplit

from execution.broker_port import (BrokerAuthError, BrokerGuardError, BrokerRateLimited, BrokerRejected, BrokerTimeout,
                                   BrokerUnavailable)
from execution.dxtrade_client import RateLimiter


LOG = logging.getLogger("ai_floor.broker.oanda")
PRACTICE_HOST = "api-fxpractice.oanda.com"
PRACTICE_REST_URL = f"https://{PRACTICE_HOST}/v3"
PRACTICE_STREAM_URL = "https://stream-fxpractice.oanda.com"  # Streaming not implemented.
LIVE_HOSTS = ("api-fxtrade.oanda.com", "stream-fxtrade.oanda.com")  # Always refused; no override exists.
DATA, TRADING = "data", "trading"
MIN_INTERVAL_SECONDS = {DATA: 0.1, TRADING: 0.5}
ALLOWED_INSTRUMENTS = ("EUR_USD",)
PRICE_QUANTUM = Decimal("0.00001")
PRACTICE_ACCOUNT_RE = re.compile(r"^101-\d{3}-\d+-\d{3}$")
_TRADE_SPECIFIER_RE = re.compile(r"^(?:\d{1,20}|@[A-Za-z0-9_.\-]{1,128})$")
_CODE_RE = re.compile(r"^[A-Z0-9_]{1,64}$")
_TRANSACTION_ID_RE = re.compile(r"^\d{1,20}$")


def mask_account(account_id):
    """``101-000-1234567-001`` -> ``101-…4567-001``. Empty -> ``""``."""
    account_id = str(account_id or "")
    if not account_id:
        return ""
    parts = account_id.split("-")
    if len(parts) == 4 and all(parts):
        return f"{parts[0]}-…{parts[2][-4:]}-{parts[3]}"
    return f"…{account_id[-4:]}" if len(account_id) > 4 else "…"


def practice_url_problem(url):
    """None when ``url`` is exactly the OANDA practice REST base (an optional trailing ``/`` is tolerated)."""
    if not isinstance(url, str) or url.rstrip("/") != PRACTICE_REST_URL:
        return "only the OANDA practice REST URL is accepted"
    return None


def _request_url_ok(url):
    """Every request URL: https, exact practice host, no userinfo/port, path under /v3/."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError):
        return False
    return (isinstance(url, str) and url.startswith(PRACTICE_REST_URL + "/") and parts.scheme == "https"
            and parts.netloc == PRACTICE_HOST and parts.hostname == PRACTICE_HOST and port is None
            and "@" not in parts.netloc and parts.path.startswith("/v3/") and "#" not in url)


def reason_code(value, default="UNRECOGNIZED_REASON"):
    """Broker reason/error codes are passed through only if they look like a code (never free broker text)."""
    return value if isinstance(value, str) and _CODE_RE.match(value) else default


def round_price(price):
    """Decimal price rounded to 5 decimals with ROUND_HALF_UP."""
    if not isinstance(price, Decimal) or not price.is_finite() or price <= 0:
        raise ValueError("price must be a finite positive Decimal")
    return price.quantize(PRICE_QUANTUM, rounding=ROUND_HALF_UP)


def format_price(price):
    """``Decimal('1.0805')`` -> ``'1.08050'`` (exactly 5 decimals)."""
    return f"{round_price(price):f}"


def format_units(units):
    """Signed integer units as string (negative for SELL)."""
    if type(units) is not int or units == 0:
        raise ValueError("units must be a non-zero int")
    return str(units)


def trade_specifier_ok(specifier):
    return isinstance(specifier, str) and bool(_TRADE_SPECIFIER_RE.match(specifier))


def _instrument(instrument):
    if instrument not in ALLOWED_INSTRUMENTS:
        raise BrokerGuardError("instrument not supported (only EUR_USD)")
    return instrument


def _refuse_redirects():
    from urllib.request import HTTPRedirectHandler

    class NoRedirectHandler(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None  # urllib then raises HTTPError(3xx): the redirect is never followed.

    return NoRedirectHandler


def _opener():
    from urllib.request import build_opener
    return build_opener(_refuse_redirects())


def urllib_transport(method, url, headers, body, timeout):
    """Default network transport. Never used by tests. Refuses non-practice URLs and never follows redirects."""
    import socket
    from urllib.error import HTTPError, URLError
    from urllib.request import Request
    if not _request_url_ok(url):
        raise BrokerGuardError("request URL refused (practice host only)")
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with _opener().open(request, timeout=timeout) as response:  # noqa: S310 - practice https host enforced
            return response.status, dict(response.headers), response.read()
    except HTTPError as error:
        return error.code, dict(error.headers or {}), error.read() or b""
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise TimeoutError("request timed out") from None
        raise OSError("connection failed") from None
    except (TimeoutError, socket.timeout):
        raise TimeoutError("request timed out") from None


class OandaClient:
    def __init__(self, token, account_id, *, base_url=PRACTICE_REST_URL, transport=None, clock=None, sleep=None,
                 limiter=None, timeout=10.0, max_429_retries=3, max_read_retries=2, backoff_base=1.0,
                 backoff_cap=8.0, pre_send_guard=None):
        problem = practice_url_problem(base_url)
        if problem:
            raise BrokerGuardError(f"OANDA {problem}")  # Never echoes the URL.
        if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token.strip()):
            raise BrokerGuardError("OANDA practice token missing or invalid")
        if not isinstance(account_id, str) or not PRACTICE_ACCOUNT_RE.match(account_id):
            raise BrokerGuardError("OANDA account id must be a practice account (101-xxx-...-xxx)")
        self._base = PRACTICE_REST_URL
        self._token = token.strip()
        self._account = account_id
        self._masked = mask_account(account_id)
        self._transport = transport or urllib_transport
        self._clock = clock or time.monotonic
        self._sleep = sleep or time.sleep
        self._limiter = limiter or RateLimiter(self._clock, self._sleep, MIN_INTERVAL_SECONDS)
        self._timeout = timeout
        self._max_429 = max(0, int(max_429_retries))
        self._max_read_retries = max(0, int(max_read_retries))
        self._backoff_base, self._backoff_cap = float(backoff_base), float(backoff_cap)
        self._pre_send_guard = pre_send_guard
        self.last_ok_at = None
        self.last_error = None

    def __repr__(self):
        return f"OandaClient(base_url={self._base!r}, account={self._masked!r})"

    __str__ = __repr__

    @property
    def account_masked(self):
        return self._masked

    def scrub(self, text):
        text = str(text)
        if self._token:
            text = text.replace(self._token, "***")
        return text.replace(quote(self._account, safe=""), self._masked).replace(self._account, self._masked)

    # -- read endpoints ------------------------------------------------------------------------------------------
    def _path(self, suffix):
        return f"/accounts/{quote(self._account, safe='')}/{suffix}"

    @staticmethod
    def _trade(specifier):
        if not trade_specifier_ok(specifier):
            raise ValueError("invalid trade specifier")
        return quote(specifier, safe="@")

    def get_summary(self):
        return self._call(DATA, "GET", self._path("summary"))

    def get_pricing(self, instrument="EUR_USD"):
        return self._call(DATA, "GET", self._path(f"pricing?instruments={_instrument(instrument)}"))

    def get_instruments(self, instrument="EUR_USD"):
        return self._call(DATA, "GET", self._path(f"instruments?instruments={_instrument(instrument)}"))

    def get_open_trades(self):
        return self._call(DATA, "GET", self._path("openTrades"))

    def get_open_positions(self):
        return self._call(DATA, "GET", self._path("openPositions"))

    def get_transactions_since(self, tx_id, types=("TRANSFER_FUNDS",)):
        """Read-only: transactions after (excluding) ``tx_id``, filtered by ``types`` (e.g. ``TRANSFER_FUNDS``)."""
        if not isinstance(tx_id, str) or not _TRANSACTION_ID_RE.match(tx_id):
            raise ValueError("invalid transaction id")
        if not types or isinstance(types, str) or any(not isinstance(t, str) or not _CODE_RE.match(t) for t in types):
            raise ValueError("invalid transaction type filter")
        return self._call(DATA, "GET", self._path(f"transactions/sinceid?id={tx_id}&type={','.join(types)}"))

    def get_trade(self, specifier):
        """``specifier`` is a tradeID or ``@<clientExtensions.id>``."""
        return self._call(DATA, "GET", self._path(f"trades/{self._trade(specifier)}"))

    # -- trading endpoints (never retried on timeout/5xx/transport) ----------------------------------------------
    def create_order(self, order):
        return self._call(TRADING, "POST", self._path("orders"), {"order": order})

    def replace_stop_loss(self, specifier, price):
        body = {"stopLoss": {"price": format_price(price), "timeInForce": "GTC"}}
        return self._call(TRADING, "PUT", self._path(f"trades/{self._trade(specifier)}/orders"), body)

    def close_trade(self, specifier, units="ALL"):
        """Close ``units`` of one trade. ``"ALL"`` closes the whole trade; a positive int closes that many units."""
        if units == "ALL":
            body = {"units": "ALL"}
        elif type(units) is int and units > 0:
            body = {"units": str(units)}
        else:
            raise ValueError("units must be 'ALL' or a positive int")
        return self._call(TRADING, "PUT", self._path(f"trades/{self._trade(specifier)}/close"), body)

    def get_pending_orders(self):
        """Read-only list of pending orders (resting limits included)."""
        return self._call(DATA, "GET", self._path("pendingOrders"))

    def cancel_order(self, order_id):
        """Cancel one pending order by its numeric id. Reduces risk; never opens."""
        if not isinstance(order_id, str) or not _TRANSACTION_ID_RE.match(order_id):
            raise ValueError("invalid order id")
        return self._call(TRADING, "PUT", self._path(f"orders/{quote(order_id, safe='')}/cancel"))

    def close_position(self, instrument, side):
        if side not in ("LONG", "SHORT"):
            raise ValueError("side must be LONG or SHORT")
        body = {"longUnits": "ALL"} if side == "LONG" else {"shortUnits": "ALL"}
        return self._call(TRADING, "PUT", self._path(f"positions/{_instrument(instrument)}/close"), body)

    @staticmethod
    def build_market_order(*, instrument, units, stop, target, client_extensions, trade_client_extensions):
        """MARKET FOK order with SL/TP on fill (GTC). Prices: 5-decimal strings; units: signed integer string."""
        return {"type": "MARKET", "instrument": _instrument(instrument), "units": format_units(units),
                "timeInForce": "FOK", "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": format_price(stop), "timeInForce": "GTC"},
                "takeProfitOnFill": {"price": format_price(target), "timeInForce": "GTC"},
                "clientExtensions": dict(client_extensions), "tradeClientExtensions": dict(trade_client_extensions)}

    @staticmethod
    def build_limit_order(*, instrument, units, price, stop, target, gtd_time, client_extensions,
                          trade_client_extensions):
        """LIMIT GTD order. Stop and target are the engine's absolute prices, attached on fill, not shifted."""
        if not isinstance(gtd_time, str) or "T" not in gtd_time or not gtd_time.endswith("Z"):
            raise ValueError("gtd_time must be an RFC3339 UTC timestamp")
        return {"type": "LIMIT", "instrument": _instrument(instrument), "units": format_units(units),
                "price": format_price(price), "timeInForce": "GTD", "gtdTime": gtd_time, "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": format_price(stop), "timeInForce": "GTC"},
                "takeProfitOnFill": {"price": format_price(target), "timeInForce": "GTC"},
                "clientExtensions": dict(client_extensions), "tradeClientExtensions": dict(trade_client_extensions)}

    # -- transport ------------------------------------------------------------------------------------------------
    def _call(self, category, method, path, body=None):
        read = method == "GET"
        attempts_429 = attempts_5xx = 0
        while True:
            status, headers, raw = self._send(category, method, path, body)
            if status == 429:  # Not processed by the broker: a bounded retry is safe even for trading calls.
                if attempts_429 >= self._max_429:
                    raise self._fail(BrokerRateLimited("rate limited after bounded backoff"))
                delay = self._retry_after(headers)
                delay = self._backoff_base * (2 ** attempts_429) if delay is None else delay
                delay = min(max(delay, self._backoff_base), self._backoff_cap)
                attempts_429 += 1
                LOG.warning("oanda 429 on %s %s; backoff %.1fs (%d/%d)", method, self.scrub(path), delay,
                            attempts_429, self._max_429)
                self._sleep(delay)
                continue
            if status >= 500 and read and attempts_5xx < self._max_read_retries:
                delay = min(self._backoff_base * (2 ** attempts_5xx), self._backoff_cap)
                attempts_5xx += 1
                LOG.warning("oanda HTTP %d on GET %s; retry %d/%d in %.1fs", status, self.scrub(path),
                            attempts_5xx, self._max_read_retries, delay)
                self._sleep(delay)
                continue
            return self._decode(status, raw, method, path)

    def _send(self, category, method, path, body):
        url = self._base + path
        if not _request_url_ok(url):
            raise self._fail(BrokerGuardError("request URL refused (practice host only)"))
        self._limiter.acquire(category)
        if category == TRADING and self._pre_send_guard is not None:
            try:
                self._pre_send_guard()  # Last authorization check, after any limiter/429 wait: may refuse to send.
            except BrokerGuardError as error:
                raise self._fail(error) from None
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._token}",
                   "Accept-Datetime-Format": "RFC3339"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        LOG.debug("oanda %s %s", method, self.scrub(path))
        try:
            return self._transport(method, url, headers, data, self._timeout)
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
        except (UnicodeDecodeError, ValueError, AttributeError):
            payload = None
        where = f"{method} {self.scrub(path)}"
        if 200 <= status < 300:
            if payload is None:
                raise self._fail(BrokerUnavailable(f"unparseable answer on {where}"))
            self.last_ok_at, self.last_error = self._clock(), None
            return payload
        if 300 <= status < 400:
            raise self._fail(BrokerUnavailable(f"redirect refused on {where}"))
        if status in (401, 403):
            raise self._fail(BrokerAuthError(f"authentication refused on {where}"))
        if status >= 500:
            raise self._fail(BrokerUnavailable(f"HTTP {status} on {where}"))
        code = None
        if isinstance(payload, dict):
            reject = payload.get("orderRejectTransaction")
            raw_code = payload.get("errorCode")
            if raw_code is None and isinstance(reject, dict):
                raw_code = reject.get("rejectReason")
            code = reason_code(raw_code, default=None)
        # errorMessage is broker free text: never copied into the exception.
        raise self._fail(BrokerRejected(f"HTTP {status} on {where}" + (f" ({code})" if code else ""),
                                        status=status, code=code))

    def _fail(self, error):
        self.last_error = type(error).__name__
        return error
