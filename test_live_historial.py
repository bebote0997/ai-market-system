"""Live panel path: flags in the Exness state DB, no stored curve, deals only in the cache the backfill writes."""
from datetime import datetime, timezone
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ui.liquid.page import load
from ui.liquid.render import fragment

NOW = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)

# Real Exness deals. Times are ET converted to UTC (EST).
DEALS = (
    ("341984", "USDJPY", "sell", "0.93", "159.15", "159.294", "2026-11-18T09:04:25+00:00", "2026-11-18T16:00:13+00:00", "-84.08"),
    ("357063", "USDJPY", "buy", "1.32", "159.628", "159.805", "2026-11-19T07:05:08+00:00", "2026-11-19T16:00:07+00:00", "146.19"),
    ("375978", "USDJPY", "sell", "0.6", "159.046", "158.738", "2026-11-20T07:32:23+00:00", "2026-11-20T16:00:08+00:00", "116.42"),
    ("382026", "GBPUSD", "buy", "1", "1.31016", "1.31007", "2026-11-20T16:00:13+00:00", "2026-11-20T16:05:17+00:00", "-9"),
    ("409759", "USDJPY", "buy", "1.71", "159.067", "159.422", "2026-11-23T08:02:04+00:00", "2026-11-23T16:00:03+00:00", "380.76"),
    ("415945", "GBPUSD", "buy", "1", "1.30193", "1.3018", "2026-11-23T16:00:09+00:00", "2026-11-23T16:31:11+00:00", "-13"),
    ("424576", "USDJPY", "buy", "0.77", "159.36", "159.071", "2026-11-24T07:00:08+00:00", "2026-11-24T16:00:03+00:00", "-139.89"),
    ("440549", "USDJPY", "sell", "1.19", "159.092", "159.119", "2026-11-25T07:30:39+00:00", "2026-11-25T16:07:49+00:00", "-20.19"),
    ("473902", "USDJPY", "buy", "1.88", "159.39", "159.544", "2026-11-27T07:38:54+00:00", "2026-11-27T16:00:08+00:00", "181.46"),
)

REASONS = {"382026": "hq_manual", "440549": "hq_manual"}


def _flag(ticket, symbol, side, lots, entry, exit_price, opened, closed, pnl):
    kind = "combo_lon_" if symbol == "USDJPY" else "combo_fix_"
    day = opened[:10]
    reason = REASONS.get(ticket, "london_1100" if symbol == "USDJPY" else "fix_30m")
    side_name = "SHORT" if side == "sell" else "LONG"
    return kind + day, json.dumps({
        "status": "done", "exited": True, "symbol": symbol, "side": side_name,
        "position_id": ticket, "volume": lots, "entry": entry, "exit_price": exit_price,
        "pnl": pnl, "exit_reason": reason, "opened_at": opened, "exit_at": closed,
    })


def _deal(ticket, symbol, side, lots, entry, exit_price, opened, closed, pnl):
    return [
        {"type": "open", "position_id": ticket, "instrument": symbol, "direction": side,
         "volume": lots, "price": entry, "profit": {"amount": "0", "currency": "USC"},
         "create_time": opened},
        {"type": "close", "position_id": ticket, "instrument": symbol,
         "direction": "buy" if side == "sell" else "sell",
         "volume": lots, "price": exit_price, "profit": {"amount": pnl, "currency": "USC"},
         "create_time": closed},
    ]


def _store(path, rows):
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE flags (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    con.executemany("INSERT INTO flags(key, value) VALUES(?, ?)", rows)
    con.commit()
    con.close()


class LiveEightRowsTests(unittest.TestCase):
    def _view(self, deals):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        state = Path(folder.name) / "exness_state.db"
        flags = []
        cached = []
        for item in DEALS:
            if item[0] != "440549":
                key, raw = _flag(*item)
                flags.append((key, raw))
            cached.extend(_deal(*item))
        # The live 25 Nov flag is present and is not a shown row. No ticket, no P/L, no opened_at.
        flags.append(("combo_lon_2026-11-25", json.dumps({
            "status": "done", "exited": False,
        })))
        flags.append(("exness_brake_start", "30086"))
        flags.append(("exness_launch_start", "2026-11-16T21:00:00+00:00"))
        flags.append(("exness_closed_deals", json.dumps(cached)))
        _store(state, flags)
        env = {
            "AI_FLOOR_BROKER": "exness",
            "AI_FLOOR_EXNESS_STATE_DB_PATH": str(state),
            "AI_FLOOR_DB_PATH": str(Path(folder.name) / "missing.db"),
        }
        return load(env=env, now=NOW)

    def test_the_live_shape_shows_nine_rows_and_a_sized_chart(self):
        view = self._view(DEALS)
        rows = view["history"]["rows"]
        tickets = [row["ticket"] for row in rows]
        self.assertEqual(tickets.count("440549"), 1)
        self.assertEqual(len(rows), 9)
        self.assertEqual(len(set(tickets)), 9)
        self.assertIn("558.67", view["history"]["stats"]["net"])
        nov25 = next(row for row in rows if row["ticket"] == "440549")
        self.assertEqual(nov25["reason"], "cierre manual HQ")
        self.assertEqual(nov25["entry"], "159.092")
        self.assertEqual(nov25["exit_price"], "159.119")
        points = view["curve"]["ranges"]["30"]["points"]
        self.assertGreaterEqual(len(points), 2)
        html = fragment(view, section="Historial")
        self.assertIn('height="220"', html)
        self.assertIn("<svg", html)
        self.assertNotIn('height=""', html)

    def test_a_flagged_ticket_is_not_added_twice(self):
        view = self._view(DEALS)
        tickets = [row["ticket"] for row in view["history"]["rows"]]
        self.assertEqual(tickets.count("341984"), 1)
        self.assertEqual(tickets.count("473902"), 1)
        self.assertEqual(len(tickets), len(set(tickets)))
