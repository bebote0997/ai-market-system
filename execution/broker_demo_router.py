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

Optional daily loss limit / drawdown halt (``execution/daily_loss_limit.py``, OFF unless ``AI_FLOOR_DAILY_LOSS_LIMIT ==
"1"``): evaluated on every cycle, every ``manage`` tick and before every entry; a halt refuses entries and (flatten ON /
drawdown) closes the routed OPEN trades. Independent from the kill switch: it never sets ``blocked_reason``. Deposits /
withdrawals are excluded via a read-only ``TRANSFER_FUNDS`` query whenever the summary's ``lastTransactionID`` moves;
while NAV (or that query) is unavailable entries are refused but an active halt keeps flattening.

Operator requests from the dashboard (``execution/operator_actions.py``, another process) arrive as state-DB flags and
are processed at the start of every ``on_cycle`` / ``manage``: ``flatten_request`` (emergency button) reconciles first,
then closes every routed OPEN trade and is dropped only after a successful reconcile with no OPEN / PENDING_SEND /
UNKNOWN row left; ``drift_clear_request`` is bound to the current ``drift_episode`` and honoured by ``reconcile`` only
when that same pass verifies the broker and the local state match (clear + request consumption + audit event in one
transaction). With an emergency active ``router_from_env`` returns None, unless a flatten request or routed rows
remain: then it builds a ``closing_only`` router on a ``close_only`` OandaBroker (entries refused ``emergency_active``,
every routed OPEN trade closed on every tick; the switch is never turned ON). Optional Friday pre-flatten (``execution/friday_close.py``, OFF unless
``AI_FLOOR_FRIDAY_PREFLATTEN == "1"``) closes routed trades before the 17:00 NY Friday halt and refuses entries
until the market reopens. The optional ``on_alert`` hook (Slack, ``runtime/broker_alerts.py``; set by
``runtime/service.py`` only when alerts are ON) receives the ``ALERT_KINDS`` events; it is best effort and never alters
routing.

Single process, exclusive ownership of the practice account (same assumption as OandaBroker).
"""
from contextlib import closing
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
import logging
from pathlib import Path
import sqlite3
import threading
from uuid import uuid4

from execution import friday_close as fc
from execution import trade_duration as td
from execution.account_registry import AccountRegistry
from execution.broker_config import load_broker_config
from execution.broker_demo_state import (CLOSED, CLOSED_AT_BROKER, DRY_RUN, HEARTBEAT_FLAG, LIVE_STATUSES, MODE_FLAG,
                                         OPEN, PENDING_SEND, REJECTED, UNKNOWN, UNRESOLVED_STATUSES, DemoStateStore,
                                         dry_run_flag, max_units_setting, routing_flag, summarize_status)
from execution.broker_port import (ACCEPTED, APPROVED, BrokerAuthError, BrokerGuardError, BrokerMalformedResponse,
                                   BrokerRateLimited, OrderPlan, RiskApproval)
from execution.demo_epoch import demo_start_equity
from execution.daily_loss_limit import NAV_INVALID, STATE_UNREADABLE, UNAVAILABLE, LossDecision, limit_from_env
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
FLATTEN_REQUEST_FLAG = "flatten_request"  # same keys as execution/operator_actions.py
DRIFT_CLEAR_REQUEST_FLAG = "drift_clear_request"
DRIFT_EPISODE_FLAG = "drift_episode"
EMERGENCY_ACTIVE = "EMERGENCY_ACTIVE"
EMERGENCY_BUTTON = "EMERGENCY_BUTTON"
FRIDAY_PREFLATTEN = "FRIDAY_PREFLATTEN"
FRIDAY_ALERT = "FRIDAY_FLATTEN_FAILED"
PENDING_EVENT_CAP = 50  # FLATTEN_REQUEST_PENDING (and dry-run previews) per request: the events table is append-only
FRIDAY_ALERT_EVERY = {fc.PREFLATTEN: timedelta(minutes=5), fc.CLOSED: timedelta(minutes=30)}
# Events forwarded to the optional ``on_alert`` hook (best effort; FRIDAY_FLATTEN_FAILED has its own explicit call).
ALERT_KINDS = frozenset({"ACCEPTED", REJECTED, UNKNOWN, CLOSED, "CLOSE_UNKNOWN", "CLOSE_FAILED", CLOSED_AT_BROKER,
                         BREAKEVEN, "DRIFT", "UNRESOLVED_ACTION", "DAILY_LOSS_HALT", "DAILY_LOSS_RESUMED",
                         "DRAWDOWN_HALT", "FLATTEN_DAILY_LOSS", "FLATTEN_DRAWDOWN", EMERGENCY_BUTTON,
                         "FLATTEN_REQUEST_DONE", "DRIFT_CLEARED", "DRIFT_CLEAR_REFUSED"})
CLOSE_SENT = {"CLOSED", CLOSED_AT_BROKER, "CLOSE_FAILED", "CLOSE_REJECTED", UNKNOWN, DRY_RUN}


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


# Close / breakeven outcomes also go to the process log (one line each; the broker trade number only, never the
# account id, token or client id). Every close path (manage, daily-loss/drawdown, Friday, emergency) uses _close.
ACTION_LOG_KINDS = frozenset({"CLOSED", "CLOSE_UNKNOWN", "CLOSE_FAILED", "CLOSE_REJECTED", CLOSED_AT_BROKER,
                              "BREAKEVEN", "BREAKEVEN_UNKNOWN", "BREAKEVEN_REJECTED", "BREAKEVEN_BLOCKED"})
_ACTION_LOG_OK = frozenset({"CLOSED", CLOSED_AT_BROKER, "BREAKEVEN"})


# Only these controlled reason codes reach the log; "EMERGENCY:<free text>" -> EMERGENCY, anything else (broker
# reject text, unexpected exception names, ...) -> OTHER, so a reason can never leak an id or token.
_LOG_REASONS = frozenset({*td.CLOSE_REASONS, SL_TP_OR_EXTERNAL, "DAILY_LOSS", "DRAWDOWN", FRIDAY_PREFLATTEN,
                          EMERGENCY_ACTIVE, EMERGENCY_BUTTON, "BrokerAuthError", "BrokerRateLimited",
                          "BrokerGuardError", "BrokerMalformedResponse", "no_open_position"})


def _log_token(value):
    text = "-" if value is None or value == "" else str(value)
    return "".join(ch if ch.isalnum() or ch in "._:-" else "_" for ch in text)[:64]


def _log_reason(reason):
    if reason is None or reason == "":
        return "-"
    if reason in _LOG_REASONS:
        return reason
    return "EMERGENCY" if str(reason).startswith("EMERGENCY") else "OTHER"


def _log_action(kind, trade_id, reason):
    level = logging.INFO if kind in _ACTION_LOG_OK else logging.WARNING
    LOG.log(level, "component=broker_demo event=%s trade_id=%s reason=%s", kind, _log_token(trade_id),
            _log_reason(reason))


def _not_routed(reason):
    return "NOT_ROUTED", reason


class BrokerDemoRouter:
    def __init__(self, broker, store, *, registry, dry_run, max_units, clock=None, loss_limit=None,
                 friday_minutes=None, closing_only=False, on_alert=None, env=None):
        self._broker = broker
        self._env = env  # demo_start_equity source (None: os.environ)
        self.closing_only = bool(closing_only)  # emergency restart: closes routed trades, never opens
        self._loss_limit = loss_limit
        self._friday_minutes = friday_minutes  # None: Friday pre-flatten OFF (behaviour unchanged)
        self._friday_alerted = None
        self._flatten_ticks = 0
        self.on_alert = on_alert  # optional callable(kind, detail dict | message str); see ALERT_KINDS
        if loss_limit is not None:
            loss_limit.on_event = self._alert  # halt / resume events reach the same hook
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
    def _event(self, kind, client_id=None, *, log_trade_id=None, **detail):
        try:
            self._store.event(kind, client_id, detail or None)
        except Exception as exc:  # noqa: BLE001 - audit is best effort; decisions use the trade rows
            LOG.warning("component=broker_demo event=EVENT_WRITE_FAILED kind=%s error_type=%s", kind,
                        type(exc).__name__)
        if kind in ACTION_LOG_KINDS:
            _log_action(kind, detail.get("trade_id", log_trade_id), detail.get("reason"))
        self._alert(kind, detail)

    def _alert(self, kind, detail):
        """Best-effort ``on_alert`` call for ALERT_KINDS; never raises and never changes the caller's outcome."""
        if self.on_alert is None or kind not in ALERT_KINDS:
            return
        try:
            self.on_alert(kind, dict(detail))
        except Exception as exc:  # noqa: BLE001 - alerting never breaks routing
            LOG.warning("component=broker_demo event=ALERT_HOOK_FAILED error_type=%s", type(exc).__name__)

    def _heartbeat(self):
        try:  # Lets the (possibly separate) dashboard process know routing is alive; best effort.
            self._store.set_flag(MODE_FLAG, "dry_run" if self.dry_run else "live")
            self._store.set_flag(HEARTBEAT_FLAG, self._clock().isoformat())
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=broker_demo event=HEARTBEAT_FAILED error_type=%s", type(exc).__name__)

    def _set_blocked(self, reason):
        self.blocked_reason = reason
        if reason == DRIFT:  # every drift block is a new episode: older drift-clear requests can never match it
            self._store.apply(set_flags={DRIFT_EPISODE_FLAG: uuid4().hex, BLOCKED_FLAG: reason})
        else:
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
            flattened, reconciled = self._process_requests(at)
            if self._loss_limit is not None:
                flattened = {**flattened, **self._loss_check(at)[1]}
            if self._friday_minutes is not None:
                flattened = {**flattened, **self._friday_tick(at)}
            if not self.dry_run and self._store.trades((OPEN,)):
                reconciled = self._manage_locked(at, skip=flattened, report=reconciled)
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
        if self.closing_only:  # emergency restart: never opens (the close-only broker refuses submits too)
            return self._refuse(client_id, "emergency_active")
        if self._store.trade(client_id) is not None:
            return "ALREADY_ROUTED", client_id
        if self._friday_minutes is not None and fc.friday_window(at, self._friday_minutes) != fc.OPEN:
            return self._refuse(client_id, "friday_close_window")  # dry-run too; no network
        report =reconciled if reconciled is not None else self.reconcile()
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
            balance = self._broker.get_balance()
        except Exception as exc:  # noqa: BLE001 - nothing sent
            return self._refuse(client_id, f"balance_unavailable:{type(exc).__name__}")
        equity = balance.equity
        entry, stop, target = (_dec(getattr(decision, n)) for n in ("entry", "stop", "target"))
        try:
            units = broker_units(equity, decision, entry, stop, self.max_units)
        except ValueError:
            return self._refuse(client_id, "sizing_invalid")
        if units < 1:
            self._event("SKIPPED_ZERO_UNITS", client_id, run_id=run_id, equity=equity)
            return "SKIPPED_ZERO_UNITS", client_id
        if self._loss_limit is not None:
            loss = self._loss_check(at, Decimal(units) * abs(entry - stop), balance=balance)[0]
            if not loss.entry_allowed:
                return self._refuse(client_id, loss.reason)
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
                self._drift_clear_request(lambda: ["broker_read_failed"])
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
            self._drift_clear_request(lambda: self._drift_problems(drift))
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

    def _drift_problems(self, drift):
        """Why a drift block cannot be cleared now (empty list = broker and local state verified equal)."""
        problems = ["drift_present"] if drift else []
        if self._store.trades(UNRESOLVED_STATUSES):
            problems.append("unresolved_submission")
        if any(r.get("action_status") == UNKNOWN for r in self._store.trades((OPEN,))):
            problems.append("unresolved_action")
        return problems

    def _drift_clear_request(self, problems):
        """Honour a dashboard ``drift_clear_request`` only when it carries the current ``drift_episode`` and this
        reconcile pass verified ``problems()`` empty. The request is consumed either way (a refused one needs a new
        request); nothing is ever cleared without one. Clear / refusal, request removal and the audit event are ONE
        transaction: if it fails nothing changes (the request stays bound to the same episode)."""
        try:
            raw = self._store.flag(DRIFT_CLEAR_REQUEST_FLAG)
            if not raw:
                return
            try:
                request = json.loads(raw)
                actor, reason = str(request["actor"])[:64], str(request.get("reason") or "")[:120]
                episode = request.get("episode")
            except (TypeError, ValueError, KeyError, AttributeError):
                actor, reason, episode = None, "", None
            if self.blocked_reason != DRIFT:
                found = ["not_blocked_by_drift"]
            elif not actor:
                found = ["request_invalid"]
            elif not isinstance(episode, str) or not episode or episode != self._store.flag(DRIFT_EPISODE_FLAG):
                found = ["stale_episode"]
            else:
                found = problems()
            consumed = {DRIFT_CLEAR_REQUEST_FLAG: raw}
            if found:
                self._store.apply(delete_flags=consumed, event=("DRIFT_CLEAR_REFUSED", None, {
                    "actor": actor, "reasons": found, "source": "dashboard"}))
                self._alert("DRIFT_CLEAR_REFUSED", {"reasons": found})
            else:
                self._store.apply(set_flags={BLOCKED_FLAG: None},
                                  delete_flags={**consumed, DRIFT_EPISODE_FLAG: episode},
                                  event=("DRIFT_CLEARED", None, {"actor": actor, "reason": reason,
                                                                 "source": "dashboard", "episode": episode}))
                self.blocked_reason = None
                self._alert("DRIFT_CLEARED", {"reason": reason})
        except Exception as exc:  # noqa: BLE001 - the transaction rolled back: nothing cleared, request kept
            LOG.warning("component=broker_demo event=DRIFT_CLEAR_FAILED error_type=%s", type(exc).__name__)

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
            flattened, report = self._process_requests(at)
            if self._loss_limit is not None:  # a halt / flatten happens on every tick, even with paper disabled
                flattened = {**flattened, **self._loss_check(at)[1]}
            if self._friday_minutes is not None:
                flattened = {**flattened, **self._friday_tick(at, price_provider)}
            report = self._manage_locked(at, price_provider=price_provider, skip=flattened, report=report)
            if flattened and report is not None:
                self._last_manage = {**flattened, **self._last_manage}
            return self._last_manage if report is not None else {"_": "READ_FAILED"}

    def _manage_locked(self, at, price_provider=None, skip=(), report=None):
        """``report``: this tick's successful reconcile (flatten request), reused instead of a second read."""
        self._last_manage = {}
        report = self.reconcile() if report is None else report
        if report is None or self.dry_run:
            return report
        quotes = {}
        for row in self._store.trades((OPEN,)):
            if row["client_id"] in skip:  # already acted on by the loss-limit flatten this tick
                continue
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
            self._event("CLOSE_FAILED", client_id, reason=type(exc).__name__, log_trade_id=row["trade_id"])
            return "CLOSE_FAILED"
        except Exception as exc:  # noqa: BLE001 - may have closed: latch until reconcile
            self._store.update(client_id, pending_action=CLOSE_PREFIX + reason, action_status=UNKNOWN)
            self._event("CLOSE_UNKNOWN", client_id, reason=type(exc).__name__, log_trade_id=row["trade_id"])
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
            self._event("CLOSE_REJECTED", client_id, reason=result.reason, log_trade_id=row["trade_id"])
            return "CLOSE_REJECTED"
        self._store.update(client_id, pending_action=CLOSE_PREFIX + reason, action_status=UNKNOWN)
        self._event("CLOSE_UNKNOWN", client_id, reason=result.reason, log_trade_id=row["trade_id"])
        return UNKNOWN

    def _breakeven(self, row):
        client_id = row["client_id"]
        entry = breakeven_stop(row)
        try:
            result = self._broker.modify_stop(row["trade_id"], entry, client_id=f"{client_id}-be")
        except (BrokerGuardError, BrokerAuthError, BrokerRateLimited) as exc:  # switch OFF / emergency / definite
            self._event("BREAKEVEN_BLOCKED", client_id, reason=type(exc).__name__, log_trade_id=row["trade_id"])
            return "BREAKEVEN_BLOCKED"
        except Exception as exc:  # noqa: BLE001
            self._store.update(client_id, pending_action=BREAKEVEN, action_status=UNKNOWN)
            self._event("BREAKEVEN_UNKNOWN", client_id, reason=type(exc).__name__, log_trade_id=row["trade_id"])
            return UNKNOWN
        if result.status == ACCEPTED:
            self._store.update(client_id, stage=td.STAGE_DAY2, initial_stop=row["stop"], stop=entry)
            self._event("BREAKEVEN", client_id, trade_id=row["trade_id"], stop=entry)
            return "BREAKEVEN"
        if result.status == REJECTED:
            self._event("BREAKEVEN_REJECTED", client_id, reason=result.reason, log_trade_id=row["trade_id"])
            return "BREAKEVEN_REJECTED"
        self._store.update(client_id, pending_action=BREAKEVEN, action_status=UNKNOWN)
        self._event("BREAKEVEN_UNKNOWN", client_id, reason=result.reason, log_trade_id=row["trade_id"])
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

    # -- operator requests (dashboard emergency button) ------------------------------------------------------------
    def _process_requests(self, at):
        """Emergency-button ``flatten_request`` (or, in ``closing_only`` mode, the active emergency itself while routed
        rows remain): reconcile first (a filled PENDING_SEND/UNKNOWN submission becomes OPEN), then close every routed
        OPEN trade (UNKNOWN actions skipped; closing is always allowed) on every tick. The request is dropped only
        after a successful reconcile this tick with no OPEN / PENDING_SEND / UNKNOWN row left (an UNKNOWN action keeps
        its row OPEN); a failed read keeps it. Dry-run: DRY_RUN events only. Returns ``({client_id: outcome}, report)``
        with this tick's broker trades (None when nothing was reconciled or the read failed); never raises."""
        try:
            raw = self._store.flag(FLATTEN_REQUEST_FLAG)
            if not raw and not (self.closing_only and self._store.trades(LIVE_STATUSES)):
                self._flatten_ticks = 0
                return {}, None
            reason, suffix = (EMERGENCY_BUTTON, "ebtn") if raw else (EMERGENCY_ACTIVE, "emergency")
            implicit = {} if raw else {"implicit": True}  # closing_only: the active emergency is the request
            self._flatten_ticks += 1
            if raw and self._flatten_ticks == 1:
                self._alert(EMERGENCY_BUTTON, {})
            quiet = self._flatten_ticks > PENDING_EVENT_CAP
            report = self.reconcile()
            if report is None:  # nothing verified: keep the request, retry next tick
                if not quiet:
                    self._event("FLATTEN_REQUEST_PENDING", None, reason="broker_read_failed", tick=self._flatten_ticks,
                                **implicit)
                return {}, None
            results = {}
            for row in self._store.trades((OPEN,)):
                client_id = row["client_id"]
                if row.get("action_status") == UNKNOWN:
                    results[client_id] = "ACTION_UNRESOLVED"
                elif self.dry_run:
                    if not quiet:
                        self._event("DRY_RUN", client_id, action="emergency_button_close", trade_id=row["trade_id"],
                                    reason=reason)
                    results[client_id] = DRY_RUN
                else:
                    results[client_id] = self._close(row, reason, f"{client_id}-{suffix}")
            remaining = len(self._store.trades(LIVE_STATUSES))
            if not remaining:
                if raw:
                    self._store.clear_flag(FLATTEN_REQUEST_FLAG, raw)
                self._event("FLATTEN_REQUEST_DONE", None, results=results, **implicit)
                self._flatten_ticks = 0
            elif not quiet:
                self._event("FLATTEN_REQUEST_PENDING", None, results=results, open_trades=remaining,
                            tick=self._flatten_ticks, **implicit)
            return results, report
        except Exception as exc:  # noqa: BLE001 - never breaks the cycle; retried next tick
            LOG.error("component=broker_demo event=FLATTEN_REQUEST_ERROR error_type=%s", type(exc).__name__)
            return {}, None

    # -- Friday close (OANDA halts EUR_USD at 17:00 NY) -------------------------------------------------------------
    def _friday_tick(self, at, price_provider=None):
        """Pre-flatten window: close every routed OPEN trade (needs a tradeable quote; retried each tick). Closed
        window: keep retrying closes (the broker rejects while halted). Alerts from Friday 16:55 NY while trades
        remain. Returns ``{client_id: outcome}`` for the rows a close was sent (or previewed) for, so the duration
        rule (the last fallback) still handles the others. Never raises."""
        try:
            window = fc.friday_window(at, self._friday_minutes)
            if window == fc.OPEN:
                self._friday_alerted = None
                return {}
            results = self._friday_closes(window, price_provider)
            remaining = len(self._store.trades((OPEN,)))
            if remaining and fc.alert_due(at):
                self._friday_alert(at, window, remaining, results)
            return {cid: outcome for cid, outcome in results.items() if outcome in CLOSE_SENT}
        except Exception as exc:  # noqa: BLE001
            LOG.error("component=broker_demo event=ALERT_FRIDAY_FLATTEN_FAILED reason=error error_type=%s",
                      type(exc).__name__)
            return {}

    def _friday_closes(self, window, price_provider):
        results, problems = {}, {}
        for row in self._store.trades((OPEN,)):
            client_id, symbol = row["client_id"], row["symbol"]
            if row.get("action_status") == UNKNOWN:
                results[client_id] = "ACTION_UNRESOLVED"
                continue
            if window == fc.PREFLATTEN:  # market still open: act only on a valid tradeable quote
                if symbol not in problems:
                    problems[symbol] = self._quote(symbol, price_provider)[1]
                    if problems[symbol] is not None:  # once per tick and symbol; retried next tick
                        self._event("FRIDAY_PREFLATTEN_SKIPPED", None, reason=problems[symbol])
                if problems[symbol] is not None:
                    results[client_id] = "MARKET_CLOSED" if problems[symbol] == "not_tradeable" else "NO_PRICE"
                    continue
            if self.dry_run:
                self._event("DRY_RUN", client_id, action="friday_preflatten", trade_id=row["trade_id"],
                            reason=FRIDAY_PREFLATTEN)
                results[client_id] = DRY_RUN
            else:
                results[client_id] = self._close(row, FRIDAY_PREFLATTEN, f"{client_id}-fri")
        if results:
            self._event("FRIDAY_PREFLATTEN", None, window=window, results=results)
        return results

    def _friday_alert(self, at, window, remaining, results):
        """LOG.error with the stable token + store event, at most every 5 min (Friday 16:55-17:00 NY) / 30 min
        (market closed), plus the optional ``on_alert`` hook."""
        if self._friday_alerted is not None and at - self._friday_alerted < FRIDAY_ALERT_EVERY[window]:
            return
        self._friday_alerted = at
        LOG.error("component=broker_demo event=ALERT_FRIDAY_FLATTEN_FAILED window=%s open_trades=%d", window,
                  remaining)
        self._event(FRIDAY_ALERT, None, window=window, open_trades=remaining, results=sorted(set(results.values())))
        if callable(self.on_alert):
            try:
                self.on_alert(FRIDAY_ALERT, f"Friday flatten incomplete: {remaining} routed trade(s) open ({window})")
            except Exception as exc:  # noqa: BLE001 - alerting never breaks the tick
                LOG.warning("component=broker_demo event=ALERT_HOOK_FAILED error_type=%s", type(exc).__name__)

    # -- daily loss limit / drawdown halt --------------------------------------------------------------------------
    def _loss_check(self, at, planned_risk=None, balance=None):
        """Evaluate the loss limit (balance from the broker unless given) and flatten when required. Returns
        ``(LossDecision, {client_id: close outcome})``. NAV unreadable/invalid or unusable state refuses entries; an
        active (latched or persisted) halt still flattens. A failed transfer reconciliation is handled the same way:
        the unadjusted NAV is never evaluated, so it cannot create a new halt."""
        if balance is None:
            try:
                balance = self._broker.get_balance()
            except Exception as exc:  # noqa: BLE001 - fail closed for entries; halts keep flattening
                self._event("DAILY_LOSS_UNAVAILABLE", None, error=type(exc).__name__)
                return self._loss_halted(at, UNAVAILABLE)
        if not self._loss_transfers(at, balance):  # unreconciled NAV: no new halts from it, active ones still flatten
            return self._loss_halted(at, UNAVAILABLE)
        try:
            decision = self._loss_limit.evaluate(balance.equity, planned_risk, now=at)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("component=broker_demo event=DAILY_LOSS_UNAVAILABLE error_type=%s", type(exc).__name__)
            return self._loss_halted(at, UNAVAILABLE)
        if decision.reason in (NAV_INVALID, STATE_UNREADABLE):
            return self._loss_halted(at, decision.reason)
        return decision, (self._loss_flatten(decision) if decision.flatten_required else {})

    def _loss_halted(self, at, reason):
        """No usable NAV/state: refuse entries; flatten anyway when a halt is already active."""
        daily, dd_halted = self._loss_limit.halt_state(at)
        decision = LossDecision(False, dd_halted or (daily and self._loss_limit.flatten), reason, daily, dd_halted,
                                None, False)
        return decision, (self._loss_flatten(decision) if decision.flatten_required else {})

    def _loss_transfers(self, at, balance):
        """Fold TRANSFER_FUNDS since the stored transaction id into the tracker (read-only query, only when the
        summary's lastTransactionID moved; never retroactive: the first id seen is just stored). False on failure:
        the id is not advanced, so the same range is retried next tick."""
        current = balance.last_transaction_id
        try:
            stored = self._loss_limit.transaction_id()
            if current == stored:
                return True
            if current is None:  # a summary without the marker after one was seen: cannot prove no transfer
                raise BrokerMalformedResponse("summary without lastTransactionID")
            net = 0
            if stored is not None:
                net, current = self._broker.transfers_since(stored, upto=current)
            # Adjustment + cursor + audit event in one transaction: a failure leaves both, so the retry applies once.
            self._loss_limit.apply_transfer(net, current, actor="broker", reason="TRANSFER_FUNDS", now=at)
            return True
        except Exception as exc:  # noqa: BLE001 - entries refused this tick
            self._event("DAILY_LOSS_TRANSFERS_UNAVAILABLE", None, error=type(exc).__name__)
            return False

    def _loss_flatten(self, decision):
        """Close every routed OPEN trade (UNKNOWN actions skipped); retried each tick while halted. Dry-run: DRY_RUN
        events only."""
        reason = "DRAWDOWN" if decision.dd_halted else "DAILY_LOSS"
        results = {}
        for row in self._store.trades((OPEN,)):
            client_id = row["client_id"]
            if row.get("action_status") == UNKNOWN:
                results[client_id] = "ACTION_UNRESOLVED"
            elif self.dry_run:
                self._event("DRY_RUN", client_id, action="daily_loss_flatten", trade_id=row["trade_id"], reason=reason)
                results[client_id] = DRY_RUN
            else:
                results[client_id] = self._close(row, reason, f"{client_id}-dlimit")
        if results:
            self._event(f"FLATTEN_{reason}", None, results=results)
        return results

    # -- PAPER drawdown basis (DEC-DEMO-GATE-1) ---------------------------------------------------------------------
    def drawdown_basis(self):
        """Basis of the PAPER Risk V2 5% drawdown gate while routing is ON: the practice NAV (one read-only
        ``get_balance``, bounded by the client's timeouts) vs ``demo_start_equity``. ``{"source", "current",
        "starting"}`` or ``{"source", "error"}``; never raises, never places, modifies or closes anything."""
        basis = {"source": "OANDA_DEMO"}
        try:
            nav = _dec(self._broker.get_balance().equity)  # no router lock: a slow read never delays manage/closes
            starting = demo_start_equity(self._env)
            if nav is None:
                basis["error"] = "nav_invalid"
            else:
                basis.update(current=nav, starting=starting)
        except Exception as exc:  # noqa: BLE001 - unreadable NAV: the gate fails closed for new risk
            basis["error"] = f"nav_unavailable:{type(exc).__name__}"
        try:
            pct = ("%.2f" % (max(Decimal(0), (basis["starting"] - basis["current"]) / basis["starting"]) * 100)
                   if "error" not in basis else "NA")
            LOG.info("component=broker_demo event=PAPER_GATE_BASIS source=OANDA_DEMO drawdown_pct=%s status=%s",
                     pct, "error" if "error" in basis else "ok")
        except Exception:  # noqa: BLE001 - logging never changes the basis
            pass
        return basis

    # -- read-only view --------------------------------------------------------------------------------------------
    def status(self):
        try:
            rows, events = self._store.trades(), self._store.events(5)
        except Exception:  # noqa: BLE001
            rows, events = [], []
        extra = None if self._loss_limit is None else self._loss_limit.status()
        return summarize_status(rows, events, routing=True, dry_run=self.dry_run, blocked_reason=self.blocked_reason,
                                max_units=self.max_units, extra=extra)


def _first_construction(path):
    """True when no switch/emergency audit row was EVER written for broker_demo (read failures raise)."""
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0) as con:
        row = con.execute("SELECT 1 FROM broker_switches_audit WHERE account_key IN ('broker_demo', 'ALL') "
                          "LIMIT 1").fetchone()
    return row is None


def _closing_needed(path):
    """True when the state DB (read-only, never created) holds a pending ``flatten_request`` or routed rows in OPEN /
    PENDING_SEND / UNKNOWN. Missing file / tables -> False; other read failures raise (-> no router, fail closed)."""
    target = Path(path)
    if not target.is_file():
        return False
    with closing(sqlite3.connect(target.resolve().as_uri() + "?mode=ro", uri=True, timeout=1.0)) as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        flag = None if "broker_demo_flags" not in tables else con.execute(
            "SELECT value FROM broker_demo_flags WHERE key = ?", (FLATTEN_REQUEST_FLAG,)).fetchone()
        row = None if "broker_demo_trades" not in tables else con.execute(
            f"SELECT 1 FROM broker_demo_trades WHERE status IN ({', '.join('?' for _ in LIVE_STATUSES)}) LIMIT 1",
            LIVE_STATUSES).fetchone()
    return bool(flag and flag[0]) or row is not None


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
    # Emergency active (env or registry): no router, unless a flatten request / routed rows remain -> closing-only.
    closing_only = False
    if config.emergency_stop:
        if not _closing_needed(registry.path if registry is not None else config.state_db_path):
            LOG.warning("component=broker_demo event=ROUTER_DISABLED reason=emergency_stop_env")
            return None
        closing_only = True
    registry = registry if registry is not None else AccountRegistry(config.state_db_path, env=derived)
    registry.initialize()
    if not closing_only and registry.emergency_active():
        if not _closing_needed(registry.path):
            LOG.warning("component=broker_demo event=ROUTER_DISABLED reason=emergency_stop")
            return None
        closing_only = True
    if not closing_only and _first_construction(registry.path):  # never switched ON while closing
        registry.set_enabled(DEMO_MODE, True, actor="router_from_env", source="routing_flag")
    broker = OandaBroker(env=derived, mode=DEMO_MODE, registry=registry, transport=transport, clock=broker_clock,
                         sleep=sleep, close_only=closing_only)
    now = clock or (lambda: datetime.now(timezone.utc))
    store = DemoStateStore(registry.path, clock=lambda: now().isoformat())
    store.initialize()
    loss_limit = limit_from_env(env, store, clock=now)
    router = BrokerDemoRouter(broker, store, registry=registry, dry_run=dry_run_flag(env),
                              max_units=max_units_setting(env), clock=now, loss_limit=loss_limit,
                              friday_minutes=fc.minutes_from_env(env), closing_only=closing_only, env=env)
    router._heartbeat()
    if closing_only:
        LOG.warning("component=broker_demo event=ROUTER_CLOSING_ONLY reason=emergency_stop")
    LOG.info("component=broker_demo event=ROUTER_READY dry_run=%s broker=%r", router.dry_run, broker)
    if loss_limit is not None:
        LOG.info("component=broker_demo event=DAILY_LOSS_LIMIT_ON limit=%r", loss_limit)
    if router._friday_minutes is not None:
        LOG.info("component=broker_demo event=FRIDAY_PREFLATTEN_ON minutes=%d", router._friday_minutes)
    return router
