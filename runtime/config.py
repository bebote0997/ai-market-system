from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
import os

from ui.adapters import MARKETS


@dataclass(frozen=True)
class RuntimeConfig:
    db_path: Path = Path("data/runtime/trading_floor.db")
    cadence_minutes: int = 15
    symbols: tuple = MARKETS
    sessions: tuple = ("LONDON", "NEW_YORK")
    scheduler_enabled: bool = False
    starting_equity: float = 10000.0
    account_id: str = "paper-main"
    max_age_seconds: tuple = (("1h", 7200), ("15m", 1800), ("5m", 600))

    def __post_init__(self):
        if self.cadence_minutes <= 0 or 60 % self.cadence_minutes or not set(self.symbols) <= set(MARKETS):
            raise ValueError("invalid cadence or symbols")
        if not set(self.sessions) <= {"LONDON", "NEW_YORK"} or self.starting_equity <= 0:
            raise ValueError("invalid sessions or equity")

    def fingerprint(self):
        content = {"cadence_minutes": self.cadence_minutes, "symbols": self.symbols,
                   "sessions": self.sessions, "scheduler_enabled": self.scheduler_enabled,
                   "max_age_seconds": self.max_age_seconds, "paper_mode": True}
        return hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()

    @classmethod
    def from_env(cls):
        return cls(db_path=Path(os.environ.get("AI_FLOOR_DB_PATH", "data/runtime/trading_floor.db")),
                   scheduler_enabled=os.environ.get("AI_FLOOR_SCHEDULER", "0") == "1")
