"""V2 Phase 8 / P3 G14 oracle (replay/rex_oracle.py): independence, differential equivalence with the runtime rules
(O-5 evidence requirement), reproduction of real REX runs and adversarial mutations (fail closed).

The TESTS may import runtime modules to compare; the oracle itself never does (checked by AST).
"""
import ast
import copy
from dataclasses import replace
from datetime import timedelta
import itertools
import json
from pathlib import Path
import random
import tempfile
import unittest

from ai.contracts import AIResponse
from ai.orchestrator import _final_status
from core.contracts import InstrumentSpec, RiskDecision, TradePlan
from execution.contracts import PaperAccount, PaperOrder, PaperPosition
from execution.paper_broker import PaperBroker
from execution.pending_order_gate import CurrentCycleGate
from execution.trade_manager import TradeManager
from replay import rex_oracle
from replay.rex_oracle import (PaperState, ai_final_status, check_run, fill_gate, gate_passed, manage_bar,
                               paper_policy, quantity_adapter, risk_v1, submit_plan)
from riesgo import crear_configuracion_riesgo_v2, evaluar_trade_plan
from runtime.gates import paper_policy as runtime_paper_policy
from runtime.paper_contracts import apply_paper_quantity_increment
from runtime.rex import REX_RUN, REX_WRITE
from storage.codec import paper_encode
from test_demo_runner import T
from test_phase8_rex_writer import catch_up_scenario, plan_scenario, rex_rows

ROOT = Path(__file__).resolve().parent
RNG = random.Random(20261009)


def state_of(account, orders=(), fills=()):
    return [paper_encode(account), sorted(paper_encode(p) for p in account.open_positions.values()),
            sorted(paper_encode(t) for t in account.closed_trades), sorted(paper_encode(o) for o in orders),
            sorted(paper_encode(f) for f in fills)]


def decision_dict(decision):
    return {k: getattr(decision, k) for k in ("status", "symbol", "side", "quantity", "capital_at_risk", "entry",
                                               "stop", "target", "reason", "equity_at_decision", "risk_fraction",
                                               "contract_multiplier")}


def same(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return a.hex() == b.hex()
    return a == b and type(a) is type(b)


class IndependenceTests(unittest.TestCase):
    def test_oracle_imports_only_the_standard_library(self):
        tree = ast.parse((ROOT / "replay" / "rex_oracle.py").read_text(encoding="utf-8"))
        modules = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import)
                   for alias in node.names}
        modules |= {node.module.split(".")[0] for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module}
        self.assertLessEqual(modules, {"datetime", "decimal", "json", "math"})

    def test_runtime_never_imports_the_replay_package(self):
        for path in (ROOT / "runtime").glob("*.py"):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("from replay", text, path.name)
            self.assertNotIn("import replay", text, path.name)


class DifferentialTests(unittest.TestCase):
    """O-5: every reconstructed rule equals the runtime rule it transcribes, on edge and seeded random inputs."""

    def test_v1_sizing_and_risk_gates(self):
        config = crear_configuracion_riesgo_v2()
        instrument = InstrumentSpec("XAUUSD", "METAL", "XAU/USD", "UTC", .01, .01, 1.0, ("1h", "15m", "5m"))
        cases = []
        for _ in range(3000):
            entry = RNG.choice([100.0, 2650.37, 1.08731, RNG.uniform(0.5, 3000)])
            risk = RNG.choice([1.0, 0.0001, RNG.uniform(0.0001, 50)])
            side = RNG.choice(["LONG", "SHORT", "LONG", "SHORT", "long", "FLAT"])
            sign = 1 if side.upper() == "LONG" else -1
            stop = entry - sign * risk
            rr = RNG.choice([3.0, 2.999999, 3.0000000001, 4.0, RNG.uniform(1, 6)])
            target = entry + sign * risk * rr
            declared = RNG.choice([rr, rr * (1 + 1e-12), rr + 0.01])
            cases.append((TradePlan("1.0", "XAUUSD", side, RNG.choice(["5m", "5m", "15m", "1h", "4h"]), entry, stop,
                                    target, declared),
                          RNG.choice([10000.0, 9876.54321, RNG.uniform(1, 1e6), 0.0, -1.0, float("nan"), 1e-9])))
        cases.append((TradePlan("1.0", "XAUUSD", "LONG", "5m", 100.0, 101.0, 130.0, 3.0), 10000.0))  # bad order
        reasons = set()
        for plan, equity in cases:
            runtime = evaluar_trade_plan(plan, equity, instrument, config)
            plan_dict = {k: getattr(plan, k) for k in ("symbol", "side", "timeframe", "entry", "stop", "target",
                                                      "risk_reward")}
            oracle, _ = risk_v1(plan_dict, equity, {"contract_multiplier": instrument.contract_multiplier}, config)
            reasons.add(runtime.reason)
            for key, value in oracle.items():
                self.assertTrue(same(getattr(runtime, key), value) or (value != value and getattr(runtime, key) != getattr(runtime, key)),
                                f"{key}: {getattr(runtime, key)!r} vs {value!r} for {plan} / {equity}")
        self.assertLessEqual({"approved", "rr_below_minimum", "rr_declared_mismatch", "invalid_numeric_value",
                              "invalid_direction_or_timeframe", "invalid_level_order"}, reasons)

    def test_quantity_adapter_including_rounding_edges(self):
        from core.contracts import FloorRunReport
        steps = [0.01, 0.1, 1.0, 0.001, 0.0, -1.0, float("nan"), True, None, 0.3]
        quantities = [0.30000000000000004, 0.3, 0.29999999999999999, 1.0, 0.009999, 12.3456789, 1e-12, 5.0,
                      2.675, 0.0, -1.0, float("inf")]
        for step, quantity in itertools.product(steps, quantities):
            decision = RiskDecision("1.0", "APPROVED", "XAUUSD", "LONG", quantity, 100.0 if quantity == quantity else 0.0,
                                    100.0, 99.0, 103.0, "approved", warnings=("w",))
            report = FloorRunReport("1.0", "r", T, "XAUUSD", {}, {}, None, None, None, decision, "PLAN_READY", ())
            instrument = InstrumentSpec("XAUUSD", "M", None, "UTC", .01, step, 1.0, ("5m",))
            label, status, out = quantity_adapter("PLAN_READY", {**decision_dict(decision), "warnings": ["w"]}, step)
            try:
                after = apply_paper_quantity_increment(report, instrument)
            except ValueError:
                self.assertIn(label, rex_oracle.ADAPTER_ERRORS, (step, quantity))
                continue
            self.assertNotIn(label, rex_oracle.ADAPTER_ERRORS, (step, quantity))
            self.assertEqual(status, after.final_status)
            for key in ("status", "quantity", "capital_at_risk", "reason"):
                self.assertTrue(same(getattr(after.risk_decision, key), out[key]), (step, quantity, key))
            self.assertEqual(list(after.risk_decision.warnings), out["warnings"])

    def test_fill_gate_every_clause(self):
        base = dict(schema_version="1.0", order_id="o", run_id="r", symbol="XAUUSD", side="LONG", quantity=10.0,
                    planned_entry=100.0, stop=99.0, target=103.0, contract_multiplier=1.0,
                    equity_at_submission=10000.0, cost_rate=0.0, as_of=T)
        variants = [{}, {"side": "SHORT", "stop": 101.0, "target": 97.0}, {"contract_multiplier": 0.0},
                    {"equity_at_submission": None}, {"equity_at_submission": 0.0}, {"quantity": 500.0},
                    {"target": 102.0}, {"stop": 100.5}, {"equity_at_submission": 100.0}]
        bars = [dict(symbol="XAUUSD", timestamp=T + timedelta(minutes=5), open=100.0, high=101.0, low=99.5,
                     close=100.5, is_closed=True),
                dict(symbol="XAUUSD", timestamp=T, open=100.0, high=101.0, low=99.0, close=100.0, is_closed=True),
                dict(symbol="EURUSD", timestamp=T + timedelta(minutes=5), open=1.0, high=1.0, low=1.0, close=1.0,
                     is_closed=True),
                dict(symbol="XAUUSD", timestamp=T + timedelta(minutes=5), open=99.2, high=101.0, low=99.0,
                     close=100.0, is_closed=True),
                dict(symbol="XAUUSD", timestamp=T + timedelta(minutes=5), open=100.0, high=99.0, low=99.5,
                     close=100.0, is_closed=True),
                dict(symbol="XAUUSD", timestamp=T + timedelta(minutes=5), open=100.0, high=101.0, low=99.0,
                     close=100.0, is_closed=False)]
        for equity in (10000.0, 50.0, 0.0):
            for variant, bar in itertools.product(variants, bars):
                order = PaperOrder(**{**base, **variant})
                account = PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0)
                account.equity = equity if equity else 10000.0
                if equity == 0.0:
                    account.equity = 1e-300
                broker = PaperBroker(account)
                broker.orders[order.order_id] = order
                state = PaperState(state_of(account, [order]))
                position = broker.process_next_bar(order, dict(bar))
                expected_events = [e.event_type for e in broker.journal]
                events, fills, positions, _ = fill_gate(state, state.orders["o"], dict(bar))
                self.assertEqual(events, expected_events, (variant, bar))
                self.assertEqual(state.orders["o"]["status"], order.status, (variant, bar))
                if position is not None:
                    got = json.loads(paper_encode(position))
                    self.assertEqual({k: v for k, v in positions[0].items() if k != "position_id"},
                                     {k: v for k, v in got.items() if k != "position_id"})

    def test_trade_manager_management(self):
        for side, (open_, high, low, close) in itertools.product(
                ("LONG", "SHORT"), ((100, 101, 99, 100.5), (94, 96, 93, 95), (106, 107, 104, 105),
                                    (100, 106, 99, 104), (100, 101, 94, 96), (100, 106, 94, 100))):
            stop, target = (95.0, 105.0) if side == "LONG" else (105.0, 95.0)
            positions = [PaperPosition("1.0", "p1", "o1", "r1", "XAUUSD", side, 3.0, 100.0, 100.0, stop, target, T,
                                       last_price=100.0, contract_multiplier=1.0, cost_rate=0.1),
                         PaperPosition("1.0", "p2", "o2", "r2", "EURUSD", "LONG", 1000.0, 1.1, 1.1, 1.0, 1.4, T,
                                       last_price=1.13, contract_multiplier=1.0)]
            account = PaperAccount("1.0", "a", 10000.0, 10000.0, 10000.0, realized_pnl=12.5)
            for order in (["XAUUSD", "EURUSD"], ["EURUSD", "XAUUSD"]):
                account.open_positions = {p.symbol: copy.deepcopy(p) for p in sorted(positions,
                                                                                      key=lambda p: order.index(p.symbol))}
                account.closed_trades = []
                state = PaperState(state_of(account))
                bar = {"symbol": "XAUUSD", "timestamp": T + timedelta(minutes=5), "open": float(open_),
                       "high": float(high), "low": float(low), "close": float(close), "is_closed": True}
                broker = PaperBroker(account)
                acct = copy.deepcopy(account)
                broker.account = acct
                TradeManager(acct, broker).process_bar(dict(bar))
                events, closed = manage_bar(state, dict(bar), order)
                self.assertEqual(events, [e.event_type for e in broker.journal])
                self.assertEqual(state.account["equity"].hex(), acct.equity.hex())
                self.assertEqual(state.account["realized_pnl"].hex(), acct.realized_pnl.hex())
                self.assertEqual(len(closed), len(acct.closed_trades))

    def test_ai_status_policy_and_gate(self):
        def response(name, status, recommendation):
            return AIResponse("1.0", "r", T, "XAUUSD", name, status, recommendation=recommendation)
        recs = [None, "AGREE", "DISAGREE", "REJECT_RECOMMENDATION", "CAUTION"]
        statuses = ["OK", "PARTIAL", "ERROR", "NO_DATA"]
        from core.contracts import FloorRunReport
        decision = RiskDecision("1.0", "APPROVED", "XAUUSD", "LONG", 1.0, 1.0, 100.0, 99.0, 103.0, "approved")
        plan = TradePlan("1.0", "XAUUSD", "LONG", "5m", 100.0, 99.0, 103.0, 3.0)
        for floor_status, warnings in itertools.product(
                ["PLAN_READY", "NO_SETUP", "PLAN_UNAVAILABLE", "RISK_REJECTED", "WATCH"], [(), ("equity_invalid",)]):
            for s_rec, t_rec, present in itertools.product(recs, recs, (True, False)):
                setup = response("setup_reviewer_ai", "OK", s_rec)
                trade = response("trade_reviewer_ai", "OK", t_rec) if present else None
                report = FloorRunReport("1.0", "r", T, "XAUUSD", {}, {}, None, None, plan, decision, floor_status,
                                        warnings)
                self.assertEqual(_final_status(report, setup, trade),
                                 ai_final_status(floor_status, list(warnings),
                                                 {"recommendation": s_rec},
                                                 {"recommendation": t_rec} if present else None))
        from ai.contracts import AIFloorReport
        for combo in itertools.product(statuses + [None], repeat=2):
            responses = [None if s is None else response("x", s, None) for s in (combo[0], "OK", "OK", combo[1], "OK")]
            for final in ("PLAN_READY", "AI_CAUTION"):
                report = AIFloorReport("1.0", "r", T, "XAUUSD", {}, *responses[:4], plan, responses[4], decision, final,
                                       (), {}, {})
                self.assertEqual(runtime_paper_policy(report), paper_policy(
                    final, decision_dict(decision), True,
                    [None if r is None else {"status": r.status} for r in responses]))
        for fields in itertools.product((True, False), (True, False), ("PLAN_READY", "AI_CAUTION", "", None)):
            gate = CurrentCycleGate("r", "XAUUSD", {}, fields[0], True, True, fields[1], fields[2])
            self.assertEqual(gate.passed(), gate_passed({"paper_enabled": fields[0], "execution_fresh": True,
                                                         "session_open": True, "ai_healthy": fields[1],
                                                         "ai_final_status": fields[2]}))

    def test_submit_plan_refusals(self):
        from core.contracts import FloorRunReport
        instrument = InstrumentSpec("XAUUSD", "METAL", "XAU/USD", "UTC", .01, .01, 1.0, ("5m",))
        decision = RiskDecision("1.0", "APPROVED", "XAUUSD", "LONG", 2.0, 2.0, 100.0, 99.0, 103.0, "approved")
        plan = TradePlan("1.0", "XAUUSD", "LONG", "5m", 100.0, 99.0, 103.0, 3.0, run_id="r", as_of=T)
        for change in ({}, {"final": "AI_CAUTION"}, {"plan": replace(plan, run_id="x")},
                       {"plan": replace(plan, as_of=None)}, {"decision": replace(decision, quantity=0.0)},
                       {"decision": replace(decision, symbol="EURUSD")}, {"open": True}, {"existing": True},
                       {"equity": 0.0}):
            account = PaperAccount("1.0", "a", 10000.0, 10000.0, change.get("equity", 10000.0) or 1.0)
            if change.get("equity") == 0.0:
                account.equity = float("nan") if False else -1.0
            broker = PaperBroker(account, instrument)
            if change.get("open"):
                account.open_positions["XAUUSD"] = PaperPosition("1.0", "p", "o", "q", "XAUUSD", "LONG", 1.0, 1.0,
                                                                 1.0, 0.5, 2.0, T)
            if change.get("existing"):
                broker.orders["old"] = PaperOrder("1.0", "old", "r", "XAUUSD", "LONG", 1.0, 1.0, 0.5, 2.0, 1.0, 1.0,
                                                  0.0, T)
            report = FloorRunReport("1.0", "r", T, "XAUUSD", {}, {}, None, None, change.get("plan", plan),
                                    change.get("decision", decision), change.get("final", "PLAN_READY"), ())
            order = broker.submit_plan(report, None, T)
            p = change.get("plan", plan)
            kind, value = submit_plan(account.equity, "XAUUSD" in account.open_positions,
                                      ["old"] if change.get("existing") else [], "r", "XAUUSD", T,
                                      change.get("final", "PLAN_READY"),
                                      {"run_id": p.run_id, "side": p.side, "as_of": p.as_of},
                                      decision_dict(change.get("decision", decision)),
                                      {"symbol": "XAUUSD", "contract_multiplier": 1.0})
            self.assertEqual(kind, "NONE" if order is None else "EXISTING" if change.get("existing") else "NEW", change)
            if kind == "NEW":
                got = json.loads(paper_encode(order))
                self.assertEqual({k: v for k, v in value.items() if k != "order_id"},
                                 {k: v for k, v in got.items() if k != "order_id"})


class ReproductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="v2-p3-oracle-")
        d = Path(cls.tmp.name)
        cls.plan_db = d / "plan.db"
        plan_scenario(cls.plan_db, steps=((0, 100.), (15, 100.), (30, 80.), (45, 100.), (60, 130.)))
        cls.cu_db = d / "cu.db"
        catch_up_scenario(cls.cu_db, d / "ev.db", symbols=("XAUUSD", "EURUSD"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    @staticmethod
    def runs(db):
        writes = {}
        for journal_id, run_id, _, record, _ in rex_rows(db, REX_WRITE):
            writes.setdefault(run_id, []).append(record)
        return [(record, writes.get(run_id, [])) for _, run_id, _, record, _ in rex_rows(db, REX_RUN)]

    def test_every_recorded_plan_run_reproduces(self):
        runs = self.runs(self.plan_db)
        self.assertEqual(len(runs), 5)
        checked = set()
        for record, writes in runs:
            verdict = check_run(record, writes)
            self.assertEqual(verdict["result"], "PASS", verdict["failures"])
            checked.update(verdict["checked"])
        self.assertLessEqual({"F1", "F1b", "ST4 V1 sizing (float.hex)", "V-P1/V-P2/V-P3", "A1-A4", "H1", "P1",
                              "ST7 legacy fill gate", "ST9 submit", "ST8 admission", "ST2 management"}, checked)

    def test_catch_up_runs_reproduce_and_unobserved_requests_fail_closed(self):
        verdicts = {}
        for record, writes in self.runs(self.cu_db):
            verdicts.setdefault(record["symbol"], []).append(check_run(record, writes))
        self.assertTrue(all(v["result"] == "PASS" for v in verdicts["XAUUSD"]), verdicts["XAUUSD"])
        # EURUSD's macro agent had no evidence: its request never reached a provider, so V-P1's request identity is
        # not observed -> INCOMPLETE -> FAIL (never approved by inference).
        for verdict in verdicts["EURUSD"]:
            self.assertEqual(verdict["result"], "FAIL")
            self.assertEqual(verdict["failures"][0]["code"], "REQUEST_IDENTITY_NOT_OBSERVED")

    def test_reconstructed_values_are_labelled(self):
        record, writes = next((r, w) for r, w in self.runs(self.plan_db) if any(
            x["context"]["stage"] == "ST7" for x in w))
        verdict = check_run(record, writes)
        stages = {item["stage"] for item in verdict["reconstructed"]}
        self.assertLessEqual({"ST4", "F1B", "ST7"}, stages)
        self.assertNotIn("sizing_intermediates", json.dumps([s for s in record["stages"]]))


class MutationTests(ReproductionTests):
    """Adversarial edits of real REX records: every one must FAIL (fail closed)."""

    def mutate(self, edit, pick=lambda record, writes: any(w["context"]["stage"] == "ST9" for w in writes)):
        record, writes = next((copy.deepcopy(r), copy.deepcopy(w)) for r, w in self.runs(self.plan_db)
                              if pick(r, w))
        self.assertEqual(check_run(record, writes)["result"], "PASS")
        edit(record, writes)
        return check_run(record, writes)

    @staticmethod
    def stage(record, name):
        return next(s for s in record["stages"] if s["stage"] == name and "observer" not in s)

    def assert_fails(self, edit, code=None, **kwargs):
        verdict = self.mutate(edit, **kwargs)
        self.assertEqual(verdict["result"], "FAIL")
        if code:
            self.assertIn(code, [f["code"] for f in verdict["failures"]], verdict["failures"])
        return verdict

    def test_ng10_ai_response_status_altered(self):
        def edit(record, writes):
            st5 = self.stage(record, "ST5")
            st5["responses"]["ai_trade_review"]["status"] = "ERROR"
            st5["audit"][-1]["response"]["status"] = "ERROR"
        self.assert_fails(edit)

    def test_ng11_equity_at_submission_altered(self):
        def edit(record, writes):
            write = next(w for w in writes if w["context"]["stage"] == "ST7")
            payloads = write["pre_state"][3]
            order = json.loads(payloads[0])
            order["equity_at_submission"] = 50.0
            payloads[0] = json.dumps(order, separators=(",", ":"))
        self.assert_fails(edit, "TRANSITION_MISMATCH",
                          pick=lambda r, w: any(x["context"]["stage"] == "ST7" and x["journal_events"] for x in w))

    def test_invalid_recommendation_and_missing_warnings(self):
        def bad_recommendation(record, writes):
            self.stage(record, "ST5")["responses"]["ai_setup_review"]["recommendation"] = "BUY_NOW"
        self.assert_fails(bad_recommendation, "RESPONSE_RECOMMENDATION_INVALID")

        def no_warnings(record, writes):
            del self.stage(record, "ST5")["responses"]["ai_structure"]["warnings"]
        self.assert_fails(no_warnings, "RESPONSE_FIELDS_MISSING")

    def test_vp1_identity_and_reuse_from_another_run(self):
        def wrong_run(record, writes):
            self.stage(record, "ST5")["responses"]["ai_macro"]["run_id"] = "another-run"
        self.assert_fails(wrong_run, "VP1_IDENTITY_MISMATCH")
        other = next(r for r, w in self.runs(self.plan_db) if not any(x["context"]["stage"] == "ST9" for x in w))

        def reused(record, writes):
            st5 = self.stage(record, "ST5")
            borrowed = self.stage(copy.deepcopy(other), "ST5")
            st5["responses"]["ai_structure"] = borrowed["responses"]["ai_structure"]
        self.assert_fails(reused, "VP1_IDENTITY_MISMATCH")

    def test_vp2_incoherent_substitution(self):
        def edit(record, writes):
            st5 = self.stage(record, "ST5")
            entry = st5["audit"][0]
            entry["validation"] = "provider_exception"  # substituted, yet status OK with a recommendation
        self.assert_fails(edit, "VP2_SUBSTITUTION_INCOHERENT")

    def test_sizing_one_ulp_and_stored_status_alone(self):
        def ulp(record, writes):
            decision = self.stage(record, "ST4")["risk_decision"]
            value = float.fromhex(decision["quantity"]["$f"])
            decision["quantity"] = {"$f": (value + value * 2 ** -52).hex()}
        self.assert_fails(ulp, "ST4_SIZING_MISMATCH")

        def status_only(record, writes):
            self.stage(record, "ST5")["final_status"] = "AI_CAUTION"
        self.assert_fails(status_only, "A1_A4_MISMATCH")

    def test_f1b_tampered(self):
        def edit(record, writes):
            self.stage(record, "F1B")["final_status"] = "RISK_REJECTED"
        self.assert_fails(edit, "F1B_STATUS_MISMATCH")

    def test_missing_stage_and_unexplained_write(self):
        def drop(record, writes):
            record["stages"] = [s for s in record["stages"] if s["stage"] != "ST8"]
        self.assert_fails(drop, "STAGE_MISSING")

        def extra(record, writes):
            clone = copy.deepcopy(writes[-1])
            clone["write_seq"] += 100
            clone["context"]["stage"] = "ST3"
            writes.append(clone)
            record["writes"].append({"write_seq": clone["write_seq"], "result": clone["result"]})
        self.assert_fails(extra, "UNEXPLAINED_WRITE")

    def test_submitted_order_tampered(self):
        def edit(record, writes):
            write = next(w for w in writes if w["context"]["stage"] == "ST9")
            payloads = write["post_state"][3]
            for i, payload in enumerate(payloads):
                order = json.loads(payload)
                if order["status"] == "PENDING":
                    order["quantity"] = order["quantity"] * 2
                    payloads[i] = json.dumps(order, separators=(",", ":"))
        self.assert_fails(edit, "TRANSITION_MISMATCH")

    def test_returned_status_and_rule_constants(self):
        def returned(record, writes):
            record["returned"] = "NO_SETUP"
        self.assert_fails(returned, "RETURNED_MISMATCH")

        def constants(record, writes):
            record["identity"]["rule_identity"]["valid_recommendations"].append("BUY")
        self.assert_fails(constants, "RULE_CONSTANTS_MISMATCH")

    def test_incomplete_run_never_passes(self):
        def edit(record, writes):
            record["complete"] = False
        self.assert_fails(edit, "REX_INCOMPLETE")


if __name__ == "__main__":
    unittest.main()
