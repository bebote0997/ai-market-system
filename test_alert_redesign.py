"""DEC-ALERTS-REDESIGN-1: Floor-style alerts (runtime/trade_alerts.py, runtime/trade_alert_source.py) and their wiring.

Builders: Block Kit structure, colours, Spanish texts, link buttons, Gmail-safe dark HTML, no JSON dump, no PAPER
numbers. Wiring: broker-demo sink (rich trade alerts + email), durable Slack sink (macro folded, PAPER trades silent in
OANDA-demo mode), Phase 9 dispatcher (OANDA_ONLY). No network: every transport/SMTP/OANDA read is a stub.
"""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

from execution.broker_demo_state import SCHEMA as STATE_SCHEMA
from execution.oanda_readonly import AccountView
from runtime import broker_alerts as ba
from runtime import trade_alert_source as src
from runtime import trade_alerts as ta
from runtime.alert_catalog import alert_event, alert_event_id
from runtime.alert_channels import GmailEmailChannel
from runtime.alert_dispatch import AlertDispatcher
from runtime.notifications import NotificationEvent, SlackNotificationSink, compact_payload

WEBHOOK = "https://hooks.slack.com/services/T0/B0/x"
URL = "https://floor.example.com"
FLOOR = URL + "/?page=floor"
NOW = datetime(2026, 10, 21, 14, 0, tzinfo=timezone.utc)
SUMMARIES = [("structure_ai", "Ruptura bajista del mínimo de 15M con BOS confirmado."),
             ("liquidity_ai", "Barrida de liquidez sobre el máximo asiático."),
             ("macro_ai", "Dólar fuerte tras datos de empleo; sesgo bajista EUR.")]
FACTS = {"bias_1h": "bearish", "bos_15m": "bearish", "retracement_15m": None, "sweep_5m": True,
         "equal_levels_5m": True}
# setups.evidence of a VALID_SETUP as the deterministic validator persists it: [structure, liquidity] per timeframe.
EVIDENCE = [{"1h": {"bias": "bearish", "structure_state": "bearish"},
             "15m": {"bias": "bearish", "bos": {"type": "BOS", "direction": "bearish", "broken_level": 1.1225},
                     "retracement": None},
             "5m": {"bias": "bearish", "bos": None}},
            {"1h": {"sweeps": []}, "15m": {"sweeps": []},
             "5m": {"sweeps": [{"type": "high", "price": 1.1229}], "liquidity_above": [], "liquidity_below": []}}]


def opened(**kw):
    base = dict(kind="OPENED", symbol="EURUSD", side="SHORT", entry="1.12186", stop="1.12696", target="1.11166",
                risk_amount=500.0, risk_pct=0.5, session=("NEW_YORK",), why=ta.why_line(FACTS), demo_day=3,
                closes=tuple(1.12 + i * 0.0001 for i in range(30)), url=FLOOR)
    return ta.TradeAlert(**{**base, **kw})


def closed(r, pnl, reason="TP", **kw):
    return opened(kind="CLOSED", result_r=r, pnl=pnl, close_reason=reason, **kw)


def blocks(payload):
    return payload["attachments"][0]["blocks"]


class SlackBuilderTests(unittest.TestCase):
    def test_opened_short_block_kit(self):
        payload = ta.trade_slack_payload(opened())
        self.assertNotIn("text", payload)  # no duplicate top-level text
        attachment = payload["attachments"][0]
        self.assertEqual(attachment["color"], ta.RED)
        self.assertEqual([b["type"] for b in blocks(payload)], ["header", "section", "context", "actions"])
        self.assertEqual(blocks(payload)[0]["text"]["text"], "🔴 EURUSD SHORT abierta · OANDA Demo")
        lines = blocks(payload)[1]["text"]["text"].splitlines()
        self.assertEqual(lines[0], "Entrada 1.12186 · SL 1.12696 (51 pips) · TP 1.11166 (1:2)")
        self.assertEqual(lines[1], "Riesgo 0.5% ($500) · Sesión Nueva York")
        self.assertEqual(lines[2], "Por qué: Barrida de liquidez 5M + ruptura bajista 15M + estructura bajista 1H")
        self.assertLessEqual(len(lines[2]) - len("Por qué: "), ta.WHY_LIMIT)
        self.assertEqual(blocks(payload)[2]["elements"][0]["text"], "OANDA Demo · Día 3 de 30")

    def test_link_buttons_only(self):
        buttons = blocks(ta.trade_slack_payload(opened()))[-1]["elements"]
        self.assertEqual([b["text"]["text"] for b in buttons], ["Ver en el Floor", "Cerrar operación"])
        for button in buttons:
            self.assertEqual(button["type"], "button")
            self.assertEqual(button["url"], FLOOR)
            self.assertNotIn("value", button)  # url buttons: no interactive payload
        closed_buttons = blocks(ta.trade_slack_payload(closed(2.0, 1000)))[-1]["elements"]
        self.assertEqual([b["text"]["text"] for b in closed_buttons], ["Ver en el Floor"])
        self.assertNotEqual(blocks(ta.trade_slack_payload(opened(url=None)))[-1]["type"], "actions")

    def test_long_is_green(self):
        payload = ta.trade_slack_payload(opened(side="LONG", stop="1.11676", target="1.13206"))
        self.assertEqual(payload["attachments"][0]["color"], ta.GREEN)
        self.assertTrue(blocks(payload)[0]["text"]["text"].startswith("🟢 EURUSD LONG abierta"))

    def test_closed_colours_and_result(self):
        for alert, color, emoji in ((closed(2.0, 1000), ta.GREEN, "✅"), (closed(-1.0, -500, "SL"), ta.RED, "❌"),
                                    (closed(0.01, 3, "MANUAL_CLOSE"), ta.GREY, "➖")):
            with self.subTest(r=alert.result_r):
                payload = ta.trade_slack_payload(alert)
                self.assertEqual(payload["attachments"][0]["color"], color)
                self.assertTrue(blocks(payload)[0]["text"]["text"].startswith(emoji))
        text = ta.payload_text(ta.trade_slack_payload(closed(2.0, 1000)))
        self.assertIn("EURUSD SHORT cerrada · Take profit · +2.0R · OANDA Demo", text)
        self.assertIn("Resultado +2.0R (+$1,000) · Take profit", text)
        reasons = {"TIME_EXIT_MAX_HOLD": "Tiempo máximo de la operación", "MANUAL_CLOSE": "Cierre manual",
                   "REVERSAL_IN_PROFIT": "Reversión en beneficio",
                   "TIME_EXIT_FRIDAY_NY_CLOSE": "Salida por tiempo (cierre del viernes)"}
        for code, label in reasons.items():
            self.assertIn(label, ta.payload_text(ta.trade_slack_payload(closed(-0.4, -200, code))))

    def test_no_json_dump_no_paper_and_escaped(self):
        hostile = opened(why="<!channel> & <https://evil.example|x> `code` *bold* _it_ ~strike~")
        payload = ta.trade_slack_payload(hostile)
        # Untrusted text is plain_text only: never parsed as mrkdwn (no mentions, links or formatting), kept literal.
        texts = [b["text"] for b in blocks(payload) if "text" in b]
        texts += [e for b in blocks(payload) if b["type"] == "context" for e in b["elements"]]
        self.assertTrue(texts)
        self.assertEqual({t["type"] for t in texts}, {"plain_text"})
        self.assertNotIn("mrkdwn", json.dumps(payload))
        self.assertIn("Por qué: <!channel> & <https://evil.example|x> `code` *bold* _it_ ~strike~",
                      ta.payload_text(payload))
        macro = ta.daily_slack_payload(ta.DailyAlert(lines=(), macro="*Fed* _hawkish_ <!here>", demo_day=2))
        self.assertNotIn("mrkdwn", json.dumps(macro))
        for payload in (ta.trade_slack_payload(opened()), ta.trade_slack_payload(closed(2.0, 1000)),
                        ta.daily_slack_payload(ta.DailyAlert(lines=(("NAV", "100,000.00 USD"),), demo_day=2))):
            text = ta.payload_text(payload)
            self.assertNotIn("{", text)
            self.assertNotIn("PAPER", text.upper())
            json.dumps(payload)  # serialisable Block Kit

    def test_missing_values_read_sin_datos(self):
        text = ta.payload_text(ta.trade_slack_payload(opened(entry=None, risk_amount=None, risk_pct=None, why=None,
                                                             demo_day=None)))
        self.assertIn("Entrada sin datos", text)
        self.assertIn("Riesgo sin datos", text)
        self.assertIn("Por qué: sin datos", text)
        self.assertIn("Día sin datos", text)

    def test_why_line(self):
        self.assertEqual(ta.why_line(FACTS), "Barrida de liquidez 5M + ruptura bajista 15M + estructura bajista 1H")
        self.assertEqual(ta.why_line({"bias_1h": "BULLISH", "retracement_15m": "bullish", "equal_levels_5m": True}),
                         "Liquidez en niveles iguales 5M + retroceso alcista 15M + estructura alcista 1H")
        self.assertEqual(ta.why_line({"sweep_5m": True, "bias_1h": "bearish"}),
                         "Barrida de liquidez 5M + estructura bajista 1H")
        self.assertEqual(ta.why_line({"bias_1h": "neutral", "bos_15m": "bearish"}), "Ruptura bajista 15M")
        for empty in ({}, None, {"bias_1h": "neutral"}, {"bos_15m": "sideways"}):
            self.assertIsNone(ta.why_line(empty))
        # Over the limit: parts are dropped whole (15M first), never cut mid-word, never an ellipsis.
        self.assertEqual(ta.why_line(FACTS, limit=50), "Barrida de liquidez 5M + estructura bajista 1H")
        self.assertEqual(ta.why_line(FACTS, limit=30), "Estructura bajista 1H")
        for facts in (FACTS, {"bias_1h": "bullish", "retracement_15m": "bullish", "equal_levels_5m": True}):
            why = ta.why_line(facts)
            self.assertLessEqual(len(why), ta.WHY_LIMIT)
            self.assertLessEqual(ta.WHY_LIMIT, 90)
            self.assertNotIn("…", why)
            self.assertNotIn(":", why)  # no per-agent «Label: fragment» concatenation
            self.assertEqual(why.count("\n"), 0)

    def test_floor_url(self):
        self.assertEqual(ta.floor_url({"AI_FLOOR_PUBLIC_URL": URL + "/"}), FLOOR)
        self.assertEqual(ta.floor_url({"RENDER_EXTERNAL_URL": URL}), FLOOR)
        self.assertEqual(ta.floor_url({"AI_FLOOR_PUBLIC_URL": URL + "/app/"}), URL + "/app/?page=floor")
        for bad in ("http://floor.example.com", "https://u:p@floor.example.com", "https://floor.example.com?x=1", "",
                    "https://floor.example.com/#section", "https://floor.example.com#", "https://floor.example.com/?"):
            self.assertIsNone(ta.floor_url({"AI_FLOOR_PUBLIC_URL": bad}), bad)

    def test_metals_have_no_invented_pips(self):
        line = opened(symbol="XAUUSD", entry="2400.5", stop="2410.5", target="2380.5").levels_line()
        self.assertEqual(line, "Entrada 2400.50 · SL 2410.50 · TP 2380.50 (1:2)")

    def test_system_and_daily(self):
        payload = ta.system_slack_payload("RUN_FAILED", "EURUSD", "OSError")
        self.assertEqual(blocks(payload)[0]["text"]["text"], "⚠️ Ciclo fallido · EURUSD")
        self.assertEqual(payload["attachments"][0]["color"], ta.GOLD)
        self.assertEqual(ta.system_slack_payload("STATE_INCONSISTENCY")["attachments"][0]["color"], ta.RED)
        daily = ta.DailyAlert(lines=(("P&L del día de trading", "+45.50 USD"),), date_label="2026-10-20",
                              macro="Dólar fuerte.", pnl=45.5, demo_day=4, url=FLOOR)
        payload = ta.daily_slack_payload(daily)
        self.assertEqual(payload["attachments"][0]["color"], ta.GREEN)
        self.assertEqual(ta.payload_text(payload).splitlines(),
                         ["📊 Resumen diario · OANDA Demo · 2026-10-20", "P&L del día de trading: +45.50 USD",
                          "Macro: Dólar fuerte.", "OANDA Demo · Día 4 de 30", "Ver en el Floor"])


class EmailBuilderTests(unittest.TestCase):
    def test_opened_email(self):
        subject, text, html = ta.trade_email(opened())
        self.assertEqual(subject, "🔴 EURUSD SHORT abierta · +0.0R · OANDA Demo")
        self.assertIn("EURUSD SHORT abierta · +0.0R", html)
        self.assertIn("AI Trading Floor", html)
        self.assertIn("Georgia", html)  # gold serif title
        for color in (ta.NAVY, ta.GOLD, ta.TEAL):
            self.assertIn(color, html)
        for label in ("Entrada", "Riesgo", "SL", "Sesión", "TP", "Por qué:", "Abrir el Floor"):
            self.assertIn(label, html)
        self.assertIn(f'href="{FLOOR}"', html)
        self.assertIn("OANDA Demo · Día 3 de 30", html)
        self.assertIn("Precio (5m)", html)  # table-bar sparkline
        for forbidden in ("<svg", "<script", "<style", "<img", "PAPER", "{"):
            self.assertNotIn(forbidden, html)
        self.assertTrue(text.startswith("EURUSD SHORT abierta · +0.0R\n"))
        self.assertIn("Entrada 1.12186 · SL 1.12696 (51 pips) · TP 1.11166 (1:2)", text)
        self.assertIn(f"Abrir el Floor: {FLOOR}", text)

    def test_closed_email_and_escaping(self):
        subject, text, html = ta.trade_email(closed(-1.0, -500, "SL", why="<b>x</b> & y"))
        self.assertEqual(subject, "❌ EURUSD SHORT cerrada · -1.0R · OANDA Demo")
        self.assertIn("Resultado -1.0R (-$500) · Stop loss", text)
        self.assertIn("&lt;b&gt;x&lt;/b&gt; &amp; y", html)
        self.assertIn(ta.RED, html)

    def test_sparkline_bars(self):
        self.assertIsNone(ta.bars_html(()))
        self.assertIsNone(ta.bars_html((1.1,)))
        bars = ta.bars_html((1.0, 2.0, 3.0))
        self.assertEqual(bars.count("<td"), 3)
        self.assertIn("height:2px", bars)
        self.assertIn("height:48px", bars)

    def test_daily_email(self):
        subject, text, html = ta.daily_email(ta.DailyAlert(lines=(("NAV", "100,000.00 USD"),), macro="Dólar fuerte",
                                                           date_label="2026-10-20", demo_day=4))
        self.assertEqual(subject, "📊 Resumen diario · OANDA Demo · 2026-10-20")
        self.assertIn("Macro:", html)
        self.assertNotIn("Por qué", html)
        self.assertIn("OANDA Demo · Día 4 de 30", text)

    def test_gmail_channel_send_rendered(self):
        smtp = mock.MagicMock()
        channel = GmailEmailChannel(password="x", sender="floor@example.com", recipient="owner@example.com",
                                    smtp_factory=lambda *a, **k: smtp)
        channel.send_rendered(*ta.trade_email(opened()), event_id="ACCEPTED:1")
        message = smtp.send_message.call_args[0][0]
        self.assertEqual([part.get_content_type() for part in message.walk()][1:], ["text/plain", "text/html"])
        self.assertEqual(message["X-AI-Floor-Event-ID"], "ACCEPTED:1")


class FakeEmail:
    configured = True

    def __init__(self, fail=False):
        self.sent, self.fail = [], fail

    def send_rendered(self, subject, text, html, *, event_id):
        if self.fail:
            raise RuntimeError("smtp down")
        self.sent.append((subject, event_id))


class BrokerSinkTests(unittest.TestCase):
    def sink(self, rich, email=None):
        self.sent = []
        return ba.BrokerAlertSink(WEBHOOK, transport=lambda url, payload, timeout: self.sent.append(payload),
                                  clock=lambda: 0.0, rich=rich, email=email)

    def test_rich_open_and_close(self):
        email = FakeEmail()
        sink = self.sink(lambda kind, detail: opened() if kind == "ACCEPTED" else closed(2.0, 1000), email)
        sink("ACCEPTED", {"trade_id": "101", "reason": "FILLED"})
        sink("CLOSED", {"trade_id": "101", "reason": "TP"})
        sink("CLOSED", {"trade_id": "102", "reason": "TP"})  # another trade in the same minute: not de-duplicated
        sink("MANUAL_CLOSE", {"trade_id": "102", "reason": "MANUAL_CLOSE"})  # folded into CLOSED
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(self.sent[0]["attachments"][0]["color"], ta.RED)
        self.assertEqual([e[1] for e in email.sent], ["ACCEPTED:101", "CLOSED:101", "CLOSED:102"])

    def test_fallbacks(self):
        sink = self.sink(lambda kind, detail: None)
        sink("ACCEPTED", {"trade_id": "101", "symbol": "EURUSD"})
        self.assertEqual(self.sent[0], {"text": ba.build_message("ACCEPTED", {"trade_id": "101", "symbol": "EURUSD"})})
        sink = self.sink(mock.Mock(side_effect=RuntimeError("db")))
        sink("CLOSED", {"trade_id": "9"})
        self.assertIn("text", self.sent[0])
        sink = self.sink(lambda kind, detail: opened(), FakeEmail(fail=True))
        sink("ACCEPTED", {"trade_id": "1"})
        self.assertEqual((sink.sent, sink.failed), (1, 0))  # email failure never fails the Slack alert
        sink("DRIFT", {"trades": [1]})
        self.assertEqual(self.sent[-1]["text"].split(" · ")[0], "[OANDA demo] Desajuste broker/estado local: entradas bloqueadas")

    def test_production_factory_is_rich(self):
        env = {"AI_FLOOR_ALERTS_ENABLED": "1", "SLACK_WEBHOOK_URL": WEBHOOK}
        sink = ba.broker_alert_sink_from_env(env)
        self.assertIsNotNone(sink._rich)
        self.assertIsNone(sink._email)  # Gmail not configured
        self.assertIsNone(ba.broker_alert_sink_from_env(env, transport=lambda *a: None)._rich)
        gmail = {"GMAIL_APP_PASSWORD": "pw", "AI_FLOOR_ALERT_EMAIL_FROM": "a@example.com",
                 "AI_FLOOR_ALERT_EMAIL_TO": "b@example.com"}
        self.assertIsNotNone(ba.broker_alert_sink_from_env({**env, **gmail})._email)
        only_email = ba.broker_alert_sink_from_env({"AI_FLOOR_ALERTS_ENABLED": "1", **gmail})  # Gmail-only setup
        self.assertIsNotNone(only_email)
        self.assertIsNone(only_email._webhook_url)
        self.assertIsNotNone(only_email._rich)
        bad_slack = ba.broker_alert_sink_from_env({"AI_FLOOR_ALERTS_ENABLED": "1", "SLACK_WEBHOOK_URL": "http://x",
                                                   **gmail})
        self.assertIsNone(bad_slack._webhook_url)
        self.assertIsNone(ba.broker_alert_sink_from_env({"AI_FLOOR_ALERTS_ENABLED": "1"}))
        self.assertIsNone(ba.broker_alert_sink_from_env({"AI_FLOOR_ALERTS_ENABLED": "0", **gmail}))

    def test_email_and_slack_are_independent(self):
        email = FakeEmail()
        sink = ba.BrokerAlertSink(WEBHOOK, transport=mock.Mock(side_effect=OSError("slack down")), clock=lambda: 0.0,
                                  rich=lambda kind, detail: opened(), email=email)
        sink("ACCEPTED", {"trade_id": "7"})
        self.assertEqual((sink.sent, sink.failed, sink.email_sent, sink.email_failed), (0, 1, 1, 0))
        self.assertEqual([e[1] for e in email.sent], ["ACCEPTED:7"])  # Slack failure never suppresses the email
        email = FakeEmail()
        sink = ba.BrokerAlertSink(None, clock=lambda: 0.0, rich=lambda kind, detail: opened(), email=email)
        sink("ACCEPTED", {"trade_id": "8"})  # Gmail-only
        self.assertEqual((sink.sent, sink.failed, sink.email_sent), (0, 0, 1))
        sink = ba.BrokerAlertSink(WEBHOOK, transport=lambda *a: None, clock=lambda: 0.0,
                                  rich=lambda kind, detail: opened(), email=FakeEmail(fail=True))
        sink("ACCEPTED", {"trade_id": "9"})
        self.assertEqual((sink.sent, sink.failed, sink.email_sent, sink.email_failed), (1, 0, 0, 1))


def state_db(path, **row):
    with closing(sqlite3.connect(path)) as db:
        db.executescript(STATE_SCHEMA)
        base = {"client_id": "aif-run-1", "run_id": "run-1", "symbol": "EURUSD", "side": "SHORT", "units": 98000,
                "entry": "1.12180", "stop": "1.12696", "target": "1.11166", "initial_stop": None, "trade_id": "101",
                "fill_price": "1.12186", "opened_at": "2026-10-21T17:30:00+00:00", "stage": None, "status": "OPEN",
                "closed_reason": None, "pending_action": None, "action_status": None,
                "created_at": "2026-10-21T13:30:00+00:00", "updated_at": "2026-10-21T13:30:00+00:00"}
        base.update(row)
        db.execute(f"INSERT INTO broker_demo_trades({','.join(base)}) VALUES({','.join('?' * len(base))})",
                   tuple(base.values()))
        db.commit()


def experiment_db(path):
    with closing(sqlite3.connect(path)) as db:
        db.execute("CREATE TABLE runs(slot_key TEXT, run_id TEXT, symbol TEXT)")
        db.execute("CREATE TABLE agent_decisions(slot_key TEXT, agent_name TEXT, as_of TEXT, reasoning_summary TEXT)")
        db.execute("INSERT INTO runs VALUES('s1','run-1','EURUSD')")
        db.executemany("INSERT INTO agent_decisions VALUES('s1',?,'2026-10-21T13:15:00+00:00',?)", SUMMARIES)
        db.execute("CREATE TABLE setups(slot_key TEXT, status TEXT, side TEXT, invalidation TEXT, evidence TEXT, "
                   "plan TEXT)")
        db.execute("INSERT INTO setups VALUES('s1','VALID_SETUP','SHORT','1.1229',?,NULL)", (json.dumps(EVIDENCE),))
        db.commit()


class SourceTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.env = {"AI_FLOOR_BROKER_STATE_DB_PATH": str(self.dir / "state.db"),
                    "AI_FLOOR_DB_PATH": str(self.dir / "floor.db"), "AI_FLOOR_DEMO_EPOCH": "2026-10-18T22:25:00Z",
                    "AI_FLOOR_PUBLIC_URL": URL}
        experiment_db(self.env["AI_FLOOR_DB_PATH"])

    def account(self, environment, env):
        return AccountView("OK", environment, currency="USD", nav=100000)

    def trades(self, env, since=None):
        return {"closed": [{"id": "101", "client_id": "aif-run-1", "instrument": "EUR_USD", "state": "CLOSED",
                            "openTime": "2026-10-21T13:30:00Z", "closeTime": "2026-10-21T18:00:00Z",
                            "initialUnits": "-98000", "price": "1.12186", "realizedPL": "999.60",
                            "financing": "-1.20", "averageClosePrice": "1.11167"}], "open": []}

    def build(self, kind, detail):
        with mock.patch("execution.demo_dashboard.demo_progress", return_value=(None, None, 3)):
            return src.trade_alert(kind, detail, self.env, now=NOW, trades_fetch=self.trades,
                                   account_fetch=self.account)

    def test_opened_from_router_row_and_oanda_nav(self):
        state_db(self.env["AI_FLOOR_BROKER_STATE_DB_PATH"])
        alert = self.build("ACCEPTED", {"trade_id": "101"})
        self.assertEqual((alert.kind, alert.symbol, alert.side, alert.entry, alert.stop), ("OPENED", "EURUSD", "SHORT",
                                                                                          1.12186, 1.12696))
        self.assertAlmostEqual(alert.risk_amount, 499.8, places=1)  # |1.12186 - 1.12696| * 98000
        self.assertAlmostEqual(alert.risk_pct, 0.4998, places=3)
        self.assertEqual(alert.session, ("NEW_YORK",))
        self.assertEqual(alert.why, "Barrida de liquidez 5M + ruptura bajista 15M + estructura bajista 1H")
        self.assertEqual((alert.demo_day, alert.url), (3, FLOOR))
        self.assertIn("Riesgo 0.5% ($499.80) · Sesión Nueva York", ta.payload_text(ta.trade_slack_payload(alert)))

    def test_no_broker_fill_is_no_data_never_the_planned_entry(self):
        state_db(self.env["AI_FLOOR_BROKER_STATE_DB_PATH"], fill_price=None)
        alert = self.build("ACCEPTED", {"trade_id": "101"})
        self.assertEqual((alert.entry, alert.risk_amount, alert.risk_pct), (None, None, None))
        text = ta.payload_text(ta.trade_slack_payload(alert))
        self.assertIn("Entrada sin datos · SL 1.12696 · TP 1.11166\n", text)  # no pips / R:R from the plan
        self.assertIn("Riesgo sin datos", text)
        self.assertNotIn("1.12180", text)

    def test_why_is_no_data_without_setup_evidence(self):
        state_db(self.env["AI_FLOOR_BROKER_STATE_DB_PATH"], run_id="run-unknown")
        self.assertIsNone(self.build("ACCEPTED", {"trade_id": "101"}).why)
        self.assertEqual(src.setup_facts({"AI_FLOOR_DB_PATH": str(self.dir / "missing.db")}, "run-1"), {})
        with closing(sqlite3.connect(self.env["AI_FLOOR_DB_PATH"])) as db:
            db.execute("UPDATE setups SET evidence='[]'")
            db.commit()
        self.assertEqual(src.setup_facts(self.env, "run-1"), {})

    def test_closed_at_broker_infers_tp_with_oanda_result(self):
        state_db(self.env["AI_FLOOR_BROKER_STATE_DB_PATH"], status="CLOSED_AT_BROKER",
                 closed_reason="SL_TP_OR_EXTERNAL")
        alert = self.build("CLOSED_AT_BROKER", {"trade_id": "101", "reason": "SL_TP_OR_EXTERNAL"})
        self.assertEqual(alert.close_reason, "TP")
        self.assertAlmostEqual(alert.pnl, 998.4)
        self.assertAlmostEqual(alert.result_r, 998.4 / 499.8, places=3)
        self.assertEqual(alert.outcome, "WIN")

    def test_no_row_or_unknown_kind_is_none(self):
        self.assertIsNone(self.build("ACCEPTED", {"trade_id": "404"}))
        self.assertIsNone(self.build("DRIFT", {"trade_id": "101"}))
        self.assertIsNone(self.build("ACCEPTED", "text"))

    def test_daily_alert_folds_macro(self):
        # Macro as_of 2026-10-21T13:15Z: inside trading day 2026-10-21 = [10-20 21:00Z, 10-21 21:00Z).
        alert = src.daily_alert(["Día 3 de 30", "Día de trading: 2026-10-21 (17:00 a 17:00 ET)", "NAV: 1.00 USD"],
                                self.env, date_label="2026-10-21", demo_day=3, pnl=-2.0)
        self.assertEqual(alert.lines, (("NAV", "1.00 USD"),))
        self.assertTrue(alert.macro.startswith("Dólar fuerte"))
        self.assertEqual(alert.color, ta.RED)

    def test_daily_macro_bounded_to_the_reported_trading_day(self):
        # A delayed summary of 2026-10-20 never shows the macro view written on 2026-10-21.
        self.assertIsNone(src.daily_alert([], self.env, date_label="2026-10-20").macro)
        window = (datetime(2026, 10, 20, 21, tzinfo=timezone.utc), datetime(2026, 10, 21, 21, tzinfo=timezone.utc))
        self.assertIsNotNone(src.daily_alert([], self.env, window=window).macro)
        late = (window[0], datetime(2026, 10, 21, 13, 15, tzinfo=timezone.utc))  # end is exclusive
        self.assertIsNone(src.daily_alert([], self.env, window=late).macro)
        self.assertIsNone(src.daily_alert([], self.env).macro)  # no window: no unbounded macro
        with closing(sqlite3.connect(self.env["AI_FLOOR_DB_PATH"])) as db:
            db.execute("INSERT INTO runs VALUES('s2','run-2','EURUSD')")
            db.execute("INSERT INTO agent_decisions VALUES('s2','macro_ai','2026-10-21T20:00:00.5+00:00','Más tarde.')")
            db.commit()
        self.assertEqual(src.daily_alert([], self.env, window=window).macro, "Más tarde.")


def note(kind, **kw):
    return NotificationEvent("1.0", "evt", "run", "2026-10-21T14:00:00Z", "INFO", kind, "EURUSD", (),
                             ({"agent": "macro_ai", "status": "OK", "reasoning_summary": "x"},), None, (), (), (), **kw)


class DurableSinkTests(unittest.TestCase):
    def test_macro_folded_and_paper_trades_silent_in_oanda_mode(self):
        for kind in ("MACRO_HIGH_IMPORTANCE", "MACRO_HIGH_RELEVANCE", "AI_REVIEW_UNHEALTHY"):
            self.assertIsNone(compact_payload(note(kind)), kind)
        with mock.patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO"}):
            for kind in ("POSITION_OPENED", "POSITION_CLOSED", "EXECUTION_DECISION", "RISK_REJECTED"):
                self.assertIsNone(compact_payload(note(kind)), kind)
            sent = []
            SlackNotificationSink(webhook_url=WEBHOOK, transport=lambda u, p, t: sent.append(p)).deliver(
                note("RUN_FAILED", execution={"reason": "OSError"}))
        self.assertEqual(ta.payload_text(sent[0]).splitlines()[:2], ["⚠️ Ciclo fallido · EURUSD", "Detalle: OSError"])
        self.assertNotIn("{", ta.payload_text(sent[0]))

    def test_paper_mode_trade_line_has_no_numbers(self):
        with mock.patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": "", "AI_FLOOR_BROKER_DEMO_ROUTING": "0"}):
            payload = compact_payload(note("EXECUTION_DECISION", execution={"execution_reason": "ORDER_SUBMITTED",
                                                                             "side": "LONG", "entry": 2400.5}))
        text = ta.payload_text(payload)
        self.assertIn("Decisión de ejecución PAPER LONG · EURUSD", text)
        self.assertNotIn("2400", text)


class DailyChannelsTests(unittest.TestCase):
    """The OANDA demo daily summary goes to Slack and email independently (each once)."""

    def deliver(self, transport, webhook=WEBHOOK, email_on=True):
        summary = note("DAILY_SUMMARY", daily_summary={"date_ny": "2026-10-20"})
        alert = ta.DailyAlert(lines=(("NAV", "1 USD"),), demo_day=2)
        with mock.patch.dict(os.environ, {"AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO"}), \
                mock.patch("runtime.notifications.oanda_demo_daily_alert", return_value=alert) as build, \
                mock.patch("runtime.alert_daily_email.email_configured", return_value=email_on), \
                mock.patch("runtime.alert_daily_email.send_daily_email", return_value=True) as email:
            try:
                SlackNotificationSink(webhook_url=webhook, transport=transport).deliver(summary)
                error = None
            except Exception as exc:  # noqa: BLE001
                error = exc
        return error, build.call_count, [c.args[1] for c in email.call_args_list]

    def test_slack_failure_still_emails_and_reports_the_slack_error(self):
        error, _, emails = self.deliver(mock.Mock(side_effect=OSError("down")))
        self.assertIsInstance(error, OSError)
        self.assertEqual(emails, ["evt"])

    def test_both_channels_once(self):
        sent = []
        self.assertEqual(self.deliver(lambda u, p, t: sent.append(p)), (None, 1, ["evt"]))
        self.assertEqual(len(sent), 1)

    def test_gmail_only_emails_without_slack(self):
        error, built, emails = self.deliver(None, webhook="")
        self.assertEqual((str(error), built, emails), ("NOT_CONFIGURED", 1, ["evt"]))

    def test_no_channel_reads_nothing(self):
        error, built, emails = self.deliver(None, webhook="", email_on=False)
        self.assertEqual((str(error), built, emails), ("NOT_CONFIGURED", 0, []))


class RecordingChannel:
    name, configured = "slack", True

    def __init__(self):
        self.events = []

    def send(self, event):
        self.events.append(event)


class DispatcherTests(unittest.TestCase):
    def test_paper_trade_alerts_suppressed_in_oanda_mode(self):
        trade = alert_event(alert_event_id("POSITION_OPENED", "x"), "POSITION_OPENED", "2026-10-21T14:00:00Z",
                            symbol="EURUSD", trade={"entry": "1.1"})
        system = alert_event(alert_event_id("SYSTEM_DEGRADED", "x"), "SYSTEM_DEGRADED", "2026-10-21T14:00:00Z")
        channel = RecordingChannel()
        dispatcher = AlertDispatcher((channel,), enabled=True, environ={"AI_FLOOR_RESULTS_SOURCE": "OANDA_DEMO"},
                                     sleep=lambda _s: None)
        self.assertEqual(dispatcher.dispatch(trade), {"slack": "OANDA_ONLY"})
        self.assertEqual(dispatcher.dispatch(system), {"slack": "DELIVERED"})
        self.assertEqual([e.event_type for e in channel.events], ["SYSTEM_DEGRADED"])
        paper = AlertDispatcher((RecordingChannel(),), enabled=True, environ={}, sleep=lambda _s: None)
        self.assertEqual(paper.dispatch(trade), {"slack": "DELIVERED"})


if __name__ == "__main__":
    unittest.main()
