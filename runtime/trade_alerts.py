"""Owner alerts in the Floor style (DEC-ALERTS-REDESIGN-1): Slack Block Kit + dark HTML email + plain text.

Pure builders, no I/O. Every number comes from the caller (OANDA demo trade rows / account reads, see
``runtime.trade_alert_source``); nothing is invented: an absent value reads "sin datos". Three event families:

- ``TradeAlert`` OPENED / CLOSED (TP, SL, time exit, max hold, manual, reversal...) of the routed OANDA demo trade,
- ``DailyAlert``: the OANDA demo daily summary (macro folded in as one line),
- ``system_slack_payload``: one compact line for operational events (no JSON dumps, no PAPER numbers).

Slack: a short message in an attachment with a coloured side bar (LONG green / SHORT red; closed: win green, loss red,
breakeven grey), a header, two or three mrkdwn lines and URL link buttons only (no interactivity; the close itself
happens on the Floor page, with its own confirmation). Email: table layout + inline CSS (Gmail safe), Floor banner,
2-column grid, «Por qué», a table-bar sparkline (no SVG, no remote image) and a gold «Abrir el Floor» button.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape
import math
import re
from urllib.parse import urlsplit

from runtime.notifications import slack_escape

NAVY, PANEL, LINE = "#0B1426", "#111D33", "#1E2C47"
GOLD, TEAL, TEXT, MUTED = "#D4AF37", "#2EC4B6", "#E8E6DF", "#9AA3B2"
GREEN, RED, GREY = "#2EB67D", "#E01E5A", "#8A94A6"
BRAND = "AI Trading Floor"
VENUE = "OANDA Demo"
NO_DATA = "sin datos"
WHY_LIMIT = 90  # «Por qué»: one plain sentence; parts are dropped whole, never cut mid-word
TEXT_LIMIT = 120  # other one-line texts (daily-summary macro line)
BREAKEVEN_R = 0.05  # |R| below this reads as breakeven
FLOOR_URL_ENV = ("AI_FLOOR_PUBLIC_URL", "RENDER_EXTERNAL_URL")  # first https value wins
FLOOR_PAGE = "/?page=floor"

CLOSE_LABELS = {
    "TP": "Take profit", "TAKE_PROFIT": "Take profit", "SL": "Stop loss", "STOP_LOSS": "Stop loss",
    "SL_TP_OR_EXTERNAL": "SL/TP en el broker",
    "TIME_EXIT_DAY1_BELOW_0_5R": "Salida por tiempo (día 1 < 0.5R)",
    "TIME_EXIT_FRIDAY_NY_CLOSE": "Salida por tiempo (cierre del viernes)",
    "TIME_EXIT_MAX_HOLD": "Tiempo máximo de la operación",
    "TIME_EXIT_DAY2_STRUCTURE_CHANGE": "Salida por tiempo (cambio de estructura)",
    "TIME_EXIT_DAY2_NY_CLOSE": "Salida por tiempo (cierre NY)",
    "FRIDAY_PREFLATTEN": "Cierre previo al fin de semana",
    "MANUAL_CLOSE": "Cierre manual", "REVERSAL_IN_PROFIT": "Reversión en beneficio",
    "EMERGENCY_BUTTON": "Botón de emergencia", "DAILY_LOSS": "Límite de pérdida diaria",
    "DRAWDOWN": "Drawdown máximo",
}
SESSION_LABELS = {"LONDON": "Londres", "NEW_YORK": "Nueva York"}
DIRECTIONS = {"bullish": "alcista", "bearish": "bajista"}
_SPACE = re.compile(r"\s+")
_CODE = re.compile(r"[A-Z0-9_]+")


# -- formatting ----------------------------------------------------------------------------------------------------
def _num(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(Decimal(str(value)))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return out if math.isfinite(out) else None


def price_decimals(symbol):
    symbol = str(symbol or "")
    if symbol.startswith(("XAU", "XAG")):
        return 2
    return 3 if symbol.endswith("JPY") else 5


def fmt_price(symbol, value):
    number = _num(value)
    return NO_DATA if number is None else f"{number:.{price_decimals(symbol)}f}"


def pips(symbol, a, b):
    """Whole pips between two prices for FX pairs; None for metals/indices (no pip convention is invented)."""
    from core.fx_policy import is_fx, pip_size
    a, b = _num(a), _num(b)
    if a is None or b is None or not is_fx(symbol):
        return None
    return round(abs(a - b) / float(pip_size(symbol)))


def rr_ratio(entry, stop, target):
    entry, stop, target = _num(entry), _num(stop), _num(target)
    if None in (entry, stop, target) or entry == stop:
        return None
    ratio = abs(target - entry) / abs(entry - stop)
    return f"1:{ratio:.0f}" if abs(ratio - round(ratio)) < 0.05 else f"1:{ratio:.1f}"


def money(value, currency="USD", signed=False):
    number = _num(value)
    if number is None:
        return NO_DATA
    sign = ("+" if number > 0 else "-" if number < 0 else "") if signed else ("-" if number < 0 else "")
    body = f"{abs(number):,.2f}" if abs(number) % 1 else f"{abs(number):,.0f}"
    return f"{sign}${body}" if currency in (None, "USD") else f"{sign}{body} {currency}"


def fmt_r(value):
    number = _num(value)
    return NO_DATA if number is None else f"{number:+.1f}R"


def session_label(names):
    labels = [SESSION_LABELS.get(name, str(name)) for name in names or ()]
    return " + ".join(labels) if labels else "Fuera de Londres/NY"


def plain(text, limit=TEXT_LIMIT):
    """One plain line: whitespace collapsed, truncated with an ellipsis at ``limit`` characters."""
    line = _SPACE.sub(" ", str(text or "")).strip()
    return line if len(line) <= limit else line[:limit - 1].rstrip(" ,;·.") + "…"


def why_line(facts, limit=WHY_LIMIT):
    """«Por qué» as ONE plain sentence from the structured setup facts of the run (``trade_alert_source.setup_facts``):
    ``sweep_5m`` (bool), ``equal_levels_5m`` (bool), ``bos_15m`` / ``retracement_15m`` / ``bias_1h`` (``bullish`` /
    ``bearish``). E.g. «Barrida de liquidez 5M + ruptura bajista 15M + estructura bajista 1H». Over ``limit`` the 15M
    part, then the 5M part, is dropped whole (no ellipsis). No usable fact -> None («sin datos»)."""
    facts = facts or {}
    def direction(key):
        return DIRECTIONS.get(str(facts.get(key) or "").lower())
    liquidity = ("barrida de liquidez 5M" if facts.get("sweep_5m") else
                 "liquidez en niveles iguales 5M" if facts.get("equal_levels_5m") else None)
    trigger = (f"ruptura {direction('bos_15m')} 15M" if direction("bos_15m") else
               f"retroceso {direction('retracement_15m')} 15M" if direction("retracement_15m") else None)
    context = f"estructura {direction('bias_1h')} 1H" if direction("bias_1h") else None
    for parts in ((liquidity, trigger, context), (liquidity, context), (context,)):
        text = " + ".join(part for part in parts if part)
        if text and len(text) <= limit:
            return text[0].upper() + text[1:]
    return None


def floor_url(env):
    """Floor page deep link from ``AI_FLOOR_PUBLIC_URL`` (else Render's ``RENDER_EXTERNAL_URL``); https only, else
    None (then no buttons)."""
    for name in FLOOR_URL_ENV:
        value = str((env or {}).get(name) or "").strip()
        if not value:
            continue
        parts = urlsplit(value)
        if parts.scheme == "https" and parts.hostname and not parts.username and not parts.query:
            return value.rstrip("/") + FLOOR_PAGE
    return None


# -- events --------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class TradeAlert:
    kind: str  # OPENED | CLOSED
    symbol: str
    side: str
    entry: object = None
    stop: object = None
    target: object = None
    risk_amount: object = None  # |entry - original stop| * units, quote currency (USD for EURUSD)
    risk_pct: object = None  # risk_amount / OANDA NAV * 100
    session: tuple = ()
    why: str | None = None
    close_reason: str | None = None
    result_r: object = None
    pnl: object = None  # realized P&L + financing, account currency
    currency: str | None = "USD"
    demo_day: int | None = None
    demo_length: int = 30
    closes: tuple = ()
    url: str | None = None

    @property
    def opened(self):
        return self.kind == "OPENED"

    @property
    def outcome(self):
        r = _num(self.result_r)
        if r is None:
            pnl = _num(self.pnl)
            r = None if pnl is None else (1.0 if pnl > 0 else -1.0 if pnl < 0 else 0.0)
        if r is None:
            return "UNKNOWN"
        return "BREAKEVEN" if abs(r) < BREAKEVEN_R else "WIN" if r > 0 else "LOSS"

    @property
    def color(self):
        if self.opened:
            return GREEN if self.side == "LONG" else RED
        return {"WIN": GREEN, "LOSS": RED}.get(self.outcome, GREY)

    @property
    def emoji(self):
        if self.opened:
            return "🟢" if self.side == "LONG" else "🔴"
        return {"WIN": "✅", "LOSS": "❌"}.get(self.outcome, "➖")

    @property
    def r_text(self):
        return "+0.0R" if self.opened else fmt_r(self.result_r)

    @property
    def reason_label(self):
        reason = str(self.close_reason or "")
        return CLOSE_LABELS.get(reason) or (reason.replace("_", " ").capitalize() if reason else NO_DATA)

    def header(self):
        if self.opened:
            return f"{self.emoji} {self.symbol} {self.side} abierta · {VENUE}"
        return f"{self.emoji} {self.symbol} {self.side} cerrada · {self.reason_label} · {self.r_text} · {VENUE}"

    def title(self):
        verb = "abierta" if self.opened else "cerrada"
        return f"{self.symbol} {self.side} {verb} · {self.r_text}"

    def level(self, label, value):
        text = f"{label} {fmt_price(self.symbol, value)}"
        distance = pips(self.symbol, self.entry, value)
        return text if distance is None or _num(value) is None else f"{text} ({distance} pips)"

    def levels_line(self):
        rr = rr_ratio(self.entry, self.stop, self.target)
        target = f"TP {fmt_price(self.symbol, self.target)}" + (f" ({rr})" if rr else "")
        return f"Entrada {fmt_price(self.symbol, self.entry)} · {self.level('SL', self.stop)} · {target}"

    def risk_text(self):
        pct, amount = _num(self.risk_pct), _num(self.risk_amount)
        if pct is None and amount is None:
            return NO_DATA
        if pct is None:
            return money(amount)
        return f"{pct:.2g}%" + (f" ({money(amount)})" if amount is not None else "")

    def risk_line(self):
        return f"Riesgo {self.risk_text()} · Sesión {session_label(self.session)}"

    def result_line(self):
        return f"Resultado {self.r_text} ({money(self.pnl, self.currency, signed=True)}) · {self.reason_label}"

    def why_text(self):
        return self.why or NO_DATA

    def footer(self):
        return footer(self.demo_day, self.demo_length)


@dataclass(frozen=True)
class DailyAlert:
    lines: tuple  # (label, value) pairs from the OANDA demo snapshot
    date_label: str | None = None
    macro: str | None = None
    pnl: object = None
    demo_day: int | None = None
    demo_length: int = 30
    url: str | None = None

    @property
    def color(self):
        pnl = _num(self.pnl)
        return GOLD if pnl is None or pnl == 0 else GREEN if pnl > 0 else RED

    def header(self):
        return f"📊 Resumen diario · {VENUE}" + (f" · {self.date_label}" if self.date_label else "")

    def title(self):
        return "Resumen diario · " + VENUE + (f" · {self.date_label}" if self.date_label else "")

    def footer(self):
        return footer(self.demo_day, self.demo_length)


def footer(day, length=30):
    return f"{VENUE} · Día {day} de {length}" if isinstance(day, int) and day > 0 else f"{VENUE} · Día {NO_DATA}"


# -- Slack ---------------------------------------------------------------------------------------------------------
def _mrkdwn(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _buttons(url, close=False):
    if not url:
        return []
    elements = [{"type": "button", "text": {"type": "plain_text", "text": "Ver en el Floor", "emoji": True},
                 "url": url, "action_id": "floor_open"}]
    if close:
        elements.append({"type": "button", "text": {"type": "plain_text", "text": "Cerrar operación", "emoji": True},
                         "url": url, "style": "danger", "action_id": "floor_close"})
    return [{"type": "actions", "elements": elements}]


def _attachment(fallback, color, header, lines, context, url, close=False):
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": header[:150], "emoji": True}},
              _mrkdwn("\n".join(slack_escape(line) for line in lines)),
              {"type": "context", "elements": [{"type": "mrkdwn", "text": slack_escape(context)}]}]
    # No top-level "text": Slack would print it above the attachment (twice the header); ``fallback`` feeds the
    # push notification.
    return {"attachments": [{"color": color, "fallback": fallback, "blocks": blocks + _buttons(url, close)}]}


def payload_text(payload):
    """Every visible text of a Slack payload, one per line (logs/tests; also handles plain ``{"text": ...}``)."""
    out = [payload["text"]] if isinstance(payload.get("text"), str) else []
    for attachment in payload.get("attachments") or ():
        for block in attachment.get("blocks") or ():
            if "text" in block:
                out.append(block["text"]["text"])
            for element in block.get("elements") or ():
                text = element.get("text")
                out.append(text["text"] if isinstance(text, dict) else text)
    return "\n".join(str(item) for item in out if item)


def trade_slack_payload(alert):
    """Short Block Kit message (attachment with coloured side bar) for an OPENED / CLOSED demo trade."""
    lines = [alert.levels_line()]
    if alert.opened:
        lines.append(alert.risk_line())
    else:
        lines += [alert.result_line(), f"Riesgo {alert.risk_text()} · Sesión {session_label(alert.session)}"]
    lines.append(f"Por qué: {alert.why_text()}")
    return _attachment(alert.header(), alert.color, alert.header(), lines, alert.footer(), alert.url,
                       close=alert.opened)


def daily_slack_payload(alert):
    lines = [f"{label}: {value}" for label, value in alert.lines]
    if alert.macro:
        lines.append(f"Macro: {plain(alert.macro)}")
    return _attachment(alert.header(), alert.color, alert.header(), lines, alert.footer(), alert.url)


SYSTEM_LABELS = {
    "RUN_FAILED": "Ciclo fallido", "STATE_INCONSISTENCY": "Estado inconsistente",
    "RECOVERY_STARTED": "Recuperación iniciada", "RECOVERY_COMPLETED": "Recuperación completada",
    "PROVIDER_FAILURE": "Fallo del proveedor de datos", "AUTH_ERROR": "Error de autenticación del proveedor",
    "ENTITLEMENT_ERROR": "Proveedor sin permisos", "RATE_LIMITED": "Proveedor limitado (rate limit)",
    "AI_PROVIDER_FAILED": "Fallo del proveedor de IA", "AI_PROVIDER_RECOVERED": "Proveedor de IA recuperado",
    "SYSTEM_DEGRADED": "Sistema degradado", "SYSTEM_CRITICAL": "Sistema crítico", "RECOVERY": "Recuperado",
    "TEST_ALERT": "Alerta de prueba",
}
_CRITICAL = {"STATE_INCONSISTENCY", "SYSTEM_CRITICAL"}
_OK = {"RECOVERY_COMPLETED", "RECOVERY", "AI_PROVIDER_RECOVERED", "TEST_ALERT"}


def system_slack_payload(event_type, symbol=None, detail=None, url=None):
    """One compact line for an operational event; ``detail`` is a short code-like reason (never a JSON dump)."""
    event_type = str(event_type)
    label = SYSTEM_LABELS.get(event_type) or (
        event_type.replace("_", " ").capitalize() if _CODE.fullmatch(event_type) else event_type)
    icon = "⛔" if event_type in _CRITICAL else "✅" if event_type in _OK else "⚠️"
    header = f"{icon} {label}" + (f" · {symbol}" if symbol else "")
    color = RED if event_type in _CRITICAL else GREEN if event_type in _OK else GOLD
    lines = [f"Detalle: {plain(detail, 160)}"] if detail else [f"Evento: {event_type}"]
    return _attachment(header, color, header, lines, f"{BRAND} · {VENUE}", url)


# -- email ---------------------------------------------------------------------------------------------------------
_EMAIL = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:{navy};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{navy}" style="background:{navy};">
<tr><td align="center" style="padding:24px 12px;">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0" bgcolor="{panel}"
 style="width:100%;max-width:600px;background:{panel};border:1px solid {line};border-top:3px solid {accent};">
<tr><td style="padding:22px 24px 14px;border-bottom:1px solid {line};">
<div style="font:400 24px/1.2 Georgia,'Times New Roman',serif;color:{gold};letter-spacing:1px;">{brand}</div>
<div style="font:600 10px/1.6 Helvetica,Arial,sans-serif;color:{teal};letter-spacing:3px;text-transform:uppercase;">{venue}</div>
</td></tr>
<tr><td style="padding:20px 24px 6px;">
<div style="font:600 20px/1.3 Helvetica,Arial,sans-serif;color:{text};"><span style="color:{accent};">&#9679;</span> {heading}</div>
{subtitle}</td></tr>
<tr><td style="padding:10px 24px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{grid}</table>
</td></tr>{why}{chart}
<tr><td align="left" style="padding:18px 24px 22px;">{button}</td></tr>
<tr><td style="padding:12px 24px;border-top:1px solid {line};font:11px/1.5 Helvetica,Arial,sans-serif;color:{muted};">
{footer} · Solo cuenta demo de práctica; sin dinero real. Aviso informativo: no crea ni modifica órdenes.</td></tr>
</table></td></tr></table></body></html>
"""
_CELL = ('<td width="50%" valign="top" style="padding:8px 8px 8px 0;border-bottom:1px solid {line};">'
         '<div style="font:600 10px/1.4 Helvetica,Arial,sans-serif;color:{muted};letter-spacing:2px;'
         'text-transform:uppercase;">{label}</div>'
         '<div style="font:600 15px/1.4 Helvetica,Arial,sans-serif;color:{text};">{value}</div></td>')


def _grid(cells):
    cells = list(cells)
    if len(cells) % 2:
        cells.append(("", ""))
    rows = []
    for i in range(0, len(cells), 2):
        rows.append("<tr>" + "".join(_CELL.format(line=LINE, muted=MUTED, text=TEXT, label=escape(label),
                                                  value=escape(value)) for label, value in cells[i:i + 2]) + "</tr>")
    return "".join(rows)


def bars_html(closes, color=GOLD, height=48, max_bars=48):
    """Gmail-safe sparkline: one table row of bottom-aligned bars (no SVG, no remote image); None without data."""
    values = [v for v in (_num(c) for c in (closes or ())) if v is not None][-max_bars:]
    if len(values) < 2:
        return None
    low, high = min(values), max(values)
    span = (high - low) or 1.0
    cells = "".join(
        f'<td valign="bottom" style="padding:0 1px;"><div style="width:6px;height:{2 + round((v - low) / span * (height - 2))}px;'
        f'background:{color};line-height:1px;font-size:1px;">&nbsp;</div></td>' for v in values)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" height="{height}" '
            f'style="height:{height}px;"><tr>{cells}</tr></table>')


def _button(url, label="Abrir el Floor"):
    if not url:
        return ""
    href = escape(url, quote=True)
    return (f'<table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td bgcolor="{GOLD}" style="background:{GOLD};border-radius:3px;">'
            f'<a href="{href}" style="display:inline-block;padding:11px 22px;font:700 13px/1 Helvetica,Arial,'
            f'sans-serif;color:{NAVY};text-decoration:none;letter-spacing:1px;">{escape(label)}</a></td></tr></table>')


def _shell(*, title, accent, heading, subtitle, grid, why, chart, url, footer_text, why_label="Por qué"):
    why_html = "" if not why else (
        f'\n<tr><td style="padding:12px 24px 4px;font:14px/1.5 Helvetica,Arial,sans-serif;color:{TEXT};">'
        f'<span style="color:{GOLD};font-weight:700;">{escape(why_label)}:</span> {escape(why)}</td></tr>')
    chart_html = "" if not chart else (
        f'\n<tr><td style="padding:14px 24px 0;"><div style="font:600 10px/1.4 Helvetica,Arial,sans-serif;'
        f'color:{MUTED};letter-spacing:2px;text-transform:uppercase;padding-bottom:6px;">Precio (5m)</div>{chart}</td></tr>')
    sub = "" if not subtitle else (
        f'<div style="font:13px/1.5 Helvetica,Arial,sans-serif;color:{MUTED};padding-top:4px;">{escape(subtitle)}</div>')
    return _EMAIL.format(title=escape(title), navy=NAVY, panel=PANEL, line=LINE, accent=accent, gold=GOLD, teal=TEAL,
                         text=TEXT, muted=MUTED, brand=escape(BRAND), venue=escape(VENUE), heading=escape(heading),
                         subtitle=sub, grid=grid, why=why_html, chart=chart_html, button=_button(url),
                         footer=escape(footer_text))


def _trade_cells(alert):
    rr = rr_ratio(alert.entry, alert.stop, alert.target)
    cells = [("Entrada", fmt_price(alert.symbol, alert.entry)), ("Riesgo", alert.risk_text()),
             ("SL", alert.level("SL", alert.stop)[3:]), ("Sesión", session_label(alert.session)),
             ("TP", fmt_price(alert.symbol, alert.target) + (f" ({rr})" if rr else ""))]
    if not alert.opened:
        cells += [("Resultado", f"{alert.r_text} ({money(alert.pnl, alert.currency, signed=True)})"),
                  ("Motivo de cierre", alert.reason_label)]
    return cells


def trade_email(alert):
    """(subject, text, html) for an OPENED / CLOSED demo trade."""
    subject = f"{alert.emoji} {alert.title()} · {VENUE}"
    subtitle = None if alert.opened else f"{alert.reason_label} · {money(alert.pnl, alert.currency, signed=True)}"
    html = _shell(title=subject, accent=alert.color, heading=alert.title(), subtitle=subtitle,
                  grid=_grid(_trade_cells(alert)), why=alert.why_text(), chart=bars_html(alert.closes),
                  url=alert.url, footer_text=alert.footer())
    lines = [alert.title(), "", alert.levels_line(), alert.risk_line()]
    if not alert.opened:
        lines.append(alert.result_line())
    lines += [f"Por qué: {alert.why_text()}"]
    if alert.url:
        lines += ["", f"Abrir el Floor: {alert.url}"]
    lines += ["", alert.footer()]
    return subject, "\n".join(lines), html


def daily_email(alert):
    subject = f"📊 {alert.title()}"
    html = _shell(title=subject, accent=alert.color, heading=alert.title(), subtitle=None,
                  grid=_grid(alert.lines), why=plain(alert.macro) if alert.macro else None, chart=None,
                  url=alert.url, footer_text=alert.footer(), why_label="Macro")
    lines = [alert.title(), ""] + [f"{label}: {value}" for label, value in alert.lines]
    if alert.macro:
        lines.append(f"Macro: {plain(alert.macro)}")
    if alert.url:
        lines += ["", f"Abrir el Floor: {alert.url}"]
    return subject, "\n".join(lines + ["", alert.footer()]), html


__all__ = ["DailyAlert", "TradeAlert", "bars_html", "daily_email", "daily_slack_payload", "floor_url", "fmt_price",
           "money", "payload_text", "pips", "rr_ratio", "system_slack_payload", "trade_email", "trade_slack_payload", "why_line"]
