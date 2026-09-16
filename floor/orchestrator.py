from datetime import datetime, timezone
import uuid

from agents.liquidity_agent import analizar_liquidez
from agents.macro_news_agent import analizar_macro_news
from agents.setup_validator import evaluar_setup
from agents.structure_agent import analizar_estructura
from agents.trade_planner import crear_trade_plan
from core.contracts import FloorRunReport, RiskDecision
from riesgo import evaluar_trade_plan


def run(snapshot, as_of, symbol, provider, instrumento, configuracion_riesgo, equity=None):
    run_id = str(uuid.uuid4())
    if not isinstance(equity, (int, float)) or isinstance(equity, bool) or equity <= 0:
        from datetime import datetime, timezone
        from core.contracts import SetupAssessment
        setup = SetupAssessment("1.0", run_id, as_of, symbol, "NO_SETUP", None, ("1h", "15m", "5m"), warnings=("equity_invalid",))
        return FloorRunReport("1.0", run_id, as_of, symbol, {}, {}, None, setup, None, None, "NO_SETUP", setup.warnings)
    structure = {timeframe: analizar_estructura(snapshot.get(timeframe), symbol, timeframe, run_id, as_of) for timeframe in ("1h", "15m", "5m") if timeframe in snapshot}
    liquidity = {timeframe: analizar_liquidez(snapshot.get(timeframe), symbol, timeframe, run_id, as_of) for timeframe in ("1h", "15m", "5m") if timeframe in snapshot}
    macro = analizar_macro_news(provider, symbol, as_of, run_id)
    setup = evaluar_setup(structure, liquidity, macro, symbol, run_id, as_of)
    if setup.status in {"NO_SETUP", "WATCH"}:
        return FloorRunReport("1.0", run_id, as_of, symbol, structure, liquidity, macro, setup, None, None, setup.status, setup.warnings)
    plan = crear_trade_plan(setup, {"data": snapshot["5m"]}, symbol, run_id, as_of)
    if plan is None:
        return FloorRunReport("1.0", run_id, as_of, symbol, structure, liquidity, macro, setup, None, None, "PLAN_UNAVAILABLE", ("plan_unavailable",))
    decision = evaluar_trade_plan(plan, equity, instrumento, configuracion_riesgo)
    final_status = "PLAN_READY" if decision.status == "APPROVED" else "RISK_REJECTED"
    return FloorRunReport("1.0", run_id, as_of, symbol, structure, liquidity, macro, setup, plan, decision, final_status, setup.warnings)
