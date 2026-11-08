"""EURUSD EMA 10/21 cross: delayed entry, session bounds, and isolation from the H1 pullback.

The router cases use a fake broker. The flag is OFF unless a test turns it on.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from execution.broker_demo_router import BrokerDemoRouter
from execution.broker_demo_state import OPEN, DemoStateStore
from execution.ema_cross import (SL_PIPS, STRATEGY, TP_PIPS, anchor_plan, client_id_for, ema, ema_flag,
                                in_entry_window, levels_from_fill, news_gate, signal_id_for, step)
from execution.ema_cross_state import EmaSignalStore, signal_from_row
from execution.eurusd_pullback import STRATEGY as PULLBACK
from execution import trade_duration as td


CONFIRM = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)  # Monday 12:00 UTC, inside London
READY = CONFIRM + timedelta(minutes=15)
JUMP = D("0.02")


def m15_long(confirm=CONFIRM, jump=JUMP):
    """Declining closes, then one jump that crosses EMA10 up through EMA21 on ``confirm``."""
    bars, price = [], D("1.20000")
    stamp = confirm - timedelta(minutes=15) * 39
    for _ in range(39):
        bars.append((stamp, price))
        stamp += timedelta(minutes=15)
        price -= D("0.00030")
    bars.append((confirm, bars[-1][1] + jump))
    return bars


def h1_up(confirm=CONFIRM):
    bars, price = [], D("1.05000")
    stamp = confirm - timedelta(hours=29)
    for _ in range(30):
        bars.append((stamp, price))
        stamp += timedelta(hours=1)
        price += D("0.00100")
    return bars


def h1_down(confirm=CONFIRM):
    return [(stamp, D("1.20000") - close + D("1.05000")) for stamp, close in h1_up(confirm)]


def macro(events=()):
    report = SimpleNamespace(status="OK", evidence=({"macro_events": tuple(events), "news_items": ()},))
    deterministic = SimpleNamespace(setup_assessment=SimpleNamespace(warnings=()), macro_news_report=report)
    return {"symbol": "EURUSD", "run_id": "run-ema", "deterministic": deterministic, "session_open": True}


class FlagTests(unittest.TestCase):
    def test_only_exact_one_enables_the_strategy(self):
        self.assertFalse(ema_flag({}))
        self.assertFalse(ema_flag({"AI_FLOOR_EMA_CROSS": "0"}))
        self.assertFalse(ema_flag({"AI_FLOOR_EMA_CROSS": "true"}))
        self.assertFalse(ema_flag({"AI_FLOOR_EMA_CROSS": "1 "}))
        self.assertTrue(ema_flag({"AI_FLOOR_EMA_CROSS": "1"}))


class RuleTests(unittest.TestCase):
    def test_ema_is_shift_zero_and_seeded_with_the_first_average(self):
        closes = tuple(D(1) for _ in range(10))
        self.assertEqual(ema(closes, 10), D(1))
        self.assertIsNone(ema(closes[:-1], 10))
        weight = D(2) / D(11)
        self.assertEqual(ema(closes + (D(3),), 10), D(3) * weight + D(1) * (1 - weight))

    def test_slippage_is_measured_from_the_actual_fill(self):
        # Quote was 1.09000, so the order carried stop 1.08800 / target 1.09400. The fill was 1.09020.
        action, stop, target, reason = anchor_plan(
            "LONG", D("1.09020"), D("1.08800"), D("1.09400"), bid=D("1.09010"), ask=D("1.09020"))
        self.assertEqual((action, reason, stop, target), ("REPLACE", "risk_above_20", D("1.08820"), D("1.09420")))
        favorable = anchor_plan("LONG", D("1.08980"), D("1.08800"), D("1.09400"), bid=D("1.08970"), ask=D("1.08980"))
        self.assertEqual(favorable[0], "REPLACE")
        self.assertEqual(favorable[3], "restore_20_40")
        self.assertEqual(anchor_plan("LONG", D("1.09000"), D("1.08800"), D("1.09400"))[:2], ("OK", D("1.08800")))
        through = anchor_plan("LONG", D("1.09020"), D("1.08800"), D("1.09400"), bid=D("1.08820"), ask=D("1.08830"))
        self.assertEqual(through[0], "CLOSE")
        self.assertEqual(anchor_plan("LONG", None, D("1.08800"), D("1.09400"))[0], "WAIT")

    def test_levels_are_20_and_40_pips_from_the_fill(self):
        self.assertEqual((SL_PIPS, TP_PIPS), (D(20), D(40)))
        fill, stop, target = levels_from_fill("LONG", D("1.09000"))
        self.assertEqual((fill, stop, target), (D("1.09000"), D("1.08800"), D("1.09400")))
        fill, stop, target = levels_from_fill("SHORT", D("1.09000"))
        self.assertEqual((fill, stop, target), (D("1.09000"), D("1.09200"), D("1.08600")))

    def test_cross_arms_only_when_the_last_completed_h1_agrees(self):
        armed = step(None, at=CONFIRM, m15=m15_long(), h1=h1_up(), session_open=True)
        self.assertEqual((armed.action, armed.signal.side, armed.signal.ready_at), ("ARM", "LONG", READY))
        blocked = step(None, at=CONFIRM, m15=m15_long(), h1=h1_down(), session_open=True)
        self.assertEqual((blocked.action, blocked.reason), ("HOLD", "h1_filter"))

    def test_a_future_h1_bar_cannot_flip_the_filter(self):
        future = h1_up() + [(CONFIRM + timedelta(hours=2), D("0.50000"))]
        armed = step(None, at=CONFIRM, m15=m15_long(), h1=future, session_open=True)
        self.assertEqual(armed.action, "ARM")

    def test_entry_waits_fifteen_minutes_then_uses_one_slot(self):
        armed = step(None, at=CONFIRM, m15=m15_long(), h1=h1_up(), session_open=True).signal
        waiting = step(armed, at=CONFIRM + timedelta(minutes=5), m15=m15_long(), h1=h1_up(), session_open=True)
        self.assertEqual(waiting.action, "HOLD")
        later = m15_long() + [(READY, m15_long()[-1][1])]
        entered = step(armed, at=READY, m15=later, h1=h1_up(), session_open=True, flat=True)
        self.assertEqual(entered.action, "ENTER")
        missed = step(armed, at=READY + timedelta(minutes=15), m15=later, h1=h1_up(), session_open=True, flat=True)
        self.assertEqual((missed.action, missed.reason), ("EXPIRE", "stale"))

    def test_opposite_cross_cancels_before_the_order(self):
        armed = step(None, at=CONFIRM, m15=m15_long(), h1=h1_up(), session_open=True).signal
        opposite = m15_long() + [(READY, m15_long()[-1][1] - D("0.05"))]
        cancelled = step(armed, at=READY, m15=opposite, h1=h1_up(), session_open=True, flat=True)
        self.assertEqual((cancelled.action, cancelled.reason), ("CANCEL", "opposite_cross"))

    def test_h1_flip_cancels_the_waiting_signal(self):
        armed = step(None, at=CONFIRM, m15=m15_long(), h1=h1_up(), session_open=True).signal
        cancelled = step(armed, at=CONFIRM + timedelta(minutes=5), m15=m15_long(), h1=h1_down(), session_open=True)
        self.assertEqual((cancelled.action, cancelled.reason), ("CANCEL", "h1_filter"))

    def test_session_boundary_and_weekend_do_not_arm(self):
        late = datetime(2026, 11, 2, 14, 45, tzinfo=timezone.utc)
        shift = late - CONFIRM
        moved = step(None, at=late, m15=[(t + shift, c) for t, c in m15_long()],
                     h1=[(t + shift, c) for t, c in h1_up()], session_open=True)
        self.assertEqual((moved.action, moved.reason), ("HOLD", "session_boundary"))
        self.assertFalse(in_entry_window(datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc)))  # Saturday
        weekend = step(None, at=datetime(2026, 11, 7, 13, 0, tzinfo=timezone.utc), m15=m15_long(), h1=h1_up(),
                       session_open=False)
        self.assertEqual(weekend.reason, "session_boundary")

    def test_news_and_an_open_position_hold_through_the_slot_then_expire(self):
        armed = step(None, at=CONFIRM, m15=m15_long(), h1=h1_up(), session_open=True).signal
        later = m15_long() + [(READY, m15_long()[-1][1])]
        held = step(armed, at=READY, m15=later, h1=h1_up(), news_reason="high_impact_active_window",
                    session_open=True, flat=True)
        self.assertEqual(held.action, "HOLD")
        busy = step(armed, at=READY, m15=later, h1=h1_up(), session_open=True, flat=False)
        self.assertEqual(busy.reason, "position_open")
        expired = step(armed, at=READY + timedelta(minutes=15), m15=later, h1=h1_up(),
                       news_reason="high_impact_active_window", session_open=True, flat=False)
        self.assertEqual(expired.action, "EXPIRE")

    def test_news_gate_matches_the_live_high_impact_block_and_allows_an_empty_calendar(self):
        self.assertIsNone(news_gate("NO_DATA"))
        self.assertEqual(news_gate(None), "macro_unavailable")
        self.assertEqual(news_gate("ERROR"), "macro_error")
        event = {"impact": "HIGH", "window": "ACTIVE_WINDOW"}
        self.assertEqual(news_gate("OK", events=(event,)), "high_impact_active_window")

    def test_signal_identity_is_stable(self):
        first = signal_id_for(CONFIRM, "LONG")
        self.assertEqual(client_id_for(first), client_id_for(first))
        self.assertNotEqual(client_id_for(first), client_id_for(signal_id_for(CONFIRM, "SHORT")))
        self.assertTrue(client_id_for(first).startswith("aif-ema-"))


class StoreTests(unittest.TestCase):
    def test_a_restart_reloads_the_pending_signal_and_refuses_a_second_submit(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "broker_state.db"
            armed = step(None, at=CONFIRM, m15=m15_long(), h1=h1_up(), session_open=True).signal
            EmaSignalStore(path).save(armed, now=CONFIRM)
            reloaded = signal_from_row(EmaSignalStore(path).pending("EURUSD"))
            self.assertEqual((reloaded.signal_id, reloaded.side, reloaded.ready_at),
                             (armed.signal_id, "LONG", READY))
            self.assertTrue(EmaSignalStore(path).save(
                type(armed)(armed.signal_id, armed.side, armed.confirmed_at, armed.ready_at,
                            status="SUBMITTED", reason="entry", client_id=armed.client_id), now=READY))
            self.assertIsNone(EmaSignalStore(path).pending("EURUSD"))
            self.assertIn(armed.signal_id, EmaSignalStore(path).terminal_ids("EURUSD"))
            self.assertFalse(EmaSignalStore(path).save(armed, now=READY))  # terminal is not re-armed


class _Broker:
    def __init__(self, trades=()):
        self.plans = []
        self.partials = []
        self.trades = list(trades)

    def open_trades(self):
        return list(self.trades)

    def get_balance(self):
        return SimpleNamespace(equity=D("100000"))

    def get_quote(self, symbol):
        return (True, D("1.08990"), D("1.09000"))

    def submit_plan(self, plan, approval):
        self.plans.append(plan)
        return SimpleNamespace(status="ACCEPTED", broker_ref="6397", reason=None)

    def close_units(self, *args, **kwargs):
        self.partials.append(args)
        raise AssertionError("EMA must not scale out")


class RouterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "broker_state.db"
        self.store = DemoStateStore(self.db)
        self.store.initialize()
        self.broker = _Broker()
        self.registry = SimpleNamespace(emergency_active=lambda: False, is_allowed=lambda _mode: True)

    def router(self, **kwargs):
        return BrokerDemoRouter(self.broker, self.store, registry=self.registry, dry_run=False, max_units=100000,
                                ema_cross=kwargs.get("ema_cross", True), pullback=False)

    def test_flag_off_does_not_create_the_signal_table_or_read_the_broker(self):
        router = self.router(ema_cross=False)
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(self.broker.trades, [])
        with sqlite3.connect(self.db) as con:
            names = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertNotIn("ema_cross_signals", names)

    def test_market_entry_is_tagged_and_not_repeated_after_a_restart(self):
        router = self.router()
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        self.assertEqual(self.broker.plans, [])
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(len(self.broker.plans), 1)
        plan = self.broker.plans[0]
        self.assertEqual((plan.side, plan.planned_entry, plan.stop, plan.target, plan.quantity),
                         ("LONG", D("1.09000"), D("1.08800"), D("1.09400"), D(100000)))
        row = self.store.trades((OPEN,))[0]
        self.assertEqual(row["strategy"], STRATEGY)
        self.assertEqual(row["scale_stage"], "NONE")
        self.assertNotEqual(row["strategy"], PULLBACK)
        again = _Broker()
        self.broker = again
        restarted = self.router()
        restarted.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(again.plans, [])

    def test_an_open_pullback_blocks_the_ema_order(self):
        self.store.insert_intent({
            "client_id": "aif-pullback", "run_id": "run-pull", "symbol": "EURUSD", "side": "SHORT", "units": 1000,
            "entry": "1.09000", "stop": "1.09250", "target": "1.08500", "status": OPEN, "trade_id": "9001",
            "strategy": PULLBACK, "order_kind": "LIMIT", "scale_stage": "NONE"})
        self.broker.trades.append(SimpleNamespace(
            trade_id="9001", instrument="EUR_USD", client_ext_id="aif-pullback", price=D("1.09000"),
            open_time=CONFIRM, stop=D("1.09250")))
        router = self.router()
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(self.store.trades((OPEN,))[0]["strategy"], PULLBACK)

    def test_reversal_does_not_close_an_ema_position(self):
        router = self.router()
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        row = self.store.trades((OPEN,))[0]
        self.broker.trades.append(SimpleNamespace(
            trade_id=row["trade_id"], instrument="EUR_USD", client_ext_id=row["client_id"],
            price=D("1.09000"), open_time=READY, stop=D("1.08800")))
        decision = SimpleNamespace(side="SHORT", symbol="EURUSD")
        self.assertEqual(router._reversal_row(decision, "EURUSD", self.broker.trades), (None, "position_already_open"))
        self.assertEqual(self.store.trade(row["client_id"])["stop"], row["stop"])

    def test_opposite_cross_and_a_missed_slot_send_nothing(self):
        router = self.router()
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        opposite = m15_long() + [(READY, m15_long()[-1][1] - D("0.05"))]
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=opposite, h1_closes=h1_up())
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(self.store.trades(), [])
        row = EmaSignalStore(self.db).get(signal_id_for(CONFIRM, "LONG"))
        self.assertEqual(row["status"], "CANCELLED")
        self.assertEqual(row["reason"], "opposite_cross")

    def test_news_blocks_the_entry_inside_the_slot(self):
        router = self.router()
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        later = m15_long() + [(READY, m15_long()[-1][1])]
        blocked = macro(events=({"impact": "HIGH", "window": "ACTIVE_WINDOW"},))
        router.on_cycle(blocked, "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.plans, [])
        self.assertEqual(EmaSignalStore(self.db).pending("EURUSD")["status"], "PENDING")

    def test_ema_management_does_not_trail_or_take_partials(self):
        router = self.router()
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        row = self.store.trades((OPEN,))[0]
        stop_before = row["stop"]
        partials = []
        router._apply_pullback_step = lambda *args, **kwargs: partials.append(args)
        day1 = datetime(2026, 11, 2, 22, 0, tzinfo=timezone.utc)  # Monday 17:00 NY, before Friday flatten
        self.assertEqual(router._manage_row(row, day1, {}, lambda _symbol: (True, D("1.09300"), D("1.09310"))), "KEEP")
        self.assertEqual(partials, [])
        self.assertEqual(self.broker.partials, [])
        self.assertEqual(self.store.trade(row["client_id"])["stop"], stop_before)
        closed = []
        router._close = lambda current, reason, _cid: closed.append(reason) or "CLOSED"
        opened = datetime.fromisoformat(self.store.trade(row["client_id"])["opened_at"])
        boundary, reason = td.hold_boundary(opened)
        self.assertEqual(router._manage_row(self.store.trade(row["client_id"]), boundary, {},
                                            lambda _symbol: (True, D("1.09000"), D("1.09010"))), "CLOSED")
        self.assertEqual(closed, [reason])
        self.assertEqual(partials, [])

    def _open_ema(self):
        router = self.router()
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        row = self.store.trades((OPEN,))[0]
        return router, row

    def _show_broker(self, row, price, stop, target):
        self.broker.trades = [SimpleNamespace(trade_id=row.get("trade_id") or "6397", price=D(price),
                                              stop=None if stop is None else D(stop),
                                              target=None if target is None else D(target), instrument="EUR_USD")]

    def test_actual_fill_slippage_replaces_stop_and_target(self):
        router, row = self._open_ema()
        self.broker.replacements = []

        def replace_protection(trade_id, stop, target, *, client_id):
            self.broker.replacements.append((trade_id, stop, target, client_id))
            self.broker.trades[0].stop = stop
            self.broker.trades[0].target = target
            return SimpleNamespace(status="ACCEPTED", reason=None)

        self.broker.replace_protection = replace_protection
        # Sent from ask 1.09000. The broker fill is 2 pips worse, so the attached stop is 22 pips away.
        # The local row is given a fake 20/40 so a local-only check would look done. The broker prices govern.
        self.store.update(row["client_id"], fill_price="1.09020", trade_id="6397", stop="1.08820", target="1.09420")
        self._show_broker(self.store.trade(row["client_id"]), "1.09020", "1.08800", "1.09400")
        self.assertEqual(router._reanchor_ema(self.store.trade(row["client_id"]), {}, None), "ANCHORED")
        self.assertEqual(self.broker.replacements[0][1:3], (D("1.08820"), D("1.09420")))
        saved = self.store.trade(row["client_id"])
        self.assertEqual((saved["stop"], saved["target"], saved["entry"], saved["scale_stage"]),
                         ("1.08820", "1.09420", "1.09020", "ANCHORED"))
        self.broker.replacements.clear()
        self.assertEqual(router._reanchor_ema(saved, {}, None), "KEEP")
        self.assertEqual(self.broker.replacements, [])

    def test_oversized_stop_is_closed_when_the_replace_fails(self):
        router, row = self._open_ema()
        self.broker.replace_protection = lambda *args, **kwargs: SimpleNamespace(status="REJECTED", reason="broker_rejected")
        closed = []
        router._close = lambda current, reason, _cid: closed.append(reason) or "CLOSED"
        self.store.update(row["client_id"], fill_price="1.09020", trade_id="6397", stop="1.08820", target="1.09420")
        self._show_broker(self.store.trade(row["client_id"]), "1.09020", "1.08800", "1.09400")
        self.assertEqual(router._reanchor_ema(self.store.trade(row["client_id"]), {}, None), "CLOSED")
        self.assertEqual(closed, ["EMA_RISK_UNCORRECTED"])

    def test_a_tighter_stop_is_kept_when_the_replace_fails(self):
        router, row = self._open_ema()
        self.broker.replace_protection = lambda *args, **kwargs: SimpleNamespace(status="REJECTED", reason="broker_rejected")
        closed = []
        router._close = lambda *args, **kwargs: closed.append(args) or "CLOSED"
        # Fill 2 pips better than the ask: the broker stop is only 18 pips from the fill.
        self.store.update(row["client_id"], fill_price="1.08980", trade_id="6397")
        self._show_broker(self.store.trade(row["client_id"]), "1.08980", row["stop"], row["target"])
        self.assertEqual(router._reanchor_ema(self.store.trade(row["client_id"]), {}, None), "KEPT")
        self.assertEqual(closed, [])
        self.assertEqual(self.store.trade(row["client_id"])["stop"], row["stop"])
        self.assertEqual(self.store.trade(row["client_id"])["scale_stage"], "KEPT")

    def test_price_already_through_the_fill_stop_closes_without_a_replace(self):
        router, row = self._open_ema()
        called = []
        self.broker.replace_protection = lambda *args, **kwargs: called.append(args)
        closed = []
        router._close = lambda current, reason, _cid: closed.append(reason) or "CLOSED"
        self.store.update(row["client_id"], fill_price="1.09020", trade_id="6397")
        self._show_broker(self.store.trade(row["client_id"]), "1.09020", row["stop"], row["target"])
        quotes = {"EURUSD": ((True, D("1.08800"), D("1.08810")), None)}
        self.assertEqual(router._reanchor_ema(self.store.trade(row["client_id"]), quotes, None), "CLOSED")
        self.assertEqual(called, [])
        self.assertEqual(closed, ["EMA_PRICE_THROUGH_STOP"])

    def test_a_local_stop_is_not_protection_when_the_broker_trade_has_none(self):
        router, row = self._open_ema()
        calls = []

        def replace_protection(trade_id, stop, target, *, client_id):
            calls.append((stop, target))
            return SimpleNamespace(status="REJECTED", reason="broker_rejected")

        self.broker.replace_protection = replace_protection
        closed = []
        router._close = lambda current, reason, _cid: closed.append(reason) or "CLOSED"
        self.store.update(row["client_id"], fill_price="1.09020", trade_id="6397", stop="1.08820", target="1.09420",
                          scale_stage="NONE")
        self._show_broker(self.store.trade(row["client_id"]), "1.09020", None, None)
        self.assertEqual(router._reanchor_ema(self.store.trade(row["client_id"]), {}, None), "CLOSED")
        self.assertEqual(calls, [(D("1.08820"), D("1.09420"))])
        self.assertEqual(closed, ["EMA_RISK_UNCORRECTED"])
        self.assertNotEqual(self.store.trade(row["client_id"])["scale_stage"], "ANCHORED")

    def test_an_unreadable_broker_trade_is_not_marked_anchored(self):
        router, row = self._open_ema()
        self.store.update(row["client_id"], fill_price="1.09000", trade_id="6397", stop="1.08800", target="1.09400")

        def unreadable():
            raise RuntimeError("broker down")

        self.broker.open_trades = unreadable
        self.assertEqual(router._reanchor_ema(self.store.trade(row["client_id"]), {}, None), "WAIT")
        self.assertEqual(self.store.trade(row["client_id"])["scale_stage"], "NONE")

    def test_a_failed_protective_close_stays_retryable(self):
        router, row = self._open_ema()
        self.broker.replace_protection = lambda *args, **kwargs: SimpleNamespace(status="REJECTED", reason="broker_rejected")
        router._close = lambda *_args, **_kwargs: "CLOSE_UNKNOWN"
        self.store.update(row["client_id"], fill_price="1.09020", trade_id="6397")
        self._show_broker(self.store.trade(row["client_id"]), "1.09020", None, "1.09420")
        self.assertEqual(router._reanchor_ema(self.store.trade(row["client_id"]), {}, None), "CLOSE_UNKNOWN")
        saved = self.store.trade(row["client_id"])
        self.assertEqual(saved["status"], "OPEN")
        self.assertNotIn(saved["scale_stage"], ("ANCHORED", "KEPT"))

    def test_daily_loss_gate_blocks_the_ema_order(self):
        router = self.router()
        router._loss_limit = object()
        router._loss_check = lambda *_args, **_kwargs: (
            SimpleNamespace(entry_allowed=False, reason="daily_loss_halt", flatten_required=False), {})
        later = m15_long() + [(READY, m15_long()[-1][1])]
        router.on_cycle(macro(), "NO_SETUP", CONFIRM, m15_bars=m15_long(), h1_closes=h1_up())
        router.on_cycle(macro(), "NO_SETUP", READY, m15_bars=later, h1_closes=h1_up())
        self.assertEqual(self.broker.plans, [])


if __name__ == "__main__":
    unittest.main()
