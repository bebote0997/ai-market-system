"""Light/Spanish dashboard: realized R original-stop fix, plain-language phrases, Control skeleton (read-only)."""
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from ui import i18n_es as es
from ui.views import control, luxury

T = '2026-10-14T21:00:00+00:00'


def ledger(path, trades, positions):
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE closed_trades(trade_id TEXT PRIMARY KEY,payload TEXT)')
        db.execute('CREATE TABLE paper_positions(position_id TEXT PRIMARY KEY,payload TEXT)')
        db.executemany('INSERT INTO closed_trades VALUES(?,?)', [(t['trade_id'], json.dumps(t)) for t in trades])
        db.executemany('INSERT INTO paper_positions VALUES(?,?)', [(p['position_id'], json.dumps(p)) for p in positions])


def trade(n, position_id, pnl, reason, quantity=1):
    return {'trade_id': f't{n}', 'position_id': position_id, 'symbol': 'XAUUSD', 'side': 'LONG', 'entry_price': 100,
            'exit_price': 100, 'quantity': quantity, 'net_pnl': pnl, 'exited_at': T, 'reason': reason}


def position(position_id, **extra):
    return {'position_id': position_id, 'entry_price': 100, 'stop': 98, 'quantity': 1, 'contract_multiplier': 10,
            'status': 'CLOSED', **extra}


# The Control page reads the OANDA balance (GET summary) when a token is in the environment: never in tests.
_NO_BALANCE_NETWORK = patch("execution.oanda_readonly.urllib_transport",
                            side_effect=AssertionError("network must not be touched"))


def setUpModule():
    _NO_BALANCE_NETWORK.start()


def tearDownModule():
    _NO_BALANCE_NETWORK.stop()


class RealizedRTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'ledger.db'

    def rows(self):
        with sqlite3.connect(self.path) as db:
            db.row_factory = sqlite3.Row
            return luxury.closed_trade_rows(db), luxury.ledger_statistics(db, datetime.now(timezone.utc))[0]

    def test_day1_close_uses_the_never_moved_stop(self):
        # Day-1 time exit: initial_stop is absent because the stop never moved; risk = |100-98| * 1 * 10 = 20.
        ledger(self.path, [trade(1, 'p1', 8, 'TIME_EXIT_DAY1_BELOW_0_5R')], [position('p1')])
        rows, stats = self.rows()
        self.assertAlmostEqual(rows[0]['realized_r'], 0.4)
        self.assertIsNone(rows[0]['realized_r_reason'])
        self.assertAlmostEqual(stats['avg_r'], 0.4)
        self.assertEqual(es.r_phrase(rows[0]['realized_r']), '+0.40R')

    def test_day2_uses_initial_stop_not_breakeven_stop(self):
        ledger(self.path, [trade(1, 'p1', -20, 'stop')],
               [position('p1', stop=100, initial_stop=98, duration_stage='DAY2_BREAKEVEN')])
        rows, stats = self.rows()
        self.assertEqual(rows[0]['realized_r'], -1)
        self.assertEqual(stats['avg_r'], -1)
        self.assertEqual(es.r_phrase(-1), '-1.00R')

    def test_moved_stop_without_initial_stop_has_spanish_reason(self):
        ledger(self.path, [trade(1, 'p1', 5, 'TIME_EXIT_DAY2_NY_CLOSE'), trade(2, 'p2', 8, 'target')],
               [position('p1', stop=100, duration_stage='DAY2_BREAKEVEN'), position('p2')])
        rows, stats = self.rows()
        by_id = {r['trade_id']: r for r in rows}
        self.assertIsNone(by_id['t1']['realized_r'])
        self.assertEqual(by_id['t1']['realized_r_reason'], 'sin datos del stop original')
        self.assertAlmostEqual(by_id['t2']['realized_r'], 0.4)
        self.assertIsNone(stats['avg_r'])
        self.assertEqual(stats['avg_r_reason'], 'sin datos del stop original en 1 de 2 operaciones')

    def test_missing_position_and_bad_numbers_are_explicit(self):
        self.assertEqual(luxury.realized_r_with_reason({'position_id': 'x', 'net_pnl': 1}, None),
                         (None, 'sin datos de la posición original'))
        base = position('p')
        for changed in ({'stop': None}, {'stop': True}, {'stop': float('nan')}):
            self.assertEqual(luxury.realized_r_with_reason({'position_id': 'p', 'net_pnl': 1}, {**base, **changed})[1],
                             es.STOP_UNKNOWN)
        self.assertIn('multiplicador', luxury.realized_r_with_reason(
            {'position_id': 'p', 'net_pnl': 1}, {**base, 'contract_multiplier': None})[1])
        self.assertEqual(luxury.realized_r({'position_id': 'p', 'net_pnl': 1, 'realized_r': 0.25}, None), 0.25)

    def test_ledger_without_positions_table(self):
        with sqlite3.connect(self.path) as db:
            db.execute('CREATE TABLE closed_trades(trade_id TEXT,payload TEXT)')
            db.execute('INSERT INTO closed_trades VALUES(?,?)', ('t', json.dumps(trade(1, 'p', 3, 'target'))))
            stats = luxury.ledger_statistics(db, datetime.now(timezone.utc))[0]
        self.assertEqual((stats['trades'], stats['wins'], stats['total_pnl']), (1, 1, 3))
        self.assertIn('sin datos de la posición original', stats['avg_r_reason'])


class SpanishPresentationTests(unittest.TestCase):
    def test_professional_metric_labels(self):
        self.assertEqual(es.pnl_since_start(47.05), 'Realized PnL · desde el inicio del historial')
        self.assertEqual(es.today_phrase(-3.5), 'Today PnL (ET)')
        self.assertEqual(es.wins_phrase(3, 4), 'Win rate · 3/4 closed trades')
        self.assertEqual(es.wins_phrase(0, 0), 'Closed trades: 0')
        self.assertEqual(es.drawdown_phrase(3.39), 'Max drawdown (realized PnL): $3.39')
        self.assertEqual(es.avg_r_phrase(0.46), 'Avg R (expectancy)')
        self.assertEqual(es.avg_r_phrase(None, 'sin stop'), 'Avg R (expectancy): sin stop')
        self.assertEqual(es.money(-2, signed=True), '−$2.00')
        self.assertEqual(es.tone(-1), 'neg')
        self.assertEqual(es.datetime_et(datetime(2026, 10, 14, 14, 15, tzinfo=timezone.utc)), '14 oct 2026 10:15 ET')

    def test_close_reasons_states_and_fallbacks(self):
        self.assertEqual(es.close_reason_text('stop'), 'Stop loss hit (SL)')
        self.assertEqual(es.close_reason_text('target'), 'Take profit hit (TP)')
        self.assertEqual(es.close_reason_text('TIME_EXIT_DAY1_BELOW_0_5R'), 'Time exit · día 1 < 0.5R')
        from execution.trade_duration import CLOSE_REASONS
        for reason in CLOSE_REASONS:
            self.assertNotEqual(es.close_reason_text(reason), reason)
        self.assertEqual(es.close_reason_text('SOMETHING_NEW'), 'SOMETHING_NEW')
        self.assertEqual(es.status_text('NO_DATA'), 'Sin datos')
        self.assertEqual(es.status_text('LONG'), 'LONG')
        self.assertEqual(es.status_text('ODD'), 'ODD')
        self.assertEqual(luxury.MISSING, 'no disponible aún')

    def test_navigation_labels_round_trip(self):
        from ui.views.dashboard import PAGES as LEGACY
        for key in luxury.PAGES + LEGACY + ('FLOOR', 'MARKETS', 'JOURNAL', 'EXPERIMENT', 'SYSTEM'):
            self.assertEqual(es.page_key(es.page_label(key)), key)
        self.assertIn('Control', luxury.PAGES)

    def test_state_chip_keeps_original_as_tooltip(self):
        chip = luxury.state_chip('REJECTED')
        self.assertIn('Rechazada', chip)
        self.assertIn('title="REJECTED"', chip)

    def test_phrases_do_not_overclaim_scope(self):
        self.assertEqual(es.pnl_since_start(None), f'Realized PnL: {es.MISSING}')
        self.assertNotIn('demo', es.pnl_since_start(1))
        self.assertNotIn('cuenta', es.drawdown_phrase(1))
        self.assertEqual(es.drawdown_phrase(None), f'Max drawdown (realized PnL): {es.MISSING}')

    def test_card_trusts_values_not_labels(self):
        markdown = []
        st = types.SimpleNamespace(markdown=lambda body, **_: markdown.append(body))
        label = es.COMPONENTS['last_run']
        attack = '<img src=x onerror=alert(1)>'
        luxury.card(st, 'Sistema', [(label, luxury.health_dot('OK')), (label, attack),
                                    ('Estado', attack), ('Estado', luxury.state_chip('REJECTED'))])
        body = markdown[0]
        self.assertNotIn('<img', body)
        self.assertEqual(body.count('&lt;img src=x onerror=alert(1)&gt;'), 2)
        self.assertIn('<span class="dot ">', body)
        self.assertIn('<span class="chip neg" title="REJECTED">', body)
        self.assertIsInstance(luxury.state_chip(None), luxury.Trusted)
        self.assertNotIsInstance(luxury.display(attack), luxury.Trusted)

    def test_slippage_plan_vs_fill(self):
        rows = dict(luxury.slippage({'symbol': 'EURUSD', 'side': 'LONG', 'entry_price': 1.1002,
                                     'planned_entry': 1.1}, {}))
        self.assertIn('+2.0 pips', rows['Slippage'])
        self.assertIn('adverse', rows['Slippage'])
        rows = dict(luxury.slippage({'symbol': 'XAUUSD', 'side': 'SHORT', 'entry_price': 2000.5}, {'entry': 2000}))
        self.assertIn('favorable', rows['Slippage'])
        self.assertNotIn('pips', rows['Slippage'])
        rows = dict(luxury.slippage({'symbol': 'XAUUSD', 'entry_price': 1}, {}))
        self.assertEqual(rows['Slippage'], 'sin planned entry guardada')

    def test_ui_stylesheet_is_dark_gold_and_offline(self):
        css = Path('ui/static/theme.css').read_text()
        self.assertFalse(Path('ui/static/luxury.css').exists())
        self.assertNotIn('@import', css)
        self.assertNotIn('http', css)
        self.assertIn('#0a0a0b', css)
        self.assertIn('#d9b45a', css)
        self.assertNotIn('#f3f6fb', css)  # no light page background left


class ControlViewTests(unittest.TestCase):
    def test_missing_registry_falls_back_to_all_off(self):
        with patch.dict(sys.modules, {'execution.account_registry': None}):
            snapshot = control.control_snapshot()
        self.assertEqual(snapshot, control.default_snapshot())
        self.assertFalse(snapshot['readable'])
        self.assertFalse(any(a['enabled'] for a in snapshot['accounts']))

    def test_registry_snapshot_and_raising_registry(self):
        module = types.ModuleType('execution.account_registry')
        module.read_control_snapshot = lambda: {'readable': True, 'emergency_stop': False, 'connected': False,
            'accounts': [{'key': 'real', 'mode': 'REAL', 'enabled': False, 'label': 'Real', 'locked': True},
                         {'key': 'bogus', 'enabled': True}]}
        with patch.dict(sys.modules, {'execution.account_registry': module}):
            snapshot = control.control_snapshot()
        self.assertTrue(snapshot['readable'])
        self.assertEqual([a['key'] for a in snapshot['accounts']], list(control.ACCOUNT_KEYS))
        real = snapshot['accounts'][2]
        self.assertEqual(control.account_status(real, snapshot), ('Real: bloqueado por seguridad', 'neg'))
        self.assertEqual(control.account_status(snapshot['accounts'][1], snapshot)[0], 'Aún no conectado')

        def boom():
            raise RuntimeError('registry down')
        module.read_control_snapshot = boom
        with patch.dict(sys.modules, {'execution.account_registry': module}):
            self.assertEqual(control.control_snapshot(), control.default_snapshot())

    def registry(self, snapshot):
        module = types.ModuleType('execution.account_registry')
        module.read_control_snapshot = lambda: snapshot
        with patch.dict(sys.modules, {'execution.account_registry': module}):
            return control.control_snapshot()

    def test_unreadable_registry_is_all_off_regardless_of_content(self):
        on = [{'key': key, 'mode': 'LIVE', 'enabled': True, 'locked': False} for key in control.ACCOUNT_KEYS]
        for readable in (False, None, 'true', 1, 'yes'):
            with self.subTest(readable=readable):
                snapshot = self.registry({'readable': readable, 'emergency_stop': True, 'connected': True,
                                          'accounts': on})
                self.assertEqual(snapshot, control.default_snapshot())

    def test_account_flags_are_strict_booleans_and_real_never_enabled(self):
        snapshot = self.registry({'readable': True, 'emergency_stop': 'yes', 'connected': 1, 'accounts': [
            {'key': 'paper', 'enabled': 'true', 'mode': 7, 'label': None},
            {'key': 'broker_demo', 'enabled': True, 'mode': 'DEMO', 'locked': 'no'},
            {'key': 'real', 'enabled': True, 'mode': 'REAL', 'locked': False}]})
        self.assertTrue(snapshot['readable'])
        self.assertIs(snapshot['emergency_stop'], False)
        self.assertIs(snapshot['connected'], False)
        paper, demo, real = snapshot['accounts']
        self.assertEqual((paper['enabled'], paper['mode'], paper['label']), (False, 'OFF', control.LABELS['paper']))
        self.assertEqual((demo['enabled'], demo['mode'], demo['locked']), (True, 'DEMO', False))
        self.assertEqual((real['enabled'], real['locked']), (False, True))
        connected = self.registry({'readable': True, 'connected': True,
                                   'accounts': [{'key': 'real', 'enabled': True, 'locked': False}]})
        self.assertEqual(control.account_status(connected['accounts'][2], connected),
                         ('Real: bloqueado por seguridad', 'neg'))

    def test_control_page_widgets_are_disabled_and_read_only(self):
        import test_phase10_dashboard as fixtures
        case = fixtures.Phase10DashboardTests('test_sqlite_connection_rejects_writes')
        case.setUp()
        self.addCleanup(case.doCleanups)
        before = hashlib.sha256(case.path.read_bytes()).hexdigest()
        # Operator actions unavailable too (they work by default since the emergency button was wired): fail closed.
        with patch.dict(sys.modules, {'execution.account_registry': None, 'execution.operator_actions': None}):
            app = fixtures.Phase10DashboardTests.app(case)
            app.radio[0].set_value('Control').run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        toggles = [t for t in app.toggle if t.key and t.key.startswith('control-')]
        self.assertEqual(len(toggles), 2)
        self.assertTrue(all(t.disabled and t.value is False for t in toggles))
        stop = next(b for b in app.button if b.key == 'control-emergency-stop')
        self.assertTrue(stop.disabled)
        self.assertIn('PARADA DE EMERGENCIA', stop.label)
        content = '\n'.join(m.value for m in app.markdown)
        self.assertIn('Aún no conectado', content)
        self.assertIn('Real: bloqueado por seguridad', content)
        self.assertEqual(hashlib.sha256(case.path.read_bytes()).hexdigest(), before)

    def test_control_source_never_writes_or_reads_environment(self):
        source = Path('ui/views/control.py').read_text()
        for forbidden in ('os.environ', 'getenv', 'INSERT', 'UPDATE ', 'DELETE', '.write', 'on_click', 'on_change'):
            self.assertNotIn(forbidden, source)



class ControlBrokerViewTests(unittest.TestCase):
    """Router chip, Demo/Real balance VIEW (read-only) and the Historial (baseline) placeholder."""

    def setUp(self):
        from execution import oanda_readonly
        self.addCleanup(patch.stopall)
        patch.object(oanda_readonly, '_VIEWS', oanda_readonly.CachedAccountViews()).start()
        env = {k: v for k, v in os.environ.items() if not k.startswith('OANDA_')}
        patch.dict(os.environ, env, clear=True).start()

    def app(self):
        import test_phase10_dashboard as fixtures
        case = fixtures.Phase10DashboardTests('test_sqlite_connection_rejects_writes')
        case.setUp()
        self.addCleanup(case.doCleanups)
        app = fixtures.Phase10DashboardTests.app(case)
        app.radio[0].set_value('Control').run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        return app

    @staticmethod
    def content(app):
        return '\n'.join(m.value for m in app.markdown)

    def test_history_section_is_separate_and_empty_without_data(self):
        with patch.dict('os.environ', {'AI_FLOOR_HISTORIAL_DIR': '/nonexistent-historial-dir'}):
            content = self.content(self.app())
        start = content.index('📚 Historial (referencia) · no son trades de OANDA')
        section = content[start:]
        self.assertIn('separadas de las estadísticas de la demo en vivo de OANDA', section)
        self.assertIn('sin datos todavía', section)
        self.assertIn('Referencia · no son trades de OANDA', section)
        self.assertNotIn('<table', section)

    def test_routing_chip_defaults_to_off(self):
        self.assertIn('Enrutado a broker demo: apagado', self.content(self.app()))
        with patch('execution.broker_demo_state.read_router_status', side_effect=RuntimeError('boom')):
            self.assertEqual(control.router_status()['state'], 'off')
        for status in (None, [], {'readable': True, 'routing': 'yes'}, {'readable': False, 'routing': True}):
            with patch('execution.broker_demo_state.read_router_status', return_value=status):
                self.assertEqual(control.routing_chip(control.router_status())[0], 'Enrutado a broker demo: apagado')
        on = {'readable': True, 'routing': True, 'dry_run': True, 'blocked_reason': 'drift',
              'open_trade': {'trade_id': '6397', 'side': 'LONG'}, 'last_events': [{'ts': 't', 'kind': 'DRIFT'}]}
        with patch('execution.broker_demo_state.read_router_status', return_value=on):
            self.assertEqual(control.router_status()['state'], 'dry_run')
            self.assertEqual(control.router_status()['blocked_reason'], 'drift')

    def test_demo_is_default_and_real_is_display_only(self):
        app = self.app()
        content = self.content(app)
        self.assertIn('Demo · cuenta practice OANDA', content)
        self.assertIn('sin token', content)
        self.assertNotIn('Real · solo lectura', content)
        view = next(t for t in app.toggle if t.key == 'account-view-real')
        self.assertIs(view.value, False)
        view.set_value(True).run()
        self.assertFalse(app.exception)
        content = self.content(app)
        self.assertIn('Real · solo lectura (operar bloqueado)', content)
        self.assertIn('sin token', content)  # no OANDA_LIVE_TOKEN
        # No order control anywhere: the only button is the (now wired, enabled by default) emergency stop, which
        # asks for a typed confirmation before doing anything.
        self.assertEqual([b.key for b in app.button], ['control-emergency-stop'])
        self.assertTrue(all(b.disabled for b in app.button if b.key != 'control-emergency-stop'))
        toggles = [t for t in app.toggle if t.key and t.key.startswith('control-')]
        self.assertTrue(all(t.disabled and t.value is False for t in toggles))

    def test_balance_uses_the_cached_summary(self):
        from datetime import timedelta
        from decimal import Decimal
        from execution import oanda_readonly as ro
        calls = []

        def fetch(environment, env):
            calls.append(environment)
            return ro.AccountView('OK', environment, '101-…0579-001', 'USD', Decimal('100000.5'), Decimal('100010.25'),
                                  Decimal('0'), 0, Decimal('9.75'), datetime.now(timezone.utc) - timedelta(seconds=3))

        patch.object(ro, '_VIEWS', ro.CachedAccountViews(fetch=fetch)).start()
        app = self.app()
        app.run()  # a refresh inside the TTL reuses the cached summary
        content = self.content(app)
        self.assertIn('100,000.50 USD', content)
        self.assertIn('actualizado hace', content)
        self.assertEqual(calls, ['practice'])

    def test_balance_lines_fallbacks(self):
        self.assertEqual(control.balance_lines(None), [('Estado', 'error de lectura')])
        from execution.oanda_readonly import AccountView
        lines = dict(control.balance_lines(AccountView('SIN_CUENTA', 'live')))
        self.assertEqual((lines['Estado'], lines['Balance'], lines['Actualizado']), ('sin cuenta', '—', '—'))

    def test_refresh_without_env_does_not_crash(self):
        from execution.oanda_readonly import cached_account_view
        self.assertEqual(cached_account_view('live').status, 'SIN_TOKEN')
        self.assertEqual(cached_account_view('practice').status, 'SIN_TOKEN')


if __name__ == '__main__':
    unittest.main()
