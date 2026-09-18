from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os

from ui.adapters import MARKETS

SUPPORTED_SYMBOLS = MARKETS
DEFAULT_ENABLED_SYMBOLS = ("XAUUSD", "EURUSD")


@dataclass(frozen=True)
class RuntimeConfig:
    db_path: Path = Path("data/runtime/trading_floor.db")
    cadence_minutes: int = 15
    enabled_symbols: tuple = DEFAULT_ENABLED_SYMBOLS
    sessions: tuple = ("LONDON", "NEW_YORK")
    scheduler_enabled: bool = False
    starting_equity: float = 10000.0
    account_id: str = "paper-main"
    max_age_seconds: tuple = (("1h", 7200), ("15m", 1800), ("5m", 600))
    market_provider_mode: str = "twelve_data"
    ai_provider_mode: str = "deterministic"
    macro_provider_mode: str = "none"

    def __post_init__(self):
        if (self.cadence_minutes <= 0 or 60 % self.cadence_minutes or
                not self.enabled_symbols or len(set(self.enabled_symbols)) != len(self.enabled_symbols) or
                not set(self.enabled_symbols) <= set(SUPPORTED_SYMBOLS)):
            raise ValueError("invalid cadence or enabled_symbols")
        if not set(self.sessions) <= {"LONDON", "NEW_YORK"} or self.starting_equity <= 0:
            raise ValueError("invalid sessions or equity")
        if self.market_provider_mode not in {"none", "massive", "twelve_data"} or self.ai_provider_mode not in {"deterministic", "openai"}:
            raise ValueError("invalid provider mode")
        if self.macro_provider_mode not in {"none", "finnhub", "official_hybrid"}:
            raise ValueError("invalid macro provider mode")

    def fingerprint(self):
        content = {"cadence_minutes": self.cadence_minutes, "enabled_symbols": self.enabled_symbols,
                   "supported_symbols": SUPPORTED_SYMBOLS,
                   "sessions": self.sessions, "scheduler_enabled": self.scheduler_enabled,
                   "max_age_seconds": self.max_age_seconds, "paper_mode": True,
                   "market_provider_mode": self.market_provider_mode,
                   "ai_provider_mode": self.ai_provider_mode,
                   "macro_provider_mode": self.macro_provider_mode}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    @classmethod
    def from_env(cls):
        from runtime.env import load_local_env
        load_local_env()
        enabled = tuple(s.strip().upper() for s in
                        os.environ.get("AI_FLOOR_ENABLED_SYMBOLS", ",".join(DEFAULT_ENABLED_SYMBOLS)).split(","))
        return cls(db_path=Path(os.environ.get("AI_FLOOR_DB_PATH", "data/runtime/trading_floor.db")),
                   enabled_symbols=enabled,
                   scheduler_enabled=os.environ.get("AI_FLOOR_SCHEDULER", "0") == "1",
                   market_provider_mode=os.environ.get("AI_FLOOR_MARKET_PROVIDER", "twelve_data"),
                   ai_provider_mode=os.environ.get("AI_FLOOR_AI_PROVIDER", "deterministic"),
                   macro_provider_mode=os.environ.get("AI_FLOOR_MACRO_PROVIDER", "none"))
