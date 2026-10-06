"""V2 Phase 5: the single, versioned PAPER risk policy (DEC-5.1 .. DEC-5.8). Pure values; no behavior.

Every threshold the Phase 5 Risk Engine and the Paper Broker share is defined here once. Values are exact
(``Fraction``) so boundary decisions never depend on binary floating point.
"""
from dataclasses import dataclass
from fractions import Fraction

from core.rr_contract import FILL_LIMITS, LIMITS, POLICY_V2_F3

RISK_POLICY_V2 = "V2_P5_RISK_1"


def fill_money_risk_factor(planned_rr, minimum_fill_rr):
    """Maximum actual-fill money risk / planned money risk permitted by the R:R contract.

    With SL and TP frozen, planned reward = P x D. At the adverse fill f where actual R:R reaches F:
    (P x D - x) / (D + x) = F  =>  x = (P - F) x D / (1 + F)  =>  actual risk = D + x = D x (1 + P) / (1 + F).
    Quantity is fixed at the fill, so money risk scales by the same factor. For P = 3, F = 2.5: 4 / 3.5 = 8/7.
    """
    planned_rr, minimum_fill_rr = Fraction(planned_rr), Fraction(minimum_fill_rr)
    return (1 + planned_rr) / (1 + minimum_fill_rr)


@dataclass(frozen=True)
class RiskPolicy:
    version: str
    per_trade_risk_fraction: Fraction  # DEC-5.1: ceiling, not a target
    notional_fraction_cap: Fraction  # DEC-5.1: 1x notional, no leverage
    aggregate_portfolio_risk_fraction: Fraction  # DEC-5.2
    account_drawdown_fraction: Fraction  # DEC-5.5: new-risk gate vs starting equity
    planned_rr: Fraction  # DEC-4.6
    minimum_actual_fill_rr: Fraction  # DEC-4.7
    max_positions_or_pending_per_symbol: int  # DEC-5.3
    correlation_adjustment: str  # DEC-5.6
    daily_loss_limit: str  # DEC-5.5: none authorized
    volatility_threshold: str  # DEC-5.8
    spread_threshold: str  # DEC-5.8

    @property
    def fill_money_risk_factor(self):
        """DEC-5.7: derived from DEC-4.6/4.7, never an independent constant."""
        return fill_money_risk_factor(self.planned_rr, self.minimum_actual_fill_rr)

    def as_record(self):
        return {"version": self.version, "per_trade_risk_fraction": str(self.per_trade_risk_fraction),
                "notional_fraction_cap": str(self.notional_fraction_cap),
                "aggregate_portfolio_risk_fraction": str(self.aggregate_portfolio_risk_fraction),
                "account_drawdown_fraction": str(self.account_drawdown_fraction),
                "planned_rr": str(self.planned_rr), "minimum_actual_fill_rr": str(self.minimum_actual_fill_rr),
                "fill_money_risk_factor": str(self.fill_money_risk_factor),
                "max_positions_or_pending_per_symbol": self.max_positions_or_pending_per_symbol,
                "correlation_adjustment": self.correlation_adjustment, "daily_loss_limit": self.daily_loss_limit,
                "volatility_threshold": self.volatility_threshold, "spread_threshold": self.spread_threshold,
                "leverage": "NONE (1x notional cap)"}


RISK_POLICY_V2_P5 = RiskPolicy(
    version=RISK_POLICY_V2,
    per_trade_risk_fraction=Fraction(1, 100),
    notional_fraction_cap=Fraction(1),
    aggregate_portfolio_risk_fraction=Fraction(23, 1000),
    account_drawdown_fraction=Fraction(5, 100),
    planned_rr=Fraction(LIMITS[POLICY_V2_F3][0]),
    minimum_actual_fill_rr=Fraction(FILL_LIMITS[POLICY_V2_F3][0]),
    max_positions_or_pending_per_symbol=1,
    correlation_adjustment="OFF (DEC-5.6: unavailable at runtime; observational only)",
    daily_loss_limit="NONE (not authorized)",
    volatility_threshold="UNAVAILABLE / INSUFFICIENT EVIDENCE (DEC-5.8)",
    spread_threshold="UNAVAILABLE / INSUFFICIENT EVIDENCE (DEC-5.8)",
)

__all__ = ["RISK_POLICY_V2", "RISK_POLICY_V2_P5", "RiskPolicy", "fill_money_risk_factor"]
