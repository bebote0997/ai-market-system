"""Chunk B: age policy, phone layout and refresh regressions."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest

from test_floor_view import fake_demo, NOW
from ui.floor_payload import demo_header
from ui.views.floor_view import render_html


class PolishBTests(unittest.TestCase):
    def header(self, minutes, *, running=False, now=None, **changes):
        now = now or datetime(2026, 10, 25, 16, tzinfo=timezone.utc)
        snap = {**fake_demo()['snapshot'](NOW), 'as_of': now - timedelta(minutes=minutes),
                'trades_as_of': now - timedelta(minutes=minutes), **changes}
        return demo_header(snap, 1, now, running=running)

    def test_closed_idle_age(self):
        for minutes, old in ((4, False), (30, False), (45, True)):
            with self.subTest(minutes=minutes):
                h = self.header(minutes)
                self.assertEqual(h['equity_stale'], old)
                self.assertEqual(h['trades_stale'], old)

    def test_running_and_market_open_keep_tight_age(self):
        self.assertTrue(self.header(4, running=True)['equity_stale'])
        self.assertTrue(self.header(4, now=NOW)['equity_stale'])
        self.assertFalse(self.header(1, running=True)['equity_stale'])

    def test_failed_read_and_unknown_time_are_not_fresh(self):
        self.assertTrue(self.header(1, stale=True)['equity_stale'])
        self.assertIsNone(self.header(1, as_of=None)['equity'])

    def test_phone_and_wall_fit(self):
        html = render_html({})
        self.assertIn('grid-template-columns:repeat(2,minmax(0,1fr))', html)
        self.assertIn('flex:0 0 25%', html)
        self.assertIn('.kpi small:first-of-type{white-space:nowrap}', html)
        self.assertIn('white-space:nowrap;min-width:0;overflow:hidden;text-overflow:ellipsis', html)
        self.assertIn('cx.scale(maxU*U/width,1)', html)
        self.assertNotIn('cx.fillText(maxU?fitText', html)
        kpis = html.split("document.getElementById('kpis').innerHTML=", 1)[1].split('// "datos reales"', 1)[0]
        self.assertNotIn('esc(STALE)', kpis)
        self.assertIn('class="data-age"', kpis)

    def test_stale_notice_includes_both_source_times(self):
        html = render_html({})
        self.assertIn('NAV ${HD.equity_as_of} ET', html)
        self.assertIn("operaciones ${HD.trades_as_of?HD.trades_as_of+' ET'", html)
        self.assertIn("ageTimes.join(' · ')", html)

    def test_refresh_is_automatic_and_faster_than_snapshot(self):
        app = Path('ui/app.py').read_text()
        floor = app.split('if page == floor_view.PAGE:', 1)[1].split('if page in dashboard.PAGES:', 1)[0]
        self.assertIn('@st.fragment(run_every=(demo_refresher.FIRST_RETRY if warming else "60s") if refresh else None)',
                      floor)
        self.assertIn('floor_view.render(st, enabled_symbols)', floor)
