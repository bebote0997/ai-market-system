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

# Closed 17 Nov 11:50:50 AM ET, before HISTORY_START (18 Nov). Not a table row.
PRIOR = ("332845", "EURUSD", "buy", "0.01", "1.10152", "1.10145",
         "2026-11-17T16:50:47+00:00", "2026-11-17T16:50:50+00:00", "-0.07")
NOTE = "Incluye −0.07 USC del 17 nov (EURUSD), anterior al historial"


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
        cached.extend(_deal(*PRIOR))
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
        self.assertIn("data:image/svg+xml;base64,", html)
        self.assertIn('class="equity on-light"', html)
        self.assertIn('class="equity on-dark"', html)
        self.assertIsNone(__import__("re").search(r"<svg\b", html))
        self.assertNotIn('height=""', html)

    def test_a_flagged_ticket_is_not_added_twice(self):
        view = self._view(DEALS)
        tickets = [row["ticket"] for row in view["history"]["rows"]]
        self.assertEqual(tickets.count("341984"), 1)
        self.assertEqual(tickets.count("473902"), 1)
        self.assertEqual(len(tickets), len(set(tickets)))

    def test_pl_total_note_includes_the_nov17_eurusd(self):
        view = self._view(DEALS)
        self.assertEqual(view["kpis"]["total"]["note"], NOTE)
        html = fragment(view, section="Historial")
        self.assertIn(NOTE, html)
        tickets = [row["ticket"] for row in view["history"]["rows"]]
        self.assertNotIn("332845", tickets)
        self.assertEqual(len(tickets), 9)

    def test_pl_total_note_is_hidden_when_prior_pnl_is_zero(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        state = Path(folder.name) / "exness_state.db"
        flags = [_flag(*item) for item in DEALS if item[0] != "440549"]
        cached = []
        for item in DEALS:
            cached.extend(_deal(*item))
        flags.append(("combo_lon_2026-11-25", json.dumps({"status": "done", "exited": False})))
        flags.append(("exness_brake_start", "30086"))
        flags.append(("exness_closed_deals", json.dumps(cached)))
        _store(state, flags)
        env = {
            "AI_FLOOR_BROKER": "exness",
            "AI_FLOOR_EXNESS_STATE_DB_PATH": str(state),
            "AI_FLOOR_DB_PATH": str(Path(folder.name) / "missing.db"),
        }
        from ui.liquid.page import load
        view = load(env=env, now=NOW)
        self.assertIsNone(view["kpis"]["total"].get("note"))
        html = fragment(view, section="Historial")
        self.assertNotIn("anterior al historial", html)
        self.assertNotIn("332845", [row["ticket"] for row in view["history"]["rows"]])


    def test_rendimiento_uses_close_times_not_a_flat_snapshot_curve(self):
        """Live on 23f1030: ui_snapshots.curve is current equity at the cycle read time.

        desk_from_book stores {at: cycle time, equity: current equity}; attach_desk
        appends those points. build_dashboard preferred that series when it had
        two or more points, so Rendimiento was nine points at 30,644.60, 27/11→27/11.
        The series must be brake_start 30086 plus the −0.07 of 17 nov, then the
        nine closes, ending at 30,644.60, with real close dates on the axis.
        """
        import sqlite3
        from ui.liquid.page import load
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        state = Path(folder.name) / "exness_state.db"
        ui_db = Path(folder.name) / "trading_floor.db"
        flags, cached = [], []
        for item in DEALS:
            if item[0] != "440549":
                flags.append(_flag(*item))
            cached.extend(_deal(*item))
        cached.extend(_deal(*PRIOR))
        flags.append(("combo_lon_2026-11-25", json.dumps({"status": "submitted", "exited": False, "side": "SHORT", "symbol": "USDJPY", "opened_at": "2026-11-25T07:30:32.198228+00:00"})))
        flags.append(("exness_brake_start", "30086"))
        flags.append(("exness_launch_start", "2026-11-16T21:00:00+00:00"))
        flags.append(("exness_closed_deals", json.dumps(cached)))
        _store(state, flags)
        # Nine cycle samples, all at today's equity and today's read time — the live shape.
        now_at = NOW.isoformat()
        flat = [{"at": "2026-11-27T16:%02d:00+00:00" % (i * 5), "equity": "30644.6"} for i in range(9)]
        con = sqlite3.connect(ui_db)
        con.execute("CREATE TABLE ui_snapshots(symbol TEXT PRIMARY KEY, payload TEXT NOT NULL)")
        con.execute("INSERT INTO ui_snapshots(symbol, payload) VALUES(?, ?)",
                    ("USDJPY", json.dumps({
                        "as_of": now_at,
                        "account_state": {"balance": "30644.6", "equity": "30644.6", "currency": "USC"},
                        "positions": [], "orders": [],
                        "curve": flat,
                    })))
        con.commit()
        con.close()
        env = {
            "AI_FLOOR_BROKER": "exness",
            "AI_FLOOR_EXNESS_STATE_DB_PATH": str(state),
            "AI_FLOOR_DB_PATH": str(ui_db),
        }
        view = load(env=env, now=NOW)
        points = view["curve"]["points"]
        tickets = [row["ticket"] for row in view["history"]["rows"]]
        self.assertEqual(view["curve"]["state"], "ok")
        self.assertEqual(len(tickets), 9)
        self.assertIn("440549", tickets)
        self.assertNotIn("332845", tickets)
        self.assertEqual(len(points), 10)
        self.assertEqual(points[0]["equity"], "30085.93")
        self.assertEqual(points[-1]["equity"], "30644.60")
        self.assertEqual(points[0]["axis"], "18/11")
        self.assertEqual(points[-1]["axis"], "27/11")
        axes = [point["axis"] for point in points]
        self.assertEqual(set(axes), {"18/11", "19/11", "20/11", "23/11", "24/11", "25/11", "27/11"})
        # Running balances at each close (start 30085.93, then the nine P/L figures).
        expected = ["30085.93", "30001.85", "30148.04", "30264.46", "30255.46",
                    "30636.22", "30623.22", "30483.33", "30463.14", "30644.60"]
        self.assertEqual([point["equity"] for point in points], expected)
        for key in ("30", "90", "365", "all"):
            series = view["curve"]["ranges"][key]["points"]
            self.assertEqual(series[-1]["equity"], "30644.60")
            self.assertEqual(series[0]["axis"], "18/11")
            self.assertEqual(series[-1]["axis"], "27/11")


class PriorNoteAndLogTests(unittest.TestCase):
    def tearDown(self):
        from ui.liquid import model as liquid_model
        liquid_model._view_logged = False
        log = __import__("logging").getLogger("ai_floor.session_combo")
        for handler in list(getattr(self, "_added_handlers", ())):
            log.removeHandler(handler)

    def test_prior_history_note_matches_the_live_sentence(self):
        from execution.combo_history import prior_history_note
        cached = []
        for item in DEALS:
            cached.extend(_deal(*item))
        cached.extend(_deal(*PRIOR))
        flags = {"exness_closed_deals": json.dumps(cached)}
        self.assertEqual(prior_history_note(flags, "USC"), NOTE)
        self.assertIsNone(prior_history_note({"exness_closed_deals": json.dumps(_deal(*DEALS[0]))}, "USC"))

    def test_ensure_session_combo_log_is_idempotent(self):
        import logging, sys
        from execution.combo_history import ensure_session_combo_log
        log = logging.getLogger("ai_floor.session_combo")
        saved = list(log.handlers)
        for handler in saved:
            log.removeHandler(handler)
        try:
            first = ensure_session_combo_log()
            second = ensure_session_combo_log()
            self.assertIs(first, second)
            stdout = [h for h in log.handlers
                      if isinstance(h, logging.StreamHandler) and getattr(h, "stream", None) is sys.stdout]
            self.assertEqual(len(stdout), 1)
        finally:
            for handler in list(log.handlers):
                if handler not in saved:
                    log.removeHandler(handler)
            for handler in saved:
                if handler not in log.handlers:
                    log.addHandler(handler)

    def test_history_view_log_reaches_stdout_with_range_counts(self):
        import io, logging
        from contextlib import redirect_stdout
        from ui.liquid import model as liquid_model
        from ui.liquid.page import load
        log = logging.getLogger("ai_floor.session_combo")
        saved_handlers, saved_level = list(log.handlers), log.level
        for handler in saved_handlers:
            log.removeHandler(handler)
        liquid_model._view_logged = False
        buf = io.StringIO()
        try:
            with redirect_stdout(buf):
                folder = tempfile.TemporaryDirectory()
                state = Path(folder.name) / "exness_state.db"
                flags = []
                cached = []
                for item in DEALS:
                    if item[0] != "440549":
                        flags.append(_flag(*item))
                    cached.extend(_deal(*item))
                flags.append(("combo_lon_2026-11-25", json.dumps({"status": "done", "exited": False})))
                flags.append(("exness_brake_start", "30086"))
                flags.append(("exness_closed_deals", json.dumps(cached)))
                _store(state, flags)
                env = {
                    "AI_FLOOR_BROKER": "exness",
                    "AI_FLOOR_EXNESS_STATE_DB_PATH": str(state),
                    "AI_FLOOR_DB_PATH": str(Path(folder.name) / "missing.db"),
                }
                view = load(env=env, now=NOW)
            text = buf.getvalue()
            self.assertIn("event=HISTORY_VIEW", text)
            self.assertIn("event=HISTORY_DEALS_MERGE added=1", text)
            self.assertIn("range_30=", text)
            self.assertIn("range_90=", text)
            self.assertIn("range_365=", text)
            self.assertIn("range_all=", text)
            self.assertIn("curve_points=", text)
            self.assertIn("source=exness_flags", text)
            self.assertGreaterEqual(len(view["curve"]["ranges"]["30"]["points"]), 2)
        finally:
            folder.cleanup()
            liquid_model._view_logged = False
            for handler in list(log.handlers):
                if handler not in saved_handlers:
                    log.removeHandler(handler)
            for handler in saved_handlers:
                if handler not in log.handlers:
                    log.addHandler(handler)
            log.setLevel(saved_level)


# Streamlit 1.65.0 st.html sanitizes with DOMPurify USE_PROFILES: {html: true}
# (frontend/lib/src/components/elements/Html/dompurifyHooks.ts). HtmlWithJs
# still uses that profile when unsafe_allow_javascript=True; it only adds
# script and style. The html profile drops SVG and MathML tags.
_STREAMLIT_165_DROPPED = (
    "svg", "polyline", "polygon", "path", "defs", "lineargradient", "stop",
    "circle", "g", "text", "line", "rect", "ellipse", "use", "symbol", "clippath",
    "math", "mrow", "mi", "mo", "mn",
)


def streamlit_1_65_html_profile(html):
    """Drop the tags Streamlit 1.65's html profile strips. Good enough to
    reproduce the missing Rendimiento curve without a Node DOMPurify build.
    """
    import re
    out = html
    for tag in _STREAMLIT_165_DROPPED:
        out = re.sub(r"(?is)<" + tag + r"\b[^>]*>.*?</" + tag + r">", "", out)
        out = re.sub(r"(?is)<" + tag + r"\b[^>]*/?>", "", out)
    return out


class ChartSanitizerTests(unittest.TestCase):
    def test_inline_svg_is_stripped_and_the_img_curve_survives(self):
        import re
        from ui.liquid.render import _chart
        points = [
            {"equity": "30086", "axis": "16/11", "y": "30,086.00", "text": "30,086.00 USC"},
            {"equity": "30644.6", "axis": "27/11", "y": "30,644.60", "text": "30,644.60 USC"},
        ]
        inline = (
            '<svg class="equity" width="640" height="220" viewBox="0 0 640 220">'
            '<defs><linearGradient id="lg-area" x1="0" y1="0" x2="0" y2="1">'
            '<stop offset="0" stop-color="#2563eb"/></linearGradient></defs>'
            '<polygon points="0,0 1,1 2,0"/>'
            '<polyline points="0,0 1,1"/>'
            '<circle cx="1" cy="1" r="3"/>'
            '<text x="0" y="10">30,086</text></svg>'
        )
        stripped = streamlit_1_65_html_profile(inline)
        self.assertIsNone(re.search(r"<svg\b", stripped))
        self.assertNotIn("<polyline", stripped)
        self.assertNotIn("<polygon", stripped)
        self.assertNotIn("<defs", stripped)
        painted = _chart(points)
        self.assertIn("data:image/svg+xml;base64,", painted)
        self.assertIn("<img", painted)
        self.assertIsNone(re.search(r"<svg\b", painted))
        survived = streamlit_1_65_html_profile(painted)
        self.assertIn("data:image/svg+xml;base64,", survived)
        self.assertIn('class="equity on-light"', survived)
        self.assertIn('class="equity on-dark"', survived)
        self.assertIn('height="220"', survived)
