"""Pantallas Liquid Glass estáticas. Sin movimiento. Navegación, tema, filtros y CSV quedan en el navegador."""
import base64
from decimal import Decimal
from functools import lru_cache
from html import escape
from pathlib import Path

from ui.liquid.model import MISSING

_ROOT = Path(__file__).resolve().parents[1] / "static" / "liquid"
SECTIONS = ("Inicio", "Posiciones", "Historial", "Riesgo", "Sistema")
RANGES = (("30", "30 Días"), ("90", "90 Días"), ("365", "1 Año"), ("all", "Todo"), ("custom", "Personalizado"))


def _css():
    return (_ROOT / "glass.css").read_text(encoding="utf-8")


@lru_cache(maxsize=4)
def _png(name):
    try:
        raw = (_ROOT / name).read_bytes()
    except OSError:
        return ""
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _mark(kind):
    """Logo 1: blanco sobre azul marino u oscuro; transparente sobre fondo claro."""
    blanco, claro = _png("logo_1_blanco.png"), _png("logo_1_transparente.png")
    if not blanco:
        return "<b>Jt Trading Floor</b>"
    if kind == "navy":
        return f'<img class="mark" src="{blanco}" alt="Jt Trading Floor">'
    src = claro or blanco
    return (f'<img class="mark on-light" src="{src}" alt="Jt Trading Floor">'
            f'<img class="mark on-dark" src="{blanco}" alt="Jt Trading Floor">')


def _script():
    return (_ROOT / "panel.js").read_text(encoding="utf-8")


def _e(value):
    return escape(MISSING if value is None else str(value), quote=True)


def _bar(item):
    if not item or item.get("bar") is None:
        return ""
    width = max(0.0, min(100.0, float(item["bar"])))
    return f'<div class="bar" aria-hidden="true"><i style="width:{width:.1f}%"></i></div>'


def _chart(points):
    values = []
    for point in points:
        try:
            values.append(Decimal(point["equity"]))
        except (ArithmeticError, ValueError, KeyError):
            return ""
    if len(values) < 2:
        return ""
    low, high = min(values), max(values)
    span = high - low or Decimal(1)
    width, height, left, right, top, bottom = 640, 220, 78, 12, 22, 28
    step = Decimal(width - left - right) / Decimal(len(values) - 1)
    coords, last_x, last_y = [], Decimal(left), Decimal(top)
    for index, value in enumerate(values):
        last_x = Decimal(left) + step * Decimal(index)
        last_y = Decimal(top) + (high - value) / span * Decimal(height - top - bottom)
        coords.append(f"{last_x:.1f},{last_y:.1f}")
    floor = height - bottom
    area = f"{left:.1f},{floor:.1f} " + " ".join(coords) + f" {last_x:.1f},{floor:.1f}"
    hi = max(range(len(values)), key=lambda index: values[index])
    lo = min(range(len(values)), key=lambda index: values[index])
    y_hi, y_lo = _e(points[hi].get("y") or values[hi]), _e(points[lo].get("y") or values[lo])
    x0, x1 = _e(points[0].get("axis") or ""), _e(points[-1].get("axis") or "")
    label = _e(points[-1].get("text") or points[-1].get("y") or "")
    label_y = Decimal(14)
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Curva de equity">'
        f'<defs><linearGradient id="lg-area" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="#2563eb" stop-opacity="0.35"/>'
        f'<stop offset="1" stop-color="#2563eb" stop-opacity="0.02"/></linearGradient></defs>'
        f'<polygon points="{area}" fill="url(#lg-area)"/>'
        f'<polyline points="{" ".join(coords)}" fill="none" stroke="var(--lg-chart)" stroke-width="2.5" '
        f'stroke-linecap="round" stroke-linejoin="round"/>'
        + "".join(f'<circle cx="{xy.split(",")[0]}" cy="{xy.split(",")[1]}" r="3.5" fill="var(--lg-chart)"/>' for xy in coords) +
        f'<text class="axis" x="4" y="{top + 4:.0f}">{y_hi}</text>'
        f'<text class="axis" x="4" y="{floor:.0f}">{y_lo}</text>'
        f'<text class="axis" x="{left:.0f}" y="{height - 8}">{x0}</text>'
        f'<text class="axis" x="{last_x:.0f}" y="{height - 8}" text-anchor="end">{x1}</text>'
        f'<text class="chart-label" x="{last_x:.1f}" y="{label_y:.1f}" text-anchor="end">{label}</text>'
        f'</svg>')


def _pill(text, tone):
    klass = tone if tone in ("buy", "sell", "wait", "run", "na", "done") else "na"
    return f'<span class="pill {klass}">{_e(text)}</span>'


def _side(row):
    return _pill(row["side"], row.get("side_tone"))


def _value(text):
    """El número grande y la divisa pequeña. El texto es el mismo."""
    raw = MISSING if text is None else str(text)
    for unit in (" USC", " USD"):
        if raw.endswith(unit):
            return f'{_e(raw[:-len(unit)])}<span class="unit">{unit.strip()}</span>'
    return _e(raw)


def _kpi(label, item):
    tone = item.get("tone") if item.get("tone") in ("pos", "neg") else ""
    minor = f'<div class="minor">{_e(item.get("minor"))}</div>' if item.get("minor") else ""
    return (f'<article class="card kpi"><div class="label">{_e(label)}</div>'
            f'<div class="value {tone}">{_value(item.get("main") or item["text"])}</div>{minor}'
            f'<div class="hint">{_e(item["sub"])}</div></article>')


def _table(headers, rows):
    head = "".join(f"<th{(' class=\"desk-col\"' if desk else '')}>{_e(name)}</th>" for name, desk in headers)
    body = "".join("<tr>" + "".join(cell for cell in row) + "</tr>" for row in rows)
    return f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _td(value, desk=False):
    klass = ' class="desk-col"' if desk else ""
    return f"<td{klass}>{value}</td>"


def _mini(title, pairs):
    lines = "".join(f'<div class="kv-row"><span>{_e(label)}</span><b>{value}</b></div>' for label, value in pairs)
    return f'<article class="mini"><header>{title}</header>{lines}</article>'


def _position_cards(rows):
    cards = []
    for row in rows:
        title = f'{_e(row["symbol"])} {_side(row)}'
        cards.append(_mini(title, (
            ("Lotes", _e(row["lots"])), ("Entrada", _e(row["entry"])), ("Precio actual", _e(row["current"])),
            ("SL", _e(row["sl"])), ("TP", _e(row["tp"])),
            ("P/L flotante", f'<span class="{_e(row["floating_tone"])}">{_e(row["floating"])}</span>'),
            ("Ticket", _e(row["ticket"])), ("Cierre programado", _e(row["close"])),
        )))
    return f'<div class="row-cards">{"".join(cards)}</div>'


def _trade_cards(rows):
    cards = []
    for row in rows:
        if row["symbol"] == MISSING:
            cards.append(f'<article class="mini skip"><span>{_e(row["day"])}</span>'
                         f'<span>Sin operación · {_e(row.get("kind", ""))} · {_e(row.get("state", MISSING))}</span></article>')
            continue
        title = f'{_e(row["day"])} {_e(row["symbol"])} {_side(row)}'
        cards.append(_mini(title, (
            ("Lotes", _e(row["lots"])), ("Entrada", _e(row.get("entry", MISSING))), ("Salida", _e(row["exit_price"])),
            ("Motivo de salida", _e(row["reason"])),
            ("Resultado", f'<span class="{_e(row["pnl_tone"])}">{_e(row["pnl"])}</span>'),
        )))
    return f'<div class="row-cards">{"".join(cards)}</div>'


def _positions_table(rows, trades=False):
    headers = [("PAR", False), ("LADO", False), ("LOTES", False), ("ENTRADA", False), ("PRECIO ACTUAL", False),
               ("SL", False), ("TP", False), ("P/L FLOTANTE", False)]
    if not trades:
        headers += [("TICKET", True), ("CIERRE PROG.", True)]
    built = []
    for row in rows:
        cells = [_td(_e(row["symbol"])), _td(_side(row)), _td(_e(row["lots"])), _td(_e(row["entry"])),
                 _td(_e(row["current"])), _td(_e(row["sl"])), _td(_e(row["tp"])),
                 _td(f'<span class="{_e(row["floating_tone"])}">{_e(row["floating"])}</span>')]
        if not trades:
            cells += [_td(_e(row["ticket"]), True), _td(_e(row["close"]), True)]
        built.append(cells)
    return _table(headers, built) + _position_cards(rows)


def _trade_table(rows, tagged=False):
    headers = ("FECHA", "PAR", "LADO", "LOTES", "ENTRADA", "SALIDA", "MOTIVO DE SALIDA", "RESULTADO USC")
    built = []
    for row in rows:
        attrs = ""
        if tagged:
            pairs = (("epoch", "epoch"), ("fecha", "day"), ("par", "symbol"), ("lado", "side"), ("lotes", "lots"),
                     ("entrada", "entry"), ("salida", "exit_price"), ("motivo", "reason"), ("resultado", "pnl"))
            attrs = " data-trade=\"1\" " + " ".join(f'data-{name}="{_e(row.get(key, MISSING))}"' for name, key in pairs)
        if row["symbol"] == MISSING:
            built.append((attrs + ' class="skip"', f'<td>{_e(row["day"])}</td><td colspan="7">Sin operación · '
                          f'{_e(row.get("kind", ""))} · {_e(row.get("state", MISSING))}</td>'))
            continue
        cells = (_e(row["day"]), _e(row["symbol"]), _side(row), _e(row["lots"]), _e(row.get("entry", MISSING)),
                 _e(row["exit_price"]), _e(row["reason"]),
                 f'<span class="{_e(row["pnl_tone"])}">{_e(row["pnl"])}</span>')
        numeric = (False, False, False, True, True, True, False, True)
        built.append((attrs, "".join(f'<td{" class=\"num\"" if num else ""}>{cell}</td>' for cell, num in zip(cells, numeric))))
    head = "".join(f'<th{" class=\"num\"" if name in ("LOTES", "ENTRADA", "SALIDA", "RESULTADO USC") else ""}>{_e(name)}</th>' for name in headers)
    body = "".join(f"<tr{attrs}>{cells}</tr>" for attrs, cells in built)
    return (f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'
            + _trade_cards(rows))


def _positions_card(view, link=False):
    block = view["positions"]
    if block["state"] == "empty":
        body = (f'<div class="empty"><i aria-hidden="true">▤</i><b>Sin posiciones abiertas</b>'
                f'<span class="hint">Libro: {_e(view["kpis"]["equity"]["sub"])}</span></div>')
    elif block["state"] == "closed":
        body = f'<div class="empty"><i aria-hidden="true">☾</i><b>{_e(block["note"])}</b></div>'
    elif block["state"] != "rows":
        body = f'<p class="na">{_e(MISSING)}</p><p class="hint">{_e(block["note"])}</p>'
    else:
        note = f'<p class="hint">{_e(block["note"])}</p>' if block["note"] else ""
        body = note + _positions_table(block["rows"])
    link_html = '<button type="button" class="linkish" data-nav="Posiciones">Ver todas →</button>' if link else ""
    return f'<article class="card"><h2 class="with-link"><span>Posiciones abiertas</span>{link_html}</h2>{body}</article>'


def _steps(steps, horizontal=False, pills=True):
    klass = "hline" if horizontal else "vline"
    rows = []
    for step in steps:
        mark = _pill(step["state"], step["tone"]) if pills else ""
        rows.append(f'<div class="step"><b>{_e(step["time"])}</b> <span>{_e(step["title"])}</span> {mark}</div>')
    return f'<div class="{klass}">{"".join(rows)}</div>'


def _pairs(pairs):
    rows = "".join(f'<div class="pair"><b>{_e(pair["symbol"])}</b>{_pill(pair["state"], pair["tone"])}</div>' for pair in pairs)
    return f'<div class="pairs">{rows}</div>'


def _strategy_card(view, horizontal=False):
    strategy = view["strategy"]
    steps = strategy["steps"] if horizontal else strategy["steps"][:3]
    body = (f'{_steps(steps, horizontal, pills=horizontal)}'
            f'<div><div class="pair-title">Estado por par</div>{_pairs(strategy["pairs"])}</div>')
    if horizontal:
        return (f'<article class="card"><h2>Estrategia de hoy</h2><p class="hint">{_e(strategy["now"])}</p>{body}</article>')
    return (f'<article class="card strategy"><h2>Estrategia de hoy</h2><p class="hint">{_e(strategy["now"])}</p>'
            f'<div class="split">{body}</div></article>')


def _short(text):
    if isinstance(text, str) and " · " in text:
        return text.split(" · ", 1)[0]
    return text


def _brake_row(title, item):
    cap = _short(item["cap"] if isinstance(item, dict) else item)
    used = item.get("text") if isinstance(item, dict) else ""
    consumo = ""
    if used and used != MISSING:
        consumo = f'<div class="hint">Consumo: {_e(used)}</div>'
    return (f'<div class="brake"><div class="brake-top"><span>{_e(title)}</span><b>{_e(cap)}</b></div>'
            f'{consumo}{_bar(item) if isinstance(item, dict) else ""}</div>')


def _brakes_card(view):
    brakes = view["brakes"]
    trade = brakes.get("trade") or {"cap": brakes["trade_cap"], "text": "", "bar": None}
    rows = (f'{_brake_row("Tope por operación", trade)}'
            f'{_brake_row("Pérdida diaria", brakes["daily"])}'
            f'{_brake_row("Caída máxima", brakes["total"])}')
    shield = (f'<div class="shield"><b>Freno activado: {_e(brakes["halt_badge"])}</b>'
              f'<div class="hint">Motivo: {_e(brakes["reason"])}</div></div>')
    return (f'<article class="card"><h2>Frenos de riesgo</h2>'
            f'<p class="hint">Solo lectura. Estos topes no se cambian desde el panel.</p>'
            f'<p class="hint">BRAKE_STATE: {_e(brakes.get("state_line"))}</p>'
            f'<div class="split brake-split"><div>{rows}</div>{shield}</div></article>')


def _chart_card(view, key, title):
    ranges = view["curve"].get("ranges") or {}
    curve = ranges.get(key) or view["curve"]
    if curve.get("state") != "ok":
        body = f'<p class="na">{_e(MISSING)}</p><p class="hint">No hay una serie de equity guardada en ese rango.</p>'
    else:
        body = _chart(curve["points"])
    hidden = "" if key in ("30", "all") else " hidden"
    attr = f' data-chart="{key}"' if key != "home" else ""
    if key == "home":
        hidden, attr = "", ""
    return f'<div{attr}{hidden}><h2>{_e(title)}</h2>{body}</div>'


def _system_card(view, compact=False):
    system = view["system"]
    if system["state"] != "ok":
        headline, checks = MISSING, f'<p class="na">{_e(MISSING)}</p>'
    else:
        stable = system["status"] == "En línea" and system["scheduler"] == "En marcha" and system["errors_state"] != "rows"
        headline = "Sistema operativo y estable" if stable else system["status"]
        scheduler = "Sí" if system["scheduler"] == "En marcha" else (MISSING if system["scheduler"] == MISSING else "No")
        rows = [("Programador en marcha", scheduler, scheduler == "Sí"), ("Último ciclo", system["last_cycle"], system["last_cycle"] != MISSING),
                ("Conexión Exness", system.get("connection", MISSING), system.get("connection") == "Conectada"),
                ("Último despliegue", system.get("deploy", MISSING), system.get("deploy", MISSING) != MISSING)]
        if not compact:
            rows.append(("Latido", system["heartbeat"], system["heartbeat"] != MISSING))
        checks = "".join(
            f'<div class="check"><span>{_e(label)}</span><b class="{"ok" if good else ""}">{_e(value)}</b></div>'
            for label, value, good in rows)
        if system["errors_state"] == "rows":
            checks += "".join(f'<div class="check"><span>{_e(row["event"])}</span><b>{_e(row["at"])}</b></div>' for row in system["errors"])
        elif system["errors_state"] == "empty":
            checks += '<p class="hint">Sin errores recientes.</p>'
    return f'<article class="card"><h2>Salud del sistema</h2><p class="headline">{_e(headline)}</p>{checks}</article>'


def _config(view):
    brakes = view["brakes"]
    trade = brakes.get("trade") or {"cap": brakes["trade_cap"], "text": "", "bar": None}
    riesgo = (f'{_brake_row("Tope por operación", trade)}'
              f'{_brake_row("Pérdida diaria", brakes["daily"])}{_brake_row("Caída máxima", brakes["total"])}'
              f'<div class="shield"><b>Freno activado: {_e(brakes["halt_badge"])}</b></div>')
    notes = f'<p class="na">{_e(MISSING)}</p><p class="hint">No hay un estado de notificaciones en las lecturas del panel.</p>'
    sistema = (f'<div class="check"><span>Ejecución</span><b>{_e(view["safety_short"])}</b></div>'
               f'<div class="check"><span>Planificador</span><b>{_e(view["system"]["scheduler"])}</b></div>'
               f'<div class="check"><span>Último ciclo</span><b>{_e(view["system"]["last_cycle"])}</b></div>'
               f'<button type="button" class="theme only-mob" data-theme-toggle="1" '
               f'aria-label="Cambiar tema claro u oscuro">Tema</button>')
    tabs = (("riesgo", "Riesgo", riesgo, False), ("avisos", "Notificaciones", notes, True), ("sistema", "Sistema", sistema, True))
    buttons = "".join(f'<button type="button" data-config="{name}"{" aria-current=\"page\"" if not hidden else ""}>{_e(label)}</button>'
                      for name, label, _body, hidden in tabs)
    panels = "".join(f'<div data-config-panel="{name}"{" hidden" if hidden else ""}>{body}</div>'
                     for name, _label, body, hidden in tabs)
    return (f'<article class="card"><h2>Configuración</h2><p class="hint">Solo lectura. No hay cambios que guardar.</p>'
            f'<div class="filters">{buttons}</div>{panels}</article>')


def _kpis(view, keys, kind="five"):
    labels = {"balance": "Balance", "equity": "Equity", "today": "P/L de hoy", "total": "P/L total",
              "cycle": "Día del ciclo", "trades": "Operaciones"}
    return f'<div class="kpis {kind}">{"".join(_kpi(labels[key], view["kpis"][key]) for key in keys)}</div>'


def _inicio(view):
    keys = ("balance", "equity", "today", "cycle", "trades")
    mobile = (f'<div class="only-mob">{_kpis(view, keys, "mob")}'
              f'<div class="stack">{_positions_card(view, link=True)}{_strategy_card(view)}{_brakes_card(view)}'
              f'<article class="card">{_chart_card(view, "home", "Historial")}'
              f'{_trade_table(view["history"]["recent"])}</article>{_system_card(view, compact=True)}</div></div>')
    desktop = (f'<div class="only-desk">{_kpis(view, keys)}'
               f'<div class="row1">{_positions_card(view, link=True)}{_strategy_card(view)}</div>'
               f'<div class="row2"><div class="stack">{_brakes_card(view)}{_system_card(view, compact=True)}</div>'
               f'<article class="card">{_chart_card(view, "home", "Historial")}'
               f'{_trade_table(view["history"]["recent"])}</article></div></div>')
    return mobile + desktop


def _positions_page(view):
    block = view["positions"]
    head = (f'<div class="kpis three">{_kpi("P/L flotante", {"text": block["floating"], "main": block["floating"], "sub": "Suma de las abiertas", "tone": block["floating_tone"], "minor": ""})}'
            f'{_kpi("Operaciones activas", {"text": view["active"], "main": view["active"], "sub": "Libro del panel", "tone": "", "minor": ""})}</div>')
    if block["state"] == "empty":
        body = '<article class="card"><h2>Posiciones</h2><p class="hint">Sin posiciones abiertas.</p></article>'
    elif block["state"] == "closed":
        body = f'<article class="card"><h2>Posiciones</h2><p class="hint">{_e(block["note"])}</p></article>'
    elif block["state"] != "rows":
        body = f'<article class="card"><h2>Posiciones</h2><p class="na">{_e(MISSING)}</p><p class="hint">{_e(block["note"])}</p></article>'
    else:
        cards = []
        for row in block["rows"]:
            fields = (("Par", row["symbol"]), ("Lado", row["side"]), ("Lotes", row["lots"]), ("Entrada", row["entry"]),
                      ("Precio actual", row["current"]), ("SL", row["sl"]), ("TP", row["tp"]),
                      ("P/L flotante", row["floating"]), ("Ticket", row["ticket"]), ("Cierre programado", row["close"]))
            lines = "".join(f'<span>{_e(label)}</span><b>{_e(value)}</b>' for label, value in fields)
            cards.append(f'<article class="card"><h2>{_e(row["symbol"])}</h2><div class="kv">{lines}</div></article>')
        body = "".join(cards)
    return head + body


def _stat(label, value, tone=""):
    return f'<div class="stat"><span>{_e(label)}</span><b class="{_e(tone)}">{_e(value)}</b></div>'


def _history_page(view):
    stats = view["history"]["stats"]
    has_curve = view["curve"].get("state") == "ok"
    ranges = "" if not has_curve else "".join(
        f'<button type="button" data-range="{key}"{" aria-current=\"page\"" if key == "30" else ""}>{_e(label)}</button>'
        for key, label in RANGES)
    charts = "".join(_chart_card(view, key, "Rendimiento") for key in ("30", "90", "365", "all")) if has_curve else (
        f'<p class="na">{_e(MISSING)}</p><p class="hint">No hay una serie de equity guardada.</p>')
    custom = f'<div data-chart="custom" hidden><p class="na">{_e(MISSING)}</p><p class="hint">El rango personalizado filtra la tabla. El gráfico no se inventa.</p></div>'
    table = _trade_table(view["history"]["rows"], tagged=True) if view["history"]["rows"] else '<p class="hint">Sin operaciones cerradas en el registro.</p>'
    resumen = f'<div class="stats">{_stat("Operaciones", stats["count"])}{_stat("Ganadoras", stats["winners"])}{_stat("Perdedoras", stats["losers"])}{_stat("Beneficio neto", stats["net"], stats["net_tone"])}</div>'
    periodo = (f'<div class="stats">{_stat("Operaciones totales", stats["count"])}{_stat("Ganadoras %", stats["win_pct"])}'
               f'{_stat("Perdedoras %", stats["loss_pct"])}{_stat("Ganancia total", stats["gross_win"])}'
               f'{_stat("Pérdida total", stats["gross_loss"])}{_stat("Beneficio neto", stats["net"], stats["net_tone"])}</div>')
    clave = (f'<div class="stats">{_stat("Profit factor", stats["profit_factor"])}{_stat("Drawdown máximo", stats["drawdown"])}'
             f'{_stat("Promedio ganancia", stats["avg_win"])}{_stat("Promedio pérdida", stats["avg_loss"])}</div>')
    return (f'<button type="button" class="back only-mob" data-nav="Inicio">← Historial</button>'
            f'{_kpis(view, ("balance", "equity", "total", "trades"))}'
            f'<article class="card"><h2>Rendimiento</h2><div class="filters">{ranges}</div>'
            f'<label class="hint">Desde <input type="date" data-from aria-label="Desde"></label> '
            f'<label class="hint">Hasta <input type="date" data-to aria-label="Hasta"></label>'
            f'{charts}{custom}</article>'
            f'<article class="card"><h2>Resumen del historial</h2>{resumen}</article>'
            f'<article class="card"><h2>Detalle de operaciones</h2>'
            f'<button type="button" data-export="1">Exportar CSV</button>'
            f'<div class="filters"><span class="hint">Últimos 30 días al abrir · Todos los pares · Todos los lados</span></div>'
            f'{table}<div class="pager"><button type="button" data-prev="1">Anterior</button> '
            f'<span data-page-label>Página 1</span> <button type="button" data-next="1">Siguiente</button></div></article>'
            f'<article class="card"><h2>Estadísticas del periodo</h2>{periodo}</article>'
            f'<article class="card"><h2>Estadísticas clave</h2>{clave}</article>')


def _nav(current):
    marks = {"Inicio": "⌂", "Posiciones": "▤", "Historial": "◷", "Riesgo": "⛨", "Sistema": "⚙"}
    return "".join(
        f'<button type="button" data-nav="{_e(name)}"{" aria-current=\"page\"" if name == current else ""}>'
        f'<i aria-hidden="true">{marks[name]}</i><span>{_e(name)}</span></button>'
        for name in SECTIONS)


def fragment(view, *, section="Inicio", theme=None):
    """One self-contained panel. ``theme`` is only for a labelled screenshot."""
    chosen = section if section in SECTIONS else "Inicio"
    panels = {"Inicio": _inicio(view), "Posiciones": _positions_page(view), "Historial": _history_page(view),
              "Riesgo": _strategy_card(view, horizontal=True) + _brakes_card(view),
              "Sistema": _system_card(view) + _config(view)}
    body = "".join(f'<section data-panel="{_e(name)}"{"" if name == chosen else " hidden"}>{panels[name]}</section>'
                   for name in SECTIONS)
    banner = f'<div class="banner">{_e(view.get("banner"))}</div>' if view.get("banner") else ""
    theme_attr = f' data-theme="{theme}"' if theme in ("light", "dark") else ""
    live = "live" if view.get("live") else "live off"
    nav = _nav(chosen)
    return (
        f'<div class="lg"{theme_attr} data-now="{_e(view.get("now_epoch"))}" data-page="0">'
        f'<style>{_css()}</style>{banner}'
        f'<div class="aif-backdrop" hidden></div>'
        f'<div class="shell"><aside class="side"><div class="logo">'
        f'{_mark("navy")}</div><nav aria-label="Secciones">{nav}</nav>'
        f'<div class="foot"><button type="button" class="theme" data-theme-toggle="1" '
        f'aria-label="Cambiar tema claro u oscuro">Tema</button>'
        f'<b>JT GROUP HOLDINGS</b><span>v2.0.0</span></div></aside><div class="main">'
        f'<header class="mob-bar"><button type="button" class="burger" data-menu="1" aria-label="Abrir menú">☰</button>'
        f'{_mark("navy")}'
        f'<div class="tools"><span class="{live}">{_e(view["safety_short"])}</span></div></header>'
        f'<header class="desk-head"><div class="brand-row">{_mark("page")}'
        f'<p>{_e(view["clock"])} '
        f'<span class="fresh">Datos: {_e(view["kpis"]["equity"]["sub"])}</span></p></div>'
        f'<div class="tools"><span class="{live}">{_e(view["safety_short"])}</span>'
        f'</div></header>{body}'
        f'<p class="hint">Solo lectura. Este panel no cierra operaciones ni cambia frenos.</p>'
        f'</div></div><nav class="tabs" aria-label="Secciones">{nav}</nav><script>{_script()}</script></div>'
    )


def document(view, *, section="Inicio", theme=None):
    """Full page for a screenshot. The Streamlit panel uses ``fragment``."""
    icon = _png("logo_1_48.png")
    favicon = f'<link rel="icon" href="{icon}">' if icon else ""
    locked = fragment(view, section=section, theme=theme)
    if theme in ("light", "dark"):
        locked = locked.replace(' data-now=', f' data-theme-lock="{theme}" data-now=', 1)
    return ("<!DOCTYPE html><html lang=\"es\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"{favicon}<title>Jt Trading Floor</title></head><body style=\"margin:0\">"
            + locked + "</body></html>")
