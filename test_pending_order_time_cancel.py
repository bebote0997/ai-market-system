"""Pre-demo: PENDING orders are cancelled at Friday 17:00 ET and at the Day-2 NY close after submission."""
import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from execution import trade_duration as td
from execution.contracts import PaperAccount, PaperOrder
from execution.paper_broker import PaperBroker
from runtime.notification_extras import order_setup_id
from test_demo_runner import patched_scouts
from storage.database import Store
from test_trade_duration import (ACCOUNT, FIVE, FRI_CLOSE, THU_OPEN, THU_SLOT, TUE_OPEN, UTC, WED_CLOSE, Prices,
                                 RuntimeCase)

TUE_CLOSE = datetime(2026, 1, 13, 22, 0, tzinfo=UTC)
THU_CLOSE = datetime(2026, 1, 15, 22, 0, tzinfo=UTC)


def order(as_of, order_id="ord-p", symbol="XAUUSD", status="PENDING"):
    return PaperOrder("1.0", order_id, "run-" + order_id, symbol, "LONG", 10.0, 100.0, 95.0, 115.0, 1.0, 10000.0,
                      0.0, as_of, status=status)


class CancelBoundaryTests(unittest.TestCase):
    def test_midweek_order_is_cancelled_at_its_day2_ny_close(self):
        self.assertEqual(td.order_cancel_boundary(order(TUE_OPEN - timedelta(hours=4))),
                         (WED_CLOSE, td.CANCEL_DAY2_NY_CLOSE))
        # Submitted after Tuesday's close: Day 1 is Wednesday, Day 2 is Thursday.
        self.assertEqual(td.order_cancel_boundary(order(TUE_CLOSE + FIVE)), (THU_CLOSE, td.CANCEL_DAY2_NY_CLOSE))

    def test_friday_close_comes_first_whenever_it_is_day1_or_day2(self):
        self.assertEqual(td.order_cancel_boundary(order(THU_OPEN)), (FRI_CLOSE, td.CANCEL_FRIDAY))  # Day 2 = Fri
        self.assertEqual(td.order_cancel_boundary(order(datetime(2026, 1, 16, 14, tzinfo=UTC))),
                         (FRI_CLOSE, td.CANCEL_FRIDAY))  # Day 1 = Fri
        self.assertEqual(td.order_cancel_boundary(order(THU_CLOSE + FIVE)), (FRI_CLOSE, td.CANCEL_FRIDAY))

    def test_boundary_is_dst_correct(self):
        # DST starts Sunday 2026-03-08: a Friday 2026-03-06 order is cancelled at 22:00 UTC (EST).
        self.assertEqual(td.order_cancel_boundary(order(datetime(2026, 3, 6, 15, tzinfo=UTC)))[0],
                         datetime(2026, 3, 6, 22, tzinfo=UTC))
        # July (EDT): Wednesday's order, Day-2 close Thursday 21:00 UTC.
        self.assertEqual(td.order_cancel_boundary(order(datetime(2026, 7, 15, 14, tzinfo=UTC))),
                         (datetime(2026, 7, 16, 21, tzinfo=UTC), td.CANCEL_DAY2_NY_CLOSE))


class RuntimeCancelTests(RuntimeCase):
    def seed_orders(self, *orders):
        store = Store(self.db)
        try:
            broker = PaperBroker(PaperAccount("1.0", ACCOUNT, 10000.0, 10000.0, 10000.0))
            broker.orders.update({o.order_id: o for o in orders})
            store.save_paper(broker)
        finally:
            store.close()

    def orders(self):
        store = Store(self.db)
        try:
            _, orders, _ = store.load_paper(ACCOUNT)
            journal = [(r["event_type"], r["timestamp"], json.loads(r["payload"] or "{}"))
                       for r in store.db.execute("SELECT * FROM journal WHERE event_type='ORDER_CANCELLED' ORDER BY id")]
            return orders, journal
        finally:
            store.close()

    def test_friday_close_cancels_pending_orders_out_of_session(self):
        self.seed_orders(order(THU_OPEN), order(THU_OPEN, "ord-e", "EURUSD"), order(THU_OPEN, "ord-f", status="FILLED"))
        runtime = self.runtime(None, FRI_CLOSE)  # no market data needed to cancel
        self.assertEqual(runtime.manage_trade_duration(FRI_CLOSE),
                         {"XAUUSD": "ORDERS_CANCELLED", "EURUSD": "ORDERS_CANCELLED"})
        orders, journal = self.orders()
        self.assertEqual({k: o.status for k, o in orders.items()},
                         {"ord-p": "CANCELLED", "ord-e": "CANCELLED", "ord-f": "FILLED"})
        self.assertEqual(sorted(p["reason"] for _, _, p in journal), [td.CANCEL_FRIDAY] * 2)
        self.assertEqual({(stamp, p["boundary_utc"]) for _, stamp, p in journal},
                         {(FRI_CLOSE.isoformat(), FRI_CLOSE.isoformat())})
        self.assertEqual(runtime.manage_trade_duration(FRI_CLOSE + timedelta(minutes=15)), {})  # idempotent
        self.assertEqual(len(self.orders()[1]), 2)

    def test_pending_order_survives_until_its_boundary(self):
        self.seed_orders(order(THU_OPEN))
        before = FRI_CLOSE - timedelta(minutes=15)
        self.assertEqual(self.runtime(None, before).manage_trade_duration(before), {})
        self.assertEqual(self.orders()[0]["ord-p"].status, "PENDING")

    def test_cycle_cancels_a_day2_expired_order_before_any_fill(self):
        """A missed 17:00 pass: the next cycle cancels the order before evaluating it on its own bar."""
        self.seed_orders(order(TUE_OPEN - FIVE))
        runtime = self.runtime(Prices(lambda at: 100.0, TUE_OPEN - 4 * FIVE), THU_SLOT)
        self.assertNotEqual(runtime.run_cycle("XAUUSD", THU_SLOT), "ERROR")
        orders, journal = self.orders()
        self.assertEqual(orders["ord-p"].status, "CANCELLED")
        self.assertEqual(self.paper()[0].open_positions, {})
        self.assertEqual([(stamp, p["reason"]) for _, stamp, p in journal],
                         [(WED_CLOSE.isoformat(), td.CANCEL_DAY2_NY_CLOSE)])

    def test_day1_order_is_left_pending_by_the_cycle(self):
        self.seed_orders(order(datetime(2026, 1, 14, 14, tzinfo=UTC)))  # Wednesday: Day-2 close is Thursday 22:00
        runtime = self.runtime(Prices(lambda at: 100.0, THU_SLOT - timedelta(hours=6)), THU_SLOT)
        self.assertNotEqual(runtime.run_cycle("XAUUSD", THU_SLOT), "ERROR")
        orders, journal = self.orders()
        self.assertNotEqual(orders["ord-p"].status, "CANCELLED")
        self.assertEqual(journal, [])

    def test_two_stale_cancellation_writes_remain_blocked_after_successful_catch_up(self):
        self.seed_orders(order(TUE_OPEN - FIVE))
        runtime = self.runtime(Prices(lambda at: 100.0, TUE_OPEN - 4 * FIVE), THU_SLOT)
        save_paper = runtime.store.save_paper
        stale_writes = []
        catch_up_results = []
        catch_up = runtime._catch_up_positions

        def save(broker, **kwargs):
            if any(e.event_type == "ORDER_CANCELLED" for e in broker.journal):
                stale_writes.append(True)
                return False
            return save_paper(broker, **kwargs)

        def catch(*args, **kwargs):
            self.assertEqual(len(stale_writes), 2)
            result = catch_up(*args, **kwargs)
            catch_up_results.append(result)
            return result

        with patch.object(runtime.store, "save_paper", side_effect=save), \
                patch.object(runtime, "_catch_up_positions", side_effect=catch), patched_scouts("LONG"):
            self.assertNotEqual(runtime.run_cycle("XAUUSD", THU_SLOT), "ERROR")
        self.assertEqual(len(stale_writes), 2)
        self.assertEqual(catch_up_results, [None])
        orders, cancelled = self.orders()
        self.assertEqual(orders["ord-p"].status, "PENDING")
        self.assertEqual(cancelled, [])
        account, journal = self.paper()
        self.assertEqual(account.open_positions, {})
        self.assertEqual(account.closed_trades, [])
        self.assertIn("STALE_PAPER_STATE", [event for event, _ in journal])
        self.assertNotIn("POSITION_OPENED", [event for event, _ in journal])


class OrderSetupIdTests(unittest.TestCase):
    class Reviews:
        def __init__(self, reviews):
            self.reviews = reviews

        def review_report(self, run_id):
            return self.reviews.get(run_id)

    def test_setup_id_comes_from_the_submitting_run(self):
        store = self.Reviews({"run-ord-p": {"execution": {"setup_id": "S-1", "order_id": "ord-p"},
                                            "setup": {"setup_id": "S-1"}},
                              "run-later": {"execution": {"setup_id": "S-OTHER"}}})
        self.assertEqual(order_setup_id(store, order(THU_OPEN)), "S-1")

    def test_explanation_is_the_fallback_and_another_orders_outcome_is_ignored(self):
        store = self.Reviews({"run-ord-p": {"execution": {"setup_id": "S-X", "order_id": "ord-other"},
                                            "setup": {"setup_id": "S-1"}}})
        self.assertEqual(order_setup_id(store, order(THU_OPEN)), "S-1")
        self.assertIsNone(order_setup_id(self.Reviews({}), order(THU_OPEN)))  # unrecorded stays NO_DATA


def setUpModule():  # DEC-XAU-OFF-1: these tests drive XAUUSD cycles; the pause itself is in test_pause_xauusd
    import unittest.mock
    unittest.enterModuleContext(unittest.mock.patch("runtime.symbol_pause.PAUSED_SYMBOLS", frozenset()))


if __name__ == "__main__":
    unittest.main()
