"""The floating glass menu: real pages, no invented badge, gold accent, collapsed pill and expanded panel."""
import unittest

from ui.nav_menu import SEARCH, SUBTITLE, TITLE, chrome, open_trades_count, section_css, stop_button
from ui.views.luxury import PAGES


class NavChromeTests(unittest.TestCase):
    def test_header_names_the_floor_and_the_search(self):
        html = chrome(None)
        self.assertIn(TITLE, html)
        self.assertIn(SUBTITLE, html)
        self.assertIn(SEARCH, html)
        self.assertIn("aif-expand", html)
        self.assertIn("aif-collapse", html)
        self.assertNotIn("data-open-trades", html)
        self.assertNotIn("http", html.lower())

    def test_open_trade_badge_only_when_the_count_is_a_positive_integer(self):
        self.assertNotIn("data-open-trades", chrome(0))
        self.assertNotIn("data-open-trades", chrome(None))
        marked = chrome(2)
        self.assertIn('data-open-trades="2"', marked)
        self.assertNotIn("data-open-trades", chrome(True))  # a bool is not a count

    def test_sections_follow_the_real_page_order(self):
        css = section_css()
        self.assertIn('content:"PRINCIPAL"', css)
        self.assertIn(f"nth-child({len(PAGES) + 1})", css)
        self.assertIn('content:"MESA"', css)
        self.assertIn(f"nth-child({len(PAGES) + 2})", css)
        self.assertIn('content:"DETALLE"', css)

    def test_browser_script_has_no_raw_less_than(self):
        # A "<" inside the st.html script is parsed as a tag and the script never runs.
        from ui.nav_menu import _SCRIPT
        self.assertNotIn("<", _SCRIPT)

    def test_stop_is_a_button_that_does_not_embed_a_script(self):
        html = stop_button()
        self.assertIn('class="aif-stop"', html)
        self.assertNotIn("<script", html)

    def test_unknown_cache_does_not_invent_a_count(self):
        self.assertIsNone(open_trades_count())


class NavStyleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.css = (Path(__file__).resolve().parent / "ui" / "static" / "theme.css").read_text(encoding="utf-8")

    def test_pill_panel_overlay_and_gold_accent(self):
        css = self.css
        self.assertIn("border-radius:999px!important", css)
        self.assertIn("border-radius:28px!important", css)
        self.assertIn("rgba(255,255,255,.10)", css)
        self.assertIn("blur(22px)", css)
        self.assertIn("inset 3px 0 0 #e8c46f", css)
        self.assertIn(".aif-backdrop", css)
        self.assertIn("body:has(.stApp.aif-nav-open) .aif-backdrop", css)
        self.assertNotIn("#ff5c00", css.lower())
        self.assertIn(".stApp:has(#tf-theme-light) [data-testid=\"stSidebar\"]", css)

    def test_collapsed_rail_does_not_widen_the_page(self):
        self.assertIn("width:76px!important", self.css)
        self.assertIn("padding-left:100px!important", self.css)
        self.assertIn("max-width:100%", self.css)
        self.assertIn("overflow-x:clip", self.css)
