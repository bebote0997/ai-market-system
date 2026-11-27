"""Computed responsive CSS checks; run with an existing Playwright environment."""
from pathlib import Path
import unittest


class LiquidPanelChromeTests(unittest.TestCase):
    def test_backdrop_starts_inert_and_content_clears_the_tab_bar(self):
        theme = Path("ui/static/theme.css").read_text(encoding="utf-8")
        glass = Path("ui/static/liquid/glass.css").read_text(encoding="utf-8")
        page = Path("ui/liquid/page.py").read_text(encoding="utf-8")
        nav = Path("ui/nav_menu.py").read_text(encoding="utf-8")
        panel = Path("ui/static/liquid/panel.js").read_text(encoding="utf-8")
        self.assertIn(".aif-backdrop{display:none;pointer-events:none}", theme)
        self.assertIn("pointer-events:auto", theme)
        self.assertIn("body:has(.stApp.aif-nav-open) .stApp > .aif-backdrop", theme)
        self.assertIn(".stApp > .aif-backdrop{display:none !important;pointer-events:none !important}", page)
        self.assertIn("Escape", nav)
        self.assertIn("!(window.innerWidth>=900)", nav)
        self.assertIn("querySelector('.lg')", nav)
        self.assertIn(".lg:not([data-open=\"1\"]) .aif-backdrop{display:none !important;pointer-events:none !important}", glass)
        self.assertIn("pointer-events:auto !important", glass)
        self.assertIn("env(safe-area-inset-bottom)", glass)
        self.assertIn('[data-panel="Riesgo"] > .card + .card{margin-top:14px}', glass)
        self.assertIn('ev.key !== "Escape"', panel)
        self.assertIn("scrollTo(0, 0)", panel)
        self.assertNotIn("<", panel)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright, 'Playwright unavailable; run with existing browser environment')
class MobileLayoutTests(unittest.TestCase):
    def test_phone_tablet_single_column_and_desktop_columns(self):
        css = Path('ui/static/theme.css').read_text()  # system fonts only, no external request
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            for width in (375, 640, 768, 1024):
                with self.subTest(width=width):
                    page.set_viewport_size({'width':width, 'height':900})
                    page.set_content('<style>body{margin:0}[data-testid="stHorizontalBlock"]{display:flex;gap:16px}'
                        '[data-testid="stColumn"]{width:30%;flex:1}.grid{display:grid;grid-template-columns:1fr 1fr 1fr}'
                        + css + '</style><div data-testid="stHorizontalBlock">'
                        + ''.join('<div data-testid="stColumn"><div class="tf"><div class="card">'
                                  + 'long-run-identity-'*20 + '</div></div></div>' for _ in range(3))
                        + '</div><div class="tf"><div class="grid"><div>A</div><div>B</div></div></div>')
                    boxes = page.locator('[data-testid="stColumn"]').evaluate_all(
                        '(els)=>els.map(e=>{let r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width}})')
                    if width <= 768:
                        self.assertTrue(all(abs(b['x']-boxes[0]['x']) < 1 for b in boxes))
                        self.assertTrue(all(abs(b['w']-width) < 1 for b in boxes))
                        self.assertLess(boxes[0]['y'], boxes[1]['y'])
                        self.assertEqual(page.locator('.grid').evaluate('(e)=>getComputedStyle(e).gridTemplateColumns').count(' '), 0)
                    else:
                        self.assertTrue(all(abs(b['y']-boxes[0]['y']) < 1 for b in boxes))
                        self.assertLess(boxes[0]['x'], boxes[1]['x'])
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), width)
            browser.close()
