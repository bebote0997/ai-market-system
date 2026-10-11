"""Process-level safety invariants: PAPER only, no broker SDK, NAS100 hard-blocked, Risk Engine before PaperBroker."""
import ast
import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from execution.paper_broker import PaperBroker
from execution.risk_reservation import prepare_reservation
from runtime.config import BLOCKED_SYMBOLS, RuntimeConfig
import test_demo_runner
import test_execution
from test_demo_runner import T, patched_scouts
from test_phase5_risk_engine import AT, INS, XAU_LONG, account, f3_plan, report

ROOT = Path(__file__).resolve().parent
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".ruff_cache"}
BROKER_SDKS = {"oanda", "oandapyV20", "MetaTrader5", "alpaca", "alpaca_trade_api", "ib_insync", "ccxt", "ibapi"}
# Every module allowed to call ``.submit_plan(``; each one reaches it only after an APPROVED Risk decision.
SUBMIT_PLAN_CALLERS = {"runtime/service.py", "execution/risk_reservation.py",
                       "replay/conflict_audit.py", "replay/risk_audit.py"}


def python_sources():
    for path in sorted(ROOT.rglob("*.py")):
        if not SKIP_DIRS.intersection(path.relative_to(ROOT).parts):
            yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def env_reads(tree):
    """String literals passed to os.environ.get / os.environ[...] / os.getenv in ``tree``."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = ast.unparse(func)
            if name in {"os.getenv", "getenv", "os.environ.get", "environ.get", "os.environ.setdefault"}:
                yield from (a.value for a in node.args[:1] if isinstance(a, ast.Constant))
        elif isinstance(node, ast.Subscript) and ast.unparse(node.value) in {"os.environ", "environ"}:
            if isinstance(node.slice, ast.Constant):
                yield node.slice.value


class RealExecutionDisabledTests(unittest.TestCase):
    def test_flag_is_false(self):
        from runtime import demo_runner
        self.assertIs(demo_runner.REAL_EXECUTION_ENABLED, False)

    def test_flag_is_a_literal_false_assigned_once(self):
        assignments = []
        for path, tree in python_sources():
            for node in ast.walk(tree):
                targets = (node.targets if isinstance(node, ast.Assign)
                           else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else [])
                for target in targets:
                    if ast.unparse(target).split(".")[-1] == "REAL_EXECUTION_ENABLED":
                        assignments.append((path.relative_to(ROOT).as_posix(), ast.unparse(node.value)))
        self.assertEqual(assignments, [("runtime/demo_runner.py", "False")])

    def test_no_env_var_feeds_the_flag(self):
        offenders = [(path.relative_to(ROOT).as_posix(), key) for path, tree in python_sources()
                     for key in env_reads(tree) if isinstance(key, str) and "REAL" in key.upper()]
        self.assertEqual(offenders, [])

    def test_env_vars_cannot_enable_it_on_import(self):
        env = {**os.environ, "REAL_EXECUTION_ENABLED": "1", "AI_FLOOR_REAL_EXECUTION_ENABLED": "1",
               "AI_FLOOR_REAL_EXECUTION": "true", "REAL_EXECUTION": "1", "PYTHONDONTWRITEBYTECODE": "1"}
        out = subprocess.run([sys.executable, "-c", "from runtime import demo_runner, cloud; "
                              "print(demo_runner.REAL_EXECUTION_ENABLED, cloud.REAL_EXECUTION_ENABLED)"],
                             cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=True)
        self.assertEqual(out.stdout.split(), ["False", "False"])


class NoBrokerSdkTests(unittest.TestCase):
    def test_no_module_imports_a_real_broker_sdk(self):
        offenders = []
        for path, tree in python_sources():
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                elif (isinstance(node, ast.Call) and ast.unparse(node.func) in
                      {"__import__", "importlib.import_module", "import_module"}
                      and node.args and isinstance(node.args[0], ast.Constant)):
                    names = [str(node.args[0].value)]
                offenders += [(path.relative_to(ROOT).as_posix(), name) for name in names
                              if name.split(".")[0] in BROKER_SDKS]
        self.assertEqual(offenders, [])


class Nas100HardBlockTests(unittest.TestCase):
    def test_blocked_list(self):
        self.assertEqual(BLOCKED_SYMBOLS, ("NAS100",))

    def test_runtime_config_rejects_nas100(self):
        for enabled in (("NAS100",), ("XAUUSD", "NAS100"), ("NAS100", "EURUSD", "XAUUSD")):
            with self.subTest(enabled=enabled), self.assertRaisesRegex(ValueError, "hard-blocked"):
                RuntimeConfig(enabled_symbols=enabled)
        self.assertEqual(RuntimeConfig().enabled_symbols, ("XAUUSD", "EURUSD"))

    def test_env_cannot_enable_nas100(self):
        for raw in ("NAS100", "XAUUSD,NAS100", "xauusd, nas100", "EURUSD,XAUUSD,NAS100"):
            with self.subTest(raw=raw), patch.dict(os.environ, {"AI_FLOOR_ENABLED_SYMBOLS": raw}), \
                    patch("runtime.env.load_local_env"), self.assertRaisesRegex(ValueError, "hard-blocked"):
                RuntimeConfig.from_env()
        with patch.dict(os.environ, {"AI_FLOOR_ENABLED_SYMBOLS": "XAUUSD,EURUSD"}), patch("runtime.env.load_local_env"):
            self.assertEqual(RuntimeConfig.from_env().enabled_symbols, ("XAUUSD", "EURUSD"))

    def test_cli_once_does_not_accept_nas100(self):
        from runtime import __main__ as cli
        with patch("sys.argv", ["runtime", "--once", "NAS100"]), \
                patch.object(cli.RuntimeConfig, "from_env") as from_env, \
                patch.object(cli, "OperationalRuntime") as runtime, \
                contextlib.redirect_stderr(io.StringIO()) as stderr, self.assertRaises(SystemExit) as exit_:
            cli.main()
        self.assertEqual(exit_.exception.code, 2)
        self.assertIn("invalid choice: 'NAS100'", stderr.getvalue())
        from_env.assert_not_called()
        runtime.assert_not_called()


class RiskApprovalBeforeBrokerTests(unittest.TestCase):
    """Every PaperBroker order is created by ``submit_plan``, and only for an APPROVED Risk decision."""

    def test_paper_orders_are_only_built_by_submit_plan(self):
        builders, callers = set(), set()
        for path, tree in python_sources():
            rel = path.relative_to(ROOT).as_posix()
            if rel.startswith("test_"):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and ast.unparse(node.func) == "PaperOrder":
                    builders.add(rel)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "submit_plan":
                    callers.add(rel)
        self.assertEqual(builders, {"execution/paper_broker.py"})
        self.assertEqual(callers, SUBMIT_PLAN_CALLERS)

    def test_submit_plan_refuses_without_approved_decision(self):
        fixture = test_execution.TestExecution()
        approved = fixture.report()
        for status in ("REJECTED", "PENDING", "approved", "", None):
            decision = type("Decision", (), {**vars(type(approved.risk_decision)), "status": status})()
            floor_report = type("Report", (), {**vars(type(approved)), "risk_decision": decision})()
            with self.subTest(status=status):
                broker = PaperBroker(fixture.account(), fixture.instrument())
                self.assertIsNone(broker.submit_plan(floor_report, {}, fixture.t(0)))
                self.assertEqual(broker.orders, {})
        no_decision = type("Report", (), {**vars(type(approved)), "risk_decision": None})()
        not_ready = type("Report", (), {**vars(type(approved)), "final_status": "RISK_REJECTED"})()
        for floor_report in (no_decision, not_ready):
            broker = PaperBroker(fixture.account(), fixture.instrument())
            self.assertIsNone(broker.submit_plan(floor_report, {}, fixture.t(0)))
            self.assertEqual(broker.orders, {})
        broker = PaperBroker(fixture.account(), fixture.instrument())  # control: the same fixture APPROVED submits
        self.assertIsNotNone(broker.submit_plan(approved, {}, fixture.t(0)))
        self.assertEqual(len(broker.orders), 1)

    def test_risk_v2_reservation_never_calls_broker_on_rejection(self):
        plan = f3_plan(*XAU_LONG)
        with patch.object(PaperBroker, "submit_plan", autospec=True, side_effect=PaperBroker.submit_plan) as spy:
            rejected = prepare_reservation(report(plan), INS["XAUUSD"], account(9500.0, realized=-500.0), {}, {}, as_of=AT)
            self.assertEqual((rejected.status, rejected.reason), ("REJECTED", "ACCOUNT_DRAWDOWN_LIMIT"))
            self.assertIsNone(rejected.order)
            spy.assert_not_called()
            approved = prepare_reservation(report(plan), INS["XAUUSD"], account(), {}, {}, as_of=AT)
        self.assertEqual(approved.status, "APPROVED")
        self.assertEqual(spy.call_count, 1)
        self.assertEqual(spy.call_args.args[1].risk_decision.status, "APPROVED")

    def test_runtime_cycle_reaches_broker_only_with_approved_decision(self):
        fixture = test_demo_runner.TestDemoRunner()
        with patch.object(PaperBroker, "submit_plan", autospec=True, side_effect=PaperBroker.submit_plan) as spy:
            fixture.setUp()
            runner = fixture.runner(multiplier=None)  # unknown contract multiplier: Risk Engine REJECTED
            try:
                with patched_scouts("LONG"):
                    self.assertEqual(runner.run_once("XAUUSD", T)["status"], "RISK_REJECTED")
                self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 0)
            finally:
                runner.close()
                fixture.tearDown()
            spy.assert_not_called()

            fixture.setUp()
            runner = fixture.runner()
            try:
                with patched_scouts("LONG"):
                    self.assertEqual(runner.run_once("XAUUSD", T)["status"], "PLAN_READY")
                self.assertEqual(runner.store.db.execute("SELECT count(*) FROM paper_orders").fetchone()[0], 1)
            finally:
                runner.close()
                fixture.tearDown()
        self.assertGreaterEqual(spy.call_count, 1)
        for call in spy.call_args_list:
            floor_report = call.args[1]
            self.assertEqual(floor_report.final_status, "PLAN_READY")
            self.assertEqual(floor_report.risk_decision.status, "APPROVED")


if __name__ == "__main__":
    unittest.main()
