"""Control screen skeleton: read-only, every control disabled. Nothing here writes or reads broker secrets."""
from html import escape

from ui.i18n_es import status_text

ACCOUNT_KEYS = ("paper", "broker_demo", "real")
LABELS = {"paper": "Cuenta de práctica interna (PAPER)", "broker_demo": "Broker demo", "real": "Cuenta real"}


def default_snapshot():
    """Unreadable registry -> everything off."""
    return {"readable": False, "emergency_stop": False, "connected": False,
            "accounts": [{"key": key, "mode": "OFF", "enabled": False, "label": LABELS[key], "locked": key == "real"}
                         for key in ACCOUNT_KEYS]}


def control_snapshot():
    try:
        from execution.account_registry import read_control_snapshot
        snapshot = read_control_snapshot()
    except Exception:  # The registry is optional: any import/read failure means "not readable -> off".
        return default_snapshot()
    if (not isinstance(snapshot, dict) or snapshot.get("readable") is not True
            or not isinstance(snapshot.get("accounts"), (list, tuple))):
        return default_snapshot()
    accounts = {a.get("key"): a for a in snapshot["accounts"] if isinstance(a, dict) and a.get("key") in ACCOUNT_KEYS}
    return {"readable": True, "emergency_stop": snapshot.get("emergency_stop") is True,
            "connected": snapshot.get("connected") is True,
            "accounts": [checked_account(base, accounts.get(base["key"], {})) for base in default_snapshot()["accounts"]]}


def checked_account(base, account):
    """Strict types only; anything else keeps the safe default. The real account is never shown enabled."""
    real = base["key"] == "real"
    return {**base, "mode": account["mode"] if isinstance(account.get("mode"), str) else base["mode"],
            "label": account["label"] if isinstance(account.get("label"), str) and account["label"] else base["label"],
            "enabled": account.get("enabled") is True and not real,
            "locked": True if real else account.get("locked") is True}


def account_status(account, snapshot):
    if account["key"] == "real" and (account.get("locked") or not account.get("enabled")):
        return "Real: bloqueado por seguridad", "neg"
    if not snapshot["connected"]:
        return "Aún no conectado", "warn"
    return ("Encendido" if account.get("enabled") else "Apagado"), ("pos" if account.get("enabled") else "")


def render(st, snapshot=None):
    from ui.views.luxury import html
    snapshot = snapshot or control_snapshot()
    accounts = {a["key"]: a for a in snapshot["accounts"]}
    readable = ("Estado leído del registro de cuentas." if snapshot["readable"]
                else "No se pudo leer el registro de cuentas: se muestra todo apagado por seguridad.")
    html(st, '<div class="card"><h3>🎛️ Panel de control · solo lectura</h3>'
             '<div class="bubble">Esta pantalla todavía no hace nada: los interruptores y el botón están '
             'desactivados. Sirve para ver el estado. La cuenta de práctica (PAPER) sigue funcionando igual.</div>'
             f'<div class="small">{escape(readable)}</div></div>')
    left, right = st.columns([1.3, 1])
    with left:
        html(st, '<h3 style="margin:4px 0">Cuentas</h3>')
        for key, icon in (("broker_demo", "🧪"), ("real", "🔒")):
            account = accounts[key]
            text, kind = account_status(account, snapshot)
            st.toggle(f"{icon} {account.get('label') or LABELS[key]}", value=bool(account.get("enabled")),
                      disabled=True, key=f"control-{key}")
            html(st, f'<span class="chip {kind}">{escape(text)}</span>'
                     f'<span class="small" style="margin-left:8px" title="{escape(str(account.get("mode") or "OFF"), quote=True)}">'
                     f'modo: {escape(status_text(account.get("mode") or "OFF"))}</span>')
        paper = accounts["paper"]
        html(st, f'<div class="small" style="margin-top:12px">{escape(paper.get("label") or LABELS["paper"])}: '
                 f'{"encendida" if paper.get("enabled") else "sin datos en el registro"} · no se controla desde aquí.</div>')
    with right:
        html(st, '<h3 style="margin:4px 0">Parada de emergencia</h3>')
        st.button("🛑 PARADA DE EMERGENCIA", disabled=True, key="control-emergency-stop", width="stretch")
        state = ("Activada: todo detenido" if snapshot["emergency_stop"] else "No activada")
        html(st, f'<div class="stop-note">Estado: {escape(state)} · el botón aún no está conectado.</div>')
