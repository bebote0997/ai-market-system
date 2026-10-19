"""DEC-14.1: real SQLite, broker, validator, Risk V2 and restart boundaries; no network."""
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from agents.setup_validator import evaluar_setup
from core.contracts import AgentMessage, FloorRunReport
from execution import opposite_policy
from execution.contracts import PaperOrder
from execution.paper_broker import PaperBroker
from execution.risk_reservation import prepare_reservation
from runtime.config import RuntimeConfig
from runtime.demo_runner import DemoRunner
from runtime.alert_catalog import from_notification
from runtime.scheduler import slot_key
from runtime.service import OperationalRuntime
from test_demo_runner import Data, T, macro_fixture, patched_scouts
from test_phase5_risk_engine import INS, account, f3_plan, position


def snapshot(price=2660., bos_close=2590.):
    result = {}
    for tf, freq, starts, values in (
        ("1h", "1h", [T.replace(minute=0) - timedelta(hours=1)], [price]),
        ("15m", "15min", [T - timedelta(minutes=30), T - timedelta(minutes=15)], [bos_close, price]),
        ("5m", "5min", [T - timedelta(minutes=5)], [price]),
    ):
        result[tf] = pd.DataFrame({"Open": values, "High": [v + 1 for v in values],
                                  "Low": [v - 1 for v in values], "Close": values,
                                  "symbol": "XAUUSD", "is_closed": True}, index=pd.DatetimeIndex(starts))
    return result


def reports(run_id="reverse", side="SHORT", entry=2660., stop=2700., bos=True, hour="bearish", snap=None):
    snap = snapshot(entry) if snap is None else snap
    direction = "bearish" if side == "SHORT" else "bullish"
    structures, liquids = {}, {}
    for tf in ("1h", "15m", "5m"):
        payload = {"bias": direction, "structure_state": direction if tf != "1h" else hour,
                   "levels": {"support": stop, "resistance": stop},
                   "retracement": {"protected_timestamp": T - timedelta(minutes=30)},
                   "evidence": [{"timestamp": snap[tf].index[-1]}], "bos": None}
        if bos and tf == "15m":
            payload["bos"] = {"type": "BOS", "direction": direction, "broken_level": 2605.,
                              "break_timestamp": snap[tf].index[0], "break_close": float(snap[tf].iloc[0]["Close"])}
        structures[tf] = AgentMessage("1.0", run_id, T, "XAUUSD", tf, "structure", "OK", evidence=(payload,))
        liquids[tf] = AgentMessage("1.0", run_id, T, "XAUUSD", tf, "liquidity", "OK",
                                  evidence=({"sweeps": [{"type": "low"}], "liquidity_above": [], "liquidity_below": []},))
    setup = evaluar_setup(structures, liquids, None, "XAUUSD", run_id, T)
    plan = f3_plan("XAUUSD", side, entry, stop, run_id=run_id)
    return FloorRunReport("1.0", run_id, T, "XAUUSD", structures, liquids, None, setup, plan, None, "PLAN_READY")


class OppositePolicyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "paper.db"
        self.config = RuntimeConfig(db_path=self.path, v2_opposite_setup_policy="STRUCTURE_CHANGE",
                                    market_provider_mode="none")
        self.rt = self.runtime()
        self.addCleanup(lambda: self.rt.close())
        self.key = slot_key("XAUUSD", T, 15)
        self.assertTrue(self.rt.store.claim_slot(self.key, "XAUUSD", T, T, run_id="reverse"))
        self.seed()

    def runtime(self):
        return OperationalRuntime(self.config, instruments=INS, clock=lambda: T)

    def seed(self, entry=2650., stop=2600., realized=0., pending=False, cost=0.):
        held = account(equity=10000 + realized, realized=realized)
        held.open_positions["XAUUSD"] = replace(position("XAUUSD", "LONG", entry, stop, 2900., 1.), cost_rate=cost)
        broker = PaperBroker(held, INS["XAUUSD"])
        broker.orders["ord-XAUUSD"] = PaperOrder("1.0", "ord-XAUUSD", "run-XAUUSD", "XAUUSD", "LONG", 1.,
                                                entry, stop, 2900., 1., 10000., cost, T - timedelta(hours=1), status="FILLED")
        if pending:
            broker.orders["pending"] = replace(broker.orders["ord-XAUUSD"], order_id="pending", status="PENDING")
        self.rt.store.save_paper(broker)

    def execute(self, report=None, snap=None, allowed=True):
        return opposite_policy.execute(self.rt, report or reports(), snap if snap is not None else snapshot(),
                                       key=self.key, at=T, ai_allowed=allowed)

    def paper(self):
        return self.rt.store.load_paper("paper-main")

    def rows(self, event):
        return [json.loads(r[0]) for r in self.rt.store.db.execute(
            "SELECT payload FROM journal WHERE event_type=?", (event,))]

    def test_profit_close_then_fresh_risk_reservation_and_fill(self):
        self.seed(cost=.1)
        original = prepare_reservation
        def verify(*args, **kwargs):
            # Durable close is visible BEFORE Risk V2; lock spans both steps.
            held, _, _ = self.paper()
            self.assertEqual(len(held.closed_trades), 1)
            self.assertNotIn("XAUUSD", held.open_positions)
            self.assertTrue(self.rt.store.owns_slot(self.key, "XAUUSD"))
            self.assertEqual(args[2].equity, 10010. - 2.65)
            return original(*args, **kwargs)
        with patch("execution.opposite_policy.prepare_reservation", side_effect=verify):
            outcome = self.execute()
        self.assertEqual(outcome.status, "SUBMITTED")
        held, orders, fills = self.paper()
        self.assertEqual(held.closed_trades[0].reason, "EXIT_STRUCTURE_CHANGE")
        self.assertEqual(len(orders), 2)
        broker = PaperBroker(held, INS["XAUUSD"])
        broker.orders, broker.fills = orders, fills
        order = orders[outcome.order_id]
        self.assertIsNotNone(order.risk_policy_version)
        opened = broker.process_next_bar(order, {"symbol": "XAUUSD", "timestamp": T + timedelta(minutes=5),
                                                "open": 2660., "high": 2661., "low": 2659., "close": 2660., "is_closed": True})
        self.assertIsNotNone(opened)
        self.rt.store.save_paper(broker, owner_key=self.key, symbol="XAUUSD")
        self.assertEqual(len(self.paper()[0].open_positions), 1)
        events = self.rt.store.capture_notifications(run_id="reverse")
        self.assertEqual({from_notification(e).event_type for e in events if from_notification(e)},
                         {"POSITION_CLOSED", "ORDER_SUBMITTED", "POSITION_OPENED"})

    def test_risk_reject_after_profit_close_stays_flat(self):
        self.seed(realized=-700.)
        result = self.execute()
        self.assertEqual(result.reason, "RISK_REJECTED_AFTER_CLOSE")
        self.assertEqual(self.paper()[0].open_positions, {})
        self.assertEqual(len(self.paper()[1]), 1)
        self.assertEqual(self.rows("RISK_V2_DECISION")[0]["risk_reason"], "ACCOUNT_DRAWDOWN_LIMIT")

    def test_loss_intact_is_kept(self):
        result = self.execute(reports(entry=2640., hour="bullish"), snapshot(2640.))
        self.assertEqual(result.reason, "OPPOSITE_DISCARDED_STRUCTURE_INTACT")
        self.assertEqual(len(self.paper()[0].open_positions), 1)
        self.assertEqual(self.paper()[0].closed_trades, [])

    def test_loss_broken_is_closed_and_reserved(self):
        result = self.execute(reports(entry=2640.), snapshot(2640.))
        self.assertEqual(result.status, "SUBMITTED")
        self.assertEqual(self.rows(opposite_policy.CLOSE)[0]["reason"], "WOULD_CLOSE_LOSS")
        self.assertEqual(self.paper()[0].open_positions, {})
        self.assertEqual(len(self.paper()[0].closed_trades), 1)

    def test_duplicate_same_direction_similar_zone_ignored(self):
        report = reports(side="LONG", entry=2650.01, stop=2600.)
        self.assertEqual(self.execute(report).reason, "SAME_SETUP_IGNORED")
        self.assertEqual(len(self.paper()[1]), 1)

    def test_same_direction_distinct_blocked(self):
        self.assertEqual(self.execute(reports(side="LONG", entry=2662.51, stop=2600.)).reason, "SAME_DIRECTION")
        self.assertEqual(self.paper()[0].closed_trades, [])

    def test_similarity_boundary_uses_original_stop_after_stop_moves(self):
        broker = self.rt._broker("XAUUSD")
        broker.account.open_positions["XAUUSD"].stop = 2649.
        self.rt.store.save_paper(broker)
        held = self.paper()[0].open_positions["XAUUSD"]
        for entry, expected in ((2637.49, "SAME_DIRECTION"), (2637.5, "SAME_SETUP_IGNORED"),
                                (2637.51, "SAME_SETUP_IGNORED"), (2662.49, "SAME_SETUP_IGNORED"),
                                (2662.5, "SAME_SETUP_IGNORED"), (2662.51, "SAME_DIRECTION")):
            with self.subTest(entry=entry):
                self.assertEqual(opposite_policy.assess(reports(side="LONG", entry=entry, stop=2600.),
                    held, snapshot(), INS["XAUUSD"], T, self.config.max_age_seconds, original_stop=2600.), expected)
        self.assertEqual(self.execute(reports(side="LONG", entry=2662.5, stop=2600.)).reason, "SAME_SETUP_IGNORED")

    def test_opposite_direction_never_same_even_with_matching_setup_id(self):
        report = reports(entry=2650., bos=False)
        held = replace(self.paper()[0].open_positions["XAUUSD"],
                       setup_id=report.setup_assessment.explanation["setup_id"])
        self.assertEqual(opposite_policy.assess(report, held, snapshot(), INS["XAUUSD"], T,
                         self.config.max_age_seconds, original_stop=2600.), "OPPOSITE_NO_STRUCTURE_CHANGE")

    def test_missing_original_order_does_not_invent_similarity(self):
        self.rt.store.db.execute("DELETE FROM paper_orders")
        self.assertEqual(self.execute(reports(side="LONG", entry=2650., stop=2600.)).reason, "NO_DATA")

    def test_opposite_without_bos_is_blocked(self):
        self.assertEqual(self.execute(reports(bos=False)).reason, "OPPOSITE_NO_STRUCTURE_CHANGE")
        self.assertEqual(self.paper()[0].closed_trades, [])

    def test_bos_does_not_close_beyond_stop_is_blocked(self):
        snap = snapshot(bos_close=2601.)
        self.assertEqual(self.execute(reports(snap=snap), snap).reason, "OPPOSITE_NO_STRUCTURE_CHANGE")

    def test_stale_and_ai_caution_no_economic_action(self):
        for allowed, snap, reason in ((False, snapshot(), "AI_CAUTION_OR_UNHEALTHY"),
                                     (True, snapshot(), "STALE_DATA")):
            with self.subTest(reason=reason):
                self.rt.store.db.execute("DELETE FROM journal WHERE run_id='reverse'")
                if allowed:
                    snap["5m"].index -= timedelta(hours=2)
                before = self.rt.store.paper_state(*self.paper())
                self.assertEqual(self.execute(snap=snap, allowed=allowed).reason, reason)
                self.assertEqual(self.rt.store.paper_state(*self.paper()), before)
                self.assertEqual(len(self.rows(opposite_policy.DECISION)), 1)

    def test_pending_unchanged_no_cancel(self):
        self.seed(pending=True)
        self.assertEqual(self.execute().reason, "PENDING_ORDER")
        self.assertEqual(self.paper()[1]["pending"].status, "PENDING")
        self.assertEqual(self.paper()[0].closed_trades, [])

    def test_crash_after_close_restart_abandons_exactly_once(self):
        with patch("execution.opposite_policy.prepare_reservation", side_effect=RuntimeError("crash")):
            with self.assertRaisesRegex(RuntimeError, "crash"):
                self.execute()
        self.assertEqual(len(self.paper()[0].closed_trades), 1)
        self.rt.store.finish(self.key, T, "FAILED", "ERROR")
        self.rt.close()
        self.rt = self.runtime()
        self.assertEqual(self.rows(opposite_policy.DECISION)[0]["reason"], "REVERSE_ABANDONED_AFTER_RESTART")
        # Same run cannot be claimed again by runtime; direct helper with owned test slot also stays abandoned.
        self.rt.store.db.execute("UPDATE runs SET status='RUNNING' WHERE slot_key=?", (self.key,))
        self.rt.store.db.execute("INSERT INTO symbol_locks VALUES(?,?)", ("XAUUSD", self.key))
        self.assertEqual(self.execute().reason, "REVERSE_ABANDONED_AFTER_RESTART")
        self.assertEqual(len(self.paper()[0].closed_trades), 1)
        self.assertEqual(len(self.paper()[1]), 1)

    def test_rerun_and_changed_candidate_identity(self):
        first = self.execute()
        before = self.rt.store.paper_state(*self.paper())
        self.assertEqual(self.execute(), first)
        self.assertEqual(self.rt.store.paper_state(*self.paper()), before)
        self.assertEqual(len(self.rows(opposite_policy.CLOSE)), 1)
        self.assertEqual(len(self.rows(opposite_policy.DECISION)), 1)
        self.assertEqual(self.execute(reports(entry=2670.)).reason, "RUN_ID_CANDIDATE_MISMATCH")

    def test_live_runtime_preserves_planner_and_rejects_non_fixed_3r_contract(self):
        # Remove only the harness claim so production run_cycle acquires its own lock/run identity.
        self.rt.store.db.execute("DELETE FROM symbol_locks")
        self.rt.store.db.execute("DELETE FROM runs")
        snap = snapshot()
        self.rt.macro_provider = macro_fixture()
        self.rt.market_provider = type("Provider", (), {"load_snapshot": lambda _, symbol, at: snap})()
        def scout(kind):
            def analyze(frame, symbol, tf, run_id, at):
                report = reports(run_id=run_id)
                return (report.structure_reports if kind == "structure" else report.liquidity_reports)[tf]
            return analyze
        with patch("floor.orchestrator.analizar_estructura", scout("structure")), \
                patch("floor.orchestrator.analizar_liquidez", scout("liquidity")):
            self.assertEqual(self.rt.run_cycle("XAUUSD", T), "PLAN_READY")
            self.assertEqual(self.rt.run_cycle("XAUUSD", T), "DUPLICATE")
        self.assertEqual(self.paper()[0].closed_trades, [])
        self.assertIn("XAUUSD", self.paper()[0].open_positions)
        self.assertEqual(len(self.paper()[1]), 1)
        self.assertEqual(self.rows("RISK_V2_DECISION"), [])
        self.assertEqual(self.rows("EXECUTION_DECISION")[0]["execution_reason"], "not_a_fixed_3r_plan")
        mapped = [from_notification(e) for e in self.rt.store.capture_notifications(
            run_id=self.rt.store.latest_review("XAUUSD")["run_id"])]
        self.assertEqual([e.event_type for e in mapped if e], [])

    def test_bos_unclosed_or_old_cannot_close(self):
        for shift in (timedelta(minutes=30), -timedelta(hours=2)):
            with self.subTest(shift=shift):
                snap = snapshot()
                report = reports(snap=snap)
                signal = report.setup_assessment.evidence[0]["15m"]["bos"]
                signal["break_timestamp"] += shift
                report = replace(report, setup_assessment=replace(report.setup_assessment, explanation={}))
                self.assertEqual(opposite_policy.assess(report, self.paper()[0].open_positions["XAUUSD"],
                    snap, INS["XAUUSD"], T, self.config.max_age_seconds), "OPPOSITE_NO_STRUCTURE_CHANGE")

    def test_short_position_profit_reverses_to_long(self):
        broker = self.rt._broker("XAUUSD")
        broker.account.open_positions["XAUUSD"] = replace(
            broker.account.open_positions["XAUUSD"], side="SHORT", stop=2700., target=2400.)
        broker.orders["ord-XAUUSD"] = replace(broker.orders["ord-XAUUSD"], side="SHORT", stop=2700., target=2400.)
        self.rt.store.save_paper(broker)
        snap = snapshot(2640., 2710.)
        result = self.execute(reports(side="LONG", entry=2640., stop=2600., hour="bullish", snap=snap), snap)
        self.assertEqual(result.status, "SUBMITTED")
        self.assertEqual(self.paper()[1][result.order_id].side, "LONG")
        self.assertEqual(self.paper()[0].closed_trades[0].gross_pnl, 10.)

    def test_zero_pnl_is_loss_branch_and_kept_if_intact(self):
        self.assertEqual(self.execute(reports(entry=2650., hour="bullish"), snapshot(2650.)).reason,
                         "OPPOSITE_DISCARDED_STRUCTURE_INTACT")

    def test_close_cas_retry_commits_one_exit_and_one_cost(self):
        original = self.rt.store.save_paper
        attempted = []
        def save(broker, **kwargs):
            attempted.append(len(broker.account.closed_trades))
            if len(attempted) == 1:
                return False  # discarded tentative exit; no durable marker/economic effect
            return original(broker, **kwargs)
        with patch.object(self.rt.store, "save_paper", side_effect=save):
            self.assertEqual(self.execute().status, "SUBMITTED")
        self.assertEqual(len(attempted), 3)  # two close attempts, one fresh reservation
        self.assertEqual(len(self.paper()[0].closed_trades), 1)
        self.assertEqual(len(self.rows(opposite_policy.CLOSE)), 1)

    def test_restart_does_not_abandon_other_active_writer(self):
        with patch("execution.opposite_policy.prepare_reservation", side_effect=RuntimeError("interrupted")):
            with self.assertRaises(RuntimeError):
                self.execute()
        opposite_policy.abandon_incomplete(self.rt.store, T)
        self.assertEqual(self.rows(opposite_policy.DECISION), [])  # still RUNNING, lock not stolen
        self.assertTrue(self.rt.store.owns_slot(self.key, "XAUUSD"))

    def test_missing_lock_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "symbol lock"):
            opposite_policy.execute(self.rt, reports(), snapshot(), key="foreign", at=T, ai_allowed=True)


class FlagAndRuntimeTests(unittest.TestCase):
    def test_flat_structure_change_matches_block_journal_orders_and_pending_gate(self):
        for pending, open_position in ((False, False), (True, False), (True, True)):
            with self.subTest(pending=pending, open_position=open_position):
                outcomes = []
                for policy in ("BLOCK", "STRUCTURE_CHANGE"):
                    with tempfile.TemporaryDirectory() as directory:
                        cfg = RuntimeConfig(db_path=Path(directory) / "db", v2_opposite_setup_policy=policy)
                        rt = DemoRunner(cfg, market_provider=Data(age=5), macro_provider=macro_fixture(),
                                        instruments=INS, clock=lambda: T)
                        try:
                            if pending:
                                broker = PaperBroker(account(), INS["XAUUSD"])
                                broker.orders["pending"] = PaperOrder(
                                    "1.0", "pending", "previous", "XAUUSD", "LONG", 1.,
                                    100., 90., 130., 1., 10000., 0., T, status="PENDING")
                                if open_position:
                                    broker.account.open_positions["XAUUSD"] = position(
                                        "XAUUSD", "LONG", 100., 90., 130., 1.)
                                rt.store.save_paper(broker)
                            with patched_scouts("LONG"), patch("execution.opposite_policy.execute") as execute, \
                                    patch("execution.paper_broker.uuid.uuid4", return_value="same-order-id"):
                                result = rt.run_once("XAUUSD", T)
                            execute.assert_not_called()
                            journal = [tuple(row) for row in rt.store.db.execute(
                                "SELECT timestamp,run_id,symbol,source,event_type,severity,payload FROM journal ORDER BY id")]
                            execution = rt.store.review_report(result["run_id"])["execution"]
                            self.assertEqual(execution["execution_reason"], "PENDING_ORDER" if pending else "ORDER_SUBMITTED")
                            self.assertEqual(len(rt.store.journal(event="PAPER_ORDER_SUBMITTED")), int(not pending))
                            outcomes.append((journal, rt.store.paper_state(*rt.store.load_paper("paper-main")), execution))
                        finally:
                            rt.close()
                self.assertEqual(outcomes[0], outcomes[1])

    def test_block_default_fingerprint_and_invalid_warning(self):
        default = RuntimeConfig(market_provider_mode="none")
        self.assertEqual(default.v2_opposite_setup_policy, "BLOCK")
        self.assertEqual(default.fingerprint(), replace(default, v2_opposite_setup_policy="BLOCK").fingerprint())
        with patch.dict(os.environ, {"V2_OPPOSITE_SETUP_POLICY": "bad"}), self.assertLogs("ai_floor.runtime", "WARNING"):
            cfg = RuntimeConfig.from_env()
        self.assertEqual(cfg.v2_opposite_setup_policy, "BLOCK")
        self.assertTrue(cfg.v2_opposite_setup_policy_warning)
        with tempfile.TemporaryDirectory() as directory:
            rt = OperationalRuntime(replace(cfg, db_path=Path(directory) / "db"), instruments=INS)
            try:
                self.assertEqual(rt.store.get_state("opposite_setup_policy_health"), "DEGRADED")
                self.assertEqual(len(rt.store.journal(event="V2_OPPOSITE_SETUP_POLICY_INVALID")), 1)
            finally:
                rt.close()

    def test_block_runtime_opposite_retains_legacy_position(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = RuntimeConfig(db_path=Path(directory) / "db")
            for minutes, side in ((0, "LONG"), (15, "LONG"), (30, "SHORT")):
                rt = DemoRunner(cfg, market_provider=Data(age=5), macro_provider=macro_fixture(),
                                instruments=INS, clock=lambda: T + timedelta(minutes=minutes))
                try:
                    with patched_scouts(side):
                        result = rt.run_once("XAUUSD", T + timedelta(minutes=minutes))
                    if minutes == 30:
                        self.assertEqual(rt.store.review_report(result["run_id"])["execution"]["execution_reason"],
                                         "EXISTING_POSITION")
                        self.assertEqual(rt.store.load_paper("paper-main")[0].closed_trades, [])
                finally:
                    rt.close()


class ReplayPolicyTests(unittest.TestCase):
    def test_missing_corpus_is_no_data_in_both_policies(self):
        from replay.opposite_policy_impact import main
        with tempfile.TemporaryDirectory() as root:
            result = main(Path(root) / "missing-replay.db", root)
        self.assertEqual(result["status"], "NO_DATA")
        for policy in result["policies"].values():
            self.assertEqual(set(policy.values()), {"NO_DATA"})

    def test_synthetic_calendar_runs_both_policies_and_risk(self):
        # Tiny explicit synthetic cohort tests the CLI algorithm; never substitutes for original corpus.
        from replay.opposite_policy_impact import policy_pass
        from replay.risk_audit import Cohort
        starts = pd.date_range(T - timedelta(minutes=45), T + timedelta(minutes=30), freq="5min")
        prices = [2650. if n < 6 else 2660. for n in range(len(starts))]
        records = [{"slot": T - timedelta(minutes=30), "symbol": "XAUUSD", "side": "LONG",
                    "setup_id": "seed", "invalidation": 2600.},
                   {"slot": T, "symbol": "XAUUSD", "side": "SHORT", "setup_id": "opposite", "invalidation": 2700.}]
        cohort = object.__new__(Cohort)
        cohort.records = records
        series = pd.Series(prices).to_numpy()
        cohort.five = {"XAUUSD": (starts, series, series + 1, series - 1, series)}
        snap = snapshot()
        snap["15m"] = pd.DataFrame({"Open": [2650., 2590.], "High": [2651., 2591.],
                                    "Low": [2649., 2589.], "Close": [2650., 2590.], "symbol": "XAUUSD", "is_closed": True},
                                   index=pd.DatetimeIndex([T - timedelta(minutes=45), T - timedelta(minutes=15)]))
        snap["5m"] = pd.DataFrame({"Open": prices, "High": [p + 1 for p in prices], "Low": [p - 1 for p in prices],
                                  "Close": prices, "symbol": "XAUUSD", "is_closed": True}, index=starts)
        def floor(current, at, symbol, macro, instrument, config, **kwargs):
            current = {tf: frame.iloc[-1:] for tf, frame in current.items()}
            side = "LONG" if at < T else "SHORT"
            report = reports(run_id=kwargs["run_id"], side=side, entry=2650. if side == "LONG" else 2660.,
                             stop=2600. if side == "LONG" else 2700., snap=current)
            return replace(report, as_of=at)
        with patch("replay.opposite_policy_impact.run_floor", side_effect=floor):
            block = policy_pass(cohort, {"XAUUSD": snap}, "BLOCK")
            structure = policy_pass(cohort, {"XAUUSD": snap}, "STRUCTURE_CHANGE")
        self.assertEqual(block["opposite_conflicts"], 1)
        self.assertEqual(block["reversals_approved_by_risk"], 0)
        self.assertEqual(structure["would_close_profit"], 1)
        self.assertEqual(structure["reversals_approved_by_risk"], 1)


def setUpModule():  # DEC-XAU-OFF-1: these tests drive XAUUSD cycles; the pause itself is in test_pause_xauusd
    import unittest.mock
    unittest.enterModuleContext(unittest.mock.patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))
