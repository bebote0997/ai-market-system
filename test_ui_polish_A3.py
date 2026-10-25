"""Presentation regressions for A3: active market, Eastern time and dark alerts."""
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ui import i18n_es as es
from ui.adapters import MARKETS, empty_market
from ui.components import panels
from ui.components.charts import price_action_figure
from ui.state import default_market_index


class DetailPolishTests(unittest.TestCase):
    def test_first_enabled_unpaused_symbol_and_fallback(self):
        self.assertEqual(MARKETS[default_market_index(('XAUUSD', 'EURUSD'))], 'EURUSD')
        self.assertEqual(default_market_index(('NAS100', 'unknown', 'XAUUSD')), 0)
        self.assertEqual(default_market_index(()), 0)
        with patch('runtime.symbol_pause.is_paused', return_value=False):
            self.assertEqual(MARKETS[default_market_index(('EURUSD', 'XAUUSD'))], 'EURUSD')
            self.assertEqual(MARKETS[default_market_index(('XAUUSD', 'EURUSD'))], 'XAUUSD')

    def test_rendered_detail_defaults_and_preserves_manual_selection(self):
        from test_ui_epoch_only import DemoPagesTests
        case = DemoPagesTests('test_legacy_pages_hide_pre_epoch_rows')
        case.setUp()
        self.addCleanup(case.doCleanups)
        app, _ = case.page('MARKETS')
        self.assertFalse(app.exception, str(app.exception))
        market = next(widget for widget in app.selectbox if widget.label == 'Mercado')
        self.assertEqual(market.value, 'EURUSD')
        market.set_value('XAUUSD').run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertEqual(next(widget for widget in app.selectbox if widget.label == 'Mercado').value, 'XAUUSD')

    def test_header_clock_is_et(self):
        st = Mock()
        st.columns.return_value = [Mock() for _ in range(6)]
        with patch('ui.components.panels.datetime') as clock:
            clock.now.return_value = datetime(2026, 10, 25, 15, 30, tzinfo=timezone.utc)
            panels.header(st, empty_market('EURUSD'))
        st.columns.return_value[4].metric.assert_called_once_with('HORA ET', '11:30 ET')
        self.assertEqual(es.column('timestamp_utc'), 'Hora (ET)')

    def test_chart_hours_follow_eastern_dst_and_preserve_input(self):
        for month, hour in ((7, 11), (12, 10)):
            at = datetime(2026, month, 1, 15, tzinfo=timezone.utc)
            bar = SimpleNamespace(timestamp=at, open=1, high=2, low=0, close=1)
            figure = price_action_figure([bar])
            self.assertEqual(figure.data[0].x[0].hour, hour)
            self.assertIsNone(figure.data[0].x[0].tzinfo)
            self.assertEqual(figure.layout.xaxis.title.text, 'Hora (ET)')
            self.assertEqual(bar.timestamp, at)

    def test_detail_page_titles_match_navigation(self):
        for key in ('MARKETS', 'JOURNAL', 'SYSTEM'):
            self.assertEqual(es.tr(key), es.LEGACY_PAGES[key])
        self.assertEqual(es.tr('EXPERIMENT'), es.LEGACY_PAGES['EXPERIMENT'])
        self.assertEqual(es.page_label('SETUPS'), 'Detalle · Setups')

    def test_alert_theme_keeps_error_severity_and_styles_other_alerts(self):
        css = Path('ui/static/theme.css').read_text()
        for kind in ('Info', 'Warning', 'Success'):
            selector = '[data-testid="stAlert"]:has([data-testid="stAlertContent' + kind + '"])'
            rule = css.split(selector + '{', 1)[1].split('}', 1)[0]
            self.assertIn('background:#0d1416', rule)
            self.assertIn('border-left:3px solid #d9b45a', rule)
            self.assertIn('[data-testid="stAlertContent' + kind + '"]{background:transparent;color:inherit}', css)
        self.assertIn('background:#2a0f16;color:#ffc2cd', css)
