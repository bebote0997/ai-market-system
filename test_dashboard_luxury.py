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
        with sqlite3.connect(self.path) as db:
            db.execute('UPDATE runs SET provider_metadata=? WHERE run_id=?',
                       (json.dumps({'market': {'provider':'twelve_data', 'status':'OK'}}), 'run-1'))
            db.execute('INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) VALUES(?,?,?,?,?,?,?)',
                       (T, 'run-1', 'XAUUSD', 'conflict_engine', 'CONFLICT_DECISION', 'INFO',
                        json.dumps({'decision':'BLOCK', 'reason':'OPPOSITE_POSITION_CONFLICT'})))
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
        for value in ('Structure AI', 'fixture only', 'setup-1', 'closed-1', 'twelve_data', 'OPPOSITE_POSITION_CONFLICT', 'not persisted yet', 'Gaps'):
            self.assertIn(value, content)
        self.assertEqual(self.digest(), before)
        next(t for t in app.toggle if t.label == 'Auto-refresh · every 60s').set_value(False).run()
        self.assert_rendered(app)
        self.assertFalse(next(t for t in app.toggle if t.label == 'Auto-refresh · every 60s').value)
        self.assertEqual(self.digest(), before)
        app.radio[0].set_value('Trades').run()
        next(b for b in app.button if b.key == 'trade-process').click().run()
        self.assertEqual(app.radio[0].value, 'Process')
        self.assertEqual(next(s for s in app.selectbox if s.label == 'Cycle · newest first').value, 'run-1')

    def test_null_plan_and_payload_render_every_screen(self):
        """Regression: a setup row whose plan is JSON null crashed Process (demo DB QA)."""
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE setups SET plan='null'")
        app = self.app()
        from ui.pages.luxury import PAGES
        for page in PAGES:
            with self.subTest(page=page):
                app.radio[0].set_value(page).run()
                self.assertFalse(app.exception)
                self.assert_rendered(app)

    def test_process_empty_has_explicit_gaps(self):
        with patch.dict(os.environ, {'AI_FLOOR_DB_PATH': str(Path(self.tmp.name)/'absent.db')}):
            app = self.app()
            app.radio[0].set_value('Process').run()
            self.assert_rendered(app)
            self.assertIn('not persisted yet', '\n'.join(m.value for m in app.markdown))

    def test_setups_real_bars_and_missing_bars(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE setups SET status='VALID_SETUP' WHERE slot_key='slot-1'")
        app = self.app()
        app.radio[0].set_value('Setups').run()
        self.assert_rendered(app)
        self.assertIn('chart unavailable: bars not persisted', '\n'.join(m.value for m in app.markdown))
        with sqlite3.connect(self.path) as db:
            snap = json.loads(db.execute('SELECT payload FROM ui_snapshots').fetchone()[0])
            snap['bars'] = {'15m': CHART_BARS}
            db.execute('UPDATE ui_snapshots SET payload=?', (json.dumps(snap),))
        before = self.digest()
        app.run()
        self.assert_rendered(app)
        self.assertTrue(app.get('plotly_chart'))
        self.assertEqual(self.digest(), before)
        # A newer snapshot is never joined to an older setup by symbol alone.
        with sqlite3.connect(self.path) as db:
            snap['run_id'] = 'other-run'
            db.execute('UPDATE ui_snapshots SET payload=?', (json.dumps(snap),))
        app.run()
        self.assertIn('chart unavailable: bars not persisted', '\n'.join(m.value for m in app.markdown))


CHART_BARS = [
    {'timestamp': '2026-10-11T14:00:00Z', 'open': 100, 'high': 102, 'low': 99, 'close': 101},
    {'timestamp': '2026-10-11T14:15:00Z', 'open': 101, 'high': 102, 'low': 100, 'close': 100.5},
]


class SetupChartTests(unittest.TestCase):
    def test_money_labels_use_contract_multiplier(self):
        from ui.components.setup_chart import build_setup_figure
        plan = {'entry':100, 'stop':99, 'target':103, 'quantity':2, 'contract_multiplier':100}
        labels = ' '.join(a.text for a in build_setup_figure(CHART_BARS, plan, 'XAUUSD').layout.annotations)
        self.assertIn('$200.00 risk', labels)
        self.assertIn('$600.00 reward', labels)
        for bad in (None, 0, -1, float('nan'), True, '100'):
            labels = ' '.join(a.text for a in build_setup_figure(
                CHART_BARS, {**plan, 'contract_multiplier': bad}, 'XAUUSD').layout.annotations)
            self.assertNotIn('$', labels, bad)
            self.assertIn('1.00 price distance', labels)
            self.assertIn('3.00 price distance', labels)

    def test_validated_setups_propagates_risk_multiplier(self):
        fixtures.Phase10DashboardTests.setUp(self)
        from ui.pages.setups import validated_setups
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE setups SET status='VALID_SETUP', plan=? WHERE slot_key='slot-1'",
                       (json.dumps({'entry':100, 'stop':99, 'target':103}),))
            db.execute('DELETE FROM risk_decisions')
            db.execute("INSERT INTO risk_decisions VALUES('slot-1','APPROVED','ok',1000,0.01,2,200,100,99,103,100)")
            db.row_factory = sqlite3.Row
            rows = validated_setups(db)
        self.assertEqual(rows[0]['plan']['quantity'], 2)
        self.assertEqual(rows[0]['plan']['contract_multiplier'], 100)

    def test_levels_zones_title_and_palette(self):
        from ui.components.setup_chart import build_setup_figure
        fig = build_setup_figure(CHART_BARS, {'entry':100, 'stop':99, 'target':103, 'quantity':2}, 'XAUUSD')
        self.assertEqual(fig.layout.paper_bgcolor, '#0b0b0c')
        self.assertEqual(fig.layout.xaxis.gridcolor, '#1c1c1f')
        self.assertEqual(fig.data[0].increasing.line.color, '#9fb89a')
        self.assertEqual(fig.data[0].decreasing.line.color, '#c48a7f')
        self.assertEqual([s.y0 for s in fig.layout.shapes[:3]], [100,99,103])
        self.assertTrue(all(s.line.dash == 'dash' for s in fig.layout.shapes[:3]))
        self.assertEqual(len(fig.layout.shapes), 5)
        self.assertIn('11 Oct 10:00 → 11 Oct 10:15 ET', fig.layout.title.text)
        self.assertIn('Last: 100.50', fig.layout.title.text)
        labels = ' '.join(a.text for a in fig.layout.annotations)
        # No persisted contract_multiplier: never assume 1, show price distance.
        self.assertNotIn('$', labels)
        self.assertIn('price distance', labels)
        for label in ('ENTRY', 'STOP LOSS', 'TAKE PROFIT 1:3'):
            self.assertIn(label, labels)
        short = build_setup_figure(CHART_BARS, {'entry':100, 'stop':101, 'target':97}, 'EURUSD')
        self.assertIn('price distance', ' '.join(a.text for a in short.layout.annotations))
        non3 = build_setup_figure(CHART_BARS, {'entry':100, 'stop':99, 'target':102}, 'EURUSD')
        self.assertNotIn('1:3', ' '.join(a.text for a in non3.layout.annotations))
        with self.assertRaisesRegex(ValueError, 'not persisted'):
            build_setup_figure([], {}, 'XAUUSD')


class SetupEmailTests(unittest.TestCase):
    digest = fixtures.Phase10DashboardTests.digest

    def setUp(self):
        fixtures.Phase10DashboardTests.setUp(self)
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE setups SET status='VALID_SETUP', plan=? WHERE slot_key='slot-1'",
                       (json.dumps({'setup_id':'setup-1', 'entry':100, 'stop':99, 'target':103, 'side':'LONG', 'risk_reward':3}),))
            snap = json.loads(db.execute('SELECT payload FROM ui_snapshots').fetchone()[0])
            snap['bars'] = {'15m': CHART_BARS}
            db.execute('UPDATE ui_snapshots SET payload=?', (json.dumps(snap),))
        self.env = {'AI_FLOOR_DB_PATH':str(self.path), 'AI_FLOOR_SETUP_CHART_EMAIL':'1',
                    'AI_FLOOR_ALERTS_ENABLED':'0', 'AI_FLOOR_ALERT_EMAIL_FROM':'sender@example.invalid',
                    'AI_FLOOR_ALERT_EMAIL_TO':'owner@example.invalid', 'GMAIL_APP_PASSWORD':'fixture-only'}

    def dispatcher(self, env=None):
        from runtime.alert_dispatch import dispatcher_from_env
        dispatcher = dispatcher_from_env(environ=env or self.env)
        dispatcher.sleep = lambda _: None
        self.addCleanup(dispatcher.ledger.close)
        return dispatcher

    def test_off_no_send_no_attachment_no_config_change(self):
        from runtime.setup_chart_email import publish_setup_charts
        from runtime.config import RuntimeConfig
        env = {**self.env, 'AI_FLOOR_SETUP_CHART_EMAIL':'0'}
        dispatcher = self.dispatcher(env)
        before = self.digest()
        baseline = RuntimeConfig.from_env().fingerprint()
        with patch('smtplib.SMTP_SSL') as smtp, patch('ui.components.setup_chart.build_setup_figure') as builder:
            publish_setup_charts(dispatcher, env)
            smtp.assert_not_called()
            builder.assert_not_called()
        with patch.dict(os.environ, {'AI_FLOOR_SETUP_CHART_EMAIL':'1'}):
            self.assertEqual(RuntimeConfig.from_env().fingerprint(), baseline)
        self.assertEqual(dispatcher.ledger.db.execute('SELECT count(*) FROM alert_events').fetchone()[0], 0)
        self.assertEqual(self.digest(), before)

    def test_on_png_fake_smtp_dedup_and_recovery_snapshot(self):
        from runtime.setup_chart_email import publish_setup_charts, CHANNEL
        dispatcher = self.dispatcher()
        before = self.digest()
        from unittest.mock import Mock
        smtp = Mock()
        with patch('smtplib.SMTP_SSL', return_value=smtp), patch('runtime.setup_chart_email.setup_chart_png', return_value=b'\x89PNG\r\n\x1a\nfixture'):
            publish_setup_charts(dispatcher, self.env)
            publish_setup_charts(dispatcher, self.env)
            dispatcher.recover_pending()
        self.assertEqual(smtp.send_message.call_count, 1)
        message = smtp.send_message.call_args.args[0]
        attachments = list(message.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), 'setup.png')
        self.assertEqual(attachments[0].get_content_type(), 'image/png')
        self.assertTrue(attachments[0].get_payload(decode=True).startswith(b'\x89PNG'))
        status = dispatcher.ledger.db.execute('SELECT channel,status FROM alert_deliveries').fetchall()
        self.assertEqual(status, [(CHANNEL, 'DELIVERED')])
        self.assertEqual(self.digest(), before)

    def test_render_failure_falls_back(self):
        from runtime.setup_chart_email import publish_setup_charts
        from unittest.mock import Mock
        smtp = Mock()
        dispatcher = self.dispatcher()
        with patch('smtplib.SMTP_SSL', return_value=smtp), patch('runtime.setup_chart_email.setup_chart_png', side_effect=RuntimeError('kaleido missing')):
            publish_setup_charts(dispatcher, self.env)
        self.assertEqual(smtp.send_message.call_count, 1)
        self.assertEqual(list(smtp.send_message.call_args.args[0].iter_attachments()), [])

    def test_hook_reconciles_on_worker_and_deduplicates_restart(self):
        from runtime.alert_hook import AlertHook
        from unittest.mock import Mock
        env = {**self.env, 'AI_FLOOR_ALERTS_LEDGER_PATH':str(Path(self.tmp.name)/'alerts.db')}
        smtp = Mock()
        before = self.digest()
        with patch('smtplib.SMTP_SSL', return_value=smtp), patch('runtime.setup_chart_email.setup_chart_png', return_value=b'\x89PNGfixture'):
            for _ in range(2):
                hook = AlertHook(environ=env)
                self.assertTrue(hook.flush())
                self.assertTrue(hook.close())
        self.assertEqual(smtp.send_message.call_count, 1)
        self.assertEqual(self.digest(), before)

    def test_pending_setup_snapshot_recovery_and_flag_off(self):
        from runtime.alert_dispatch import dispatcher_from_env
        from runtime.setup_chart_email import SetupChartEvent
        from unittest.mock import Mock
        ledger_path = str(Path(self.tmp.name)/'pending-alerts.db')
        event = SetupChartEvent('pending-setup-event', 'SETUP_VALIDATED', T, 'run-1', 'XAUUSD',
            (), 'Validated PAPER setup', (('setup_id','setup-1'),), (), tuple(CHART_BARS),
            {'entry':100, 'stop':99, 'target':103})
        dispatcher = dispatcher_from_env(ledger_path=ledger_path, environ=self.env)
        dispatcher.ledger.save(event)
        dispatcher.ledger.close()
        smtp = Mock()
        off = dispatcher_from_env(ledger_path=ledger_path,
            environ={**self.env, 'AI_FLOOR_SETUP_CHART_EMAIL':'0', 'AI_FLOOR_ALERTS_ENABLED':'1'})
        with patch('smtplib.SMTP_SSL', return_value=smtp):
            off.recover_pending()
        smtp.send_message.assert_not_called()
        off.ledger.close()
        on = dispatcher_from_env(ledger_path=ledger_path, environ=self.env)
        self.addCleanup(on.ledger.close)
        before = self.digest()
        with patch('smtplib.SMTP_SSL', return_value=smtp), patch('runtime.setup_chart_email.setup_chart_png', return_value=b'\x89PNGsnapshot'):
            on.recover_pending()
            on.recover_pending()
        self.assertEqual(smtp.send_message.call_count, 1)
        self.assertEqual(self.digest(), before)
        self.assertEqual(on.ledger.status(event.event_id)['setup_chart_email']['status'], 'DELIVERED')

    def test_missing_bars_fallback_and_retry(self):
        import smtplib
        from runtime.setup_chart_email import publish_setup_charts
        from unittest.mock import Mock
        with sqlite3.connect(self.path) as db:
            snapshot = json.loads(db.execute('SELECT payload FROM ui_snapshots').fetchone()[0])
            snapshot.pop('bars')
            db.execute('UPDATE ui_snapshots SET payload=?', (json.dumps(snapshot),))
        before = self.digest()
        dispatcher = self.dispatcher()
        smtp = Mock()
        smtp.send_message.side_effect = [smtplib.SMTPServerDisconnected(), None]
        with patch('smtplib.SMTP_SSL', return_value=smtp), patch('runtime.setup_chart_email.setup_chart_png') as render:
            publish_setup_charts(dispatcher, self.env)
        render.assert_not_called()
        self.assertEqual(smtp.send_message.call_count, 2)
        self.assertEqual(list(smtp.send_message.call_args.args[0].iter_attachments()), [])
        self.assertIn('chart unavailable: bars not persisted', smtp.send_message.call_args.args[0].get_body(preferencelist=('plain',)).get_content())
        self.assertEqual(dispatcher.ledger.db.execute('SELECT attempts FROM alert_deliveries').fetchone()[0], 2)
        self.assertEqual(self.digest(), before)
