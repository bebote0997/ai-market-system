"""PAPER demo runner: durable cycles, preflight, and optional no-order diagnosis."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
import sqlite3

from runtime.notifications import NullNotificationSink
from runtime.scheduler import Scheduler, slot_at, slot_key
from runtime.service import OperationalRuntime
from storage.database import Store


REAL_EXECUTION_ENABLED = False


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
    try:
        store = Store(config.db_path)
        try:
            checks["db_writable_schema"] = store.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            if checks["db_writable_schema"]:
                with store.transaction():
                    store.db.execute("CREATE TEMP TABLE IF NOT EXISTS phase7_write_probe(value INTEGER)")
                    store.db.execute("INSERT INTO phase7_write_probe VALUES(1)")
                    store.db.execute("DELETE FROM phase7_write_probe")
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
                 notification_sink=None, **runtime_kwargs):
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
            self.store.set_state("phase7_readiness", doctor.status)
            self.store.set_state("phase7_preflight_checks", json.dumps(doctor.checks, sort_keys=True))
        self._deliver(self.store.capture_notifications())

    def _deliver(self, events):
        for event in events:
            try:
                self.sink.deliver(event)
            except Exception:
                # The persisted event and trading outcome are the source of truth.
                pass

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
        self._deliver(self.store.capture_notifications())
        review = self.store.review_report(run["run_id"])
        return {"schema_version": "1.0", "run_id": run["run_id"], "symbol": symbol,
                "status": status, "durable_status": run["status"],
                "review_durable": review is not None,
                "journal_count": len(self.store.journal(run_id=run["run_id"])),
                "paper_orders_enabled": not self.dry_run}

    def tick(self):
        return Scheduler(self, self.clock).tick()

    def daily_summary(self, at=None):
        at = at or self.clock()
        date = at.astimezone(timezone.utc).date().isoformat()
        with self.store.transaction():
            if self.store.get_state("daily_summary_date") == date:
                return False
            count = self.store.db.execute("SELECT count(*) FROM runs WHERE substr(as_of,1,10)=?", (date,)).fetchone()[0]
            self.store.set_state("daily_summary_date", date)
            self.store._event(at, f"daily:{date}", None, "demo_runner", "DAILY_SUMMARY", "INFO",
                              {"date": date, "cycles": count})
        self._deliver(self.store.capture_notifications(run_id=f"daily:{date}"))
        return True

    def close(self):
        self.runtime.close()
