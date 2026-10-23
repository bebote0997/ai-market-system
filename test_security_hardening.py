"""Regression tests for the 2026-10 security audit fixes (M-1, L-1..L-7, I-1..I-5)."""
from datetime import datetime, timezone
import importlib.util
import io
import json
import logging
import os
from pathlib import Path
import pickle
import re
import shlex
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from data.twelve_data_provider import TwelveDataMarketDataProvider, TwelveDataProviderError, _http_json
from replay.safe_pickle import UnsafePickleError, loads as safe_loads
from runtime.alert_channels import AlertChannelError, GmailEmailChannel, SlackAlertChannel
from runtime.notifications import NotificationEvent, SlackNotificationError, SlackNotificationSink, _slack_post
from runtime.trade_alerts import payload_text
from ui import auth
from ui.auth import LoginLimiter, require_dashboard_access

ROOT = Path(__file__).resolve().parent
PASSWORD = "correct-horse-battery"
REMOTE = {"RENDER": "true", "AI_FLOOR_DASHBOARD_PASSWORD": PASSWORD}


class Stop(Exception):
    pass


class Rerun(Exception):
    pass


class FakeUI:
    """Minimal Streamlit double: one instance per browser session."""

    def __init__(self, supplied="", click=True, address="0.0.0.0"):
        self.session_state = {}
        self.supplied, self.click, self.address = supplied, click, address
        self.errors, self.infos, self.inputs = [], [], 0

    def get_option(self, name):
        return self.address if name == "server.address" else None

    def title(self, *_): pass
    def info(self, message): self.infos.append(message)
    def error(self, message): self.errors.append(message)
    def text_input(self, *_, **__):
        self.inputs += 1
        return self.supplied
    def button(self, *_): return self.click
    def stop(self): raise Stop()
    def rerun(self): raise Rerun()


class Clock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class DashboardLoginTests(unittest.TestCase):
    def setUp(self):
        self.clock, self.sleeps, self.limiter = Clock(), [], LoginLimiter()

    def attempt(self, ui, env=REMOTE):
        try:
            return require_dashboard_access(ui, env, clock=self.clock, sleep=self.sleeps.append,
                                            limiter=self.limiter)
        except Stop:
            return "STOP"
        except Rerun:
            return "RERUN"

    def test_l1_non_ascii_input_and_password_never_raise(self):
        self.assertEqual(self.attempt(FakeUI("contraseña-ñandú-€")), "STOP")
        env = {"RENDER": "true", "AI_FLOOR_DASHBOARD_PASSWORD": "contraseña-ñandú-€"}
        ui = FakeUI("contraseña-ñandú-€")
        self.assertEqual(self.attempt(ui, env), "RERUN")
        self.assertIs(ui.session_state["dashboard_authenticated"], True)

    def test_m1_failure_delays_logs_without_input_and_locks_session(self):
        ui = FakeUI("guess-the-password-1")
        with self.assertLogs("ui.auth", logging.WARNING) as logs:
            for _ in range(auth.SESSION_MAX_FAILURES):
                self.assertEqual(self.attempt(ui), "STOP")
        self.assertEqual(self.sleeps, [auth.FAILURE_DELAY_SECONDS] * auth.SESSION_MAX_FAILURES)
        self.assertTrue(any("LOGIN_FAILED" in line for line in logs.output))
        self.assertFalse(any("guess-the-password" in line for line in logs.output))
        # Locked: the correct password is not even prompted for until the lockout expires.
        ui.supplied, ui.inputs = PASSWORD, 0
        self.assertEqual(self.attempt(ui), "STOP")
        self.assertEqual(ui.inputs, 0)
        self.assertIn("Demasiados intentos fallidos", ui.errors[-1])
        self.clock.now += auth.SESSION_LOCKOUT_SECONDS
        self.assertEqual(self.attempt(ui), "RERUN")

    def test_m1_new_sessions_share_a_global_failure_budget(self):
        for _ in range(auth.GLOBAL_MAX_FAILURES):
            self.attempt(FakeUI("wrong-password-xx"))  # fresh session each time
        owner = FakeUI(PASSWORD)
        self.assertEqual(self.attempt(owner), "STOP")
        self.assertEqual(owner.inputs, 0)
        self.clock.now += auth.GLOBAL_LOCKOUT_SECONDS
        self.assertEqual(self.attempt(owner), "RERUN")

    def test_m1_session_expires_after_ttl(self):
        ui = FakeUI(PASSWORD)
        self.assertEqual(self.attempt(ui), "RERUN")
        self.assertEqual(ui.session_state["dashboard_auth_expires_at"],
                         self.clock.now + auth.DEFAULT_SESSION_TTL_SECONDS)
        ui.click = False
        self.assertTrue(self.attempt(ui))
        self.clock.now += auth.DEFAULT_SESSION_TTL_SECONDS
        self.assertEqual(self.attempt(ui), "STOP")
        self.assertFalse(ui.session_state["dashboard_authenticated"])
        self.assertTrue(ui.infos)
        # A bare flag without an expiry (e.g. injected state) is not a valid session.
        bare = FakeUI(click=False)
        bare.session_state["dashboard_authenticated"] = True
        self.assertEqual(self.attempt(bare), "STOP")

    def test_m1_session_ttl_env_is_validated(self):
        ui = FakeUI(PASSWORD)
        self.attempt(ui, {**REMOTE, "AI_FLOOR_DASHBOARD_SESSION_TTL_SECONDS": "600"})
        self.assertEqual(ui.session_state["dashboard_auth_expires_at"], self.clock.now + 600)
        for bad in ("abc", "-1", "10", str(30 * 24 * 3600)):
            with self.subTest(bad=bad):
                ui = FakeUI(PASSWORD)
                self.assertEqual(self.attempt(ui, {**REMOTE, "AI_FLOOR_DASHBOARD_SESSION_TTL_SECONDS": bad}), "STOP")
                self.assertIn("misconfigured", ui.errors[-1])
                self.assertNotIn("dashboard_authenticated", ui.session_state)

    def test_m1_short_password_is_refused(self):
        ui = FakeUI("short")
        self.assertEqual(self.attempt(ui, {"RENDER": "true", "AI_FLOOR_DASHBOARD_PASSWORD": "short"}), "STOP")
        self.assertIn("at least 12 characters", ui.errors[-1])
        self.assertEqual(ui.inputs, 0)

    def test_l3_no_password_opens_only_on_loopback(self):
        for address in ("127.0.0.1", "localhost", "::1", "[::1]"):
            self.assertTrue(self.attempt(FakeUI(address=address), {}), address)
        for address in ("0.0.0.0", "", None, "192.168.1.10"):
            with self.subTest(address=address):
                ui = FakeUI(address=address)
                self.assertEqual(self.attempt(ui, {}), "STOP")
                self.assertIn("not configured", ui.errors[-1])
        self.assertEqual(self.attempt(FakeUI(address="127.0.0.1"), {"RENDER": "true"}), "STOP")


DOCUMENTED_LAUNCH = re.compile(r"streamlit run ui/app\.py[^`\"\n;)]*")


@unittest.skipUnless(importlib.util.find_spec("streamlit"), "streamlit not installed")
class DocumentedLaunchTests(unittest.TestCase):
    """The documented local launch passes the no-password loopback gate under real Streamlit config."""

    def launch_address(self, command):
        import streamlit
        from streamlit.web import bootstrap, cli
        argv = shlex.split(command)
        self.assertEqual(argv[:3], ["streamlit", "run", "ui/app.py"])
        params = cli.main_run.make_context("run", argv[2:]).params
        flags = {name: value for name, value in params.items() if name not in ("target", "args")}
        self.addCleanup(bootstrap.load_config_options, {})
        bootstrap.load_config_options(flag_options=flags)
        return streamlit

    def documented_commands(self):
        commands = []
        for name in ("docs/archive/UI_SPEC.md", "docs/archive/OPERATIONAL_RUNTIME_SPEC.md", "ui/app.py"):
            found = DOCUMENTED_LAUNCH.findall((ROOT / name).read_text(encoding="utf-8"))
            self.assertTrue(found, name)
            commands += [(name, command.strip()) for command in found]
        return commands

    def test_documented_launch_commands_open_locally_without_password(self):
        for name, command in self.documented_commands():
            with self.subTest(source=name, command=command):
                st = self.launch_address(command)
                self.assertEqual(st.get_option("server.address"), "127.0.0.1")
                self.assertTrue(require_dashboard_access(st, {}))

    def test_bare_launch_without_address_is_refused(self):
        st = self.launch_address("streamlit run ui/app.py")
        self.assertIsNone(st.get_option("server.address"))
        self.assertFalse(auth._loopback_only(st))


class CloudStartupTests(unittest.TestCase):
    def setUp(self):
        install = patch("execution.log_redaction.install_log_redaction")  # keep the test process logging untouched
        install.start()
        self.addCleanup(install.stop)

    def test_weak_or_misconfigured_dashboard_settings_stop_startup(self):
        from runtime import cloud
        base = {"RENDER": "true", "PORT": "10000"}
        cases = ({**base, "AI_FLOOR_DASHBOARD_PASSWORD": "short"},
                 {**base, "AI_FLOOR_DASHBOARD_PASSWORD": PASSWORD, "AI_FLOOR_DASHBOARD_SESSION_TTL_SECONDS": "x"})
        for env in cases:
            with self.subTest(env=sorted(env)), patch.dict(os.environ, env, clear=True), \
                    patch.object(cloud.subprocess, "Popen") as popen, \
                    self.assertRaises((RuntimeError, ValueError)) as caught:
                cloud.serve()
            popen.assert_not_called()
            self.assertNotIn(PASSWORD, str(caught.exception))

    def test_streamlit_is_started_without_error_details(self):
        source = (ROOT / "runtime" / "cloud.py").read_text(encoding="utf-8")
        self.assertIn('"--client.showErrorDetails=none"', source)

    def test_preflight_requires_a_strong_password(self):
        from runtime.cloud import cloud_preflight
        from runtime.config import RuntimeConfig
        config = RuntimeConfig(db_path=Path("/nonexistent/x.db"))
        weak = cloud_preflight(config, env={"AI_FLOOR_DASHBOARD_PASSWORD": "short"}, disk_mounted=False)
        strong = cloud_preflight(config, env={"AI_FLOOR_DASHBOARD_PASSWORD": PASSWORD}, disk_mounted=False)
        self.assertFalse(weak.checks["dashboard_auth"])
        self.assertTrue(strong.checks["dashboard_auth"])


class StreamlitPagesTests(unittest.TestCase):
    def test_l2_no_auto_routed_pages_directory_next_to_entry_point(self):
        self.assertFalse((ROOT / "ui" / "pages").exists())
        self.assertTrue((ROOT / "ui" / "views" / "dashboard.py").is_file())


class DependencyPinningTests(unittest.TestCase):
    def requirements(self, name):
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        return [line for line in lines if line.strip() and not line.lstrip().startswith(("#", "-r"))]

    def test_l4_direct_dependencies_are_exact_pins(self):
        for name in ("requirements.txt", "requirements-ui.txt", "requirements-dev.txt"):
            for line in self.requirements(name):
                self.assertRegex(line, r"^[A-Za-z0-9_.\-\[\]]+==[0-9][^ ]*$", f"{name}: {line}")
        ui = dict(line.split("==") for line in self.requirements("requirements-ui.txt"))
        major, minor = map(int, ui["streamlit"].split(".")[:2])
        self.assertGreaterEqual((major, minor), (1, 51))

    def test_l4_ci_and_render_install_a_hash_locked_set(self):
        text = (ROOT / "requirements-ci.txt").read_text(encoding="utf-8")
        blocks = re.split(r"\n(?=[A-Za-z])", "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")).strip())
        self.assertGreater(len(blocks), 10)
        for block in blocks:
            self.assertRegex(block.splitlines()[0], r"^[A-Za-z0-9_.\-]+==\S+( ; .+)? \\$")
            self.assertIn("--hash=sha256:", block)
        direct = {line.split("==")[0].lower(): line.split("==")[1]
                  for name in ("requirements.txt", "requirements-ui.txt") for line in self.requirements(name)}
        for package, version in direct.items():
            self.assertIn(f"\n{package}=={version} \\", "\n" + text)
        self.assertIn("pip install -r requirements-ci.txt", (ROOT / "render.yaml").read_text(encoding="utf-8"))

    def test_i4_github_actions_pinned_to_commit_shas(self):
        workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        uses = re.findall(r"uses:\s*(\S+)", workflow)
        self.assertTrue(uses)
        for action in uses:
            self.assertRegex(action, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")


class TwelveDataKeyTests(unittest.TestCase):
    SECRET = "td-secret-key-123456"

    def test_l6_key_is_never_in_the_url(self):
        seen = []

        def transport(url, key, timeout):
            seen.append((url, key))
            raise TwelveDataProviderError("AUTH_ERROR", "FOREX")
        provider = TwelveDataMarketDataProvider(api_key=self.SECRET, transport=transport, retries=0)
        with self.assertRaises(TwelveDataProviderError) as caught:
            provider._request("EURUSD", "5m")
        url, key = seen[0]
        self.assertEqual(key, self.SECRET)
        self.assertNotIn(self.SECRET, url)
        self.assertNotIn("apikey", url.lower())
        self.assertNotIn(self.SECRET, str(caught.exception))

    def test_l6_key_is_sent_as_authorization_header(self):
        with patch("data.twelve_data_provider.urlopen") as urlopen:
            urlopen.return_value.__enter__.return_value = io.BytesIO(b'{"status": "ok"}')
            self.assertEqual(_http_json("https://api.twelvedata.com/time_series?symbol=x", self.SECRET, 5),
                             {"status": "ok"})
        request = urlopen.call_args[0][0]
        self.assertEqual(request.get_header("Authorization"), "apikey " + self.SECRET)
        self.assertNotIn(self.SECRET, request.full_url)

    def test_l6_errors_and_undecodable_bodies_carry_no_key(self):
        from urllib.error import HTTPError, URLError
        for error in (HTTPError("https://api.twelvedata.com/x", 401, "nope", {}, io.BytesIO(b"")),
                      URLError("down"), ValueError("bad json")):
            def transport(url, key, timeout, error=error):
                raise error
            provider = TwelveDataMarketDataProvider(api_key=self.SECRET, transport=transport, retries=0)
            with self.subTest(error=type(error).__name__), self.assertRaises(TwelveDataProviderError) as caught:
                provider._request("XAUUSD", "1h")
            self.assertNotIn(self.SECRET, str(caught.exception))
            self.assertIsNone(caught.exception.__cause__)


def event(summary):
    # The compact line carries the execution reason: the hostile text reaches Slack through it.
    return NotificationEvent("1.0", "evt", "run", "2026-10-13T00:00:00Z", "INFO", "AI_REVIEW", "XAUUSD",
                             (), ({"agent": "macro", "status": "OK", "reasoning_summary": summary},),
                             None, (), (), (), execution={"reason": summary})


class SlackHardeningTests(unittest.TestCase):
    def test_l7_markup_is_escaped(self):
        sent = []
        sink = SlackNotificationSink(webhook_url="https://hooks.slack.com/services/T/B/x",
                                     transport=lambda url, payload, timeout: sent.append(payload))
        sink.deliver(event("<!channel> see <https://evil.example|bank> & more"))
        # Block Kit plain_text only: Slack never parses it as mrkdwn (no mention, link or formatting), shown literally.
        self.assertNotIn("mrkdwn", json.dumps(sent[0]))
        self.assertNotIn("text", sent[0])  # no top-level mrkdwn text
        self.assertIn("<!channel> see <https://evil.example|bank> & more", payload_text(sent[0]))

    def test_i3_only_https_slack_webhooks_are_opened(self):
        for url in ("file:///etc/passwd", "http://hooks.slack.com/services/x", "https://evil.example/hook",
                    "https://hooks.slack.com.evil.example/x"):
            with self.subTest(url=url), patch("runtime.notifications.urlopen") as urlopen:
                with self.assertRaises(SlackNotificationError) as caught:
                    _slack_post(url, {"text": "x"}, 5)
                self.assertEqual(str(caught.exception), "INVALID_WEBHOOK_URL")
                urlopen.assert_not_called()
        with patch("runtime.notifications.urlopen") as urlopen, self.assertRaises(AlertChannelError) as caught:
            SlackAlertChannel(webhook_url="file:///etc/passwd", environ={}).send(MagicMock())
        self.assertEqual(caught.exception.code, "INVALID_WEBHOOK_URL")
        self.assertFalse(caught.exception.retryable)
        urlopen.assert_not_called()


class EmailSenderTests(unittest.TestCase):
    def test_i1_no_personal_address_in_source(self):
        for path in (ROOT / "runtime").rglob("*.py"):
            self.assertIsNone(re.search(r"[\w.+-]+@gmail\.com", path.read_text(encoding="utf-8")), path)

    def test_i1_missing_sender_or_recipient_is_not_configured(self):
        for env in ({"GMAIL_APP_PASSWORD": "pw", "AI_FLOOR_ALERT_EMAIL_TO": "to@example.invalid"},
                    {"GMAIL_APP_PASSWORD": "pw", "AI_FLOOR_ALERT_EMAIL_FROM": "from@example.invalid"}):
            factory = MagicMock()
            channel = GmailEmailChannel(environ=env, smtp_factory=factory)
            with self.subTest(env=sorted(env)), self.assertRaises(AlertChannelError) as caught:
                channel.send(MagicMock())
            self.assertEqual(caught.exception.code, "NOT_CONFIGURED")
            self.assertFalse(caught.exception.retryable)
            self.assertEqual(len(channel.missing), 1)
            factory.assert_not_called()

    def test_i1_alert_test_reports_missing_setting_names_only(self):
        from runtime import alert_test
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.dict(os.environ, {"GMAIL_APP_PASSWORD": "secret-app-pw",
                                        "AI_FLOOR_ALERTS_LEDGER_PATH": str(Path(tmp) / "a.db")}, clear=True), \
                patch("sys.stdout", out):
            self.assertEqual(alert_test.main(["--email"]), 1)
        report = json.loads(out.getvalue())
        self.assertEqual(report["missing_settings"], ["AI_FLOOR_ALERT_EMAIL_FROM", "AI_FLOOR_ALERT_EMAIL_TO"])
        self.assertNotIn("secret-app-pw", out.getvalue())


class ReviewExportRedactionTests(unittest.TestCase):
    def test_l5_all_allowlisted_credentials_are_redacted(self):
        from runtime import review_export
        from runtime.env import NAMES
        values = {"GMAIL_APP_PASSWORD": "gmail-app-pass-1234", "FXMACRODATA_API_KEY": "fxmacro-key-12345"}
        with patch.dict(os.environ, values):
            cleaned = review_export._sanitize({"note": "x gmail-app-pass-1234 y fxmacro-key-12345"})
        self.assertEqual(cleaned["note"], "x [REDACTED] y [REDACTED]")
        for name in NAMES:
            if name.endswith(("_API_KEY", "_PASSWORD", "_WEBHOOK_URL")):
                self.assertIn(name, review_export._SECRET_NAMES)


class SafePickleTests(unittest.TestCase):
    def test_i2_lab_records_round_trip(self):
        slot = pd.Timestamp("2026-09-17 17:30", tz="UTC")
        chunk = {"symbol": "XAUUSD", "slots": 3, "records": [
            {"symbol": "XAUUSD", "slot": slot, "session": ("london",), "v1": {"r": np.float64(1.5)},
             "at": datetime(2026, 9, 17, tzinfo=timezone.utc), "seen": {1, 2}, "none": None}]}
        self.assertEqual(safe_loads(pickle.dumps(chunk)), chunk)

    def test_i2_code_execution_payload_is_refused(self):
        class Exploit:
            def __reduce__(self):
                return (os.system, ("echo pwned",))
        payload = pickle.dumps(Exploit())
        with patch("os.system") as system, self.assertRaises(UnsafePickleError):
            safe_loads(payload)
        system.assert_not_called()

    def test_i2_replay_tools_do_not_use_raw_pickle_loads(self):
        for name in ("fill_audit.py", "risk_audit.py", "lab_report.py"):
            source = (ROOT / "replay" / name).read_text(encoding="utf-8")
            self.assertNotIn("pickle.loads", source, name)
            self.assertIn("load_lab_chunk", source, name)


class BlockedSymbolDisplayTests(unittest.TestCase):
    def test_i5_dashboard_never_shows_nas100_enabled(self):
        from ui.state import enabled_market_symbols
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "floor.db"
            with sqlite3.connect(path) as db:
                db.execute("CREATE TABLE system_state(key TEXT PRIMARY KEY, value TEXT)")
                db.execute("INSERT INTO system_state VALUES('enabled_symbols', ?)",
                           (json.dumps(["XAUUSD", "NAS100"]),))
            db.close()
            with patch.dict(os.environ, {"AI_FLOOR_DB_PATH": str(path)}):
                self.assertEqual(enabled_market_symbols(), ("XAUUSD",))
                with sqlite3.connect(path) as db:
                    db.execute("UPDATE system_state SET value=? WHERE key='enabled_symbols'",
                               (json.dumps(["NAS100"]),))
                db.close()
                self.assertNotIn("NAS100", enabled_market_symbols())


if __name__ == "__main__":
    unittest.main()
