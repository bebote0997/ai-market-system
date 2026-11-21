"""11:00 ET London exit that matches the broker book when the comment and the stored ticket did not close."""
from decimal import Decimal
import json
import logging
import unittest

from execution.exness_transport import CommandResult, FakeExnessTransport, Position
from execution.session_combo import SessionCombo, london_key
from test_session_combo import ny, world


def _flag(day, **extra):
    payload = {"status": "filled", "exited": False, "symbol": "USDJPY", "side": "LONG", "position_id": None,
               "client_id": None, "volume": "9.99", "logged_range": True,
               "exit_at": ny(day.year, day.month, day.day, 11, 0).isoformat(),
               "opened_at": ny(day.year, day.month, day.day, 2, 10).isoformat()}
    payload.update(extra)
    return json.dumps(payload)


def _pos(ticket, at, side="LONG", symbol="USDJPY"):
    return Position(ticket, symbol, side, Decimal("0.01"), Decimal("150.20"), comment="", opened_at=at)


class UnknownClose(FakeExnessTransport):
    def submit(self, command):
        if command.kind == "CLOSE":
            self.commands.append(command)
            return CommandResult("UNKNOWN", command.idempotency_key, command.position_ref, "accepted")
        return super().submit(command)


class LondonBookExitTests(unittest.TestCase):
    def test_book_closes_only_the_london_window_ticket(self):
        at = ny(2026, 1, 16, 10, 59)
        clock, fake, broker, combo = world(at)
        fake.positions = [_pos("early", ny(2026, 1, 16, 2, 10)),
                          _pos("late", ny(2026, 1, 16, 9, 0)),
                          _pos("other", ny(2026, 1, 16, 2, 12), symbol="EURUSD"),
                          _pos("short", ny(2026, 1, 16, 2, 20), side="SHORT")]
        fake.set_flag(london_key(at.date()), _flag(at.date()))
        combo.tick(at, [])
        self.assertEqual(fake.commands, [])
        clock.at = ny(2026, 1, 16, 11, 0)
        fake.push_tick(Decimal("150.30"), Decimal("150.31"), clock.at)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            SessionCombo(broker).tick(clock.at, [])
        closes = [command for command in fake.commands if command.kind == "CLOSE"]
        self.assertEqual([command.position_ref for command in closes], ["early"])
        self.assertIn("reason=london_1100", "\n".join(logs.output))
        self.assertEqual({row.ticket for row in fake.positions}, {"late", "other", "short"})
        saved = json.loads(fake.get_flag(london_key(at.date())))
        self.assertEqual(saved["status"], "done")
        self.assertTrue(saved["exited"])
        self.assertEqual(saved["position_id"], "early")

    def test_a_done_day_closes_nothing(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, broker, combo = world(at)
        alerts = []
        broker._on_alert = lambda kind, detail: alerts.append(kind)
        fake.positions = [_pos("live", ny(2026, 1, 16, 2, 10))]
        fake.set_flag(london_key(at.date()), _flag(at.date(), status="done", exited=True, position_id="already"))
        combo.tick(at, [])
        self.assertEqual(fake.commands, [])
        self.assertEqual(alerts, [])
        self.assertEqual([row.ticket for row in fake.positions], ["live"])

    def test_an_sltp_link_inside_the_window_closes_past_two_minutes(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, broker, combo = world(at)
        fake.positions = [_pos("linked", ny(2026, 1, 16, 2, 30))]
        fake.sltp_ids = (("sltp-1", "linked"),)
        fake.set_flag(london_key(at.date()), _flag(at.date()))
        combo.tick(at, [])
        self.assertEqual([command.position_ref for command in fake.commands if command.kind == "CLOSE"], ["linked"])

    def test_two_window_matches_alert_and_do_not_close(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, broker, combo = world(at)
        alerts = []
        broker._on_alert = lambda kind, detail: alerts.append((kind, detail.get("severity")))
        fake.positions = [_pos("a", ny(2026, 1, 16, 2, 10)), _pos("b", ny(2026, 1, 16, 2, 11))]
        fake.set_flag(london_key(at.date()), _flag(at.date()))
        combo.tick(at, [])
        self.assertEqual(fake.commands, [])
        self.assertEqual(alerts, [("EXNESS_LONDON_BOOK", "HIGH")])
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "filled")

    def test_a_manual_position_with_a_stop_outside_the_window_does_not_close(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, broker, combo = world(at)
        alerts = []
        broker._on_alert = lambda kind, detail: alerts.append((kind, detail.get("severity")))
        fake.positions = [Position("manual", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                   Decimal("149.50"), None, "", ny(2026, 1, 16, 9, 0))]
        fake.set_flag(london_key(at.date()), _flag(at.date()))
        combo.tick(at, [])
        self.assertEqual([command for command in fake.commands if command.kind == "CLOSE"], [])
        self.assertEqual([row.ticket for row in fake.positions], ["manual"])
        self.assertEqual(alerts, [("EXNESS_LONDON_BOOK", "HIGH")])
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "filled")

    def test_a_finished_day_does_not_close_a_later_position(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, broker, combo = world(at)
        fake.positions = [_pos("manual", ny(2026, 1, 16, 4, 30))]
        fake.set_flag(london_key(at.date()), _flag(at.date(), status="done", exited=True, position_id="already-closed"))
        combo.tick(at, [])
        self.assertEqual(fake.commands, [])
        self.assertEqual([row.ticket for row in fake.positions], ["manual"])

    def test_saturday_does_not_close(self):
        at = ny(2026, 1, 17, 11, 0)
        self.assertEqual(at.weekday(), 5)
        _clock, fake, broker, combo = world(at)
        fake.positions = [_pos("early", ny(2026, 1, 17, 2, 10))]
        fake.set_flag(london_key(at.date()), _flag(at.date()))
        combo.tick(at, [])
        self.assertEqual(fake.commands, [])

    def test_unknown_close_of_the_stored_ticket_does_not_close_a_manual_position(self):
        """A 202 on the stored ticket leaves it in the book. The 03:00 manual USDJPY stays open, with no HIGH alert."""
        at = ny(2026, 1, 16, 11, 0)
        opened = ny(2026, 1, 16, 3, 0)
        _clock, fake, broker, combo = world(at, transport=UnknownClose())
        alerts = []
        broker._on_alert = lambda kind, detail: alerts.append((kind, detail.get("severity")))
        fake.positions = [
            Position("stored", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                     Decimal("149.50"), None, "", opened),
            Position("manual", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.40"),
                     Decimal("149.80"), None, "", opened),
        ]
        fake.set_flag(london_key(at.date()), _flag(at.date(), position_id="stored", opened_at=opened.isoformat()))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, [])
        text = "\n".join(logs.output)
        self.assertNotIn("EXNESS_LONDON_BOOK", text)
        self.assertEqual(alerts, [])
        self.assertEqual([command.position_ref for command in fake.commands if command.kind == "CLOSE"], ["stored"])
        self.assertEqual({row.ticket for row in fake.positions}, {"stored", "manual"})
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "filled")

    def test_unknown_close_of_the_only_ticket_does_not_raise_a_high_alert(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, broker, combo = world(at, transport=UnknownClose())
        alerts = []
        broker._on_alert = lambda kind, detail: alerts.append(kind)
        fake.positions = [Position("stored", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                   Decimal("149.50"), None, "", ny(2026, 1, 16, 3, 0))]
        fake.set_flag(london_key(at.date()), _flag(
            at.date(), position_id="stored", opened_at=ny(2026, 1, 16, 3, 0).isoformat()))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, [])
        self.assertNotIn("EXNESS_LONDON_BOOK", "\n".join(logs.output))
        self.assertEqual(alerts, [])
        self.assertEqual([command.position_ref for command in fake.commands if command.kind == "CLOSE"], ["stored"])
        self.assertEqual([row.ticket for row in fake.positions], ["stored"])
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "filled")

    def test_unknown_close_leaves_the_position_open(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, broker, combo = world(at, transport=UnknownClose())
        fake.positions = [_pos("early", ny(2026, 1, 16, 2, 10)), _pos("late", ny(2026, 1, 16, 9, 0))]
        fake.set_flag(london_key(at.date()), _flag(at.date()))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, [])
        text = "\n".join(logs.output)
        self.assertIn("reason=london_1100", text)
        self.assertIn("status=UNKNOWN", text)
        self.assertEqual([command.position_ref for command in fake.commands if command.kind == "CLOSE"], ["early"])
        self.assertEqual({row.ticket for row in fake.positions}, {"early", "late"})
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "filled")


if __name__ == "__main__":
    unittest.main()
