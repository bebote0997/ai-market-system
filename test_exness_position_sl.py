"""A position's SL/TP is a separate ``sltp`` order in the snapshot (OpenAPI Position has no stop_loss_price)."""
from decimal import Decimal
import unittest

from execution.exness_transport import Command, RestExnessTransport


EUR = {"volume_min": "0.01", "volume_max": "200", "volume_step": "0.01", "point_digits": 5,
       "contract_size": "1000", "quote_currency": "USD", "base_currency": "EUR", "margin_currency": "USC"}


class Map:
    def __init__(self, positions, orders):
        self.account_id = "99"
        self.sent = []
        self.routes = {
            "/v1/configuration/accounts/99/account": {"settings": {"currency": "USC"}},
            "/v1/trading/accounts/99/snapshot": {
                "account_state": {"balance": "30086.00", "equity": "30086.00", "used_margin": "1.10"},
                "positions": positions, "orders": orders},
            "/v1/configuration/accounts/99/instruments": {"instruments": ["EURUSD"]},
            "/v1/configuration/accounts/99/instruments/EURUSD/conditions": EUR,
        }

    def get_json(self, path):
        return self.routes[path]

    def mutate(self, method, path, body, key):
        self.sent.append((method, path, body, key))
        return {"status": 202, "payload": {"operation_id": "1", "status": "accepted"}, "uncertain": False}


def position(pid="332845", **extra):
    # Fields of the OpenAPI Position object (snapshot example): no stop_loss_price / take_profit_price.
    row = {"position_id": pid, "direction": "buy", "instrument": "EURUSD", "initial_volume": "0.01",
           "volume": "0.01", "open_price": "1.10152", "state": "open", "margin_rate": "1.0",
           "create_time": "2026-11-17T16:50:45+00:00", "commission": "0", "swap": "0"}
    row.update(extra)
    return row


def sltp(pid="332845", oid="5001", state="placed", **prices):
    # order_type=sltp may omit direction, instrument, price and volume (inherited from the position).
    row = {"order_id": oid, "order_type": "sltp", "state": state, "position_id": pid,
           "create_time": "2026-11-17T16:50:45+00:00"}
    row.update(prices)
    return row


class PositionStopFromSltpTests(unittest.TestCase):
    def test_stop_comes_from_the_linked_sltp_order(self):
        book = RestExnessTransport(Map([position()], [sltp(stop_loss_price="1.09844")])).read_state()
        self.assertEqual(book.positions[0].stop, Decimal("1.09844"))
        self.assertIsNone(book.positions[0].target)
        self.assertEqual(book.sltp_ids, (("5001", "332845"),))

    def test_stop_and_target_from_sltp_and_no_leak_to_other_positions(self):
        orders = [sltp(stop_loss_price="1.09844", take_profit_price="1.10800"),
                  sltp(pid="1", oid="5002", stop_loss_price="1.20000")]
        book = RestExnessTransport(Map([position(), position(pid="2")], orders)).read_state()
        by_id = {p.ticket: p for p in book.positions}
        self.assertEqual((by_id["332845"].stop, by_id["332845"].target), (Decimal("1.09844"), Decimal("1.10800")))
        self.assertEqual((by_id["2"].stop, by_id["2"].target), (None, None))

    def test_type_field_is_also_an_sltp_order(self):
        order = {"order_id": "5001", "type": "sltp", "state": "placed", "position_id": "332845",
                 "stop_loss_price": "1.09844", "take_profit_price": "1.10800"}
        book = RestExnessTransport(Map([position()], [order])).read_state()
        self.assertEqual((book.positions[0].stop, book.positions[0].target), (Decimal("1.09844"), Decimal("1.10800")))
        self.assertEqual(book.orders, ())

    def test_final_state_sltp_is_not_protection(self):
        book = RestExnessTransport(Map([position()], [sltp(state="cancelled", stop_loss_price="1.09844")])).read_state()
        self.assertIsNone(book.positions[0].stop)

    def test_empty_open_time_falls_back_and_a_blank_pair_is_none(self):
        book = RestExnessTransport(Map([position(open_time="", create_time="2026-11-25T07:10:00+00:00")], [])).read_state()
        self.assertEqual(book.positions[0].opened_at.isoformat(), "2026-11-25T07:10:00+00:00")
        blank = position(open_time="", create_time="")
        book = RestExnessTransport(Map([blank], [])).read_state()
        self.assertIsNone(book.positions[0].opened_at)
        book = RestExnessTransport(Map([position(open_time=None)], [])).read_state()
        self.assertIsNotNone(book.positions[0].opened_at)

    def test_no_sltp_still_reads_none(self):
        book = RestExnessTransport(Map([position()], [])).read_state()
        self.assertIsNone(book.positions[0].stop)

    def test_inline_position_stop_is_still_read(self):
        book = RestExnessTransport(Map([position(stop_loss_price="1.09900")], [])).read_state()
        self.assertEqual(book.positions[0].stop, Decimal("1.09900"))

    def test_sltp_without_inherited_fields_is_not_a_working_order(self):
        book = RestExnessTransport(Map([position()], [sltp(stop_loss_price="1.09844")])).read_state()
        self.assertEqual(book.orders, ())

    def test_market_open_sends_stop_as_a_decimal_string_in_the_body(self):
        client = Map([], [])
        RestExnessTransport(client).submit(Command("k1", "MARKET", "EURUSD", "LONG", Decimal("0.01"), None,
                                                   Decimal("1.09844"), None))
        method, path, body, key = client.sent[0]
        self.assertEqual((method, path, key), ("POST", "/v1/trading/accounts/99/positions", "k1"))
        self.assertEqual(body["stop_loss_price"], "1.09844")
        self.assertEqual(body["side"], "buy")
        self.assertNotIn("take_profit_price", body)


if __name__ == "__main__":
    unittest.main()
