"""V2 Phase 8 / P8.4 R4: REVISION visibility in the trading journal / health state and the pre-DEMO gate."""
from contextlib import redirect_stderr
from datetime import timedelta
import io
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

from replay.activation_preview import sha256
from runtime import revision_review
from runtime.revision_review import demo_gate, main, record_review
from storage.database import Store
from storage.evidence_store import EvidenceStore
from test_phase8_activation_simulation import S1, S2, bar
from test_phase8_catch_up_certification import Harness

S3 = S2 + timedelta(minutes=15)
BENIGN = {("XAUUSD", bar(2)): (100.0, 100.2, 99.8, 100.1)}      # differs from the committed flat bar, crosses nothing
MATERIAL = {("XAUUSD", bar(2)): (100.0, 100.5, 94.0, 96.0)}     # crosses SL 95 of the open LONG; committed did not
BENIGN_2 = {("XAUUSD", bar(2)): (100.0, 100.3, 99.7, 100.0)}    # a second, different revision of the same bar
MATERIAL_2 = {("XAUUSD", bar(2)): (100.0, 100.4, 93.0, 95.5)}   # a second, different material revision of that bar


class RevisionReviewTests(Harness):
    def rows(self, event=revision_review.EVENT):
        store = Store(self.db)
        try:
            return [json.loads(r[0]) for r in store.db.execute(
                "SELECT payload FROM journal WHERE event_type=? ORDER BY id", (event,))], store.get_state(
                "evidence_revisions")
        finally:
            store.close()

    def test_non_material_revision_is_visible_and_only_a_warning(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, BENIGN, flag=True)
        rows, summary = self.rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["bar_start"], rows[0]["material"], rows[0]["affected"]), (bar(2).isoformat(), False, []))
        self.assertEqual(json.loads(summary), {"recorded": 1, "unreviewed": 1, "material_unreviewed": 0,
                                               "escalated_unresolved": 0})  # P8.4G adds the escalated count
        gate = demo_gate(self.db, self.ev)
        self.assertEqual((gate["status"], len(gate["non_material_unreviewed"]), gate["unclassified"]), ("CLEAR", 1, []))
        self.assertIn("XAUUSD", self.paper()[0].open_positions)

    def test_material_revision_blocks_until_reviewed_and_never_changes_positions(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, MATERIAL, flag=True)
        rows, summary = self.rows()
        record = rows[0]
        self.assertEqual((record["material"], [a["level"] for a in record["affected"]]), (True, ["SL"]))
        self.assertEqual(record["committed"]["low"], 99.95)  # first committed bar kept
        self.assertEqual(json.loads(summary)["material_unreviewed"], 1)
        account = self.paper()[0]
        self.assertEqual((list(account.open_positions), account.closed_trades), (["XAUUSD"], []))
        self.assertEqual(demo_gate(self.db, self.ev)["status"], "BLOCKED")
        store = Store(self.db)
        try:
            with self.assertRaises(ValueError):
                record_review(store, record["review_key"], decision="IGNORE", reviewer="owner")
            with self.assertRaises(ValueError):
                record_review(store, "unknown", decision="ACCEPTED_FIRST_COMMITTED", reviewer="owner")
            record_review(store, record["review_key"], decision="ACCEPTED_FIRST_COMMITTED", reviewer="owner",
                          note="provider correction reviewed; committed bar stays authoritative")
        finally:
            store.close()
        gate = demo_gate(self.db, self.ev)
        self.assertEqual((gate["status"], gate["material_unreviewed"]), ("CLEAR", []))
        self.assertEqual(json.loads(self.rows()[1])["material_unreviewed"], 0)
        self.assertIn("XAUUSD", self.paper()[0].open_positions)  # reviews never move economics

    def test_repeated_presentation_and_restart_do_not_duplicate(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        for slot in (S2, S3):
            self.cycle(slot, MATERIAL, flag=True)
        self.assertEqual(self.cycle(S3, MATERIAL, flag=True), "DUPLICATE")
        self.assertEqual(len(self.rows()[0]), 1)

    def test_unclassified_anomaly_blocks_and_classification_failure_never_changes_the_cycle(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        with patch("runtime.revision_review.classify", side_effect=RuntimeError("bug")):
            status = self.cycle(S2, BENIGN, flag=True)
        self.assertNotEqual(status, "ERROR")  # observability failure is swallowed
        self.assertEqual(self.rows()[0], [])
        gate = demo_gate(self.db, self.ev)
        self.assertEqual((gate["status"], [u["bar_start"] for u in gate["unclassified"]]),
                         ("BLOCKED", [bar(2).isoformat()]))

    def test_gate_is_read_only_and_cli_exit_codes(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, MATERIAL, flag=True)
        before = {p: sha256(p) for p in (self.db, self.ev)}
        self.assertEqual(main(["gate", "--trading-db", str(self.db), "--evidence-db", str(self.ev)]), 3)
        self.assertEqual({p: sha256(p) for p in (self.db, self.ev)}, before)
        key = self.rows()[0][0]["review_key"]
        self.assertEqual(main(["review", "--trading-db", str(self.db), "--key", key, "--decision", "ESCALATED",
                               "--reviewer", "owner"]), 0)
        # P8.4G (DEC-8.12) supersedes the P8.4 expectation "ESCALATED -> gate exit 0": ESCALATED is not final.
        self.assertEqual(main(["gate", "--trading-db", str(self.db), "--evidence-db", str(self.ev)]), 3)
        self.assertEqual(main(["review", "--trading-db", str(self.db), "--key", key, "--decision",
                               "ACCEPTED_FIRST_COMMITTED", "--reviewer", "owner"]), 0)
        self.assertEqual(main(["gate", "--trading-db", str(self.db), "--evidence-db", str(self.ev)]), 0)

    def test_flag_off_never_records_revisions(self):
        self.seed()
        self.cycle(S1, {}, flag=False)
        self.cycle(S2, MATERIAL, flag=False)
        self.assertEqual(self.rows()[0], [])
        # P8.4F supersedes the P8.4 assertion ``demo_gate(self.db)["status"] == "CLEAR"``: without the evidence
        # database the gate cannot prove coverage, so it is BLOCKED (P8.5 HIGH, false CLEAR #1).
        gate = demo_gate(self.db, None)
        self.assertEqual((gate["status"], gate["blockers"]), ("BLOCKED", ["EVIDENCE_DB_MISSING"]))


class RevisionGateCoverageTests(Harness):
    """P8.4F FIX3 (P8.5 HIGH): CLEAR only with full coverage by individual anomaly identity."""

    def anomaly_ids(self):
        db = sqlite3.connect(self.ev)
        try:
            return [r[0] for r in db.execute("SELECT anomaly_id FROM evidence_anomalies WHERE kind='REVISION' "
                                             "ORDER BY rowid")]
        finally:
            db.close()

    def records(self):
        return RevisionReviewTests.rows(self)[0]

    def test_false_clear_1_missing_evidence_db_is_blocked(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        with patch("runtime.revision_review.classify", side_effect=RuntimeError("bug")):
            self.cycle(S2, BENIGN, flag=True)  # one anomaly left unclassified
        self.assertEqual(demo_gate(self.db, self.ev)["blockers"], ["UNCLASSIFIED"])
        for missing in (None, Path(self.tmp.name) / "absent.db"):
            gate = demo_gate(self.db, missing)
            self.assertEqual((gate["status"], gate["blockers"]), ("BLOCKED", ["EVIDENCE_DB_MISSING"]))
        self.assertFalse((Path(self.tmp.name) / "absent.db").exists())  # the gate never creates it
        self.assertEqual(main(["gate", "--trading-db", str(self.db), "--evidence-db",
                               str(Path(self.tmp.name) / "absent.db")]), 3)
        with self.assertRaises(SystemExit), redirect_stderr(io.StringIO()):
            main(["gate", "--trading-db", str(self.db)])  # --evidence-db is mandatory

    def test_false_clear_2_second_revision_of_a_classified_bar_is_unclassified(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, BENIGN, flag=True)
        with patch("runtime.revision_review.classify", side_effect=RuntimeError("bug")):
            self.cycle(S3, BENIGN_2, flag=True)  # same bar, different content: a second anomaly
        first, second = self.anomaly_ids()
        self.assertEqual([r["anomaly_id"] for r in self.records()], [first])
        gate = demo_gate(self.db, self.ev)
        self.assertEqual((gate["status"], gate["blockers"], [u["anomaly_id"] for u in gate["unclassified"]]),
                         ("BLOCKED", ["UNCLASSIFIED"], [second]))
        self.assertEqual((gate["anomalies"], gate["classified"]), (2, 1))

    def test_multiple_revisions_of_one_bar_are_reviewed_individually_and_persist(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, MATERIAL, flag=True)
        self.cycle(S3, MATERIAL_2, flag=True)
        self.cycle(S3 + timedelta(minutes=15), MATERIAL_2, flag=True)  # same content again: deduplicated
        ids = self.anomaly_ids()
        records = self.records()
        self.assertEqual(len(ids), 2)
        self.assertEqual([r["anomaly_id"] for r in records], ids)
        self.assertEqual({r["bar_start"] for r in records}, {bar(2).isoformat()})
        self.assertTrue(all(r["material"] and r["review_key"] == r["anomaly_id"] for r in records))
        self.assertEqual(main(["review", "--trading-db", str(self.db), "--key", ids[0], "--decision",
                               "ACCEPTED_FIRST_COMMITTED", "--reviewer", "owner"]), 0)
        gate = demo_gate(self.db, self.ev)  # partial review: the other revision of the same bar still blocks
        self.assertEqual((gate["status"], [r["anomaly_id"] for r in gate["material_unreviewed"]]),
                         ("BLOCKED", [ids[1]]))
        self.assertEqual(main(["review", "--trading-db", str(self.db), "--key", ids[1], "--decision", "ESCALATED",
                               "--reviewer", "owner"]), 0)
        # P8.4G (DEC-8.12) supersedes the P8.4F expectation that ESCALATED resolves: still BLOCKED until final.
        self.assertEqual(demo_gate(self.db, self.ev)["blockers"], ["ESCALATED_UNRESOLVED"])
        self.assertEqual(main(["review", "--trading-db", str(self.db), "--key", ids[1], "--decision",
                               "ACCEPTED_FIRST_COMMITTED", "--reviewer", "owner"]), 0)
        self.cycle(S3 + timedelta(minutes=30), {}, flag=True)  # decisions are durable across a restart/cycle
        gate = demo_gate(self.db, self.ev)
        self.assertEqual((gate["status"], gate["blockers"], gate["anomalies"], gate["classified"]),
                         ("CLEAR", [], 2, 2))
        self.assertEqual(json.loads(RevisionReviewTests.rows(self)[1])["material_unreviewed"], 0)
        self.assertIn("XAUUSD", self.paper()[0].open_positions)

    def test_classification_without_a_matching_anomaly_blocks(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, BENIGN, flag=True)
        other = Path(self.tmp.name) / "other_evidence.db"
        EvidenceStore(other).close()  # a different, empty evidence database
        gate = demo_gate(self.db, other)
        self.assertEqual((gate["status"], gate["blockers"], len(gate["orphan_classifications"])),
                         ("BLOCKED", ["CLASSIFICATION_WITHOUT_ANOMALY"], 1))

    def test_unidentifiable_revision_is_never_recorded_with_a_guessed_identity(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        records = revision_review.classify("XAUUSD", {}, {"5m": SimpleNamespace(revisions=(bar(2).isoformat(),))},
                                           {}, [], {})
        self.assertEqual(records, [])


class EscalationTests(Harness):
    """P8.4G FIX B (DEC-8.12, P8.5R HIGH): ESCALATED is never a final resolution."""

    def review(self, key, decision, reviewer="owner", note=""):
        store = Store(self.db)
        try:
            record_review(store, key, decision=decision, reviewer=reviewer, note=note)
        finally:
            store.close()

    def reviews(self):
        return RevisionReviewTests.rows(self, revision_review.REVIEW_EVENT)[0]

    def committed_digest(self):
        db = sqlite3.connect(self.ev)
        try:
            return db.execute("SELECT digest, payload FROM market_evidence WHERE timeframe='5m' AND bar_start=?",
                              (bar(2).isoformat(),)).fetchone()
        finally:
            db.close()

    def material(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, MATERIAL, flag=True)
        return RevisionReviewTests.rows(self)[0][0]["anomaly_id"]

    def test_material_escalated_blocks_until_explicit_final_decision(self):
        key = self.material()
        committed, paper = self.committed_digest(), self.paper()[0]
        self.assertEqual(demo_gate(self.db, self.ev)["blockers"], ["MATERIAL_UNREVIEWED"])  # no review
        self.review(key, "ESCALATED", note="provider asked")
        gate = demo_gate(self.db, self.ev)
        self.assertEqual((gate["status"], gate["blockers"], [r["anomaly_id"] for r in gate["escalated_unresolved"]]),
                         ("BLOCKED", ["ESCALATED_UNRESOLVED"], [key]))
        self.assertEqual(json.loads(RevisionReviewTests.rows(self)[1])["escalated_unresolved"], 1)
        self.review(key, "ACCEPTED_FIRST_COMMITTED", note="provider confirmed; committed bar stays authoritative")
        gate = demo_gate(self.db, self.ev)
        self.assertEqual((gate["status"], gate["blockers"], gate["resolved"]), ("CLEAR", [], 1))
        history = self.reviews()  # append-only, auditable
        self.assertEqual([(r["decision"], r["final"], r["previous_decision"], r["anomaly_id"]) for r in history],
                         [("ESCALATED", False, None, key), ("ACCEPTED_FIRST_COMMITTED", True, "ESCALATED", key)])
        self.assertEqual(self.committed_digest(), committed)  # committed bar never modified
        after = self.paper()[0]
        self.assertEqual((after.open_positions, after.closed_trades, after.cash, after.realized_pnl),
                         (paper.open_positions, paper.closed_trades, paper.cash, paper.realized_pnl))

    def test_non_material_escalated_also_blocks(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, BENIGN, flag=True)
        key = RevisionReviewTests.rows(self)[0][0]["anomaly_id"]
        self.assertEqual(demo_gate(self.db, self.ev)["status"], "CLEAR")  # unreviewed non-material: warning only
        self.review(key, "ESCALATED")
        self.assertEqual(demo_gate(self.db, self.ev)["blockers"], ["ESCALATED_UNRESOLVED"])

    def test_escalation_after_a_final_decision_reopens_and_keeps_history(self):
        key = self.material()
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        self.assertEqual(demo_gate(self.db, self.ev)["status"], "CLEAR")
        self.review(key, "ESCALATED", note="new information")
        self.assertEqual(demo_gate(self.db, self.ev)["blockers"], ["ESCALATED_UNRESOLVED"])
        self.assertEqual([r["decision"] for r in self.reviews()], ["ACCEPTED_FIRST_COMMITTED", "ESCALATED"])

    def test_new_partial_revision_never_erases_previous_decisions(self):
        key = self.material()
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        self.cycle(S3, MATERIAL_2, flag=True)  # a second anomaly of the same bar arrives later
        self.cycle(S3 + timedelta(minutes=15), MATERIAL, flag=True)  # the first content re-presented: no duplicate
        gate = demo_gate(self.db, self.ev)
        second = [r["anomaly_id"] for r in gate["material_unreviewed"]]
        self.assertEqual((gate["status"], gate["blockers"], gate["resolved"], len(second)),
                         ("BLOCKED", ["MATERIAL_UNREVIEWED"], 1, 1))
        self.assertNotEqual(second[0], key)
        self.assertEqual([r["anomaly_id"] for r in self.reviews()], [key])  # first decision intact
        self.review(second[0], "ESCALATED")
        self.assertEqual(demo_gate(self.db, self.ev)["blockers"], ["ESCALATED_UNRESOLVED"])
        self.review(second[0], "ACCEPTED_FIRST_COMMITTED")
        self.assertEqual(demo_gate(self.db, self.ev)["status"], "CLEAR")

    def test_unclassified_still_blocks_even_when_everything_recorded_is_resolved(self):
        key = self.material()
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        with patch("runtime.revision_review.classify", side_effect=RuntimeError("bug")):
            self.cycle(S3, MATERIAL_2, flag=True)
        self.assertEqual(demo_gate(self.db, self.ev)["blockers"], ["UNCLASSIFIED"])

    def test_review_requires_known_anomaly_valid_decision_and_reviewer(self):
        key = self.material()
        for kwargs in ({"key": key, "decision": "RESOLVED"}, {"key": "unknown", "decision": "ESCALATED"},
                       {"key": key, "decision": "ACCEPTED_FIRST_COMMITTED", "reviewer": "  "}):
            with self.assertRaises(ValueError):
                self.review(**kwargs)
        self.assertEqual(self.reviews(), [])
