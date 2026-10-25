"""Epoch regressions for technical health, alerts, system and journal views."""
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

from test_ui_epoch_only import EPOCH, BEFORE, AFTER
from ui.views import dashboard, journal, system


class PolishA1Tests(unittest.TestCase):
    def test_health_before_first_cycle_is_well_and_waiting(self):
        health = {'status': 'DEGRADED', 'last_run': BEFORE, 'last_success': BEFORE,
                  'last_cycle_status': 'FAILED', 'freshness': 'STALE_DATA', 'last_review_agents': 4}
        result = system.demo_health(health, None, [], EPOCH)
        self.assertEqual(result['status'], 'OK')
        self.assertEqual(result['freshness'], dashboard.FIRST_CYCLE_WAIT)
        self.assertEqual(dict(system.health_rows(result, EPOCH))['Estado'], 'Bien')
        self.assertEqual(system.demo_health(health, None, [], None), health)

    def test_pre_epoch_health_preserves_operational_failures(self):
        from datetime import datetime, timezone
        base = {'status': 'DEGRADED', 'last_run': BEFORE, 'last_success': BEFORE,
                'freshness': 'STALE_DATA', 'scheduler': 'RUNNING',
                'runtime_heartbeat': datetime.now(timezone.utc).isoformat(),
                'market_data': 'Twelve Data'}
        self.assertEqual(system.demo_health(base, None, [], EPOCH)['status'], 'OK')
        for changes, expected in (({'scheduler': 'STOPPED'}, 'STOPPED'),
                                  ({'runtime_heartbeat': BEFORE}, 'DEGRADED'),
                                  ({'market_data': 'NOT CONFIGURED'}, 'DEGRADED')):
            with self.subTest(changes=changes):
                self.assertEqual(system.demo_health({**base, **changes}, None, [], EPOCH)['status'], expected)

    def test_current_errors_survive_without_a_current_cycle(self):
        for severity in ('ERROR', 'CRITICAL'):
            for stamp, status in ((BEFORE, 'OK'), (AFTER, 'DEGRADED')):
                result = system.demo_health({'last_run': BEFORE}, None,
                                           [{'timestamp_utc': stamp, 'severity': severity}], EPOCH)
                self.assertEqual(result['status'], status)
        self.assertEqual(system.demo_health({'last_run': AFTER, 'last_cycle_status': 'FAILED'},
                                           None, [], EPOCH)['status'], 'DEGRADED')

    def test_persisted_errors_are_preserved(self):
        store = Mock()
        with patch.object(dashboard, 'records', return_value=[{'timestamp': AFTER, 'severity': 'ERROR'}]), \
                patch.object(dashboard, 'payloads', return_value=[]), \
                patch.object(dashboard, 'notification_rows', return_value=[]):
            self.assertEqual(system.demo_health({'last_run': BEFORE}, store, [], EPOCH)['status'], 'DEGRADED')

    def test_health_values_are_epoch_filtered_without_mutation(self):
        states = {'last_run': BEFORE, 'last_success': AFTER, 'heartbeat': AFTER, 'scheduler': 'RUNNING'}
        projected = dashboard.current_states(states, EPOCH)
        self.assertEqual(projected['last_run'], dashboard.FIRST_CYCLE_WAIT)
        self.assertEqual(projected['last_success'], AFTER)
        self.assertEqual(projected['heartbeat'], AFTER)
        self.assertEqual(states['last_run'], BEFORE)
        self.assertEqual(dashboard.current_states(states, None), states)

    def test_retried_old_notifications_stay_hidden(self):
        with sqlite3.connect(':memory:') as db:
            db.row_factory = sqlite3.Row
            db.executescript('CREATE TABLE notification_events(event_id TEXT, payload TEXT);'
                             'CREATE TABLE notification_deliveries(event_id TEXT, attempted_at TEXT);')
            for event, stamp in (('old', BEFORE), ('new', AFTER)):
                db.execute('INSERT INTO notification_events VALUES(?,?)',
                           (event, json.dumps({'timestamp_utc': stamp, 'severity': 'CRITICAL'})))
                db.execute('INSERT INTO notification_deliveries VALUES(?,?)', (event, AFTER))
            self.assertEqual([r['event_id'] for r in dashboard.notification_rows(db, EPOCH)], ['new'])
            self.assertEqual(len(dashboard.notification_rows(db, None)), 2)

    def test_journal_filters_even_when_caller_does_not(self):
        st = Mock()
        columns = [Mock() for _ in range(5)]
        columns[0].selectbox.return_value = 'ALL'
        for col in columns[1:4]:
            col.text_input.return_value = ''
        columns[4].date_input.return_value = None
        st.columns.return_value = columns
        row = {'timestamp': BEFORE, 'run_id': 'daily:2026-10-23', 'symbol': '',
               'source': 'summary', 'event_type': 'DAILY_SUMMARY', 'payload': '{}'}
        with patch.object(dashboard, 'demo_since', return_value=EPOCH), \
                patch.object(journal, 'dataframe') as table:
            journal.render(st, SimpleNamespace(sample=False), [row])
            table.assert_not_called()
            st.info.assert_called_with(dashboard.FIRST_CYCLE_WAIT)
            journal.render(st, SimpleNamespace(sample=False), [{**row, 'timestamp': AFTER}])
            table.assert_called_once()


class TechnicalPagesTests(unittest.TestCase):
    def test_technical_pages_do_not_show_pre_epoch_evidence(self):
        from test_ui_epoch_only import DemoPagesTests
        case = DemoPagesTests('test_legacy_pages_hide_pre_epoch_rows')
        case.setUp()
        self.addCleanup(case.doCleanups)
        for page in ('HEALTH', 'ALERTS', 'JOURNAL', 'SYSTEM'):
            with self.subTest(page=page):
                app, _ = case.page(page)
                shown = '\n'.join(str(frame.value) for frame in app.dataframe)
                self.assertNotIn(BEFORE, shown)
                self.assertNotIn('daily:2026-10-23', shown)
                if page == 'SYSTEM':
                    self.assertNotIn('Con problemas', shown)


class ComponentRecoveryTests(unittest.TestCase):
    def test_only_run_failures_recover_after_a_later_success(self):
        health = {'status': 'OK', 'last_run': AFTER, 'last_success': AFTER,
                  'last_cycle_status': 'COMPLETED', 'market_data': 'Twelve Data'}
        cases = (
            ({'event_type': 'RUN_FAILED'}, 'OK'),
            ({'component': 'provider'}, 'DEGRADED'),
            ({'component': 'notifications'}, 'DEGRADED'),
            ({'event_type': 'PROVIDER_ERROR'}, 'DEGRADED'),
            ({}, 'DEGRADED'),
        )
        for severity in ('ERROR', 'CRITICAL'):
            for event, expected in cases:
                with self.subTest(event=event, severity=severity):
                    error = {'timestamp_utc': EPOCH, 'severity': severity, **event}
                    self.assertEqual(system.demo_health(health, None, [error], EPOCH)['status'], expected)

    def test_persisted_run_and_journal_failures_require_a_strictly_later_success(self):
        store = Mock()
        for source in ('runs', 'journal'):
            error = {'timestamp': EPOCH, 'event_type': 'RUN_FAILED', 'severity': 'ERROR'}
            for success, expected in ((BEFORE, 'DEGRADED'), (EPOCH, 'DEGRADED'),
                                      (AFTER, 'OK'), (None, 'DEGRADED')):
                with self.subTest(source=source, success=success):
                    health = {'status': 'OK', 'last_run': AFTER, 'last_success': success,
                              'last_cycle_status': 'COMPLETED'}
                    with patch.object(dashboard, 'records',
                                      side_effect=lambda db, table, **kw: [error] if table == source else []), \
                            patch.object(dashboard, 'payloads', return_value=[]), \
                            patch.object(dashboard, 'notification_rows', return_value=[]):
                        self.assertEqual(system.demo_health(health, store, [], EPOCH)['status'], expected)

    def test_other_journal_errors_do_not_recover_after_success(self):
        store = Mock()
        health = {'status': 'OK', 'last_run': AFTER, 'last_success': AFTER,
                  'last_cycle_status': 'COMPLETED'}
        error = {'timestamp': EPOCH, 'event_type': 'PROVIDER_ERROR', 'severity': 'ERROR'}
        with patch.object(dashboard, 'records',
                          side_effect=lambda db, table, **kw: [error] if table == 'journal' else []), \
                patch.object(dashboard, 'payloads', return_value=[]), \
                patch.object(dashboard, 'notification_rows', return_value=[]):
            self.assertEqual(system.demo_health(health, store, [], EPOCH)['status'], 'DEGRADED')

    def test_live_health_applies_after_success(self):
        health = {'status': 'OK', 'last_run': AFTER, 'last_success': AFTER,
                  'last_cycle_status': 'COMPLETED', 'scheduler': 'RUNNING'}
        for changes, expected in (({}, 'OK'), ({'scheduler': 'STOPPED'}, 'STOPPED'),
                                  ({'status': 'DEGRADED'}, 'DEGRADED')):
            with self.subTest(changes=changes):
                self.assertEqual(system.demo_health({**health, **changes}, None, [], EPOCH)['status'], expected)


if __name__ == '__main__':
    unittest.main()
