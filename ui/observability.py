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
    """``NO_DATA`` and ``NOT_CHECKED`` mean no stored check. They do not say a call succeeded."""
    if isinstance(value, str) and value.strip() in NOT_CHECKED:
        return " · sin comprobación guardada"
    return ""


def parse_fence_flag(value):
    """The broker flag only. ``1`` is on and ``0`` is off. Anything else, including absence, is UNKNOWN."""
    if value == "1":
        return "on"
    if value == "0":
        return "off"
    return "UNKNOWN"


def read_env(env):
    """The mapping the caller supplied, or the process environment when the caller supplied none."""
    if env is not None:
        return env
    import os
    return os.environ


def health_gap_note(*, observations, heartbeat, last_cycle, now, sessions_open, errors=()):
    """What to say when the component table has no dated rows in this window.

    Missing rows are missing evidence. They do not prove that nothing failed. A closed session
    does not erase an error that is already stored. This function does not name a next slot.
    """
    if observations:
        return None
    stored_errors = [item for item in errors if isinstance(item, str) and item.strip()]
    if heartbeat is None and last_cycle is None and not stored_errors:
        return "Esperando el primer ciclo"
    parts = ["No hay observaciones de componentes con fecha en este periodo. "
             "Esa ausencia no demuestra que no haya un fallo."]
    if stored_errors:
        parts.append("Hay error guardado: " + "; ".join(stored_errors) + ".")
    if last_cycle is not None:
        parts.append(f"Último ciclo guardado: {last_cycle}.")
    if heartbeat is not None:
        parts.append(f"Última señal de vida guardada: {heartbeat}.")
    if sessions_open is False:
        parts.append("El calendario del programador no tiene una sesión abierta ahora. "
                     "Eso no anula un error guardado.")
    return " ".join(parts)


def transition_status_lines(*, recorded, configured, cycle, fence, receipt, expectation=None):
    """Owner lines. ``recorded`` is the last immutable step. ``receipt`` is the current request.

    ``fence`` is already parsed from the broker flag: ``on``, ``off``, or ``UNKNOWN``.
    ``expectation`` True/False is a calculation from the step table and is labeled as not the flag.
    ``receipt`` None means the receipt could not be read. An empty dict means the read succeeded
    and there is no receipt row.
    """
    lines = []
    if receipt is None:
        lines.append("Solicitud en curso: UNKNOWN")
    elif not receipt.get("present"):
        lines.append("Solicitud en curso: ninguna")
    else:
        state = receipt.get("state")
        label = {"pending": "pendiente", "recorded": "anotada", "refused": "rechazada"}.get(state)
        when = receipt.get("at") if isinstance(receipt.get("at"), str) and receipt.get("at").strip() else None
        reason = receipt.get("reason") if isinstance(receipt.get("reason"), str) and receipt.get("reason").strip() else None
        if label is None:
            lines.append("Solicitud en curso: UNKNOWN")
        else:
            extra = ""
            if reason:
                extra += f" · {reason}"
            if when:
                extra += f" · {when}"
            lines.append(f"Solicitud en curso: {label}{extra}")
            if state == "refused":
                lines.append("Ese recibo no añadió un paso.")
    recorded_to = mode_label((recorded or {}).get("to"))
    recorded_from = mode_label((recorded or {}).get("from"))
    if recorded is None:
        lines.append("Último paso inmutable: UNKNOWN")
    elif not recorded:
        lines.append("Último paso inmutable: ninguno")
    elif recorded_from and recorded_to:
        when = (recorded or {}).get("at")
        lines.append("Último paso inmutable: " + recorded_from + " → " + recorded_to
                     + (f" · {when}" if isinstance(when, str) and when.strip() else " · hora del paso: UNKNOWN"))
    else:
        lines.append("Último paso inmutable: UNKNOWN")
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
        lines.append("Bandera de valla: activa.")
    elif fence == "off":
        lines.append("Bandera de valla: no activa.")
    else:
        lines.append("Bandera de valla: UNKNOWN")
    if expectation is True:
        lines.append("Cálculo por los pasos anotados (no es la bandera): haría falta una valla.")
    elif expectation is False:
        lines.append("Cálculo por los pasos anotados (no es la bandera): los pasos no piden valla.")
    return lines


def history_record_card(row, label, when):
    """One row of any history table. ``label`` is the section name. Nothing is dropped for being past the first runs."""
    kind = label if isinstance(label, str) and label.strip() else "Registro"
    symbol = row.get("symbol") if isinstance(row.get("symbol"), str) and row.get("symbol").strip() else ""
    moment = when if isinstance(when, str) and when.strip() else "hora UNKNOWN"
    title = f"{kind} · {symbol} · {moment}" if symbol else f"{kind} · {moment}"
    lines = []
    status = row.get("status") if isinstance(row.get("status"), str) and row.get("status").strip() else None
    if status:
        lines.append(f"Estado: {status}")
    event = row.get("event_type") if isinstance(row.get("event_type"), str) and row.get("event_type").strip() else None
    if event:
        lines.append(f"Evento: {event}")
    provider = mode_label(row.get("provider"))
    lines.append("Proveedor de ese ciclo: " + provider if provider else "Proveedor de ese ciclo: UNKNOWN")
    lines.append("Fila de esta página.")
    return {"title": title, "lines": lines}


def history_card(row):
    """One narrow history row. Provider text is tied to that cycle's time."""
    symbol = row.get("symbol") if isinstance(row.get("symbol"), str) and row.get("symbol") else "símbolo UNKNOWN"
    when = row.get("when") if isinstance(row.get("when"), str) and row.get("when").strip() else "hora UNKNOWN"
    status = row.get("status") if isinstance(row.get("status"), str) and row.get("status").strip() else "estado UNKNOWN"
    provider = mode_label(row.get("provider"))
    provider_line = ("Proveedor de ese ciclo: " + provider) if provider else "Proveedor de ese ciclo: UNKNOWN"
    return {"title": f"{symbol} · {when}",
            "lines": [f"Estado: {status}", provider_line, "Ciclo guardado. No es el proveedor configurado ahora."]}
