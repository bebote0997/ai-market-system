"""Provider transition: one explicit field, flat account, no silent resume, history kept."""
import sqlite3
import unittest
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from runtime.cloud import cloud_preflight
from runtime.config import RuntimeConfig
from runtime.provider_transition import (load_transitions, record_provider_transition, transition_resumes)
from storage.database import Store
from test_cloud_resume import FREEZE, ExistingExperimentPreflight


WHEN = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)


def _payload(mode):
    config = replace(RuntimeConfig(ai_provider_mode=mode), scheduler_enabled=True)
    return config.fingerprint_content()


class TransitionRules(unittest.TestCase):
    def setUp(self):
        self.path = Path("data/runtime") / f"transition-{uuid4().hex}.db"
        self.addCleanup(self.path.unlink, missing_ok=True)
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.origin = _payload("openai")
        self.dest = _payload("xai")

    def test_only_the_provider_field_can_be_recorded_and_only_when_flat(self):
        record_provider_transition(self.db, from_payload=self.origin, to_payload=self.dest,
                                   open_positions=0, operator_note="reviewed openai to xai", recorded_at=WHEN)
        self.assertEqual(len(load_transitions(self.db)), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE provider_transitions SET operator_note = 'changed'")
        with self.assertRaises(ValueError):
            record_provider_transition(self.db, from_payload=self.origin, to_payload=self.dest,
                                       open_positions=1, operator_note="not flat", recorded_at=WHEN)
        wider = dict(self.dest)
        wider["sessions"] = ["LONDON"]
        with self.assertRaises(ValueError):
            record_provider_transition(self.db, from_payload=self.origin, to_payload=wider,
                                       open_positions=0, operator_note="too many fields", recorded_at=WHEN)
        self.assertEqual(len(load_transitions(self.db)), 1)

    def test_unapproved_and_extra_fingerprints_do_not_resume(self):
        origin_fp = RuntimeConfig(ai_provider_mode="openai").fingerprint()
        # fingerprint() uses scheduler False by default; the resume payload forces scheduler True.
        origin_fp = replace(RuntimeConfig(ai_provider_mode="openai"), scheduler_enabled=True).fingerprint()
        dest_fp = replace(RuntimeConfig(ai_provider_mode="xai"), scheduler_enabled=True).fingerprint()
        rows = [(FREEZE, origin_fp, 10000.0)]
        self.assertFalse(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.dest, starting_equity=10000.0, records=()))
        record_provider_transition(self.db, from_payload=self.origin, to_payload=self.dest,
                                   open_positions=0, operator_note="reviewed", recorded_at=WHEN)
        records = load_transitions(self.db)
        self.assertTrue(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.dest, starting_equity=10000.0, records=records))
        self.assertFalse(transition_resumes(
            rows + [(FREEZE, "f" * 64, 10000.0)], freeze=FREEZE, freeze_ok=True, started=True,
            account_present=True, current_payload=self.dest, starting_equity=10000.0, records=records))
        self.assertFalse(transition_resumes(
            [(FREEZE, dest_fp, 10000.0)], freeze=FREEZE, freeze_ok=True, started=True,
            account_present=True, current_payload=self.dest, starting_equity=10000.0, records=records))
        self.assertNotEqual(origin_fp, dest_fp)

    def test_a_reverse_step_is_a_second_explicit_record(self):
        record_provider_transition(self.db, from_payload=self.origin, to_payload=self.dest,
                                   open_positions=0, operator_note="forward", recorded_at=WHEN)
        record_provider_transition(self.db, from_payload=self.dest, to_payload=self.origin,
                                   open_positions=0, operator_note="reverse", recorded_at=WHEN)
        origin_fp = replace(RuntimeConfig(ai_provider_mode="openai"), scheduler_enabled=True).fingerprint()
        rows = [(FREEZE, origin_fp, 10000.0)]
        self.assertTrue(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.origin, starting_equity=10000.0, records=load_transitions(self.db)))
        broken = dict(load_transitions(self.db)[1])
        broken["to_payload"] = dict(broken["to_payload"], macro_provider_mode="finnhub")
        self.assertFalse(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.origin, starting_equity=10000.0,
            records=(load_transitions(self.db)[0], broken)))


class ResumeIntegration(ExistingExperimentPreflight):
    def test_provider_drift_stays_blocked_until_an_explicit_flat_record(self):
        drifted = replace(self.config, ai_provider_mode="xai")
        blocked = cloud_preflight(drifted, env={**self.env, "XAI_API_KEY": "present"}, disk_mounted=True)
        self.assertFalse(blocked.checks["experiment_resumable"])
        before = self.snapshot()
        with closing(Store(self.path)) as store:
            record_provider_transition(
                store.db, from_payload=replace(self.config, scheduler_enabled=True).fingerprint_content(),
                to_payload=replace(drifted, scheduler_enabled=True).fingerprint_content(),
                open_positions=0, operator_note="owner reviewed openai to xai", recorded_at=WHEN)
        resumed = cloud_preflight(drifted, env={**self.env, "XAI_API_KEY": "present"}, disk_mounted=True)
        self.assertTrue(resumed.checks["experiment_resumable"])
        with closing(Store(self.path, readonly=True)) as store:
            fingerprints = {row[0] for row in store.db.execute("SELECT config_fingerprint FROM run_metadata")}
        self.assertEqual(fingerprints, {replace(self.config, scheduler_enabled=True).fingerprint()})
        self.assertEqual([row for row in self.snapshot() if row.startswith("INSERT INTO \"run_metadata\"")],
                         [row for row in before if row.startswith("INSERT INTO \"run_metadata\"")])


if __name__ == "__main__":
    unittest.main()
