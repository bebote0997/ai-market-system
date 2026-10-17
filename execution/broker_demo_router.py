"""Broker-demo router: sends the runtime's APPROVED EURUSD plans to the OANDA PRACTICE account (independent of paper).

Built ONLY through ``router_from_env``, which returns None unless ``AI_FLOOR_BROKER_DEMO_ROUTING == "1"`` (exact).
With the flag ON it derives its own env mapping (``AI_FLOOR_BROKER_DEMO_ENABLED=1``, provider ``oanda``; ``os.environ``
is never mutated), switches ``broker_demo`` ON in the registry on the very first construction only (no audit row for
``broker_demo`` and no emergency row ever recorded) and builds an ``OandaBroker`` whose constructor enforces every
guard (REAL_EXECUTION_ENABLED False, practice host/account, token, switch ON, no emergency). Any failure -> masked
warning and None: paper trading is never affected.

The router never touches PaperBroker or the experiment DB. Its state is ``broker_demo_trades``/``broker_demo_events``
in the broker state DB (``execution/broker_demo_state.py``). Flow per cycle (``on_cycle``): candidate checks without
network (PLAN_READY, EURUSD only, ``eligible`` True, Risk APPROVED, plan == decision, geometry) -> idempotency by
``client_id = aif-<run_id>`` -> ``reconcile()`` (drift / unresolved / read failure block routing) -> single net position
gate -> sizing from broker NAV -> write-ahead intent (PENDING_SEND) -> ``OandaBroker.submit_plan`` -> ACCEPTED (OPEN) /
REJECTED / UNKNOWN (blocks routing until reconcile proves the outcome).

Dry-run (``AI_FLOOR_BROKER_DEMO_DRY_RUN=1``, opt-in) records the exact order JSON (event DRY_RUN) and never POSTs/PUTs;
it may read summary/pricing/openTrades. ``manage`` applies the trade-duration rule (execution/trade_duration.py) to
LIVE routed trades; ``emergency_close_all`` closes every routed OPEN trade (closing is always allowed).

Single process, exclusive ownership of the practice account (same assumption as OandaBroker).
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import logging
from pathlib import Path
import sqlite3
import threading

from execution import trade_duration as td
from execution.account_registry import AccountRegistry
from execution.broker_config import load_broker_config
from execution.broker_demo_state import (CLOSED, CLOSED_AT_BROKER, DRY_RUN, HEARTBEAT_FLAG, LIVE_STATUSES, MODE_FLAG,
                                         OPEN, PENDING_SEND, REJECTED, UNKNOWN, UNRESOLVED_STATUSES, DemoStateStore,
                                         dry_run_flag, max_units_setting, routing_flag, summarize_status)
from execution.broker_port import (ACCEPTED, APPROVED, BrokerAuthError, BrokerGuardError, BrokerMalformedResponse,
                                   BrokerRateLimited, OrderPlan, RiskApproval)
from execution.dxtrade_broker import real_execution_enabled
from execution.oanda_broker import (ACCOUNT_ENV, ALLOWED_SYMBOLS, CLIENT_TAG, DEMO_MODE, INSTRUMENTS, PROVIDER,
                                    TOKEN_ENV, OandaBroker, _comment, client_extension_id, size_units)
from execution.oanda_client import OandaClient, round_price


LOG = logging.getLogger("ai_floor.broker.demo_router")
RISK_FRACTION_CAP = Decimal("0.01")
PLAN_READY = "PLAN_READY"
DRIFT = "drift"
UNRESOLVED_ACTION = "unresolved_action"
BLOCKED_FLAG = "blocked_reason"
CLOSE_PREFIX = "CLOSE:"
BREAKEVEN = "BREAKEVEN"
SL_TP_OR_EXTERNAL = "SL_TP_OR_EXTERNAL"


def _dec(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = Decimal(str(value))
    except ArithmeticError:
        return None
    return out if out.is_finite() else None


def _parse_time(value):
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None


def risk_fraction(decision):
    """RiskDecision.risk_fraction when it is a number in (0, 0.01]; otherwise the 0.01 hard cap."""
    value = _dec(getattr(decision, "risk_fraction", None))
    return value if value is not None and 0 < value <= RISK_FRACTION_CAP else RISK_FRACTION_CAP


def broker_units(equity, decision, entry, stop, max_units):
    """size_units(equity * risk_fraction, entry, stop) capped at ``max_units`` (0 -> skip the order)."""
    return min(size_units(equity * risk_fraction(decision), entry, stop), int(max_units))


def build_order_preview(plan, units):
    """The exact OANDA order JSON ``submit_plan`` would POST for ``plan`` with ``units`` (pure, no network). Contains
    instrument, signed units, SL/TP and clientExtensions only: never headers, token or account id."""
    if plan.symbol not in ALLOWED_SYMBOLS:
        raise ValueError("symbol not supported")
    if type(units) is not int or units < 1:
        raise ValueError("units must be a positive int")
    extensions = {"id": client_extension_id(plan.client_id), "tag": CLIENT_TAG, "comment": _comment(plan.source_run_id)}
    return OandaClient.build_market_order(instrument=INSTRUMENTS[plan.symbol],
                                          units=units if plan.side == "LONG" else -units,
                                          stop=round_price(plan.stop), target=round_price(plan.target),
                                          client_extensions=extensions, trade_client_extensions=extensions)


@dataclass
class PositionView:
    """Position-like adapter for the pure functions of execution/trade_duration.py."""
    symbol: str
    side: str
    entry_price: Decimal
    stop: Decimal
    initial_stop: Decimal
    opened_at: datetime
    duration_stage: str
    position_id: str


def position_view(row):
    opened = _parse_time(row.get("opened_at")) or _parse_time(row.get("created_at"))
    if opened is None:
        raise ValueError("trade row without a usable open time")
    return PositionView(row["symbol"], row["side"], _dec(row.get("fill_price")) or _dec(row["entry"]),
                        _dec(row["stop"]), _dec(row.get("initial_stop")), opened, row.get("stage") or None,
                        row.get("trade_id") or row["client_id"])


def duration_action(trade_row, now, price):
    """Pure trade-duration decision for a routed LIVE trade: ``("CLOSE", reason)``, ``("BREAKEVEN", None)`` or None.

    Next boundary = Day-1 NY close (17:00 America/New_York after the open; Day-2 close once the stop was moved). At or
    after an unprocessed boundary: Friday -> close; Day-2 stage -> close; Day-1 below +0.5R of the ORIGINAL stop
    distance -> close; else breakeven. Max 2 days: a Day-1 trade already past its Day-2 close is closed. ``price``
    (bid for LONG, ask for SHORT) is only needed for the Day-1 R test; None there -> None (retry next tick)."""
    position = position_view(trade_row)
    boundary = td.next_boundary(position)
    if now < boundary:
        return None
    if position.duration_stage is None and now >= td.day2_close(position):
        return "CLOSE", td.CLOSE_FRIDAY if td.is_friday_close(td.day2_close(position)) else td.CLOSE_DAY2_NY_CLOSE
    if td.is_friday_close(boundary) or position.duration_stage is not None:
        return td.boundary_decision(position, boundary, price)
    if price is None:
        return None
    return td.boundary_decision(position, boundary, price)


def breakeven_stop(row):
    """The stop a Day-1 keep moves to: the fill price (planned entry until the fill is known), 5 decimals."""
    return round_price(_dec(row.get("fill_price")) or _dec(row["entry"]))


def _not_routed(reason):
    return "NOT_ROUTED", reason


class BrokerDemoRouter:
    def __init__(self, broker, store, *, registry, dry_run, max_units, clock=None):
        self._broker = broker
        self._store = store
        self._registry = registry
        self.dry_run = bool(dry_run)
        self.max_units = int(max_units)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()
        self.blocked_reason = store.flag(BLOCKED_FLAG) or None
        self._last_manage = {}

    def __repr__(self):
        return f"BrokerDemoRouter(broker={self._broker!r}, dry_run={self.dry_run}, max_units={self.max_units})"

    __str__ = __repr__

    # -- helpers ---------------------------------------------------------------------------------------------------
    def _event(self, kind, client_id=None, **detail):
        try:
            self._store.event(kind, client_id, detail or None)
        except Exception as exc:  # noqa: BLE001 - audit is best effort; decisions use the trade rows
            LOG.warning("component=broker_demo event=EVENT_WRITE_FAILED kind=%s error_type=%s", kind,
                        type(exc).__name__)

    def _heartbeat(self):
        try:  # Lets the (possibly separate) dashboard process know routing is alive; best effort.
            self._store.set_flag(MODE_FLAG, "dry_run" if self.dry_run else "live")
            self._store.set_flag(HEARTBEAT_FLAG, self._clock().isoformat())
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=broker_demo event=HEARTBEAT_FAILED error_type=%s", type(exc).__name__)

    def _set_blocked(self, reason):
        self.blocked_reason = reason
        self._store.set_flag(BLOCKED_FLAG, reason)

    def _open_allowed(self):
        try:
            return not self._registry.emergency_active() and self._registry.is_allowed(DEMO_MODE)
        except Exception:  # noqa: BLE001 - unreadable registry: fail closed
            return False

    # -- routing ---------------------------------------------------------------------------------------------------
    def on_cycle(self, context, status, at):
        """Called post-cycle by the runtime. Returns ``(outcome, reason)``; never changes the paper cycle."""
        with self._lock:
            self._heartbeat()
            reconciled = None
            if not self.dry_run and self._store.trades((OPEN,)):
                reconciled = self._manage_locked(at)
            return self._route(context, status, at, reconciled)

    def _candidate(self, context, status):
        """(plan, decision, symbol, run_id) or a NOT_ROUTED tuple. No network."""
        if status != PLAN_READY or not isinstance(context, dict):
            return "IGNORED", None
        symbol, run_id = context.get("symbol"), context.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            return _not_routed("run_id_missing")
        if symbol not in ALLOWED_SYMBOLS:
            return _not_routed("symbol_not_supported")
        deterministic, ai = context.get("deterministic"), context.get("ai")
        if context.get("eligible") is not True:
            return _not_routed("not_eligible")
        if getattr(deterministic, "final_status", None) != PLAN_READY or getattr(ai, "final_status", None) != PLAN_READY:
            return _not_routed("not_plan_ready")
        plan, decision = getattr(deterministic, "trade_plan", None), getattr(deterministic, "risk_decision", None)
        ai_decision = getattr(ai, "risk_decision", None)
        if (plan is None or getattr(decision, "status", None) != APPROVED
                or getattr(ai_decision, "status", None) != APPROVED):
            return _not_routed("risk_not_approved")
        prices = [(_dec(getattr(plan, n, None)), _dec(getattr(decision, n, None))) for n in ("entry", "stop", "target")]
        if (any(p is None or d is None or p != d for p, d in prices)
                or getattr(plan, "symbol", None) != symbol or getattr(decision, "symbol", None) != symbol
                or getattr(plan, "side", None) != getattr(decision, "side", None)
                or getattr(decision, "side", None) not in ("LONG", "SHORT")):
            return _not_routed("plan_decision_mismatch")
        entry, stop, target = (d for _p, d in prices)
        side = decision.side
        if not (stop < entry < target if side == "LONG" else target < entry < stop):
            return _not_routed("invalid_plan_geometry")
        return plan, decision, symbol, run_id

    def _route(self, context, status, at, reconciled):
        candidate = self._candidate(context, status)
        if len(candidate) == 2:
            outcome, reason = candidate
            if outcome == "NOT_ROUTED":
                self._event("NOT_ROUTED", None, reason=reason, symbol=context.get("symbol") if isinstance(
                    context, dict) else None, run_id=context.get("run_id") if isinstance(context, dict) else None)
            return candidate
        plan, decision, symbol, run_id = candidate
        client_id = f"aif-{run_id}"
        if self._store.trade(client_id) is not None:
            return "ALREADY_ROUTED", client_id
        report = reconciled if reconciled is not None else self.reconcile()
        if report is None:
            return self._refuse(client_id, "broker_read_failed")
        if self.blocked_reason:
            return self._refuse(client_id, f"blocked:{self.blocked_reason}")
        if self._store.trades(UNRESOLVED_STATUSES):
            return self._refuse(client_id, "unresolved_submission")
        if not self.dry_run and not self._open_allowed():
            return self._refuse(client_id, "broker_demo_not_allowed")
        if self._store.trades(LIVE_STATUSES) or any(t.instrument == INSTRUMENTS[symbol] for t in report):
            return self._refuse(client_id, "position_already_open")
        try:
            equity = self._broker.get_balance().equity
        except Exception as exc:  # noqa: BLE001 - nothing sent
            return self._refuse(client_id, f"balance_unavailable:{type(exc).__name__}")
        entry, stop, target = (_dec(getattr(decision, n)) for n in ("entry", "stop", "target"))
        try:
            units = broker_units(equity, decision, entry, stop, self.max_units)
        except ValueError:
            return self._refuse(client_id, "sizing_invalid")
        if units < 1:
            self._event("SKIPPED_ZERO_UNITS", client_id, run_id=run_id, equity=equity)
            return "SKIPPED_ZERO_UNITS", client_id
        try:  # A re-sized approval derived from the APPROVED decision: only the quantity differs.
            order_plan = OrderPlan(symbol, decision.side, Decimal(units), entry, stop, target, client_id=client_id,
                                   source_run_id=run_id, risk_policy_version=getattr(plan, "policy_version", None))
            approval = replace(RiskApproval.from_decision(decision, run_id=run_id), quantity=Decimal(units))
        except (TypeError, ValueError, ArithmeticError):
            return self._refuse(client_id, "approval_invalid")
        row = {"client_id": client_id, "run_id": run_id, "symbol": symbol, "side": decision.side, "units": units,
               "entry": round_price(entry), "stop": round_price(stop), "target": round_price(target)}
        if self.dry_run:
            return self._dry_run(order_plan, units, row)
        return self._send(order_plan, approval, row, at)

    def _refuse(self, client_id, reason):
        self._event("NOT_ROUTED", client_id, reason=reason)
        return "NOT_ROUTED", reason

    def _dry_run(self, order_plan, units, row):
        preview = build_order_preview(order_plan, units)
        tradeable = None
        try:
            tradeable = self._broker.get_quote(order_plan.symbol)[0]
        except Exception:  # noqa: BLE001 - informational only in dry-run
            pass
        self._store.insert_intent({**row, "status": DRY_RUN})
        self._event("DRY_RUN", row["client_id"], order=preview, tradeable=tradeable)
        return "DRY_RUN", row["client_id"]

    def _send(self, order_plan, approval, row, at):
        client_id = row["client_id"]
        self._store.insert_intent({**row, "status": PENDING_SEND})  # write-ahead: a failed insert sends nothing
        self._event("INTENT", client_id, units=row["units"], side=row["side"], entry=row["entry"], stop=row["stop"],
                    target=row["target"])
        try:
            result = self._broker.submit_plan(order_plan, approval)
        except (BrokerGuardError, BrokerAuthError, BrokerRateLimited) as exc:  # definite: nothing processed
            self._store.update(client_id, status=REJECTED, closed_reason=type(exc).__name__)
            self._event("REJECTED", client_id, reason=type(exc).__name__)
            return REJECTED, type(exc).__name__
        except Exception as exc:  # noqa: BLE001 - outcome unknown: keep it latched until reconcile proves it
            self._store.update(client_id, status=UNKNOWN, closed_reason=type(exc).__name__)
            self._event("UNKNOWN", client_id, reason=type(exc).__name__)
            return UNKNOWN, type(exc).__name__
        if result.status == ACCEPTED:
            self._mark_open(client_id, result.broker_ref, at)
            self._event("ACCEPTED", client_id, trade_id=result.broker_ref, reason=result.reason)
            return ACCEPTED, result.broker_ref
        if result.status == REJECTED:
            self._store.update(client_id, status=REJECTED, closed_reason=result.reason)
            self._event("REJECTED", client_id, reason=result.reason)
            return REJECTED, result.reason
        self._store.update(client_id, status=UNKNOWN, closed_reason=result.reason)
        self._event("UNKNOWN", client_id, reason=result.reason)
        return UNKNOWN, result.reason

    def _mark_open(self, client_id, trade_id, at, trade=None):
        if trade is None:
            try:
                trade = next((t for t in self._broker.open_trades() if t.trade_id == trade_id), None)
            except Exception:  # noqa: BLE001 - the next reconcile fills the open time / fill price
                trade = None
        opened = trade.open_time if trade is not None and trade.open_time is not None else at
        self._store.update(client_id, status=OPEN, trade_id=trade_id, opened_at=opened,
                           fill_price=None if trade is None else trade.price, closed_reason=None)

    # -- reconciliation --------------------------------------------------------------------------------------------
    def reconcile(self):
        """Compare broker open trades with local rows. Returns the broker trades, or None when the read failed (then
        nothing is marked missing and routing is blocked for this tick)."""
        with self._lock:
            try:
                trades = self._broker.open_trades()
            except Exception as exc:  # noqa: BLE001
                self._event("RECONCILE_READ_FAILED", None, error=type(exc).__name__)
                return None
            by_id = {t.trade_id: t for t in trades}
            by_ext = {t.client_ext_id: t for t in trades if t.client_ext_id}
            matched = set()
            for row in self._store.trades(LIVE_STATUSES):
                trade_id = self._reconcile_row(row, by_id, by_ext)
                if trade_id is not None:
                    matched.add(trade_id)
            drift = [(t.trade_id, t.instrument) for t in trades if t.trade_id not in matched]
            if drift and self.blocked_reason != DRIFT:
                self._set_blocked(DRIFT)
                self._event("DRIFT", None, trades=drift)
            if self.blocked_reason != DRIFT:  # drift wins; the next reconcile after clear_drift re-checks this
                unresolved = [r["client_id"] for r in self._store.trades((OPEN,)) if r.get("action_status") == UNKNOWN]
                if unresolved and self.blocked_reason != UNRESOLVED_ACTION:
                    self._set_blocked(UNRESOLVED_ACTION)
                    self._event("UNRESOLVED_ACTION", None, client_ids=unresolved)
                elif not unresolved and self.blocked_reason == UNRESOLVED_ACTION:
                    self._set_blocked(None)
                    self._event("UNRESOLVED_ACTION_RESOLVED", None)
            return trades

    def _reconcile_row(self, row, by_id, by_ext):
        """Resolve one local row; returns the broker tradeID it is matched to (None when not open at the broker)."""
        client_id = row["client_id"]
        if row["status"] == OPEN:
            trade = by_id.get(row["trade_id"])
            if trade is None:
                action = row.get("pending_action") or ""
                reason = action[len(CLOSE_PREFIX):] if action.startswith(CLOSE_PREFIX) else SL_TP_OR_EXTERNAL
                status = CLOSED if action.startswith(CLOSE_PREFIX) else CLOSED_AT_BROKER
                self._store.update(client_id, status=status, closed_reason=reason, pending_action=None,
                                   action_status=None)
                self._event(status, client_id, trade_id=row["trade_id"], reason=reason)
                return None
            updates = {}
            if not row.get("fill_price") or trade.open_time is not None and not row.get("opened_at"):
                updates.update(fill_price=trade.price, **({"opened_at": trade.open_time} if trade.open_time else {}))
            # An UNKNOWN action on a trade that is still open is resolved only by definitive evidence: a breakeven
            # whose stop is at the broker. "Still open" alone proves nothing (a close/modify may still be in flight),
            # so the latch stays (blocked_reason unresolved_action) until evidence or clear_unresolved().
            if (row.get("action_status") == UNKNOWN and row.get("pending_action") == BREAKEVEN
                    and trade.stop is not None and round_price(trade.stop) == breakeven_stop(row)):
                updates.update(stage=td.STAGE_DAY2, initial_stop=row["stop"], stop=breakeven_stop(row),
                               pending_action=None, action_status=None)
                self._event("BREAKEVEN", client_id, trade_id=trade.trade_id, stop=trade.stop, resolved=True)
            if updates:
                self._store.update(client_id, **updates)
            return trade.trade_id
        trade = by_ext.get(client_extension_id(client_id))
        if trade is not None:
            self._store.update(client_id, status=OPEN, trade_id=trade.trade_id, fill_price=trade.price,
                               opened_at=trade.open_time or row.get("created_at"), closed_reason=None)
            self._event("RESOLVED", client_id, trade_id=trade.trade_id, status=OPEN)
            return trade.trade_id
        try:
            trade_id = self._broker.trade_id_for_client(client_id)
        except Exception:  # noqa: BLE001 - stays unresolved (routing blocked)
            return None
        if trade_id is not None:  # the trade exists but is no longer open: it was filled and closed at the broker
            self._store.update(client_id, status=CLOSED_AT_BROKER, trade_id=trade_id, closed_reason=SL_TP_OR_EXTERNAL)
            self._event("RESOLVED", client_id, trade_id=trade_id, status=CLOSED_AT_BROKER)
        return None

    def clear_drift(self, actor, reason):
        """Operator acknowledgement of a drift (e.g. after closing the unknown trade). The next reconcile re-blocks if
        the drift is still there."""
        if not actor:
            raise ValueError("actor required")
        with self._lock:
            self._set_blocked(None)
            self._event("DRIFT_CLEARED", None, actor=str(actor), reason=str(reason))

    def clear_unresolved(self, actor, reason):
        """Operator release of UNKNOWN close/breakeven actions (e.g. after checking the platform): the latch is
        dropped, so ``manage`` may retry the action on its next pass. Returns the released client ids."""
        if not actor:
            raise ValueError("actor required")
        with self._lock:
            released = [r["client_id"] for r in self._store.trades((OPEN,)) if r.get("action_status") == UNKNOWN]
            for client_id in released:
                self._store.update(client_id, pending_action=None, action_status=None)
            if self.blocked_reason == UNRESOLVED_ACTION:
                self._set_blocked(None)
            self._event("UNRESOLVED_CLEARED", None, actor=str(actor), reason=str(reason), client_ids=released)
            return released

    def mark_not_sent(self, client_id, actor, reason):
        """Operator release of an unresolved submission, only when a fresh read proves no trade has that client id."""
        if not actor:
            raise ValueError("actor required")
        with self._lock:
            row = self._store.trade(client_id)
            if row is None or row["status"] not in UNRESOLVED_STATUSES:
                return False
            ext_id = client_extension_id(client_id)
            if any(t.client_ext_id == ext_id for t in self._broker.open_trades()):
                return False
            if self._broker.trade_id_for_client(client_id) is not None:
                return False
            self._store.update(client_id, status=REJECTED, closed_reason="operator_confirmed_not_sent")
            self._event("NOT_SENT_CONFIRMED", client_id, actor=str(actor), reason=str(reason))
            return True

    # -- position management ---------------------------------------------------------------------------------------
    def manage(self, at, *, price_provider=None):
        """Trade-duration rule on LIVE routed trades (reconcile first). Returns {client_id: outcome}."""
        with self._lock:
            self._heartbeat()
            report = self._manage_locked(at, price_provider=price_provider)
            return self._last_manage if report is not None else {"_": "READ_FAILED"}

    def _manage_locked(self, at, price_provider=None):
        self._last_manage = {}
        report = self.reconcile()
        if report is None or self.dry_run:
            return report
        quotes = {}
        for row in self._store.trades((OPEN,)):
            if row.get("action_status") == UNKNOWN:
                self._last_manage[row["client_id"]] = "ACTION_UNRESOLVED"
                continue
            self._last_manage[row["client_id"]] = self._manage_row(row, at, quotes, price_provider)
        return report

    def _quote(self, symbol, price_provider):
        """``((tradeable, bid, ask), None)`` for a valid tradeable quote, else ``(None, problem)``."""
        try:
            quote = price_provider(symbol) if price_provider is not None else self._broker.get_quote(symbol)
        except Exception as exc:  # noqa: BLE001
            return None, "pricing_malformed" if isinstance(exc, BrokerMalformedResponse) else "pricing_unavailable"
        if quote is None:
            return None, "pricing_unavailable"
        if not isinstance(quote, tuple) or len(quote) != 3:
            return None, "pricing_malformed"
        tradeable, bid, ask = quote
        if tradeable is not True:
            return None, "not_tradeable"
        bid, ask = _dec(bid), _dec(ask)
        if bid is None or ask is None or bid <= 0 or ask < bid:
            return None, "pricing_malformed"
        return (True, bid, ask), None

    def _manage_row(self, row, at, quotes, price_provider):
        try:
            due = at >= td.next_boundary(position_view(row))
        except ValueError:
            return "NO_OPEN_TIME"
        if not due:
            return "KEEP"
        # Every management action (Friday / Day-2 / Day-1 close, breakeven) needs a valid tradeable quote. Without one
        # nothing is sent; the boundary stays overdue and is retried on every tick.
        if row["symbol"] not in quotes:
            quotes[row["symbol"]] = quote, problem = self._quote(row["symbol"], price_provider)
            if problem is not None:  # once per tick (the quote is read once per tick and symbol)
                LOG.info("component=broker_demo event=MANAGE_SKIPPED reason=%s", problem)
                self._event("MANAGE_SKIPPED", None, reason=problem)
        quote, problem = quotes[row["symbol"]]
        if problem is not None:
            return "MARKET_CLOSED" if problem == "not_tradeable" else "NO_PRICE"
        _tradeable, bid, ask = quote
        action = duration_action(row, at, bid if row["side"] == "LONG" else ask)
        if action is None:
            return "KEEP"
        kind, reason = action
        if kind == "CLOSE":
            return self._close(row, reason, f"{row['client_id']}-close")
        return self._breakeven(row)

    def _close(self, row, reason, close_id):
        client_id = row["client_id"]
        try:
            result = self._broker.close_market(row["trade_id"], reason=reason, client_id=close_id)
        except (BrokerAuthError, BrokerRateLimited) as exc:  # definite: not processed, retried next tick
            self._event("CLOSE_FAILED", client_id, reason=type(exc).__name__)
            return "CLOSE_FAILED"
        except Exception as exc:  # noqa: BLE001 - may have closed: latch until reconcile
            self._store.update(client_id, pending_action=CLOSE_PREFIX + reason, action_status=UNKNOWN)
            self._event("CLOSE_UNKNOWN", client_id, reason=type(exc).__name__)
            return UNKNOWN
        if result.status == ACCEPTED:
            self._store.update(client_id, status=CLOSED, closed_reason=reason, pending_action=None, action_status=None)
            self._event("CLOSED", client_id, trade_id=row["trade_id"], reason=reason)
            return "CLOSED"
        if result.status == REJECTED and result.reason == "no_open_position":
            self._store.update(client_id, status=CLOSED_AT_BROKER, closed_reason=SL_TP_OR_EXTERNAL,
                               pending_action=None, action_status=None)
            self._event(CLOSED_AT_BROKER, client_id, trade_id=row["trade_id"], reason=SL_TP_OR_EXTERNAL)
            return CLOSED_AT_BROKER
        if result.status == REJECTED:
            self._event("CLOSE_REJECTED", client_id, reason=result.reason)
            return "CLOSE_REJECTED"
        self._store.update(client_id, pending_action=CLOSE_PREFIX + reason, action_status=UNKNOWN)
        self._event("CLOSE_UNKNOWN", client_id, reason=result.reason)
        return UNKNOWN

    def _breakeven(self, row):
        client_id = row["client_id"]
        entry = breakeven_stop(row)
        try:
            result = self._broker.modify_stop(row["trade_id"], entry, client_id=f"{client_id}-be")
        except (BrokerGuardError, BrokerAuthError, BrokerRateLimited) as exc:  # switch OFF / emergency / definite
            self._event("BREAKEVEN_BLOCKED", client_id, reason=type(exc).__name__)
            return "BREAKEVEN_BLOCKED"
        except Exception as exc:  # noqa: BLE001
            self._store.update(client_id, pending_action=BREAKEVEN, action_status=UNKNOWN)
            self._event("BREAKEVEN_UNKNOWN", client_id, reason=type(exc).__name__)
            return UNKNOWN
        if result.status == ACCEPTED:
            self._store.update(client_id, stage=td.STAGE_DAY2, initial_stop=row["stop"], stop=entry)
            self._event("BREAKEVEN", client_id, trade_id=row["trade_id"], stop=entry)
            return "BREAKEVEN"
        if result.status == REJECTED:
            self._event("BREAKEVEN_REJECTED", client_id, reason=result.reason)
            return "BREAKEVEN_REJECTED"
        self._store.update(client_id, pending_action=BREAKEVEN, action_status=UNKNOWN)
        self._event("BREAKEVEN_UNKNOWN", client_id, reason=result.reason)
        return UNKNOWN

    def emergency_close_all(self, reason="emergency", **_kwargs):
        """close_market every routed OPEN trade (allowed with the switch OFF / emergency). Usable as the registry
        ``on_emergency`` hook (``on_emergency(reason=...)``); NOT registered automatically.

        Dry-run sends nothing: it only records a DRY_RUN preview event per OPEN row. A dry-run router never creates
        broker trades, so an OPEN row here comes from an earlier live run (or a trade opened outside the router) and
        must be closed manually / with scripts/oanda_first_test_order.py --close."""
        with self._lock:
            results = {}
            for row in self._store.trades((OPEN,)):
                close_reason = f"EMERGENCY:{reason}"[:64]
                if self.dry_run:
                    self._event("DRY_RUN", row["client_id"], action="emergency_close", trade_id=row["trade_id"],
                                reason=close_reason)
                    results[row["client_id"]] = DRY_RUN
                    continue
                results[row["client_id"]] = self._close(row, close_reason, f"{row['client_id']}-emergency")
            return results

    # -- read-only view --------------------------------------------------------------------------------------------
    def status(self):
        try:
            rows, events = self._store.trades(), self._store.events(5)
        except Exception:  # noqa: BLE001
            rows, events = [], []
        return summarize_status(rows, events, routing=True, dry_run=self.dry_run, blocked_reason=self.blocked_reason,
                                max_units=self.max_units)


def _first_construction(path):
    """True when no switch/emergency audit row was EVER written for broker_demo (read failures raise)."""
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0) as con:
        row = con.execute("SELECT 1 FROM broker_switches_audit WHERE account_key IN ('broker_demo', 'ALL') "
                          "LIMIT 1").fetchone()
    return row is None


def router_from_env(env, *, registry=None, transport=None, clock=None, sleep=None, broker_clock=None):
    """The only constructor. None unless ``AI_FLOOR_BROKER_DEMO_ROUTING == "1"``; any failure -> None (fail closed)."""
    if not routing_flag(env):
        return None
    try:
        return _build(env, registry=registry, transport=transport, clock=clock, sleep=sleep, broker_clock=broker_clock)
    except Exception as exc:  # noqa: BLE001 - paper is never affected by a broken broker setup
        LOG.warning("component=broker_demo event=ROUTER_DISABLED error_type=%s", type(exc).__name__)
        return None


def _build(env, *, registry, transport, clock, sleep, broker_clock):
    if real_execution_enabled() is not False:
        raise BrokerGuardError("REAL_EXECUTION_ENABLED must stay False")
    derived = {**env, "AI_FLOOR_BROKER_DEMO_ENABLED": "1", "AI_FLOOR_BROKER_DEMO_PROVIDER": PROVIDER}
    token = derived.get(TOKEN_ENV)
    if not isinstance(token, str) or not token.strip():
        LOG.warning("component=broker_demo event=ROUTER_DISABLED reason=%s_missing", TOKEN_ENV)
        return None
    account = derived.get(ACCOUNT_ENV)
    if not isinstance(account, str) or not account.strip():  # no default account id exists in code
        LOG.warning("component=broker_demo event=ROUTER_DISABLED reason=%s_missing", ACCOUNT_ENV)
        return None
    config = load_broker_config(derived)
    if config.emergency_stop:
        LOG.warning("component=broker_demo event=ROUTER_DISABLED reason=emergency_stop_env")
        return None
    registry = registry if registry is not None else AccountRegistry(config.state_db_path, env=derived)
    registry.initialize()
    if registry.emergency_active():
        LOG.warning("component=broker_demo event=ROUTER_DISABLED reason=emergency_stop")
        return None
    if _first_construction(registry.path):
        registry.set_enabled(DEMO_MODE, True, actor="router_from_env", source="routing_flag")
    broker = OandaBroker(env=derived, mode=DEMO_MODE, registry=registry, transport=transport, clock=broker_clock,
                         sleep=sleep)
    now = clock or (lambda: datetime.now(timezone.utc))
    store = DemoStateStore(registry.path, clock=lambda: now().isoformat())
    store.initialize()
    router = BrokerDemoRouter(broker, store, registry=registry, dry_run=dry_run_flag(env),
                              max_units=max_units_setting(env), clock=now)
    router._heartbeat()
    LOG.info("component=broker_demo event=ROUTER_READY dry_run=%s broker=%r", router.dry_run, broker)
    return router
