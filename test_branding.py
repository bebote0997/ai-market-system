"""Dashboard branding is 'AI Trading Floor' (the old 'Mesa de Trading' name is gone from UI sources)."""
from pathlib import Path
import unittest

from ui.i18n_es import tr

ROOT = Path(__file__).resolve().parent


class BrandingTests(unittest.TestCase):
    def test_translated_brand_strings(self):
        self.assertEqual(tr("Trading Floor"), "AI Trading Floor")
        self.assertEqual(tr("AI TRADING FLOOR"), "AI TRADING FLOOR")

    def test_ui_sources_use_new_name_only(self):
        for path in sorted((ROOT / "ui").rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("Mesa de Trading", text, path.name)
            self.assertNotIn("MESA DE TRADING", text, path.name)
        self.assertIn('page_title="AI Trading Floor', (ROOT / "ui/app.py").read_text(encoding="utf-8"))
        self.assertIn('st.title("AI Trading Floor · panel privado', (ROOT / "ui/auth.py").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
