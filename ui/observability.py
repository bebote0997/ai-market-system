"""Read-only labels for the control receipt, the health page, and history cards.

Nothing here writes, probes a provider, or decides the next cycle. A missing fact stays UNKNOWN.
Modes outside deterministic / openai / xai are not printed: an unexpected value could be a mistake,
and the screen must not echo it.
"""
from datetime import datetime, timezone

MODES = {"deterministic": "determinista", "openai": "OpenAI", "xai": "xAI"}
NOT_CHECKED = {"NO_DATA", "NOT_CHECKED", "NOT CONFIGURED", "Sin datos", "Sin comprobar", "sin datos"}


def mode_label(value):
    """Public name, or None when the value is absent or not one of the three modes."""
    if not isinstance(value, str):
        return None
    return MODES.get(value.strip().lower())


def _aware(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def age_phrase(value, now):
    """How old a stored timestamp is at ``now``, or None when it cannot be read."""
    at = _aware(value)
    moment = _aware(now)
    if at is None or moment is None:
        return None
    seconds = (moment - at).total_seconds()
    if seconds < -60:
        return "la marca es posterior a esta vista"
    minutes = int(max(0, seconds) // 60)
    if minutes < 1:
        return "hace menos de 1 min"
    if minutes < 60:
        return f"hace {minutes} min"
    hours, rest = divmod(minutes, 60)
    if hours < 48:
        return f"hace {hours} h {rest} min"
    return f"hace {hours // 24} días"


def stamped(shown, value, now):
    """``shown`` plus the age when the stored value is a timestamp."""
    age = age_phrase(value, now)
    if not shown or not age:
        return shown
    return f"{shown} · {age}"


def not_checked_suffix(value):
    """``NO_DATA`` and ``NOT_CHECKED`` are the absence of a check, not a failed call."""
    if isinstance(value, str) and value.strip() in NOT_CHECKED:
        return " · no comprobado (no es un fallo)"
    return ""


def health_gap_note(*, observations, heartbeat, last_cycle, now, sessions_open):
    """What to say when the component table has no dated rows in this window.

    ``sessions_open`` False uses the scheduler calendar the caller already evaluated.
    None means that calendar was not consulted. This function does not name a next slot.
    """
    if observations:
        return None
    if heartbeat is None and last_cycle is None:
        return "Esperando el primer ciclo"
    parts = ["No hay observaciones de componentes con fecha en este periodo. "
             "Eso es «no comprobado», no un fallo de una llamada."]
    if last_cycle is not None:
        parts.append(f"Último ciclo guardado: {last_cycle}.")
    if heartbeat is not None:
        parts.append(f"Última señal de vida guardada: {heartbeat}.")
    if sessions_open is False:
        parts.append("El calendario del programador (Londres y Nueva York, días laborables de 8 a 17, "
                     "hora local de cada plaza) no tiene una sesión abierta en este momento.")
    return " ".join(parts)


def transition_status_lines(*, recorded, configured, cycle, fence):
    """Owner lines. ``recorded`` is {from, to, at} or None. ``cycle`` is {mode, at} or None.

    ``fence`` is ``on``, ``off``, or anything else, which is shown as UNKNOWN.
    The routing block chip is not an input: a clear book does not mean the fence is off.
    """
    lines = []
    recorded_to = mode_label((recorded or {}).get("to"))
    recorded_from = mode_label((recorded or {}).get("from"))
    if recorded_from and recorded_to:
        when = (recorded or {}).get("at")
        lines.append("Paso anotado: " + recorded_from + " → " + recorded_to
                     + (f" · {when}" if isinstance(when, str) and when.strip() else " · hora del paso: UNKNOWN"))
    else:
        lines.append("Paso anotado: UNKNOWN")
    configured_label = mode_label(configured)
    lines.append("Proveedor configurado en este proceso: " + (configured_label or "UNKNOWN"))
    if recorded_to and configured_label:
        if recorded_to == configured_label:
            lines.append("El proveedor configurado coincide con el destino del paso anotado.")
        else:
            lines.append("El proveedor configurado no es el destino del paso anotado.")
    cycle_label = mode_label((cycle or {}).get("mode"))
    cycle_at = (cycle or {}).get("at") if isinstance((cycle or {}).get("at"), str) else None
    if cycle_label or cycle_at:
        lines.append("Proveedor del último ciclo guardado: " + (cycle_label or "UNKNOWN")
                     + (f" · {cycle_at}" if cycle_at else "")
                     + ". Es el proveedor de ese ciclo, no el configurado ahora.")
    else:
        lines.append("Proveedor del último ciclo guardado: UNKNOWN")
    if fence == "on":
        lines.append("Valla de entradas: activa (leída del registro).")
    elif fence == "off":
        lines.append("Valla de entradas: no activa (leída del registro).")
    else:
        lines.append("Valla de entradas: UNKNOWN")
    return lines


def history_card(row):
    """One narrow history row. Provider text is tied to that cycle's time."""
    symbol = row.get("symbol") if isinstance(row.get("symbol"), str) and row.get("symbol") else "símbolo UNKNOWN"
    when = row.get("when") if isinstance(row.get("when"), str) and row.get("when").strip() else "hora UNKNOWN"
    status = row.get("status") if isinstance(row.get("status"), str) and row.get("status").strip() else "estado UNKNOWN"
    provider = mode_label(row.get("provider"))
    provider_line = ("Proveedor de ese ciclo: " + provider) if provider else "Proveedor de ese ciclo: UNKNOWN"
    return {"title": f"{symbol} · {when}",
            "lines": [f"Estado: {status}", provider_line, "Ciclo guardado. No es el proveedor configurado ahora."]}
