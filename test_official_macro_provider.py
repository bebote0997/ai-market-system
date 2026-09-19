"""Deterministic official feed normalization; zero API requests."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from uuid import uuid4

from agents.macro_news_agent import analizar_macro_news
from data.official_macro_provider import OfficialMacroProvider, OfficialSource, parse_ics, parse_rss
from runtime.review import build_review
from storage.database import Store


UTC = timezone.utc
NOW = datetime(2026, 9, 17, 15, 0, tzinfo=UTC)
BLS = OfficialSource("BLS", "https://www.bls.gov/schedule/news_release/bls.ics", "USD", "US", "ics", "America/New_York")
BEA = OfficialSource("BEA", "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics", "USD", "US", "ics", "America/New_York")
EURO = OfficialSource("Eurostat", "https://ec.europa.eu/eurostat/o/calendars/eventsIcal?theme=&category=", "EUR", "EA", "ics", "Europe/Luxembourg")
FED = OfficialSource("Federal Reserve", "https://www.federalreserve.gov/feeds/press_monetary.xml", "USD", "US", "rss")


def ics(title, start, *, stamp="20260917T120000Z", uid="event-1"):
    return ("BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nSUMMARY:{title}\r\nDTSTART{start}\r\n"
            f"DTSTAMP:{stamp}\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")


class OfficialMacroSafety(unittest.TestCase):
    def test_bls_explicit_timezone_dst_and_no_synthetic_values(self):
        payload = ics("Consumer Price Index", ";TZID=America/New_York:20260918T083000")
        event = parse_ics(BLS, payload, NOW)[0]
        self.assertEqual(event.event_timestamp, datetime(2026, 9, 18, 12, 30, tzinfo=UTC))
        self.assertEqual(event.source_timestamp, datetime(2026, 9, 17, 12, tzinfo=UTC))
        self.assertEqual(event.category, "inflation")
        self.assertIsNone(event.actual)
        self.assertIsNone(event.previous)
        self.assertIsNone(event.forecast)
        self.assertIsNone(event.impact)
        self.assertEqual(event.data_quality["policy_relevance"], "HIGH")
        self.assertEqual(event.data_quality["source_importance"], None)

    def test_bea_gdp_and_pce_release_hours(self):
        first = parse_ics(BEA, ics("Gross Domestic Product, Third Estimate", ";VALUE=DATE-TIME:20260930T123000Z"), NOW)[0]
        second = parse_ics(BEA, ics("Personal Income and Outlays", ":20260930T123000Z", uid="pce"), NOW)[0]
        self.assertEqual(first.category, "growth")
        self.assertEqual(parse_ics(BEA, ics("GDP (Third Estimate), Industries, Corporate Profits, State GDP", ":20260930T123000Z"), NOW)[0].category, "growth")
        self.assertEqual(second.category, "inflation")
        self.assertEqual(first.event_timestamp.hour, 12)

    def test_eurostat_date_only_stays_date_only_in_macro_agent(self):
        event = parse_ics(EURO, ics("Inflation, euro area", ";VALUE=DATE:20260918"), NOW)[0]
        self.assertIsNone(event.event_timestamp)
        self.assertEqual(event.event_date, "2026-09-18")
        provider = SimpleNamespace(macro_events_at=lambda _: (event,), news_items=lambda: (), health="READY")
        report = analizar_macro_news(provider, "EURUSD", NOW, "run")
        self.assertEqual(report.status, "OK")
        item = report.evidence[0]["macro_events"][0]
        self.assertEqual(item["window"], "DATE_ONLY_UPCOMING")
        self.assertIsNone(item["event_timestamp"])
        self.assertIsNone(item["impact"])

    def test_rss_publication_boundary_and_review_provenance(self):
        rss = """<rss><channel><item><title>Federal Reserve issues FOMC statement</title>
        <guid>https://www.federalreserve.gov/release</guid>
        <pubDate>Thu, 17 Sep 2026 18:00:00 GMT</pubDate></item></channel></rss>"""
        fetched = datetime(2026, 9, 17, 18, 1, tzinfo=UTC)
        event = parse_rss(FED, rss, fetched)[0]
        provider = SimpleNamespace(macro_events_at=lambda _: (event,), news_items=lambda: (), health="READY")
        before = analizar_macro_news(provider, "XAUUSD", datetime(2026, 9, 17, 18, 0, tzinfo=UTC), "run")
        self.assertEqual(before.data_quality["accepted_macro"], 0)
        after = analizar_macro_news(provider, "XAUUSD", fetched, "run")
        self.assertEqual(after.status, "OK")
        deterministic = SimpleNamespace(setup_assessment=SimpleNamespace(status="NO_SETUP"),
                                        risk_decision=None, macro_news_report=after)
        ai = SimpleNamespace(run_id="run", symbol="XAUUSD", as_of=fetched,
                             final_status="NO_SETUP", warnings=(), ai_structure=None,
                             ai_liquidity=None, ai_macro=None, ai_setup_review=None,
                             ai_trade_review=None)
        review = build_review("slot", deterministic, ai)
        self.assertEqual(review.macro_evidence[0]["event_id"], event.event_id)
        self.assertEqual(review.macro_evidence[0]["data_quality"]["source_url"], FED.url)
        self.assertIsNone(review.macro_evidence[0]["impact"])
        self.assertEqual(review.macro_evidence[0]["source_timestamp"], event.source_timestamp)

    def test_future_or_imprecise_update_is_not_accepted(self):
        future_stamp = ics("Consumer Price Index", ";TZID=America/New_York:20260918T083000",
                           stamp="20260919T120000Z")
        event = parse_ics(BLS, future_stamp, NOW)[0]
        provider = SimpleNamespace(macro_events_at=lambda _: (event,), news_items=lambda: (), health="READY")
        report = analizar_macro_news(provider, "XAUUSD", NOW)
        self.assertEqual(report.data_quality["accepted_macro"], 0)
        bad_time = ics("Consumer Price Index", ":20260918T083000")
        with self.assertRaisesRegex(RuntimeError, "UNKNOWN_TIMEZONE"):
            parse_ics(BLS, bad_time, NOW)

    def test_cache_negative_backoff_and_partial_source_health(self):
        clock = [NOW]
        calls = []
        payload = ics("Gross Domestic Product", ":20260930T123000Z")
        def transport(source, _timeout):
            calls.append(source.name)
            if source.name == "BLS":
                raise HTTPError(source.url, 403, "denied", {}, None)
            return payload
        provider = OfficialMacroProvider(sources=(BLS, BEA), transport=transport,
                                         clock=lambda: clock[0], cache_seconds=3600)
        report = analizar_macro_news(provider, "XAUUSD", NOW)
        self.assertEqual(report.status, "PARTIAL")
        self.assertEqual(provider.health_by_source["BLS"], "ACCESS_DENIED")
        self.assertEqual(provider.health_by_source["BEA"], "READY")
        clock[0] += timedelta(minutes=15)
        analizar_macro_news(provider, "XAUUSD", clock[0])
        self.assertEqual(calls, ["BLS", "BEA"])

    def test_all_sources_failed_yields_no_data_and_no_fabricated_events(self):
        provider = OfficialMacroProvider(sources=(BLS,), clock=lambda: NOW,
            transport=lambda source, timeout: (_ for _ in ()).throw(HTTPError(source.url, 403, "denied", {}, None)))
        report = analizar_macro_news(provider, "XAUUSD", NOW)
        self.assertEqual(provider.health, "NO_DATA")
        self.assertEqual(report.status, "NO_DATA")
        self.assertEqual(report.data_quality["accepted_macro"], 0)

    def test_internal_policy_alert_is_distinct_from_source_importance(self):
        path = Path("data/runtime") / f"official-macro-{uuid4().hex}.db"
        store = Store(path)
        try:
            event = parse_ics(EURO, ics("Inflation, euro area", ";VALUE=DATE:20260918"), NOW)[0]
            provider = SimpleNamespace(macro_events_at=lambda _: (event,), news_items=lambda: (), health="READY")
            report = analizar_macro_news(provider, "EURUSD", NOW, "run")
            store.record_macro_awareness(NOW, "run", "EURUSD", report.evidence[0]["macro_events"])
            self.assertEqual(len(store.journal(event="MACRO_HIGH_RELEVANCE")), 1)
            self.assertEqual(len(store.journal(event="MACRO_HIGH_IMPORTANCE")), 0)
        finally:
            store.close()
            for suffix in ("", "-wal", "-shm"):
                path.with_name(path.name + suffix).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
