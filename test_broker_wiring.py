"""Broker skeleton is inert: no runtime/floor/ui/entry-point module imports it, and REAL execution stays OFF.

Exceptions (each a lazy, guarded import): the read-only Control screen (registry snapshot, router status, read-only
balance), the read-only OANDA demo view (execution.demo_dashboard) of the Overview/Trades/Decisions and experiment
screens and of the daily Slack summary (runtime/notifications.py), and ONE import of execution.broker_demo_router in
runtime/service.py, executed only when AI_FLOOR_BROKER_DEMO_ROUTING is exactly "1"."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
NEW_MODULES = {"execution.broker_port", "execution.paper_broker_adapter", "execution.dxtrade_client",
               "execution.dxtrade_broker", "execution.broker_config", "execution.account_registry",
               "execution.oanda_client", "execution.oanda_broker", "execution.broker_demo_router",
               "execution.broker_demo_state", "execution.oanda_readonly", "execution.operator_actions",
               "execution.demo_dashboard"}
SHORT_NAMES = ("broker_port", "paper_broker_adapter", "dxtrade_client", "dxtrade_broker", "broker_config",
               "account_registry", "oanda_client", "oanda_broker", "broker_demo_router", "broker_demo_state",
               "oanda_readonly", "operator_actions", "demo_dashboard")
GUARDED_DIRS = ("runtime", "floor", "ui")
GUARDED_FILES = ("servicio.py", "app.py", "main.py")
# The only allowed references: the read-only Control screen reads the account registry snapshot, the router status
# and the read-only balance through lazy, guarded imports (any failure => everything shown OFF); runtime/service.py
# lazily imports router_from_env behind the exact routing flag. Nothing else may import the skeleton.
ALLOWED_READONLY = {("ui/views/control.py", "execution.account_registry"), ("ui/views/control.py", "account_registry"),
                    ("ui/views/control.py", "execution.broker_demo_state"), ("ui/views/control.py", "broker_demo_state"),
                    ("ui/views/control.py", "execution.oanda_readonly"), ("ui/views/control.py", "oanda_readonly"),
                    # Emergency button / drift-clear request (writes only the state DB; never orders): lazy + guarded.
                    ("ui/views/control.py", "execution.operator_actions"), ("ui/views/control.py", "operator_actions"),
                    # Floor «Cerrar operación» (owner-only manual close REQUEST in the state DB; the router closes):
                    # lazy + guarded.
                    ("ui/views/floor_actions.py", "execution.operator_actions"),
                    ("ui/views/floor_actions.py", "operator_actions"),
                    ("runtime/service.py", "execution.broker_demo_router"), ("runtime/service.py", "broker_demo_router"),
                    # OANDA demo numbers (GET-only reads + broker state DB mode=ro; no writes, no orders): lazy + guarded.
                    ("ui/views/luxury.py", "execution.demo_dashboard"), ("ui/views/luxury.py", "demo_dashboard"),
                    ("ui/views/experiment.py", "execution.demo_dashboard"), ("ui/views/experiment.py", "demo_dashboard"),
                    # DEC-DEMO-GATE-1: daily Slack summary with OANDA demo figures while routing is ON: lazy + guarded.
                    ("runtime/notifications.py", "execution.demo_dashboard"),
                    ("runtime/notifications.py", "demo_dashboard"),
                    # Daily summary: FRESH GET-only reads (no TTL cache) when the 17:00 ET read failed: lazy + guarded.
                    ("runtime/notifications.py", "execution.oanda_readonly"),
                    ("runtime/notifications.py", "oanda_readonly"),
                    # DEC-ALERTS-REDESIGN-1: Floor-style trade alerts from OANDA demo data (GET-only reads + broker
                    # state DB mode=ro; no writes, no orders), in the alert worker thread: lazy + guarded.
                    ("runtime/trade_alert_source.py", "execution.demo_dashboard"),
                    ("runtime/trade_alert_source.py", "demo_dashboard"),
                    ("runtime/trade_alert_source.py", "execution.oanda_readonly"),
                    ("runtime/trade_alert_source.py", "oanda_readonly"),
                    # Floor page: peek-only reader of the oanda_readonly TTL caches via sys.modules (never imports or
                    # fetches; no network, no orders), plugged into demo_dashboard.demo_snapshot readers.
                    # DEC-OANDA-ONLY-1 Process view: OANDA-demo rows of one run (state DB mode=ro; no writes): lazy.
                    ("ui/views/process.py", "execution.broker_demo_state"), ("ui/views/process.py", "broker_demo_state"),
                    ("ui/floor_cache.py", "execution.oanda_readonly"), ("ui/floor_cache.py", "oanda_readonly"),
                    # Floor demo cache producer: the existing cached GET-only readers (summary + trades) in a daemon
                    # thread of the dashboard process; credentials checked for presence only; no writes, no orders.
                    ("ui/demo_refresher.py", "execution.oanda_readonly"), ("ui/demo_refresher.py", "oanda_readonly"),
                    ("ui/floor_cache.py", "execution.demo_dashboard"), ("ui/floor_cache.py", "demo_dashboard")}
LAZY_IMPORTS = {"ui/views/process.py": {"execution.broker_demo_state": [["CLOSED", "CLOSED_AT_BROKER",
                                                                          "read_run_trades"]]},
                "ui/views/control.py": {"execution.account_registry": [["read_control_snapshot"], ["ensure_registry_initialized"]],
                                        "execution.broker_demo_state": [["read_router_status"]],
                                        "execution.oanda_readonly": [["cached_account_view"]],
                                        "execution.historial": [["load_baselines"]],
                                        "execution.operator_actions": [["button_flags", "operator_state",
                                                                        "request_drift_clear",
                                                                        "request_emergency_stop"]]},
                "runtime/service.py": {"execution.broker_demo_router": [["router_from_env"]]},
                "ui/demo_refresher.py": {"execution.oanda_readonly": [["ENV_NAMES"], ["cached_account_view",
                                                                                      "cached_demo_trades"]]},
                "ui/views/floor_actions.py": {"execution.operator_actions": [["manual_close_target", "operator_state",
                                                                              "practice_problem",
                                                                              "request_manual_close"]]},
                "ui/views/luxury.py": {"execution.demo_dashboard": [["demo_epoch", "demo_progress",
                                                                     "demo_snapshot"]]},
                # Experimento counts the day from the epoch (ui.demo_clock) and reads numbers via luxury.demo_api.
                "ui/views/experiment.py": {"execution.demo_dashboard": []},
                "runtime/trade_alert_source.py": {"execution.demo_dashboard": [["state_db_path"], ["demo_progress"]],
                                                  "execution.oanda_readonly": [["account_view", "demo_trades"]]},
                "runtime/notifications.py": {"execution.demo_dashboard": [["demo_progress", "demo_snapshot"]],
                                             "execution.oanda_readonly": [["account_view", "demo_trades"]],
                                             "execution.results_source": [["demo_results"]]}}


def guarded_sources():
    for directory in GUARDED_DIRS:
        yield from sorted((ROOT / directory).rglob("*.py"))
    yield from sorted(ROOT.glob("ejecutar_*.py"))
    yield from (ROOT / name for name in GUARDED_FILES if (ROOT / name).exists())


def imported_modules(tree):
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            yield node.module
            yield from (f"{node.module}.{alias.name}" for alias in node.names)


class BrokerSkeletonNotWiredTests(unittest.TestCase):
    def test_no_guarded_module_imports_the_broker_skeleton(self):
        offenders = []
        for path in guarded_sources():
            text = path.read_text(encoding="utf-8")
            tree = ast.parse(text, filename=str(path))
            names = set(imported_modules(tree))
            offenders += [(path.relative_to(ROOT).as_posix(), name) for name in names if name in NEW_MODULES]
            offenders += [(path.relative_to(ROOT).as_posix(), short) for short in SHORT_NAMES if short in text]
        self.assertEqual([o for o in offenders if o not in ALLOWED_READONLY], [])

    def test_allowed_imports_are_lazy_and_minimal(self):
        for rel, modules in LAZY_IMPORTS.items():
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
            for module, expected in modules.items():
                with self.subTest(file=rel, module=module):
                    self.assertFalse([n for n in top_level if module.split(".")[-1] in ast.dump(n)])
                    imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == module]
                    self.assertEqual([[a.name for a in n.names] for n in imports], expected)

    def test_service_router_import_is_behind_the_exact_flag_and_guarded(self):
        tree = ast.parse((ROOT / "runtime/service.py").read_text(encoding="utf-8"))
        guarded = []
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and "AI_FLOOR_BROKER_DEMO_ROUTING" in ast.unparse(node.test):
                self.assertIn("== '1'", ast.unparse(node.test))
                for inner in ast.walk(node):
                    if isinstance(inner, ast.Try) and any(isinstance(n, ast.ImportFrom) and n.module ==
                                                          "execution.broker_demo_router" for n in ast.walk(inner)):
                        guarded.append(inner)
        self.assertEqual(len(guarded), 1)
        self.assertEqual([ast.unparse(h.type) for h in guarded[0].handlers], ["Exception"])

    def test_guarded_sources_exist(self):
        found = {path.relative_to(ROOT).parts[0] for path in guarded_sources()}
        self.assertTrue({"runtime", "ui"} <= found)

    def test_real_execution_still_disabled(self):
        from runtime import demo_runner
        self.assertIs(demo_runner.REAL_EXECUTION_ENABLED, False)

    def test_render_yaml_has_no_broker_keys(self):
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        for key in ("DXTRADE_", "AI_FLOOR_BROKER_", "OANDA_"):
            self.assertNotIn(key, text)

    def test_order_path_never_imports_the_readonly_client(self):
        for rel in ("execution/oanda_client.py", "execution/oanda_broker.py", "execution/broker_demo_router.py",
                    "execution/broker_demo_state.py", "scripts/oanda_first_test_order.py"):
            with self.subTest(file=rel):
                self.assertNotIn("oanda_readonly", (ROOT / rel).read_text(encoding="utf-8"))
                self.assertNotIn("demo_dashboard", (ROOT / rel).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
