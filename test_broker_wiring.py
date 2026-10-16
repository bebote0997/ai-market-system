"""Broker skeleton is inert: no runtime/floor/ui/entry-point module imports it, and REAL execution stays OFF."""
import ast
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent
NEW_MODULES = {"execution.broker_port", "execution.paper_broker_adapter", "execution.dxtrade_client",
               "execution.dxtrade_broker", "execution.broker_config", "execution.account_registry"}
GUARDED_DIRS = ("runtime", "floor", "ui")
GUARDED_FILES = ("servicio.py", "app.py", "main.py")
# The only allowed reference: the read-only Control screen reads the account registry snapshot through a lazy,
# guarded import (any failure => everything shown OFF). Nothing else may import the skeleton.
ALLOWED_READONLY = {("ui/views/control.py", "execution.account_registry"), ("ui/views/control.py", "account_registry")}


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
            offenders += [(path.relative_to(ROOT).as_posix(), short) for short in
                          ("broker_port", "paper_broker_adapter", "dxtrade_client", "dxtrade_broker",
                           "broker_config", "account_registry") if short in text]
        self.assertEqual([o for o in offenders if o not in ALLOWED_READONLY], [])

    def test_control_screen_only_imports_read_control_snapshot_lazily(self):
        tree = ast.parse((ROOT / "ui/views/control.py").read_text(encoding="utf-8"))
        top_level = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertFalse([n for n in top_level if "account_registry" in ast.dump(n)])
        imports = [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "execution.account_registry"]
        self.assertEqual([[a.name for a in n.names] for n in imports], [["read_control_snapshot"]])

    def test_guarded_sources_exist(self):
        found = {path.relative_to(ROOT).parts[0] for path in guarded_sources()}
        self.assertTrue({"runtime", "ui"} <= found)

    def test_real_execution_still_disabled(self):
        from runtime import demo_runner
        self.assertIs(demo_runner.REAL_EXECUTION_ENABLED, False)

    def test_render_yaml_has_no_broker_keys(self):
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        for key in ("DXTRADE_", "AI_FLOOR_BROKER_"):
            self.assertNotIn(key, text)


if __name__ == "__main__":
    unittest.main()
