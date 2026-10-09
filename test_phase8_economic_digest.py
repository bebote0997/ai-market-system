"""V2 Phase 8 / P2a-lib (DEC-8.22-c, restricted): deterministic economic digests (storage/economic_digest.py) and the
read-only verifier (replay/economic_verifier.py). Temporary databases only."""
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import subprocess
import sys
import tempfile
import unittest

from execution.contracts import PaperAccount, PaperFill, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager
from replay.economic_verifier import main as verifier_main, verify
from storage.database import Store
from storage.economic_digest import EDG_TABLES, H, cj, edg, expected_edg_genesis, psh
from test_phase8_observe_only import make
from test_runtime_catch_up import ACCOUNT, SLOT, T0, T1

ROOT = Path(__file__).resolve().parent


def populate(db):
    """Rows in all five PAPER tables through the real code paths, including a CLOSED position and a closed trade."""
    store = Store(db)
    try:
        broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
        for symbol, entry in (("XAUUSD", 100.0), ("EURUSD", 1.10)):
            order = PaperOrder("1.0", f"ord-{symbol}", f"run-{symbol}", symbol, "LONG", 10.0, entry, entry * .95,
                               entry * 1.10, 1.0, 10000.0, 0.0, T0 - timedelta(minutes=5), status="FILLED")
            broker.orders[order.order_id] = order
            fill = PaperFill("1.0", f"fill-{symbol}", order.order_id, order.run_id, symbol, "LONG", 10.0, entry, entry,
                             T0)
            broker.fills[fill.fill_id] = fill
            broker.account.open_positions[symbol] = PaperPosition(
                "1.0", f"pos-{symbol}", order.order_id, order.run_id, symbol, "LONG", 10.0, entry, entry,
                order.stop, order.target, T0, last_price=entry, contract_multiplier=1.0)
        broker.orders["pend"] = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "SHORT", 1.0, 100.0, 105.0, 85.0,
                                           1.0, 10000.0, 0.0, T0)
        store.save_paper(broker)
        account, orders, fills = store.load_paper(ACCOUNT)
        broker = PaperBroker(account)
        broker.orders, broker.fills = orders, fills
        TradeManager(account, broker).process_bar({"symbol": "XAUUSD", "timestamp": T1, "open": 100.0, "high": 100.5,
                                                   "low": 94.0, "close": 96.0, "is_closed": True})
        store.save_paper(broker)  # XAUUSD closed: paper_positions row -> CLOSED, closed_trades row inserted
    finally:
        store.close()


def digest_of(db):
    conn = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        return edg(conn)
    finally:
        conn.close()


def state_digest(db):
    store = Store(db, readonly=True)
    try:
        return psh(store.paper_state(*store.load_paper(ACCOUNT)))
    finally:
        store.close()


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p2a-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.db = self.dir / "trading_floor.db"
        populate(self.db)

    def raw(self, sql, params=()):
        conn = sqlite3.connect(self.db)  # simulated change on the temporary test DB only
        try:
            conn.execute(sql, params)
            conn.commit()
        finally:
            conn.close()


class CanonicalTests(unittest.TestCase):
    def test_real_text_blob_null_and_bool_are_unambiguous(self):
        self.assertEqual(cj(0.1 + 0.2), json.dumps((0.1 + 0.2).hex()))
        self.assertNotEqual(cj(0.1 + 0.2), cj(0.3))
        self.assertEqual(cj(3.0), '"0x1.8000000000000p+1"')
        self.assertEqual(cj("  ñ€ x é "), json.dumps("  ñ€ x é ", ensure_ascii=False))  # TEXT kept exactly
        self.assertEqual(cj(b"\x00\xff"), '{"blob_hex":"00ff"}')
        self.assertNotEqual(cj(b"\x00\xff"), cj("00ff"))
        self.assertEqual((cj(None), cj(True), cj(7)), ("null", "true", "7"))
        self.assertEqual(cj({"b": (1, 2), "a": None}), '{"a":null,"b":[1,2]}')

    def test_non_canonical_values_are_rejected(self):
        for bad in (float("nan"), float("inf"), {1: "x"}, object()):
            with self.subTest(bad=bad), self.assertRaises((ValueError, TypeError)):
                cj(bad)

    def test_h_is_domain_separated(self):
        self.assertNotEqual(H("A", "x"), H("B", "x"))
        self.assertEqual(H("A", "x"), hashlib.sha256("A\nx".encode("utf-8")).hexdigest())
        with self.assertRaises(TypeError):
            H("A", b"x")


class DeterminismTests(Base):
    def test_identical_across_processes(self):
        child = subprocess.run(
            [sys.executable, "-B", "-c",
             "import sqlite3, sys; from storage.economic_digest import edg; "
             "print(edg(sqlite3.connect(sys.argv[1]))['edg'])", str(self.db)],
            cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(child.stdout.strip(), digest_of(self.db)["edg"])

    def test_identical_after_vacuum(self):
        before = digest_of(self.db)
        self.raw("VACUUM")
        self.assertEqual(digest_of(self.db), before)

    def test_independent_of_insertion_order(self):
        other = self.dir / "reversed.db"
        Store(other).close()
        src, dst = sqlite3.connect(self.db), sqlite3.connect(other)
        try:
            for name, _ in EDG_TABLES:
                rows = src.execute(f"SELECT * FROM {name}").fetchall()
                dst.executemany(f"INSERT INTO {name} VALUES(?,?)", list(reversed(rows)))
            dst.commit()
        finally:
            src.close()
            dst.close()
        self.assertEqual(digest_of(other), digest_of(self.db))


class CoverageTests(Base):
    def test_any_row_change_in_each_economic_table_changes_edg(self):
        base = digest_of(self.db)
        self.assertTrue(all(base["tables"][name]["count"] > 0 for name, _ in EDG_TABLES))
        changes = {
            "paper_accounts": "UPDATE paper_accounts SET payload=payload||' '",
            "paper_orders": "UPDATE paper_orders SET payload=replace(payload,'10000.0','10000.5') WHERE order_id='pend'",
            "paper_fills": "UPDATE paper_fills SET payload=payload||' ' WHERE fill_id='fill-EURUSD'",
            "paper_positions": "UPDATE paper_positions SET payload=payload||' ' "
                               "WHERE json_extract(payload,'$.status')='CLOSED'",  # a CLOSED position row
            "closed_trades": "UPDATE closed_trades SET payload=payload||' '",
        }
        for name, sql in changes.items():
            with self.subTest(table=name):
                shutil.copyfile(self.db, self.dir / "backup.db")
                self.raw(sql)
                after = digest_of(self.db)
                self.assertNotEqual(after["edg"], base["edg"])
                self.assertNotEqual(after["tables"][name]["digest"], base["tables"][name]["digest"])
                shutil.copyfile(self.dir / "backup.db", self.db)
        self.raw("DELETE FROM paper_fills WHERE fill_id='fill-XAUUSD'")
        deleted = digest_of(self.db)
        self.assertNotEqual(deleted["edg"], base["edg"])
        self.raw("INSERT INTO closed_trades VALUES('extra','{}')")
        added = digest_of(self.db)
        self.assertNotEqual(added["edg"], deleted["edg"])
        self.assertEqual(added["tables"]["closed_trades"]["count"], base["tables"]["closed_trades"]["count"] + 1)

    def test_non_economic_tables_do_not_change_edg(self):
        base = digest_of(self.db)
        self.raw("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                 "VALUES('2026-01-15T13:30:00+00:00',NULL,'XAUUSD','x','X','INFO','{}')")
        self.raw("INSERT OR REPLACE INTO system_state(key,value) VALUES('anything','1')")
        self.raw("INSERT INTO review_reports(run_id,slot_key,payload) VALUES('r','s','{}')")
        self.assertEqual(digest_of(self.db), base)

    def test_psh_matches_the_cas_tuple_and_does_not_cover_closed_positions(self):
        store = Store(self.db, readonly=True)
        try:
            state = store.paper_state(*store.load_paper(ACCOUNT))
        finally:
            store.close()
        independent = hashlib.sha256(("V2PAPER/STATE/1\n" + json.dumps(
            [state[0], list(state[1]), list(state[2]), list(state[3]), list(state[4])],
            sort_keys=True, separators=(",", ":"), ensure_ascii=False)).encode("utf-8")).hexdigest()
        self.assertEqual(psh(state), independent)
        before_psh, before_edg = state_digest(self.db), digest_of(self.db)["edg"]
        self.raw("UPDATE paper_positions SET payload=payload||' ' WHERE json_extract(payload,'$.status')='CLOSED'")
        self.assertEqual(state_digest(self.db), before_psh)  # documented limit: psh is not a completeness proof
        self.assertNotEqual(digest_of(self.db)["edg"], before_edg)  # EDG sees it
        for bad in ((), ("a",), [None, (), (), (), ()], (None, (), (), (1,), ())):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                psh(bad)

    def test_schema_drift_fails_closed(self):
        empty = sqlite3.connect(":memory:")
        with self.assertRaises(ValueError):
            edg(empty)
        drift = sqlite3.connect(":memory:")
        for name, _ in EDG_TABLES:
            drift.execute(f"CREATE TABLE {name}(x TEXT)")
        with self.assertRaises((ValueError, sqlite3.Error)):
            edg(drift)

    def test_every_economic_column_is_text(self):
        conn = sqlite3.connect(self.db)
        try:
            for name, _ in EDG_TABLES:
                self.assertEqual({row[2] for row in conn.execute(f"PRAGMA table_info({name})")}, {"TEXT"})
        finally:
            conn.close()


class GenesisTests(unittest.TestCase):
    def test_expected_genesis_equals_a_db_bootstrapped_by_the_real_runtime(self):
        tmp = tempfile.TemporaryDirectory(prefix="v2-p2a-gen-")
        self.addCleanup(tmp.cleanup)
        db = Path(tmp.name) / "trading_floor.db"
        runtime = make(db, None, SLOT, ())  # OperationalRuntime bootstrap creates the account (flag OFF)
        runtime.close()
        self.assertEqual(digest_of(db), expected_edg_genesis(ACCOUNT, 10000.0))
        self.assertNotEqual(expected_edg_genesis(ACCOUNT, 10000.0)["edg"], expected_edg_genesis(ACCOUNT, 9999.0)["edg"])


class VerifierTests(Base):
    def test_read_only_report_on_a_read_only_source(self):
        before = hashlib.sha256(self.db.read_bytes()).hexdigest()
        os.chmod(self.db, stat.S_IREAD)  # the verifier needs no write access at all
        self.addCleanup(os.chmod, self.db, stat.S_IREAD | stat.S_IWRITE)
        out = self.dir / "report.json"
        self.assertEqual(verifier_main(["--trading-db", str(self.db), "--out", str(out)]), 0)
        report = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(hashlib.sha256(self.db.read_bytes()).hexdigest(), before)
        self.assertTrue(report["source_unchanged"] and report["read_only"] and report["account_present"])
        self.assertEqual((report["schema_version"], report["integrity_check"], report["edg_version"]), (3, "ok", 1))
        self.assertEqual(report["edg"], digest_of(self.db)["edg"])
        self.assertEqual(report["psh"], state_digest(self.db))
        self.assertEqual(report["tables"]["paper_positions"]["count"], 2)  # the CLOSED and the OPEN position

    def test_refuses_to_overwrite_the_source_and_missing_source(self):
        with self.assertRaises(SystemExit):
            verifier_main(["--trading-db", str(self.db), "--out", str(self.db)])
        with self.assertRaises(FileNotFoundError):
            verify(self.dir / "absent.db")

    def test_no_runtime_module_imports_the_library_yet(self):
        users = [p.name for p in (ROOT / "runtime").glob("*.py") if "economic_digest" in p.read_text(encoding="utf-8")]
        # Superseded by P3 (Owner authorization 2026-10-09): the REX writer is the ONLY runtime user of the library.
        self.assertEqual(users, ["rex.py"])

    def test_refuses_the_source_wal_and_shm_through_any_equivalent_path(self):
        for suffix in ("", "-wal", "-shm"):
            target = Path(str(self.db) + suffix)
            if not target.exists():
                target.write_bytes(b"keep-" + suffix.encode())
            content = target.read_bytes()
            spellings = [str(target), str(self.dir / "sub" / ".." / target.name), os.path.relpath(target)]
            if os.name == "nt":
                spellings.append(str(target).upper())
            for name, make_link in (("sym", os.symlink), ("hard", os.link)):
                link = self.dir / f"{name}{suffix or '-db'}.json"
                try:
                    make_link(target, link)
                except (OSError, NotImplementedError):
                    continue  # links not permitted on this host
                spellings.append(str(link))
            for out in spellings:
                with self.subTest(suffix=suffix, out=out), self.assertRaises(SystemExit):
                    verifier_main(["--trading-db", str(self.db), "--out", out])
                self.assertEqual(target.read_bytes(), content)


class Interleaved:
    """Connection proxy: after paper_accounts has been fully read, a concurrent writer runs before the next table."""

    def __init__(self, conn, write):
        self._conn, self._write, self.outcome = conn, write, None

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql, *args):
        if self.outcome is None and 'FROM "paper_orders"' in sql:
            self.outcome = self._write()
        return self._conn.execute(sql, *args)


class SnapshotTests(Base):
    def writer(self):
        conn = sqlite3.connect(self.db, timeout=0, isolation_level=None)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE paper_accounts SET payload=payload||' '")  # a table already read
            conn.execute("UPDATE closed_trades SET payload=payload||' '")  # a table not read yet
            try:
                conn.execute("COMMIT")
                return "committed"
            except sqlite3.OperationalError as exc:
                conn.execute("ROLLBACK")
                return exc
        finally:
            conn.close()

    def interleaved_digest(self):
        conn = sqlite3.connect(self.db, isolation_level=None)
        try:
            proxy = Interleaved(conn, self.writer)
            result = edg(proxy)
            self.assertFalse(conn.in_transaction)  # its own read transaction is ended
            return result, proxy.outcome
        finally:
            conn.close()

    def test_wal_concurrent_commit_is_invisible_to_a_running_digest(self):
        self.raw("PRAGMA journal_mode=WAL")
        base = digest_of(self.db)
        result, outcome = self.interleaved_digest()
        self.assertEqual(outcome, "committed")
        self.assertEqual(result, base)  # the pre-write snapshot, not a mix
        after = digest_of(self.db)
        self.assertNotEqual(after["tables"]["paper_accounts"]["digest"], base["tables"]["paper_accounts"]["digest"])
        self.assertNotEqual(after["tables"]["closed_trades"]["digest"], base["tables"]["closed_trades"]["digest"])

    def test_rollback_journal_concurrent_commit_is_blocked_during_the_digest(self):
        base = digest_of(self.db)
        result, outcome = self.interleaved_digest()
        self.assertIsInstance(outcome, sqlite3.OperationalError)  # SHARED lock held across all five tables
        self.assertEqual(result, base)
        self.assertEqual(digest_of(self.db), base)

    def test_caller_transaction_is_reused_and_never_ended(self):
        base = digest_of(self.db)
        conn = sqlite3.connect(self.db, isolation_level=None)
        try:
            conn.execute("BEGIN")
            conn.execute("UPDATE paper_orders SET payload=payload||' ' WHERE order_id='pend'")
            inside = edg(conn)
            self.assertTrue(conn.in_transaction)  # no COMMIT / ROLLBACK by edg
            self.assertNotEqual(inside["edg"], base["edg"])  # the caller's own view
            conn.execute("ROLLBACK")
            self.assertEqual(edg(conn), base)
        finally:
            conn.close()
        legacy = sqlite3.connect(self.db)  # default Python isolation_level
        try:
            self.assertEqual(edg(legacy), base)
            self.assertFalse(legacy.in_transaction)
        finally:
            legacy.close()

    def test_fails_closed_without_a_confirmed_transaction(self):
        conn = sqlite3.connect(self.db, isolation_level=None)

        class NoTransaction:
            in_transaction = False

            def execute(self, sql, *args):
                return conn.execute(sql, *args)

        try:
            with self.assertRaises(RuntimeError):
                edg(NoTransaction())
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
