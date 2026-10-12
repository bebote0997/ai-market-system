"""Luxury screens share the Phase 10 offline fixture and remain read-only."""
import json
import os
from pathlib import Path
import sqlite3
import unittest
import hashlib
from unittest.mock import patch

from streamlit.testing.v1 import AppTest
import test_phase10_dashboard as fixtures

APP, T = fixtures.APP, fixtures.T
from ui.pages import luxury
from ui.tables import normalize_rows


class LuxuryDashboardTests(unittest.TestCase):
    app = fixtures.Phase10DashboardTests.app
    digest = fixtures.Phase10DashboardTests.digest
    assert_rendered = fixtures.Phase10DashboardTests.assert_rendered
    def setUp(self):
        fixtures.Phase10DashboardTests.setUp(self)
        with sqlite3.connect(self.path) as db:
            db.execute('INSERT INTO closed_trades VALUES(?,?)', ('closed-1', json.dumps({
                'trade_id': 'closed-1', 'position_id': 'old-position', 'run_id': 'run-1',
                'symbol': 'XAUUSD', 'side': 'LONG', 'entry_price': 100, 'exit_price': 103,
                'net_pnl': 3, 'exited_at': T, 'reason': 'TARGET',
                'schema_version': '1.0', 'quantity': 1, 'gross_pnl': 3, 'cost': 0})))
            snapshot = json.loads(db.execute('SELECT payload FROM ui_snapshots').fetchone()[0])
            snapshot['agents'] = [{'name': 'Structure AI', 'status': 'OK', 'bias': 'LONG',
                                  'confidence': 0.7, 'timestamp': T,
                                  'reasoning_summary': '<script>alert("x")</script>'}]
            snapshot['risk_status'] = 'REJECTED'
            snapshot['risk_decision'] = {'reason': 'fixture rejection'}
            db.execute('UPDATE ui_snapshots SET payload=?', (json.dumps(snapshot),))
            db.execute('UPDATE setups SET plan=? WHERE slot_key=?', (json.dumps({
                'setup_id': 'setup-1', 'entry': 100, 'stop': 99, 'target': 103, 'risk_reward': 3}), 'slot-1'))

    def test_new_screens_escape_data_and_do_not_write(self):
        before = self.digest()
        health_before = hashlib.sha256(self.health_path.read_bytes()).hexdigest()
        app = self.app()
        for page in luxury.PAGES:
            app.radio[0].set_value(page).run()
            self.assert_rendered(app)
            self.assertEqual(self.digest(), before)
            self.assertEqual(hashlib.sha256(self.health_path.read_bytes()).hexdigest(), health_before)
            self.assertTrue(any('PAPER · REAL EXECUTION DISABLED' in m.value for m in app.markdown))
        app.radio[0].set_value('The Floor').run()
        content = '\n'.join(m.value for m in app.markdown)
        self.assertIn('&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;', content)
        self.assertNotIn('<script>', content)
        self.assertIn('0.7', content)
        app.radio[0].set_value('Trades').run()
        content = '\n'.join(m.value for m in app.markdown)
        self.assertIn('3', content)
        self.assertIn('Net PnL', content)
        self.assertIn('not available yet', content)
        self.assertTrue(app.selectbox)

    def test_empty_new_screens(self):
        with patch.dict(os.environ, {'AI_FLOOR_DB_PATH': str(Path(self.tmp.name)/'missing.db'),
                                     'AI_FLOOR_HEALTH_DB_PATH': str(Path(self.tmp.name)/'missing-health.db')}):
            app = self.app()
            for page in luxury.PAGES:
                app.radio[0].set_value(page).run()
                self.assert_rendered(app)
                content = '\n'.join(m.value for m in app.markdown)
                if page != 'Analysts · soon':
                    self.assertIn('not available yet', content)
            self.assertFalse((Path(self.tmp.name)/'missing.db').exists())

    def test_all_dataframes_use_shared_normalization_and_modern_width(self):
        for path in (APP.parent).rglob('*.py'):
            source = path.read_text()
            self.assertNotIn('use_container_width', source)
            if 'st.dataframe(' in source:
                self.assertEqual(path.name, 'tables.py')
        self.assertEqual(normalize_rows([{'status': ('A', 'B')}, {'status': 'OK'}]),
                         [{'status': '["A", "B"]'}, {'status': 'OK'}])
        self.assertEqual(normalize_rows([{'status': 1}, {'status': 'OK'}]),
                         [{'status': '1'}, {'status': 'OK'}])
        self.assertEqual(normalize_rows([{'status': {'a': 1}}, {'status': None}])[0]['status'], '{"a": 1}')

    def test_system_tuple_status_renders(self):
        with patch('runtime.health.health', return_value={'status': ('A', 'B'), 'scheduler': 'OFF'}):
            app = self.app()
            app.radio[0].set_value('SYSTEM').run()
            self.assert_rendered(app)
            self.assertEqual(app.dataframe[0].value.iloc[0]['status'], '["A", "B"]')

    def test_auth_gate_is_first_and_unchanged(self):
        with patch('ui.state.sqlite3.connect', side_effect=AssertionError('unauthenticated read')):
            app = AppTest.from_file(str(APP)).run()
            self.assertFalse(app.exception)
            self.assertFalse(app.radio)
        source = APP.read_text()
        self.assertLess(source.index('require_dashboard_access(st)'), source.index('luxury.apply(st)'))
        self.assertLess(source.index('require_dashboard_access(st)'), source.index('enabled_market_symbols()'))

    def test_metrics_have_explicit_population_and_cash_drawdown(self):
        from datetime import datetime, timezone
        rows = [{'net_pnl': 10, 'exited_at': T}, {'net_pnl': -4, 'exited_at': T}]
        stats, curve = luxury.trade_statistics(rows, datetime.now(timezone.utc))
        self.assertEqual(stats['Win rate · closed trades'], '50.0%')
        self.assertEqual(stats['Max drawdown · realized PnL'], '4.00 (cash)')
        self.assertEqual(stats['Avg R realized'], luxury.MISSING)
        self.assertEqual(curve[-1]['Cumulative realized PnL'], 6)
        self.assertEqual(luxury.trade_statistics([], datetime.now(timezone.utc))[1], [])

    def test_only_named_policy_environment_values_are_displayed(self):
        with patch.dict(os.environ, {'V2_PLANNER_POLICY': '<b>V2_F3</b>',
                                     'V2_OPPOSITE_SETUP_POLICY': 'BLOCK', 'UNRELATED_SECRET': 'never-display-this'}):
            app = self.app()
            content = '\n'.join(m.value for m in app.markdown)
            self.assertIn('&lt;b&gt;V2_F3&lt;/b&gt;', content)
            self.assertIn('source: env', content)
            self.assertNotIn('never-display-this', content)
            self.assertEqual(luxury.policy_value({'V2_PLANNER_POLICY': 'V1'}, 'V2_PLANNER_POLICY'),
                             ('V1', 'system_state'))

    def test_styles_scoped_and_injected_once(self):
        app = self.app()
        styles = [m.value for m in app.markdown if 'Cormorant+Garamond' in m.value]
        self.assertEqual(len(styles), 1)
        self.assertIn('@media(max-width:640px)', styles[0])
        self.assertIn('.lux table', styles[0])

    def test_empty_database_screens_are_read_only(self):
        path = Path(self.tmp.name)/'empty.db'
        fixtures.Store(path).close()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with patch.dict(os.environ, {'AI_FLOOR_DB_PATH': str(path)}):
            app = self.app()
            for page in luxury.PAGES:
                app.radio[0].set_value(page).run()
                self.assert_rendered(app)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)

    def test_process_real_rows_refresh_and_links(self):
        before = self.digest()
        app = self.app()
        self.assertTrue(any(t.label == 'Auto-refresh · every 60s' and t.value for t in app.toggle))
        app.radio[0].set_value('Process').run()
        next(s for s in app.selectbox if s.label == 'Cycle · newest first').set_value('run-1').run()
        self.assert_rendered(app)
        content = '\n'.join(m.value for m in app.markdown)
        from ui.pages.process import STEPS
        for step in STEPS:
            self.assertIn(step, content)
        for value in ('Structure AI', 'fixture only', 'setup-1', 'closed-1', 'not persisted yet', 'Gaps'):
            self.assertIn(value, content)
        self.assertEqual(self.digest(), before)
        app.radio[0].set_value('Trades').run()
        next(b for b in app.button if b.key == 'trade-process').click().run()
        self.assertEqual(app.radio[0].value, 'Process')
        self.assertEqual(next(s for s in app.selectbox if s.label == 'Cycle · newest first').value, 'run-1')

    def test_process_empty_has_explicit_gaps(self):
        with patch.dict(os.environ, {'AI_FLOOR_DB_PATH': str(Path(self.tmp.name)/'absent.db')}):
            app = self.app()
            app.radio[0].set_value('Process').run()
            self.assert_rendered(app)
            self.assertIn('not persisted yet', '\n'.join(m.value for m in app.markdown))
