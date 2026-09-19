"""Controlled FXMacroData certification; output contains no credentials or raw payloads."""
from datetime import datetime, timedelta, timezone
import json

from agents.macro_news_agent import analizar_macro_news
from data.fxmacrodata_provider import FXMacroDataError, FXMacroDataProvider, HIGH_INDICATORS, _utc_epoch
from runtime.env import load_local_env


def _calendar(provider, currency, now, end):
    payload = provider._request("GET", f"/calendar/{currency.lower()}",
                                {"start_date": now.date().isoformat(),
                                 "end_date": end.date().isoformat(), "timezone": "UTC"})
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    valid = [row for row in rows if (isinstance(row, dict) and row.get("release") in HIGH_INDICATORS
             and _utc_epoch(row.get("announcement_datetime")) is not None
             and now < _utc_epoch(row["announcement_datetime"]) <= end
             and isinstance(row.get("calendar_event_id"), str))]
    return payload, valid


def _actual_evidence(payload, now):
    rows = payload.get("data") if isinstance(payload.get("data"), list) else []
    confirmed = [row for row in rows if (isinstance(row, dict) and row.get("val") is not None
                 and row.get("publication_time_status") == "confirmed"
                 and _utc_epoch(row.get("announcement_datetime")) is not None
                 and _utc_epoch(row["announcement_datetime"]) <= now
                 and isinstance(row.get("announcement_id"), str)
                 and (row.get("announcement_source_url") or row.get("source_url")))]
    revisions = sum(bool(row.get("revisions")) for row in confirmed)
    return len(confirmed), revisions


def _prediction_evidence(payload, now):
    safe = consensus = 0
    for group in payload.get("data", ()) if isinstance(payload.get("data"), list) else ():
        for item in group.get("predictions", ()) if isinstance(group.get("predictions"), list) else ():
            generated = _utc_epoch(item.get("generated_at"))
            provenance = item.get("provenance") or {}
            if (generated is not None and generated <= now and item.get("is_pre_release") is True
                    and provenance.get("reconstructed_with_later_data") is not True):
                safe += 1
                consensus += item.get("prediction_class") in {"compiled_consensus", "forecaster_survey"}
    return safe, consensus


def certify(provider, now):
    now = now.astimezone(timezone.utc)
    end = now + timedelta(days=14)
    result = {"status": "BLOCKED", "checked_at_utc": now.isoformat(),
              "window_end_utc": end.isoformat(), "provider": "FXMacroData"}
    try:
        runtime_events = provider.macro_events_at(now)
        calendars = {currency: {"future_relevant": sum(e.currency == currency for e in runtime_events),
                                "exact_utc": sum(e.currency == currency and e.event_timestamp is not None for e in runtime_events),
                                "stable_ids": sum(e.currency == currency and bool(e.event_id) for e in runtime_events),
                                "indicators": sorted({e.title for e in runtime_events if e.currency == currency}),
                                **provider.calendar_diagnostics.get(currency, {})}
                     for currency in ("USD", "EUR")}
        endpoint_checks = {}
        for (currency, indicator), (announcements, predictions) in provider.evidence_by_series.items():
            actuals, revisions = _actual_evidence(announcements, now)
            forecasts, consensus = _prediction_evidence(predictions, now)
            endpoint_checks[currency] = {"indicator": indicator, "confirmed_actual_rows": actuals,
                                         "rows_with_revisions": revisions,
                                         "safe_pre_release_forecasts": forecasts,
                                         "consensus_forecasts": consensus,
                                         "announcement_id_present": any(bool(x.get("announcement_id")) for x in announcements.get("data", []))}
        changes = provider.changes()
        change_actuals = {currency: 0 for currency in ("USD", "EUR")}
        for change in changes.get("data", ()) if isinstance(changes.get("data"), list) else ():
            currency = str(change.get("currency", "")).upper()
            latest = change.get("latest_announcement") or {}
            released = _utc_epoch(change.get("release_timestamp")) or _utc_epoch(latest.get("announcement_datetime"))
            if (currency in change_actuals and latest.get("val") is not None and released is not None
                    and released <= now and (latest.get("release_url") or latest.get("source_url"))):
                change_actuals[currency] += 1
        decision = (now - timedelta(minutes=1)).isoformat()
        panel = provider.research_panel([decision], (now - timedelta(days=45)).date().isoformat(), now.date().isoformat())
        reports = {symbol: analizar_macro_news(provider, symbol, now, f"fxmd-cert-{symbol}")
                   for symbol in ("XAUUSD", "EURUSD")}
        checks = {
            "usd_future": calendars.get("USD", {}).get("future_relevant", 0) > 0,
            "eur_future": calendars.get("EUR", {}).get("future_relevant", 0) > 0,
            "calendar_exact_utc": all(v["future_relevant"] == v["exact_utc"] for v in calendars.values()),
            "stable_calendar_ids": all(v["future_relevant"] == v["stable_ids"] for v in calendars.values()),
            "confirmed_actual_and_source": all(v["confirmed_actual_rows"] > 0 or change_actuals[c] > 0
                                               for c, v in endpoint_checks.items()),
            "announcement_ids": all(v["announcement_id_present"] for v in endpoint_checks.values()),
            "changes_contract": isinstance(changes.get("next_cursor"), str) and isinstance(changes.get("retention_seconds"), int),
            "research_panel": panel.get("complete") is True and panel.get("value_mode") == "source",
            "macro_provider_ready": provider.health == "READY" and {e.currency for e in runtime_events} == {"USD", "EUR"},
            "macro_agent_grounded": all(report.status == "OK" for report in reports.values()),
        }
        result.update({"status": "PASS" if all(checks.values()) else "BLOCKED",
                       "provider_health": provider.health, "checks": checks,
                       "calendar": calendars, "evidence": endpoint_checks,
                       "runtime_event_count": len(runtime_events),
                       "macro_agent": {symbol: report.status for symbol, report in reports.items()},
                       "panel_status": panel.get("status"), "panel_complete": panel.get("complete"),
                       "panel_availability": panel.get("availability"),
                       "changes_count": changes.get("count", len(changes.get("data", [])))})
        result["confirmed_change_actuals"] = change_actuals
    except FXMacroDataError as exc:
        result.update({"error_type": exc.kind, "http_status": exc.http_status})
    except Exception as exc:
        result.update({"error_type": type(exc).__name__})
    return result


def main():
    load_local_env()
    observed_at = datetime.now(timezone.utc)
    result = certify(FXMacroDataProvider(max_indicators_per_currency=1,
                                        clock=lambda: observed_at), observed_at)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
