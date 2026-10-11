"""V2 Phase 8 / P4a pre-correction of G15 (replay/rex_chain.py): malformed input is CERTIFICATION_INVALID with a
code and a structured context, never an uncontrolled exception. Temporary copies only."""
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest

from replay.rex_chain import verify
from storage.economic_digest import H, expected_edg_genesis
from test_phase8_rex_writer import plan_scenario

GENESIS = expected_edg_genesis("paper-main", 10000.0)["edg"]


class MalformedInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp_class = tempfile.TemporaryDirectory(prefix="v2-p4a-malformed-")
        cls.master = Path(cls.tmp_class.name) / "master.db"
        plan_scenario(cls.master)

    @classmethod
    def tearDownClass(cls):
        cls.tmp_class.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p4a-malformed-case-", ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "trading_floor.db"
        shutil.copyfile(self.master, self.db)

    def sql(self, statement, params=()):
        conn = sqlite3.connect(self.db)  # tampering of the temporary copy only
        try:
            conn.execute(statement, params)
            conn.commit()
        finally:
            conn.close()

    def resign_first(self, event_type, edit):
        conn = sqlite3.connect(self.db)
        try:
            journal_id, payload = conn.execute("SELECT id,payload FROM journal WHERE event_type=? ORDER BY id LIMIT 1",
                                               (event_type,)).fetchone()
            record = json.loads(json.loads(payload)["rex"])
            record = edit(record) or record
            text = json.dumps(record, sort_keys=True, separators=(",", ":"))
            conn.execute("UPDATE journal SET payload=? WHERE id=?",
                         (json.dumps({"rex": text, "rex_digest": H("V2REX/1", text)}), journal_id))
            conn.commit()
        finally:
            conn.close()
        return journal_id

    def invalid(self, code, **context):
        report = verify(self.db, edg_start=GENESIS)  # never raises
        self.assertEqual(report["classification"], "CERTIFICATION_INVALID")
        self.assertEqual(report["sqlite_result"], "CERTIFICATION_INVALID")
        found = [f for f in report["findings"] if f["code"] == code]
        self.assertTrue(found, report["findings"])
        for key, value in context.items():
            self.assertTrue(any(f.get("context", {}).get(key) == value for f in found), (key, value, found))
        self.assertTrue(report["source_unchanged"])
        return report

    # -- economic payloads -------------------------------------------------------------------------------------------
    def test_invalid_economic_json(self):
        self.sql("UPDATE paper_accounts SET payload='{bad'")
        self.invalid("ECONOMIC_PAYLOAD_MALFORMED", table="paper_accounts", check="json", stage="current_state")

    def test_missing_economic_fields(self):
        self.sql("""UPDATE paper_orders SET payload=json_object('order_id', order_id)""")
        self.invalid("ECONOMIC_PAYLOAD_MALFORMED", table="paper_orders", check="contract_fields")

    def test_unexpected_economic_types(self):
        self.sql("UPDATE paper_orders SET payload='[1,2]'")
        self.invalid("ECONOMIC_PAYLOAD_MALFORMED", table="paper_orders", check="json_object")
        shutil.copyfile(self.master, self.db)
        self.sql("UPDATE paper_fills SET payload=json_set(payload,'$.quantity','ten')")
        self.invalid("FINAL_EDG_MISMATCH")  # readable but wrong: the chain catches it

    def test_non_utf8_economic_payload(self):
        self.sql("UPDATE paper_accounts SET payload=CAST(X'7B22FF' AS TEXT)")
        self.invalid("ECONOMIC_PAYLOAD_MALFORMED", table="paper_accounts", check="utf8")

    # -- REX records -------------------------------------------------------------------------------------------------
    def test_rex_write_with_wrong_structure(self):
        jid = self.resign_first("REX_WRITE", lambda r: r.update(pre=5))
        self.invalid("REX_RECORD_MALFORMED", journal_id=jid, kind="REX_WRITE")

    def test_rex_write_events_missing_keys_or_wrong_type(self):
        def drop_keys(record):
            write_events = record["journal_events"] or [{}]
            record["journal_events"] = [{"x": 1} for _ in write_events]
        jid = self.resign_first("REX_WRITE", drop_keys)
        self.invalid("REX_RECORD_MALFORMED", journal_id=jid)
        shutil.copyfile(self.master, self.db)
        jid = self.resign_first("REX_WRITE", lambda r: r.update(journal_events="none"))
        self.invalid("REX_RECORD_MALFORMED", journal_id=jid)

    def test_rex_run_with_wrong_types(self):
        self.resign_first("REX_RUN", lambda r: r.update(writes="all"))
        self.invalid("REX_RECORD_MALFORMED", kind="REX_RUN")

    def test_rex_record_that_is_not_an_object(self):
        jid = self.resign_first("REX_WRITE", lambda r: ["not", "an", "object"])
        self.invalid("REX_RECORD_MALFORMED", journal_id=jid, field="<record>")

    def test_non_utf8_journal_payload(self):
        jid = sqlite3.connect(self.db).execute("SELECT MIN(id) FROM journal WHERE source='rex'").fetchone()[0]
        self.sql("UPDATE journal SET payload=CAST(X'7B22FF' AS TEXT) WHERE id=?", (jid,))
        self.invalid("PAYLOAD_NOT_UTF8", journal_id=jid, table="journal")

    # -- snapshot processing and cleanup -----------------------------------------------------------------------------
    def test_unprocessable_copy_is_invalid_not_a_crash(self):
        self.sql("DROP TABLE journal")
        self.invalid("VERIFIER_INPUT_UNPROCESSABLE", error_type="RuntimeError")  # Store: incomplete schema
        self.db.write_bytes(b"this is not a SQLite database at all" * 200)
        self.invalid("VERIFIER_INPUT_UNPROCESSABLE")

    def test_cleanup_never_masks_the_classification(self):
        """The case that previously ended in PermissionError on Windows (a statement kept alive by the traceback)."""
        self.sql("UPDATE paper_orders SET payload='[1,2]'")
        for _ in range(3):  # repeated analyses of the same malformed copy, each with its own snapshot
            self.invalid("ECONOMIC_PAYLOAD_MALFORMED")

    def test_well_formed_period_is_unaffected(self):
        report = verify(self.db, edg_start=GENESIS)
        self.assertEqual(report["sqlite_result"], "SQLITE_CHECKS_PASS", report["findings"])


if __name__ == "__main__":
    unittest.main()
