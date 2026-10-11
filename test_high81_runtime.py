import unittest
"""Owner F08/HIGH-8.1: default OFF runtime, temporary DBs, no operational activation."""
from datetime import timedelta
from unittest.mock import patch

from execution.contracts import PaperOrder
from storage.database import Store
import test_phase8_catch_up_certification as certification
from test_runtime_catch_up import BASE, make_runtime


class DefaultRuntimeTests(certification.Harness):
    def test_unordered_snapshot_excludes_bar_not_yet_closed(self):
        self.seed()
        slot = certification.SLOT
        runtime = make_runtime(self.db, None, slot, flag=False)
        load = runtime.market_provider.load_snapshot

        def snapshot(symbol, at):
            data = load(symbol, at)
            data["5m"].loc[slot] = {"Open": 100., "High": 101., "Low": 90., "Close": 100.,
                                   "symbol": symbol, "is_closed": True}
            data["5m"] = data["5m"].iloc[::-1]
            return data

        runtime.market_provider.load_snapshot = snapshot
        try:
            self.assertNotEqual(runtime.run_cycle("XAUUSD", slot), "ERROR")
        finally:
            runtime.close()
        account = self.paper()[0]
        self.assertEqual(account.closed_trades, [])
        self.assertEqual(account.open_positions["XAUUSD"].last_processed_at, certification.B4)

    def test_earlier_target_wins_over_later_stop(self):
        self.seed()
        history = {("XAUUSD", certification.B1): (100., 111., 99., 100.),
                   ("XAUUSD", certification.B2): (100., 101., 94., 100.)}
        self.run_default("XAUUSD", history)
        trade = self.paper()[0].closed_trades[0]
        self.assertEqual((trade.exited_at, trade.reason, trade.exit_price), (certification.B1, "target", 110.))

    def run_default(self, symbol, overrides, slot=certification.SLOT):
        runtime = make_runtime(self.db, None, slot, flag=False, overrides=overrides, recovery=0)
        try:
            return runtime.run_cycle(symbol, slot)
        finally:
            runtime.close()

    def test_both_symbols_sides_intermediate_exits_gaps_and_priority(self):
        for symbol in ("EURUSD", "XAUUSD"):
            scale = BASE[symbol] / 100
            for case, (side, entry, stop, target, source) in certification.IntermediateTouchTests.CASES.items():
                with self.subTest(symbol=symbol, case=case):
                    self.setUp()
                    overrides = {(symbol, stamp): tuple(v * scale for v in values)
                                 for (_, stamp), values in source.items()}
                    self.seed(side, entry * scale, stop * scale, target * scale, symbol=symbol)
                    bars = [(stamp, *(v * scale for v in values)) for (_, stamp), values in source.items()]
                    expected = certification.oracle(side, entry * scale, stop * scale, target * scale, 10, bars)
                    self.assertNotEqual(self.run_default(symbol, overrides), "ERROR")
                    self.assertEqual(self.run_default(symbol, overrides), "DUPLICATE")
                    self.assertNotEqual(self.run_default(symbol, overrides,
                                                        certification.SLOT + timedelta(minutes=15)), "ERROR")
                    account, _, fills, journal = self.paper()
                    self.assertEqual(len(fills), 0)
                    if expected is None:
                        self.assertIn(symbol, account.open_positions)
                        self.assertEqual(account.closed_trades, [])
                    else:
                        self.assertNotIn(symbol, account.open_positions)
                        self.assertEqual(len(account.closed_trades), 1)
                        trade = account.closed_trades[0]
                        self.assertEqual((trade.exited_at, trade.reason), (expected[0], expected[2]))
                        self.assertAlmostEqual(trade.exit_price, float(expected[1]))
                        self.assertAlmostEqual(trade.net_pnl, float(expected[3]))
                        self.assertAlmostEqual(account.equity, 10000 + float(expected[3]))
                        self.assertEqual(sum(e[0] == "POSITION_CLOSED" for e in journal), 1)

    def test_restart_before_and_after_intermediate_close_commit(self):
        original = Store.save_paper
        for symbol in ("EURUSD", "XAUUSD"):
            for after_commit in (False, True):
                with self.subTest(symbol=symbol, after_commit=after_commit):
                    self.setUp()
                    base = BASE[symbol]
                    self.seed(entry=base, stop=base * .95, target=base * 1.10, symbol=symbol)
                    overrides = {(symbol, certification.B2): (base, base * 1.001, base * .94, base * .96)}

                    def fail_on_close(store, broker, *args, **kwargs):
                        if broker.account.closed_trades:
                            if after_commit:
                                original(store, broker, *args, **kwargs)
                            raise RuntimeError("simulated interruption at close commit")
                        return original(store, broker, *args, **kwargs)

                    with patch.object(Store, "save_paper", fail_on_close):
                        self.assertEqual(self.run_default(symbol, overrides), "ERROR")
                    account = self.paper()[0]
                    self.assertEqual(len(account.closed_trades), int(after_commit))
                    if not after_commit:
                        self.assertEqual(account.open_positions[symbol].last_processed_at, certification.B1)
                    self.run_default(symbol, overrides, certification.SLOT + timedelta(minutes=15))
                    account, _, fills, journal = self.paper()
                    self.assertEqual(len(account.closed_trades), 1)
                    self.assertEqual(account.closed_trades[0].exited_at, certification.B2)
                    self.assertEqual(len(fills), 0)
                    self.assertEqual(sum(e[0] == "POSITION_CLOSED" for e in journal), 1)

    def test_pending_fill_stays_current_bar_and_is_not_duplicated(self):
        pending = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0,
                             1.0, 10000.0, 0.0, certification.OPEN)
        self.seed(side=None, pending=pending)
        # Earlier bars could hit SL; no historical pending fill is authorized.
        overrides = {("XAUUSD", certification.B2): (100., 101., 90., 100.)}
        self.run_default("XAUUSD", overrides)
        self.assertEqual(self.run_default("XAUUSD", overrides), "DUPLICATE")
        self.run_default("XAUUSD", overrides, certification.SLOT + timedelta(minutes=15))
        account, orders, fills, _ = self.paper()
        self.assertEqual(orders["pend"].status, "FILLED")
        self.assertEqual(len(fills), 1)
        self.assertEqual(next(iter(fills.values())).fill_timestamp, certification.B4)
        self.assertEqual(account.closed_trades, [])

    @unittest.expectedFailure
    def test_pending_does_not_fill_on_bar_not_yet_closed(self):
        """KNOWN GAP (Codex review of 983d2d2, LOW; tracked in docs/issues.md as ISSUE-012).
        progress_pending trusts the provider's closed-bar contract: a bar starting at the slot is NOT filtered by
        the runtime. Real adapters already drop forming bars (data/twelve_data_provider.py, data/massive_provider.py),
        so PAPER is not affected with them. Fixing it in the runtime changes many certified fixtures; deferred to an
        explicit decision. Remove expectedFailure when the runtime guard lands."""
        pending = PaperOrder("1.0", "pend", "run-pend", "XAUUSD", "LONG", 1.0, 100.0, 95.0, 130.0,
                             1.0, 10000.0, 0.0, certification.OPEN)
        self.seed(side=None, pending=pending)
        slot = certification.SLOT
        runtime = make_runtime(self.db, None, slot, flag=False, recovery=0)
        load = runtime.market_provider.load_snapshot

        def snapshot(symbol, at):
            # 13:30 bar closes at 13:35 > slot; it touches the entry but must stay invisible.
            data = load(symbol, at)
            data["5m"].loc[slot] = {"Open": 101., "High": 102., "Low": 99., "Close": 101.,
                                   "symbol": symbol, "is_closed": True}
            return data

        runtime.market_provider.load_snapshot = snapshot
        try:
            self.assertNotEqual(runtime.run_cycle("XAUUSD", slot), "ERROR")
        finally:
            runtime.close()
        self.run_default("XAUUSD", {}, slot + timedelta(minutes=15))
        _, orders, fills, _ = self.paper()
        self.assertEqual(orders["pend"].status, "FILLED")
        self.assertEqual(len(fills), 1)
        fill = next(iter(fills.values()))
        self.assertEqual((fill.fill_timestamp, fill.fill_price), (certification.B4, 100.))

    def test_invalid_intermediate_bar_blocks_economics(self):
        self.seed()
        self.run_default("XAUUSD", {("XAUUSD", certification.B2): (100., 99., 94., 100.)})
        account, _, fills, journal = self.paper()
        self.assertEqual(account.open_positions["XAUUSD"].last_processed_at, None)
        self.assertFalse(account.closed_trades)
        self.assertFalse(fills)
        self.assertIn("EVIDENCE_UNAVAILABLE", [e[0] for e in journal])
