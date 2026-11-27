"""Fase 3: filas fantasma, enlace, despliegue, servidor, P/L flotante y consumo."""
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from execution.exness_brakes import LINK_AT_FLAG, LINK_FLAG
from execution.session_combo import fix_key
from ui.liquid.model import MISSING, build_dashboard
from ui.liquid.render import fragment

NOW = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)
ENV = {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_EXNESS_STATE_DB_PATH": "/nonexistent/exness.db"}


def _view(**kwargs):
    base = dict(env=ENV, now=NOW, flags={}, health={"runtime_heartbeat": NOW.isoformat(),
                                                    "scheduler": "RUNNING", "status": "ONLINE"}, errors=[])
    base.update(kwargs)
    return build_dashboard(**base)


class GhostRowTests(unittest.TestCase):
    def test_un_fix_omitido_no_pinta_fila_vacia_y_dice_omitida(self):
        from test_session_combo import fix_bars, lon, world
        at = lon(2026, 11, 27, 16, 1)
        _clock, fake, _broker, combo = world(at)
        bars = fix_bars("GBPUSD", "1.25010", at) + fix_bars("EURUSD", "1.25010", at)
        combo.tick(at, bars)
        saved = json.loads(fake.get_flag(fix_key(at.date())))
        self.assertEqual(saved["status"], "skipped")
        self.assertEqual(saved["exit_reason"], "below_threshold")
        late = lon(2026, 11, 27, 16, 6)
        _clock, fake, _broker, combo = world(late)
        combo.tick(late, [])
        closed = json.loads(fake.get_flag(fix_key(late.date())))
        self.assertEqual(closed["status"], "skipped")
        self.assertEqual(closed["exit_reason"], "window_closed")
        flags = {
            fix_key(at.date()): json.dumps(saved),
            "combo_lon_2026-11-25": json.dumps({
                "status": "done", "symbol": "USDJPY", "side": "SHORT", "exited": True,
                "opened_at": "2026-11-25T12:10:00+00:00", "pnl": "-20.19",
            }),
            "combo_fix_2026-11-26": json.dumps({"status": "done"}),
        }
        view = _view(flags=flags)
        symbols = [row["symbol"] for row in view["history"]["rows"]]
        self.assertEqual(symbols, ["USDJPY"])
        self.assertIn("20.19", view["history"]["rows"][0]["pnl"])
        pairs = {pair["symbol"]: pair["state"] for pair in view["strategy"]["pairs"]}
        self.assertEqual(pairs["EURUSD"], "Omitida")
        self.assertEqual(pairs["GBPUSD"], "Omitida")
        html = fragment(view, section="Historial")
        self.assertIn("USDJPY", html)
        self.assertNotIn("Sin operación", html)


class LinkTests(unittest.TestCase):
    def test_el_tick_renueva_el_enlace_sin_llamar_al_broker(self):
        from execution.exness_brakes import ExnessBrakes, MemoryFlags
        from execution.exness_broker import ExnessBroker
        from execution.exness_transport import FakeExnessTransport
        from runtime.cloud_runner import TICK_SECONDS
        from runtime.service import OperationalRuntime
        self.assertEqual(TICK_SECONDS, 30)
        self.assertIn("_refresh_exness_link(runner)", Path("runtime/cloud_runner.py").read_text(encoding="utf-8"))
        fake = FakeExnessTransport(clock=lambda: NOW)
        calls = {"n": 0}
        original = fake.read_state

        def counted():
            calls["n"] += 1
            return original()

        fake.read_state = counted
        fake.push_tick(Decimal("1.10000"), Decimal("1.10020"), NOW)
        broker = ExnessBroker(fake, env=dict(ENV), brakes=ExnessBrakes.from_env(MemoryFlags(), {}), clock=lambda: NOW)

        class Runtime:
            exness_session = type("S", (), {"broker": broker})()

            def refresh_exness_link(self, when):
                return OperationalRuntime.refresh_exness_link(self, when)

        runtime = Runtime()
        self.assertIs(runtime.refresh_exness_link(NOW), True)
        self.assertEqual(calls["n"], 0)
        self.assertEqual(broker._brakes._store.get_flag(LINK_FLAG), "1")
        view = _view(flags={LINK_FLAG: "1", LINK_AT_FLAG: broker._brakes._store.get_flag(LINK_AT_FLAG)})
        self.assertEqual(view["system"]["connection"], "Conectada")
        self.assertIn("Conectada", fragment(view, section="Sistema"))
        fake.quotes.mark_disconnected()
        self.assertIs(runtime.refresh_exness_link(NOW), False)
        self.assertEqual(calls["n"], 0)
        self.assertEqual(broker._brakes._store.get_flag(LINK_FLAG), "0")


class DeployTests(unittest.TestCase):
    def test_el_despliegue_usa_el_id_de_instancia_de_render(self):
        shown = _view(env=dict(ENV, RENDER_INSTANCE_ID="abc123def456"))
        self.assertEqual(shown["system"]["deploy"], "abc123def456")
        both = _view(env=dict(ENV, RENDER_GIT_COMMIT="abcdef1234567890", RENDER_INSTANCE_ID="abc123def456"))
        self.assertEqual(both["system"]["deploy"], "abcdef1")
        ignored = _view(env=dict(ENV, AI_FLOOR_GIT_COMMIT="deadbeef"))
        self.assertEqual(ignored["system"]["deploy"], MISSING)


class ServerBlockTests(unittest.TestCase):
    def test_el_bloque_de_servidor_sin_fuente_no_se_pinta(self):
        view = _view()
        html = fragment(view, section="Sistema")
        for gone in ("Información del servidor", "Tiempo activo", ">Ping<", ">Servidor<"):
            self.assertNotIn(gone, html)
        self.assertNotIn("Ping", view["unavailable"])
        self.assertNotIn("Tiempo activo", view["unavailable"])


class FloatingTests(unittest.TestCase):
    def test_el_libro_vacio_muestra_cero_y_las_abiertas_se_suman(self):
        empty = _view(book={"positions": [], "orders": []})
        self.assertEqual(empty["positions"]["state"], "empty")
        self.assertIn("0.00", empty["positions"]["floating"])
        self.assertNotEqual(empty["positions"]["floating"], MISSING)
        book = {"positions": [
            {"position_id": "1", "instrument": "EURUSD", "side": "LONG", "volume": "0.01", "profit": "1.50"},
            {"position_id": "2", "instrument": "GBPUSD", "side": "SHORT", "volume": "0.02", "profit": "-0.25"},
        ], "orders": []}
        view = _view(book=book)
        self.assertIn("1.25", view["positions"]["floating"])


class TradeCapTests(unittest.TestCase):
    def test_el_consumo_del_tope_es_cero_con_el_libro_vacio(self):
        empty = _view(book={"positions": [], "orders": []})
        self.assertIn("0.00", empty["brakes"]["trade"]["text"])
        html = fragment(empty, section="Riesgo")
        self.assertIn("Consumo:", html)
        self.assertNotIn("Consumo: sin datos", html)
        book = {"positions": [{"position_id": "1", "instrument": "EURUSD", "side": "LONG", "volume": "0.01"}],
                "orders": []}
        open_book = _view(book=book)
        self.assertEqual(open_book["brakes"]["trade"]["text"], "")
        self.assertNotIn("Consumo: sin datos", fragment(open_book, section="Riesgo"))
