"""Liquid Glass view model, SL/TP join (bug #95), and a static render smoke. No network and no flag writes."""
from datetime import datetime, timedelta, timezone
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
            "freshness": "NO_DATA", "market_data": "exness",
            "runtime_heartbeat": "2026-11-21T15:59:00+00:00"}, errors=[])
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
        self.assertEqual(older["reason"], MISSING)
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

    def test_weekend_health_stays_degraded_without_a_fresh_heartbeat_or_provider(self):
        saturday = datetime(2026, 11, 21, 16, 0, tzinfo=timezone.utc)
        base = {"status": "DEGRADED", "scheduler": "RUNNING", "last_cycle_status": "COMPLETED",
                "freshness": "STALE_DATA", "market_data": "exness"}
        missing = _view(now=saturday, health=dict(base, runtime_heartbeat="NO_DATA"))
        self.assertEqual(missing["system"]["status"], "Degradado")
        stale = _view(now=saturday, health=dict(base, runtime_heartbeat="2026-11-20T15:00:00+00:00"))
        self.assertEqual(stale["system"]["status"], "Degradado")
        unconfigured = _view(now=saturday, health=dict(
            base, market_data="NOT CONFIGURED", runtime_heartbeat="2026-11-21T15:59:30+00:00"))
        self.assertEqual(unconfigured["system"]["status"], "Degradado")
        closed = _view(now=saturday, health=dict(base, runtime_heartbeat="2026-11-21T15:59:30+00:00"))
        self.assertEqual(closed["system"]["status"], "Mercado cerrado")

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
        self.assertEqual(view["history"]["stats"]["count"], "0")
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

    def test_header_hides_the_cent_label_and_pins_the_badge_to_the_logo(self):
        from ui.exness_present import LABEL
        view = example_view()
        html = fragment(view)
        self.assertEqual(view["title"], LABEL)
        self.assertEqual(view["safety_short"], "Ejecución real activa")
        self.assertTrue(view["live"])
        self.assertNotIn(LABEL, html)
        self.assertIn("RESULTADO USC", html)
        self.assertIn("USC", html)
        mob = html.split('class="mob-bar"', 1)[1].split("</header>", 1)[0]
        desk = html.split('class="desk-head"', 1)[1].split("</header>", 1)[0]
        side = html.split('class="logo"', 1)[1].split("</div>", 1)[0]
        for chunk in (mob, desk, side):
            self.assertNotIn("Exness Standard Cent", chunk)
        self.assertIn("Ejecución real activa", mob)
        self.assertIn("Ejecución real activa", desk)
        self.assertLess(mob.index('class="mark"'), mob.index('class="live"'))
        self.assertNotIn("live off", mob)
        off = fragment(_view())
        self.assertIn("Ejecución real desactivada", off)
        self.assertIn('class="live off"', off)
        css = Path("ui/static/liquid/glass.css").read_text(encoding="utf-8")
        self.assertIn(".mob-bar{display:none;", css)
        self.assertIn("flex-wrap:nowrap", css)
        self.assertNotIn("flex:1 1 100%", css)
        self.assertIn(".mob-bar .mark{height:41px", css)
        self.assertIn(".desk-head .mark{height:52px}", css)
        self.assertIn(".mob-bar .tools{margin-left:auto", css)

    def test_menu_backdrop_starts_hidden_and_tabs_clear_the_bar(self):
        html = fragment(example_view())
        body = html.split("</style>", 1)[1].split("<script>", 1)[0]
        self.assertIn('<div class="aif-backdrop" hidden></div>', body)
        self.assertNotIn("data-open", body)
        css = Path("ui/static/liquid/glass.css").read_text(encoding="utf-8")
        js = Path("ui/static/liquid/panel.js").read_text(encoding="utf-8")
        self.assertIn(".lg:not([data-open=\"1\"]) .aif-backdrop{display:none !important;pointer-events:none !important}", css)
        self.assertIn("pointer-events:auto !important", css)
        self.assertIn("padding-bottom:98px", css)
        self.assertIn("padding-bottom:calc(98px + env(safe-area-inset-bottom))", css)
        self.assertIn('[data-panel="Riesgo"] > .card + .card{margin-top:14px}', css)
        self.assertIn('ev.key !== "Escape"', js)
        self.assertIn("menu(false)", js)
        self.assertIn("window.scrollTo(0, 0);\n      remember();", js)
        self.assertIn("sessionStorage.getItem(scrollKey)", js)

    def test_example_banner_is_only_on_the_screenshot_view(self):
        view = example_view()
        html = document(view, theme="dark")
        self.assertIn(BANNER, html)
        self.assertIn('data-theme="dark"', html)
        self.assertIn("20.19", html)
        self.assertIn("Omitida", html)
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
        self.assertLess(page.index("st.markdown(_CHROME"), page.index("fragment(load("))


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


class HistorySquareTests(unittest.TestCase):
    """The 25 Nov USDJPY is filled, not exited, and its P/L is a deal amount. Table and summary both keep it."""

    def _flags(self):
        def done(day, symbol, side, pnl, ticket):
            return json.dumps({
                "status": "done", "exited": True, "symbol": symbol, "side": side,
                "position_id": ticket, "volume": "0.40", "pnl": pnl,
                "opened_at": f"2026-11-{day}T12:00:00+00:00",
                "exit_at": f"2026-11-{day}T16:00:00+00:00",
            })

        return {
            "combo_lon_2026-11-27": done("27", "USDJPY", "LONG", "181.46", "t27"),
            "combo_lon_2026-11-25": json.dumps({
                "status": "filled", "exited": False, "symbol": "USDJPY", "side": "SHORT",
                "position_id": "440549", "volume": "1.19", "client_id": "combo-lon-2026-11-25",
                "opened_at": "2026-11-25T07:30:39+00:00", "exit_at": "2026-11-25T16:07:49+00:00",
                "entry": "159.092", "exit_price": "159.119", "exit_reason": "hq_manual",
                "pnl": {"amount": "-20.19", "currency": "USC"},
            }),
            "combo_lon_2026-11-24": done("24", "USDJPY", "LONG", "-139.89", "t24"),
            "combo_fix_2026-11-23": done("23", "GBPUSD", "LONG", "-13.00", "f23"),
            "combo_lon_2026-11-23": done("23", "USDJPY", "LONG", "380.76", "t23"),
            "combo_fix_2026-11-20": done("20", "GBPUSD", "LONG", "-9", "f20"),
            "combo_lon_2026-11-20": done("20", "USDJPY", "SHORT", "116.42", "t20"),
            "combo_lon_2026-11-19": done("19", "USDJPY", "LONG", "146.19", "t19"),
            "combo_lon_2026-11-18": done("18", "USDJPY", "SHORT", "-84.08", "t18"),
            "combo_fix_2026-11-26": json.dumps({"status": "skipped"}),
            "combo_fix_2026-11-22": json.dumps({"status": "done", "exited": False}),
            "combo_lon_2026-11-21": json.dumps({
                "status": "filled", "exited": False, "symbol": "USDJPY", "side": "LONG",
                "position_id": "still-open", "opened_at": "2026-11-21T12:00:00+00:00",
            }),
        }

    def test_table_and_summary_include_the_nov_25_deal_and_hide_ghosts(self):
        now = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)
        view = _view(now=now, flags=self._flags())
        rows = view["history"]["rows"]
        symbols = [(row["day"], row["symbol"], row["pnl"]) for row in rows]
        self.assertEqual(len(rows), 9)
        self.assertTrue(any(row["day"].startswith("25 ") and row["symbol"] == "USDJPY" and "20.19" in row["pnl"] for row in rows))
        self.assertFalse(any(row["symbol"] == MISSING and row["pnl"] == MISSING for row in rows))
        self.assertNotIn("still-open", [row["ticket"] for row in rows])
        stats = view["history"]["stats"]
        self.assertEqual(stats["count"], "9")
        self.assertEqual(len(rows), int(stats["count"]))
        self.assertIn("558.67", stats["net"])
        self.assertEqual(stats["net_tone"], "pos")
        html = fragment(view, section="Historial")
        self.assertIn("25 nov 2026", html)
        self.assertIn("20.19", html)
        self.assertIn("Ganancia bruta", html)
        self.assertNotIn("Ganancia total", html)
        self.assertIn("−139.89 USC · −1.40 USD", html)
        self.assertNotIn("−139.89 USC · 1.40 USD", html)
        self.assertNotIn("Sin operación", html)
        self.assertEqual(len(symbols), 9)

    def test_rendimiento_has_one_heading_and_opens_on_thirty_days(self):
        from ui.liquid.preview import example_view
        html = fragment(example_view(), section="Historial")
        self.assertEqual(html.count("<h2>Rendimiento</h2>"), 1)
        self.assertIn('data-chart="30"', html)
        self.assertNotIn('data-chart="30" hidden', html)
        self.assertIn('data-chart="all" hidden', html)
        self.assertIn('data-chart="90" hidden', html)
        self.assertIn('data-chart="365" hidden', html)
        view = example_view()
        points = view["curve"]["points"]
        self.assertEqual(view["curve"]["state"], "ok")
        self.assertEqual(view["curve"]["ranges"]["30"]["state"], "ok")
        self.assertEqual(len(points), 10)
        self.assertEqual(points[0]["equity"], "30086")
        self.assertEqual(points[-1]["equity"], "30644.67")
        self.assertIn("558.67", view["history"]["stats"]["net"])
        self.assertIn("558.60", view["kpis"]["total"]["text"])
        self.assertIn("30,644.60", view["kpis"]["balance"]["text"])
        order = ["473902", "440549", "424576", "415945", "409759", "382026", "375978", "357063", "341984"]
        self.assertEqual([row["ticket"] for row in view["history"]["rows"]], order)
        for row in view["history"]["rows"]:
            self.assertNotEqual(row["entry"], MISSING)
            self.assertNotEqual(row["exit_price"], MISSING)
            self.assertNotEqual(row["lots"], MISSING)
            self.assertNotIn(row["side"], (MISSING, ""))
        table = html.split("<h2>Detalle de operaciones</h2>", 1)[1].split("<h2>Estadísticas del periodo</h2>", 1)[0]
        self.assertNotIn("sin datos", table.lower())
        self.assertIn('data-range="30"', html)
        self.assertNotIn("No hay una serie de equity guardada", html)
        self.assertIn("<svg", html)

    def test_a_short_stored_curve_uses_the_closed_history(self):
        flags = {
            "exness_brake_start": "10000",
            "exness_launch_start": "2026-11-16T21:00:00+00:00",
            "combo_lon_2026-11-18": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "pnl": "-84.08",
                "exit_at": "2026-11-18T16:00:13+00:00",
            }),
            "combo_lon_2026-11-19": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "pnl": "146.19",
                "exit_at": "2026-11-19T16:00:07+00:00",
            }),
        }
        now = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)
        one = _view(now=now, flags=flags, curve=[{"at": "2026-11-20T12:00:00+00:00", "equity": "1"}])
        self.assertEqual(one["curve"]["state"], "ok")
        self.assertEqual(one["curve"]["points"][0]["equity"], "10000")
        self.assertEqual(one["curve"]["points"][-1]["equity"], "10062.11")
        kept = _view(now=now, flags=flags, curve=[
            {"at": "2026-11-18T16:00:00+00:00", "equity": "10000"},
            {"at": "2026-11-19T16:00:00+00:00", "equity": "10010"},
        ])
        self.assertEqual(kept["curve"]["points"][-1]["equity"], "10010")

    def test_history_sorts_by_close_time_and_breaks_ties_by_ticket(self):
        now = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)
        same = "2026-11-23T16:00:00+00:00"
        flags = {
            "combo_lon_2026-11-23": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "pnl": "1",
                "position_id": "100", "exit_at": same, "opened_at": "2026-11-23T12:00:00+00:00",
            }),
            "combo_fix_2026-11-23": json.dumps({
                "status": "done", "exited": True, "symbol": "GBPUSD", "pnl": "2",
                "position_id": "200", "exit_at": same, "opened_at": "2026-11-23T08:00:00+00:00",
            }),
            "combo_lon_2026-11-24": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "pnl": "3",
                "position_id": "50", "exit_at": "2026-11-24T16:00:00+00:00",
                "opened_at": "2026-11-24T07:00:00+00:00",
            }),
        }
        view = _view(now=now, flags=flags)
        self.assertEqual([row["ticket"] for row in view["history"]["rows"]], ["50", "200", "100"])

    def test_a_loss_signs_both_the_usc_and_the_usd(self):
        from decimal import Decimal
        from ui.liquid.model import _signed
        text, tone = _signed(Decimal("-139.89"), "USC")
        self.assertEqual(text, "−139.89 USC · −1.40 USD")
        self.assertEqual(tone, "neg")
        plus, plus_tone = _signed(Decimal("181.46"), "USC")
        self.assertEqual(plus, "+181.46 USC · 1.81 USD")
        self.assertEqual(plus_tone, "pos")

    def test_closed_leg_keeps_lots_entry_exit_and_reason(self):
        now = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)
        flags = {
            "combo_lon_2026-11-27": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "side": "LONG",
                "position_id": "t27", "volume": "1.88", "entry": "159.39", "exit_price": "159.544",
                "pnl": "181.46", "exit_reason": "london_1100",
                "opened_at": "2026-11-27T12:00:00+00:00", "exit_at": "2026-11-27T16:00:00+00:00",
            }),
            "combo_lon_2026-11-25": json.dumps({
                "status": "done", "exited": True, "symbol": "USDJPY", "side": "SHORT",
                "position_id": "440549", "volume": "1.19",
                "entry": "159.092", "exit_price": "159.119", "exit_reason": "london_1100",
                "opened_at": "2026-11-25T07:30:39+00:00", "exit_at": "2026-11-25T16:07:49+00:00",
                "pnl": "-20.19",
            }),
        }
        view = _view(now=now, flags=flags)
        by_day = {row["day"][:2]: row for row in view["history"]["rows"]}
        nov27 = by_day["27"]
        self.assertEqual(nov27["lots"], "1.88")
        self.assertEqual(nov27["entry"], "159.39")
        self.assertEqual(nov27["exit_price"], "159.544")
        self.assertEqual(nov27["reason"], "cierre 11:00 ET")
        self.assertIn("181.46", nov27["pnl"])
        nov25 = by_day["25"]
        self.assertEqual(nov25["side"], "Venta")
        self.assertEqual(nov25["lots"], "1.19")
        self.assertEqual(nov25["entry"], "159.092")
        self.assertEqual(nov25["exit_price"], "159.119")
        self.assertEqual(nov25["reason"], "cierre manual HQ")
        html = fragment(view, section="Historial")
        self.assertIn("1.88", html)
        self.assertIn("159.544", html)
        self.assertIn("cierre 11:00 ET", html)
        self.assertIn("cierre manual HQ", html)

    def test_parts_sum_to_one_row(self):
        now = datetime(2026, 11, 27, 21, 0, tzinfo=timezone.utc)
        flags = {"combo_lon_2026-11-25": json.dumps({
            "status": "filled", "exited": False, "position_id": None,
            "opened_at": "2026-11-25T07:10:00+00:00", "exit_at": "2026-11-25T16:00:00+00:00",
            "parts": [
                {"symbol": "USDJPY", "side": "LONG", "profit": {"amount": "-12.00", "currency": "USC"}},
                {"symbol": "USDJPY", "side": "LONG", "profit": {"amount": "-8.19", "currency": "USC"}},
            ],
        })}
        view = _view(now=now, flags=flags)
        self.assertEqual(len(view["history"]["rows"]), 1)
        row = view["history"]["rows"][0]
        self.assertEqual(row["symbol"], "USDJPY")
        self.assertEqual(row["side"], "Compra")
        self.assertIn("20.19", row["pnl"])
        self.assertEqual(view["history"]["stats"]["count"], "1")
        self.assertIn("20.19", view["history"]["stats"]["net"])


class EmptyBookTests(unittest.TestCase):
    def test_a_fresh_empty_mark_is_zero_and_a_missing_read_is_sin_datos(self):
        now = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)
        fresh = json.dumps({"at": now.isoformat(), "positions": []})
        flags = {"exness_link": "1", "exness_link_at": now.isoformat(), "exness_live_marks": fresh}
        snapshot = {"as_of": now.isoformat(), "account_state": {"balance": "10000", "equity": "10000", "currency": "USC"}}
        view = _view(now=now, flags=flags, snapshot=snapshot)
        self.assertEqual(view["positions"]["state"], "empty")
        self.assertIn("0.00", view["positions"]["floating"])
        self.assertNotEqual(view["positions"]["floating"], MISSING)
        self.assertEqual(view["active"], "0")
        html = fragment(view, section="Posiciones")
        self.assertIn("Sin posiciones abiertas.", html)
        self.assertNotIn("El libro de posiciones no está en el panel", html)

        stale_at = (now - timedelta(minutes=46)).isoformat()
        stale = _view(now=now, flags={"exness_live_marks": json.dumps({"at": stale_at, "positions": []})}, snapshot={})
        self.assertEqual(stale["positions"]["state"], "sin datos")
        self.assertEqual(stale["positions"]["note"], "El libro de posiciones no está en el panel.")
        self.assertEqual(stale["positions"]["floating"], MISSING)

        down = _view(now=now, flags={"exness_link": "0", "exness_live_marks": fresh}, snapshot={})
        self.assertEqual(down["positions"]["state"], "sin datos")
        self.assertEqual(down["positions"]["floating"], MISSING)

        absent = _view(now=now, flags={}, snapshot={"as_of": now.isoformat()})
        self.assertEqual(absent["positions"]["state"], "sin datos")

        listed = _view(now=now, snapshot={"positions": [], "orders": []})
        self.assertEqual(listed["positions"]["state"], "empty")
        self.assertIn("0.00", listed["positions"]["floating"])


if __name__ == "__main__":
    unittest.main()
