"""Stop-loss check after a session-combo fill: one replace_protection, then one close, never a loop."""
from datetime import timedelta
from decimal import Decimal
import json
import logging
import unittest

from execution.exness_transport import Position
from execution.session_combo import SessionCombo, fix_key
from test_session_combo import fix_bars, lon, world


AT = lon(2026, 1, 15, 16, 0)


def kinds(fake):
    return [command.kind for command in fake.commands]


class ProtectionTests(unittest.TestCase):
    def setUp(self):
        self.bars = fix_bars("GBPUSD", "1.25150", AT)
        self.clock, self.fake, self.broker, self.combo = world(AT)
        self.fake.push_tick(Decimal("1.25140"), Decimal("1.25150"), AT)
        self.combo.tick(AT, self.bars)
        self.assertEqual(kinds(self.fake), ["MARKET"])
        self.ticket = self.fake.positions[0].ticket
        self.sent_stop = self.fake.commands[0].stop

    def set_stop(self, stop):
        held = self.fake.positions[0]
        self.fake.positions[0] = Position(held.ticket, held.symbol, held.side, held.volume, held.price, stop,
                                          held.target, held.comment, held.opened_at)

    def tick(self, seconds, combo=None):
        self.clock.at = AT + timedelta(seconds=seconds)
        self.fake.push_tick(Decimal("1.25140"), Decimal("1.25150"), self.clock.at)
        with self.assertLogs("ai_floor.session_combo", level=logging.DEBUG) as logs:
            logging.getLogger("ai_floor.session_combo").debug("tick")
            (combo or self.combo).tick(self.clock.at, self.bars)
        return "\n".join(logs.output)

    def leg(self):
        return json.loads(self.fake.get_flag(fix_key(AT.date())))

    def test_stop_present_nothing_to_do(self):
        out = self.tick(30)
        out += self.tick(60)
        self.assertEqual(kinds(self.fake), ["MARKET"])
        self.assertNotIn("PROTECTION", out)

    def test_not_checked_on_the_manage_that_sent_the_order(self):
        self.set_stop(None)
        out = self.tick(0)
        self.assertEqual(kinds(self.fake), ["MARKET"])
        self.assertNotIn("PROTECTION", out)

    def test_missing_once_sends_one_replace_with_the_stored_stop(self):
        self.set_stop(None)
        out = self.tick(30)
        self.assertIn("event=PROTECTION_MISSING", out)
        self.assertEqual(kinds(self.fake), ["MARKET", "MODIFY"])
        modify = self.fake.commands[1]
        self.assertEqual((modify.position_ref, modify.stop), (self.ticket, self.sent_stop))
        self.assertIsNone(modify.target)
        self.assertTrue(self.leg()["protection_retry_done"])

    def test_still_missing_on_the_next_read_closes(self):
        self.set_stop(None)
        self.tick(30)
        out = self.tick(60)
        self.assertIn("event=PROTECTION_CLOSE", out)
        self.assertIn("status=ACCEPTED", out)
        self.assertEqual(kinds(self.fake), ["MARKET", "MODIFY", "CLOSE"])
        self.assertEqual(self.fake.commands[2].position_ref, self.ticket)
        self.assertEqual(self.fake.positions, [])
        self.tick(90)
        self.assertEqual(kinds(self.fake), ["MARKET", "MODIFY", "CLOSE"])
        self.assertEqual(self.leg()["status"], "done")

    def test_replace_shows_up_on_the_next_read_no_close(self):
        self.set_stop(None)
        self.tick(30)
        self.set_stop(self.sent_stop)
        out = self.tick(60)
        out += self.tick(90)
        self.assertEqual(kinds(self.fake), ["MARKET", "MODIFY"])
        self.assertNotIn("PROTECTION_CLOSE", out)
        self.assertEqual(len(self.fake.positions), 1)

    def test_never_more_than_one_replace_per_leg_across_restarts_and_failed_close(self):
        self.set_stop(None)
        self.tick(30)
        # The close is refused locally (stale quote): no second replace and no close loop afterwards.
        self.clock.at = AT + timedelta(seconds=60)
        SessionCombo(self.broker).tick(self.clock.at, self.bars)
        for seconds in (90, 120, 150):
            self.tick(seconds, combo=SessionCombo(self.broker))
        self.assertEqual(kinds(self.fake).count("MODIFY"), 1)
        self.assertEqual(kinds(self.fake).count("CLOSE"), 0)  # refused before sending; the time exit remains
        self.assertEqual(len(self.fake.positions), 1)
        leg = self.leg()
        self.assertTrue(leg["protection_retry_done"])
        self.assertTrue(leg["protection_close_done"])


if __name__ == "__main__":
    unittest.main()
