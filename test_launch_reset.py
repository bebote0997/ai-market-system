"""Opt-in launch reset: backup first, archive by cutoff, never delete. Synthetic SQLite only."""
from datetime import datetime, timezone
import inspect
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from execution.demo_dashboard import demo_progress, demo_snapshot
from execution.demo_metrics import demo_statistics
from execution.launch_reset import (TOKEN_ENV, apply_launch_reset, launch_cutoff, session_counters)
from runtime.cloud import serve
from ui.views import dashboard


UTC = timezone.utc
LAUNCH = datetime(2026, 11, 14, 15, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 14, 18, 0, tzinfo=UTC)
BEFORE, AFTER = "2026-10-20T18:00:00Z", "2026-11-14T16:00:00Z"


def _env(folder, token="launch-2026-11-14"):
    return {"AI_FLOOR_EXNESS_STATE_DB_PATH": str(folder / "exness.db"),
            "AI_FLOOR_DB_PATH": str(folder / "trading.db"),
            "AI_FLOOR_BROKER_STATE_DB_PATH": str(folder / "broker.db"),
            TOKEN_ENV: token}


def _dump(path):
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        names = [row[0] for row in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY 1")]
        return {name: con.execute(f'SELECT * FROM "{name}"').fetchall() for name in names}
    finally:
        con.close()


def _seed(folder):
    broker = sqlite3.connect(folder / "broker.db")
    broker.execute("CREATE TABLE broker_demo_trades (client_id TEXT PRIMARY KEY, opened_at TEXT, status TEXT)")
    broker.execute("CREATE TABLE broker_demo_events (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT)")
    broker.execute("CREATE TRIGGER broker_demo_events_no_delete BEFORE DELETE ON broker_demo_events "
                   "BEGIN SELECT RAISE(ABORT, 'broker_demo_events is append-only'); END")
    broker.execute("INSERT INTO broker_demo_trades VALUES ('aif-old', ?, 'CLOSED')", (BEFORE,))
    broker.execute("INSERT INTO broker_demo_events(ts, kind) VALUES (?, 'FILL')", (BEFORE,))
    broker.commit()
    broker.close()
    trading = sqlite3.connect(folder / "trading.db")
    trading.execute("CREATE TABLE runs (slot_key TEXT, run_id TEXT, symbol TEXT, as_of TEXT, started_at TEXT)")
    trading.execute("INSERT INTO runs VALUES ('old', 'r-old', 'EURUSD', ?, ?)", (BEFORE, BEFORE))
    trading.execute("INSERT INTO runs VALUES ('new', 'r-new', 'EURUSD', ?, ?)", (AFTER, AFTER))
    trading.commit()
    trading.close()
    state = sqlite3.connect(folder / "exness.db")
    state.execute("CREATE TABLE flags (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    state.execute("INSERT INTO flags VALUES ('exness_brake_halt', '1')")
    state.commit()
    state.close()


def _flag(path, key):
    con = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        row = con.execute("SELECT value FROM flags WHERE key = ?", (key,)).fetchone()
    finally:
        con.close()
    return None if row is None else row[0]


class _Account:
    status = "NO_DATA"
    error_code = "unavailable"
    stale = False
    nav = balance = currency = as_of = None


def _trade(client, opened, pnl):
    return {"id": client, "client_id": client, "state": "CLOSED", "instrument": "EUR_USD",
            "openTime": opened, "closeTime": opened, "realizedPL": pnl, "financing": "0",
            "initialUnits": "1000", "price": "1.1"}


class LaunchResetTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        _seed(self.folder)
        self.env = _env(self.folder)
        self.broker_before = _dump(self.folder / "broker.db")
        self.trading_before = _dump(self.folder / "trading.db")

    def test_backup_then_archive_keeps_every_row_and_resets_counters(self):
        result = apply_launch_reset(self.env, now=LAUNCH)
        self.assertEqual(result.reason, "applied")
        self.assertEqual(result.launch_start, LAUNCH)
        self.assertEqual(_dump(self.folder / "broker.db"), self.broker_before)
        self.assertEqual(_dump(self.folder / "trading.db"), self.trading_before)
        self.assertEqual(_flag(self.folder / "exness.db", "exness_brake_halt"), "1")
        counters = session_counters(self.env)
        self.assertEqual(counters["day"], 0)
        self.assertEqual(counters["trades"], 0)
        self.assertEqual(counters["launch_start"], LAUNCH)
        self.assertEqual(launch_cutoff(self.env), LAUNCH)
        names = {path.name for path in result.backups}
        self.assertEqual(len(names), 3)
        broker_copy = next(path for path in result.backups if path.name.endswith("_broker.db"))
        trading_copy = next(path for path in result.backups if path.name.endswith("_trading.db"))
        exness_copy = next(path for path in result.backups if path.name.endswith("_exness.db"))
        self.assertEqual(_dump(broker_copy), self.broker_before)
        self.assertEqual(_dump(trading_copy), self.trading_before)
        self.assertEqual(_flag(exness_copy, "exness_brake_halt"), "1")
        self.assertIsNone(_flag(exness_copy, "exness_session_day"))
        self.assertFalse(any(path.name.endswith(".tmp") for path in (self.folder / "launch_backups").iterdir()))

    def test_the_same_token_does_not_run_again_and_a_new_token_can(self):
        first = apply_launch_reset(self.env, now=LAUNCH)
        again = apply_launch_reset(self.env, now=LATER)
        self.assertEqual(again.reason, "already_applied")
        self.assertEqual(again.launch_start, LAUNCH)
        self.assertEqual(again.backups, ())
        self.assertEqual(len(list((self.folder / "launch_backups").glob("*.db"))), len(first.backups))
        self.assertEqual(session_counters(self.env)["launch_start"], LAUNCH)
        second = apply_launch_reset(_env(self.folder, "launch-next"), now=LATER)
        self.assertEqual(second.reason, "applied")
        self.assertEqual(session_counters(self.env)["day"], 0)
        self.assertEqual(session_counters(self.env)["trades"], 0)
        self.assertEqual(session_counters(self.env)["launch_start"], LATER)
        self.assertEqual(_dump(self.folder / "broker.db"), self.broker_before)
        self.assertEqual(_dump(self.folder / "trading.db"), self.trading_before)
        third = apply_launch_reset(self.env, now=LATER)
        self.assertEqual(third.reason, "already_applied")
        self.assertEqual(third.launch_start, LAUNCH)
        self.assertEqual(session_counters(self.env)["launch_start"], LATER)
        con = sqlite3.connect((self.folder / "exness.db").resolve().as_uri() + "?mode=ro", uri=True)
        try:
            tokens = {row[0] for row in con.execute("SELECT token FROM launch_resets")}
        finally:
            con.close()
        self.assertEqual(tokens, {"launch-2026-11-14", "launch-next"})

    def test_a_failed_backup_leaves_the_sources_and_does_not_record_the_token(self):
        (self.folder / "broker.db").write_bytes(b"this is not a database")
        result = apply_launch_reset(self.env, now=LAUNCH)
        self.assertEqual((result.applied, result.reason), (False, "backup_failed"))
        self.assertIsNone(launch_cutoff(self.env))
        self.assertEqual(session_counters(self.env)["day"], None)
        self.assertEqual(_dump(self.folder / "trading.db"), self.trading_before)
        self.assertEqual(_flag(self.folder / "exness.db", "exness_brake_halt"), "1")
        con = sqlite3.connect((self.folder / "exness.db").resolve().as_uri() + "?mode=ro", uri=True)
        try:
            names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
        self.assertNotIn("launch_resets", names)
        folder = self.folder / "launch_backups"
        self.assertEqual([] if not folder.exists() else list(folder.iterdir()), [])

    def test_unset_or_invalid_token_does_nothing(self):
        bare = dict(self.env)
        bare[TOKEN_ENV] = "  "
        self.assertEqual(apply_launch_reset(bare, now=LAUNCH).reason, "disabled")
        self.assertEqual(_flag(self.folder / "exness.db", "exness_session_day"), None)
        spaced = dict(self.env)
        spaced[TOKEN_ENV] = "not a token"
        self.assertEqual(apply_launch_reset(spaced, now=LAUNCH).reason, "invalid_token")
        naive = datetime(2026, 11, 14, 15, 0)
        self.assertEqual(apply_launch_reset(self.env, now=naive).reason, "invalid_time")
        self.assertIsNone(launch_cutoff(self.env))
        self.assertEqual(_dump(self.folder / "broker.db"), self.broker_before)

    def test_dashboard_and_stats_honor_the_cutoff(self):
        apply_launch_reset(self.env, now=LAUNCH)
        with patch.dict(os.environ, self.env, clear=True):
            since = dashboard.demo_since()
        self.assertEqual(since, LAUNCH)
        later_epoch = {**self.env, "AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO",
                       "AI_FLOOR_DEMO_EPOCH": "2026-10-18T22:25:00Z"}
        with patch.dict(os.environ, later_epoch, clear=True):
            self.assertEqual(dashboard.demo_since(), LAUNCH)
        db = sqlite3.connect(self.folder / "trading.db")
        db.row_factory = sqlite3.Row
        try:
            kept = {row["run_id"] for row in dashboard.history(db, since=since) if row["record"] == "runs"}
            stored = {row["run_id"] for row in dashboard.history(db) if row["record"] == "runs"}
        finally:
            db.close()
        self.assertEqual(kept, {"r-new"})
        self.assertEqual(stored, {"r-old", "r-new"})
        old, new = _trade("aif-old", BEFORE, "50"), _trade("aif-new", AFTER, "3")
        stats, _curve, opened = demo_statistics([old, new], LATER, LAUNCH)
        self.assertEqual(stats["trades"], 1)
        self.assertEqual(stats["total_pnl"], 3.0)
        self.assertEqual(opened, [])
        only_old, _, _ = demo_statistics([old], LATER, LAUNCH)
        self.assertEqual(only_old["trades"], 0)

        def reader(_epoch):
            return {"closed": [old, new], "open": [], "error": None}

        snap = demo_snapshot(LATER, self.env, trades_reader=reader, account_reader=lambda _kind: _Account())
        self.assertEqual(snap["stats"]["trades"], 1)
        self.assertEqual(snap["stats"]["total_pnl"], 3.0)
        self.assertEqual(snap["closed_rows"][0]["client_id"], "aif-new")
        _epoch, _first, day = demo_progress(LATER, self.env)
        self.assertEqual(day, 1)
        self.assertEqual(session_counters(self.env)["day"], 0)
        self.assertEqual(session_counters(self.env)["trades"], 0)

    def test_cloud_startup_calls_the_reset(self):
        self.assertIn("apply_launch_reset", inspect.getsource(serve))

    def test_a_start_with_no_databases_still_writes_a_backup_before_the_counters(self):
        folder = self.folder / "fresh"
        folder.mkdir()
        result = apply_launch_reset(_env(folder, "empty-token"), now=LAUNCH)
        self.assertEqual(result.reason, "applied")
        self.assertEqual(len(result.backups), 1)
        self.assertTrue(result.backups[0].name.endswith("_empty.db"))
        self.assertEqual(session_counters(_env(folder, "empty-token"))["day"], 0)
        self.assertEqual(session_counters(_env(folder, "empty-token"))["trades"], 0)
