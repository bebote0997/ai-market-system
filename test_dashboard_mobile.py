"""Computed responsive CSS checks; run with an existing Playwright environment."""
from pathlib import Path
import unittest

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@unittest.skipUnless(sync_playwright, 'Playwright unavailable; run with existing browser environment')
class MobileLayoutTests(unittest.TestCase):
    def test_phone_tablet_single_column_and_desktop_columns(self):
        css = Path('ui/static/luxury.css').read_text().split('\n', 1)[1]  # no external font request
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            for width in (375, 640, 768, 1024):
                with self.subTest(width=width):
                    page.set_viewport_size({'width':width, 'height':900})
                    page.set_content('<style>body{margin:0}[data-testid="stHorizontalBlock"]{display:flex;gap:16px}'
                        '[data-testid="stColumn"]{width:30%;flex:1}.grid{display:grid;grid-template-columns:1fr 1fr 1fr}'
                        + css + '</style><div data-testid="stHorizontalBlock">'
                        + ''.join('<div data-testid="stColumn"><div class="lux"><div class="card">'
                                  + 'long-run-identity-'*20 + '</div></div></div>' for _ in range(3))
                        + '</div><div class="lux"><div class="grid"><div>A</div><div>B</div></div></div>')
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
