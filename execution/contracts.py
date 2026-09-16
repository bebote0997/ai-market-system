from dataclasses import dataclass, field
import math
from typing import Optional


def _valid_multiplier(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) and float(value) > 0


def _valid_side(value):
    return value in {"LONG", "SHORT"}


@dataclass
class PaperAccount:
    schema_version: str
    account_id: str
    starting_equity: float
    cash: float
    equity: float
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    open_positions: dict = field(default_factory=dict)
    closed_trades: list = field(default_factory=list)

    def __post_init__(self):
        values = (self.starting_equity, self.cash, self.equity, self.realized_pnl, self.unrealized_pnl)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) for value in values):
            raise ValueError("PaperAccount numeric fields must be finite non-bool numbers")
        if self.starting_equity <= 0:
            raise ValueError("starting_equity must be positive")


@dataclass
class PaperOrder:
    schema_version: str
    order_id: str
    run_id: str
    symbol: str
    side: str
    quantity: float
    planned_entry: float
    stop: float
    target: float
    contract_multiplier: Optional[float] = None
    equity_at_submission: Optional[float] = None
    cost_rate: float = 0.0
    as_of: Optional[object] = None
    status: str = "PENDING"

    def __post_init__(self):
        if not _valid_side(self.side):
            raise ValueError("PaperOrder side must be LONG or SHORT")


@dataclass
class PaperFill:
    schema_version: str
    fill_id: str
    order_id: str
    run_id: str
    symbol: str
    side: str
    quantity: float
    planned_entry: float
    fill_price: float
    fill_timestamp: object

    @property
    def slippage(self):
        return self.fill_price - self.planned_entry


@dataclass
class PaperPosition:
    schema_version: str
    position_id: str
    origin_order_id: str
    run_id: str
    symbol: str
    side: str
    quantity: float
    planned_entry: float
    entry_price: float
    stop: float
    target: float
    opened_at: object
    status: str = "OPEN"
    last_price: Optional[float] = None
    contract_multiplier: Optional[float] = None
    cost_rate: float = 0.0
    last_processed_at: Optional[object] = None

    def __post_init__(self):
        if not _valid_side(self.side):
            raise ValueError("PaperPosition side must be LONG or SHORT")


@dataclass
class ClosedTrade:
    schema_version: str
    trade_id: str
    position_id: str
    run_id: str
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    cost: float
    net_pnl: float
    exited_at: object
    reason: str


@dataclass(frozen=True)
class JournalEvent:
    timestamp: object
    run_id: str
    symbol: str
    entity_id: str
    event_type: str
    details: dict = field(default_factory=dict)
