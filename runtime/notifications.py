"""Durable notification contract. Delivery never controls trading."""
from dataclasses import asdict, dataclass
import json
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
    daily_summary: dict | None = None

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


class SlackNotificationError(RuntimeError):
    """Safe delivery error; never carries webhook URL or response body."""


def _slack_post(url, payload, timeout):
    request = Request(url, data=json.dumps(payload).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        if response.status != 200:
            raise SlackNotificationError("PROVIDER_FAILURE")


class SlackNotificationSink:
    durable_delivery = True

    def __init__(self, *, webhook_url=None, transport=None, timeout=10):
        self.webhook_url = webhook_url if webhook_url is not None else os.environ.get("SLACK_WEBHOOK_URL")
        self.transport = transport or _slack_post
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("invalid Slack timeout")

    def deliver(self, event: NotificationEvent) -> None:
        if not self.webhook_url:
            raise SlackNotificationError("NOT_CONFIGURED")
        details = {
            "event_id": event.event_id, "run_id": event.run_id,
            "timestamp_utc": event.timestamp_utc, "severity": event.severity,
            "type": event.type, "symbol": event.symbol,
            "agent_summaries": tuple({"agent": item.get("agent"), "status": item.get("status"),
                                       "summary": str(item.get("reasoning_summary") or "")[:240]}
                                      for item in event.agent_summaries),
            "risk_status": event.risk_decision.get("status") if event.risk_decision else None,
            "paper_order_ids": event.paper_order_ids, "paper_trade_ids": event.paper_trade_ids,
            "evidence_refs": event.evidence_refs,
        }
        payload = {"text": "AI Market System PAPER alert\n" + json.dumps(details, sort_keys=True)}
        if event.type == "DAILY_SUMMARY":
            payload = {"text": "AI Market System PAPER DAILY_SUMMARY\n" +
                       json.dumps(event.daily_summary, sort_keys=True, ensure_ascii=False)}
        try:
            self.transport(self.webhook_url, payload, self.timeout)
        except HTTPError as exc:
            raise SlackNotificationError("RATE_LIMITED" if exc.code == 429 else "PROVIDER_FAILURE") from None
        except (URLError, TimeoutError, ConnectionError):
            raise SlackNotificationError("CONNECTION_ERROR") from None
