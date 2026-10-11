"""V2 Phase 9: alert dispatch. OFF BY DEFAULT; never raises; never touches trading state.

``AI_FLOOR_ALERTS_ENABLED`` must be exactly ``1`` to enable delivery; anything else (including absent or
malformed) is OFF. The dispatcher is not wired into any order or trading path: callers hand it an
``AlertEvent`` (an immutable text snapshot) and get back a status mapping. Every error — rendering, channel,
ledger — is recorded/logged by type and swallowed.

Delivery status is durable per (event_id, channel) in a SQLite ledger (``:memory:`` unless a path is given):
a claim row is inserted before the first attempt, so an event_id is attempted at most once per channel
(deduplication, F09-T14) even across processes sharing the ledger file. Transient failures are retried with
bounded exponential backoff (F09-T17); the final DELIVERED/FAILED outcome and attempt count are stored
(F09-T15) and consecutive failures per channel are tracked for failure detection (F09-T16).
"""
from datetime import datetime, timezone
import logging
import os
import sqlite3
import threading
import time

from runtime.alert_catalog import PRIORITY_RANK, AlertEvent, from_notification

LOG = logging.getLogger(__name__)
ENABLED_ENV = "AI_FLOOR_ALERTS_ENABLED"
MAX_ATTEMPTS_LIMIT = 5
MAX_DELAY_LIMIT = 30.0
FAILING_THRESHOLD = 3

_SCHEMA = """CREATE TABLE IF NOT EXISTS alert_deliveries(
event_id TEXT NOT NULL, channel TEXT NOT NULL, event_type TEXT NOT NULL, priority TEXT NOT NULL,
status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, error_type TEXT,
claimed_at TEXT NOT NULL, completed_at TEXT, PRIMARY KEY(event_id, channel))"""


def alerts_enabled(environ=None):
    env = os.environ if environ is None else environ
    return env.get(ENABLED_ENV, "0") == "1"


def _utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class AlertDeliveryLedger:
    def __init__(self, path=":memory:"):
        self._lock = threading.Lock()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.execute(_SCHEMA)

    def close(self):
        self.db.close()

    def __del__(self):
        db = getattr(self, "db", None)
        if db is not None:
            db.close()

    def claim(self, event, channel, at):
        with self._lock:
            return self.db.execute(
                "INSERT OR IGNORE INTO alert_deliveries(event_id,channel,event_type,priority,status,claimed_at) "
                "VALUES(?,?,?,?,'SENDING',?)",
                (event.event_id, channel, event.event_type, event.priority, at)).rowcount == 1

    def complete(self, event_id, channel, at, attempts, error_type=None):
        with self._lock:
            self.db.execute(
                "UPDATE alert_deliveries SET status=?,attempts=?,error_type=?,completed_at=? "
                "WHERE event_id=? AND channel=? AND status='SENDING'",
                ("FAILED" if error_type else "DELIVERED", attempts, error_type, at, event_id, channel))

    def status(self, event_id):
        with self._lock:
            rows = self.db.execute("SELECT channel,status,attempts,error_type FROM alert_deliveries "
                                   "WHERE event_id=? ORDER BY channel", (event_id,)).fetchall()
        return {channel: {"status": status, "attempts": attempts, "error_type": error}
                for channel, status, attempts, error in rows}

    def failures(self):
        with self._lock:
            rows = self.db.execute("SELECT event_id,channel,event_type,priority,attempts,error_type "
                                   "FROM alert_deliveries WHERE status='FAILED' ORDER BY claimed_at").fetchall()
        keys = ("event_id", "channel", "event_type", "priority", "attempts", "error_type")
        return [dict(zip(keys, row)) for row in rows]


class AlertDispatcher:
    def __init__(self, channels, *, enabled=None, ledger=None, sleep=time.sleep, clock=_utc_now,
                 max_attempts=3, base_delay=1.0, max_delay=8.0, environ=None):
        if not 1 <= int(max_attempts) <= MAX_ATTEMPTS_LIMIT:
            raise ValueError("max_attempts out of bounds")
        if not 0 <= float(base_delay) <= float(max_delay) <= MAX_DELAY_LIMIT:
            raise ValueError("backoff out of bounds")
        # Only an explicit True (or env value exactly "1") enables delivery.
        self.enabled = alerts_enabled(environ) if enabled is None else enabled is True
        self.channels = tuple(channels)
        self.ledger = ledger if ledger is not None else AlertDeliveryLedger()
        self.sleep, self.clock = sleep, clock
        self.max_attempts, self.base_delay, self.max_delay = int(max_attempts), float(base_delay), float(max_delay)
        self._consecutive_failures = {channel.name: 0 for channel in self.channels}
        self._last_error = {channel.name: None for channel in self.channels}

    def backoff(self, attempt):
        """Delay before retry number ``attempt`` (1-based): base * 2**(attempt-1), capped at max_delay."""
        return min(self.max_delay, self.base_delay * (2 ** (attempt - 1)))

    def dispatch(self, event):
        """Deliver one alert to every channel. Returns {channel: status}; never raises."""
        try:
            if not self.enabled:
                return {"_": "DISABLED"}
            if not isinstance(event, AlertEvent):
                return {"_": "INVALID_EVENT"}
            return {channel.name: self._deliver(channel, event) for channel in self.channels}
        except Exception as exc:  # noqa: BLE001 - alerting never controls anything
            LOG.warning("component=alerts event=DISPATCH_FAILED error_type=%s", type(exc).__name__)
            return {"_": "DISPATCH_FAILED"}

    def dispatch_many(self, events):
        """Dispatch highest priority first; returns {event_id: {channel: status}}; never raises."""
        try:
            ordered = sorted(events, key=lambda e: PRIORITY_RANK[e.priority])
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=alerts event=DISPATCH_FAILED error_type=%s", type(exc).__name__)
            return {}
        return {event.event_id: self.dispatch(event) for event in ordered}

    def _deliver(self, channel, event):
        try:
            if not getattr(channel, "configured", True):
                return "NOT_CONFIGURED"  # nothing claimed: configuring the channel later can still deliver
            if not self.ledger.claim(event, channel.name, self.clock()):
                return "DUPLICATE"
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=alerts event=LEDGER_FAILED channel=%s error_type=%s",
                        channel.name, type(exc).__name__)
            return "LEDGER_FAILED"
        attempts, error = 0, None
        while attempts < self.max_attempts:
            if attempts:
                self.sleep(self.backoff(attempts))
            attempts += 1
            try:
                channel.send(event)
                error = None
                break
            except Exception as exc:  # noqa: BLE001 - typed code only; never the message or secret
                error = getattr(exc, "code", None) or type(exc).__name__
                if getattr(exc, "retryable", True) is False:
                    break
        self._record(channel.name, error)
        try:
            self.ledger.complete(event.event_id, channel.name, self.clock(), attempts, error)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=alerts event=LEDGER_FAILED channel=%s error_type=%s",
                        channel.name, type(exc).__name__)
        return "FAILED" if error else "DELIVERED"

    def _record(self, name, error):
        self._last_error[name] = error
        if error is None:
            self._consecutive_failures[name] = 0
            return
        self._consecutive_failures[name] += 1
        LOG.warning("component=alerts event=DELIVERY_FAILED channel=%s error_type=%s consecutive=%d",
                    name, error, self._consecutive_failures[name])

    def health(self):
        """Per-channel failure detection: DISABLED / NOT_CONFIGURED / OK / DEGRADED / FAILING."""
        result = {}
        for channel in self.channels:
            failures = self._consecutive_failures[channel.name]
            if not self.enabled:
                status = "DISABLED"
            elif not getattr(channel, "configured", True):
                status = "NOT_CONFIGURED"
            elif failures >= FAILING_THRESHOLD:
                status = "FAILING"
            elif failures:
                status = "DEGRADED"
            else:
                status = "OK"
            result[channel.name] = {"status": status, "consecutive_failures": failures,
                                    "last_error_type": self._last_error[channel.name]}
        return result


class AlertNotificationSink:
    """NotificationSink adapter over the dispatcher for the existing durable notification contract.

    Not wired anywhere. ``durable_delivery`` is False because the dispatcher keeps its own ledger.
    """
    durable_delivery = False

    def __init__(self, dispatcher):
        self.dispatcher = dispatcher

    def deliver(self, event):
        try:
            alert = from_notification(event)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=alerts event=MAPPING_FAILED error_type=%s", type(exc).__name__)
            return None
        if alert is not None:
            self.dispatcher.dispatch(alert)
        return None


def dispatcher_from_env(*, ledger_path=":memory:", environ=None):
    """Email + Slack dispatcher configured from the environment; OFF unless AI_FLOOR_ALERTS_ENABLED=1."""
    from runtime.alert_channels import GmailEmailChannel, SlackAlertChannel
    return AlertDispatcher((GmailEmailChannel(environ=environ), SlackAlertChannel(environ=environ)),
                           ledger=AlertDeliveryLedger(ledger_path), environ=environ)


__all__ = ["AlertDeliveryLedger", "AlertDispatcher", "AlertNotificationSink", "ENABLED_ENV", "alerts_enabled",
           "dispatcher_from_env"]
