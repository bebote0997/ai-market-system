"""P6.1E: immutable run_id -> candidate identity (INV-6.1 .. INV-6.10), fingerprint + sequential collision matrix.
Real-process collisions: test_phase6_candidate_race.py."""
import json
import unittest
from dataclasses import replace
from datetime import timedelta
from fractions import Fraction

from core.risk_policy import RISK_POLICY_V2_P5
from execution.candidate_identity import (CLAIM_EVENT, FINGERPRINT_VERSION, candidate_fields, claim_run,
                                          claimed_fingerprint, fingerprint)
from execution.conflict_engine import CONFLICT_POLICY_VERSION
from execution.paper_broker import PaperBroker
from execution.conflict_path import EVENT, STALE_EVENT, candidate as path_candidate
from execution.risk_reservation import reserve_and_submit
from test_phase5_risk_engine import ACCOUNT, AT, EUR_SHORT, INS, XAU_LONG, account, position
from test_phase6_conflict_engine import XAU_SHORT, Case, candidate

XAU_LONG_SL = ("XAUUSD", "LONG", 2650.0, 2590.0)
VARIANTS = {"symbol": (EUR_SHORT, 0), "setup_id": (XAU_LONG, 1), "direction": (XAU_SHORT, 0),
            "entry": (("XAUUSD", "LONG", 2651.0, 2600.0), 0), "sl": (XAU_LONG_SL, 0),
            "multiple": (("XAUUSD", "SHORT", 2640.0, 2690.0), 3)}


def fields_of(report, policy=RISK_POLICY_V2_P5, instrument=None, account_id=ACCOUNT):
    return candidate_fields(report, instrument or INS[report.symbol], account_id=account_id,
                            setup_id=path_candidate(report)[0], policy=policy,
                            conflict_policy_version=CONFLICT_POLICY_VERSION)


class FingerprintTests(unittest.TestCase):
    def test_deterministic_canonical_and_versioned(self):
        base = candidate(XAU_LONG, "run-x")
        fields = fields_of(base)
        self.assertEqual(fields["fingerprint_version"], FINGERPRINT_VERSION)
        self.assertEqual(fingerprint(fields), fingerprint(fields_of(candidate(XAU_LONG, "run-other"))))  # run_id excluded
        ints = replace(base, trade_plan=replace(base.trade_plan, entry=2650, stop=2600, target=2800, risk_reward=3))
        self.assertEqual(fingerprint(fields_of(ints)), fingerprint(fields))  # 2650 == 2650.0
        self.assertEqual((fields["entry"], fields["stop"], fields["target"]), ("2650", "2600", "2800"))
        self.assertEqual(len(fingerprint(fields)), 64)

    def test_every_economic_difference_changes_the_fingerprint(self):
        base = fingerprint(fields_of(candidate(XAU_LONG, "run-x")))
        plan = candidate(XAU_LONG, "run-x")
        changed = {name: candidate(args, "run-x", anchor) for name, (args, anchor) in VARIANTS.items()}
        changed["tp"] = replace(plan, trade_plan=replace(plan.trade_plan, target=2801.0))
        changed["declared_rr"] = replace(plan, trade_plan=replace(plan.trade_plan, risk_reward=3.5))
        changed["as_of"] = replace(plan, trade_plan=replace(plan.trade_plan, as_of=AT + timedelta(minutes=5)))
        prints = {name: fingerprint(fields_of(report)) for name, report in changed.items()}
        prints["policy"] = fingerprint(fields_of(plan, replace(RISK_POLICY_V2_P5,
                                                               aggregate_portfolio_risk_fraction=Fraction(15, 1000))))
        prints["instrument"] = fingerprint(fields_of(plan, instrument=replace(INS["XAUUSD"], contract_multiplier=2.0)))
        prints["account"] = fingerprint(fields_of(plan, account_id="other-account"))
        for name, value in prints.items():
            self.assertNotEqual(value, base, name)
        self.assertEqual(len(set(prints.values())), len(prints))


class SequentialCollisionTests(Case):
    def claims(self, run_id):
        return [json.loads(r[0]) for r in self.store().db.execute(
            "SELECT payload FROM journal WHERE event_type=? AND run_id=?", (CLAIM_EVENT, run_id))]

    def invariants(self, run_id):
        """INV-6.1/6.6/6.7: one claim; every decisive row and order of the run matches it."""
        claims = self.claims(run_id)
        self.assertEqual(len(claims), 1)
        claimed = claims[0]
        self.assertEqual(claimed["economic_action"], "NONE")
        decisive = [r for r in self.rows() if r["run_id"] == run_id and r["decision"] in ("RESERVED", "BLOCKED",
                                                                                         "RISK_REJECTED")]
        self.assertLessEqual(len(decisive), 1)
        for row in decisive:
            self.assertEqual(row["candidate_fingerprint"], claimed["fingerprint"])
        orders = [o for o in self.store().load_paper(ACCOUNT)[1].values() if o.run_id == run_id]
        self.assertLessEqual(len(orders), 1)
        for order in orders:
            c = claimed["candidate"]
            self.assertEqual((order.symbol, order.side, order.setup_id), (c["symbol"], c["side"], c["setup_id"]))
        return decisive, orders

    def collide(self, first_outcome):
        for name, (args, anchor) in VARIANTS.items():
            with self.subTest(variant=name):
                other = self.submit(candidate(args, "run-x", anchor))
                self.assertEqual((other.status, other.reason), ("NOT_SUBMITTED", "RUN_ID_CANDIDATE_MISMATCH"))
        decisive, orders = self.invariants("run-x")
        self.assertEqual([d["decision"] for d in decisive], [first_outcome])
        again = self.submit(candidate(XAU_LONG, "run-x"))  # INV-6.3 identical candidate converges
        self.assertEqual(again.status, "DUPLICATE_RUN")
        self.assertEqual(self.invariants("run-x"), (decisive, orders))
        return orders

    def test_blocked_then_different_candidates(self):
        self.seed(replace(position("XAUUSD", "SHORT", 2650.0, 2700.0, 2500.0, 1.0), setup_id="S-OLD"))
        self.assertEqual(self.submit(candidate(XAU_LONG, "run-x")).status, "BLOCKED")
        self.assertEqual(self.collide("BLOCKED"), [])

    def test_risk_rejected_then_different_candidates(self):
        self.store().save_paper(PaperBroker(account(9500.0, realized=-500.0)))
        self.assertEqual(self.submit(candidate(XAU_LONG, "run-x")).status, "RISK_REJECTED")
        self.assertEqual(self.collide("RISK_REJECTED"), [])

    def test_reserved_then_different_candidates(self):
        first = self.submit(candidate(XAU_LONG, "run-x"))
        self.assertEqual(first.status, "RESERVED")
        self.assertEqual([o.order_id for o in self.collide("RESERVED")], [first.order.order_id])

    def test_claim_only_crash_is_recoverable_and_still_binding(self):
        report = candidate(XAU_LONG, "run-x")
        self.assertEqual(claim_run(self.store(), run_id="run-x", symbol="XAUUSD", fields=fields_of(report), as_of=AT,
                                   has_history=lambda s: False), "CLAIMED")  # process "dies" here
        self.assertEqual((self.rows(), self.rows("RISK_V2_DECISION"), self.store().load_paper(ACCOUNT)[1]),
                         ([], [], {}))  # INV-6.4: a claim alone is not economic approval
        other = self.submit(candidate(XAU_SHORT, "run-x"))
        self.assertEqual(other.reason, "RUN_ID_CANDIDATE_MISMATCH")  # still bound after the crash
        resumed = self.submit(report)  # INV-6.5: same candidate resumes normally (Risk V2 still decides: INV-6.10)
        self.assertEqual((resumed.status, resumed.record["risk_status"]), ("RESERVED", "APPROVED"))
        self.invariants("run-x")

    def test_policy_mismatch_on_same_run(self):
        self.assertEqual(self.submit(candidate(XAU_LONG, "run-x")).status, "RESERVED")
        tight = replace(RISK_POLICY_V2_P5, aggregate_portfolio_risk_fraction=Fraction(15, 1000))
        self.assertEqual(self.submit(candidate(XAU_LONG, "run-x"), policy=tight).reason, "RUN_ID_CANDIDATE_MISMATCH")

    def test_stale_observation_is_not_an_identity_barrier(self):
        store = self.store()
        store.event(AT, "run-x", "XAUUSD", "conflict_engine", STALE_EVENT, "WARNING",
                    {"run_id": "run-x", "observation": "STALE_STATE", "decisive": False, "economic_action": "NONE"})
        self.assertEqual(self.submit(candidate(XAU_LONG, "run-x")).status, "RESERVED")
        self.invariants("run-x")

    def test_legacy_history_without_claim_fails_closed(self):
        store = self.store()  # a P6.1/P6.1D decisive row written before claims existed
        store.event(AT, "run-x", "XAUUSD", "conflict_engine", EVENT, "INFO",
                    {"run_id": "run-x", "decision": "BLOCKED", "symbol": "XAUUSD"})
        result = self.submit(candidate(XAU_LONG, "run-x"))
        self.assertEqual((result.status, result.reason), ("NOT_SUBMITTED", "RUN_ID_IDENTITY_UNKNOWN"))
        self.assertEqual(self.claims("run-x"), [])  # no identity invented
        self.setUp()  # an order written by the identity-unaware Phase 5 API with the same run_id
        self.assertEqual(reserve_and_submit(self.store(), candidate(EUR_SHORT, "run-y"), INS["EURUSD"],
                                            account_id=ACCOUNT, as_of=AT).status, "RESERVED")
        result = self.submit(candidate(XAU_LONG, "run-y"))
        self.assertEqual((result.status, result.reason), ("NOT_SUBMITTED", "RUN_ID_IDENTITY_UNKNOWN"))
        self.assertEqual(len(self.store().load_paper(ACCOUNT)[1]), 1)

    def test_foreign_order_after_claim_is_never_adopted(self):
        report = candidate(XAU_LONG, "run-x")
        claim_run(self.store(), run_id="run-x", symbol="XAUUSD", fields=fields_of(report), as_of=AT,
                  has_history=lambda s: False)
        # The identity-unaware Phase 5 API reserves a DIFFERENT candidate under the claimed run_id.
        self.assertEqual(reserve_and_submit(self.store(), candidate(EUR_SHORT, "run-x"), INS["EURUSD"],
                                            account_id=ACCOUNT, as_of=AT).status, "RESERVED")
        result = self.submit(report)  # INV-6.7: not reconciled to the foreign order
        self.assertEqual((result.status, result.reason, result.order), ("NOT_SUBMITTED", "RUN_ID_CANDIDATE_MISMATCH",
                                                                         None))
        self.assertEqual(claimed_fingerprint(self.store(), "run-x"), fingerprint(fields_of(report)))


if __name__ == "__main__":
    unittest.main()
