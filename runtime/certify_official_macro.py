"""One bounded request per official source; print aggregate, secret-free evidence."""
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import sys

from data.official_macro_provider import OfficialMacroProvider, SOURCES


def main():
    now = datetime.now(timezone.utc)
    selected = tuple(source for source in SOURCES if len(sys.argv) < 2 or source.name == sys.argv[1])
    if not selected:
        raise SystemExit("unknown official source")
    provider = OfficialMacroProvider(clock=lambda: now, sources=selected)
    events = provider.macro_events_at(now)
    horizon = now + timedelta(days=14)
    upcoming = tuple(item for item in events if
                     (item.event_timestamp is not None and now <= item.event_timestamp <= horizon) or
                     (item.event_date is not None and now.date().isoformat() <= item.event_date <= horizon.date().isoformat()))
    released = tuple(item for item in events if item.event_timestamp is not None and item.event_timestamp <= now)
    print(json.dumps({"checked_at_utc": now.isoformat(), "health": provider.health,
                      "source_health": provider.health_by_source,
                      "available_events": len(events),
                      "upcoming_14d_by_currency": dict(Counter(item.currency for item in upcoming)),
                      "upcoming_14d_by_category_currency": dict(Counter(
                          f"{item.currency}:{item.category}" for item in upcoming)),
                      "upcoming_date_only": len([item for item in upcoming if item.event_date]),
                      "released_feed_items_by_currency": dict(Counter(item.currency for item in released)),
                      "source_importance_supplied": len([item for item in events if item.impact is not None]),
                      "actual_values_supplied": len([item for item in events if item.actual is not None]),
                      "future_fomc_schedule_feed": False,
                      "future_ecb_schedule_feed": False}, sort_keys=True))


if __name__ == "__main__":
    main()
