"""Read-only "Floor" page: payload builder (real values / None, never invented), escaping, no secrets, sample-mode
example data only with the EJEMPLO marker, navigation wiring, and no order-placing imports."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from execution.contracts import PaperAccount
from storage.codec import paper_encode
from storage.database import Store
from ui.floor_payload import AGENT_IDS, build_floor_payload, payload_json, signals_and_hit_rate
from ui.state import dashboard_database
from ui.views import floor_view

ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 20, 13, 30, tzinfo=timezone.utc)  # 09:30 ET
T_RUN = "2026-10-20T12:00:00+00:00"
T_OPEN = "2026-10-20T12:01:00+00:00"
ACCOUNT_ID = "paper-acct-SECRET-42"
OANDA_ACCOUNT = "101-000-1234567-001"  # synthetic (test_security_audit.SYNTHETIC_ACCOUNTS)
TOKEN = "tok-abcdef0123456789abcdef0123456789-secret"
# Figures of the approved prototype (mockup v4): only ever shown in sample mode, never hard-coded in the asset.
EXAMPLE_FIGURES = ("100,412.50", "100412", "412.50", "1.08724", "1.08720", "1.08520", "1.09120", "1.0851",
                   "Día 3 de 30", "25,000", "+0.42 R", "0.83", "−0.61%", "BOS H1", "Sweep Asia", "DXY 104.2",
                   "US10Y", "20 Oct 08:01", "$500.00", "+$210.00", "G1–G11", "score 0.12")
FORBIDDEN_MODULES = ("execution.paper_broker", "execution.oanda_broker",
                     "execution.oanda_client", "execution.broker_demo_router", "execution.broker_port",
                     "execution.dxtrade_broker", "execution.dxtrade_client", "execution.operator_actions",
                     "execution.pending_order_gate", "execution.oanda_readonly", "execution.demo_dashboard",
                     "runtime.demo_runner", "runtime.service")
FLOOR_MODULES = ("ui/floor_payload.py", "ui/floor_cache.py", "ui/views/floor_view.py", "ui/fixtures/floor_example.py")


def seed(path):
    store = Store(path)
    try:
        db = store.db
        db.execute("INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,completed_at,status,final_status) "
                   "VALUES(?,?,?,?,?,?,?,?)", ("slot-e1", "run-e1", "EURUSD", T_RUN, T_RUN, T_OPEN, "COMPLETED",
                                              "PLAN_READY"))
        db.execute("INSERT INTO agent_decisions(slot_key,agent_name,as_of,status,bias,confidence,reasoning_summary) "
                   "VALUES(?,?,?,?,?,?,?)", ("slot-e1", "structure_ai", T_RUN, "OK", "BULLISH", 0.7,
                                             "estructura alcista real"))
        db.execute("INSERT INTO setups(slot_key,status,side,evidence,plan) VALUES(?,?,?,?,?)",
                   ("slot-e1", "VALID_SETUP", "LONG", "[]", json.dumps(
                       {"run_id": "run-e1", "entry": 1.1000, "stop": 1.0980, "target": 1.1060, "risk_reward": 3.0})))
        db.execute("INSERT INTO risk_decisions(slot_key,status,reason,risk_fraction,capital_at_risk,entry,stop,target)"
                   " VALUES(?,?,?,?,?,?,?,?)", ("slot-e1", "APPROVED", "dentro de límites", 0.01, 100.0, 1.1,
                                                1.098, 1.106))
        db.execute("INSERT INTO paper_accounts VALUES(?,?)", (ACCOUNT_ID, paper_encode(
            PaperAccount("1.0", ACCOUNT_ID, 10000, 10020, 10055.5, 20, 5))))
        db.execute("INSERT INTO paper_positions VALUES(?,?)", ("pos-open", json.dumps({
            "position_id": "pos-open", "origin_order_id": "o1", "run_id": "run-e1", "symbol": "EURUSD",
            "side": "LONG", "schema_version": "1.0", "quantity": 1000, "planned_entry": 1.1000, "entry_price": 1.1002,
            "stop": 1.0980, "target": 1.1060, "opened_at": T_OPEN, "status": "OPEN", "last_price": 1.1010,
            "contract_multiplier": 1})))
        db.execute("INSERT INTO paper_positions VALUES(?,?)", ("pos-old", json.dumps({
            "position_id": "pos-old", "origin_order_id": "o0", "run_id": "run-e0", "symbol": "EURUSD",
            "side": "LONG", "schema_version": "1.0", "quantity": 1000, "planned_entry": 1.0900, "entry_price": 1.0900,
            "stop": 1.0880, "target": 1.0960, "opened_at": "2026-10-19T12:00:00+00:00", "status": "CLOSED",
            "contract_multiplier": 1})))
        db.execute("INSERT INTO closed_trades VALUES(?,?)", ("trade-old", json.dumps({
            "trade_id": "trade-old", "position_id": "pos-old", "run_id": "run-e0", "symbol": "EURUSD", "side": "LONG",
            "entry_price": 1.0900, "exit_price": 1.0960, "quantity": 1000, "gross_pnl": 6.0, "cost": 0.0,
            "net_pnl": 6.0, "exited_at": "2026-10-19T15:00:00+00:00", "reason": "TARGET"})))
        db.execute("INSERT INTO journal(timestamp,run_id,symbol,source,event_type,severity,payload) "
                   "VALUES(?,?,?,?,?,?,?)", (T_OPEN, "run-e1", "EURUSD", "paper", "POSITION_OPENED", "INFO", "{}"))
        store.save_snapshot("EURUSD", {
            "schema_version": "1.0", "symbol": "EURUSD", "run_id": "run-e1", "state": "PLAN_READY", "as_of": T_RUN,
            "freshness": "CURRENT", "setup_status": "VALID_SETUP", "risk_status": "APPROVED",
            "agents": [{"name": "Macro AI", "status": "OK", "bias": "NEUTRAL", "confidence": 0.55,
                        "reasoning_summary": "sin eventos de alto impacto", "timestamp": T_RUN}]})
    finally:
        store.close()


def fake_demo(stats=True, closed_rows=()):
    snapshot = {
        "epoch": NOW - timedelta(days=2), "nav": 100123.45, "balance": 100100.0, "currency": "USD",
        "capital": {"start": 100000.0, "change": 123.45}, "stale": False, "as_of": NOW, "trades_as_of": NOW,
        "trades_error": None, "curve": [
            {"Closed at": NOW, "Cumulative realized PnL": 50.0}, {"Closed at": NOW, "Cumulative realized PnL": 100.0}],
        "stats": {"trades": 2, "wins": 1, "total_pnl": 100.0, "today_pnl": 0.0, "max_drawdown": 30.0,
                  "avg_r": 0.25, "avg_r_reason": None} if stats else None,
        "open_rows": [{"trade_id": "77", "client_id": "aif-run-e1", "symbol": "EURUSD", "side": "LONG",
                       "units": 5000.0, "quantity": 5000.0, "opened_at": NOW, "entry_price": 1.10013, "stop": 1.098,
                       "target": 1.106, "unrealized_pnl": 12.5}],
        "closed_rows": list(closed_rows),
        "daily_loss": {"status": "OK", "fraction": 0.02, "used_pct": 25.0, "day_start_nav": 100000.0,
                       "pnl": -500.0, "loss": 500.0, "drawdown_halted": False}}
    return {"epoch": lambda: snapshot["epoch"], "snapshot": lambda _now, **_readers: snapshot,
            "progress": lambda _now: (snapshot["epoch"], NOW, 2)}


class FloorPayloadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "floor.db"

    def build(self, **kwargs):
        with dashboard_database(self.path) as db:
            return build_floor_payload(now=NOW, db=db, **kwargs)

    def test_no_database_is_all_none(self):
        payload = self.build(sample_mode=False)
        for key in ("source", "header", "trades", "cycle", "symbols", "feed", "market", "curve"):
            self.assertIsNone(payload[key], key)
        self.assertEqual(payload["agents"], dict.fromkeys(AGENT_IDS))
        self.assertEqual((payload["real_fields"], payload["example_fields"]), ([], []))

    def test_empty_database_never_raises_and_invents_nothing(self):
        Store(self.path).close()
        payload = self.build(sample_mode=False, enabled_symbols=("XAUUSD", "EURUSD"))
        self.assertEqual(payload["source"], "PAPER")
        self.assertTrue(all(value is None for value in payload["header"].values()), payload["header"])
        self.assertFalse(payload["trades"])
        self.assertIsNone(payload["cycle"])
        self.assertEqual(payload["agents"], dict.fromkeys(AGENT_IDS))
        self.assertEqual(payload["symbols"], {"EURUSD": "ACTIVE", "XAUUSD": "PAUSED"})  # DEC-XAU-OFF-1
        self.assertEqual(payload["real_fields"], ["symbols.EURUSD", "symbols.XAUUSD"])
        self.assertEqual(payload["example_fields"], [])

    def test_read_errors_yield_none_not_exceptions(self):
        Store(self.path).close()
        with patch("ui.views.luxury.ledger_statistics", side_effect=RuntimeError("boom")), \
                patch("ui.views.process.cycle_evidence", side_effect=ValueError("bad json")):
            payload = self.build(sample_mode=False)
        self.assertIsNone(payload["header"])
        broken = {"snapshot": lambda _now, **_readers: 1 / 0, "progress": lambda _now: 1 / 0}
        payload = self.build(sample_mode=False, demo=broken)
        self.assertEqual((payload["source"], payload["header"], payload["trades"]), ("OANDA_DEMO", None, None))

    def test_seeded_rows_give_real_values(self):
        seed(self.path)
        payload = self.build(sample_mode=False, enabled_symbols=("EURUSD",))
        header = payload["header"]
        self.assertEqual(header["equity"], 10055.5)
        self.assertEqual((header["pnl"], header["win_rate"], header["trades"]), (6.0, 100.0, 1))
        self.assertAlmostEqual(header["r_total"], 3.0)  # +6.0 on a 0.002 * 1000 risk
        self.assertIsNone(header["day_text"])  # PAPER (routing OFF): no demo clock
        opened = payload["trades"][0]
        self.assertEqual((opened["status"], opened["side"], opened["symbol"]), ("OPEN", "LONG", "EURUSD"))
        self.assertEqual((opened["entry_expected"], opened["entry_actual"]), (1.1, 1.1002))
        self.assertAlmostEqual(opened["slippage_pips"], 2.0)
        self.assertTrue(opened["slippage_against"])
        self.assertEqual((opened["sl_expected"], opened["tp_expected"], opened["r_planned"]), (1.098, 1.106, 3.0))
        self.assertEqual(opened["risk_expected"], 100.0)
        self.assertAlmostEqual(opened["pnl"], 0.8)  # (1.1010 - 1.1002) * 1000
        closed = payload["trades"][1]
        self.assertEqual((closed["status"], closed["pnl"]), ("CLOSED", 6.0))
        self.assertAlmostEqual(closed["r_realized"], 3.0)
        agents = payload["agents"]
        self.assertEqual(agents["Structure"]["confidence"], 0.7)
        self.assertEqual(agents["Structure"]["analysis"], "estructura alcista real")
        self.assertEqual(agents["Structure"]["bias"], "LONG")
        self.assertEqual(agents["Macro"]["analysis"], "sin eventos de alto impacto")  # from the persisted snapshot
        self.assertIn("dentro de límites", agents["RiskEng"]["analysis"])
        self.assertEqual(agents["Exec"]["bias"], "LONG")
        for agent_id in ("Liquidity", "Risk", "Reviewer", "Conflict"):
            self.assertIsNone(agents[agent_id], agent_id)
        steps = payload["cycle"]["steps"]
        self.assertEqual(len(steps), 8)
        self.assertTrue(steps[1]["done"])  # agent outputs persisted
        self.assertTrue(steps[4]["done"])  # risk decision persisted
        self.assertTrue(steps[7]["done"])  # run completed
        self.assertEqual(steps[7]["at"], "08:01")
        self.assertFalse(steps[3]["done"])  # no conflict evidence
        self.assertEqual(payload["curve"], [6.0])
        self.assertIn("EURUSD", payload["feed"][0])
        self.assertIn("header.equity", payload["real_fields"])
        self.assertIn("trades", payload["real_fields"])
        self.assertNotIn("header.day_text", payload["real_fields"])
        self.assertEqual(payload["example_fields"], [])

    def test_signals_link_by_exact_run_and_give_hit_rate(self):
        seed(self.path)
        store = Store(self.path)
        store.db.execute("INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status) VALUES(?,?,?,?,?,?)",
                         ("slot-e0", "run-e0", "EURUSD", "2026-10-19T11:00:00+00:00", "2026-10-19T11:00:00+00:00",
                          "COMPLETED"))
        store.db.execute("INSERT INTO agent_decisions(slot_key,agent_name,as_of,status,bias,confidence) "
                         "VALUES(?,?,?,?,?,?)", ("slot-e0", "structure_ai", "2026-10-19T11:00:00+00:00", "OK",
                                                 "BULLISH", 0.6))
        store.close()
        agent = self.build(sample_mode=False)["agents"]["Structure"]
        self.assertEqual(agent["hit_rate"], 100.0)  # one linked signal: LONG bias, LONG trade won
        self.assertEqual([s["outcome"] for s in agent["signals"]][0], "sin operación")  # run-e1 not closed yet
        self.assertAlmostEqual(agent["signals"][1]["r"], 3.0)

    def test_demo_signal_outcomes_come_from_demo_trades_only(self):
        seed(self.path)  # PAPER ledger: run-e0 closed with a win (must NOT leak into the demo metrics)
        store = Store(self.path)
        store.db.execute("INSERT INTO runs(slot_key,run_id,symbol,as_of,started_at,status) VALUES(?,?,?,?,?,?)",
                         ("slot-e0", "run-e0", "EURUSD", "2026-10-19T11:00:00+00:00", "2026-10-19T11:00:00+00:00",
                          "COMPLETED"))
        store.db.execute("INSERT INTO agent_decisions(slot_key,agent_name,as_of,status,bias,confidence) "
                         "VALUES(?,?,?,?,?,?)", ("slot-e0", "structure_ai", "2026-10-19T11:00:00+00:00", "OK",
                                                 "BULLISH", 0.6))
        store.close()
        agent = self.build(sample_mode=False, demo=fake_demo())["agents"]["Structure"]
        self.assertIsNone(agent["hit_rate"])  # no demo trade closed: the PAPER win is not counted
        self.assertEqual({s["outcome"] for s in agent["signals"]}, {"sin operación"})
        lost = {"trade_id": "9", "client_id": "aif-run-e0", "symbol": "EURUSD", "side": "LONG", "net_pnl": -40.0,
                "realized_r": -1.0}
        agent = self.build(sample_mode=False, demo=fake_demo(closed_rows=[lost]))["agents"]["Structure"]
        self.assertEqual(agent["hit_rate"], 0.0)  # the demo trade of run-e0 lost
        self.assertEqual(agent["signals"][1]["r"], -1.0)
        agent = self.build(sample_mode=False, demo=fake_demo(stats=False))["agents"]["Structure"]
        self.assertIsNone(agent["hit_rate"])  # demo trades unknown: outcomes unknown, never PAPER ones
        self.assertEqual({s["outcome"] for s in agent["signals"]}, {None})

    def test_breakeven_trade_is_neutral_in_the_hit_rate(self):
        rows = [{"bias": "BEARISH", "run_id": "r-be", "as_of": NOW}, {"bias": "BULLISH", "run_id": "r-win", "as_of": NOW},
                {"bias": "BULLISH", "run_id": "r-loss", "as_of": NOW}]
        closed = {"r-be": {"side": "LONG", "net_pnl": 0.0}, "r-win": {"side": "LONG", "net_pnl": 25.0},
                  "r-loss": {"side": "LONG", "net_pnl": -10.0}}
        _signals, hit_rate = signals_and_hit_rate(rows, closed)
        self.assertEqual(hit_rate, 50.0)  # opposite bias at breakeven is neither a hit nor a miss
        self.assertIsNone(signals_and_hit_rate(rows[:1], {"r-be": closed["r-be"]})[1])
        self.assertIsNone(signals_and_hit_rate([{**rows[0], "bias": "BULLISH"}], {"r-be": closed["r-be"]})[1])

    def test_demo_nav_freshness_is_kept(self):
        def equity(**changes):
            demo = fake_demo()
            snapshot = {**demo["snapshot"](NOW), **changes}
            header = self.build(sample_mode=False, demo={**demo, "snapshot": lambda _now, **_r: snapshot})["header"]
            return header["equity"], header["equity_stale"], header["equity_as_of"]
        self.assertEqual(equity(as_of=NOW - timedelta(seconds=30)), (100123.45, False, "09:29"))
        self.assertEqual(equity(as_of=NOW - timedelta(hours=2)), (100123.45, True, "07:30"))  # old cached read
        self.assertEqual(equity(as_of=NOW, stale=True), (100123.45, True, "09:30"))  # last good read after an error
        self.assertEqual(equity(as_of=None), (None, None, None))  # unknown observation time: never shown as current
        html = floor_view.render_html(self.build(sample_mode=False, demo=fake_demo()))
        self.assertIn("(dato antiguo · ${HD.equity_as_of} ET)", html)

    def test_demo_trades_freshness_is_tracked(self):
        def trades(**changes):
            demo = fake_demo(stats=changes.pop("stats", True))
            snapshot = {**demo["snapshot"](NOW), **changes}
            payload = self.build(sample_mode=False, demo={**demo, "snapshot": lambda _now, **_r: snapshot})
            return payload["header"]["trades_stale"], payload["header"]["trades_as_of"], payload["trades"]
        stale, as_of, rows = trades(trades_as_of=NOW - timedelta(seconds=30))
        self.assertEqual((stale, as_of), (False, "09:29"))
        self.assertTrue(rows)
        stale, as_of, rows = trades(trades_as_of=NOW - timedelta(hours=2))
        self.assertEqual((stale, as_of), (True, "07:30"))  # old cached read: still shown, marked old
        self.assertTrue(rows)
        self.assertEqual(trades(trades_as_of=None)[:2], (True, None))  # unknown observation time: never current
        self.assertEqual(trades(stats=False, trades_as_of=NOW - timedelta(hours=2)), (None, None, None))
        html = floor_view.render_html(self.build(sample_mode=False, demo=fake_demo()))
        self.assertIn("HD.trades_stale===true", html)
        self.assertIn("(dato antiguo${HD.trades_as_of?` · ${HD.trades_as_of} ET`:''})", html)

    def test_peeked_trades_carry_their_observation_time(self):
        from ui import floor_cache
        clock = [1000.0]
        holder = SimpleNamespace(_clock=lambda: clock[0], _cache={})
        fake = SimpleNamespace(_TRADES=holder)
        with patch.dict(sys.modules, {floor_cache.READONLY_MODULE: fake}):
            self.assertIsNone(floor_cache.peek_demo_trades("epoch"))
            read_at = NOW - timedelta(minutes=10)
            holder._cache["epoch"] = (700.0, {"closed": [], "open": [], "as_of": read_at})
            self.assertEqual(floor_cache.peek_demo_trades("epoch")["observed_at"], read_at)  # the read's own time
            holder._cache["epoch"] = (700.0, {"error": "deadline"})  # no as_of: store time on the cache clock
            observed = floor_cache.peek_demo_trades("epoch")["observed_at"]
            age = (datetime.now(timezone.utc) - observed).total_seconds()
            self.assertTrue(299 <= age <= 330, age)
            holder._clock = None  # unusable clock: unknown, never "now"
            self.assertIsNone(floor_cache.peek_demo_trades("epoch")["observed_at"])
            self.assertEqual(holder._cache["epoch"], (700.0, {"error": "deadline"}))  # the cache is never changed

    def test_demo_routing_uses_oanda_view_never_paper_numbers(self):
        seed(self.path)
        payload = self.build(sample_mode=False, demo=fake_demo())
        header = payload["header"]
        self.assertEqual(payload["source"], "OANDA_DEMO")
        self.assertEqual((header["equity"], header["pnl"], header["win_rate"]), (100123.45, 100.0, 50.0))
        self.assertAlmostEqual(header["r_total"], 0.5)
        self.assertAlmostEqual(header["drawdown_pct"], 0.03)
        self.assertAlmostEqual(header["daily_loss_used_pct"], 0.5)  # 25 % of a 2 % limit
        self.assertEqual(header["daily_loss_limit_pct"], 2.0)
        self.assertEqual(header["day_text"], "Día 2 de 30")
        trade = payload["trades"][0]
        self.assertEqual((trade["id"], trade["entry_actual"], trade["entry_expected"]), ("77", 1.10013, 1.1))
        self.assertAlmostEqual(trade["slippage_pips"], 1.3)
        self.assertEqual(trade["r_planned"], 3.0)  # plan of the run linked by the client id aif-<run_id>
        self.assertNotIn(10055.5, header.values())
        unknown = self.build(sample_mode=False, demo=fake_demo(stats=False))
        self.assertIsNone(unknown["header"]["pnl"])
        self.assertIsNone(unknown["trades"])

    def demo_env(self):
        return {"AI_FLOOR_BROKER_DEMO_ROUTING": "1", "OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": OANDA_ACCOUNT}

    def test_routing_on_with_empty_cache_never_calls_the_broker(self):
        from execution import oanda_readonly
        from ui.views.luxury import demo_api
        seed(self.path)
        transport = Mock(side_effect=AssertionError("network must not be touched"))
        with patch.dict(os.environ, self.demo_env()), patch.object(oanda_readonly, "urllib_transport", transport), \
                patch.object(oanda_readonly._VIEWS, "_cache", {}), patch.object(oanda_readonly._TRADES, "_cache", {}):
            payload = self.build(sample_mode=False, demo=demo_api())
            self.assertEqual((oanda_readonly._VIEWS._cache, oanda_readonly._TRADES._cache), ({}, {}))  # not filled
        transport.assert_not_called()
        self.assertEqual(payload["source"], "OANDA_DEMO")
        self.assertIsNone(payload["trades"])
        self.assertTrue(all(payload["header"][k] is None for k in ("equity", "pnl", "win_rate", "r_total")))
        self.assertIsNone(payload["agents"]["Structure"]["hit_rate"])
        self.assertNotIn(10055.5, payload["header"].values())  # never the PAPER equity

    def test_routing_on_reads_the_stored_cache_without_refreshing(self):
        from execution import oanda_readonly
        from ui.views.luxury import demo_api
        seed(self.path)
        view = oanda_readonly.AccountView("OK", "practice", "***", "USD", 100050, 100070, 0, 0, 0,
                                          as_of=NOW - timedelta(hours=1), stale=False)
        trades = {"closed": [], "open": [], "as_of": NOW - timedelta(hours=1)}
        transport = Mock(side_effect=AssertionError("network must not be touched"))
        with patch.dict(os.environ, self.demo_env()), patch.object(oanda_readonly, "urllib_transport", transport), \
                patch.object(oanda_readonly._VIEWS, "_cache", {"practice": (-1e9, view)}), \
                patch.object(oanda_readonly._TRADES, "_cache", {}):
            demo = demo_api()
            oanda_readonly._TRADES._cache[demo["epoch"]()] = (-1e9, trades)  # long expired: a refresh would GET
            payload = self.build(sample_mode=False, demo=demo)
        transport.assert_not_called()
        header = payload["header"]
        self.assertEqual((header["equity"], header["equity_stale"], header["equity_as_of"]), (100070.0, True, "08:30"))
        self.assertEqual(header["trades"], 0)  # the stored (empty) trades read, not refreshed
        self.assertEqual((header["trades_stale"], header["trades_as_of"]), (True, "08:30"))  # an hour-old read

    def test_sample_mode_fills_only_missing_sections_and_marks_them(self):
        seed(self.path)
        payload = self.build(sample_mode=True, enabled_symbols=("EURUSD", "XAUUSD"))
        self.assertEqual(payload["header"]["equity"], 10055.5)  # real value kept
        self.assertNotIn("header.equity", payload["example_fields"])
        self.assertIn("header.day_text", payload["example_fields"])
        self.assertIn("agents.Liquidity", payload["example_fields"])
        self.assertNotIn("agents.Structure", payload["example_fields"])
        self.assertNotIn("trades", payload["example_fields"])
        self.assertNotIn("header.day_text", payload["real_fields"])
        empty = build_floor_payload(sample_mode=True, now=NOW)
        self.assertIn("trades", empty["example_fields"])
        self.assertEqual(empty["header"]["day_text"], "Día 3 de 30")
        self.assertEqual(empty["real_fields"], [])
        self.assertEqual(build_floor_payload(sample_mode=False, now=NOW)["example_fields"], [])


class FloorHtmlTests(unittest.TestCase):
    def test_payload_is_escaped_and_injected_once(self):
        payload = build_floor_payload(now=NOW)
        payload["feed"] = ["</script><script>alert(1)</script><!--"]
        html = floor_view.render_html(payload)
        self.assertNotIn("__FLOOR_PAYLOAD__", html)
        data = html.split('<script id="floor-data" type="application/json">', 1)[1].split("</script>", 1)[0]
        self.assertNotIn("</", data)
        self.assertNotIn("<!--", data)
        self.assertEqual(json.loads(data)["feed"], payload["feed"])
        self.assertEqual(json.loads(payload_json({"x": "a</b"})), {"x": "a</b"})

    def test_asset_has_no_hard_coded_example_figures(self):
        asset = floor_view.ASSET.read_text(encoding="utf-8")
        self.assertEqual(asset.count(floor_view.PLACEHOLDER), 1)
        html = floor_view.render_html(build_floor_payload(sample_mode=False, now=NOW))
        for figure in EXAMPLE_FIGURES:
            with self.subTest(figure=figure):
                self.assertNotIn(figure, asset)
                self.assertNotIn(figure, html)
        self.assertNotRegex(asset, r"\$\d")  # no dollar amounts at all
        self.assertNotIn("EJEMPLO", json.dumps(build_floor_payload(sample_mode=False, now=NOW)))
        self.assertIn("1.08724", floor_view.render_html(build_floor_payload(sample_mode=True, now=NOW)))

    def test_no_fetch_or_external_resource_in_asset(self):
        asset = floor_view.ASSET.read_text(encoding="utf-8")
        for token in ("fetch(", "XMLHttpRequest", "WebSocket", "http://", "https://", "<link", "src=", "import("):
            self.assertNotIn(token, asset)

    def test_no_account_id_or_secret_in_payload_or_html(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "floor.db"
            seed(path)
            env = {"AI_FLOOR_OANDA_ACCOUNT_ID": OANDA_ACCOUNT, "AI_FLOOR_OANDA_TOKEN": TOKEN,
                   "AI_FLOOR_DASHBOARD_PASSWORD": "pw-secret-xyz"}
            with patch.dict(os.environ, env), dashboard_database(path) as db:
                for sample in (False, True):
                    for demo in (None, fake_demo()):
                        payload = build_floor_payload(sample_mode=sample, now=NOW, db=db, demo=demo)
                        text = floor_view.render_html(payload)
                        for secret in (ACCOUNT_ID, OANDA_ACCOUNT, TOKEN, "pw-secret-xyz", "aif-run-e1"):
                            self.assertNotIn(secret, text)
                        self.assertNotRegex(json.dumps(payload), r'"(account_id|client_id|token|password)"')
                        self.assertIsNone(re.search(r"\d{3}-\d{3}-\d{5,}-\d{3}", text))


class FloorWiringTests(unittest.TestCase):
    def test_app_registers_the_floor_page(self):
        source = (ROOT / "ui" / "app.py").read_text(encoding="utf-8")
        self.assertIn("from ui.views import floor_view", source)
        self.assertIn("luxury.PAGES + (floor_view.PAGE,) + dashboard.PAGES", source)
        # Inside the 60s auto-refresh fragment (like the luxury pages) so payload/freshness recompute each refresh.
        self.assertIn("if page == floor_view.PAGE:\n    @st.fragment(run_every=\"60s\" if refresh else None)\n"
                      "    def render_floor_view():\n        floor_view.render(st, enabled_symbols)\n"
                      "    render_floor_view()\n    st.stop()", source)
        self.assertEqual((floor_view.PAGE, floor_view.LABEL), ("Floor", "Floor"))

    def test_floor_modules_import_no_order_or_broker_path(self):
        for rel in FLOOR_MODULES:
            tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
            names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
            names += [node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            with self.subTest(module=rel):
                self.assertFalse([n for n in names if n.startswith(("execution", "broker")) or n in FORBIDDEN_MODULES],
                                 names)
        code = ("import sys, ui.views.floor_view, ui.floor_payload, ui.fixtures.floor_example\n"
                f"print([m for m in {FORBIDDEN_MODULES!r} if m in sys.modules])")
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=120,
                             env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.strip(), "[]")


class FloorPageAppTests(unittest.TestCase):
    """The page renders in the real app (behind the login), in both modes, and never writes the DB."""

    def test_floor_page_renders_read_only(self):
        from streamlit.testing.v1 import AppTest
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "floor.db"
            seed(path)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            env = {"AI_FLOOR_DB_PATH": str(path), "AI_FLOOR_HEALTH_DB_PATH": str(Path(tmp) / "health.db"),
                   "AI_FLOOR_REMOTE_DASHBOARD": "1", "RENDER": "false",
                   "AI_FLOOR_DASHBOARD_PASSWORD": "fixture-password"}
            transport = Mock(side_effect=AssertionError("network must not be touched"))
            with patch.dict(os.environ, env), patch("execution.oanda_readonly.urllib_transport", transport):
                for sample in (False, True):
                    app = AppTest.from_file(str(ROOT / "ui" / "app.py"), default_timeout=30)
                    app.session_state["dashboard_authenticated"] = True
                    app.session_state["dashboard_auth_expires_at"] = time.monotonic() + 3600
                    app.session_state["sample_mode"] = sample
                    app.run()
                    self.assertIn("Floor", app.radio[0].options)
                    app.radio[0].set_value("Floor").run()
                    with self.subTest(sample=sample):
                        self.assertFalse(app.exception, str(app.exception))
                        self.assertFalse(app.error, str(app.error))
                        caption = " ".join(c.value for c in app.caption)
                        self.assertIn("solo lectura", caption)
                        self.assertEqual("EJEMPLO" in caption, sample)
            transport.assert_not_called()
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)


if __name__ == "__main__":
    unittest.main()
