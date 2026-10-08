"""PAPER demo runner: durable cycles, preflight, and optional no-order diagnosis."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import logging
import os
import sqlite3

from runtime.notifications import NullNotificationSink
from runtime.scheduler import Scheduler, slot_at, slot_key
from runtime.service import OperationalRuntime
from storage.database import Store
from storage.daily_summary import build_daily_summary


REAL_EXECUTION_ENABLED = False
LOG = logging.getLogger("ai_floor.observability")


@dataclass(frozen=True)
class PreflightReport:
    status: str
    checks: dict
    market_provider: str
    ai_provider: str
    enabled_symbols: tuple
    paper_mode: bool
    real_execution_disabled: bool

    def payload(self):
        return asdict(self)


def preflight(config, *, env=None):
    """Local checks only: no network calls, key values, or strategy execution."""
    env = os.environ if env is None else env
    checks = {
        "enabled_symbols": tuple(config.enabled_symbols) == ("XAUUSD", "EURUSD"),
        "market_provider": config.market_provider_mode == "twelve_data",
        "ai_provider": config.ai_provider_mode == "openai",
        "scheduler_configured": config.cadence_minutes == 15 and not config.scheduler_enabled,
        "paper_mode": True,
        "real_execution_disabled": not REAL_EXECUTION_ENABLED,
        "experiment_not_started": True,
        "twelve_data_credential": bool(env.get("TWELVE_DATA_API_KEY")),
        "openai_credential": bool(env.get("OPENAI_API_KEY")),
        "openai_model": env.get("OPENAI_MODEL", "gpt-5.6-terra") == "gpt-5.6-terra",
    }
    from runtime.config import catch_up_storage_checks, persistent_write_probe
    checks.update(catch_up_storage_checks(config, mount=None, disk_mounted=True))  # V2 P8.4 R2; {} while OFF
    try:
        store = Store(config.db_path)
        try:
            checks["db_writable_schema"] = store.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            # P8.4G (DEC-8.11, P8.5R HIGH): persistent write probe, always rolled back (see persistent_write_probe).
            checks["db_writable_schema"] = checks["db_writable_schema"] and persistent_write_probe(
                store.db, config.db_path)
            checks["experiment_not_started"] = store.get_state("experiment_started") != "1"
        finally:
            store.close()
    except (OSError, RuntimeError, ValueError, sqlite3.Error):
        checks["db_writable_schema"] = False
    return PreflightReport("READY" if all(checks.values()) else "NOT_READY", checks,
                           config.market_provider_mode, config.ai_provider_mode,
                           config.enabled_symbols, True, not REAL_EXECUTION_ENABLED)


class DemoRunner:
    def __init__(self, config, *, dry_run=False, diagnostic_outside_session=False,
                 notification_sink=None, experiment_baseline_sha=None, experiment_freeze_sha=None,
                 **runtime_kwargs):
        if (experiment_baseline_sha is None) != (experiment_freeze_sha is None):
            raise ValueError("experiment baseline and freeze SHAs must be supplied together")
        self.config = config
        self.dry_run = bool(dry_run)
        self.sink = notification_sink or NullNotificationSink()
        self.runtime = OperationalRuntime(config, paper_enabled=not self.dry_run,
                                          diagnostic_outside_session=diagnostic_outside_session,
                                          **runtime_kwargs)
        self.store = self.runtime.store
        self.clock = self.runtime.clock
        doctor = preflight(config)
        with self.store.transaction():
            self.store.set_state("runner", "RUNNING")
            self.store.set_state("phase7_readiness", doctor.status)
            self.store.set_state("phase7_preflight_checks", json.dumps(doctor.checks, sort_keys=True))
        if experiment_baseline_sha is not None:
            self.store.start_experiment_if_unstarted(self.clock(), experiment_baseline_sha,
                                                     experiment_freeze_sha)
        self._capture_and_deliver()

    def _capture_and_deliver(self, *, run_id=None):
        try:
            events = self.store.capture_notifications(run_id=run_id)
            self._deliver(events)
        except Exception as exc:
            LOG.warning("component=notifications event=CAPTURE_OR_DELIVERY_FAILED error_type=%s",
                        type(exc).__name__)

    def _deliver(self, events):
        for event in events:
            durable = False
            try:
                durable = getattr(self.sink, "durable_delivery", False)
                if durable and not self.store.claim_notification_delivery(event.event_id, self.clock()):
                    continue
                self.sink.deliver(event)
                if durable:
                    self.store.complete_notification_delivery(event.event_id, self.clock())
            except Exception as exc:
                # The persisted event and trading outcome are the source of truth.
                if durable:
                    try:
                        self.store.complete_notification_delivery(event.event_id, self.clock(), type(exc).__name__)
                    except Exception as record_error:
                        LOG.warning("component=notifications event=DELIVERY_RECORD_FAILED error_type=%s",
                                    type(record_error).__name__)

    def run_cycle(self, symbol, scheduled_at):
        return self.run_once(symbol, scheduled_at)["status"]

    def run_once(self, symbol, scheduled_at=None):
        at = scheduled_at or self.clock()
        slot = slot_at(at, self.config.cadence_minutes)
        status = self.runtime.run_cycle(symbol, slot)
        key = slot_key(symbol, slot, self.config.cadence_minutes)
        run = self.store.run(key)
        if run is None:
            raise RuntimeError("cycle has no durable run")
        # A position close belongs to its opening run, even when observed in a
        # later cycle. Capture all newly committed journal rows exactly once.
        self._capture_and_deliver()
        try:
            review = self.store.review_report(run["run_id"])
            journal_count = len(self.store.journal(run_id=run["run_id"]))
        except Exception as exc:
            LOG.warning("component=observability event=RESULT_READ_FAILED error_type=%s",
                        type(exc).__name__)
            review, journal_count = None, 0
        return {"schema_version": "1.0", "run_id": run["run_id"], "symbol": symbol,
                "status": status, "durable_status": run["status"],
                "review_durable": review is not None,
                "journal_count": journal_count,
                "paper_orders_enabled": not self.dry_run}

    def tick(self):
        return Scheduler(self, self.clock).tick()

    def daily_summary(self, at=None):
        """Persist the previous complete UTC day, then attempt notification once."""
        at = at or self.clock()
        with self.store.transaction():
            summary = build_daily_summary(self.store, at)
            date = summary["date_utc"]
            if self.store.db.execute(
                "SELECT 1 FROM journal WHERE event_type='DAILY_SUMMARY' AND run_id=?",
                (f"daily:{date}",)).fetchone():
                return False
            self.store.set_state("daily_summary_date", date)
            self.store._event(at, f"daily:{date}", None, "demo_runner", "DAILY_SUMMARY", "INFO",
                              summary)
        self._capture_and_deliver(run_id=f"daily:{date}")
        return True

    def close(self):
        with self.store.transaction():
            self.store.set_state("runner", "STOPPED")
        self.runtime.close()
