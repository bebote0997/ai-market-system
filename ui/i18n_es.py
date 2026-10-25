"""Spanish presentation layer for the dashboard: labels, columns, states, close reasons and plain phrases.

Presentation only. Stored values, data keys and identifiers stay untouched; every mapping falls back to the
original text so unknown values are shown as persisted (never invented).
"""
from datetime import datetime
from html import escape
import math
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
MISSING = "no disponible aún"
NO_DATA = "Sin datos"
NO_PRICE = "Aún sin precio actual"
STOP_UNKNOWN = "sin datos del stop original"

# Navigation: internal page keys stay as identifiers; only the label is translated.
PAGES = {
    "Overview": "🏠 Inicio", "The Floor": "🏢 La mesa", "Process": "🔄 Proceso",
    "Setups": "🎯 Setups", "Trades": "💼 Trades", "Decisions": "🧠 Decisiones",
    "Analysts · soon": "🔬 Analistas · pronto", "Control": "🎛️ Control",
}
LEGACY_PAGES = {
    "OVERVIEW": "Resumen", "POSITIONS": "Posiciones", "SETUPS": "Setups",
    "DECISIONS": "Decisiones", "HEALTH": "Salud del sistema", "ALERTS": "Alertas",
    "HISTORY": "Historial", "ERRORS": "Errores", "FLOOR": "Mesa", "MARKETS": "Mercados",
    "JOURNAL": "Bitácora", "EXPERIMENT": "Experimento", "SYSTEM": "Sistema",
}
LEGACY_PREFIX = "Detalle · "
PAGE_TITLES = {key: label.split(" ", 1)[1] for key, label in PAGES.items()}

# Headings, captions, buttons and messages (English original -> Spanish).
TEXT = {
    "Navigation": "Navegación",
    "Market": "Mercado",
    "Enabled for experiment: ": "Activos en el experimento: ",
    "SAMPLE / DEMO · FLOOR, MARKETS, JOURNAL, SYSTEM": "Datos de ejemplo en vistas detalladas",
    "Auto-refresh · every 60s": "Actualizar solo · cada 60 s",
    "PAPER · REAL EXECUTION DISABLED": "OANDA Demo (práctica) · ejecución real desactivada",
    "Observation only · no live orders": "Solo observación · no envía órdenes reales",
    "PAPER · REAL EXECUTION DISABLED · NO REAL MONEY":
        "Cuenta de práctica OANDA Demo · sin dinero real · ejecución real desactivada",
    "PAPER MODE · REAL EXECUTION DISABLED · NO REAL MONEY":
        "Cuenta de práctica OANDA Demo · sin dinero real · ejecución real desactivada",
    "SAMPLE / DEMO": "DATOS DE EJEMPLO",
    "Paper trading only · observation only": "Solo práctica · solo observación",
    "Legacy / Detail": "Vistas detalladas (técnicas)",
    "Trading Floor": "AI Trading Floor",
    "PAPER · V2": "OANDA Demo · V2.1",
    # Overview
    "Equity · PAPER": "Equity · práctica interna",
    "Closed-trade curve": "Closed-trade curve",
    "Closed-trade curve · latest 200 points · cumulative realized PnL over full ledger":
        "Cumulative realized PnL · closed trades (últimos 200 puntos)",
    "Session": "Sesión de mercado",
    "System": "Estado del sistema",
    "Open positions": "Open positions",
    "Active policies": "Reglas activas",
    "Outside sessions": "Fuera de sesión",
    "No open position": "No open position",
    # Trades
    "Closed trades · latest published": "Closed trades",
    "Trade detail": "Trade detail",
    "Plan": "Trade plan",
    "Order planned entry": "Entrada planeada de la orden",
    "Execution": "Execution",
    "Slippage · plan vs fill": "Slippage · plan vs fill",
    "Open Process · ": "Ver el proceso · ",
    # Decisions / Floor / Process / Setups / Analysts
    "Decision process": "Ver el proceso de una decisión",
    "Decisions · newest first": "Decisiones · las más recientes primero",
    "Agent rooms": "Agentes de IA",
    "Risk Engine": "Motor de riesgo",
    "Post-trade analysis department · soon": "Departamento de análisis · próximamente",
    "Future analysts will review closed trades and propose improvements. Proposals require owner approval; nothing is automatic.":
        "Pronto unos analistas revisarán las operaciones cerradas y propondrán mejoras. "
        "Nada se aplica solo: todo necesita tu aprobación.",
    "Latest 200 cycles; exact run links can open older cycles.":
        "Últimos 200 ciclos de análisis. Los enlaces desde otras vistas pueden abrir ciclos anteriores.",
    "Process symbol": "Símbolo",
    "Cycle · newest first": "Ciclo · el más reciente primero",
    "Timestamps below are persisted record timestamps; absent step timestamps are not inferred.":
        "Las horas son las que quedaron guardadas; si un paso no tiene hora, no se inventa.",
    "Process": "Proceso",
    "not persisted yet": "aún no guardado",
    "Persisted evidence": "Datos guardados",
    "Gaps · not persisted yet": "Datos que faltan",
    "Gaps": "Datos que faltan",
    "None": "Ninguno",
    "Latest 200 setups · cache: closed 5m prices as of setup; OHLC only when persisted.":
        "Últimos 200 setups. Gráfico con cierres de 5 min hasta el momento del setup.",
    "Validated setups": "Valid setups",
    "Setups symbol": "Símbolo",
    "Setup · newest first": "Setup · más reciente primero",
    "Setup ID not persisted; setup email dedup unavailable": "Setup ID no guardado",
    "Chart": "Gráfico",
    "chart unavailable: bars not persisted": "gráfico no disponible: no hay velas guardadas",
    "Exact-cycle 15m OHLC bars not persisted": "No hay velas de 15 min guardadas para este ciclo",
    "chart unavailable: persisted bars invalid": "gráfico no disponible: las velas guardadas no son válidas",
    "5m close prices · candle_cache · no OHLC available": "precios de cierre de 5 min · sin velas completas",
    "Persisted 15m OHLC": "Velas de 15 min guardadas",
    "DASHBOARD_ERROR": "No se pudieron leer los datos guardados",
    # Legacy views
    "Persisted PAPER evidence · SQLite read-only · missing evidence = NO_DATA":
        "Datos guardados de la cuenta de práctica · solo lectura · lo que falta se muestra como «Sin datos»",
    "Market state / charts": "Estado del mercado y gráficos",
    "Open positions · all symbols": "Open positions · todos los símbolos",
    "Pending orders · all symbols": "Pending orders · todos los símbolos",
    "Latest setup per symbol": "Último setup por símbolo",
    "AI decisions · interpretation": "Decisiones de la IA · interpretación",
    "Risk decisions · deterministic authority": "Decisiones del motor de riesgo · autoridad final",
    "System Health observations": "Observaciones de salud del sistema",
    "Persisted component observations; no probes or health policy changes are performed.":
        "Observaciones guardadas; esta vista no hace pruebas ni cambia nada.",
    "Provider configuration is not proof of API health. Statuses are persisted observations.":
        "Tener un proveedor configurado no prueba que funcione. Los estados son observaciones guardadas.",
    "Provider / API run evidence": "Evidencia de proveedores por ciclo",
    "Phase 9 alert deliveries": "Envíos de alertas (fase 9)",
    "V1 notification deliveries": "Envíos de notificaciones (V1)",
    "Run ID": "ID del ciclo",
    "Setup ID": "Setup ID",
    "History page": "Página del historial",
    "Exact persisted IDs; both fields combine with AND. Up to 200 rows per table/page, newest first.":
        "IDs exactos; si llenas ambos se combinan. Hasta 200 filas por tabla y página, lo más reciente primero.",
    "Failed runs": "Ciclos fallidos",
    "Error journal": "Bitácora de errores",
    "AI TRADING FLOOR": "AI TRADING FLOOR",
    "SAMPLE / DEMO — NOT CURRENT PRICES": "DATOS DE EJEMPLO — NO SON PRECIOS ACTUALES",
    "CRITICAL — STALE DATA": "CRÍTICO — DATOS VIEJOS",
    "NEXT ANALYSIS: NOT SCHEDULED · AI INTERPRETS. PYTHON VALIDATES. RISK ENGINE AUTHORIZES. PAPER BROKER EXECUTES.":
        "PRÓXIMO ANÁLISIS: SIN PROGRAMAR · LA IA INTERPRETA, PYTHON VALIDA, EL MOTOR DE RIESGO AUTORIZA "
        "Y EL BROKER DE PRÁCTICA EJECUTA.",
    "PRICE ACTION · 15M STRUCTURE": "PRECIO · ESTRUCTURA DE 15 MIN",
    "CRITICAL — STALE DATA · historical observation only": "CRÍTICO — DATOS VIEJOS · solo como historial",
    "NO_DATA — no closed 15M bars in this snapshot.": "Sin datos — no hay velas cerradas de 15 min en esta foto.",
    "CHART_ERROR": "Error del gráfico",
    "FLOOR ASSISTANT": "ASISTENTE DE LA MESA",
    "Observation only. No authorization or execution controls.": "Solo observación. No autoriza ni ejecuta nada.",
    "AGENT CONSENSUS": "OPINIÓN DE LOS AGENTES",
    "SETUP": "SETUP",
    "DETERMINISTIC RISK ENGINE": "MOTOR DE RIESGO (REGLAS FIJAS)",
    "PYTHON DETERMINISTIC": "CALCULADO POR PYTHON, SIN IA",
    "PAPER POSITION": "POSICIÓN DE PRÁCTICA",
    "NO REAL MONEY": "SIN DINERO REAL",
    "FLAT": "FLAT",
    "PIPELINE": "PASOS DEL ANÁLISIS",
    "AI MEETING": "REUNIÓN DE LA IA",
    "MACRO / NEWS · FACT vs AI INTERPRETATION": "NOTICIAS · HECHOS vs INTERPRETACIÓN DE LA IA",
    "FACT": "HECHOS",
    "AI INTERPRETATION": "INTERPRETACIÓN DE LA IA",
    "NO_DATA — no sourced event facts in this snapshot.": "Sin datos — no hay noticias con fuente en esta foto.",
    "JOURNAL PREVIEW": "VISTA PREVIA DE LA BITÁCORA",
    "No journal events in this snapshot.": "No hay eventos en la bitácora de esta foto.",
    "JOURNAL": "BITÁCORA",
    "In-memory snapshot only · durable persistence deferred": "Solo la foto en memoria",
    "NO_DATA — no journal events in this snapshot.": "Sin datos — no hay eventos en la bitácora.",
    "MARKETS": "MERCADOS",
    "1H context · 15M structure · 5M timing. Bias is context, not an order.":
        "1 h contexto · 15 min estructura · 5 min momento de entrada. La tendencia es contexto, no una orden.",
    "30-DAY AUTONOMOUS DEMO TEST": "PRUEBA DEMO AUTÓNOMA DE 30 DÍAS (OANDA DEMO)",
    "30-DAY DEMO IN PROGRESS": "DEMO DE 30 DÍAS EN CURSO: cuenta de práctica interna desde el 14 oct 2026; el broker demo OANDA cuenta sus propios 30 días desde su primer trade enrutado",
    "The demo runs on the cloud scheduler with durable history. Metrics and the OANDA demo evidence report grow with observed trades.":
        "El demo corre en el programador de la nube con historial duradero. Las métricas y el informe de evidencia del demo OANDA crecen con las operaciones observadas.",
    "SYSTEM": "SISTEMA",
    "Read-only service inventory": "Inventario de servicios · solo lectura",
    "Latest review": "Última revisión",
    "Audit by run ID": "Auditar por ID de ciclo",
    "Run ID not found": "No se encontró ese ID de ciclo",
    "Download run evidence (JSON)": "Descargar evidencia del ciclo (JSON)",
    "Notifications": "Notificaciones",
    "Download review evidence (JSON)": "Descargar evidencia de revisión (JSON)",
    "Review export temporarily unavailable; PAPER execution is unaffected.":
        "La exportación no está disponible por ahora; la cuenta de práctica sigue igual.",
    "Formatted JSON": "Ver datos técnicos (JSON)",
    "Formatted JSON · first 20 displayed records": "Ver datos técnicos (JSON) · primeros 20 registros",
    "What does it mean?": "¿Qué significa?",
}

# Table columns and field labels (data key -> Spanish header). Keys stay untranslated in the data itself.
COLUMNS = {
    "symbol": "Símbolo", "side": "Side", "quantity": "Size", "entry_price": "Entry (fill)",
    "exit_price": "Exit", "stop": "Stop loss (SL)", "target": "Take profit (TP)", "unrealized_pnl": "Unrealized PnL",
    "net_pnl": "Net PnL", "realized_r": "R realized", "close_reason": "Exit reason",
    "exited_at": "Exit time (ET)", "opened_at": "Entry time (ET)", "trade_id": "Trade ID", "position_id": "Position ID",
    "as_of": "Hora (ET)", "run_id": "ID del ciclo", "source": "Origen", "agent_name": "Agente", "status": "Estado",
    "bias": "Bias", "confidence": "Confianza", "reasoning_summary": "Resumen", "reason": "Razón",
    "error": "Error", "entry": "Entry", "risk_reward": "R:R", "planned_entry": "Planned entry",
    "account_id": "Cuenta", "realized_pnl": "Realized PnL", "equity": "Equity", "setup_id": "Setup ID",
    "timestamp": "Hora", "service": "Servicio", "event": "Evento", "details": "Detalles", "experiment": "Experimento",
    "analysis_state": "Estado del análisis", "AI contextual bias": "AI bias", "risk_state": "Riesgo",
    "freshness": "Actualidad de datos", "timestamp_utc": "Hora (UTC)", "type": "Tipo", "severity": "Gravedad",
    "state": "Estado", "setup": "Setup", "open_position": "Open position", "scheduler": "Programador",
    "last_success": "Último ciclo exitoso", "heartbeat": "Última señal de vida", "last_run": "Último ciclo",
    "market_data_provider": "Proveedor de precios", "ai_provider": "Proveedor de IA", "decision": "Decisión",
    "risk_fraction": "Risk per trade (fracción)", "equity_at_decision": "Equity at decision", "capital_at_risk": "Cash at risk",
    "contract_multiplier": "Multiplicador", "Status": "Estado", "Timestamp": "Hora (ET)",
}

# Persisted states/values -> plain Spanish (the original stays visible as a tooltip/small text).
STATUS = {
    "OPEN": "Abierta", "CLOSED": "Cerrada", "PENDING": "Pendiente", "FILLED": "Filled", "CANCELLED": "Cancelada",
    "LONG": "LONG", "SHORT": "SHORT", "BULLISH": "Alcista", "BEARISH": "Bajista",
    "NEUTRAL": "Neutral", "UNKNOWN": "Desconocida", "MIXED": "Mixta",
    "NO_SETUP": "No setup", "WATCH": "Watch", "VALID_SETUP": "Valid setup",
    "PLAN_READY": "Plan listo", "PAPER PLAN READY": "Plan de práctica listo", "AI_CAUTION": "La IA pide cautela",
    "RISK_REJECTED": "Rechazada por riesgo", "APPROVED": "Aprobada", "REJECTED": "Rechazada",
    "NOT CALLED": "No consultado", "NOT_REQUIRED": "No hacía falta", "MISSING": "Sin respuesta",
    "OK": "Bien", "PARTIAL": "Parcial", "ERROR": "Error", "FAILED": "Falló", "COMPLETED": "Completado",
    "RUNNING": "En marcha", "STOPPED": "Detenido", "DISABLED": "Desactivado", "ENABLED": "Activo",
    "SUPPORTED · NOT ENABLED": "Soportado, no activo", "HEALTHY": "Funcionando", "DEGRADED": "Con problemas",
    "UNHEALTHY": "Con fallas", "CURRENT": "Al día", "STALE_DATA": "Datos viejos", "NO_DATA": NO_DATA,
    "LOADING": "Cargando", "EMPTY": "Vacío", "PROVIDER_FAILURE": "Falla del proveedor",
    "PARTIAL_AI_FAILURE": "Falla parcial de la IA", "STATE_INCONSISTENCY": "Estado inconsistente",
    "CHART_ERROR": "Error del gráfico", "DETERMINISTIC": "Reglas fijas", "BLOCK": "Bloquear", "ALLOW": "Permitir",
    "INFO": "Información", "WARNING": "Aviso", "CRITICAL": "Crítico", "DELIVERED": "Entregado",
    "DONE": "Hecho", "WAITING": "En espera", "SKIPPED": "Omitido", "COMPLETE": "Completo",
    "BARS PROVIDED": "Velas disponibles", "SAMPLE / DEMO": "Datos de ejemplo", "OFF": "Apagado", "ON": "Encendido",
}

# Stored close reason -> one plain sentence. Unknown reasons are shown as stored.
CLOSE_REASONS = {
    "STOP": "Stop loss hit (SL)",
    "STOP_HIT": "Stop loss hit (SL)",
    "STOPPED": "Stop loss hit (SL)",
    "TARGET": "Take profit hit (TP)",
    "TIME_EXIT_DAY1_BELOW_0_5R": "Time exit · día 1 < 0.5R",
    "TIME_EXIT_DAY2_STRUCTURE_CHANGE": "Time exit · día 2 · cambio de estructura",
    "TIME_EXIT_DAY2_NY_CLOSE": "Time exit · día 2 · cierre NY",
    "TIME_EXIT_FRIDAY_NY_CLOSE": "Time exit · viernes 16:45 ET (sin exposición de fin de semana)",
    "TIME_EXIT_MAX_HOLD": "Time exit · máximo de días de trading",
    "EXIT_STRUCTURE_CHANGE": "Exit · cambio de estructura",
    "EXIT_REVERSAL_IN_PROFIT": "Exit · reversión en beneficio (setup opuesto)",
    "REVERSAL_IN_PROFIT": "Exit · reversión en beneficio (setup opuesto)",
    "MANUAL_CLOSE": "Exit · cierre manual del owner (botón «Cerrar operación»)",
    "SL_TP_OR_EXTERNAL": "Cerrada en el broker (SL/TP o externa)",
    "EMERGENCY_BUTTON": "Exit · botón de parada de emergencia",
    "EXIT_SESSION": "Exit · fin de sesión",
    "EXIT_HALT": "Exit · halt del sistema",
    "EXIT_DATA": "Exit · datos de mercado inválidos",
    "EXIT_NO_PROVIDER": "Exit · sin proveedor de precios",
    "EXIT_PROVIDER_ERROR": "Exit · error del proveedor de precios",
    "EXIT_EXECUTION_GATE": "Exit · execution gate",
    "EXIT_ERROR": "Exit · error del sistema",
    "SYMBOL_PAUSED": "Exit · símbolo pausado (decisión del owner)",
}

COMPONENTS = {
    "market_data": "Datos de mercado", "ai": "Inteligencia artificial", "scheduler": "Programador",
    "runner": "Motor de análisis", "macro": "Noticias macro", "market_data_provider": "Proveedor de precios",
    "ai_provider": "Proveedor de IA", "last_run": "Último ciclo",
}

STEPS = {
    "Data intake": "Llegada de datos", "Agent outputs": "Opinión de los agentes de IA",
    "Setup reviewer + deterministic setup": "Revisión de la oportunidad",
    "Conflict engine / opposite-setup policy": "Revisión de conflictos con otras operaciones",
    "Planner R:R and plan": "Plan: entrada, stop y meta", "Risk Engine": "Motor de riesgo",
    "Paper Broker entry": "Entrada en la cuenta de práctica", "Paper Broker exit": "Salida de la operación",
    "Final outcome": "Resultado final", "Cycle OHLC bars": "Velas del ciclo",
    "Macro provider/news evidence": "Noticias macro", "Per-cycle planner policy": "Regla del planificador del ciclo",
}

AGENTS = {
    "Structure AI": "IA de estructura", "Liquidity AI": "IA de liquidez", "Macro AI": "IA de noticias (macro)",
    "Setup Reviewer": "Revisor de oportunidades", "Trade Reviewer": "Revisor de operaciones",
    "structure_ai": "IA de estructura", "liquidity_ai": "IA de liquidez", "macro_ai": "IA de noticias (macro)",
    "setup_reviewer_ai": "Revisor de oportunidades", "trade_reviewer_ai": "Revisor de operaciones",
}

SESSIONS = {"LONDON": "Londres", "NEW_YORK": "Nueva York"}

POLICIES = {"V2_PLANNER_POLICY": "Regla del planificador", "V2_OPPOSITE_SETUP_POLICY": "Regla ante señales opuestas"}

TOOLTIPS = {
    "equity": "Equity = balance + unrealized PnL de las open positions.",
    "pnl": "Realized PnL = suma del net PnL de los closed trades.",
    "today": "Realized PnL de los closed trades de hoy (fecha de Nueva York).",
    "win_rate": "Win rate = closed trades con net PnL > 0 / total de closed trades.",
    "drawdown": "Max drawdown = mayor caída pico-valle del realized PnL acumulado.",
    "avg_r": "Avg R (expectancy) = media del R realizado; R = net PnL / riesgo inicial (|entry − SL| × size).",
    "unrealized": "Unrealized PnL de las open positions (mark-to-market).",
    "slippage": "Slippage = diferencia entre la entrada planeada y el fill.",
}

# OANDA demo: "Día N de 30" counter and the daily-loss status card (DEC-5.5a).
DEMO_LENGTH_DAYS = 30
DEMO_NO_TRADES = "Demo OANDA: aún sin operaciones del demo"
DAILY_LOSS_TITLE = "Daily loss limit"
DAILY_LOSS_STATUS = {
    "OK": "OK",
    "TRIGGERED": "TRIGGERED · sin nuevas entradas hasta las 17:00 ET",
    "PENDING": "OK · pending (aún sin evaluar en este trading day)",
    "OFF": "OFF",
    "NO_DATA": "NO DATA",
}
DAILY_LOSS_LABELS = {
    "status": "Estado",
    "loss": "Daily loss (realized + unrealized)",
    "used": "Limit used",
    "day_start_nav": "Start-of-day NAV (17:00 ET)",
    "drawdown": "Max drawdown halt (5 %)",
}
R_UNKNOWN = "—"

MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
DAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS_LONG = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
               "octubre", "noviembre", "diciembre")


def tr(text):
    return TEXT.get(text, text)


def column(key):
    return COLUMNS.get(key, key.replace("_", " "))


def status_text(value):
    if value is None or value == "":
        return MISSING
    return STATUS.get(str(value), str(value))


def close_reason_text(reason):
    if reason is None or reason == "":
        return MISSING
    return CLOSE_REASONS.get(str(reason).upper(), str(reason))


def page_label(key):
    if key in PAGES:
        return PAGES[key]
    return LEGACY_PREFIX + LEGACY_PAGES.get(key, key.title())


def page_key(label):
    """Inverse of page_label (tests and links address pages by their stable key)."""
    for key in (*PAGES, *LEGACY_PAGES):
        if page_label(key) == label:
            return key
    return label


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def money(value, signed=False):
    if not number(value):
        return MISSING
    text = f"${abs(value):,.2f}"
    if signed:
        return ("+" if value > 0 else "−" if value < 0 else "") + text
    return ("−" if value < 0 else "") + text


def tone(value):
    """CSS tone for a gain/loss number."""
    return "pos" if number(value) and value > 0 else "neg" if number(value) and value < 0 else "flat"


def datetime_et(value):
    if not isinstance(value, datetime) or value.tzinfo is None:
        return MISSING
    local = value.astimezone(ET)
    return f"{local.day:02d} {MONTHS[local.month-1]} {local.year} {local:%H:%M} ET"


def long_date_et(value):
    local = value.astimezone(ET)
    return f"{DAYS[local.weekday()]} {local.day} de {MONTHS_LONG[local.month-1]} de {local.year} · {local:%H:%M} ET"


# Metric labels for the KPI tiles (standard trading terminology).
def pnl_since_start(total):
    if not number(total):
        return f"Realized PnL: {MISSING}"
    return "Realized PnL · desde el inicio del historial"


def today_phrase(value):
    if not number(value):
        return f"Today PnL (ET): {MISSING}"
    return "Today PnL (ET)"


def wins_phrase(wins, total):
    if not total:
        return "Closed trades: 0"
    return f"Win rate · {wins}/{total} closed trades"


def drawdown_phrase(value):
    return f"Max drawdown (realized PnL): {money(value) if number(value) else MISSING}"


def r_phrase(value, reason=None):
    if not number(value):
        return f"R: {reason or MISSING}"
    return f"{value:+.2f}R"


def avg_r_phrase(value, reason=None):
    if not number(value):
        return f"Avg R (expectancy): {reason or MISSING}"
    return "Avg R (expectancy)"


def demo_day_text(day, length=DEMO_LENGTH_DAYS):
    """"Día N de 30" (day 1 = NY date of the first routed trade); past the end "Día 30 de 30 – completo"."""
    if not isinstance(day, int) or isinstance(day, bool) or day < 1:
        return DEMO_NO_TRADES
    if day > length:
        return f"Día {length} de {length} – completo"
    return f"Día {day} de {length}"


def daily_loss_rows(view):
    """(label, text) rows of the daily-loss card from ``demo_metrics.daily_loss_view``; unknown amounts -> NO_DATA."""
    view = view or {}
    status = view.get("status") or "NO_DATA"
    rows = [(DAILY_LOSS_LABELS["status"], DAILY_LOSS_STATUS.get(status, status))]
    if status in ("OFF", "NO_DATA"):
        return rows
    fraction = view.get("fraction")
    limit = f"{100 * fraction:.0f} %" if number(fraction) else "2 %"
    loss, used = view.get("loss"), view.get("used_pct")
    rows += [(DAILY_LOSS_LABELS["loss"], money(loss) if number(loss) else NO_DATA),
             (f'{DAILY_LOSS_LABELS["used"]} ({limit})', f"{used:.0f} %" if number(used) else NO_DATA),
             (DAILY_LOSS_LABELS["day_start_nav"],
              money(view.get("day_start_nav")) if number(view.get("day_start_nav")) else NO_DATA),
             (DAILY_LOSS_LABELS["drawdown"], "TRIGGERED" if view.get("drawdown_halted") else "OK")]
    return rows


def tip(text):
    """Small '¿Qué significa?' hint using a native tooltip (no JavaScript)."""
    return (f'<span class="tip" title="{escape(text, quote=True)}" tabindex="0">'
            f'ⓘ {escape(TEXT["What does it mean?"])}</span>')
