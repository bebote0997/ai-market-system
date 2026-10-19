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
    execution: dict | None = None
    # Pre-demo: order/position alert block read from durable PAPER state (storage.database.Store.alert_trade).
    trade: dict | None = None

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


SLACK_WEBHOOK_PREFIX = "https://hooks.slack.com/"


def valid_slack_webhook(url):
    """Only HTTPS Slack incoming-webhook URLs are ever opened (no file://, http:// or other hosts)."""
    return isinstance(url, str) and url.startswith(SLACK_WEBHOOK_PREFIX)


def slack_escape(text):
    """Escape Slack control characters so text cannot form mentions (<!channel>) or disguised links."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _slack_post(url, payload, timeout):
    if not valid_slack_webhook(url):
        raise SlackNotificationError("INVALID_WEBHOOK_URL")
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
        if event.type == "DAILY_SUMMARY" and _routing_on():
            payload = {"text": oanda_demo_summary_text(event.timestamp_utc)}  # DEC-DEMO-GATE-1: never PAPER numbers
        elif event.type == "DAILY_SUMMARY":
            payload = {"text": "AI Market System PAPER DAILY_SUMMARY\n" +
                       json.dumps(event.daily_summary, sort_keys=True, ensure_ascii=False)}
        elif event.type == "EXECUTION_DECISION":
            outcome = event.execution or {}
            payload = {"text": "AI Market System PAPER execution\n" + json.dumps({
                "run_id": event.run_id, "symbol": event.symbol,
                "setup_id": outcome.get("setup_id"),
                "setup_status": outcome.get("setup_status"),
                "plan_status": outcome.get("plan_status"),
                "direction": outcome.get("side"),
                "execution_status": outcome.get("execution_status"),
                "execution_reason": outcome.get("execution_reason"),
                "blocking_position_id": outcome.get("blocking_position_id"),
                "blocking_order_id": outcome.get("blocking_order_id"),
                "order_id": outcome.get("order_id"),
                "risk_status": event.risk_decision.get("status") if event.risk_decision else None,
            }, sort_keys=True)}
        send_slack(self.transport, self.webhook_url, payload, self.timeout)


def _routing_on():
    try:
        from execution.demo_dashboard import demo_mode
        return demo_mode(os.environ)
    except Exception:  # noqa: BLE001 - unreadable flag: the unchanged PAPER summary
        return False


def _amount(value, currency, signed=False):
    if value is None:
        return "sin datos"
    return (f"{value:+,.2f}" if signed else f"{value:,.2f}") + (f" {currency}" if currency else "")


def oanda_demo_summary_text(timestamp_utc=None):
    """Daily Slack summary of the OANDA practice demo (routing ON): figures since the demo epoch only, from
    ``execution.demo_dashboard`` (read-only, never raises). A failed read reads "sin datos"; no ids or tokens."""
    header = "AI Market System OANDA DEMO DAILY_SUMMARY"
    try:
        return "\n".join([header, *_demo_lines(timestamp_utc)])
    except Exception:  # noqa: BLE001 - no demo data: never PAPER numbers
        return header + "\nOANDA demo: sin datos"


def _demo_lines(timestamp_utc):
    from datetime import datetime, timezone
    from execution.demo_dashboard import demo_progress, demo_snapshot
    from execution.demo_epoch import parse_aware
    from execution.demo_metrics import DEMO_LENGTH_DAYS
    now = parse_aware(timestamp_utc) or datetime.now(timezone.utc)
    snapshot = demo_snapshot(now)
    stats, currency = snapshot.get("stats"), snapshot.get("currency")
    if stats is None and snapshot.get("nav") is None:
        return ["OANDA demo: sin datos"]
    day = demo_progress(now)[2]
    avg_r = None if stats is None else stats.get("avg_r")
    lines = [f"Día {day} de {DEMO_LENGTH_DAYS}" if day else "Demo OANDA: aún sin operaciones del demo",
             "NAV: " + _amount(snapshot.get("nav"), currency),
             "P&L desde el inicio del demo: " + _amount((snapshot.get("capital") or {}).get("change"), currency, True)]
    if stats is None:
        return lines + ["OANDA demo: sin datos de operaciones"]
    return lines + [f"Operaciones cerradas: {stats.get('trades')} (ganadoras: {stats.get('wins')})",
                    "R medio: " + (f"{avg_r:.2f}" if avg_r is not None else "sin datos" +
                                   (f" ({stats['avg_r_reason']})" if stats.get("avg_r_reason") else "")),
                    "P&L de hoy (ET): " + _amount(stats.get("today_pnl"), currency, True),
                    f"Operaciones abiertas: {len(snapshot.get('open_rows') or ())}"]


def send_slack(transport, url, payload, timeout):
    """Post one Slack payload; failures surface only as typed SlackNotificationError codes."""
    if isinstance(payload.get("text"), str):
        payload = {**payload, "text": slack_escape(payload["text"])}
    try:
        transport(url, payload, timeout)
    except HTTPError as exc:
        raise SlackNotificationError("RATE_LIMITED" if exc.code == 429 else "PROVIDER_FAILURE") from None
    except (URLError, TimeoutError, ConnectionError):
        raise SlackNotificationError("CONNECTION_ERROR") from None
