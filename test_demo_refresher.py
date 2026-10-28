"""Floor demo cache producer (ui/demo_refresher.py): starts once, only with demo results + epoch + credentials, fills
the exact cache keys the Floor peeks (fresh numbers on the Floor), survives read failures, backs off. Offline."""
from datetime import datetime, timezone
from decimal import Decimal
import os
import threading
import time
import unittest
from unittest.mock import Mock, patch

from execution import oanda_readonly as ro
from ui import demo_refresher
from ui.floor_payload import build_floor_payload

TOKEN = "tok-abcdef0123456789abcdef0123456789-secret"
OANDA_ACCOUNT = "101-000-1234567-001"  # synthetic (test_security_audit.SYNTHETIC_ACCOUNTS)
EPOCH = "2026-10-25T15:30:20Z"
EPOCH_DT = datetime(2026, 10, 25, 15, 30, 20, tzinfo=timezone.utc)


def demo_env(**extra):
    env = {"AI_FLOOR_BROKER_DEMO_ROUTING": "1", "AI_FLOOR_DEMO_EPOCH": EPOCH, "OANDA_PRACTICE_TOKEN": TOKEN,
           "OANDA_ACCOUNT_ID": OANDA_ACCOUNT}
    env.update(extra)
    return {k: v for k, v in env.items() if v is not None}


def ok_view(environment="practice", env=None):
    return ro.AccountView("OK", environment, "101-…4567-001", "USD", Decimal("100250.5"), Decimal("100300.75"),
                          Decimal("0"), 0, Decimal("0"), as_of=datetime.now(timezone.utc))


def ok_trades(env=None, *, since=None):
    return {"closed": [], "open": [], "as_of": datetime.now(timezone.utc)}


def wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class RefresherTestCase(unittest.TestCase):
    def setUp(self):
        demo_refresher.stop()
        self.addCleanup(demo_refresher.stop)


class StartConditionsTests(RefresherTestCase):
    def test_noop_without_demo_results_epoch_or_credentials(self):
        reader = Mock(side_effect=AssertionError("must not read"))
        for env in ({}, demo_env(AI_FLOOR_BROKER_DEMO_ROUTING="0"),
                    demo_env(OANDA_PRACTICE_TOKEN=None), demo_env(OANDA_ACCOUNT_ID="  ")):
            with self.subTest(env=sorted(env)):
                self.assertFalse(demo_refresher.ensure_started(env, trades_reader=reader, account_reader=reader))
                self.assertFalse(demo_refresher.running())
        with patch("execution.demo_epoch.demo_epoch", return_value=None):
            self.assertFalse(demo_refresher.ensure_started(demo_env(), trades_reader=reader, account_reader=reader))
        reader.assert_not_called()

    def test_results_source_oanda_demo_also_starts_it(self):
        env = demo_env(AI_FLOOR_BROKER_DEMO_ROUTING=None, AI_FLOOR_RESULTS_SOURCE="OANDA_DEMO")
        self.assertEqual(demo_refresher.should_run(env), (True, EPOCH_DT))

    def test_starts_one_thread_once(self):
        trades = Mock(side_effect=ok_trades)
        views = Mock(side_effect=ok_view)
        for _ in range(3):
            self.assertTrue(demo_refresher.ensure_started(demo_env(), trades_reader=trades, account_reader=views,
                                                          interval=0.01))
        alive = [t for t in threading.enumerate() if t.name == demo_refresher.THREAD_NAME]
        self.assertEqual(len(alive), 1)
        self.assertTrue(alive[0].daemon)
        self.assertTrue(wait_for(lambda: trades.call_count >= 2))
        self.assertEqual({c.args for c in trades.call_args_list}, {(EPOCH_DT,)})  # the epoch key, every time
        self.assertEqual({c.args for c in views.call_args_list}, {("practice",)})
        demo_refresher.stop()
        self.assertFalse(demo_refresher.running())

    def test_never_logs_credentials(self):
        with self.assertLogs("ui.demo_refresher", level="INFO") as logs:
            demo_refresher.ensure_started(demo_env(), trades_reader=Mock(side_effect=RuntimeError(TOKEN)),
                                          account_reader=ok_view, sleep=lambda s: time.sleep(0.01))
            self.assertTrue(wait_for(lambda: any("RuntimeError" in line for line in logs.output)))
            demo_refresher.stop()
        text = "\n".join(logs.output)
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(OANDA_ACCOUNT, text)


class LoopTests(unittest.TestCase):
    def test_survives_exceptions_and_backs_off_after_repeated_failures(self):
        stop = threading.Event()
        calls, sleeps = [], []
        outcomes = iter([RuntimeError("x"), None, {"error": "deadline"}, RuntimeError("y"), "ok", "ok"])

        def trades(epoch):
            calls.append(epoch)
            outcome = next(outcomes)
            if isinstance(outcome, Exception):
                raise outcome
            return ok_trades() if outcome == "ok" else outcome

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 6:
                stop.set()

        demo_refresher.run(EPOCH_DT, trades, ok_view, sleep=sleep, stop_event=stop, interval=2, backoff=30,
                           failures_before_backoff=3)
        self.assertEqual(len(calls), 6)  # never stopped by an exception
        self.assertEqual(sleeps, [2, 2, 30, 30, 2, 2])  # backoff from the 3rd consecutive failure; reset on success

    def test_account_error_counts_as_failure(self):
        bad = ro.AccountView("ERROR", "practice", error_code="timeout")
        self.assertFalse(demo_refresher.refresh_once(EPOCH_DT, ok_trades, lambda env: bad))
        self.assertTrue(demo_refresher.refresh_once(EPOCH_DT, ok_trades, ok_view))

    def test_stale_last_good_view_counts_as_failure_and_backs_off(self):
        # CachedAccountViews keeps the last good view marked stale after a failed refresh: shown, but a failed read
        good = ro.CachedAccountViews(fetch=ok_view)
        clock = [0.0]
        good._clock = lambda: clock[0]
        good.get("practice")
        good._fetch = lambda environment, env=None: ro.AccountView("ERROR", environment, error_code="auth")
        clock[0] = 60.0
        stale = good.get("practice")
        self.assertEqual((stale.status, stale.stale), ("OK", True))
        self.assertFalse(demo_refresher.refresh_once(EPOCH_DT, ok_trades, lambda env: stale))
        stop, sleeps = threading.Event(), []

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 4:
                stop.set()

        demo_refresher.run(EPOCH_DT, ok_trades, lambda env: stale, sleep=sleep, stop_event=stop, interval=2,
                           backoff=30, failures_before_backoff=3)
        self.assertEqual(sleeps, [2, 2, 30, 30])


class FloorCacheKeyTests(RefresherTestCase):
    def test_fills_the_exact_keys_the_floor_peeks_and_the_floor_shows_fresh_numbers(self):
        from ui.views import luxury
        transport = Mock(side_effect=AssertionError("network must not be touched"))
        trades_cache = ro.CachedDemoTrades(fetch=ok_trades)
        views_cache = ro.CachedAccountViews(fetch=ok_view)
        with patch.dict(os.environ, demo_env()), patch.object(ro, "urllib_transport", transport), \
                patch.object(ro, "_TRADES", trades_cache), patch.object(ro, "_VIEWS", views_cache):
            self.assertTrue(demo_refresher.ensure_started())  # default readers: cached_demo_trades / cached_account_view
            self.assertTrue(wait_for(lambda: "practice" in views_cache._cache and EPOCH_DT in trades_cache._cache))
            demo_refresher.stop()
            payload = build_floor_payload(sample_mode=False, now=datetime.now(timezone.utc), demo=luxury.demo_api())
        transport.assert_not_called()
        header = payload["header"]
        self.assertEqual(header["equity"], 100300.75)
        self.assertIs(header["equity_stale"], False)
        self.assertIs(header["trades_stale"], False)
        self.assertEqual(header["trades"], 0)
        self.assertFalse(payload.get("loading"))


class ColdStartTests(RefresherTestCase):
    def build(self):
        from ui.views import luxury
        return build_floor_payload(sample_mode=False, now=datetime.now(timezone.utc), demo=luxury.demo_api())

    def test_cold_caches_show_loading_while_the_producer_fills_them(self):
        from ui.views import floor_view
        release = threading.Event()
        self.addCleanup(release.set)

        def blocked(*args, **kwargs):  # the first OANDA read is still in flight
            release.wait(5)
            return None

        with patch.dict(os.environ, demo_env()), patch.object(ro._VIEWS, "_cache", {}), \
                patch.object(ro._TRADES, "_cache", {}):
            self.assertTrue(demo_refresher.ensure_started(trades_reader=blocked, account_reader=blocked))
            self.assertTrue(demo_refresher.warming_up())
            payload = self.build()
            release.set()
            demo_refresher.stop()
            self.assertFalse(demo_refresher.warming_up())
            idle = self.build()  # no producer: plain "sin datos" (nothing claims to be loading)
        header = payload["header"]
        self.assertTrue(payload["loading"])
        self.assertEqual((header["equity_loading"], header["trades_loading"]), (True, True))
        self.assertEqual((header["equity"], header["equity_stale"], header["trades_stale"]), (None, None, None))
        self.assertNotIn("header.equity_loading", payload["real_fields"])
        self.assertFalse(idle["loading"])
        self.assertEqual((idle["header"]["equity_loading"], idle["header"]["trades_loading"]), (False, False))
        html = floor_view.render_html(payload)
        self.assertIn("Cargando datos del demo…", html)
        self.assertIn('"loading": true', html)

    def test_warm_view_and_cold_trades_load_only_the_trade_cells(self):
        with patch.dict(os.environ, demo_env()), patch.object(ro._VIEWS, "_cache", {"practice": (ro._VIEWS._clock(),
                                                                                                  ok_view())}), \
                patch.object(ro._TRADES, "_cache", {}):
            release = threading.Event()
            self.addCleanup(release.set)
            demo_refresher.ensure_started(trades_reader=lambda epoch: release.wait(5), account_reader=ok_view)
            header = self.build()["header"]
            release.set()
        self.assertEqual((header["equity"], header["equity_stale"], header["equity_loading"]), (100300.75, False, False))
        self.assertTrue(header["trades_loading"])


    def test_failed_trades_read_shows_sin_datos_not_loading(self):
        # CachedDemoTrades stores None after an auth / connection error: a completed read, not a cold cache
        from ui.views import floor_view
        stored_none = {EPOCH_DT: (ro._TRADES._clock(), None)}
        with patch.dict(os.environ, demo_env()), patch.object(ro._TRADES, "_cache", stored_none), \
                patch.object(ro._VIEWS, "_cache", {"practice": (ro._VIEWS._clock(), ok_view())}):
            release = threading.Event()
            self.addCleanup(release.set)
            self.assertTrue(demo_refresher.ensure_started(trades_reader=lambda epoch: release.wait(5),
                                                          account_reader=ok_view))
            self.assertFalse(demo_refresher.warming_up())  # back to the 60 s refresh, no endless 8 s retries
            payload = self.build()
            release.set()
        header = payload["header"]
        self.assertFalse(payload["loading"])
        self.assertEqual((header["trades"], header["trades_loading"]), (None, False))
        self.assertNotIn("Cargando datos del demo…", floor_view.render_html(payload).split("<script", 1)[0])

    def test_failed_account_read_shows_sin_datos_not_loading(self):
        error = ro.AccountView("ERROR", "practice", error_code="auth")
        with patch.dict(os.environ, demo_env()), patch.object(ro._VIEWS, "_cache", {"practice": (
                ro._VIEWS._clock(), error)}), patch.object(ro._TRADES, "_cache", {}):
            release = threading.Event()
            self.addCleanup(release.set)
            demo_refresher.ensure_started(trades_reader=lambda epoch: release.wait(5), account_reader=ok_view)
            payload = self.build()
            release.set()
        header = payload["header"]
        self.assertEqual((header["equity"], header["equity_loading"]), (None, False))
        self.assertTrue(header["trades_loading"])  # the trades entry is still truly cold


class FloorCacheReadAttemptedTests(unittest.TestCase):
    def test_absent_entry_vs_stored_failed_read(self):
        from ui.floor_cache import peek_demo_trades, read_attempted
        with patch.object(ro._TRADES, "_cache", {EPOCH_DT: (ro._TRADES._clock(), None)}):
            self.assertIsNone(peek_demo_trades(EPOCH_DT))
            self.assertTrue(read_attempted("_TRADES", EPOCH_DT))
            self.assertFalse(read_attempted("_TRADES", datetime(2026, 1, 1, tzinfo=timezone.utc)))
        with patch.object(ro._TRADES, "_cache", None):
            self.assertFalse(read_attempted("_TRADES", EPOCH_DT))  # unreadable cache: never raises


class WallScreenTextTests(unittest.TestCase):
    def test_known_trades_without_a_curve_never_say_sin_datos_on_the_nav_screen(self):
        from ui.views import floor_view
        html = floor_view.render_html({})
        self.assertIn("NOTR?NOOP:HD.trades_loading===true?LOADT:Number.isInteger(HD.trades)?CURVE_SOON:EMPTY", html)
        self.assertIn("CURVE_SOON='la curva aparece con 2 operaciones cerradas'", html)
        self.assertIn("HD.trades_loading===true?`P&L ${LOADT}`", html)  # demo status wall while loading


class FloorRefreshWiringTests(unittest.TestCase):
    def test_floor_retries_sooner_only_while_warming_up(self):
        from pathlib import Path
        app = (Path(__file__).resolve().parent / "ui" / "app.py").read_text(encoding="utf-8")
        floor = app.split("if page == floor_view.PAGE:", 1)[1].split("if page in dashboard.PAGES:", 1)[0]
        self.assertIn("warming = refresh and demo_refresher.warming_up()", floor)
        self.assertIn("if warming and not demo_refresher.warming_up():\n            st.rerun()", floor)
        self.assertEqual(demo_refresher.FIRST_RETRY, "8s")
        self.assertIn("demo_refresher.ensure_started()", app)


class OpenTradeRefreshTests(RefresherTestCase):
    """Page auto-refresh: 30 s while a cached OPEN trade exists, else 60 s. Peek only: no broker call, TTL unchanged."""

    def started(self, trades):
        reader = Mock(return_value=trades)
        self.assertTrue(demo_refresher.ensure_started(demo_env(), trades_reader=reader, account_reader=Mock(return_value=None),
                                                      interval=0.01))
        return reader

    def peek_with(self, trades):
        epoch = demo_refresher._epoch
        return patch.object(ro._TRADES, "_cache", {epoch: (ro._TRADES._clock(), trades)})

    def test_constants_and_ttl_are_unchanged(self):
        self.assertEqual((demo_refresher.IDLE_REFRESH, demo_refresher.OPEN_REFRESH, demo_refresher.FIRST_RETRY),
                         ("60s", "30s", "8s"))
        self.assertEqual(ro.CACHE_TTL_SECONDS, 10.0)

    def test_idle_without_producer_or_cache(self):
        self.assertFalse(demo_refresher.has_open_trade())
        self.assertEqual(demo_refresher.open_trade_refresh(), "60s")

    def test_open_trade_gives_30s_and_flips_back_when_it_closes(self):
        opened = {"closed": [], "open": [{"id": "102"}], "as_of": datetime.now(timezone.utc)}
        flat = {"closed": [{"id": "101"}], "open": [], "as_of": datetime.now(timezone.utc)}
        self.started(flat)
        with self.peek_with(opened):
            self.assertTrue(demo_refresher.has_open_trade())
            self.assertEqual(demo_refresher.open_trade_refresh(), "30s")
        with self.peek_with(flat):
            self.assertEqual(demo_refresher.open_trade_refresh(), "60s")

    def test_failed_or_missing_reads_stay_at_60s(self):
        self.started({"closed": [], "open": [], "as_of": datetime.now(timezone.utc)})
        for stored in (None, {"error": "deadline", "open": [{"id": "1"}], "as_of": datetime.now(timezone.utc)}, "junk"):
            with self.subTest(stored=stored), self.peek_with(stored):
                self.assertEqual(demo_refresher.open_trade_refresh(), "60s")
        with patch.object(ro._TRADES, "_cache", {}):
            self.assertEqual(demo_refresher.open_trade_refresh(), "60s")

    def test_peek_never_calls_the_broker(self):
        self.started({"closed": [], "open": [], "as_of": datetime.now(timezone.utc)})
        fetch = Mock(side_effect=AssertionError("no broker call from the interval peek"))
        with patch.object(ro._TRADES, "_fetch", fetch), patch.object(ro._VIEWS, "_fetch", fetch):
            with self.peek_with({"closed": [], "open": [{"id": "1"}], "as_of": datetime.now(timezone.utc)}):
                self.assertEqual(demo_refresher.open_trade_refresh(), "30s")
            with patch.object(ro._TRADES, "_cache", {}):
                self.assertEqual(demo_refresher.open_trade_refresh(), "60s")
        fetch.assert_not_called()

    def test_app_wiring_uses_it_in_both_fragments_and_reruns_once_on_a_flip(self):
        from pathlib import Path
        app = (Path(__file__).resolve().parent / "ui" / "app.py").read_text(encoding="utf-8")
        self.assertNotIn('run_every="60s"', app)
        self.assertIn("def open_trade_refresh():", app)
        luxury = app.split("if page in luxury.PAGES:", 1)[1].split("if page == floor_view.PAGE:", 1)[0]
        self.assertIn("luxury_every = open_trade_refresh() if refresh else None", luxury)
        self.assertIn("@st.fragment(run_every=luxury_every)", luxury)
        self.assertIn("if refresh and open_trade_refresh() != luxury_every:\n            st.rerun()", luxury)
        floor = app.split("if page == floor_view.PAGE:", 1)[1].split("if page in dashboard.PAGES:", 1)[0]
        self.assertIn("(demo_refresher.FIRST_RETRY if warming else open_trade_refresh()) if refresh else None", floor)
        self.assertIn("@st.fragment(run_every=floor_every)", floor)
        self.assertIn("elif refresh and not warming and open_trade_refresh() != floor_every:\n            st.rerun()", floor)


class LifespanTests(RefresherTestCase):
    def test_lifespan_starts_and_stops(self):
        import asyncio
        started = Mock(return_value=True)

        async def run():
            async with demo_refresher.lifespan(None):
                started.assert_called_once()

        with patch.object(demo_refresher, "ensure_started", started), \
                patch.object(demo_refresher, "stop") as stop:
            asyncio.run(run())
        stop.assert_called_once()


if __name__ == "__main__":
    unittest.main()
