"""Fase G1/G2: read-only chart cache, GET /chart/<SYMBOL>.json and the Posiciones chart. No orders."""
import asyncio
import inspect
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import tempfile
import time
import unittest
from pathlib import Path

from execution.chart_cache import CLOSED_BARS, SYMBOLS, ChartCache, chart_cache_enabled, position_from_desk
from execution.exness_m5 import BAR, floor_m5_utc
from execution.exness_stream import QuoteBook
from ui.auth import issue_remember_token
from ui.liquid.chart_feed import ChartFeedMiddleware, answer


NOW = datetime(2026, 11, 27, 18, 40, tzinfo=timezone.utc)
PASSWORD = "panel-password-1"
ACCOUNT = "99887766"
HOST = "secret.example.test"
KEY = "exnsk-fixture-key-value"
TICKET = "TICKET55"
ROOT = Path(__file__).resolve().parents[1]


def _book(at=NOW):
    book = QuoteBook()
    book.note_tick(Decimal("150.100"), Decimal("150.120"), at, symbol="USDJPY")
    book.note_tick(Decimal("1.08000"), Decimal("1.08020"), at, symbol="EURUSD")
    return book


def _desk():
    return {
        "account_id": ACCOUNT,
        "host": f"https://{HOST}",
        "api_key": KEY,
        "positions": [{
            "position_id": TICKET,
            "instrument": "EURUSD",
            "side": "LONG",
            "open_price": "1.08000",
            "stop_loss_price": "1.00000",
            "take_profit_price": "9.00000",
            "profit": "1.25",
            "comment": f"https://{HOST}/{ACCOUNT}",
        }],
        "orders": [{
            "type": "sltp",
            "position_id": TICKET,
            "stop_loss_price": "1.07000",
            "take_profit_price": "1.11000",
            "account": ACCOUNT,
        }],
    }


def _bars(count, boundary):
    rows = []
    for index in range(count):
        opened = boundary - BAR * (count - index)
        price = Decimal("1.10000") + (Decimal(index) / Decimal("100000"))
        rows.append({"time": opened.isoformat(), "open": format(price, "f"),
                     "high": format(price + Decimal("0.00020"), "f"),
                     "low": format(price - Decimal("0.00010"), "f"),
                     "close": format(price, "f")})
    rows.append({"time": boundary.isoformat(), "open": "1.20000", "high": "1.20020",
                 "low": "1.19980", "close": "1.20010"})
    return {"candles": rows}


class _Client:
    def __init__(self, payload):
        self.account_id = ACCOUNT
        self.paths = []
        self.payload = payload

    def get_json(self, path):
        self.paths.append(path)
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


def _token(now=None):
    return issue_remember_token(PASSWORD, time.time() if now is None else now)


def _headers(token):
    return [("cookie", f"aif_remember={token}")]


class ProtectionTests(unittest.TestCase):
    def test_sl_tp_come_from_the_sltp_order_not_the_client_entry(self):
        found = position_from_desk("EURUSD", _desk())
        self.assertEqual(found["entry"], "1.08000")
        self.assertEqual(found["stop"], "1.07000")
        self.assertEqual(found["target"], "1.11000")
        self.assertEqual(found["side"], "LONG")
        self.assertEqual(found["floating"], "1.25")
        self.assertNotIn("1.00000", json.dumps(found))
        self.assertNotIn("9.00000", json.dumps(found))

    def test_no_position_leaves_the_field_null(self):
        self.assertIsNone(position_from_desk("EURUSD", {"positions": [], "orders": _desk()["orders"]}))
        other = {"positions": [{**_desk()["positions"][0], "instrument": "GBPUSD"}], "orders": []}
        self.assertIsNone(position_from_desk("EURUSD", other))


class CacheTests(unittest.TestCase):
    def test_closed_candles_are_capped_and_the_open_bar_comes_from_the_tick(self):
        boundary = floor_m5_utc(NOW)
        client = _Client(_bars(CLOSED_BARS + 10, boundary))
        cache = ChartCache(interval=0)
        cache.refresh(NOW, client=client, quotes=_book(), desk={"positions": [], "orders": []})
        view = cache.view("EURUSD")
        self.assertIsNone(view["position"])
        self.assertEqual(view["bid"], "1.08000")
        self.assertEqual(view["ask"], "1.08020")
        self.assertFalse(view["stale"])
        self.assertEqual(view["last_update"], NOW.isoformat())
        self.assertEqual(view["candles"][-1]["time"], boundary.isoformat())
        self.assertEqual(view["candles"][-1]["close"], "1.08000")
        stored = cache.view("USDJPY")
        closed = [row for row in stored["candles"] if row["time"] < boundary.isoformat()]
        self.assertEqual(len(closed), CLOSED_BARS)
        self.assertEqual(closed[0]["time"], (boundary - BAR * CLOSED_BARS).isoformat())
        self.assertTrue(all(row["close"] != "1.20010" for row in stored["candles"]))
        self.assertEqual(len(client.paths), 1)
        from urllib.parse import parse_qs
        query = parse_qs(client.paths[0].split("?", 1)[1])
        self.assertEqual(query["timeframe"], ["M5"])
        self.assertEqual(query["price_type"], ["bid"])
        self.assertEqual(query["instrument"], ["USDJPY"])
        self.assertEqual(query["to"], [boundary.strftime("%Y-%m-%dT%H:%M:%SZ")])
        self.assertEqual(query["from"], [(boundary - BAR * CLOSED_BARS).strftime("%Y-%m-%dT%H:%M:%SZ")])

    def test_a_dropped_feed_freezes_last_update_and_marks_stale(self):
        book = _book()
        cache = ChartCache()
        cache.refresh(NOW, client=None, quotes=book, desk=_desk())
        fresh = cache.view("EURUSD")
        self.assertFalse(fresh["stale"])
        book.mark_disconnected()
        later = NOW + timedelta(seconds=90)
        client = _Client(_bars(2, floor_m5_utc(later)))
        cache.refresh(later, client=client, quotes=book, desk=_desk())
        stale = cache.view("EURUSD")
        self.assertTrue(stale["stale"])
        self.assertEqual(stale["last_update"], fresh["last_update"])
        self.assertEqual(stale["bid"], fresh["bid"])
        self.assertTrue(client.paths)  # closed M5 bars are still read; the quote socket is not required
        self.assertEqual(stale["position"]["stop"], "1.07000")


    def test_a_cold_start_with_no_quotes_still_loads_closed_candles_and_a_read_time(self):
        """Live Saturday shape: fresh process, quote socket down, no prior flag.

        The cache must still GET the last closed M5 bars (Friday) and stamp last_update
        so the panel does not show an empty chart and «última lectura --:--».
        """
        from execution.exness_stream import QuoteBook
        boundary = floor_m5_utc(NOW)
        client = _Client(_bars(CLOSED_BARS, boundary))
        cache = ChartCache()
        cache.refresh(NOW, client=client, quotes=QuoteBook(), desk={"positions": [], "orders": []})
        view = cache.view("USDJPY")
        self.assertTrue(client.paths)
        self.assertGreaterEqual(len(view["candles"]), 2)
        self.assertTrue(all(row["time"] < boundary.isoformat() for row in view["candles"]))
        self.assertEqual(view["last_update"], NOW.isoformat())
        self.assertTrue(view["stale"])
        self.assertIsNone(view["bid"])
        self.assertIsNone(view["position"])
        # A second due refresh walks the other symbol; the first series stays in the published flag.
        later = NOW + timedelta(seconds=30)
        cache.refresh(later, client=client, quotes=QuoteBook(), desk={"positions": [], "orders": []})
        euro = cache.view("EURUSD")
        self.assertGreaterEqual(len(euro["candles"]), 2)
        self.assertEqual(euro["last_update"], later.isoformat())
        self.assertTrue(euro["stale"])
        published = {}
        cache.publish(type("S", (), {"set_flag": lambda self, key, value: published.update({key: value})})())
        payload = json.loads(published["chart_feed"])
        self.assertTrue(payload["symbols"]["USDJPY"]["candles"])
        self.assertTrue(payload["symbols"]["USDJPY"]["last_update"])
        self.assertTrue(payload["symbols"]["USDJPY"]["stale"])

    def test_unset_flag_is_on_and_zero_does_not_call_the_broker(self):
        self.assertTrue(chart_cache_enabled({}))
        self.assertTrue(chart_cache_enabled({"AI_FLOOR_CHART_CACHE": ""}))
        self.assertFalse(chart_cache_enabled({"AI_FLOOR_CHART_CACHE": "0"}))
        from execution.exness_session import ExnessSession
        client = _Client({"candles": []})
        session = ExnessSession.__new__(ExnessSession)
        session._env = {"AI_FLOOR_CHART_CACHE": "0"}
        session.broker = type("B", (), {})()
        session.broker._transport = type("T", (), {"_client": client, "quotes": _book()})()
        session.broker._brakes = None
        self.assertIsNone(session.refresh_chart(NOW))
        self.assertEqual(client.paths, [])
        session._env = {}
        session.refresh_chart(NOW)
        self.assertEqual(len(client.paths), 1)
        self.assertNotIn("refresh_chart", inspect.getsource(ExnessSession.manage))
        self.assertNotIn("chart_cache", inspect.getsource(ExnessSession._manage))


class EndpointTests(unittest.TestCase):
    def setUp(self):
        self.now = time.time()
        self.env = {"AI_FLOOR_DASHBOARD_PASSWORD": PASSWORD, "RENDER": "true"}
        self.cache = ChartCache(interval=0)
        self.cache.refresh(NOW, client=_Client(_bars(3, floor_m5_utc(NOW))), quotes=_book(), desk=_desk())

    def _get(self, symbol="EURUSD", headers=None, env=None, cache=None):
        status, body = answer("GET", f"/chart/{symbol}.json", headers if headers is not None else _headers(_token(self.now)),
                              self.env if env is None else env, now=self.now, cache=self.cache if cache is None else cache)
        return status, json.loads(body.decode("ascii"))

    def test_other_methods_are_405_without_a_password(self):
        for method in ("POST", "PUT", "PATCH", "DELETE"):
            with self.subTest(method=method):
                status, body = answer(method, "/chart/EURUSD.json", [], self.env, now=self.now)
                self.assertEqual(status, 405)
                self.assertEqual(json.loads(body.decode("ascii"))["error"], "method_not_allowed")
                self.assertNotIn(PASSWORD, body.decode("ascii"))

    def test_get_requires_the_panel_password(self):
        status, body = answer("GET", "/chart/EURUSD.json", [], self.env, now=self.now, cache=self.cache)
        self.assertEqual(status, 401)
        self.assertNotIn(PASSWORD, body.decode("ascii"))
        status, payload = self._get(headers=[("cookie", "aif_remember=v1.1.nope.dead")])
        self.assertEqual(status, 401)
        self.assertNotIn("bid", payload)
        remote = {"RENDER": "true"}
        status, _body = answer("GET", "/chart/EURUSD.json", [], remote, now=self.now, cache=self.cache)
        self.assertEqual(status, 401)
        status, payload = self._get()
        self.assertEqual(status, 200)
        self.assertEqual(payload["symbol"], "EURUSD")
        self.assertEqual(payload["position"]["stop"], "1.07000")
        self.assertEqual(payload["position"]["target"], "1.11000")
        raw = f"aif_remember={_token(self.now)}".encode("ascii")
        status, body = answer("GET", "/chart/EURUSD.json", [(b"cookie", raw)], self.env, now=self.now, cache=self.cache)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body.decode("ascii"))["position"]["side"], "LONG")

    def test_no_position_still_returns_candles_and_price(self):
        cache = ChartCache(interval=0)
        cache.refresh(NOW, client=_Client(_bars(4, floor_m5_utc(NOW))), quotes=_book(),
                      desk={"positions": [], "orders": []})
        status, payload = self._get(cache=cache)
        self.assertEqual(status, 200)
        self.assertIsNone(payload["position"])
        self.assertTrue(payload["candles"])
        self.assertEqual(payload["bid"], "1.08000")
        self.assertFalse(payload["stale"])

    def test_the_body_has_no_secrets(self):
        status, body = answer("GET", "/chart/EURUSD.json", _headers(_token(self.now)), self.env, now=self.now,
                              cache=self.cache)
        self.assertEqual(status, 200)
        text = body.decode("ascii")
        for secret in (ACCOUNT, HOST, KEY, PASSWORD, TICKET, "1.00000", "9.00000", "https://", "position_id"):
            self.assertNotIn(secret, text, secret)
        payload = json.loads(text)
        self.assertEqual(set(payload), {"symbol", "candles", "bid", "ask", "position", "last_update", "stale"})
        self.assertEqual(set(payload["position"]), {"entry", "stop", "target", "side", "floating"})

    def test_unknown_symbol_is_404_and_the_published_flag_is_what_the_browser_reads(self):
        status, _body = self._get("GBPUSD")
        self.assertEqual(status, 404)
        from execution.exness_brakes import SqliteFlags
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "exness_state.db")
            self.cache.publish(SqliteFlags(path))
            env = {**self.env, "AI_FLOOR_EXNESS_STATE_DB_PATH": path}
            status, raw = answer("GET", "/chart/USDJPY.json", _headers(_token(self.now)), env, now=self.now)
            self.assertEqual(status, 200)
            payload = json.loads(raw.decode("ascii"))
            self.assertEqual(payload["bid"], "150.100")
            self.assertIsNone(payload["position"])
            for secret in (ACCOUNT, HOST, KEY, TICKET):
                self.assertNotIn(secret, raw.decode("ascii"))

    def test_the_middleware_answers_405_and_does_not_call_the_dashboard(self):
        called = []

        async def app(scope, receive, send):
            called.append(scope.get("path"))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"dashboard"})

        async def once(method, path):
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            await ChartFeedMiddleware(app)({"type": "http", "method": method, "path": path, "headers": []}, receive, send)
            return sent

        sent = asyncio.run(once("DELETE", "/chart/EURUSD.json"))
        self.assertEqual(sent[0]["status"], 405)
        self.assertEqual(called, [])
        sent = asyncio.run(once("GET", "/positions"))
        self.assertEqual(called, ["/positions"])
        self.assertEqual(sent[1]["body"], b"dashboard")


class OrderPathTests(unittest.TestCase):
    def test_new_code_does_not_import_anything_that_sends_an_order(self):
        from execution.exness_session import ExnessSession
        from runtime.service import OperationalRuntime
        text = "\n".join([
            (ROOT / "execution" / "chart_cache.py").read_text(encoding="utf-8"),
            (ROOT / "ui" / "liquid" / "chart_feed.py").read_text(encoding="utf-8"),
            (ROOT / "ui" / "static" / "liquid" / "chart.js").read_text(encoding="utf-8"),
            inspect.getsource(ExnessSession.refresh_chart),
            inspect.getsource(OperationalRuntime.refresh_chart_cache),
        ])
        render = (ROOT / "ui" / "liquid" / "render.py").read_text(encoding="utf-8")
        for banned in ("close_market", "mutate", "submit_plan", "open_market"):
            self.assertNotIn(banned, render, banned)
        for banned in ("close_market", "mutate", "submit_plan", "open_market", "get_json("):
            if banned == "get_json(":
                feed = (ROOT / "ui" / "liquid" / "chart_feed.py").read_text(encoding="utf-8")
                self.assertNotIn(banned, feed)
                continue
            self.assertNotIn(banned, text, banned)
        poll = inspect.getsource(OperationalRuntime.process_operator_requests)
        self.assertLess(poll.index("self.pace_exness(at)"), poll.index("self.refresh_chart_cache(at)"))
        self.assertEqual(SYMBOLS, ("USDJPY", "EURUSD"))



# Streamlit 1.65 HtmlWithJs (`st.html(..., unsafe_allow_javascript=True)`):
# SANITIZE_HTML_BASE_OPTIONS = {USE_PROFILES: {html: true}, FORCE_BODY: true}
# plus ADD_TAGS: script, style and ADD_ATTR: src, type, async, defer, nonce,
# crossorigin, referrerpolicy, integrity. The html profile drops SVG/MathML.
# Canvas stays because it is an HTML element. The live panel uses this path
# (`ui/liquid/page.py` `_draw`).
_HTML_PROFILE_DROP = (
    "svg", "math", "polyline", "polygon", "path", "defs", "linearGradient",
    "clipPath", "g", "line", "rect", "circle", "ellipse", "use", "symbol",
)


def live_html_sanitize(html):
    """Drop SVG/MathML the way Streamlit 1.65's html profile does, keep script/canvas."""
    import re
    out = html
    for tag in _HTML_PROFILE_DROP:
        out = re.sub(rf"<{tag}\b[^>]*>.*?</{tag}>", "", out, flags=re.I | re.S)
        out = re.sub(rf"<{tag}\b[^>]*/?>", "", out, flags=re.I)
    return out


class PositionsChartTests(unittest.TestCase):
    def test_posiciones_includes_the_read_only_chart_and_survives_the_live_sanitizer(self):
        from ui.liquid.render import SECTIONS, fragment
        from test_liquid_glass import _view

        view = _view()
        html = fragment(view, section="Posiciones")
        self.assertEqual(SECTIONS, ("Inicio", "Posiciones", "Historial", "Riesgo", "Sistema"))
        self.assertIn('class="live-chart"', html)
        self.assertIn('data-symbol="USDJPY"', html)
        self.assertIn("Sin posición · 0.00 USC", html)
        self.assertIn("Powered by", html)
        self.assertIn("https://www.tradingview.com", html)
        self.assertIn('src="/liquid-assets/lightweight-charts.js"', html)
        self.assertIn('src="/liquid-assets/chart.js"', html)
        self.assertIn('data-chart-symbol="EURUSD"', html)
        self.assertNotIn("data-panel=\"Gráfica\"", html)
        self.assertEqual(html.count("<section data-panel="), 5)
        for banned in ("close_market", "operate", "cantidad", "quantity", "type=\"number\"", "method=\"post\""):
            self.assertNotIn(banned, html.lower() if banned != "type=\"number\"" else html)

        planted = html.replace(
            'class="live-chart"',
            'class="live-chart"><svg class="would-vanish"><polyline points="0,0 1,1"/></svg><canvas class="chart-canvas"',
            1,
        )
        cleaned = live_html_sanitize(planted)
        self.assertIn('src="/liquid-assets/lightweight-charts.js"', cleaned)
        self.assertIn('src="/liquid-assets/chart.js"', cleaned)
        self.assertIn('class="live-chart"', cleaned)
        self.assertIn("<script", cleaned)
        self.assertIn('class="chart-canvas"', cleaned)
        self.assertNotIn("<svg", cleaned.lower())
        self.assertNotIn("polyline", cleaned.lower())
        self.assertNotIn('class="would-vanish"', cleaned)

    def test_chart_script_polls_the_get_endpoint_and_draws_readonly_lines(self):
        source = (ROOT / "ui" / "static" / "liquid" / "chart.js").read_text(encoding="utf-8")
        self.assertIn("5000", source)
        self.assertIn("/chart/", source)
        self.assertIn("credentials: \"same-origin\"", source)
        self.assertIn("createPriceLine", source)
        self.assertIn("axisPressedMouseMove: false", source)
        self.assertIn("Sin posición · 0.00 USC", source)
        self.assertIn("sin datos · última lectura", source)
        self.assertIn("method: \"GET\"", source)
        for banned in ("close_market", "mutate", "submit_plan", "open_market", "method: \"POST\"", "fetch("):
            if banned == "fetch(":
                self.assertIn('fetch("/chart/"', source)
                self.assertEqual(source.count("fetch("), 1)
                continue
            self.assertNotIn(banned, source, banned)

    def test_vendored_library_is_lightweight_charts_5_3_0(self):
        lib = (ROOT / "ui" / "static" / "liquid" / "lightweight-charts.js").read_text(encoding="utf-8")
        self.assertIn("TradingView Lightweight Charts", lib)
        self.assertIn("v5.3.0", lib)
        self.assertIn("window.LightweightCharts=", lib.replace(" ", ""))
        self.assertTrue((ROOT / "ui" / "static" / "liquid" / "LIGHTWEIGHT_CHARTS_LICENSE").is_file())
        self.assertIn("Apache License", (ROOT / "ui" / "static" / "liquid" / "LIGHTWEIGHT_CHARTS_LICENSE").read_text(encoding="utf-8"))


class AssetTests(unittest.TestCase):
    def test_get_serves_the_vendored_scripts_and_other_methods_are_405(self):
        from ui.liquid.chart_feed import asset_answer

        status, body, typ = asset_answer("GET", "/liquid-assets/lightweight-charts.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", typ)
        self.assertIn(b"5.3.0", body)
        status, body, typ = asset_answer("GET", "/liquid-assets/chart.js")
        self.assertEqual(status, 200)
        self.assertIn(b"createPriceLine", body)
        self.assertEqual(asset_answer("POST", "/liquid-assets/chart.js")[0], 405)
        self.assertEqual(asset_answer("GET", "/liquid-assets/../chart_feed.py"), None)
        self.assertEqual(asset_answer("GET", "/liquid-assets/secret.js"), None)

    def test_middleware_serves_assets_without_the_cookie(self):
        called = []

        async def app(scope, receive, send):
            called.append(scope.get("path"))
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"dashboard"})

        async def once(method, path):
            sent = []

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            async def send(message):
                sent.append(message)

            await ChartFeedMiddleware(app)({"type": "http", "method": method, "path": path, "headers": []}, receive, send)
            return sent

        sent = asyncio.run(once("GET", "/liquid-assets/chart.js"))
        self.assertEqual(sent[0]["status"], 200)
        self.assertIn(b"createPriceLine", sent[1]["body"])
        self.assertEqual(called, [])
        sent = asyncio.run(once("PUT", "/liquid-assets/lightweight-charts.js"))
        self.assertEqual(sent[0]["status"], 405)
        self.assertEqual(called, [])
        sent = asyncio.run(once("GET", "/positions"))
        self.assertEqual(called, ["/positions"])



if __name__ == "__main__":
    unittest.main()
