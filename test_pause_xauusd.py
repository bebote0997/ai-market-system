"""DEC-XAU-OFF-1: XAUUSD paused in the PAPER engine by code (runtime.symbol_pause), not by env (temporary DBs)."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import runtime.service as service
from ai.provider import DeterministicAIProvider
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from runtime import symbol_pause
from runtime.cloud import cloud_preflight
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner, preflight
from runtime.halt import HaltGate
from runtime.scheduler import Scheduler
from runtime.service import OperationalRuntime
from storage.database import Store
from test_demo_runner import instrument, macro_fixture
from test_runtime_catch_up import EUR
from test_trade_duration import Prices

ACCOUNT = "paper-main"
FIVE = timedelta(minutes=5)
UTC = timezone.utc
SLOT = datetime(2026, 1, 15, 13, 30, tzinfo=UTC)  # Thursday, London + New York session
OPENED = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)  # Day-1 NY close (22:00 UTC) is still ahead: no duration exit
OFF_SLOT = datetime(2026, 1, 15, 2, 15, tzinfo=UTC)  # no session: the manage pass runs instead of cycles
OFF_OPENED = datetime(2026, 1, 14, 23, 0, tzinfo=UTC)  # after Wednesday's NY close: Day-1 close is Thursday 22:00


def last_bar_at(slot, price=101.0, other=100.0):
    """5m closes: ``price`` on the latest closed bar before ``slot``, ``other`` before it (no SL/TP touched)."""
    return lambda at: price if at == slot - FIVE else other


def xau_short(opened_at=OPENED):
    return PaperPosition("1.0", "pos-xau", "ord-xau", "run-xau", "XAUUSD", "SHORT", 10.0, 100.0, 100.0, 105.0, 90.0,
                         opened_at, last_price=100.0, contract_multiplier=1.0)


class PauseCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="pause-xauusd-")
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "trading_floor.db"
        logged = patch.object(symbol_pause, "_logged", set())
        logged.start()
        self.addCleanup(logged.stop)

    def seed(self, position=None, pending=()):
        store = Store(self.db)
        try:
            broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
            if position is not None:
                broker.orders["ord-xau"] = PaperOrder("1.0", "ord-xau", position.run_id, "XAUUSD", position.side,
                                                      position.quantity, 100.0, 105.0, 90.0, 1.0, 10000.0, 0.0,
                                                      position.opened_at - FIVE, status="FILLED")
                broker.account.open_positions["XAUUSD"] = position
            for order_id, symbol in pending:
                broker.orders[order_id] = PaperOrder("1.0", order_id, f"run-{order_id}", symbol, "LONG", 1.0, 100.0,
                                                     95.0, 115.0, 1.0, 10000.0, 0.0, SLOT - 4 * FIVE)
            store.save_paper(broker)
        finally:
            store.close()

    def config(self, **values):
        return RuntimeConfig(db_path=self.db, enabled_symbols=("XAUUSD", "EURUSD"), scheduler_enabled=True, **values)

    def runtime(self, provider, clock=SLOT, **kwargs):
        runtime = OperationalRuntime(self.config(), market_provider=provider, ai_provider=DeterministicAIProvider(),
                                     macro_provider=macro_fixture(), instruments={"XAUUSD": instrument(), "EURUSD": EUR},
                                     clock=lambda: clock, broker_router=kwargs.pop("broker_router", None), **kwargs)
        self.addCleanup(runtime.close)
        return runtime

    def paper(self):
        store = Store(self.db)
        try:
            account, orders, _ = store.load_paper(ACCOUNT)
            journal = [(r["event_type"], r["symbol"], json.loads(r["payload"] or "{}"))
                       for r in store.db.execute("SELECT event_type,symbol,payload FROM journal ORDER BY id")]
            runs = [r[0] for r in store.db.execute("SELECT symbol FROM runs ORDER BY symbol")]
            return account, orders, journal, runs
        finally:
            store.close()

    def events(self, name):
        return [payload for event, _, payload in self.paper()[2] if event == name]


class Spy:
    """Market provider spy: the symbols whose snapshot was loaded."""

    def __init__(self, provider):
        self.provider, self.symbols = provider, []

    def load_snapshot(self, symbol, slot):
        self.symbols.append(symbol)
        return self.provider.load_snapshot(symbol, slot)


class NoCycleTests(PauseCase):
    def test_tick_runs_eurusd_only_no_xauusd_claim_ai_call_or_data(self):
        self.seed()
        provider = Spy(Prices(lambda at: 100.0, SLOT - 12 * FIVE))
        runtime = self.runtime(provider)
        with patch.object(service, "run_ai", wraps=service.run_ai) as ai, \
                patch.object(service, "run_floor", wraps=service.run_floor) as floor, \
                self.assertLogs("ai_floor.runtime", "INFO") as logs:
            results = Scheduler(runtime, lambda: SLOT).tick()
            Scheduler(runtime, lambda: SLOT + timedelta(minutes=15)).tick()
        self.assertEqual(len(results), 1)
        self.assertEqual(self.paper()[3], ["EURUSD", "EURUSD"])  # EURUSD unchanged; no XAUUSD run row (claim)
        self.assertEqual(set(provider.symbols), {"EURUSD"})  # flat XAUUSD: no market data at all
        self.assertTrue(ai.called and floor.called)
        for call in (*ai.call_args_list, *floor.call_args_list):
            self.assertNotIn("XAUUSD", call.args)
        paused = [line for line in logs.output if "event=SYMBOL_PAUSED" in line]
        self.assertEqual(paused, ["INFO:ai_floor.runtime:component=scheduler event=SYMBOL_PAUSED symbol=XAUUSD"])

    def test_direct_cycle_is_refused_without_claim(self):
        self.seed()
        runtime = self.runtime(Prices(lambda at: 100.0, SLOT - 12 * FIVE))
        self.assertEqual(runtime.run_cycle("XAUUSD", SLOT), "SYMBOL_PAUSED")
        self.assertEqual(self.paper()[3], [])

    def test_unpaused_xauusd_still_cycles(self):
        self.seed()
        runtime = self.runtime(Prices(lambda at: 100.0, SLOT - 12 * FIVE))
        with patch.object(symbol_pause, "PAUSED_SYMBOLS", frozenset()):
            self.assertEqual(len(Scheduler(runtime, lambda: SLOT).tick()), 2)
        self.assertEqual(self.paper()[3], ["EURUSD", "XAUUSD"])

    def test_config_fingerprint_and_preflight_unchanged(self):
        config = RuntimeConfig(db_path=self.db)
        self.assertEqual(config.enabled_symbols, ("XAUUSD", "EURUSD"))
        legacy = {"cadence_minutes": config.cadence_minutes, "enabled_symbols": config.enabled_symbols,
                  "supported_symbols": ("XAUUSD", "NAS100", "EURUSD"), "sessions": config.sessions,
                  "scheduler_enabled": config.scheduler_enabled, "max_age_seconds": config.max_age_seconds,
                  "paper_mode": True, "market_provider_mode": config.market_provider_mode,
                  "ai_provider_mode": config.ai_provider_mode, "macro_provider_mode": config.macro_provider_mode}
        self.assertEqual(config.fingerprint(), hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest())
        with patch.object(symbol_pause, "PAUSED_SYMBOLS", frozenset()):
            self.assertEqual(RuntimeConfig(db_path=self.db).fingerprint(), config.fingerprint())
        self.assertTrue(preflight(config, env={}).checks["enabled_symbols"])
        self.assertTrue(cloud_preflight(config, env={}, disk_mounted=False).checks["enabled_symbols"])


class CloseTests(PauseCase):
    def assert_closed_once(self, slot, price=101.0):
        account, _, journal, _ = self.paper()
        self.assertNotIn("XAUUSD", account.open_positions)
        self.assertEqual(len(account.closed_trades), 1)
        trade = account.closed_trades[0]
        self.assertEqual((trade.symbol, trade.reason, trade.exit_price, trade.exited_at),
                         ("XAUUSD", "SYMBOL_PAUSED", price, slot))
        self.assertEqual(self.events("POSITION_CLOSED"), [{"exit_reason": "SYMBOL_PAUSED"}])
        exits = self.events("SYMBOL_PAUSED_EXIT")
        self.assertEqual(len(exits), 1)
        self.assertEqual((exits[0]["price"], exits[0]["price_bar_start"]), (price, (slot - FIVE).isoformat()))
        self.assertAlmostEqual(account.realized_pnl, trade.net_pnl)

    def test_in_session_tick_closes_once_at_last_closed_bar_via_close_market(self):
        self.seed(xau_short())
        runtime = self.runtime(Prices(last_bar_at(SLOT), OPENED - 4 * FIVE))
        with patch.object(PaperBroker, "close_market", autospec=True, side_effect=PaperBroker.close_market) as close:
            results = Scheduler(runtime, lambda: SLOT).tick()
        self.assertEqual(len(results), 1)  # EURUSD cycle only
        self.assertEqual(close.call_count, 1)
        self.assertEqual(close.call_args.args[1:], ("XAUUSD", 101.0, SLOT))
        self.assertEqual(close.call_args.kwargs, {"exit_reason": "SYMBOL_PAUSED", "decision_run_id": "run-xau"})
        self.assert_closed_once(SLOT)
        self.assertNotIn("XAUUSD", self.paper()[3])
        # Idempotent: the next tick finds XAUUSD flat and does nothing.
        self.assertEqual(runtime.close_paused_symbols(SLOT + timedelta(minutes=15)), {})
        self.assert_closed_once(SLOT)

    def test_never_closes_on_a_bar_older_than_the_durable_watermark(self):
        from dataclasses import replace as dc_replace
        self.seed(dc_replace(xau_short(), last_processed_at=SLOT + timedelta(minutes=30)))
        runtime = self.runtime(Prices(last_bar_at(SLOT), OPENED - 4 * FIVE))
        with patch.object(PaperBroker, "close_market", autospec=True) as close:
            Scheduler(runtime, lambda: SLOT).tick()
        close.assert_not_called()
        self.assertIn("XAUUSD", self.paper()[0].open_positions)

    def test_close_notification_is_the_normal_position_closed_alert(self):
        self.seed(xau_short())
        runtime = self.runtime(Prices(last_bar_at(SLOT), OPENED - 4 * FIVE))
        self.assertEqual(runtime.close_paused_symbols(SLOT), {"XAUUSD": "POSITION_CLOSED"})
        events = [e for e in runtime.store.capture_notifications() if e.symbol == "XAUUSD"]
        self.assertEqual([e.type for e in events], ["POSITION_CLOSED"])
        self.assertEqual(events[0].execution["exit_reason"], "SYMBOL_PAUSED")

    def test_demo_runner_delivers_the_close_once(self):
        self.seed(xau_short())
        delivered = []

        class Sink:
            def deliver(self, event):
                delivered.append(event)
        runner = DemoRunner(self.config(), market_provider=Prices(last_bar_at(SLOT), OPENED - 4 * FIVE),
                            ai_provider=DeterministicAIProvider(), macro_provider=macro_fixture(),
                            instruments={"XAUUSD": instrument(), "EURUSD": EUR}, clock=lambda: SLOT,
                            notification_sink=Sink(), broker_router=None)
        try:
            runner.tick()
            runner.tick()
        finally:
            runner.close()
        closed = [e for e in delivered if e.type == "POSITION_CLOSED" and e.symbol == "XAUUSD"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0].execution["exit_reason"], "SYMBOL_PAUSED")
        self.assertEqual([e.symbol for e in delivered if e.symbol == "XAUUSD" and e.type != "POSITION_CLOSED"], [])
        self.assert_closed_once(SLOT)

    def test_out_of_session_manage_pass_closes(self):
        self.seed(xau_short(OFF_OPENED))
        runtime = self.runtime(Prices(last_bar_at(OFF_SLOT), OFF_OPENED - 4 * FIVE), OFF_SLOT)
        self.assertEqual(Scheduler(runtime, lambda: OFF_SLOT).tick(), [])
        self.assert_closed_once(OFF_SLOT)
        self.assertEqual(runtime.manage_trade_duration(OFF_SLOT + timedelta(minutes=15)), {})

    def test_snapshot_failure_retries_next_tick_without_raising(self):
        self.seed(xau_short())

        class Down:
            def load_snapshot(self, symbol, slot):
                raise RuntimeError("provider down")
        runtime = self.runtime(Down())
        with self.assertLogs("ai_floor.runtime", "WARNING") as logs:
            self.assertEqual(runtime.close_paused_symbols(SLOT), {"XAUUSD": "ERROR"})
            Scheduler(runtime, lambda: SLOT).tick()  # never raises into the scheduler
        self.assertTrue(any("event=PAUSED_CLOSE_FAILED" in line for line in logs.output))
        self.assertIn("XAUUSD", self.paper()[0].open_positions)
        runtime.market_provider = Prices(last_bar_at(SLOT + timedelta(minutes=15)), OPENED - 4 * FIVE)
        later = SLOT + timedelta(minutes=15)
        self.assertEqual(runtime.close_paused_symbols(later), {"XAUUSD": "POSITION_CLOSED"})
        self.assert_closed_once(later)

    def test_no_closed_bar_is_no_data_and_retried(self):
        self.seed(xau_short(SLOT))  # no bar has closed after the fill yet
        runtime = self.runtime(Prices(lambda at: 100.0, SLOT - 12 * FIVE))
        self.assertEqual(runtime.close_paused_symbols(SLOT), {"XAUUSD": "NO_DATA"})
        self.assertIn("XAUUSD", self.paper()[0].open_positions)

    def test_stop_hit_before_the_slot_wins_over_the_pause_close(self):
        self.seed(xau_short())
        hit = SLOT - 6 * FIVE
        runtime = self.runtime(Prices(lambda at: 106.0 if at == hit else 100.0, OPENED - 4 * FIVE))
        self.assertEqual(runtime.close_paused_symbols(SLOT), {"XAUUSD": "CLOSED_BY_MANAGEMENT"})
        trade = self.paper()[0].closed_trades[0]
        self.assertEqual((trade.reason, trade.exit_price), ("stop", 106.0))  # gap through the stop: fills at open
        self.assertEqual(self.events("SYMBOL_PAUSED_EXIT"), [])

    def test_pending_orders_cancelled_and_eurusd_untouched(self):
        self.seed(pending=(("pend-xau", "XAUUSD"), ("pend-eur", "EURUSD")))
        runtime = self.runtime(None)
        self.assertEqual(runtime.close_paused_symbols(SLOT), {"XAUUSD": "ORDERS_CANCELLED"})
        orders = self.paper()[1]
        self.assertEqual((orders["pend-xau"].status, orders["pend-eur"].status), ("CANCELLED", "PENDING"))
        self.assertEqual(self.events("ORDER_CANCELLED"), [{"reason": "SYMBOL_PAUSED"}])
        self.assertEqual(runtime.close_paused_symbols(SLOT + timedelta(minutes=15)), {})

    def test_halt_gate_and_paper_disabled_are_respected(self):
        self.seed(xau_short(), pending=(("pend-xau", "XAUUSD"),))
        runtime = self.runtime(Prices(last_bar_at(SLOT), OPENED - 4 * FIVE))
        runtime.halt_gate = HaltGate()
        runtime.halt_gate.request()
        self.assertEqual(runtime.close_paused_symbols(SLOT), {})
        Scheduler(runtime, lambda: SLOT).tick()
        runtime.halt_gate = None
        runtime.paper_enabled = False
        self.assertEqual(runtime.close_paused_symbols(SLOT), {})
        account, orders, journal, _ = self.paper()
        self.assertIn("XAUUSD", account.open_positions)
        self.assertEqual(orders["pend-xau"].status, "PENDING")
        self.assertEqual([e for e in journal if e[1] == "XAUUSD"], [])

    def test_halt_at_admission_stops_without_raising(self):
        self.seed(xau_short())
        runtime = self.runtime(Prices(last_bar_at(SLOT), OPENED - 4 * FIVE))
        gate = runtime.halt_gate = HaltGate()
        real_admit = gate.admit

        def admit(kind):
            if kind == "symbol_pause_close":
                gate.request()
            return real_admit(kind)
        gate.admit = admit
        self.assertEqual(runtime.close_paused_symbols(SLOT), {})
        self.assertIn("XAUUSD", self.paper()[0].open_positions)

    def test_never_touches_the_broker_router(self):
        self.seed(xau_short())
        router = MagicMock()
        runtime = self.runtime(Prices(last_bar_at(SLOT), OPENED - 4 * FIVE), broker_router=router)
        runtime.close_paused_symbols(SLOT)
        self.assertEqual(router.mock_calls, [])
        self.assert_closed_once(SLOT)
        Scheduler(runtime, lambda: SLOT + timedelta(minutes=15)).tick()  # EURUSD cycle: the router sees EURUSD only
        self.assertEqual(self.paper()[3], ["EURUSD"])
        self.assertEqual(router.on_cycle.call_count, 1)
        self.assertEqual({c[0] for c in router.mock_calls} - {"on_cycle", "drawdown_basis"}, set())


if __name__ == "__main__":
    unittest.main()
