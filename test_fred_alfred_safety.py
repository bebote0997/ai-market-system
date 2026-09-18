"""Date-only FRED and vintage ALFRED evidence cannot cross the macro gate."""
import unittest
from datetime import datetime, timezone

from agents.macro_news_agent import analizar_macro_news
from core.contracts import MacroEvent
from data.macro_news import InMemoryMacroNewsProvider


UTC = timezone.utc
AS_OF = datetime(2026, 9, 17, 13, 30, tzinfo=UTC)


def candidate(**changes):
    item = dict(schema_version="1.0", event_id="fred:release:9:2026-09-18",
                source="FRED", source_timestamp=None, event_timestamp=None,
                received_at=AS_OF, title="Advance Monthly Sales for Retail and Food Services",
                category="growth", currency="USD", impact=None, actual=None,
                forecast=None, previous=None,
                data_quality={"release_date": "2026-09-18", "precision": "DATE_ONLY"})
    item.update(changes)
    return MacroEvent(**item)


class FredAlfredSafety(unittest.TestCase):
    def test_date_only_future_release_does_not_invent_intraday_timestamp(self):
        report = analizar_macro_news(InMemoryMacroNewsProvider([candidate()]), "XAUUSD", AS_OF)
        self.assertEqual(report.status, "PARTIAL")
        self.assertEqual(report.data_quality["accepted_macro"], 0)
        self.assertIn("missing_source_or_timestamp", report.warnings)

    def test_alfred_revised_value_needs_proven_publication_time(self):
        event = candidate(event_timestamp=datetime(2026, 9, 17, 12, tzinfo=UTC),
                          actual="101.2", result_timestamp=None,
                          data_quality={"vintage_date": "2026-09-17", "revision": True})
        report = analizar_macro_news(InMemoryMacroNewsProvider([event]), "EURUSD", AS_OF)
        self.assertEqual(report.data_quality["accepted_macro"], 0)
        self.assertIn("future_information", report.warnings)

    def test_later_vintage_and_fetch_cannot_leak_to_earlier_as_of(self):
        late = datetime(2026, 9, 18, 8, tzinfo=UTC)
        event = candidate(event_timestamp=datetime(2026, 9, 17, 12, tzinfo=UTC),
                          actual="101.2", result_timestamp=late, known_at=late,
                          fetched_at=late,
                          data_quality={"vintage_date": "2026-09-18", "revision": True})
        report = analizar_macro_news(InMemoryMacroNewsProvider([event]), "XAUUSD", AS_OF)
        self.assertEqual(report.data_quality["accepted_macro"], 0)
        self.assertIn("future_information", report.warnings)

    def test_future_received_or_source_update_is_rejected(self):
        event_time = datetime(2026, 9, 18, 12, tzinfo=UTC)
        late = datetime(2026, 9, 18, 8, tzinfo=UTC)
        for changes in ({"received_at": late}, {"source_timestamp": late},
                        {"fetched_at": late}):
            with self.subTest(changes=changes):
                report = analizar_macro_news(InMemoryMacroNewsProvider([
                    candidate(event_timestamp=event_time, **changes)]), "XAUUSD", AS_OF)
                self.assertEqual(report.data_quality["accepted_macro"], 0)
                self.assertIn("future_information", report.warnings)


if __name__ == "__main__":
    unittest.main()
