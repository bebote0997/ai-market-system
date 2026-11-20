"""Follow-ups on the session combo: pauses, 202 closes, persisted sltp, and protection checks."""
from datetime import timedelta
from decimal import Decimal
import json
import logging
import unittest

from execution.exness_transport import CommandResult, Position, WorkingOrder
from execution.session_combo import SessionCombo, _FILL_PAUSE_S, london_key
from test_session_combo import ny, world


def _quiet(combo):
    combo._sleep = lambda _seconds: None
    return combo


class PauseTests(unittest.TestCase):
    def test_await_held_pauses_between_reads(self):
        at = ny(2026, 1, 16, 2, 10)
        _clock, _fake, _broker, combo = world(at)
        pauses = []
        combo._sleep = pauses.append
        seen = {"n": 0}

        def locate(_state, fresh=False):
            seen["n"] += 1
            self.assertTrue(fresh)
            if seen["n"] < 3:
                return "unknown", None
            return "held", Position("7", "USDJPY", "LONG", Decimal("0.01"), Decimal("150"), comment="c")

        combo._locate = locate
        kind, row = combo._await_held({"position_id": None, "client_id": "combo-lon-2026-01-16-LONG"})
        self.assertEqual((kind, row.ticket), ("held", "7"))
        self.assertEqual(pauses, list(_FILL_PAUSE_S[:2]))


class CloseGuardTests(unittest.TestCase):
    def test_no_close_without_the_london_comment_or_before_1100(self):
        at = ny(2026, 1, 16, 10, 59)
        _clock, fake, _broker, combo = world(at)
        fake.positions.append(Position("1", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                       Decimal("149.80"), Decimal("151.00"),
                                       comment="combo-lon-2026-01-16-LONG", opened_at=ny(2026, 1, 16, 2, 10)))
        fake.set_flag(london_key(at.date()), json.dumps({
            "status": "filled", "client_id": "combo-lon-2026-01-16-LONG",
            "exit_at": ny(2026, 1, 16, 11, 0).isoformat(), "exited": False,
            "symbol": "USDJPY", "side": "LONG", "position_id": "1", "stop": "149.80", "target": "151.00",
            "opened_at": ny(2026, 1, 16, 2, 10).isoformat()}))
        combo.tick(at, [])
        self.assertEqual([c for c in fake.commands if c.kind == "CLOSE"], [])
        other = ny(2026, 1, 16, 11, 0)
        fake.positions.append(Position("2", "EURUSD", "LONG", Decimal("0.01"), Decimal("1.10"),
                                       comment="manual-note", opened_at=other))
        combo.tick(other, [])
        closed = [c.position_ref for c in fake.commands if c.kind == "CLOSE"]
        self.assertEqual(closed, ["1"])
        self.assertTrue(any(p.ticket == "2" for p in fake.positions))


class ProtectionTests(unittest.TestCase):
    def test_no_sltp_when_the_position_already_has_a_stop(self):
        at = ny(2026, 1, 16, 2, 20)
        _clock, fake, _broker, combo = world(at)
        fake.positions.append(Position("4", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                       Decimal("149.80"), Decimal("151.00"),
                                       comment="combo-lon-2026-01-16-LONG", opened_at=at))
        fake.set_flag(london_key(at.date()), json.dumps({
            "status": "filled", "client_id": "combo-lon-2026-01-16-LONG",
            "exit_at": ny(2026, 1, 16, 11, 0).isoformat(), "exited": False,
            "symbol": "USDJPY", "side": "LONG", "position_id": "4", "stop": "149.80", "target": "151.00",
            "opened_at": at.isoformat()}))
        combo.tick(at, [])
        self.assertEqual([c for c in fake.commands if c.kind == "MODIFY"], [])

    def test_sltp_guard_survives_a_restart(self):
        at = ny(2026, 1, 16, 2, 20)
        _clock, fake, broker, combo = world(at)
        fake.positions.append(Position("4", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                       comment="combo-lon-2026-01-16-LONG", opened_at=at))
        fake.set_flag(london_key(at.date()), json.dumps({
            "status": "filled", "client_id": "combo-lon-2026-01-16-LONG",
            "exit_at": ny(2026, 1, 16, 11, 0).isoformat(), "exited": False,
            "symbol": "USDJPY", "side": "LONG", "position_id": "4", "stop": "149.80", "target": "151.00",
            "opened_at": at.isoformat(), "volume": "0.01"}))
        combo.tick(at, [])
        self.assertEqual(len([c for c in fake.commands if c.kind == "MODIFY"]), 1)
        self.assertEqual(fake.get_flag("sltp_sent_4"), "1")
        held = fake.positions[0]
        fake.positions[0] = Position(held.ticket, held.symbol, held.side, held.volume, held.price,
                                     Decimal("149.80"), Decimal("151.00"), held.comment, held.opened_at)
        later = at + timedelta(minutes=30)
        restarted = _quiet(SessionCombo(broker))
        restarted.tick(later, [])
        self.assertEqual(len([c for c in fake.commands if c.kind == "MODIFY"]), 1)

    def test_one_replace_then_one_close_when_the_stop_never_appears(self):
        """PR #96, adapted: the send manage does not re-check; the next one replaces once; the one after closes."""
        sent = ny(2026, 1, 16, 2, 10)
        _clock, fake, _broker, combo = world(sent)
        fake.positions.append(Position("4", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                       comment="combo-lon-2026-01-16-LONG", opened_at=sent))
        fake.set_flag(london_key(sent.date()), json.dumps({
            "status": "filled", "client_id": "combo-lon-2026-01-16-LONG",
            "exit_at": ny(2026, 1, 16, 11, 0).isoformat(), "exited": False,
            "symbol": "USDJPY", "side": "LONG", "position_id": "4", "stop": "149.80", "target": "151.00",
            "opened_at": sent.isoformat(), "sltp_sent": "4",
            "protection_after": sent.isoformat(), "protection_retry_done": False}))
        fake.set_flag("sltp_sent_4", "1")
        same = sent
        combo.tick(same, [])
        self.assertEqual([c.kind for c in fake.commands], [])
        nxt = sent + timedelta(minutes=30)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(nxt, [])
        self.assertIn("PROTECTION_REPLACE", "\n".join(logs.output))
        self.assertEqual([c.kind for c in fake.commands], ["MODIFY"])
        after = nxt + timedelta(minutes=30)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(after, [])
        self.assertIn("PROTECTION_CLOSE", "\n".join(logs.output))
        self.assertIn("status=closed", "\n".join(logs.output))
        self.assertEqual([c.kind for c in fake.commands], ["MODIFY", "CLOSE"])
        self.assertEqual(json.loads(fake.get_flag(london_key(sent.date())))["status"], "done")

    def test_missing_orders_never_closes_and_two_valid_reads_do(self):
        sent = ny(2026, 1, 16, 2, 10)
        _clock, fake, _broker, combo = world(sent)
        fake.orders_present = False
        fake.positions.append(Position("4", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                       comment="combo-lon-2026-01-16-LONG", opened_at=sent))
        fake.set_flag(london_key(sent.date()), json.dumps({
            "status": "filled", "position_id": "4", "stop": "149.80", "target": "151.00",
            "symbol": "USDJPY", "side": "LONG", "exited": False, "sltp_sent": "4",
            "protection_after": sent.isoformat(), "protection_retry_done": True,
            "exit_at": ny(2026, 1, 16, 11, 0).isoformat(),
            "opened_at": sent.isoformat()}))
        fake.set_flag("sltp_sent_4", "1")
        combo.tick(sent + timedelta(minutes=30), [])
        combo.tick(sent + timedelta(minutes=60), [])
        self.assertEqual([c.kind for c in fake.commands if c.kind == "CLOSE"], [])
        fake.orders_present = True
        combo.tick(sent + timedelta(minutes=90), [])
        self.assertEqual([c.kind for c in fake.commands if c.kind == "CLOSE"], [])
        combo.tick(sent + timedelta(minutes=120), [])
        self.assertEqual([c.position_ref for c in fake.commands if c.kind == "CLOSE"], ["4"])

    def test_replace_cancels_existing_sltp_orders_before_sending_another(self):
        at = ny(2026, 1, 16, 2, 10)
        _clock, fake, broker, _combo = world(at)
        fake.positions.append(Position("4", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.200"),
                                       comment="combo-lon-2026-01-16-LONG"))
        fake.orders.append(WorkingOrder("a", "USDJPY", "LONG", Decimal("0.01"), Decimal("149.8"),
                                        kind="sltp", position_id="4"))
        fake.orders.append(WorkingOrder("b", "USDJPY", "LONG", Decimal("0.01"), Decimal("149.8"),
                                        kind="sltp", position_id="4"))
        result = broker.replace_protection("4", Decimal("149.80"), Decimal("151.00"), client_id="sltp-4-2")
        self.assertNotEqual(result.status, "REJECTED")
        kinds = [c.kind for c in fake.commands]
        self.assertEqual(kinds[:2], ["CANCEL", "CANCEL"])
        self.assertIn("MODIFY", kinds)
        self.assertEqual([o.ticket for o in fake.orders if o.kind == "sltp"], [])


class CloseAckTests(unittest.TestCase):
    def test_http_202_close_is_closed_once_the_position_is_gone(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, _broker, combo = world(at)

        def submit(command):
            if command.kind == "CLOSE":
                fake.positions = [p for p in fake.positions if p.ticket != command.position_ref]
                result = CommandResult("UNKNOWN", command.idempotency_key, None, "accepted")
                fake.commands.append(command)
                return result
            return type(fake).submit(fake, command)

        fake.submit = submit
        fake.positions.append(Position("9", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                       Decimal("149.80"), comment="combo-lon-2026-01-16-LONG",
                                       opened_at=ny(2026, 1, 16, 2, 10)))
        fake.set_flag(london_key(at.date()), json.dumps({
            "status": "filled", "client_id": "combo-lon-2026-01-16-LONG",
            "exit_at": at.isoformat(), "exited": False, "symbol": "USDJPY", "side": "LONG",
            "position_id": "9", "stop": "149.80", "opened_at": ny(2026, 1, 16, 2, 10).isoformat()}))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, [])
        text = "\n".join(logs.output)
        self.assertIn("reason=london_1100", text)
        self.assertIn("status=closed", text)
        self.assertNotIn("status=UNKNOWN", text)
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "done")

    def test_unknown_close_stays_open_when_the_reread_fails(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, broker, combo = world(at)
        fake.positions.append(Position("9", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                       Decimal("149.80"), comment="combo-lon-2026-01-16-LONG",
                                       opened_at=ny(2026, 1, 16, 2, 10)))
        real = broker._read
        calls = {"n": 0}

        def flaky(*, fresh=False):
            calls["n"] += 1
            if calls["n"] > 1 and fresh:
                raise RuntimeError("reread failed")
            return real(fresh=fresh)

        broker._read = flaky

        def submit(command):
            if command.kind == "CLOSE":
                result = CommandResult("UNKNOWN", command.idempotency_key, None, "accepted")
                fake.commands.append(command)
                return result
            return type(fake).submit(fake, command)

        fake.submit = submit
        fake.set_flag(london_key(at.date()), json.dumps({
            "status": "filled", "client_id": "combo-lon-2026-01-16-LONG",
            "exit_at": at.isoformat(), "exited": False, "symbol": "USDJPY", "side": "LONG",
            "position_id": "9", "stop": "149.80", "opened_at": ny(2026, 1, 16, 2, 10).isoformat()}))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, [])
        self.assertIn("status=UNKNOWN", "\n".join(logs.output))
        self.assertNotEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "done")


class UnreadSendTests(unittest.TestCase):
    def test_pre_send_read_failure_is_unknown_and_does_not_skip_the_day(self):
        at = ny(2026, 1, 16, 2, 10)
        _clock, fake, broker, combo = world(at)
        broker._read = lambda *, fresh=False: (_ for _ in ()).throw(RuntimeError("book unread"))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            sent, lots = combo._send(at.date(), "USDJPY", "LONG", Decimal("150.20"), Decimal("149.80"),
                                     Decimal("151.00"), "combo-lon-2026-01-16-LONG")
        self.assertEqual((sent.status, sent.reason, lots), ("UNKNOWN", "book_unread", None))
        self.assertIn("status=UNKNOWN", "\n".join(logs.output))
        self.assertNotIn("SIZE_SKIP", "\n".join(logs.output))
        state = {"status": "pending", "client_id": None, "exit_at": None, "exited": False, "symbol": None,
                 "side": None, "logged_range": True, "position_id": None, "volume": None, "opened_at": None,
                 "stop": None, "target": None, "sltp_sent": None, "protection_after": None,
                 "protection_retry_done": False, "protection_close_done": False}
        self.assertFalse(combo._commit(london_key(at.date()), state, at.date(), "USDJPY", "LONG",
                                       Decimal("150.20"), Decimal("149.80"), Decimal("151.00"),
                                       "combo-lon-2026-01-16-LONG", ny(2026, 1, 16, 11, 0), at))
        self.assertEqual(state["status"], "armed")
        self.assertEqual([c for c in fake.commands if c.kind == "MARKET"], [])


class PendingAndRoundingTests(unittest.TestCase):
    def test_leftover_sltp_orders_are_not_pending_entries(self):
        at = ny(2026, 1, 16, 2, 10)
        _clock, fake, _broker, combo = world(at)
        fake.orders.append(WorkingOrder("s", "USDJPY", "LONG", Decimal("0.01"), Decimal("150"),
                                        kind="sltp"))
        self.assertFalse(combo._position_open())
        fake.orders.append(WorkingOrder("L", "USDJPY", "LONG", Decimal("0.01"), Decimal("150"),
                                        kind="limit"))
        self.assertTrue(combo._position_open())

    def test_replace_protection_rejects_the_wrong_side_and_rounds(self):
        at = ny(2026, 1, 16, 2, 10)
        _clock, fake, broker, _combo = world(at)
        fake.positions.append(Position("4", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.200"),
                                       comment="combo-lon-2026-01-16-LONG"))
        wrong = broker.replace_protection("4", Decimal("150.20"), Decimal("151"), client_id="sltp-4-1")
        self.assertEqual(wrong.reason, "stop_side")
        ok = broker.replace_protection("4", Decimal("149.80123"), Decimal("151.00987"), client_id="sltp-4-2")
        self.assertNotEqual(ok.status, "REJECTED")
        sent = [c for c in fake.commands if c.kind == "MODIFY"][-1]
        self.assertEqual((sent.stop, sent.target), (Decimal("149.801"), Decimal("151.010")))


if __name__ == "__main__":
    unittest.main()
