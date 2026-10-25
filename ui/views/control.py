"""Control screen: account switches are read-only; the emergency-stop button works. Nothing here reads broker secrets.

Emergency stop (ON unless ``AI_FLOOR_EMERGENCY_BUTTON == "0"``): click, type "PARAR TODO", confirm ->
``execution.operator_actions.request_emergency_stop`` (every account OFF + emergency latched in the state DB + a
flatten request the router executes on its next tick). Clearing the emergency is NOT possible from here. A drift block
shows a "Bloqueo por drift" section; its clear-request button exists only with ``AI_FLOOR_DRIFT_CLEAR_BUTTON == "1"``
and the router clears only after verifying broker == local state. Env flags are read by ``operator_actions``.

Also shows (read-only): the broker-demo routing chip/status (``execution.broker_demo_state.read_router_status``), the
Demo/Real account VIEW toggle with balances from ``execution.oanda_readonly`` (GET summary only; Real is display only,
trading stays blocked) and the "Historial (referencia)" EURUSD baseline cards from ``execution.historial`` (aggregated
local files, never OANDA trades). Every lazy import failure => "apagado" / "—" / "sin datos".
"""
from datetime import datetime, timezone
from decimal import Decimal
from html import escape

from ui.i18n_es import status_text

ACCOUNT_KEYS = ("paper", "broker_demo", "real")
BALANCE_REFRESH = "10s"  # the read-only summary itself is cached >= 5 s (execution/oanda_readonly.py)
VIEW_LABELS = {"practice": "Demo · cuenta practice OANDA", "live": "Real · solo lectura (operar bloqueado)"}
MISSING_ENV_NAMES = {"practice": ("OANDA_PRACTICE_TOKEN", "OANDA_ACCOUNT_ID"),  # variable NAMES only
                     "live": ("OANDA_LIVE_TOKEN", "OANDA_LIVE_ACCOUNT_ID")}
# What the owner reads instead of the variable name (presentation only; the names stay in MISSING_ENV_NAMES).
MISSING_TEXT = {"OANDA_PRACTICE_TOKEN": "falta configurar el acceso a OANDA (token de la cuenta demo)",
                "OANDA_ACCOUNT_ID": "falta configurar el número de la cuenta demo de OANDA",
                "OANDA_LIVE_TOKEN": "falta configurar el acceso a OANDA (token de la cuenta real)",
                "OANDA_LIVE_ACCOUNT_ID": "falta configurar el número de la cuenta real de OANDA"}
VIEW_STATUS = {"SIN_TOKEN": "sin token", "SIN_CUENTA": "sin cuenta", "ERROR": "error de lectura"}
ROUTING_CHIPS = {"off": ("Enrutado a broker demo: apagado", ""), "dry_run": ("Enrutado a broker demo: dry-run", "warn"),
                 "on": ("Enrutado a broker demo: activo (cuenta practice)", "pos")}
DASH = "—"
LABELS = {"paper": "Cuenta de práctica interna", "broker_demo": "Broker demo (OANDA)", "real": "Cuenta real"}
# Mirrors execution.historial.SOURCES (kept here so a failed lazy import still names the expected files).
HISTORY_SOURCES = (("v1_replay", "v1_replay_eurusd.json", "Replay estrategia V1 (velas Twelve Data)"),
                   ("paper", "paper_closed_trades_eurusd.json", "Operaciones cerradas de práctica interna (agregado)"))
HISTORY_TITLE = "📚 Historial (referencia) · no son trades de OANDA"
HISTORY_CHIP = "Referencia · no son trades de OANDA"
HISTORY_EMPTY = "sin datos todavía (se cargará con la exportación)"
EMERGENCY_PHRASE, DRIFT_PHRASE = "PARAR TODO", "LIMPIAR DRIFT"
DRIFT_SENT = ("Solicitud enviada: el sistema la verificará en el próximo ciclo; se limpia solo si las posiciones del "
              "broker y el estado local coinciden")


def default_snapshot():
    """Unreadable registry -> everything off."""
    return {"readable": False, "emergency_stop": False, "connected": False,
            "accounts": [{"key": key, "mode": "OFF", "enabled": False, "label": LABELS[key], "locked": key == "real"}
                         for key in ACCOUNT_KEYS]}


def control_snapshot():
    try:
        from execution.account_registry import read_control_snapshot
        snapshot = read_control_snapshot()
        if isinstance(snapshot, dict) and snapshot.get("readable") is not True:
            # Fresh disk: create the registry with every switch OFF (never touches an existing file), then re-read.
            from execution.account_registry import ensure_registry_initialized
            if ensure_registry_initialized():
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


def account_status(account, snapshot, routing=None, read_ok=False):
    """Chip text of one account row. For the OANDA demo row the same facts as the routing chip and the balance panel:
    Emergency stop and account OFF take priority. Routing and the read-only balance determine the remaining
    connectivity text; the registry's ``connected`` flag may lag behind (presentation only)."""
    if account["key"] == "real" and (account.get("locked") or not account.get("enabled")):
        return "Real: bloqueado por seguridad", "neg"
    if account["key"] == "broker_demo":
        connection = "Conectado (saldo leído)" if read_ok else "Saldo no disponible"
        if snapshot.get("emergency_stop") is True:
            return f"Parada de emergencia activada · {connection}", "neg"
        if account.get("enabled") is not True or account.get("mode") == "OFF":
            return f"Cuenta apagada · {connection}", "warn"
        if routing == "dry_run":
            return f"{connection} · enrutado en prueba (dry-run)", "warn"
        if routing != "on":
            return f"{connection} · enrutado apagado", ""
        if read_ok:
            return "Conectado · activo (enrutado encendido, saldo leído)", "pos"
        return "Enrutado encendido · no se pudo leer el saldo de OANDA", "warn"
    if not snapshot["connected"]:
        return "Aún no conectado", "warn"
    return ("Encendido" if account.get("enabled") else "Apagado"), ("pos" if account.get("enabled") else "")


def router_status():
    """Read-only routing status with strict types; any failure => everything off."""
    off = {"state": "off", "blocked_reason": None, "open_trade": None, "last_events": []}
    try:
        from execution.broker_demo_state import read_router_status
        status = read_router_status()
    except Exception:  # The router is optional: unreadable => "apagado".
        return off
    if not isinstance(status, dict) or status.get("readable") is not True or status.get("routing") is not True:
        return off
    trade = status.get("open_trade") if isinstance(status.get("open_trade"), dict) else None
    events = [e for e in status.get("last_events") or () if isinstance(e, dict)][:5]
    blocked = status.get("blocked_reason")
    return {"state": "dry_run" if status.get("dry_run") is True else "on",
            "blocked_reason": blocked if isinstance(blocked, str) and blocked else None,
            "open_trade": trade, "last_events": events}


def routing_chip(status):
    return ROUTING_CHIPS.get(status.get("state") if isinstance(status, dict) else None, ROUTING_CHIPS["off"])


def account_view(environment):
    """Cached read-only AccountView or None (never raises)."""
    try:
        from execution.oanda_readonly import cached_account_view
        return cached_account_view(environment)
    except Exception:
        return None


def money(value, currency=None):
    if not isinstance(value, Decimal) or not value.is_finite():
        return DASH
    return f"{value:,.2f}" + (f" {currency}" if isinstance(currency, str) and currency else "")


def _missing_env_name(view, status):
    """Name of the env var the view needs (never its value) when the view says token/account is missing."""
    if status not in ("SIN_TOKEN", "SIN_CUENTA"):
        return None
    names = MISSING_ENV_NAMES.get(getattr(view, "environment", None))
    if not names:
        return None
    return names[0] if status == "SIN_TOKEN" else names[1]


def balance_lines(view, now=None):
    """(label, text) pairs for one account view; missing values => "—"."""
    status = getattr(view, "status", None)
    if status not in ("OK", "SIN_TOKEN", "SIN_CUENTA", "ERROR"):
        return [("Estado", VIEW_STATUS["ERROR"])]
    currency = getattr(view, "currency", None)
    stale = " (datos anteriores)" if getattr(view, "stale", False) is True else ""
    lines = [("Estado", ("conectado" if status == "OK" else VIEW_STATUS[status]) + stale)]
    missing = _missing_env_name(view, status)
    if missing:
        lines.append(("Falta", MISSING_TEXT.get(missing, "falta configurar el acceso a OANDA")))  # never a value
    account = getattr(view, "account_masked", None)
    lines.append(("Cuenta", account if isinstance(account, str) and account else DASH))
    for label, name in (("Balance", "balance"), ("NAV (equity)", "nav"), ("Margen usado", "margin_used"),
                        ("P&L no realizado", "unrealized_pl")):
        lines.append((label, money(getattr(view, name, None), currency)))
    trades = getattr(view, "open_trades", None)
    lines.append(("Posiciones abiertas", str(trades) if type(trades) is int else DASH))
    as_of = getattr(view, "as_of", None)
    now = now or datetime.now(timezone.utc)
    if isinstance(as_of, datetime) and as_of.tzinfo is not None:
        lines.append(("Actualizado", f"actualizado hace {max(0, int((now - as_of).total_seconds()))}s"))
    else:
        lines.append(("Actualizado", DASH))
    return lines


def render_balances(st, real):
    from ui.views.luxury import html
    environment = "live" if real else "practice"
    rows = "".join(f'<div class="small">{escape(label)}: <b>{escape(text)}</b></div>'
                   for label, text in balance_lines(account_view(environment)))
    note = ("Solo se lee el saldo (GET resumen de cuenta). No hay botones de operar: la ejecución REAL sigue bloqueada."
            if real else "Cuenta demo (practice) de OANDA. Saldo leído en modo solo lectura.")
    html(st, f'<div class="card"><h3>{escape(VIEW_LABELS[environment])}</h3>{rows}'
             f'<div class="small">{escape(note)}</div></div>')


def render_accounts_view(st):
    """Demo | Real VIEW toggle (default Demo) + auto-refreshing read-only balance."""
    from ui.views.luxury import html
    html(st, '<h3 style="margin:12px 0 4px">💼 Saldo de la cuenta OANDA</h3>')
    real = st.toggle("Vista: Demo | Real (Real = solo lectura)", value=False, key="account-view-real")

    def balances():
        render_balances(st, real)

    fragment = getattr(st, "fragment", None)
    if callable(fragment):
        fragment(run_every=BALANCE_REFRESH)(balances)()
    else:
        balances()


BLOCKED_TEXT = {"drift": "las posiciones del broker no coinciden con el estado local (drift)",
                "emergency": "parada de emergencia activada", "daily_loss": "límite de pérdida diaria alcanzado"}


def current_events(events, since=None):
    """Router events for display: ET time + readable kind; in demo mode only those at/after the epoch."""
    from ui.i18n_es import datetime_et
    from ui.views.dashboard import is_current, parse_time
    rows = []
    for event in events or ():
        if since is not None and not is_current(event.get("ts"), since):
            continue
        at = parse_time(event.get("ts"))
        rows.append((datetime_et(at) if at else str(event.get("ts") or DASH),
                     status_text(event.get("kind")) if event.get("kind") else DASH))
    return rows


def render_router(st, status, since=None):
    from ui.views.luxury import html
    text, kind = routing_chip(status)
    trade = status.get("open_trade")
    parts = (("lado", "side"), ("unidades", "units"), ("estado", "status"))
    trade_text = DASH if not trade else " · ".join(
        [f"operación …{escape(str(trade['trade_id'])[-4:])}"] * bool(trade.get("trade_id"))
        + [f"{label} {escape(status_text(trade.get(key)))}" for label, key in parts if trade.get(key) is not None])
    events = "".join(f'<div class="small">{escape(at)} · {escape(what)}</div>'
                     for at, what in current_events(status.get("last_events"), since))
    events = events or f'<div class="small">{"Esperando el primer ciclo" if since is not None else DASH}</div>'
    blocked = status.get("blocked_reason")
    blocked = BLOCKED_TEXT.get(blocked, status_text(blocked)) if blocked else "ninguno"
    html(st, f'<div class="card"><span class="chip {kind}">{escape(text)}</span>'
             f'<div class="small">Bloqueo: {escape(blocked)}</div>'
             f'<div class="small">Operación abierta en el broker demo: {trade_text}</div>'
             f'<div class="small">Últimos eventos:</div>{events}</div>')


def history_baselines():
    """[(source, filename, label, stats|None)] in the fixed order; any import/read failure => every source "sin datos"."""
    try:
        from execution.historial import load_baselines
        records = load_baselines()
        rows = [(r.source, r.filename,
                 r.label.replace("Trades cerrados PAPER (agregado)", HISTORY_SOURCES[1][2])
                        .replace("PAPER", "práctica interna"),
                 r.stats if r.status == "OK" and isinstance(r.stats, dict) else None) for r in records]
    except Exception:  # The baseline is optional: unreadable => "sin datos".
        rows = []
    known = {row[0]: row for row in rows}
    return [known.get(source) or (source, filename, label, None) for source, filename, label in HISTORY_SOURCES]


def r_text(value, signed=True):
    if type(value) not in (int, float):
        return DASH
    return f"{value:+.2f} R" if signed else f"{value:.2f} R"


def history_lines(stats):
    """(label, text) pairs, money-free: counts and R-multiples only."""
    period = stats.get("period") or {}
    pf = stats.get("profit_factor")
    rate = stats.get("win_rate")
    return [("Periodo", f"{period.get('from') or DASH} → {period.get('to') or DASH}"),
            ("Operaciones cerradas", str(stats.get("trades", DASH))),
            ("Ganadoras / perdedoras", f"{stats.get('wins', DASH)} / {stats.get('losses', DASH)}"),
            ("Acierto", f"{rate * 100:.1f} %" if type(rate) in (int, float) else DASH),
            ("Expectativa (R medio)", r_text(stats.get("expectancy_r"))),
            ("Factor de beneficio", f"{pf:.2f}" if type(pf) in (int, float) else DASH),
            ("Caída máxima", r_text(stats.get("max_drawdown_r"), signed=False)),
            ("Media ganadora / perdedora",
             f"{r_text(stats.get('avg_r_win'))} / {r_text(stats.get('avg_r_loss'))}"),
            ("Generado", str(stats.get("generated_at") or DASH))]


def operator_api():
    """Lazy operator actions + their read-only state; None when unavailable (=> emergency button disabled, no drift
    button). Never raises."""
    try:
        from execution.operator_actions import button_flags, operator_state, request_drift_clear, request_emergency_stop
        flags, state = button_flags(), operator_state()
    except Exception:  # The actions are optional: unavailable => nothing can be requested from here.
        return None
    if not isinstance(flags, dict) or not isinstance(state, dict):
        return None
    return {"flags": flags, "state": state, "stop": request_emergency_stop, "drift_clear": request_drift_clear}


def _ok(result):
    return isinstance(result, dict) and result.get("ok") is True


def render_emergency(st, snapshot, api):
    """Two-step emergency stop: button -> typed phrase "PARAR TODO" -> "Confirmar parada". Never offers a clear."""
    from ui.views.luxury import html
    state = st.session_state
    enabled = api is not None and api["flags"].get("emergency") is True
    if st.button("🛑 PARADA DE EMERGENCIA", disabled=not enabled, key="control-emergency-stop", width="stretch"):
        state["control-emergency-confirming"] = True
        state["control-emergency-result"] = None
    if enabled and state.get("control-emergency-confirming") is True:
        html(st, '<div class="small">Esto apaga todas las cuentas y pide cerrar todas las posiciones del broker '
                 f'demo. Para confirmar escribe exactamente <b>{EMERGENCY_PHRASE}</b>.</div>')
        phrase = st.text_input("Frase de confirmación", key="control-emergency-phrase")
        if st.button("Confirmar parada", key="control-emergency-confirm"):
            if phrase == EMERGENCY_PHRASE:
                result = api["stop"](actor="dashboard", reason="boton del panel")
                state["control-emergency-result"] = "ok" if _ok(result) else "error"
                state["control-emergency-confirming"] = False
            else:
                html(st, '<div class="small">La frase no coincide: no se hizo nada.</div>')
    result = state.get("control-emergency-result")
    if result == "ok":
        html(st, '<span class="chip neg">Parada activada: cuentas apagadas y cierre de posiciones solicitado '
                 '(se ejecuta en el próximo ciclo del sistema).</span>')
    elif result == "error":
        html(st, '<span class="chip neg">Error: la parada no se pudo guardar por completo. Revisa el sistema; como '
                 'respaldo, el owner puede activar la parada de emergencia en la configuración del servidor.</span>')
    if api is None:
        note = "el botón no está disponible (no se pudo cargar el módulo de acciones)"
    elif not enabled:
        note = "botón desactivado por configuración"
    else:
        note = "pide confirmación escrita antes de actuar"
    status = "Activada: todo detenido" if snapshot["emergency_stop"] else "No activada"
    html(st, f'<div class="stop-note">Estado: {escape(status)} · {escape(note)}. Quitar la parada no es posible desde '
             'el panel.</div>')


def render_drift(st, api):
    """Shown only while the router is blocked by drift; the request button needs AI_FLOOR_DRIFT_CLEAR_BUTTON=1."""
    from ui.views.luxury import html
    if api is None or api["state"].get("blocked_reason") != "drift":
        return
    html(st, '<h3 style="margin:12px 0 4px">⚠️ Bloqueo por drift</h3><div class="bubble">El broker tiene posiciones '
             'que no coinciden con el estado local del sistema, así que no se abren operaciones nuevas.</div>')
    if api["flags"].get("drift_clear") is not True:
        html(st, '<div class="small">La limpieza desde el panel está desactivada. Revisa y cierra a mano en OANDA las '
                 'posiciones que no correspondan; el bloqueo se mantiene hasta que el owner lo limpie.</div>')
        return
    if api["state"].get("drift_clear_pending") is True:
        html(st, '<div class="small">Ya hay una solicitud pendiente de verificación.</div>')
    html(st, f'<div class="small">Para pedir la limpieza escribe exactamente <b>{DRIFT_PHRASE}</b>.</div>')
    phrase = st.text_input("Frase de confirmación (drift)", key="control-drift-phrase")
    if st.button("Solicitar limpieza del drift", key="control-drift-clear"):
        if phrase != DRIFT_PHRASE:
            html(st, '<div class="small">La frase no coincide: no se hizo nada.</div>')
            return
        result = api["drift_clear"](actor="dashboard", reason="boton del panel")
        if _ok(result):
            text = DRIFT_SENT
        elif isinstance(result, dict) and result.get("reason") == "no_drift":
            text = "Ya no hay bloqueo por drift: no se envió nada."
        else:
            text = "Error: no se pudo enviar la solicitud."
        html(st, f'<div class="small"><b>{escape(text)}</b></div>')


def render_history(st):
    from ui.views.luxury import html
    cards = []
    for _source, filename, label, stats in history_baselines():
        if stats is None:
            body = (f'<div class="small">{escape(HISTORY_EMPTY)}</div>'
                    f'<div class="small" title="data/historial/{escape(filename)}">Se cargará cuando se exporte el '
                    'historial de referencia.</div>')
        else:
            body = "".join(f'<div class="small">{escape(k)}: <b>{escape(v)}</b></div>' for k, v in history_lines(stats))
            if stats.get("notes"):
                body += f'<div class="small">Nota: {escape(stats["notes"])}</div>'
        cards.append(f'<div class="card"><h4>{escape(label)} · EURUSD</h4>'
                     f'<span class="chip">{escape(HISTORY_CHIP)}</span>{body}</div>')
    html(st, f'<div class="card"><h3>{escape(HISTORY_TITLE)}</h3>'
             '<div class="bubble">Estadísticas de referencia (baseline) de sistemas anteriores, solo en R y conteos. '
             'Están separadas de las estadísticas de la demo en vivo de OANDA y nunca se mezclan con ellas.</div>'
             f'{"".join(cards)}</div>')


def render(st, snapshot=None):
    from ui.views.luxury import html
    snapshot = snapshot or control_snapshot()
    accounts = {a["key"]: a for a in snapshot["accounts"]}
    readable = ("Estado leído del registro de cuentas." if snapshot["readable"]
                else "No se pudo leer el registro de cuentas: se muestra todo apagado por seguridad.")
    api = operator_api()
    routing = router_status()
    from ui.views.dashboard import demo_since
    since = demo_since()  # OANDA demo: router events before the epoch are not shown as current
    read_ok = getattr(account_view("practice"), "status", None) == "OK"
    html(st, '<div class="card"><h3>🎛️ Panel de control</h3>'
             '<div class="bubble">Los interruptores de las cuentas son solo de lectura: muestran el estado y no se '
             'cambian desde aquí. El botón de parada de emergencia sí funciona: apaga todas las cuentas y pide cerrar '
             'las posiciones del broker demo. Quitar la parada de emergencia no es posible desde el panel. La cuenta '
             'de práctica interna sigue funcionando igual.</div>'
             f'<div class="small">{escape(readable)}</div></div>')
    left, right = st.columns([1.3, 1])
    with left:
        html(st, '<h3 style="margin:4px 0">Cuentas</h3>')
        for key, icon in (("broker_demo", "🧪"), ("real", "🔒")):
            account = accounts[key]
            text, kind = account_status(account, snapshot, routing["state"], read_ok)
            st.toggle(f"{icon} {account.get('label') or LABELS[key]}", value=bool(account.get("enabled")),
                      disabled=True, key=f"control-{key}")
            html(st, f'<span class="chip {kind}">{escape(text)}</span>'
                     f'<span class="small" style="margin-left:8px" title="{escape(str(account.get("mode") or "OFF"), quote=True)}">'
                     f'modo: {escape(status_text(account.get("mode") or "OFF"))}</span>')
        paper = accounts["paper"]
        html(st, f'<div class="small" style="margin-top:12px">{escape(LABELS["paper"])}: '  # registry label "PAPER" is internal
                 f'{"encendida" if paper.get("enabled") else "sin datos en el registro"} · no se controla desde aquí.</div>')
    with right:
        html(st, '<h3 style="margin:4px 0">Parada de emergencia</h3>')
        render_emergency(st, snapshot, api)
    render_drift(st, api)
    html(st, '<h3 style="margin:12px 0 4px">🔀 Enrutado a broker demo</h3>')
    render_router(st, routing, since)
    render_accounts_view(st)
    render_history(st)
