"""Readable Spanish projections preserve raw evidence and suppress technical identities."""
import copy
import json
import re
import unittest
from unittest.mock import Mock

from ui import i18n_es as es
from ui.json_views import details
from ui.tables import dataframe

FORBIDDEN = re.compile(r'\d{4}-\d{2}-\d{2}T|\bNone\b|NO_DATA|NOT CALLED|None reported|Transfer recorded|origen: env|\bpractice\b|' + es.UUID_PATTERN.pattern)


class SpanishDetailTests(unittest.TestCase):
    def test_nested_projection_and_original_evidence(self):
        row = {'run_id': 'private-run', 'as_of': '2026-10-25T15:30:20Z',
               'details': json.dumps({'run_id': 'private-run', 'status': 'NO_DATA', 'error': 'fallo real',
                                      'empty': [], 'missing': None, 'reason': 'Transfer recorded',
                                      'event_id': '938b422c-8356-4c4f-9086-83fd892bcd4b'})}
        original = copy.deepcopy(row)
        st = Mock()
        dataframe(st, [row])
        shown = str(st.dataframe.call_args.args[0])
        self.assertIsNone(FORBIDDEN.search(shown), shown)
        self.assertNotIn('private-run', shown)
        self.assertIn('25 oct 11:30 ET', shown)
        self.assertIn('fallo real', shown)
        self.assertEqual(row, original)
        st.json.assert_not_called()

    def test_empty_values_and_escaped_summary(self):
        for value in (None, '', [], {}, '[]', '{}', 'null', 'None'):
            self.assertEqual(es.readable(value), es.MISSING)
        text = details({'reason': '<script>error</script>', 'run_id': 'private'})
        self.assertIn('&lt;script&gt;', text)
        self.assertNotIn('private', text)
        self.assertEqual(es.readable('NOT CALLED'), 'No consultado')
        self.assertEqual(es.tr('None reported'), 'sin novedades')
        self.assertEqual(es.status_text('broker_demo'), 'broker demo')
        self.assertEqual(es.status_text('real'), 'real (bloqueado)')

    def test_control_events(self):
        from ui.views import control
        rows = control.current_events([{"ts": "2026-10-25T15:30:20Z", "kind": "Transfer recorded"}])
        self.assertEqual(rows, [("25 oct 2026 11:30 ET", "transferencia registrada")])
        self.assertNotRegex(es.readable("cuenta practice"), FORBIDDEN)

    def test_rendered_detail_pages(self):
        from test_ui_epoch_only import DemoPagesTests
        case = DemoPagesTests('test_legacy_pages_hide_pre_epoch_rows')
        case.setUp()
        self.addCleanup(case.doCleanups)
        empty_epoch = case.epoch
        case.epoch = '2020-01-01T00:00:00Z'  # include persisted fixture evidence, not just empty pages
        for page in ('HEALTH', 'ALERTS', 'HISTORY', 'SYSTEM', 'JOURNAL', 'FLOOR', 'Control'):
            with self.subTest(page=page):
                epoch = case.epoch
                if page == 'FLOOR':
                    case.epoch = empty_epoch  # fixture snapshot has no agent contracts
                app, _ = case.page(page)
                case.epoch = epoch
                self.assertFalse(app.exception, str(app.exception))
                shown = '\n'.join(str(item.value) for kind in ('markdown', 'caption', 'info', 'warning',
                                   'error', 'success', 'text', 'json', 'dataframe') for item in getattr(app, kind))
                self.assertIsNone(FORBIDDEN.search(shown), shown)

    def test_process_leak_scan(self):
        from test_dashboard_luxury import LuxuryDashboardTests
        case = LuxuryDashboardTests('test_process_real_rows_refresh_and_links')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.test_process_real_rows_refresh_and_links()
        app = case.app()
        app.radio[0].set_value('Process').run()
        shown = '\n'.join(str(item.value) for item in app.markdown)
        self.assertNotRegex(shown, r'schema version:|origin order id:|gross pnl:|cost:|started at:|record:|plan:|market:|provider:|conflict_engine|Lado: LONG|twelve_data')
