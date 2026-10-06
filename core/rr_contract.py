"""V2 Phase 4 (DEC-4.2): the single R:R mathematical authority.

Used by the Phase 4 Trade Planner, Risk, the deterministic AI trade gate and the Paper Broker fill gate.
R:R is always recomputed from levels; a declared ``risk_reward`` is only traceability and must match.

    LONG:  risk = entry - stop    reward = target - entry    (stop < entry < target)
    SHORT: risk = stop - entry    reward = entry - target    (target < entry < stop)

Arithmetic is ``Decimal`` on the shortest ``repr`` of each price, so increment-normalized prices compare
exactly (no binary-float artifacts such as 1.0939999999999996 deciding a boundary).
"""
from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal, InvalidOperation, localcontext
import math

POLICY_V1 = "V1_FIXED_3R"
POLICY_V2_D = "V2_P4_D_FIRST_OBSTACLE_2R_5R"
# policy -> (floor, ceiling); both inclusive. V1 has no ceiling.
LIMITS = {POLICY_V1: (Decimal(3), None), POLICY_V2_D: (Decimal(2), Decimal(5))}
# Relative tolerance for comparing a DECLARED ratio with the recomputed one. It only absorbs binary-float
# noise of legacy float plans; it is never applied to the policy floor/ceiling of the Phase 4 path.
DECLARED_TOLERANCE = Decimal("1e-9")

WITHIN_POLICY = "WITHIN_POLICY"
RR_BELOW_FLOOR = "RR_BELOW_FLOOR"
OUT_OF_POLICY_EXTENDED_TARGET = "OUT_OF_POLICY_EXTENDED_TARGET"


@dataclass(frozen=True)
class Geometry:
    side: str
    entry: Decimal
    stop: Decimal
    target: Decimal
    risk: Decimal
    reward: Decimal
    rr: Decimal


def to_decimal(value):
    """Finite positive price as Decimal, else None (bool, NaN, Infinity, <= 0, non-numeric)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        number = value
    elif isinstance(value, (int, float)):
        if not math.isfinite(float(value)):
            return None
        number = Decimal(repr(float(value))) if isinstance(value, float) else Decimal(value)
    else:
        return None
    if not number.is_finite() or number <= 0:
        return None
    return number


def geometry(side, entry, stop, target):
    """(Geometry, None) for valid finite positive geometry, else (None, reason)."""
    if side not in ("LONG", "SHORT"):
        return None, "invalid_side"
    values = [to_decimal(v) for v in (entry, stop, target)]
    if any(v is None for v in values):
        return None, "invalid_numeric"
    entry, stop, target = values
    if side == "LONG":
        if not stop < entry < target:
            return None, "invalid_level_order"
        risk, reward = entry - stop, target - entry
    else:
        if not target < entry < stop:
            return None, "invalid_level_order"
        risk, reward = stop - entry, entry - target
    with localcontext() as context:
        context.prec = 34
        rr = reward / risk
    return Geometry(side, entry, stop, target, risk, reward, rr), None


def classify(rr, policy):
    """WITHIN_POLICY / RR_BELOW_FLOOR / OUT_OF_POLICY_EXTENDED_TARGET on the exact ratio."""
    floor, ceiling = LIMITS[policy]
    if rr < floor:
        return RR_BELOW_FLOOR
    if ceiling is not None and rr > ceiling:
        return OUT_OF_POLICY_EXTENDED_TARGET
    return WITHIN_POLICY


def band(rr):
    """Analytics band only (never a decision): BELOW_2, 2_TO_3, 3_TO_4, 4_TO_5, EQ_5, ABOVE_5."""
    if rr < 2:
        return "BELOW_2"
    if rr < 3:
        return "2_TO_3"
    if rr < 4:
        return "3_TO_4"
    if rr < 5:
        return "4_TO_5"
    return "EQ_5" if rr == 5 else "ABOVE_5"


def declared_matches(declared, rr):
    """A declared ratio is consistent with the recomputed one (relative DECLARED_TOLERANCE)."""
    if isinstance(declared, bool) or not isinstance(declared, (int, float, Decimal)):
        return False
    value = Decimal(repr(float(declared))) if isinstance(declared, float) else Decimal(declared)
    if not value.is_finite():
        return False
    return abs(value - rr) <= DECLARED_TOLERANCE * max(Decimal(1), abs(rr))


def normalize(price, increment, rounding):
    """Price on the instrument grid; ``rounding`` is ROUND_FLOOR or ROUND_CEILING (never nearest)."""
    step = to_decimal(increment)
    value = to_decimal(price)
    if step is None or value is None:
        return None
    units = (value / step).to_integral_value(rounding=rounding)
    result = (units * step).quantize(step)  # Canonical: the instrument's decimals (e.g. 2640.00, 1.08700).
    return result if result > 0 else None


__all__ = ["DECLARED_TOLERANCE", "Geometry", "LIMITS", "OUT_OF_POLICY_EXTENDED_TARGET", "POLICY_V1",
           "POLICY_V2_D", "ROUND_CEILING", "ROUND_FLOOR", "RR_BELOW_FLOOR", "WITHIN_POLICY", "band", "classify",
           "declared_matches", "geometry", "normalize", "to_decimal"]
