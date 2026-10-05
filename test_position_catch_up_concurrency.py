"""Deterministic independent-connection/process regression for B2.1 writer A/B/A."""
import json
from dataclasses import replace
import subprocess
import sys
import textwrap
import time
from unittest import mock

from storage.database import Store
from test_position_catch_up import CatchUpCase, ACCOUNT, ROOT, at, candle, digest, quiet
from execution.position_catch_up import catch_up_position
from execution.paper_broker import PaperBroker
from execution.trade_manager import TradeManager


class ConcurrencyTests(CatchUpCase):
    def interleave(self, bar):
        self.open_position()
        self.ingest([bar])
        other = Store(self.trading_path)
        self.addCleanup(other.close)
        original = Store.save_paper
        winners = []
        snapshots = []

        def pause_before_save(store, broker, **kwargs):
            if store is self.store and not winners:
                # A has loaded/validated and computed process_bar, but not saved.
                winners.append(catch_up_position(other, self.evidence, account_id=ACCOUNT,
                                                symbol="XAUUSD", as_of=at(50)))
                snapshots.append(self.state())
            return original(store, broker, **kwargs)

        with mock.patch.object(Store, "save_paper", pause_before_save):
            loser = self.run_catch_up()
        return winners[0], loser, snapshots[0]

    def test_close_a_computes_b_commits_a_resumes(self):
        winner, loser, committed = self.interleave(candle(1, low=94.0))
        final = self.state()
        self.assertEqual(final["closed_rows"], 1, final)
        self.assertEqual(final["events"], ["STOP_HIT", "POSITION_CLOSED"])
        self.assertEqual(final["realized"], sum(t[3] for t in final["closed"]))
        self.assertEqual((final["realized"], final["equity"]), (-10.0, 9990.0))
        self.assertEqual(winner.applied, (at(1).isoformat(),))
        self.assertEqual(loser.applied, ())
        self.assertEqual(loser.status, "STALE")
        self.assertEqual(final, committed)

    def test_progress_a_computes_b_commits_a_resumes(self):
        winner, loser, committed = self.interleave(quiet(1, 103.0))
        self.assertEqual(winner.applied, (at(1).isoformat(),))
        self.assertEqual(loser.applied, ())
        self.assertEqual(self.state(), committed)
        self.assertEqual(committed["open"], (at(1), 103.0))
        self.assertEqual((committed["closed_rows"], committed["events"]), (0, []))
        self.assertEqual((committed["unrealized"], committed["equity"]), (6.0, 10006.0))

    def test_unchanged_price_still_excludes_stale_watermark(self):
        winner, loser, committed = self.interleave(quiet(1))
        self.assertEqual((winner.applied, loser.applied), ((at(1).isoformat(),), ()))
        self.assertEqual(loser.status, "STALE")
        self.assertEqual(self.state(), committed)

    def test_other_symbol_progress_cannot_be_overwritten_by_stale_account(self):
        self.open_position()
        account, orders, fills = self.store.load_paper(ACCOUNT)
        account.open_positions["EURUSD"] = replace(account.open_positions["XAUUSD"],
                                                   position_id="pos-2", symbol="EURUSD")
        broker = PaperBroker(account)
        broker.orders, broker.fills = orders, fills
        self.store.save_paper(broker)
        self.ingest([quiet(1, 103.0)])
        other = Store(self.trading_path)
        self.addCleanup(other.close)
        original = Store.save_paper

        def concurrent_other_symbol(store, computed, **kwargs):
            if store is self.store:
                account, orders, fills = other.load_paper(ACCOUNT)
                winner = PaperBroker(account)
                winner.orders, winner.fills = orders, fills
                TradeManager(account, winner).process_bar({"symbol": "EURUSD", "timestamp": at(1),
                    "open": 100.0, "high": 104.0, "low": 99.0, "close": 104.0, "is_closed": True})
                original(other, winner)
            return original(store, computed, **kwargs)

        with mock.patch.object(Store, "save_paper", concurrent_other_symbol):
            self.assertEqual(self.run_catch_up().status, "STALE")
        self.assertEqual(self.run_catch_up().applied, (at(1).isoformat(),))
        account, _, _ = self.store.load_paper(ACCOUNT)
        self.assertEqual(account.open_positions["EURUSD"].last_price, 104.0)
        self.assertEqual((account.unrealized_pnl, account.equity), (14.0, 10014.0))

    def test_failure_inside_guarded_save_rolls_back_all_economics(self):
        self.open_position()
        self.ingest([candle(1, low=94.0)])
        before = self.state()
        evidence_before = digest(self.evidence_path)
        original = Store._event
        def fail_after_event(store, *args, **kwargs):
            self.assertTrue(store.db.in_transaction)
            original(store, *args, **kwargs)
            raise OSError("failure after writing close economics and journal")
        with mock.patch.object(Store, "_event", fail_after_event):
            with self.assertRaises(OSError):
                self.run_catch_up()
        self.assertEqual(self.state(), before)
        self.assertFalse(self.store.db.in_transaction)
        self.assertEqual(digest(self.evidence_path), evidence_before)
        self.assertEqual(self.run_catch_up().status, "CLOSED")
        self.assertEqual(self.state()["closed_rows"], 1)

    def test_evidence_rollback_does_not_undo_committed_trading(self):
        self.open_position()
        self.ingest([candle(1, low=94.0)])
        self.run_catch_up()
        before = self.state()
        trading_before = digest(self.trading_path)
        evidence_before = digest(self.evidence_path)
        with self.assertRaises(OSError):
            with self.evidence_store.transaction():
                self.evidence_store.db.execute("DELETE FROM market_evidence")
                raise OSError("evidence transaction failure")
        self.assertEqual(digest(self.trading_path), trading_before)
        self.assertEqual(digest(self.evidence_path), evidence_before)
        self.assertEqual(self.run_catch_up().applied, ())
        self.assertEqual(self.state(), before)

    def spawn(self, mode):
        signal = self.dir / (mode + ".ready")
        code = textwrap.dedent('''
            import contextlib, json, os, sys
            from pathlib import Path
            from datetime import datetime
            from storage.database import Store
            from storage.evidence_store import EvidenceStore
            from data.market_evidence import MarketEvidenceEngine
            from execution.position_catch_up import catch_up_position
            trading, evidence_path, signal_path, mode, as_of = sys.argv[1:]
            store = Store(trading)
            evidence_store = EvidenceStore(evidence_path)
            evidence = MarketEvidenceEngine(evidence_store)
            def signal():
                Path(signal_path).write_text("ready", encoding="utf-8")
            if mode == "pause":
                original = Store.save_paper
                def paused(self, broker, **kwargs):
                    signal()  # A already computed the transition; B can now commit.
                    assert sys.stdin.readline().strip() == "resume"
                    return original(self, broker, **kwargs)
                Store.save_paper = paused
            elif mode in ("crash", "contend"):
                original = Store.transaction
                @contextlib.contextmanager
                def transaction(self):
                    if mode == "contend":
                        signal()  # B is about to acquire A's occupied write lock.
                    with original(self):
                        yield
                        if mode == "crash":
                            assert self.db.in_transaction
                            signal()  # Effects written, lock held, no commit yet.
                            assert sys.stdin.readline().strip() == "resume"
                            os._exit(71)
                Store.transaction = transaction
            result = catch_up_position(store, evidence, account_id="paper-main",
                                       symbol="XAUUSD", as_of=datetime.fromisoformat(as_of))
            print(json.dumps({"status": result.status, "applied": result.applied}), flush=True)
            store.close()
            evidence_store.close()
        ''')
        child = subprocess.Popen([sys.executable, "-B", "-c", code, str(self.trading_path),
                                  str(self.evidence_path), str(signal), mode, at(50).isoformat()],
                                 cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)

        def cleanup():
            if child.poll() is None:
                child.kill()
            child.communicate(timeout=10)
        self.addCleanup(cleanup)
        return child, signal

    def ready(self, child, signal):
        deadline = time.monotonic() + 15
        while not signal.exists():
            if child.poll() is not None:
                self.fail(f"child exited before barrier: {child.communicate(timeout=5)}")
            if time.monotonic() >= deadline:
                self.fail("child did not reach deterministic barrier")
            time.sleep(0.01)

    def result(self, child, resume=False):
        out, err = child.communicate("resume\n" if resume else None, timeout=20)
        self.assertEqual(child.returncode, 0, err)
        return json.loads(out)

    def process_race(self, bar):
        self.open_position()
        self.ingest([bar])
        evidence_before = digest(self.evidence_path)
        a, signal = self.spawn("pause")
        self.ready(a, signal)
        b, _ = self.spawn("normal")
        winner = self.result(b)
        committed = self.state()
        loser = self.result(a, resume=True)
        self.assertEqual(winner["applied"], [at(1).isoformat()])
        self.assertEqual(loser, {"status": "STALE", "applied": []})
        self.assertEqual(self.state(), committed)
        for _ in range(3):  # New process/connection on every restart/retry.
            retry, _ = self.spawn("normal")
            self.assertEqual(self.result(retry)["applied"], [])
            self.assertEqual(self.state(), committed)
        self.assertEqual(digest(self.evidence_path), evidence_before)
        return committed

    def test_two_processes_close_and_restarts(self):
        state = self.process_race(candle(1, low=94.0))
        self.assertEqual(state["closed_rows"], 1)
        self.assertEqual(state["events"], ["STOP_HIT", "POSITION_CLOSED"])
        self.assertEqual(state["realized"], sum(t[3] for t in state["closed"]))
        self.assertEqual((state["realized"], state["equity"]), (-10.0, 9990.0))

    def test_two_processes_progress_and_restarts(self):
        state = self.process_race(quiet(1, 103.0))
        self.assertEqual(state["open"], (at(1), 103.0))
        self.assertEqual((state["closed_rows"], state["events"]), (0, []))
        self.assertEqual((state["unrealized"], state["equity"]), (6.0, 10006.0))

    def test_crashed_writer_releases_transaction_to_second_process(self):
        self.open_position()
        self.ingest([candle(1, low=94.0)])
        evidence_before = digest(self.evidence_path)
        before = self.state()
        a, a_signal = self.spawn("crash")
        self.ready(a, a_signal)
        self.assertEqual(self.state(), before)  # A's uncommitted economics are invisible.
        b, b_signal = self.spawn("contend")
        self.ready(b, b_signal)
        a.communicate("resume\n", timeout=20)
        self.assertEqual(a.returncode, 71)
        self.assertEqual(self.result(b)["applied"], [at(1).isoformat()])
        final = self.state()
        self.assertEqual(final["closed_rows"], 1)
        self.assertEqual(final["events"], ["STOP_HIT", "POSITION_CLOSED"])
        self.assertEqual((final["realized"], final["equity"]), (-10.0, 9990.0))
        self.assertEqual(final["realized"], sum(t[3] for t in final["closed"]))
        self.assertEqual(digest(self.evidence_path), evidence_before)
        self.assertEqual(self.run_catch_up().applied, ())
        self.assertEqual(self.state(), final)
