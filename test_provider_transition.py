"""Provider transition: one explicit field, verified flat book, no silent resume, history kept."""
import inspect
import sqlite3
import threading
import time
import unittest
from contextlib import closing
from unittest.mock import patch
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from core import fx_policy
from execution.broker_demo_state import DemoStateStore
from execution.contracts import PaperOrder
from execution.eurusd_pullback import LIMIT_OFFSET_PIPS
from execution.operator_actions import consume_provider_transition, request_provider_transition
from execution.paper_broker import PaperBroker
from runtime.cloud import cloud_preflight
from runtime.config import RuntimeConfig
from runtime.provider_transition import load_transitions, record_provider_transition, transition_resumes
from runtime.service import OperationalRuntime
from storage.database import Store
from test_cloud_resume import FREEZE, ExistingExperimentPreflight
from ui.auth import AUTHENTICATED, EXPIRES_AT


WHEN = datetime(2026, 11, 7, 18, 0, tzinfo=timezone.utc)
FLAT = {"paper_open": 0, "paper_pending": 0, "broker_live": 0}


def _payload(mode):
    config = replace(RuntimeConfig(ai_provider_mode=mode), scheduler_enabled=True)
    return config.fingerprint_content()


def _record(db, origin, dest, **overrides):
    fields = dict(from_payload=origin, to_payload=dest, flat_counts=dict(FLAT), operator_note="reviewed",
                  git_commit=FREEZE, starting_equity=10000.0, actor="owner", recorded_at=WHEN)
    fields.update(overrides)
    return record_provider_transition(db, **fields)


def _broker(path, status=None):
    DemoStateStore(path).flag("heartbeat")  # creates the file and the trade table
    if status is None:
        return
    with closing(sqlite3.connect(path)) as con:
        con.execute(
            "INSERT INTO broker_demo_trades(client_id, run_id, symbol, side, units, entry, stop, target, "
            "status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("aif-1", "run", "EURUSD", "LONG", 1, "1", "1", "1", status, "t", "t"))
        con.commit()


class TransitionRules(unittest.TestCase):
    def setUp(self):
        self.path = Path("data/runtime") / f"transition-{uuid4().hex}.db"
        self.addCleanup(self.path.unlink, missing_ok=True)
        self.db = sqlite3.connect(self.path, timeout=5)
        self.addCleanup(self.db.close)
        self.origin = _payload("openai")
        self.dest = _payload("xai")

    def test_only_the_provider_field_can_be_recorded_and_only_when_flat(self):
        recorded = _record(self.db, self.origin, self.dest, operator_note="reviewed openai to xai")
        self.assertEqual(recorded["status"], "recorded")
        self.assertEqual(len(load_transitions(self.db)), 1)
        again = _record(self.db, self.origin, self.dest, operator_note="reviewed openai to xai")
        self.assertEqual(again["status"], "duplicate")
        self.assertEqual(len(load_transitions(self.db)), 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE provider_transitions SET operator_note = 'changed'")
        with self.assertRaises(ValueError):
            _record(self.db, self.origin, self.dest, flat_counts={"paper_open": 0, "paper_pending": 1, "broker_live": 0},
                    operator_note="not flat")
        wider = dict(self.dest)
        wider["sessions"] = ["LONDON"]
        with self.assertRaises(ValueError):
            _record(self.db, self.origin, wider, operator_note="too many fields")
        with self.assertRaises(ValueError):
            _record(self.db, self.origin, _payload("deterministic"), operator_note="does not link")
        self.assertEqual(len(load_transitions(self.db)), 1)

    def test_concurrent_appends_cannot_fork_the_chain(self):
        errors = []

        def worker(dest):
            con = sqlite3.connect(self.path, timeout=5)
            try:
                _record(con, self.origin, dest, operator_note="race")
            except ValueError:
                errors.append("chain")
            finally:
                con.close()

        threads = [threading.Thread(target=worker, args=(self.dest,)),
                   threading.Thread(target=worker, args=(_payload("deterministic"),))]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        self.assertEqual(len(load_transitions(self.db)), 1)
        self.assertEqual(errors, ["chain"])

    def test_a_recorded_step_still_resumes_before_the_provider_changes(self):
        origin_fp = replace(RuntimeConfig(ai_provider_mode="openai"), scheduler_enabled=True).fingerprint()
        dest_fp = replace(RuntimeConfig(ai_provider_mode="xai"), scheduler_enabled=True).fingerprint()
        rows = [(FREEZE, origin_fp, 10000.0)]
        self.assertFalse(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.dest, starting_equity=10000.0, records=()))
        _record(self.db, self.origin, self.dest)
        records = load_transitions(self.db)
        self.assertTrue(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.origin, starting_equity=10000.0, records=records))
        self.assertTrue(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.dest, starting_equity=10000.0, records=records))
        self.assertFalse(transition_resumes(
            rows + [(FREEZE, "f" * 64, 10000.0)], freeze=FREEZE, freeze_ok=True, started=True,
            account_present=True, current_payload=self.dest, starting_equity=10000.0, records=records))
        self.assertFalse(transition_resumes(
            [(FREEZE, dest_fp, 10000.0)], freeze=FREEZE, freeze_ok=True, started=True,
            account_present=True, current_payload=self.dest, starting_equity=10000.0, records=records))
        tampered = dict(records[0])
        tampered["paper_pending"] = 1
        self.assertFalse(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.dest, starting_equity=10000.0, records=(tampered,)))
        self.assertNotEqual(origin_fp, dest_fp)

    def test_a_reverse_step_is_a_second_explicit_record(self):
        _record(self.db, self.origin, self.dest, operator_note="forward")
        _record(self.db, self.dest, self.origin, operator_note="reverse")
        origin_fp = replace(RuntimeConfig(ai_provider_mode="openai"), scheduler_enabled=True).fingerprint()
        rows = [(FREEZE, origin_fp, 10000.0)]
        records = load_transitions(self.db)
        self.assertTrue(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.dest, starting_equity=10000.0, records=records))
        self.assertTrue(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.origin, starting_equity=10000.0, records=records))
        broken = dict(records[1])
        broken["to_payload"] = dict(broken["to_payload"], macro_provider_mode="finnhub")
        self.assertFalse(transition_resumes(
            rows, freeze=FREEZE, freeze_ok=True, started=True, account_present=True,
            current_payload=self.origin, starting_equity=10000.0, records=(records[0], broken)))

    def test_pullback_contract_is_unchanged(self):
        self.assertEqual(LIMIT_OFFSET_PIPS, Decimal("12.5"))
        self.assertEqual(fx_policy.FX_SL_MIN_PIPS, Decimal(25))
        self.assertEqual(fx_policy.FX_SL_MAX_PIPS, Decimal(25))
        self.assertEqual(fx_policy.FX_TP_RR, Decimal(2))
        self.assertIn("consume_provider_transition",
                      inspect.getsource(OperationalRuntime.process_operator_requests))


class ResumeIntegration(ExistingExperimentPreflight):
    def test_provider_drift_stays_blocked_until_an_explicit_flat_record(self):
        drifted = replace(self.config, ai_provider_mode="xai")
        blocked = cloud_preflight(drifted, env={**self.env, "XAI_API_KEY": "present"}, disk_mounted=True)
        self.assertFalse(blocked.checks["experiment_resumable"])
        before = self.snapshot()
        with closing(Store(self.path)) as store:
            _record(store.db, replace(self.config, scheduler_enabled=True).fingerprint_content(),
                    replace(drifted, scheduler_enabled=True).fingerprint_content(),
                    operator_note="owner reviewed openai to xai")
        pending = cloud_preflight(self.config, env=self.env, disk_mounted=True)
        self.assertTrue(pending.checks["experiment_resumable"])
        resumed = cloud_preflight(drifted, env={**self.env, "XAI_API_KEY": "present"}, disk_mounted=True)
        self.assertTrue(resumed.checks["experiment_resumable"])
        with closing(Store(self.path, readonly=True)) as store:
            fingerprints = {row[0] for row in store.db.execute("SELECT config_fingerprint FROM run_metadata")}
        self.assertEqual(fingerprints, {replace(self.config, scheduler_enabled=True).fingerprint()})
        self.assertEqual([row for row in self.snapshot() if row.startswith("INSERT INTO \"run_metadata\"")],
                         [row for row in before if row.startswith("INSERT INTO \"run_metadata\"")])

    def _flat_books(self):
        broker = self.path.with_name(self.path.name + "-broker.db")
        self.addCleanup(broker.unlink, missing_ok=True)
        _broker(broker)
        with closing(Store(self.path)) as store:
            store.db.execute("DELETE FROM paper_positions")
            store.db.commit()
        return broker

    def test_the_operator_request_observes_positions_and_pending_orders(self):
        broker = self.path.with_name(self.path.name + "-broker.db")
        self.addCleanup(broker.unlink, missing_ok=True)
        _broker(broker)
        refused = request_provider_transition(
            "dashboard", "CAMBIAR A XAI", to_mode="xai", request_id="open-position", path=broker,
            trading_db=self.path)
        self.assertEqual(refused["reason"], "not_flat")
        self.assertGreater(refused["paper_open"], 0)
        self.assertIsNone(DemoStateStore(broker).flag("provider_transition_request"))
        self._flat_books()
        with closing(Store(self.path)) as store:
            account, _orders, _fills = store.load_paper("paper-main")
        # The helper above already deleted positions. A pending paper order still blocks.
        order = PaperOrder("1.0", "ord-1", "run", "EURUSD", "LONG", 1, 1.1, 1.0, 1.2, as_of=WHEN)
        holder = PaperBroker(account)
        holder.orders[order.order_id] = order
        with closing(Store(self.path)) as store:
            store.save_paper(holder)
            store.db.execute("DELETE FROM paper_positions")
            store.db.commit()
        pending = request_provider_transition(
            "dashboard", "CAMBIAR A XAI", to_mode="xai", request_id="pending-order", path=broker,
            trading_db=self.path)
        self.assertEqual(pending["reason"], "not_flat")
        self.assertEqual(pending["paper_pending"], 1)
        with closing(Store(self.path)) as store:
            store.db.execute("DELETE FROM paper_orders")
            store.db.commit()
        _broker(broker, "PENDING_LIMIT")
        resting = request_provider_transition(
            "dashboard", "CAMBIAR A XAI", to_mode="xai", request_id="resting-limit", path=broker,
            trading_db=self.path)
        self.assertEqual(resting["reason"], "not_flat")
        self.assertEqual(resting["broker_live"], 1)
        with closing(sqlite3.connect(broker)) as con:
            con.execute("DELETE FROM broker_demo_trades")
            con.commit()
        queued = request_provider_transition(
            "dashboard", "CAMBIAR A XAI", to_mode="xai", request_id="flat-now", path=broker,
            trading_db=self.path)
        self.assertEqual(queued["reason"], "requested")
        with closing(Store(self.path)) as store:
            applied = consume_provider_transition(
                store, self.config, env=self.env, broker_db_path=broker, recorded_at=WHEN, now=WHEN)
        self.assertEqual(applied["reason"], "recorded")
        self.assertIsNone(DemoStateStore(broker).flag("provider_transition_request"))
        self.assertTrue(cloud_preflight(self.config, env=self.env, disk_mounted=True).checks["experiment_resumable"])
        drifted = replace(self.config, ai_provider_mode="xai")
        self.assertTrue(cloud_preflight(
            drifted, env={**self.env, "XAI_API_KEY": "present"}, disk_mounted=True).checks["experiment_resumable"])
        with closing(Store(self.path)) as store:
            rows = load_transitions(store.db)
            journal = store.db.execute(
                "SELECT event_type, payload FROM journal WHERE event_type = 'PROVIDER_TRANSITION_RECORDED'").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["from_value"], "openai")
        self.assertEqual(rows[0]["to_value"], "xai")
        self.assertEqual(rows[0]["git_commit"], FREEZE)
        self.assertEqual(journal[0][0], "PROVIDER_TRANSITION_RECORDED")
        self.assertNotIn("sk-", journal[0][1])

    def test_a_queued_request_is_not_applied_when_the_book_is_not_flat(self):
        broker = self.path.with_name(self.path.name + "-broker.db")
        self.addCleanup(broker.unlink, missing_ok=True)
        _broker(broker, "UNKNOWN")
        DemoStateStore(broker).set_flag(
            "provider_transition_request",
            '{"actor":"dashboard","reason":"CAMBIAR A XAI","request_id":"forced","to_mode":"xai","ts":"t"}')
        with closing(Store(self.path)) as store:
            applied = consume_provider_transition(
                store, self.config, env=self.env, broker_db_path=broker, now=WHEN)
            self.assertEqual(applied["reason"], "not_flat")
            self.assertEqual(load_transitions(store.db), ())
        self.assertIsNone(DemoStateStore(broker).flag("provider_transition_request"))
        self.assertTrue(any(event["kind"] == "PROVIDER_TRANSITION_REFUSED"
                            for event in DemoStateStore(broker).events(10)))

    def test_the_request_cannot_smuggle_another_field(self):
        broker = self._flat_books()
        raw = ('{"actor":"dashboard","reason":"CAMBIAR A XAI","request_id":"smuggle","to_mode":"xai",'
               '"to_payload":{"ai_provider_mode":"xai","sessions":["LONDON"]},"ts":"t"}')
        DemoStateStore(broker).set_flag("provider_transition_request", raw)
        with closing(Store(self.path)) as store:
            applied = consume_provider_transition(
                store, self.config, env=self.env, broker_db_path=broker, recorded_at=WHEN, now=WHEN)
            row = load_transitions(store.db)[0]
        self.assertEqual(applied["reason"], "recorded")
        self.assertEqual(row["to_payload"]["sessions"], row["from_payload"]["sessions"])
        self.assertNotEqual(row["to_payload"]["sessions"], ["LONDON"])
        self.assertEqual(set(row["to_payload"]) ^ set(row["from_payload"]), set())

    def test_a_freeze_mismatch_does_not_write_and_stays_queued(self):
        broker = self._flat_books()
        DemoStateStore(broker).set_flag(
            "provider_transition_request",
            '{"actor":"dashboard","reason":"CAMBIAR A XAI","request_id":"freeze","to_mode":"xai","ts":"t"}')
        with closing(Store(self.path)) as store:
            applied = consume_provider_transition(
                store, self.config, env={**self.env, "AI_FLOOR_GIT_COMMIT": "a" * 40},
                broker_db_path=broker, now=WHEN)
            self.assertEqual(load_transitions(store.db), ())
        self.assertEqual(applied["reason"], "freeze_mismatch")
        self.assertIsNotNone(DemoStateStore(broker).flag("provider_transition_request"))

    def test_wrong_phrase_does_not_queue(self):
        from ui.views import control
        calls = []
        session = {AUTHENTICATED: True, EXPIRES_AT: time.monotonic() + 600}
        st = _Fake(session)
        api = {"transition": lambda *args, **kwargs: calls.append(kwargs) or {"ok": True, "reason": "requested"}}
        with patch.dict("os.environ", {"AI_FLOOR_DASHBOARD_PASSWORD": "fixture-password"}):
            control.render_provider_transition(st, api)
            st.click("control-provider-xai")
            control.render_provider_transition(st, api)
            st.inputs["control-provider-phrase"] = "cambiar"
            st.click("control-provider-confirm")
            control.render_provider_transition(st, api)
        self.assertEqual(calls, [])
        self.assertIn("La frase no coincide", st.content)


class _Fake:
    def __init__(self, session):
        self.session_state = session
        self.inputs = {}
        self.pending = set()
        self.markdowns = []

    def markdown(self, body, **_):
        self.markdowns.append(body)

    def button(self, _label, key=None, **_):
        return key in self.pending

    def text_input(self, _label, key=None, **_):
        return self.inputs.get(key, "")

    def click(self, key):
        self.pending.add(key)

    @property
    def content(self):
        return "\n".join(self.markdowns)


if __name__ == "__main__":
    unittest.main()
