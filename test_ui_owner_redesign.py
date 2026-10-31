"""Owner UI pass: remember-me token, Spanish summaries, header KPIs, close-body candles, agent cards."""
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
    def test_known_english_becomes_spanish_and_unknown_stays_original(self):
        text = ("Caution is warranted: all specialist inputs are neutral and the deterministic assessment "
                "remains WATCH, so no directional confluence or executable setup is present.")
        summary = summarize_es(text)
        self.assertIn("observación", summary)
        self.assertIn("setup operable", summary)
        self.assertNotEqual(summary, text)
        self.assertIsNone(summarize_es("The widget is blue and the box is round today."))
        self.assertEqual(summarize_es("estructura alcista real"), "estructura alcista real")
        same = summarize_es(text)
        self.assertEqual(same, summary)  # process cache

    def test_long_original_is_behind_ver_mas_when_translated(self):
        html = present_summary("The evidence is mixed and liquidity below remains the draw for now.")
        self.assertIn("Ver más", html)
        self.assertIn("Ocultar", html)
        self.assertIn("mixta", html)
        self.assertNotIn("<script>", present_summary('<script>alert(1)</script> ' * 30))


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
