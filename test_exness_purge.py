"""Paper purge is a confirmed one-off. The Exness cycle does not write paper for the combo symbols."""
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from runtime.exness_purge import CONFIRM, apply_purge, main, plan
from runtime.service import OperationalRuntime


class PurgeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.db = self.root / "trading_floor.db"
        con = sqlite3.connect(self.db)
        con.execute("CREATE TABLE paper_accounts (account_id TEXT, payload TEXT)")
        con.execute("CREATE TABLE paper_orders (order_id TEXT, payload TEXT)")
        con.execute("CREATE TABLE paper_fills (fill_id TEXT, payload TEXT)")
        con.execute("CREATE TABLE paper_positions (position_id TEXT, payload TEXT)")
        con.execute("CREATE TABLE closed_trades (trade_id TEXT, payload TEXT)")
        con.execute("INSERT INTO paper_accounts VALUES ('paper', '{\"equity\": 1}')")
        con.execute("INSERT INTO paper_positions VALUES ('p1', '{\"symbol\": \"EURUSD\"}')")
        con.commit()
        con.close()

    def test_dry_run_copies_nothing_and_deletes_nothing(self):
        report = plan(self.db)
        self.assertTrue(report["dry_run"])
        self.assertEqual(report["counts"]["paper_accounts"], 1)
        self.assertFalse(report["vacuum"])
        self.assertEqual(list(self.root.iterdir()), [self.db])
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(main(["--db", str(self.db)]), 0)
        self.assertEqual(sqlite3.connect(self.db).execute("SELECT COUNT(*) FROM paper_accounts").fetchone()[0], 1)

    def test_apply_without_confirm_does_not_delete(self):
        report = apply_purge(self.db, confirm="no", backup_dir=self.root / "bak")
        self.assertFalse(report["deleted"])
        self.assertEqual(sqlite3.connect(self.db).execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0], 1)
        self.assertFalse((self.root / "bak").exists())

    def test_apply_backs_up_then_deletes_and_vacuums(self):
        report = apply_purge(self.db, confirm=CONFIRM, backup_dir=self.root / "bak")
        self.assertTrue(report["deleted"])
        self.assertTrue(report["vacuum"])
        backup = Path(report["backup"])
        self.assertTrue(backup.is_file())
        self.assertEqual(sqlite3.connect(backup).execute("SELECT COUNT(*) FROM paper_accounts").fetchone()[0], 1)
        paper = backup.with_name(backup.name.replace(".db", ".paper.json"))
        saved = json.loads(paper.read_text())
        self.assertEqual(len(saved["tables"]["paper_positions"]), 1)
        live = sqlite3.connect(self.db)
        self.assertEqual(live.execute("SELECT COUNT(*) FROM paper_accounts").fetchone()[0], 0)
        self.assertEqual(live.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0], 0)

    def test_purge_is_not_wired_into_startup_or_deploy(self):
        root = Path(__file__).resolve().parent
        for name in ("runtime/cloud.py", "runtime/cloud_runner.py", "runtime/service.py", "render.yaml"):
            text = (root / name).read_text(encoding="utf-8") if (root / name).is_file() else ""
            self.assertNotIn("exness_purge", text, name)
            self.assertNotIn("apply_purge", text, name)


class LiveCycleTests(unittest.TestCase):
    def test_combo_module_does_not_use_paper_or_oanda(self):
        text = Path("execution/session_combo.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("save_paper", text)
        self.assertNotIn("oanda", text)
        self.assertNotIn("paper_broker", text)

    def test_eurusd_catch_up_and_guarded_write_do_not_touch_paper_when_exness(self):
        runtime = OperationalRuntime.__new__(OperationalRuntime)
        runtime.exness_session = object()
        runtime._broker = lambda symbol: ("broker", symbol)
        with patch("runtime.service.catch_up_position", side_effect=AssertionError("paper catch-up")):
            self.assertIsNone(runtime._catch_up_positions("EURUSD", None, None))
            self.assertIsNone(runtime._catch_up_positions("GBPUSD", None, None))
        self.assertEqual(runtime._guarded_paper_write("USDJPY", "k", lambda _b: (True, "wrote")),
                         (("broker", "USDJPY"), None))
        runtime.exness_session = None
        with patch.dict(os.environ, {"AI_FLOOR_BROKER": "oanda"}):
            self.assertFalse(runtime._exness_live_symbol("EURUSD"))
            self.assertFalse(runtime._exness_live_symbol("XAUUSD"))


if __name__ == "__main__":
    unittest.main()
