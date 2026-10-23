"""Write sample Floor-style alerts (DEC-ALERTS-REDESIGN-1) as HTML (+ PNG when a renderer exists) for owner review.

    python scripts/render_alert_samples.py [OUTPUT_DIR]   # default /workspace/plans/alert_samples (outside the repo)

Fictitious example values only (no account data, example.com URL). Writes per sample: ``<name>.email.html``,
``<name>.slack.html`` (a Slack-like preview of the Block Kit payload), ``<name>.slack.json``, ``<name>.txt`` and, when
playwright, wkhtmltoimage or a headless Chrome is available, ``<name>.email.png`` / ``<name>.slack.png``. Nothing is
sent anywhere. PNGs are review artefacts: never commit them.
"""
from html import escape
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime import trade_alerts as ta  # noqa: E402

DEFAULT_OUT = Path("/workspace/plans/alert_samples")
URL = "https://floor.example.com/?page=floor"
WHY = ta.why_line({"bias_1h": "bearish", "bos_15m": "bearish", "sweep_5m": True})
CLOSES = tuple(1.1235 - i * 0.00004 + (0.00012 if i % 7 == 3 else 0) for i in range(48))
BASE = dict(symbol="EURUSD", side="SHORT", entry="1.12186", stop="1.12696", target="1.11166", risk_amount=500,
            risk_pct=0.5, session=("NEW_YORK",), why=WHY, demo_day=5, closes=CLOSES, url=URL)


def samples():
    yield "opened_short", ta.TradeAlert(kind="OPENED", **BASE)
    yield "opened_long", ta.TradeAlert(kind="OPENED", **{**BASE, "side": "LONG", "stop": "1.11676",
                                                        "target": "1.13206", "closes": CLOSES[::-1]})
    yield "closed_tp", ta.TradeAlert(kind="CLOSED", close_reason="TP", result_r=2.0, pnl=1000, **BASE)
    yield "closed_sl", ta.TradeAlert(kind="CLOSED", close_reason="SL", result_r=-1.0, pnl=-500, **BASE)
    yield "closed_time_exit", ta.TradeAlert(kind="CLOSED", close_reason="TIME_EXIT_FRIDAY_NY_CLOSE", result_r=0.4,
                                            pnl=200, **BASE)
    yield "closed_max_hold", ta.TradeAlert(kind="CLOSED", close_reason="TIME_EXIT_MAX_HOLD", result_r=-0.3, pnl=-150,
                                           **BASE)
    yield "closed_manual", ta.TradeAlert(kind="CLOSED", close_reason="MANUAL_CLOSE", result_r=0.0, pnl=0, **BASE)
    yield "closed_reversal", ta.TradeAlert(kind="CLOSED", close_reason="REVERSAL_IN_PROFIT", result_r=0.8, pnl=400,
                                           **BASE)
    yield "daily_summary", ta.DailyAlert(
        lines=(("NAV (a las 10:00 ET)", "100,850.00 USD"), ("Balance (a las 10:00 ET)", "100,800.00 USD"),
               ("P&L desde el inicio del demo (a las 10:00 ET)", "+850.00 USD"),
               ("P&L del día de trading", "+1,000.00 USD"), ("Operaciones cerradas en el día", "1 (ganadoras: 1)"),
               ("R medio del día", "2.00"), ("Operaciones abiertas (a las 10:00 ET)", "0")),
        date_label="2026-10-20", macro="Dólar fuerte tras datos de empleo; sin eventos de alto impacto hoy.",
        pnl=1000, demo_day=5, url=URL)


def _mrkdwn(text):
    return escape(text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")).replace("\n", "<br>")


def slack_preview(payload):
    """Approximate Slack rendering of one attachment (dark theme) for screenshots."""
    attachment = payload["attachments"][0]
    parts = []
    for block in attachment["blocks"]:
        if block["type"] == "header":
            parts.append(f'<div style="font:700 17px/1.4 Lato,Helvetica,Arial,sans-serif;color:#f8f8f8;">'
                         f'{escape(block["text"]["text"])}</div>')
        elif block["type"] == "section":
            parts.append(f'<div style="font:15px/1.5 Lato,Helvetica,Arial,sans-serif;color:#d1d2d3;margin-top:6px;">'
                         f'{_mrkdwn(block["text"]["text"])}</div>')
        elif block["type"] == "context":
            parts.append(f'<div style="font:12px/1.5 Lato,Helvetica,Arial,sans-serif;color:#9a9b9e;margin-top:6px;">'
                         f'{_mrkdwn(block["elements"][0]["text"])}</div>')
        elif block["type"] == "actions":
            buttons = "".join(
                f'<span style="display:inline-block;margin:8px 8px 0 0;padding:6px 12px;border:1px solid '
                f'{"#e01e5a" if b.get("style") == "danger" else "#565856"};border-radius:4px;color:'
                f'{"#e01e5a" if b.get("style") == "danger" else "#d1d2d3"};font:700 13px Lato,Helvetica,Arial,'
                f'sans-serif;">{escape(b["text"]["text"])}</span>' for b in block["elements"])
            parts.append(f"<div>{buttons}</div>")
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
            '<body style="margin:0;padding:20px;background:#1a1d21;width:640px;">'
            '<div style="font:700 15px Lato,Helvetica,Arial,sans-serif;color:#f8f8f8;margin-bottom:6px;">'
            'AI Trading Floor <span style="font-weight:400;color:#9a9b9e;font-size:12px;">APP 10:00</span></div>'
            f'<div style="border-left:4px solid {attachment["color"]};padding:4px 0 4px 12px;">{"".join(parts)}</div>'
            '</body></html>')


def _renderer():
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return "playwright"
    except ImportError:
        pass
    if shutil.which("wkhtmltoimage"):
        return "wkhtmltoimage"
    for name in ("google-chrome", "chromium", "chromium-browser"):
        if shutil.which(name):
            return name
    return None


def to_png(renderer, html_path, png_path, width, height):
    if renderer == "playwright":
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch()
            page = browser.new_page(viewport={"width": width, "height": height})
            page.goto(html_path.as_uri())
            page.screenshot(path=str(png_path), full_page=True)
            browser.close()
        return
    if renderer == "wkhtmltoimage":
        cmd = ["wkhtmltoimage", "--quiet", "--width", str(width), str(html_path), str(png_path)]
    else:
        cmd = [renderer, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
               f"--window-size={width},{height}", f"--screenshot={png_path}", html_path.as_uri()]
    subprocess.run(cmd, check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else DEFAULT_OUT
    if out.resolve().is_relative_to(ROOT):
        raise SystemExit("output directory must be outside the repository (PNGs are never committed)")
    out.mkdir(parents=True, exist_ok=True)
    renderer = _renderer()
    written = []
    for name, alert in samples():
        if isinstance(alert, ta.DailyAlert):
            payload, (subject, text, html) = ta.daily_slack_payload(alert), ta.daily_email(alert)
        else:
            payload, (subject, text, html) = ta.trade_slack_payload(alert), ta.trade_email(alert)
        files = {f"{name}.email.html": html, f"{name}.slack.html": slack_preview(payload),
                 f"{name}.slack.json": json.dumps(payload, ensure_ascii=False, indent=2),
                 f"{name}.txt": f"Asunto: {subject}\n\n{text}\n"}
        for filename, content in files.items():
            (out / filename).write_text(content, encoding="utf-8")
            written.append(filename)
        if renderer:
            for kind, size in (("email", (680, 1100)), ("slack", (700, 360))):
                try:
                    to_png(renderer, out / f"{name}.{kind}.html", out / f"{name}.{kind}.png", *size)
                    written.append(f"{name}.{kind}.png")
                except Exception as exc:  # noqa: BLE001 - HTML samples stay usable
                    print(f"PNG failed for {name}.{kind}: {type(exc).__name__}", file=sys.stderr)
    print(f"renderer={renderer or 'none (html only)'} out={out} files={len(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
