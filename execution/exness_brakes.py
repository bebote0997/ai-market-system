"""Dollar brakes for a future real Exness account. Floor side. Defaults: $10 day, $50 from the start.

Both halt until ``clear_halt`` is called with ``RESUME_EXNESS``. Amounts are in account currency.
A non-USD currency is refused: the docs do not define a conversion from the configured USD limits.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import logging
import os
from pathlib import Path
import sqlite3
from typing import Optional, Protocol

from execution.daily_loss_limit import day_key


LOG = logging.getLogger("ai_floor.exness")
DAILY_ENV, DRAWDOWN_ENV = "AI_FLOOR_EXNESS_DAILY_LOSS_USD", "AI_FLOOR_EXNESS_MAX_DRAWDOWN_USD"
START_ENV = "AI_FLOOR_EXNESS_START_BALANCE"
DEFAULT_DAILY, DEFAULT_DRAWDOWN = Decimal("10"), Decimal("50")
RESUME_PHRASE = "RESUME_EXNESS"
HALT_FLAG, REASON_FLAG = "exness_brake_halt", "exness_brake_reason"
DAY_FLAG, DAY_EQUITY_FLAG = "exness_brake_day", "exness_brake_day_equity"
START_FLAG = "exness_brake_start"


class FlagStore(Protocol):
    def get_flag(self, key: str) -> Optional[str]: ...

    def set_flag(self, key: str, value: Optional[str]) -> None: ...


class SqliteFlags:
    """Halt flags on the durable runtime disk. A restart does not clear a brake."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.path, timeout=5) as con:
            con.execute("CREATE TABLE IF NOT EXISTS flags (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    def get_flag(self, key):
        with sqlite3.connect(self.path, timeout=5) as con:
            row = con.execute("SELECT value FROM flags WHERE key = ?", (key,)).fetchone()
        return None if row is None else row[0]

    def set_flag(self, key, value):
        with sqlite3.connect(self.path, timeout=5) as con:
            if value is None:
                con.execute("DELETE FROM flags WHERE key = ?", (key,))
            else:
                con.execute("INSERT INTO flags(key, value) VALUES(?, ?) "
                            "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, str(value)))


def flag_store_from_env(env):
    raw = (env.get("AI_FLOOR_EXNESS_STATE_DB_PATH") or "").strip()
    return SqliteFlags(raw or "data/runtime/exness_state.db")


class MemoryFlags:
    def __init__(self):
        self._flags = {}

    def get_flag(self, key):
        return self._flags.get(key)

    def set_flag(self, key, value):
        if value is None:
            self._flags.pop(key, None)
        else:
            self._flags[key] = str(value)


def _money(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        out = value if isinstance(value, Decimal) else Decimal(str(value).strip())
    except (ArithmeticError, ValueError):
        return None
    return out if out.is_finite() else None


def _env_amount(raw, default):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    parsed = _money(raw)
    return parsed if parsed is not None and parsed > 0 else None


@dataclass(frozen=True)
class BrakeStatus:
    entry_allowed: bool
    flatten: bool
    reason: Optional[str]


class ExnessBrakes:
    def __init__(self, store, *, daily_usd=DEFAULT_DAILY, drawdown_usd=DEFAULT_DRAWDOWN, start_balance=None):
        self._store = store
        self.daily_usd = daily_usd
        self.drawdown_usd = drawdown_usd
        self._configured_start = start_balance

    @classmethod
    def from_env(cls, store, env):
        daily = _env_amount(env.get(DAILY_ENV), DEFAULT_DAILY)
        drawdown = _env_amount(env.get(DRAWDOWN_ENV), DEFAULT_DRAWDOWN)
        raw = env.get(START_ENV)
        start = None if raw is None or str(raw).strip() == "" else _money(raw)
        if daily is None or drawdown is None or (raw not in (None, "") and (start is None or start <= 0)):
            return None
        return cls(store, daily_usd=daily, drawdown_usd=drawdown, start_balance=start)

    def halted(self):
        return self._store.get_flag(HALT_FLAG) == "1"

    def clear_halt(self, confirm):
        if confirm != RESUME_PHRASE:
            LOG.warning("component=exness event=RESUME_REFUSED reason=phrase_mismatch")
            return False
        self._store.set_flag(HALT_FLAG, None)
        self._store.set_flag(REASON_FLAG, None)
        LOG.info("component=exness event=BRAKE_RESUMED")
        return True

    def _halt(self, reason):
        if self._store.get_flag(REASON_FLAG) != reason:
            LOG.warning("component=exness event=BRAKE_HALT reason=%s", reason)
        self._store.set_flag(HALT_FLAG, "1")
        self._store.set_flag(REASON_FLAG, reason)

    def evaluate(self, equity, currency, now):
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            return BrakeStatus(False, self.halted(), "invalid_time")
        if self.halted():
            return BrakeStatus(False, True, self._store.get_flag(REASON_FLAG) or "brake_halt")
        equity = _money(equity)
        if equity is not None and equity <= 0:
            self._halt("drawdown")
            return BrakeStatus(False, True, "drawdown")
        if not isinstance(currency, str) or currency.strip().upper() != "USD":
            return BrakeStatus(False, False, "currency_unsupported")
        if equity is None or equity <= 0:
            return BrakeStatus(False, False, "equity_unavailable")
        try:
            key = day_key(now.astimezone(timezone.utc))
        except (ValueError, OverflowError):
            return BrakeStatus(False, False, "invalid_time")
        if self._store.get_flag(DAY_FLAG) != key or _money(self._store.get_flag(DAY_EQUITY_FLAG)) is None:
            self._store.set_flag(DAY_FLAG, key)
            self._store.set_flag(DAY_EQUITY_FLAG, format(equity, "f"))
            day_open = equity
        else:
            day_open = _money(self._store.get_flag(DAY_EQUITY_FLAG))
        start = _money(self._store.get_flag(START_FLAG))
        if start is None:
            start = self._configured_start if self._configured_start is not None else equity
            self._store.set_flag(START_FLAG, format(start, "f"))
        if day_open is None or start is None or day_open <= 0 or start <= 0:
            return BrakeStatus(False, False, "equity_unavailable")
        if day_open - equity >= self.daily_usd:
            self._halt("daily_loss")
            return BrakeStatus(False, True, "daily_loss")
        if start - equity >= self.drawdown_usd:
            self._halt("drawdown")
            return BrakeStatus(False, True, "drawdown")
        return BrakeStatus(True, False, None)
