"""FXMacroData adapter with fetch-time and publication-time no-lookahead gates."""
from datetime import datetime, timedelta, timezone
import gzip
import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from core.contracts import MacroEvent


BASE_URL = "https://api.fxmacrodata.com/v1"
RELEVANCE_POLICY_VERSION = "fx-floor-14d-v1"
HIGH_INDICATORS = {
    "inflation": "inflation", "core_inflation": "inflation", "core_pce": "inflation",
    "ppi": "inflation", "non_farm_payrolls": "employment", "employment": "employment",
    "unemployment": "employment", "initial_jobless_claims": "employment",
    "gdp": "growth", "retail_sales": "growth", "policy_rate": "interest_rates",
    "policy_rate_mro": "interest_rates", "policy_rate_mlf": "interest_rates",
}


class FXMacroDataError(RuntimeError):
    def __init__(self, kind, *, http_status=None):
        self.kind = kind
        self.http_status = http_status
        super().__init__(kind)


def _utc_epoch(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, timezone.utc)


def _utc_iso(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp.astimezone(timezone.utc) if stamp.tzinfo and stamp.utcoffset() is not None else None


def _calendar_time_is_exact(row):
    if _utc_epoch(row.get("announcement_datetime")) is None or row.get("release_time_assumed") is True:
        return False
    status = row.get("release_time_status")
    return status == "confirmed" or (status is None and row.get("release_date_confirmed") is True)


def _http(api_key, method, path, params, body, timeout):
    query = urlencode(params or {})
    url = BASE_URL + path + ("?" + query if query else "")
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"X-API-Key": api_key, "Accept": "application/json", "Accept-Encoding": "gzip"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=data, headers=headers, method=method)
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding", "").lower() == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


class FXMacroDataProvider:
    name = "fxmacrodata"

    def __init__(self, *, api_key=None, timeout=20, cache_seconds=21600,
                 transport=None, clock=None, max_indicators_per_currency=None):
        self.api_key = api_key if api_key is not None else os.environ.get("FXMACRODATA_API_KEY")
        self.timeout = float(timeout)
        self.cache_seconds = int(cache_seconds)
        if self.timeout <= 0 or not 900 <= self.cache_seconds <= 86400:
            raise ValueError("invalid FXMacroData configuration")
        self.transport = transport or _http
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._cache = ()
        self._fetched_at = None
        self._retry_after = None
        self.max_indicators_per_currency = max_indicators_per_currency
        self.evidence_by_series = {}
        self.calendar_diagnostics = {}
        self.health = "NOT_CONFIGURED" if not self.api_key else "NOT_CHECKED"
        self.health_by_currency = {"USD": "NOT_CHECKED", "EUR": "NOT_CHECKED"}

    def _request(self, method, path, params=None, body=None):
        if not self.api_key:
            raise FXMacroDataError("NOT_CONFIGURED")
        try:
            payload = self.transport(self.api_key, method, path, params or {}, body, self.timeout)
        except HTTPError as exc:
            kind = "RATE_LIMITED" if exc.code == 429 else "AUTH_ERROR" if exc.code == 401 else "NOT_ENTITLED" if exc.code == 403 else "PROVIDER_FAILURE"
            raise FXMacroDataError(kind, http_status=exc.code) from None
        except (URLError, TimeoutError, ConnectionError):
            raise FXMacroDataError("PROVIDER_FAILURE") from None
        if not isinstance(payload, dict):
            raise FXMacroDataError("INVALID_RESPONSE")
        return payload

    @staticmethod
    def _consensus(payload, indicator, scheduled, fetched_at):
        candidates = []
        for group in payload.get("data", ()) if isinstance(payload.get("data"), list) else ():
            if group.get("indicator", indicator).lower() != indicator:
                continue
            group_time = _utc_epoch(group.get("announcement_datetime"))
            if group_time != scheduled:
                continue
            for item in group.get("predictions", ()) if isinstance(group.get("predictions"), list) else ():
                if item.get("prediction_class") not in {"compiled_consensus", "forecaster_survey"}:
                    continue
                generated = _utc_epoch(item.get("generated_at"))
                provenance = item.get("provenance") or {}
                if (generated is None or generated > fetched_at or generated >= scheduled or
                        item.get("is_pre_release") is not True or
                        provenance.get("reconstructed_with_later_data") is True or
                        item.get("event_compatible") is not True):
                    continue
                candidates.append((generated, item.get("predicted_value"), item.get("prediction_source")))
        if not candidates:
            return None, None
        _, value, source = max(candidates, key=lambda row: row[0])
        return value, source

    @staticmethod
    def _value_as_of(row, as_of):
        vintages = []
        for revision in row.get("revisions", ()) if isinstance(row.get("revisions"), list) else ():
            published = None
            ns = revision.get("publication_at_ns")
            if isinstance(ns, int):
                published = datetime.fromtimestamp(ns / 1_000_000_000, timezone.utc)
            if (revision.get("publication_time_status") == "confirmed" and published is not None and
                    published <= as_of and revision.get("val") is not None):
                vintages.append((published, revision["val"]))
        if vintages:
            return max(vintages, key=lambda item: item[0])
        published = _utc_epoch(row.get("announcement_datetime"))
        if row.get("publication_time_status") == "confirmed" and published is not None and published <= as_of:
            return published, row.get("val")
        return None, None

    @staticmethod
    def _previous(payload, fetched_at):
        rows = payload.get("data", ()) if isinstance(payload.get("data"), list) else ()
        usable = []
        for row in rows:
            published = _utc_epoch(row.get("announcement_datetime"))
            collected = _utc_iso(row.get("collected_at_iso"))
            if (row.get("publication_time_status") != "confirmed" or published is None or
                    published > fetched_at or (collected is not None and collected > fetched_at)):
                continue
            _, value = FXMacroDataProvider._value_as_of(row, fetched_at)
            if value is not None:
                usable.append((published, value, row))
        return max(usable, key=lambda item: item[0]) if usable else (None, None, None)

    def _refresh(self, now):
        start = now.date().isoformat()
        end = (now + timedelta(days=14)).date().isoformat()
        events = []
        try:
            for currency in ("USD", "EUR"):
                calendar = self._request("GET", f"/calendar/{currency.lower()}",
                                         {"start_date": start, "end_date": end, "timezone": "UTC"})
                rows = calendar.get("data")
                if not isinstance(rows, list):
                    raise FXMacroDataError("INVALID_RESPONSE")
                dated = [_utc_epoch(row.get("announcement_datetime")) for row in rows
                         if isinstance(row, dict)]
                dated = [stamp for stamp in dated if stamp is not None]
                self.calendar_diagnostics[currency] = {
                    "raw_rows": len(rows),
                    "earliest_utc": min(dated).isoformat() if dated else None,
                    "latest_utc": max(dated).isoformat() if dated else None,
                    "documented_relevant_rows": sum(
                        isinstance(row, dict) and row.get("release") in HIGH_INDICATORS for row in rows),
                    "provider_ids": sum(isinstance(row, dict) and bool(row.get("calendar_event_id")) for row in rows),
                    "confirmed_exact_rows": sum(
                        isinstance(row, dict) and _calendar_time_is_exact(row) for row in rows),
                    "release_time_statuses": sorted({str(row.get("release_time_status"))
                        for row in rows if isinstance(row, dict)}),
                    "release_dates_confirmed": sum(
                        isinstance(row, dict) and row.get("release_date_confirmed") is True for row in rows),
                    "release_times_assumed": sum(
                        isinstance(row, dict) and row.get("release_time_assumed") is True for row in rows),
                }
                relevant = [row for row in rows if (isinstance(row, dict) and row.get("release") in HIGH_INDICATORS
                            and _utc_epoch(row.get("announcement_datetime")) is not None
                            and _utc_epoch(row["announcement_datetime"]) > now
                            and _calendar_time_is_exact(row))]
                if self.max_indicators_per_currency is not None:
                    selected_names = []
                    for row in relevant:
                        if row["release"] not in selected_names:
                            selected_names.append(row["release"])
                    selected_names = selected_names[:self.max_indicators_per_currency]
                    relevant = [row for row in relevant if row["release"] in selected_names]
                self.health_by_currency[currency] = "READY" if relevant else "EMPTY"
                for row in relevant:
                    indicator = row["release"]
                    scheduled = _utc_epoch(row.get("announcement_datetime"))
                    provider_event_id = row.get("calendar_event_id")
                    if scheduled is None or scheduled <= now:
                        continue
                    event_id = (provider_event_id if isinstance(provider_event_id, str) and provider_event_id
                                else f"{currency.lower()}:{indicator}:{int(scheduled.timestamp())}")
                    announcements = self._request("GET", f"/announcements/{currency.lower()}/{indicator}",
                                                  {"limit": 5, "value_mode": "source", "series_mode": "raw", "revisions": "all"})
                    predictions = self._request("GET", f"/predictions/{currency.lower()}/{indicator}",
                                                {"start_date": start, "end_date": end, "pre_release_only": "true"})
                    self.evidence_by_series[(currency, indicator)] = (announcements, predictions)
                    previous_time, previous, prior = self._previous(announcements, now)
                    consensus, consensus_source = self._consensus(predictions, indicator, scheduled, now)
                    source_url = (prior or {}).get("announcement_source_url") or (prior or {}).get("source_url")
                    source_name = (prior or {}).get("source")
                    events.append(MacroEvent(
                        "1.0", f"fxmacrodata:{event_id}", "FXMacroData" + (f" / {source_name}" if source_name else ""),
                        None, scheduled, now, indicator.replace("_", " ").title(),
                        HIGH_INDICATORS[indicator], currency, "HIGH", None, consensus, previous,
                        data_quality={"provider": "FXMacroData", "calendar_event_id": event_id,
                                      "source_url": row.get("source_url") or source_url,
                                      "provider_calendar_event_id": provider_event_id,
                                      "event_id_origin": "PROVIDER" if provider_event_id else "DETERMINISTIC",
                                      "scheduled_timezone": "UTC",
                                      "release_time_status": row.get("release_time_status"),
                                      "importance_origin": "INTERNAL_POLICY",
                                      "relevance_policy_version": RELEVANCE_POLICY_VERSION,
                                      "consensus_source": consensus_source,
                                      "previous_release_utc": previous_time.isoformat() if previous_time else None},
                        result_timestamp=None, known_at=now,
                        country="US" if currency == "USD" else "Euro Area", fetched_at=now,
                        importance="HIGH", consensus=consensus))
        except FXMacroDataError as exc:
            self.health = exc.kind
            self._retry_after = now + timedelta(seconds=3600 if exc.kind == "RATE_LIMITED" else 300)
            raise
        self._cache = tuple(events)
        self._fetched_at = now
        self._retry_after = None
        self.health = "READY" if all(v == "READY" for v in self.health_by_currency.values()) and events else "PARTIAL" if events else "EMPTY"

    def macro_events_at(self, as_of):
        if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("aware as_of required")
        as_of = as_of.astimezone(timezone.utc)
        now = self.clock().astimezone(timezone.utc)
        if self._fetched_at is None or now - self._fetched_at >= timedelta(seconds=self.cache_seconds):
            if self._retry_after is not None and now < self._retry_after:
                if self._fetched_at is None:
                    raise FXMacroDataError(self.health)
                self.health = "PARTIAL"
            else:
                self._refresh(now)
        return tuple(event for event in self._cache if event.fetched_at <= as_of)

    def changes(self, *, since=None):
        params = {"currencies": "USD,EUR", "payload": "full", "limit": 50}
        if since:
            params["since"] = since
        return self._request("GET", "/announcements/changes", params)

    def research_panel(self, decision_times, start_date, end_date):
        body = {"series": [{"currency": c, "indicator": i} for c, i in
                           (("USD", "inflation"), ("EUR", "inflation"))],
                "decision_times": list(decision_times), "start_date": start_date,
                "end_date": end_date, "availability": "public"}
        return self._request("POST", "/research/panel", body=body)

    def news_items(self):
        return ()
