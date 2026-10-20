"""Router close/breakeven outcomes emit one log line (component=broker_demo event=<EVENT> trade_id=<id> reason=<r>);
never the account id or token. Fake transport only."""
from datetime import timedelta
import unittest

from execution import broker_demo_router as bdr
from execution import trade_duration as td
import test_broker_demo_router as base
from test_broker_demo_router import (A, ACCOUNT, CID, PRICING as PRICING_PATH, TOKEN, RouterBase, close_fill,
                                     open_trade, pricing, sl_tx)

LOGGER = "ai_floor.broker.demo_router"


class ActionLogTests(RouterBase):
    DAY1 = base.ManageTests.DAY1

    def opened(self):
        return base.ManageTests.opened(self)

    def lines(self, logs, event):
        return [r.getMessage() for r in logs.records if f"event={event} " in r.getMessage()]

    def assert_safe(self, logs):
        for record in logs.records:
            self.assertNotIn(ACCOUNT, record.getMessage())
            self.assertNotIn(TOKEN, record.getMessage())
            self.assertNotIn(CID, record.getMessage())

    def test_close_rejected_then_closed(self):
        router = self.opened()
        self.server.add("GET", PRICING_PATH, pricing(bid="1.09100", ask="1.09110"))
        self.server.add("PUT", f"{A}/trades/6397/close", (400, {"errorCode": "MARKET_HALTED"}),
                        (200, {"orderFillTransaction": close_fill()}))
        with self.assertLogs(LOGGER, "INFO") as logs:
            router.manage(self.DAY1)
            router.manage(self.DAY1 + timedelta(minutes=1))
        [rejected] = self.lines(logs, "CLOSE_REJECTED")
        self.assertRegex(rejected, r"^component=broker_demo event=CLOSE_REJECTED trade_id=6397 reason=\S+$")
        self.assertEqual(self.lines(logs, "CLOSED"),
                         [f"component=broker_demo event=CLOSED trade_id=6397 reason={td.CLOSE_DAY1_BELOW_HALF_R}"])
        self.assertEqual([r.levelname for r in logs.records if "event=CLOSE" in r.getMessage()], ["WARNING", "INFO"])
        self.assert_safe(logs)

    def test_breakeven(self):
        router = self.opened()
        self.server.add("GET", PRICING_PATH, pricing(bid="1.09600", ask="1.09610"))
        self.server.add("GET", f"{A}/trades/6397", (200, {"trade": open_trade()}))
        self.server.add("PUT", f"{A}/trades/6397/orders", (200, {"stopLossOrderTransaction": sl_tx("1.09000")}))
        with self.assertLogs(LOGGER, "INFO") as logs:
            router.manage(self.DAY1)
        self.assertEqual(self.lines(logs, "BREAKEVEN"), ["component=broker_demo event=BREAKEVEN trade_id=6397 reason=-"])
        self.assert_safe(logs)

    def test_emergency_close_unknown(self):
        router = self.opened()
        self.server.add("PUT", f"{A}/trades/6397/close", (503, {"errorMessage": "down"}))
        with self.assertLogs(LOGGER, "INFO") as logs:
            router.emergency_close_all(reason="drill")
        [line] = self.lines(logs, "CLOSE_UNKNOWN")
        self.assertRegex(line, r"^component=broker_demo event=CLOSE_UNKNOWN trade_id=6397 reason=\S+$")
        self.assert_safe(logs)


class LogFormatTests(unittest.TestCase):
    def test_every_kind_is_logged_and_tokens_are_sanitised(self):
        for kind in sorted(bdr.ACTION_LOG_KINDS):
            with self.subTest(kind=kind), self.assertLogs(LOGGER, "INFO") as logs:
                bdr._log_action(kind, "6397", "EMERGENCY:a b\nc")
            self.assertEqual(logs.records[0].getMessage(),
                             f"component=broker_demo event={kind} trade_id=6397 reason=EMERGENCY")
        for reason in (*td.CLOSE_REASONS, "SL_TP_OR_EXTERNAL", "DAILY_LOSS", "DRAWDOWN", "FRIDAY_PREFLATTEN",
                       "EMERGENCY_ACTIVE", "EMERGENCY_BUTTON", "BrokerRateLimited"):
            self.assertEqual(bdr._log_reason(reason), reason)
        self.assertEqual(bdr._log_reason(None), "-")
        self.assertEqual(bdr.ACTION_LOG_KINDS, {
            "CLOSED", "CLOSE_UNKNOWN", "CLOSE_FAILED", "CLOSE_REJECTED", "CLOSED_AT_BROKER", "BREAKEVEN",
            "BREAKEVEN_UNKNOWN", "BREAKEVEN_REJECTED", "BREAKEVEN_BLOCKED"})

    def test_uncontrolled_reasons_never_reach_the_log(self):
        for reason, expected in ((f"EMERGENCY:account {ACCOUNT}", "EMERGENCY"), (f"EMERGENCY_{TOKEN}", "EMERGENCY"),
                                 (f"rejected for {ACCOUNT}", "OTHER"), (f"Bearer {TOKEN}", "OTHER"),
                                 (ACCOUNT, "OTHER"), (TOKEN, "OTHER"), ("ValueError", "OTHER")):
            with self.subTest(reason=reason), self.assertLogs(LOGGER, "INFO") as logs:
                bdr._log_action("CLOSE_REJECTED", "6397", reason)
            message = logs.records[0].getMessage()
            self.assertEqual(message, f"component=broker_demo event=CLOSE_REJECTED trade_id=6397 reason={expected}")
            self.assertNotIn(ACCOUNT, message)
            self.assertNotIn(TOKEN, message)


if __name__ == "__main__":
    unittest.main()
