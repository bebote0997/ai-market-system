"""Broker skeleton is inert: no runtime/floor/ui/entry-point module imports it, and REAL execution stays OFF.

Exceptions (each a lazy, guarded import): the read-only Control screen (registry snapshot, router status, read-only
balance) and ONE import of execution.broker_demo_router in runtime/service.py, executed only when
AI_FLOOR_BROKER_DEMO_ROUTING is exactly "1"."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
NEW_MODULES = {"execution.broker_port", "execution.paper_broker_adapter", "execution.dxtrade_client",
               "execution.dxtrade_broker", "execution.broker_config", "execution.account_registry",
               "execution.oanda_client", "execution.oanda_broker", "execution.broker_demo_router",
               "execution.broker_demo_state", "execution.oanda_readonly", "execution.operator_actions"}
SHORT_NAMES = ("broker_port", "paper_broker_adapter", "dxtrade_client", "dxtrade_broker", "broker_config",
               "account_registry", "oanda_client", "oanda_broker", "broker_demo_router", "broker_demo_state",
               "oanda_readonly", "operator_actions")
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
                    ("runtime/service.py", "execution.broker_demo_router"), ("runtime/service.py", "broker_demo_router")}
LAZY_IMPORTS = {"ui/views/control.py": {"execution.account_registry": [["read_control_snapshot"], ["ensure_registry_initialized"]],
                                        "execution.broker_demo_state": [["read_router_status"]],
                                        "execution.oanda_readonly": [["cached_account_view"]],
                                        "execution.historial": [["load_baselines"]],
                                        "execution.operator_actions": [["button_flags", "operator_state",
                                                                        "request_drift_clear",
                                                                        "request_emergency_stop"]]},
                "runtime/service.py": {"execution.broker_demo_router": [["router_from_env"]]}}


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


if __name__ == "__main__":
    unittest.main()
