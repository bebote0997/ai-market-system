"""V2 Phase 8 / P1-B (DEC-8.14 / 8.15 / 8.16): scope preflight with STRICT evidence completeness, the conservative
revision gate for disabled symbols, and the preview with --catch-up-symbols. Temporary databases only."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from data.market_evidence import MarketBar, MarketEvidenceEngine
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from replay.activation_preview import main as preview_main, preview
from runtime.cloud import cloud_preflight
from runtime.config import RuntimeConfig, catch_up_scope_checks
from runtime.demo_runner import preflight
from runtime.revision_review import demo_gate
from storage.database import Store
from storage.evidence_store import EvidenceStore
from test_phase8_observe_only import EUR_STOP_T2, XAU_STOP_T3, economics, make
from test_runtime_catch_up import ACCOUNT, BASE, FIVE, SLOT, Bars, seed

AS_OF = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc)  # newest closed 5m bar: 13:55
NEWEST = AS_OF - FIVE


class ScopePreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p1b-pre-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db, self.ev = self.dir / "trading_floor.db", self.dir / "market_evidence.db"

    def config(self, scope=("XAUUSD",)):
        return RuntimeConfig(db_path=self.db, enabled_symbols=("XAUUSD", "EURUSD"), v2_position_catch_up=bool(scope),
                             v2_position_catch_up_symbols=tuple(scope),
                             market_evidence_path=self.ev if scope else None)

    def ingest(self, symbol, starts):
        """Commit flat closed 5m bars (in order) through the real evidence engine."""
        store = EvidenceStore(self.ev)
        try:
            engine = MarketEvidenceEngine(store, enabled_symbols=("XAUUSD", "EURUSD"))
            base = BASE[symbol]
            for start in starts:
                engine.ingest(symbol, "5m", [MarketBar(symbol, "5m", start, base, base, base, base, is_closed=True)],
                              as_of=AS_OF)
        finally:
            store.close()

    def position(self, symbol="XAUUSD", watermark=datetime(2026, 1, 15, 13, 20, tzinfo=timezone.utc)):
        store = Store(self.db)
        try:
            broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
            order = PaperOrder("1.0", "ord", "run", symbol, "LONG", 1.0, BASE[symbol], BASE[symbol] * .95,
                               BASE[symbol] * 1.1, 1.0, 10000.0, 0.0, watermark - FIVE, status="FILLED")
            broker.orders[order.order_id] = order
            broker.account.open_positions[symbol] = PaperPosition(
                "1.0", "pos", order.order_id, order.run_id, symbol, "LONG", 1.0, BASE[symbol], BASE[symbol],
                order.stop, order.target, watermark - FIVE, last_price=BASE[symbol], contract_multiplier=1.0,
                last_processed_at=watermark)
            store.save_paper(broker)
        finally:
            store.close()

    def bars(self, first, last=NEWEST):
        out, stamp = [], first
        while stamp <= last:
            out.append(stamp)
            stamp += FIVE
        return out

    def checks(self, scope=("XAUUSD",), as_of=AS_OF):
        return catch_up_scope_checks(self.config(scope), as_of=as_of)

    # P1: OFF adds no keys; ON reports the scope
    def test_p1_off_report_unchanged_and_on_reports_scope(self):
        self.assertEqual(catch_up_scope_checks(self.config(())), {})
        off = preflight(self.config(()), env={})
        self.assertFalse([k for k in off.checks if k.startswith("catch_up_")])
        on = preflight(self.config(), env={})
        self.assertTrue(on.checks["catch_up_scope_valid"])
        self.assertIn("catch_up_evidence_contiguous", on.checks)
        env = {"AI_FLOOR_DURABLE_MOUNT": str(self.dir), "AI_FLOOR_DASHBOARD_PASSWORD": "x"}
        self.assertIn("catch_up_evidence_contiguous", cloud_preflight(self.config(), env=env, disk_mounted=True).checks)

    # Owner decision: absent or empty evidence never satisfies the check (first activation needs prior evidence)
    def test_absent_or_empty_evidence_store_fails_closed(self):
        self.assertFalse(self.checks()["catch_up_evidence_contiguous"])  # absent
        EvidenceStore(self.ev).close()  # exists, empty
        self.assertFalse(self.checks()["catch_up_evidence_contiguous"])

    # NE7 (positive): a fully committed window and trailing bar -> True
    def test_ne7_complete_window_passes(self):
        self.position()
        self.ingest("XAUUSD", self.bars(datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)))
        self.assertEqual(self.checks(), {"catch_up_scope_valid": True, "catch_up_evidence_contiguous": True})

    # NE1: a missing interior bar inside an open position's window
    def test_ne1_missing_interior_bar_fails(self):
        self.position()
        starts = self.bars(datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc))
        starts.remove(datetime(2026, 1, 15, 13, 40, tzinfo=timezone.utc))
        self.ingest("XAUUSD", starts)
        self.assertFalse(self.checks()["catch_up_evidence_contiguous"])

    # NE2: the trailing (newest) bar missing, even with no open position
    def test_ne2_trailing_bar_missing_fails_without_positions(self):
        self.ingest("XAUUSD", self.bars(datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc), NEWEST - FIVE))
        self.assertFalse(self.checks()["catch_up_evidence_contiguous"])
        self.ingest("XAUUSD", [NEWEST])
        self.assertTrue(self.checks()["catch_up_evidence_contiguous"])

    # NE3: a GAP anomaly inside the window fails even if the window bars are present
    def test_ne3_gap_anomaly_in_window_fails(self):
        self.position()
        self.ingest("XAUUSD", self.bars(datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)))
        db = sqlite3.connect(self.ev)
        try:
            db.execute("INSERT INTO evidence_anomalies(anomaly_id,symbol,timeframe,kind,bar_start,payload) "
                       "VALUES('gap-x','XAUUSD','5m','GAP',?, '{}')", (datetime(2026, 1, 15, 13, 45,
                                                                                tzinfo=timezone.utc).isoformat(),))
            db.commit()
        finally:
            db.close()
        self.assertFalse(self.checks()["catch_up_evidence_contiguous"])

    # NE4: STRICT is around the clock: a window across a weekend without weekend bars fails
    def test_ne4_weekend_window_fails_in_strict(self):
        friday = datetime(2026, 1, 16, 21, 0, tzinfo=timezone.utc)
        monday = datetime(2026, 1, 19, 8, 0, tzinfo=timezone.utc)
        self.position(watermark=friday)
        self.ingest("XAUUSD", [friday] + self.bars(monday - 12 * FIVE, monday - FIVE))
        self.assertFalse(catch_up_scope_checks(self.config(), as_of=monday)["catch_up_evidence_contiguous"])

    # NE5: CALENDAR mode does not exist (DEC-8.14): there is no way to relax STRICT by configuration
    def test_ne5_no_calendar_relaxation_exists(self):
        self.assertFalse(hasattr(RuntimeConfig(), "catch_up_calendar"))

    # NE6: a future as-of is never accepted
    def test_ne6_future_as_of_fails(self):
        self.ingest("XAUUSD", self.bars(datetime(2026, 1, 15, 13, 0, tzinfo=timezone.utc)))
        future = datetime.now(timezone.utc) + timedelta(days=1)
        self.assertFalse(self.checks(as_of=future)["catch_up_evidence_contiguous"])

    def test_out_of_scope_symbol_is_not_required(self):
        self.position(symbol="EURUSD")  # EURUSD outside the scope: its window is not checked
        self.ingest("XAUUSD", [NEWEST])
        self.assertTrue(self.checks()["catch_up_evidence_contiguous"])
        self.assertFalse(self.checks(("XAUUSD", "EURUSD"))["catch_up_evidence_contiguous"])  # in scope -> required


class DisabledSymbolGateTests(unittest.TestCase):
    def test_anomaly_for_a_disabled_symbol_blocks(self):
        tmp = tempfile.TemporaryDirectory(prefix="v2-p1b-gate-")
        self.addCleanup(tmp.cleanup)
        db, ev = Path(tmp.name) / "trading_floor.db", Path(tmp.name) / "market_evidence.db"
        Store(db).close()
        EvidenceStore(ev).close()
        conn = sqlite3.connect(ev)
        try:
            conn.execute("INSERT INTO evidence_anomalies(anomaly_id,symbol,timeframe,kind,bar_start,payload) "
                         "VALUES('nas','NAS100','5m','REVISION','2026-01-15T13:00:00+00:00','{}')")
            conn.commit()
        finally:
            conn.close()
        gate = demo_gate(db, ev)
        self.assertEqual(gate["status"], "BLOCKED")
        self.assertIn("ANOMALY_FOR_DISABLED_SYMBOL", gate["blockers"])
        self.assertEqual(gate["anomalies_for_disabled_symbols"][0]["symbol"], "NAS100")
        self.assertNotIn("ANOMALY_FOR_DISABLED_SYMBOL",
                         demo_gate(db, ev, ("XAUUSD", "EURUSD", "NAS100"))["blockers"])  # only when not enabled


class PreviewScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p1b-prev-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def bars_json(self, overrides):
        rows = []
        for symbol in ("XAUUSD", "EURUSD"):
            frame = Bars(overrides).load_snapshot(symbol, SLOT)["5m"]
            for stamp, row in frame.iterrows():
                rows.append({"symbol": symbol, "bar_start": stamp.isoformat(), "open": row["Open"],
                             "high": row["High"], "low": row["Low"], "close": row["Close"]})
        path = self.dir / "bars.json"
        path.write_text(json.dumps(rows), encoding="utf-8")
        return path

    def test_v1_preview_paths_match_a_real_cycle_with_scope_xauusd(self):
        db, ev = self.dir / "trading_floor.db", self.dir / "market_evidence.db"
        seed(db, positions=("XAUUSD", "EURUSD"))
        overrides = {**XAU_STOP_T3, **EUR_STOP_T2}
        report = preview(db, as_of=SLOT, bars_json=self.bars_json(overrides), catch_up_symbols=("XAUUSD",))
        by_symbol = {p["symbol"]: p for p in report["positions"]}
        self.assertEqual((by_symbol["XAUUSD"]["path"], by_symbol["EURUSD"]["path"]), ("CATCH_UP", "NEWEST_BAR"))
        self.assertTrue(by_symbol["EURUSD"]["ongoing_high_8_1_exposure"])
        self.assertIsNone(by_symbol["EURUSD"]["expected_close"])  # the newest bar alone misses EUR's T2 touch
        self.assertEqual(report["certification_eligibility"], "TECHNICAL_ONLY")
        self.assertIn("OUTSIDE_CATCH_UP_SCOPE", [r["risk"] for r in report["risks"]])
        copy = self.dir / "copy.db"
        shutil.copyfile(db, copy)
        runtime = make(copy, ev, SLOT, ("XAUUSD",), overrides)
        try:
            runtime.run_cycle("XAUUSD", SLOT)
            runtime.run_cycle("EURUSD", SLOT)
        finally:
            runtime.close()
        trade = economics(copy, "XAUUSD")["closed"][0]
        expected = by_symbol["XAUUSD"]["expected_close"]
        self.assertEqual((trade.exited_at.isoformat(), float(expected["exit_price"]), trade.reason),
                         (expected["bar_start"], trade.exit_price, expected["reason"]))
        self.assertIsNotNone(economics(copy, "EURUSD")["position"])  # agrees with the NEWEST_BAR prediction

    def test_v1_cli_validates_the_scope_and_first_scope_is_gone(self):
        db = self.dir / "trading_floor.db"
        seed(db, positions=("XAUUSD",))
        out = self.dir / "r.json"
        with self.assertRaises(ValueError):
            preview_main(["--trading-db", str(db), "--as-of", SLOT.isoformat(), "--catch-up-symbols", "xauusd",
                          "--out", str(out)])
        with self.assertRaises(SystemExit):
            preview_main(["--trading-db", str(db), "--as-of", SLOT.isoformat(), "--first-scope", "XAUUSD",
                          "--out", str(out)])


if __name__ == "__main__":
    unittest.main()
