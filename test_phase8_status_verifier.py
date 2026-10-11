"""DEC-8.18a status verification on deterministic temporary Owner copies."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stderr
import io

from replay.status_verifier import BASELINE, EX_NOTE, main, sha256, verify
from storage.database import Store
from storage.evidence_store import EvidenceStore

T = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)


class StatusVerifierTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / 'copy #?.db'
        self.store = Store(self.db)
        self.addCleanup(self.store.close)

    def report(self, **kwargs):
        return verify(self.db, copy_time=(T + timedelta(hours=1)).isoformat(),
                      now_utc=(T + timedelta(days=14)).isoformat(), **kwargs)

    def start(self):
        self.store.start_experiment_if_unstarted(T, BASELINE, 'a' * 40)

    def test_fresh_not_started(self):
        report = self.report()
        self.assertEqual(report['axes']['FS'], 'NOT_STARTED')
        self.assertEqual(report['axes']['PD'], 'NOT APPLICABLE')
        self.assertEqual(report['axes']['RS'], 'UNCERTIFIED')
        self.assertEqual(report['axes']['EX'], 'UNKNOWN')
        self.assertEqual(report['axes']['EX_attestation'], EX_NOTE)
        self.assertEqual(report['integrity'], 'OK')
        self.assertEqual(set(report['checks']), {'Q' + str(i) for i in range(1, 9)})

    def test_consistent_start_and_elapsed_boundary(self):
        self.start()
        report = self.report()
        self.assertEqual(report['axes']['FS'], 'STARTED')
        self.assertEqual(report['axes']['PD'], 'PERIOD_ELAPSED')
        self.assertEqual(report['integrity'], 'OK')
        report = verify(self.db, copy_time=T.isoformat(), now_utc=(T + timedelta(days=14, seconds=-1)).isoformat())
        self.assertEqual(report['axes']['PD'], 'NOT_ELAPSED')

    def test_duplicate_start(self):
        self.start()
        self.store.event(T, None, None, 'experiment', 'EXPERIMENT_STARTED',
                         payload={'baseline_sha': BASELINE, 'freeze_sha': 'a' * 40})
        report = self.report()
        self.assertEqual(report['axes']['FS'], 'START_INCONSISTENT')
        self.assertEqual(report['integrity'], 'INCONSISTENT')
        self.assertEqual(report['decision'], 'STOP')
        self.assertEqual(report['checks']['Q3']['count'], 2)

    def test_bad_start_contracts(self):
        self.start()
        for key, value in [('experiment_started_at_utc', '2026-09-19T12:00:00'),
                           ('experiment_started_at_utc', '2026-09-21T12:00:00Z'),
                           ('experiment_baseline_sha', 'b' * 40),
                           ('experiment_freeze_sha', 'invalid')]:
            with self.subTest(key=key, value=value):
                old = self.store.get_state(key)
                self.store.set_state(key, value)
                self.assertEqual(self.report()['axes']['FS'], 'START_INCONSISTENT')
                self.store.set_state(key, old)
        self.assertEqual(verify(self.db, now_utc=T.isoformat())['axes']['FS'], 'START_INCONSISTENT')

    def test_execution_db_only(self):
        self.store.set_state('runner', 'RUNNING')
        self.store.set_state('heartbeat', (T + timedelta(minutes=30)).isoformat())
        self.assertEqual(self.report()['axes']['EX'], 'RUNNING')
        self.store.set_state('heartbeat', T.isoformat())
        self.assertEqual(self.report()['axes']['EX'], 'UNKNOWN')
        self.store.set_state('runner', 'STOPPED')
        self.assertEqual(self.report()['axes']['EX'], 'STOPPED')
        self.store.claim_slot('interrupted', 'XAUUSD', T, T, run_id='interrupted')
        report = self.report()
        self.assertEqual(report['axes']['EX'], 'STOPPED')
        self.assertEqual([r['run_id'] for r in report['recover_runs']], ['interrupted'])
        self.store.set_state('heartbeat', (T + timedelta(hours=2)).isoformat())
        self.assertEqual(self.report()['axes']['EX'], 'UNKNOWN')

    def test_schema_mismatch(self):
        self.store.db.execute('UPDATE schema_info SET version=2')
        report = self.report()
        self.assertEqual(report['integrity'], 'INCONSISTENT')
        self.assertEqual(report['checks']['Q1']['schema_versions'], [2])

    def test_corrupt_database(self):
        bad = Path(self.tmp.name) / 'corrupt.db'
        bad.write_bytes(b'not sqlite')
        before = sha256(bad)
        report = verify(bad, copy_time=T.isoformat(), now_utc=T.isoformat())
        self.assertEqual(report['integrity'], 'INCONSISTENT')
        self.assertTrue(report['errors'])
        self.assertEqual(before, sha256(bad))

    def test_unchanged_and_json_cli(self):
        self.start()
        ev = Path(self.tmp.name) / 'evidence.db'
        EvidenceStore(ev).close()
        before = [sha256(path) for path in (self.db, ev)]
        report = self.report(evidence_db=ev)
        self.assertEqual(report['checks']['Q8']['gate']['status'], 'CLEAR')
        self.assertEqual(before, [sha256(path) for path in (self.db, ev)])
        self.assertTrue(all(h['unchanged'] for h in report['hashes'].values()))
        out = Path(self.tmp.name) / 'report.json'
        self.assertEqual(main(['--trading-db', str(self.db), '--copy-time', T.isoformat(), '--out', str(out)]), 0)
        self.assertEqual(json.loads(out.read_text())['axes']['FS'], 'STARTED')
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            main(['--trading-db', str(self.db), '--out', str(self.db)])
        self.assertEqual(before[0], sha256(self.db))

    def test_history_orphan_and_missing_metadata(self):
        self.start()
        self.store.claim_slot('slot', 'XAUUSD', T, T, run_id='r')
        report = self.report()
        self.assertEqual(report['integrity'], 'OK')
        self.assertIn('METADATA_MISSING_NONECONOMIC', [r.get('flag') for r in report['checks']['Q7']['rows']])
        self.store.event(T, 'r', 'XAUUSD', 'missing', 'POSITION_CLOSED')
        report = self.report()
        self.assertEqual(report['integrity'], 'INCONSISTENT')
        self.assertTrue(any(f['rule'] == 'E7' for f in report['checks']['Q7']['findings']))

    def test_invalid_utc_argument(self):
        with self.assertRaises(ValueError):
            verify(self.db, copy_time='2026-09-19T12:00:00')

    def test_unmapped_history_and_sequence_mismatch(self):
        self.start()
        self.store.claim_slot('slot', 'XAUUSD', T, T, run_id='r')
        self.store.db.execute("DELETE FROM journal WHERE event_type='RUN_STARTED'")
        report = self.report()
        self.assertEqual(report['integrity'], 'INCONSISTENT')
        self.assertTrue({'M2', 'M9', 'M10'} <= {f['rule'] for f in report['checks']['Q7']['findings']})

    def test_window_excludes_end_but_preserves_membership(self):
        self.start()
        end = T + timedelta(days=14)
        self.store.claim_slot('late', 'XAUUSD', end, end, run_id='late-run')
        report = self.report()
        self.assertEqual(report['runs_after_period'], ['late'])
        rows = [r for r in report['checks']['Q7']['rows'] if r.get('period_flag')]
        self.assertTrue(rows)
        self.assertEqual(rows[0]['category'], 'S1')

    def test_no_result_certification_inferred_from_event(self):
        self.start()
        self.store.event(T, None, None, 'experiment', 'EXPERIMENT_RESULT_CERTIFIED',
                         payload={'segment_id': 'fake', 'segment_hash': 'a' * 64})
        self.assertEqual(self.report()['axes']['RS'], 'UNCERTIFIED')

    def test_real_economic_history_and_duplicate_close(self):
        from test_demo_runner import T as runtime_time
        from test_phase8_rex_writer import plan_scenario
        self.store.start_experiment_if_unstarted(runtime_time, BASELINE, 'a' * 40)
        plan_scenario(self.db)
        report = self.report()
        self.assertEqual(report['integrity'], 'OK', report['checks']['Q7'])
        rows = report['checks']['Q7']['rows']
        self.assertTrue(any(r['table'] == 'closed_trades' and r['category'] == 'S1' for r in rows))
        self.store.db.execute("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                              "SELECT timestamp,run_id,symbol,source,event_type,severity,payload FROM journal "
                              "WHERE event_type='POSITION_CLOSED'")
        report = self.report()
        self.assertEqual(report['integrity'], 'INCONSISTENT')
        self.assertTrue(any(f['rule'] == 'E7' for f in report['checks']['Q7']['findings']))


if __name__ == '__main__':
    unittest.main()
