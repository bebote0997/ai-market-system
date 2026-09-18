"""Deterministic Macro/News scout over normalized provider data only."""
from datetime import date, datetime, timedelta, timezone
import math
from typing import Optional
from zoneinfo import ZoneInfo

from core.contracts import AgentMessage, MacroEvent, NewsItem
from data.macro_news import macro_event_to_dict, news_item_to_dict

UPCOMING_WINDOW = timedelta(hours=24)
ACTIVE_WINDOW = timedelta(hours=1)
RECENT_WINDOW = timedelta(hours=24)
IMPACT_LEVELS = {"LOW", "MEDIUM", "HIGH", "UNKNOWN"}
SUPPORTED_SYMBOLS = {"XAUUSD", "NAS100", "US100", "EURUSD"}
ALL_SYMBOLS = ("XAUUSD", "NAS100", "EURUSD")
RELEVANT_CATEGORIES = {
    "central_bank", "interest_rates", "inflation", "employment",
    "growth", "currency", "bonds/yields", "risk_event",
}


def _utc(value):
    if value is None:
        return None
    if not isinstance(value, datetime):
        try:
            value = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def _valid_source_and_time(source, timestamp):
    return isinstance(source, str) and bool(source.strip()) and timestamp is not None


def _impact(value):
    if isinstance(value, str) and value.upper() in IMPACT_LEVELS:
        return value.upper()
    return "UNKNOWN"


def _macro_relevant(event, symbol):
    category = (event.get("category") or "").lower()
    currency = (event.get("currency") or "").upper()
    if category not in RELEVANT_CATEGORIES:
        return False
    if category == "risk_event":
        return True
    if currency == "USD":
        return symbol in ALL_SYMBOLS
    if currency == "EUR":
        return symbol == "EURUSD"
    return False


def _news_relevant(item, symbol):
    symbols = {str(value).upper() for value in item.get("symbols", ())}
    relevance = {str(value).upper() for value in item.get("relevance", ())}
    return symbol in symbols or symbol in relevance or "ALL" in relevance


def _window(event_timestamp, as_of):
    if event_timestamp > as_of:
        return "UPCOMING"
    age = as_of - event_timestamp
    if age <= ACTIVE_WINDOW:
        return "ACTIVE_WINDOW"
    if age <= RECENT_WINDOW:
        return "RECENT"
    return "STALE"


def _stable_key(item, kind):
    identifier = item.get("event_id" if kind == "macro" else "news_id")
    if identifier:
        return kind, item.get("source"), identifier
    fields = ("source", "event_timestamp", "title", "currency", "category") if kind == "macro" else ("source", "published_at", "headline", "category")
    return kind, item.get("source"), tuple(str(item.get(field)) for field in fields)


def analizar_macro_news(provider, symbol, as_of, run_id="macro-news"):
    timestamp = _utc(as_of)
    if timestamp is None or not isinstance(symbol, str) or symbol.upper() not in SUPPORTED_SYMBOLS:
        return AgentMessage("1.0", run_id, datetime.now(timezone.utc), symbol or "", "context", "macro_news", "ERROR", warnings=("invalid_context",))
    symbol = symbol.upper()
    try:
        events = provider.macro_events_at(as_of) if hasattr(provider, "macro_events_at") else provider.macro_events()
        raw_events = [macro_event_to_dict(item) for item in events]
        raw_news = [news_item_to_dict(item) for item in provider.news_items()]
    except Exception as error:
        return AgentMessage("1.0", run_id, timestamp, symbol, "context", "macro_news", "ERROR", warnings=(f"provider_error:{type(error).__name__}",))

    accepted_events = []
    accepted_news = []
    warnings = []
    seen = set()
    rejected = 0
    for item in raw_events:
        key = _stable_key(item, "macro")
        if key in seen:
            warnings.append("duplicate")
            continue
        seen.add(key)
        source_time = _utc(item.get("source_timestamp"))
        event_time = _utc(item.get("event_timestamp"))
        received = _utc(item.get("received_at"))
        fetched_at = _utc(item.get("fetched_at"))
        result_time = _utc(item.get("result_timestamp"))
        known_at = _utc(item.get("known_at")) or source_time
        event_date = None
        if item.get("event_date") is not None:
            try:
                event_date = date.fromisoformat(item["event_date"])
            except (TypeError, ValueError):
                event_date = None
        date_timezone = (item.get("data_quality") or {}).get("event_timezone")
        if event_date is not None and date_timezone not in {"Europe/Luxembourg", "America/New_York"}:
            event_date = None
        if not item.get("source") or (event_time is None and event_date is None):
            rejected += 1
            warnings.append("missing_source_or_timestamp")
            continue
        if item.get("source_timestamp") is not None and source_time is None:
            rejected += 1
            warnings.append("invalid_timestamp")
            continue
        if item.get("received_at") is not None and received is None:
            rejected += 1
            warnings.append("invalid_timestamp")
            continue
        if item.get("fetched_at") is not None and fetched_at is None:
            rejected += 1
            warnings.append("invalid_timestamp")
            continue
        if any(value is not None and value > timestamp for value in
               (source_time, received, fetched_at)):
            rejected += 1
            warnings.append("future_information")
            continue
        if known_at is not None and known_at > timestamp:
            rejected += 1
            warnings.append("future_information")
            continue
        if item.get("actual") is not None and (result_time is None or result_time > timestamp):
            rejected += 1
            warnings.append("future_information")
            continue
        if not _macro_relevant(item, symbol):
            continue
        item["source_timestamp"] = source_time
        item["event_timestamp"] = event_time
        item["received_at"] = received
        item["known_at"] = known_at
        item["result_timestamp"] = result_time
        item["impact"] = (None if (item.get("data_quality") or {}).get("importance_origin") == "INTERNAL_POLICY"
                          and item.get("impact") is None else _impact(item.get("impact")))
        if event_time is None:
            days = (event_date - timestamp.astimezone(ZoneInfo(date_timezone)).date()).days
            item["window"] = "DATE_ONLY_TODAY" if days == 0 else "DATE_ONLY_UPCOMING" if 0 < days <= 1 else "DATE_ONLY_SCHEDULED" if days > 1 else "DATE_ONLY_PAST"
        else:
            item["window"] = _window(event_time, timestamp)
        accepted_events.append(item)

    seen = set()
    for item in raw_news:
        key = _stable_key(item, "news")
        if key in seen:
            warnings.append("duplicate")
            continue
        seen.add(key)
        published = _utc(item.get("published_at"))
        received = _utc(item.get("received_at"))
        known_at = _utc(item.get("known_at")) or received or published
        if not _valid_source_and_time(item.get("source"), published) or published is None or known_at is None:
            rejected += 1
            warnings.append("missing_source_or_timestamp")
            continue
        if known_at > timestamp:
            rejected += 1
            warnings.append("future_information")
            continue
        if not _news_relevant(item, symbol):
            continue
        item["published_at"] = published
        item["received_at"] = received
        item["known_at"] = known_at
        item["window"] = _window(published, timestamp)
        accepted_news.append(item)

    status = "OK" if accepted_events or accepted_news else ("PARTIAL" if rejected else "NO_DATA")
    if rejected and status == "OK":
        status = "PARTIAL"
    if getattr(provider, "health", None) == "PARTIAL":
        warnings.append("provider_partial")
        if status == "OK":
            status = "PARTIAL"
    quality = {"accepted_macro": len(accepted_events), "accepted_news": len(accepted_news), "rejected": rejected}
    evidence = ({"macro_events": tuple(accepted_events), "news_items": tuple(accepted_news)},)
    return AgentMessage("1.0", run_id, timestamp, symbol, "context", "macro_news", status, evidence=evidence, data_quality=quality, warnings=tuple(dict.fromkeys(warnings)))
