import unittest
from datetime import datetime, timedelta, timezone

from agents.macro_news_agent import analizar_macro_news
from core.contracts import MacroEvent, NewsItem
from data.macro_news import InMemoryMacroNewsProvider


UTC = timezone.utc


class TestMacroNewsAgent(unittest.TestCase):
    def event(self, **kwargs):
        values = {
            "schema_version": "1.0", "event_id": "e1", "source": "calendar",
            "source_timestamp": datetime(2026, 1, 1, 10, tzinfo=UTC),
            "event_timestamp": datetime(2026, 1, 1, 12, tzinfo=UTC),
            "received_at": datetime(2026, 1, 1, 10, 30, tzinfo=UTC),
            "title": "USD event", "category": "inflation", "currency": "USD",
            "impact": None, "actual": None, "forecast": None, "previous": None,
            "data_quality": {},
        }
        values.update(kwargs)
        return MacroEvent(**values)

    def news(self, **kwargs):
        values = {
            "schema_version": "1.0", "news_id": "n1", "source": "wire",
            "published_at": datetime(2026, 1, 1, 11, tzinfo=UTC),
            "received_at": datetime(2026, 1, 1, 11, 30, tzinfo=UTC),
            "headline": "Market headline", "category": "risk_event",
            "symbols": ("XAUUSD",), "relevance": (), "data_quality": {},
        }
        values.update(kwargs)
        return NewsItem(**values)

    def as_of(self, hour=13):
        return datetime(2026, 1, 1, 13, tzinfo=UTC) + timedelta(hours=hour - 13)

    def test_valid_macro_and_news_ok(self):
        report = analizar_macro_news(InMemoryMacroNewsProvider([self.event()], [self.news()]), "XAUUSD", self.as_of())
        self.assertEqual(report.status, "OK")
        self.assertEqual(len(report.evidence[0]["macro_events"]), 1)
        self.assertEqual(len(report.evidence[0]["news_items"]), 1)

    def test_missing_source_or_timestamp_is_rejected(self):
        bad_source = self.event(source="")
        bad_time = self.event(event_id="e2", event_timestamp=None)
        report = analizar_macro_news(InMemoryMacroNewsProvider([bad_source, bad_time]), "XAUUSD", self.as_of())
        self.assertEqual(report.status, "PARTIAL")
        self.assertGreaterEqual(report.data_quality["rejected"], 2)

    def test_utc_normalization_and_invalid_timezone(self):
        event = self.event(source_timestamp=datetime(2026, 1, 1, 10, tzinfo=timezone(timedelta(hours=-2))))
        report = analizar_macro_news(InMemoryMacroNewsProvider([event]), "XAUUSD", self.as_of())
        self.assertEqual(report.evidence[0]["macro_events"][0]["source_timestamp"].tzinfo, UTC)
        bad = self.event(source_timestamp=datetime(2026, 1, 1, 10))
        self.assertEqual(analizar_macro_news(InMemoryMacroNewsProvider([bad]), "XAUUSD", self.as_of()).status, "PARTIAL")

    def test_future_information_and_actual_gate(self):
        future_received = self.event(received_at=self.as_of(14))
        future_actual = self.event(event_id="future-result", event_timestamp=self.as_of(14), actual=1.2)
        report = analizar_macro_news(InMemoryMacroNewsProvider([future_received, future_actual]), "EURUSD", self.as_of())
        self.assertEqual(report.status, "PARTIAL")
        self.assertIn("future_information", report.warnings)

    def test_windows(self):
        upcoming = self.event(event_timestamp=self.as_of(14), received_at=self.as_of(10))
        active = self.event(event_id="e2", event_timestamp=self.as_of(12, ), received_at=self.as_of(10))
        recent = self.event(event_id="e3", event_timestamp=self.as_of(-10), received_at=self.as_of(-10))
        stale = self.event(event_id="e4", event_timestamp=self.as_of(-48), received_at=self.as_of(-48))
        evidence = analizar_macro_news(InMemoryMacroNewsProvider([upcoming, active, recent, stale]), "XAUUSD", self.as_of()).evidence[0]["macro_events"]
        self.assertEqual({item["window"] for item in evidence}, {"UPCOMING", "ACTIVE_WINDOW", "RECENT", "STALE"})

    def test_impact_unknown_is_not_invented(self):
        item = analizar_macro_news(InMemoryMacroNewsProvider([self.event(impact=None)]), "XAUUSD", self.as_of()).evidence[0]["macro_events"][0]
        self.assertEqual(item["impact"], "UNKNOWN")

    def test_relevance_by_symbol_and_currency(self):
        usd = self.event()
        eur = self.event(event_id="e2", currency="EUR")
        gold = analizar_macro_news(InMemoryMacroNewsProvider([usd, eur]), "XAUUSD", self.as_of()).evidence[0]["macro_events"]
        euro = analizar_macro_news(InMemoryMacroNewsProvider([usd, eur]), "EURUSD", self.as_of()).evidence[0]["macro_events"]
        self.assertEqual(len(gold), 1)
        self.assertEqual(len(euro), 2)

    def test_deduplication_macro_and_news(self):
        event = self.event()
        news = self.news()
        report = analizar_macro_news(InMemoryMacroNewsProvider([event, event], [news, news]), "XAUUSD", self.as_of())
        self.assertEqual(len(report.evidence[0]["macro_events"]), 1)
        self.assertEqual(len(report.evidence[0]["news_items"]), 1)
        self.assertIn("duplicate", report.warnings)

    def test_same_provider_id_different_provider_id_is_not_global_duplicate(self):
        first = self.event(source="calendar-a")
        second = self.event(source="calendar-b")
        report = analizar_macro_news(InMemoryMacroNewsProvider([first, second]), "XAUUSD", self.as_of())
        self.assertEqual(len(report.evidence[0]["macro_events"]), 2)

    def test_same_headline_different_timestamp_not_deduped(self):
        first = self.news()
        second = self.news(news_id=None, published_at=self.as_of(12))
        report = analizar_macro_news(InMemoryMacroNewsProvider(news=[first, second]), "XAUUSD", self.as_of(13))
        self.assertEqual(len(report.evidence[0]["news_items"]), 2)

    def test_provider_empty_error_and_partial(self):
        self.assertEqual(analizar_macro_news(InMemoryMacroNewsProvider(), "XAUUSD", self.as_of()).status, "NO_DATA")
        self.assertEqual(analizar_macro_news(InMemoryMacroNewsProvider(error=RuntimeError()), "XAUUSD", self.as_of()).status, "ERROR")
        report = analizar_macro_news(InMemoryMacroNewsProvider([self.event(source="")], [self.news()]), "XAUUSD", self.as_of())
        self.assertEqual(report.status, "PARTIAL")

    def test_symbols_and_contexts(self):
        report = analizar_macro_news(InMemoryMacroNewsProvider([self.event()], [self.news()]), "NAS100", self.as_of())
        self.assertEqual(report.symbol, "NAS100")
        self.assertEqual(report.agent, "macro_news")
        self.assertEqual(report.schema_version, "1.0")

    def test_invalid_symbol_and_as_of(self):
        self.assertEqual(analizar_macro_news(InMemoryMacroNewsProvider(), "BTC", self.as_of()).status, "ERROR")
        self.assertEqual(analizar_macro_news(InMemoryMacroNewsProvider(), "XAUUSD", "bad").status, "ERROR")

    def test_deterministic_same_input_content(self):
        provider = InMemoryMacroNewsProvider([self.event()], [self.news()])
        first = analizar_macro_news(provider, "XAUUSD", self.as_of())
        second = analizar_macro_news(provider, "XAUUSD", self.as_of())
        self.assertEqual(first.evidence, second.evidence)
        self.assertEqual(first.data_quality, second.data_quality)


if __name__ == "__main__":
    unittest.main()
