"""Brakes for an Exness account. Floor side. Amounts are in account currency.

USD defaults: 10 day, 50 from the start. USC defaults: 200 day, 1000 from the start
(100 USC = 1 USD; the same 2% and 10% of a 10,000 USC balance as 10 and 50 USD on 500 USD).
Both halt until ``clear_halt`` is called with ``RESUME_EXNESS``. Any other currency is refused.
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
BRAKE_LOG = logging.getLogger("ai_floor.exness_brakes")
DAILY_ENV, DRAWDOWN_ENV = "AI_FLOOR_EXNESS_DAILY_LOSS_USD", "AI_FLOOR_EXNESS_MAX_DRAWDOWN_USD"
START_ENV = "AI_FLOOR_EXNESS_START_BALANCE"
DEFAULT_DAILY, DEFAULT_DRAWDOWN = Decimal("10"), Decimal("50")
USC_DAILY, USC_DRAWDOWN = Decimal("200"), Decimal("1000")
ACCOUNT_CURRENCIES = ("USD", "USC")
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
        daily = _env_amount(env.get(DAILY_ENV), None)
        drawdown = _env_amount(env.get(DRAWDOWN_ENV), None)
        raw = env.get(START_ENV)
        start = None if raw is None or str(raw).strip() == "" else _money(raw)
        explicit_daily = env.get(DAILY_ENV) not in (None, "")
        explicit_drawdown = env.get(DRAWDOWN_ENV) not in (None, "")
        if (explicit_daily and daily is None) or (explicit_drawdown and drawdown is None):
            return None
        if raw not in (None, "") and (start is None or start <= 0):
            return None
        return cls(store, daily_usd=daily, drawdown_usd=drawdown, start_balance=start)

    def _limits(self, currency):
        code = currency.strip().upper() if isinstance(currency, str) else ""
        if code not in ACCOUNT_CURRENCIES:
            return None
        daily = self.daily_usd if self.daily_usd is not None else (USC_DAILY if code == "USC" else DEFAULT_DAILY)
        drawdown = self.drawdown_usd if self.drawdown_usd is not None else (
            USC_DRAWDOWN if code == "USC" else DEFAULT_DRAWDOWN)
        return daily, drawdown

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
        limits = self._limits(currency)
        if limits is None:
            return BrakeStatus(False, False, "currency_unsupported")
        if equity is not None and equity <= 0:
            self._halt("drawdown")
            return BrakeStatus(False, True, "drawdown")
        if equity is None or equity <= 0:
            return BrakeStatus(False, False, "equity_unavailable")
        daily_limit, drawdown_limit = limits
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
        if day_open - equity >= daily_limit:
            self._halt("daily_loss")
            return BrakeStatus(False, True, "daily_loss")
        if start - equity >= drawdown_limit:
            self._halt("drawdown")
            return BrakeStatus(False, True, "drawdown")
        return BrakeStatus(True, False, None)


def _stored(store, key):
    try:
        value = store.get_flag(key)
    except Exception:  # noqa: BLE001 - the startup log must not raise
        return "none"
    if not isinstance(value, str):
        return "none"
    text = value.strip()
    if not text or any(ch.isspace() for ch in text):
        return "none"
    return text


def _equity_fields(reader):
    try:
        got = reader() if reader is not None else None
    except Exception:  # noqa: BLE001 - a failed equity read still logs the stored brakes
        return "none", "none"
    if not isinstance(got, tuple) or len(got) != 2:
        return "none", "none"
    amount = _money(got[0])
    code = got[1].strip().upper() if isinstance(got[1], str) else ""
    equity = "none" if amount is None else format(amount, "f")
    currency = code if code in ACCOUNT_CURRENCIES else "none"
    return equity, currency


def log_brake_state(store, equity_reader):
    """One INFO line of the stored brakes plus one equity read. No writes. Never raises."""
    try:
        equity, currency = _equity_fields(equity_reader)
        BRAKE_LOG.info(
            "component=exness_brakes event=BRAKE_STATE equity=%s currency=%s brake_start=%s "
            "day_base=%s day=%s halt=%s halt_reason=%s",
            equity, currency, _stored(store, START_FLAG), _stored(store, DAY_EQUITY_FLAG),
            _stored(store, DAY_FLAG), "true" if _stored(store, HALT_FLAG) == "1" else "false",
            _stored(store, REASON_FLAG))
    except Exception:  # noqa: BLE001 - startup continues when the log itself fails
        return
