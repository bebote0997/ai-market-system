from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Tuple


@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    asset_class: str
    provider_symbol: Optional[str]
    timezone: str
    price_increment: Optional[float]
    quantity_increment: Optional[float]
    contract_multiplier: Optional[float]
    allowed_timeframes: Tuple[str, ...]


@dataclass(frozen=True)
class MarketBar:
    schema_version: str
    symbol: str
    timeframe: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float]
    source: str
    timezone: str = "UTC"
    is_closed: bool = True
    data_quality: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TradePlan:
    schema_version: str
    symbol: str
    side: str
    timeframe: str
    entry: float
    stop: float
    target: float
    risk_reward: float
    invalidation: str = ""
    evidence: tuple = ()
    cancel_conditions: tuple = ()
    run_id: Optional[str] = None
    as_of: Optional[datetime] = None


@dataclass(frozen=True)
class RiskDecision:
    schema_version: str
    status: str
    symbol: str
    side: str
    quantity: Optional[float]
    capital_at_risk: Optional[float]
    entry: float
    stop: float
    target: float
    reason: str
    warnings: tuple = ()


@dataclass(frozen=True)
class AgentMessage:
    schema_version: str
    run_id: str
    timestamp: datetime
    symbol: str
    timeframe: str
    agent: str
    status: str
    evidence: tuple = ()
    data_quality: dict = field(default_factory=dict)
    warnings: tuple = ()
    reasoning_summary: Optional[str] = None


@dataclass(frozen=True)
class MacroEvent:
    schema_version: str
    event_id: Optional[str]
    source: Optional[str]
    source_timestamp: Optional[datetime]
    event_timestamp: Optional[datetime]
    received_at: Optional[datetime]
    title: Optional[str]
    category: Optional[str]
    currency: Optional[str]
    impact: Optional[str]
    actual: Optional[Any] = None
    forecast: Optional[Any] = None
    previous: Optional[Any] = None
    data_quality: dict = field(default_factory=dict)
    result_timestamp: Optional[datetime] = None
    known_at: Optional[datetime] = None


@dataclass(frozen=True)
class NewsItem:
    schema_version: str
    news_id: Optional[str]
    source: Optional[str]
    published_at: Optional[datetime]
    received_at: Optional[datetime]
    headline: Optional[str]
    category: Optional[str]
    symbols: Tuple[str, ...] = ()
    relevance: Tuple[str, ...] = ()
    data_quality: dict = field(default_factory=dict)
    known_at: Optional[datetime] = None


@dataclass(frozen=True)
class MacroNewsReport:
    schema_version: str
    run_id: str
    timestamp: datetime
    symbol: str
    status: str
    macro_events: Tuple[dict, ...] = ()
    news_items: Tuple[dict, ...] = ()
    data_quality: dict = field(default_factory=dict)
    warnings: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SetupAssessment:
    schema_version: str
    run_id: str
    timestamp: datetime
    symbol: str
    status: str
    side: Optional[str]
    timeframes: Tuple[str, ...]
    evidence: tuple = ()
    invalidation: Optional[float] = None
    warnings: Tuple[str, ...] = ()
    data_quality: dict = field(default_factory=dict)


@dataclass(frozen=True)
class FloorRunReport:
    schema_version: str
    run_id: str
    as_of: datetime
    symbol: str
    structure_reports: dict
    liquidity_reports: dict
    macro_news_report: Optional[AgentMessage]
    setup_assessment: SetupAssessment
    trade_plan: Optional[TradePlan]
    risk_decision: Optional[RiskDecision]
    final_status: str
    warnings: Tuple[str, ...] = ()
