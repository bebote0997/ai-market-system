"""Owner UI pass: remember-me token, Spanish summaries, header KPIs, close-body candles, agent cards."""
import threading
import time
import unittest

from ui.auth import issue_remember_token, remember_token_valid
from ui.components.setup_chart import bars_for_dashboard, build_setup_figure
from ui.es_brief import present_summary, summarize_es
from ui.views.luxury import demo_kpis

PASSWORD = "correct-horse-battery"
NOW = 1_700_000_000


class RememberTokenTests(unittest.TestCase):
    def test_round_trip_expiry_and_password_change(self):
        token = issue_remember_token(PASSWORD, NOW)
        self.assertTrue(remember_token_valid(token, PASSWORD, NOW + 10))
        self.assertFalse(remember_token_valid(token, PASSWORD, NOW + 31 * 24 * 3600))
        self.assertFalse(remember_token_valid(token, "another-password-ok", NOW + 10))
        self.assertFalse(remember_token_valid("v1.1.abc.dead", PASSWORD, NOW))
        self.assertNotIn(PASSWORD, token)
        # A second issue is a different nonce; both verify. Nothing is stored client-side except the token.
        self.assertNotEqual(token, issue_remember_token(PASSWORD, NOW))

    def test_issue_uses_wall_clock_not_the_session_ttl(self):
        token = issue_remember_token(PASSWORD, time.time())
        self.assertTrue(token.startswith("v1."))
        self.assertTrue(remember_token_valid(token, PASSWORD, time.time()))


class SpanishSummaryTests(unittest.TestCase):
    def test_translation_is_cached_and_a_failure_keeps_the_original(self):
        from ui import es_translate
        calls = []

        def transport(text, _key, _model, _timeout):
            calls.append(text)
            if "blue" in text:
                raise TimeoutError("timeout")
            return ("Conviene ser prudente: los especialistas están neutrales y la evaluación sigue en observación, "
                    "así que no hay un setup operable.")

        text = ("Caution is warranted: all specialist inputs are neutral and the deterministic assessment "
                "remains WATCH, so no directional confluence or executable setup is present.")
        env = {"AI_FLOOR_AI_PROVIDER": "openai", "OPENAI_API_KEY": "test-key", "OPENAI_MODEL": "gpt-5.6-terra"}
        with es_translate.override(transport, env):
            summary = summarize_es(text)
            self.assertIn("observación", summary)
            self.assertIn("setup operable", summary)
            self.assertNotEqual(summary, text)
            summarize_es(text)
            self.assertEqual(calls, [text])  # the second refresh does not call again
            self.assertIsNone(summarize_es("The widget is blue and the box is round today."))
            self.assertEqual(summarize_es("estructura alcista real"), "estructura alcista real")
            html = present_summary(text)
        self.assertIn("Ver más", html)
        self.assertIn("observación", html)
        self.assertIn(text.split(",")[0], html)  # the original stays behind Ver más
        self.assertIsNone(summarize_es(text))  # no key in this process: the page shows the original
        self.assertNotIn("<script>", present_summary("<script>alert(1)</script> " * 5))

    def test_one_render_does_not_wait_again_after_the_budget(self):
        from ui import es_translate
        pending = threading.Event()
        es_translate.clear_cache()
        previous = es_translate._BUDGET_SECONDS
        es_translate._BUDGET_SECONDS = 0.2
        try:
            started = time.monotonic()
            es_translate._wait(pending)
            es_translate._wait(pending)  # the window is already over: the next note must not open another
            elapsed = time.monotonic() - started
        finally:
            es_translate._BUDGET_SECONDS = previous
            es_translate.clear_cache()
        self.assertGreater(elapsed, 0.15)
        self.assertLess(elapsed, 0.45)

    def test_practice_candles_keep_wicks_and_refuse_anything_else(self):
        from ui.oanda_candles import _allowed, override, practice_candles
        self.assertIsNone(practice_candles("EURUSD", "2026-10-30T20:00:00+00:00", env={}))
        self.assertFalse(_allowed("https://api-fxtrade.oanda.com/v3/instruments/EUR_USD/candles?price=MBA&granularity=M5&count=48"))
        self.assertFalse(_allowed("https://api-fxpractice.oanda.com/v3/instruments/EUR_USD/candles?price=MBA&granularity=M5&count=48&token=x"))
        seen = []

        def transport(url, token, timeout):
            seen.append((url, token, timeout))
            return {"candles": [
                {"complete": True, "time": "2026-10-30T19:00:00.000000000Z",
                 "mid": {"o": "1.1000", "h": "1.1050", "l": "1.0980", "c": "1.1020"}},
                {"complete": True, "time": "2026-10-30T19:05:00.000000000Z",
                 "bid": {"o": "1.1020", "h": "1.1030", "l": "1.1005", "c": "1.1010"}},
                {"complete": False, "time": "2026-10-30T19:10:00.000000000Z",
                 "mid": {"o": "1.1010", "h": "1.1012", "l": "1.1008", "c": "1.1011"}},
            ]}

        with override(transport):
            bars = practice_candles("EURUSD", "2026-10-30T20:00:00+00:00",
                                    env={"OANDA_PRACTICE_TOKEN": "practice-token"})
            again = practice_candles("EURUSD", "2026-10-30T20:00:00+00:00",
                                     env={"OANDA_PRACTICE_TOKEN": "practice-token"})
        self.assertEqual(len(seen), 1)  # the second read is the cache
        self.assertNotIn("practice-token", seen[0][0])
        self.assertEqual(len(bars), 2)
        self.assertGreater(bars[0]["high"], max(bars[0]["open"], bars[0]["close"]))  # a real wick
        self.assertLess(bars[0]["low"], min(bars[0]["open"], bars[0]["close"]))
        self.assertEqual(bars[1]["close"], 1.1010)  # bid used when mid is absent
        self.assertEqual(again, bars)
        fig = build_setup_figure(bars, {"entry": 1.102, "stop": 1.098, "target": 1.110}, "EURUSD")
        self.assertEqual(fig.data[0].type, "candlestick")
        self.assertIn("5m OHLC", fig.layout.title.text)
        self.assertEqual([shape.y0 for shape in fig.layout.shapes if shape.type == "line"][:3], [1.102, 1.098, 1.110])


class AccountCardTests(unittest.TestCase):
    def snapshot(self):
        return {"epoch": None, "stats": {"trades": 0, "wins": 0, "total_pnl": 0.0, "today_pnl": 0.0,
                                          "max_drawdown": 0.0, "avg_r": None, "avg_r_reason": None},
                "curve": [], "open_rows": [], "closed_rows": [], "nav": 100.0, "balance": 90.0,
                "currency": "USD", "capital": {"start": 80.0, "change": 10.0}, "stale": False, "trades_error": None}

    def test_balance_comes_before_nav_and_seven_rows_are_not_kpi_tiles(self):
        tiles = demo_kpis(self.snapshot())
        joined = "".join(tiles)
        self.assertLess(joined.index("Balance · OANDA demo"), joined.index("NAV (equity) · OANDA demo"))
        self.assertEqual(sum(tile.count('class="kpi') for tile in tiles), 3)
        self.assertIn('class="card account-sheet"', joined)
        for label in ("P&amp;L no realizado", "P&amp;L de hoy (ET)", "Posiciones abiertas", "Operaciones cerradas",
                      "Acierto", "R medio (expectativa)", "Caída máxima (P&amp;L realizado)"):
            self.assertIn(f'<span class="mute">{label}</span>', joined)
            self.assertNotIn(f'<div class="phrase">{label}</div>', joined)


class CandleBodyTests(unittest.TestCase):
    def test_closes_become_candles_without_inventing_a_price(self):
        bars = [{"timestamp": f"2026-10-30T0{hour}:00:00+00:00", "close": price, "timeframe": "5m",
                 "source": "candle_cache"} for hour, price in enumerate((1.10, 1.12, 1.11), start=1)]
        shown = bars_for_dashboard(bars)
        self.assertEqual(shown[0]["open"], 1.10)  # no previous close: a flat body at the stored close
        self.assertEqual((shown[1]["open"], shown[1]["close"], shown[1]["high"], shown[1]["low"]),
                         (1.10, 1.12, 1.12, 1.10))
        self.assertEqual(bars[1]["source"], "candle_cache")  # the email path's bars are untouched
        fig = build_setup_figure(shown, {"entry": 1.11, "stop": 1.10, "target": 1.13}, "EURUSD")
        self.assertEqual(fig.data[0].type, "candlestick")
        self.assertIn("5m candles", fig.layout.title.text)
        raw = build_setup_figure(bars, {"entry": 1.11, "stop": 1.10, "target": 1.13}, "EURUSD")
        self.assertEqual(raw.data[0].type, "scatter")


if __name__ == "__main__":
    unittest.main()
