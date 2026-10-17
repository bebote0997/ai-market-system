"""scripts/oanda_first_test_order.py: dry-run by default, explicit approval gate, unit cap, guards, one POST only with
the exact confirmation, read-only status, no secrets in output. Fake transport only; default transport patched."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from execution import account_registry
from execution.account_registry import AccountRegistry
from scripts import oanda_first_test_order as script
from test_broker_oanda import FILLED, NOT_FOUND, OPEN_TRADES, ORDERS, PRICING, close_fill, pricing
from test_broker_oanda_client import A, ACCOUNT, TOKEN, FakeServer, raising_transport

CONFIRM = "I-APPROVE-PRACTICE-TEST-ORDER"


class ScriptTests(unittest.TestCase):
    def setUp(self):
        default = patch("execution.oanda_client.urllib_transport", side_effect=raising_transport)
        self.default_transport = default.start()
        self.addCleanup(default.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "broker_state.db"
        self.addCleanup(account_registry._EMERGENCY_LATCHES.pop, account_registry._latch_key(self.db), None)
        self.read_env = {"OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": ACCOUNT,
                         "AI_FLOOR_BROKER_STATE_DB_PATH": str(self.db)}
        self.env = {**self.read_env, "AI_FLOOR_BROKER_DEMO_ENABLED": "1", "AI_FLOOR_BROKER_DEMO_PROVIDER": "oanda"}
        self.server = FakeServer()
        self.server.add("GET", PRICING, pricing(bid="1.09500", ask="1.09510"))

    def tearDown(self):
        self.default_transport.assert_not_called()

    def run_script(self, *argv, env=None):
        out = io.StringIO()
        code = script.main(list(argv), self.env if env is None else env, self.server, out=out)
        text = out.getvalue()
        self.assertNotIn(TOKEN, text)
        self.assertNotIn(ACCOUNT, text)
        return code, text

    def enable(self):
        AccountRegistry(self.db, env=self.env).set_enabled("broker_demo", True, actor="owner")

    def posts(self):
        return [c for c in self.server.calls if c[0] != "GET"]

    def test_default_is_dry_run_without_post(self):
        code, text = self.run_script(env=self.read_env)  # no demo flags / switch needed to preview
        self.assertEqual(code, 0)
        payload = json.loads(text)
        order = payload["order"]
        self.assertTrue(payload["mode"].startswith("DRY_RUN"))
        self.assertEqual((order["instrument"], order["units"]), ("EUR_USD", "1"))
        self.assertEqual(order["stopLossOnFill"]["price"], "1.09310")  # ask - 20 pips
        self.assertEqual(order["takeProfitOnFill"]["price"], "1.09910")  # ask + 40 pips
        self.assertRegex(order["clientExtensions"]["id"], r"^aif-test-\d{8}T\d{6}Z$")
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.server.paths(), [PRICING])

    def test_sell_preview_uses_bid(self):
        code, text = self.run_script("--side", "SELL", env=self.read_env)
        order = json.loads(text)["order"]
        self.assertEqual((order["units"], order["stopLossOnFill"]["price"], order["takeProfitOnFill"]["price"]),
                         ("-1", "1.09700", "1.09100"))

    def test_refusals_touch_no_network(self):
        self.enable()
        for argv in (("--send",), ("--send", "--confirm", "yes"), ("--send", "--confirm", CONFIRM.lower()),
                     ("--send", "--confirm", CONFIRM, "--units", "101"), ("--units", "0"),
                     ("--close", "6397"), ("--close", "6397", "--confirm", "nope"),
                     ("--send", "--close", "1", "--confirm", CONFIRM)):
            with self.subTest(argv=argv):
                code, text = self.run_script(*argv)
                self.assertEqual(code, 2)
                self.assertIn("REFUSED", text)
        self.assertEqual(self.server.calls, [])

    def test_missing_account_id_refuses_every_mode(self):
        self.enable()
        env = {k: v for k, v in self.env.items() if k != "OANDA_ACCOUNT_ID"}
        for argv in ((), ("--status",), ("--send", "--confirm", CONFIRM)):
            with self.subTest(argv=argv):
                code, text = self.run_script(*argv, env=env)
                self.assertEqual(code, 2)
                self.assertIn("guards not satisfied", text)
        self.assertEqual(self.server.calls, [])

    def test_guards_off_refuse_send(self):
        for env in (self.read_env, {**self.env, "AI_FLOOR_EMERGENCY_STOP": "1"}, self.env):  # last: switch OFF
            with self.subTest(env=sorted(env)):
                code, text = self.run_script("--send", "--confirm", CONFIRM, env=env)
                self.assertEqual(code, 2)
                self.assertIn("guards not satisfied", text)
        self.assertEqual(self.server.calls, [])

    def test_market_closed_refuses(self):
        self.enable()
        self.server.routes[("GET", PRICING)] = [pricing(tradeable=False)]
        code, text = self.run_script("--send", "--confirm", CONFIRM)
        self.assertEqual(code, 2)
        self.assertIn("not tradeable", text)
        self.assertEqual(self.posts(), [])

    def test_correct_invocation_sends_exactly_one_post(self):
        self.enable()
        self.server.add("GET", OPEN_TRADES, (200, {"trades": []}))
        with patch.object(script, "test_plan", wraps=script.test_plan) as plan:
            self.server.add("GET", f"{A}/trades/@aif-test-20261017T120000Z", NOT_FOUND)
            self.server.add("POST", ORDERS, FILLED)
            from datetime import datetime, timezone
            out = io.StringIO()
            code = script.main(["--send", "--confirm", CONFIRM, "--units", "100"], self.env, self.server, out=out,
                               now=datetime(2026, 10, 17, 12, tzinfo=timezone.utc))
        self.assertEqual(code, 0, out.getvalue())
        plan.assert_called_once()
        [post] = self.posts()
        self.assertEqual(post[0], "POST")
        self.assertEqual(post[3]["order"]["units"], "100")
        result = json.loads(out.getvalue())
        self.assertEqual((result["submit"], result["trade_id"]), ("ACCEPTED", "6397"))
        self.assertNotIn(TOKEN, out.getvalue())
        self.assertNotIn(ACCOUNT, out.getvalue())

    def test_close_with_confirm(self):
        self.enable()
        self.server.add("PUT", f"{A}/trades/6397/close", (200, {"orderFillTransaction": close_fill()}))
        code, text = self.run_script("--close", "6397", "--confirm", CONFIRM)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(text)["close"], "ACCEPTED")

    def test_status_is_read_only(self):
        self.server.add("GET", f"{A}/summary", (200, {"account": {"balance": "100000", "NAV": "100000",
                                                                  "marginUsed": "0", "currency": "USD"}}))
        self.server.add("GET", OPEN_TRADES, (200, {"trades": []}))
        code, text = self.run_script("--status", env=self.read_env)
        self.assertEqual(code, 0)
        payload = json.loads(text)
        self.assertEqual((payload["account"], payload["currency"]), ("101-…5678-001", "USD"))
        self.assertEqual(self.posts(), [])

    def test_nothing_runs_at_import_and_docstring_says_not_run(self):
        self.assertIn("NOT run", script.__doc__)
        self.assertEqual(script.MAX_UNITS, 100)


if __name__ == "__main__":
    unittest.main()
