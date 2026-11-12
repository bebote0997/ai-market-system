"""Exness API adapter: sizing, read-only default, idempotency, brakes, stale quotes."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import unittest

from execution.broker_port import ACCEPTED, APPROVED, BrokerBalance, OrderPlan, RiskApproval
from execution.exness_brakes import RESUME_PHRASE, ExnessBrakes
from execution.exness_broker import REAL_CONFIRM, ExnessBroker, broker_selected, real_execution_armed
from execution.exness_client import ExnessClient
from execution.exness_session import ExnessSession
from execution.exness_sign import EMPTY_BODY_HASH, body_hash, payload_bytes
from execution.exness_sizing import size_lots, spec_from_conditions
from execution.exness_stream import backoff_seconds, subscribe_ticks
from execution.exness_transport import FakeExnessTransport, Position
from runtime.demo_runner import REAL_EXECUTION_ENABLED


ROOT = Path(__file__).resolve().parent
NOW = datetime(2026, 11, 11, 15, 0, tzinfo=timezone.utc)
SECRET = "this-private-key-is-not-logged-anywhere"
COND = {"volume_min": "0.01", "volume_step": "0.01", "volume_max": "200", "point_digits": 5,
        "contractSize": "100000", "profitRate": "1"}


def armed(**extra):
    env = {"AI_FLOOR_BROKER": "exness", "AI_FLOOR_EXNESS_REAL_EXECUTION": "1",
           "AI_FLOOR_EXNESS_REAL_CONFIRM": REAL_CONFIRM, "EXN_PRIVATE_KEY": SECRET,
           "EXN_API_KEY": "exnsk-public-key-value", "EXN_ACCOUNT_ID": "123456",
           "EXN_DISCOVERY_BASE_URL": "https://gateway.example"}
    env.update(extra)
    return env


def terminal():
    fake = FakeExnessTransport(clock=lambda: NOW)
    fake.balance = BrokerBalance(Decimal("500"), Decimal("500"), Decimal("0"), "USD")
    fake.conditions = {"EURUSD": dict(COND)}
    fake.push_tick(Decimal("1.10000"), Decimal("1.10002"), NOW)
    return fake


def broker(fake=None, env=None, clock=None):
    fake = terminal() if fake is None else fake
    env = armed() if env is None else env
    return ExnessBroker(fake, env=env, brakes=ExnessBrakes.from_env(fake, env), clock=clock or (lambda: NOW)), fake


def plan(lots="0.02", stop="1.09750"):
    return OrderPlan("EURUSD", "LONG", Decimal(lots), Decimal("1.10000"), Decimal(stop), Decimal("1.10500"),
                     "aif-run-1", "run-1")


def approval(lots="0.02", stop="1.09750"):
    return RiskApproval(APPROVED, "run-1", "EURUSD", "LONG", Decimal(lots), Decimal("1.10000"), Decimal(stop),
                        Decimal("1.10500"))


class SizingTests(unittest.TestCase):
    def spec(self):
        spec, reason = spec_from_conditions(COND, "USD")
        self.assertIsNone(reason)
        return spec

    def test_standard_500_one_percent_25_pips_is_two_hundredths(self):
        lots, reason = size_lots(Decimal("500"), Decimal("0.01"), Decimal("1.10000"), Decimal("1.09750"), self.spec())
        self.assertEqual((lots, reason), (Decimal("0.02"), None))

    def test_20_pips_floors_and_a_smaller_stop_distance_is_not_rounded_up(self):
        lots, reason = size_lots(Decimal("500"), Decimal("0.01"), Decimal("1.10000"), Decimal("1.09800"), self.spec())
        self.assertEqual((lots, reason), (Decimal("0.02"), None))
        self.assertLess(lots, Decimal("0.025"))

    def test_below_minimum_is_a_skip(self):
        lots, reason = size_lots(Decimal("500"), Decimal("0.01"), Decimal("1.10000"), Decimal("1.08000"), self.spec())
        self.assertEqual((lots, reason), (Decimal("0"), "below_volume_min"))

    def test_missing_contract_size_is_not_invented(self):
        payload = dict(COND)
        del payload["contractSize"]
        spec, reason = spec_from_conditions(payload, "USD")
        self.assertEqual((spec, reason), (None, "contract_size_unavailable"))


class SafetyTests(unittest.TestCase):
    def test_real_flag_stays_false_and_exness_is_opt_in(self):
        self.assertIs(REAL_EXECUTION_ENABLED, False)
        self.assertFalse(broker_selected({}))
        self.assertFalse(real_execution_armed({"AI_FLOOR_EXNESS_REAL_EXECUTION": "1"}))
        self.assertTrue(real_execution_armed(armed()))
        text = (ROOT / "render.yaml").read_text(encoding="utf-8")
        self.assertNotIn("EXN_", text)
        self.assertNotIn("AI_FLOOR_EXNESS", text)

    def test_orders_are_refused_until_both_keys_match(self):
        adapter, fake = broker(env={"AI_FLOOR_BROKER": "exness", "EXN_PRIVATE_KEY": SECRET})
        result = adapter.submit_plan(plan(), approval())
        self.assertEqual(result.reason, "read_only")
        self.assertEqual(fake.commands, [])
        self.assertEqual(adapter.connectivity_report()["contract_size"], "100000")
        self.assertEqual(fake.commands, [])

    def test_fill_logs_spread_cost_and_a_retry_does_not_double_send(self):
        adapter, fake = broker()
        with self.assertLogs("ai_floor.exness", level="INFO") as logs:
            first = adapter.submit_plan(plan(), approval())
            second = adapter.submit_plan(plan(), approval())
        self.assertEqual((first.status, first.broker_ref), (ACCEPTED, "1"))
        self.assertEqual(fake.positions[0].comment, "aif-run-1")
        self.assertEqual((second.status, second.reason, second.broker_ref), (ACCEPTED, "already_submitted", "1"))
        self.assertEqual(len(fake.commands), 1)
        text = "\n".join(logs.output)
        self.assertIn("expected_cost=0.04", text)
        self.assertNotIn(SECRET, text)

    def test_second_quote_resizes_or_skips_when_the_cap_breaks(self):
        jpy = {"volume_min": "0.01", "volume_step": "0.01", "point_digits": 3,
               "contractSize": "100000", "profitRate": "0.01"}

        class MovingQuote(FakeExnessTransport):
            def __init__(self, second):
                super().__init__(clock=lambda: NOW)
                self.second = second
                self.reads = 0

            def read_state(self):
                self.reads += 1
                if self.reads >= 2:
                    self.push_tick(self.second[0], self.second[1], NOW)
                return super().read_state()

        def opened(second):
            fake = MovingQuote(second)
            fake.balance = BrokerBalance(Decimal("500"), Decimal("500"), Decimal("0"), "USD")
            fake.instruments = ("USDJPY",)
            fake.conditions = {"USDJPY": jpy}
            fake.push_tick(Decimal("150.19"), Decimal("150.20"), NOW)
            adapter = ExnessBroker(fake, env=armed(), brakes=ExnessBrakes.from_env(fake, armed()), clock=lambda: NOW)
            result = adapter.open_market(symbol="USDJPY", side="LONG", quantity=Decimal("0.02"),
                                          entry=Decimal("150.20"), stop=Decimal("150.00"), target=Decimal("150.60"),
                                          client_id="combo-lon-2026-01-15-LONG", source_run_id="session-combo")
            return result, fake

        resized, fake = opened((Decimal("150.29"), Decimal("150.30")))
        self.assertEqual(resized.status, ACCEPTED)
        self.assertEqual(fake.commands[0].volume, Decimal("0.01"))
        self.assertEqual(fake.commands[0].price, Decimal("150.30"))
        skipped, fake = opened((Decimal("150.50"), Decimal("150.51")))
        self.assertEqual(skipped.reason, "min_volume_over_cap")
        self.assertEqual(fake.commands, [])

    def test_above_risk_and_unknown_symbol(self):
        adapter, fake = broker()
        refused = adapter.submit_plan(plan(lots="0.03"), approval(lots="0.03"))
        self.assertEqual(refused.reason, "above_risk")
        self.assertEqual(fake.commands, [])
        fake.instruments = ()
        missing = adapter.submit_plan(plan(), approval())
        self.assertEqual(missing.reason, "symbol_unresolved")

    def test_one_position_friday_stale_and_reread(self):
        adapter, fake = broker()
        fake.positions.append(Position("9", "EURUSD", "LONG", Decimal("0.01"), Decimal("1.1"), comment="manual"))
        self.assertEqual(adapter.submit_plan(plan(), approval()).reason, "position_already_open")
        self.assertEqual(fake.commands, [])
        friday_at = datetime(2026, 10, 16, 21, 0, tzinfo=timezone.utc)
        friday_book = terminal()
        friday_book.push_tick(Decimal("1.10000"), Decimal("1.10002"), friday_at)
        friday = ExnessBroker(friday_book, env=armed(), brakes=ExnessBrakes.from_env(friday_book, armed()),
                               clock=lambda: friday_at)
        self.assertEqual(friday.submit_plan(plan(), approval()).reason, "friday_close_window")
        stale = terminal()
        stale.push_tick(Decimal("1.10000"), Decimal("1.10002"), NOW - timedelta(seconds=30))
        alerts = []
        stale_broker = ExnessBroker(stale, env=armed(), brakes=ExnessBrakes.from_env(stale, armed()),
                                    clock=lambda: NOW, on_alert=lambda kind, detail: alerts.append(kind))
        self.assertEqual(stale_broker.submit_plan(plan(), approval()).reason, "quote_stale")
        self.assertEqual(alerts, ["EXNESS_DISCONNECTED"])
        self.assertFalse(stale_broker.health().connected)
        self.assertEqual(stale.commands, [])

        class Move(FakeExnessTransport):
            def __init__(self):
                super().__init__(clock=lambda: NOW)
                self.balance = BrokerBalance(Decimal("500"), Decimal("500"), Decimal("0"), "USD")
                self.conditions = {"EURUSD": dict(COND)}
                self.push_tick(Decimal("1.10000"), Decimal("1.10002"), NOW)
                self.reads = 0

            def read_state(self):
                self.reads += 1
                if self.reads >= 2:
                    self.positions = [Position("1", "EURUSD", "SHORT", Decimal("0.01"), Decimal("1.1"))]
                return super().read_state()

        moving = Move()
        changed = ExnessBroker(moving, env=armed(), brakes=ExnessBrakes.from_env(moving, armed()), clock=lambda: NOW)
        self.assertEqual(changed.submit_plan(plan(), approval()).reason, "reread_changed")
        self.assertEqual(moving.commands, [])

    def test_disconnect_clears_the_quote_and_backoff_grows(self):
        fake = terminal()
        fake.quotes.mark_disconnected()
        self.assertFalse(fake.quotes.fresh(NOW, 5))
        self.assertEqual([backoff_seconds(n) for n in range(6)], [1, 2, 4, 8, 16, 30])
        self.assertEqual(subscribe_ticks(["EURUSD"])["subscribe"]["event"], "ticks")


class BrakeTests(unittest.TestCase):
    def test_ten_and_fifty_dollars_halt_until_the_phrase(self):
        fake = terminal()
        brakes = ExnessBrakes.from_env(fake, armed())
        self.assertTrue(brakes.evaluate(Decimal("500"), "USD", NOW).entry_allowed)
        halted = brakes.evaluate(Decimal("490"), "USD", NOW)
        self.assertEqual((halted.entry_allowed, halted.flatten, halted.reason), (False, True, "daily_loss"))
        self.assertFalse(brakes.clear_halt("no"))
        self.assertTrue(brakes.clear_halt(RESUME_PHRASE))
        wide = ExnessBrakes(fake, daily_usd=Decimal("1000"), drawdown_usd=Decimal("50"), start_balance=Decimal("500"))
        wide.evaluate(Decimal("500"), "USD", NOW)
        self.assertEqual(wide.evaluate(Decimal("450"), "USD", NOW).reason, "drawdown")
        other = ExnessBrakes.from_env(FakeExnessTransport(), armed())
        self.assertEqual(other.evaluate(Decimal("500"), "EUR", NOW).reason, "currency_unsupported")

    def test_armed_cycle_refuses_an_engine_plan(self):
        from types import SimpleNamespace
        fake = terminal()
        session = ExnessSession(broker(fake)[0], env=armed(), clock=lambda: NOW)
        self.assertIsNone(session._runner)
        # 25 pips on 500 USD sizes to 0.02, which submit_plan would accept. The cycle must not send it.
        decision = SimpleNamespace(status="APPROVED", symbol="EURUSD", side="LONG", entry=Decimal("1.10000"),
                                   stop=Decimal("1.09750"), target=Decimal("1.10500"), risk_fraction=Decimal("0.01"))
        context = {"symbol": "EURUSD", "run_id": "run-9", "eligible": True,
                   "deterministic": SimpleNamespace(final_status="PLAN_READY", risk_decision=decision, trade_plan=decision),
                   "ai": SimpleNamespace(final_status="PLAN_READY", risk_decision=SimpleNamespace(status="APPROVED"))}
        for extra in ({}, {"AI_FLOOR_EMA_ONLY": "1"}, {"AI_FLOOR_EMA_ONLY": "0"}):
            session = ExnessSession(broker(fake)[0], env=armed(**extra), clock=lambda: NOW)
            self.assertEqual(session.on_cycle(context, "PLAN_READY", NOW), ("NOT_ROUTED", "session_combo_only"))
        self.assertEqual(fake.commands, [])
        bare = armed()
        del bare["AI_FLOOR_EXNESS_REAL_EXECUTION"]
        del bare["AI_FLOOR_EXNESS_REAL_CONFIRM"]
        quiet = ExnessSession(broker(fake)[0], env=bare, clock=lambda: NOW)
        self.assertEqual(quiet.on_cycle(context, "PLAN_READY", NOW), ("NOT_ROUTED", "session_combo_only"))
        self.assertEqual(fake.commands, [])


class ClientTests(unittest.TestCase):
    def test_render_lock_pins_cryptography_and_the_ticks_socket(self):
        direct = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        locked = (ROOT / "requirements-ci.txt").read_text(encoding="utf-8")
        self.assertIn("cryptography==50.0.2", direct)
        self.assertIn("websockets==17.2", direct)
        self.assertIn("cryptography==50.0.2", locked)
        self.assertIn("sha256:9dab55f57c74c3cad24c323bacbbd04be4705ba6eb0d92e920b1fc4837ed5079", locked)
        self.assertIn("websockets==17.2", locked)
    def test_signed_get_uses_the_documented_empty_body_hash_and_idempotency_is_once(self):
        self.assertEqual(body_hash(b""), EMPTY_BODY_HASH)
        payload = json.loads(payload_bytes("exnsk-public-key-value", "", 1_000, "GET",
                                           "/v1/trading/access-point?account_id=123456", EMPTY_BODY_HASH))
        self.assertEqual(payload["idempotency_key"], "")
        self.assertEqual(payload["path"], "/v1/trading/access-point?account_id=123456")
        calls = []
        now = [1_000_000]
        slept = []

        def caller(method, url, headers, body):
            calls.append((method, url, dict(headers), body))
            if "access-point" in url:
                return 200, {"access_point": "https://ap.example", "account_id": "123456"}, {}
            if method == "POST":
                return 202, {"operation_id": "9", "status": "accepted"}, {}
            return 429, {"error_message": "REQUEST_RATE_LIMIT"}, {"Retry-After": "2"}

        def sleep(seconds):
            slept.append(seconds)
            now[0] += int(round(seconds * 1000))

        client = ExnessClient(armed(), caller=caller, sign=lambda data: b"sig", now_ms=lambda: now[0], sleep=sleep)
        self.assertEqual(client.discover(), "https://ap.example")
        signed = calls[0][2]
        self.assertEqual(signed["EXN-IDEMPOTENCY-KEY"], "")
        self.assertNotIn(SECRET, json.dumps(signed))
        now[0] += 100
        first = client.mutate("POST", "/v1/trading/accounts/123456/positions", {"instrument": "EURUSD"}, "aif-run-1")
        now[0] += 100
        second = client.mutate("POST", "/v1/trading/accounts/123456/positions", {"instrument": "EURUSD"}, "aif-run-1")
        posts = [call for call in calls if call[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(first["payload"]["operation_id"], second["payload"]["operation_id"])
        self.assertEqual(posts[0][2]["EXN-IDEMPOTENCY-KEY"], "aif-run-1")
        path = "/v1/server-events/accounts/123456/ws/ticks"
        signed = client.signed_headers("GET", path)
        self.assertEqual(signed["EXN-IDEMPOTENCY-KEY"], "")
        self.assertEqual(signed["EXN-SIGN-VERSION"], "1")
        import base64
        padded = signed["EXN-DATA"] + "=" * (-len(signed["EXN-DATA"]) % 4)
        body = json.loads(base64.urlsafe_b64decode(padded))
        self.assertEqual((body["method"], body["path"], body["idempotency_key"]), ("GET", path, ""))
        before = now[0]
        again = client.signed_headers("GET", path)
        self.assertEqual(slept, [0.1])
        self.assertEqual(int(again["EXN-TIMESTAMP"]) - before, 100)
        now[0] += 100
        limited = client.mutate("GET", "/v1/configuration/accounts/123456/account", None, "read-1")
        # GET through mutate still sends; the canned caller returns 429 and that key is remembered.
        self.assertEqual(limited["status"], 429)
        self.assertEqual(client.mutate("GET", "/v1/configuration/accounts/123456/account", None, "read-1")["status"], 429)

    def test_short_latency_waits_for_the_gap_and_a_429_does_not(self):
        now = [1_000_000]
        calls = []
        slept = []

        def caller(method, url, headers, body):
            del method, headers, body
            calls.append(url)
            now[0] += 20
            if "access-point" in url:
                return 200, {"access_point": "https://ap.example"}, {}
            return 200, {"ok": True}, {}

        def sleep(seconds):
            slept.append(seconds)
            now[0] += int(round(seconds * 1000))

        client = ExnessClient(armed(), caller=caller, sign=lambda data: b"sig", now_ms=lambda: now[0], sleep=sleep)
        self.assertEqual(client.discover(), "https://ap.example")
        for _ in range(8):
            self.assertEqual(client.get_json("/v1/configuration/accounts/123456/account"), {"ok": True})
        self.assertEqual(len(calls), 9)
        self.assertTrue(slept)
        self.assertTrue(all(0 < delay <= 0.1 for delay in slept))
        signed = client.signed_headers("GET", "/v1/server-events/accounts/123456/ws/ticks")
        self.assertEqual(signed["EXN-IDEMPOTENCY-KEY"], "")
        self.assertIn("EXN-SIGN", signed)

        blocked = [1_000_000]
        seen = []

        def limited(method, url, headers, body):
            del method, headers, body
            seen.append(url)
            blocked[0] += 150
            if "access-point" in url:
                return 200, {"access_point": "https://ap.example"}, {}
            return 429, {"error_message": "REQUEST_RATE_LIMIT"}, {"Retry-After": "5"}

        def no_sleep(seconds):
            raise AssertionError(seconds)

        slow = ExnessClient(armed(), caller=limited, sign=lambda data: b"sig", now_ms=lambda: blocked[0], sleep=no_sleep)
        self.assertEqual(slow.discover(), "https://ap.example")
        blocked[0] += 150
        with self.assertRaises(RuntimeError):
            slow.get_json("/v1/configuration/accounts/123456/account")
        with self.assertRaises(RuntimeError):
            slow.get_json("/v1/configuration/accounts/123456/account")
        self.assertEqual(len(seen), 2)

    def test_one_cached_snapshot_still_rereads_before_an_order(self):
        fake = terminal()
        adapter, _fake = broker(fake)
        calls = []
        original = fake.read_state

        def counted():
            calls.append(1)
            return original()

        fake.read_state = counted
        adapter.cache_account_reads(True)
        adapter.note_link()
        adapter.evaluate_brakes(NOW)
        adapter.open_trades()
        adapter.pending_orders()
        self.assertEqual(len(calls), 1)
        adapter._read(fresh=True)
        adapter._read(fresh=True)
        self.assertEqual(len(calls), 3)
        adapter.cache_account_reads(False)
        adapter.note_link()
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()
