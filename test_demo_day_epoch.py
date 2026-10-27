""""Día N de 30" from the demo EPOCH everywhere (screens, trade alerts, daily summary, email): one rule,
``execution.demo_epoch.demo_day_from_epoch``, also behind ``execution.demo_dashboard.demo_progress``. Synthetic data only."""
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

from execution import demo_dashboard
from execution.demo_epoch import demo_day_from_epoch
from ui import demo_clock

ET = ZoneInfo("America/New_York")
UTC = timezone.utc
EPOCH = "2026-10-25T15:30:20Z"  # 2026-10-25 11:30 ET (Sunday) = day 1


class DemoProgressFromEpochTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        # No routed trade at all: the state DB does not even exist.
        self.env = {"AI_FLOOR_DEMO_EPOCH": EPOCH, "AI_FLOOR_BROKER_DEMO_ROUTING": "1",
                    "AI_FLOOR_BROKER_STATE_DB_PATH": str(Path(tmp.name) / "missing.db")}

    def day(self, *local):
        return demo_dashboard.demo_progress(datetime(*local, tzinfo=ET), self.env)[2]

    def test_oct_27_is_day_3_without_a_routed_trade(self):
        epoch, first, day = demo_dashboard.demo_progress(datetime(2026, 10, 27, 4, 30, tzinfo=ET), self.env)
        self.assertEqual(epoch, datetime(2026, 10, 25, 15, 30, 20, tzinfo=UTC))
        self.assertIsNone(first)  # still returned as before
        self.assertEqual(day, 3)
        self.assertEqual(self.day(2026, 10, 26, 17, 0, 33), 2)  # the Oct 26 summary: no more "Día sin datos"

    def test_ny_midnight_boundaries(self):
        self.assertIsNone(self.day(2026, 10, 25, 11, 30, 19))  # before the epoch
        self.assertEqual(self.day(2026, 10, 25, 11, 30, 20), 1)
        self.assertEqual(self.day(2026, 10, 25, 23, 59, 59), 1)
        self.assertEqual(self.day(2026, 10, 26, 0, 0, 0), 2)
        # 2026-10-26 03:59 UTC is still Oct 25 in New York.
        self.assertEqual(demo_dashboard.demo_progress(datetime(2026, 10, 26, 3, 59, tzinfo=UTC), self.env)[2], 1)
        self.assertEqual(demo_dashboard.demo_progress(datetime(2026, 10, 26, 4, 0, tzinfo=UTC), self.env)[2], 2)
        self.assertEqual(self.day(2026, 11, 2, 0, 30), 9)  # across the DST change (Nov 1)

    def test_uncapped_past_day_30(self):
        self.assertEqual(self.day(2026, 11, 23, 12, 0), 30)
        self.assertEqual(self.day(2026, 11, 24, 12, 0), 31)
        self.assertEqual(self.day(2027, 1, 1, 12, 0), 69)

    def test_routed_trades_never_move_day_1(self):
        with patch.object(demo_dashboard, "first_demo_open", return_value="2026-10-27T08:30:50+00:00"):
            epoch, first, day = demo_dashboard.demo_progress(datetime(2026, 10, 27, 12, tzinfo=ET), self.env)
        self.assertEqual((first, day), ("2026-10-27T08:30:50+00:00", 3))

    def test_unknown_inputs_are_none_and_never_raise(self):
        self.assertIsNone(demo_day_from_epoch(None, datetime(2026, 10, 27, tzinfo=UTC)))
        self.assertIsNone(demo_day_from_epoch(datetime(2026, 10, 25), datetime(2026, 10, 27, tzinfo=UTC)))
        self.assertIsNone(demo_day_from_epoch(datetime(2026, 10, 25, tzinfo=UTC), "2026-10-27"))
        with patch.object(demo_dashboard, "demo_epoch", return_value=None):
            self.assertIsNone(demo_dashboard.demo_progress(datetime(2026, 10, 27, tzinfo=UTC), self.env)[2])

    def test_single_source_of_truth_with_the_screens(self):
        self.assertIs(demo_clock.demo_day_from_epoch, demo_day_from_epoch)
        with patch.dict(os.environ, self.env):
            from ui.views import luxury
            now = datetime(2026, 10, 27, 12, tzinfo=ET)
            self.assertEqual(luxury.demo_day(luxury.demo_api(), now),
                             demo_dashboard.demo_progress(now, self.env)[2])


class AlertsUseTheEpochDayTests(unittest.TestCase):
    def test_trade_alert_and_daily_summary_footer(self):
        from runtime import notifications
        from runtime.trade_alert_source import daily_alert
        env = {"AI_FLOOR_DEMO_EPOCH": EPOCH, "AI_FLOOR_BROKER_DEMO_ROUTING": "1",
               "AI_FLOOR_BROKER_STATE_DB_PATH": "/nonexistent/state.db"}
        snapshot = {"stats": {"trades": 0, "wins": 0, "avg_r": None, "avg_r_reason": None, "today_pnl": 0.0},
                    "nav": 100000.0, "currency": "USD", "capital": {"change": 0.0}, "open_rows": [], "closed_rows": []}
        sent = datetime(2026, 10, 26, 21, 0, 33, tzinfo=UTC)  # 17:00:33 ET
        with patch.dict(os.environ, env), patch("execution.demo_dashboard.demo_snapshot", return_value=snapshot):
            alert = notifications.oanda_demo_daily_alert("2026-10-26T21:00:00Z", sent_at=sent)
        self.assertEqual(alert.demo_day, 2)
        self.assertEqual(daily_alert((), env, demo_day=3).demo_day, 3)


if __name__ == "__main__":
    unittest.main()
