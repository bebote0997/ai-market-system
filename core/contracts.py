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
