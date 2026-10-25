"""Regression checks for the second presentation review."""
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from ui.i18n_es import STATUS, status_text
from ui.views.system import demo_health


class HealthRecoveryTests(unittest.TestCase):
    since = datetime(2026, 10, 25, tzinfo=timezone.utc)

    def project(self, error_at, **changes):
        health = dict(status="ONLINE", scheduler="RUNNING", last_run="2026-10-25T12:00:00Z",
                      last_success="2026-10-25T12:00:00Z", last_cycle_status="COMPLETED")
        health.update(changes)
        events = [{"timestamp_utc": error_at, "severity": "ERROR"}]
        original = [dict(event) for event in events]
        result = demo_health(health, None, events, self.since)
        self.assertEqual(events, original)
        return result["status"]

    def test_unclassified_older_error_stays_degraded(self):
        # No component-level recovery evidence: only run failures are resolved by a later success.
        self.assertEqual(self.project("2026-10-25T11:00:00Z"), "DEGRADED")

    def test_new_error(self):
        self.assertEqual(self.project("2026-10-25T13:00:00Z"), "DEGRADED")

    def test_scheduler_stopped(self):
        self.assertEqual(self.project("2026-10-25T11:00:00Z", scheduler="STOPPED"), "STOPPED")

    def test_failed_latest_and_live_degradation(self):
        self.assertEqual(self.project("2026-10-25T11:00:00Z", last_cycle_status="FAILED"), "DEGRADED")
        self.assertEqual(self.project("2026-10-25T11:00:00Z", status="DEGRADED"), "DEGRADED")

    def test_journal_and_failed_run_recover_without_removing_records(self):
        old = [{"as_of": "2026-10-25T11:00:00Z", "timestamp": "2026-10-25T11:00:00Z", "status": "FAILED", "event_type": "RUN_FAILED"}]
        with patch("ui.views.dashboard.records", return_value=old), \
             patch("ui.views.dashboard.payloads", return_value=[]), \
             patch("ui.views.dashboard.notification_rows", return_value=[]):
            health = dict(status="ONLINE", scheduler="RUNNING", last_run="2026-10-25T12:00:00Z",
                          last_success="2026-10-25T12:00:00Z", last_cycle_status="COMPLETED")
            self.assertEqual(demo_health(health, Mock(), [], self.since)["status"], "ONLINE")
        self.assertEqual(len(old), 1)


class PresentationTests(unittest.TestCase):
    def test_alert_inner_container(self):
        css = Path("ui/static/theme.css").read_text()
        self.assertIn('[data-testid="stAlertContainer"]{background:#0d1416!important', css)
        self.assertIn('border-left:3px solid #d9b45a!important', css)
        self.assertIn('stAlertContentError', css)

    def test_compact_kpis_and_daily_loss_row(self):
        html = Path("ui/static/floor/floor.html").read_text()
        self.assertIn("NOOPK='—'", html)
        self.assertIn(' · sin operaciones', html)
        self.assertIn('.kpi.daily-loss{grid-column:1 / -1}', html)
        self.assertIn('font-size:14px;white-space:nowrap;min-width:0;overflow:hidden', html)
        self.assertNotIn('width:min-content', html)
        self.assertNotIn('overflow-wrap:anywhere}.kpi b.txt', html)

    def test_machine_codes_have_explicit_labels(self):
        from execution.conflict_engine import REASON_CODES
        codes = (*REASON_CODES, "ACCOUNT_DRAWDOWN_LIMIT", "PER_TRADE_RISK_LIMIT",
                 "PORTFOLIO_RISK_LIMIT", "UNKNOWN_PENDING_RISK_POLICY", "BREAKEVEN_UNKNOWN",
                 "MANUAL_CLOSE_FAILED", "DAILY_LOSS_HALT", "UNRESOLVED_ACTION",
                 "AI_REVIEW_RECOVERED", "STRUCTURE_REVERSE_SUBMITTED", "RATE_LIMITED",
                 "INVALID_RESPONSE", "TIME_CANCEL_FRIDAY_NY_CLOSE", "instrument_contract_data_unavailable")
        for code in codes:
            with self.subTest(code=code):
                self.assertIn(code, STATUS)
                self.assertNotEqual(status_text(code), code.replace('_', ' ').capitalize())


class StatusLabelTests(unittest.TestCase):
    def test_stopped_stays_operational_label(self):
        from ui.i18n_es import STATUS
        self.assertEqual(STATUS["STOPPED"], "Detenido")
