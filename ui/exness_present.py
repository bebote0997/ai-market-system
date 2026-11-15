"""Exness presentation for the dashboard. Read-only.

When ``AI_FLOOR_BROKER`` is exactly ``exness``, the screens say ``Exness Standard Cent (USC)``
and the day counter starts at the first stored Exness order after the launch cutoff.
Nothing here writes a flag, a brake, an order, or the launch reset.
"""
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3

LABEL = "Exness Standard Cent (USC)"
WAITING = "Esperando primera operación real"
HISTORICAL = "Histórico OANDA práctica"
ACTIVE_BADGE = "Ejecución real activa · Exness"
OFF_SAFETY = "sin dinero real · ejecución real desactivada"
_USC = Decimal("100")
_REAL = frozenset({"filled", "submitted", "done"})
_COMBO = ("combo_lon_", "combo_fix_")


def broker_is_exness(env=None):
    """True only for the exact broker value ``exness``."""
    try:
        raw = (os.environ if env is None else env).get("AI_FLOOR_BROKER")
    except Exception:  # noqa: BLE001 - a broken mapping is not Exness
        return False
    return isinstance(raw, str) and raw.strip().lower() == "exness"


def live_execution(env=None):
    """True only when the Exness adapter would arm, and the broker is Exness.

    Uses ``execution.exness_broker.real_execution_armed`` unchanged: both
    ``AI_FLOOR_EXNESS_REAL_EXECUTION`` and ``AI_FLOOR_EXNESS_REAL_CONFIRM`` must match.
    """
    env = os.environ if env is None else env
    if not broker_is_exness(env):
        return False
    try:
        from execution.exness_broker import real_execution_armed
        return real_execution_armed(env) is True
    except Exception:  # noqa: BLE001 - a broken read stays on the off badge; nothing is logged
        return False


def live_badge(env=None):
    """The active badge, or None when execution is not armed."""
    return ACTIVE_BADGE if live_execution(env) else None


def combo_symbols():
    """Session-combo instruments in definition order: London pair, then the fix pairs."""
    from execution.session_combo import FIX_SYMBOLS, LONDON_SYMBOL
    ordered = []
    for symbol in (LONDON_SYMBOL, *tuple(FIX_SYMBOLS)):
        if isinstance(symbol, str) and symbol and symbol not in ordered:
            ordered.append(symbol)
    return tuple(ordered)


def combo_caption():
    symbols = ", ".join(combo_symbols())
    pairs = symbols or "sin datos"
    return f"Estrategia 1 (session combo) · pares: {pairs}"


def show_experiment_market(env=None):
    """The old experiment selector stays only while the broker is not Exness."""
    return not broker_is_exness(env)


def _waiting():
    return {"waiting": True, "day": 0, "trades": 0, "first_at": None}


def _aware(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        text = value.strip()
        if text.endswith(("Z", "z")):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _flags(env):
    """Every stored flag, or ``{}`` when the file or the table cannot be read. Never creates a file."""
    try:
        from execution.launch_reset import state_db_path
        path = state_db_path(env)
    except Exception:  # noqa: BLE001
        return {}
    if not isinstance(path, Path) or not path.is_file():
        return {}
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)
    except (OSError, sqlite3.Error):
        return {}
    try:
        rows = con.execute("SELECT key, value FROM flags").fetchall()
    except sqlite3.Error:
        return {}
    finally:
        con.close()
    return {key: value for key, value in rows if isinstance(key, str) and isinstance(value, str)}


class _FlagMap:
    """The same ``get_flag`` surface ``log_brake_state`` reads. No ``set_flag``."""

    def __init__(self, flags):
        self._flags = flags

    def get_flag(self, key):
        return self._flags.get(key)


def _real_times(flags, cutoff, now):
    times = []
    for key, raw in flags.items():
        if not key.startswith(_COMBO):
            continue
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if not isinstance(parsed, dict) or parsed.get("status") not in _REAL:
            continue
        opened = _aware(parsed.get("opened_at"))
        if opened is None or opened > now:
            continue
        if cutoff is not None and opened < cutoff:
            continue
        identity = parsed.get("client_id") or parsed.get("position_id")
        if parsed.get("status") != "filled" and not identity:
            continue
        times.append(opened)
    return times


def session_clock(env=None, now=None, flags=None):
    """Day 1 is the New York date of the first real Exness order after the launch cutoff.

    Before that order the day is 0 and the trade count is 0. The stored reset counters are not read.
    """
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        return _waiting()
    now = now.astimezone(timezone.utc)
    try:
        from execution.launch_reset import launch_cutoff
        cutoff = launch_cutoff(env)
    except Exception:  # noqa: BLE001 - no cutoff, so every stored order can count
        cutoff = None
    if cutoff is not None and (not isinstance(cutoff, datetime) or cutoff.tzinfo is None):
        cutoff = None
    try:
        found = _flags(env) if flags is None else dict(flags)
        times = _real_times(found, cutoff, now)
    except Exception:  # noqa: BLE001
        return _waiting()
    if not times:
        return _waiting()
    first = min(times)
    try:
        from execution.demo_epoch import demo_day_from_epoch
        day = demo_day_from_epoch(first, now)
    except Exception:  # noqa: BLE001
        day = None
    if not isinstance(day, int) or isinstance(day, bool) or day < 1:
        return _waiting()
    return {"waiting": False, "day": day, "trades": len(times), "first_at": first}


def clock_line(clock):
    if not isinstance(clock, dict) or clock.get("waiting") is not False:
        return f"{WAITING} · Día 0 · 0 operaciones"
    day, trades = clock.get("day"), clock.get("trades")
    if not isinstance(day, int) or isinstance(day, bool) or day < 1:
        return f"{WAITING} · Día 0 · 0 operaciones"
    if not isinstance(trades, int) or isinstance(trades, bool) or trades < 1:
        trades = 0
    if day > 30:
        phrase = "Día 30 de 30 – completo"
    else:
        phrase = f"Día {day} de 30"
    noun = "operación" if trades == 1 else "operaciones"
    return f"{phrase} · {trades} {noun}"


def hero_line(env=None, now=None):
    return f"V2.1 · {LABEL} · {clock_line(session_clock(env, now=now))}"


def _money(value):
    from execution.exness_brakes import _money as parse_money
    return parse_money(value)


def _amount(text):
    if not isinstance(text, str) or text == "none":
        return "ninguno"
    amount = _money(text)
    if amount is None:
        return "ninguno"
    return f"{amount:,.2f} USC"


def _reason(text):
    if text == "daily_loss":
        return "pérdida diaria"
    if text == "drawdown":
        return "caída máxima"
    if text in (None, "", "none"):
        return "ninguno"
    if isinstance(text, str) and text.isascii() and text.replace("_", "").isalnum() and len(text) <= 32:
        return text
    return "ninguno"


def brake_view(env=None, flags=None):
    """Configured caps plus the stored brake fields ``log_brake_state`` reads. No writes."""
    env = os.environ if env is None else env
    found = _flags(env) if flags is None else dict(flags)
    from execution.exness_brakes import (
        DAILY_ENV, DAY_EQUITY_FLAG, DRAWDOWN_ENV, HALT_FLAG, REASON_FLAG, START_FLAG, USC_DAILY,
        USC_DRAWDOWN, _env_amount, _stored)
    from execution.exness_sizing import account_risk_cap
    store = _FlagMap(found)
    return {
        "cap": account_risk_cap("USC", env),
        "daily": _env_amount(env.get(DAILY_ENV), USC_DAILY),
        "drawdown": _env_amount(env.get(DRAWDOWN_ENV), USC_DRAWDOWN),
        "brake_start": _stored(store, START_FLAG),
        "day_base": _stored(store, DAY_EQUITY_FLAG),
        "halt": _stored(store, HALT_FLAG) == "1",
        "halt_reason": _stored(store, REASON_FLAG),
    }


def _blank_account():
    return {"balance": None, "equity": None, "currency": None}


_account_reader = None


def set_account_reader(reader):
    """Test and screenshot hook. ``None`` restores the live read. Does not touch the broker flags."""
    global _account_reader
    _account_reader = reader


def _live_account(env):
    """Balance and equity from the same two GETs as the startup equity read. No flag store."""
    from execution.exness_client import ExnessClient
    from execution.exness_live import signer_from_env, urllib_caller
    from execution.exness_transport import _account_currency, _dec
    client = ExnessClient(env, caller=urllib_caller, sign=signer_from_env(env))
    account_id = client.account_id
    account = client.get_json(f"/v1/configuration/accounts/{account_id}/account")
    snapshot = client.get_json(f"/v1/trading/accounts/{account_id}/snapshot")
    state = snapshot.get("account_state") if isinstance(snapshot, dict) else None
    state = state if isinstance(state, dict) else {}
    account = account if isinstance(account, dict) else {}
    return {"balance": _dec(state.get("balance")), "equity": _dec(state.get("equity")),
            "currency": _account_currency(account, state)}


def _normalize(raw):
    if raw is None:
        return _blank_account()
    if isinstance(raw, (tuple, list)) and len(raw) >= 2:
        balance = equity = raw[0]
        currency = raw[1] if len(raw) > 1 else "USC"
        if len(raw) >= 3:
            balance, equity, currency = raw[0], raw[1], raw[2]
    elif isinstance(raw, dict):
        balance, equity, currency = raw.get("balance"), raw.get("equity"), raw.get("currency")
    else:
        return _blank_account()
    code = currency.strip().upper() if isinstance(currency, str) else ""
    if code not in ("USC", "USD"):
        return _blank_account()
    return {"balance": _money(balance), "equity": _money(equity), "currency": code}


def read_account(env=None, reader=None):
    """Balance and equity. A failed read is empty. The exception text is never kept."""
    env = os.environ if env is None else env
    reader = _account_reader if reader is None else reader
    try:
        raw = reader(env) if reader is not None else _live_account(env)
    except Exception:  # noqa: BLE001 - show "sin datos"; do not log the host or the account
        return _blank_account()
    try:
        return _normalize(raw)
    except Exception:  # noqa: BLE001
        return _blank_account()


def usd_equivalent(amount, currency):
    """USD = USC / 100. A USD amount is already in dollars."""
    amount = _money(amount)
    if amount is None or currency not in ("USC", "USD"):
        return None
    if currency == "USD":
        return amount.quantize(Decimal("0.01"))
    return (amount / _USC).quantize(Decimal("0.01"))


def money_line(amount, currency):
    amount = _money(amount)
    if amount is None or currency not in ("USC", "USD"):
        return "sin datos"
    text = f"{amount:,.2f} {currency}"
    usd = usd_equivalent(amount, currency)
    if currency == "USC" and usd is not None:
        text += f" · {usd:,.2f} USD"
    return text


def _cap_line(amount):
    amount = _money(amount)
    return "sin datos" if amount is None else f"{amount:,.2f} USC"


def _rows(pairs):
    from html import escape
    return "".join(
        f'<div class="row"><span class="mute">{escape(label)}</span><span>{escape(value)}</span></div>'
        for label, value in pairs)


def panel_html(env=None, now=None, reader=None, flags=None):
    """Spanish account and brake card. No account id, host, or secret."""
    from html import escape
    env = os.environ if env is None else env
    clock = session_clock(env, now=now, flags=flags)
    account = read_account(env, reader=reader)
    brakes = brake_view(env, flags=flags)
    currency = account["currency"] or "USC"
    account_rows = [
        ("Balance", money_line(account["balance"], account["currency"])),
        ("Equity", money_line(account["equity"], account["currency"])),
    ]
    brake_rows = [
        ("Tope por operación", _cap_line(brakes["cap"])),
        ("Pérdida diaria", _cap_line(brakes["daily"])),
        ("Caída máxima", _cap_line(brakes["drawdown"])),
        ("Inicio del freno", _amount(brakes["brake_start"])),
        ("Base del día", _amount(brakes["day_base"])),
        ("Freno", "sí" if brakes["halt"] else "no"),
        ("Motivo", _reason(brakes["halt_reason"])),
    ]
    del currency
    return (
        f'<div class="card exness-desk"><h3>{escape(LABEL)}</h3>'
        f'<div class="small"><b>{escape(clock_line(clock))}</b></div>'
        f'{_rows(account_rows)}<h3>Frenos</h3>{_rows(brake_rows)}</div>'
    )


def summary_lines(env=None, now=None, reader=None):
    """Spanish daily summary for the Exness account. No OANDA wording and no secrets."""
    env = os.environ if env is None else env
    if isinstance(now, str):
        now = _aware(now)
    clock = session_clock(env, now=now)
    account = read_account(env, reader=reader)
    brakes = brake_view(env)
    return [
        LABEL,
        clock_line(clock),
        "Operaciones: " + ("0" if clock["waiting"] else str(clock["trades"])),
        "Balance: " + money_line(account["balance"], account["currency"]),
        "Equity: " + money_line(account["equity"], account["currency"]),
        "Tope por operación: " + _cap_line(brakes["cap"]),
        "Pérdida diaria: " + _cap_line(brakes["daily"]),
        "Caída máxima: " + _cap_line(brakes["drawdown"]),
        "Inicio del freno: " + _amount(brakes["brake_start"]),
        "Base del día: " + _amount(brakes["day_base"]),
        "Freno: " + ("sí" if brakes["halt"] else "no"),
        "Motivo: " + _reason(brakes["halt_reason"]),
    ]


def floor_overlay(payload, now=None, env=None, reader=None):
    """Replace the visible broker name and the day line. Drops OANDA practice rows from this surface."""
    if not isinstance(payload, dict) or not broker_is_exness(env):
        return payload
    account = read_account(env, reader=reader)
    clock = session_clock(env, now=now)
    header = dict(payload.get("header") or {})
    header["day_text"] = clock_line(clock)
    header["trades"] = 0 if clock["waiting"] else clock["trades"]
    header["closed_trades"] = header["trades"]
    if account["equity"] is not None:
        header["equity"] = float(account["equity"])
        header["balance"] = None if account["balance"] is None else float(account["balance"])
        header["equity_stale"] = False
    agents = payload.get("agents")
    if isinstance(agents, dict):
        agents = dict(agents)
        agents["Exec"] = None
    else:
        agents = payload.get("agents")
    return {**payload, "broker_label": LABEL, "header": header, "trades": [], "curve": None,
            "epoch_text": None, "agents": agents}
