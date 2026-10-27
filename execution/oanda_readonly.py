"""Read-only OANDA account balance for the dashboard Demo/Real VIEW. Stdlib only. DISPLAY ONLY.

Deliberately independent from the order path: it imports nothing from execution/oanda_client.py,
execution/oanda_broker.py or the router, and none of them import it (test_oanda_readonly scans the sources).

* ``OandaReadOnlyClient(token, account_id, environment)`` with ``environment`` in ("practice", "live"). Hosts are a
  hard-coded allow-list (``api-fxpractice.oanda.com`` / ``api-fxtrade.oanda.com``), https only, no override, no port,
  no userinfo, redirects refused. The class has exactly TWO requests, both GET: ``/v3/accounts/{id}/summary`` and
  ``/v3/accounts/{id}/trades?state=OPEN|CLOSED&count=500[&beforeID=N]`` (dashboard demo trades, paged, hard cap
  ``TRADES_MAX_PAGES``); there is no code path to any other method, path or query key. Reading a live summary does
  NOT enable trading: REAL_EXECUTION_ENABLED stays False and the order client still refuses api-fxtrade.
* Tokens: practice ONLY from ``OANDA_PRACTICE_TOKEN`` (+ ``OANDA_ACCOUNT_ID``, required, no default,
  prefix ``101-``); live ONLY from ``OANDA_LIVE_TOKEN`` (+ ``OANDA_LIVE_ACCOUNT_ID``, prefix ``001-``). Never crossed.
  Without an account id the view is ``SIN_CUENTA`` (account discovery via GET /v3/accounts is not allowed).
* Trades refresh: one overall deadline for CLOSED+OPEN (``deadline`` error) and an explicit ``incomplete`` error when
  the page cap runs out before the epoch; both are ``{"error": code}`` results, never partial totals.
* Errors are codes only (no broker text, token or account id); the account is always masked. Trades are reduced to
  ``TRADE_FIELDS`` (no account id, no transaction ids); ``cached_demo_trades`` reads the PRACTICE account only.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import re
import threading
import time
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

HOSTS = {"practice": "api-fxpractice.oanda.com", "live": "api-fxtrade.oanda.com"}
ACCOUNT_RE = {"practice": re.compile(r"^101-\d{3}-\d+-\d{3}$"), "live": re.compile(r"^001-\d{3}-\d+-\d{3}$")}
ENV_NAMES = {"practice": ("OANDA_PRACTICE_TOKEN", "OANDA_ACCOUNT_ID"), "live": ("OANDA_LIVE_TOKEN", "OANDA_LIVE_ACCOUNT_ID")}
TIMEOUT_SECONDS = 4.0
CACHE_TTL_SECONDS = 10.0  # >= 5 s: the dashboard refresh never hammers the API
OK, SIN_TOKEN, SIN_CUENTA, ERROR = "OK", "SIN_TOKEN", "SIN_CUENTA", "ERROR"
STATUSES = (OK, SIN_TOKEN, SIN_CUENTA, ERROR)
TRADE_STATES = ("OPEN", "CLOSED")
TRADES_PAGE_SIZE = 500
TRADES_MAX_PAGES = 10  # hard cap: at most 10 GETs per state and refresh
# CLOSED+OPEN share one budget; the summary GET (TIMEOUT_SECONDS) is separate: a refresh never exceeds ~12 s overall.
TRADES_REFRESH_DEADLINE_SECONDS = 8.0
TRADES_ERRORS = ("incomplete", "deadline")  # explicit non-success states of demo_trades (dashboard: "sin datos")
TRADES_QUERY_KEYS = ("state", "count", "beforeID")
TRADES_PATH_RE = re.compile(r"^/v3/accounts/[0-9-]+/trades$")
INSTRUMENT_RE = re.compile(r"^[A-Z0-9]{2,10}_[A-Z0-9]{2,10}$")
TRADE_FIELDS = ("id", "instrument", "initialUnits", "currentUnits", "price", "openTime", "closeTime", "state",
                "realizedPL", "financing", "unrealizedPL", "averageClosePrice", "client_id", "stop_loss",
                "take_profit")


class ReadOnlyError(Exception):
    """Carries only a short code (``auth``, ``rate_limited``, ``timeout``...); never broker text or secrets."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def mask(account_id):
    parts = str(account_id or "").split("-")
    if len(parts) == 4 and all(parts):
        return f"{parts[0]}-…{parts[2][-4:]}-{parts[3]}"
    return "…" if account_id else ""


def _url_ok(url, host):
    try:
        parts = urlsplit(url)
        port = parts.port
    except (TypeError, ValueError):
        return False
    return (parts.scheme == "https" and parts.netloc == host and parts.hostname == host and port is None
            and "@" not in parts.netloc and not parts.query and not parts.fragment)


def _trades_url_ok(url, host):
    """The trades list URL only: same host rules, path ``/v3/accounts/{id}/trades``, allow-listed query keys once."""
    try:
        parts = urlsplit(url)
        port = parts.port
        query = parse_qsl(parts.query, keep_blank_values=True, strict_parsing=True)
    except (TypeError, ValueError):
        return False
    keys = [key for key, _value in query]
    return (parts.scheme == "https" and parts.netloc == host and parts.hostname == host and port is None
            and "@" not in parts.netloc and not parts.fragment and bool(TRADES_PATH_RE.match(parts.path))
            and len(keys) == len(set(keys)) and set(keys) <= set(TRADES_QUERY_KEYS) and "state" in keys)


MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def _bounded_read(response, timeout, *, clock=None, chunk=65536, max_bytes=MAX_RESPONSE_BYTES):
    """Read the body in chunks with a wall-clock limit (``timeout``) and a size cap: a slow trickle can never hold a
    dashboard session beyond the request budget."""
    import time
    clock = clock or time.monotonic
    deadline = clock() + float(timeout if timeout is not None else 10)
    parts, size = [], 0
    while True:
        if clock() > deadline:
            raise TimeoutError("timeout")
        data = response.read(chunk)
        if not data:
            return b"".join(parts)
        size += len(data)
        if size > max_bytes:
            raise OSError("response too large")
        parts.append(data)


def urllib_transport(method, url, headers, body, timeout):
    """Default network transport (never used by tests). GET only; redirects are never followed."""
    import socket
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    if method != "GET" or body is not None or not any(_url_ok(url, host) or _trades_url_ok(url, host)
                                                      for host in HOSTS.values()):
        raise ReadOnlyError("refused")
    try:
        with build_opener(NoRedirect).open(Request(url, headers=headers, method="GET"), timeout=timeout) as response:  # noqa: S310
            return response.status, dict(response.headers), _bounded_read(response, timeout)
    except HTTPError as error:
        return error.code, dict(error.headers or {}), b""
    except URLError as error:
        if isinstance(error.reason, (TimeoutError, socket.timeout)):
            raise TimeoutError("timeout") from None
        raise OSError("connection failed") from None
    except (TimeoutError, socket.timeout):
        raise TimeoutError("timeout") from None


@dataclass(frozen=True)
class AccountView:
    status: str
    environment: str
    account_masked: str = ""
    currency: str = None
    balance: Decimal = None
    nav: Decimal = None
    margin_used: Decimal = None
    open_trades: int = None
    unrealized_pl: Decimal = None
    as_of: datetime = None
    error_code: str = None
    stale: bool = False

    def __post_init__(self):
        if self.status not in STATUSES:
            raise ValueError("invalid status")


class OandaReadOnlyClient:
    """GET /v3/accounts/{id}/summary and GET /v3/accounts/{id}/trades (list) and nothing else."""

    def __init__(self, token, account_id, environment, *, transport=None, timeout=TIMEOUT_SECONDS):
        if environment not in HOSTS:
            raise ValueError("environment must be 'practice' or 'live'")
        if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token.strip()):
            raise ReadOnlyError("token_invalid")
        if not isinstance(account_id, str) or not ACCOUNT_RE[environment].match(account_id):
            raise ReadOnlyError("account_prefix_mismatch")
        self._token = token.strip()
        self._account = account_id
        self.environment = environment
        self.account_masked = mask(account_id)
        base = f"https://{HOSTS[environment]}/v3/accounts/{quote(account_id, safe='')}"
        self._url = base + "/summary"
        self._trades_url = base + "/trades"
        self._transport = transport or urllib_transport
        self._timeout = float(timeout)

    def __repr__(self):
        return f"OandaReadOnlyClient(environment={self.environment!r}, account={self.account_masked!r})"

    __str__ = __repr__

    def _get(self, url, url_ok, timeout=None):
        """One GET -> decoded JSON object (Decimal numbers). Raises ReadOnlyError(code) on any failure."""
        if not url_ok(url, HOSTS[self.environment]):
            raise ReadOnlyError("refused")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._token}"}
        try:
            status, _headers, raw = self._transport("GET", url, headers, None,
                                                    self._timeout if timeout is None else timeout)
        except TimeoutError:
            raise ReadOnlyError("timeout") from None
        except ReadOnlyError:
            raise
        except Exception:  # noqa: BLE001 - never echo transport text (it could carry the URL)
            raise ReadOnlyError("unavailable") from None
        if status in (401, 403):
            raise ReadOnlyError("auth")
        if status == 429:
            raise ReadOnlyError("rate_limited")
        if not 200 <= status < 300:
            raise ReadOnlyError("redirect" if 300 <= status < 400 else f"http_{int(status)}")
        try:
            payload = json.loads(raw.decode("utf-8"), parse_float=Decimal)
        except (AttributeError, UnicodeDecodeError, ValueError):
            raise ReadOnlyError("malformed") from None
        if not isinstance(payload, dict):
            raise ReadOnlyError("malformed")
        return payload

    def get_account_summary(self):
        """The summary ``account`` object (Decimal numbers). Raises ReadOnlyError(code) on any failure."""
        account = self._get(self._url, _url_ok).get("account")
        if not isinstance(account, dict):
            raise ReadOnlyError("malformed")
        return account

    def get_trades(self, state, *, since=None, max_pages=TRADES_MAX_PAGES, deadline=None, clock=time.monotonic):
        """Trades in ``state`` (OPEN|CLOSED), newest first, reduced to ``TRADE_FIELDS``. Follows ``beforeID`` pages
        while a page is full and (with ``since``) every trade on it opened at/after ``since``; never more than
        ``TRADES_MAX_PAGES`` GETs. ``deadline`` (a ``clock()`` value) bounds the whole read; each GET's timeout is
        capped by the time left. Raises ReadOnlyError(code) on any failure: ``deadline`` when the time is up,
        ``incomplete`` when the page cap runs out with more (post-``since``) trades left: never partial totals."""
        if state not in TRADE_STATES:
            raise ReadOnlyError("state_invalid")
        trades, seen, before = [], set(), None
        for _page in range(max(1, min(int(max_pages), TRADES_MAX_PAGES))):
            timeout = None
            if deadline is not None:
                left = deadline - clock()
                if left <= 0:
                    raise ReadOnlyError("deadline")
                timeout = min(self._timeout, left)
            query = {"state": state, "count": str(TRADES_PAGE_SIZE)}
            if before is not None:
                query["beforeID"] = str(before)
            page = self._get(f"{self._trades_url}?{urlencode(query)}", _trades_url_ok, timeout).get("trades")
            if not isinstance(page, list) or not all(isinstance(trade, dict) for trade in page):
                raise ReadOnlyError("malformed")
            safe = [safe_trade(trade) for trade in page]
            trades += [trade for trade in safe if trade["id"] is not None and trade["id"] not in seen]
            seen.update(trade["id"] for trade in safe)
            ids = [int(trade["id"]) for trade in safe if trade["id"] is not None]
            opened = [_time(trade["openTime"]) for trade in safe]
            if len(page) < TRADES_PAGE_SIZE or not ids or min(ids) <= 1:
                break
            if since is not None and any(at is None or at < since for at in opened):
                break
            before = min(ids) - 1
        else:
            raise ReadOnlyError("incomplete")
        if deadline is not None and clock() > deadline:
            raise ReadOnlyError("deadline")
        return trades


def _dec(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except ArithmeticError:
        return None
    return out if out.is_finite() else None


def _time(value):
    """OANDA RFC 3339 time (nanoseconds) -> aware UTC datetime, or None."""
    if not isinstance(value, str):
        return None
    match = re.match(r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})$", value.strip())
    if not match:
        return None
    stamp, fraction, zone = match.groups()
    try:
        parsed = datetime.fromisoformat(f"{stamp}.{(fraction or '0')[:6].ljust(6, '0')}"
                                        f"{'+00:00' if zone == 'Z' else zone}")
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc)


def _nested_price(trade, key):
    order = trade.get(key)
    return _dec(order.get("price")) if isinstance(order, dict) else None


def safe_trade(trade):
    """Only ``TRADE_FIELDS``: numbers as Decimal, times as validated RFC 3339 strings, ids as digit strings."""
    def text(key, pattern):
        value = trade.get(key)
        return value if isinstance(value, str) and re.match(pattern, value) else None

    extensions = trade.get("clientExtensions")
    client_id = extensions.get("id") if isinstance(extensions, dict) else None
    out = {"id": text("id", r"^\d{1,20}$"), "instrument": text("instrument", INSTRUMENT_RE.pattern),
           "openTime": trade.get("openTime") if _time(trade.get("openTime")) else None,
           "closeTime": trade.get("closeTime") if _time(trade.get("closeTime")) else None,
           "state": text("state", r"^[A-Z_]{1,32}$"),
           "client_id": client_id if isinstance(client_id, str) and re.match(r"^[A-Za-z0-9._:-]{1,128}$", client_id)
           else None,
           "stop_loss": _nested_price(trade, "stopLossOrder"), "take_profit": _nested_price(trade, "takeProfitOrder")}
    for key in ("initialUnits", "currentUnits", "price", "realizedPL", "financing", "unrealizedPL", "averageClosePrice"):
        out[key] = _dec(trade.get(key))
    return {key: out[key] for key in TRADE_FIELDS}


def _int(value):
    try:
        return int(str(value)) if value is not None and not isinstance(value, bool) else None
    except ValueError:
        return None


def account_view(environment, env=None, *, transport=None, now=None):
    """Fresh AccountView for ``environment`` from the matching env names only. Never raises."""
    env = os.environ if env is None else env
    now = now or datetime.now(timezone.utc)
    token_name, account_name = ENV_NAMES.get(environment, (None, None))
    if token_name is None:
        return AccountView(ERROR, str(environment), error_code="environment_invalid", as_of=now)
    token = env.get(token_name)
    token = token.strip() if isinstance(token, str) else ""
    account = env.get(account_name)
    account = account.strip() if isinstance(account, str) else ""
    if not token:
        return AccountView(SIN_TOKEN, environment, mask(account), as_of=now)
    if not account:
        return AccountView(SIN_CUENTA, environment, as_of=now)
    try:
        client = OandaReadOnlyClient(token, account, environment, transport=transport)
        summary = client.get_account_summary()
    except ReadOnlyError as error:
        return AccountView(ERROR, environment, mask(account), as_of=now, error_code=error.code)
    except Exception:  # noqa: BLE001
        return AccountView(ERROR, environment, mask(account), as_of=now, error_code="unexpected")
    currency = summary.get("currency")
    return AccountView(OK, environment, mask(account), currency if isinstance(currency, str) else None,
                       _dec(summary.get("balance")), _dec(summary.get("NAV")), _dec(summary.get("marginUsed")),
                       _int(summary.get("openTradeCount")), _dec(summary.get("unrealizedPL")), now)


class CachedAccountViews:
    """TTL cache (>= 5 s) per environment. An ERROR keeps the last good view, marked ``stale`` with the error code.

    The lock only guards the cache dicts, never the network (same pattern as ``CachedDemoTrades``): one caller
    refreshes while the others get the previous view for that environment at once, or wait up to ``wait_seconds`` for
    the first read when none exists (then an ERROR ``timeout`` view)."""

    def __init__(self, ttl=CACHE_TTL_SECONDS, *, clock=None, fetch=None, wait_seconds=TIMEOUT_SECONDS):
        self.ttl = max(5.0, float(ttl))
        self._clock = clock or time.monotonic
        self._fetch = fetch or account_view
        self._wait = float(wait_seconds)
        self._lock = threading.Lock()
        self._cache = {}  # environment -> (fetched_at, view)
        self._good = {}  # environment -> last OK view
        self._inflight = {}  # environment -> threading.Event set when that refresh ends

    def get(self, environment, env=None):
        with self._lock:
            cached = self._cache.get(environment)
            if cached is not None and self._clock() - cached[0] < self.ttl:
                return cached[1]
            running = self._inflight.get(environment)
            owner = running is None
            if owner:
                running = self._inflight[environment] = threading.Event()
        if not owner:
            if cached is not None:
                return cached[1]  # previous view while another session refreshes
            running.wait(self._wait)
            with self._lock:
                cached = self._cache.get(environment)
            return cached[1] if cached is not None else AccountView(ERROR, str(environment), error_code="timeout")
        view = None
        try:
            view = self._fetch(environment, env)
        finally:
            with self._lock:
                if view is not None:
                    if view.status == OK:
                        self._good[environment] = view
                    elif view.status == ERROR and environment in self._good:
                        view = replace(self._good[environment], stale=True, error_code=view.error_code)
                    self._cache[environment] = (self._clock(), view)
                self._inflight.pop(environment, None)
            running.set()
        return view


_VIEWS = CachedAccountViews()


def cached_account_view(environment):
    """Dashboard entry point (module-level cache shared by every refresh). Never raises."""
    try:
        return _VIEWS.get(environment)
    except Exception:  # noqa: BLE001
        return AccountView(ERROR, str(environment), error_code="unexpected")


def demo_trades(env=None, *, since=None, transport=None, now=None, clock=time.monotonic,
                deadline_seconds=TRADES_REFRESH_DEADLINE_SECONDS, report_errors=False):
    """``{"closed": [...], "open": [...], "as_of": now}`` for the PRACTICE account only (``OANDA_PRACTICE_TOKEN`` +
    ``OANDA_ACCOUNT_ID``); ``{"error": "deadline"|"incomplete", "as_of": now}`` when CLOSED+OPEN did not finish
    within ``deadline_seconds`` or the page cap ran out; None when not configured or on any other read failure.
    ``report_errors`` (daily-summary diagnostics): every failure is ``{"error": <code>, "as_of": now}`` instead of None
    (the ``ReadOnlyError`` code, ``not_configured`` or ``unexpected``; never response text). Never raises."""
    env = os.environ if env is None else env
    deadline = clock() + float(deadline_seconds)
    try:
        token_name, account_name = ENV_NAMES["practice"]
        token, account = env.get(token_name), env.get(account_name)
        token = token.strip() if isinstance(token, str) else ""
        account = account.strip() if isinstance(account, str) else ""
        if not token or not account:
            return {"error": "not_configured", "as_of": now or datetime.now(timezone.utc)} if report_errors else None
        client = OandaReadOnlyClient(token, account, "practice", transport=transport)
        closed = client.get_trades("CLOSED", since=since, deadline=deadline, clock=clock)
        opened = client.get_trades("OPEN", since=since, deadline=deadline, clock=clock)
        return {"closed": closed, "open": opened, "as_of": now or datetime.now(timezone.utc)}
    except ReadOnlyError as error:
        if error.code in TRADES_ERRORS or report_errors:
            return {"error": error.code, "as_of": now or datetime.now(timezone.utc)}
        return None
    except Exception:  # noqa: BLE001 - codes are not needed by the dashboard: no data is "sin datos"
        return {"error": "unexpected", "as_of": now or datetime.now(timezone.utc)} if report_errors else None


class CachedDemoTrades:
    """TTL cache (>= 5 s) of ``demo_trades`` keyed by ``since``; a failed read is cached too (no hammering).

    The lock only guards the cache dict, never the network: one caller refreshes while the others get the previous
    (stale) value for the same ``since`` at once, or wait up to ``wait_seconds`` for the first read when none exists."""

    def __init__(self, ttl=CACHE_TTL_SECONDS, *, clock=None, fetch=None, wait_seconds=TRADES_REFRESH_DEADLINE_SECONDS):
        self.ttl = max(5.0, float(ttl))
        self._clock = clock or time.monotonic
        self._fetch = fetch or demo_trades
        self._wait = float(wait_seconds)
        self._lock = threading.Lock()
        self._cache = {}  # since -> (fetched_at, result)
        self._inflight = {}  # since -> threading.Event set when that refresh ends

    def get(self, since, env=None):
        with self._lock:
            cached = self._cache.get(since)
            if cached is not None and self._clock() - cached[0] < self.ttl:
                return cached[1]
            running = self._inflight.get(since)
            if running is None:
                running = self._inflight[since] = threading.Event()
                owner = True
            else:
                owner = False
        if not owner:
            if cached is not None:
                return cached[1]  # stale while another session refreshes
            running.wait(self._wait)
            with self._lock:
                cached = self._cache.get(since)
            return cached[1] if cached is not None else None
        result = None
        try:
            result = self._fetch(env, since=since)
        finally:
            with self._lock:
                self._cache = {since: (self._clock(), result)}
                self._inflight.pop(since, None)
            running.set()
        return result


_TRADES = CachedDemoTrades()


def cached_demo_trades(since):
    """Dashboard entry point for the demo trades (practice account only). Never raises; failure -> None."""
    try:
        return _TRADES.get(since)
    except Exception:  # noqa: BLE001
        return None
