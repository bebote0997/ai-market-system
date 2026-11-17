"""Regression: 2026-11-17 London USDJPY. Every open_market came back quote_stale while ticks were live.

``now`` was taken before the multi-second REST read; ticks that arrived during the read were newer than
``now`` and the negative age counted as stale. The age is now measured when it is checked.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import unittest

from execution.broker_port import ACCEPTED, BrokerBalance
from execution.exness_brakes import ExnessBrakes
from execution.exness_broker import REAL_CONFIRM, ExnessBroker
from execution.exness_stream import FUTURE_TICK_TOLERANCE_SECONDS, QuoteBook
from execution.exness_transport import FakeExnessTransport

T0 = datetime(2026, 11, 17, 8, 57, 45, tzinfo=timezone.utc)
JPY = {"volume_min": "0.01", "volume_max": "200", "volume_step": "0.01", "point_digits": 3,
       "contract_size": "1000", "quote_currency": "JPY", "base_currency": "USD", "margin_currency": "USC"}


def armed():
    return {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_EXNESS_REAL_EXECUTION": "1",
            "AI_FLOOR_EXNESS_REAL_CONFIRM": REAL_CONFIRM, "AI_FLOOR_EXNESS_RISK_CAP": "300"}


class Clock:
    def __init__(self):
        self.at = T0

    def __call__(self):
        return self.at


class LiveTicks(FakeExnessTransport):
    """Each REST read takes 1.5 s, and USDJPY ticks during it (as it does at the London open)."""

    def __init__(self, clock):
        super().__init__(clock=clock)
        self.clock = clock
        self.balance = BrokerBalance(Decimal("30086"), Decimal("30086"), Decimal("0"), "USC")
        self.instruments = ("USDJPY",)
        self.conditions = {"USDJPY": dict(JPY)}
        self.push_tick(Decimal("159.620"), Decimal("159.630"), T0, symbol="USDJPY")

    def read_state(self):
        self.clock.at += timedelta(seconds=1.5)
        self.push_tick(Decimal("159.620"), Decimal("159.630"), self.clock.at, symbol="USDJPY")
        return super().read_state()


def open_long(adapter):
    return adapter.open_market(symbol="USDJPY", side="LONG", quantity=Decimal("0.01"), entry=Decimal("159.630"),
                               stop=Decimal("159.064"), target=Decimal("160.762"),
                               client_id="combo-lon-2026-11-17-LONG", source_run_id="session-combo-2026-11-17")


class QuoteAgeTests(unittest.TestCase):
    def test_ticks_during_the_read_are_not_stale(self):
        clock = Clock()
        fake = LiveTicks(clock)
        adapter = ExnessBroker(fake, env=armed(), brakes=ExnessBrakes.from_env(fake, armed()), clock=clock)
        result = open_long(adapter)
        self.assertNotEqual(result.reason, "quote_stale")
        self.assertEqual(result.status, ACCEPTED)
        self.assertEqual(len(fake.commands), 1)

    def test_old_tick_is_still_stale(self):
        clock = Clock()
        fake = LiveTicks(clock)
        fake.read_state = lambda: FakeExnessTransport.read_state(fake)
        clock.at = T0 + timedelta(seconds=30)
        adapter = ExnessBroker(fake, env=armed(), brakes=ExnessBrakes.from_env(fake, armed()), clock=clock)
        self.assertEqual(open_long(adapter).reason, "quote_stale")
        self.assertEqual(fake.commands, [])

    def test_future_tolerance_is_bounded(self):
        book = QuoteBook()
        book.note_tick(Decimal("1"), Decimal("1.1"), T0 + timedelta(seconds=1), symbol="USDJPY")
        self.assertTrue(book.fresh(T0, 5, "USDJPY"))
        book.note_tick(Decimal("1"), Decimal("1.1"), T0 + timedelta(seconds=FUTURE_TICK_TOLERANCE_SECONDS + 1),
                       symbol="USDJPY")
        self.assertFalse(book.fresh(T0, 5, "USDJPY"))
        book.note_tick(Decimal("1"), Decimal("1.1"), T0 - timedelta(seconds=6), symbol="USDJPY")
        self.assertFalse(book.fresh(T0, 5, "USDJPY"))


if __name__ == "__main__":
    unittest.main()
