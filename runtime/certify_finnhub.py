"""One controlled Economic Calendar request; print only safe certification evidence."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json

from data.finnhub_macro_provider import FinnhubMacroDataProvider, FinnhubMacroError, _category
from runtime.env import load_local_env


def main():
    load_local_env()
    now = datetime.now(timezone.utc)
    provider = FinnhubMacroDataProvider()
    try:
        events = provider.events_at(now)
    except FinnhubMacroError as exc:
        print(json.dumps({"status": "BLOCKED", "provider_health": provider.health,
                          "error_type": exc.kind, "http_status": exc.http_status,
                          "checked_at_utc": now.isoformat()}, sort_keys=True))
        return 1
    except Exception as exc:
        print(json.dumps({"status": "BLOCKED", "provider_health": "PROVIDER_FAILURE",
                          "error_type": type(exc).__name__,
                          "checked_at_utc": now.isoformat()}, sort_keys=True))
        return 1
    by_currency = Counter(event.currency for event in events)
    relevant = tuple(event for event in events if event.currency in {"USD", "EUR"}
                     and _category(event.event) != "unknown")
    by_relevant_currency = Counter(event.currency for event in relevant)
    by_impact = Counter(event.importance for event in relevant)
    # Both enabled markets require USD releases; EURUSD also requires EUR releases.
    passed = by_relevant_currency["USD"] > 0 and by_relevant_currency["EUR"] > 0
    print(json.dumps({"status": "PASS" if passed else "BLOCKED",
                      "provider_health": provider.health,
                      "checked_at_utc": now.isoformat(),
                      "window_from_utc_date": (now - timedelta(days=1)).date().isoformat(),
                      "window_to_utc_date": (now + timedelta(days=7)).date().isoformat(),
                      "total_events": len(events),
                      "currency_counts": dict(by_currency),
                      "relevant_currency_counts": dict(by_relevant_currency),
                      "relevant_impact_counts": dict(by_impact),
                      "earliest_relevant_utc": min((event.event_time_utc for event in relevant),
                                                    default=None).isoformat() if relevant else None,
                      "latest_relevant_utc": max((event.event_time_utc for event in relevant),
                                                  default=None).isoformat() if relevant else None}, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
