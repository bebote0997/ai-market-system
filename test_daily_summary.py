"""Offline persisted daily summaries: no runner/scheduler/provider activation."""
from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from uuid import uuid4

from runtime.demo_runner import DemoRunner
from runtime.notifications import SlackNotificationSink, SlackNotificationError, paper_summary_text
from storage.daily_summary import (MAX_CATCH_UP, NEW_YORK, build_daily_summary, pending_summary_days, summary_day,
                                    summary_window)
from storage.database import Store


T = datetime(2026, 9, 19, tzinfo=timezone.utc)  # Fri 2026-09-18 20:00 EDT: trading day 2026-09-18 has ended
DAY = T - timedelta(hours=4)  # Fri 16:00 EDT, inside [Thu 17:00, Fri 17:00) NY


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
            self.assertEqual(payload['text'], paper_summary_text(events[-1]['daily_summary']))
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
        summary = self.store.notification_events()[-1]['daily_summary']
        self.assertEqual(self.sent[0]['text'], paper_summary_text(summary))
        self.assertEqual((summary['date_ny'], summary['window_start_utc'], summary['close_at_utc'],
                          summary['trading_day_key']),
                         ('2026-09-18', '2026-09-17T21:00:00+00:00', '2026-09-18T21:00:00+00:00',
                          '2026-09-17T17:00:00-04:00'))
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
        self.run_row('new', 'EURUSD', 'WATCH', at=T - timedelta(hours=3))  # 17:00 NY: the next trading day
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
                    'positions_closed': 2, 'realized_pnl_day': 10, 'wins_day': 1, 'avg_r_day': None,
                    'avg_r_day_reason': 'sin datos de la posición original en 2 de 2 operaciones',
                    'unrealized_pnl_current': 25,
                    'equity_current': 10125, 'provider_problems': 2, 'errors': 2,
                    'macro_high_events': 1, 'macro_policy_high_events': 1,
                    'runner_status': 'STOPPED', 'scheduler_status': 'STOPPED'}
        for key, value in expected.items():
            self.assertEqual(summary[key], value, key)

    def test_offset_timestamps_missing_pnl_and_unbounded_counts(self):
        # Trading day 2026-09-18 (16:00 NY), despite the timestamp's local date being the 19th.
        offset = datetime(2026, 9, 19, 1, tzinfo=timezone(timedelta(hours=5)))
        self.run_row('offset', 'EURUSD', at=offset)
        self.entity('closed_trades', 'missing', exited_at=offset.isoformat())
        for i in range(510):
            self.store.event(DAY, str(i), 'EURUSD', 'ai_provider', 'PROVIDER_FAILURE', 'ERROR')
        summary = build_daily_summary(self.store, T)
        self.assertEqual(summary['cycles'], 1)
        self.assertEqual(summary['provider_problems'], 510)
        self.assertIsNone(summary['realized_pnl_day'])
        self.entity('closed_trades', 'win', exited_at=DAY.isoformat(), net_pnl=5)
        self.assertIsNone(build_daily_summary(self.store, T)['wins_day'])  # a trade without P&L: wins unknown
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
        self.assertFalse(self.runner.daily_summary(T + timedelta(days=1)))  # Saturday: still Friday's trading day
        self.assertTrue(self.runner.daily_summary(T + timedelta(days=3)))  # Monday 20:00 NY
        self.assertFalse(self.runner.daily_summary(T))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.store.notification_events()[0]['daily_summary'], saved)

    def ny(self, *args):
        return datetime(*args, tzinfo=NEW_YORK).astimezone(timezone.utc)

    def test_trading_day_window_across_utc_midnight_and_the_17_00_close(self):
        # EDT: trading day 2026-09-18 (Fri) = [Thu 17:00, Fri 17:00) NY = [21:00Z on the 17th, 21:00Z on the 18th).
        self.run_row('before', 'EURUSD', at=self.ny(2026, 9, 17, 16, 59))
        self.run_row('first', 'EURUSD', at=self.ny(2026, 9, 17, 17, 0))
        self.run_row('utc-18th', 'EURUSD', at=self.ny(2026, 9, 17, 21, 30))  # 01:30Z on the 18th
        self.run_row('last', 'EURUSD', at=self.ny(2026, 9, 18, 16, 59))
        self.run_row('after', 'EURUSD', at=self.ny(2026, 9, 18, 17, 0))
        self.entity('closed_trades', 'across-midnight', exited_at=self.ny(2026, 9, 17, 20, 30).isoformat(), net_pnl=7)
        self.entity('closed_trades', 'at-close', exited_at=self.ny(2026, 9, 18, 17, 0).isoformat(), net_pnl=100)
        self.assertEqual(summary_day(self.ny(2026, 9, 18, 16, 59, 59)), date(2026, 9, 17))
        self.assertEqual(summary_day(self.ny(2026, 9, 18, 17, 0)), date(2026, 9, 18))
        self.assertEqual(summary_day(self.ny(2026, 9, 17, 21, 30)), date(2026, 9, 17))  # after UTC midnight
        summary = build_daily_summary(self.store, self.ny(2026, 9, 18, 22, 0))
        self.assertEqual((summary['date_ny'], summary['cycles'], summary['realized_pnl_day'], summary['wins_day']),
                         ('2026-09-18', 3, 7, 1))
        # First tick before the close -> the previous trading day; at/after the close -> that day, once.
        self.runner.sink.transport = lambda *args: self.sent.append(args[1])
        self.assertTrue(self.runner.daily_summary(self.ny(2026, 9, 18, 16, 59)))
        self.assertTrue(self.runner.daily_summary(self.ny(2026, 9, 18, 17, 0)))
        for at in (self.ny(2026, 9, 18, 17, 1), self.ny(2026, 9, 18, 20, 30), self.ny(2026, 9, 19, 12, 0),
                   self.ny(2026, 9, 20, 17, 30), self.ny(2026, 9, 21, 16, 59)):
            self.assertFalse(self.runner.daily_summary(at))  # incl. after UTC midnight and the closed weekend
        self.assertTrue(self.runner.daily_summary(self.ny(2026, 9, 21, 17, 0)))
        self.assertEqual([e['run_id'] for e in self.store.notification_events()],
                         ['daily:2026-09-17', 'daily:2026-09-18', 'daily:2026-09-21'])
        self.assertEqual(self.store.get_state('daily_summary_date'), '2026-09-21')

    def test_weekend_sends_friday_once_and_monday_covers_sunday_open(self):
        self.runner.sink.transport = lambda *args: self.sent.append(args[1])
        self.assertTrue(self.runner.daily_summary(self.ny(2026, 9, 19, 12, 0)))  # first tick on Saturday
        self.assertEqual(self.store.notification_events()[0]['run_id'], 'daily:2026-09-18')
        self.assertFalse(self.runner.daily_summary(self.ny(2026, 9, 20, 23, 0)))  # Sunday after the reopen
        self.assertEqual(summary_window(date(2026, 9, 21))[1:3],
                         (self.ny(2026, 9, 20, 17, 0), self.ny(2026, 9, 21, 17, 0)))
        self.assertEqual(len(self.sent), 1)

    def test_pending_days_catch_up_after_downtime_and_never_go_back(self):
        self.assertEqual(pending_summary_days(None, self.ny(2026, 9, 24, 18)), [date(2026, 9, 24)])
        self.assertEqual(pending_summary_days('2026-09-18', self.ny(2026, 9, 23, 18)),
                         [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23)])
        self.assertEqual(pending_summary_days('2026-09-19', self.ny(2026, 9, 21, 18)), [date(2026, 9, 21)])
        self.assertEqual(pending_summary_days('2026-09-18', self.ny(2026, 9, 18, 18)), [])
        self.assertEqual(pending_summary_days('2026-09-18', self.ny(2026, 9, 17, 18)), [])  # clock behind
        days = pending_summary_days('2026-08-31', self.ny(2026, 9, 18, 18))
        self.assertEqual((len(days), days[-1]), (MAX_CATCH_UP, date(2026, 9, 18)))
        # A restart after a missed close sends each missed trading day once, oldest first.
        self.runner.sink.transport = lambda *args: self.sent.append(args[1])
        self.assertTrue(self.runner.daily_summary(self.ny(2026, 9, 21, 17, 5)))
        self.store.close()
        self.store = Store(self.path)
        self.runner.store = self.store
        self.assertTrue(self.runner.daily_summary(self.ny(2026, 9, 24, 3, 0)))  # down from Tue 17:00 to Thu 03:00 NY
        self.assertFalse(self.runner.daily_summary(self.ny(2026, 9, 24, 3, 5)))
        self.assertEqual([e['run_id'] for e in self.store.notification_events()],
                         ['daily:2026-09-21', 'daily:2026-09-22', 'daily:2026-09-23'])
        self.assertEqual(len(self.sent), 3)

    def test_dst_change_close_and_window(self):
        # 2026-11-01 02:00: EDT -> EST. The 17:00 close moves from 21:00Z to 22:00Z; that trading day lasts 25 hours.
        utc = timezone.utc
        self.assertEqual(summary_window(date(2026, 10, 30)),
                         ('2026-10-30', datetime(2026, 10, 29, 21, tzinfo=utc), datetime(2026, 10, 30, 21, tzinfo=utc),
                          '2026-10-29T17:00:00-04:00'))
        self.assertEqual(summary_window(date(2026, 11, 1))[1:3],
                         (datetime(2026, 10, 31, 21, tzinfo=utc), datetime(2026, 11, 1, 22, tzinfo=utc)))
        self.assertEqual(summary_window(date(2026, 11, 2)),
                         ('2026-11-02', datetime(2026, 11, 1, 22, tzinfo=utc), datetime(2026, 11, 2, 22, tzinfo=utc),
                          '2026-11-01T17:00:00-05:00'))
        # Spring forward 2026-03-08: close moves from 22:00Z to 21:00Z; that trading day lasts 23 hours.
        self.assertEqual(summary_window(date(2026, 3, 8))[1:3],
                         (datetime(2026, 3, 7, 22, tzinfo=utc), datetime(2026, 3, 8, 21, tzinfo=utc)))
        self.assertEqual(summary_day(datetime(2026, 11, 2, 21, 30, tzinfo=utc)), date(2026, 10, 30))  # 16:30 EST
        self.assertEqual(summary_day(datetime(2026, 11, 2, 22, tzinfo=utc)), date(2026, 11, 2))
        self.run_row('fall-back-hour', 'EURUSD', at=datetime(2026, 11, 1, 21, 30, tzinfo=utc))  # Sun 16:30 EST
        self.run_row('sunday-open', 'EURUSD', at=datetime(2026, 11, 1, 22, 30, tzinfo=utc))  # Sun 17:30 EST
        self.assertEqual(build_daily_summary(self.store, datetime(2026, 11, 2, 23, tzinfo=utc))['cycles'], 1)

    def test_never_sent_twice_for_one_trading_day_across_restarts(self):
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
                day = self.ny(2026, 9, 21 + len(self.store.notification_events()), 18)  # Mon..Fri
                with patch.object(self.runner.sink, 'transport', side_effect=error) as transport:
                    self.assertTrue(self.runner.daily_summary(day))
                    self.assertFalse(self.runner.daily_summary(day))
                    self.assertEqual(transport.call_count, 1)
        self.assertEqual({r[0] for r in self.store.db.execute('SELECT status FROM notification_deliveries')}, {'FAILED'})
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM runs').fetchone()[0], 0)
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM paper_orders').fetchone()[0], 0)

    def test_crash_after_journal_commit_is_captured_and_sent_on_the_next_pass(self):
        with patch.object(self.store, 'capture_notifications', side_effect=RuntimeError('crash')):
            self.assertTrue(self.runner.daily_summary(T))
        self.assertEqual(len(self.sent), 0)
        self.assertFalse(self.runner.daily_summary(T))
        self.assertFalse(self.runner.daily_summary(T + timedelta(hours=1)))
        self.assertEqual(len(self.sent), 1)

    def test_crash_after_capture_before_claim_is_sent_on_the_next_pass_exactly_once(self):
        with patch.object(self.runner, '_deliver'):  # the process dies between capture and claim
            self.assertTrue(self.runner.daily_summary(T))
        self.assertEqual((len(self.store.notification_events()), len(self.sent)), (1, 0))
        for at in (T + timedelta(minutes=15), T + timedelta(hours=2)):
            self.assertFalse(self.runner.daily_summary(at))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT status FROM notification_deliveries')],
                         ['DELIVERED'])

    def test_crash_after_claim_is_never_resent_and_logged_unknown_once(self):
        with patch.object(self.runner.sink, 'deliver', side_effect=KeyboardInterrupt), \
                self.assertRaises(KeyboardInterrupt):  # the process dies mid-send: claimed, no outcome
            self.runner.daily_summary(T)
        with self.assertNoLogs('ai_floor.observability', 'WARNING'):
            self.assertFalse(self.runner.daily_summary(T + timedelta(minutes=5)))  # may still be in flight
        with self.assertLogs('ai_floor.observability', 'WARNING') as logs:
            self.assertFalse(self.runner.daily_summary(T + timedelta(minutes=20)))
        self.assertEqual(logs.output, ['WARNING:ai_floor.observability:component=notifications '
                                       'event=DAILY_SUMMARY_UNKNOWN date=2026-09-18'])
        with self.assertNoLogs('ai_floor.observability', 'WARNING'):
            self.assertFalse(self.runner.daily_summary(T + timedelta(hours=3)))
        self.assertEqual(len(self.sent), 0)
        self.assertEqual([r[0] for r in self.store.db.execute('SELECT status FROM notification_deliveries')],
                         ['UNKNOWN'])

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
