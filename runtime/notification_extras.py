"""Pre-demo notification extras, kept outside ``storage/database.py`` (its bytes are pinned by a frozen hash).

- ``capture_ai_review_health``: persists AI_REVIEW_UNHEALTHY (transition rows only, ``notify``) and
  AI_REVIEW_RECOVERED journal rows as NotificationEvents, with the same event-id scheme and table as
  ``Store.capture_notifications``, so they reach the sink and the email/Slack alerts exactly once.
- ``with_trade``: attaches the durable order/position block (entry, SL, TP, quantity, R:R, risk %, times) to
  order/position notifications. Python formats R:R and risk once here; absent durable data stays absent (NO_DATA).
Delivery never controls trading: both functions only read PAPER state.
"""
from dataclasses import replace
import json
import uuid

from runtime.notifications import NotificationEvent
from runtime.scheduler import session_names
from storage.codec import paper_decode, parse_utc, safe_json, utc

AI_REVIEW_EVENTS = ("AI_REVIEW_UNHEALTHY", "AI_REVIEW_RECOVERED")
TRADE_EVENTS = frozenset({"EXECUTION_DECISION", "STRUCTURE_REVERSE_SUBMITTED", "POSITION_OPENED", "POSITION_CLOSED"})


def _event_id(journal_id):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"journal:{journal_id}"))


def capture_ai_review_health(store, *, run_id=None):
    events = []
    with store.transaction():
        rows = store.db.execute("SELECT * FROM journal WHERE event_type IN (?,?) AND (? IS NULL OR run_id=?) "
                                "ORDER BY id", (*AI_REVIEW_EVENTS, run_id, run_id)).fetchall()
        for row in rows:
            payload = json.loads(row["payload"] or "{}")
            if row["event_type"] == "AI_REVIEW_UNHEALTHY" and payload.get("notify") is False:
                continue  # journaled every time; alerted on the transition only
            event_id = _event_id(row["id"])
            if store.db.execute("SELECT 1 FROM notification_events WHERE event_id=?", (event_id,)).fetchone():
                continue
            event = NotificationEvent("1.0", event_id, row["run_id"] or "system", row["timestamp"], row["severity"],
                                      row["event_type"], row["symbol"], session_names(parse_utc(row["timestamp"])),
                                      (), None, (), (), (), None, payload)
            store.db.execute("INSERT INTO notification_events VALUES(?,?,?)",
                             (event_id, row["id"], safe_json(event.payload())))
            events.append(event)
    return events


def _paper(store, table, id_field, value, name):
    if not value:
        return None
    row = store.db.execute(f"SELECT payload FROM {table} WHERE {id_field}=?", (value,)).fetchone()
    return None if row is None else paper_decode(name, row[0])


def order_setup_id(store, order):
    """Setup ID of an order not stamped with one (original V1 setup path): the setup identity recorded by the run
    that submitted it (its execution outcome, else its Setup Validator explanation). Never the alerting run's own
    setup, which may be a different opportunity (e.g. a position closed by a later run). None when unrecorded."""
    review = store.review_report(order.run_id) if order.run_id else None
    if not review:
        return None
    execution = review.get("execution") or {}
    if execution.get("setup_id") and execution.get("order_id") in {None, order.order_id}:
        return execution["setup_id"]
    return (review.get("setup") or {}).get("setup_id")


def trade_block(store, row, review):
    """The order/position alert block of journal ``row`` from durable PAPER state, or {} without an order."""
    payload = json.loads(row["payload"] or "{}")
    trade = position = None
    if row["event_type"] == "POSITION_CLOSED":
        trade = _paper(store, "closed_trades", "trade_id", row["source"], "ClosedTrade")
        position = trade and _paper(store, "paper_positions", "position_id", trade.position_id, "PaperPosition")
    elif row["event_type"] == "POSITION_OPENED":
        position = _paper(store, "paper_positions", "position_id", row["source"], "PaperPosition")
    order_id = position.origin_order_id if position is not None else payload.get("order_id")
    order = _paper(store, "paper_orders", "order_id", order_id, "PaperOrder")
    if order is None:
        return {}
    review = review or {}
    entry = position.entry_price if position is not None else order.planned_entry
    # The position's own stop once filled (the trade-duration rule may have moved it to entry); R:R and risk stay
    # measured on the original order stop.
    stop = position.stop if position is not None else order.stop
    out = {"side": order.side, "entry": entry, "stop_loss": stop, "take_profit": order.target,
           "quantity": order.quantity, "order_id": order.order_id}
    setup_id = order.setup_id or order_setup_id(store, order)
    if setup_id:
        out["setup_id"] = setup_id
    risk, reward = abs(entry - order.stop), abs(order.target - entry)
    if risk > 0:
        out["rr"] = f"1:{reward / risk:.2f}"
    planned_money = abs(order.planned_entry - order.stop) * order.quantity * (order.contract_multiplier or 0)
    if planned_money > 0 and order.equity_at_submission:
        out["risk_pct"] = f"{planned_money / order.equity_at_submission * 100:.2f}%"
    elif (review.get("risk_decision") or {}).get("risk_fraction") is not None:
        out["risk_pct"] = f"{review['risk_decision']['risk_fraction'] * 100:.2f}%"
    signal = None if order.as_of is None else utc(order.as_of)
    if signal is None and order.run_id:
        submitting_review = store.review_report(order.run_id) or {}
        signal = submitting_review.get("as_of")
    if signal:
        out["signal_at_utc"] = utc(parse_utc(signal))
    if position is None:
        out.update(executed_at_utc="PENDING_FILL", closed_at_utc="NOT_FILLED")
    else:
        out["executed_at_utc"] = utc(position.opened_at)
        out["closed_at_utc"] = utc(trade.exited_at) if trade is not None else "OPEN"
    return out


def with_trade(store, events):
    """``events`` with the durable trade block attached to order/position notifications."""
    out = []
    for event in events:
        submitted = event.type != "EXECUTION_DECISION" or (event.execution or {}).get("execution_status") == "SUBMITTED"
        if event.type in TRADE_EVENTS and submitted:
            row = store.db.execute("SELECT j.* FROM notification_events n JOIN journal j ON j.id=n.journal_id "
                                   "WHERE n.event_id=?", (event.event_id,)).fetchone()
            block = None if row is None else trade_block(store, row, store.review_report(row["run_id"])
                                                         if row["run_id"] else None)
            event = replace(event, trade=block or None)
        out.append(event)
    return out


__all__ = ["AI_REVIEW_EVENTS", "capture_ai_review_health", "order_setup_id", "trade_block", "with_trade"]
