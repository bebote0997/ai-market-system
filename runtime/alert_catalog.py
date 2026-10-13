"""V2 Phase 9: alert event catalog and trade alert content. Pure data; no I/O.

Ten catalogued events (F09-T01…T10), each with a fixed priority (F09-T19). Alert content only reports
values supplied by the caller: nothing is computed, rounded or invented, and every absent value is rendered
as the explicit ``NO_DATA`` state (F09-T11).
"""
from dataclasses import dataclass
import math
from types import MappingProxyType
import uuid

NO_DATA = "NO_DATA"
SCHEMA_VERSION = "1.0"

PRIORITY_RANK = MappingProxyType({"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3})


@dataclass(frozen=True)
class AlertKind:
    event_type: str
    label: str
    priority: str
    trade: bool  # carries the trade alert block


CATALOG = MappingProxyType({kind.event_type: kind for kind in (
    AlertKind("VALID_EXECUTED_TRADE", "VALID/EXECUTED TRADE", "HIGH", True),
    AlertKind("ORDER_SUBMITTED", "ORDER SUBMITTED", "MEDIUM", True),
    AlertKind("POSITION_OPENED", "POSITION OPENED", "HIGH", True),
    AlertKind("POSITION_CLOSED", "POSITION CLOSED", "HIGH", True),
    AlertKind("RISK_REJECTED", "RISK REJECTED", "MEDIUM", True),
    AlertKind("AI_CAUTION", "AI CAUTION", "MEDIUM", True),
    AlertKind("PROVIDER_FAILURE", "PROVIDER FAILURE", "HIGH", False),
    AlertKind("SYSTEM_DEGRADED", "SYSTEM DEGRADED", "HIGH", False),
    AlertKind("SYSTEM_CRITICAL", "SYSTEM CRITICAL", "CRITICAL", False),
    AlertKind("RECOVERY", "RECOVERY", "LOW", False),
)})
TEST_KIND = AlertKind("TEST_ALERT", "TEST ALERT", "LOW", True)  # F09-T18; not one of the ten events

# (key, label) in display order. Timestamps are UTC ISO strings supplied by the caller.
TRADE_FIELDS = (
    ("symbol", "Symbol"), ("side", "Side"), ("entry", "Entry"), ("stop_loss", "SL"),
    ("take_profit", "TP"), ("quantity", "Quantity"), ("rr", "R:R"), ("risk_pct", "Risk %"),
    ("session", "Session"), ("run_id", "Run ID"), ("setup_id", "Setup ID"), ("order_id", "Order ID"),
    ("signal_at_utc", "Signal (UTC)"), ("executed_at_utc", "Executed (UTC)"),
    ("closed_at_utc", "Closed (UTC)"), ("chart_ref", "Chart"),
)
TRADE_KEYS = frozenset(key for key, _ in TRADE_FIELDS)


def display(value):
    """Caller value as text, unchanged; absent values become NO_DATA."""
    if value is None or (isinstance(value, (str, tuple, list)) and not value):
        return NO_DATA
    if isinstance(value, (tuple, list)):
        return "+".join(display(item) for item in value)
    return str(value)


@dataclass(frozen=True)
class AlertEvent:
    event_id: str
    event_type: str
    occurred_at_utc: str | None
    run_id: str | None = None
    symbol: str | None = None
    session: tuple = ()
    summary: str | None = None
    trade: tuple = ()  # ((key, text), ...) frozen snapshot; never a live reference to trading state
    closes: tuple = ()  # bar closes supplied by the caller for the chart (F09-T12); empty = NO_DATA

    def __post_init__(self):
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id is required")
        if self.event_type not in CATALOG and self.event_type != TEST_KIND.event_type:
            raise ValueError("unknown alert event type")

    @property
    def kind(self):
        return CATALOG.get(self.event_type, TEST_KIND)

    @property
    def priority(self):
        return self.kind.priority

    @property
    def is_test(self):
        return self.event_type == TEST_KIND.event_type

    def trade_rows(self):
        """Every trade field in display order, NO_DATA where the caller supplied nothing."""
        if not self.kind.trade:
            return ()
        values = dict(self.trade)
        fallback = {"symbol": display(self.symbol), "run_id": display(self.run_id), "session": display(self.session),
                    "chart_ref": f"SPARKLINE ({len(self.closes)} closes)" if self.closes else NO_DATA}
        return tuple((label, values.get(key, fallback.get(key, NO_DATA))) for key, label in TRADE_FIELDS)


def alert_event(event_id, event_type, occurred_at_utc, *, run_id=None, symbol=None, session=(),
                summary=None, trade=None, closes=None):
    """Build an AlertEvent, snapshotting ``trade`` into text (and ``closes`` into a tuple) so later mutation
    cannot leak in or out."""
    trade = dict(trade or {})
    unknown = set(trade) - TRADE_KEYS
    if unknown:
        raise ValueError("unknown trade fields: " + ",".join(sorted(unknown)))
    session = tuple(session) if isinstance(session, (tuple, list)) else ((session,) if session else ())
    closes = tuple(float(close) for close in (closes or ()))
    if not all(math.isfinite(close) for close in closes):
        raise ValueError("closes must be finite")
    return AlertEvent(event_id, event_type, occurred_at_utc, run_id, symbol, session, summary,
                      tuple((key, display(trade.get(key))) for key, _ in TRADE_FIELDS if key in trade), closes)


def alert_event_id(event_type, *parts):
    """Deterministic event_id so the same fact always deduplicates to the same alert."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "ai-floor-alert:" + "|".join(map(str, (event_type, *parts)))))


def make_test_alert(occurred_at_utc, *, nonce):
    """A clearly labelled TEST alert (F09-T18) that exercises every trade field as NO_DATA."""
    return alert_event(alert_event_id(TEST_KIND.event_type, nonce), TEST_KIND.event_type, occurred_at_utc,
                       summary="Test alert. No trade, order or position is associated with this message.")


# Existing durable NotificationEvent types (runtime.notifications) mapped onto the catalog. Unmapped → None.
_NOTIFICATION_MAP = MappingProxyType({
    "STRUCTURE_REVERSE_SUBMITTED": "ORDER_SUBMITTED",
    "POSITION_OPENED": "POSITION_OPENED", "POSITION_CLOSED": "POSITION_CLOSED",
    "RISK_REJECTED": "RISK_REJECTED", "AI_CAUTION": "AI_CAUTION",
    "PROVIDER_FAILURE": "PROVIDER_FAILURE", "AUTH_ERROR": "PROVIDER_FAILURE",
    "ENTITLEMENT_ERROR": "PROVIDER_FAILURE", "RATE_LIMITED": "PROVIDER_FAILURE",
    "AI_PROVIDER_FAILED": "PROVIDER_FAILURE", "AI_PROVIDER_RECOVERED": "RECOVERY",
    "RUN_FAILED": "SYSTEM_DEGRADED", "STATE_INCONSISTENCY": "SYSTEM_CRITICAL",
    "RECOVERY_COMPLETED": "RECOVERY",
    "AI_REVIEW_UNHEALTHY": "SYSTEM_DEGRADED", "AI_REVIEW_RECOVERED": "RECOVERY",
})


def from_notification(event):
    """Map a persisted ``NotificationEvent`` to an AlertEvent, or None when it is not an alert."""
    event_type = _NOTIFICATION_MAP.get(event.type)
    execution = event.execution or {}
    # The reverse alert is already durable with its order; the ordinary execution projection
    # reports the same submission and must not send a second Phase 9 alert for that fact.
    if event.type == "EXECUTION_DECISION" and execution.get("execution_reason") == "STRUCTURE_REVERSE_SUBMITTED":
        return None
    if event.type == "EXECUTION_DECISION" and execution.get("execution_status") == "SUBMITTED":
        event_type = "ORDER_SUBMITTED"
    if event_type is None:
        return None
    risk = event.risk_decision or {}
    trade = {"setup_id": execution.get("setup_id"), "order_id": execution.get("order_id"),
             "side": execution.get("side") or risk.get("side")}
    if event.type == "STRUCTURE_REVERSE_SUBMITTED":
        trade.update({key: execution[key] for key in ("entry", "stop_loss", "take_profit", "quantity")})
    # Pre-demo: the durable order/position snapshot supplied at capture (entry, SL, TP, quantity, R:R, risk, times).
    trade.update({key: value for key, value in (getattr(event, "trade", None) or {}).items()
                  if key in TRADE_KEYS and value is not None})
    if event.paper_order_ids and event.type != "POSITION_OPENED":  # POSITION_OPENED carries the position id
        trade["order_id"] = trade["order_id"] or event.paper_order_ids[0]
    summary = execution.get("exit_reason") or execution.get("reason") or ("source=" + event.type)
    return alert_event(event.event_id, event_type, event.timestamp_utc, run_id=event.run_id,
                       symbol=event.symbol, session=event.session, summary=summary,
                       trade={key: value for key, value in trade.items() if value is not None})


__all__ = ["AlertEvent", "AlertKind", "CATALOG", "NO_DATA", "PRIORITY_RANK", "TEST_KIND", "TRADE_FIELDS",
           "alert_event", "alert_event_id", "display", "from_notification", "make_test_alert"]
