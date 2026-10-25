"""The product is "OANDA Demo": no user-facing «PAPER» label in the Spanish UI, and the Floor-styled start page."""
import os
from pathlib import Path
import re
import unittest
from unittest.mock import patch

from ui import i18n_es as es
from ui.views import luxury

ROOT = Path(__file__).resolve().parent
WORD = "PAPER"  # case-sensitive: "paper_*" identifiers and English keys are internal


def visible(text):
    text = re.sub(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+", "", text)  # the inline banner bytes
    return text


class SpanishValuesTests(unittest.TestCase):
    def test_i18n_values_never_say_paper(self):
        tables = {name: getattr(es, name) for name in dir(es) if isinstance(getattr(es, name), dict)}
        self.assertIn("TEXT", tables)
        for name, table in tables.items():
            for key, value in table.items():
                if isinstance(value, str):
                    with self.subTest(table=name, key=key):
                        self.assertNotIn(WORD, value)

    def test_page_title_login_heading_and_start_page_labels(self):
        self.assertIn('page_title="AI Trading Floor · OANDA Demo"', (ROOT / "ui/app.py").read_text(encoding="utf-8"))
        self.assertIn('st.title("AI Trading Floor · panel privado · OANDA Demo (práctica)")',
                      (ROOT / "ui/auth.py").read_text(encoding="utf-8"))
        self.assertEqual(luxury.PAGES[0], "Overview")
        self.assertEqual(es.PAGE_TITLES["Overview"], "Inicio")
        self.assertEqual(es.page_key(es.page_label("Overview")), "Overview")
        self.assertEqual(es.tr("PAPER · V2"), "OANDA Demo · V2.1")
        for mode, source in ((False, "práctica interna"), (True, "Cuenta de práctica OANDA Demo")):
            with patch("ui.views.luxury.demo_results_on", return_value=mode):
                for key in ("PAPER · REAL EXECUTION DISABLED", "PAPER · REAL EXECUTION DISABLED · NO REAL MONEY",
                            "PAPER MODE · REAL EXECUTION DISABLED · NO REAL MONEY"):
                    self.assertEqual(es.tr(key), f"{source} · sin dinero real · ejecución real desactivada")
        self.assertEqual(es.tr("Paper trading only · observation only"), "Solo práctica · solo observación")

    def test_safety_uses_effective_results_mode(self):
        for results, routing, demo in (("", "0", False), ("OANDA_DEMO", "0", True), ("", "1", True)):
            with patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": results,
                                         "AI_FLOOR_BROKER_DEMO_ROUTING": routing}):
                label = es.tr("PAPER MODE · REAL EXECUTION DISABLED · NO REAL MONEY")
                self.assertEqual("OANDA Demo" in label, demo)
                self.assertIn("sin dinero real · ejecución real desactivada", label)
                self.assertNotIn(WORD, label)

    def test_hero_subline(self):
        self.assertEqual(luxury.hero_subline(True, 3), "V2.1 · OANDA Demo · Día 3 de 30")
        self.assertEqual(luxury.hero_subline(True, None), "V2.1 · OANDA Demo · Esperando el primer ciclo")
        self.assertEqual(luxury.hero_subline(True, True), "V2.1 · OANDA Demo · Esperando el primer ciclo")
        self.assertNotIn(WORD, luxury.hero_subline(False, None))
        self.assertTrue(luxury.banner_uri().startswith("data:image/png;base64,"))


class RenderedScreensTests(unittest.TestCase):
    def setUp(self):
        import test_dashboard_luxury as tdl
        tdl.setUpModule()
        self.addCleanup(tdl.tearDownModule)
        self.case = tdl.LuxuryDashboardTests("test_empty_new_screens")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)

    def texts(self, app):
        parts = [m.value for m in app.markdown] + [c.value for c in app.caption] + [t.value for t in app.title]
        parts += [m.value for kind in (app.info, app.warning, app.error, app.success) for m in kind]
        return "\n".join(str(p) for p in parts)

    def scan(self):
        app = self.case.app()
        for page in luxury.PAGES:
            with self.subTest(page=page):
                app.radio[0].set_value(page).run()
                self.assertFalse(app.exception, str(app.exception))
                text = visible(self.texts(app))
                self.assertEqual([text[max(0, m.start()-60):m.end()+60] for m in re.finditer(WORD, text)], [])
        return app

    def test_main_screens_without_paper_label(self):
        os.environ.pop("AI_FLOOR_BROKER_DEMO_ROUTING", None)
        self.scan()

    def test_main_screens_without_paper_label_in_oanda_demo_mode(self):
        state = Path(self.case.tmp.name) / "broker_state.db"
        with patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO", "AI_FLOOR_BROKER_DEMO_ROUTING": "1",
                                     "AI_FLOOR_BROKER_STATE_DB_PATH": str(state)}), \
                patch("execution.oanda_readonly.cached_demo_trades", return_value={"closed": [], "open": []}), \
                patch("execution.oanda_readonly.cached_account_view", return_value=None):
            self.scan()
            app = self.case.app()
            content = self.texts(app)
            self.assertIn('class="hero"', content)
            self.assertIn("<h1>Inicio</h1>", content)
            self.assertIn("V2.1 · OANDA Demo · ", content)
            self.assertIn("Cuenta de práctica OANDA Demo · sin dinero real · ejecución real desactivada", content)
            self.assertIn("OANDA Demo · V2.1", content)  # sidebar brand
            self.assertIn("Solo práctica · solo observación", content)

    def test_start_page_is_default_and_opens_the_floor(self):
        app = self.case.app()
        self.assertEqual(app.radio[0].value, "Overview")
        self.assertEqual(app.radio[0].options[0], es.page_label("Overview"))
        button = app.button(key="open-floor")
        self.assertEqual(button.label, luxury.FLOOR_BUTTON)
        button.click().run()
        self.assertEqual(app.radio[0].value, "Floor")


if __name__ == "__main__":
    unittest.main()
