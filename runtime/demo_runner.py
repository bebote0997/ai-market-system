"""PAPER demo runner: durable cycles, preflight, and optional no-order diagnosis."""
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
import logging
import os
from pathlib import Path
import sqlite3

from runtime.alert_hook import AlertHook
from runtime.notification_extras import capture_ai_review_health, with_trade
from runtime.notifications import NotificationEvent, NullNotificationSink
from runtime.scheduler import Scheduler, slot_at, slot_key
from runtime.service import OperationalRuntime
from storage.codec import utc
from storage.database import Store
from storage.daily_summary import build_daily_summary, pending_summary_days
from runtime.halt import HaltRefused, startup_write


REAL_EXECUTION_ENABLED = False
SUMMARY_CLAIM_GRACE = timedelta(minutes=15)  # a summary claim older than this without an outcome is a crash mid-send
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
    from runtime.config import catch_up_scope_checks, catch_up_storage_checks, persistent_write_probe
    checks.update(catch_up_storage_checks(config, mount=None, disk_mounted=True))  # V2 P8.4 R2; {} while OFF
    checks.update(catch_up_scope_checks(config))  # V2 P1-B: scope and STRICT evidence; {} while OFF
    from runtime.genesis import sealed_preflight_checks
    sealed = sealed_preflight_checks(config, env)  # V2 P2b (R-GEN-1d, D-3): {} while OFF; before any DB open
    checks.update(sealed)
    try:
        if sealed and (not sealed["sealed_genesis_context"] or not Path(config.db_path).exists()):
            raise FileNotFoundError("sealed preflight: no verified context or no existing file (never created)")
        if sealed:  # LOW-1: non-creating open (mode=rw)
            from runtime.genesis import SealedStore
            store = SealedStore(config.db_path)
        else:
            store = Store(config.db_path)
        try:
            checks["db_writable_schema"] = store.db.execute("PRAGMA quick_check").fetchone()[0] == "ok"
            # P8.4G (DEC-8.11, P8.5R HIGH): persistent write probe, always rolled back (see persistent_write_probe).
            checks["db_writable_schema"] = checks["db_writable_schema"] and persistent_write_probe(
                store.db, config.db_path)
            checks["experiment_not_started"] = store.get_state("experiment_started") != "1"
        finally:
            store.close()
    except (OSError, RuntimeError, ValueError, sqlite3.Error):  # GenesisSealError is a RuntimeError
        checks["db_writable_schema"] = False
    return PreflightReport("READY" if all(checks.values()) else "NOT_READY", checks,
                           config.market_provider_mode, config.ai_provider_mode,
                           config.enabled_symbols, True, not REAL_EXECUTION_ENABLED)


class DemoRunner:
    halt_gate = None  # V2 P4a R-HALT; None = legacy (flag OFF)

    def __init__(self, config, *, dry_run=False, diagnostic_outside_session=False,
                 notification_sink=None, experiment_baseline_sha=None, experiment_freeze_sha=None,
                 alert_hook=None, **runtime_kwargs):
        if (experiment_baseline_sha is None) != (experiment_freeze_sha is None):
            raise ValueError("experiment baseline and freeze SHAs must be supplied together")
        self.config = config
        self.dry_run = bool(dry_run)
        self.sink = notification_sink or NullNotificationSink()
        self.alert_hook = alert_hook or AlertHook()
        self.runtime = OperationalRuntime(config, paper_enabled=not self.dry_run,
                                          diagnostic_outside_session=diagnostic_outside_session,
                                          **runtime_kwargs)
        self.store = self.runtime.store
        self.clock = self.runtime.clock
        self.halt_gate = self.runtime.halt_gate  # V2 P4a R-HALT; None = legacy (flag OFF)
        try:
            self._start(config, experiment_baseline_sha, experiment_freeze_sha)
        except HaltRefused:
            self.store.close()  # V2 P4a MEDIUM-1: no runner state, experiment or notification write after T_h
            raise

    def _start(self, config, experiment_baseline_sha, experiment_freeze_sha):
        gate = self.halt_gate
        with startup_write(gate, "preflight"):  # the preflight write probe opens a transaction on main
            doctor = preflight(config)
        with startup_write(gate, "runner_state"):
            with self.store.transaction():
                self.store.set_state("runner", "RUNNING")
                self.store.set_state("phase7_readiness", doctor.status)
                self.store.set_state("phase7_preflight_checks", json.dumps(doctor.checks, sort_keys=True))
        if experiment_baseline_sha is not None:
            with startup_write(gate, "experiment_start"):  # never start or modify an experiment after T_h
                self.store.start_experiment_if_unstarted(self.clock(), experiment_baseline_sha,
                                                         experiment_freeze_sha)
        with startup_write(gate, "notifications"):
            self._capture_and_deliver()

    def _capture_and_deliver(self, *, run_id=None):
        try:
            events = self.store.capture_notifications(run_id=run_id)
            # Pre-demo: order/position alerts carry durable trade data; AI review health alerts (transitions).
            events = with_trade(self.store, events) + capture_ai_review_health(self.store, run_id=run_id)
            self._deliver(events)
        except Exception as exc:
            LOG.warning("component=notifications event=CAPTURE_OR_DELIVERY_FAILED error_type=%s",
                        type(exc).__name__)
            return
        try:  # V2 Phase 9: alerts read only the events persisted above; OFF unless AI_FLOOR_ALERTS_ENABLED=1
            self.alert_hook(events)
        except Exception as exc:
            LOG.warning("component=alerts event=HOOK_FAILED error_type=%s", type(exc).__name__)

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
        if status in {"HALT_REFUSED", "SYMBOL_PAUSED"}:  # V2 P4a / DEC-XAU-OFF-1: no claim, no run, nothing persisted
            return {"schema_version": "1.0", "run_id": None, "symbol": symbol, "status": status,
                    "durable_status": None, "review_durable": False, "journal_count": 0,
                    "paper_orders_enabled": not self.dry_run}
        key = slot_key(symbol, slot, self.config.cadence_minutes)
        run = self.store.run(key)
        if run is None:
            raise RuntimeError("cycle has no durable run")
        # A position close belongs to its opening run, even when observed in a
        # later cycle. Capture all newly committed journal rows exactly once.
        gate = getattr(self, "halt_gate", None)
        if gate is None or gate.allow("S"):
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

    def manage_trade_duration(self, at):
        """Out-of-session trade-duration pass (Scheduler); its closes are notified like any other."""
        results = self.runtime.manage_trade_duration(at)
        gate = getattr(self, "halt_gate", None)
        if results and (gate is None or gate.allow("S")):
            self._capture_and_deliver()
        return results

    def process_operator_requests(self, at):
        """Between-tick poll of the dashboard requests (cloud runner loop); its closes are notified like any other."""
        results = self.runtime.process_operator_requests(at)
        gate = getattr(self, "halt_gate", None)
        if results and (gate is None or gate.allow("S")):
            self._capture_and_deliver()
        return results

    def close_paused_symbols(self, at):
        """DEC-XAU-OFF-1 in-session flatten of paused symbols (Scheduler); its close is notified like any other."""
        results = self.runtime.close_paused_symbols(at)
        gate = getattr(self, "halt_gate", None)
        if results and (gate is None or gate.allow("S")):
            self._capture_and_deliver()
        return results

    def tick(self):
        return Scheduler(self, self.clock).tick()

    def flush_evidence_observations(self):
        self.runtime.flush_evidence_observations()

    def daily_summary(self, at=None):
        """Persist the summary of each forex trading day (17:00 -> 17:00 America/New_York, labelled by the NY date of
        its 17:00 end) that has ended and was not summarized yet, then attempt notification once per day; deduped
        durably on that label (journal run_id ``daily:<date>``, last label in state ``daily_summary_date``). Weekend
        and catch-up rules: ``storage.daily_summary.pending_summary_days``."""
        gate = getattr(self, "halt_gate", None)  # None on legacy-built runners (flag OFF)
        if gate is not None and not gate.allow("S"):
            return False  # V2 P4a: summaries and notifications are suppressed after T_h
        at = at or self.clock()
        persisted = []
        with self.store.transaction():
            for day in pending_summary_days(self.store.get_state("daily_summary_date"), at):
                date = day.isoformat()
                if self.store.db.execute(
                        "SELECT 1 FROM journal WHERE event_type='DAILY_SUMMARY' AND run_id=?",
                        (f"daily:{date}",)).fetchone():
                    continue
                summary = build_daily_summary(self.store, at, day)
                self.store.set_state("daily_summary_date", date)
                self.store._event(at, f"daily:{date}", None, "demo_runner", "DAILY_SUMMARY", "INFO",
                                  summary)
                persisted.append(date)
        for date in persisted:
            self._capture_and_deliver(run_id=f"daily:{date}")
        self._summary_outbox(at)
        return bool(persisted)

    def _summary_outbox(self, at):
        """Durable sinks only: deliver DAILY_SUMMARY journal rows / notification events a crash left uncaptured or
        never claimed; a claim older than ``SUMMARY_CLAIM_GRACE`` without an outcome is marked UNKNOWN, not resent."""
        if not getattr(self.sink, "durable_delivery", False):
            return
        db = self.store.db
        summary = "json_extract(e.payload,'$.type')='DAILY_SUMMARY'"
        try:
            for (run_id,) in db.execute(
                    "SELECT j.run_id FROM journal j WHERE j.event_type='DAILY_SUMMARY' AND NOT EXISTS ("
                    "SELECT 1 FROM notification_events e WHERE e.journal_id=j.id) ORDER BY j.id").fetchall():
                self._capture_and_deliver(run_id=run_id)  # crash between the journal commit and the capture
            self._deliver([NotificationEvent(**json.loads(row[0])) for row in db.execute(
                "SELECT e.payload FROM notification_events e LEFT JOIN notification_deliveries d USING(event_id) "
                f"WHERE {summary} AND d.event_id IS NULL ORDER BY e.journal_id").fetchall()])  # never claimed
            with self.store.transaction():  # claimed, no outcome: Slack cannot dedupe -> UNKNOWN, never resent
                stale = db.execute(
                    "SELECT d.event_id, json_extract(e.payload,'$.run_id') FROM notification_deliveries d "
                    f"JOIN notification_events e USING(event_id) WHERE d.status='CLAIMED' AND {summary} "
                    "AND datetime(d.attempted_at) <= datetime(?) ORDER BY e.journal_id",
                    (utc(at - SUMMARY_CLAIM_GRACE),)).fetchall()
                for event_id, _ in stale:
                    db.execute("UPDATE notification_deliveries SET status='UNKNOWN' WHERE event_id=? "
                               "AND status='CLAIMED'", (event_id,))
            for _, run_id in stale:
                LOG.warning("component=notifications event=DAILY_SUMMARY_UNKNOWN date=%s",
                            str(run_id).removeprefix("daily:"))
        except Exception as exc:
            LOG.warning("component=notifications event=SUMMARY_OUTBOX_FAILED error_type=%s", type(exc).__name__)

    def close(self):
        gate = getattr(self, "halt_gate", None)
        if gate is not None and gate.halted and gate.recorded_journal_id is None:
            # V2 P4a: a halt observed outside any run (between ticks / symbols) is still recorded durably (H).
            from runtime.halt import halt_bookkeeping
            try:
                halt_bookkeeping(self.store, gate)
            except Exception as exc:  # noqa: BLE001 - rolled back: the halt is NOT confirmed
                LOG.error("component=halt event=HALT_UNCONFIRMED error_type=%s", type(exc).__name__)
        with self.store.transaction():
            self.store.set_state("runner", "STOPPED")
        if isinstance(self.alert_hook, AlertHook):
            self.alert_hook.close(timeout=0)  # daemon drains independently; shutdown never waits on delivery
        self.runtime.close()
