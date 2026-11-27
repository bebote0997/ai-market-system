"""Read-only historial backfill from Exness deals, and exit_price/pnl on new combo closes."""
from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import unittest
from zoneinfo import ZoneInfo

from execution.combo_history import (KNOWN_EXIT_REASONS, backfill_combo_history, derive_exit_reason,
                                     fetch_deals, fill_if_empty, remember_exit)
from execution.exness_brakes import MemoryFlags
from execution.session_combo import london_key, fix_key
from test_session_combo import world, range_pair, bar, ny
from test_liquid_glass import _view
from ui.liquid.model import MISSING, _exit_label


NY = ZoneInfo("America/New_York")


def deal(ticket, kind, price, profit, when, instrument="USDJPY", direction="buy", volume="1"):
    amount = profit
    currency = None
    if isinstance(profit, tuple):
        amount, currency = profit
    payload = {"amount": amount}
    if currency:
        payload["currency"] = currency
    return {"deal": {"type": kind, "position_id": ticket, "price": price, "profit": payload,
                     "instrument": instrument, "direction": direction, "volume": volume,
                     "create_time": when},
            "event_time": when}


# Exact figures from the 18-20 Nov 2026 GET-only deals read.
KNOWN_DEALS = [
    deal("341984", "open", "159.15", "0", "2026-11-18T09:04:25.892074+00:00", direction="sell", volume="0.93"),
    deal("341984", "close", "159.294", ("-84.08", "USC"), "2026-11-18T16:00:13.631297+00:00",
         direction="buy", volume="0.93"),
    deal("357063", "open", "159.628", "0", "2026-11-19T07:05:08.69103+00:00", direction="buy", volume="1.32"),
    deal("357063", "close", "159.805", ("146.19", "USC"), "2026-11-19T16:00:07.564338+00:00",
         direction="sell", volume="1.32"),
    deal("375978", "open", "159.046", "0", "2026-11-20T07:32:23.593013+00:00", direction="sell", volume="0.6"),
    deal("375978", "close", "158.738", ("116.42", "USC"), "2026-11-20T16:00:08.435986+00:00",
         direction="buy", volume="0.6"),
    deal("382026", "open", "1.31016", "0", "2026-11-20T16:00:13.745398+00:00",
         instrument="GBPUSD", direction="buy", volume="1"),
    deal("382026", "close", "1.31007", ("-9", "USC"), "2026-11-20T16:05:17.993306+00:00",
         instrument="GBPUSD", direction="sell", volume="1"),
]


def stale_leg(ticket, symbol, side, volume, day, kind="lon"):
    prefix = "combo_lon_" if kind == "lon" else "combo_fix_"
    return prefix + day, {
        "status": "done", "exited": True, "symbol": symbol, "side": side,
        "position_id": ticket, "volume": volume,
        "opened_at": day + "T12:00:00+00:00",
    }


class FakeHistoryClient:
    def __init__(self, deals=None, error=None):
        self.account_id = "test"
        self.deals = list(deals or [])
        self.error = error
        self.paths = []
        self.mutated = 0

    def get_json(self, path):
        self.paths.append(path)
        if self.error is not None:
            raise self.error
        if "/deals" not in path.split("?", 1)[0]:
            raise AssertionError("not a deals history GET")
        return {"items": list(self.deals), "has_more": False}

    def mutate(self, *args, **kwargs):
        self.mutated += 1
        raise AssertionError("mutate must not be called")


class FakeBroker:
    def __init__(self, store=None, client=None, clock=None):
        self._brakes = type("B", (), {"_store": store or MemoryFlags()})()
        self._transport = type("T", (), {"_client": client})()
        self._clock = clock or (lambda: datetime(2026, 11, 21, 15, 0, tzinfo=timezone.utc))

    @property
    def flag_store(self):
        return self._brakes._store


def put(store, key, state):
    store.set_flag(key, json.dumps(state, sort_keys=True))


def load(store, key):
    raw = store.get_flag(key)
    return None if raw is None else json.loads(raw)


class FillRulesTests(unittest.TestCase):
    def test_fill_if_empty_does_not_overwrite(self):
        state = {"entry": "1.000"}
        self.assertFalse(fill_if_empty(state, "entry", "1.31016"))
        self.assertEqual(state["entry"], "1.000")
        self.assertTrue(fill_if_empty(state, "exit_price", "1.31007"))
        self.assertEqual(state["exit_price"], "1.31007")
        self.assertFalse(fill_if_empty(state, "exit_price", "9"))

    def test_known_reasons_and_no_invented_fix(self):
        self.assertEqual(derive_exit_reason({"position_id": "382026"}, "combo_fix_2026-11-20"), "hq_manual")
        self.assertEqual(derive_exit_reason({"position_id": "341984"}, "combo_lon_2026-11-18"), "london_1100")
        self.assertIsNone(derive_exit_reason({"position_id": "99"}, "combo_fix_2026-11-19"))
        self.assertEqual(derive_exit_reason({"position_id": "1"}, "combo_lon_2026-11-21"), "london_1100")
        self.assertEqual(KNOWN_EXIT_REASONS["357063"], "london_1100")
        self.assertEqual(KNOWN_EXIT_REASONS["375978"], "london_1100")


class BackfillTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryFlags()
        self.client = FakeHistoryClient(KNOWN_DEALS)
        self.broker = FakeBroker(self.store, self.client)
        for key, state in (
            stale_leg("341984", "USDJPY", "SHORT", "0.93", "2026-11-18"),
            stale_leg("357063", "USDJPY", "LONG", "1.32", "2026-11-19"),
            stale_leg("375978", "USDJPY", "SHORT", "0.6", "2026-11-20"),
            stale_leg("382026", "GBPUSD", "LONG", "1", "2026-11-20", kind="fix"),
        ):
            put(self.store, key, state)
        put(self.store, "combo_fix_2026-11-19", {"status": "done", "exited": False})

    def test_fills_the_four_known_tickets_and_leaves_the_fix_without_a_deal(self):
        result = backfill_combo_history(self.broker)
        self.assertTrue(result["fetched"])
        self.assertEqual(result["updated"], 4)
        self.assertEqual(result["pending"], 0)
        self.assertEqual(self.client.mutated, 0)
        self.assertTrue(self.client.paths)
        self.assertTrue(all("/deals" in path for path in self.client.paths))
        rows = {
            "341984": ("2026-11-18", "lon", "159.15", "159.294", "-84.08", "london_1100"),
            "357063": ("2026-11-19", "lon", "159.628", "159.805", "146.19", "london_1100"),
            "375978": ("2026-11-20", "lon", "159.046", "158.738", "116.42", "london_1100"),
            "382026": ("2026-11-20", "fix", "1.31016", "1.31007", "-9", "hq_manual"),
        }
        for ticket, (day, kind, entry, exit_price, pnl, reason) in rows.items():
            key = (london_key if kind == "lon" else fix_key)(
                datetime.fromisoformat(day).date())
            state = load(self.store, key)
            self.assertEqual(state["position_id"], ticket)
            self.assertEqual(state["entry"], entry)
            self.assertEqual(state["exit_price"], exit_price)
            self.assertEqual(state["pnl"], pnl)
            self.assertEqual(state["exit_reason"], reason)
        skipped = load(self.store, "combo_fix_2026-11-19")
        self.assertEqual(skipped, {"status": "done", "exited": False})
        self.assertIsNone(skipped.get("entry"))
        self.assertIsNone(skipped.get("exit_price"))
        self.assertIsNone(skipped.get("pnl"))
        self.assertIsNone(skipped.get("exit_reason"))

    def test_second_run_is_idempotent_and_does_not_overwrite(self):
        backfill_combo_history(self.broker)
        key = london_key(datetime(2026, 11, 19).date())
        state = load(self.store, key)
        state["entry"] = "1.000"
        state["pnl"] = "0"
        put(self.store, key, state)
        self.client.paths.clear()
        result = backfill_combo_history(self.broker)
        again = load(self.store, key)
        self.assertEqual(again["entry"], "1.000")
        self.assertEqual(again["pnl"], "0")
        self.assertEqual(again["exit_price"], "159.805")
        self.assertEqual(result["updated"], 0)

    def test_a_failed_read_leaves_flags_and_does_not_raise(self):
        self.client.error = RuntimeError("down")
        result = backfill_combo_history(self.broker)
        self.assertEqual(result["updated"], 0)
        self.assertEqual(load(self.store, "combo_lon_2026-11-19")["position_id"], "357063")
        self.assertIsNone(load(self.store, "combo_lon_2026-11-19").get("entry"))

    def test_fetch_deals_never_calls_mutate(self):
        rows = fetch_deals(self.client, datetime(2026, 11, 18, tzinfo=timezone.utc),
                           datetime(2026, 11, 21, tzinfo=timezone.utc))
        self.assertEqual(len(rows), 8)
        self.assertEqual(self.client.mutated, 0)
        self.assertEqual(rows[0]["position_id"], "341984")


class NewCloseTests(unittest.TestCase):
    def test_a_london_close_stores_exit_price_and_pnl(self):
        day = ny(2026, 1, 16, 2, 10).date()
        clock, fake, broker, combo = world(ny(2026, 1, 16, 2, 10))
        morning = range_pair(day) + [bar(ny(2026, 1, 16, 2, 10), "150.18", "150.25", "150.12", "150.22")]
        combo.tick(clock.at, morning)
        ticket = json.loads(fake.get_flag(london_key(day)))["position_id"]
        client = FakeHistoryClient([
            deal(ticket, "open", "150.20", "0", "2026-01-16T07:10:00+00:00", direction="buy", volume="0.02"),
            deal(ticket, "close", "150.30", ("12.5", "USD"), "2026-01-16T16:00:00+00:00",
                 direction="sell", volume="0.02"),
        ])
        fake._client = client
        clock.at = ny(2026, 1, 16, 11, 0)
        fake.push_tick(Decimal("150.30"), Decimal("150.32"), clock.at)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO):
            combo.tick(clock.at, morning)
        state = json.loads(fake.get_flag(london_key(day)))
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["exit_reason"], "london_1100")
        self.assertTrue(state.get("entry"))
        self.assertEqual(state["exit_price"], "150.3")
        self.assertEqual(state["pnl"], "12.5")
        self.assertEqual(client.mutated, 0)
        # The new fields are in _FIELDS, so a new process still sees them.
        _, _, _, again = world(clock.at, store=fake)
        reloaded = again._leg(london_key(day))
        self.assertEqual(reloaded["exit_price"], "150.3")
        self.assertEqual(reloaded["pnl"], "12.5")

    def test_remember_exit_swallows_a_failed_deal_read(self):
        state = {"status": "done", "exited": True, "position_id": "9", "exit_reason": "london_1100"}
        client = FakeHistoryClient(error=RuntimeError("down"))
        broker = FakeBroker(MemoryFlags(), client)
        remember_exit(state, "9", key="combo_lon_2026-11-18", broker=broker)
        self.assertEqual(state["exit_reason"], "london_1100")
        self.assertIsNone(state.get("exit_price"))
        self.assertEqual(client.mutated, 0)


class FlatFlagBackfillTests(unittest.TestCase):
    def test_a_manual_close_lands_in_historial_with_its_pnl(self):
        from ui.liquid.render import fragment
        day = datetime(2026, 11, 25, tzinfo=timezone.utc)
        store = MemoryFlags()
        opened = "2026-11-25T07:10:00+00:00"
        put(store, london_key(day.date()), {
            "status": "filled", "exited": False, "symbol": "USDJPY", "side": "LONG",
            "position_id": None, "volume": "0.40", "opened_at": opened,
            "exit_at": "2026-11-25T16:00:00+00:00", "client_id": "combo-lon-2026-11-25",
        })
        client = FakeHistoryClient([
            deal("425111", "open", "156.10", "0", opened, direction="buy", volume="0.40"),
            deal("425111", "close", "156.02", ("-20.19", "USC"), "2026-11-25T16:00:00+00:00",
                 direction="sell", volume="0.40"),
        ])
        broker = FakeBroker(store, client)
        broker.open_trades = lambda: []
        result = backfill_combo_history(broker, now=datetime(2026, 11, 25, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(result["updated"], 1)
        self.assertEqual(client.mutated, 0)
        state = load(store, london_key(day.date()))
        self.assertTrue(state["exited"])
        self.assertEqual(state["status"], "done")
        self.assertEqual(state["position_id"], "425111")
        self.assertEqual(state["entry"], "156.1")
        self.assertEqual(state["exit_price"], "156.02")
        self.assertEqual(state["pnl"], "-20.19")
        again = backfill_combo_history(broker, now=datetime(2026, 11, 25, 21, 30, tzinfo=timezone.utc))
        self.assertEqual(again["updated"], 0)
        self.assertEqual(load(store, london_key(day.date()))["pnl"], "-20.19")
        now = datetime(2026, 11, 25, 21, 0, tzinfo=timezone.utc)
        flags = {london_key(day.date()): store.get_flag(london_key(day.date()))}
        stuck = dict(flags)
        stuck[london_key(day.date())] = json.dumps({
            "status": "filled", "exited": False, "symbol": "USDJPY", "side": "LONG", "position_id": None,
        })
        before = _view(now=now, flags=stuck)
        self.assertEqual(before["strategy"]["london"]["pill"], "En curso")
        view = _view(now=now, flags=flags)
        self.assertEqual(view["strategy"]["london"]["pill"], "Hecha")
        self.assertEqual(view["strategy"]["pairs"][0]["state"], "Hecha")
        self.assertNotIn("En curso", fragment(view, section="Inicio"))
        self.assertNotIn("En curso", fragment(view, section="Riesgo"))
        row = next(item for item in view["history"]["rows"] if item["ticket"] == "425111")
        self.assertIn("20.19", row["pnl"])
        self.assertIn("20.19", view["history"]["stats"]["net"])

    def test_an_open_position_is_not_marked_exited(self):
        day = datetime(2026, 11, 25, tzinfo=timezone.utc)
        store = MemoryFlags()
        put(store, london_key(day.date()), {
            "status": "filled", "exited": False, "symbol": "USDJPY", "side": "LONG",
            "position_id": "425111", "opened_at": "2026-11-25T07:10:00+00:00",
        })
        broker = FakeBroker(store, FakeHistoryClient())
        broker.open_trades = lambda: [type("P", (), {"ticket": "425111", "symbol": "USDJPY", "side": "LONG"})()]
        result = backfill_combo_history(broker, now=datetime(2026, 11, 25, 21, 0, tzinfo=timezone.utc))
        self.assertEqual(result["updated"], 0)
        state = load(store, london_key(day.date()))
        self.assertFalse(state["exited"])
        self.assertEqual(state["status"], "filled")


class HistorialViewTests(unittest.TestCase):
    def test_panel_shows_filled_fields_and_keeps_the_skipped_fix_blank(self):
        flags = {
            "combo_lon_2026-11-18": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "side": "SHORT",
                "position_id": "341984", "volume": "0.93", "entry": "159.15",
                "exit_price": "159.294", "pnl": "-84.08", "exit_reason": "london_1100",
            }),
            "combo_lon_2026-11-19": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "side": "LONG",
                "position_id": "357063", "volume": "1.32", "entry": "159.628",
                "exit_price": "159.805", "pnl": "146.19", "exit_reason": "london_1100",
            }),
            "combo_lon_2026-11-20": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "side": "SHORT",
                "position_id": "375978", "volume": "0.6", "entry": "159.046",
                "exit_price": "158.738", "pnl": "116.42", "exit_reason": "london_1100",
            }),
            "combo_fix_2026-11-20": json.dumps({
                "status": "done", "exited": True, "symbol": "GBPUSD", "side": "LONG",
                "position_id": "382026", "volume": "1", "entry": "1.31016",
                "exit_price": "1.31007", "pnl": "-9", "exit_reason": "hq_manual",
            }),
            "combo_fix_2026-11-19": json.dumps({"status": "done", "exited": False}),
        }
        view = _view(now=datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc), flags=flags)
        by_ticket = {row["ticket"]: row for row in view["history"]["rows"]}
        self.assertEqual(by_ticket["341984"]["entry"], "159.15")
        self.assertEqual(by_ticket["341984"]["exit_price"], "159.294")
        self.assertIn("84.08", by_ticket["341984"]["pnl"])
        self.assertEqual(by_ticket["341984"]["reason"], "cierre 11:00 ET")
        self.assertEqual(by_ticket["357063"]["reason"], "cierre 11:00 ET")
        self.assertIn("146.19", by_ticket["357063"]["pnl"])
        self.assertIn("116.42", by_ticket["375978"]["pnl"])
        self.assertEqual(by_ticket["382026"]["reason"], "cierre manual HQ")
        self.assertIn("9.00", by_ticket["382026"]["pnl"] or "")
        skipped = next(row for row in view["history"]["rows"] if row["ticket"] == MISSING)
        self.assertEqual(skipped["entry"], MISSING)
        self.assertEqual(skipped["exit_price"], MISSING)
        self.assertEqual(skipped["reason"], MISSING)
        self.assertEqual(skipped["pnl"], MISSING)
        self.assertEqual(_exit_label({"exit_reason": "hq_manual"}), "cierre manual HQ")


    def test_stats_use_only_rows_with_pnl(self):
        flags = {
            "combo_lon_2026-11-18": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "side": "SHORT",
                "position_id": "341984", "volume": "0.93", "entry": "159.15",
                "exit_price": "159.294", "pnl": "-84.08", "exit_reason": "london_1100",
                "opened_at": "2026-11-18T09:04:25+00:00",
            }),
            "combo_lon_2026-11-19": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "side": "LONG",
                "position_id": "357063", "volume": "1.32", "entry": "159.628",
                "exit_price": "159.805", "pnl": "146.19", "exit_reason": "london_1100",
                "opened_at": "2026-11-19T07:05:08+00:00",
            }),
            "combo_lon_2026-11-20": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "side": "SHORT",
                "position_id": "375978", "volume": "0.6", "entry": "159.046",
                "exit_price": "158.738", "pnl": "116.42", "exit_reason": "london_1100",
                "opened_at": "2026-11-20T07:32:23+00:00",
            }),
            "combo_fix_2026-11-20": json.dumps({
                "status": "done", "exited": True, "symbol": "GBPUSD", "side": "LONG",
                "position_id": "382026", "volume": "1", "entry": "1.31016",
                "exit_price": "1.31007", "pnl": "-9", "exit_reason": "hq_manual",
                "opened_at": "2026-11-20T16:00:13+00:00",
            }),
            "combo_fix_2026-11-19": json.dumps({"status": "done", "exited": False}),
            "combo_fix_2026-11-18": json.dumps({"status": "done"}),
            "combo_lon_2026-11-17": json.dumps({"status": "skipped"}),
        }
        view = _view(now=datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc), flags=flags)
        stats = view["history"]["stats"]
        self.assertEqual(stats["count"], "4")
        self.assertEqual(stats["winners"], "2")
        self.assertEqual(stats["losers"], "2")
        self.assertIn("169.53", stats["net"])
        self.assertEqual(stats["net_tone"], "pos")
        self.assertNotEqual(stats["profit_factor"], MISSING)
        tickets = [row["ticket"] for row in view["history"]["rows"]]
        self.assertEqual(tickets.count(MISSING), 3)
        self.assertIn("341984", tickets)
        self.assertIn("382026", tickets)

    def test_stats_stay_blank_when_no_row_has_pnl(self):
        flags = {
            "combo_fix_2026-11-19": json.dumps({"status": "done", "exited": False}),
            "combo_lon_2026-11-18": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "position_id": "1",
            }),
        }
        view = _view(now=datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc), flags=flags)
        stats = view["history"]["stats"]
        self.assertEqual(len(view["history"]["rows"]), 2)
        self.assertEqual(stats["count"], "0")
        self.assertEqual(stats["winners"], MISSING)
        self.assertEqual(stats["net"], MISSING)
        self.assertEqual(stats["profit_factor"], MISSING)
