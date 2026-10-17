"""Read-only OANDA account balance for the dashboard Demo/Real VIEW. Stdlib only. DISPLAY ONLY.

Deliberately independent from the order path: it imports nothing from execution/oanda_client.py,
execution/oanda_broker.py or the router, and none of them import it (test_oanda_readonly scans the sources).

* ``OandaReadOnlyClient(token, account_id, environment)`` with ``environment`` in ("practice", "live"). Hosts are a
  hard-coded allow-list (``api-fxpractice.oanda.com`` / ``api-fxtrade.oanda.com``), https only, no override, no port,
  no userinfo, redirects refused. The class has exactly ONE request: ``GET /v3/accounts/{id}/summary``; there is no
  code path to any other method or path. Reading a live summary does NOT enable trading: REAL_EXECUTION_ENABLED stays
  False and the order client still refuses api-fxtrade.
* Tokens: practice ONLY from ``OANDA_PRACTICE_TOKEN`` (+ ``OANDA_ACCOUNT_ID``, required, no default,
  prefix ``101-``); live ONLY from ``OANDA_LIVE_TOKEN`` (+ ``OANDA_LIVE_ACCOUNT_ID``, prefix ``001-``). Never crossed.
  Without an account id the view is ``SIN_CUENTA`` (account discovery via GET /v3/accounts is not allowed).
* Errors are codes only (no broker text, token or account id); the account is always masked.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
import re
import threading
import time
from urllib.parse import quote, urlsplit

HOSTS = {"practice": "api-fxpractice.oanda.com", "live": "api-fxtrade.oanda.com"}
ACCOUNT_RE = {"practice": re.compile(r"^101-\d{3}-\d+-\d{3}$"), "live": re.compile(r"^001-\d{3}-\d+-\d{3}$")}
ENV_NAMES = {"practice": ("OANDA_PRACTICE_TOKEN", "OANDA_ACCOUNT_ID"), "live": ("OANDA_LIVE_TOKEN", "OANDA_LIVE_ACCOUNT_ID")}
TIMEOUT_SECONDS = 4.0
CACHE_TTL_SECONDS = 10.0  # >= 5 s: the dashboard refresh never hammers the API
OK, SIN_TOKEN, SIN_CUENTA, ERROR = "OK", "SIN_TOKEN", "SIN_CUENTA", "ERROR"
STATUSES = (OK, SIN_TOKEN, SIN_CUENTA, ERROR)


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


def urllib_transport(method, url, headers, body, timeout):
    """Default network transport (never used by tests). GET only; redirects are never followed."""
    import socket
    from urllib.error import HTTPError, URLError
    from urllib.request import HTTPRedirectHandler, Request, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    if method != "GET" or body is not None or not any(_url_ok(url, host) for host in HOSTS.values()):
        raise ReadOnlyError("refused")
    try:
        with build_opener(NoRedirect).open(Request(url, headers=headers, method="GET"), timeout=timeout) as response:  # noqa: S310
            return response.status, dict(response.headers), response.read()
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
    """GET /v3/accounts/{id}/summary and nothing else."""

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
        self._url = f"https://{HOSTS[environment]}/v3/accounts/{quote(account_id, safe='')}/summary"
        self._transport = transport or urllib_transport
        self._timeout = float(timeout)

    def __repr__(self):
        return f"OandaReadOnlyClient(environment={self.environment!r}, account={self.account_masked!r})"

    __str__ = __repr__

    def get_account_summary(self):
        """The summary ``account`` object (Decimal numbers). Raises ReadOnlyError(code) on any failure."""
        if not _url_ok(self._url, HOSTS[self.environment]):
            raise ReadOnlyError("refused")
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self._token}"}
        try:
            status, _headers, raw = self._transport("GET", self._url, headers, None, self._timeout)
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
        account = payload.get("account") if isinstance(payload, dict) else None
        if not isinstance(account, dict):
            raise ReadOnlyError("malformed")
        return account


def _dec(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except ArithmeticError:
        return None
    return out if out.is_finite() else None


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
    """TTL cache (>= 5 s) per environment. An ERROR keeps the last good view, marked ``stale`` with the error code."""

    def __init__(self, ttl=CACHE_TTL_SECONDS, *, clock=None, fetch=None):
        self.ttl = max(5.0, float(ttl))
        self._clock = clock or time.monotonic
        self._fetch = fetch or account_view
        self._lock = threading.Lock()
        self._cache = {}  # environment -> (fetched_at, view)
        self._good = {}  # environment -> last OK view

    def get(self, environment, env=None):
        with self._lock:
            cached = self._cache.get(environment)
            if cached is not None and self._clock() - cached[0] < self.ttl:
                return cached[1]
            view = self._fetch(environment, env)
            if view.status == OK:
                self._good[environment] = view
            elif view.status == ERROR and environment in self._good:
                view = replace(self._good[environment], stale=True, error_code=view.error_code)
            self._cache[environment] = (self._clock(), view)
            return view


_VIEWS = CachedAccountViews()


def cached_account_view(environment):
    """Dashboard entry point (module-level cache shared by every refresh). Never raises."""
    try:
        return _VIEWS.get(environment)
    except Exception:  # noqa: BLE001
        return AccountView(ERROR, str(environment), error_code="unexpected")
