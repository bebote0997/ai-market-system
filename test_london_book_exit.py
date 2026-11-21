"""11:00 ET London exit that matches the broker book when the comment and the stored ticket did not close."""
from decimal import Decimal
import json
import logging
import unittest

from execution.exness_client import ExnessClient
from execution.exness_transport import CommandResult, FakeExnessTransport, Position
from execution.session_combo import SessionCombo, fix_key, london_key
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


class RejectedClose(FakeExnessTransport):
    def submit(self, command):
        if command.kind == "CLOSE":
            self.commands.append(command)
            return CommandResult("REJECTED", command.idempotency_key, None, "rejected")
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
        """A first 202 leaves the stored ticket open. The 03:00 manual USDJPY stays open, and there is no HIGH alert yet."""
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
        self.assertNotIn("EXNESS_EXIT_FAILED", text)
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
        self.assertNotIn("EXNESS_EXIT_FAILED", "\n".join(logs.output))
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

    def test_a_202_that_closes_a_moment_later_raises_no_high_and_ends_done(self):
        at = ny(2026, 1, 16, 11, 0)
        clock, fake, broker, combo = world(at, transport=UnknownClose())
        alerts = []
        broker._on_alert = lambda kind, detail: alerts.append(kind)
        opened = ny(2026, 1, 16, 2, 10)
        fake.positions = [Position("stored", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                   Decimal("149.50"), None, "", opened)]
        fake.set_flag(london_key(at.date()), _flag(at.date(), position_id="stored", opened_at=opened.isoformat()))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, [])
        self.assertNotIn("EXNESS_EXIT_FAILED", "\n".join(logs.output))
        self.assertNotIn("EXNESS_LONDON_BOOK", "\n".join(logs.output))
        self.assertEqual(alerts, [])
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "filled")
        fake.positions = []
        clock.at = ny(2026, 1, 16, 11, 1)
        fake.push_tick(Decimal("150.30"), Decimal("150.31"), clock.at)
        combo.tick(clock.at, [])
        self.assertEqual(alerts, [])
        self.assertEqual([command.position_ref for command in fake.commands if command.kind == "CLOSE"], ["stored"])
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "done")

    def test_a_failed_close_keeps_alerting_and_sending_until_1300(self):
        for transport in (RejectedClose, UnknownClose):
            with self.subTest(transport=transport.__name__):
                at = ny(2026, 1, 16, 11, 0)
                clock, fake, broker, combo = world(at, transport=transport())
                alerts = []
                broker._on_alert = lambda kind, detail: alerts.append(kind)
                opened = ny(2026, 1, 16, 2, 10)
                fake.positions = [Position("stored", "USDJPY", "LONG", Decimal("0.01"), Decimal("150.20"),
                                           Decimal("149.50"), None, "", opened)]
                fake.set_flag(london_key(at.date()), _flag(at.date(), position_id="stored",
                                                           opened_at=opened.isoformat()))
                for hour in (11, 12, 13):
                    clock.at = ny(2026, 1, 16, hour, 0)
                    fake.push_tick(Decimal("150.30"), Decimal("150.31"), clock.at)
                    combo.tick(clock.at, [])
                closes = [command for command in fake.commands if command.kind == "CLOSE"]
                self.assertGreater(len(closes), 1)
                self.assertTrue(all(command.position_ref == "stored" for command in closes))
                self.assertIn("EXNESS_EXIT_FAILED", alerts)
                self.assertNotIn("EXNESS_LONDON_BOOK", alerts)
                self.assertEqual([row.ticket for row in fake.positions], ["stored"])

    def test_a_fix_ticket_in_flight_does_not_block_the_london_fallback(self):
        at = ny(2026, 1, 16, 11, 0)
        _clock, fake, broker, combo = world(at)

        def submit(command):
            if command.kind == "CLOSE" and command.position_ref == "fix":
                fake.commands.append(command)
                return CommandResult("REJECTED", command.idempotency_key, None, "rejected")
            return FakeExnessTransport.submit(fake, command)

        fake.submit = submit
        fake.positions = [_pos("early", ny(2026, 1, 16, 2, 10)),
                          Position("fix", "GBPUSD", "SHORT", Decimal("0.01"), Decimal("1.25000"),
                                   comment="", opened_at=ny(2026, 1, 16, 10, 0))]
        fake.set_flag(london_key(at.date()), _flag(at.date()))
        fake.set_flag(fix_key(at.date()), json.dumps({
            "status": "filled", "exited": False, "symbol": "GBPUSD", "side": "SHORT",
            "position_id": "fix", "client_id": "combo-fix-2026-01-16-SHORT",
            "exit_at": at.isoformat(), "opened_at": ny(2026, 1, 16, 10, 0).isoformat()}))
        combo.tick(at, [])
        closed = [command.position_ref for command in fake.commands if command.kind == "CLOSE"]
        self.assertIn("fix", closed)
        self.assertIn("early", closed)
        self.assertEqual([row.ticket for row in fake.positions], ["fix"])


class CloseRetryTests(unittest.TestCase):
    def _client(self, caller):
        now = [1_000_000]

        def sleep(seconds):
            now[0] += int(seconds * 1000) + 1

        return ExnessClient(
            {"EXN_API_KEY": "exnsk-public-key-value", "EXN_ACCOUNT_ID": "123456",
             "EXN_DISCOVERY_BASE_URL": "https://gateway.example"},
            caller=caller, sign=lambda data: b"sig", now_ms=lambda: now[0], sleep=sleep)

    def test_a_rejected_close_is_sent_again_and_an_unknown_that_closed_is_not(self):
        positions = [{"position_id": "stored", "state": "open"}]
        deletes = []

        def caller(method, url, headers, body):
            del headers, body
            if "access-point" in url:
                return 200, {"access_point": "https://ap.example"}, {}
            if method == "GET" and url.endswith("/snapshot"):
                return 200, {"positions": list(positions)}, {}
            if method == "DELETE":
                deletes.append(url)
                return 400, {"error_message": "REJECTED"}, {}
            return 200, {}, {}

        client = self._client(caller)
        path = "/v1/trading/accounts/123456/positions/stored"
        client.mutate("DELETE", path, None, "close-stored-exit")
        client.mutate("DELETE", path, None, "close-stored-exit")
        self.assertEqual(len(deletes), 2)

        deletes.clear()
        positions.clear()
        unknown = {"n": 0}

        def caller_unknown(method, url, headers, body):
            del headers, body
            if "access-point" in url:
                return 200, {"access_point": "https://ap.example"}, {}
            if method == "GET" and url.endswith("/snapshot"):
                return 200, {"positions": list(positions)}, {}
            if method == "DELETE":
                deletes.append(url)
                unknown["n"] += 1
                if unknown["n"] == 1:
                    positions.clear()
                return 202, {"operation_id": "1", "status": "accepted"}, {}
            return 200, {}, {}

        client = self._client(caller_unknown)
        client.mutate("DELETE", path, None, "close-stored-exit")
        client.mutate("DELETE", path, None, "close-stored-exit")
        self.assertEqual(len(deletes), 1)
        self.assertTrue(deletes[0].endswith("/positions/stored"))

        deletes.clear()
        positions.append({"position_id": "stored", "state": "open"})
        unknown["n"] = 0

        def caller_still_open(method, url, headers, body):
            del headers, body
            if "access-point" in url:
                return 200, {"access_point": "https://ap.example"}, {}
            if method == "GET" and url.endswith("/snapshot"):
                return 200, {"positions": list(positions)}, {}
            if method == "DELETE" and "?volume=" in url:
                deletes.append(url)
                return 202, {"operation_id": "partial", "status": "accepted"}, {}
            if method == "DELETE":
                deletes.append(url)
                return 202, {"operation_id": "2", "status": "accepted"}, {}
            return 200, {}, {}

        client = self._client(caller_still_open)
        client.mutate("DELETE", path, None, "close-stored-exit")
        client.mutate("DELETE", path, None, "close-stored-exit")
        full = [url for url in deletes if "?volume=" not in url]
        self.assertEqual(len(full), 2)
        deletes.clear()
        partial = path + "?volume=0.01"
        client.mutate("DELETE", partial, None, "close-stored-partial")
        client.mutate("DELETE", partial, None, "close-stored-partial")
        self.assertEqual(len(deletes), 1)
        self.assertIn("?volume=", deletes[0])


if __name__ == "__main__":
    unittest.main()
