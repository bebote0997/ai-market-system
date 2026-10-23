"""Owner alerts in the Floor style (DEC-ALERTS-REDESIGN-1): Slack Block Kit + dark HTML email + plain text.

Pure builders, no I/O. Every number comes from the caller (OANDA demo trade rows / account reads, see
``runtime.trade_alert_source``); nothing is invented: an absent value reads "sin datos". Three event families:

- ``TradeAlert`` OPENED / CLOSED (TP, SL, time exit, max hold, manual, reversal...) of the routed OANDA demo trade,
- ``DailyAlert``: the OANDA demo daily summary (macro folded in as one line),
- ``system_slack_payload``: one compact line for operational events (no JSON dumps, no PAPER numbers).

Slack: a short message in an attachment with a coloured side bar (LONG green / SHORT red; closed: win green, loss red,
breakeven grey) laid out like the approved mockup: «AI Trading Floor» title, divider, the bold event line (``rich_text``),
two or three ``plain_text`` lines (agent / broker text is never parsed as mrkdwn: no mentions, links or `*_~
formatting), an optional Floor thumbnail (``AI_FLOOR_ALERT_IMAGE_URL``) and URL link buttons only (no interactivity;
the close itself happens on the Floor page, with its own confirmation). Email: table layout + inline CSS (Gmail safe):
title, the isometric Floor banner, a bordered summary box with a 2-column grid and dividers, gold «Por qué», a real
line sparkline (matplotlib PNG), a wide gold «Abrir el Floor» button and the «OANDA Demo · Día N de 30» footer; both
images are inline CID parts of the message (never remote).
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html import escape
import math
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit


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
FLOOR_QUERY = "page=floor"

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
    """Floor page deep link from ``AI_FLOOR_PUBLIC_URL`` (else Render's ``RENDER_EXTERNAL_URL``); https only, no
    credentials / query / fragment, else None (then no buttons). Rebuilt from the parsed parts: ``<path>/?page=floor``."""
    for name in FLOOR_URL_ENV:
        value = str((env or {}).get(name) or "").strip()
        if not value:
            continue
        parts = _https_parts(value)
        if parts is not None:
            return urlunsplit(("https", parts.netloc, parts.path.rstrip("/") + "/", FLOOR_QUERY, ""))
    return None


def _https_parts(value):
    """Parsed parts of a plain https URL (host, optional valid port, path only: no credentials, query, fragment,
    whitespace or control characters), else None. Never raises (a malformed optional URL just means no link)."""
    if not value or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in value) or "?" in value \
            or "#" in value or "\\" in value:
        return None
    try:
        parts = urlsplit(value)
        parts.port  # noqa: B018 - raises ValueError for an invalid port
    except ValueError:
        return None
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password or parts.query:
        return None
    return parts


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
    image_url: str | None = None  # optional Slack thumbnail (``image_url(env)``)

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
    image_url: str | None = None

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
def _plain(text):
    return {"type": "plain_text", "text": text, "emoji": True}


def _buttons(url, close=False):
    """Link buttons (no interactivity), neutral style like the approved mockup; the close confirms on the Floor."""
    if not url:
        return []
    elements = [{"type": "button", "text": {"type": "plain_text", "text": "Ver en el Floor", "emoji": True},
                 "url": url, "action_id": "floor_open"}]
    if close:
        elements.append({"type": "button", "text": {"type": "plain_text", "text": "Cerrar operación", "emoji": True},
                         "url": url, "action_id": "floor_close"})
    return [{"type": "actions", "elements": elements}]


def _bold_line(text):
    """The event line in bold via ``rich_text`` (a structured element: never parsed as mrkdwn)."""
    return {"type": "rich_text", "elements": [{"type": "rich_text_section", "elements": [
        {"type": "text", "text": text[:300], "style": {"bold": True}}]}]}


def _attachment(fallback, color, header, lines, context, url, close=False, image_url=None):
    """Mockup layout: brand title, divider, bold event line, compact plain lines (+ optional Floor thumbnail),
    footer, buttons. Slack can't colour the card background; the coloured side bar carries the direction/result."""
    section = {"type": "section", "text": _plain("\n".join(lines))}
    if image_url:
        section["accessory"] = {"type": "image", "image_url": image_url, "alt_text": BRAND}
    blocks = [{"type": "header", "text": {"type": "plain_text", "text": BRAND, "emoji": True}},
              {"type": "divider"}, _bold_line(header), section,
              {"type": "context", "elements": [_plain(context)]}]
    # No top-level "text": Slack would print it above the attachment (twice the header); ``fallback`` feeds the
    # push notification.
    return {"attachments": [{"color": color, "fallback": fallback, "blocks": blocks + _buttons(url, close)}]}


def payload_text(payload):
    """Every visible text of a Slack payload, one per line (logs/tests; also handles plain ``{"text": ...}``)."""
    out = [payload["text"]] if isinstance(payload.get("text"), str) else []

    def rich(elements):
        for element in elements or ():
            if element.get("type") == "text":
                out.append(element.get("text"))
            rich(element.get("elements"))

    for attachment in payload.get("attachments") or ():
        for block in attachment.get("blocks") or ():
            if block.get("type") == "rich_text":
                rich(block.get("elements"))
                continue
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
                       close=alert.opened, image_url=alert.image_url)


def daily_slack_payload(alert):
    lines = [f"{label}: {value}" for label, value in alert.lines]
    if alert.macro:
        lines.append(f"Macro: {plain(alert.macro)}")
    return _attachment(alert.header(), alert.color, alert.header(), lines, alert.footer(), alert.url,
                       image_url=alert.image_url)


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
# Layout of the approved mockup (right side of the «EJEMPLO» image): title, Floor banner, bordered summary box with a
# 2-column grid and dividers, gold «Por qué», line sparkline, wide gold button, «OANDA Demo · Día N de 30» footer.
# Tables + inline styles only (Gmail / Outlook safe); images are inline CID parts of the same message (never remote).
PAGE, CARD, BORDER, BOX = "#0A1020", "#0F1729", "#26324A", "#0C1424"
SPARK = "#3DDC97"
BANNER_CID = "floor-banner@ai-trading-floor"
CHART_CID = "floor-chart@ai-trading-floor"
ASSETS = Path(__file__).resolve().parent / "assets"
_FONT = "Helvetica,Arial,sans-serif"
_SERIF = "Georgia,'Times New Roman',serif"

_EMAIL = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark">
<title>{title}</title></head>
<body style="margin:0;padding:0;background:{page};">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{page}" style="background:{page};">
<tr><td align="center" style="padding:28px 12px;">
<table role="presentation" width="480" cellpadding="0" cellspacing="0" border="0" bgcolor="{card}"
 style="width:100%;max-width:480px;background:{card};border:1px solid {border};border-radius:12px;">
<tr><td style="padding:22px 22px 14px;font:700 23px/1.3 {font};color:#FFFFFF;">{heading}</td></tr>
<tr><td style="padding:0 22px;">{banner}</td></tr>
<tr><td style="padding:12px 22px 0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{box}"
 style="background:{box};border:1px solid {border};border-radius:8px;border-collapse:separate;">{grid}{why}{chart}
<tr><td colspan="2" style="padding:6px 14px 14px;">{button}</td></tr>
</table></td></tr>
<tr><td align="center" style="padding:14px 22px 18px;font:13px/1.5 {font};color:{muted};">{footer}</td></tr>
</table>
<div style="max-width:480px;padding:10px 4px 0;font:11px/1.5 {font};color:#5F6B80;text-align:center;">
Solo cuenta demo de práctica; sin dinero real. Aviso informativo: no crea ni modifica órdenes.</div>
</td></tr></table></body></html>
"""


def _value(label, value, color=None):
    tint = f"color:{color};" if color else ""
    label = f"{escape(label)} " if label else ""
    return f'{label}<span style="{tint}">{escape(value)}</span>'


def _cell(content, *, left):
    border = f"border-bottom:1px solid {BORDER};" + (f"border-right:1px solid {BORDER};" if left else "")
    return (f'<td width="{"58%" if left else "42%"}" valign="top" style="padding:12px 14px;{border}'
            f'font:16px/1.8 {_FONT};color:{TEXT};">{content}</td>')


def _grid(rows):
    """``rows``: [(left_html, right_html)], rendered as a bordered 2-column grid with dividers."""
    return "".join("<tr>" + _cell(left, left=True) + _cell(right, left=False) + "</tr>" for left, right in rows)


def _img(cid, width, height, alt, style=""):
    return (f'<img src="cid:{cid}" width="{width}" height="{height}" alt="{escape(alt, quote=True)}" '
            f'style="display:block;width:100%;max-width:{width}px;height:auto;border:0;outline:none;{style}">')


def _banner(has_image):
    if has_image:
        return _img(BANNER_CID, 436, 121, BRAND, f"border:1px solid {BORDER};border-radius:8px;"
                    f"font:600 26px/121px {_SERIF};color:{GOLD};background:{NAVY};text-align:center;")
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="{NAVY}" '
            f'style="background:{NAVY};border:1px solid {BORDER};border-radius:8px;"><tr><td style="padding:22px 20px;'
            f'font:600 30px/1.1 {_SERIF};color:{GOLD};">{escape(BRAND)}</td></tr></table>')


def _button(url, label="Abrir el Floor"):
    if not url:
        return ""
    href = escape(url, quote=True)
    return (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>'
            f'<td align="center" bgcolor="{GOLD}" style="background:{GOLD};border-radius:6px;">'
            f'<a href="{href}" style="display:block;padding:13px 20px;font:700 17px/1.2 {_FONT};color:{NAVY};'
            f'text-decoration:none;text-align:center;border-radius:6px;">{escape(label)}</a></td></tr></table>')


def _shell(*, title, heading_html, rows, why, chart, url, footer_text, has_banner, why_label="Por qué"):
    why_html = "" if not why else (
        f'\n<tr><td colspan="2" style="padding:13px 14px 4px;font:16px/1.5 {_FONT};color:{TEXT};">'
        f'<span style="color:{GOLD};font-weight:700;">{escape(why_label)}:</span> {escape(why)}</td></tr>')
    chart_html = "" if not chart else (
        f'\n<tr><td colspan="2" style="padding:8px 14px 10px;">'
        f'<table role="presentation" cellpadding="0" cellspacing="0" border="0" bgcolor="{CARD}" '
        f'style="background:{CARD};border:1px solid {BORDER};border-radius:4px;"><tr><td style="padding:2px;">'
        f'{_img(CHART_CID, 180, 48, "Precio 5m (gráfico)")}</td></tr></table></td></tr>')
    return _EMAIL.format(title=escape(title), page=PAGE, card=CARD, border=BORDER, box=BOX, text=TEXT, muted=MUTED,
                         font=_FONT, heading=heading_html, banner=_banner(has_banner), grid=_grid(rows),
                         why=why_html, chart=chart_html, button=_button(url), footer=escape(footer_text))


_ASSET_CACHE = {}


def _asset(name):
    """Bytes of a packaged image (``runtime/assets``), cached; None when missing (the email then has no image)."""
    if name not in _ASSET_CACHE:
        try:
            _ASSET_CACHE[name] = (ASSETS / name).read_bytes()
        except OSError:
            _ASSET_CACHE[name] = None
    return _ASSET_CACHE[name]


def sparkline_png(closes, color=SPARK, width=180, height=48, scale=2):
    """Real line sparkline (PNG bytes, matplotlib Agg, no pyplot state) of the closes; None without >= 2 values or
    when matplotlib is unavailable / fails (the email then has no chart)."""
    values = [v for v in (_num(c) for c in (closes or ())) if v is not None]
    if len(values) < 2:
        return None
    try:
        from io import BytesIO
        from matplotlib.backends.backend_agg import FigureCanvasAgg
        from matplotlib.figure import Figure
        figure = Figure(figsize=(width / 100, height / 100), dpi=100 * scale, facecolor=CARD)
        FigureCanvasAgg(figure)
        axes = figure.add_axes((0.03, 0.12, 0.94, 0.76))
        axes.set_facecolor(CARD)
        axes.plot(range(len(values)), values, color=color, linewidth=1.2, solid_joinstyle="round")
        low, high = min(values), max(values)
        pad = (high - low) * 0.08 or abs(high) * 1e-6 or 1e-6
        axes.set_xlim(0, len(values) - 1)
        axes.set_ylim(low - pad, high + pad)
        axes.axis("off")
        buffer = BytesIO()
        figure.savefig(buffer, format="png", facecolor=CARD)
        return buffer.getvalue()
    except Exception:  # noqa: BLE001 - a missing chart never blocks the alert
        return None


def _images(chart_png=None):
    banner = _asset("floor_banner.png")
    images = [(BANNER_CID, banner)] if banner else []
    if chart_png:
        images.append((CHART_CID, chart_png))
    return tuple(images)


def _trade_rows(alert):
    rr = rr_ratio(alert.entry, alert.stop, alert.target)
    tp = fmt_price(alert.symbol, alert.target) + (f" ({rr})" if rr else "")
    rows = [(_value("Entrada", fmt_price(alert.symbol, alert.entry)), _value("Riesgo", alert.risk_text())),
            (_value("SL", alert.level("SL", alert.stop)[3:]) + "<br>" + _value("TP", tp),
             _value("", "Sesión") + "<br>" + _value("", session_label(alert.session)))]
    if not alert.opened:
        rows.append((_value("Resultado", f"{alert.r_text} ({money(alert.pnl, alert.currency, signed=True)})",
                            alert.color), _value("Cierre", alert.reason_label)))
    return rows


def _heading(alert):
    verb = "abierta" if alert.opened else "cerrada"
    r_color = TEXT if alert.opened else alert.color
    return (f'{escape(alert.symbol)} {escape(alert.side)} {verb} · '
            f'<span style="color:{r_color};">{escape(alert.r_text)}</span>')


def trade_email(alert):
    """(subject, text, html, images) for an OPENED / CLOSED demo trade; ``images`` = ((cid, png bytes), ...) to attach
    inline (``GmailEmailChannel.send_rendered``)."""
    subject = f"{alert.emoji} {alert.title()} · {VENUE}"
    chart = sparkline_png(alert.closes)
    images = _images(chart)
    html = _shell(title=subject, heading_html=_heading(alert), rows=_trade_rows(alert), why=alert.why_text(),
                  chart=chart is not None, url=alert.url, footer_text=alert.footer(),
                  has_banner=any(cid == BANNER_CID for cid, _ in images))
    lines = [alert.title(), "", alert.levels_line(), alert.risk_line()]
    if not alert.opened:
        lines.append(alert.result_line())
    lines += [f"Por qué: {alert.why_text()}"]
    if alert.url:
        lines += ["", f"Abrir el Floor: {alert.url}"]
    lines += ["", alert.footer()]
    return subject, "\n".join(lines), html, images


def daily_email(alert):
    subject = f"📊 {alert.title()}"
    pairs = [_value(label, value) for label, value in alert.lines]
    if len(pairs) % 2:
        pairs.append("")
    rows = [(pairs[i], pairs[i + 1]) for i in range(0, len(pairs), 2)]
    images = _images()
    html = _shell(title=subject, heading_html=escape(alert.title()), rows=rows,
                  why=plain(alert.macro) if alert.macro else None, chart=False, url=alert.url,
                  footer_text=alert.footer(), has_banner=bool(images), why_label="Macro")
    lines = [alert.title(), ""] + [f"{label}: {value}" for label, value in alert.lines]
    if alert.macro:
        lines.append(f"Macro: {plain(alert.macro)}")
    if alert.url:
        lines += ["", f"Abrir el Floor: {alert.url}"]
    return subject, "\n".join(lines + ["", alert.footer()]), html, images


def image_url(env):
    """Optional public https PNG (``AI_FLOOR_ALERT_IMAGE_URL``) shown as the Slack thumbnail; None (no image) unless
    it is an https URL without credentials / query / fragment ending in ``.png``. Slack rejects a message whose image
    cannot be downloaded, so nothing is derived or defaulted (and ``send_slack`` retries a 400 once without it)."""
    parts = _https_parts(str((env or {}).get("AI_FLOOR_ALERT_IMAGE_URL") or "").strip())
    if parts is not None and parts.path.lower().endswith(".png"):
        return urlunsplit(("https", parts.netloc, parts.path, "", ""))
    return None


__all__ = ["DailyAlert", "TradeAlert", "daily_email", "daily_slack_payload", "floor_url", "fmt_price", "image_url",
           "money", "payload_text", "pips", "rr_ratio", "sparkline_png", "system_slack_payload", "trade_email",
           "trade_slack_payload", "why_line"]
