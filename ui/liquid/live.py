"""Precio y P/L flotante de solo lectura. Como mucho un GET cada 30 s, detrás de la clave del panel."""
from datetime import datetime, timezone
import json
from threading import Lock
import time

TTL = 30.0
_LOCK = Lock()
_CACHE = {"at": None, "value": None}


def _aware(value):
    if isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def cached_call(reader, now, ttl=TTL):
    """``reader`` corre una vez por ventana. ``now`` es un reloj monotónico en segundos."""
    with _LOCK:
        seen = _CACHE["at"]
        if seen is not None and 0 <= now - seen < ttl:
            return _CACHE["value"]
        try:
            value = reader()
        except Exception:  # noqa: BLE001 - un fallo no se reintenta dentro de la ventana
            value = None
        _CACHE["at"] = now
        _CACHE["value"] = value
        return value


def reset_cache():
    """Solo para pruebas."""
    with _LOCK:
        _CACHE["at"] = None
        _CACHE["value"] = None


def stored_marks(env, now):
    """Marcas que el ciclo ya guardó, si tienen menos de 30 s. Si no, None."""
    from ui.exness_present import _flags
    raw = _flags(env).get("exness_live_marks")
    if not isinstance(raw, str) or not raw.startswith("{"):
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or not isinstance(parsed.get("positions"), list):
        return None
    stamp = _aware(parsed.get("at"))
    clock = _aware(now)
    if stamp is None or clock is None or not 0 <= (clock - stamp).total_seconds() < TTL:
        return None
    return parsed


def _can_get(env):
    from ui.exness_present import broker_is_exness
    if not broker_is_exness(env):
        return False
    try:
        raw = env.get("EXN_ACCOUNT_ID")
    except Exception:  # noqa: BLE001
        return False
    return isinstance(raw, str) and raw.strip().isdigit()


def _snapshot_marks(env):
    """Un GET del snapshot. Sin órdenes y sin otra ruta."""
    from execution.exness_client import ExnessClient
    from execution.exness_live import signer_from_env, urllib_caller
    client = ExnessClient(env, caller=urllib_caller, sign=signer_from_env(env))
    account = client.account_id
    payload = client.get_json(f"/v1/trading/accounts/{account}/snapshot")
    return _marks_from_snapshot(payload)


def _marks_from_snapshot(payload):
    if not isinstance(payload, dict):
        return None
    rows = []
    for item in payload.get("positions") or []:
        if not isinstance(item, dict):
            continue
        ticket = item.get("position_id")
        if ticket is None:
            ticket = item.get("id")
        if ticket is None:
            continue
        current = item.get("current_price", item.get("price_current", item.get("market_price")))
        profit = item.get("profit", item.get("unrealized_pnl", item.get("floating_pnl")))
        rows.append({"position_id": str(ticket), "current": current, "profit": profit})
    return {"at": None, "positions": rows}


def panel_marks(env, now=None, *, reader=None, mono=None):
    """Marcas frescas del ciclo, o un GET cacheado. Con el mercado cerrado no llama al broker."""
    from execution.trade_duration import entries_blocked
    now = now or datetime.now(timezone.utc)
    try:
        closed = entries_blocked(now) is True
    except Exception:  # noqa: BLE001
        closed = False
    stored = stored_marks(env, now)
    if stored is not None or closed:
        return stored
    if reader is None and not _can_get(env):
        return None
    call = reader if reader is not None else (lambda: _snapshot_marks(env))
    clock = time.monotonic() if mono is None else mono
    return cached_call(call, clock)
