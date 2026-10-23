"""Write sample Floor-style alerts (DEC-ALERTS-REDESIGN-1) as HTML (+ PNG when a renderer exists) for owner review.

    python scripts/render_alert_samples.py [OUTPUT_DIR] [--mockup IMAGE]   # default /workspace/plans/alert_samples_v2

Fictitious example values only (no account data, example.com URL). Writes per sample: ``<name>.email.html``,
``<name>.slack.html`` (a Slack-like preview of the Block Kit payload), ``<name>.slack.json``, ``<name>.txt`` and, when
playwright, wkhtmltoimage or a headless Chrome is available, ``<name>.email.png`` / ``<name>.slack.png``. Nothing is
sent anywhere. PNGs are review artefacts: never commit them. The email preview resolves the inline ``cid:`` images to
files written next to it. With ``--mockup`` (the approved «EJEMPLO» image: Slack card on the left half, email on the
right half) it also writes ``<name>.compare.email.png`` / ``<name>.compare.slack.png`` (mockup | new) side by side.
"""
from dataclasses import replace
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

DEFAULT_OUT = Path("/workspace/plans/alert_samples_v2")
URL = "https://floor.example.com/?page=floor"
WHY = ta.why_line({"bias_1h": "bearish", "bos_15m": "bearish", "sweep_5m": True})
CLOSES = tuple(1.1235 - i * 0.00004 + 0.00009 * ((i * 7919) % 11 - 5) / 5 for i in range(48))  # example walk
BASE = dict(symbol="EURUSD", side="SHORT", entry="1.12186", stop="1.12696", target="1.11166", risk_amount=500,
            risk_pct=0.5, session=("NEW_YORK",), why=WHY, demo_day=3, closes=CLOSES, url=URL)


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


def _plain(text):
    return escape(text).replace("\n", "<br>")


SLACK_FONT = "Lato,'Helvetica Neue',Helvetica,Arial,sans-serif"


def _rich_text(block):
    out = []
    for section in block.get("elements", ()):
        for element in section.get("elements", ()):
            text = _plain(element.get("text", ""))
            out.append(f"<b>{text}</b>" if element.get("style", {}).get("bold") else text)
    return "".join(out)


def slack_preview(payload, thumb_uri=None):
    """Slack desktop dark-theme rendering of one Block Kit attachment (avatar row, coloured bar, blocks) for review."""
    attachment = payload["attachments"][0]
    parts = []
    for block in attachment["blocks"]:
        kind = block["type"]
        if kind == "header":
            parts.append(f'<div style="font:900 18px/1.33 {SLACK_FONT};color:#F8F8F8;margin:2px 0 6px;">'
                         f'{escape(block["text"]["text"])}</div>')
        elif kind == "divider":
            parts.append('<div style="border-top:1px solid #35373B;margin:6px 0 10px;"></div>')
        elif kind == "rich_text":
            parts.append(f'<div style="font:15px/1.46 {SLACK_FONT};color:#F8F8F8;">{_rich_text(block)}</div>')
        elif kind == "section":
            image = block.get("accessory")
            thumb = "" if not image else (
                f'<img src="{thumb_uri or escape(image["image_url"], quote=True)}" width="72" height="72" '
                'style="border-radius:6px;margin-left:12px;flex:none;">')
            parts.append(f'<div style="display:flex;align-items:flex-start;margin-top:4px;"><div style="flex:1;'
                         f'font:15px/1.6 {SLACK_FONT};color:#D1D2D3;">{_plain(block["text"]["text"])}</div>{thumb}</div>')
        elif kind == "context":
            parts.append(f'<div style="font:12px/1.5 {SLACK_FONT};color:#ABABAD;margin-top:6px;">'
                         f'{_plain(block["elements"][0]["text"])}</div>')
        elif kind == "actions":
            buttons = "".join(
                f'<span style="display:inline-block;margin:10px 8px 0 0;padding:0 12px;height:28px;line-height:28px;'
                f'border:1px solid #545454;border-radius:4px;background:#1A1D21;color:#D1D2D3;'
                f'font:700 13px {SLACK_FONT};">{escape(b["text"]["text"])}</span>' for b in block["elements"])
            parts.append(f"<div>{buttons}</div>")
    avatar = ('<div style="width:36px;height:36px;border-radius:6px;background:#0B1426;border:1px solid #2A3550;'
              'flex:none;display:flex;align-items:center;justify-content:center;font:700 15px Georgia,serif;'
              'color:#D4AF37;">AI</div>')
    return ('<!DOCTYPE html><html><head><meta charset="utf-8"></head>'
            '<body style="margin:0;padding:18px 20px;background:#1A1D21;width:620px;">'
            f'<div style="display:flex;gap:8px;">{avatar}<div style="flex:1;">'
            f'<div style="font:900 15px {SLACK_FONT};color:#F8F8F8;margin-bottom:4px;">AI Trading Floor '
            '<span style="font:700 10px Lato,Arial;color:#ABABAD;background:#2C2D30;padding:1px 3px;border-radius:2px;'
            'vertical-align:2px;">APP</span> <span style="font-weight:400;color:#ABABAD;font-size:12px;">10:00</span>'
            f'</div><div style="border-left:4px solid {attachment["color"]};border-radius:4px 0 0 4px;'
            f'padding:2px 0 4px 12px;">{"".join(parts)}</div></div></div></body></html>')


def email_preview(html, out, name, images):
    """Email HTML for a browser: the inline ``cid:`` images are written next to it and referenced by file name."""
    for cid, png in images:
        filename = f"{name}.{cid.split('@', 1)[0]}.png"
        (out / filename).write_bytes(png)
        html = html.replace(f"cid:{cid}", filename)
    return html


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
            chrome = shutil.which("google-chrome") or shutil.which("chromium")
            browser = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=2)
            page.goto(html_path.as_uri())
            page.wait_for_timeout(200)
            body = page.evaluate("[document.body.scrollWidth, document.body.scrollHeight]")
            page.screenshot(path=str(png_path), clip={"x": 0, "y": 0, "width": min(width, body[0]),
                                                       "height": body[1]})
            browser.close()
        return
    if renderer == "wkhtmltoimage":
        cmd = ["wkhtmltoimage", "--quiet", "--width", str(width), str(html_path), str(png_path)]
    else:
        cmd = [renderer, "--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
               f"--window-size={width},{height}", f"--screenshot={png_path}", html_path.as_uri()]
    subprocess.run(cmd, check=True, timeout=60, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def compare(mockup, new_png, out_png, half):
    """Side-by-side review image: the matching half of the approved mockup | the new rendering, same height."""
    from PIL import Image, ImageDraw
    ref = Image.open(mockup).convert("RGB")
    w, h = ref.size
    ref = ref.crop((0, int(h * 0.25), w // 2, int(h * 0.74)) if half == "left" else (w // 2, 0, w, h))
    new = Image.open(new_png).convert("RGB")
    height = 900
    ref = ref.resize((round(ref.width * height / ref.height), height))
    new = new.resize((round(new.width * height / new.height), height))
    canvas = Image.new("RGB", (ref.width + new.width + 60, height + 70), "#05080F")
    canvas.paste(ref, (20, 50))
    canvas.paste(new, (ref.width + 40, 50))
    draw = ImageDraw.Draw(canvas)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 22)
    except OSError:
        font = None
    draw.text((20, 14), "Mockup aprobado", fill="#D4AF37", font=font)
    draw.text((ref.width + 40, 14), "Nuevo (generado por el código)", fill="#D4AF37", font=font)
    canvas.save(out_png)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    mockup = None
    if "--mockup" in args:
        index = args.index("--mockup")
        mockup = Path(args[index + 1])
        del args[index:index + 2]
    out = Path(args[0]) if args else DEFAULT_OUT
    if out.resolve().is_relative_to(ROOT):
        raise SystemExit("output directory must be outside the repository (PNGs are never committed)")
    out.mkdir(parents=True, exist_ok=True)
    renderer = _renderer()
    written = []
    thumb = (ROOT / "runtime" / "assets" / "floor_thumb.png").as_uri()
    for name, alert in samples():
        if isinstance(alert, ta.DailyAlert):
            payload, (subject, text, html, images) = ta.daily_slack_payload(alert), ta.daily_email(alert)
        else:
            payload, (subject, text, html, images) = ta.trade_slack_payload(alert), ta.trade_email(alert)
        files = {f"{name}.email.html": email_preview(html, out, name, images),
                 f"{name}.slack.html": slack_preview(payload),
                 f"{name}.slack.json": json.dumps(payload, ensure_ascii=False, indent=2),
                 f"{name}.txt": f"Asunto: {subject}\n\n{text}\n"}
        if name == "opened_short":  # optional Floor thumbnail (only when AI_FLOOR_ALERT_IMAGE_URL is set)
            with_thumb = ta.trade_slack_payload(replace(alert, image_url="https://floor.example.com/floor_thumb.png"))
            files[f"{name}.slack_thumb.html"] = slack_preview(with_thumb, thumb_uri=thumb)
        for filename, content in files.items():
            (out / filename).write_text(content, encoding="utf-8")
            written.append(filename)
        if not renderer:
            continue
        kinds = [("email", (560, 900)), ("slack", (660, 300))] + (
            [("slack_thumb", (660, 300))] if f"{name}.slack_thumb.html" in files else [])
        for kind, size in kinds:
            try:
                to_png(renderer, out / f"{name}.{kind}.html", out / f"{name}.{kind}.png", *size)
                written.append(f"{name}.{kind}.png")
            except Exception as exc:  # noqa: BLE001 - HTML samples stay usable
                print(f"PNG failed for {name}.{kind}: {type(exc).__name__}", file=sys.stderr)
                continue
            if mockup and kind in ("email", "slack") and not isinstance(alert, ta.DailyAlert):
                compare(mockup, out / f"{name}.{kind}.png", out / f"{name}.compare.{kind}.png",
                        "right" if kind == "email" else "left")
                written.append(f"{name}.compare.{kind}.png")
    print(f"renderer={renderer or 'none (html only)'} out={out} files={len(written)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
