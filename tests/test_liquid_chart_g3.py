"""G3: status copy, ET axis/clock, and the 414 TP clearance fixture. No orders."""
from datetime import datetime
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo

from ui.liquid.chart_status import chart_status, format_et_time, fx_market_closed

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
ROOT = Path(__file__).resolve().parents[1]
CANDLES = [{"time": "2026-11-27T21:50:00+00:00", "open": "1", "high": "1", "low": "1", "close": "1"}]


def _et(year, month, day, hour, minute, fold=0):
    return datetime(year, month, day, hour, minute, tzinfo=ET, fold=fold)


class MarketHoursTests(unittest.TestCase):
    def test_friday_5pm_et_to_sunday_5pm_et_is_closed_in_est_and_edt(self):
        # EST (UTC-5), January 2026
        self.assertFalse(fx_market_closed(_et(2026, 1, 16, 16, 59)))  # Fri 4:59 PM
        self.assertTrue(fx_market_closed(_et(2026, 1, 16, 17, 0)))     # Fri 5:00 PM
        self.assertTrue(fx_market_closed(_et(2026, 1, 17, 12, 0)))     # Sat
        self.assertTrue(fx_market_closed(_et(2026, 1, 18, 16, 59)))   # Sun 4:59 PM
        self.assertFalse(fx_market_closed(_et(2026, 1, 18, 17, 0)))    # Sun 5:00 PM
        # Same instants as UTC: Fri 22:00 UTC is 5:00 PM EST; Fri 21:00 UTC is 4:00 PM EST
        self.assertFalse(fx_market_closed(datetime(2026, 1, 16, 21, 59, tzinfo=UTC)))
        self.assertTrue(fx_market_closed(datetime(2026, 1, 16, 22, 0, tzinfo=UTC)))

        # EDT (UTC-4), after the 2026 spring-forward (8 Mar)
        self.assertFalse(fx_market_closed(_et(2026, 3, 13, 16, 59)))
        self.assertTrue(fx_market_closed(_et(2026, 3, 13, 17, 0)))
        self.assertTrue(fx_market_closed(_et(2026, 3, 14, 8, 0)))
        self.assertTrue(fx_market_closed(_et(2026, 3, 15, 16, 59)))
        self.assertFalse(fx_market_closed(_et(2026, 3, 15, 17, 0)))
        # Friday 21:00 UTC is 5:00 PM EDT; 20:00 UTC is 4:00 PM EDT
        self.assertFalse(fx_market_closed(datetime(2026, 3, 13, 20, 59, tzinfo=UTC)))
        self.assertTrue(fx_market_closed(datetime(2026, 3, 13, 21, 0, tzinfo=UTC)))

        # EST again after the 2026 fall-back (1 Nov)
        self.assertFalse(fx_market_closed(_et(2026, 11, 6, 16, 59)))
        self.assertTrue(fx_market_closed(_et(2026, 11, 6, 17, 0)))
        self.assertTrue(fx_market_closed(_et(2026, 11, 8, 16, 59)))
        self.assertFalse(fx_market_closed(_et(2026, 11, 8, 17, 0)))


class StatusAndClockTests(unittest.TestCase):
    def test_format_et_uses_new_york_not_utc(self):
        # 21:50 UTC on Fri 27 Nov 2026 is 4:50 PM ET (EST)
        self.assertEqual(format_et_time(datetime(2026, 11, 27, 21, 50, tzinfo=UTC)), "4:50 PM ET")
        self.assertEqual(format_et_time("2026-11-27T21:50:00+00:00"), "4:50 PM ET")
        self.assertEqual(format_et_time(1795816200), "4:50 PM ET")  # 2026-11-27 21:50 UTC
        # 20:50 UTC in July is 4:50 PM EDT
        self.assertEqual(format_et_time(datetime(2026, 7, 2, 20, 50, tzinfo=UTC)), "4:50 PM ET")

    def test_status_labels_follow_candles_market_and_socket(self):
        closed = _et(2026, 11, 27, 17, 5)
        opened = _et(2026, 11, 27, 16, 0)
        self.assertEqual(
            chart_status(candles=CANDLES, stale=True, last_update=None, now=closed),
            "mercado cerrado · última vela 4:50 PM ET",
        )
        self.assertEqual(
            chart_status(candles=CANDLES, stale=True, last_update="2026-11-27T21:50:00+00:00", now=opened),
            "sin conexión · última vela 4:50 PM ET",
        )
        self.assertEqual(
            chart_status(candles=CANDLES, stale=False, last_update="2026-11-27T21:50:00+00:00", now=opened),
            "última lectura 4:50 PM ET",
        )
        self.assertEqual(
            chart_status(candles=[], stale=True, last_update=None, now=closed),
            "sin datos",
        )
        self.assertEqual(
            chart_status(candles=[], stale=False, last_update=None, now=opened),
            "sin datos",
        )


class ScriptAndLayoutTests(unittest.TestCase):
    def test_chart_script_formats_the_axis_in_et_and_uses_the_weekend_rule(self):
        source = (ROOT / "ui" / "static" / "liquid" / "chart.js").read_text(encoding="utf-8")
        self.assertIn("America/New_York", source)
        self.assertIn("tickMarkFormatter", source)
        self.assertIn("timeFormatter", source)
        self.assertIn("mercado cerrado · última vela", source)
        self.assertIn("sin conexión · última vela", source)
        self.assertIn('"sin datos"', source)
        self.assertIn("hour12: true", source)
        self.assertIn("weekday === \"Fri\"", source)
        self.assertIn("weekday === \"Sun\"", source)
        self.assertIn("17 * 60", source)
        self.assertNotIn("sin datos · última lectura", source)
        for banned in ("close_market", "mutate", "submit_plan", "open_market"):
            self.assertNotIn(banned, source, banned)

    def test_tp_label_sits_above_the_iphone_tab_bar_in_the_414_fixture(self):
        css = (ROOT / "ui" / "static" / "liquid" / "glass.css").read_text(encoding="utf-8")
        js = (ROOT / "ui" / "static" / "liquid" / "chart.js").read_text(encoding="utf-8")
        self.assertIn("@media (max-width:414px)", css)
        self.assertIn("min(34vh,260px)", css)
        # main already reserves the fixed tab bar
        self.assertIn("padding-bottom:calc(98px + env(safe-area-inset-bottom))", css)
        self.assertIn("scaleMargins", js)
        self.assertIn("bottom: 0.22", js)
        viewport = 896
        tab_top = viewport - 98
        chart_height = 260
        margin_bottom = 0.22
        label_h = 18
        # Worst case: the canvas sits flush with the tab bar. A TP at the low of the
        # range is lifted by scaleMargins.bottom, so its label stays above the bar.
        canvas_bottom = tab_top
        tp_label_bottom = canvas_bottom - (margin_bottom * chart_height) + label_h
        self.assertLess(tp_label_bottom, tab_top)
        self.assertGreater(margin_bottom * chart_height, label_h + 20)


if __name__ == "__main__":
    unittest.main()
