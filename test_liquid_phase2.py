"""Fase 2 del panel: refresco, P/L vivo, fuente del resumen, TP del Fix, conexión y freno."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from execution.exness_brakes import (
    DAY_EQUITY_FLAG, DAY_FLAG, HALT_FLAG, LINK_AT_FLAG, LINK_FLAG, LIVE_FLAG, REASON_FLAG,
    START_FLAG, STATE_FLAG, MemoryFlags, publish_runtime_state)
from execution.session_combo import fix_prices
from ui.liquid.live import cached_call, panel_marks, reset_cache
from ui.liquid.model import MISSING, build_dashboard
from ui.liquid.render import fragment


NOW = datetime(2026, 11, 20, 16, 0, tzinfo=timezone.utc)
ENV = {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_EXNESS_STATE_DB_PATH": "/nonexistent/exness.db"}
ROOT = Path(__file__).resolve().parent


def _view(**kwargs):
    base = dict(env=ENV, now=NOW, flags={}, health={"runtime_heartbeat": NOW.isoformat(), "scheduler": "RUNNING",
                                                    "status": "ONLINE"}, errors=[])
    base.update(kwargs)
    return build_dashboard(**base)


class RefreshTests(unittest.TestCase):
    def test_panel_refreshes_without_resetting_tab_or_scroll(self):
        page = (ROOT / "ui/liquid/page.py").read_text(encoding="utf-8")
        script = (ROOT / "ui/static/liquid/panel.js").read_text(encoding="utf-8")
        self.assertIn('run_every="20s"', page)
        self.assertIn("fragment(load(", page)
        self.assertIn("aif-liquid-section", script)
        self.assertIn("aif-liquid-scroll", script)
        self.assertIn("sessionStorage", script)
        self.assertNotIn("location.reload", script)
        self.assertNotIn("location.href", script)


class FloatingTests(unittest.TestCase):
    def setUp(self):
        reset_cache()

    def test_marks_fill_price_and_floating_pnl(self):
        view = _view(book={"positions": [{
            "position_id": "55", "instrument": "EURUSD", "side": "LONG", "volume": "0.01",
            "open_price": "1.08000"}], "orders": []}, marks={
            "positions": [{"position_id": "55", "current": "1.08100", "profit": "1.50"}]})
        row = view["positions"]["rows"][0]
        self.assertEqual(row["current"], "1.081")
        self.assertIn("1.50", row["floating"])
        self.assertIn("1.50", view["positions"]["floating"])

    def test_broker_is_called_at_most_once_per_thirty_seconds(self):
        calls = {"n": 0}

        def reader():
            calls["n"] += 1
            return {"positions": [{"position_id": "1", "current": "1.1", "profit": "1"}]}

        self.assertEqual(cached_call(reader, 10)["positions"][0]["current"], "1.1")
        cached_call(reader, 20)
        cached_call(reader, 39.9)
        self.assertEqual(calls["n"], 1)
        cached_call(reader, 40)
        self.assertEqual(calls["n"], 2)

    def test_a_fresh_flag_skips_the_get_and_a_closed_market_does_too(self):
        calls = {"n": 0}
        flags = {"exness_live_marks": json.dumps({
            "at": NOW.isoformat(), "positions": [{"position_id": "9", "current": "1.2", "profit": "3"}]})}
        with patch("ui.exness_present._flags", return_value=flags):
            got = panel_marks(ENV, NOW, reader=lambda: calls.__setitem__("n", calls["n"] + 1))
        self.assertEqual(got["positions"][0]["current"], "1.2")
        self.assertEqual(calls["n"], 0)
        saturday = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)
        with patch("ui.exness_present._flags", return_value={}):
            self.assertIsNone(panel_marks(dict(ENV, EXN_ACCOUNT_ID="99"), saturday, reader=lambda: calls.__setitem__("n", 1)))
        self.assertEqual(calls["n"], 0)


class SummarySourceTests(unittest.TestCase):
    def test_five_pm_and_nine_fifteen_use_exness(self):
        from runtime.notifications import NotificationEvent, SlackNotificationSink, _summary_source
        for hour, minute in ((17, 0), (21, 15)):
            with self.subTest(hour=hour, minute=minute):
                sent = []
                sink = SlackNotificationSink(webhook_url="https://hooks.slack.com/services/T/B/x",
                                             transport=lambda url, payload, timeout: sent.append(payload))
                when = datetime(2026, 11, 20, hour, minute, tzinfo=timezone(timedelta(hours=-5)))
                event = NotificationEvent("1.0", "evt", "daily:2026-11-20", when.isoformat(), "INFO",
                                          "DAILY_SUMMARY", None, (), (), None, (), (), (),
                                          daily_summary={"date_ny": "2026-11-20"})
                with patch.dict("os.environ", {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_BROKER_DEMO_ROUTING": "1"}), \
                        patch("ui.exness_present.summary_lines", return_value=["Exness Standard Cent (USC)", "Freno: no"]), \
                        patch("ui.exness_present.session_clock", return_value={"waiting": True}), \
                        self.assertLogs("runtime.notifications", "INFO") as logs:
                    sink.deliver(event)
                    self.assertEqual(_summary_source(), "EXNESS")
                self.assertTrue(any("source=EXNESS" in line for line in logs.output))
                self.assertFalse(any("source=OANDA_DEMO" in line for line in logs.output))
                text = json.dumps(sent[-1])
                self.assertIn("Exness", text)
                self.assertNotIn("OANDA Demo", text)


class FixTpTests(unittest.TestCase):
    def test_fix_target_is_twice_the_thirty_pip_stop(self):
        stop, target = fix_prices(Decimal("1.25150"), "SHORT", "GBPUSD")
        self.assertEqual((stop, target), (Decimal("1.25450"), Decimal("1.24550")))
        stop, target = fix_prices(Decimal("1.25000"), "LONG", "GBPUSD")
        self.assertEqual((stop, target), (Decimal("1.24700"), Decimal("1.25600")))


class ConnectionTests(unittest.TestCase):
    def test_connection_follows_the_link_and_the_heartbeat(self):
        flags = {LINK_FLAG: "1", LINK_AT_FLAG: NOW.isoformat()}
        view = _view(flags=flags)
        self.assertEqual(view["system"]["connection"], "Conectada")
        self.assertIn("Conectada", fragment(view, section="Sistema"))
        stale = _view(flags=flags, health={"runtime_heartbeat": (NOW - timedelta(minutes=5)).isoformat(),
                                           "scheduler": "RUNNING", "status": "DEGRADED"})
        self.assertEqual(stale["system"]["connection"], "Sin latido")
        down = _view(flags={LINK_FLAG: "0", LINK_AT_FLAG: NOW.isoformat()})
        self.assertEqual(down["system"]["connection"], "Sin conexión")
        self.assertEqual(_view()["system"]["connection"], MISSING)
        weekend = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)
        closed = _view(now=weekend, flags=flags, health={
            "status": "DEGRADED", "scheduler": "RUNNING", "last_cycle_status": "COMPLETED",
            "freshness": "NO_DATA", "market_data": "exness", "runtime_heartbeat": weekend.isoformat()})
        self.assertEqual(closed["system"]["connection"], "mercado cerrado")


class BrakeStateTests(unittest.TestCase):
    def test_brake_state_is_logged_persisted_and_shown(self):
        store = MemoryFlags()
        store.set_flag(HALT_FLAG, "1")
        store.set_flag(REASON_FLAG, "daily_loss")
        store.set_flag(START_FLAG, "25000")
        store.set_flag(DAY_EQUITY_FLAG, "24950.5")
        store.set_flag(DAY_FLAG, "2026-11-20")
        at = NOW
        with self.assertLogs("ai_floor.exness_brakes", level="INFO") as logs:
            publish_runtime_state(store, equity=Decimal("24800.25"), currency="USC", fresh=True, at=at,
                                  marks=[{"position_id": "55", "current": "1.08100", "profit": "-2.00"}],
                                  daily_limit=Decimal("200"))
        line = next(item for item in logs.output if "event=BRAKE_STATE" in item)
        self.assertIn("equity=24800.25", line)
        self.assertIn("daily_limit=200", line)
        self.assertIn("halt=true", line)
        saved = json.loads(store.get_flag(STATE_FLAG))
        self.assertEqual(saved["equity"], "24800.25")
        self.assertEqual(saved["daily"], "200")
        self.assertIs(saved["halt"], True)
        self.assertEqual(store.get_flag(LINK_FLAG), "1")
        self.assertEqual(store.get_flag(LINK_AT_FLAG), at.isoformat())
        marks = json.loads(store.get_flag(LIVE_FLAG))
        self.assertEqual(marks["positions"][0]["current"], "1.08100")
        view = _view(flags={STATE_FLAG: store.get_flag(STATE_FLAG), HALT_FLAG: "1", REASON_FLAG: "daily_loss"})
        self.assertIn("24,800.25", view["brakes"]["state_line"])
        self.assertIn("200.00", view["brakes"]["state_line"])
        self.assertIn("freno activo", view["brakes"]["state_line"])
        self.assertIn("BRAKE_STATE:", fragment(view))
        again = MemoryFlags()
        with self.assertNoLogs("ai_floor.exness_brakes", level="INFO"):
            publish_runtime_state(again, equity=Decimal("1"), currency="USC", fresh=False, at=at,
                                  marks=[], daily_limit=Decimal("200"), log=False)
        self.assertEqual(json.loads(again.get_flag(STATE_FLAG))["halt"], False)
