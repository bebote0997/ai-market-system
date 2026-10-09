"""V2 Phase 8 / P3 REX writer (runtime/rex.py): encoding, journal persistence, write evidence, integrity flags.

Temporary databases and test scenarios only; REX stays OFF by default (``AI_FLOOR_V2_REX``). Shared scenario helpers
for the other P3 test modules live here.
"""
from contextlib import ExitStack
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid

from ai.provider import DeterministicAIProvider
from execution.contracts import PaperAccount
from execution.paper_broker import PaperBroker
from runtime import rex
from runtime.config import RuntimeConfig
from runtime.rex import (REX_RUN, REX_VERSION, REX_WRITE, RexProviderProbe, RexRecorder, RexStore, rex_encode,
                         rex_payload)
from runtime.service import OperationalRuntime
from storage.database import Store
from storage.economic_digest import H, edg, psh
from test_demo_runner import Data, T, instrument, macro_fixture, patched_scouts

ACCOUNT = "paper-main"
PLAN_STEPS = ((0, 100.), (15, 100.), (30, 80.), (45, 100.))  # submit, fill, stop (management), new submit


class Ids:
    """Deterministic uuid4 for twin comparisons (REX itself never draws a uuid)."""

    def __init__(self):
        self.n = 0

    def __call__(self):
        self.n += 1
        return uuid.UUID(int=self.n)


def plan_scenario(db, *, rex_on=True, steps=PLAN_STEPS, extra=None):
    """XAUUSD LONG setups through the real OperationalRuntime (legacy path); returns the run_cycle results."""
    config = RuntimeConfig(db_path=Path(db), enabled_symbols=("XAUUSD", "EURUSD"), v2_rex=rex_on)
    results = []
    with ExitStack() as stack:
        stack.enter_context(patch("uuid.uuid4", Ids()))
        for patcher in extra or ():
            stack.enter_context(patcher)
        for minutes, close in steps:
            runtime = OperationalRuntime(config, market_provider=Data(close=close),
                                         ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                                         instruments={"XAUUSD": instrument()},
                                         clock=lambda m=minutes: T + timedelta(minutes=m))
            try:
                with patched_scouts("LONG"):
                    results.append(runtime.run_cycle("XAUUSD", T + timedelta(minutes=minutes)))
            finally:
                runtime.close()
    return results


def catch_up_scenario(db, ev, *, rex_on=True, symbols=("XAUUSD",)):
    """Seeded open positions, XAUUSD in the catch-up scope (evidence DB ``ev``); EURUSD legacy."""
    from test_phase8_observe_only import EUR_STOP_T2, XAU_STOP_T3, make
    from test_runtime_catch_up import FIVE, SLOT, seed
    seed(db, positions=("XAUUSD", "EURUSD"))
    results = []
    with patch("uuid.uuid4", Ids()):
        for slot in (SLOT, SLOT + 3 * FIVE):
            runtime = make(db, ev, slot, ("XAUUSD",), {**XAU_STOP_T3, **EUR_STOP_T2})
            runtime.config = replace(runtime.config, v2_rex=rex_on)
            try:
                for symbol in symbols:
                    results.append(runtime.run_cycle(symbol, slot))
            finally:
                runtime.close()
    return results


def rex_rows(db, kind=None):
    conn = sqlite3.connect(db)
    try:
        rows = conn.execute("SELECT id, run_id, event_type, payload FROM journal WHERE source='rex' ORDER BY id")
        out = []
        for journal_id, run_id, event_type, payload in rows:
            body = json.loads(payload)
            record = json.loads(body["rex"]) if "rex" in body else body
            if kind is None or event_type == kind:
                out.append((journal_id, run_id, event_type, record, body))
        return out
    finally:
        conn.close()


class Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p3-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "trading_floor.db"


class EncodingTests(unittest.TestCase):
    def test_typed_unambiguous_encoding(self):
        at = datetime(2026, 1, 15, 13, 30, tzinfo=timezone(timedelta(hours=1)))
        self.assertEqual(rex_encode(0.1 + 0.2), {"$f": (0.1 + 0.2).hex()})
        self.assertNotEqual(rex_encode(0.1 + 0.2), rex_encode(0.3))
        self.assertEqual(rex_encode(Decimal("1.50")), {"$d": "1.50"})
        self.assertEqual(rex_encode(at), {"$t": "2026-01-15T12:30:00+00:00"})
        self.assertEqual(rex_encode((1, True, None, "0x1p+0")), [1, True, None, "0x1p+0"])  # text stays text
        self.assertEqual(rex_encode(PaperAccount("1.0", "a", 1.0, 1.0, 1.0))["$type"], "PaperAccount")

    def test_rejections(self):
        for bad in (float("nan"), float("inf"), datetime(2026, 1, 1), {"$f": "x"}, {1: 2}, object(), {1, 2}):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                rex_encode(bad)

    def test_payload_digest_and_size_limit(self):
        payload = rex_payload({"a": 1.5})
        self.assertEqual(payload["rex_digest"], H(REX_VERSION, payload["rex"]))
        self.assertEqual(json.loads(payload["rex"]), {"a": {"$f": (1.5).hex()}})
        with patch.object(rex, "REX_MAX_BYTES", 5), self.assertRaises(rex.RexError):
            rex_payload({"a": "too long"})


class RuntimeRecordingTests(Temp):
    def test_off_by_default_writes_no_rex(self):
        self.assertFalse(RuntimeConfig().v2_rex)
        plan_scenario(self.db, rex_on=False)
        self.assertEqual(rex_rows(self.db), [])

    def test_runs_and_writes_are_recorded_and_linked(self):
        results = plan_scenario(self.db)
        self.assertEqual(results, ["PLAN_READY", "PLAN_READY", "ERROR", "PLAN_READY"])
        runs = rex_rows(self.db, REX_RUN)
        writes = rex_rows(self.db, REX_WRITE)
        self.assertEqual(len(runs), 4)
        self.assertTrue(writes)
        self.assertFalse(rex_rows(self.db, "REX_FAILURE"))
        for journal_id, run_id, _, record, body in runs + writes:
            self.assertEqual(body["rex_digest"], H(REX_VERSION, body["rex"]))
            self.assertEqual(record["rex_version"], REX_VERSION)
            self.assertEqual(record["run_id"], run_id)
            self.assertTrue(record["complete"])
        by_id = {journal_id: record for journal_id, _, _, record, _ in writes}
        for run_journal, run_id, _, record, _ in runs:
            for item in record["writes"]:
                self.assertEqual(by_id[item["rex_journal_id"]]["run_id"], run_id)
                self.assertLess(item["rex_journal_id"], run_journal)
        stages = [w["context"]["stage"] for _, _, _, w, _ in writes]
        self.assertIn("ST9", stages)
        self.assertIn("ST7", stages)
        self.assertIn("ST2L", stages)
        events = [e["event_type"] for _, _, _, w, _ in writes for e in w["journal_events"]]
        self.assertEqual(sorted(set(events)), ["ORDER_FILLED", "ORDER_SUBMITTED", "POSITION_CLOSED",
                                               "POSITION_OPENED", "STOP_HIT"])

    def test_write_evidence_matches_the_committed_database(self):
        plan_scenario(self.db)
        conn = sqlite3.connect(self.db)
        try:
            current = edg(conn)["edg"]
        finally:
            conn.close()
        writes = [w for _, _, _, w, _ in rex_rows(self.db, REX_WRITE)]
        self.assertEqual(writes[-1]["edg_after"], current)
        for before, after in zip(writes, writes[1:]):
            self.assertEqual(after["edg_before"], before["edg_after"])  # nothing between writes changed EDG
            self.assertEqual(after["psh_before"], before["psh_after"])
        for w in writes:
            self.assertEqual(w["integrity"], {"foreign_write_detected": False, "same_connection_write_detected": False,
                                              "expected_matches_pre": True, "saved_matches_post": True})
            self.assertEqual(psh((w["post_state"][0], *map(tuple, w["post_state"][1:]))), w["psh_after"])

    def test_rex_events_never_become_notifications(self):
        plan_scenario(self.db)
        store = Store(self.db)
        try:
            events = store.capture_notifications()
        finally:
            store.close()
        self.assertFalse([e for e in events if "REX" in str(getattr(e, "event_type", ""))])

    def test_runtime_provider_probe_sees_every_provider_request(self):
        plan_scenario(self.db, steps=((0, 100.),))
        record = rex_rows(self.db, REX_RUN)[0][3]
        self.assertEqual(sorted(q["agent_name"] for q in record["ai_requests"]),
                         sorted(e["agent"] for s in record["stages"] if s["stage"] == "ST5" for e in s["audit"]))


class WriteEvidenceTests(Temp):
    def setUp(self):
        super().setUp()
        self.store = Store(self.db)
        self.addCleanup(self.store.close)
        self.store.save_paper(PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0)))
        self.recorder = RexRecorder(self.store, account_id=ACCOUNT, run_id="run-1", slot_key="k", symbol="XAUUSD",
                                    slot=T, identity={})

    def broker(self):
        account, orders, fills = self.store.load_paper(ACCOUNT)
        broker = PaperBroker(account)
        broker.orders, broker.fills = orders, fills
        return broker

    def write(self, mutate=lambda b: None, between=None):
        broker = self.broker()
        expected = self.store.paper_state(broker.account, broker.orders, broker.fills)
        mutate(broker)
        proxy = RexStore(self.store, self.recorder)
        if between is not None:
            real = self.store.save_paper

            def save(*args, **kwargs):
                between()
                return real(*args, **kwargs)
            with patch.object(self.store, "save_paper", side_effect=save):
                return proxy.save_paper(broker, expected_state=expected)
        return proxy.save_paper(broker, expected_state=expected)

    def last(self):
        return rex_rows(self.db, REX_WRITE)[-1][3]

    def test_proxy_returns_exactly_what_save_paper_returns(self):
        def bump(broker):
            broker.account.cash = 9000.0
        self.assertIsNone(self.write(bump))
        self.assertEqual(self.last()["result"], "COMMITTED")
        self.assertTrue(self.last()["complete"])
        with patch.object(Store, "save_paper", return_value=False):
            self.assertIs(self.write(), False)
        with patch.object(Store, "save_paper", side_effect=RuntimeError("paper persistence without slot ownership")):
            with self.assertRaisesRegex(RuntimeError, "slot ownership"):
                self.write()
        self.assertEqual(self.last()["result"], "RAISED")

    def test_foreign_write_is_detected(self):
        def foreign():
            other = sqlite3.connect(self.db)
            other.execute("INSERT OR REPLACE INTO system_state(key,value) VALUES('x','1')")
            other.commit()
            other.close()
        self.write(between=foreign)
        entry = self.last()
        self.assertTrue(entry["integrity"]["foreign_write_detected"])
        self.assertFalse(entry["complete"])

    def test_same_connection_write_after_save_is_detected(self):
        real = self.store.save_paper

        def save(*args, **kwargs):
            result = real(*args, **kwargs)
            self.store.set_state("y", "1")  # autocommit write on the SAME connection, after the save
            return result
        broker = self.broker()
        expected = self.store.paper_state(broker.account, broker.orders, broker.fills)
        token = self.recorder.before_write(expected)
        with patch.object(self.store, "save_paper", side_effect=save):
            result = self.store.save_paper(broker, expected_state=expected)
        self.recorder.after_write(token, broker, result, total_changes_after_save=self.store.db.total_changes - 1)
        self.assertTrue(self.last()["integrity"]["same_connection_write_detected"])
        self.assertFalse(self.last()["complete"])

    def test_data_version_alone_misses_same_connection_writes(self):
        """O-6 limit, documented: PRAGMA data_version never changes for the connection's own commits."""
        conn = self.store.db
        before = conn.execute("PRAGMA data_version").fetchone()[0]
        self.store.set_state("z", "1")
        self.assertEqual(conn.execute("PRAGMA data_version").fetchone()[0], before)

    def test_edg_is_never_computed_inside_an_open_transaction(self):
        conn = self.store.db
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("INSERT OR REPLACE INTO system_state(key,value) VALUES('pending','1')")
        calls = []
        with patch.object(rex, "edg", side_effect=lambda c: calls.append(c)):
            token = self.recorder.before_write(None)
        self.assertEqual(calls, [])  # never called while the connection holds a transaction
        self.assertIsNone(token["pre"])  # refused, recorded as a failure
        self.assertTrue(conn.in_transaction)  # the caller's transaction is neither committed nor rolled back
        conn.execute("ROLLBACK")
        self.assertIn("write:pre_snapshot", [f["where"] for f in self.recorder.failures])

    def test_edg_is_computed_on_committed_state_only(self):
        seen = []
        real = rex.edg

        def spy(conn):
            result = real(conn)
            independent = sqlite3.connect(self.db)  # another connection sees only committed data
            try:
                seen.append(result == real(independent))
            finally:
                independent.close()
            return result
        with patch.object(rex, "edg", side_effect=spy):
            self.write(lambda b: setattr(b.account, "cash", 8000.0))
        self.assertEqual(seen, [True, True])

    def test_stale_then_retry_records_two_attempts(self):
        """NG14: a STALE first attempt and a committed second attempt; only the second has psh_after."""
        self.recorder.context("ST9", {})
        broker = self.broker()
        stale_expected = self.store.paper_state(broker.account, broker.orders, broker.fills)
        other = Store(self.db)
        try:
            moved = other.load_paper(ACCOUNT)[0]
            moved.cash = 7777.0
            other.save_paper(PaperBroker(moved))
        finally:
            other.close()
        proxy = RexStore(self.store, self.recorder)
        self.assertIs(proxy.save_paper(broker, expected_state=stale_expected), False)
        fresh = self.broker()
        expected = self.store.paper_state(fresh.account, fresh.orders, fresh.fills)
        fresh.account.cash = 1.0
        self.assertIsNone(proxy.save_paper(fresh, expected_state=expected))
        first, second = [w for _, _, _, w, _ in rex_rows(self.db, REX_WRITE)]
        self.assertEqual((first["attempt"], first["result"], first["psh_after"]), (1, "STALE", None))
        self.assertEqual((second["attempt"], second["result"]), (2, "COMMITTED"))
        self.assertIsNotNone(second["psh_after"])
        self.assertEqual(first["expected_psh"], psh(stale_expected))


class ProbeTests(unittest.TestCase):
    def test_probe_records_identity_and_delegates_everything(self):
        @dataclass
        class Request:
            schema_version: str = "1.0"
            run_id: str = "r"
            symbol: str = "XAUUSD"
            as_of: datetime = T
            agent_name: str = "structure_ai"
            role: str = "x"
            prompt_version: str = "p"

        class Provider:
            metadata = {"model": "m"}
            last_call = {"attempts": 1}

            def generate(self, request):
                return ("response", request.run_id)

        recorder = RexRecorder.__new__(RexRecorder)
        recorder.ai_requests, recorder.failures = [], []
        recorder.run_id = recorder.symbol = None
        probe = RexProviderProbe(Provider(), recorder)
        self.assertEqual(probe.generate(Request()), ("response", "r"))
        self.assertEqual((probe.metadata, probe.last_call), ({"model": "m"}, {"attempts": 1}))
        self.assertEqual(rex_encode(recorder.ai_requests)[0]["symbol"], "XAUUSD")


if __name__ == "__main__":
    unittest.main()
