"""Official calendars and release feeds; no inferred release values or times."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import hashlib
import os
import re
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

from core.contracts import MacroEvent


@dataclass(frozen=True)
class OfficialSource:
    name: str
    url: str
    currency: str
    country: str
    format: str
    timezone: str | None = None


SOURCES = (
    OfficialSource("BLS", "https://www.bls.gov/schedule/news_release/bls.ics", "USD", "US", "ics", "America/New_York"),
    OfficialSource("BEA", "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics", "USD", "US", "ics", "America/New_York"),
    OfficialSource("Eurostat", "https://ec.europa.eu/eurostat/o/calendars/eventsIcal?theme=&category=", "EUR", "EA", "ics", "Europe/Luxembourg"),
    OfficialSource("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml", "USD", "US", "rss"),
    OfficialSource("ECB", "https://www.ecb.europa.eu/rss/press.html", "EUR", "EA", "rss"),
)


class OfficialMacroError(RuntimeError):
    pass


def policy_category(source, title):
    """Our deterministic experiment relevance policy, not source importance."""
    value = title.casefold()
    if source.name == "Federal Reserve":
        return "interest_rates" if "fomc statement" in value or "federal open market committee" in value else None
    if source.name == "ECB":
        return "interest_rates" if "monetary policy decisions" in value else None
    if source.name == "BLS":
        if "consumer price index" in value or "producer price index" in value:
            return "inflation"
        if "employment situation" in value or "job openings and labor turnover" in value:
            return "employment"
    if source.name == "BEA":
        if "personal income and outlays" in value:
            return "inflation"
        if ("gross domestic product" in value or "gdp" in value) and not value.startswith(("state gdp", "gdp by county", "gdp by state")):
            return "growth"
        if "international trade in goods and services" in value:
            return "growth"
    if source.name == "Eurostat":
        if "inflation" in value or "consumer prices" in value:
            return "inflation"
        if "gdp" in value or "industrial production" in value or "retail trade" in value:
            return "growth"
        if "employment" in value or "unemployment" in value:
            return "employment"
    return None


def _utc(value):
    if value.tzinfo is None or value.utcoffset() is None:
        raise OfficialMacroError("UNKNOWN_TIMEZONE")
    return value.astimezone(timezone.utc)


def _ical_lines(payload):
    if not isinstance(payload, str) or not payload.lstrip("\ufeff\r\n ").startswith("BEGIN:VCALENDAR"):
        raise OfficialMacroError("INVALID_RESPONSE")
    lines = []
    for line in payload.replace("\r\n", "\n").split("\n"):
        if line.startswith((" ", "\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    current = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            current = {}
        elif line == "END:VEVENT" and current is not None:
            yield current
            current = None
        elif current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key] = value


def _ical_time(fields, name):
    matches = [(key, value) for key, value in fields.items() if key == name or key.startswith(name + ";")]
    if len(matches) != 1:
        return None, None
    key, raw = matches[0]
    if re.search(r"(?:^|;)VALUE=DATE(?:;|$)", key) or re.fullmatch(r"\d{8}", raw):
        if not re.fullmatch(r"\d{8}", raw):
            raise OfficialMacroError("INVALID_DATE")
        return None, datetime.strptime(raw, "%Y%m%d").date().isoformat()
    tz_match = re.search(r"TZID=([^;:]+)", key)
    try:
        if raw.endswith("Z"):
            stamp = datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        elif tz_match:
            stamp = datetime.strptime(raw, "%Y%m%dT%H%M%S").replace(tzinfo=ZoneInfo(tz_match.group(1)))
        else:
            raise OfficialMacroError("UNKNOWN_TIMEZONE")
    except (ValueError, KeyError):
        raise OfficialMacroError("INVALID_TIMESTAMP") from None
    return _utc(stamp), None


def _event(source, identity, title, event_time, event_date, fetched_at, source_time):
    category = policy_category(source, title)
    if category is None:
        return None
    stable = hashlib.sha256(f"{source.name}|{identity}".encode()).hexdigest()[:24]
    quality = {"source_url": source.url, "source_identifier": identity,
               "time_precision": "SECOND" if event_time else "DATE_ONLY",
               "event_timezone": source.timezone if event_date else "UTC" if event_time else None,
               "policy_relevance": "HIGH", "policy_classification": category,
               "importance_origin": "INTERNAL_POLICY", "source_importance": None}
    return MacroEvent("1.0", f"official:{stable}", source.name, source_time,
                      event_time, fetched_at, title, category, source.currency, None,
                      actual=None, forecast=None, previous=None, data_quality=quality,
                      known_at=fetched_at, country=source.country, fetched_at=fetched_at,
                      importance=None, consensus=None, event_date=event_date)


def parse_ics(source, payload, fetched_at):
    events = []
    for fields in _ical_lines(payload):
        title = fields.get("SUMMARY", "").replace("\\,", ",").strip()
        if policy_category(source, title) is None:
            continue
        stamp, event_date = _ical_time(fields, "DTSTART")
        if stamp is None and event_date is None:
            raise OfficialMacroError("MISSING_EVENT_TIME")
        source_stamp, _ = _ical_time(fields, "DTSTAMP") if any(k.startswith("DTSTAMP") for k in fields) else (None, None)
        identity = fields.get("UID") or f"{title}|{stamp or event_date}"
        event = _event(source, identity, title, stamp, event_date, fetched_at, source_stamp)
        if event:
            events.append(event)
    return tuple(events)


def parse_rss(source, payload, fetched_at):
    try:
        root = ET.fromstring(payload)
    except ET.ParseError:
        raise OfficialMacroError("INVALID_RESPONSE") from None
    if root.tag != "rss":
        raise OfficialMacroError("INVALID_RESPONSE")
    events = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        if policy_category(source, title) is None:
            continue
        raw = item.findtext("pubDate")
        if not raw:
            raise OfficialMacroError("MISSING_PUBLICATION_TIME")
        try:
            published = _utc(parsedate_to_datetime(raw))
        except (TypeError, ValueError):
            raise OfficialMacroError("INVALID_TIMESTAMP") from None
        identity = item.findtext("guid") or item.findtext("link") or f"{title}|{published.isoformat()}"
        events.append(_event(source, identity, title, published, None, fetched_at, published))
    return tuple(events)


def _fetch(source, timeout, max_bytes=2_000_000):
    request = Request(source.url, headers={"User-Agent": "ai-market-system-paper/1.0", "Accept": "text/calendar,application/rss+xml,application/xml,text/xml,*/*"})
    with urlopen(request, timeout=timeout) as response:
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            raise OfficialMacroError("RESPONSE_TOO_LARGE")
        return body.decode("utf-8-sig")


class OfficialMacroProvider:
    name = "official_hybrid"

    def __init__(self, *, sources=SOURCES, transport=None, clock=None,
                 cache_seconds=21600, timeout=15):
        self.sources = tuple(sources)
        self.transport = transport or _fetch
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.cache_seconds = int(cache_seconds)
        self.timeout = float(timeout)
        if not 900 <= self.cache_seconds <= 86400 or self.timeout <= 0:
            raise ValueError("invalid provider configuration")
        self._cache = {}
        self._retry_after = {}
        self.health_by_source = {source.name: "NOT_CHECKED" for source in self.sources}
        self.health = "NOT_CHECKED"

    def _source_events(self, source, now):
        cached = self._cache.get(source.name)
        if cached and now - cached[0] < timedelta(seconds=self.cache_seconds):
            return cached[1]
        if now < self._retry_after.get(source.name, datetime.min.replace(tzinfo=timezone.utc)):
            return ()
        try:
            raw = self.transport(source, self.timeout)
            events = parse_ics(source, raw, now) if source.format == "ics" else parse_rss(source, raw, now)
            self._cache[source.name] = (now, events)
            self._retry_after.pop(source.name, None)
            self.health_by_source[source.name] = "READY" if events else "EMPTY"
            return events
        except Exception as exc:
            # Deliberately no stale cache fallback or exception body/URL logging.
            self.health_by_source[source.name] = "ACCESS_DENIED" if getattr(exc, "code", None) == 403 else "RATE_LIMITED" if getattr(exc, "code", None) == 429 else "PROVIDER_FAILURE"
            self._retry_after[source.name] = now + timedelta(minutes=60)
            return ()

    def macro_events_at(self, as_of):
        as_of = _utc(as_of)
        now = _utc(self.clock())
        events = []
        for source in self.sources:
            events.extend(self._source_events(source, now))
        failed = any(state not in {"READY", "EMPTY"} for state in self.health_by_source.values())
        self.health = "PARTIAL" if failed and events else "NO_DATA" if failed else "READY" if events else "NO_DATA"
        return tuple(event for event in events if event.fetched_at <= as_of and
                     (event.source_timestamp is None or event.source_timestamp <= as_of))

    def news_items(self):
        return ()
