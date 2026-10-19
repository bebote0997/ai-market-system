"""OANDA practice demo "starts from zero": epoch, demo-trade filter, demo metrics, GET-only trades read, dashboard
(routing OFF unchanged / ON = demo numbers only, never PAPER), decisions filter, day counter and the pre-demo archive.
Synthetic data only; fake transports; no network."""
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from execution import demo_dashboard, oanda_readonly as ro
from execution.broker_demo_state import SCHEMA
from execution.demo_epoch import DEFAULT_DEMO_EPOCH_UTC, demo_epoch, is_demo_trade
from execution.demo_metrics import (STAT_KEYS, demo_capital, demo_day, demo_rows, demo_statistics, first_demo_open,
                                    load_risk_rows, parse_time)
from runtime import cloud

ACCOUNT = "101-000-1234567-001"
TOKEN = "tok-FAKE-practice-demo-zero"
EPOCH = datetime(2026, 10, 18, 22, 25, tzinfo=timezone.utc)
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)  # 11:00 ET, Oct 20


def raw_trade(trade_id, client_id, opened, closed=None, pl="0", financing="0", units="1000", price="1.10000",
              close_price=None, upl=None, sl=None, tp=None):
    trade = {"id": str(trade_id), "instrument": "EUR_USD", "price": price, "openTime": opened,
             "state": "CLOSED" if closed else "OPEN", "initialUnits": units,
             "currentUnits": "0" if closed else units, "realizedPL": pl, "financing": financing,
             "accountID": ACCOUNT, "closingTransactionIDs": ["9"], "initialMarginRequired": "33.0"}
    if client_id is not None:
        trade["clientExtensions"] = {"id": client_id, "tag": "x"}
    if closed:
        trade.update(closeTime=closed, averageClosePrice=close_price or price)
    else:
        trade["unrealizedPL"] = upl or "0"
    if sl:
        trade["stopLossOrder"] = {"id": "77", "price": sl, "state": "PENDING"}
    if tp:
        trade["takeProfitOrder"] = {"id": "78", "price": tp, "state": "PENDING"}
    return trade


def safe(*trades):
    return [ro.safe_trade(trade) for trade in trades]


SCENARIO = safe(
    raw_trade(11, "aif-r1", "2026-10-19T12:01:13.123456789Z", "2026-10-19T14:00:00.000000000Z", pl="30.0",
              financing="-0.5"),
    raw_trade(12, "aif-r2", "2026-10-20T01:00:00Z", "2026-10-20T03:30:00Z", pl="-40.0"),  # 23:30 ET Oct 19
    raw_trade(13, "aif-r3", "2026-10-20T13:00:00Z", "2026-10-20T14:00:00Z", pl="20.0"),  # today ET
    raw_trade(5, "aif-test-20261018T150000Z", "2026-10-19T00:00:00Z", "2026-10-19T01:00:00Z", pl="999"),
    raw_trade(4, "aif-old", "2026-10-18T20:00:00Z", "2026-10-19T02:00:00Z", pl="500"),  # opened before the epoch
    raw_trade(6, None, "2026-10-19T05:00:00Z", "2026-10-19T06:00:00Z", pl="777"),  # manual, no client id
    raw_trade(14, "aif-r4", "2026-10-20T14:30:00Z", units="-1000", upl="-3.25", sl="1.10500", tp="1.09500"),
    raw_trade(7, "aif-test-open", "2026-10-20T14:00:00Z", upl="50"),
)
RISK = {"aif-r1": {"units": 1000, "entry": "1.1000", "fill_price": "1.1002", "stop": "1.0972", "initial_stop": None},
        "aif-r2": {"units": 1000, "entry": "1.1000", "fill_price": "1.1000", "stop": "1.1000", "initial_stop": "1.0960"},
        "aif-r3": {"units": 1000, "entry": "1.1000", "fill_price": None, "stop": "1.0980", "initial_stop": None}}


def refuse_network(*_args, **_kwargs):
    raise AssertionError("network must not be touched")


class EpochTests(unittest.TestCase):
    def test_default_and_overrides(self):
        self.assertEqual(demo_epoch({}), EPOCH)
        self.assertEqual(DEFAULT_DEMO_EPOCH_UTC, "2026-10-18T22:25:00Z")
        self.assertEqual(demo_epoch({"AI_FLOOR_DEMO_EPOCH": "2026-10-19T00:00:00Z"}),
                         datetime(2026, 10, 19, tzinfo=timezone.utc))
        self.assertEqual(demo_epoch({"AI_FLOOR_DEMO_EPOCH": "2026-10-18T20:00:00-04:00"}),
                         datetime(2026, 10, 19, tzinfo=timezone.utc))
        for bad in ("2026-10-19T00:00:00", "garbage", "", "  ", None, 5):
            with self.subTest(bad=bad):
                self.assertEqual(demo_epoch({"AI_FLOOR_DEMO_EPOCH": bad}), EPOCH)
        self.assertEqual(demo_epoch(None).tzinfo, timezone.utc)
        with patch.dict(os.environ, {"AI_FLOOR_DEMO_EPOCH": "2026-10-19T00:00:00+00:00"}):
            self.assertEqual(demo_epoch(), datetime(2026, 10, 19, tzinfo=timezone.utc))
        self.assertEqual(demo_epoch(SimpleNamespace()), EPOCH)  # broken mapping: never raises

    def test_demo_trade_ids(self):
        self.assertTrue(is_demo_trade("aif-6c1707b7-ddca"))
        for client_id in ("aif-test-20261018T150000Z", "aif-test-close-12", "aif-", "manual", "", None, 5):
            with self.subTest(client_id=client_id):
                self.assertFalse(is_demo_trade(client_id))


class MetricsTests(unittest.TestCase):
    def test_filtering_excludes_test_order_pre_epoch_and_manual(self):
        closed, opened = demo_rows(SCENARIO, EPOCH, RISK)
        self.assertEqual([r["client_id"] for r in closed], ["aif-r1", "aif-r2", "aif-r3"])
        self.assertEqual([r["client_id"] for r in opened], ["aif-r4"])

    def test_stats_math(self):
        stats, curve, opened = demo_statistics(SCENARIO, NOW, EPOCH, RISK)
        self.assertEqual(set(stats), set(STAT_KEYS))
        from ui.views.luxury import EMPTY_STATS
        self.assertEqual(set(stats), set(EMPTY_STATS))
        self.assertEqual((stats["trades"], stats["wins"]), (3, 2))
        self.assertAlmostEqual(stats["total_pnl"], 9.5)  # 29.5 (financing included) - 40 + 20
        self.assertAlmostEqual(stats["today_pnl"], 20.0)  # the 03:30Z close is still Oct 19 in ET
        self.assertAlmostEqual(stats["max_drawdown"], 40.0)
        r1, r2, r3 = 29.5 / 3.0, -10.0, 10.0  # fill vs stop; Day-2 moved stop uses initial_stop; planned entry
        self.assertAlmostEqual(stats["avg_r"], (r1 + r2 + r3) / 3)
        self.assertIsNone(stats["avg_r_reason"])
        self.assertEqual([round(p["Cumulative realized PnL"], 6) for p in curve], [29.5, -10.5, 9.5])
        self.assertEqual(opened[0]["symbol"], "EURUSD")
        self.assertEqual((opened[0]["side"], opened[0]["quantity"], opened[0]["entry_price"], opened[0]["stop"],
                          opened[0]["target"], opened[0]["unrealized_pnl"]), ("SHORT", 1000.0, 1.1, 1.105, 1.095, -3.25))

    def test_unknown_risk_means_no_average_r(self):
        stats, _curve, _open = demo_statistics(SCENARIO, NOW, EPOCH, {k: v for k, v in RISK.items() if k != "aif-r2"})
        self.assertIsNone(stats["avg_r"])
        self.assertIn("en 1 de 3 operaciones", stats["avg_r_reason"])
        self.assertAlmostEqual(stats["total_pnl"], 9.5)

    def test_empty_is_zero(self):
        for trades in ([], None, safe(raw_trade(5, "aif-test-x", "2026-10-19T00:00:00Z", "2026-10-19T01:00:00Z",
                                                pl="10"))):
            stats, curve, opened = demo_statistics(trades, NOW, EPOCH, {})
            self.assertEqual((stats["trades"], stats["wins"], stats["total_pnl"], stats["today_pnl"],
                              stats["max_drawdown"], stats["avg_r"]), (0, 0, 0.0, 0.0, 0.0, None))
            self.assertEqual((curve, opened), ([], []))

    def test_capital_day_and_first_open(self):
        stats = demo_statistics(SCENARIO, NOW, EPOCH, RISK)[0]
        capital = demo_capital(Decimal("100012.25"), Decimal("100009.5"), stats)
        self.assertAlmostEqual(capital["start"], 100000.0)
        self.assertAlmostEqual(capital["change"], 12.25)
        self.assertEqual(demo_capital(None, None, stats), {"start": None, "change": None})
        first = parse_time("2026-10-19T12:01:13Z")
        self.assertEqual(demo_day(first, datetime(2026, 10, 20, 3, 30, tzinfo=timezone.utc)), 1)  # 23:30 ET Oct 19
        self.assertEqual(demo_day(first, datetime(2026, 10, 20, 4, 30, tzinfo=timezone.utc)), 2)  # 00:30 ET Oct 20
        self.assertEqual(demo_day(first, datetime(2026, 12, 31, tzinfo=timezone.utc)), 30)
        self.assertIsNone(demo_day(None, NOW))
        rows = {"aif-test-1": {"opened_at": "2026-10-18T23:00:00+00:00"},
                "aif-a": {"opened_at": "2026-10-19 12:01:13.500000+00:00"},
                "aif-b": {"opened_at": "2026-10-20T12:00:00+00:00"}, "aif-old": {"opened_at": "2026-10-17T12:00:00Z"},
                "aif-c": {"opened_at": None}}
        self.assertEqual(first_demo_open(rows, EPOCH), datetime(2026, 10, 19, 12, 1, 13, 500000, tzinfo=timezone.utc))
        self.assertIsNone(first_demo_open({}, EPOCH))

    def test_risk_rows_loader_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.db"
            self.assertEqual(load_risk_rows(missing), {})
            self.assertFalse(missing.exists())
            path = Path(tmp) / "broker_state.db"
            with closing(sqlite3.connect(path)) as con:
                con.executescript(SCHEMA)
                for client_id in ("aif-r1", "aif-test-1"):
                    con.execute("INSERT INTO broker_demo_trades(client_id,run_id,symbol,side,units,entry,stop,target,"
                                "status,created_at,updated_at,opened_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (client_id, "r", "EURUSD", "LONG", 1000, "1.1", "1.09", "1.13", "OPEN", "t", "t",
                                 "2026-10-19T12:01:13+00:00"))
                con.commit()
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            rows = load_risk_rows(path)
            self.assertEqual(list(rows), ["aif-r1"])
            self.assertEqual(rows["aif-r1"]["units"], 1000)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            Path(tmp, "bad.db").write_bytes(b"not a db")
            self.assertEqual(load_risk_rows(Path(tmp, "bad.db")), {})


class Pages:
    """Fake trades endpoint: ``pages`` is a list of trade lists served in order; records every request."""

    def __init__(self, *pages, status=200):
        self.pages, self.calls, self.status = list(pages), [], status

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, body))
        page = self.pages.pop(0) if self.pages else []
        return self.status, {}, json.dumps({"trades": page, "lastTransactionID": "999"}).encode()


def full_page(top, opened="2026-10-19T12:00:00Z", last_opened=None):
    trades = [raw_trade(top - i, f"aif-{top - i}", opened, "2026-10-19T13:00:00Z") for i in range(ro.TRADES_PAGE_SIZE)]
    if last_opened:
        trades[-1]["openTime"] = last_opened
    return trades


class ReadOnlyTradesTests(unittest.TestCase):
    def setUp(self):
        default = patch("execution.oanda_readonly.urllib_transport", side_effect=refuse_network)
        self.default = default.start()
        self.addCleanup(default.stop)

    def tearDown(self):
        self.default.assert_not_called()

    def client(self, transport):
        return ro.OandaReadOnlyClient(TOKEN, ACCOUNT, "practice", transport=transport)

    def test_paging_follows_before_id_get_only_until_epoch(self):
        transport = Pages(full_page(1500), full_page(1000, last_opened="2026-10-18T10:00:00Z"), full_page(500))
        trades = self.client(transport).get_trades("CLOSED", since=EPOCH)
        self.assertEqual(len(transport.calls), 2)
        self.assertEqual(len(trades), 1000)
        for method, url, body in transport.calls:
            self.assertEqual((method, body), ("GET", None))
            parts = urlsplit(url)
            self.assertEqual((parts.scheme, parts.netloc, parts.path),
                             ("https", "api-fxpractice.oanda.com", f"/v3/accounts/{ACCOUNT}/trades"))
            self.assertTrue(ro._trades_url_ok(url, "api-fxpractice.oanda.com"))
        self.assertEqual(parse_qs(urlsplit(transport.calls[0][1]).query), {"state": ["CLOSED"], "count": ["500"]})
        self.assertEqual(parse_qs(urlsplit(transport.calls[1][1]).query)["beforeID"], ["1000"])

    def test_short_page_stops_and_hard_cap(self):
        transport = Pages(safe_short := [raw_trade(3, "aif-a", "2026-10-19T12:00:00Z")])
        self.assertEqual(len(self.client(transport).get_trades("OPEN", since=EPOCH)), len(safe_short))
        self.assertEqual(len(transport.calls), 1)
        transport = Pages(*[full_page(100000 - 500 * i) for i in range(20)])
        with self.assertRaises(ro.ReadOnlyError) as ctx:  # cap reached before the epoch: never partial totals
            self.client(transport).get_trades("CLOSED", since=EPOCH, max_pages=99)
        self.assertEqual(ctx.exception.code, "incomplete")
        self.assertEqual(len(transport.calls), ro.TRADES_MAX_PAGES)
        transport = Pages(full_page(1500), full_page(1000, last_opened="2026-10-18T10:00:00Z"))
        self.assertEqual(len(self.client(transport).get_trades("CLOSED", since=EPOCH, max_pages=2)), 1000)

    def test_page_cap_before_epoch_is_incomplete_not_success(self):
        env = {"OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": ACCOUNT}
        transport = Pages(*[full_page(100000 - 500 * i) for i in range(ro.TRADES_MAX_PAGES)])
        self.assertEqual(ro.demo_trades(env, transport=transport, since=EPOCH, now=NOW),
                         {"error": "incomplete", "as_of": NOW})
        view = ro.AccountView("OK", "practice", nav=Decimal("100000"), balance=Decimal("100000"))
        snapshot = demo_dashboard.demo_snapshot(NOW, SnapshotTests.ENV, account_reader=lambda env: view,
                                                trades_reader=lambda since: {"error": "incomplete", "as_of": NOW})
        self.assertIsNone(snapshot["stats"])
        self.assertEqual((snapshot["trades_error"], snapshot["curve"], snapshot["closed_rows"]), ("incomplete", [], []))
        self.assertEqual(snapshot["capital"], {"start": None, "change": None})

    def test_refresh_deadline_is_an_error_state(self):
        env = {"OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": ACCOUNT}
        now = [0.0]
        timeouts = []

        def slow(method, url, headers, body, timeout):  # every GET "takes" 3 s of the fake clock
            timeouts.append(timeout)
            now[0] += 3.0
            return 200, {}, json.dumps({"trades": full_page(100000 - 500 * len(timeouts))}).encode()

        result = ro.demo_trades(env, transport=slow, since=EPOCH, now=NOW, clock=lambda: now[0], deadline_seconds=8)
        self.assertEqual(result, {"error": "deadline", "as_of": NOW})
        self.assertEqual(len(timeouts), 3)  # 0 s, 3 s, 6 s; at 9 s the budget is gone: no 4th GET
        self.assertEqual(timeouts, [ro.TIMEOUT_SECONDS, ro.TIMEOUT_SECONDS, 2.0])  # capped by the time left
        now[0] = 0.0

        def late(method, url, headers, body, timeout):  # one short page, but it ends past the deadline
            now[0] += 9.0
            return 200, {}, json.dumps({"trades": []}).encode()
        self.assertEqual(ro.demo_trades(env, transport=late, since=EPOCH, now=NOW, clock=lambda: now[0],
                                        deadline_seconds=8), {"error": "deadline", "as_of": NOW})
        self.assertLessEqual(ro.TRADES_REFRESH_DEADLINE_SECONDS + ro.TIMEOUT_SECONDS, 12.0)

    def test_cache_never_holds_the_lock_during_network_io(self):
        import threading
        started, release = threading.Event(), threading.Event()
        fresh = {"closed": [], "open": [], "as_of": NOW}
        now = [0.0]

        def fetch(env, since):
            started.set()
            self.assertTrue(release.wait(5))
            return fresh
        old = {"closed": SCENARIO, "open": [], "as_of": NOW}
        cache = ro.CachedDemoTrades(ttl=5, clock=lambda: now[0], fetch=fetch, wait_seconds=5)
        cache._cache = {EPOCH: (0.0, old)}
        now[0] = 6.0  # expired: the next get refreshes
        worker = threading.Thread(target=lambda: results.append(cache.get(EPOCH)))
        results = []
        worker.start()
        self.assertTrue(started.wait(5))
        self.assertIs(cache.get(EPOCH), old)  # another session gets the stale value at once, no blocking
        release.set()
        worker.join(5)
        self.assertEqual(results, [fresh])
        self.assertIs(cache.get(EPOCH), fresh)
        cache = ro.CachedDemoTrades(ttl=5, clock=lambda: now[0], fetch=lambda env, since: 1 / 0)
        with self.assertRaises(ZeroDivisionError):
            cache.get(EPOCH)
        self.assertEqual(cache._inflight, {})  # a failed refresh never leaves the slot taken

    def test_only_safe_fields_and_no_account_id(self):
        transport = Pages(SCENARIO_RAW)
        trades = self.client(transport).get_trades("OPEN")
        self.assertEqual(set(trades[0]), set(ro.TRADE_FIELDS))
        self.assertNotIn(ACCOUNT, repr(trades))
        self.assertNotIn(TOKEN, repr(trades))
        self.assertEqual((trades[0]["stop_loss"], trades[0]["take_profit"], trades[0]["client_id"]),
                         (Decimal("1.10500"), Decimal("1.09500"), "aif-r4"))
        with self.assertRaises(ro.ReadOnlyError):
            self.client(transport).get_trades("ALL")

    def test_errors_and_default_transport_refusals(self):
        with self.assertRaises(ro.ReadOnlyError) as ctx:
            self.client(Pages(status=401)).get_trades("OPEN")
        self.assertEqual(ctx.exception.code, "auth")
        base = f"https://api-fxpractice.oanda.com/v3/accounts/{ACCOUNT}/trades"
        for method, url in (("GET", base + "?state=OPEN&foo=1"), ("GET", base + "/12/close?state=OPEN"),
                            ("POST", base + "?state=OPEN"), ("GET", base + "?count=500"), ("GET", base + "?state=OPEN&state=CLOSED"),
                            ("GET", "https://evil.test/v3/accounts/1/trades?state=OPEN")):
            with self.subTest(method=method, url=url), self.assertRaises(ro.ReadOnlyError):
                REAL_TRANSPORT(method, url, {}, None, 1)

    def test_demo_trades_practice_only_and_never_raises(self):
        transport = Pages([], [])
        self.assertIsNone(ro.demo_trades({}, transport=transport))
        self.assertIsNone(ro.demo_trades({"OANDA_LIVE_TOKEN": TOKEN, "OANDA_LIVE_ACCOUNT_ID": "001-001-87654321-001"},
                                         transport=transport))
        self.assertEqual(transport.calls, [])
        env = {"OANDA_PRACTICE_TOKEN": TOKEN, "OANDA_ACCOUNT_ID": ACCOUNT}
        self.assertIsNone(ro.demo_trades(env, transport=Pages(status=500)))
        result = ro.demo_trades(env, transport=Pages([], []), since=EPOCH, now=NOW)
        self.assertEqual(result, {"closed": [], "open": [], "as_of": NOW})
        with patch.object(ro._TRADES, "get", side_effect=RuntimeError("boom")):
            self.assertIsNone(ro.cached_demo_trades(EPOCH))

    def test_cache_ttl(self):
        now, calls = [0.0], []
        cache = ro.CachedDemoTrades(ttl=1, clock=lambda: now[0], fetch=lambda env, since: calls.append(since) or None)
        cache.get(EPOCH)
        now[0] = 4.9
        cache.get(EPOCH)
        self.assertEqual(len(calls), 1)
        now[0] = 5.0
        cache.get(EPOCH)
        self.assertEqual(len(calls), 2)


SCENARIO_RAW = [raw_trade(14, "aif-r4", "2026-10-20T14:30:00Z", units="-1000", upl="-3.25", sl="1.10500",
                          tp="1.09500")]
REAL_TRANSPORT = ro.urllib_transport


class SnapshotTests(unittest.TestCase):
    ENV = {"AI_FLOOR_BROKER_DEMO_ROUTING": "1", "AI_FLOOR_BROKER_STATE_DB_PATH": "/nonexistent/broker_state.db"}

    def test_read_failure_is_no_data_never_paper(self):
        view = ro.AccountView("OK", "practice", nav=Decimal("100000"), balance=Decimal("100000"))
        snapshot = demo_dashboard.demo_snapshot(NOW, self.ENV, trades_reader=lambda since: None,
                                                account_reader=lambda env: view)
        self.assertIsNone(snapshot["stats"])
        self.assertEqual((snapshot["curve"], snapshot["open_rows"], snapshot["nav"]), ([], [], 100000.0))
        snapshot = demo_dashboard.demo_snapshot(NOW, self.ENV, trades_reader=lambda since: 1 / 0,
                                                account_reader=lambda env: view)
        self.assertIsNone(snapshot["stats"])

    def test_snapshot_ok(self):
        view = ro.AccountView("OK", "practice", nav=Decimal("100012.25"), balance=Decimal("100009.5"))
        seen = []
        snapshot = demo_dashboard.demo_snapshot(
            NOW, self.ENV, trades_reader=lambda since: seen.append(since) or {"closed": SCENARIO, "open": []},
            account_reader=lambda env: view)
        self.assertEqual(seen, [EPOCH])
        self.assertEqual(snapshot["stats"]["trades"], 3)
        self.assertAlmostEqual(snapshot["capital"]["start"], 100000.0)
        self.assertEqual(demo_dashboard.demo_mode({}), False)
        self.assertEqual(demo_dashboard.demo_mode({"AI_FLOOR_BROKER_DEMO_ROUTING": "1"}), True)


try:
    import streamlit  # noqa: F401
    HAVE_UI = True
except ImportError:  # pragma: no cover
    HAVE_UI = False


@unittest.skipUnless(HAVE_UI, "streamlit not installed")
class DashboardTests(unittest.TestCase):
    """Routing ON: demo numbers only. Routing OFF: the PAPER screens (and no demo read at all)."""

    def setUp(self):
        import test_dashboard_luxury as tdl
        self.case = tdl.LuxuryDashboardTests("test_empty_new_screens")
        self.case.setUp()
        self.addCleanup(self.case.doCleanups)
        self.tmp = Path(self.case.tmp.name)
        self.state = self.tmp / "broker_state.db"
        default = patch("execution.oanda_readonly.urllib_transport", side_effect=refuse_network)
        default.start()
        self.addCleanup(default.stop)
        self.trades = {"closed": SCENARIO, "open": []}
        self.view = ro.AccountView("OK", "practice", "101-…4567-001", "USD", Decimal("100009.5"),
                                   Decimal("100012.25"))
        for target, reader in (("execution.oanda_readonly.cached_demo_trades", lambda since: self.trades),
                               ("execution.oanda_readonly.cached_account_view", lambda env: self.view)):
            patcher = patch(target, side_effect=reader)
            patcher.start()
            self.addCleanup(patcher.stop)

    def env(self, **extra):
        return patch.dict(os.environ, {"AI_FLOOR_BROKER_DEMO_ROUTING": "1",
                                       "AI_FLOOR_BROKER_STATE_DB_PATH": str(self.state), **extra})

    def page(self, name):
        app = self.case.app()
        app.radio[0].set_value(name).run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertFalse(app.error, str(app.error))
        return app, "\n".join(m.value for m in app.markdown)

    def test_routing_off_shows_paper_and_never_reads_the_demo(self):
        os.environ.pop("AI_FLOOR_BROKER_DEMO_ROUTING", None)
        with patch("execution.demo_dashboard.demo_snapshot", side_effect=AssertionError("demo read")):
            _app, content = self.page("Overview")
        self.assertIn("Capital de tu cuenta de práctica", content)
        self.assertIn("+$3.00", content)  # the PAPER fixture trade
        self.assertNotIn("Demo OANDA", content)

    def test_routing_on_overview_is_demo_only(self):
        before = self.case.digest()
        with self.env():
            _app, content = self.page("Overview")
        self.assertIn("Demo OANDA (práctica) · desde 18 oct 2026 18:25 ET", content)
        self.assertIn("Capital de la cuenta demo OANDA", content)
        self.assertIn("$100,012.25", content)
        self.assertIn("inicio del demo $100,000.00", content)
        self.assertIn("+$9.50", content)
        self.assertIn("archivados y no cuentan", content)
        self.assertNotIn("Capital de tu cuenta de práctica", content)
        self.assertNotIn("+$3.00", content)
        self.assertEqual(self.case.digest(), before)
        self.assertFalse(self.state.exists())  # read-only: the broker state DB is never created

    def test_routing_on_read_failure_shows_sin_datos(self):
        self.trades = None
        with self.env():
            _app, content = self.page("Overview")
        self.assertIn("sin datos", content)
        self.assertNotIn("+$3.00", content)
        self.assertNotIn("Capital de tu cuenta de práctica", content)

    def test_routing_on_incomplete_or_slow_read_shows_sin_datos_for_totals(self):
        for code, reason in (("incomplete", "totales incompletos"), ("deadline", "OANDA tardó demasiado")):
            self.trades = {"error": code, "as_of": NOW}
            with self.subTest(code=code), self.env():
                _app, content = self.page("Overview")
                self.assertIn(f"sin datos ({reason}", content)
                self.assertIn("Resultado desde el inicio del demo: sin datos", content)
                self.assertNotIn("+$3.00", content)
                _app, content = self.page("Trades")
                self.assertIn(f"sin datos ({reason}", content)
                self.assertNotIn("Operaciones cerradas del demo OANDA", content)

    def test_routing_on_demo_module_unavailable_never_shows_paper(self):
        import sys
        from ui.views import luxury
        self.assertEqual(luxury.DEMO_DEFAULT_EPOCH, EPOCH)
        with self.env(), patch.dict(sys.modules, {"execution.demo_dashboard": None}):
            api = luxury.demo_api()
            self.assertIsNotNone(api)
            self.assertEqual(api["epoch"](), EPOCH)  # the Decisions filter stays on
            self.assertIsNone(api["snapshot"](NOW)["stats"])
            _app, content = self.page("Overview")
            self.assertIn("sin datos", content)
            self.assertIn("Demo OANDA (práctica)", content)
            self.assertNotIn("+$3.00", content)
            self.assertNotIn("Capital de tu cuenta de práctica", content)
            _app, content = self.page("Trades")
            self.assertIn("sin datos (la vista del demo no está disponible)", content)
            self.assertNotIn("+$3.00", content)
            self.page("Decisions")
            from ui.views.experiment import demo_progress_text
            self.assertEqual(demo_progress_text(NOW), "Demo OANDA: sin datos")
        with patch.dict(os.environ, {"AI_FLOOR_BROKER_DEMO_ROUTING": "0"}), \
                patch.dict(sys.modules, {"execution.demo_dashboard": None}):
            self.assertIsNone(luxury.demo_api())

    def test_routing_on_empty_demo_starts_from_zero(self):
        self.trades = {"closed": [], "open": []}
        with self.env():
            _app, content = self.page("Overview")
        self.assertIn("Desde el inicio del demo: sin ganancias ni pérdidas", content)
        self.assertIn("Aún no hay operaciones cerradas del demo.", content)
        self.assertNotIn("+$3.00", content)

    def test_routing_on_trades_lists_oanda_demo(self):
        self.trades = {"closed": SCENARIO, "open": safe(*SCENARIO_RAW)}
        with self.env():
            _app, content = self.page("Trades")
        self.assertIn("Operaciones cerradas del demo OANDA", content)
        self.assertIn("−$40.00", content)
        self.assertIn("−$3.25", content)
        self.assertNotIn("+$3.00", content)
        self.assertNotIn("+$999.00", content)  # the manual test order
        self.assertNotIn("+$500.00", content)  # opened before the epoch

    def test_routing_on_decisions_since_epoch(self):
        with self.env():
            _app, content = self.page("Decisions")
        self.assertIn("Ciclo de análisis", content)  # fixture runs are a minute old: after the epoch
        with self.env(AI_FLOOR_DEMO_EPOCH="2999-01-01T00:00:00Z"):
            _app, content = self.page("Decisions")
        self.assertNotIn("Ciclo de análisis", content)
        from ui.views.luxury import since_epoch
        rows = [{"as_of": "2026-10-18T22:24:59+00:00"}, {"as_of": "2026-10-18T22:25:00+00:00"}, {"as_of": None},
                {"as_of": "bad"}]
        self.assertEqual(since_epoch(rows, EPOCH), [rows[1]])

    def test_experiment_day_counter(self):
        from ui.views.experiment import demo_progress_text
        os.environ.pop("AI_FLOOR_BROKER_DEMO_ROUTING", None)
        self.assertIsNone(demo_progress_text(NOW))
        with self.env():
            self.assertEqual(demo_progress_text(NOW), "Demo OANDA: aún sin operaciones del demo")
            with closing(sqlite3.connect(self.state)) as con:
                con.executescript(SCHEMA)
                for client_id, opened in (("aif-test-20261018T150000Z", "2026-10-18T23:00:00+00:00"),
                                          ("aif-r1", "2026-10-19 12:01:13.250000+00:00")):
                    con.execute("INSERT INTO broker_demo_trades(client_id,run_id,symbol,side,units,entry,stop,target,"
                                "status,created_at,updated_at,opened_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                                (client_id, "r", "EURUSD", "LONG", 1000, "1.1", "1.09", "1.13", "CLOSED", "t", "t",
                                 opened))
                con.commit()
            self.assertEqual(demo_progress_text(NOW), "Demo OANDA: Día 2 de 30")
            app = self.case.app()
            app.radio[0].set_value("EXPERIMENT").run()
            self.assertFalse(app.exception, str(app.exception))


class ArchiveTests(unittest.TestCase):
    PLENTY = staticmethod(lambda _path: SimpleNamespace(free=10 ** 12))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "trading_floor.db"
        self.target = Path(self.tmp.name) / "archive" / "trading_floor_pre_demo_20261018.db"

    def make_db(self, wal=False):
        with closing(sqlite3.connect(self.db)) as con:
            if wal:
                con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE closed_trades(id TEXT PRIMARY KEY, payload TEXT)")
            con.executemany("INSERT INTO closed_trades VALUES (?,?)", [(f"t{i}", "{}") for i in range(50)])
            con.commit()

    def digest(self):
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(self.tmp.name).iterdir()
                if p.is_file() and not p.name.endswith("-shm")}

    def test_created_once_source_unchanged(self):
        self.make_db()
        before = self.digest()
        with self.assertLogs("runtime.cloud", "INFO") as logs:
            self.assertEqual(cloud.archive_pre_demo_db(self.db, disk_usage=self.PLENTY), "PRE_DEMO_ARCHIVED")
        self.assertEqual(self.digest(), before)
        self.assertRegex(logs.output[0], r"component=archive event=PRE_DEMO_ARCHIVED bytes=\d+$")
        self.assertNotIn(self.tmp.name, "\n".join(logs.output))
        with closing(sqlite3.connect(f"{self.target.as_uri()}?mode=ro", uri=True)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM closed_trades").fetchone()[0], 50)
        self.assertEqual(self.target.stat().st_mode & 0o777, 0o600)
        self.assertEqual([p.name for p in self.target.parent.iterdir()], [self.target.name])  # no temp left
        stamp = self.target.stat().st_mtime_ns
        with self.assertLogs("runtime.cloud", "INFO") as logs:
            self.assertEqual(cloud.archive_pre_demo_db(self.db, disk_usage=self.PLENTY), "PRE_DEMO_ARCHIVE_SKIPPED")
        self.assertIn("reason=exists", logs.output[0])
        self.assertEqual(self.target.stat().st_mtime_ns, stamp)
        self.assertTrue(self.db.exists())

    def test_wal_source_with_frames_is_archived_and_unchanged(self):
        self.make_db(wal=True)
        with closing(sqlite3.connect(self.db)) as writer:  # un-checkpointed frames stay in the WAL
            writer.execute("PRAGMA wal_autocheckpoint=0")
            writer.execute("INSERT INTO closed_trades VALUES ('late', '{}')")
            writer.commit()
            before = self.digest()
            self.assertGreater(Path(f"{self.db}-wal").stat().st_size, 0)
            self.assertEqual(cloud.archive_pre_demo_db(self.db, disk_usage=self.PLENTY), "PRE_DEMO_ARCHIVED")
            self.assertEqual(self.digest(), before)
        with closing(sqlite3.connect(f"{self.target.as_uri()}?mode=ro", uri=True)) as con:
            self.assertEqual(con.execute("SELECT count(*) FROM closed_trades").fetchone()[0], 51)

    def test_skips_never_raise(self):
        with self.assertLogs("runtime.cloud", "INFO") as logs:
            self.assertEqual(cloud.archive_pre_demo_db(self.db, disk_usage=self.PLENTY), "PRE_DEMO_ARCHIVE_SKIPPED")
        self.assertIn("reason=missing_source", logs.output[0])
        self.assertFalse(self.target.parent.exists())
        self.make_db()
        size = self.db.stat().st_size
        tight = lambda _path: SimpleNamespace(free=2 * size + cloud.ARCHIVE_MARGIN_BYTES - 1)  # noqa: E731
        with self.assertLogs("runtime.cloud", "INFO") as logs:
            self.assertEqual(cloud.archive_pre_demo_db(self.db, disk_usage=tight), "PRE_DEMO_ARCHIVE_SKIPPED")
        self.assertIn("reason=low_space", logs.output[0])
        self.assertFalse(self.target.exists())

        def broken(_path):
            raise OSError("statvfs failed")
        with self.assertLogs("runtime.cloud", "INFO") as logs:
            self.assertEqual(cloud.archive_pre_demo_db(self.db, disk_usage=broken), "PRE_DEMO_ARCHIVE_SKIPPED")
        self.assertIn("reason=error error_type=OSError", logs.output[0])
        with patch.object(cloud.sqlite3, "connect", side_effect=sqlite3.OperationalError("locked")), \
                self.assertLogs("runtime.cloud", "INFO"):
            self.assertEqual(cloud.archive_pre_demo_db(self.db, disk_usage=self.PLENTY), "PRE_DEMO_ARCHIVE_SKIPPED")
        self.assertEqual(list(self.target.parent.iterdir()), [])  # partial temp removed
        bogus = Path(self.tmp.name) / "bogus.db"
        bogus.write_bytes(b"x")
        Path(f"{bogus}-wal").write_bytes(b"x")
        with self.assertLogs("runtime.cloud", "INFO") as logs:
            self.assertEqual(cloud.archive_pre_demo_db(bogus, disk_usage=self.PLENTY), "PRE_DEMO_ARCHIVE_SKIPPED")
        self.assertIn("reason=not_sqlite", logs.output[0])
        self.assertTrue(Path(f"{bogus}-wal").exists())

    def test_deadline_aborts_and_removes_only_the_temp_file(self):
        self.make_db()
        with closing(sqlite3.connect(self.db)) as con:  # many pages => several backup steps
            con.executemany("INSERT INTO closed_trades VALUES (?,?)", [(f"big{i}", "x" * 4000) for i in range(400)])
            con.commit()
        before = self.digest()
        ticks = iter([0.0, 0.0, 0.0] + [25.0] * 1000)  # start, first check, first step... then past 20 s
        with self.assertLogs("runtime.cloud", "INFO") as logs:
            self.assertEqual(cloud.archive_pre_demo_db(self.db, disk_usage=self.PLENTY, clock=lambda: next(ticks)),
                             "PRE_DEMO_ARCHIVE_SKIPPED")
        self.assertEqual(len(logs.output), 1)
        self.assertRegex(logs.output[0], r"event=PRE_DEMO_ARCHIVE_SKIPPED reason=timeout$")
        self.assertEqual(self.digest(), before)  # the source is untouched
        self.assertEqual(list(self.target.parent.iterdir()), [])  # temp removed, no archive
        ticks = iter([0.0] + [20.0] * 10)  # even a one-step copy is dropped once the deadline has passed
        with self.assertLogs("runtime.cloud", "INFO") as logs:
            cloud.archive_pre_demo_db(self.db, disk_usage=self.PLENTY, clock=lambda: next(ticks))
        self.assertIn("reason=timeout", logs.output[0])
        self.assertFalse(self.target.exists())
        self.assertEqual(cloud.archive_pre_demo_db(self.db, disk_usage=self.PLENTY), "PRE_DEMO_ARCHIVED")

    def test_serve_tests_leave_no_archive_in_the_repo(self):
        """Every serve() test either mocks the archive or points it at a temp dir: none writes data/runtime/archive."""
        import test_security_audit
        import test_security_hardening
        import test_watchdog_diagnostics
        repo = Path(__file__).resolve().parent
        archive = repo / "data" / "runtime" / cloud.ARCHIVE_DIR
        listing = sorted(archive.iterdir()) if archive.exists() else None
        real, in_repo = cloud.archive_pre_demo_db, []

        def guard(db_path, **kwargs):
            if Path(db_path).resolve().is_relative_to(repo):
                in_repo.append(str(db_path))
                return "PRE_DEMO_ARCHIVE_SKIPPED"
            return real(db_path, **kwargs)
        loader = unittest.TestLoader()
        suite = unittest.TestSuite(loader.loadTestsFromTestCase(case) for case in (
            test_security_audit.CloudServeTests, test_security_hardening.CloudStartupTests,
            test_watchdog_diagnostics.ServeWatchdogTests))
        suite.addTest(ArchiveTests("test_serve_archives_before_any_child_and_preflight_is_untouched"))
        with patch.object(cloud, "archive_pre_demo_db", side_effect=guard):
            result = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)
        self.assertEqual(in_repo, [])
        self.assertEqual(sorted(archive.iterdir()) if archive.exists() else None, listing)

    def test_serve_archives_before_any_child_and_preflight_is_untouched(self):
        order = []
        env = {"RENDER": "true", "PORT": "10000", "AI_FLOOR_DASHBOARD_PASSWORD": "a-strong-password-123",
               "AI_FLOOR_DB_PATH": str(self.db), "AI_FLOOR_BROKER_STATE_DB_PATH": str(Path(self.tmp.name) / "s.db")}

        class Stop(Exception):
            pass

        def popen(*_args, **_kwargs):
            order.append("popen")
            raise Stop

        preflight = SimpleNamespace(infra_ready=True)
        with patch.dict(os.environ, env, clear=True), patch.object(cloud.subprocess, "Popen", side_effect=popen), \
                patch.object(cloud.os, "umask"), patch.object(cloud.signal, "signal"), \
                patch("execution.log_redaction.install_log_redaction"), \
                patch.object(cloud, "cloud_preflight", return_value=preflight) as doctor, \
                patch.object(cloud, "archive_pre_demo_db", side_effect=lambda path: order.append(("archive", path))):
            with self.assertRaises(Stop):
                cloud.serve()
        self.assertEqual(order, [("archive", self.db), "popen"])
        self.assertEqual(doctor.call_count, 1)
        with patch.dict(os.environ, env, clear=True), patch.object(cloud.subprocess, "Popen", side_effect=popen), \
                patch.object(cloud.os, "umask"), patch.object(cloud.signal, "signal"), \
                patch("execution.log_redaction.install_log_redaction"), \
                patch.object(cloud, "cloud_preflight", return_value=preflight), \
                patch.object(cloud, "archive_pre_demo_db", side_effect=RuntimeError("boom")):
            with self.assertRaises(Stop):  # an archive failure never blocks startup
                cloud.serve()


if __name__ == "__main__":
    unittest.main()
