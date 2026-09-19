"""Freeze regressions: injected clocks/providers and isolated SQLite, no live runner."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from ai.provider import DeterministicAIProvider
from core.contracts import MacroEvent
from data.macro_news import InMemoryMacroNewsProvider
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.scheduler import slot_key
from storage.daily_summary import build_daily_summary
from storage.database import Store
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts


class FreezeSafetyTests(unittest.TestCase):
    def setUp(self):
        self.path = Path('data/runtime') / f'freeze-test-{uuid4().hex}.db'
        self.now = T
        self.runner = None

    def tearDown(self):
        if self.runner:
            self.runner.close()
        self.path.unlink(missing_ok=True)
        self.path.with_suffix('.runner.lock').unlink(missing_ok=True)

    def make_runner(self, **kwargs):
        macro = kwargs.pop('macro_provider', macro_fixture())
        self.runner = DemoRunner(RuntimeConfig(db_path=self.path, market_provider_mode='none',
            macro_provider_mode='fxmacrodata'), market_provider=Data(), ai_provider=DeterministicAIProvider(),
            macro_provider=macro, instruments={'XAUUSD': instrument()}, clock=lambda: self.now, **kwargs)
        return self.runner

    def order_count(self):
        return self.runner.store.db.execute('SELECT count(*) FROM paper_orders').fetchone()[0]

    def test_slot_fresh_but_wall_stale_cannot_create_order(self):
        self.now = T + timedelta(minutes=14)
        runner = self.make_runner()
        with patched_scouts('LONG'):
            self.assertEqual(runner.run_once('XAUUSD', T)['status'], 'STALE_DATA')
        self.assertEqual(self.order_count(), 0)

    def test_wall_freshness_exact_600_seconds_still_allows_existing_path(self):
        self.now = T + timedelta(seconds=600)
        runner = self.make_runner()
        with patched_scouts('LONG'):
            self.assertEqual(runner.run_once('XAUUSD', T)['status'], 'PLAN_READY')
        self.assertEqual(self.order_count(), 1)

    def test_wall_freshness_601_seconds_blocks(self):
        self.now = T + timedelta(seconds=601)
        runner = self.make_runner()
        with patched_scouts('LONG'):
            self.assertEqual(runner.run_once('XAUUSD', T)['status'], 'STALE_DATA')
        self.assertEqual(self.order_count(), 0)

    def test_ai_latency_cannot_submit_with_expired_data(self):
        from runtime.service import run_ai
        runner = self.make_runner()
        def delayed(*args):
            report = run_ai(*args)
            self.now = T + timedelta(seconds=601)
            return report
        with patched_scouts('LONG'), patch('runtime.service.run_ai', side_effect=delayed):
            self.assertEqual(runner.run_once('XAUUSD', T)['status'], 'STALE_DATA')
        self.assertEqual(self.order_count(), 0)

    def test_ai_latency_cannot_fill_a_pending_order_with_expired_data(self):
        from runtime.service import run_ai
        runner = self.make_runner()
        with patched_scouts('LONG'):
            runner.run_once('XAUUSD', T)
        self.now = T + timedelta(minutes=15)
        def delayed(*args):
            report = run_ai(*args)
            self.now += timedelta(seconds=601)
            return report
        with patched_scouts('LONG'), patch('runtime.service.run_ai', side_effect=delayed):
            self.assertEqual(runner.run_once('XAUUSD', T+timedelta(minutes=15))['status'], 'STALE_DATA')
        self.assertEqual(self.order_count(), 1)
        self.assertEqual(runner.store.db.execute('SELECT count(*) FROM paper_fills').fetchone()[0], 0)

    def test_session_boundary_after_ai_blocks_order(self):
        from runtime.service import run_ai
        slot = datetime(2026, 1, 15, 21, 45, tzinfo=timezone.utc)
        self.now = slot + timedelta(minutes=9)
        runner = self.make_runner()
        # Data's timestamp is the slot; make it newer within the slot's
        # permitted evidence cutoff impossible, so cross the boundary with
        # both gates failing and assert session blocking takes precedence.
        def delayed(*args):
            report = run_ai(*args)
            self.now = slot + timedelta(minutes=15)
            return report
        with patched_scouts('LONG'), patch('runtime.service.run_ai', side_effect=delayed):
            self.assertEqual(runner.run_once('XAUUSD', slot)['status'], 'SESSION_SKIPPED')
        self.assertEqual(self.order_count(), 0)

    def test_historical_open_session_cannot_bypass_current_closed_session(self):
        self.now = T + timedelta(days=3)
        runner = self.make_runner()
        self.assertEqual(runner.run_once('XAUUSD', T)['status'], 'SESSION_SKIPPED')
        self.assertEqual(self.order_count(), 0)

    def test_fxmacrodata_high_evidence_reaches_durable_notification(self):
        event = MacroEvent('1.0', 'fxmacrodata:test', 'FXMacroData', T-timedelta(hours=1),
            T+timedelta(hours=2), T-timedelta(hours=1), 'CPI', 'inflation', 'USD', 'HIGH',
            known_at=T-timedelta(hours=1), importance='HIGH')
        runner = self.make_runner(macro_provider=InMemoryMacroNewsProvider(events=(event,)))
        runner.run_once('XAUUSD', T)
        alerts = [e for e in runner.store.notification_events() if e['type'] == 'MACRO_HIGH_IMPORTANCE']
        self.assertEqual(len(alerts), 1)
        self.assertIn('fxmacrodata:test', alerts[0]['evidence_refs'])
        self.assertEqual(build_daily_summary(runner.store, T+timedelta(days=1))['macro_high_events'], 1)

    def test_real_skipped_cycle_is_excluded_from_daily_summary(self):
        self.now = T + timedelta(days=3)
        runner = self.make_runner()
        self.assertEqual(runner.run_once('XAUUSD', self.now)['status'], 'SESSION_SKIPPED')
        self.assertEqual(runner.store.latest_run()['final_status'], 'NO_DATA')
        self.assertEqual(build_daily_summary(runner.store, self.now+timedelta(days=1))['cycles'], 0)

    def test_exclusive_cloud_recovery_releases_recent_interrupted_symbol(self):
        store = Store(self.path)
        try:
            store.claim_slot(slot_key('XAUUSD', T, 15), 'XAUUSD', T, T)
        finally:
            store.close()
        self.now = T + timedelta(seconds=30)
        runner = self.make_runner(recovery_stale_after_seconds=0)
        self.assertEqual(runner.store.latest_run()['status'], 'FAILED')
        self.now = T+timedelta(minutes=15)
        with patched_scouts('LONG'):
            self.assertNotEqual(runner.run_once('XAUUSD', self.now)['status'], 'DUPLICATE')

    def test_cloud_exclusive_lock_wires_approved_specs_and_recent_recovery(self):
        from runtime import cloud_runner
        from runtime.paper_contracts import paper_instruments
        config = RuntimeConfig(db_path=self.path, scheduler_enabled=True, macro_provider_mode='fxmacrodata')
        flock = unittest.mock.Mock()
        fake_fcntl = SimpleNamespace(flock=flock, LOCK_EX=2, LOCK_NB=4)
        # No actual scheduler or runtime is constructed: a fake tick immediately
        # ends the supervisor, while validating what would be wired after lock.
        with patch.dict('os.environ', {'RENDER': 'true', 'AI_FLOOR_CLOUD_RUNNER': '1'}), \
             patch.dict('sys.modules', {'fcntl': fake_fcntl}), \
             patch.object(RuntimeConfig, 'from_env', return_value=config), \
             patch.object(cloud_runner, 'cloud_preflight', return_value=SimpleNamespace(experiment_ready=True)), \
             patch.object(cloud_runner.signal, 'signal'), \
             patch.object(cloud_runner, 'DemoRunner') as factory:
            factory.return_value.tick.side_effect = RuntimeError('end fake loop')
            with self.assertRaisesRegex(RuntimeError, 'end fake loop'):
                cloud_runner.main()
            self.assertEqual(flock.call_count, 1)
            self.assertEqual(factory.call_args.kwargs['instruments'], paper_instruments())
            self.assertEqual(factory.call_args.kwargs['recovery_stale_after_seconds'], 0)
            factory.return_value.close.assert_called_once()
            factory.reset_mock()
            flock.side_effect = BlockingIOError()
            with self.assertRaisesRegex(RuntimeError, 'authority already active'):
                cloud_runner.main()
            factory.assert_not_called()


if __name__ == '__main__':
    unittest.main()
