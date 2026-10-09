"""V2 Phase 2 / B2.1: exactly-once, chronological catch-up of EXISTING open PAPER positions.

Every committed closed 5m bar after an open position's durable trading-side watermark
(``last_processed_at``, else ``opened_at``) reaches the unchanged ``TradeManager.process_bar``
exactly once, oldest first. Not wired into the runtime.

Exactly-once invariant, per (position, bar):
- Each bar is applied to PAPER state freshly loaded from the trading DB and persisted with one
  ``Store.save_paper`` call: one trading-DB transaction holding the position (including
  ``last_processed_at``), any closed trade, account realized/unrealized PnL and equity, and the
  journal. Either nothing from the bar is committed (the bar stays eligible) or all of it is.
- The next bar is chosen from the durable watermark only, never from process memory.
- Before computation we capture an immutable PAPER state comparison value. save_paper
  revalidates it under its BEGIN IMMEDIATE, before any write. A concurrent winner
  makes the loser stale; the loser discards its computed effects and returns STALE.
  The comparison includes the whole account and loaded PAPER objects so another
  symbol's progress cannot be overwritten. SQLite provides cross-process exclusion.

Authorities: the Evidence Store says which bars exist (read-only here, never written); the trading
DB says which effects were applied. There is no cross-database transaction. If evidence cannot be
read, nothing is applied (fail closed) and there is no fallback to newest-bar-only processing.

This is management of positions that already exist. It never runs Setup, AI, Risk, the Trade
Planner, ``submit_plan`` or ``process_next_bar``, and never creates an order or a trade except the
close that the existing TradeManager semantics produce.
"""
from dataclasses import dataclass
from datetime import datetime, timezone

from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from storage.codec import utc

TIMEFRAME = "5m"


class CatchUpEvidenceError(RuntimeError):
    """Committed evidence could not be read or violates its contract; nothing was applied."""


@dataclass(frozen=True)
class CatchUpResult:
    symbol: str
    status: str  # NO_OP (no open position) | UP_TO_DATE | APPLIED | CLOSED | STALE
    applied: tuple  # bar_start of every bar applied (and committed) by this call, oldest first
    closed_by: str | None  # bar_start of the bar that closed the position, if any
    watermark: str | None  # durable position watermark after this call (None once closed)


def _watermark(position):
    return position.last_processed_at if position.last_processed_at is not None else position.opened_at


def _eligible_bars(evidence, symbol, after, as_of):
    """Committed closed 5m bars strictly after ``after`` and closed by ``as_of``, oldest first.
    Any read failure or contract violation raises before a single bar is applied."""
    try:
        bars = tuple(evidence.committed(symbol, TIMEFRAME, after=after))
    except Exception as exc:  # noqa: BLE001 - every evidence failure fails closed
        raise CatchUpEvidenceError(f"committed evidence unavailable: {type(exc).__name__}") from exc
    previous = after
    for bar in bars:
        if bar.symbol != symbol or bar.timeframe != TIMEFRAME or bar.is_closed is not True:
            raise CatchUpEvidenceError("committed evidence does not match the requested stream")
        if bar.start <= previous:
            raise CatchUpEvidenceError("committed evidence is not strictly chronological")
        previous = bar.start
    return tuple(bar for bar in bars if bar.closed_at(as_of))


def _bar(bar):
    """The bar dict TradeManager.process_bar already receives from the V1 runtime."""
    return {"symbol": bar.symbol, "timestamp": bar.start, "open": bar.open, "high": bar.high,
            "low": bar.low, "close": bar.close, "is_closed": True}


def _load(store, account_id, instrument):
    account, orders, fills = store.load_paper(account_id)
    if account is None:
        return None
    broker = PaperBroker(account, instrument)
    broker.orders, broker.fills = orders, fills
    return broker


def _notify(observer, event, **data):
    """V2 P3 REX observer hook (O-4): evidence only. Never changes a decision or state; failures are ignored."""
    if observer is None:
        return
    try:
        observer(event, **data)
    except Exception:  # noqa: BLE001 - REX evidence can never affect PAPER economics
        pass


def catch_up_position(store, evidence, *, account_id, symbol, as_of, instrument=None, owner_key=None,
                      observer=None):
    """Apply every eligible committed closed 5m bar to the open position of ``symbol``.

    ``store`` is the trading ``storage.database.Store``; ``evidence`` is a ``MarketEvidenceEngine``.
    ``owner_key`` is passed to ``save_paper`` unchanged (slot ownership when called from a cycle).
    ``observer`` (V2 P3 REX, O-4; None = legacy) receives the exact bar and ``expected_state`` before each
    ``save_paper``; it can change neither a decision nor any state, and its failures are ignored.
    """
    if not isinstance(as_of, datetime) or as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of: timezone-aware datetime required")
    as_of = as_of.astimezone(timezone.utc)
    broker = _load(store, account_id, instrument)
    position = None if broker is None else broker.account.open_positions.get(symbol)
    if position is None:
        return CatchUpResult(symbol, "NO_OP", (), None, None)  # Bootstrap: nothing to replay.
    bars = _eligible_bars(evidence, symbol, _watermark(position), as_of)
    applied = []
    stale = False
    for bar in bars:
        broker = _load(store, account_id, instrument)  # Durable state only, for every bar.
        position = broker.account.open_positions.get(symbol)
        if position is None or bar.start <= _watermark(position):
            break  # Closed, or progressed by another writer: never apply a bar twice.
        expected_state = store.paper_state(broker.account, broker.orders, broker.fills)
        _notify(observer, "catch_up_bar", bar=_bar(bar), bar_start=bar.bar_start, digest=bar.digest,
                open_positions=list(broker.account.open_positions), expected_state=expected_state)
        closed = TradeManager(broker.account, broker).process_bar(_bar(bar))
        if store.save_paper(broker, owner_key=owner_key, symbol=symbol, expected_state=expected_state) is False:
            stale = True
            break  # Discard this computed transition; a later call reloads/retries from durable state.
        applied.append(bar.bar_start)
        if closed:
            return CatchUpResult(symbol, "CLOSED", tuple(applied), bar.bar_start, None)
    final = _load(store, account_id, instrument).account.open_positions.get(symbol)
    watermark = None if final is None else utc(_watermark(final))
    return CatchUpResult(symbol, "STALE" if stale else "APPLIED" if applied else "UP_TO_DATE",
                         tuple(applied), None, watermark)


__all__ = ["CatchUpEvidenceError", "CatchUpResult", "catch_up_position"]
