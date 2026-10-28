"""DEC-DEMO-READ-RETRY-1: the dashboard's trade-derived numbers (P&L realizado / no realizado / de hoy) survive a flaky
OANDA CLOSED-trades read: attempt timeout + retries inside the deadline, last good read kept and shown with its age
instead of "sin datos". Display/read-only code; offline (fake transport and clocks)."""
from datetime import datetime, timedelta, timezone
import json
import unittest

from execution import oanda_readonly as ro
from execution.demo_dashboard import demo_snapshot
from ui import demo_refresher
from ui.views import luxury

TOKEN = "tok-FAKE-practice-0123abcd"
ACCOUNT = "101-001-12345678-001"
ENV = {"OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": ACCOUNT, "AI_FLOOR_DEMO_EPOCH": "2026-10-25T15:30:20Z",
       "AI_FLOOR_DB_PATH": "/nonexistent/none.db"}
EPOCH = datetime(2026, 10, 25, 15, 30, 20, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 28, 18, 55, tzinfo=timezone.utc)
CLOSED = {"trades": [{"id": "40", "instrument": "EUR_USD", "price": "1.11269", "openTime": "2026-10-28T14:15:57.877354647Z",
                      "initialUnits": "100000", "currentUnits": "0", "state": "CLOSED", "realizedPL": "65.0000",
                      "financing": "0.0000", "closeTime": "2026-10-28T14:56:52.974689602Z",
                      "averageClosePrice": "1.11334"}]}
OPEN = {"trades": []}


class Script:
    """Fake transport: per state a list of outcomes (int status / payload dict / exception), last one repeats."""

    def __init__(self, closed, opened=None):
        self.script = {"CLOSED": list(closed), "OPEN": list(opened or [OPEN])}
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        state = "CLOSED" if "state=CLOSED" in url else "OPEN"
        self.calls.append((state, timeout))
        queue = self.script[state]
        outcome = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, int):
            return outcome, {}, b""
        return 200, {}, json.dumps(outcome).encode()


class RetryTests(unittest.TestCase):
    def read(self, transport, **kw):
        return ro.demo_trades(ENV, since=EPOCH, transport=transport, now=NOW, **kw)

    def test_a_hung_closed_read_is_retried_and_succeeds(self):
        transport = Script([TimeoutError("timeout"), CLOSED])
        result = self.read(transport)
        self.assertEqual([t["id"] for t in result["closed"]], ["40"])
        self.assertEqual([state for state, _t in transport.calls], ["CLOSED", "CLOSED", "OPEN"])
        self.assertTrue(all(t <= ro.TRADES_ATTEMPT_TIMEOUT_SECONDS for _s, t in transport.calls))

    def test_a_5xx_and_a_dropped_connection_are_retried_too(self):
        transport = Script([502, OSError("connection failed"), CLOSED])
        self.assertEqual(len(self.read(transport)["closed"]), 1)
        self.assertEqual(len([c for c in transport.calls if c[0] == "CLOSED"]), 3)

    def test_gives_up_after_the_attempts_with_the_same_failure_contract(self):
        transport = Script([TimeoutError("timeout")])
        self.assertIsNone(self.read(transport))
        self.assertEqual(len(transport.calls), ro.TRADES_ATTEMPTS)
        self.assertEqual(self.read(Script([TimeoutError("timeout")]), report_errors=True)["error"], "timeout")

    def test_auth_and_rate_limit_are_not_retried(self):
        for status, code in ((401, "auth"), (429, "rate_limited"), (404, "http_404")):
            with self.subTest(status=status):
                transport = Script([status])
                self.assertEqual(self.read(transport, report_errors=True)["error"], code)
                self.assertEqual(len(transport.calls), 1)

    def test_retries_stay_inside_the_overall_deadline(self):
        ticks = iter(range(0, 100, 5))
        transport = Script([TimeoutError("timeout")])
        result = self.read(transport, clock=lambda: float(next(ticks)), deadline_seconds=8, report_errors=True)
        self.assertIn(result["error"], ("timeout", "deadline"))
        self.assertEqual(len(transport.calls), 1)  # the budget was spent: no further attempt


class StaleCacheTests(unittest.TestCase):
    def setUp(self):
        self.now = 1000.0
        self.results = []
        self.fetches = 0

        def fetch(env, since=None):
            self.fetches += 1
            return self.results.pop(0)

        self.cache = ro.CachedDemoTrades(ttl=10, clock=lambda: self.now, fetch=fetch)

    def good(self, at):
        return {"closed": [{"id": "40"}], "open": [], "as_of": at}

    def test_failed_refresh_serves_the_last_good_read_with_its_own_time(self):
        read_at = NOW - timedelta(minutes=1)
        self.results = [self.good(read_at), None]
        self.assertNotIn("stale", self.cache.get(EPOCH))
        self.now += 11
        stale = self.cache.get(EPOCH)
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["stale_error"], "unavailable")
        self.assertEqual(stale["as_of"], read_at)
        self.assertEqual(stale["closed"], [{"id": "40"}])

    def test_error_dict_keeps_its_code_and_a_failure_is_retried_after_5_seconds(self):
        self.results = [self.good(NOW), {"error": "deadline", "as_of": NOW}, {"error": "deadline", "as_of": NOW},
                        self.good(NOW + timedelta(seconds=30))]
        self.cache.get(EPOCH)
        self.now += 11
        self.assertEqual(self.cache.get(EPOCH)["stale_error"], "deadline")
        self.now += 4
        self.cache.get(EPOCH)
        self.assertEqual(self.fetches, 2)  # still inside the 5 s failure TTL: no new read
        self.now += 2
        self.assertEqual(self.cache.get(EPOCH)["stale_error"], "deadline")
        self.assertEqual(self.fetches, 3)
        self.now += 6
        fresh = self.cache.get(EPOCH)
        self.assertNotIn("stale", fresh)  # recovered: a good read replaces the stale one
        self.assertEqual(self.fetches, 4)

    def test_too_old_last_good_read_is_not_served(self):
        self.results = [self.good(NOW), None]
        self.cache.get(EPOCH)
        self.now += ro.TRADES_STALE_MAX_SECONDS + 1
        self.assertIsNone(self.cache.get(EPOCH))

    def test_stale_fallback_is_not_served_past_the_maximum_on_a_cache_hit(self):
        self.results = [self.good(NOW), None, self.good(NOW + timedelta(hours=1))]
        self.cache.get(EPOCH)
        self.now += ro.TRADES_STALE_MAX_SECONDS - 5  # the failed refresh happens just inside the maximum
        self.assertTrue(self.cache.get(EPOCH)["stale"])
        self.now += 8  # past the failure TTL and past the maximum
        result = self.cache.get(EPOCH)
        self.assertNotIn("stale", result)  # re-fetched instead of serving a >30 min old read
        self.assertEqual(self.fetches, 3)

    def test_cold_failure_is_stored_as_before(self):
        self.results = [None, {"error": "incomplete", "as_of": NOW}]
        self.assertIsNone(self.cache.get(EPOCH))
        self.now += 6
        self.assertEqual(self.cache.get(EPOCH)["error"], "incomplete")


class SnapshotAndScreenTests(unittest.TestCase):
    def stale_trades(self, seen):
        return {"closed": [{"id": "40", "instrument": "EUR_USD", "price": ro._dec("1.11269"),
                            "openTime": "2026-10-28T14:15:57.877354647Z", "initialUnits": ro._dec("100000"),
                            "currentUnits": ro._dec("0"), "state": "CLOSED", "realizedPL": ro._dec("65"),
                            "financing": ro._dec("0"), "closeTime": "2026-10-28T14:56:52.974689602Z",
                            "averageClosePrice": ro._dec("1.11334"), "unrealizedPL": None, "client_id": "aif-run-40",
                            "stop_loss": None, "take_profit": None}],
                "open": [], "as_of": seen, "stale": True, "stale_error": "timeout"}

    def snapshot(self, trades):
        view = ro.AccountView("OK", "practice", "101-…5678-001", "USD", ro._dec("100151.01"), ro._dec("100151.01"),
                              ro._dec("0"), 0, ro._dec("0"), as_of=NOW)
        return demo_snapshot(NOW, ENV, trades_reader=lambda since: trades, account_reader=lambda env: view)

    def test_stale_trades_keep_the_numbers_and_are_flagged(self):
        seen = NOW - timedelta(minutes=3)
        snap = self.snapshot(self.stale_trades(seen))
        self.assertTrue(snap["trades_stale"])
        self.assertEqual(snap["trades_as_of"], seen)
        self.assertEqual(snap["stats"]["total_pnl"], 65.0)
        self.assertEqual(snap["read_codes"]["trades"], "stale_timeout")
        self.assertIsNone(snap["trades_error"])
        fresh = self.snapshot({**self.stale_trades(NOW), "stale": None})
        self.assertFalse(fresh["trades_stale"])
        self.assertIsNone(fresh["read_codes"]["trades"])

    def test_tiles_show_the_value_with_its_age_instead_of_sin_datos(self):
        snap = self.snapshot(self.stale_trades(NOW - timedelta(minutes=3)))
        html = "".join(luxury.demo_kpis(snap, NOW))
        self.assertIn("datos de hace 3 min", html)
        self.assertIn("+$65.00", html)
        self.assertNotIn("sin datos (", html)  # the failure wording of the screenshot
        fresh = "".join(luxury.demo_kpis(self.snapshot({**self.stale_trades(NOW), "stale": None}), NOW))
        self.assertNotIn("datos de hace", fresh)

    def test_age_text_and_unknown_time(self):
        self.assertEqual([luxury.age_text(s) for s in (5, 89, 90, 600, 3900)],
                         ["hace 5 s", "hace 89 s", "hace 1 min", "hace 10 min", "hace 1 h 5 min"])
        self.assertEqual(luxury.trades_age_note({"trades_stale": True, "trades_as_of": None}), " · datos anteriores")
        self.assertEqual(luxury.trades_age_note({"trades_stale": False}), "")


class RefresherTests(unittest.TestCase):
    def test_stale_trades_count_as_a_failed_read_and_the_outcome_is_logged_once(self):
        view = ro.AccountView("OK", "practice", "x", "USD", ro._dec("1"), ro._dec("1"), ro._dec("0"), 0, ro._dec("0"),
                              as_of=NOW)
        stale = {"closed": [], "open": [], "as_of": NOW, "stale": True, "stale_error": "timeout"}
        good = {"closed": [], "open": [], "as_of": NOW}
        demo_refresher._last_trades_code = None
        with self.assertLogs("ui.demo_refresher", level="INFO") as logs:
            self.assertFalse(demo_refresher.refresh_once(EPOCH, lambda e: stale, lambda e: view))
            self.assertFalse(demo_refresher.refresh_once(EPOCH, lambda e: stale, lambda e: view))
            self.assertTrue(demo_refresher.refresh_once(EPOCH, lambda e: good, lambda e: view))
        self.assertEqual([r.getMessage() for r in logs.records],
                         ["floor demo refresher: trades read stale:timeout", "floor demo refresher: trades read ok"])


if __name__ == "__main__":
    unittest.main()
