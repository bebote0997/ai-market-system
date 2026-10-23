"""Regenerate ``runtime/assets/floor_banner.png`` (isometric Floor + serif gold «AI Trading Floor») for the alerts.

    python scripts/make_floor_banner.py OUT.png   # needs playwright + Chrome; then quantize to 256 colours (PIL)

Dev tool only (the committed PNG is what the emails attach); not imported by the runtime.
"""
import math
import shutil
import sys
from pathlib import Path

W, H = 1044, 290
C, S = math.cos(math.radians(30)), math.sin(math.radians(30))
OX, OY, U = 735, 102, 41  # origin + unit

def iso(x, y, z=0):
    return (OX + (x - y) * C * U, OY + (x + y) * S * U - z * U)

def poly(pts, **kw):
    attrs = " ".join(f'{k.replace("_", "-")}="{v}"' for k, v in kw.items())
    return f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" {attrs}/>'

def tile(x, y, w, d, z=0):
    return [iso(x, y, z), iso(x + w, y, z), iso(x + w, y + d, z), iso(x, y + d, z)]

def box(x, y, w, d, h, top, left, right, stroke):
    out = []
    out.append(poly([iso(x, y + d, 0), iso(x + w, y + d, 0), iso(x + w, y + d, h), iso(x, y + d, h)], fill=left, stroke=stroke, stroke_width=1))
    out.append(poly([iso(x + w, y, 0), iso(x + w, y + d, 0), iso(x + w, y + d, h), iso(x + w, y, h)], fill=right, stroke=stroke, stroke_width=1))
    out.append(poly(tile(x, y, w, d, h), fill=top, stroke=stroke, stroke_width=1))
    return "".join(out)

parts = []
# floor grid
for i in range(-6, 14):
    a, b = iso(i, -6), iso(i, 14)
    parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="#1f3a5c" stroke-width="1" opacity=".55"/>')
    a, b = iso(-6, i), iso(14, i)
    parts.append(f'<line x1="{a[0]:.1f}" y1="{a[1]:.1f}" x2="{b[0]:.1f}" y2="{b[1]:.1f}" stroke="#1f3a5c" stroke-width="1" opacity=".55"/>')

def room(x, y, w, d, color, dot=None, label=None):
    s = poly(tile(x, y, w, d), fill=color, fill_opacity=".07", stroke=color, stroke_width=2, stroke_opacity=".9")
    s += poly(tile(x + .15, y + .15, w - .3, d - .3), fill="none", stroke=color, stroke_width=1, stroke_opacity=".35")
    if dot:
        px, py = iso(x + dot[0], y + dot[1])
        s += f'<circle cx="{px:.1f}" cy="{py:.1f}" r="9" fill="{color}" opacity=".25"/><circle cx="{px:.1f}" cy="{py:.1f}" r="4.5" fill="{color}"/>'
    return s

parts.append(room(0.2, -2.6, 2.6, 2.4, "#4f8cff", dot=(1.4, .5)))      # top (liquidity, blue)
parts.append(room(-1.6, 3.2, 2.6, 2.4, "#9b6bff", dot=(1.0, 1.0)))     # left (structure, purple)
parts.append(room(4.2, 0.6, 2.8, 2.6, "#ff4d6d", dot=(1.0, .8)))       # right (risk, red)
# chart screens (two panels on the floor near center)
def screen(x, y, w, d, color, path, stroke="#2ec4b6"):
    s = box(x, y, w, d, .12, "#0d1a2c", "#0a1422", "#081120", color)
    pts = []
    for i, v in enumerate(path):
        t = i / (len(path) - 1)
        pts.append(iso(x + .2 + t * (w - .4), y + d * .5 + (v - .5) * d * .7, .12))
    s += f'<polyline points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" fill="none" stroke="{stroke}" stroke-width="2"/>'
    return s
wave = [0.5, 0.3, 0.6, 0.4, 0.7, 0.45, 0.35, 0.55, 0.3, 0.5]
parts.append(screen(1.6, 2.6, 2.2, 1.1, "#2b6fd6", wave))
parts.append(box(2.9, 1.0, 1.4, 1.0, .12, "#121a26", "#0b1119", "#080d14", "#3a4556"))
pts = [iso(3.1 + i * .12, 1.5 + (0.25 if i % 3 == 1 else -0.1) * .6, .12) for i in range(9)]
parts.append(f'<polyline points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in pts)}" fill="none" stroke="#e8e6df" stroke-width="1.6" opacity=".8"/>')
px, py = iso(0.15, 0.95, .95)
parts.append(f'''<g transform="translate({px:.1f},{py:.1f}) scale(1.5)">
<path d="M-15,8 Q-16,-14 0,-16 Q16,-14 15,8 L13,26 L-13,26 Z" fill="#1c2638"/>
<path d="M-4,-15 L0,-4 L4,-15 Z" fill="#e8e6df"/><path d="M-1,-12 L1,-12 L2,0 L0,3 L-2,0Z" fill="#c0283f"/>
<path d="M14,-6 Q26,0 28,8 L24,11 Q18,4 12,2 Z" fill="#1c2638"/>
<circle cx="0" cy="-26" r="10" fill="#e9b996"/><path d="M-10,-28 Q-9,-38 0,-38 Q10,-38 10,-28 Q6,-33 0,-32 Q-6,-33 -10,-28Z" fill="#2a1c14"/>
<rect x="-9" y="26" width="7" height="20" fill="#121a26"/><rect x="2" y="26" width="7" height="20" fill="#121a26"/>
</g>''')
# desk + trader
parts.append(box(-0.2, 0.9, 1.6, .9, .55, "#7a4a24", "#4a2c14", "#5c3619", "#2a180a"))
lx, ly = iso(0.5, 1.1, .55)
parts.append(f'<g transform="translate({lx:.1f},{ly:.1f}) scale(1.3)"><polygon points="-14,-2 12,-10 16,3 -10,11" fill="#b9c3cf" stroke="#6b7684"/>')
parts.append('<polygon points="12,-10 16,3 19,-17 15,-30" fill="#8fd3ff" stroke="#5b6b7c"/></g>')
svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">' \
      f'<defs><linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#0d1830"/><stop offset="1" stop-color="#0a1222"/></linearGradient>' \
      f'<linearGradient id="gold" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f3d77a"/><stop offset="1" stop-color="#c9a13b"/></linearGradient>' \
      f'<linearGradient id="fade" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#0c162b" stop-opacity="1"/><stop offset=".42" stop-color="#0c162b" stop-opacity="1"/><stop offset=".56" stop-color="#0c162b" stop-opacity="0"/></linearGradient></defs>' \
      f'<rect width="{W}" height="{H}" fill="url(#bg)"/>' + "".join(parts) + \
      f'<rect width="{W}" height="{H}" fill="url(#fade)"/>' \
      f'<text x="40" y="128" font-family="Cormorant Garamond, Georgia, serif" font-weight="600" font-size="86" fill="url(#gold)">AI Trading</text>' \
      f'<text x="112" y="232" font-family="Cormorant Garamond, Georgia, serif" font-weight="600" font-size="98" fill="url(#gold)">Floor</text></svg>'
out = Path(sys.argv[1])
out.with_suffix(".svg").write_text(svg)
html = f'<!DOCTYPE html><html><body style="margin:0">{svg}</body></html>'
out.with_suffix(".html").write_text(html)
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    b = p.chromium.launch(**({"executable_path": chrome} if chrome else {}))
    pg = b.new_page(viewport={"width": W, "height": H})
    pg.goto(out.with_suffix(".html").as_uri()); pg.wait_for_timeout(300)
    pg.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": W, "height": H})
    b.close()
print("ok", out)
