"""Ack UNKNOWN is not a skip: poll the fill, protect it, and exit London by the live ticket."""
from datetime import timedelta
from decimal import Decimal
import json
import logging
import unittest

from execution.exness_transport import CommandResult, FakeExnessTransport, Position
from execution.session_combo import SessionCombo, london_key
from test_session_combo import Clock, _markets, bar, ny, range_pair, world


class AckTransport(FakeExnessTransport):
    """MARKET answers HTTP-200 style: UNKNOWN, reason accepted. The position shows up on a later read."""

    def __init__(self, *args, appear_after=1, protect=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.appear_after = appear_after
        self.protect = protect
        self._held = None
        self._reads = 0

    def submit(self, command):
        if command.kind != "MARKET":
            return super().submit(command)
        prior = self._done.get(command.idempotency_key)
        if prior is not None:
            return prior
        self._seq += 1
        ref = str(self._seq)
        self._held = Position(ref, command.symbol, command.side, command.volume, command.price,
                              command.stop if self.protect else None,
                              command.target if self.protect else None,
                              command.idempotency_key, self._clock())
        result = CommandResult("UNKNOWN", command.idempotency_key, "op-not-a-ticket", "accepted")
        self._done[command.idempotency_key] = result
        self.commands.append(command)
        return result

    def read_state(self):
        if self._held is not None:
            self._reads += 1
            if self._reads >= self.appear_after:
                self.positions.append(self._held)
                self._held = None
        return super().read_state()


def london_bars(at):
    return range_pair(at.date()) + [bar(at, "150.18", "150.25", "150.12", "150.22")]


class AckFillTests(unittest.TestCase):
    def test_unknown_ack_polls_until_the_position_is_filled_without_size_skip(self):
        at = ny(2026, 1, 16, 2, 10)
        transport = AckTransport(clock=Clock(at), appear_after=3)
        clock, fake, _broker, combo = world(at, transport=transport)
        fake.push_tick(Decimal("150.19"), Decimal("150.20"), at)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(clock.at, london_bars(at))
        text = "\n".join(logs.output)
        self.assertNotIn("SIZE_SKIP", text)
        self.assertNotIn("unfilled", text)
        self.assertGreaterEqual(transport._reads, 3)
        self.assertEqual(len(_markets(fake)), 1)
        saved = json.loads(fake.get_flag(london_key(at.date())))
        self.assertEqual(saved["status"], "filled")
        self.assertEqual(saved["position_id"], fake.positions[0].ticket)
        self.assertNotEqual(saved["position_id"], "op-not-a-ticket")

    def test_unconfirmed_ack_stays_submitted_and_is_not_resent(self):
        at = ny(2026, 1, 16, 2, 10)
        transport = AckTransport(clock=Clock(at), appear_after=100)
        clock, fake, _broker, combo = world(at, transport=transport)
        fake.push_tick(Decimal("150.19"), Decimal("150.20"), at)
        bars = london_bars(at)
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(clock.at, bars)
        self.assertNotIn("SIZE_SKIP", "\n".join(logs.output))
        self.assertNotIn("unfilled", "\n".join(logs.output))
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "submitted")
        self.assertEqual(len(_markets(fake)), 1)
        clock.at = at + timedelta(minutes=5)
        fake.push_tick(Decimal("150.19"), Decimal("150.20"), clock.at)
        combo.tick(clock.at, bars)
        self.assertEqual(len(_markets(fake)), 1)
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "submitted")
        transport.appear_after = 0
        combo.tick(clock.at, bars)
        self.assertEqual(len(_markets(fake)), 1)
        saved = json.loads(fake.get_flag(london_key(at.date())))
        self.assertEqual(saved["status"], "filled")
        self.assertEqual(saved["position_id"], fake.positions[0].ticket)

    def test_sltp_is_placed_when_adopt_fails_and_the_position_has_no_stop(self):
        at = ny(2026, 1, 16, 2, 10)
        transport = AckTransport(clock=Clock(at), appear_after=1, protect=False)
        clock, fake, _broker, combo = world(at, transport=transport)
        fake.push_tick(Decimal("150.19"), Decimal("150.20"), at)

        def boom(*_args, **_kwargs):
            raise RuntimeError("adopt failed")

        combo._adopt = boom
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(clock.at, london_bars(at))
        self.assertNotIn("SIZE_SKIP", "\n".join(logs.output))
        market = _markets(fake)[0]
        modifies = [command for command in fake.commands if command.kind == "MODIFY"]
        self.assertEqual(len(modifies), 1)
        self.assertEqual(modifies[0].position_ref, fake.positions[0].ticket)
        self.assertEqual(modifies[0].stop, market.stop)
        self.assertEqual(modifies[0].target, market.target)
        self.assertEqual(json.loads(fake.get_flag(london_key(at.date())))["status"], "filled")

    def test_london_1100_closes_the_live_ticket_when_the_flag_has_no_position_id(self):
        at = ny(2026, 1, 16, 11, 0)
        clock, fake, broker, combo = world(at)
        fake.push_tick(Decimal("150.10"), Decimal("150.11"), at)
        fake.positions.append(Position("341984", "USDJPY", "SHORT", Decimal("0.02"), Decimal("150.00"),
                                       comment="combo-lon-2026-01-16-SHORT", opened_at=ny(2026, 1, 16, 2, 10)))
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(at, [])
        self.assertIn("reason=london_1100", "\n".join(logs.output))
        closes = [command for command in fake.commands if command.kind == "CLOSE"]
        self.assertEqual([command.position_ref for command in closes], ["341984"])
        self.assertEqual(fake.positions, [])
        saved = json.loads(fake.get_flag(london_key(at.date())))
        self.assertEqual(saved["status"], "done")
        self.assertEqual(saved["position_id"], "341984")
        SessionCombo(broker).tick(at, [])
        self.assertEqual([command.position_ref for command in fake.commands if command.kind == "CLOSE"], ["341984"])

    def test_london_1100_closes_by_ticket_when_adopt_raises(self):
        at = ny(2026, 1, 16, 11, 0)
        clock, fake, _broker, combo = world(at)
        fake.push_tick(Decimal("150.10"), Decimal("150.11"), at)
        fake.positions.append(Position("341984", "USDJPY", "SHORT", Decimal("0.02"), Decimal("150.00"),
                                       comment="combo-lon-2026-01-16-SHORT", opened_at=ny(2026, 1, 16, 2, 10)))
        fake.set_flag(london_key(at.date()), json.dumps({
            "status": "submitted", "client_id": "combo-lon-2026-01-16-SHORT", "exit_at": at.isoformat(),
            "exited": False, "symbol": "USDJPY", "side": "SHORT", "logged_range": True, "position_id": None,
            "volume": "0.02", "opened_at": ny(2026, 1, 16, 2, 10).isoformat()}))

        def boom(*_args, **_kwargs):
            raise RuntimeError("adopt failed")

        combo._adopt = boom
        with self.assertLogs("ai_floor.session_combo", level=logging.INFO) as logs:
            combo.tick(clock.at, [])
        self.assertIn("reason=london_1100", "\n".join(logs.output))
        self.assertEqual(fake.commands[-1].kind, "CLOSE")
        self.assertEqual(fake.commands[-1].position_ref, "341984")
        self.assertEqual(fake.positions, [])


if __name__ == "__main__":
    unittest.main()
