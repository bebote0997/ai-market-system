"""Position size from an Exness instrument condition. Pure Decimal.

The public reference names ``volume_min``, ``volume_max``, ``volume_step`` and ``point_digits``.
The formulas page sizes money as ``volume * price_distance * contractSize * ProfitRate``
(https://www.exness-api.com/trading/guides/formulas-calculations). This module uses that identity.
It does not invent a contract size. ``ProfitRate`` is 1 only when the payload says the quote currency
and the account currency are the same, or when the payload already carries ``profitRate``.
"""
from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal
from typing import Optional


RISK_FRACTION_CAP = Decimal("0.01")
USD_RISK_CAP = Decimal("5")


def _pos(value):
    if isinstance(value, bool) or not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        return None
    return value


@dataclass(frozen=True)
class InstrumentSpec:
    volume_min: Decimal
    volume_step: Decimal
    point_digits: int
    contract_size: Decimal
    profit_rate: Decimal
    volume_max: Optional[Decimal] = None

    def __post_init__(self):
        for name in ("volume_min", "volume_step", "contract_size", "profit_rate"):
            if _pos(getattr(self, name)) is None:
                raise ValueError(f"{name} must be a finite positive Decimal")
        if type(self.point_digits) is not int or not 0 <= self.point_digits <= 10:
            raise ValueError("point_digits must be an int in 0..10")
        if self.volume_max is not None and _pos(self.volume_max) is None:
            raise ValueError("volume_max must be a finite positive Decimal")


def floor_to_step(amount, step):
    amount = _pos(amount) if amount != 0 else Decimal(0)
    step = _pos(step)
    if amount is None or step is None:
        return Decimal(0)
    count = (amount / step).to_integral_value(rounding=ROUND_FLOOR)
    if count <= 0:
        return Decimal(0)
    return (count * step).quantize(step)


def size_lots(balance, risk_fraction, entry, stop, spec):
    """``(lots, reason)``. Lots are floored to ``volume_step`` and never raised to ``volume_min``."""
    if not isinstance(spec, InstrumentSpec):
        return Decimal(0), "symbol_spec_missing"
    balance, entry, stop = _pos(balance), _pos(entry), _pos(stop)
    fraction = _pos(risk_fraction) if not isinstance(risk_fraction, bool) else None
    if (balance is None or fraction is None or fraction > RISK_FRACTION_CAP or entry is None or stop is None
            or entry == stop):
        return Decimal(0), "sizing_invalid"
    raw = balance * fraction / (abs(entry - stop) * spec.contract_size * spec.profit_rate)
    lots = floor_to_step(raw, spec.volume_step)
    if spec.volume_max is not None and lots > spec.volume_max:
        lots = floor_to_step(spec.volume_max, spec.volume_step)
    if lots < spec.volume_min:
        return Decimal(0), "below_volume_min"
    return lots, None


def capped_lots(equity, balance, entry, stop, spec, fraction=RISK_FRACTION_CAP, usd_cap=USD_RISK_CAP):
    """``(lots, reason)``. Budget is the lesser of ``fraction`` of equity, of balance, and ``usd_cap``.

    Lots are floored to ``volume_step`` and never raised to ``volume_min``. ``entry`` and ``stop`` are the
    prices the order will actually use.
    """
    if not isinstance(spec, InstrumentSpec):
        return Decimal(0), "symbol_spec_missing"
    equity, balance = _pos(equity), _pos(balance)
    entry, stop = _pos(entry), _pos(stop)
    fraction = _pos(fraction) if not isinstance(fraction, bool) else None
    cap = _pos(usd_cap) if not isinstance(usd_cap, bool) else None
    if (equity is None or balance is None or fraction is None or fraction > RISK_FRACTION_CAP or cap is None
            or entry is None or stop is None or entry == stop):
        return Decimal(0), "sizing_invalid"
    budget = min(equity * fraction, balance * fraction, cap)
    unit = abs(entry - stop) * spec.contract_size * spec.profit_rate
    if unit <= 0:
        return Decimal(0), "sizing_invalid"
    if spec.volume_min * unit > budget:
        return Decimal(0), "min_volume_over_cap"
    lots = floor_to_step(budget / unit, spec.volume_step)
    if lots < spec.volume_min:
        return Decimal(0), "min_volume_over_cap"
    return lots, None


def on_step(volume, spec):
    if not isinstance(spec, InstrumentSpec) or _pos(volume) is None:
        return False
    count = volume / spec.volume_step
    return count == count.to_integral_value()


def spread_cost(bid, ask, lots, spec):
    """Account-currency cost of the spread on ``lots``, using the documented PnL identity. None if unusable."""
    bid, ask, lots = _pos(bid), _pos(ask), _pos(lots)
    if not isinstance(spec, InstrumentSpec) or bid is None or ask is None or lots is None or ask < bid:
        return None
    return (ask - bid) * spec.contract_size * spec.profit_rate * lots


def _dec(value):
    if isinstance(value, bool) or isinstance(value, float) or value is None:
        return None
    try:
        out = Decimal(value) if isinstance(value, int) else Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() else None


def spec_from_conditions(payload, account_currency=None):
    """Build a spec from a conditions object. Missing contract size or profit rate -> ``(None, reason)``.

    ``contractSize`` and ``profitRate`` are the identifiers on the formulas page. The reference page does not
    publish the JSON names; if the live payload uses different keys the reason is ``contract_size_unavailable``
    or ``profit_rate_unavailable`` and nothing is sized.
    """
    if not isinstance(payload, dict):
        return None, "symbol_spec_missing"
    volume_min, volume_step = _dec(payload.get("volume_min")), _dec(payload.get("volume_step"))
    digits = payload.get("point_digits")
    contract = _dec(payload.get("contractSize"))
    if (volume_min is None or volume_min <= 0 or volume_step is None or volume_step <= 0
            or isinstance(digits, bool) or type(digits) is not int or contract is None or contract <= 0):
        return None, "contract_size_unavailable" if contract is None else "symbol_spec_missing"
    rate = _dec(payload.get("profitRate"))
    quote = payload.get("quote_currency") if isinstance(payload.get("quote_currency"), str) else None
    # TODO: the formulas page names ProfitRate and the conversion rule, not the JSON key ``quote_currency``.
    if rate is None and quote and account_currency and quote.upper() == str(account_currency).upper():
        rate = Decimal(1)
    if rate is None or rate <= 0:
        return None, "profit_rate_unavailable"
    maximum = None if payload.get("volume_max") in (None, "") else _dec(payload.get("volume_max"))
    try:
        return InstrumentSpec(volume_min, volume_step, digits, contract, rate, maximum), None
    except ValueError:
        return None, "symbol_spec_missing"
