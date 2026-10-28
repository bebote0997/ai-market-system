"""Daily OANDA demo summary at the 17:00 ET rollover: a failed NAV / trades read is retried twice (20 s, 40 s) with
FRESH reads (no TTL cache), then reported "sin datos" per piece with ONE diagnostic line of non-secret codes.
Fake readers only; no network, no sleeps."""
from datetime import datetime, timezone
from decimal import Decimal
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from execution import demo_dashboard, oanda_readonly
from execution.oanda_readonly import AccountView, ReadOnlyError
from runtime import notifications

TOKEN = "tok-FAKE-practice-0123abcd"
ACCOUNT = "101-001-12345678-001"
CLOSE = "2026-10-27T21:00:00Z"
SENT = datetime(2026, 10, 27, 21, 0, 33, tzinfo=timezone.utc)  # 17:00:33 ET
OK_VIEW = AccountView("OK", "practice", "***-001", "USD", Decimal("100123.45"), Decimal("100200.00"),
                      Decimal("0"), 0, Decimal("76.55"), SENT)
FAILED_VIEW = AccountView("ERROR", "practice", "***-001", as_of=SENT, error_code="http_503")
TRADES = {"closed": [], "open": [], "as_of": SENT}


class Readers:
    """Scripted fresh / cached readers: each list is consumed one item per call (the last one is sticky)."""

    def __init__(self, cached_account, cached_trades, fresh_accounts, fresh_trades):
        self.cached_account, self.cached_trades = cached_account, cached_trades
        self.fresh_accounts, self.fresh_trades = list(fresh_accounts), list(fresh_trades)
        self.calls = []

    @staticmethod
    def _next(queue):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    def account_view(self, environment, env=None, **_):
        self.calls.append(("fresh_account", environment))
        return self._next(self.fresh_accounts)

    def demo_trades(self, env=None, *, since=None, report_errors=False, **_):
        self.calls.append(("fresh_trades", report_errors))
        return self._next(self.fresh_trades)

    def cached_account_view(self, environment):
        self.calls.append(("cached_account", environment))
        return self.cached_account

    def cached_demo_trades(self, since):
        self.calls.append(("cached_trades", since))
        return self.cached_trades


class DailySummaryRetryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = {"AI_FLOOR_DEMO_EPOCH": "2026-10-25T15:30:20Z", "AI_FLOOR_BROKER_DEMO_ROUTING": "1",
               "AI_FLOOR_BROKER_STATE_DB_PATH": str(Path(tmp.name) / "state.db"),
               "OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": ACCOUNT}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sleeps = []
        self.clock = [1000.0]
        for name, value in (("_retry_started_at", None), ("_monotonic", lambda: self.clock[0])):
            guard = patch.object(notifications, name, value)
            guard.start()
            self.addCleanup(guard.stop)

    def text(self, readers):
        with patch.multiple(oanda_readonly, account_view=readers.account_view, demo_trades=readers.demo_trades,
                            cached_account_view=readers.cached_account_view,
                            cached_demo_trades=readers.cached_demo_trades):
            return notifications.oanda_demo_summary_text(CLOSE, sent_at=SENT, sleep=self.sleeps.append)

    def test_complete_first_read_needs_no_retry(self):
        readers = Readers(OK_VIEW, TRADES, [FAILED_VIEW], [None])
        with self.assertNoLogs("runtime.notifications", "WARNING"):
            text = self.text(readers)
        self.assertIn("NAV (a las 17:00 ET): 100,200.00 USD", text)
        self.assertEqual(self.sleeps, [])
        self.assertEqual([c[0] for c in readers.calls], ["cached_trades", "cached_account"])

    def test_fail_then_succeed_gives_real_numbers(self):
        readers = Readers(FAILED_VIEW, None, [FAILED_VIEW, OK_VIEW], [{"error": "deadline", "as_of": SENT}, TRADES])
        with self.assertNoLogs("runtime.notifications", "WARNING"):
            text = self.text(readers)
        self.assertEqual(self.sleeps, [20, 40])
        self.assertIn("NAV (a las 17:00 ET): 100,200.00 USD", text)
        self.assertIn("Balance (a las 17:00 ET): 100,123.45 USD", text)
        self.assertIn("Operaciones cerradas en el día: 0 (ganadoras: 0)", text)
        self.assertIn("Día 3 de 30", text)
        self.assertNotIn("sin datos de operaciones", text)
        fresh = [c for c in readers.calls if c[0].startswith("fresh")]
        self.assertEqual(fresh, [("fresh_trades", True), ("fresh_account", "practice")] * 2)  # caches bypassed

    def test_first_retry_success_stops(self):
        readers = Readers(FAILED_VIEW, TRADES, [OK_VIEW], [TRADES])
        self.text(readers)
        self.assertEqual(self.sleeps, [20])

    def test_three_failures_are_sin_datos_with_one_diagnostic_line(self):
        readers = Readers(FAILED_VIEW, None, [FAILED_VIEW], [{"error": "deadline", "as_of": SENT}])
        with self.assertLogs("runtime.notifications", "WARNING") as logs:
            text = self.text(readers)
        self.assertEqual(self.sleeps, [20, 40])
        self.assertEqual(text.splitlines()[1:], ["OANDA demo: sin datos"])
        self.assertEqual([r.getMessage() for r in logs.records],
                         ["component=notifications event=DAILY_SUMMARY_READ_FAILED account=http_503 trades=deadline"])
        dump = "\n".join(logs.output)
        for secret in (TOKEN, ACCOUNT, "***-001", "12345678"):
            self.assertNotIn(secret, dump)
        self.assertNotIn("100,", text)  # never an invented / PAPER figure

    def test_both_failed_alert_still_has_the_demo_day(self):
        readers = Readers(FAILED_VIEW, None, [FAILED_VIEW], [None])
        with patch.multiple(oanda_readonly, account_view=readers.account_view, demo_trades=readers.demo_trades,
                            cached_account_view=readers.cached_account_view,
                            cached_demo_trades=readers.cached_demo_trades), \
                self.assertLogs("runtime.notifications", "WARNING"):
            alert = notifications.oanda_demo_daily_alert(CLOSE, sent_at=SENT, sleep=self.sleeps.append)
        self.assertEqual(alert.demo_day, 3)  # the Oct 27 footer: "Día 3 de 30", not "Día sin datos"

    def test_only_the_account_failed_keeps_the_trades_lines(self):
        readers = Readers(FAILED_VIEW, TRADES, [FAILED_VIEW], [TRADES])
        with self.assertLogs("runtime.notifications", "WARNING") as logs:
            text = self.text(readers)
        self.assertIn("NAV (a las 17:00 ET): sin datos", text)
        self.assertIn("P&L desde el inicio del demo (a las 17:00 ET): sin datos", text)
        self.assertIn("Operaciones cerradas en el día: 0 (ganadoras: 0)", text)
        self.assertIn("Operaciones abiertas (a las 17:00 ET): 0", text)
        self.assertIn("account=http_503 trades=ok", logs.output[0])

    def test_only_the_trades_failed_keeps_the_account_lines(self):
        readers = Readers(OK_VIEW, None, [OK_VIEW], [{"error": "http_502", "as_of": SENT}])
        with self.assertLogs("runtime.notifications", "WARNING") as logs:
            text = self.text(readers)
        self.assertIn("NAV (a las 17:00 ET): 100,200.00 USD", text)
        self.assertIn("OANDA demo: sin datos de operaciones", text)
        self.assertIn("account=ok trades=http_502", logs.output[0])

    def test_stale_account_view_is_retried_then_recovers(self):
        stale = AccountView("OK", "practice", "***-001", "USD", Decimal("99000.00"), Decimal("99100.00"),
                            Decimal("0"), 0, Decimal("0"), datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc),
                            stale=True, error_code="timeout")
        readers = Readers(stale, TRADES, [OK_VIEW], [TRADES])
        with self.assertNoLogs("runtime.notifications", "WARNING"):
            text = self.text(readers)
        self.assertEqual(self.sleeps, [20])
        self.assertIn("NAV (a las 17:00 ET): 100,200.00 USD", text)
        self.assertNotIn("dato antiguo", text)

    def test_stale_account_view_persistent_logs_and_keeps_dato_antiguo(self):
        stale = AccountView("OK", "practice", "***-001", "USD", Decimal("99000.00"), Decimal("99100.00"),
                            Decimal("0"), 0, Decimal("0"), datetime(2026, 10, 27, 18, 0, tzinfo=timezone.utc),
                            stale=True, error_code="timeout")
        readers = Readers(stale, TRADES, [FAILED_VIEW], [TRADES])  # fresh account reads keep failing
        with self.assertLogs("runtime.notifications", "WARNING") as logs:
            text = self.text(readers)
        self.assertEqual(self.sleeps, [20, 40])
        self.assertEqual([r.getMessage() for r in logs.records],
                         ["component=notifications event=DAILY_SUMMARY_READ_FAILED account=http_503 trades=ok"])
        self.assertIn("NAV (a las 2026-10-27 14:00 ET, dato antiguo): 99,100.00 USD", text)
        self.assertIn("Balance (a las 2026-10-27 14:00 ET, dato antiguo): 99,000.00 USD", text)
        self.assertIn("Operaciones cerradas en el día: 0 (ganadoras: 0)", text)

    def test_waiting_retry_sequence_at_most_once_per_cooldown(self):
        failing = Readers(FAILED_VIEW, None, [FAILED_VIEW], [{"error": "deadline", "as_of": SENT}])
        with self.assertLogs("runtime.notifications", "WARNING") as logs:
            for _ in range(3):
                self.text(failing)
        self.assertEqual(self.sleeps, [20, 40])  # later summaries: a single attempt, no wait
        self.assertEqual(len(logs.records), 3)  # every incomplete summary is still diagnosed
        self.clock[0] += notifications.SUMMARY_RETRY_COOLDOWN
        with self.assertLogs("runtime.notifications", "WARNING"):
            self.text(failing)
        self.assertEqual(self.sleeps, [20, 40, 20, 40])

    def test_five_pending_catch_up_days_block_at_most_60_s(self):
        from runtime.demo_runner import DemoRunner
        from storage.database import Store
        failing = Readers(FAILED_VIEW, None, [FAILED_VIEW], [{"error": "deadline", "as_of": SENT}])
        store = Store(Path(os.environ["AI_FLOOR_BROKER_STATE_DB_PATH"]).with_name("runner.db"))
        self.addCleanup(store.close)
        sent = []
        runner = object.__new__(DemoRunner)
        runner.store, runner.clock = store, lambda: SENT
        runner.sink = notifications.SlackNotificationSink(
            webhook_url="https://example.invalid/test", transport=lambda _url, payload, _timeout: sent.append(payload),
            clock=lambda: SENT)
        store.set_state("daily_summary_date", "2026-10-19")  # Mon: Oct 20..23 + 26..27 pending -> the last 5
        with patch.multiple(oanda_readonly, account_view=failing.account_view, demo_trades=failing.demo_trades,
                            cached_account_view=failing.cached_account_view,
                            cached_demo_trades=failing.cached_demo_trades), \
                patch.object(notifications, "_sleep", self.sleeps.append), \
                self.assertLogs("runtime.notifications", "WARNING"):
            self.assertTrue(runner.daily_summary(SENT))
        self.assertEqual(len(sent), 5)
        self.assertLessEqual(sum(self.sleeps), 60)
        self.assertEqual(self.sleeps, [20, 40])


class ReadCodeTests(unittest.TestCase):
    def snapshot(self, account, trades):
        return demo_dashboard.demo_snapshot(SENT, {"AI_FLOOR_BROKER_STATE_DB_PATH": "/nonexistent/state.db"},
                                            trades_reader=lambda _since: trades, account_reader=lambda _env: account)

    def test_codes_in_the_snapshot(self):
        self.assertEqual(self.snapshot(OK_VIEW, TRADES)["read_codes"], {"account": None, "trades": None})
        self.assertEqual(self.snapshot(FAILED_VIEW, None)["read_codes"], {"account": "http_503", "trades": "unavailable"})
        stale = AccountView("OK", "practice", nav=Decimal("1"), stale=True, error_code="timeout")
        self.assertEqual(self.snapshot(stale, {"error": "incomplete"})["read_codes"],
                         {"account": "stale_timeout", "trades": "incomplete"})
        self.assertEqual(self.snapshot(AccountView("SIN_TOKEN", "practice"), TRADES)["read_codes"]["account"],
                         "sin_token")
        odd = self.snapshot(AccountView("ERROR", "practice", error_code="Bad thing: 101-001-x"), TRADES)
        self.assertRegex(odd["read_codes"]["account"], r"^[a-z0-9_]{1,32}$")

    def test_unexpected_exception_only_marks_the_reads_not_done(self):
        def boom(*_):
            raise RuntimeError("boom " + TOKEN)

        def snap(trades_reader, account_reader):
            return demo_dashboard.demo_snapshot(SENT, {"AI_FLOOR_BROKER_STATE_DB_PATH": "/nonexistent/state.db"},
                                                trades_reader=trades_reader, account_reader=account_reader)

        after_trades = snap(lambda _since: TRADES, boom)  # trades read OK, account read raised
        self.assertEqual(after_trades["read_codes"], {"account": "unexpected", "trades": None})
        self.assertIsNone(after_trades["stats"])
        self.assertEqual(snap(lambda _since: None, boom)["read_codes"],
                         {"account": "unexpected", "trades": "unavailable"})
        self.assertEqual(snap(boom, lambda _env: OK_VIEW)["read_codes"],
                         {"account": "unexpected", "trades": "unexpected"})
        self.assertNotIn(TOKEN, repr(after_trades))

    def test_demo_trades_reports_codes_only_when_asked(self):
        env = {"OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": ACCOUNT}
        self.assertIsNone(oanda_readonly.demo_trades({}))
        self.assertEqual(oanda_readonly.demo_trades({}, report_errors=True)["error"], "not_configured")
        for raised, code in ((ReadOnlyError("http_503"), "http_503"), (RuntimeError("boom " + TOKEN), "unexpected")):
            with self.subTest(code=code), patch.object(oanda_readonly.OandaReadOnlyClient, "get_trades",
                                                       side_effect=raised):
                self.assertIsNone(oanda_readonly.demo_trades(env))  # the dashboard contract is unchanged
                result = oanda_readonly.demo_trades(env, report_errors=True)
                self.assertEqual(result["error"], code)
                self.assertNotIn(TOKEN, repr(result))


if __name__ == "__main__":
    unittest.main()
