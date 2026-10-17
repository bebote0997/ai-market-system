"""Security hardening pass (docs/security_audit.md): no real account ids in the tree, log redaction, owner-only state
files and umask, explicit Streamlit hardening flags, auth gate ordering, egress/TLS hygiene and secrets hygiene.
Offline only: no network, no real serve(), fake secrets only."""
import ast
from decimal import Decimal
import io
import logging
import os
from pathlib import Path
import re
import sqlite3
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, patch

from execution import log_redaction
from execution.fs_security import private_state_dir, secure_sqlite_files
from execution.log_redaction import install_log_redaction, redact

ROOT = Path(__file__).resolve().parent
SYNTHETIC_ACCOUNTS = {"101-000-1234567-001", "101-001-12345678-001", "001-001-12345678-001", "001-001-87654321-001"}
ACCOUNT_RE = re.compile(r"\b\d{3}-\d{3}-\d{5,}-\d{3}\b")
OANDA_TOKEN = "0123456789abcdef0123456789abcdef-fedcba9876543210fedcba9876543210"
ACCOUNT = "101-000-1234567-001"
WEBHOOK = "https://hooks.slack.com/services/T000FAKE/B000FAKE/xxxxFAKExxxx"
POSIX = os.name == "posix"


def repo_files():
    try:
        out = subprocess.run(["git", "ls-files", "--cached", "--others", "--exclude-standard"], cwd=ROOT,
                             capture_output=True, text=True, check=True, timeout=30).stdout
        files = [ROOT / line for line in out.splitlines() if line]
    except (OSError, subprocess.SubprocessError):
        skip = {".git", "__pycache__", "venv", ".venv", "node_modules"}
        files = [p for p in ROOT.rglob("*") if p.is_file() and not skip & set(p.relative_to(ROOT).parts)]
    return [p for p in files if p.is_file() and p.suffix not in (".db", ".png", ".jpg", ".pkl", ".parquet", ".docx")]


def source_files():
    """Non-test Python sources (application code and scripts)."""
    skip = {".git", "__pycache__", "venv", ".venv", "docs"}
    for path in sorted(ROOT.rglob("*.py")):
        rel = path.relative_to(ROOT)
        if skip & set(rel.parts) or rel.name.startswith("test_"):
            continue
        yield path


class AccountIdTests(unittest.TestCase):
    def test_only_synthetic_account_ids_appear(self):
        found = set()
        for path in repo_files():
            for match in ACCOUNT_RE.findall(path.read_text(encoding="utf-8", errors="ignore")):
                found.add((path.relative_to(ROOT).as_posix(), match))
        self.assertEqual({item for item in found if item[1] not in SYNTHETIC_ACCOUNTS}, set())

    def test_no_default_account_id_in_code(self):
        from execution import oanda_broker, oanda_readonly
        self.assertFalse(hasattr(oanda_broker, "DEFAULT_ACCOUNT_ID"))
        self.assertFalse(hasattr(oanda_readonly, "DEFAULT_PRACTICE_ACCOUNT"))

    def test_dashboard_shows_sin_cuenta(self):
        from execution.oanda_readonly import account_view
        from ui.views.control import balance_lines
        view = account_view("practice", {"OANDA_PRACTICE_TOKEN": "tok-FAKE-practice-0123abcd"},
                            transport=MagicMock(side_effect=AssertionError("no network")))
        self.assertEqual(view.status, "SIN_CUENTA")
        self.assertEqual(balance_lines(view)[0], ("Estado", "sin cuenta"))


class RedactTests(unittest.TestCase):
    def test_shapes_are_redacted(self):
        cases = {
            f"token {OANDA_TOKEN} end": OANDA_TOKEN,
            "Authorization: Bearer abc.def-123": "abc.def-123",
            "headers={'Authorization': 'Basic dXNlcjpwYXNz'}": "dXNlcjpwYXNz",
            "sent Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig": "eyJhbGciOiJIUzI1NiJ9",
            "key sk-proj-ABCDEFGHIJKLMNOP1234 used": "sk-proj-ABCDEFGHIJKLMNOP1234",
            f"posting to {WEBHOOK} now": "B000FAKE",
            "db at postgres://admin:hunter2pass@db.internal:5432/x": "hunter2pass",
        }
        for text, secret in cases.items():
            with self.subTest(text=text):
                self.assertNotIn(secret, redact(text))
                self.assertIn("[REDACTED]", redact(text))

    def test_account_id_is_masked_not_removed(self):
        self.assertEqual(redact(f"account {ACCOUNT} ok"), "account 101-…4567-001 ok")
        self.assertEqual(redact("already 101-…4567-001"), "already 101-…4567-001")

    def test_extra_secrets_and_non_strings(self):
        self.assertEqual(redact("pw=s3cr3t-value!", extra_secrets=("s3cr3t-value!",)), "pw=[REDACTED]")
        self.assertEqual(redact(12345), "12345")
        self.assertEqual(redact("plain text, prices 1.09500 and 2026-10-17"), "plain text, prices 1.09500 and 2026-10-17")

    def test_env_secret_selection(self):
        env = {"OANDA_PRACTICE_TOKEN": "tok-FAKE-practice-0123abcd", "GMAIL_APP_PASSWORD": "gmail-fake-pw-1",
               "SLACK_WEBHOOK_URL": WEBHOOK, "STREAMLIT_SERVER_COOKIE_SECRET": "cookie-fake-secret",
               "MY_API_KEY": "short", "AI_FLOOR_MARKET_PROVIDER": "twelve_data_long_value", "SOME_SECRET": "  padded-secret  "}
        secrets = log_redaction.env_secrets(env)
        for value in ("tok-FAKE-practice-0123abcd", "gmail-fake-pw-1", WEBHOOK, "cookie-fake-secret", "padded-secret"):
            self.assertIn(value, secrets)
        self.assertNotIn("short", secrets)  # < 8 chars
        self.assertNotIn("twelve_data_long_value", secrets)  # not a secret-like name


class LogRedactionInstallTests(unittest.TestCase):
    SECRET = "tok-FAKE-env-secret-9876"

    def setUp(self):
        self.addCleanup(log_redaction.uninstall_log_redaction)
        self.assertTrue(install_log_redaction({"OANDA_PRACTICE_TOKEN": self.SECRET}))
        self.logger = logging.getLogger("ai_floor.test.redaction")

    def test_records_are_redacted_through_a_real_logger(self):
        with self.assertLogs(self.logger, logging.INFO) as logs:
            self.logger.info("token=%s account=%s hook=%s", self.SECRET, ACCOUNT, WEBHOOK)
            self.logger.warning("oanda %s", OANDA_TOKEN)
            self.logger.info({"Authorization": "Bearer abcdef123456"})
        text = "\n".join(logs.output) + "\n".join(r.getMessage() for r in logs.records)
        for secret in (self.SECRET, ACCOUNT, "B000FAKE", OANDA_TOKEN, "abcdef123456"):
            self.assertNotIn(secret, text)
        self.assertIn("101-…4567-001", text)

    def test_handler_output_and_exception_tracebacks_are_redacted(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        self.logger.addHandler(handler)
        self.addCleanup(self.logger.removeHandler, handler)
        try:
            raise RuntimeError(f"broker said {OANDA_TOKEN} for {ACCOUNT} with {self.SECRET}")
        except RuntimeError:
            with self.assertLogs(self.logger, logging.ERROR) as logs:
                self.logger.exception("failed calling %s", WEBHOOK)
        text = stream.getvalue() + "\n".join(logs.output)
        self.assertIn("Traceback", text)
        self.assertIn("RuntimeError", text)
        for secret in (OANDA_TOKEN, ACCOUNT, self.SECRET, "B000FAKE"):
            self.assertNotIn(secret, text)

    def test_raw_exc_info_is_dropped_after_redaction(self):
        try:
            raise RuntimeError(f"broker said {OANDA_TOKEN}")
        except RuntimeError:
            with self.assertLogs(self.logger, logging.ERROR) as logs:
                self.logger.exception("failed")
        record = logs.records[0]
        self.assertIsNone(record.exc_info)
        self.assertNotIn(OANDA_TOKEN, record.exc_text)
        self.assertIn("RuntimeError", record.exc_text)

    def test_formatter_format_exception_is_redacted_when_exc_text_is_cleared(self):
        try:
            raise ValueError(self.SECRET)
        except ValueError:
            record = self.logger.makeRecord(self.logger.name, logging.ERROR, "f", 1, "x", (), sys.exc_info())
        record.exc_text = None  # e.g. a handler that resets the cache
        self.assertNotIn(self.SECRET, logging.Formatter().format(record))
        self.assertNotIn(self.SECRET, logging.Formatter().formatStack(f"stack {self.SECRET}"))

    def test_install_is_idempotent_and_refreshes_secrets(self):
        factory = logging.getLogRecordFactory()
        self.assertTrue(install_log_redaction({"OANDA_LIVE_TOKEN": "tok-FAKE-second-1234"}))
        self.assertIs(logging.getLogRecordFactory(), factory)  # not wrapped twice
        with self.assertLogs(self.logger, logging.INFO) as logs:
            self.logger.info("live tok-FAKE-second-1234")
        self.assertNotIn("tok-FAKE-second-1234", "\n".join(logs.output))

    def test_bad_format_args_never_raise_or_leak(self):
        with self.assertLogs(self.logger, logging.INFO) as logs:
            self.logger.info("%s %s " + self.SECRET, "only-one-arg")
        self.assertEqual(logs.records[0].getMessage(), log_redaction.FAILED_MESSAGE)
        self.assertNotIn(self.SECRET, "\n".join(logs.output))

    def test_install_never_raises(self):
        with patch.object(log_redaction.logging, "getLogRecordFactory", side_effect=RuntimeError("boom")):
            self.assertFalse(install_log_redaction({}))

    def test_entry_points_install_it_guarded(self):
        for rel in ("runtime/cloud.py", "runtime/cloud_runner.py", "runtime/__main__.py", "ui/app.py"):
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            guarded = [node for node in ast.walk(tree) if isinstance(node, ast.Try)
                       and "install_log_redaction()" in ast.unparse(node)
                       and [ast.unparse(h.type) for h in node.handlers] == ["Exception"]]
            with self.subTest(file=rel):
                self.assertEqual(len(guarded), 1)


class FilePermissionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)

    @staticmethod
    def mode(path):
        return stat.S_IMODE(os.stat(path).st_mode)

    @unittest.skipUnless(POSIX, "POSIX permissions only")
    def test_new_broker_state_db_is_owner_only_in_a_dir_we_created(self):
        from execution import account_registry
        from execution.account_registry import AccountRegistry
        from execution.broker_demo_state import DemoStateStore
        db = self.base / "state" / "broker_state.db"
        self.addCleanup(account_registry._EMERGENCY_LATCHES.pop, account_registry._latch_key(db), None)
        self.addCleanup(os.umask, os.umask(0o022))  # a permissive umask: the 0o600 comes from secure_sqlite_files
        AccountRegistry(db, env={}).initialize()
        DemoStateStore(db).initialize()
        self.assertEqual(self.mode(db), 0o600)
        self.assertEqual(self.mode(db.parent), 0o700)

    @unittest.skipUnless(POSIX, "POSIX permissions only")
    def test_existing_directory_is_never_chmodded(self):
        from execution.broker_demo_state import DemoStateStore
        existing = self.base / "shared"
        existing.mkdir()
        os.chmod(existing, 0o755)
        db = existing / "broker_state.db"
        DemoStateStore(db).initialize()
        self.assertEqual(self.mode(existing), 0o755)
        self.assertEqual(self.mode(db), 0o600)

    @unittest.skipUnless(POSIX, "POSIX permissions only")
    def test_side_files_and_symlinks(self):
        db = self.base / "x.db"
        for suffix in ("", "-wal", "-shm"):
            Path(f"{db}{suffix}").write_text("")
            os.chmod(f"{db}{suffix}", 0o644)
        outside = self.base / "outside.txt"
        outside.write_text("")
        os.chmod(outside, 0o644)
        Path(f"{db}-journal").symlink_to(outside)
        changed = secure_sqlite_files(db)
        self.assertEqual({self.mode(f"{db}{s}") for s in ("", "-wal", "-shm")}, {0o600})
        self.assertEqual(self.mode(outside), 0o644)  # symlink target untouched
        self.assertEqual(len(changed), 3)
        self.assertEqual(secure_sqlite_files(db), [])  # nothing left to change

    def test_private_state_dir_and_never_raises(self):
        target = self.base / "new" / "nested"
        self.assertTrue(private_state_dir(target))
        self.assertFalse(private_state_dir(target))  # already exists: not ours to chmod
        self.assertEqual(secure_sqlite_files(self.base / "missing.db"), [])
        self.assertEqual(secure_sqlite_files(None), [])


class CloudServeTests(unittest.TestCase):
    PASSWORD = "correct-horse-battery-staple"
    REQUIRED_FLAGS = ("--server.enableXsrfProtection=true", "--server.enableCORS=true",
                      "--browser.gatherUsageStats=false", "--server.enableStaticServing=false",
                      "--client.toolbarMode=viewer", "--client.showErrorDetails=none")

    def serve(self, extra_env=None):
        from runtime import cloud
        manager = MagicMock()
        child = MagicMock()
        child.poll.return_value = 1  # the child "stopped": serve() raises instead of looping
        manager.popen.return_value = child
        env = {"RENDER": "true", "PORT": "10000", "AI_FLOOR_DASHBOARD_PASSWORD": self.PASSWORD,
               **(extra_env or {})}
        with patch.dict(os.environ, env, clear=True), patch.object(cloud.subprocess, "Popen", manager.popen), \
                patch.object(cloud.os, "umask", manager.umask), patch.object(cloud.signal, "signal"), \
                patch.object(cloud, "cloud_preflight", return_value=SimpleNamespace(infra_ready=True)), \
                patch("execution.log_redaction.install_log_redaction", manager.install), \
                self.assertRaises(RuntimeError):
            cloud.serve()
        return manager

    def test_umask_is_set_before_any_child_is_spawned(self):
        manager = self.serve()
        names = [call[0] for call in manager.mock_calls if call[0] in ("umask", "popen", "install")]
        self.assertEqual(names[:3], ["install", "umask", "popen"])
        manager.umask.assert_called_once_with(0o077)

    @unittest.skipUnless(POSIX, "POSIX permissions")
    def test_serve_tightens_existing_trading_db_and_sidecars(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "trading_floor.db"
            for suffix in ("", "-wal", "-shm"):
                path = Path(f"{db}{suffix}")
                path.write_bytes(b"x")
                os.chmod(path, 0o644)
            other = Path(tmp) / "other.txt"
            other.write_bytes(b"x")
            os.chmod(other, 0o644)
            self.serve({"AI_FLOOR_DB_PATH": str(db), "AI_FLOOR_BROKER_STATE_DB_PATH": str(Path(tmp) / "none.db")})
            for suffix in ("", "-wal", "-shm"):
                self.assertEqual(os.stat(f"{db}{suffix}").st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(other).st_mode & 0o777, 0o644)  # unrelated files are never touched

    def test_streamlit_hardening_flags_and_no_weakening(self):
        command = self.serve().popen.call_args[0][0]
        self.assertEqual(command[1:4], ["-m", "streamlit", "run"])
        for flag in self.REQUIRED_FLAGS:
            self.assertIn(flag, command)
        source = (ROOT / "runtime" / "cloud.py").read_text(encoding="utf-8")
        for weak in ("enableXsrfProtection=false", "enableCORS=false", "sslCertFile", "gatherUsageStats=true",
                     "toolbarMode=developer", "enableStaticServing=true"):
            self.assertNotIn(weak, source)
            self.assertNotIn(weak, " ".join(command))

    def test_streamlit_config_file_does_not_weaken_flags(self):
        config = ROOT / ".streamlit" / "config.toml"
        text = config.read_text(encoding="utf-8").replace(" ", "") if config.exists() else ""
        for weak in ("enableXsrfProtection=false", "enableCORS=false", "sslCertFile", "toolbarMode=\"developer\""):
            self.assertNotIn(weak, text)


def _is_st_call(node):
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name) and node.func.value.id in ("st", "streamlit"))


class AuthGateOrderTests(unittest.TestCase):
    APP = ROOT / "ui" / "app.py"

    def test_require_dashboard_access_precedes_every_rendering_statement(self):
        body = ast.parse(self.APP.read_text(encoding="utf-8")).body
        gate = [i for i, node in enumerate(body) if isinstance(node, ast.Expr)
                and ast.unparse(node.value) == "require_dashboard_access(st)"]
        self.assertEqual(len(gate), 1, "the gate must be ONE unconditional top-level statement")
        allowed_before = {"sys.path.insert", "st.set_page_config"}
        for node in body[:gate[0]]:
            with self.subTest(statement=ast.unparse(node)[:80]):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    continue
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                    continue  # docstring
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                    self.assertIn(ast.unparse(node.value.func), allowed_before)
                    continue
                if isinstance(node, ast.Try):  # only the guarded log-redaction install
                    self.assertIn("install_log_redaction()", ast.unparse(node))
                    self.assertFalse([n for n in ast.walk(node) if _is_st_call(n)])
                    continue
                self.fail("unexpected statement before require_dashboard_access")
        for node in body[gate[0] + 1:]:
            self.assertNotIn("require_dashboard_access", ast.unparse(node))  # no second, weaker gate

    def test_ui_modules_render_nothing_at_import_time(self):
        for path in sorted((ROOT / "ui").rglob("*.py")):
            if path.name == "app.py":
                continue
            if path.name == "asgi_app.py":  # builds the ASGI app only; renders nothing itself
                kinds = [type(n).__name__ for n in ast.parse(path.read_text(encoding="utf-8")).body]
                self.assertEqual(set(kinds) - {"Expr", "Import", "ImportFrom"}, {"Assign"})
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            top = [n for n in tree.body if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
            calls = [ast.unparse(n)[:60] for stmt in top for n in ast.walk(stmt) if _is_st_call(n)]
            with self.subTest(module=path.relative_to(ROOT).as_posix()):
                self.assertEqual(calls, [])

    def test_router_and_balance_views_are_unreachable_before_login(self):
        from streamlit.testing.v1 import AppTest
        import ui.views.control  # noqa: F401 - patch targets must be importable
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"RENDER": "true", "AI_FLOOR_DASHBOARD_PASSWORD": "fixture-password-123",
                                        "AI_FLOOR_DB_PATH": str(Path(tmp) / "floor.db"),
                                        "AI_FLOOR_HEALTH_DB_PATH": str(Path(tmp) / "health.db")}), \
                patch("ui.views.control.router_status") as router_status, \
                patch("ui.views.control.account_view") as account_view, \
                patch("ui.views.control.control_snapshot") as snapshot:
            app = AppTest.from_file(str(self.APP), default_timeout=30).run()
            self.assertFalse(app.exception, str(app.exception))
            self.assertEqual([t.label for t in app.text_input], ["Contraseña"])
            self.assertEqual(len(app.radio), 0)  # sidebar navigation never rendered
            self.assertEqual(len(app.selectbox), 0)
            self.assertFalse(Path(tmp, "floor.db").exists())  # no store opened pre-auth
        router_status.assert_not_called()
        account_view.assert_not_called()
        snapshot.assert_not_called()

    def test_session_ttl_default_stays_8h(self):
        from ui import auth
        self.assertEqual(auth.DEFAULT_SESSION_TTL_SECONDS, 8 * 3600)
        self.assertEqual(auth.session_ttl_seconds({}), 28800)
        self.assertIn("hmac.compare_digest", (ROOT / "ui" / "auth.py").read_text(encoding="utf-8"))


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}
NON_NETWORK_HOSTS = {"www.w3.org"}  # XML namespace identifiers, never fetched
URL_RE = re.compile(r"http://([^/\s:'\"]+)")


def _docstring_nodes(tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                yield first.value


class EgressTlsTests(unittest.TestCase):
    def test_no_tls_weakening_shell_true_or_plain_http_egress(self):
        offenders = []
        for path in source_files():
            rel = path.relative_to(ROOT).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=rel)
            docstrings = {id(node) for node in _docstring_nodes(tree)}
            for node in ast.walk(tree):
                if isinstance(node, ast.keyword) and isinstance(node.value, ast.Constant):
                    if (node.arg, node.value.value) in (("verify", False), ("shell", True)):
                        offenders.append((rel, node.value.lineno, f"{node.arg}={node.value.value}"))
                elif isinstance(node, (ast.Attribute, ast.Name)):
                    name = node.attr if isinstance(node, ast.Attribute) else node.id
                    if name in ("CERT_NONE", "_create_unverified_context"):
                        offenders.append((rel, node.lineno, name))
                elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) and node.value.value is False:
                    if any(isinstance(t, ast.Attribute) and t.attr == "check_hostname" for t in node.targets):
                        offenders.append((rel, node.lineno, "check_hostname = False"))
                elif (isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings):
                    for host in URL_RE.findall(node.value):
                        if host.lower() not in LOOPBACK_HOSTS | NON_NETWORK_HOSTS:
                            offenders.append((rel, node.lineno, f"http://{host}"))
        self.assertEqual(offenders, [])

    def test_scanner_catches_planted_violations(self):
        planted = ('import ssl, subprocess, requests\nctx = ssl._create_unverified_context()\n'
                   'ctx.check_hostname = False\nsubprocess.run("x", shell=True)\nrequests.get(u, verify=False)\n'
                   'URL = "http://api.example.com/v1"\nOK = "http://127.0.0.1:8501/"\n')
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "planted.py").write_text(planted, encoding="utf-8")
            with patch(f"{__name__}.source_files", return_value=[Path(tmp, "planted.py")]), \
                    patch(f"{__name__}.ROOT", Path(tmp)), self.assertRaises(AssertionError) as caught:
                self.test_no_tls_weakening_shell_true_or_plain_http_egress()
        for marker in ("_create_unverified_context", "check_hostname = False", "shell=True", "verify=False",
                       "http://api.example.com"):
            self.assertIn(marker, str(caught.exception))
        self.assertNotIn("127.0.0.1", str(caught.exception))


SECRET_NAME_RE = re.compile(r"TOKEN|SECRET|PASSWORD|API_KEY|WEBHOOK|ACCOUNT")


class SecretsHygieneTests(unittest.TestCase):
    def test_env_example_has_no_secret_values(self):
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
            if not line.strip() or line.lstrip().startswith("#") or "=" not in line:
                continue
            name, value = line.split("=", 1)
            with self.subTest(name=name):
                if SECRET_NAME_RE.search(name):
                    self.assertEqual(value.strip(), "")

    def test_render_yaml_secrets_are_sync_false_and_no_broker_keys(self):
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertNotIn("OANDA_", text)
        self.assertNotIn("DXTRADE_", text)
        entries = re.findall(r"-\s*key:\s*(\S+)\s*\n\s*(value:.*|sync:.*)", text)
        self.assertTrue(entries)
        for key, setting in entries:
            if re.search(r"TOKEN|SECRET|PASSWORD|API_KEY|WEBHOOK|COOKIE", key):
                with self.subTest(key=key):
                    self.assertEqual(setting.replace(" ", ""), "sync:false")

    def test_reprs_never_contain_token_or_account(self):
        from execution.account_registry import AccountRegistry
        from execution.broker_config import load_broker_config
        from execution.broker_demo_router import BrokerDemoRouter
        from execution.broker_demo_state import DemoStateStore
        from execution.oanda_broker import OandaBroker
        from execution.oanda_client import OandaClient
        from execution.oanda_readonly import OandaReadOnlyClient
        token = "tok-FAKE-practice-0123abcd"
        no_net = MagicMock(side_effect=AssertionError("no network"))
        env = {"AI_FLOOR_BROKER_DEMO_ENABLED": "1", "AI_FLOOR_BROKER_DEMO_PROVIDER": "oanda",
               "OANDA_PRACTICE_TOKEN": token, "OANDA_ACCOUNT_ID": ACCOUNT}
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "broker_state.db"
            registry = AccountRegistry(db, env=env)
            registry.set_enabled("broker_demo", True, actor="test")
            broker = OandaBroker(env=env, mode="broker_demo", registry=registry, transport=no_net)
            router = BrokerDemoRouter(broker, DemoStateStore(db), registry=registry, dry_run=True, max_units=100)
            objects = (broker, broker._client, OandaClient(token, ACCOUNT, transport=no_net),
                       OandaReadOnlyClient(token, ACCOUNT, "practice", transport=no_net), router,
                       load_broker_config(env), broker.health())
            from execution import account_registry
            account_registry._EMERGENCY_LATCHES.pop(account_registry._latch_key(db), None)
        for obj in objects:
            for text in (repr(obj), str(obj)):
                with self.subTest(obj=type(obj).__name__):
                    self.assertNotIn(token, text)
                    self.assertNotIn(ACCOUNT, text)

    def test_review_export_never_contains_env_secrets(self):
        from runtime import review_export
        secrets = {"OANDA_PRACTICE_TOKEN": "tok-FAKE-practice-0123abcd", "OANDA_LIVE_TOKEN": OANDA_TOKEN,
                   "OPENAI_API_KEY": "sk-FAKEFAKEFAKEFAKEFAKE1234", "AI_FLOOR_DASHBOARD_PASSWORD": "dash-fake-pw-123",
                   "SLACK_WEBHOOK_URL": WEBHOOK, "GMAIL_APP_PASSWORD": "gmail-fake-pw-1"}
        leak = " ".join([*secrets.values(), ACCOUNT, "Authorization: Bearer abcdef123456"])
        db = sqlite3.connect(":memory:")
        db.execute("CREATE TABLE review_reports(payload TEXT)")
        db.execute("CREATE TABLE notification_events(journal_id INTEGER, payload TEXT)")
        db.execute("INSERT INTO review_reports VALUES (?)",
                   (f'{{"run_id": "r1", "error": "{leak}", "warnings": ["{leak}"], "agents": {{"note": "{leak}"}}}}',))
        db.execute("INSERT INTO notification_events VALUES (1, ?)", (f'{{"message": "{leak}", "api_token": "x"}}',))
        store = SimpleNamespace(db=db, get_state=lambda key: leak)
        with patch.dict(os.environ, secrets):
            output = review_export.review_bundle_json(store)
        db.close()
        self.assertIn('"run_id":"r1"', output)
        for secret in (*secrets.values(), "B000FAKE", ACCOUNT, "abcdef123456"):
            self.assertNotIn(secret, output)


class DecimalSanity(unittest.TestCase):
    def test_redaction_keeps_numbers(self):  # prices / ids in logs stay readable
        self.assertEqual(redact(str(Decimal("1.09512"))), "1.09512")
        self.assertEqual(redact("tradeID=6397 units=-100"), "tradeID=6397 units=-100")


if __name__ == "__main__":
    unittest.main()
