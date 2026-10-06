"""V2 Phase 5 Risk Engine (pure, deterministic). Evaluates a fixed-3R plan against the durable PAPER portfolio.

Gate order (first failing gate decides; the reason priority is this order):
  1 account state valid        -> INVALID_ACCOUNT_STATE
  2 plan geometry (single R:R contract, exactly 3R, declared R:R must match) -> existing geometry reasons
  3 conservative equity basis  = min(current_equity, starting_equity + realized_pnl)   (``cash`` is not used)
  4 account drawdown gate      current_equity <= starting_equity x (1 - 5%) -> ACCOUNT_DRAWDOWN_LIMIT
  5 sizing: min(conservative x 1% / (D x m), conservative x 100% / (entry x m)), floored to the PAPER increment;
    TRUE planned money risk = quantity x D x m (never above the 1% ceiling)        -> PAPER_QUANTITY_BELOW_INCREMENT
  6 symbol rule: one open position or pending order per symbol                   -> SYMBOL_EXPOSURE_LIMIT
  7 aggregate: open risk + pending reserved risk + proposed reservation <= 2.30% x conservative
                                                                                 -> PORTFOLIO_RISK_LIMIT
Risk amounts (money at risk to the stop, never quantity or notional):
  open position  : max(0, (fill - SL) LONG | (SL - fill) SHORT) x quantity x multiplier  (persisted fill price; no
                   market data; equals the worst-case decline of conservative equity when net unrealized >= 0 and
                   over-states it otherwise, i.e. never under-states)
  pending order  : |planned entry - SL| x quantity x multiplier x fill factor (8/7)  (the worst fill DEC-4.7 permits)
  proposed trade : TRUE planned money risk x fill factor
Exact arithmetic (Fraction) throughout; nothing is fetched, mutated or moved.
"""
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from core.contracts import RiskDecision
from core.risk_policy import RISK_POLICY_V2_P5
from core.rr_contract import POLICY_V2_F3, WITHIN_POLICY, classify, declared_matches, geometry, to_decimal


@dataclass(frozen=True)
class RiskV2Result:
    decision: RiskDecision
    record: dict


def _f(value):
    """Finite positive number as an exact Fraction (prices, quantities, multipliers), else None."""
    number = to_decimal(value)
    return None if number is None else Fraction(number)


def _real(value):
    """Finite real number of any sign as an exact Fraction (equity, realized PnL), else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return None
    number = Decimal(repr(float(value))) if isinstance(value, float) else Decimal(value)
    return Fraction(number) if number.is_finite() else None


def _money(value):
    return str(Decimal(value.numerator) / Decimal(value.denominator))


def _float(value):
    return float(Decimal(value.numerator) / Decimal(value.denominator))


def position_risk(position):
    entry, stop, qty, mult = (_f(position.entry_price), _f(position.stop), _f(position.quantity),
                              _f(position.contract_multiplier))
    if None in (entry, stop, qty, mult):
        return None
    distance = (entry - stop) if position.side == "LONG" else (stop - entry)
    return max(Fraction(0), distance) * qty * mult


def pending_risk(order, factor):
    entry, stop, qty, mult = (_f(order.planned_entry), _f(order.stop), _f(order.quantity),
                              _f(order.contract_multiplier))
    if None in (entry, stop, qty, mult):
        return None
    return abs(entry - stop) * qty * mult * factor


def evaluate(plan, account, orders, instrument, policy=RISK_POLICY_V2_P5, *, setup_id=None, at=None):
    factor = policy.fill_money_risk_factor
    record = {"policy": policy.as_record(), "policy_version": policy.version, "run_id": getattr(plan, "run_id", None),
              "setup_id": setup_id, "symbol": getattr(plan, "symbol", None), "direction": getattr(plan, "side", None),
              "planned_entry": None, "sl": None, "tp": None, "planned_rr": str(policy.planned_rr),
              "minimum_fill_rr": str(policy.minimum_actual_fill_rr), "timestamp": None if at is None else str(at)}

    def reject(reason, **extra):
        record.update(extra, risk_status="REJECTED", risk_reason=reason)
        entry = getattr(plan, "entry", None)
        return RiskV2Result(RiskDecision("1.0", "REJECTED", getattr(plan, "symbol", ""), getattr(plan, "side", ""),
                                         None, None, entry if isinstance(entry, (int, float)) else 0.0,
                                         getattr(plan, "stop", 0.0), getattr(plan, "target", 0.0), reason), record)

    # 1 account state
    if account is None:
        return reject("INVALID_ACCOUNT_STATE")
    current, starting, realized = _real(account.equity), _f(account.starting_equity), _real(account.realized_pnl)
    if current is None or starting is None or realized is None or not isinstance(account.open_positions, dict):
        return reject("INVALID_ACCOUNT_STATE")
    # 2 plan geometry (fixed 3R, recomputed; declared R:R cannot override)
    if getattr(plan, "policy_version", None) != POLICY_V2_F3:
        return reject("rr_policy_mismatch")
    shape, why = geometry(plan.side, plan.entry, plan.stop, plan.target)
    if shape is None:
        return reject({"invalid_numeric": "invalid_numeric_value"}.get(why, why))
    if not declared_matches(plan.risk_reward, shape.rr):
        return reject("rr_declared_mismatch")
    if classify(shape.rr, POLICY_V2_F3) != WITHIN_POLICY:
        return reject("rr_below_minimum" if shape.rr < 3 else "rr_above_maximum")
    record.update(planned_entry=str(shape.entry), sl=str(shape.stop), tp=str(shape.target))
    mult = _f(getattr(instrument, "contract_multiplier", None))
    increment = _f(getattr(instrument, "quantity_increment", None))
    if getattr(instrument, "symbol", None) != plan.symbol or mult is None or increment is None:
        return reject("instrument_contract_data_unavailable")
    # 3 conservative equity
    realized_balance = starting + realized
    conservative = min(current, realized_balance)
    record.update(current_equity=_money(current), realized_balance=_money(realized_balance),
                  conservative_equity=_money(conservative))
    if conservative <= 0:
        return reject("INVALID_ACCOUNT_STATE")
    # 4 drawdown gate (new risk only)
    drawdown = max(Fraction(0), (starting - current) / starting)
    record.update(drawdown_fraction=_money(drawdown), drawdown_limit=str(policy.account_drawdown_fraction))
    if current <= starting * (1 - policy.account_drawdown_fraction):
        return reject("ACCOUNT_DRAWDOWN_LIMIT")
    # 5 sizing on the conservative basis; PAPER floor; TRUE planned money risk
    entry, stop = Fraction(shape.entry), Fraction(shape.stop)
    distance = Fraction(shape.risk)
    budget = conservative * policy.per_trade_risk_fraction
    raw_qty = min(budget / (distance * mult), conservative * policy.notional_fraction_cap / (entry * mult))
    qty = (raw_qty / increment).__floor__() * increment
    planned_money = qty * distance * mult
    record.update(risk_fraction=str(policy.per_trade_risk_fraction), risk_budget=_money(budget),
                  quantity=_money(qty), planned_monetary_risk=_money(planned_money),
                  fill_risk_factor=str(factor), reserved_fill_risk=_money(planned_money * factor),
                  maximum_fill_money_risk=_money(planned_money * factor))
    if qty <= 0:
        return reject("PAPER_QUANTITY_BELOW_INCREMENT")
    if planned_money > budget:  # Unreachable by construction (floor only reduces); fail closed regardless.
        return reject("PER_TRADE_RISK_LIMIT")
    # 6/7 portfolio
    open_risk = symbol_risk = pending_reserved = Fraction(0)
    symbol_count = 0
    for position in account.open_positions.values():
        risk = position_risk(position)
        if risk is None:
            return reject("INVALID_ACCOUNT_STATE")
        open_risk += risk
        if position.symbol == plan.symbol:
            symbol_risk += risk
            symbol_count += 1
    for order in orders.values():
        if order.status != "PENDING":
            continue
        risk = pending_risk(order, factor)
        if risk is None:
            return reject("INVALID_ACCOUNT_STATE")
        pending_reserved += risk
        if order.symbol == plan.symbol:
            symbol_risk += risk
            symbol_count += 1
    proposed = planned_money * factor
    limit = conservative * policy.aggregate_portfolio_risk_fraction
    post_trade = open_risk + pending_reserved + proposed
    record.update(existing_open_risk=_money(open_risk), existing_pending_risk=_money(pending_reserved),
                  existing_symbol_risk=_money(symbol_risk), post_trade_portfolio_risk=_money(post_trade),
                  portfolio_risk_limit=_money(limit), correlation_state=policy.correlation_adjustment)
    if symbol_count >= policy.max_positions_or_pending_per_symbol:
        return reject("SYMBOL_EXPOSURE_LIMIT")
    if post_trade > limit:
        return reject("PORTFOLIO_RISK_LIMIT")
    record.update(risk_status="APPROVED", risk_reason="approved")
    decision = RiskDecision("1.0", "APPROVED", plan.symbol, plan.side, _float(qty), _float(planned_money),
                            plan.entry, plan.stop, plan.target, "approved", equity_at_decision=_float(conservative),
                            risk_fraction=float(policy.per_trade_risk_fraction), contract_multiplier=float(mult))
    return RiskV2Result(decision, record)


__all__ = ["RiskV2Result", "evaluate", "pending_risk", "position_risk"]
