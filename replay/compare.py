"""F04-T16: comparison report for replay decisions. Gross only (DEC-4.4); probabilities only with enough data."""
from collections import Counter
from decimal import Decimal
from statistics import median

from core.rr_contract import band

MIN_TRADES_PER_SYMBOL = 100
MIN_OBSERVATIONS_PER_BAND = 30


def _band(decision):
    if decision.band:
        return decision.band
    # Analytics only: legacy V1 float ratios (e.g. 2.9999999999997) are banded on the ratio rounded to 9
    # decimals; Phase 4 plans already carry the exact contract band.
    return None if decision.rr is None else band(Decimal(str(round(float(decision.rr), 9))))


def summarize(decisions):
    """Metrics for ONE policy's decisions."""
    plans = [d for d in decisions if d.final_status == "PLAN_READY"]
    filled = [d for d in plans if d.fill == "FILLED"]
    closed = [d for d in filled if d.outcome in ("TARGET", "STOP")]
    rrs = sorted(float(d.rr) for d in plans if d.rr is not None)
    curve, peak, drawdown, total = 0.0, 0.0, 0.0, 0.0
    for d in sorted(closed, key=lambda d: d.exit_at):
        curve += d.r_multiple
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
        total += d.r_multiple
    per_symbol = Counter(d.symbol for d in closed)
    per_band = Counter(_band(d) for d in closed)
    sufficient = (all(per_symbol.get(s, 0) >= MIN_TRADES_PER_SYMBOL for s in {d.symbol for d in decisions})
                  and bool(per_band) and all(n >= MIN_OBSERVATIONS_PER_BAND for n in per_band.values()))
    return {
        "decisions": len(decisions),
        "valid_setups": sum(d.setup_status == "VALID_SETUP" for d in decisions),
        "plans_ready": len(plans),
        "rejected_or_unavailable": dict(Counter(d.plan_status for d in decisions
                                                if d.setup_status == "VALID_SETUP" and d.final_status != "PLAN_READY")),
        "no_valid_target": sum(d.plan_status == "NO_VALID_TARGET" for d in decisions),
        "rr_below_floor": sum(d.plan_status == "RR_BELOW_FLOOR" for d in decisions),
        "out_of_policy_extended_target": sum(d.plan_status == "OUT_OF_POLICY_EXTENDED_TARGET" for d in decisions),
        "rr_distribution": {"min": rrs[0], "median": median(rrs), "max": rrs[-1]} if rrs else None,
        "rr_bands": dict(Counter(_band(d) for d in plans)),
        "target_sources": dict(Counter(d.target_source for d in plans)),
        "fills": dict(Counter(d.fill for d in plans)),
        "outcomes": dict(Counter(d.outcome for d in filled)),
        "target_reach_rate": None if not closed else sum(d.outcome == "TARGET" for d in closed) / len(closed),
        "stop_rate": None if not closed else sum(d.outcome == "STOP" for d in closed) / len(closed),
        "gross_r_total": total if closed else None,
        "gross_expectancy_r": total / len(closed) if closed else None,
        "max_drawdown_r": drawdown if closed else None,
        "session_distribution": dict(Counter("+".join(d.session) for d in plans)),
        "symbol_distribution": dict(Counter(d.symbol for d in plans)),
        "data_sufficiency": {"closed_trades_per_symbol": dict(per_symbol), "closed_per_band": dict(per_band),
                             "min_trades_per_symbol": MIN_TRADES_PER_SYMBOL,
                             "min_observations_per_band": MIN_OBSERVATIONS_PER_BAND, "sufficient": sufficient},
        "historical_probability": "UNAVAILABLE / INSUFFICIENT_EVIDENCE" if not sufficient else "ELIGIBLE_FOR_REVIEW",
        "net_pnl": "UNAVAILABLE", "effective_rr": "UNAVAILABLE", "cost_model": "NO_COST_MODEL",
    }


def compare(decisions):
    """Per-policy summaries on identical slots, plus a check that both saw the same setups."""
    policies = sorted({d.policy for d in decisions})
    by_policy = {p: [d for d in decisions if d.policy == p] for p in policies}
    setups = {p: [(d.symbol, d.slot, d.setup_status, d.setup_id) for d in by_policy[p]] for p in policies}
    identical = len({tuple(v) for v in setups.values()}) <= 1
    return {"policies": {p: summarize(by_policy[p]) for p in policies}, "identical_setups_across_policies": identical,
            "claim": "REPLAY OBSERVATION ONLY — not a historical performance or probability claim"}
