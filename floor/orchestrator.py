import uuid

from agents.liquidity_agent import analizar_liquidez
from agents.macro_news_agent import analizar_macro_news
from agents.setup_validator import evaluar_setup
from agents.structure_agent import analizar_estructura
from agents.trade_planner import crear_trade_plan
from core.contracts import FloorRunReport
from riesgo import evaluar_trade_plan
from core.rr_contract import POLICY_V1, POLICY_V2_D, POLICY_V2_F3


def run(snapshot, as_of, symbol, provider, instrumento, configuracion_riesgo, equity=None, run_id=None,
        planner_policy=POLICY_V1):
    """``planner_policy`` POLICY_V1 (default, frozen V1), POLICY_V2_F3 (DEC-4.6 fixed 3R) or POLICY_V2_D (research);
    Phase 4 policies are tests/replay only (no runtime path)."""
    if planner_policy not in (POLICY_V1, POLICY_V2_D, POLICY_V2_F3):
        raise ValueError("unknown planner policy")
    run_id = run_id or str(uuid.uuid4())
    if not isinstance(equity, (int, float)) or isinstance(equity, bool) or equity <= 0:
        from core.contracts import SetupAssessment
        setup = SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, ("1h", "15m", "5m"), warnings=("equity_invalid",))
        return FloorRunReport("1.0", run_id, as_of, symbol, {}, {}, None, setup, None, None, "NO_SETUP", setup.warnings)
    structure = {timeframe: analizar_estructura(snapshot.get(timeframe), symbol, timeframe, run_id, as_of) for timeframe in ("1h", "15m", "5m") if timeframe in snapshot}
    liquidity = {timeframe: analizar_liquidez(snapshot.get(timeframe), symbol, timeframe, run_id, as_of) for timeframe in ("1h", "15m", "5m") if timeframe in snapshot}
    macro = analizar_macro_news(provider, symbol, as_of, run_id)
    setup = evaluar_setup(structure, liquidity, macro, symbol, run_id, as_of)
    if setup.status in {"NO_SETUP", "WATCH"}:
        return FloorRunReport("1.0", run_id, as_of, symbol, structure, liquidity, macro, setup, None, None, setup.status, setup.warnings)
    target_decision = {}
    if planner_policy == POLICY_V2_D:
        from agents.target_planner import plan_policy_d
        plan, target_decision = plan_policy_d(setup, {"data": snapshot["5m"]}, symbol, run_id, as_of, instrumento)
    elif planner_policy == POLICY_V2_F3:
        from agents.target_planner import plan_fixed_3r
        plan, target_decision = plan_fixed_3r(setup, {"data": snapshot["5m"]}, symbol, run_id, as_of, instrumento)
    else:
        plan = crear_trade_plan(setup, {"data": snapshot["5m"]}, symbol, run_id, as_of)
    if plan is None:
        warnings = ("plan_unavailable",) + ((target_decision["status"].lower(),) if target_decision else ())
        return FloorRunReport("1.0", run_id, as_of, symbol, structure, liquidity, macro, setup, None, None, "PLAN_UNAVAILABLE", warnings, target_decision=target_decision)
    decision = evaluar_trade_plan(plan, equity, instrumento, configuracion_riesgo)
    final_status = "PLAN_READY" if decision.status == "APPROVED" else "RISK_REJECTED"
    return FloorRunReport("1.0", run_id, as_of, symbol, structure, liquidity, macro, setup, plan, decision, final_status, setup.warnings, target_decision=target_decision)
