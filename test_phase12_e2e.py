"""Phase 12 offline E2E: fixture bars, real DemoRunner contracts and SQLite.

Scouts supply explicit synthetic evidence (Phase 11 harness pattern); setup,
planner, deterministic AI, risk, broker and manager remain production code.
REX is enabled only on these temporary test configs, never via runtime env.
"""
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from ai.provider import DeterministicAIProvider, FakeAIProvider
from data.macro_news import InMemoryMacroNewsProvider
from replay.engine import snapshot_at
from replay.rex_chain import verify
from runtime.alert_catalog import from_notification
from runtime.config import RuntimeConfig
from runtime.alert_channels import GmailEmailChannel
from runtime.alert_dispatch import AlertDeliveryLedger, AlertDispatcher, AlertNotificationSink
from runtime.demo_runner import DemoRunner, REAL_EXECUTION_ENABLED
from runtime.notifications import FakeNotificationSink
from runtime.paper_contracts import paper_instruments
from storage.economic_digest import expected_edg_genesis
from test_demo_runner import T, macro_fixture, scout
from test_phase8_rex_writer import Ids
from ui.adapters import from_persisted_snapshot
from ui.fixtures.demo_floor import demo_market
from ui.state import current_market


SYMBOLS = ("XAUUSD", "EURUSD")


class FixtureReplay:
    """5m starts, aggregated higher frames; reveal only bars closed by the slot."""

    def __init__(self):
        self.frames = {}
        self.seen = []
        for symbol, base, width in (("XAUUSD", 100., 1.), ("EURUSD", 1.10, .001)):
            rows = []
            starts = pd.date_range(T - timedelta(hours=2), T + timedelta(hours=1), freq="5min")
            for stamp in starts:
                high, low, close = base + width, base - width, base
                if stamp == T + timedelta(minutes=25):  # mark-to-market, no exit
                    close = base + (5. if symbol == "XAUUSD" else -.005)
                    high, low = max(high, close), min(low, close)
                if stamp == T + timedelta(minutes=40):
                    high = 131. if symbol == "XAUUSD" else 1.111
                    close = 130. if symbol == "XAUUSD" else 1.11
                rows.append((base, high, low, close))
            five = pd.DataFrame(rows, columns=("Open", "High", "Low", "Close"), index=starts)
            frames = {"5m": five}
            for tf in ("15m", "1h"):
                frames[tf] = five.resample("15min" if tf == "15m" else "1h").agg(
                    {"Open": "first", "High": "max", "Low": "min", "Close": "last"})
            for frame in frames.values():
                frame["symbol"], frame["is_closed"] = symbol, True
            self.frames[symbol] = frames

    def load_snapshot(self, symbol, at):
        snapshot = snapshot_at(self.frames[symbol], at)
        self.seen.append((symbol, at, snapshot))
        return snapshot


def fixture_scout(kind):
    def analyze(frame, symbol, tf, run_id, at):
        source = scout("LONG" if symbol == "XAUUSD" else "SHORT")
        source.kind = kind
        message = source(frame, symbol, tf, run_id, at)
        if kind == "liquidity" and at >= T + timedelta(minutes=45):
            message = replace(message, evidence=({"sweeps": [], "liquidity_above": [],
                                                 "liquidity_below": []},))
        if kind == "structure" and symbol == "EURUSD":
            payload = {**message.evidence[0], "levels": {"support": 1.09, "resistance": 1.11}}
            message = replace(message, evidence=(payload,))
        return message
    return analyze


def normalized_journal(store):
    """Keep every row/field/payload/digest; only REX observer wall time is volatile.

    The fixture's Ids stream fixes entity UUIDs before hashing (as in P8 replay).
    Market and runtime clocks are fixed, so their timestamps are never removed.
    """
    rows = list(reversed(store.journal(limit=10000)))
    for row in rows:
        row["payload"] = json.loads(row["payload"])
        if row["source"] == "rex":
            row["timestamp"] = "REX_OBSERVER_WALL_TIME"
    return json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


class Phase12E2ETests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="phase12-e2e-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.enterContext(patch("socket.create_connection", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.dict("os.environ", {"AI_FLOOR_ALERTS_ENABLED": "0"}))

    def replay(self, name, *, rex=True):
        path = self.root / f"{name}.db"
        config = RuntimeConfig(db_path=path, enabled_symbols=SYMBOLS, v2_rex=rex,
                               market_provider_mode="none", ai_provider_mode="deterministic")
        data, sink = FixtureReplay(), FakeNotificationSink()
        news = replace(macro_fixture().news_items()[0], symbols=SYMBOLS)
        now = [T]
        with patch("uuid.uuid4", Ids()):
            runner = DemoRunner(config, market_provider=data, ai_provider=DeterministicAIProvider(),
                                macro_provider=InMemoryMacroNewsProvider(news=(news,)),
                                instruments=paper_instruments(), notification_sink=sink, clock=lambda: now[0])
            self.addCleanup(runner.close)
            results, states = [], []
            for minutes in (0, 15, 30, 45):
                now[0] = T + timedelta(minutes=minutes)
                # Exit slot has no new liquidity setup and explicitly absent macro evidence.
                if minutes == 45:
                    runner.runtime.macro_provider = InMemoryMacroNewsProvider()
                with patch("floor.orchestrator.analizar_estructura", fixture_scout("structure")), \
                        patch("floor.orchestrator.analizar_liquidez", fixture_scout("liquidity")):
                    for symbol in SYMBOLS:
                        result = runner.run_once(symbol, now[0])
                        self.assertEqual(result["durable_status"], "COMPLETED")
                        self.assertTrue(result["review_durable"])
                        results.append(result)
                        before = runner.store.paper_state(*runner.store.load_paper(config.account_id))
                        journal = normalized_journal(runner.store)
                        self.assertEqual(runner.run_once(symbol, now[0])["status"], "DUPLICATE")
                        self.assertEqual(runner.store.paper_state(*runner.store.load_paper(config.account_id)), before)
                        self.assertEqual(normalized_journal(runner.store), journal)
                states.append(runner.store.load_paper(config.account_id))
        return runner, results, states, data, sink

    def test_e2e_F12_T01_T13_market_to_exit_accounting(self):
        runner, results, states, data, _ = self.replay("lifecycle")
        self.assertFalse(REAL_EXECUTION_ENABLED)
        self.assertEqual([r["status"] for r in results], ["PLAN_READY"] * 6 + ["WATCH"] * 2)
        for symbol, at, snapshot in data.seen:
            self.assertEqual(set(snapshot), {"1h", "15m", "5m"})
            for tf, frame in snapshot.items():
                duration = timedelta(hours=1) if tf == "1h" else timedelta(minutes=15 if tf == "15m" else 5)
                self.assertTrue((frame.index + duration <= at).all(), (symbol, tf))
                self.assertTrue((frame.symbol == symbol).all())
        submitted, filled, marked, closed = states
        self.assertEqual((len(submitted[1]), len(submitted[2])), (2, 0))
        self.assertEqual((len(filled[1]), len(filled[2])), (2, 2))
        self.assertEqual(set(filled[0].open_positions), set(SYMBOLS))
        self.assertEqual(closed[0].open_positions, {})
        self.assertEqual(len(closed[0].closed_trades), 2)
        for state, equity, realized, unrealized in zip(states,
                (10000., 10000., 10095.45, 10209.10), (0., 0., 0., 209.10), (0., 0., 95.45, 0.)):
            self.assertAlmostEqual(state[0].equity, equity)
            self.assertAlmostEqual(state[0].realized_pnl, realized)
            self.assertAlmostEqual(state[0].unrealized_pnl, unrealized)
        self.assertEqual({o.symbol: o.quantity for o in closed[1].values()}, {"XAUUSD": 10., "EURUSD": 9090.})
        self.assertEqual({o.status for o in closed[1].values()}, {"FILLED"})
        for fill in closed[2].values():
            order = closed[1][fill.order_id]
            self.assertEqual((fill.run_id, fill.symbol, fill.side, fill.quantity, fill.fill_price),
                             (order.run_id, order.symbol, order.side, order.quantity, order.planned_entry))
            self.assertGreater(fill.fill_timestamp, order.as_of)
            self.assertEqual(fill.fill_timestamp, T + timedelta(minutes=10))
        for i, result in enumerate(results[:6]):
            review = runner.store.review_report(result["run_id"])
            self.assertEqual(review["setup_status"], "VALID_SETUP")
            risk = review["risk_decision"]
            self.assertEqual(risk["status"], "APPROVED")
            plan = risk  # Review persists the Risk Engine's authorized plan geometry.
            self.assertAlmostEqual(abs(plan["target"] - plan["entry"]) / abs(plan["entry"] - plan["stop"]), 3.)
            self.assertEqual(risk["side"], "LONG" if result["symbol"] == "XAUUSD" else "SHORT")
            self.assertTrue(review["agents"])
            self.assertTrue(all(a["status"] in {"OK", "PARTIAL"} for a in review["agents"]))
            self.assertEqual(len(review["agents"]), 5)
            self.assertTrue(all(a["validation"] == "ok" and a["evidence_received"] for a in review["agents"]))
            self.assertLessEqual(risk["capital_at_risk"], states[i // 2][0].equity * .01)
            self.assertEqual(review["execution"]["execution_reason"],
                             "ORDER_SUBMITTED" if i < 2 else "EXISTING_POSITION")
        expected_unrealized = sum((p.last_price - p.entry_price) * p.quantity * p.contract_multiplier *
                                  (1 if p.side == "LONG" else -1) for p in marked[0].open_positions.values())
        self.assertGreater(expected_unrealized, 0)
        self.assertAlmostEqual(marked[0].unrealized_pnl, expected_unrealized)
        orders = {o.symbol: o for o in closed[1].values()}
        for trade in closed[0].closed_trades:
            order = orders[trade.symbol]
            self.assertEqual(trade.reason, "target" if trade.symbol == "XAUUSD" else "stop")
            self.assertEqual(trade.exit_price, order.target if trade.reason == "target" else order.stop)
            gross = (trade.exit_price - trade.entry_price) * order.quantity * order.contract_multiplier * (
                1 if order.side == "LONG" else -1)
            cost = order.cost_rate * order.quantity * trade.entry_price / 100
            self.assertAlmostEqual(trade.gross_pnl, gross)
            self.assertAlmostEqual(trade.cost, cost)
            self.assertAlmostEqual(trade.net_pnl, gross - cost)
        for account, _, _ in states:
            self.assertEqual(account.cash, 10000.)  # PAPER cash contract: PnL lives in equity.
            self.assertAlmostEqual(account.realized_pnl, sum(t.net_pnl for t in account.closed_trades))
            self.assertAlmostEqual(account.equity, 10000. + account.realized_pnl + account.unrealized_pnl)
        self.assertEqual(closed[0].unrealized_pnl, 0)

    def test_e2e_F12_T14_T16_notifications_review_ui_and_full_journal(self):
        runner, results, states, _, sink = self.replay("projections")
        # Exact chronological sequence, including REX writes and all analytical events.
        rows = list(reversed(runner.store.journal(limit=10000)))
        analysis = ["STRUCTURE_COMPLETE", "LIQUIDITY_COMPLETE", "MACRO_COMPLETE", "AI_REVIEW_COMPLETE"]
        approved = analysis + ["SETUP_VALID_SETUP", "PLAN_CREATED", "RISK_APPROVED"]
        tail = ["EXECUTION_DECISION", "RUN_COMPLETED", "REX_RUN"]
        expected = [(None, "RECOVERY_STARTED"), (None, "RECOVERY_COMPLETED")]
        for minutes in (0, 15, 30, 45):
            for symbol in SYMBOLS:
                events = ["RUN_STARTED", "DATA_CHECK"]
                if minutes == 0:
                    events += approved + ["ORDER_SUBMITTED", "REX_WRITE", "PAPER_ORDER_SUBMITTED"]
                elif minutes == 15:
                    events += approved + ["ORDER_FILLED", "POSITION_OPENED", "REX_WRITE"]
                elif minutes == 30:
                    events += ["REX_WRITE"] * 3 + approved
                else:
                    events += ["REX_WRITE"] * 2 + ["TARGET_HIT" if symbol == "XAUUSD" else "STOP_HIT",
                                                    "POSITION_CLOSED", "REX_WRITE"]
                    events += analysis + ["SETUP_WATCH"]
                expected.extend((symbol, event) for event in events + tail)
        self.assertEqual([(r["symbol"], r["event_type"]) for r in rows], expected)
        before = runner.store.paper_state(*runner.store.load_paper("paper-main"))
        journal_before = normalized_journal(runner.store)
        for symbol in SYMBOLS:
            snapshot = runner.store.load_snapshot(symbol)
            self.assertIsNotNone(snapshot)
            review = runner.store.latest_review(symbol)
            self.assertEqual(review["final_status"], "WATCH")
            self.assertEqual(review["execution"]["execution_reason"], "WATCH")
            vm = from_persisted_snapshot(snapshot, now=T + timedelta(minutes=45))
            self.assertEqual((vm.run_id, vm.state, vm.setup_status, vm.freshness),
                             (review["run_id"], "WATCH", "WATCH", "CURRENT"))
            with patch.dict("os.environ", {"AI_FLOOR_DB_PATH": str(runner.config.db_path)}):
                live = current_market(SimpleNamespace(session_state={"sample_mode": False}), symbol, demo_market)
            self.assertFalse(live.sample)
            self.assertEqual(live.positions, ())
            self.assertEqual(len(live.orders), 1)
            self.assertEqual(live.orders[0].status, "FILLED")
            self.assertEqual(live.account.equity, states[-1][0].equity)
            opening = runner.store.review_report(next(r["run_id"] for r in results if r["symbol"] == symbol))
            self.assertEqual(len(opening["paper"]["closed_trades"]), 1)
            self.assertEqual(opening["paper"]["open_positions"], [])
        persisted = runner.store.notification_events()
        self.assertEqual({e["event_id"] for e in persisted}, {e.event_id for e in sink.events})
        for symbol in SYMBOLS:
            for event in ("POSITION_OPENED", "POSITION_CLOSED"):
                self.assertEqual(len([e for e in persisted if e["symbol"] == symbol and e["type"] == event]), 1)
            close = next(e for e in persisted if e["symbol"] == symbol and e["type"] == "POSITION_CLOSED")
            self.assertEqual(close["run_id"], next(r["run_id"] for r in results if r["symbol"] == symbol))
        self.assertEqual(len(results), 8)
        # Persisted close notification -> production email adapter, fake SMTP only.
        ledger = AlertDeliveryLedger(self.root / "email.db")
        self.addCleanup(ledger.close)
        sent = []
        smtp = SimpleNamespace(login=lambda *a: None, send_message=lambda message: sent.append(message),
                               quit=lambda: None)
        channel = GmailEmailChannel(password="synthetic", sender="a@example.invalid", recipient="b@example.invalid", smtp_factory=lambda *a, **k: smtp)
        dispatcher = AlertDispatcher([channel], enabled=True, ledger=ledger, sleep=lambda _: None,
                                     clock=lambda: T.isoformat(), max_attempts=1)
        email = AlertNotificationSink(dispatcher)
        for event in sink.events:
            if event.type == "POSITION_CLOSED":
                email.deliver(event)
                self.assertEqual(ledger.status(from_notification(event).event_id)["email"]["status"], "DELIVERED")
        self.assertEqual(len(sent), 2)
        self.assertFalse(ledger.failures())
        self.assertEqual(runner.store.paper_state(*runner.store.load_paper("paper-main")), before)
        self.assertEqual(normalized_journal(runner.store), journal_before)

    def test_e2e_F12_T17_transition_failures_do_not_authorize_orders(self):
        for fault, status, reason, event in (
                ("market", "NO_DATA", None, "DATA_UNAVAILABLE"),
                ("macro", "NO_SETUP", "NO_SETUP", "PROVIDER_FAILURE"),
                ("risk", "RISK_REJECTED", "RISK_REJECTED", "RISK_REJECTED"),
                ("ai", "PLAN_READY", "POLICY_REVIEW_UNAVAILABLE", "PROVIDER_FAILURE")):
            with self.subTest(fault=fault):
                config = RuntimeConfig(db_path=self.root / f"fault-{fault}.db", enabled_symbols=SYMBOLS,
                                       market_provider_mode="none", ai_provider_mode="deterministic")
                data = FixtureReplay()
                instruments = paper_instruments()
                if fault == "risk":
                    instruments["XAUUSD"] = replace(instruments["XAUUSD"], contract_multiplier=None)
                if fault == "market":
                    data = SimpleNamespace(load_snapshot=lambda *a: {})
                macro = InMemoryMacroNewsProvider(error=OSError("fixture macro unavailable")) if fault == "macro" \
                    else macro_fixture()
                ai = FakeAIProvider(raises=RuntimeError("fixture AI unavailable")) if fault == "ai" \
                    else DeterministicAIProvider()
                runner = DemoRunner(config, market_provider=data, ai_provider=ai, macro_provider=macro,
                                    instruments=instruments, clock=lambda: T)
                self.addCleanup(runner.close)
                before = runner.store.paper_state(*runner.store.load_paper(config.account_id))
                with patch("floor.orchestrator.analizar_estructura", fixture_scout("structure")), \
                        patch("floor.orchestrator.analizar_liquidez", fixture_scout("liquidity")):
                    result = runner.run_once("XAUUSD", T)
                self.assertEqual(result["status"], status)
                self.assertTrue(result["review_durable"])
                review = runner.store.review_report(result["run_id"])
                if reason is not None:
                    self.assertEqual(review["execution"]["execution_reason"], reason)
                self.assertTrue(runner.store.journal(run_id=result["run_id"], event=event))
                self.assertFalse(runner.store.journal(event="ORDER_SUBMITTED"))
                self.assertEqual(runner.store.paper_state(*runner.store.load_paper(config.account_id)), before)

    def test_e2e_F12_rex_hash_chain_and_independent_reproducibility(self):
        first, _, _, _, _ = self.replay("first")
        second, _, _, _, _ = self.replay("second")
        unobserved, _, _, _, _ = self.replay("rex-off", rex=False)
        self.assertEqual(first.store.paper_state(*first.store.load_paper("paper-main")),
                         unobserved.store.paper_state(*unobserved.store.load_paper("paper-main")))
        self.assertFalse(RuntimeConfig().v2_rex)
        genesis = expected_edg_genesis("paper-main", 10000.)["edg"]
        for runner in (first, second):
            report = verify(runner.config.db_path, edg_start=genesis)
            self.assertEqual(report["sqlite_result"], "SQLITE_CHECKS_PASS", report["findings"])
            self.assertTrue(report["source_unchanged"])
            self.assertEqual(report["classification"], "NOT VERIFIED")
            self.assertEqual(report["counts"]["rex_runs"], 8)
            self.assertEqual(report["counts"]["claimed_runs"], 8)
            self.assertEqual(report["counts"]["economic_events"], 10)
            self.assertEqual(report["counts"]["rex_failures"], 0)
            self.assertEqual(len(report["g14"]), 8)
            self.assertTrue(all(g["result"] == "PASS" for g in report["g14"]))
        a, b = normalized_journal(first.store), normalized_journal(second.store)
        self.assertEqual(a, b)
        self.assertEqual(hashlib.sha256(a).digest(), hashlib.sha256(b).digest())

    def test_e2e_F12_nas100_blocked_before_data_or_writes(self):
        with self.assertRaisesRegex(ValueError, "hard-blocked"):
            RuntimeConfig(db_path=self.root / "blocked.db", enabled_symbols=("NAS100",))
        runner, _, _, data, _ = self.replay("blocked")
        before = normalized_journal(runner.store)
        seen = len(data.seen)
        with self.assertRaisesRegex(ValueError, "disabled"):
            runner.run_once("NAS100", T)
        self.assertEqual(len(data.seen), seen)
        self.assertEqual(normalized_journal(runner.store), before)
        self.assertIsNone(runner.store.load_snapshot("NAS100"))


def setUpModule():  # DEC-XAU-OFF-1: these tests drive XAUUSD cycles; the pause itself is in test_pause_xauusd
    import unittest.mock
    unittest.enterModuleContext(unittest.mock.patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))
