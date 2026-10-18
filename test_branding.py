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


class BrokerLabelTests(unittest.TestCase):
    def test_user_visible_broker_label_is_oanda_only(self):
        from execution.account_registry import LABELS, PROVIDER_LABELS, read_control_snapshot
        from ui.views import control
        self.assertEqual(LABELS["broker_demo"], "Broker demo (OANDA)")
        self.assertEqual(set(PROVIDER_LABELS.values()), {"Broker demo (OANDA)"})
        self.assertEqual(control.LABELS["broker_demo"], "Broker demo (OANDA)")
        snapshot = read_control_snapshot("/nonexistent/broker_state.db", env={})
        shown = " ".join(a["label"] for a in snapshot["accounts"]) + " " + " ".join(control.LABELS.values())
        self.assertNotIn("DXtrade", shown)
        self.assertNotIn("DXTRADE", shown.upper())

    def test_ui_sources_have_no_dxtrade_text(self):
        for path in sorted((ROOT / "ui").rglob("*.py")):
            self.assertNotIn("dxtrade", path.read_text(encoding="utf-8").lower(), path.name)


class DemoTextTests(unittest.TestCase):
    def test_experiment_texts_are_30_day_and_not_stale(self):
        for key in ("30-DAY AUTONOMOUS DEMO TEST", "30-DAY DEMO IN PROGRESS"):
            self.assertNotEqual(tr(key), key)
        self.assertIn("30 DÍAS", tr("30-DAY AUTONOMOUS DEMO TEST"))
        source = (ROOT / "ui/views/experiment.py").read_text(encoding="utf-8")
        for stale in ("14-DAY", "NOT STARTED", "No scheduler"):
            self.assertNotIn(stale, source)
        self.assertNotIn("14 DÍAS", (ROOT / "ui/i18n_es.py").read_text(encoding="utf-8"))
