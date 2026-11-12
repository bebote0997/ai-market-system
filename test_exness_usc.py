"""USC cent-account mappings confirmed on the live read-only check and the OpenAPI."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from execution.exness_brakes import RESUME_PHRASE, ExnessBrakes
from execution.exness_broker import REAL_CONFIRM, ExnessBroker, real_execution_armed
from execution.exness_check import report
from execution.exness_client import ExnessClient
from execution.exness_m5 import parse_candle_payload, parse_tick_frame
from execution.exness_sizing import account_risk_cap, size_lots, spec_from_conditions
from execution.exness_transport import Command, FakeExnessTransport, RestExnessTransport
from execution.session_combo import size_for_cap
from runtime.demo_runner import REAL_EXECUTION_ENABLED


NOW = datetime(2026, 11, 12, 0, 20, tzinfo=timezone.utc)
EUR = {"volume_min": "0.01", "volume_max": "200", "volume_step": "0.01", "point_digits": 5,
       "contract_size": "1000", "quote_currency": "USD", "base_currency": "EUR", "margin_currency": "USC"}
JPY = {"volume_min": "0.01", "volume_max": "200", "volume_step": "0.01", "point_digits": 3,
       "contract_size": "1000", "quote_currency": "JPY", "base_currency": "USD", "margin_currency": "USC"}


def env():
    return {"AI_FLOOR_BROKER": "exness", "EXN_API_KEY": "exnsk-public", "EXN_ACCOUNT_ID": "99",
            "EXN_DISCOVERY_BASE_URL": "https://gw.example", "EXN_PRIVATE_KEY": "not-a-live-secret"}


class Map:
    def __init__(self, routes):
        self.routes = routes
        self.account_id = "99"
        self.sent = []

    def get_json(self, path):
        return self.routes[path]

    def mutate(self, method, path, body, key):
        self.sent.append((method, path, body, key))
        return {"status": 202, "payload": {"operation_id": "1", "status": "accepted"}, "uncertain": False}


def live_routes(equity="0"):
    account = "/v1/configuration/accounts/99/account"
    snapshot = "/v1/trading/accounts/99/snapshot"
    listed = "/v1/configuration/accounts/99/instruments"
    routes = {
        account: {"is_demo": True, "settings": {"currency": "USC", "trade_mode": "full", "account_status": "active",
                                                "execution_mode": "instant", "leverage": "2000"}},
        snapshot: {"account_state": {"balance": equity, "equity": equity, "used_margin": "0", "leverage": "0"},
                   "positions": [{"position_id": 77, "direction": "buy", "open_price": "1.10000",
                                  "instrument": "EURUSD", "volume": "0.01", "state": "open",
                                  "comment": "combo-fix-2026-01-15-EURUSD"}],
                   "orders": [{"order_id": "9", "direction": "sell", "order_type": "limit", "instrument": "GBPUSD",
                               "price": "1.25000", "volume": "0.02", "stop_loss_price": "1.26000",
                               "take_profit_price": "1.23000"}]},
        listed: {"instruments": ["EURUSD", "GBPUSD", "USDJPY"]},
        "/v1/configuration/accounts/99/instruments/EURUSD/conditions": EUR,
        "/v1/configuration/accounts/99/instruments/GBPUSD/conditions": dict(EUR, base_currency="GBP"),
        "/v1/configuration/accounts/99/instruments/USDJPY/conditions": JPY,
    }
    return routes


class UscMappingTests(unittest.TestCase):
    def test_settings_currency_and_contract_size_build_a_balance(self):
        book = RestExnessTransport(Map(live_routes())).read_state()
        self.assertEqual(book.balance.currency, "USC")
        self.assertEqual(book.balance.balance, Decimal("0"))
        self.assertEqual(book.specs["EURUSD"].contract_size, Decimal("1000"))
        self.assertEqual(book.specs["EURUSD"].profit_rate, Decimal("100"))
        self.assertEqual(book.specs["GBPUSD"].profit_rate, Decimal("100"))
        self.assertNotIn("USDJPY", book.specs)
        self.assertEqual(book.positions[0].ticket, "77")
        self.assertEqual(book.positions[0].side, "LONG")
        self.assertEqual(book.positions[0].price, Decimal("1.10000"))
        self.assertEqual(book.positions[0].comment, "combo-fix-2026-01-15-EURUSD")
        self.assertEqual((book.orders[0].ticket, book.orders[0].side, book.orders[0].kind), ("9", "SHORT", "limit"))

    def test_usdjpy_rate_uses_only_an_in_hand_quote(self):
        missing, reason = spec_from_conditions(JPY, "USC")
        self.assertEqual((missing, reason), (None, "profit_rate_unavailable"))
        spec, reason = spec_from_conditions(JPY, "USC", Decimal("160"))
        self.assertIsNone(reason)
        self.assertEqual(spec.profit_rate, Decimal("100") / Decimal("160"))
        same, reason = spec_from_conditions(dict(EUR, quote_currency="USC"), "USC")
        self.assertEqual(same.profit_rate, Decimal("1"))

    def test_usc_brakes_are_one_thousand_and_five_thousand_and_usd_stays_ten(self):
        store = FakeExnessTransport()
        usc = ExnessBrakes.from_env(store, {})
        self.assertTrue(usc.evaluate(Decimal("50000"), "USC", NOW).entry_allowed)
        self.assertEqual(usc.evaluate(Decimal("49000"), "USC", NOW).reason, "daily_loss")
        self.assertTrue(usc.clear_halt(RESUME_PHRASE))
        fresh = ExnessBrakes.from_env(FakeExnessTransport(), {"AI_FLOOR_EXNESS_DAILY_LOSS_USD": "100000"})
        fresh.evaluate(Decimal("50000"), "USC", NOW)
        self.assertEqual(fresh.evaluate(Decimal("45000"), "USC", NOW).reason, "drawdown")
        usd = ExnessBrakes.from_env(FakeExnessTransport(), {})
        self.assertTrue(usd.evaluate(Decimal("500"), "USD", NOW).entry_allowed)
        self.assertEqual(usd.evaluate(Decimal("490"), "USD", NOW).reason, "daily_loss")
        self.assertEqual(ExnessBrakes.from_env(FakeExnessTransport(), {}).evaluate(Decimal("10"), "EUR", NOW).reason,
                         "currency_unsupported")

    def test_risk_cap_is_five_hundred_usc_and_one_percent(self):
        self.assertEqual(account_risk_cap("USC"), Decimal("500"))
        self.assertEqual(account_risk_cap("USD"), Decimal("5"))
        spec, _reason = spec_from_conditions(EUR, "USC")
        lots, reason = size_for_cap(Decimal("50000"), Decimal("50000"), Decimal("1.10000"), Decimal("1.09700"), spec, "USC")
        self.assertEqual((lots, reason), (Decimal("1.66"), None))
        skipped, reason = size_for_cap(Decimal("50000"), Decimal("50000"), Decimal("1.10000"), Decimal("0.10000"), spec, "USC")
        self.assertEqual((skipped, reason), (Decimal("0"), "min_volume_over_cap"))
        sized, reason = size_lots(Decimal("50000"), Decimal("0.01"), Decimal("1.10000"), Decimal("1.09700"), spec)
        self.assertEqual(sized, Decimal("1.66"))
        self.assertIsNone(reason)

    def test_live_tick_and_candle_shapes(self):
        parsed = parse_tick_frame({"instrument": "USDJPY", "bid": 159.6, "ask": 159.601, "timestamp": "2026-11-12T00:15:00+00:00"})
        self.assertEqual(parsed[0], "USDJPY")
        self.assertEqual(parsed[1], Decimal("159.6"))
        bars, reason = parse_candle_payload(
            {"candles": [{"time": "2026-11-12T00:15:00+00:00", "open": 159.5, "high": 159.7, "low": 159.4,
                          "close": 159.6, "volume": 620}]}, "USDJPY")
        self.assertIsNone(reason)
        self.assertEqual(len(bars), 1)
        self.assertEqual(bars[0].close, Decimal("159.6"))
        self.assertEqual(bars[0].symbol, "USDJPY")

    def test_order_bodies_comment_type_sltp_and_partial_volume(self):
        client = Map(live_routes("50000"))
        transport = RestExnessTransport(client)
        transport.quotes.note_tick(Decimal("160"), Decimal("160"), NOW, symbol="USDJPY")
        market = Command("combo-lon-2026-11-12-LONG", "MARKET", "EURUSD", "LONG", Decimal("0.01"),
                         Decimal("1.1"), Decimal("1.09"), Decimal("1.12"))
        self.assertEqual(transport.submit(market).status, "UNKNOWN")
        self.assertEqual(client.sent[-1][2]["comment"], "combo-lon-2026-11-12-LONG")
        self.assertNotIn("order_type", client.sent[-1][2])
        limit = Command("limit-1", "LIMIT", "GBPUSD", "SHORT", Decimal("0.02"), Decimal("1.25"), Decimal("1.26"), Decimal("1.23"))
        transport.submit(limit)
        self.assertEqual(client.sent[-1][2]["type"], "limit")
        self.assertNotIn("order_type", client.sent[-1][2])
        modify = Command("mod-1", "MODIFY", "", stop=Decimal("1.09"), target=Decimal("1.12"), position_ref="77")
        transport.submit(modify)
        self.assertEqual(client.sent[-1][0], "POST")
        self.assertEqual(client.sent[-1][2]["type"], "sltp")
        self.assertEqual(client.sent[-1][2]["position_id"], "77")
        self.assertTrue(client.sent[-1][1].endswith("/orders"))
        partial = Command("part-1", "CLOSE", "", volume=Decimal("0.01"), position_ref="77")
        transport.submit(partial)
        self.assertTrue(client.sent[-1][1].endswith("/positions/77?volume=0.01"))

    def test_bare_discovery_host_is_https_and_the_check_sends_nothing(self):
        def caller(method, url, headers, body):
            return 200, {"access_point": "https://ap.example"}, {}

        client = ExnessClient({**env(), "EXN_DISCOVERY_BASE_URL": "gw.example"}, caller=caller,
                              sign=lambda data: b"sig", now_ms=lambda: 1_000_000)
        self.assertEqual(client.gateway, "https://gw.example")
        self.assertFalse(REAL_EXECUTION_ENABLED)
        self.assertFalse(real_execution_armed(env()))
        book = RestExnessTransport(Map(live_routes()))
        with tempfile.TemporaryDirectory() as folder:
            view = report({**env(), "AI_FLOOR_EXNESS_STATE_DB_PATH": str(Path(folder) / "state.db")}, transport=book)
        self.assertFalse(hasattr(book, "commands"))
        self.assertEqual(view["currency"], "USC")
        self.assertEqual(view["balance"], "0")
        self.assertFalse(view["armed"])
        self.assertFalse(view["halted"])

    def test_partial_close_refuses_more_than_the_position(self):
        fake = FakeExnessTransport(clock=lambda: NOW)
        from execution.broker_port import BrokerBalance
        from execution.exness_transport import Position
        fake.balance = BrokerBalance(Decimal("50000"), Decimal("50000"), Decimal("0"), "USC")
        fake.instruments = ("EURUSD",)
        fake.conditions = {"EURUSD": EUR}
        fake.push_tick(Decimal("1.10000"), Decimal("1.10002"), NOW)
        fake.positions.append(Position("77", "EURUSD", "LONG", Decimal("0.05"), Decimal("1.1")))
        broker = ExnessBroker(fake, env={**env(), "AI_FLOOR_EXNESS_REAL_EXECUTION": "1",
                                          "AI_FLOOR_EXNESS_REAL_CONFIRM": REAL_CONFIRM},
                              brakes=ExnessBrakes.from_env(fake, {}), clock=lambda: NOW)
        self.assertEqual(broker.close_units("77", Decimal("0.10"), client_id="partial-too-big").reason, "volume_invalid")
        self.assertEqual(fake.commands, [])
        closed = broker.close_units("77", Decimal("0.02"), client_id="partial-ok")
        self.assertEqual(closed.status, "ACCEPTED")
        self.assertEqual(fake.positions[0].volume, Decimal("0.03"))
        self.assertEqual(fake.commands[0].volume, Decimal("0.02"))
