"""Offline persisted daily summaries: no runner/scheduler/provider activation."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

from runtime.demo_runner import DemoRunner
from runtime.notifications import SlackNotificationSink, SlackNotificationError
from storage.daily_summary import NEW_YORK, build_daily_summary, summary_window
from storage.database import Store


T = datetime(2026, 9, 19, tzinfo=timezone.utc)
DAY = T - timedelta(hours=1)


class DailySummaryTests(unittest.TestCase):
    def setUp(self):
        self.path = Path('data/runtime') / f'daily-test-{uuid4().hex}.db'
        self.store = Store(self.path)
        # Exercise only the summary/delivery methods, never construct a runtime.
        self.runner = object.__new__(DemoRunner)
        self.runner.store = self.store
        self.runner.clock = lambda: T
        self.sent = []
        self.runner.sink = SlackNotificationSink(webhook_url='https://example.invalid/test',
                                                 transport=self.transport)

    def tearDown(self):
        self.store.close()
        self.path.unlink(missing_ok=True)

    def transport(self, url, payload, timeout):
        # A separate connection proves the snapshot and claim were committed.
        other = Store(self.path, readonly=True)
        try:
            events = other.notification_events()
            self.assertTrue(events)
            self.assertEqual(other.db.execute('SELECT status FROM notification_deliveries ORDER BY attempted_at LIMIT 1').fetchone()[0], 'CLAIMED')
            self.assertEqual(json.loads(payload['text'].split('\n', 1)[1]), events[-1]['daily_summary'])
        finally:
            other.close()
        self.sent.append(payload)

    def entity(self, table, key, **payload):
        self.store.db.execute(f'INSERT INTO {table} VALUES(?,?)', (key, json.dumps(payload)))

    def run_row(self, key, symbol, setup=None, risk=None, at=DAY, final='NO_SETUP'):
        self.store.db.execute('INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status,final_status) VALUES(?,?,?,?,?,?,?)',
                              (key, key, symbol, at.isoformat(), at.isoformat(), 'COMPLETED', final))
        if setup:
            self.store.db.execute('INSERT INTO setups(slot_key,status) VALUES(?,?)', (key, setup))
        if risk:
            self.store.db.execute('INSERT INTO risk_decisions(slot_key,status) VALUES(?,?)', (key, risk))

    def test_empty_day_has_zero_counts_and_null_unknown_values(self):
        self.assertTrue(self.runner.daily_summary(T))
        summary = json.loads(self.sent[0]['text'].split('\n', 1)[1])
        self.assertEqual((summary['date_ny'], summary['close_at_utc']), ('2026-09-18', '2026-09-18T21:00:00+00:00'))
        for field in ('cycles', 'NO_SETUP', 'WATCH', 'VALID_SETUP', 'RISK_REJECTED',
                      'paper_orders_created', 'positions_opened', 'positions_closed',
                      'positions_open_current', 'realized_pnl_day', 'provider_problems', 'errors', 'macro_high_events'):
            self.assertEqual(summary[field], 0, field)
        for field in ('equity_current', 'unrealized_pnl_current', 'runner_status', 'scheduler_status'):
            self.assertIsNone(summary[field], field)

    def test_full_persisted_summary_counts_day_and_current_snapshot_separately(self):
        self.store.set_state('enabled_symbols', '["XAUUSD", "EURUSD"]')
        self.store.set_state('account_id', 'paper')
        self.store.set_state('scheduler', 'STOPPED')
        self.store.set_state('runner', 'STOPPED')
        for key, symbol, setup, risk in (
            ('a', 'XAUUSD', 'NO_SETUP', None), ('b', 'EURUSD', 'WATCH', None),
            ('c', 'XAUUSD', 'VALID_SETUP', 'REJECTED'), ('d', 'EURUSD', 'VALID_SETUP', 'APPROVED')):
            self.run_row(key, symbol, setup, risk)
        self.run_row('skipped', 'EURUSD', final='SESSION_SKIPPED')
        self.run_row('old', 'EURUSD', 'WATCH', at=T-timedelta(days=2))
        self.run_row('new', 'EURUSD', 'WATCH', at=T + timedelta(hours=4))  # 00:00 NY on the 19th
        self.entity('paper_orders', 'o', as_of=DAY.isoformat())
        self.entity('paper_orders', 'o-old', as_of=(T-timedelta(days=2)).isoformat())
        self.entity('paper_positions', 'p', opened_at=DAY.isoformat(), status='CLOSED')
        self.entity('paper_positions', 'p-old', opened_at=(T-timedelta(days=2)).isoformat(), status='OPEN')
        self.entity('closed_trades', 't', exited_at=DAY.isoformat(), net_pnl=12.5)
        self.entity('closed_trades', 't2', exited_at=DAY.isoformat(), net_pnl=-2.5)
        self.entity('closed_trades', 'old-t', exited_at=(T-timedelta(days=2)).isoformat(), net_pnl=999)
        self.entity('paper_accounts', 'paper', equity=10125, unrealized_pnl=25, realized_pnl=100)
        for kind, severity in [('PROVIDER_FAILURE', 'ERROR'), ('RATE_LIMITED', 'WARNING'),
                               ('DATA_CHECK', 'WARNING'), ('RUN_FAILED', 'ERROR')]:
            self.store.event(DAY, 'a', 'XAUUSD', 'market_data', kind, severity)
        for symbol in ('XAUUSD', 'EURUSD'):
            self.store.event(DAY, 'a', symbol, 'macro_news', 'MACRO_HIGH_IMPORTANCE', 'WARNING', {'event_id': 'macro-1'})
        self.store.event(DAY, 'a', 'EURUSD', 'macro_news', 'MACRO_HIGH_RELEVANCE', 'WARNING', {'event_id': 'policy-1'})
        self.assertTrue(self.runner.daily_summary(T))
        summary = self.store.notification_events()[0]['daily_summary']
        expected = {'cycles': 4, 'cycles_by_symbol': {'XAUUSD': 2, 'EURUSD': 2},
                    'NO_SETUP': 1, 'WATCH': 1, 'VALID_SETUP': 2, 'RISK_REJECTED': 1,
                    'paper_orders_created': 1, 'positions_opened': 1, 'positions_open_current': 1,
                    'positions_closed': 2, 'realized_pnl_day': 10, 'unrealized_pnl_current': 25,
                    'equity_current': 10125, 'provider_problems': 2, 'errors': 2,
                    'macro_high_events': 1, 'macro_policy_high_events': 1,
                    'runner_status': 'STOPPED', 'scheduler_status': 'STOPPED'}
        for key, value in expected.items():
            self.assertEqual(summary[key], value, key)

    def test_offset_timestamps_missing_pnl_and_unbounded_counts(self):
        # NY 18th, despite the timestamp's local date being the 19th.
        offset = datetime(2026, 9, 19, 1, tzinfo=timezone(timedelta(hours=2)))
        self.run_row('offset', 'EURUSD', at=offset)
        self.entity('closed_trades', 'missing', exited_at=offset.isoformat())
        for i in range(510):
            self.store.event(DAY, str(i), 'EURUSD', 'ai_provider', 'PROVIDER_FAILURE', 'ERROR')
        summary = build_daily_summary(self.store, T)
        self.assertEqual(summary['cycles'], 1)
        self.assertEqual(summary['provider_problems'], 510)
        self.assertIsNone(summary['realized_pnl_day'])
        with self.assertRaises(ValueError):
            self.runner.daily_summary(T.replace(tzinfo=None))

    def test_dedup_survives_restart_out_of_order_dates_and_changed_data(self):
        self.assertTrue(self.runner.daily_summary(T))
        saved = self.store.notification_events()[0]['daily_summary']
        self.store.close()
        self.store = Store(self.path)
        self.runner.store = self.store
        self.run_row('late', 'EURUSD', 'WATCH')
        self.runner.sink.transport = lambda *args: self.sent.append(args[1])
        self.assertFalse(self.runner.daily_summary(T))
        self.assertTrue(self.runner.daily_summary(T + timedelta(days=1)))
        self.assertFalse(self.runner.daily_summary(T))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.store.notification_events()[0]['daily_summary'], saved)

    def ny(self, *args):
        return datetime(*args, tzinfo=NEW_YORK).astimezone(timezone.utc)

    def test_ny_date_window_across_utc_midnight_and_the_17_00_close(self):
        # EDT: NY date 2026-09-18 = [04:00Z on the 18th, 04:00Z on the 19th); the close is 21:00Z on the 18th.
        self.run_row('before', 'EURUSD', at=self.ny(2026, 9, 17, 23, 59))
        self.run_row('first', 'EURUSD', at=self.ny(2026, 9, 18, 0, 0))
        self.run_row('utc-19th', 'EURUSD', at=self.ny(2026, 9, 18, 21, 30))  # 01:30Z on the 19th, NY 18th
        self.run_row('after', 'EURUSD', at=self.ny(2026, 9, 19, 0, 0))
        self.entity('closed_trades', 'across-midnight', exited_at=self.ny(2026, 9, 18, 20, 30).isoformat(), net_pnl=7)
        self.assertEqual(summary_window(self.ny(2026, 9, 18, 16, 59, 59))[0], '2026-09-17')
        self.assertEqual(summary_window(self.ny(2026, 9, 18, 17, 0))[0], '2026-09-18')
        self.assertEqual(summary_window(self.ny(2026, 9, 19, 16, 0))[0], '2026-09-18')  # after UTC midnight
        summary = build_daily_summary(self.store, self.ny(2026, 9, 18, 22, 0))
        self.assertEqual((summary['date_ny'], summary['cycles'], summary['realized_pnl_day']), ('2026-09-18', 2, 7))
        # First tick before the close -> the previous NY date; at/after the close -> that day, once.
        self.runner.sink.transport = lambda *args: self.sent.append(args[1])
        self.assertTrue(self.runner.daily_summary(self.ny(2026, 9, 18, 16, 59)))
        self.assertTrue(self.runner.daily_summary(self.ny(2026, 9, 18, 17, 0)))
        for at in (self.ny(2026, 9, 18, 17, 1), self.ny(2026, 9, 18, 20, 30), self.ny(2026, 9, 19, 16, 59)):
            self.assertFalse(self.runner.daily_summary(at))  # incl. after UTC midnight (new UTC date)
        self.assertEqual([e['run_id'] for e in self.store.notification_events()], ['daily:2026-09-17', 'daily:2026-09-18'])

    def test_dst_change_close_and_window(self):
        # 2026-11-01: EDT -> EST. The 17:00 close moves from 21:00Z to 22:00Z; that NY date lasts 25 hours.
        self.assertEqual(summary_window(self.ny(2026, 10, 31, 17, 0)),
                         ('2026-10-31', datetime(2026, 10, 31, 4, tzinfo=timezone.utc),
                          datetime(2026, 11, 1, 4, tzinfo=timezone.utc), datetime(2026, 10, 31, 21, tzinfo=timezone.utc)))
        date, start, end, close = summary_window(datetime(2026, 11, 1, 22, tzinfo=timezone.utc))
        self.assertEqual((date, start, end, close), ('2026-11-01', datetime(2026, 11, 1, 4, tzinfo=timezone.utc),
                                                     datetime(2026, 11, 2, 5, tzinfo=timezone.utc),
                                                     datetime(2026, 11, 1, 22, tzinfo=timezone.utc)))
        self.assertEqual(summary_window(datetime(2026, 11, 1, 21, 30, tzinfo=timezone.utc))[0], '2026-10-31')
        # Spring forward 2026-03-08: close moves from 22:00Z to 21:00Z; the NY date lasts 23 hours.
        self.assertEqual(summary_window(datetime(2026, 3, 8, 21, tzinfo=timezone.utc))[1:3],
                         (datetime(2026, 3, 8, 5, tzinfo=timezone.utc), datetime(2026, 3, 9, 4, tzinfo=timezone.utc)))
        self.run_row('fall-back-hour', 'EURUSD', at=datetime(2026, 11, 2, 4, 30, tzinfo=timezone.utc))  # 23:30 EST
        self.assertEqual(build_daily_summary(self.store, datetime(2026, 11, 2, 12, tzinfo=timezone.utc))['cycles'], 1)

    def test_never_sent_twice_for_one_ny_date_across_restarts(self):
        close = self.ny(2026, 9, 18, 17, 0)
        self.assertTrue(self.runner.daily_summary(close))
        for at in (close + timedelta(minutes=5), close + timedelta(hours=7, minutes=30)):  # restarts, before/after 0Z
            self.store.close()
            self.store = Store(self.path)
            self.runner.store = self.store
            self.runner.sink.transport = lambda *args: self.sent.append(args[1])
            self.assertFalse(self.runner.daily_summary(at))
        self.assertEqual(len(self.sent), 1)

    def test_slack_failures_are_isolated_recorded_and_never_retried(self):
        for error in (TimeoutError(), URLError('offline'), HTTPError('redacted', 429, '', {}, None),
                      HTTPError('redacted', 500, '', {}, None), RuntimeError('transport failure')):
            with self.subTest(error=type(error).__name__):
                day = T + timedelta(days=len(self.store.notification_events()))
                with patch.object(self.runner.sink, 'transport', side_effect=error) as transport:
                    self.assertTrue(self.runner.daily_summary(day))
                    self.assertFalse(self.runner.daily_summary(day))
                    self.assertEqual(transport.call_count, 1)
        self.assertEqual({r[0] for r in self.store.db.execute('SELECT status FROM notification_deliveries')}, {'FAILED'})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM runs').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM paper_orders').fetchone()[0], 0)

    def test_crash_after_journal_commit_can_capture_persisted_summary(self):
        with patch.object(self.store, 'capture_notifications', side_effect=RuntimeError('crash')):
            self.assertTrue(self.runner.daily_summary(T))
        self.assertEqual(len(self.sent), 0)
        events = self.store.capture_notifications()
        self.runner._deliver(events)
        self.runner._deliver(events)
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.runner.daily_summary(T))

    def test_transport_maps_errors_without_url_or_response_body(self):
        self.runner.daily_summary(T)
        from runtime.notifications import NotificationEvent
        event = NotificationEvent(**self.store.notification_events()[0])
        for error, message in ((HTTPError('private-url', 429, 'private-body', {}, None), 'RATE_LIMITED'),
                               (HTTPError('private-url', 503, 'private-body', {}, None), 'PROVIDER_FAILURE'),
                               (TimeoutError('private-url'), 'CONNECTION_ERROR')):
            with patch.object(self.runner.sink, 'transport', side_effect=error):
                with self.assertRaises(SlackNotificationError) as caught:
                    self.runner.sink.deliver(event)
                self.assertEqual(str(caught.exception), message)

    def test_concurrent_summary_calls_create_one_snapshot_and_one_attempt(self):
        from concurrent.futures import ThreadPoolExecutor
        def summarize(_):
            store = Store(self.path)
            try:
                runner = object.__new__(DemoRunner)
                runner.store, runner.clock = store, lambda: T
                runner.sink = SlackNotificationSink(webhook_url='https://example.invalid/test',
                    transport=lambda *args: self.sent.append(args[1]))
                return runner.daily_summary(T)
            finally:
                store.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(summarize, range(2))), [False, True])
        self.assertEqual(len(self.store.notification_events()), 1)
        self.assertEqual(len(self.sent), 1)

    def test_provider_invalid_payload_and_bar_problems_are_counted(self):
        for kind in ('INVALID_RESPONSE', 'INVALID_BAR'):
            self.store.event(DAY, 'run', 'EURUSD', 'market_data', kind, 'WARNING')
        self.assertEqual(build_daily_summary(self.store, T)['provider_problems'], 2)


if __name__ == '__main__':
    unittest.main()
