"""Durable notification contract. Delivery never controls trading."""
from dataclasses import asdict, dataclass
from typing import Protocol


@dataclass(frozen=True)
class NotificationEvent:
    schema_version: str
    event_id: str
    run_id: str
    timestamp_utc: str
    severity: str
    type: str
    symbol: str | None
    session: tuple
    agent_summaries: tuple
    risk_decision: dict | None
    paper_order_ids: tuple
    paper_trade_ids: tuple
    evidence_refs: tuple

    def payload(self):
        return asdict(self)


class NotificationSink(Protocol):
    def deliver(self, event: NotificationEvent) -> None:
        ...


class NullNotificationSink:
    def deliver(self, event: NotificationEvent) -> None:
        return None


class FakeNotificationSink:
    def __init__(self):
        self.events = []

    def deliver(self, event: NotificationEvent) -> None:
        self.events.append(event)
