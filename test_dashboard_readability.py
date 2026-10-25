"""Cache schema compatibility, memory bounds, readable evidence and offline email."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

from runtime.setup_candle_reader import cache_path, read_candles
from ui.json_views import details, ordered

AT = '2026-10-13T15:00:00+00:00'


class CandleReaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'candle_cache.db'
        with sqlite3.connect(self.path) as db:
            # Exact origin/v2/pre-demo-fixes schema (WITHOUT ROWID).
            db.execute('CREATE TABLE candles(symbol TEXT NOT NULL,bar_start TEXT NOT NULL,close REAL NOT NULL,'
                       'PRIMARY KEY(symbol,bar_start)) WITHOUT ROWID')
            at = datetime.fromisoformat(AT)
            db.executemany('INSERT INTO candles VALUES(?,?,?)',
                [('XAUUSD', (at-timedelta(minutes=5*i)).isoformat(), 100+i) for i in range(310)])
            db.execute('INSERT INTO candles VALUES(?,?,?)', ('EURUSD', AT, 1.1))

    def test_paths_read_only_limit_symbol_cutoff_and_order(self):
        self.assertEqual(cache_path({}), Path('data/runtime/candle_cache.db'))
        self.assertEqual(cache_path({'AI_FLOOR_DB_PATH': '/tmp/demo/trading.db'}), Path('/tmp/demo/candle_cache.db'))
        self.assertEqual(cache_path({'AI_FLOOR_CANDLE_CACHE_PATH': str(self.path)}), self.path)
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        connect = sqlite3.connect
        with patch('runtime.setup_candle_reader.sqlite3.connect', wraps=connect) as opened:
            bars = read_candles(self.path, 'XAUUSD', AT)
        self.assertIn('?mode=ro', opened.call_args.args[0])
        self.assertEqual(len(bars), 48)
        self.assertEqual(bars[0]['close'], 148)
        self.assertEqual(bars[-1]['close'], 101)
        self.assertEqual(bars[-1]['timestamp'], '2026-10-13T14:55:00+00:00')
        self.assertEqual(set(bars[0]), {'timestamp', 'close', 'timeframe', 'source'})
        self.assertEqual(read_candles(self.path, 'EURUSD', AT), ())
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)

    def test_missing_corrupt_invalid_and_unaware_fallback(self):
        missing = self.path.with_name('missing.db')
        self.assertEqual(read_candles(missing, 'XAUUSD', AT), ())
        self.assertFalse(missing.exists())
        self.assertEqual(read_candles(self.path, 'XAUUSD', '2026-10-13T15:00:00'), ())
        for bad in (0, -1, float('inf'), 'bad'):
            with sqlite3.connect(self.path) as db:
                db.execute("UPDATE candles SET close=? WHERE bar_start='2026-10-13T14:55:00+00:00'", (bad,))
            self.assertEqual(read_candles(self.path, 'XAUUSD', AT), ())
        for limit in (0, -1, 289, True, 1.5):
            with self.assertRaises(ValueError):
                read_candles(self.path, 'XAUUSD', AT, limit=limit)
        self.path.write_bytes(b'corrupt')
        self.assertEqual(read_candles(self.path, 'XAUUSD', AT), ())

    def test_line_chart_and_real_browser_free_png(self):
        from ui.components.setup_chart import build_setup_figure
        from runtime.setup_chart_png import setup_chart_png
        bars = read_candles(self.path, 'XAUUSD', AT)
        plan = {'entry':101, 'stop':100, 'target':104, 'quantity':2, 'contract_multiplier':100}
        fig = build_setup_figure(bars, plan, 'XAUUSD')
        self.assertEqual(fig.data[0].type, 'scatter')
        self.assertIn('5m closes', fig.layout.title.text)
        self.assertIn('no OHLC', fig.layout.title.text)
        self.assertEqual([s.y0 for s in fig.layout.shapes[:3]], [101,100,104])
        self.assertIn('$200.00 risk', ' '.join(a.text for a in fig.layout.annotations))
        with patch('plotly.graph_objects.Figure.to_image', side_effect=AssertionError('browser export')):
            png = setup_chart_png(bars, plan, 'XAUUSD')
        self.assertTrue(png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertGreater(len(png), 1000)


class PresentationMemoryTests(unittest.TestCase):
    def test_json_order_collapsed_formatting_and_escaping(self):
        value = {'payload': {'extra':'<script>alert(1)</script>', 'status':'OK'}, 'symbol':'XAUUSD',
                 'run_id':'run-1', 'status':'VALID_SETUP'}
        self.assertEqual(list(ordered(value))[:3], ['run_id', 'symbol', 'status'])
        html = details(json.dumps(value))
        self.assertNotIn('<details', html)
        self.assertNotIn('run-1', html)
        self.assertIn('Setup válido', html)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_dataframe_consumes_only_bounded_rows(self):
        from ui.tables import dataframe
        count = []
        def rows():
            for i in range(10000):
                count.append(i)
                yield {'run_id': str(i)}
        st = Mock()
        dataframe(st, rows())
        self.assertEqual(len(count), 200)
        self.assertEqual(len(st.dataframe.call_args.args[0]), 200)

    def test_full_ledger_metrics_with_bounded_curve(self):
        from ui.views.luxury import ledger_statistics, trade_statistics
        with sqlite3.connect(':memory:') as db:
            db.execute('CREATE TABLE closed_trades(trade_id TEXT,payload TEXT)')
            at = datetime.fromisoformat(AT)
            trades = [{'net_pnl': 10 if i % 2 else -4, 'realized_r': 1 if i % 2 else -1,
                       'exited_at': (at+timedelta(seconds=i)).isoformat(), 'unused': 'x'*10000}
                      for i in range(501)]
            db.executemany('INSERT INTO closed_trades VALUES(?,?)', [(str(i),json.dumps(t)) for i,t in enumerate(trades)])
            stats, curve = ledger_statistics(db, at)
            expected, full_curve = trade_statistics(trades, at)
        self.assertEqual(stats, expected)
        self.assertEqual(len(curve), 200)
        self.assertEqual(curve, full_curve[-200:])

    def test_cache_has_ttl_and_entry_bound(self):
        import ast
        source = Path('ui/candle_cache.py').read_text()
        tree = ast.parse(source)
        call = next(n for n in ast.walk(tree) if isinstance(n, ast.Call) and
                    isinstance(n.func, ast.Attribute) and n.func.attr == 'cache_data')
        values = {k.arg: ast.literal_eval(k.value) for k in call.keywords}
        self.assertEqual(values['ttl'], 30)
        self.assertEqual(values['max_entries'], 32)


class CacheEmailTests(unittest.TestCase):
    def setUp(self):
        import test_phase10_dashboard as fixtures
        fixtures.Phase10DashboardTests.setUp(self)
        self.cache = Path(self.tmp.name)/'candle_cache.db'
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE runs SET as_of=?,completed_at=? WHERE run_id='run-1'", (AT, AT))
            db.execute("UPDATE setups SET status='VALID_SETUP',plan=? WHERE slot_key='slot-1'",
                       (json.dumps({'setup_id':'cache-setup', 'entry':100, 'stop':99, 'target':103}),))
            db.execute('DELETE FROM ui_snapshots')
        with sqlite3.connect(self.cache) as db:
            db.execute('CREATE TABLE candles(symbol TEXT NOT NULL,bar_start TEXT NOT NULL,close REAL NOT NULL,'
                       'PRIMARY KEY(symbol,bar_start)) WITHOUT ROWID')
            db.execute('INSERT INTO candles VALUES(?,?,?)', ('XAUUSD', '2026-10-13T14:55:00+00:00', 100.5))
        self.env = {'AI_FLOOR_DB_PATH': str(self.path), 'AI_FLOOR_SETUP_CHART_EMAIL':'1',
                    'AI_FLOOR_ALERTS_ENABLED':'0', 'AI_FLOOR_ALERT_EMAIL_FROM':'sender@example.invalid',
                    'AI_FLOOR_ALERT_EMAIL_TO':'owner@example.invalid', 'GMAIL_APP_PASSWORD':'fixture-only'}

    def test_real_cache_png_smtp_dedup_and_snapshot_recovery(self):
        from runtime.alert_dispatch import dispatcher_from_env
        from runtime.setup_chart_email import publish_setup_charts, CHANNEL
        ledger = str(Path(self.tmp.name)/'alerts-cache.db')
        dispatcher = dispatcher_from_env(ledger_path=ledger, environ=self.env)
        before = hashlib.sha256(self.cache.read_bytes()).hexdigest()
        smtp = Mock()
        with patch('smtplib.SMTP_SSL', return_value=smtp):
            publish_setup_charts(dispatcher, self.env)
            publish_setup_charts(dispatcher, self.env)
        self.assertEqual(smtp.send_message.call_count, 1)
        attachment = list(smtp.send_message.call_args.args[0].iter_attachments())[0]
        self.assertTrue(attachment.get_payload(decode=True).startswith(b'\x89PNG'))
        data = json.loads(dispatcher.ledger.db.execute('SELECT snapshot FROM alert_events').fetchone()[0])
        self.assertEqual(data['bars'][0]['close'], 100.5)
        self.assertEqual(data['bars'][0]['source'], 'candle_cache')
        self.assertEqual(data['bars'][0]['timeframe'], '5m')
        self.assertEqual(hashlib.sha256(self.cache.read_bytes()).hexdigest(), before)
        # Replay from persisted prices after cache disappears, using the same PNG path.
        dispatcher.ledger.db.execute('DELETE FROM alert_deliveries WHERE channel=?', (CHANNEL,))
        dispatcher.ledger.close()
        self.cache.unlink()
        restarted = dispatcher_from_env(ledger_path=ledger, environ=self.env)
        self.addCleanup(restarted.ledger.close)
        with patch('smtplib.SMTP_SSL', return_value=smtp), patch('runtime.setup_chart_email.read_candles', side_effect=AssertionError('re-read')):
            restarted.recover_pending()
            restarted.recover_pending()
        self.assertEqual(smtp.send_message.call_count, 2)
        self.assertTrue(list(smtp.send_message.call_args.args[0].iter_attachments()))

    def test_off_does_not_read_cache_or_open_dashboard(self):
        from runtime.setup_chart_email import publish_setup_charts
        with patch('runtime.setup_chart_email.read_candles') as reader, patch('ui.state.dashboard_database') as db:
            publish_setup_charts(Mock(), {'AI_FLOOR_SETUP_CHART_EMAIL':'0'})
        reader.assert_not_called()
        db.assert_not_called()

    def test_explicit_cache_path_and_no_cache_fallback(self):
        from runtime.setup_chart_email import publish_setup_charts
        dispatcher = Mock()
        custom = self.cache.with_name('explicit.db')
        self.cache.rename(custom)
        publish_setup_charts(dispatcher, {**self.env, 'AI_FLOOR_CANDLE_CACHE_PATH':str(custom)})
        self.assertEqual(dispatcher.dispatch.call_args.args[0].bars[0]['close'], 100.5)
        custom.unlink()
        publish_setup_charts(dispatcher, self.env)
        self.assertEqual(dispatcher.dispatch.call_args.args[0].bars, ())

    def test_dashboard_cache_chart_is_wired_and_read_only(self):
        import test_phase10_dashboard as fixtures
        before = hashlib.sha256(self.path.read_bytes()).hexdigest()
        cache_before = hashlib.sha256(self.cache.read_bytes()).hexdigest()
        app = fixtures.Phase10DashboardTests.app(self)
        app.radio[0].set_value('Setups').run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertEqual(len(app.get('plotly_chart')), 1)
        self.assertTrue(any('precios de cierre de 5 min' in c.value for c in app.caption))
        self.assertEqual(hashlib.sha256(self.path.read_bytes()).hexdigest(), before)
        self.assertEqual(hashlib.sha256(self.cache.read_bytes()).hexdigest(), cache_before)

    def test_validated_pages_bounded_email_reconciles_all_and_old_run_links_work(self):
        from ui.views.setups import validated_setups, iter_validated_setups
        from ui.state import dashboard_database
        import test_phase10_dashboard as fixtures
        from runtime.setup_chart_email import publish_setup_charts
        with sqlite3.connect(self.path) as db:
            for i in range(450):
                stamp = (datetime.fromisoformat(AT)+timedelta(seconds=i+1)).isoformat()
                db.execute('INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status) VALUES(?,?,?,?,?,?)',
                           (f'new-slot-{i}', f'new-run-{i}', 'XAUUSD', stamp, stamp, 'COMPLETED'))
                db.execute('INSERT INTO setups(slot_key,status,evidence,plan) VALUES(?,?,?,?)',
                           (f'new-slot-{i}', 'VALID_SETUP' if i < 250 else 'WATCH', '[]',
                            json.dumps({'setup_id':f'new-setup-{i}'})))
        with dashboard_database(self.path) as db:
            self.assertEqual(len(validated_setups(db)), 200)
            self.assertEqual(len(list(iter_validated_setups(db))), 251)
        dispatcher = Mock()
        publish_setup_charts(dispatcher, self.env)
        self.assertEqual(dispatcher.dispatch.call_count, 251)
        app = fixtures.Phase10DashboardTests.app(self)
        app.session_state['process_run_id'] = 'run-1'
        app.radio[0].set_value('Process').run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        choices = next(s for s in app.selectbox if s.label == 'Ciclo · el más reciente primero')
        self.assertEqual(len(choices.options), 200)
        self.assertEqual(choices.value, 'run-1')

    def test_legacy_view_never_loads_historical_fills_or_trades(self):
        from ui.state import current_market
        from storage.database import Store
        st = Mock()
        st.session_state = {'sample_mode':False}
        with patch.object(Store, 'load_paper', side_effect=AssertionError('full ledger')):
            vm = current_market(st, 'XAUUSD', Mock())
        self.assertEqual(vm.account.equity, 10025)
        self.assertEqual(len(vm.positions), 1)
        self.assertEqual(len(vm.orders), 1)
        self.assertEqual(vm.account.closed_trades, [])

    def test_exact_snapshot_validation_keeps_legacy_plan_identity_and_risk(self):
        from ui.views.setups import validated_setups
        from ui.state import dashboard_database
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE setups SET status='WATCH' WHERE slot_key='slot-1'")
            db.execute('INSERT INTO ui_snapshots VALUES(?,?)', ('XAUUSD', json.dumps({
                'symbol':'XAUUSD', 'run_id':'run-1', 'setup_status':'VALID_SETUP', 'plan':{}})))
        with dashboard_database(self.path) as db:
            rows = validated_setups(db)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['setup_id'], 'cache-setup')
        self.assertEqual(rows[0]['plan']['entry'], 100)


if __name__ == '__main__':
    unittest.main()
