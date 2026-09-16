"""Pure snapshot-to-view-model conversion. No analysis or execution occurs here."""
from dataclasses import dataclass
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from storage.codec import parse_utc
from typing import Optional

MARKETS = ("XAUUSD", "NAS100", "EURUSD")
SAFETY_LABELS = ("PAPER MODE", "REAL EXECUTION DISABLED")
AGENT_NAMES = ("Structure AI", "Liquidity AI", "Macro AI", "Setup Reviewer", "Trade Reviewer")
STATES = {"NO_DATA", "NO_SETUP", "WATCH", "VALID_SETUP", "AI_CAUTION", "RISK_REJECTED", "PLAN_READY", "LOADING", "EMPTY", "STALE_DATA", "PROVIDER_FAILURE", "PARTIAL_AI_FAILURE", "STATE_INCONSISTENCY", "CHART_ERROR", "ERROR"}


@dataclass(frozen=True)
class AgentView:
    name: str
    status: str
    bias: str
    confidence: Optional[float]
    recommendation: Optional[str]
    evidence: tuple
    conflicts: tuple
    warnings: tuple
    timestamp: Optional[datetime]
    reasoning_summary: Optional[str]
    observations: tuple


@dataclass(frozen=True)
class FloorViewModel:
    run_id: Optional[str]
    symbol: str
    as_of: Optional[datetime]
    state: str
    setup_status: str
    setup_side: Optional[str]
    setup_evidence: tuple
    setup_invalidation: Optional[float]
    plan: Optional[object]
    risk_status: str
    risk_decision: Optional[object]
    agents: tuple
    warnings: tuple
    facts: tuple
    interpretation: tuple
    freshness: str
    sample: bool = False
    bars: tuple = ()
    annotations: tuple = ()
    account: Optional[object] = None
    orders: tuple = ()
    positions: tuple = ()
    journal: tuple = ()
    macro_facts: tuple = ()
    prompt_versions: tuple = ()
    schema_version: Optional[str] = None


def _agent(name, response):
    if response is None:
        return AgentView(name, "MISSING", "UNKNOWN", None, None, (), (), (), None, None, ())
    return AgentView(name, response.status, response.bias,
                     response.confidence if response.status in {"OK", "PARTIAL"} else None,
                     response.recommendation, tuple(response.supporting_evidence),
                     tuple(response.conflicting_evidence), tuple(response.warnings),
                     response.as_of, response.reasoning_summary, tuple(response.observations))


def from_ai_report(report, *, now=None, max_age_seconds=900, sample=False, bars=(), annotations=(), account=None, orders=(), positions=(), journal=(), macro_facts=()):
    """Copy display fields from a completed AIFloorReport; never mutate it."""
    if report.symbol not in MARKETS:
        raise ValueError("unsupported floor market")
    deterministic = report.deterministic_reports
    setup = deterministic["setup_assessment"]
    risk = report.risk_decision
    agents = tuple(_agent(name, response) for name, response in zip(AGENT_NAMES,
        (report.ai_structure, report.ai_liquidity, report.ai_macro, report.ai_setup_review, report.ai_trade_review)))
    now = now or datetime.now(timezone.utc)
    age = (now - report.as_of).total_seconds() if report.as_of else None
    freshness = "NO_DATA" if age is None else "STALE_DATA" if age > max_age_seconds else "CURRENT"
    provider_failure = any(a.status == "ERROR" for a in agents)
    partial_ai = any(a.status in {"ERROR", "MISSING", "NO_DATA", "PARTIAL"} for a in agents)
    state = report.final_status if report.final_status in STATES else "STATE_INCONSISTENCY"
    warnings = tuple(report.warnings)
    if provider_failure:
        warnings += ("PROVIDER_FAILURE",)
    elif partial_ai:
        warnings += ("PARTIAL_AI_FAILURE",)
    if freshness == "STALE_DATA":
        warnings += ("CRITICAL — STALE DATA",)
    if state == "PLAN_READY" and (risk is None or risk.status != "APPROVED"):
        state = "STATE_INCONSISTENCY"
        warnings += ("RiskDecision does not authorize plan",)
    if state == "RISK_REJECTED" and (risk is None or risk.status != "REJECTED"):
        state = "STATE_INCONSISTENCY"
    facts = (f"{report.symbol}: {state}", f"Risk Engine: {risk.status if risk else 'NOT CALLED'}")
    interpretation = tuple(f"{a.name}: {a.reasoning_summary}" for a in agents if a.reasoning_summary and a.status in {"OK", "PARTIAL"})
    return FloorViewModel(report.run_id, report.symbol, report.as_of, state, setup.status,
        setup.side, tuple(setup.evidence), setup.invalidation, report.trade_plan,
        risk.status if risk else "NOT CALLED", risk, agents, warnings, facts,
        interpretation, freshness, sample, tuple(bars), tuple(annotations), account,
        tuple(orders), tuple(positions), tuple(journal), tuple(macro_facts),
        tuple(sorted(report.prompt_versions.items())), report.schema_version)


def empty_market(symbol):
    if symbol not in MARKETS:
        raise ValueError("unsupported floor market")
    return FloorViewModel(None, symbol, None, "NO_DATA", "NO_SETUP", None, (), None,
                          None, "NOT CALLED", None, tuple(_agent(n, None) for n in AGENT_NAMES),
                          (), (f"{symbol}: NO_DATA", "Risk Engine: NOT CALLED"), (), "NO_DATA")


def from_persisted_snapshot(payload, *, now=None, max_age_seconds=900):
    if payload.get("schema_version") != "1.0" or payload.get("symbol") not in MARKETS:
        raise ValueError("incompatible UI snapshot")
    vm = empty_market(payload["symbol"])
    agents = tuple(AgentView(a["name"], a["status"], a["bias"], a.get("confidence"),
        a.get("recommendation"), tuple(a.get("evidence") or ()), tuple(a.get("conflicts") or ()),
        tuple(a.get("warnings") or ()), parse_utc(a["timestamp"]) if a.get("timestamp") else None,
        a.get("reasoning_summary"), tuple(a.get("observations") or ())) for a in payload.get("agents", ()))
    as_of = parse_utc(payload["as_of"]) if payload.get("as_of") else None
    freshness = payload.get("freshness", "NO_DATA")
    warnings = tuple(payload.get("warnings") or ())
    if freshness == "CURRENT" and as_of and ((now or datetime.now(timezone.utc)) - as_of).total_seconds() > max_age_seconds:
        freshness = "STALE_DATA"
        warnings += ("CRITICAL — STALE DATA",)
    return replace(vm, run_id=payload.get("run_id"),
        as_of=as_of,
        state=payload["state"], setup_status=payload.get("setup_status", "NO_SETUP"),
        setup_side=payload.get("setup_side"), setup_evidence=tuple(payload.get("setup_evidence") or ()),
        setup_invalidation=payload.get("setup_invalidation"),
        plan=SimpleNamespace(**payload["plan"]) if payload.get("plan") else None,
        risk_status=payload.get("risk_status", "NOT CALLED"),
        risk_decision=SimpleNamespace(**payload["risk_decision"]) if payload.get("risk_decision") else None,
        agents=agents or vm.agents, warnings=warnings,
        facts=tuple(payload.get("facts") or ()), interpretation=tuple(payload.get("interpretation") or ()),
        freshness=freshness,
        prompt_versions=tuple(tuple(x) for x in payload.get("prompt_versions") or ()),
        schema_version=payload["schema_version"])


def state_label(state):
    return "PAPER PLAN READY" if state == "PLAN_READY" else "CRITICAL — STALE DATA" if state == "STALE_DATA" else state


def assistant_lines(vm):
    return {"FACTS": vm.facts + ("NO REAL MONEY",),
            "AI INTERPRETATION": vm.interpretation or ("NO_DATA",),
            "WARNINGS / UNCERTAINTY": vm.warnings or ("None reported",)}
