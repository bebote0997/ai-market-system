"""V2 Phase 2 / Batch 1: Market Evidence Engine foundation and H02 evidence processing."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import random
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import unittest

import pandas as pd

from data import market_evidence as module
from data.market_evidence import (
    EvidenceError, MarketBar, MarketEvidenceEngine, TIMEFRAMES, bars_from_frame,
)
from data.twelve_data_provider import TIMEFRAMES as TWELVE_DATA_TIMEFRAMES, TwelveDataMarketDataProvider
from data.massive_provider import TIMEFRAMES as MASSIVE_TIMEFRAMES
from runtime.config import DEFAULT_ENABLED_SYMBOLS
from storage import evidence_store as store_module
from storage.database import Store
from storage.evidence_store import (
    EVIDENCE_APPLICATION_ID, EVIDENCE_SCHEMA_VERSION, EVIDENCE_TABLES, EvidenceStore, EvidenceStoreError,
)
from storage.health_store import HealthStore, HealthStoreError
from test_demo_runner import frames as v1_frames

ROOT = Path(__file__).resolve().parent
T0 = datetime(2026, 10, 5, 13, 0, tzinfo=timezone.utc)  # Monday, London/New York overlap.


def bar(index, *, timeframe="5m", symbol="XAUUSD", close=None, closed=True, provider="twelve_data", volume=None):
    start = T0 + index * TIMEFRAMES[timeframe]
    close = 100.0 + index if close is None else close
    return MarketBar(symbol, timeframe, start, close, close + 1, close - 1, close, volume=volume,
                     provider=provider, is_closed=closed)


def after(index, timeframe="5m"):
    """as_of at which bar ``index`` has just closed (start + duration)."""
    return T0 + (index + 1) * TIMEFRAMES[timeframe]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class EvidenceCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-evidence-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "market_evidence.db"
        self.open()

    def open(self):
        self.store = EvidenceStore(self.path)
        self.addCleanup(self.store.close)
        self.engine = MarketEvidenceEngine(self.store)
        return self.engine

    def reopen(self):
        self.store.close()
        return self.open()

    def starts(self, bars):
        return [b.start for b in bars]

    def stream(self, symbol="XAUUSD", timeframe="5m"):
        return self.engine.committed(symbol, timeframe)

    def dump(self):
        return (self.store.db.execute("SELECT symbol,timeframe,bar_start,stream_seq,payload,digest FROM market_evidence "
                                      "ORDER BY symbol,timeframe,stream_seq").fetchall(),
                self.store.db.execute("SELECT anomaly_id,symbol,timeframe,kind,bar_start,payload "
                                      "FROM evidence_anomalies ORDER BY anomaly_id").fetchall())


class IdentityTests(EvidenceCase):
    def test_identity_is_semantic_and_deterministic(self):
        first, second = bar(3), bar(3)
        self.assertEqual((first.key, first.digest), (second.key, second.digest))
        self.assertEqual(first.key, ("XAUUSD", "5m", (T0 + timedelta(minutes=15)).isoformat()))
        shifted = MarketBar("XAUUSD", "5m", (T0 + timedelta(minutes=15)).astimezone(timezone(timedelta(hours=2))),
                            103.0, 104.0, 102.0, 103.0, provider="massive", is_closed=True)
        self.assertEqual((shifted.key, shifted.digest), (first.key, first.digest))  # Provider is not identity.
        self.assertNotEqual(bar(3, close=200.0).digest, first.digest)
        self.assertNotEqual(bar(3, timeframe="15m").key, first.key)
        self.assertNotEqual(bar(3, symbol="EURUSD").key, first.key)

    def test_contract_validation(self):
        naive = datetime(2026, 10, 5, 13, 0)
        cases = (lambda: MarketBar("XAUUSD", "5m", naive, 1, 2, 0.5, 1, is_closed=True),
                 lambda: MarketBar("XAUUSD", "1m", T0, 1, 2, 0.5, 1, is_closed=True),
                 lambda: MarketBar("XAUUSD", "5m", T0, 1, 0.9, 0.5, 1, is_closed=True),
                 lambda: MarketBar("XAUUSD", "5m", T0, float("nan"), 2, 0.5, 1, is_closed=True),
                 lambda: MarketBar("XAUUSD", "5m", T0, True, 2, 0.5, 1, is_closed=True),
                 lambda: MarketBar("XAUUSD", "5m", T0, 1, 2, 0.5, 1, volume=-1, is_closed=True),
                 lambda: MarketBar("XAUUSD", "5m", T0, 1, 2, 0.5, 1, is_closed=1))
        for case in cases:
            with self.assertRaises(EvidenceError):
                case()

    def test_duplicate_identity_in_one_presentation(self):
        result = self.engine.ingest("XAUUSD", "5m", [bar(0), bar(0), bar(1)], as_of=after(1))
        self.assertEqual((result.presented, len(result.committed)), (3, 2))
        with self.assertRaises(EvidenceError):
            self.engine.ingest("XAUUSD", "5m", [bar(2), bar(2, close=500.0)], as_of=after(2))
        self.assertEqual(self.starts(self.stream()), [bar(0).start, bar(1).start])  # Nothing from the rejected one.

    def test_payload_round_trip(self):
        original = bar(4, volume=12.5)
        self.assertEqual(MarketBar.from_payload(original.payload()), original)


class OrderTests(EvidenceCase):
    def test_sequential_reversed_and_shuffled_input_commit_identically(self):
        window = [bar(i) for i in range(12)]
        expected = None
        orders = [window, list(reversed(window))] + [random.Random(seed).sample(window, len(window)) for seed in range(5)]
        for index, presented in enumerate(orders):
            with self.subTest(order=index):
                self.path = self.dir / f"order-{index}.db"
                self.open()
                result = self.engine.ingest("XAUUSD", "5m", presented, as_of=after(11))
                self.assertEqual(self.starts(result.committed), sorted(b.start for b in window))
                dump = self.dump()
                seqs = [row[3] for row in dump[0]]
                self.assertEqual(seqs, list(range(1, 13)))  # stream_seq follows chronology.
                expected = dump if expected is None else expected
                self.assertEqual(dump, expected)  # Byte-identical evidence on every repeated run.

    def test_streams_are_ordered_independently_without_artificial_priority(self):
        snapshot_a = {"5m": [bar(i) for i in range(3)], "15m": [bar(0, timeframe="15m")]}
        for timeframe, bars in reversed(list(snapshot_a.items())):
            self.engine.ingest("XAUUSD", timeframe, bars, as_of=after(2))
        self.engine.ingest("EURUSD", "5m", [bar(1, symbol="EURUSD")], as_of=after(2))
        self.assertEqual(self.starts(self.stream()), [bar(i).start for i in range(3)])
        self.assertEqual(self.starts(self.stream(timeframe="15m")), [T0])
        self.assertEqual(self.starts(self.stream("EURUSD")), [bar(1).start])


class IdempotencyTests(EvidenceCase):
    def test_same_batch_twice_commits_once(self):
        window = [bar(i) for i in range(4)]
        first = self.engine.ingest("XAUUSD", "5m", window, as_of=after(3))
        second = self.engine.ingest("XAUUSD", "5m", window, as_of=after(3))
        self.assertEqual(len(first.committed), 4)
        self.assertEqual((second.committed, len(second.duplicates)), ((), 4))
        self.assertEqual(len(self.stream()), 4)

    def test_overlapping_provider_windows(self):
        self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(0, 4)], as_of=after(3))
        result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(2, 7)], as_of=after(6))
        self.assertEqual(self.starts(result.committed), [bar(i).start for i in range(4, 7)])
        self.assertEqual(result.duplicates, (bar(2).bar_start, bar(3).bar_start))
        self.assertEqual(self.starts(self.stream()), [bar(i).start for i in range(7)])

    def test_duplicate_scheduler_slot_and_provider_retry(self):
        window = [bar(i) for i in range(3)]
        for _ in range(3):  # The same slot presented repeatedly.
            self.engine.ingest("XAUUSD", "5m", window, as_of=after(2))
        retried = window + window[::-1] + window  # A retried request returning the same bars again.
        result = self.engine.ingest("XAUUSD", "5m", retried, as_of=after(2))
        self.assertEqual((result.committed, len(self.stream())), ((), 3))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM market_evidence").fetchone()[0], 3)


class CatchUpTests(EvidenceCase):
    def test_no_new_bars(self):
        self.engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        result = self.engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0) + timedelta(minutes=2))
        self.assertEqual((result.committed, result.watermark), ((), bar(0).bar_start))
        empty = self.engine.ingest("XAUUSD", "5m", [], as_of=after(1))
        self.assertEqual((empty.presented, empty.committed), (0, ()))

    def test_one_new_bar(self):
        self.engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        result = self.engine.ingest("XAUUSD", "5m", [bar(0), bar(1)], as_of=after(1))
        self.assertEqual(self.starts(result.committed), [bar(1).start])

    def test_multiple_new_bars_never_jump_from_t0_to_t4(self):
        self.engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        # Downtime: the provider window now holds T0..T4; a newest-only consumer would see only T4.
        result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        self.assertEqual(self.starts(result.committed), [bar(i).start for i in (1, 2, 3, 4)])
        self.assertTrue(result.contiguous)
        self.assertEqual(self.starts(self.engine.committed("XAUUSD", "5m", after=bar(0).start)),
                         [bar(i).start for i in (1, 2, 3, 4)])
        self.assertEqual(result.watermark, bar(4).bar_start)

    def test_v1_cadence_three_5m_bars_per_15m_slot(self):
        """V1 consumes only the latest 5m bar per 15m slot; the evidence engine keeps all three."""
        for slot in range(1, 4):
            as_of = T0 + slot * timedelta(minutes=15)
            window = [bar(i) for i in range(slot * 3)]
            self.engine.ingest("XAUUSD", "5m", window, as_of=as_of)
        self.assertEqual(self.starts(self.stream()), [bar(i).start for i in range(9)])


class ClosedBarTests(EvidenceCase):
    def test_forming_bar_is_held_then_committed_once_closed(self):
        window = [bar(0), bar(1), bar(2)]
        result = self.engine.ingest("XAUUSD", "5m", window, as_of=after(1) + timedelta(minutes=2))
        self.assertEqual((self.starts(result.committed), result.held), ([bar(0).start, bar(1).start], (bar(2).bar_start,)))
        later = self.engine.ingest("XAUUSD", "5m", window, as_of=after(2))
        self.assertEqual(self.starts(later.committed), [bar(2).start])

    def test_boundary_matches_v1_providers(self):
        # V1: a bar is skipped iff start + duration > as_of, so a bar ending exactly at as_of is closed.
        self.assertTrue(bar(0).closed_at(after(0)))
        self.assertFalse(bar(0).closed_at(after(0) - timedelta(seconds=1)))
        self.assertFalse(bar(0, closed=False).closed_at(after(5)))

    def test_not_closed_bar_holds_every_later_bar(self):
        window = [bar(0), bar(1, closed=False), bar(2)]
        result = self.engine.ingest("XAUUSD", "5m", window, as_of=after(2))
        self.assertEqual(self.starts(result.committed), [bar(0).start])
        self.assertEqual(result.held, (bar(1).bar_start, bar(2).bar_start))
        self.assertEqual(self.starts(self.stream()), [bar(0).start])

    def test_real_twelve_data_frame_flows_through_unchanged_v1_rule(self):
        as_of = after(5) + timedelta(minutes=2)  # Bar 6 is still forming.
        values = [{"datetime": (T0 + i * timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S"),
                   "open": "100", "high": "101", "low": "99", "close": "100.5"} for i in range(7)][::-1]

        def transport(url, timeout):
            interval = "15min" if "15min" in url else "5min" if "5min" in url else "1h"
            return {"status": "ok", "meta": {"symbol": "XAU/USD", "interval": interval}, "values": values}
        provider = TwelveDataMarketDataProvider(api_key="dummy", transport=transport, sleep=lambda _: None)
        frame = provider.load_snapshot("XAUUSD", as_of)["5m"]
        bars = bars_from_frame(frame, symbol="XAUUSD", timeframe="5m")
        result = self.engine.ingest("XAUUSD", "5m", bars, as_of=as_of)
        self.assertEqual(self.starts(result.committed), list(frame.index.to_pydatetime()))
        self.assertEqual(self.starts(result.committed), [bar(i).start for i in range(6)])
        self.assertEqual(result.committed[0].provider, "twelve_data")


class RestartTests(EvidenceCase):
    def run_child(self, body):
        code = textwrap.dedent("""
            import os, sys
            from datetime import timedelta
            sys.path.insert(0, {root!r})
            from storage.evidence_store import EvidenceStore
            from data.market_evidence import MarketEvidenceEngine
            from test_market_evidence import bar, after
            store = EvidenceStore({path!r})
            engine = MarketEvidenceEngine(store)
        """).format(root=str(ROOT), path=str(self.path)) + textwrap.dedent(body)
        return subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=60)

    def test_clean_restart(self):
        self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(3)], as_of=after(2))
        self.reopen()
        self.assertEqual(self.engine.watermark("XAUUSD", "5m"), bar(2))
        result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        self.assertEqual((self.starts(result.committed), len(result.duplicates)), ([bar(3).start, bar(4).start], 3))
        self.assertEqual([r[3] for r in self.dump()[0]], [1, 2, 3, 4, 5])

    def test_process_crash_before_first_commit_loses_nothing(self):
        self.engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        self.store.close()
        child = self.run_child("""
            import contextlib
            from storage import evidence_store
            @contextlib.contextmanager
            def crash_before_commit(self):
                self.db.execute("BEGIN IMMEDIATE")
                yield
                os._exit(17)  # Hard crash inside the open transaction, after the INSERT.
            evidence_store.EvidenceStore.transaction = crash_before_commit
            engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        """)
        self.assertEqual(child.returncode, 17, child.stderr)
        self.open()
        self.assertEqual(self.starts(self.stream()), [bar(0).start])  # Uncommitted evidence was not kept...
        result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        self.assertEqual(self.starts(result.committed), [bar(i).start for i in (1, 2, 3, 4)])  # ...and stays eligible.

    def test_process_crash_after_partial_catch_up_then_restart(self):
        self.engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        self.store.close()
        child = self.run_child("""
            original = MarketEvidenceEngine._commit
            calls = []
            def crash_after_two(self, item):
                outcome = original(self, item)
                if outcome[0] == "COMMITTED":
                    calls.append(item)
                if len(calls) == 2:
                    os._exit(23)  # T1 and T2 committed; T3, T4 not yet.
                return outcome
            MarketEvidenceEngine._commit = crash_after_two
            engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        """)
        self.assertEqual(child.returncode, 23, child.stderr)
        self.open()
        self.assertEqual(self.starts(self.stream()), [bar(i).start for i in (0, 1, 2)])
        result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        self.assertEqual(self.starts(result.committed), [bar(3).start, bar(4).start])
        self.assertEqual(len(result.duplicates), 3)
        self.assertEqual([r[3] for r in self.dump()[0]], [1, 2, 3, 4, 5])
        self.assertEqual(self.starts(self.stream()), [bar(i).start for i in range(5)])

    def test_repeated_crash_and_restart(self):
        self.store.close()
        window = "[bar(i) for i in range(6)]"
        for attempt in range(3):
            child = self.run_child(f"""
                original = MarketEvidenceEngine._commit
                def crash_after_new_commit(self, item):
                    outcome = original(self, item)
                    if outcome[0] == "COMMITTED":
                        os._exit(31)
                    return outcome
                MarketEvidenceEngine._commit = crash_after_new_commit
                engine.ingest("XAUUSD", "5m", {window}, as_of=after(5))
            """)
            self.assertEqual(child.returncode, 31, child.stderr)
        self.open()
        self.assertEqual(self.starts(self.stream()), [bar(i).start for i in range(3)])
        result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(6)], as_of=after(5))
        self.assertEqual(self.starts(result.committed), [bar(i).start for i in (3, 4, 5)])
        self.assertEqual([r[3] for r in self.dump()[0]], list(range(1, 7)))

    def test_in_process_failure_mid_catch_up(self):
        original = MarketEvidenceEngine._commit
        calls = []

        def fail_third(engine, item):
            calls.append(item)
            if len(calls) == 3:
                raise OSError("disk went away")
            return original(engine, item)
        MarketEvidenceEngine._commit = fail_third
        self.addCleanup(setattr, MarketEvidenceEngine, "_commit", original)
        with self.assertRaises(OSError):
            self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        MarketEvidenceEngine._commit = original
        self.assertEqual(self.starts(self.stream()), [bar(0).start, bar(1).start])
        result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        self.assertEqual(self.starts(result.committed), [bar(i).start for i in (2, 3, 4)])


class GapTests(EvidenceCase):
    def test_missing_bars_are_recorded_never_fabricated(self):
        result = self.engine.ingest("XAUUSD", "5m", [bar(1), bar(2), bar(5)], as_of=after(5))
        self.assertEqual(self.starts(result.committed), [bar(1).start, bar(2).start, bar(5).start])
        self.assertFalse(result.contiguous)
        (gap,) = result.gaps
        self.assertEqual((gap.kind, gap.details["after"], gap.details["before"], gap.details["missing_intervals"]),
                         ("GAP", bar(2).bar_start, bar(5).bar_start, 2.0))
        self.reopen()
        self.assertEqual(self.engine.anomalies(), (gap,))
        self.assertEqual(len(self.stream()), 3)  # No synthetic candle for bars 3 and 4.
        again = self.engine.ingest("XAUUSD", "5m", [bar(1), bar(2), bar(5)], as_of=after(5))
        self.assertEqual((again.gaps, len(self.engine.anomalies())), ((), 1))

    def test_gap_between_watermark_and_a_later_provider_window(self):
        self.engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(10, 13)], as_of=after(12))
        self.assertEqual(result.gaps[0].details["missing_intervals"], 9.0)
        self.assertFalse(result.contiguous)

    def test_late_bar_is_visible_not_committed(self):
        self.engine.ingest("XAUUSD", "5m", [bar(1), bar(2), bar(5)], as_of=after(5))
        result = self.engine.ingest("XAUUSD", "5m", [bar(3)], as_of=after(5))
        self.assertEqual((result.committed, result.late, result.watermark), ((), (bar(3).bar_start,), bar(5).bar_start))
        self.assertEqual([a.kind for a in self.engine.anomalies()], ["LATE", "GAP"])  # Chronological by bar_start.
        self.assertNotIn(bar(3).start, self.starts(self.stream()))

    def test_revised_bar_never_overwrites_committed_evidence(self):
        self.engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        result = self.engine.ingest("XAUUSD", "5m", [bar(0, close=150.0)], as_of=after(0))
        self.assertEqual(result.revisions, (bar(0).bar_start,))
        self.assertEqual(self.stream(), (bar(0),))
        self.assertEqual(self.engine.anomalies()[0].kind, "REVISION")


class MultiStreamTests(EvidenceCase):
    def snapshot(self, symbol, minutes):
        """V1 provider frame shape with one bar per timeframe that has just closed at ``at``."""
        at = T0 + timedelta(minutes=minutes)
        snapshot = {}
        for timeframe, duration in TIMEFRAMES.items():
            frame = v1_frames(at - duration)["5m"]
            frame["symbol"], frame["timeframe"], frame["provider"] = symbol, timeframe, "twelve_data"
            snapshot[timeframe] = frame
        return snapshot, at

    def test_snapshot_all_timeframes_and_symbols(self):
        for symbol in DEFAULT_ENABLED_SYMBOLS:
            for minutes in (60, 120):
                snapshot, at = self.snapshot(symbol, minutes)
                results = self.engine.ingest_snapshot(symbol, dict(reversed(list(snapshot.items()))), as_of=at)
                self.assertEqual(list(results), ["1h", "15m", "5m"])
                self.assertTrue(all(len(r.committed) == 1 for r in results.values()))
        for symbol in DEFAULT_ENABLED_SYMBOLS:
            for timeframe in ("1h", "15m", "5m"):
                self.assertEqual(len(self.engine.committed(symbol, timeframe)), 2)

    def test_missing_timeframe_is_no_data_and_invalid_frame_commits_nothing(self):
        snapshot, at = self.snapshot("XAUUSD", 60)
        del snapshot["15m"]
        results = self.engine.ingest_snapshot("XAUUSD", snapshot, as_of=at)
        self.assertEqual((results["15m"].presented, results["15m"].committed), (0, ()))
        broken, at = self.snapshot("EURUSD", 60)
        broken["5m"]["symbol"] = "XAUUSD"
        with self.assertRaises(EvidenceError):
            self.engine.ingest_snapshot("EURUSD", broken, as_of=at)
        self.assertEqual(self.engine.committed("EURUSD", "1h"), ())

    def test_frame_adapter_rejections(self):
        frame = v1_frames(T0)["5m"]  # Already the V1 shape: tz-aware index, symbol, is_closed.
        self.assertEqual(len(bars_from_frame(frame, symbol="XAUUSD", timeframe="5m")), 1)
        naive = frame.copy()
        naive.index = naive.index.tz_localize(None)
        duplicated = pd.concat([frame, frame])
        missing = frame.drop(columns=["is_closed"])
        mislabeled = frame.assign(timeframe="1h")
        for bad in (naive, duplicated, missing, mislabeled, "not a frame"):
            with self.assertRaises(EvidenceError):
                bars_from_frame(bad, symbol="XAUUSD", timeframe="5m")
        self.assertEqual(bars_from_frame(frame.iloc[0:0], symbol="XAUUSD", timeframe="5m"), ())

    def test_timeframes_match_v1_providers(self):
        self.assertEqual(TIMEFRAMES, {tf: spec[1] for tf, spec in TWELVE_DATA_TIMEFRAMES.items()})
        self.assertEqual(TIMEFRAMES, {tf: spec[2] for tf, spec in MASSIVE_TIMEFRAMES.items()})


class Nas100Tests(EvidenceCase):
    def test_nas100_remains_off(self):
        self.assertNotIn("NAS100", DEFAULT_ENABLED_SYMBOLS)
        self.assertNotIn("NAS100", self.engine.enabled_symbols)
        with self.assertRaises(EvidenceError):
            self.engine.ingest("NAS100", "5m", [bar(0, symbol="NAS100")], as_of=after(0))
        self.assertEqual(self.store.db.execute("SELECT COUNT(*) FROM market_evidence").fetchone()[0], 0)


class PersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-evidence-store-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "market_evidence.db"

    def test_fresh_store_identity_and_reopen(self):
        store = EvidenceStore(self.path)
        MarketEvidenceEngine(store).ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        self.assertEqual(store.db.execute("PRAGMA application_id").fetchone()[0], EVIDENCE_APPLICATION_ID)
        self.assertEqual(store.db.execute("SELECT version FROM evidence_schema_info").fetchone()[0],
                         EVIDENCE_SCHEMA_VERSION)
        store.close()
        reopened = EvidenceStore(self.path)
        self.addCleanup(reopened.close)
        self.assertEqual(MarketEvidenceEngine(reopened).committed("XAUUSD", "5m"), (bar(0),))
        tables = {r[0] for r in reopened.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertEqual(tables, set(EVIDENCE_TABLES))

    def test_read_only_mode(self):
        with self.assertRaises(EvidenceStoreError):
            EvidenceStore(self.path, readonly=True)
        EvidenceStore(self.path).close()
        before = digest(self.path)
        readonly = EvidenceStore(self.path, readonly=True)
        self.addCleanup(readonly.close)
        engine = MarketEvidenceEngine(readonly)
        self.assertEqual(engine.committed("XAUUSD", "5m"), ())
        with self.assertRaises(EvidenceStoreError):
            engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        self.assertEqual(digest(self.path), before)

    def test_trading_db_and_foreign_databases_are_refused_and_never_written(self):
        trading = self.dir / "trading_floor.db"
        Store(trading).close()
        health = self.dir / "system_health.db"
        HealthStore(health).close()
        foreign = self.dir / "foreign.db"
        connection = sqlite3.connect(foreign)
        connection.execute("CREATE TABLE t(x)")
        connection.commit()
        connection.close()
        for path in (trading, health, foreign):
            with self.subTest(path=path.name):
                before = digest(path)
                with self.assertRaises(EvidenceStoreError):
                    EvidenceStore(path)
                self.assertEqual(digest(path), before)
        evidence = self.dir / "evidence.db"
        EvidenceStore(evidence).close()
        with self.assertRaises(HealthStoreError):
            HealthStore(evidence)  # The Phase 1 sidecar refuses the Phase 2 one, too.
        other = Store(self.dir / "other_trading.db")
        self.addCleanup(other.close)
        with self.assertRaises(ValueError):
            MarketEvidenceEngine(other)

    def test_never_created_under_the_trading_db_name(self):
        path = self.dir / "runtime" / "trading_floor.db"
        with self.assertRaises(EvidenceStoreError):
            EvidenceStore(path)
        self.assertFalse(path.exists())
        Store(path).close()  # The trading DB can still initialize at its own path.

    def test_corrupt_and_incompatible_stores_fail_safely(self):
        self.path.write_bytes(b"not a sqlite database" * 100)
        before = digest(self.path)
        with self.assertRaises(EvidenceStoreError):
            EvidenceStore(self.path)
        self.assertEqual(digest(self.path), before)
        other = self.dir / "versioned.db"
        EvidenceStore(other).close()
        connection = sqlite3.connect(other)
        connection.execute("UPDATE evidence_schema_info SET version=99")
        connection.commit()
        connection.close()
        with self.assertRaises(EvidenceStoreError):
            EvidenceStore(other)

    def test_trading_db_untouched_by_evidence_processing(self):
        trading = self.dir / "trading_floor.db"
        store = Store(trading)
        store.set_state("v1_evidence", "unchanged")
        store.close()
        before = digest(trading)
        evidence = EvidenceStore(self.dir / "market_evidence.db")
        self.addCleanup(evidence.close)
        engine = MarketEvidenceEngine(evidence)
        engine.ingest("XAUUSD", "5m", [bar(i) for i in range(6)], as_of=after(5))
        engine.ingest("EURUSD", "1h", [bar(i, symbol="EURUSD", timeframe="1h") for i in range(2)], as_of=after(1, "1h"))
        self.assertEqual(digest(trading), before)
        readonly = Store(trading, readonly=True)
        self.addCleanup(readonly.close)
        self.assertEqual(readonly.db.execute("SELECT version FROM schema_info").fetchone()[0], 3)
        tables = {r[0] for r in readonly.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertFalse(tables & set(EVIDENCE_TABLES))


class SafetyTests(unittest.TestCase):
    FORBIDDEN_MODULES = {"execution", "riesgo", "floor", "agents", "ai", "estrategia", "operaciones", "urllib",
                         "requests", "socket", "time", "random", "uuid", "os"}
    FORBIDDEN_CALLS = {"submit_plan", "process_next_bar", "process_bar", "evaluar_trade_plan", "crear_trade_plan",
                       "save_paper", "load_snapshot", "now", "utcnow", "time", "uuid4", "run_cycle", "run_floor"}

    def test_engine_and_store_are_isolated_from_trading_and_network(self):
        for source in (Path(module.__file__), Path(store_module.__file__)):
            with self.subTest(module=source.name):
                tree = ast.parse(source.read_bytes())
                imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | {
                    a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                self.assertFalse({m for m in imports if m.split(".")[0] in self.FORBIDDEN_MODULES})
                calls = {getattr(n.func, "attr", getattr(n.func, "id", "")) for n in ast.walk(tree)
                         if isinstance(n, ast.Call)}
                self.assertFalse(calls & self.FORBIDDEN_CALLS)

    def test_not_wired_into_the_runtime(self):
        users = []
        for path in ROOT.rglob("*.py"):
            if ".venv" in path.parts or path.name.startswith("test_") or path.name in {
                    "market_evidence.py", "evidence_store.py"}:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if "market_evidence" in text or "evidence_store" in text:
                users.append(path.relative_to(ROOT).as_posix())
        self.assertEqual(users, [])


if __name__ == "__main__":
    unittest.main()
