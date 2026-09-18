"""Finnhub economic calendar adapter. No scraping, no inferred timestamps."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.contracts import MacroEvent


ENDPOINT = "https://finnhub.io/api/v1/calendar/economic"
EURO_COUNTRIES = {"EU", "EA", "EZ", "DE", "FR", "IT", "ES", "NL", "BE", "AT", "IE", "PT", "FI", "GR"}


class FinnhubMacroError(RuntimeError):
    def __init__(self, kind, *, http_status=None):
        self.kind = kind
        self.http_status = http_status
        super().__init__(kind)


@dataclass(frozen=True)
class EconomicCalendarEvent:
    schema_version: str
    event_id: str
    source: str
    event_time_utc: datetime
    country: str
    currency: str
    event: str
    importance: str
    actual: object
    previous: object
    consensus: object
    fetched_at: datetime
    source_timestamp: datetime | None = None


def _timestamp(value):
    if not isinstance(value, str):
        raise FinnhubMacroError("INVALID_EVENT")
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise FinnhubMacroError("INVALID_EVENT") from None
    if stamp.tzinfo is None or stamp.utcoffset() is None:
        raise FinnhubMacroError("UNKNOWN_TIMEZONE")
    return stamp.astimezone(timezone.utc)


def _category(title):
    name = title.lower()
    if any(word in name for word in ("interest rate", "rate decision", "fed ", "ecb ", "fomc")):
        return "interest_rates"
    if any(word in name for word in ("cpi", "inflation", "pce", "ppi")):
        return "inflation"
    if any(word in name for word in ("payroll", "unemployment", "employment", "jobless")):
        return "employment"
    if "gdp" in name or "growth" in name:
        return "growth"
    return "unknown"


def _http_json(api_key, from_date, to_date, timeout):
    url = ENDPOINT + "?" + urlencode({"from": from_date, "to": to_date})
    request = Request(url, headers={"X-Finnhub-Token": api_key, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:
        return json.load(response)


class FinnhubMacroDataProvider:
    name = "finnhub"

    def __init__(self, *, api_key=None, timeout=15, cache_seconds=3600,
                 transport=None, clock=None):
        self.api_key = api_key if api_key is not None else os.environ.get("FINNHUB_API_KEY")
        self.timeout = float(timeout)
        self.cache_seconds = int(cache_seconds)
        if self.timeout <= 0 or not 900 <= self.cache_seconds <= 86400:
            raise ValueError("invalid macro provider configuration")
        self.transport = transport or _http_json
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._cache = ()
        self._fetched_at = None
        self.health = "NOT_CONFIGURED" if not self.api_key else "NOT_CHECKED"

    def _normalize(self, item, fetched_at):
        if not isinstance(item, dict):
            raise FinnhubMacroError("INVALID_EVENT")
        country = item.get("country")
        title = item.get("event")
        if not isinstance(country, str) or not country.strip() or not isinstance(title, str) or not title.strip():
            raise FinnhubMacroError("INVALID_EVENT")
        country = country.strip().upper()
        currency = "USD" if country == "US" else "EUR" if country in EURO_COUNTRIES else "OTHER"
        event_time = _timestamp(item.get("time"))
        raw_importance = item.get("impact")
        importance = raw_importance.upper() if isinstance(raw_importance, str) and raw_importance.upper() in {"HIGH", "MEDIUM", "LOW"} else "UNKNOWN"
        source_time = _timestamp(item["source_timestamp"]) if item.get("source_timestamp") else None
        external_id = item.get("id")
        stable = str(external_id) if external_id is not None else hashlib.sha256(
            f"{country}|{event_time.isoformat()}|{title}".encode()).hexdigest()[:24]
        return EconomicCalendarEvent("1.0", f"finnhub:{stable}", "Finnhub Economic Calendar",
                                     event_time, country, currency, title.strip(), importance,
                                     item.get("actual"), item.get("prev"), item.get("estimate"),
                                     fetched_at, source_time)

    def _refresh(self, now):
        if not self.api_key:
            self.health = "NOT_CONFIGURED"
            raise FinnhubMacroError("NOT_CONFIGURED")
        start = (now - timedelta(days=1)).date().isoformat()
        end = (now + timedelta(days=7)).date().isoformat()
        try:
            payload = self.transport(self.api_key, start, end, self.timeout)
        except HTTPError as exc:
            self.health = "RATE_LIMITED" if exc.code == 429 else "AUTH_ERROR" if exc.code in {401, 403} else "PROVIDER_FAILURE"
            raise FinnhubMacroError(self.health, http_status=exc.code) from None
        except (URLError, TimeoutError, ConnectionError):
            self.health = "PROVIDER_FAILURE"
            raise FinnhubMacroError("PROVIDER_FAILURE") from None
        if not isinstance(payload, dict) or not isinstance(payload.get("economicCalendar"), list):
            self.health = "INVALID_RESPONSE"
            raise FinnhubMacroError("INVALID_RESPONSE")
        try:
            events = tuple(self._normalize(item, now) for item in payload["economicCalendar"])
        except FinnhubMacroError as exc:
            self.health = exc.kind
            raise
        self._cache = events
        self._fetched_at = now
        self.health = "READY" if events else "EMPTY"

    def events_at(self, as_of):
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("aware as_of required")
        now = self.clock().astimezone(timezone.utc)
        if self._fetched_at is None or now - self._fetched_at >= timedelta(seconds=self.cache_seconds):
            self._refresh(now)
        return tuple(event for event in self._cache if event.fetched_at <= as_of)

    def macro_events_at(self, as_of):
        items = []
        for event in self.events_at(as_of):
            if event.currency not in {"USD", "EUR"}:
                continue
            released = event.event_time_utc <= as_of
            items.append(MacroEvent("1.0", event.event_id, event.source,
                                    event.source_timestamp, event.event_time_utc, event.fetched_at,
                                    event.event, _category(event.event), event.currency,
                                    event.importance, event.actual if released else None,
                                    event.consensus, event.previous,
                                    result_timestamp=event.fetched_at if released and event.actual is not None else None,
                                    known_at=event.fetched_at, country=event.country,
                                    fetched_at=event.fetched_at, importance=event.importance,
                                    consensus=event.consensus))
        return tuple(items)

    def news_items(self):
        return ()
