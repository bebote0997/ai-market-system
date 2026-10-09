"""V2 Phase 8 / P1-A (DEC-8.21, DEC-8.22-a): LOW-1 hardening of the REVISION review gate (P8.6 design section 4).

A review row counts only if it is VALID (source, decision, review_key == anomaly_id of a recorded EVIDENCE_REVISION,
reviewer, final, previous_decision). The latest row decides; an invalid latest row blocks the gate with no fallback.
Remediation is append-only. Direct journal writes below simulate tampering on a temporary test DB only.
"""
from datetime import datetime, timezone
import json
import sqlite3
from unittest.mock import patch

from runtime import revision_review
from runtime.revision_review import REVIEW_EVENT, SOURCE, demo_gate, record_review
from storage.database import Store
from test_phase8_activation_simulation import S1, S2, bar
from test_phase8_catch_up_certification import Harness

MATERIAL = {("XAUUSD", bar(2)): (100.0, 100.5, 94.0, 96.0)}  # crosses SL 95 of the open LONG; committed did not


class LowOneReviewValidationTests(Harness):
    def material(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, MATERIAL, flag=True)
        store = Store(self.db)
        try:
            return json.loads(store.db.execute("SELECT payload FROM journal WHERE event_type=?",
                                               (revision_review.EVENT,)).fetchone()[0])["anomaly_id"]
        finally:
            store.close()

    def review(self, key, decision, reviewer="owner"):
        store = Store(self.db)
        try:
            record_review(store, key, decision=decision, reviewer=reviewer)
        finally:
            store.close()

    def forge(self, payload, source=SOURCE, event_type=REVIEW_EVENT):
        """Simulated tampering: a row written directly to the journal, bypassing record_review."""
        store = Store(self.db)
        try:
            with store.transaction():
                store._event(datetime.now(timezone.utc), None, "XAUUSD", source, event_type, "INFO", payload)
        finally:
            store.close()

    def valid_payload(self, key, decision="ACCEPTED_FIRST_COMMITTED", previous=None, **overrides):
        payload = {"review_key": key, "anomaly_id": key, "decision": decision,
                   "final": decision in revision_review.FINAL_DECISIONS, "previous_decision": previous,
                   "reviewer": "owner", "note": ""}
        payload.update(overrides)
        return payload

    def journal_snapshot(self):
        db = sqlite3.connect(self.db)
        try:
            return db.execute("SELECT id, timestamp, run_id, symbol, source, event_type, severity, payload "
                              "FROM journal ORDER BY id").fetchall()
        finally:
            db.close()

    def gate(self):
        before = self.journal_snapshot()
        result = demo_gate(self.db, self.ev)
        self.assertEqual(self.journal_snapshot(), before)  # the gate never writes, so history is unchanged
        return result

    # --- LOW-1 reproduction (P8.5R2): an unknown decision written directly must not clear a material anomaly
    def test_unknown_decision_blocks(self):
        key = self.material()
        self.forge({"review_key": key, "decision": "RESOLVED"})
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"]), ("BLOCKED", ["INVALID_REVIEW_RECORD"]))
        self.assertIn("unknown_decision", gate["invalid_reviews"][0]["reasons"])

    def test_well_formed_unknown_decision_still_blocks(self):
        key = self.material()
        self.forge(self.valid_payload(key, decision="RESOLVED", final=True))
        self.assertEqual(self.gate()["blockers"], ["INVALID_REVIEW_RECORD"])

    def test_missing_reviewer_missing_final_and_inconsistent_final_block(self):
        for overrides in ({"reviewer": "  "}, {"reviewer": None}, {"final": None}, {"final": False}, {"final": "true"}):
            with self.subTest(overrides=overrides):
                self.setUp()
                key = self.material()
                payload = self.valid_payload(key)
                payload.update(overrides)
                if overrides.get("final", 0) is None:
                    payload.pop("final")
                self.forge(payload)
                gate = self.gate()
                self.assertEqual((gate["status"], gate["blockers"]), ("BLOCKED", ["INVALID_REVIEW_RECORD"]))

    def test_wrong_source_blocks(self):
        key = self.material()
        self.forge(self.valid_payload(key), source="manual_sql")
        gate = self.gate()
        self.assertEqual(gate["blockers"], ["INVALID_REVIEW_RECORD"])
        self.assertIn("wrong_source", gate["invalid_reviews"][0]["reasons"])

    def test_review_key_must_equal_anomaly_id(self):
        key = self.material()
        self.forge(self.valid_payload(key, anomaly_id="other"))
        self.assertEqual(self.gate()["blockers"], ["INVALID_REVIEW_RECORD"])

    def test_legacy_row_without_previous_decision_is_invalid(self):
        key = self.material()
        payload = self.valid_payload(key)
        payload.pop("previous_decision")
        self.forge(payload)
        self.assertEqual(self.gate()["blockers"], ["INVALID_REVIEW_RECORD"])

    def test_removed_middle_row_breaks_the_chain_and_blocks(self):
        key = self.material()
        self.review(key, "ESCALATED")
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        self.review(key, "ESCALATED")
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        self.assertEqual(self.gate()["status"], "CLEAR")
        db = sqlite3.connect(self.db)
        try:  # simulated tampering: delete the second review row
            ids = [r[0] for r in db.execute("SELECT id FROM journal WHERE event_type=? ORDER BY id", (REVIEW_EVENT,))]
            db.execute("DELETE FROM journal WHERE id=?", (ids[1],))
            db.commit()
        finally:
            db.close()
        gate = self.gate()
        # rows now: ESC(prev None), ESC(prev ACC: wrong), ACC(prev ESC: correct) -> latest valid; the broken row is
        # historical and is listed
        self.assertEqual(gate["status"], "CLEAR")
        self.assertEqual([r["reasons"] for r in gate["historical_invalid_reviews"]], [["previous_decision_mismatch"]])

    def test_reordered_latest_row_blocks(self):
        key = self.material()
        self.review(key, "ESCALATED")
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        db = sqlite3.connect(self.db)
        try:  # simulated tampering: swap the payloads of the two review rows
            rows = db.execute("SELECT id, payload FROM journal WHERE event_type=? ORDER BY id", (REVIEW_EVENT,)).fetchall()
            db.execute("UPDATE journal SET payload=? WHERE id=?", (rows[1][1], rows[0][0]))
            db.execute("UPDATE journal SET payload=? WHERE id=?", (rows[0][1], rows[1][0]))
            db.commit()
        finally:
            db.close()
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"]), ("BLOCKED", ["INVALID_REVIEW_RECORD"]))
        self.assertIn("previous_decision_mismatch", gate["invalid_reviews"][0]["reasons"])

    def test_invalid_then_valid_final_clears_and_lists_the_invalid_row(self):
        key = self.material()
        self.forge({"review_key": key, "decision": "RESOLVED"})
        self.assertEqual(self.gate()["blockers"], ["INVALID_REVIEW_RECORD"])
        self.review(key, "ACCEPTED_FIRST_COMMITTED")  # append-only remediation
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"], gate["resolved"]), ("CLEAR", [], 1))
        self.assertEqual(len(gate["historical_invalid_reviews"]), 1)
        rows = [json.loads(r[7]) for r in self.journal_snapshot() if r[5] == REVIEW_EVENT]
        self.assertEqual([r["decision"] for r in rows], ["RESOLVED", "ACCEPTED_FIRST_COMMITTED"])  # history kept
        self.assertEqual(rows[1]["previous_decision"], "RESOLVED")  # literal previous decision (design 4.1)

    def test_valid_final_then_invalid_blocks_without_fallback(self):
        key = self.material()
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        self.assertEqual(self.gate()["status"], "CLEAR")
        self.forge(self.valid_payload(key, previous="ACCEPTED_FIRST_COMMITTED", reviewer=""))
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"], gate["resolved"]), ("BLOCKED", ["INVALID_REVIEW_RECORD"], 0))

    def test_review_for_unknown_key_blocks(self):
        key = self.material()
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        self.forge(self.valid_payload("not-a-recorded-anomaly"))
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"]), ("BLOCKED", ["REVIEW_WITHOUT_RECORD"]))
        self.assertEqual(gate["reviews_without_record"][0]["review_key"], "not-a-recorded-anomaly")

    def test_classification_with_mismatched_bar_blocks(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        with patch("runtime.revision_review.classify", side_effect=RuntimeError("bug")):
            self.cycle(S2, MATERIAL, flag=True)  # anomaly recorded in evidence, no classification row
        ev = sqlite3.connect(self.ev)
        try:
            anomaly_id = ev.execute("SELECT anomaly_id FROM evidence_anomalies WHERE kind='REVISION'").fetchone()[0]
        finally:
            ev.close()
        self.forge({"anomaly_id": anomaly_id, "review_key": anomaly_id, "symbol": "XAUUSD", "timeframe": "5m",
                    "bar_start": bar(3).isoformat(), "material": False, "affected": []},
                   event_type=revision_review.EVENT)  # a forged classification for the wrong bar
        self.review(anomaly_id, "ACCEPTED_FIRST_COMMITTED")
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"]), ("BLOCKED", ["CLASSIFICATION_MISMATCH"]))
        self.assertEqual(gate["classification_mismatches"][0]["anomaly"][2], bar(2).isoformat())

    def test_health_summary_counts_an_invalid_latest_row_as_unresolved(self):
        key = self.material()
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        self.forge({"review_key": key, "decision": "RESOLVED"})
        store = Store(self.db)
        try:
            summary = revision_review._summary(store.db)
        finally:
            store.close()
        self.assertEqual((summary["unreviewed"], summary["material_unreviewed"]), (1, 1))

    def test_record_review_still_rejects_bad_input_and_writes_valid_rows(self):
        key = self.material()
        store = Store(self.db)
        try:
            for kwargs in ({"decision": "RESOLVED", "reviewer": "owner"}, {"decision": "ESCALATED", "reviewer": " "}):
                with self.assertRaises(ValueError):
                    record_review(store, key, **kwargs)
            with self.assertRaises(ValueError):
                record_review(store, "unknown", decision="ESCALATED", reviewer="owner")
        finally:
            store.close()
        self.review(key, "ESCALATED")
        self.review(key, "ACCEPTED_FIRST_COMMITTED")
        gate = self.gate()
        self.assertEqual((gate["status"], gate["invalid_reviews"], gate["historical_invalid_reviews"]), ("CLEAR", [], []))


class ClassificationConflictTests(Harness):
    """P1-A hotfix (Copilot HIGH on f8299cb): every classification of an anomaly is validated; duplicates block."""

    def setup_genuine(self):
        self.seed()
        self.cycle(S1, {}, flag=True)
        self.cycle(S2, MATERIAL, flag=True)  # the runtime writes exactly one genuine classification
        store = Store(self.db)
        try:
            return json.loads(store.db.execute("SELECT payload FROM journal WHERE event_type=?",
                                               (revision_review.EVENT,)).fetchone()[0])
        finally:
            store.close()

    def write_classifications(self, rows, *, replace_genuine=False):
        """Simulated tampering on the temporary test DB: append classification rows (optionally after removing the
        genuine one so the order can be inverted), then a valid final review."""
        store = Store(self.db)
        try:
            with store.transaction():
                if replace_genuine:
                    store.db.execute("DELETE FROM journal WHERE event_type=?", (revision_review.EVENT,))
                for row in rows:
                    store._event(datetime.now(timezone.utc), None, "XAUUSD", SOURCE, revision_review.EVENT, "INFO",
                                 row)
            record_review(store, rows[0]["anomaly_id"], decision="ACCEPTED_FIRST_COMMITTED", reviewer="owner")
        finally:
            store.close()

    def gate(self):
        return LowOneReviewValidationTests.gate(self)

    def journal_snapshot(self):
        return LowOneReviewValidationTests.journal_snapshot(self)

    def test_genuine_then_contradictory_blocks_copilot_case(self):
        genuine = self.setup_genuine()
        self.write_classifications([dict(genuine, bar_start=bar(3).isoformat())])  # second row, contradictory
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"]),
                         ("BLOCKED", ["CLASSIFICATION_MISMATCH", "CLASSIFICATION_CONFLICT"]))
        self.assertEqual(gate["classification_mismatches"][0]["classification"][2], bar(3).isoformat())

    def test_contradictory_then_genuine_blocks(self):
        genuine = self.setup_genuine()
        self.write_classifications([dict(genuine, bar_start=bar(3).isoformat()), genuine], replace_genuine=True)
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"]),
                         ("BLOCKED", ["CLASSIFICATION_MISMATCH", "CLASSIFICATION_CONFLICT"]))

    def test_identical_duplicate_blocks(self):
        genuine = self.setup_genuine()
        self.write_classifications([dict(genuine)])
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"]), ("BLOCKED", ["CLASSIFICATION_CONFLICT"]))
        self.assertEqual(gate["classification_conflicts"][0]["count"], 2)

    def test_duplicate_differing_only_in_material_blocks(self):
        genuine = self.setup_genuine()
        self.write_classifications([dict(genuine, material=False, affected=[])])
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"]), ("BLOCKED", ["CLASSIFICATION_CONFLICT"]))

    def test_single_genuine_classification_still_clears(self):
        genuine = self.setup_genuine()
        store = Store(self.db)
        try:
            record_review(store, genuine["anomaly_id"], decision="ACCEPTED_FIRST_COMMITTED", reviewer="owner")
        finally:
            store.close()
        gate = self.gate()
        self.assertEqual((gate["status"], gate["blockers"], gate["classification_conflicts"]), ("CLEAR", [], []))
