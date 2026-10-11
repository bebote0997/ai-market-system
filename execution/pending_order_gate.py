"""V2 Phase 2 / B2.2: cycle-gated evaluation of PENDING PAPER orders (owner decision P1).

P1: NO CURRENT CYCLE GATE = NO PENDING-ORDER EVALUATION. Not wired into the runtime (B2.3).

- A pending order is evaluated only by the unchanged ``PaperBroker.process_next_bar`` and only
  with the bar of a real current cycle that already passed the existing V1 gates
  (``CurrentCycleGate``). P1 decides WHEN evaluation is allowed, never HOW it is evaluated.
- Committed closed 5m bars after ``order.as_of`` that are not the current gated cycle's bar
  (intermediate/recovered bars, or every bar when there is no current gate) are never evaluated.
  Each is journaled once as ``PENDING_NOT_EVALUATED`` / ``NO_CURRENT_CYCLE_GATE``; it can neither
  fill, reject nor cancel the order. No gate is invented, reconstructed, inherited or carried
  forward: a gate whose bar is older than newer committed evidence is not current.
- A gate's approval cannot travel backward in time: the only bar ever handed to
  ``process_next_bar`` is the gate's own bar, and only when it is strictly after ``order.as_of``.
- A failed or crashed cycle produces no gate (``None``) or a gate that did not pass.

Exactly-once: the evaluation applies to PAPER state freshly loaded from the trading DB and is
persisted with one ``save_paper`` call guarded by the B2.1 ``expected_state`` revalidation under
BEGIN IMMEDIATE; a concurrent winner makes this call STALE and nothing is written. The
observability rows are inserted under one BEGIN IMMEDIATE that re-reads which orders are still
PENDING and skips any (event, symbol, order_id, bar timestamp) already journaled, so retries,
restarts and duplicate evidence add no duplicate rows. No schema change.

It never runs Setup, AI, the setup reviewer, Risk, the Trade Planner or ``submit_plan``, and never
calls ``process_next_bar`` with a historical bar.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from execution.paper_broker import PaperBroker

TIMEFRAME = "5m"
EVENT = "PENDING_NOT_EVALUATED"
REASON = "NO_CURRENT_CYCLE_GATE"
# Same final statuses the V1 runtime refuses before progressing a pending order.
BLOCKING_FINAL_STATUSES = frozenset({"AI_CAUTION", "ERROR", "RISK_REJECTED"})


class PendingGateEvidenceError(RuntimeError):
    """Committed evidence could not be read or violates its contract; nothing was written."""


@dataclass(frozen=True)
class CurrentCycleGate:
    """Outcome of the EXISTING V1 gates for one real cycle, built by that cycle (B2.3).

    ``bar`` is the cycle's own latest closed 5m bar, in the dict form V1 hands to
    ``process_next_bar``. ``session_open`` already reflects the V1 diagnostic override.
    """
    run_id: str
    symbol: str
    bar: dict
    paper_enabled: bool
    execution_fresh: bool
    session_open: bool
    ai_healthy: bool
    ai_final_status: str

    def passed(self):
        # A missing/empty/non-str final status never authorizes evaluation (fail closed).
        return (self.paper_enabled is True and self.execution_fresh is True and self.session_open is True
                and self.ai_healthy is True and isinstance(self.ai_final_status, str)
                and self.ai_final_status != "" and self.ai_final_status not in BLOCKING_FINAL_STATUSES)


@dataclass(frozen=True)
class PendingGateResult:
    symbol: str
    status: str  # NO_OP (no pending order) | NOT_EVALUATED | EVALUATED | STALE
    gate: str | None  # CURRENT | NO_CURRENT_CYCLE_GATE (None for NO_OP)
    not_evaluated: tuple  # (order_id, bar_start) of every bar not evaluated for a pending order
    journaled: int  # PENDING_NOT_EVALUATED rows newly written by this call
    evaluated: tuple  # (order_id, status after process_next_bar) committed by this call


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _committed_bars(evidence, symbol, after, as_of):
    """Committed closed 5m bars strictly after ``after`` and closed by ``as_of``, oldest first."""
    try:
        bars = tuple(evidence.committed(symbol, TIMEFRAME, after=after))
    except Exception as exc:  # noqa: BLE001 - every evidence failure fails closed
        raise PendingGateEvidenceError(f"committed evidence unavailable: {type(exc).__name__}") from exc
    previous = after
    for bar in bars:
        if bar.symbol != symbol or bar.timeframe != TIMEFRAME or bar.is_closed is not True:
            raise PendingGateEvidenceError("committed evidence does not match the requested stream")
        if bar.start <= previous:
            raise PendingGateEvidenceError("committed evidence is not strictly chronological")
        previous = bar.start
    return tuple(bar for bar in bars if bar.closed_at(as_of))


def _pending(orders, symbol):
    return [o for o in orders.values() if o.symbol == symbol and o.status == "PENDING" and _aware(o.as_of)]


def _current_bar_time(gate, symbol, bars):
    """The gate bar's timestamp when ``gate`` is a passed gate of the current cycle, else None.

    Current means not older than the newest committed closed bar: an earlier cycle's approval
    is never carried forward to bars it did not see."""
    if gate is None or not gate.passed() or gate.symbol != symbol or not isinstance(gate.bar, dict):
        return None
    timestamp = gate.bar.get("timestamp")
    if gate.bar.get("symbol") != symbol or not _aware(timestamp):
        return None
    if bars and bars[-1].start > timestamp:
        return None
    return timestamp


def _journal_not_evaluated(store, account_id, symbol, bars, current, owner_key):
    """Insert each missing PENDING_NOT_EVALUATED row; returns (all keys, rows written)."""
    keys, written = [], 0
    with store.transaction():
        if owner_key is not None and not store.owns_slot(owner_key, symbol):
            raise RuntimeError("paper journal without slot ownership")
        _, orders, _ = store.load_paper(account_id)  # Durable PENDING status under the write lock.
        for order in _pending(orders, symbol):
            for bar in bars:
                if bar.start <= order.as_of or bar.start == current:
                    continue  # Before the order existed, or the current gated cycle's own bar.
                keys.append((order.order_id, bar.bar_start))
                if store.db.execute("SELECT 1 FROM journal WHERE event_type=? AND symbol=? AND source=? "
                                    "AND timestamp=?", (EVENT, symbol, order.order_id, bar.bar_start)).fetchone():
                    continue
                store._event(bar.start, order.run_id, symbol, order.order_id, EVENT, "INFO",
                             {"order_id": order.order_id, "bar_start": bar.bar_start, "reason": REASON})
                written += 1
    return tuple(keys), written


def _admit(halt_gate, kind):
    """V2 P4a R-HALT read 1 (L(W)) before an economic attempt loads its state; None gate = legacy."""
    return None if halt_gate is None else halt_gate.admit(kind)


def _pre_save(halt_gate, admission):
    """V2 P4a R-HALT read 2 immediately before ``save_paper``; raises when halted (no call, no effect)."""
    if halt_gate is not None:
        halt_gate.pre_save(admission)


def _notify(observer, event, **data):
    """V2 P3 REX observer hook (O-4): evidence only. Never changes a decision or state; failures are ignored."""
    if observer is None:
        return
    try:
        observer(event, **data)
    except Exception:  # noqa: BLE001 - REX evidence can never affect PAPER economics
        pass


def gate_pending_orders(store, evidence, *, account_id, symbol, as_of, gate=None, instrument=None,
                        owner_key=None, observer=None, halt_gate=None):
    """Apply P1 to the PENDING PAPER orders of ``symbol``.

    ``store`` is the trading ``storage.database.Store``; ``evidence`` a ``MarketEvidenceEngine``
    (read only). ``gate`` is the ``CurrentCycleGate`` of the calling cycle, or None when that
    cycle failed/crashed or there is no cycle. ``owner_key`` is passed through for slot ownership.
    ``observer`` (V2 P3 REX, O-4; None = legacy) receives the exact gate bar, the pending order iteration order and
    ``expected_state`` before ``save_paper``; it can change neither a decision nor any state; failures are ignored.
    ``halt_gate`` (V2 P4a R-HALT; None = legacy): admission before the evaluation, observability rows only while
    allowed, and a second read before ``save_paper``; a refusal propagates ``HaltRefused``.
    """
    if not _aware(as_of):
        raise ValueError("as_of: timezone-aware datetime required")
    as_of = as_of.astimezone(timezone.utc)
    account, orders, _ = store.load_paper(account_id)
    pending = [] if account is None else _pending(orders, symbol)
    if not pending:
        return PendingGateResult(symbol, "NO_OP", None, (), 0, ())
    admission = _admit(halt_gate, "pending_gate")
    bars = _committed_bars(evidence, symbol, min(o.as_of for o in pending), as_of)
    current = _current_bar_time(gate, symbol, bars)
    if halt_gate is None or halt_gate.allow("O"):
        keys, written = _journal_not_evaluated(store, account_id, symbol, bars, current, owner_key)
    else:  # after T_h the observability rows are suppressed (I-R1c); nothing economic depends on them
        keys, written = (), 0
    newest = bars[-1].bar_start if bars else None
    if current is None:
        _notify(observer, "pending_gate_not_evaluated", reason=REASON, newest_committed_bar_start=newest,
                gate_passed=None if gate is None else gate.passed(), not_evaluated=keys)
        return PendingGateResult(symbol, "NOT_EVALUATED", REASON, keys, written, ())
    account, orders, fills = store.load_paper(account_id)  # Durable state only.
    broker = PaperBroker(account, instrument)
    broker.orders, broker.fills = orders, fills
    expected_state = store.paper_state(broker.account, broker.orders, broker.fills)
    _notify(observer, "pending_gate", bar=dict(gate.bar), current=current, newest_committed_bar_start=newest,
            order_ids=[o.order_id for o in _pending(broker.orders, symbol)], expected_state=expected_state)
    evaluated = []
    for order in _pending(broker.orders, symbol):
        if current > order.as_of:  # V1 order temporal rule: never a bar at or before order.as_of.
            broker.process_next_bar(order, gate.bar)
            evaluated.append((order.order_id, order.status))
    if broker.journal:
        _pre_save(halt_gate, admission)
    if broker.journal and store.save_paper(broker, owner_key=owner_key, symbol=symbol,
                                           expected_state=expected_state) is False:
        return PendingGateResult(symbol, "STALE", "CURRENT", keys, written, ())
    return PendingGateResult(symbol, "EVALUATED", "CURRENT", keys, written, tuple(evaluated))


__all__ = ["CurrentCycleGate", "PendingGateEvidenceError", "PendingGateResult", "gate_pending_orders"]
