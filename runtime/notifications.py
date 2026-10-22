"""Durable notification contract. Delivery never controls trading."""
from dataclasses import asdict, dataclass
import json
import logging
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

LOG = logging.getLogger(__name__)


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

    def __init__(self, *, webhook_url=None, transport=None, timeout=10, clock=None):
        self.webhook_url = webhook_url if webhook_url is not None else os.environ.get("SLACK_WEBHOOK_URL")
        self.transport = transport or _slack_post
        self.clock = clock  # send time of the OANDA demo snapshot (NAV/balance/open/day counter); None = now
        self.timeout = float(timeout)
        if self.timeout <= 0:
            raise ValueError("invalid Slack timeout")

    def deliver(self, event: NotificationEvent) -> None:
        if not self.webhook_url:
            raise SlackNotificationError("NOT_CONFIGURED")
        if event.type == "DAILY_SUMMARY":
            self._deliver_daily_summary(event)
            return
        payload = compact_payload(event)
        if payload is not None:
            send_slack(self.transport, self.webhook_url, payload, self.timeout)

    def _deliver_daily_summary(self, event):
        """DEC-DEMO-GATE-1: OANDA demo figures while routing is ON (never PAPER numbers), else the PAPER summary.
        One log line per attempt: source + NY date of the summary only (never the webhook URL or figures). Both
        cover the summary's trading day [``window_start_utc``, ``close_at_utc``) = 17:00 -> 17:00 America/New_York."""
        source = "OANDA_DEMO" if _routing_on() else "PAPER"
        summary = event.daily_summary or {}
        alert = None
        if source == "OANDA_DEMO":
            alert = oanda_demo_daily_alert(summary.get("close_at_utc") or event.timestamp_utc,
                                           summary.get("window_start_utc"), self.clock() if self.clock else None)
            from runtime.trade_alerts import daily_slack_payload
            payload = daily_slack_payload(alert)
        else:
            payload = {"text": paper_summary_text(summary)}
        date = str(summary.get("date_ny") or "")[:10] or "-"
        try:
            send_slack(self.transport, self.webhook_url, payload, self.timeout)
        except Exception as exc:
            code = exc.args[0] if isinstance(exc, SlackNotificationError) and exc.args else type(exc).__name__
            LOG.warning("component=notifications event=DAILY_SUMMARY_FAILED source=%s date=%s error_type=%s", source,
                        date, code)
            raise
        LOG.info("component=notifications event=DAILY_SUMMARY_SENT source=%s date=%s", source, date)
        if alert is not None:
            _email_daily_summary(alert, event.event_id)


# DEC-ALERTS-REDESIGN-1: what the durable Slack sink sends besides the daily summary. Never a JSON dump.
FOLDED_TYPES = frozenset({"MACRO_HIGH_IMPORTANCE", "MACRO_HIGH_RELEVANCE", "AI_REVIEW_UNHEALTHY",
                          "AI_REVIEW_RECOVERED"})  # macro: folded into «Por qué» / the daily summary
TRADE_TYPES = frozenset({"EXECUTION_DECISION", "POSITION_OPENED", "POSITION_CLOSED", "STRUCTURE_REVERSE_SUBMITTED",
                         "RISK_REJECTED", "AI_CAUTION"})  # PAPER engine: the OANDA router alerts report demo trades
PAPER_LABELS = {"EXECUTION_DECISION": "Decisión de ejecución PAPER", "POSITION_OPENED": "Posición PAPER abierta",
                "POSITION_CLOSED": "Posición PAPER cerrada", "STRUCTURE_REVERSE_SUBMITTED": "Reversión PAPER enviada",
                "RISK_REJECTED": "Plan rechazado por el Risk Engine", "AI_CAUTION": "Precaución de la IA"}


def compact_payload(event):
    """Compact Floor-style Slack payload for a non-summary event, or None when it is not sent: macro / per-agent
    events are folded; PAPER trade events are not sent in OANDA-demo results mode (no PAPER numbers ever)."""
    from runtime.trade_alerts import SYSTEM_LABELS, system_slack_payload
    if event.type in FOLDED_TYPES:
        return None
    execution = event.execution or {}
    if event.type in TRADE_TYPES:
        if _routing_on():
            return None
        reason = execution.get("execution_reason") or execution.get("execution_status") or (
            (event.risk_decision or {}).get("status"))
        side = execution.get("side") or (event.risk_decision or {}).get("side")
        label = PAPER_LABELS[event.type] + (f" {side}" if side else "")
        return system_slack_payload(label, event.symbol, reason)
    reason = execution.get("reason") or (event.type if event.type not in SYSTEM_LABELS else None)
    return system_slack_payload(event.type, event.symbol, reason)


def paper_summary_text(summary):
    """PAPER daily summary as short «clave: valor» lines (routing OFF and no OANDA-only mode); no JSON dump."""
    lines = ["AI Market System · Resumen diario PAPER"]
    for key, value in sorted((summary or {}).items()):
        if isinstance(value, dict):
            value = ", ".join(f"{k}={v}" for k, v in sorted(value.items())) or "—"
        lines.append(f"{key}: {'sin datos' if value is None else value}")
    return "\n".join(lines)


def oanda_demo_daily_alert(timestamp_utc=None, window_start_utc=None, sent_at=None):
    """``runtime.trade_alerts.DailyAlert`` of the OANDA demo summary (same figures as ``oanda_demo_summary_text``)."""
    from runtime.trade_alert_source import daily_alert
    meta = {}
    try:
        lines = _demo_lines(timestamp_utc, window_start_utc, sent_at, meta)
    except Exception:  # noqa: BLE001 - no demo data: never PAPER numbers
        lines = ["OANDA demo: sin datos"]
    return daily_alert(lines, os.environ, date_label=meta.get("label"), demo_day=meta.get("day"),
                       pnl=meta.get("pnl"))


def _email_daily_summary(alert, event_id):
    """Best effort, after Slack succeeded (so a Slack retry never re-sends it): OANDA demo summary by email."""
    try:
        from runtime.alert_daily_email import send_daily_email
        send_daily_email(alert, event_id)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("component=notifications event=DAILY_SUMMARY_EMAIL_FAILED error_type=%s", type(exc).__name__)


def _routing_on():
    """OANDA demo summary while routing is ON or ``AI_FLOOR_RESULTS_SOURCE=OANDA_DEMO`` (DEC-OANDA-ONLY-1: never the
    PAPER summary in that mode, even with routing OFF)."""
    try:
        from execution.results_source import demo_results
        return demo_results(os.environ)
    except Exception:  # noqa: BLE001 - unreadable flag: the unchanged PAPER summary
        return False


def _amount(value, currency, signed=False):
    if value is None:
        return "sin datos"
    return (f"{value:+,.2f}" if signed else f"{value:,.2f}") + (f" {currency}" if currency else "")


def oanda_demo_summary_text(timestamp_utc=None, window_start_utc=None, sent_at=None):
    """Daily Slack summary of the OANDA practice demo (routing ON): figures since the demo epoch only, from
    ``execution.demo_dashboard`` (read-only, never raises); the day's figures cover the trading day ending at
    ``timestamp_utc`` (17:00 NY), from ``window_start_utc`` (default: the previous 17:00 NY). NAV, balance, open
    trades and the demo day counter are the snapshot at ``sent_at`` (default: now), labelled with its ET time (a
    catch-up summary of an older day is sent later). A failed read reads "sin datos"; no ids or tokens."""
    header = "AI Market System OANDA DEMO DAILY_SUMMARY"
    try:
        return "\n".join([header, *_demo_lines(timestamp_utc, window_start_utc, sent_at)])
    except Exception:  # noqa: BLE001 - no demo data: never PAPER numbers
        return header + "\nOANDA demo: sin datos"


def _demo_lines(timestamp_utc, window_start_utc, sent_at=None, meta=None):
    """Summary lines; ``meta`` (dict) receives the trading-day label, demo day and day P&L for the Floor alert."""
    meta = {} if meta is None else meta
    from datetime import datetime, timezone
    from execution.demo_dashboard import demo_progress, demo_snapshot
    from execution.demo_epoch import parse_aware
    from execution.demo_metrics import DEMO_LENGTH_DAYS, ET, window_statistics
    from storage.daily_summary import summary_window, trading_day
    from ui.i18n_es import demo_day_text
    close = parse_aware(timestamp_utc) or datetime.now(timezone.utc)
    label, start, end, _ = summary_window(trading_day(close))
    meta["label"] = label
    start = parse_aware(window_start_utc) or start
    now = sent_at if isinstance(sent_at, datetime) and sent_at.tzinfo else datetime.now(timezone.utc)
    at = f"(a las {now.astimezone(ET):%H:%M} ET)"
    snapshot = demo_snapshot(now)
    stats, currency = snapshot.get("stats"), snapshot.get("currency")
    if stats is None and snapshot.get("nav") is None:
        return ["OANDA demo: sin datos"]
    day = meta["day"] = demo_progress(now)[2]
    nav, balance, change = snapshot.get("nav"), snapshot.get("balance"), (snapshot.get("capital") or {}).get("change")
    account_at = at
    if snapshot.get("stale"):  # last good account read: never labelled as current
        as_of = snapshot.get("as_of")
        if isinstance(as_of, datetime) and as_of.tzinfo:
            account_at = f"(a las {as_of.astimezone(ET):%Y-%m-%d %H:%M} ET, dato antiguo)"
        else:
            nav = balance = change = None
    lines = [demo_day_text(day, DEMO_LENGTH_DAYS),
             f"Día de trading: {label} (17:00 a 17:00 ET)",
             f"NAV {account_at}: " + _amount(nav, currency),
             f"Balance {account_at}: " + _amount(balance, currency),
             f"P&L desde el inicio del demo {account_at}: " + _amount(change, currency, True)]
    if stats is None:
        return lines + ["OANDA demo: sin datos de operaciones"]
    window = window_statistics(snapshot.get("closed_rows") or [], start, end)
    meta["pnl"] = window["pnl"]
    avg_r = window["avg_r"]
    closed = ("sin datos" if window["trades"] is None else
              f"{window['trades']} (ganadoras: {'—' if window['wins'] is None else window['wins']})")
    return lines + ["P&L del día de trading: " + _amount(window["pnl"], currency, True),
                    "Operaciones cerradas en el día: " + closed,
                    "R medio del día: " + (f"{avg_r:.2f}" if avg_r is not None else "sin datos" +
                                           (f" ({window['avg_r_reason']})" if window["avg_r_reason"] else "")),
                    f"Operaciones abiertas {at}: {len(snapshot.get('open_rows') or ())}"]


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
