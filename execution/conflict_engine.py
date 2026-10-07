"""V2 Phase 6 conflict engine (pure, deterministic; DEC-6.1 .. DEC-6.7).

Classifies a NEW candidate (symbol, direction, setup_id) against the durable same-symbol PAPER exposure (open
position and PENDING orders). Approved Phase 6 policy: analysis may continue; new same-symbol economic exposure may
not. Any same-symbol exposure therefore BLOCKS; only NO_CONFLICT may continue — to the certified Risk Engine V2, which
decides independently (NO_CONFLICT is not a risk approval).

Precedence, per exposure (the blocking entity is the open position if any, else the first PENDING order by id):
  1 exposure setup_id unknown (None)                 -> UNKNOWN_SETUP_ID   / UNKNOWN_EXISTING_SETUP_ID
  2 exposure setup_id == new setup_id               -> DUPLICATE_SETUP    / DUPLICATE_SETUP
  3 same direction                                  -> SAME_DIRECTION_NEW_SETUP / SAME_DIRECTION_{POSITION|PENDING}_CONFLICT
  4 opposite direction                              -> OPPOSITE_SETUP     / OPPOSITE_{POSITION|PENDING}_CONFLICT
A candidate without its own setup_id is never submitted (INVALID_CANDIDATE / MISSING_NEW_SETUP_ID).

Not classified (DEC-6.2, not determinable from current evidence): SAME_THESIS, NEW_STRUCTURE, TREND_CHANGE. A
different setup_id never means "different thesis". Authority: classification only. It never closes, modifies,
cancels, re-stamps, resizes, nets, hedges or reverses anything and never moves SL/TP; it reads, it does not write.
"""
from dataclasses import dataclass

CONFLICT_POLICY_VERSION = "V2_P6_CONFLICT_1"

NO_CONFLICT = "NO_CONFLICT"
DUPLICATE_SETUP = "DUPLICATE_SETUP"
SAME_DIRECTION_NEW_SETUP = "SAME_DIRECTION_NEW_SETUP"
OPPOSITE_SETUP = "OPPOSITE_SETUP"
UNKNOWN_SETUP_ID = "UNKNOWN_SETUP_ID"
INVALID_CANDIDATE = "INVALID_CANDIDATE"
CLASSIFICATIONS = (NO_CONFLICT, DUPLICATE_SETUP, SAME_DIRECTION_NEW_SETUP, OPPOSITE_SETUP, UNKNOWN_SETUP_ID,
                   INVALID_CANDIDATE)

EXISTING_POSITION_CONFLICT = "EXISTING_POSITION_CONFLICT"
EXISTING_PENDING_CONFLICT = "EXISTING_PENDING_CONFLICT"

REASON_CODES = ("NO_CONFLICT", "DUPLICATE_SETUP", "UNKNOWN_EXISTING_SETUP_ID", "SAME_DIRECTION_POSITION_CONFLICT",
                "SAME_DIRECTION_PENDING_CONFLICT", "OPPOSITE_POSITION_CONFLICT", "OPPOSITE_PENDING_CONFLICT",
                "MISSING_NEW_SETUP_ID", "INVALID_DIRECTION")

BLOCK_NEW_EXPOSURE = "BLOCK_NEW_EXPOSURE"
CONTINUE_TO_RISK = "CONTINUE_TO_RISK"


@dataclass(frozen=True)
class Exposure:
    kind: str  # POSITION | PENDING_ORDER
    entity_id: str
    run_id: str
    direction: str
    setup_id: object  # str or None (UNKNOWN)
    entry: float
    stop: float
    target: float
    quantity: float

    def as_record(self):
        return {"kind": self.kind, "entity_id": self.entity_id, "run_id": self.run_id, "direction": self.direction,
                "setup_id": self.setup_id if self.setup_id is not None else "UNKNOWN", "entry": self.entry,
                "stop": self.stop, "target": self.target, "quantity": self.quantity}


@dataclass(frozen=True)
class ConflictDecision:
    policy_version: str
    status: str  # ALLOW | BLOCK
    classification: str
    conflict_kind: object  # EXISTING_POSITION_CONFLICT | EXISTING_PENDING_CONFLICT | None
    reason_code: str
    economic_action: str
    requires_risk_evaluation: bool
    symbol: str
    new_setup_id: object
    new_direction: str
    new_entry: object
    new_stop: object
    new_target: object
    as_of: object
    exposures: tuple
    blocking_entity_type: object
    blocking_entity_id: object

    def _first(self, kind):
        return next((e for e in self.exposures if e.kind == kind), None)

    def as_record(self):
        position, order = self._first("POSITION"), self._first("PENDING_ORDER")
        return {"conflict_policy_version": self.policy_version, "conflict_status": self.status,
                "classification": self.classification, "conflict_kind": self.conflict_kind,
                "reason_code": self.reason_code, "economic_action": self.economic_action,
                "requires_risk_evaluation": self.requires_risk_evaluation, "symbol": self.symbol,
                "new_setup_id": self.new_setup_id, "new_direction": self.new_direction, "new_entry": self.new_entry,
                "new_stop": self.new_stop, "new_target": self.new_target,
                "as_of": None if self.as_of is None else str(self.as_of),
                "existing_position_id": position and position.entity_id,
                "existing_position_setup_id": position and (position.setup_id or "UNKNOWN"),
                "existing_position_direction": position and position.direction,
                "existing_order_id": order and order.entity_id,
                "existing_order_setup_id": order and (order.setup_id or "UNKNOWN"),
                "existing_order_direction": order and order.direction,
                "exposures": [e.as_record() for e in self.exposures],
                "blocking_entity_type": self.blocking_entity_type, "blocking_entity_id": self.blocking_entity_id}


def exposures_for(symbol, account, orders):
    """Durable same-symbol exposure, position first then PENDING orders by id (deterministic order)."""
    found = []
    position = account.open_positions.get(symbol) if account is not None else None
    if position is not None:
        found.append(Exposure("POSITION", position.position_id, position.run_id, position.side,
                              getattr(position, "setup_id", None), position.entry_price, position.stop,
                              position.target, position.quantity))
    for order_id in sorted(orders):
        order = orders[order_id]
        if order.symbol == symbol and order.status == "PENDING":
            found.append(Exposure("PENDING_ORDER", order.order_id, order.run_id, order.side,
                                  getattr(order, "setup_id", None), order.planned_entry, order.stop, order.target,
                                  order.quantity))
    return tuple(found)


def classify(*, symbol, direction, setup_id, account, orders, entry=None, stop=None, target=None, as_of=None):
    """ConflictDecision for one candidate against durable PAPER state. Pure: reads only."""
    exposures = exposures_for(symbol, account, orders)

    def decide(classification, reason, kind=None, blocking=None):
        allow = classification == NO_CONFLICT
        return ConflictDecision(CONFLICT_POLICY_VERSION, "ALLOW" if allow else "BLOCK", classification, kind, reason,
                                CONTINUE_TO_RISK if allow else BLOCK_NEW_EXPOSURE, allow, symbol, setup_id, direction,
                                entry, stop, target, as_of, exposures,
                                None if blocking is None else blocking.kind, None if blocking is None else blocking.entity_id)

    if direction not in ("LONG", "SHORT"):
        return decide(INVALID_CANDIDATE, "INVALID_DIRECTION")
    if not isinstance(setup_id, str) or not setup_id:
        return decide(INVALID_CANDIDATE, "MISSING_NEW_SETUP_ID")
    if not exposures:
        return decide(NO_CONFLICT, "NO_CONFLICT")
    blocking = exposures[0]
    kind = EXISTING_POSITION_CONFLICT if blocking.kind == "POSITION" else EXISTING_PENDING_CONFLICT
    where = "POSITION" if blocking.kind == "POSITION" else "PENDING"
    if blocking.setup_id is None:
        return decide(UNKNOWN_SETUP_ID, "UNKNOWN_EXISTING_SETUP_ID", kind, blocking)
    if blocking.setup_id == setup_id:
        return decide(DUPLICATE_SETUP, "DUPLICATE_SETUP", kind, blocking)
    if blocking.direction == direction:
        return decide(SAME_DIRECTION_NEW_SETUP, f"SAME_DIRECTION_{where}_CONFLICT", kind, blocking)
    return decide(OPPOSITE_SETUP, f"OPPOSITE_{where}_CONFLICT", kind, blocking)


__all__ = ["BLOCK_NEW_EXPOSURE", "CLASSIFICATIONS", "CONFLICT_POLICY_VERSION", "CONTINUE_TO_RISK", "ConflictDecision",
           "DUPLICATE_SETUP", "EXISTING_PENDING_CONFLICT", "EXISTING_POSITION_CONFLICT", "Exposure", "INVALID_CANDIDATE",
           "NO_CONFLICT", "OPPOSITE_SETUP", "REASON_CODES", "SAME_DIRECTION_NEW_SETUP", "UNKNOWN_SETUP_ID", "classify",
           "exposures_for"]
