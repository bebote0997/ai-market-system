"""Immediate Floor «Cerrar operación»: the runner polls the operator requests between ticks (DEC-MANUAL-CLOSE-1 amended).
In session the ~30 s ticks inside an already claimed 15-minute slot return DUPLICATE and never reach ``router.on_cycle``;
``process_operator_requests`` runs the same request code paths as ``manage`` without a cycle. Fake transport only."""
from datetime import timedelta
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from execution import operator_actions as ops
from execution.broker_demo_router import HEARTBEAT_FLAG
from runtime import cloud_runner
from runtime.demo_runner import DemoRunner
from test_broker_demo_router import CID, OPEN_TRADES, open_trade, trades
from test_manual_close import LATER, ManualBase
from test_ops_hardening import CLOSE, CLOSED_OK


class RouterPollTests(ManualBase):
    def test_no_flag_is_one_read_and_no_network(self):
        router = self.opened()
        before = len(self.server.calls)
        with patch.object(router._store, "flag", wraps=router._store.flag) as single:
            self.assertEqual(router.process_operator_requests(LATER), {})
        self.assertEqual(len(self.server.calls), before)  # no broker / network call at all
        single.assert_not_called()  # the cheap path is the single combined read
        self.assertEqual(self.puts(), [])

    def test_flag_closes_within_one_poll_with_event_and_alert(self):
        router = self.opened()
        router.on_alert = MagicMock()
        heartbeat, posts = self.store().flag(HEARTBEAT_FLAG), self.posts()
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]
        self.assertEqual(router.process_operator_requests(LATER), {CID: "CLOSED"})
        row = self.rows()[0]
        self.assertEqual((row["status"], row["closed_reason"]), ("CLOSED", "MANUAL_CLOSE"))
        self.assertIsNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        [done] = self.events("MANUAL_CLOSE")
        self.assertEqual((done[2]["request_id"], done[2]["outcome"]), ("req-1", "CLOSED"))
        kinds = [c.args[0] for c in router.on_alert.call_args_list]
        self.assertEqual(kinds.count("MANUAL_CLOSE"), 1)  # delivered from the outbox in the same poll
        self.assertEqual([r["status"] for r in self.store().outbox()], ["SENT"])
        self.assertEqual(self.store().flag(HEARTBEAT_FLAG), heartbeat)  # no heartbeat from a poll
        self.assertEqual(self.posts(), posts)  # never opens

    def test_double_poll_never_closes_twice(self):
        router = self.opened()
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]
        router.process_operator_requests(LATER)
        self.assertEqual(router.process_operator_requests(LATER + timedelta(seconds=5)), {})
        router.manage(LATER + timedelta(seconds=30))
        self.assertEqual(self.puts(), [CLOSE])
        self.assertEqual(len(self.events("MANUAL_CLOSE")), 1)

    def test_dry_run_same_as_manage(self):
        self.opened()
        self.request()
        dry = self.router(AI_FLOOR_BROKER_DEMO_DRY_RUN="1")
        self.assertEqual(dry.process_operator_requests(LATER), {CID: "DRY_RUN"})
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.assertEqual(len(self.events("MANUAL_CLOSE_DRY_RUN")), 1)

    def test_blocked_router_behaves_like_manage(self):
        outcomes = []
        for poll in (True, False):
            with self.subTest(poll=poll):
                self.setUp()
                router = self.opened()
                router._set_blocked("drift")
                self.request()
                self.server.add("PUT", CLOSE, CLOSED_OK)
                result = router.process_operator_requests(LATER) if poll else router.manage(LATER)
                outcomes.append((result.get(CID), self.rows()[0]["closed_reason"], self.puts()))
        self.assertEqual(outcomes[0], outcomes[1])

    def test_closing_only_closes_without_opening(self):
        self.opened()
        router = self.router()
        router.closing_only = True
        posts = self.posts()
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]
        result = router.process_operator_requests(LATER)
        self.assertEqual(result[CID], "CLOSED")
        self.assertEqual(self.puts(), [CLOSE])
        self.assertEqual(self.posts(), posts)

    def test_flatten_request_is_processed_too(self):
        router = self.opened()
        self.stop()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]
        self.assertEqual(router.process_operator_requests(LATER), {CID: "CLOSED"})
        self.assertIsNone(self.store().flag(ops.FLATTEN_REQUEST_FLAG))

    def test_unreadable_flags_never_raise(self):
        router = self.opened()
        with patch.object(router._store, "flags", side_effect=RuntimeError("db locked")), \
                self.assertLogs("ai_floor.broker.demo_router", "WARNING") as logs:
            self.assertEqual(router.process_operator_requests(LATER), {})
        self.assertIn("OPERATOR_POLL_FAILED error_type=RuntimeError", logs.output[0])
        self.assertNotIn("db locked", logs.output[0])


class RuntimeDuplicateSlotTests(ManualBase):
    def runtime(self, router, **kwargs):
        from runtime.config import RuntimeConfig
        from runtime.service import OperationalRuntime
        config = RuntimeConfig(db_path=Path(self.tmp.name) / "trading.db", enabled_symbols=("EURUSD",))
        runtime = OperationalRuntime(config, clock=lambda: self.now, broker_router=router, **kwargs)
        self.addCleanup(runtime.close)
        return runtime

    def test_in_session_duplicate_slot_is_closed_by_one_poll(self):
        router = self.opened()
        runtime = self.runtime(router)
        runtime.run_cycle("EURUSD", self.now)  # the slot is claimed (router.on_cycle ran once)
        self.request()
        self.server.add("PUT", CLOSE, CLOSED_OK)
        self.assertEqual(runtime.run_cycle("EURUSD", self.now), "DUPLICATE")
        self.assertEqual(self.puts(), [])  # the old behaviour: waits for the next slot
        self.assertIsNotNone(self.store().flag(ops.MANUAL_CLOSE_REQUEST_FLAG))
        self.server.routes[("GET", OPEN_TRADES)] = [trades(open_trade()), trades()]
        self.assertEqual(runtime.process_operator_requests(LATER), {CID: "CLOSED"})
        self.assertEqual(self.rows()[0]["closed_reason"], "MANUAL_CLOSE")
        self.assertEqual(len(self.events("MANUAL_CLOSE")), 1)

    def test_halt_and_missing_router(self):
        router = SimpleNamespace(process_operator_requests=MagicMock(return_value={"x": "CLOSED"}))
        runtime = self.runtime(router)
        runtime.halt_gate = SimpleNamespace(halted=True)
        self.assertEqual(runtime.process_operator_requests(LATER), {})
        router.process_operator_requests.assert_not_called()
        runtime.halt_gate = None
        self.assertEqual(runtime.process_operator_requests(LATER), {"x": "CLOSED"})
        runtime.broker_router = None
        self.assertEqual(runtime.process_operator_requests(LATER), {})

    def test_router_exception_never_raises(self):
        router = SimpleNamespace(process_operator_requests=MagicMock(side_effect=RuntimeError("secret detail")))
        runtime = self.runtime(router)
        with self.assertLogs("ai_floor.runtime", "WARNING") as logs:
            self.assertEqual(runtime.process_operator_requests(LATER), {})
        self.assertNotIn("secret detail", "\n".join(logs.output))


class DemoRunnerWrapperTests(unittest.TestCase):
    def runner(self, results, gate=None):
        runner = DemoRunner.__new__(DemoRunner)
        runner.runtime = SimpleNamespace(process_operator_requests=MagicMock(return_value=results))
        runner.halt_gate = gate
        runner._capture_and_deliver = MagicMock()
        return runner

    def test_delivers_notifications_only_when_something_happened(self):
        runner = self.runner({CID: "CLOSED"})
        self.assertEqual(runner.process_operator_requests(LATER), {CID: "CLOSED"})
        runner.runtime.process_operator_requests.assert_called_once_with(LATER)
        runner._capture_and_deliver.assert_called_once_with()
        quiet = self.runner({})
        quiet.process_operator_requests(LATER)
        quiet._capture_and_deliver.assert_not_called()
        halted = self.runner({CID: "CLOSED"}, gate=SimpleNamespace(allow=lambda _c: False))
        halted.process_operator_requests(LATER)
        halted._capture_and_deliver.assert_not_called()


class CloudRunnerLoopTests(unittest.TestCase):
    def test_polls_every_five_seconds_and_keeps_the_tick_cadence(self):
        runner, sleeps = MagicMock(), []
        cloud_runner._idle(runner, lambda: False, sleep=sleeps.append)
        self.assertEqual(len(sleeps), cloud_runner.TICK_SECONDS)
        self.assertEqual(sum(sleeps), 30)
        self.assertEqual(runner.process_operator_requests.call_count, 6)
        runner.tick.assert_not_called()
        runner.daily_summary.assert_not_called()

    def test_loop_survives_wrapper_exceptions_logging_only_the_type(self):
        runner = MagicMock()
        runner.process_operator_requests.side_effect = ValueError("token=abc account=101-000")
        with self.assertLogs("runtime.cloud_runner", logging.WARNING) as logs:
            cloud_runner._idle(runner, lambda: False, sleep=lambda _s: None)
        self.assertEqual(runner.process_operator_requests.call_count, 6)
        self.assertTrue(all("OPERATOR_POLL_FAILED error_type=ValueError" in line for line in logs.output))
        self.assertNotIn("abc", "\n".join(logs.output))

    def test_stop_is_honoured_within_a_second(self):
        runner, sleeps, state = MagicMock(), [], {"stop": False}

        def sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) == 7:
                state["stop"] = True

        cloud_runner._idle(runner, lambda: state["stop"], sleep=sleep)
        self.assertEqual(len(sleeps), 7)
        self.assertEqual(runner.process_operator_requests.call_count, 1)  # the 5 s poll; none after the stop

    def test_main_loop_uses_the_polling_idle(self):
        source = Path(cloud_runner.__file__).read_text(encoding="utf-8")
        self.assertIn("_idle(runner, lambda: stop)", source)
        self.assertIn("runner.tick()", source)
        self.assertIn("runner.daily_summary(", source)


if __name__ == "__main__":
    unittest.main()
