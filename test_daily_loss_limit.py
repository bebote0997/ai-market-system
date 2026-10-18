"""Daily loss limit (2 % of E0) + drawdown halt (5 %): pure functions, the persistent tracker (day roll at 17:00
America/New_York, restart, transfers, manual clears) and the broker-demo router integration (flatten, dry-run, kill
switch independence, flag OFF = identical router). Offline: fake transport only (helpers from test_broker_demo_router).
"""
from contextlib import closing
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from execution import daily_loss_limit as dl
from execution.broker_demo_state import DemoStateStore, read_router_status
from execution.daily_loss_limit import DailyLossLimit, daily_loss_status, day_key, drawdown_status, limit_from_env
from test_broker_demo_router import CID, NOW, OPEN_TRADES, SUMMARY, RouterBase, context, summary, trades
from test_broker_oanda import close_fill
from test_broker_oanda_client import A, ACCOUNT, TOKEN

D = Decimal
UTC = timezone.utc
CLOSE = f"{A}/trades/6397/close"
LIMIT_ON = {"AI_FLOOR_DAILY_LOSS_LIMIT": "1"}


class PureDailyLossTests(unittest.TestCase):
    def test_exact_boundary_breaches(self):
        breached = daily_loss_status(D("1000"), D("980"), D("0"))  # -2.00 %
        self.assertEqual((breached.pnl, breached.limit_amount, breached.floor), (D("-20"), D("20.00"), D("980.00")))
        self.assertTrue(breached.breached)
        self.assertFalse(breached.entry_allowed)
        self.assertEqual(breached.reason, "daily_loss_halt")
        almost = daily_loss_status(D("1000"), D("980.10"), D("0"))  # -1.99 %
        self.assertFalse(almost.breached)
        self.assertTrue(almost.entry_allowed)
        self.assertEqual(almost.remaining_budget, D("0.10"))

    def test_gains_do_not_raise_the_floor(self):
        up = daily_loss_status(D("1000"), D("1100"), D("0"))
        self.assertEqual(up.floor, D("980.00"))
        # Gave back 21 from the +100 peak: still +79 on the day -> not breached.
        self.assertFalse(daily_loss_status(D("1000"), D("1079"), D("0")).breached)

    def test_budget_check(self):
        status = daily_loss_status(D("1000"), D("990"), D("0"), planned_risk=D("10"))
        self.assertEqual(status.remaining_budget, D("10.00"))
        self.assertTrue(status.entry_allowed)  # planned risk == remaining budget is allowed
        over = daily_loss_status(D("1000"), D("990"), D("0"), planned_risk=D("10.01"))
        self.assertEqual((over.entry_allowed, over.breached, over.reason), (False, False, "daily_loss_budget"))
        for risk in (D("0"), D("-1")):
            self.assertEqual(daily_loss_status(D("1000"), D("990"), D("0"), planned_risk=risk).reason,
                             "daily_loss_budget")

    def test_transfers_excluded(self):
        # A $500 deposit must not mask a $20 trading loss...
        self.assertTrue(daily_loss_status(D("1000"), D("1480"), D("500")).breached)
        self.assertTrue(daily_loss_status(D("1000"), D("1480"), D("0")).entry_allowed)  # (would mask it if ignored)
        # ...and a $300 withdrawal must not cause a false halt.
        withdrawal = daily_loss_status(D("1000"), D("700"), D("-300"))
        self.assertEqual((withdrawal.pnl, withdrawal.breached, withdrawal.entry_allowed), (D("0"), False, True))

    def test_invalid_inputs_fail_closed(self):
        bad = (None, D("NaN"), D("Infinity"), 0, -1, True, "abc", object())
        for value in bad:
            for args in ((value, D("1000"), D("0")), (D("1000"), value, D("0"))):
                with self.subTest(args=args):
                    status = daily_loss_status(*args)
                    self.assertEqual((status.breached, status.entry_allowed, status.reason),
                                     (False, False, "invalid_input"))
        for value in (None, D("NaN"), True, "x"):
            with self.subTest(transfers=value):
                self.assertEqual(daily_loss_status(D("1000"), D("1000"), value).reason, "invalid_input")
        self.assertEqual(daily_loss_status(D("1000"), D("1000"), D("0"), planned_risk="x").reason, "invalid_input")

    def test_drawdown_status(self):
        self.assertTrue(drawdown_status(D("1000"), D("950")).breached)
        hold = drawdown_status(D("1000"), D("950.01"))
        self.assertEqual((hold.valid, hold.breached, hold.floor), (True, False, D("950.00")))
        for start in (None, 0, D("NaN"), True):
            self.assertEqual(drawdown_status(start, D("900")), dl.DrawdownStatus(False, False, None, None))


class DayKeyTests(unittest.TestCase):
    def test_17h_new_york_boundary(self):
        self.assertEqual(day_key(datetime(2026, 10, 14, 20, 59, tzinfo=UTC)), "2026-10-13T17:00:00-04:00")  # 16:59 NY
        self.assertEqual(day_key(datetime(2026, 10, 14, 21, 0, tzinfo=UTC)), "2026-10-14T17:00:00-04:00")  # 17:00 NY
        other = timezone(timedelta(hours=2))  # any aware timezone works
        self.assertEqual(day_key(datetime(2026, 10, 14, 23, 0, tzinfo=other)), "2026-10-14T17:00:00-04:00")

    def test_dst_transitions(self):
        # Spring forward 2026-03-08: 17:00 NY = 22:00 UTC on Mar 7 (EST), 21:00 UTC on Mar 8 (EDT).
        self.assertEqual(day_key(datetime(2026, 3, 8, 20, 59, tzinfo=UTC)), "2026-03-07T17:00:00-05:00")
        self.assertEqual(day_key(datetime(2026, 3, 8, 21, 0, tzinfo=UTC)), "2026-03-08T17:00:00-04:00")
        # Fall back 2026-11-01: 17:00 NY = 21:00 UTC on Oct 31 (EDT), 22:00 UTC on Nov 1 (EST).
        self.assertEqual(day_key(datetime(2026, 11, 1, 21, 30, tzinfo=UTC)), "2026-10-31T17:00:00-04:00")
        self.assertEqual(day_key(datetime(2026, 11, 1, 22, 0, tzinfo=UTC)), "2026-11-01T17:00:00-05:00")

    def test_weekend_boundaries_exist(self):
        self.assertEqual(day_key(datetime(2026, 10, 17, 22, 0, tzinfo=UTC)), "2026-10-17T17:00:00-04:00")  # Sat 18:00
        self.assertEqual(day_key(datetime(2026, 10, 18, 14, 0, tzinfo=UTC)), "2026-10-17T17:00:00-04:00")  # Sun 10:00
        self.assertEqual(day_key(datetime(2026, 10, 18, 21, 0, tzinfo=UTC)), "2026-10-18T17:00:00-04:00")  # Sun 17:00

    def test_naive_datetime_is_rejected(self):
        for value in (datetime(2026, 10, 14, 21, 0), "2026-10-14T21:00:00+00:00", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                day_key(value)


class TrackerBase(unittest.TestCase):
    T0 = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)  # Wednesday 10:00 NY

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "broker_state.db"
        self.now = self.T0
        self.store = DemoStateStore(self.db, clock=lambda: self.now.isoformat())
        self.addCleanup(dl._HALT_LATCHES.clear)

    def limit(self, **kwargs):
        return DailyLossLimit(self.store, clock=lambda: self.now, **kwargs)

    def events(self, kind=None):
        with closing(sqlite3.connect(self.db)) as con:
            rows = con.execute("SELECT kind, detail FROM broker_demo_events ORDER BY id").fetchall()
        return [(k, json.loads(d) if d else None) for k, d in rows if kind in (None, k)]

    def flags(self):
        if not self.db.exists():
            return {}
        with closing(sqlite3.connect(self.db)) as con:
            return dict(con.execute("SELECT key, value FROM broker_demo_flags").fetchall())


class TrackerTests(TrackerBase):
    def test_halt_persists_until_17h_then_auto_resumes_with_new_e0(self):
        limit = self.limit()
        first = limit.evaluate(D("1000"))
        self.assertEqual((first.entry_allowed, first.reason, first.halted), (True, None, False))
        self.assertEqual(self.flags()["dl_e0"], "1000")
        self.now += timedelta(hours=1)
        halt = limit.evaluate(D("980"))
        self.assertEqual((halt.entry_allowed, halt.flatten_required, halt.reason, halt.newly_halted),
                         (False, True, "daily_loss_halt", True))
        [(_k, detail)] = self.events("DAILY_LOSS_HALT")
        self.assertEqual(detail, {"day": "2026-10-13T17:00:00-04:00", "pnl": "-20", "floor": "980.00", "nav": "980",
                                  "e0": "1000"})
        recovered = limit.evaluate(D("1010"))  # NAV recovered: still halted for the day
        self.assertEqual((recovered.entry_allowed, recovered.halted, recovered.newly_halted), (False, True, False))
        self.assertEqual(len(self.events("DAILY_LOSS_HALT")), 1)
        self.now = datetime(2026, 10, 14, 20, 59, tzinfo=UTC)  # 16:59 NY
        self.assertFalse(limit.evaluate(D("1010")).entry_allowed)
        self.now = datetime(2026, 10, 14, 21, 0, tzinfo=UTC)  # 17:00 NY: new day, E0 = last observed NAV
        resumed = limit.evaluate(D("1010"))
        self.assertEqual((resumed.entry_allowed, resumed.halted, resumed.daily.pnl), (True, False, D("0")))
        self.assertEqual(self.flags()["dl_e0"], "1010")
        [(_k, detail)] = self.events("DAILY_LOSS_RESUMED")
        self.assertEqual((detail["day"], detail["new_day"]), ("2026-10-13T17:00:00-04:00", "2026-10-14T17:00:00-04:00"))

    def test_restart_mid_day_keeps_e0_and_halt(self):
        self.limit().evaluate(D("1000"))
        self.now += timedelta(hours=2)
        restarted = self.limit()  # new instance, same store
        self.assertEqual(restarted.evaluate(D("990")).daily.pnl, D("-10"))
        self.assertEqual(self.flags()["dl_e0"], "1000")
        restarted.evaluate(D("979"))
        again = self.limit().evaluate(D("1005"))
        self.assertEqual((again.halted, again.entry_allowed, again.reason), (True, False, "daily_loss_halt"))

    def test_flatten_off_only_halts(self):
        limit = self.limit(flatten=False)
        limit.evaluate(D("1000"))
        halt = limit.evaluate(D("980"))
        self.assertEqual((halt.halted, halt.flatten_required, halt.entry_allowed), (True, False, False))

    def test_drawdown_is_stricter_flattens_always_and_never_auto_resumes(self):
        limit = self.limit(flatten=False)
        navs = (("1000", "981"), ("981", "962"), ("962", "950"))  # -1.9 %, -1.94 %, -1.25 % a day
        for day, (start, end) in enumerate(navs):
            self.now = self.T0 + timedelta(days=day)
            self.assertTrue(limit.evaluate(D(start)).entry_allowed)
            decision = limit.evaluate(D(end))
        self.assertEqual((decision.dd_halted, decision.halted, decision.reason, decision.flatten_required),
                         (True, False, "drawdown_halt", True))
        self.assertFalse(decision.daily.breached)
        [(_k, detail)] = self.events("DRAWDOWN_HALT")
        self.assertEqual((detail["start"], detail["floor"], detail["nav"]), ("1000", "950.00", "950"))
        self.now += timedelta(days=1)  # next day: the daily part resumes, the drawdown halt does not
        later = self.limit(flatten=False).evaluate(D("1000"))
        self.assertEqual((later.dd_halted, later.entry_allowed, later.reason), (True, False, "drawdown_halt"))
        with self.assertRaises(ValueError):
            limit.clear_drawdown_halt("", "x")
        limit.clear_drawdown_halt("owner", "written review A4")
        [(_k, detail)] = self.events("DRAWDOWN_HALT_CLEARED")
        self.assertEqual(detail, {"actor": "owner", "reason": "written review A4"})
        cleared = limit.evaluate(D("1000"))
        self.assertEqual((cleared.dd_halted, cleared.entry_allowed), (False, True))
        self.assertEqual(self.flags()["dd_start"], "1000")  # re-baselined

    def test_both_breached_reports_drawdown(self):
        limit = self.limit(start_equity_override=D("1000"))
        limit.evaluate(D("960"))
        both = limit.evaluate(D("940"))  # -2.08 % daily and -6 % drawdown
        self.assertEqual((both.halted, both.dd_halted, both.reason), (True, True, "drawdown_halt"))
        self.assertNotIn("dd_start", self.flags())  # the override is never persisted

    def test_manual_clear_is_audited_and_retriggers_if_still_breached(self):
        limit = self.limit()
        limit.evaluate(D("1000"))
        limit.evaluate(D("980"))
        with self.assertRaises(ValueError):
            limit.clear_daily_halt(None, "x")
        limit.clear_daily_halt("owner", "approved early resume")
        [(_k, detail)] = self.events("DAILY_LOSS_HALT_CLEARED")
        self.assertEqual((detail["actor"], detail["reason"]), ("owner", "approved early resume"))
        again = limit.evaluate(D("980"))
        self.assertEqual((again.halted, again.newly_halted), (True, True))
        limit.clear_daily_halt("owner", "recovered")
        self.assertTrue(limit.evaluate(D("995")).entry_allowed)

    def test_transfers_recorded_for_the_day(self):
        limit = self.limit()
        self.assertIsNone(limit.record_transfer(D("500"), "owner", "ACH"))  # no state yet: E0 will include it
        self.assertEqual(self.events("TRANSFER_RECORDED")[0][1]["applied_to"], "none")
        limit.evaluate(D("1000"))
        for amount, actor in ((0, "owner"), ("x", "owner"), (D("NaN"), "owner"), (D("5"), "")):
            with self.subTest(amount=amount, actor=actor), self.assertRaises(ValueError):
                limit.record_transfer(amount, actor, "ACH")
        self.assertEqual(limit.record_transfer(D("500"), "owner", "ACH deposit"), D("500"))
        self.assertTrue(limit.evaluate(D("1480")).halted)  # deposit does not mask the -20 loss
        [_first, (_k, detail)] = self.events("TRANSFER_RECORDED")
        self.assertEqual((detail["amount"], detail["total"], detail["actor"]), ("500", "500", "owner"))
        self.now += timedelta(days=1)
        limit.evaluate(D("1480"))
        limit.record_transfer(D("-300"), "owner", "ACH withdrawal")
        self.assertTrue(limit.evaluate(D("1180")).entry_allowed)  # the withdrawal is no loss

    def test_invalid_nav_fails_closed_without_state_change(self):
        limit = self.limit()
        for nav in (None, D("NaN"), D("Infinity"), True, "abc"):
            with self.subTest(nav=nav):
                decision = limit.evaluate(nav)
                self.assertEqual((decision.entry_allowed, decision.flatten_required, decision.reason),
                                 (False, False, "nav_invalid"))
        self.assertEqual(self.flags(), {})

    def test_unreadable_or_corrupt_state_fails_closed(self):
        limit = self.limit()
        limit.evaluate(D("1000"))
        self.store.set_flag("dl_e0", "garbage")
        decision = limit.evaluate(D("1000"))
        self.assertEqual((decision.entry_allowed, decision.flatten_required, decision.reason),
                         (False, False, "state_unreadable"))
        broken = MagicMock()
        broken.flag.side_effect = sqlite3.OperationalError("locked")
        self.assertEqual(DailyLossLimit(broken, clock=lambda: self.now).evaluate(D("1000")).reason,
                         "state_unreadable")
        broken.set_flag.assert_not_called()
        self.assertFalse(DailyLossLimit(broken).status()["readable"])

    def test_status_view(self):
        limit = self.limit(fraction=D("0.01"))
        limit.evaluate(D("1000"))
        limit.evaluate(D("990"))
        status = limit.status()
        self.assertEqual((status["fraction"], status["e0"], status["floor"], status["daily_halted"],
                          status["drawdown_halted"], status["drawdown_floor"]),
                         ("0.01", "1000", "990.00", True, False, "950.00"))
        json.dumps(status)

    def test_fraction_can_only_be_stricter(self):
        self.assertEqual(self.limit(fraction=D("0.05")).fraction, D("0.02"))
        self.assertEqual(self.limit(fraction=D("0.01")).fraction, D("0.01"))
        self.assertEqual(self.limit(fraction=0).fraction, D("0.02"))

    def test_transfer_before_first_evaluation_of_the_day_adjusts_next_e0(self):
        limit = self.limit()
        limit.evaluate(D("1000"))
        self.now += timedelta(days=1)  # new day, not evaluated yet: the deposit goes into the pending baseline
        self.assertIsNone(limit.record_transfer(D("500"), "broker", "TRANSFER_FUNDS"))
        self.assertEqual(self.flags()["dl_last_nav"], "1500")
        self.assertEqual(self.events("TRANSFER_RECORDED")[-1][1]["applied_to"], "next_e0")
        halt = limit.evaluate(D("1470"))  # E0 = 1500: the deposit does not hide the -30 (-2 %) loss
        self.assertEqual((halt.halted, halt.daily.pnl, self.flags()["dl_e0"]), (True, D("-30"), "1500"))
        self.now += timedelta(days=1)
        limit.record_transfer(D("-600"), "broker", "TRANSFER_FUNDS")
        later = limit.evaluate(D("870"))  # E0 = 870, drawdown base 1000 + 500 - 600 = 900 -> floor 855
        self.assertEqual((later.entry_allowed, later.dd_halted, later.daily.pnl), (True, False, D("0")))
        self.assertEqual(self.flags()["dd_transfers"], "-100")

    def test_withdrawal_does_not_trigger_drawdown(self):
        limit = self.limit()
        limit.evaluate(D("1000"))
        limit.record_transfer(D("-300"), "broker", "TRANSFER_FUNDS")
        decision = limit.evaluate(D("700"))  # -30 % NAV, all of it withdrawn: no loss, no drawdown
        self.assertEqual((decision.entry_allowed, decision.halted, decision.dd_halted), (True, False, False))
        self.assertTrue(limit.evaluate(D("665")).dd_halted)  # 700 * 0.95: a real 5 % loss still halts
        override = DailyLossLimit(DemoStateStore(Path(self.tmp.name) / "o.db"), clock=lambda: self.now,
                                  start_equity_override=D("1000"))
        override.evaluate(D("1000"))
        override.record_transfer(D("-300"), "broker", "TRANSFER_FUNDS")
        self.assertFalse(override.evaluate(D("700")).dd_halted)

    def test_halt_write_failure_keeps_halt_and_flatten(self):
        original = self.store.set_flag
        failing = {"on": True}

        def set_flag(key, value):
            if failing["on"] and key in ("dl_halt", "dd_halt") and value is not None:
                raise sqlite3.OperationalError("disk I/O error")
            return original(key, value)

        limit = self.limit(flatten=True)
        with patch.object(self.store, "set_flag", side_effect=set_flag):
            limit.evaluate(D("1000"))
            self.assertTrue(limit.evaluate(D("980")).halted)
            self.assertIsNone(self.flags()["dl_halt"])
            recovered = limit.evaluate(D("1010"))
            self.assertEqual((recovered.entry_allowed, recovered.flatten_required, recovered.reason),
                             (False, True, "daily_loss_halt"))
            other = self.limit()  # another tracker on the same state path sees the latch
            self.assertEqual(other.evaluate(D("1010")).reason, "daily_loss_halt")
            self.assertEqual(other.halt_state(), (True, False))
            self.assertTrue(other.status()["daily_halted"])
            failing["on"] = False
            limit.evaluate(D("1010"))  # the write is retried once possible
        self.assertEqual(self.flags()["dl_halt"], "2026-10-13T17:00:00-04:00")
        limit.clear_daily_halt("owner", "test")
        self.assertTrue(limit.evaluate(D("1010")).entry_allowed)

    def test_drawdown_write_failure_needs_explicit_clear(self):
        original = self.store.set_flag

        def set_flag(key, value):
            if key == "dd_halt" and value is not None:
                raise sqlite3.OperationalError("disk I/O error")
            return original(key, value)

        limit = self.limit(flatten=False, start_equity_override=D("1000"))
        with patch.object(self.store, "set_flag", side_effect=set_flag):
            self.assertTrue(limit.evaluate(D("940")).dd_halted)
            self.now += timedelta(days=2)
            recovered = self.limit(flatten=False, start_equity_override=D("1000")).evaluate(D("1100"))
            self.assertEqual((recovered.entry_allowed, recovered.flatten_required, recovered.reason),
                             (False, True, "drawdown_halt"))
            self.assertNotIn("dd_halt", self.flags())
            limit.clear_drawdown_halt("owner", "written review")
            self.assertTrue(limit.evaluate(D("1100")).entry_allowed)

    def test_restart_across_17h_keeps_the_loss(self):
        self.limit().evaluate(D("1000"))
        self.now = datetime(2026, 10, 14, 20, 0, tzinfo=UTC)  # 16:00 NY, last observation before downtime
        self.limit().evaluate(D("1000"))
        self.now = datetime(2026, 10, 15, 13, 0, tzinfo=UTC)  # restarted next morning; lost 25 while down
        decision = self.limit().evaluate(D("975"))
        self.assertEqual((decision.halted, decision.daily.pnl, self.flags()["dl_e0"]), (True, D("-25"), "1000"))

    def test_first_and_stale_baselines_use_current_nav(self):
        first = self.limit().evaluate(D("1000"))
        self.assertEqual((first.daily.pnl, self.flags()["dl_e0"]), (D("0"), "1000"))
        self.assertEqual(self.flags()["dl_last_at"], self.T0.isoformat())
        self.now += timedelta(days=8)  # last NAV older than 7 days: ignored
        stale = self.limit().evaluate(D("960"))
        self.assertEqual((stale.entry_allowed, stale.daily.pnl, self.flags()["dl_e0"]), (True, D("0"), "960"))

    def test_non_positive_nav_is_a_drawdown_breach(self):
        for n, nav in enumerate((0, D("-5"))):
            with self.subTest(nav=nav):
                store = DemoStateStore(Path(self.tmp.name) / f"np-{n}.db")
                limit = DailyLossLimit(store, flatten=False, clock=lambda: self.now)
                decision = limit.evaluate(nav)
                self.assertEqual((decision.entry_allowed, decision.flatten_required, decision.dd_halted,
                                  decision.reason, decision.newly_halted), (False, True, True, "drawdown_halt", True))
                self.assertIsNotNone(store.flag("dd_halt"))
                self.assertIsNone(store.flag("dl_e0"))
                self.assertFalse(limit.evaluate(nav).newly_halted)


class EnvTests(unittest.TestCase):
    def test_limit_from_env_is_none_unless_exact_1(self):
        store = MagicMock()
        for value in (None, "0", "", "true", "yes", " 1", "1 "):
            env = {} if value is None else {"AI_FLOOR_DAILY_LOSS_LIMIT": value}
            with self.subTest(value=value):
                self.assertIsNone(limit_from_env(env, store))
        self.assertEqual(store.mock_calls, [])
        self.assertIsInstance(limit_from_env(LIMIT_ON, store), DailyLossLimit)
        self.assertEqual(store.mock_calls, [])  # construction reads/writes nothing

    def test_pct_only_stricter(self):
        cases = {None: "0.02", "0.01": "0.01", "0.02": "0.02", "0.005": "0.005", "0.05": "0.02", "2": "0.02",
                 "0": "0.02", "-0.01": "0.02", "abc": "0.02", "nan": "0.02", "inf": "0.02", "": "0.02"}
        for raw, expected in cases.items():
            env = {**LIMIT_ON, **({} if raw is None else {"AI_FLOOR_DAILY_LOSS_PCT": raw})}
            with self.subTest(raw=raw):
                self.assertEqual(limit_from_env(env, MagicMock()).fraction, D(expected))

    def test_flatten_off_only_with_exact_0(self):
        for raw, expected in ((None, True), ("0", False), ("false", True), ("no", True), ("", True), (" 0", True),
                              ("1", True)):
            env = {**LIMIT_ON, **({} if raw is None else {"AI_FLOOR_DAILY_LOSS_FLATTEN": raw})}
            with self.subTest(raw=raw):
                self.assertIs(limit_from_env(env, MagicMock()).flatten, expected)

    def test_start_equity_override(self):
        for raw, expected in (("1000", D("1000")), ("-5", None), ("0", None), ("x", None), (None, None)):
            env = {**LIMIT_ON, **({} if raw is None else {"AI_FLOOR_DAILY_LOSS_START_EQUITY": raw})}
            with self.subTest(raw=raw):
                self.assertEqual(limit_from_env(env, MagicMock()).start_equity_override, expected)


def summary_tx(nav, tx):
    return (200, {"account": {"balance": nav, "NAV": nav, "marginUsed": "0", "currency": "USD",
                              "lastTransactionID": tx}, "lastTransactionID": tx})


def transfers(*items, last="200"):
    return (200, {"transactions": [{"id": i, "type": "TRANSFER_FUNDS", "amount": a} for i, a in items],
                  "lastTransactionID": last})


def since(tx_id):
    return f"{A}/transactions/sinceid?id={tx_id}&type=TRANSFER_FUNDS"


class RouterLossLimitTests(RouterBase):
    def setUp(self):
        super().setUp()
        self.addCleanup(dl._HALT_LATCHES.clear)

    def flags(self):
        with closing(sqlite3.connect(self.db)) as con:
            return dict(con.execute("SELECT key, value FROM broker_demo_flags").fetchall())

    def opened(self, **extra):
        """Live router with the limit ON and one routed OPEN trade (E0 = 10000)."""
        router = self.router(**{**LIMIT_ON, **extra})
        self.ready()
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("ACCEPTED", "6397"))
        return router

    def nav(self, value):
        self.server.routes[("GET", SUMMARY)] = [summary(value)]

    def puts(self):
        return [p for m, p in self.writes() if m == "PUT"]

    def test_flag_off_router_is_identical(self):
        for n, value in enumerate((None, "0", "true")):
            with self.subTest(value=value):
                self.db = Path(self.tmp.name) / f"off-{n}.db"  # fresh state + fake server per value
                self.env = {**self.env, "AI_FLOOR_BROKER_STATE_DB_PATH": str(self.db)}
                self.server.calls.clear()
                self.server.routes.clear()
                extra = {} if value is None else {"AI_FLOOR_DAILY_LOSS_LIMIT": value}
                with patch.object(DailyLossLimit, "evaluate") as evaluate:
                    router = self.router(**extra)
                    self.assertIsNone(router._loss_limit)
                    self.ready()
                    self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("ACCEPTED", "6397"))
                    router.manage(NOW)
                evaluate.assert_not_called()
                self.assertEqual(self.server.count("GET", SUMMARY), 1)  # only the sizing read
                self.assertFalse([k for k in self.flags() if k.startswith(("dl_", "dd_"))])
                self.assertNotIn("loss_limit", router.status())
                self.assertFalse(self.events("FLATTEN_DAILY_LOSS"))

    def test_flatten_on_closes_open_trades_and_refuses_entries(self):
        router = self.opened()
        self.assertEqual(self.flags()["dl_e0"], "10000")
        self.nav("9800")  # -2 % including open P&L (NAV)
        self.server.add("PUT", CLOSE, (200, {"orderFillTransaction": close_fill()}))
        with patch.object(router._broker, "close_market", wraps=router._broker.close_market) as close:
            self.server.routes[("GET", OPEN_TRADES)] = [trades()]
            self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "CLOSED"})
        close.assert_called_once()
        self.assertEqual(close.call_args.kwargs["reason"], "DAILY_LOSS")
        self.assertTrue(close.call_args.kwargs["client_id"].endswith("-dlimit"))
        self.assertEqual(self.writes(), [("POST", f"{A}/orders"), ("PUT", CLOSE)])  # no other order endpoint
        row = self.rows()[0]
        self.assertEqual((row["status"], row["closed_reason"]), ("CLOSED", "DAILY_LOSS"))
        [(_k, _c, detail)] = self.events("FLATTEN_DAILY_LOSS")
        self.assertEqual(detail["results"], {CID: "CLOSED"})
        self.assertEqual(len(self.events("DAILY_LOSS_HALT")), 1)
        # Independent from the kill switch: no blocked_reason, registry untouched.
        self.assertIsNone(router.blocked_reason)
        self.assertIsNone(self.flags().get("blocked_reason"))
        registry = self.registry()
        self.assertFalse(registry.emergency_active())
        self.assertTrue(registry.is_allowed("broker_demo"))
        self.assertEqual(len(registry.audit_log()), 1)
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW + timedelta(minutes=2)),
                         ("NOT_ROUTED", "daily_loss_halt"))
        self.assertEqual(len([w for w in self.writes() if w[0] == "POST"]), 1)
        status = router.status()
        self.assertTrue(status["loss_limit"]["daily_halted"])
        self.assertNotIn("loss_limit", read_router_status(self.db, now=NOW))  # dashboard view unchanged
        dump = json.dumps(self.events()) + json.dumps(status)
        self.assertNotIn(TOKEN, dump)
        self.assertNotIn(ACCOUNT, dump)

    def test_open_pnl_counts_via_nav(self):
        router = self.opened()
        self.server.routes[("GET", SUMMARY)] = [(200, {"account": {"balance": "10000", "NAV": "9800",
                                                                   "marginUsed": "0", "currency": "USD"}})]
        self.server.add("PUT", CLOSE, (200, {"orderFillTransaction": close_fill()}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "CLOSED"})

    def test_flatten_off_only_halts(self):
        router = self.opened(AI_FLOOR_DAILY_LOSS_FLATTEN="0")
        self.nav("9800")
        self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "KEEP"})
        self.assertEqual(self.puts(), [])
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.assertEqual(len(self.events("DAILY_LOSS_HALT")), 1)
        self.assertFalse(self.events("FLATTEN_DAILY_LOSS"))

    def test_close_failure_is_retried_next_tick(self):
        router = self.opened()
        self.nav("9790")
        self.server.add("PUT", CLOSE, (400, {"errorCode": "MARKET_HALTED"}), (200, {"orderFillTransaction": close_fill()}))
        self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "CLOSE_REJECTED"})
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.assertEqual(router.manage(NOW + timedelta(minutes=2)), {CID: "CLOSED"})
        self.assertEqual(self.puts(), [CLOSE, CLOSE])

    def test_unknown_action_rows_are_skipped(self):
        router = self.opened()
        DemoStateStore(self.db).update(CID, pending_action="CLOSE:x", action_status="UNKNOWN")
        self.nav("9800")
        with patch.object(router._broker, "close_market") as close:
            self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "ACTION_UNRESOLVED"})
        close.assert_not_called()
        self.assertEqual(self.puts(), [])

    def test_dry_run_sends_nothing(self):
        self.router()
        self.ready()
        self.router().on_cycle(context(), "PLAN_READY", NOW)  # a live run left an OPEN trade
        dry = self.router(AI_FLOOR_BROKER_DEMO_DRY_RUN="1", **LIMIT_ON)
        dry.manage(NOW)  # E0 = 10000
        self.nav("9800")
        calls = len(self.server.calls)
        self.assertEqual(dry.manage(NOW + timedelta(minutes=1)), {CID: "DRY_RUN"})
        self.assertEqual([m for m, _p, _h, _b in self.server.calls[calls:]], ["GET", "GET"])  # summary + openTrades
        self.assertEqual([m for m, _p in self.writes()], ["POST"])
        [(_k, cid, detail)] = [e for e in self.events("DRY_RUN") if e[2].get("action") == "daily_loss_flatten"]
        self.assertEqual((cid, detail["trade_id"], detail["reason"]), (CID, "6397", "DAILY_LOSS"))
        self.assertEqual(self.rows()[0]["status"], "OPEN")

    def test_dry_run_entries_are_refused(self):
        router = self.router(AI_FLOOR_BROKER_DEMO_DRY_RUN="1", **LIMIT_ON)
        self.server.add("GET", OPEN_TRADES, trades())
        self.nav("10000")
        self.assertEqual(router.on_cycle(context(), "NO_SETUP", NOW), ("IGNORED", None))  # E0 = 10000
        self.nav("9800")
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("NOT_ROUTED", "daily_loss_halt"))
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.rows(), [])

    def test_budget_refuses_entry_that_could_cross_the_limit(self):
        router = self.router(**LIMIT_ON)
        self.server.add("GET", OPEN_TRADES, trades())
        self.nav("10000")
        router.on_cycle(context(), "NO_SETUP", NOW)
        self.nav("9850")  # remaining budget 50 < planned risk 9850 units * 0.01 = 98.50
        self.assertEqual(router.on_cycle(context(), "PLAN_READY", NOW), ("NOT_ROUTED", "daily_loss_budget"))
        self.assertEqual(self.writes(), [])
        self.assertIsNone(router.blocked_reason)

    def test_drawdown_flattens_even_with_flatten_off(self):
        router = self.opened(AI_FLOOR_DAILY_LOSS_FLATTEN="0", AI_FLOOR_DAILY_LOSS_START_EQUITY="10500")
        self.nav("9970")  # -0.3 % on the day, below the 9975 drawdown floor
        self.server.add("PUT", CLOSE, (200, {"orderFillTransaction": close_fill()}))
        with patch.object(router._broker, "close_market", wraps=router._broker.close_market) as close:
            self.server.routes[("GET", OPEN_TRADES)] = [trades()]
            self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "CLOSED"})
        self.assertEqual(close.call_args.kwargs["reason"], "DRAWDOWN")
        self.assertTrue(close.call_args.kwargs["client_id"].endswith("-dlimit"))
        self.assertTrue(self.events("FLATTEN_DRAWDOWN"))
        self.assertFalse(self.events("DAILY_LOSS_HALT"))
        self.nav("10000")
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW + timedelta(days=1)),
                         ("NOT_ROUTED", "drawdown_halt"))  # next day: still halted

    def test_emergency_and_loss_halt_are_independent(self):
        router = self.opened()
        self.nav("9800")
        self.server.add("PUT", CLOSE, (200, {"orderFillTransaction": close_fill()}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        router.manage(NOW + timedelta(minutes=1))
        registry = self.registry()
        registry.emergency_stop(actor="operator", reason="drill")
        registry.clear_emergency(actor="operator")
        registry.set_enabled("broker_demo", True, actor="operator")
        # Clearing the emergency leaves the loss halt active.
        self.assertEqual(router.on_cycle(context(run_id="run-2"), "PLAN_READY", NOW + timedelta(minutes=2)),
                         ("NOT_ROUTED", "daily_loss_halt"))
        # Clearing the loss halt leaves an active emergency in place.
        registry.emergency_stop(actor="operator", reason="drill 2")
        router._loss_limit.clear_daily_halt("owner", "test")
        self.assertTrue(registry.emergency_active())
        self.assertEqual(router.on_cycle(context(run_id="run-3"), "PLAN_READY", NOW + timedelta(minutes=3)),
                         ("NOT_ROUTED", "broker_demo_not_allowed"))
        self.assertIsNone(router.blocked_reason)

    def test_nav_read_failure_refuses_entries_and_leaves_trades(self):
        router = self.opened()
        self.server.routes[("GET", SUMMARY)] = [(500, {})]
        decision, flattened = router._loss_check(NOW + timedelta(minutes=1))
        self.assertEqual((decision.entry_allowed, decision.flatten_required, decision.reason, flattened),
                         (False, False, "daily_loss_unavailable", {}))
        with patch.object(router._broker, "close_market") as close:
            router.manage(NOW + timedelta(minutes=1))
        close.assert_not_called()
        self.assertEqual(self.rows()[0]["status"], "OPEN")
        self.assertTrue(self.events("DAILY_LOSS_UNAVAILABLE"))
        self.assertEqual(self.flags()["dl_e0"], "10000")

    def test_restart_keeps_halt(self):
        router = self.opened(AI_FLOOR_DAILY_LOSS_FLATTEN="0")
        self.nav("9800")
        router.manage(NOW + timedelta(minutes=1))
        self.nav("10100")
        restarted = self.router(**LIMIT_ON)
        self.assertEqual(restarted.status()["loss_limit"]["daily_halted"], True)
        decision, _ = restarted._loss_check(NOW + timedelta(minutes=2))
        self.assertEqual((decision.halted, decision.entry_allowed), (True, False))

    def tx_router(self):
        """opened() + a first summary carrying lastTransactionID 100 (stored, never queried retroactively)."""
        router = self.opened()
        self.server.routes[("GET", SUMMARY)] = [summary_tx("10000", "100")]
        self.assertTrue(router._loss_check(NOW + timedelta(minutes=1))[0].entry_allowed)
        self.assertEqual(self.flags()["dl_tx_id"], "100")
        self.assertFalse([p for p in self.server.paths("GET") if "/transactions" in p])
        return router

    def test_deposit_during_a_loss_day_does_not_hide_the_loss(self):
        router = self.tx_router()
        self.server.routes[("GET", SUMMARY)] = [summary_tx("10300", "105")]
        self.server.add("GET", since("100"), transfers(("104", "500"), ("106", "999"), last="106"))
        self.server.add("PUT", CLOSE, (200, {"orderFillTransaction": close_fill()}))
        self.server.routes[("GET", OPEN_TRADES)] = [trades()]
        self.assertEqual(router.manage(NOW + timedelta(minutes=2)), {CID: "CLOSED"})  # 10300 - 10000 - 500 = -2 %
        self.assertEqual(self.flags()["dl_transfers"], "500")  # #106 is after the summary snapshot: not counted
        self.assertEqual(self.flags()["dl_tx_id"], "105")
        [(_k, _c, detail)] = self.events("TRANSFER_RECORDED")
        self.assertEqual((detail["amount"], detail["actor"], detail["reason"]), ("500", "broker", "TRANSFER_FUNDS"))
        self.assertEqual([m for m, _p in self.writes()], ["POST", "PUT"])
        router._loss_check(NOW + timedelta(minutes=3))  # lastTransactionID unchanged: no transactions call
        self.assertEqual(self.server.count("GET", since("100")), 1)
        self.assertFalse([p for p in self.server.paths("GET") if "sinceid?id=105" in p])

    def test_withdrawal_does_not_halt_or_drawdown(self):
        router = self.tx_router()
        self.server.routes[("GET", SUMMARY)] = [summary_tx("9300", "101")]
        self.server.add("GET", since("100"), transfers(("101", "-700"), last="101"))
        decision, flattened = router._loss_check(NOW + timedelta(minutes=2))  # -7 % NAV, all withdrawn
        self.assertEqual((decision.entry_allowed, decision.halted, decision.dd_halted, flattened),
                         (True, False, False, {}))
        self.assertEqual(self.puts(), [])

    def test_failing_transfer_query_refuses_entries_and_retries(self):
        router = self.tx_router()
        self.server.routes[("GET", SUMMARY)] = [summary_tx("10000", "101")]
        self.server.add("GET", since("100"), (500, {}))
        decision, flattened = router._loss_check(NOW + timedelta(minutes=2))
        self.assertEqual((decision.entry_allowed, decision.reason, decision.flatten_required, flattened),
                         (False, "daily_loss_unavailable", False, {}))
        self.assertEqual(self.flags()["dl_tx_id"], "100")  # not advanced
        self.assertTrue(self.events("DAILY_LOSS_TRANSFERS_UNAVAILABLE"))
        self.server.routes[("GET", since("100"))] = [(200, {"transactions": [{"id": "101", "type": "TRANSFER_FUNDS",
                                                                              "amount": "x"}],
                                                            "lastTransactionID": "101"})]
        self.assertEqual(router._loss_check(NOW + timedelta(minutes=3))[0].reason, "daily_loss_unavailable")
        self.server.routes[("GET", since("100"))] = [transfers(last="101")]
        self.assertTrue(router._loss_check(NOW + timedelta(minutes=4))[0].entry_allowed)
        self.assertEqual(self.flags()["dl_tx_id"], "101")
        # Halts are unaffected by a failing query: a breach still halts and flattens.
        self.server.routes[("GET", SUMMARY)] = [summary_tx("9800", "102")]
        self.server.routes[("GET", since("101"))] = [(500, {})]
        self.server.add("PUT", CLOSE, (200, {"orderFillTransaction": close_fill()}))
        decision, flattened = router._loss_check(NOW + timedelta(minutes=5))
        self.assertEqual((decision.reason, flattened), ("daily_loss_halt", {CID: "CLOSED"}))

    def test_flatten_retried_while_nav_unavailable(self):
        router = self.opened()
        self.nav("9790")
        self.server.add("PUT", CLOSE, (400, {"errorCode": "MARKET_HALTED"}), (200, {"orderFillTransaction": close_fill()}))
        self.assertEqual(router.manage(NOW + timedelta(minutes=1)), {CID: "CLOSE_REJECTED"})
        self.server.routes[("GET", SUMMARY)] = [(500, {})]
        decision, flattened = router._loss_check(NOW + timedelta(minutes=2))
        self.assertEqual((decision.entry_allowed, decision.flatten_required, decision.reason, flattened),
                         (False, True, "daily_loss_unavailable", {CID: "CLOSED"}))
        self.assertEqual(self.puts(), [CLOSE, CLOSE])
        self.assertEqual(self.rows()[0]["status"], "CLOSED")

    def test_nan_nav_keeps_flattening_an_active_drawdown(self):
        router = self.opened(AI_FLOOR_DAILY_LOSS_FLATTEN="0", AI_FLOOR_DAILY_LOSS_START_EQUITY="10500")
        self.nav("9970")
        self.server.add("PUT", CLOSE, (400, {"errorCode": "MARKET_HALTED"}), (200, {"orderFillTransaction": close_fill()}))
        self.assertEqual(router._loss_check(NOW + timedelta(minutes=1))[1], {CID: "CLOSE_REJECTED"})
        with patch.object(router._broker, "get_balance", return_value=MagicMock(equity=D("NaN"),
                                                                                  last_transaction_id=None)):
            decision, flattened = router._loss_check(NOW + timedelta(minutes=2))
        self.assertEqual((decision.reason, decision.dd_halted, flattened), ("nav_invalid", True, {CID: "CLOSED"}))

    def test_zero_nav_halts_and_flattens(self):
        router = self.opened(AI_FLOOR_DAILY_LOSS_FLATTEN="0")
        self.nav("0")
        self.server.add("PUT", CLOSE, (200, {"orderFillTransaction": close_fill()}))
        decision, flattened = router._loss_check(NOW + timedelta(minutes=1))
        self.assertEqual((decision.dd_halted, decision.reason, flattened), (True, "drawdown_halt", {CID: "CLOSED"}))
        self.assertIn("dd_halt", self.flags())
        self.assertTrue(self.events("FLATTEN_DRAWDOWN"))


if __name__ == "__main__":
    unittest.main()
