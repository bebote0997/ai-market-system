"""Liquid Glass view model, SL/TP join (bug #95), and a static render smoke. No network and no flag writes."""
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import json
import os
import tempfile
import unittest
from unittest.mock import patch

from ui.liquid.model import MISSING, build_dashboard, linked_protection
from ui.liquid.preview import BANNER, example_view
from ui.liquid.render import document, fragment

NOW = datetime(2026, 11, 20, 16, 0, tzinfo=timezone.utc)
ENV = {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_EXNESS_STATE_DB_PATH": "/nonexistent/exness.db"}


def _view(**kwargs):
    base = dict(env=ENV, now=NOW, flags={}, book=None, health=None, errors=None, curve=None)
    base.update(kwargs)
    return build_dashboard(**base)


class ProtectionTests(unittest.TestCase):
    def test_position_stop_fields_are_ignored(self):
        position = {"position_id": "77", "stop_loss_price": "9.99", "take_profit_price": "8.88", "stop": "1", "target": "2"}
        self.assertEqual(linked_protection(position, ()), (None, None))

    def test_sltp_order_matches_position_id_only(self):
        position = {"position_id": 77, "stop_loss_price": "9.99", "take_profit_price": "8.88"}
        orders = [
            {"type": "limit", "position_id": "77", "stop_loss_price": "1", "take_profit_price": "2"},
            {"type": "sltp", "position_id": "other", "stop_loss_price": "3", "take_profit_price": "4"},
            {"order_type": "SLTP", "position_id": 77, "stop_loss_price": "1.25010", "take_profit_price": "1.26020"},
        ]
        stop, target = linked_protection(position, orders)
        self.assertEqual((stop, target), (Decimal("1.25010"), Decimal("1.26020")))


class ModelTests(unittest.TestCase):
    def test_empty_sources_stay_explicit_and_keep_the_usc_caps(self):
        view = _view()
        self.assertEqual(view["kpis"]["balance"]["text"], MISSING)
        self.assertEqual(view["kpis"]["today"]["text"], MISSING)
        self.assertEqual(view["kpis"]["total"]["text"], MISSING)
        self.assertEqual(view["kpis"]["cycle"]["text"], "Día 0 de 30")
        self.assertEqual(view["positions"]["state"], "sin datos")
        self.assertEqual(view["curve"]["state"], "sin datos")
        self.assertEqual(view["brakes"]["daily"]["cap"], "200.00 USC · 2.00 USD")
        self.assertEqual(view["brakes"]["total"]["cap"], "1,000.00 USC · 10.00 USD")
        self.assertEqual(view["brakes"]["daily"]["used"], MISSING)
        self.assertEqual(view["brakes"]["halt"], "No")
        self.assertIsNone(view["banner"])

    def test_caps_follow_the_runtime_env_not_the_code_default(self):
        from execution.exness_brakes import DAILY_ENV, DRAWDOWN_ENV
        from execution.exness_sizing import RISK_CAP_ENV, USC_RISK_CAP
        bare = _view()
        self.assertIn(f"{USC_RISK_CAP:,.2f} USC", bare["brakes"]["trade_cap"])
        env = dict(ENV, **{RISK_CAP_ENV: "300", DAILY_ENV: "250", DRAWDOWN_ENV: "1500"})
        view = _view(env=env)
        self.assertIn("300.00 USC", view["brakes"]["trade_cap"])
        self.assertNotIn("100.00 USC", view["brakes"]["trade_cap"])
        self.assertIn("250.00 USC", view["brakes"]["daily"]["cap"])
        self.assertIn("1,500.00 USC", view["brakes"]["total"]["cap"])
        self.assertNotIn("200.00 USC", view["brakes"]["daily"]["cap"])

    def test_missing_snapshot_does_not_invent_equity(self):
        view = _view(flags={"exness_brake_day_equity": "10000", "exness_brake_start": "10000"})
        self.assertEqual(view["kpis"]["balance"]["text"], MISSING)
        self.assertEqual(view["kpis"]["equity"]["text"], MISSING)
        self.assertEqual(view["kpis"]["today"]["text"], MISSING)
        self.assertEqual(view["kpis"]["total"]["text"], MISSING)
        self.assertIn("Consumo de frenos", view["unavailable"])

    def test_live_equity_builds_pnl_and_brake_use(self):
        view = _view(flags={"exness_brake_day_equity": "10000", "exness_brake_start": "10000"},
                     account_reader=lambda _env: {"balance": "10020", "equity": "9980", "currency": "USC"})
        self.assertEqual(view["kpis"]["today"]["tone"], "neg")
        self.assertIn("20.00 USC", view["kpis"]["today"]["text"])
        self.assertIn("20.00 USC", view["kpis"]["total"]["text"])
        self.assertEqual(view["brakes"]["daily"]["text"], "20.00 USC · 0.20 USD · 10.0 %")
        self.assertEqual(view["brakes"]["total"]["bar"], 2.0)
        self.assertNotIn("Consumo de frenos", view["unavailable"])

    def test_open_position_uses_the_linked_sltp_order(self):
        book = {"positions": [{
            "position_id": "77", "instrument": "USDJPY", "direction": "sell", "volume": "0.10",
            "open_price": "150.100", "stop_loss_price": "9.99", "take_profit_price": "8.88",
        }], "orders": [
            {"type": "sltp", "position_id": "77", "stop_loss_price": "150.400", "take_profit_price": "149.500"},
        ]}
        flags = {"combo_lon_2026-11-20": json.dumps({
            "status": "filled", "position_id": "77", "exit_at": "2026-11-20T16:00:00+00:00", "exited": False,
        })}
        view = _view(book=book, flags=flags)
        row = view["positions"]["rows"][0]
        self.assertEqual(row["symbol"], "USDJPY")
        self.assertEqual(row["side"], "Venta")
        self.assertEqual(row["lots"], "0.1")
        self.assertEqual(row["entry"], "150.1")
        self.assertEqual(row["sl"], "150.4")
        self.assertEqual(row["tp"], "149.5")
        self.assertEqual(row["current"], MISSING)
        self.assertIn("20 nov 2026 11:00 ET", row["close"])
        self.assertNotIn("9.99", json.dumps(row))

    def test_flat_book_is_empty_and_a_flag_only_leg_is_partial(self):
        self.assertEqual(_view(book={"positions": [], "orders": []})["positions"]["state"], "empty")
        flags = {"combo_lon_2026-11-20": json.dumps({
            "status": "filled", "symbol": "USDJPY", "side": "SHORT", "position_id": "77", "volume": "0.2",
            "opened_at": "2026-11-20T12:00:00+00:00", "exited": False,
        })}
        partial = _view(flags=flags)["positions"]
        self.assertEqual(partial["state"], "rows")
        self.assertEqual(partial["rows"][0]["sl"], MISSING)
        self.assertEqual(partial["rows"][0]["entry"], MISSING)
        self.assertIn("sin datos", partial["note"])

    def test_history_keeps_closed_legs_and_drops_the_open_one(self):
        flags = {
            "combo_lon_2026-11-20": json.dumps({"status": "filled", "symbol": "USDJPY", "side": "SHORT",
                                               "position_id": "77", "opened_at": "2026-11-20T12:00:00+00:00", "exited": False}),
            "combo_fix_2026-11-18": json.dumps({"status": "done", "symbol": "GBPUSD", "side": "LONG",
                                               "position_id": "9", "opened_at": "2026-11-18T16:00:00+00:00", "exited": True}),
            "combo_lon_2026-11-21": json.dumps({"status": "done", "symbol": "USDJPY", "opened_at": "2026-11-21T12:00:00+00:00"}),
        }
        view = _view(flags=flags)
        self.assertEqual([row["ticket"] for row in view["history"]["rows"]], ["9"])
        self.assertEqual(view["history"]["rows"][0]["pnl"], MISSING)
        self.assertEqual(view["strategy"]["london"]["state"], "Abierta")
        self.assertEqual(view["strategy"]["london"]["pill"], "En curso")
        self.assertEqual(view["strategy"]["steps"][1]["state"], "Hecha")
        self.assertEqual(view["strategy"]["pairs"][1]["state"], MISSING)
        self.assertEqual(view["history"]["stats"]["count"], "1")
        self.assertEqual(view["history"]["stats"]["net"], MISSING)
        self.assertEqual(view["strategy"]["fix"]["state"], MISSING)

    def test_curve_needs_two_past_points(self):
        one = _view(curve=[{"at": "2026-11-20T12:00:00+00:00", "equity": "10000"}])
        self.assertEqual(one["curve"]["state"], "sin datos")
        future = _view(curve=[{"at": "2026-11-20T12:00:00+00:00", "equity": "10000"},
                              {"at": "2026-11-21T12:00:00+00:00", "equity": "11000"}])
        self.assertEqual(future["curve"]["state"], "sin datos")
        ok = _view(curve=[{"at": "2026-11-20T15:00:00+00:00", "equity": "10010"},
                          {"at": "2026-11-19T15:00:00+00:00", "equity": "10000"}])
        self.assertEqual(ok["curve"]["state"], "ok")
        self.assertEqual(ok["curve"]["points"][0]["equity"], "10000")

    def test_system_hides_payloads_and_unsafe_text(self):
        view = _view(health={"runtime_heartbeat": "2026-11-20T15:59:00+00:00", "scheduler": "RUNNING",
                             "status": ("A", "B"), "last_cycle_status": "COMPLETED",
                             "last_run": "https://secret.example/host"},
                     errors=[{"event": "RUN_FAILED", "at": "2026-11-20T15:00:00+00:00", "payload": "token-secret"},
                             {"event": "not a code", "at": "x"}])
        self.assertEqual(view["system"]["scheduler"], "En marcha")
        self.assertEqual(view["system"]["status"], MISSING)
        self.assertEqual(view["system"]["last_run"], MISSING)
        self.assertEqual(view["system"]["errors"], [{"at": "20 nov 2026 10:00 ET", "event": "RUN_FAILED"}])
        self.assertNotIn("token-secret", json.dumps(view["system"]))
        self.assertNotIn("secret.example", json.dumps(view))

    def test_halt_reason_is_a_short_label(self):
        view = _view(flags={"exness_brake_halt": "1", "exness_brake_reason": "daily_loss"})
        self.assertEqual(view["brakes"]["halt"], "Activo")
        self.assertEqual(view["brakes"]["reason"], "pérdida diaria")
        leaked = _view(flags={"exness_brake_halt": "1", "exness_brake_reason": "https://broker.example/99"})
        self.assertEqual(leaked["brakes"]["reason"], "ninguno")


class RenderSmokeTests(unittest.TestCase):
    def test_empty_panel_lists_sections_and_stays_static(self):
        html = fragment(_view(), section="Inicio")
        for name in ("Inicio", "Posiciones", "Historial", "Riesgo", "Sistema"):
            self.assertIn(f'data-nav="{name}"', html)
            self.assertIn(f'data-panel="{name}"', html)
        self.assertIn("sin datos", html)
        self.assertIn("blur(24px)", html)
        self.assertIn("prefers-color-scheme", html)
        self.assertIn("max-width:390px", html)
        self.assertIn("localStorage", html)
        self.assertIn("aif-liquid-theme", html)
        self.assertIn("Jt Trading Floor", html)
        self.assertIn("row-cards", html)
        self.assertNotIn("DATOS DE EJEMPLO", html)
        self.assertIn("JT GROUP HOLDINGS", html)
        self.assertIn("v2.0.0", html)
        self.assertIn("Exportar CSV", html)
        self.assertIn("Freno activado", html)
        self.assertNotIn("Guardar cambios", html)
        self.assertNotIn("Parada de emergencia", html)
        self.assertNotIn("Cerrar posición", html)
        self.assertNotIn(BANNER, html)
        lowered = html.lower()
        for banned in ("transition", "animation", "@keyframes", "<form", 'type="submit"', "cerrar", "set_flag", "http://", "https://"):
            self.assertNotIn(banned, lowered, banned)
        self.assertNotIn("<", Path("ui/static/liquid/panel.js").read_text(encoding="utf-8"))

    def test_example_banner_is_only_on_the_screenshot_view(self):
        view = example_view()
        html = document(view, theme="dark")
        self.assertIn(BANNER, html)
        self.assertIn('data-theme="dark"', html)
        self.assertIn("150.4", html)
        self.assertNotIn("9.99", html)
        self.assertNotIn("8.88", html)
        self.assertIsNone(_view()["banner"])

    def test_modules_do_not_touch_flags_brakes_or_the_commit(self):
        text = "\n".join(path.read_text(encoding="utf-8") for path in Path("ui/liquid").glob("*.py"))
        for banned in ("set_flag", "AI_FLOOR_GIT_COMMIT", "submit(", "REAL_EXECUTION_ENABLED"):
            self.assertNotIn(banned, text)


class LoadTests(unittest.TestCase):
    def test_load_reads_nothing_it_cannot_open_and_creates_no_file(self):
        from ui.liquid.page import load
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(ENV, AI_FLOOR_DB_PATH=str(Path(tmp) / "missing.db"),
                       EXN_ACCOUNT_ID="99887766", EXN_DISCOVERY_BASE_URL="https://secret.example.test")
            with patch.dict(os.environ, env, clear=False):
                view = load(env=env, now=NOW)
            self.assertFalse((Path(tmp) / "missing.db").exists())
            html = fragment(view)
            self.assertNotIn("99887766", html)
            self.assertNotIn("secret.example", html)
            self.assertEqual(view["positions"]["state"], "sin datos")
            self.assertEqual(view["curve"]["state"], "sin datos")


class WiringTests(unittest.TestCase):
    def test_app_keeps_the_old_pages_and_opens_on_inicio(self):
        source = Path("ui/app.py").read_text(encoding="utf-8")
        self.assertIn("luxury.PAGES + (floor_view.PAGE,) + dashboard.PAGES", source)
        self.assertIn("+ LIQUID_PAGES", source)
        self.assertLess(source.index('if st.query_params.get("page") == "floor"'),
                        source.index('st.session_state["luxury_navigation"] = "Inicio"'))
        self.assertLess(source.index("if page in LIQUID_PAGES:"), source.index("if page in luxury.PAGES:"))
        self.assertLess(source.index("require_dashboard_access(st)"), source.index('= "Inicio"'))


if __name__ == "__main__":
    unittest.main()
