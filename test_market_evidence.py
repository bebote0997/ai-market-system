"""V2 Phase 2 / Batch 1: Market Evidence Engine foundation and H02 evidence processing."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
import os
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

    def test_process_crash_in_duplicate_check_transaction_loses_nothing(self):
        # The first transaction of this presentation is the already-committed T0 (a DUPLICATE, no
        # INSERT); the crash happens there, before any new bar is processed.
        self.engine.ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
        self.store.close()
        child = self.run_child("""
            import contextlib
            from storage import evidence_store
            @contextlib.contextmanager
            def crash_in_first_transaction(self):
                self.db.execute("BEGIN IMMEDIATE")
                yield
                os._exit(17)  # Hard crash inside the first (duplicate T0) transaction.
            evidence_store.EvidenceStore.transaction = crash_in_first_transaction
            engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        """)
        self.assertEqual(child.returncode, 17, child.stderr)
        self.open()
        self.assertEqual(self.starts(self.stream()), [bar(0).start])
        result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
        self.assertEqual(self.starts(result.committed), [bar(i).start for i in (1, 2, 3, 4)])

    def test_process_crash_after_real_insert_before_commit_then_restart(self):
        # Real pre-commit window: the crash happens inside the transaction that has just INSERTed a
        # genuinely new bar, before COMMIT. Crash points: first new bar (T1) and a middle bar (T2).
        for crash_on_insert in (1, 2):
            with self.subTest(crash_on_insert=crash_on_insert):
                self.store.close()
                self.path = self.dir / f"crash-on-insert-{crash_on_insert}.db"
                self.open().ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
                self.store.close()
                child = self.run_child(f"""
                    import contextlib
                    from storage import evidence_store
                    inserts = []
                    @contextlib.contextmanager
                    def crash_after_insert_before_commit(self):
                        self.db.execute("BEGIN IMMEDIATE")
                        changes = self.db.total_changes
                        yield
                        if self.db.total_changes > changes:  # This transaction INSERTed new evidence.
                            inserts.append(1)
                            if len(inserts) == {crash_on_insert}:
                                pending = self.db.execute("SELECT bar_start FROM market_evidence "
                                                          "ORDER BY stream_seq DESC LIMIT 1").fetchone()[0]
                                print("UNCOMMITTED", pending, flush=True)
                                os._exit(19)  # Hard crash: INSERT done, COMMIT never executed.
                        self.db.execute("COMMIT")
                    evidence_store.EvidenceStore.transaction = crash_after_insert_before_commit
                    engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
                """)
                self.assertEqual(child.returncode, 19, child.stderr)
                pending = bar(crash_on_insert)
                self.assertEqual(child.stdout.split(), ["UNCOMMITTED", pending.bar_start])  # Inserted, in the txn.
                self.open()
                survivors = [bar(i).start for i in range(crash_on_insert)]
                self.assertEqual(self.starts(self.stream()), survivors)  # The uncommitted bar was not kept.
                result = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
                self.assertEqual(self.starts(result.committed), [bar(i).start for i in range(crash_on_insert, 5)])
                self.assertEqual(len(result.duplicates), crash_on_insert)
                self.assertEqual(self.starts(self.stream()), [bar(i).start for i in range(5)])  # None lost.
                self.assertEqual([r[3] for r in self.dump()[0]], [1, 2, 3, 4, 5])  # Each committed once.
                self.assertEqual(self.engine.anomalies("XAUUSD", "5m"), ())
                again = self.engine.ingest("XAUUSD", "5m", [bar(i) for i in range(5)], as_of=after(4))
                self.assertEqual((again.committed, len(again.duplicates)), ((), 5))

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

    def assert_refused_unchanged(self, path):
        listing = sorted(p.name for p in path.parent.iterdir())
        before = (digest(path), path.stat().st_size) if path.is_file() else None
        for readonly in (False, True):
            try:
                store = EvidenceStore(path, readonly=readonly)
            except EvidenceStoreError:
                continue
            store.close()
            self.fail(f"{path.name} was accepted as a MARKET EVIDENCE database (readonly={readonly})")
        self.assertEqual((digest(path), path.stat().st_size) if path.is_file() else None, before)  # Unchanged.
        self.assertEqual(sorted(p.name for p in path.parent.iterdir()), listing)  # No journal/WAL/new file.

    def test_m1_trading_db_refused_by_name_and_identity_before_any_write(self):
        cases = self.dir / "cases"
        cases.mkdir()
        for index, name in enumerate(("trading_floor.db", "TRADING_FLOOR.DB")):  # A, B: existing EMPTY files.
            with self.subTest(case=f"empty {name}"):
                folder = cases / f"empty{index}"  # Separate folders: no reliance on filesystem case rules.
                folder.mkdir()
                (folder / name).write_bytes(b"")
                self.assert_refused_unchanged(folder / name)
                self.assertEqual((folder / name).read_bytes(), b"")
        for name in ("Trading_Floor.Db", "trading_floor.db.", "trading_floor.db ", "trading_floor.db:evidence",
                     "nested/../trading_floor.db"):  # Missing paths: case/normalization variants.
            with self.subTest(case=f"variant {name!r}"):
                folder = cases / f"variant{len(list(cases.iterdir()))}"
                (folder / "nested").mkdir(parents=True)
                with self.assertRaises(EvidenceStoreError):
                    EvidenceStore(folder / name)
                self.assertEqual(sorted(p.name for p in folder.iterdir()), ["nested"])  # Nothing created.
        real = cases / "real" / "trading_floor.db"  # C: an initialized trading DB, by name and by copy.
        Store(real).close()
        self.assert_refused_unchanged(real)
        copy = cases / "real" / "scratch_copy.db"
        copy.write_bytes(real.read_bytes())
        self.assert_refused_unchanged(copy)
        empty = cases / "linked" / "trading_floor.db"  # Same file under another name (hard link).
        empty.parent.mkdir()
        empty.write_bytes(b"")
        os.link(empty, cases / "linked" / "evidence.db")
        self.assert_refused_unchanged(cases / "linked" / "evidence.db")
        self.assertEqual(empty.read_bytes(), b"")
        health = cases / "system_health.db"  # D: Phase 1 sidecar.
        HealthStore(health).close()
        self.assert_refused_unchanged(health)
        foreign = cases / "foreign.db"  # E: unrelated SQLite database.
        connection = sqlite3.connect(foreign)
        connection.execute("CREATE TABLE t(x)")
        connection.commit()
        connection.close()
        self.assert_refused_unchanged(foreign)
        for name in ("market_evidence.db", "trading_floor_evidence.db"):  # F: legitimate new evidence DBs.
            with self.subTest(case=f"new {name}"):
                store = EvidenceStore(cases / "new" / name)
                MarketEvidenceEngine(store).ingest("XAUUSD", "5m", [bar(0)], as_of=after(0))
                store.close()
                reopened = EvidenceStore(cases / "new" / name, readonly=True)
                self.addCleanup(reopened.close)
                self.assertEqual(MarketEvidenceEngine(reopened).committed("XAUUSD", "5m"), (bar(0),))

    @staticmethod
    def sqlite_target(store):
        return Path(next(row[2] for row in store.db.execute("PRAGMA database_list") if row[1] == "main")).resolve()

    def test_m1_uri_significant_path_never_reaches_the_protected_trading_db(self):
        # Independent reproduction: "trading_floor.db#evidence" was validated as another name but
        # SQLite parsed "#evidence" as a URI fragment and initialized the empty trading DB.
        folder = self.dir / "uri"
        (folder / "nested").mkdir(parents=True)
        protected = folder / "trading_floor.db"
        protected.write_bytes(b"")
        before = (protected.stat().st_size, digest(protected))
        listing = set(p.name for p in folder.iterdir())
        requested = set()
        for suffix in ("#evidence", "#", "?mode=rwc", "?mode=ro#x", "%23evidence", "%3Fmode=rwc", " #x",
                       "#é市場"):
            for relative in (f"trading_floor.db{suffix}", f"nested/../trading_floor.db{suffix}"):
                path = folder / relative
                for readonly in (False, True):
                    with self.subTest(path=relative, readonly=readonly):
                        try:
                            store = EvidenceStore(path, readonly=readonly)
                        except EvidenceStoreError:
                            continue  # Refused (e.g. '?' is not a valid Windows filename, or missing).
                        try:  # Accepted only as the literal, distinct file that was validated.
                            self.assertEqual(self.sqlite_target(store), path.resolve())
                            self.assertNotEqual(path.resolve().name, protected.name)
                            requested.add(path.resolve().name)
                        finally:
                            store.close()
        self.assertEqual((protected.stat().st_size, digest(protected)), before)  # Never opened/initialized.
        created = set(p.name for p in folder.iterdir()) - listing
        self.assertEqual(created, requested)  # Only the literal requested files; no journal/WAL/SHM/side file.
        self.assertFalse({n for n in created if n.endswith(("-journal", "-wal", "-shm"))})

    def test_m1_legitimate_uri_significant_filenames_open_their_own_file(self):
        folder = self.dir / "legit"
        folder.mkdir()
        names = ["market#evidence.db", "market%23evidence.db", "market%2523evidence.db", "market evidence.db",
                 "market?evidence.db", "évidence_市場.db"]
        usable = []
        for name in names:  # Filenames the platform cannot represent (e.g. '?' on Windows) must fail closed.
            probe = folder / "probe"
            probe.mkdir(exist_ok=True)
            try:
                (probe / name).write_bytes(b"")
                (probe / name).unlink()
                usable.append(name)
            except OSError:
                with self.assertRaises(EvidenceStoreError):
                    EvidenceStore(folder / name)
                self.assertFalse((folder / name).exists())
        self.assertIn("market#evidence.db", usable)
        for index, name in enumerate(usable):  # Distinct close per file: any aliasing would show up.
            store = EvidenceStore(folder / name)
            self.assertEqual(self.sqlite_target(store), (folder / name).resolve())
            MarketEvidenceEngine(store).ingest("XAUUSD", "5m", [bar(0, close=200.0 + index)], as_of=after(0))
            store.close()
        for index, name in enumerate(usable):
            for readonly in (False, True):
                with self.subTest(name=name, readonly=readonly):
                    store = EvidenceStore(folder / name, readonly=readonly)
                    try:
                        self.assertEqual(self.sqlite_target(store), (folder / name).resolve())
                        self.assertEqual(MarketEvidenceEngine(store).committed("XAUUSD", "5m"),
                                         (bar(0, close=200.0 + index),))
                    finally:
                        store.close()
        self.assertEqual(sorted(p.name for p in folder.iterdir() if p.is_file()), sorted(usable))

    SCHEMA = {
        "market_evidence": "symbol TEXT NOT NULL, timeframe TEXT NOT NULL, bar_start TEXT NOT NULL, "
                           "stream_seq INTEGER NOT NULL, payload TEXT NOT NULL, digest TEXT NOT NULL, "
                           "PRIMARY KEY(symbol,timeframe,bar_start), UNIQUE(symbol,timeframe,stream_seq)",
        "evidence_anomalies": "anomaly_id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timeframe TEXT NOT NULL, "
                              "kind TEXT NOT NULL, bar_start TEXT NOT NULL, payload TEXT NOT NULL",
    }

    def crafted(self, name, extra="", **tables):
        """A database that identifies itself as a MARKET EVIDENCE sidecar (application_id, version,
        table names) with the given table definitions."""
        path = self.dir / name
        connection = sqlite3.connect(path)
        definitions = {**self.SCHEMA, **tables}
        connection.executescript(
            "CREATE TABLE evidence_schema_info(version INTEGER NOT NULL);" +
            "".join(f"CREATE TABLE {table}({body});" for table, body in definitions.items()) + extra +
            f"INSERT INTO evidence_schema_info(version) VALUES({EVIDENCE_SCHEMA_VERSION});"
            f"PRAGMA application_id={EVIDENCE_APPLICATION_ID};")
        connection.close()
        return path

    def test_m2_self_identified_store_with_wrong_schema_is_refused_on_open_unchanged(self):
        evidence = self.SCHEMA["market_evidence"]
        cases = {
            "missing_column": evidence.replace(", digest TEXT NOT NULL", ""),
            "renamed_column": evidence.replace("digest TEXT", "checksum TEXT"),
            "wrong_type": evidence.replace("stream_seq INTEGER", "stream_seq TEXT"),
            "nullable_column": evidence.replace("payload TEXT NOT NULL", "payload TEXT"),
            "missing_stream_seq_unique": evidence.replace(", UNIQUE(symbol,timeframe,stream_seq)", ""),
            "missing_primary_key": evidence.replace("PRIMARY KEY(symbol,timeframe,bar_start), ", ""),
            "narrower_primary_key": evidence.replace("PRIMARY KEY(symbol,timeframe,bar_start)",
                                                     "PRIMARY KEY(symbol,bar_start)"),
            "case_insensitive_identity": evidence.replace("symbol TEXT NOT NULL", "symbol TEXT NOT NULL COLLATE NOCASE"),
        }
        for name, body in cases.items():
            with self.subTest(case=name):
                path = self.crafted(f"{name}.db", market_evidence=body)
                self.assert_refused_unchanged(path)
        anomalies = self.SCHEMA["evidence_anomalies"].replace("anomaly_id TEXT PRIMARY KEY", "anomaly_id TEXT NOT NULL")
        with self.subTest(case="missing_anomaly_identity"):
            self.assert_refused_unchanged(self.crafted("anomaly.db", evidence_anomalies=anomalies))
        with self.subTest(case="extra_unique_constraint"):
            self.assert_refused_unchanged(self.crafted("extra_unique.db", "CREATE UNIQUE INDEX u ON market_evidence(symbol);"))
        with self.subTest(case="trigger"):
            self.assert_refused_unchanged(self.crafted(
                "trigger.db", "CREATE TRIGGER t AFTER INSERT ON market_evidence BEGIN DELETE FROM market_evidence; END;"))

    def test_m2_behavior_changing_constraints_are_refused_on_open_unchanged(self):
        # Each schema identifies itself as a sidecar but can make a canonical write behave differently.
        evidence, anomalies = self.SCHEMA["market_evidence"], self.SCHEMA["evidence_anomalies"]
        cases = {
            # A: independent finding — 2 GAPs detected, 1 persisted through INSERT OR IGNORE.
            "partial_unique_anomalies": ("CREATE UNIQUE INDEX g ON evidence_anomalies(symbol) WHERE kind='GAP';", {}),
            # B: partial indexes able to block valid bars.
            "partial_unique_bars": ("CREATE UNIQUE INDEX p ON market_evidence(symbol) WHERE stream_seq > 1;", {}),
            "partial_index_failing_predicate": ("CREATE INDEX p ON market_evidence(symbol) WHERE json(symbol);", {}),
            "expression_index": ("CREATE INDEX e ON market_evidence(json(symbol));", {}),
            # C: CHECK constraints (table- and column-level).
            "check_table": ("", {"market_evidence": evidence + ", CHECK(stream_seq < 2)"}),
            "check_column": ("", {"market_evidence": evidence.replace("payload TEXT NOT NULL",
                                                                      "payload TEXT NOT NULL CHECK(length(payload) < 9)")}),
            # D: foreign key.
            "foreign_key": ("", {"evidence_anomalies": anomalies.replace(
                "symbol TEXT NOT NULL", "symbol TEXT NOT NULL REFERENCES market_evidence(symbol)")}),
            # E: trigger on the anomalies table.
            "trigger_anomalies": ("CREATE TRIGGER t BEFORE INSERT ON evidence_anomalies BEGIN SELECT RAISE(IGNORE); END;", {}),
            # F: incompatible collation (unique key, non-key column).
            "nocase_unique_key": ("", {"market_evidence": evidence.replace(
                "UNIQUE(symbol,timeframe,stream_seq)", "UNIQUE(symbol COLLATE NOCASE,timeframe,stream_seq)")}),
            "nocase_column": ("", {"evidence_anomalies": anomalies.replace("kind TEXT NOT NULL",
                                                                           "kind TEXT NOT NULL COLLATE NOCASE")}),
            # G: extra UNIQUE constraints.
            "extra_table_unique": ("", {"market_evidence": evidence + ", UNIQUE(digest)"}),
            "extra_unique_anomalies": ("CREATE UNIQUE INDEX u ON evidence_anomalies(bar_start);", {}),
            # Other behavior-changing mechanisms.
            "on_conflict_replace": ("", {"market_evidence": evidence.replace(
                "UNIQUE(symbol,timeframe,stream_seq)", "UNIQUE(symbol,timeframe,stream_seq) ON CONFLICT REPLACE")}),
            "not_null_on_conflict_ignore": ("", {"evidence_anomalies": anomalies.replace(
                "payload TEXT NOT NULL", "payload TEXT NOT NULL ON CONFLICT IGNORE")}),
            "generated_column": ("", {"market_evidence": evidence.replace(
                "digest TEXT NOT NULL", "digest TEXT NOT NULL, derived TEXT GENERATED ALWAYS AS (json(symbol)) VIRTUAL")}),
            "numeric_affinity": ("", {"market_evidence": evidence.replace("digest TEXT", "digest NUMERIC")}),
            "strict_table": ("", {"market_evidence": evidence + ") STRICT; SELECT (1"}),
            "without_rowid": ("", {"evidence_anomalies": anomalies + ") WITHOUT ROWID; SELECT (1"}),
        }
        for name, (extra, tables) in cases.items():
            with self.subTest(case=name):
                self.assert_refused_unchanged(self.crafted(f"behavior_{name}.db", extra, **tables))

    def test_m2_canonical_schema_opens_and_persists_every_detected_anomaly(self):
        path = self.crafted("canonical.db")  # H: the canonical definitions, built outside EvidenceStore.
        store = EvidenceStore(path)
        self.addCleanup(store.close)
        engine = MarketEvidenceEngine(store)
        result = engine.ingest("XAUUSD", "5m", [bar(0), bar(2), bar(4)], as_of=after(4))
        self.assertEqual(len(result.gaps), 2)
        self.assertEqual([a.kind for a in engine.anomalies()], ["GAP", "GAP"])  # Detected == persisted.

    def test_m2_semantically_equivalent_schema_opens_and_ingests_normally(self):
        # I: formatting/case, column and key order, same-affinity type spellings, explicit BINARY
        # collation, DEFAULT values, quoted identifiers, comments and literals that merely contain
        # constraint keywords, and plain non-unique indexes cannot change a canonical write.
        path = self.crafted(
            "equivalent.db", "CREATE UNIQUE INDEX seq_identity ON market_evidence(timeframe, stream_seq, symbol);"
                             "CREATE INDEX by_digest ON market_evidence(digest);"
                             "CREATE INDEX by_kind ON evidence_anomalies(kind, bar_start DESC);",
            market_evidence="stream_seq integer not null, digest varchar(64) not null, payload text not null, "
                            "bar_start text not null collate binary, \"timeframe\" character(3) not null, "
                            "symbol text not null default 'CHECK REFERENCES COLLATE NOCASE', "
                            "constraint identity primary key (bar_start, symbol, timeframe) -- no CHECK here\n",
            evidence_anomalies="payload text not null, kind text not null, bar_start text not null, "
                               "timeframe text not null, symbol text not null, anomaly_id text, "
                               "/* STRICT? no */ primary key (anomaly_id)")
        store = EvidenceStore(path)
        self.addCleanup(store.close)
        engine = MarketEvidenceEngine(store)
        engine.ingest("XAUUSD", "5m", [bar(0), bar(2)], as_of=after(2))
        self.assertEqual(engine.committed("XAUUSD", "5m"), (bar(0), bar(2)))
        self.assertEqual([a.kind for a in engine.anomalies()], ["GAP"])
        self.assertEqual(engine.ingest("XAUUSD", "5m", [bar(0), bar(2)], as_of=after(2)).committed, ())

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
        # B2.3B: the runtime is the only user, behind v2_position_catch_up (OFF by default).
        self.assertEqual(sorted(users), ["runtime/config.py", "runtime/service.py"])


if __name__ == "__main__":
    unittest.main()
