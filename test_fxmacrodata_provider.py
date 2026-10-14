import unittest
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError

from agents.macro_news_agent import analizar_macro_news
from data.fxmacrodata_provider import FXMacroDataError, FXMacroDataProvider, _calendar_time_is_exact


UTC = timezone.utc
NOW = datetime(2026, 9, 19, 12, tzinfo=UTC)
FUTURE = int((NOW + timedelta(days=2)).timestamp())
PAST = int((NOW - timedelta(days=30)).timestamp())


class FixtureTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, key, method, path, params, body, timeout):
        self.calls.append((method, path, params, body))
        if path.startswith("/calendar/"):
            currency = path.rsplit("/", 1)[1]
            return {"currency": currency.upper(), "data": [{
                "announcement_datetime": FUTURE, "release": "inflation",
                "release_time_status": "confirmed", "release_time_assumed": False,
                "calendar_event_id": f"{currency}_inflation_{FUTURE}"}]}
        if path.startswith("/announcements/") and path != "/announcements/changes":
            currency = path.split("/")[2]
            return {"data": [{"announcement_id": f"{currency}_inflation_2026-08-01",
                              "announcement_datetime": PAST, "publication_time_status": "confirmed",
                              "val": 2.4, "source": "Official statistics office",
                              "announcement_source_url": "https://official.example/release",
                              "collected_at_iso": (NOW - timedelta(days=29)).isoformat(),
                              "revisions": []}]}
        if path.startswith("/predictions/"):
            currency = path.split("/")[2]
            return {"data": [{"announcement_id": f"{currency}_inflation_2026-09-21",
                              "currency": currency.upper(), "indicator": "inflation",
                              "announcement_datetime": FUTURE, "predictions": [{
                                  "predicted_value": 2.5, "event_compatible": True,
                                  "prediction_class": "compiled_consensus", "prediction_source": "survey",
                                  "generated_at": int((NOW - timedelta(days=1)).timestamp()),
                                  "is_pre_release": True,
                                  "provenance": {"reconstructed_with_later_data": False}}]}]}
        if path == "/announcements/changes":
            return {"data": [], "next_cursor": "cursor", "has_more": False, "retention_seconds": 3600}
        if path == "/research/panel":
            return {"status": "ok", "complete": True, "availability": "public",
                    "value_mode": "source", "dataset_version": "a" * 64,
                    "value_metadata": {}, "series": body["series"], "data": [],
                    "quality": {"point_in_time_safe": True}, "limits": {}}
        raise AssertionError(path)


class FXMacroDataSafety(unittest.TestCase):
    def test_refresh_boundary_preserves_prior_vintage_without_lookahead(self):
        clock = [NOW]
        transport = FixtureTransport()
        value = [2.5]

        def changing(*args):
            payload = transport(*args)
            if args[2].startswith("/predictions/"):
                payload["data"][0]["predictions"][0]["predicted_value"] = value[0]
            return payload

        provider = FXMacroDataProvider(api_key="fixture", transport=changing, clock=lambda: clock[0])
        original = provider.macro_events_at(NOW)
        slot = NOW + timedelta(hours=6)
        clock[0] = slot + timedelta(seconds=2)
        value[0] = 9.0
        at_boundary = provider.macro_events_at(slot)
        self.assertEqual(at_boundary, original)
        self.assertEqual(analizar_macro_news(provider, "XAUUSD", slot).status, "OK")
        self.assertTrue(all(event.known_at == NOW for event in at_boundary))
        visible = provider.macro_events_at(clock[0])
        self.assertEqual(len(visible), 2)  # one latest vintage per event, not duplicates
        self.assertTrue(all(event.consensus == 9.0 and event.known_at == clock[0] and
                            event.fetched_at == clock[0] for event in visible))
        self.assertEqual(provider.macro_events_at(slot), original)
        self.assertEqual(provider.macro_events_at(NOW - timedelta(microseconds=1)), ())
        clock[0] += timedelta(hours=6)
        provider.macro_events_at(clock[0])
        self.assertEqual(len(provider._previous_cache), 2)  # bounded to two snapshots
        self.assertEqual(len(provider._cache), 2)

    def test_new_events_are_known_only_after_fetch_completes(self):
        clock = [NOW]
        transport = FixtureTransport()

        def delayed(*args):
            result = transport(*args)
            clock[0] += timedelta(seconds=1)
            return result

        provider = FXMacroDataProvider(api_key="fixture", transport=delayed, clock=lambda: clock[0])
        self.assertEqual(provider.macro_events_at(NOW), ())  # no previous snapshot on cold start
        events = provider.macro_events_at(clock[0])
        self.assertEqual(len(events), 2)
        self.assertTrue(all(event.fetched_at == clock[0] and event.known_at == clock[0] and
                            event.received_at == clock[0] for event in events))

    def test_exact_six_hour_refresh_and_new_event_visibility(self):
        clock = [NOW]
        transport = FixtureTransport()

        def calendar_changes(*args):
            result = transport(*args)
            if args[2].startswith("/calendar/") and clock[0] >= NOW + timedelta(hours=6):
                new = dict(result["data"][0])
                new["calendar_event_id"] += "_new"
                result["data"].append(new)
            return result

        provider = FXMacroDataProvider(api_key="fixture", transport=calendar_changes, clock=lambda: clock[0])
        original = provider.macro_events_at(NOW)
        calls = len(transport.calls)
        clock[0] = NOW + timedelta(hours=6) - timedelta(microseconds=1)
        self.assertEqual(provider.macro_events_at(clock[0]), original)
        self.assertEqual(len(transport.calls), calls)
        clock[0] += timedelta(microseconds=1)
        self.assertEqual(provider.macro_events_at(clock[0] - timedelta(microseconds=1)), original)
        self.assertGreater(len(transport.calls), calls)
        self.assertEqual(len(provider.macro_events_at(clock[0])), 4)

    def test_future_usd_eur_events_are_utc_grounded_and_enriched(self):
        transport = FixtureTransport()
        provider = FXMacroDataProvider(api_key="fixture", transport=transport, clock=lambda: NOW)
        events = provider.macro_events_at(NOW)
        self.assertEqual({event.currency for event in events}, {"USD", "EUR"})
        self.assertTrue(all(event.event_timestamp.tzinfo == UTC for event in events))
        self.assertTrue(all(event.actual is None for event in events))
        self.assertTrue(all(event.previous == 2.4 and event.consensus == 2.5 for event in events))
        self.assertTrue(all(event.impact == "HIGH" for event in events))
        self.assertEqual(provider.health, "READY")
        self.assertEqual(analizar_macro_news(provider, "EURUSD", NOW).status, "OK")

    def test_fetch_time_boundary_prevents_historical_reconstruction(self):
        provider = FXMacroDataProvider(api_key="fixture", transport=FixtureTransport(), clock=lambda: NOW)
        provider.macro_events_at(NOW)
        self.assertEqual(provider.macro_events_at(NOW - timedelta(microseconds=1)), ())

    def test_forecast_must_be_pre_release_compatible_and_not_reconstructed(self):
        payload = {"data": [{"indicator": "inflation", "announcement_datetime": FUTURE,
                             "predictions": [{"predicted_value": 9, "event_compatible": True,
                                "prediction_class": "compiled_consensus", "generated_at": FUTURE + 1,
                                "is_pre_release": False, "provenance": {"reconstructed_with_later_data": False}}]}]}
        self.assertEqual(FXMacroDataProvider._consensus(payload, "inflation",
                                                       datetime.fromtimestamp(FUTURE, UTC), NOW), (None, None))

    def test_revision_selection_uses_only_confirmed_vintage_known_as_of(self):
        first = NOW - timedelta(days=10)
        later = NOW + timedelta(days=1)
        row = {"announcement_datetime": int(first.timestamp()), "publication_time_status": "confirmed", "val": 1.0,
               "revisions": [
                   {"val": 1.1, "publication_at_ns": int(first.timestamp() * 1e9),
                    "publication_time_status": "confirmed"},
                   {"val": 1.2, "publication_at_ns": int(later.timestamp() * 1e9),
                    "publication_time_status": "confirmed"}]}
        self.assertEqual(FXMacroDataProvider._value_as_of(row, NOW)[1], 1.1)
        self.assertEqual(FXMacroDataProvider._value_as_of(row, later)[1], 1.2)

    def test_unconfirmed_publication_is_fail_closed(self):
        payload = {"data": [{"announcement_datetime": PAST, "publication_time_status": "unverified", "val": 2.0}]}
        self.assertEqual(FXMacroDataProvider._previous(payload, NOW), (None, None, None))

    def test_calendar_exactness_accepts_confirmed_date_but_rejects_assumed_time(self):
        row = {"announcement_datetime": FUTURE, "release_date_confirmed": True,
               "release_time_status": None, "release_time_assumed": False}
        self.assertTrue(_calendar_time_is_exact(row))
        row["release_time_assumed"] = True
        self.assertFalse(_calendar_time_is_exact(row))
        self.assertFalse(_calendar_time_is_exact({"release_date_confirmed": True}))

    def test_cache_avoids_scheduler_requests_and_support_endpoints(self):
        transport = FixtureTransport()
        provider = FXMacroDataProvider(api_key="fixture", transport=transport, clock=lambda: NOW)
        provider.macro_events_at(NOW)
        count = len(transport.calls)
        provider.macro_events_at(NOW)
        self.assertEqual(len(transport.calls), count)
        self.assertFalse(provider.changes()["has_more"])
        panel = provider.research_panel([NOW.isoformat()], "2026-09-01", "2026-09-19")
        self.assertTrue(panel["quality"]["point_in_time_safe"])

    def test_auth_entitlement_rate_limit_are_classified(self):
        for status, expected in ((401, "AUTH_ERROR"), (403, "NOT_ENTITLED"), (429, "RATE_LIMITED"), (500, "PROVIDER_FAILURE")):
            def failed(*_, code=status):
                raise HTTPError("sanitized", code, "error", {}, None)
            provider = FXMacroDataProvider(api_key="fixture", transport=failed, clock=lambda: NOW)
            with self.assertRaises(FXMacroDataError) as caught:
                provider.macro_events_at(NOW)
            self.assertEqual(caught.exception.kind, expected)


if __name__ == "__main__":
    unittest.main()
