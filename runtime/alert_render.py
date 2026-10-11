"""V2 Phase 9: alert rendering — one responsive HTML template plus plain text (F09-T12/T13).

Every caller value is HTML-escaped. No remote images, scripts or tracking. The email embeds a worker-rendered
PNG via CID when available, with the inline SVG sparkline as fallback. Missing closes yield NO_DATA.
"""
from html import escape

from runtime.alert_catalog import display
from runtime.alert_chart import sparkline_svg

BRAND = "AI Trading Floor"
_PRIORITY_COLOR = {"CRITICAL": "#b3261e", "HIGH": "#c9a227", "MEDIUM": "#8a94a6", "LOW": "#4f7a5a"}

_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>{title}</title>
<style>
body{{margin:0;padding:0;background:#0d1117;}}
.wrap{{width:100%;max-width:600px;margin:0 auto;}}
.cell{{padding:12px 16px;border-bottom:1px solid #222b38;font:14px/1.4 Georgia,'Times New Roman',serif;}}
@media only screen and (max-width:480px){{
.cell{{display:block;width:auto!important;padding:8px 12px;}}
.label{{border-bottom:0!important;padding-bottom:0!important;}}
h1{{font-size:18px!important;}}
}}
</style></head>
<body style="margin:0;padding:0;background:#0d1117;color:#e6e1d3;">
<table role="presentation" class="wrap" width="100%" cellpadding="0" cellspacing="0"
 style="max-width:600px;margin:0 auto;background:#121821;border:1px solid #2a3442;">
<tr><td style="padding:20px 16px;border-bottom:2px solid #c9a227;">
<div style="font:600 11px/1.2 Helvetica,Arial,sans-serif;letter-spacing:3px;color:#c9a227;">{brand} · PAPER</div>
<h1 style="margin:8px 0 0;font:400 22px/1.3 Georgia,'Times New Roman',serif;color:#f4efe1;">{label}</h1>
<div style="margin-top:8px;"><span style="display:inline-block;padding:2px 8px;border:1px solid {color};
color:{color};font:600 11px/1.6 Helvetica,Arial,sans-serif;letter-spacing:2px;">{priority}</span></div>
</td></tr>
<tr><td style="padding:0;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0">{rows}</table>
</td></tr>{chart}
<tr><td style="padding:14px 16px;font:11px/1.5 Helvetica,Arial,sans-serif;color:#7d8696;">
PAPER trading only. No real broker and no real money. Informational alert; it does not create,
modify or cancel orders. NO_DATA marks a value that was not available.</td></tr>
</table></body></html>
"""

_CHART = '\n<tr><td style="padding:12px 16px;border-bottom:1px solid #222b38;">{0}</td></tr>'

_ROW = ('<tr><td class="cell label" width="38%" style="color:#9aa3b2;">{0}</td>'
        '<td class="cell" style="color:#f4efe1;">{1}</td></tr>')


def _header(event):
    return (("Event ID", event.event_id), ("Event (UTC)", display(event.occurred_at_utc)),
            ("Run ID", display(event.run_id)), ("Symbol", display(event.symbol)),
            ("Session", display(event.session)), ("Summary", display(event.summary)))


def rows(event):
    """Display rows for an event; trade rows (when the kind carries them) replace duplicate header rows."""
    trade = event.trade_rows()
    trade_labels = {label for label, _ in trade}
    return tuple(row for row in _header(event) if row[0] not in trade_labels) + trade


def subject(event):
    test = "[TEST] " if event.is_test else ""
    return f"{test}[{event.priority}] {BRAND} PAPER — {event.kind.label} — {display(event.symbol)}"


def render_text(event):
    lines = [subject(event), ""]
    lines += [f"{label}: {value}" for label, value in rows(event)]
    lines += ["", "PAPER trading only. Informational alert; it does not create, modify or cancel orders."]
    return "\n".join(lines)


def render_html(event, *, chart_cid=None):
    svg = sparkline_svg(event.closes) if event.kind.trade else None  # generated markup, numbers only
    chart = f'<img src="cid:{escape(chart_cid, quote=True)}" alt="Close price chart" width="480" style="max-width:100%;height:auto;">' if chart_cid else svg
    return _HTML.format(
        title=escape(subject(event)), brand=escape(BRAND), label=escape(event.kind.label),
        priority=escape(event.priority), color=_PRIORITY_COLOR[event.priority],
        rows="".join(_ROW.format(escape(label), escape(value)) for label, value in rows(event)),
        chart=_CHART.format(chart) if chart else "")


__all__ = ["render_html", "render_text", "rows", "subject"]
