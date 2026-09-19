"""User-approved internal PAPER units; these are not real broker contracts."""
from dataclasses import replace
from decimal import Decimal, ROUND_FLOOR
import math

from core.contracts import InstrumentSpec


def paper_instruments():
    """Prices remain observed USD/unit; quantities never convert to lots."""
    return {
        'XAUUSD': InstrumentSpec('XAUUSD', 'metals', 'XAU/USD', 'UTC',
                                 .01, .001, 1.0, ('1h', '15m', '5m')),
        'EURUSD': InstrumentSpec('EURUSD', 'forex', 'EUR/USD', 'UTC',
                                 .00001, 1.0, 1.0, ('1h', '15m', '5m')),
    }


def apply_paper_quantity_increment(report, instrument):
    """Reduce an approved size to whole PAPER increments before IA/persistence.

    This adapter cannot approve a rejected plan or change any entry/SL/TP price.
    The unchanged Risk Engine remains the authority for the upper size bound.
    """
    decision = report.risk_decision
    if decision is None or decision.status != 'APPROVED':
        return report
    step = instrument.quantity_increment if instrument is not None else None
    if (not isinstance(step, (int, float)) or isinstance(step, bool) or
            not math.isfinite(step) or step <= 0):
        raise ValueError('PAPER quantity increment required')
    quantity = decision.quantity
    if (not isinstance(quantity, (int, float)) or isinstance(quantity, bool) or
            not math.isfinite(quantity) or quantity <= 0):
        raise ValueError('invalid approved PAPER quantity')
    decimal_step = Decimal(str(step))
    units = (Decimal(str(quantity)) / decimal_step).to_integral_value(rounding=ROUND_FLOOR)
    rounded = float(units * decimal_step)
    if rounded <= 0:
        return replace(report, final_status='RISK_REJECTED', risk_decision=replace(
            decision, status='REJECTED', quantity=0.0, capital_at_risk=0.0,
            reason='paper_quantity_below_increment'))
    if rounded > quantity:
        raise ValueError('PAPER rounding must never increase quantity')
    if rounded == quantity:
        return report
    # Scale the approved monetary bound with the reduced quantity, preserving
    # its currency and contract multiplier without a second risk authorization.
    capital = decision.capital_at_risk * (rounded / quantity)
    return replace(report, risk_decision=replace(decision, quantity=rounded,
        capital_at_risk=capital, warnings=(*decision.warnings, 'paper_quantity_rounded_down')))
