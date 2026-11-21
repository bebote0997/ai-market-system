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

    def test_missing_snapshot_shows_the_day_base_and_not_a_made_up_pnl(self):
        view = _view(flags={"exness_brake_day_equity": "10000", "exness_brake_start": "10000"})
        self.assertEqual(view["kpis"]["balance"]["text"], MISSING)
        self.assertIn("10,000.00 USC", view["kpis"]["equity"]["text"])
        self.assertEqual(view["kpis"]["equity"]["sub"], "Base del día, 17:00 ET")
        self.assertEqual(view["kpis"]["today"]["text"], MISSING)
        self.assertEqual(view["kpis"]["total"]["text"], MISSING)
        self.assertIn("Consumo de frenos", view["unavailable"])

    def test_closed_market_uses_the_last_reading_and_does_not_look_degraded(self):
        from ui.liquid.model import CLOSED
        saturday = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)
        flags = {"exness_last_balance": "30200", "exness_last_equity": "30255.46",
                 "exness_last_currency": "USC", "exness_last_read_at": "2026-11-21T21:00:00+00:00",
                 "exness_brake_day_equity": "30000", "exness_brake_start": "25000",
                 "combo_lon_2026-11-20": json.dumps({
                     "status": "done", "symbol": "GBPUSD", "side": "buy", "volume": "1", "exited": True,
                     "entry": "1.2510", "exit_reason": "london_1100",
                 }),
                 "combo_lon_2026-11-19": json.dumps({
                     "status": "done", "symbol": "USDJPY", "side": "buy", "volume": "1.32", "exited": True,
                 })}
        view = _view(now=saturday, flags=flags, health={
            "status": "DEGRADED", "scheduler": "RUNNING", "last_cycle_status": "COMPLETED",
            "freshness": "NO_DATA", "runtime_heartbeat": "2026-11-21T15:59:00+00:00"}, errors=[])
        self.assertIn("30,200.00 USC", view["kpis"]["balance"]["text"])
        self.assertIn("30,255.46 USC", view["kpis"]["equity"]["text"])
        self.assertIn("última lectura", view["kpis"]["equity"]["sub"])
        self.assertIn("255.46 USC", view["kpis"]["today"]["text"])
        self.assertNotEqual(view["brakes"]["daily"]["text"], CLOSED)
        self.assertEqual(view["positions"]["state"], "closed")
        self.assertEqual(view["positions"]["note"], CLOSED)
        self.assertEqual(view["system"]["status"], "Mercado cerrado")
        self.assertEqual(view["system"]["connection"], CLOSED)
        london = view["history"]["rows"][0]
        self.assertEqual(london["entry"], "1.251")
        self.assertEqual(london["reason"], "cierre 11:00 ET")
        older = next(row for row in view["history"]["rows"] if row["symbol"] == "USDJPY")
        self.assertEqual(older["entry"], MISSING)
        self.assertEqual(older["reason"], "cierre 11:00 ET")
        self.assertEqual(older["pnl"], MISSING)
        html = fragment(view, section="Historial")
        self.assertIn("1.251", html)
        self.assertIn("cierre 11:00 ET", html)
        self.assertNotIn("Degradado", fragment(view, section="Sistema"))

    def test_deploy_label_is_the_render_commit_only(self):
        shown = _view(env=dict(ENV, RENDER_GIT_COMMIT="abcdef1234567890"))
        self.assertEqual(shown["system"]["deploy"], "abcdef1")
        ignored = _view(env=dict(ENV, AI_FLOOR_GIT_COMMIT="deadbeef"))
        self.assertEqual(ignored["system"]["deploy"], MISSING)

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
        css = Path("ui/static/liquid/glass.css").read_text(encoding="utf-8")
        wide = css.split("@media (max-width:900px)", 1)[1].split("@media", 1)[0]
        self.assertIn(".table-wrap{display:none}", wide)
        self.assertIn(".row-cards{display:flex", wide)
        self.assertIn(".hline{grid-template-columns:minmax(0,1fr)}", wide)
        self.assertIn(".lg .live{background:#0f7a45;color:#fff", css)
        self.assertNotIn(".pill,.live,", css)
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

    def test_latest_reads_os_environ_not_only_a_dict(self):
        import sqlite3
        from ui.liquid.snapshot import latest
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trading_floor.db"
            con = sqlite3.connect(path)
            con.execute("CREATE TABLE ui_snapshots(symbol TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            con.execute("INSERT INTO ui_snapshots(symbol, payload) VALUES(?, ?)",
                        ("EURUSD", json.dumps({"as_of": "2026-06-02T15:00:00+00:00",
                                               "account_state": {"balance": "1000", "equity": "1004", "currency": "USC"},
                                               "positions": [], "orders": []})))
            con.commit()
            con.close()
            with patch.dict(os.environ, {"AI_FLOOR_DB_PATH": str(path)}):
                found = latest(os.environ)
            self.assertEqual(found["account_state"]["equity"], "1004")
            view = build_dashboard(env=ENV, now=NOW, flags={}, snapshot=found)
            self.assertIn("1,004.00 USC", view["kpis"]["equity"]["text"])


class WiringTests(unittest.TestCase):
    def test_app_keeps_the_old_pages_and_opens_on_inicio(self):
        source = Path("ui/app.py").read_text(encoding="utf-8")
        self.assertIn("luxury.PAGES + (floor_view.PAGE,) + dashboard.PAGES", source)
        self.assertIn("+ LIQUID_PAGES", source)
        self.assertLess(source.index('if st.query_params.get("page") == "floor"'),
                        source.index('st.session_state["luxury_navigation"] = "Inicio"'))
        self.assertLess(source.index("if page in LIQUID_PAGES:"), source.index("if page in luxury.PAGES:"))
        self.assertLess(source.index("require_dashboard_access(st)"), source.index('= "Inicio"'))
        page = Path("ui/liquid/page.py").read_text(encoding="utf-8")
        self.assertIn('data-testid="stSidebar"', page)
        self.assertLess(page.index("st.markdown(_CHROME"), page.index("fragment(load()"))


class DeskSnapshotTests(unittest.TestCase):
    def test_manage_keeps_the_book_it_already_read(self):
        from execution.broker_port import BrokerBalance
        from execution.exness_brakes import ExnessBrakes
        from execution.exness_broker import ExnessBroker
        from execution.exness_session import ExnessSession
        from execution.exness_transport import FakeExnessTransport, Position
        at = datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc)
        fake = FakeExnessTransport(clock=lambda: at)
        fake.balance = BrokerBalance(Decimal("1000"), Decimal("1004"), Decimal("0"), "USC")
        fake.positions = [Position("55", "EURUSD", "LONG", Decimal("0.01"), Decimal("1.08000"),
                                   Decimal("1.07000"), Decimal("1.11000"))]
        fake.push_tick(Decimal("1.08000"), Decimal("1.08002"), at)
        calls = {"n": 0}
        original = fake.read_state

        def counted():
            calls["n"] += 1
            return original()

        fake.read_state = counted
        broker = ExnessBroker(fake, env={"AI_FLOOR_BROKER": "exness"},
                              brakes=ExnessBrakes.from_env(fake, {}), clock=lambda: at)
        session = ExnessSession(broker, env={"AI_FLOOR_BROKER": "exness"}, clock=lambda: at)
        session.manage(at)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(session.desk["account_state"]["equity"], "1004")
        self.assertEqual(session.desk["positions"][0]["instrument"], "EURUSD")
        self.assertNotIn("stop_loss_price", session.desk["positions"][0])
        order = session.desk["orders"][0]
        self.assertEqual(order["type"], "sltp")
        self.assertEqual(order["position_id"], "55")
        self.assertEqual(order["stop_loss_price"], "1.07000")

    def test_remember_desk_failure_still_turns_the_cache_off(self):
        from execution.exness_brakes import ExnessBrakes
        from execution.exness_broker import ExnessBroker
        from execution.exness_session import ExnessSession
        from execution.exness_transport import FakeExnessTransport
        at = datetime(2026, 6, 2, 15, 0, tzinfo=timezone.utc)
        fake = FakeExnessTransport(clock=lambda: at)
        broker = ExnessBroker(fake, env={"AI_FLOOR_BROKER": "exness"},
                              brakes=ExnessBrakes.from_env(fake, {}), clock=lambda: at)
        session = ExnessSession(broker, env={"AI_FLOOR_BROKER": "exness"}, clock=lambda: at)
        seen = []
        original = broker.cache_account_reads

        def traced(enabled):
            seen.append(bool(enabled))
            return original(enabled)

        broker.cache_account_reads = traced

        def boom(_at):
            raise RuntimeError("desk copy failed")

        session._remember_desk = boom
        with self.assertLogs("ai_floor.exness", level="WARNING") as caught:
            session.manage(at)
        self.assertEqual(seen, [True, False])
        self.assertIsNone(broker._read_cache)
        self.assertTrue(any("DESK_REMEMBER_FAILED" in line for line in caught.output))

    def test_snapshot_stores_that_desk_and_the_panel_reads_it(self):
        from runtime.service import OperationalRuntime as TradingService
        desk = {"account_state": {"balance": "1000", "equity": "1004", "currency": "USC"},
                "positions": [{"position_id": "55", "instrument": "EURUSD", "side": "LONG",
                               "volume": "0.01", "open_price": "1.08000",
                               "stop_loss_price": "9.99", "take_profit_price": "8.88"}],
                "orders": [{"type": "sltp", "position_id": "55",
                            "stop_loss_price": "1.07000", "take_profit_price": "1.11000"}],
                "point": {"at": "2026-06-02T15:00:00+00:00", "equity": "1004"}}
        held = {"prior": {"curve": [{"at": "2026-06-01T15:00:00+00:00", "equity": "1000"}]}}

        class Store:
            def load_snapshot(self, symbol):
                self.asked = symbol
                return held["prior"]

        holder = type("Holder", (), {"exness_session": type("S", (), {"desk": desk})(), "store": Store()})()
        first = TradingService._with_desk(holder, {"symbol": "EURUSD", "state": "COMPLETED"})
        self.assertEqual(holder.store.asked, "EURUSD")
        self.assertEqual(first["account_state"]["balance"], "1000")
        self.assertEqual(first["curve"], [
            {"at": "2026-06-01T15:00:00+00:00", "equity": "1000"},
            {"at": "2026-06-02T15:00:00+00:00", "equity": "1004"}])
        held["prior"] = first
        second = TradingService._with_desk(holder, {"symbol": "EURUSD", "state": "COMPLETED"})
        self.assertEqual(len(second["curve"]), 2)
        bare = type("Holder", (), {"exness_session": None, "store": Store()})()
        untouched = TradingService._with_desk(bare, {"symbol": "EURUSD", "state": "NO_DATA"})
        self.assertNotIn("account_state", untouched)
        self.assertNotIn("curve", untouched)
        view = build_dashboard(env=ENV, now=NOW, flags={}, snapshot=first)
        self.assertIn("1,004.00 USC", view["kpis"]["equity"]["text"])
        self.assertEqual(view["positions"]["rows"][0]["sl"], "1.07")
        self.assertNotIn("9.99", json.dumps(view["positions"]["rows"][0]))
        self.assertEqual(view["curve"]["state"], "ok")


if __name__ == "__main__":
    unittest.main()
