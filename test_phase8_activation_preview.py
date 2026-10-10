"""V2 Phase 8 / P8.4 R3: read-only activation preview (replay/activation_preview.py) on temporary databases."""
import json
import shutil
import sqlite3
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from execution.contracts import PaperOrder
from replay import activation_preview
from replay.activation_preview import main, preview
from storage.database import Store
from test_phase8_activation_simulation import S1, S2, bar
from test_phase8_catch_up_certification import FLAT, OPEN, Harness


def bars_json(path, history, until):
    rows, stamp = [], OPEN
    while stamp + timedelta(minutes=5) <= until:
        o, h, l, c = history.get(("XAUUSD", stamp), FLAT)
        rows.append({"symbol": "XAUUSD", "bar_start": stamp.isoformat(), "open": o, "high": h, "low": l, "close": c})
        stamp += timedelta(minutes=5)
    Path(path).write_text(json.dumps(rows), encoding="utf-8")
    return path


class PreviewTests(Harness):
    def files(self):
        return {p.name: activation_preview.sha256(p) for p in Path(self.tmp.name).iterdir() if p.is_file()}

    def test_preview_matches_the_real_first_activation_cycle(self):
        """Touch after the flag-OFF watermark: the preview predicts the close; running the real flag-ON cycle on a
        SEPARATE copy produces exactly that close; the previewed sources are untouched."""
        self.seed()
        history = {("XAUUSD", bar(6)): (100.0, 100.4, 94.5, 95.5)}
        self.cycle(S1, history, flag=False)
        bars = bars_json(Path(self.tmp.name) / "bars.json", history, S2)
        before = self.files()
        report = preview(self.db, as_of=S2, bars_json=bars)
        self.assertEqual(self.files(), before)  # nothing in the source directory changed
        self.assertTrue(report["source_unchanged"])
        position = report["positions"][0]
        self.assertEqual((position["watermark"], position["catch_up_bars"]),
                         (bar(4).isoformat(), [bar(i).isoformat() for i in (5, 6, 7)]))
        self.assertIsNone(position["historic_missed"])
        expected = position["expected_close"]
        self.assertEqual((expected["bar_start"], Decimal(expected["exit_price"]), expected["reason"]),
                         (bar(6).isoformat(), Decimal(95), "stop"))
        copy = Path(self.tmp.name) / "trading_copy_for_real_cycle.db"
        shutil.copyfile(self.db, copy)
        self.db = copy  # real flag-ON cycle on a separate copy only
        self.cycle(S2, history, flag=True)
        trade = self.paper()[0].closed_trades[0]
        self.assertEqual((trade.exited_at.isoformat(), Decimal(str(trade.exit_price)), trade.reason),
                         (expected["bar_start"], Decimal(expected["exit_price"]), expected["reason"]))
        self.assertEqual(Decimal(str(trade.net_pnl)), Decimal(expected["net_pnl"]))
        self.assertEqual(Decimal(report["expected_realized_after_first_cycle"]),
                         Decimal(str(self.paper()[0].realized_pnl)))
        self.assertIn("IMMEDIATE_CLOSE_EXPECTED", [r["risk"] for r in report["risks"]])

    def test_historic_missed_touch_is_reported_never_applied(self):
        self.seed()
        history = {("XAUUSD", bar(2)): (100.0, 100.5, 94.0, 96.0)}
        self.seed_historical_watermark(bar(4))  # preserved pre-fix state, not the corrected OFF runtime
        report = preview(self.db, as_of=S2, bars_json=bars_json(Path(self.tmp.name) / "b.json", history, S2))
        position = report["positions"][0]
        self.assertEqual((position["historic_missed"]["bar_start"], Decimal(position["historic_missed"]["net_pnl"])),
                         (bar(2).isoformat(), Decimal(-50)))
        self.assertIsNone(position["expected_close"])  # activation will NOT close it
        divergence = [r for r in report["risks"] if r["risk"] == "HISTORIC_DIVERGENCE"]
        self.assertEqual(divergence[0]["treatment"].split(" ")[0], "REPORT_ONLY")
        self.assertIn("XAUUSD", self.paper()[0].open_positions)  # still open in the source

    def test_pending_orders_revisions_gaps_and_scope(self):
        pending = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0, 1.0, 10000.0, 0.0,
                             S1)
        self.seed(side="SHORT", stop=105.0, target=90.0, symbol="EURUSD", pending=pending)
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, {("XAUUSD", bar(2)): (100.0, 100.5, 94.0, 96.0)}, flag=True)  # creates a REVISION
        later = S2 + timedelta(minutes=30)
        rows = bars_json(Path(self.tmp.name) / "b.json", {}, later)
        data = json.loads(Path(rows).read_text(encoding="utf-8"))
        del data[9]  # remove 13:50: inside the range of the XAUUSD position opened on the 13:40 gate bar -> gap
        Path(rows).write_text(json.dumps(data), encoding="utf-8")
        report = preview(self.db, as_of=S2, evidence_db=self.ev)
        self.assertEqual([r["bar_start"] for r in report["revisions"]], [bar(2).isoformat()])
        kinds = {r["risk"] for r in report["risks"]}
        self.assertTrue({"REVISION_PRESENT", "NO_BAR_DATA", "OUTSIDE_CATCH_UP_SCOPE"} <= kinds)  # EURUSD (P1-B: renamed with --catch-up-symbols)
        gap_report = preview(self.db, as_of=later, bars_json=rows)
        self.assertTrue(any(r["risk"] == "MISSING_BARS" for r in gap_report["risks"]))
        self.assertEqual(report["pending_orders"], [])  # the pending order filled on the 13:40 gate bar (S2 cycle)
        store = Store(self.db)
        try:
            account, orders, _ = store.load_paper("paper-main")
        finally:
            store.close()
        self.assertEqual(orders["pend"].status, "FILLED")

    def test_pending_order_gate_prediction(self):
        pending = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0, 1.0, 10000.0, 0.0,
                             S1)
        self.seed(side=None, pending=pending)
        report = preview(self.db, as_of=S2, bars_json=bars_json(Path(self.tmp.name) / "b.json", {}, S2))
        item = report["pending_orders"][0]
        self.assertEqual((item["gate_bar"], item["not_evaluated_bars"]), (bar(7).isoformat(), [bar(6).isoformat()]))
        self.cycle(S2, {}, flag=True)  # the real activation cycle agrees
        _, orders, fills, journal = self.paper()
        self.assertEqual(next(iter(fills.values())).fill_timestamp.isoformat(), item["gate_bar"])
        self.assertEqual(sorted(e[2]["bar_start"] for e in journal if e[0] == "PENDING_NOT_EVALUATED"),
                         item["not_evaluated_bars"])

    def test_pending_order_never_assigned_a_bar_at_or_before_its_as_of(self):
        """P8.5 MEDIUM regression: the preview predicted the 13:25 bar for an order created at 13:30."""
        pending = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0, 1.0, 10000.0, 0.0,
                             S1)
        self.seed(side=None, pending=pending)
        report = preview(self.db, as_of=S1, bars_json=bars_json(Path(self.tmp.name) / "b.json", {}, S1))
        item = report["pending_orders"][0]
        self.assertEqual((item["gate_bar"], item["expected_status"], item["not_evaluated_bars"]),
                         (None, "STAYS_PENDING_NO_ELIGIBLE_BAR", []))
        copy = Path(self.tmp.name) / "isolated_copy.db"
        shutil.copyfile(self.db, copy)
        self.db = copy  # the real runtime on an isolated copy agrees: not progressed at the S1 cycle
        self.cycle(S1, {}, flag=True)
        _, orders, fills, _ = self.paper()
        self.assertEqual((orders["pend"].status, fills), ("PENDING", {}))

    def test_cli_is_read_only_and_refuses_to_overwrite_sources(self):
        self.seed()
        bars = bars_json(Path(self.tmp.name) / "b.json", {}, S2)
        out = Path(self.tmp.name) / "report.json"
        before = activation_preview.sha256(self.db)
        self.assertEqual(main(["--trading-db", str(self.db), "--as-of", S2.isoformat(), "--bars-json", str(bars),
                               "--out", str(out)]), 0)
        self.assertEqual(activation_preview.sha256(self.db), before)
        self.assertTrue(json.loads(out.read_text(encoding="utf-8"))["source_unchanged"])
        with self.assertRaises(SystemExit):
            main(["--trading-db", str(self.db), "--as-of", S2.isoformat(), "--out", str(self.db)])
        db = sqlite3.connect(self.db)
        try:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        finally:
            db.close()
        source = (Path(__file__).resolve().parent / "replay" / "activation_preview.py").read_text(encoding="utf-8")
        for forbidden in ("save_paper", "TradeManager", "process_bar", "UPDATE ", "INSERT ", "DELETE "):
            self.assertNotIn(forbidden, source)
