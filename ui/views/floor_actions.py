"""«Cerrar operación» on the Floor page: the trade manager / risk desk ("Gestor de la operación") watching the routed
OANDA demo trade.

Two clicks (button -> «Confirmar cierre»); enabled only for an owner-authenticated session
(``ui.auth.owner_authenticated``), with broker-demo routing ON on the practice account, a routed OPEN trade and no
close already pending. The click only stores a request (``execution.operator_actions.request_manual_close``, imported
lazily); the router closes the trade on its next tick through its own close path (reason ``MANUAL_CLOSE``). The
confirmation carries one ``request_id`` and is consumed before the request is sent: a repeated / concurrent click never
queues a second close. Nothing here touches the network.
"""
from uuid import uuid4


MANUAL_CLOSE_TITLE = "🧑‍💼 Gestor de la operación · mesa de riesgo"
CONFIRM_KEY, RESULT_KEY = "floor-manual-close-confirming", "floor-manual-close-result"
RESULT_TEXT = {
    "requested": "Cierre solicitado: el sistema cierra la operación en unos segundos (cuenta de práctica OANDA).",
    "duplicate": "Este cierre ya estaba solicitado: no se envía otro.",
    "already_pending": "Ya hay un cierre pendiente: no se envía otro.",
    "no_open_trade": "La operación ya no está abierta: no se hizo nada.",
    "action_unresolved": "Hay una acción anterior sin resolver en el broker: no se hizo nada.",
    "routing_off": "El enrutado al demo OANDA está apagado: no se hizo nada.",
    "not_practice": "Solo se permite en la cuenta de práctica OANDA: no se hizo nada.",
    "real_execution_enabled": "Ejecución real detectada: bloqueado.",
}
RESULT_ERROR = "Error: no se pudo guardar la solicitud de cierre."


def manual_close_api():
    """Lazy operator actions + read-only state; None when unavailable (=> button disabled). Never raises."""
    try:
        from execution.operator_actions import (manual_close_target, operator_state, practice_problem,
                                                request_manual_close)
        return {"problem": practice_problem(), "target": manual_close_target(), "state": operator_state(),
                "close": request_manual_close}
    except Exception:  # noqa: BLE001 - the action is optional: unavailable => nothing can be requested
        return None


def manual_close_disabled(api, owner):
    """Why «Cerrar operación» is disabled (Spanish sentence), or None when it can be used."""
    if owner is not True:
        return "Solo el owner con sesión iniciada (contraseña del panel) puede cerrar operaciones."
    if not isinstance(api, dict):
        return "Acciones del operador no disponibles."
    if api.get("problem") == "routing_off":
        return "El enrutado a la cuenta demo de OANDA está apagado."
    if api.get("problem"):
        return "Solo disponible en la cuenta de práctica OANDA (la cuenta real está bloqueada)."
    target = api.get("target")
    if not isinstance(target, dict):
        return "No hay ninguna operación enrutada abierta."
    if target.get("unresolved"):
        return "Hay una acción anterior sin resolver en el broker; espera a la reconciliación."
    if (api.get("state") or {}).get("manual_close_pending") is True:
        return "Cierre ya solicitado: el sistema lo ejecuta en unos segundos."
    return None


def target_text(target):
    if not isinstance(target, dict):
        return "Sin operación enrutada abierta."
    try:
        units = f"{abs(float(target.get('units'))):,.0f} u".replace(",", " ")
    except (TypeError, ValueError):
        units = "—"
    trade = str(target.get("trade_id") or "")
    return (f"{target.get('symbol') or '—'} {target.get('side') or '—'} · {units} · "
            f"trade …{trade[-4:] if trade else '—'} · OANDA práctica")


def render_manual_close(st, api=None, owner=None):
    """«Cerrar operación» with a confirmation step. ``api``/``owner`` are injectable for tests."""
    from ui.auth import owner_authenticated
    state = st.session_state
    api = manual_close_api() if api is None else api
    owner = owner_authenticated(st) if owner is None else owner
    disabled = manual_close_disabled(api, owner)
    target = api.get("target") if isinstance(api, dict) else None
    st.markdown(f"**{MANUAL_CLOSE_TITLE}**")
    st.caption(target_text(target))
    if st.button("Cerrar operación", disabled=disabled is not None, key="floor-manual-close"):
        pending = state.get(CONFIRM_KEY)
        if not (isinstance(pending, dict) and pending.get("client_id") == target["client_id"]):
            state[CONFIRM_KEY] = {"client_id": target["client_id"], "request_id": uuid4().hex}
        state[RESULT_KEY] = None
    pending = state.get(CONFIRM_KEY)
    if disabled is None and isinstance(pending, dict) and pending.get("client_id") == target["client_id"]:
        st.warning("¿Cerrar ahora esta operación a mercado en la cuenta de práctica OANDA? Pulsa «Confirmar cierre».")
        if st.button("Confirmar cierre", key="floor-manual-close-confirm"):
            state.pop(CONFIRM_KEY, None)  # one request per confirmation: a re-render can never send it twice
            try:
                result = api["close"](actor="dashboard", reason="Floor: Cerrar operación",
                                      client_id=pending["client_id"], request_id=pending["request_id"])
            except Exception:  # noqa: BLE001 - request_manual_close never raises; belt and braces
                result = None
            state[RESULT_KEY] = result.get("reason") if isinstance(result, dict) else "error"
        elif st.button("Cancelar", key="floor-manual-close-cancel"):
            state.pop(CONFIRM_KEY, None)
    elif pending is not None and disabled is not None:
        state.pop(CONFIRM_KEY, None)  # the trade closed / the session lost owner rights: drop the confirmation
    result = state.get(RESULT_KEY)
    if result:
        st.info(RESULT_TEXT.get(result, RESULT_ERROR))
    if disabled is not None:
        st.caption(disabled)
