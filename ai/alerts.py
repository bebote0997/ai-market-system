"""V2 Phase 7 (P7.1 Batch C, DEC-7.9): typed AI provider alerts. OFF BY DEFAULT.

Turns ``ProviderHealthTracker`` transition events into ``NotificationEvent``s for the existing isolated notification
sinks. Delivery can never affect trading: nothing returns to the caller except a count, and delivery errors are
logged by type and swallowed. Spam control: only state TRANSITIONS produce events (a repeated identical failure
produces none), and each (event, incident) is delivered at most once. Messages carry the typed outcome and times
only: no key, header, prompt, body or balance claim.
"""
import logging
import uuid

from runtime.notifications import NotificationEvent

LOG = logging.getLogger(__name__)
ALERTED = frozenset({"PROVIDER_FAILED", "PROVIDER_RECOVERED"})


class AIProviderAlerts:
    def __init__(self, sink=None, enabled=False):
        self.sink = sink
        self.enabled = enabled is True  # anything but an explicit True is OFF
        self._delivered = set()
        self.suppressed = 0

    def process(self, events, *, symbol=None):
        """Deliver alerts for transition events; returns the number delivered."""
        delivered = 0
        for event in events:
            if event.get("event") not in ALERTED:
                continue
            key = (event["event"], event.get("incident_started_at"))
            if not self.enabled or self.sink is None or key in self._delivered:
                self.suppressed += 1
                continue
            self._delivered.add(key)
            notification = NotificationEvent(
                "1.0", str(uuid.uuid5(uuid.NAMESPACE_URL, "ai-provider:" + "|".join(map(str, key)))),
                event.get("run_id") or "ai-provider", event["at"],
                "WARNING" if event["event"] == "PROVIDER_FAILED" else "INFO", "AI_" + event["event"], symbol, (),
                ({"outcome": event.get("outcome"), "from": event.get("from"), "to": event.get("to"),
                  "incident_started_at": event.get("incident_started_at"),
                  "duration_seconds": event.get("duration_seconds")},), None, (), (), ())
            try:
                self.sink.deliver(notification)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - delivery never controls anything
                LOG.warning("component=ai_alerts event=DELIVERY_FAILED error_type=%s", type(exc).__name__)
        return delivered


__all__ = ["AIProviderAlerts"]
