"""V2 Phase 9 wiring: hand already-persisted notification events to the alert dispatcher after a cycle.

Outside the trading path: the runner calls the hook only after the cycle committed and ``capture_notifications``
persisted its ``NotificationEvent``s; the hook reads those immutable events and nothing else (no store, broker or
PAPER state). OFF unless ``AI_FLOOR_ALERTS_ENABLED`` is exactly ``1`` — when OFF no dispatcher or channel is built
and nothing is mapped or sent. Never raises.

Each persisted event is captured exactly once (``notification_events`` dedups by journal row), so restarts do not
replay history. ``AI_FLOOR_ALERTS_LEDGER_PATH`` optionally makes the per-channel delivery ledger durable; the
default is in-memory (dedup within the process).
"""
import logging
import os

from runtime.alert_catalog import from_notification
from runtime.alert_dispatch import alerts_enabled, dispatcher_from_env

LOG = logging.getLogger(__name__)
LEDGER_ENV = "AI_FLOOR_ALERTS_LEDGER_PATH"


class AlertHook:
    def __init__(self, *, environ=None, dispatcher_factory=None):
        self.environ = environ
        self.dispatcher_factory = dispatcher_factory or dispatcher_from_env
        self.dispatcher = None  # built lazily on the first enabled call; reused so health/dedup persist

    def __call__(self, events):
        """Dispatch alerts for ``events``; returns {event_id: {channel: status}}, or None when OFF/failed."""
        try:
            env = os.environ if self.environ is None else self.environ
            if not alerts_enabled(env):
                return None
            alerts = [alert for alert in map(_to_alert, events) if alert is not None]
            if not alerts:
                return {}
            if self.dispatcher is None:
                self.dispatcher = self.dispatcher_factory(ledger_path=env.get(LEDGER_ENV) or ":memory:", environ=env)
            return self.dispatcher.dispatch_many(alerts)
        except Exception as exc:  # noqa: BLE001 - alerting never controls anything
            LOG.warning("component=alerts event=HOOK_FAILED error_type=%s", type(exc).__name__)
            return None


def _to_alert(event):
    try:
        return from_notification(event)
    except Exception as exc:  # noqa: BLE001 - one malformed event never blocks the others
        LOG.warning("component=alerts event=MAPPING_FAILED error_type=%s", type(exc).__name__)
        return None


__all__ = ["AlertHook", "LEDGER_ENV"]
