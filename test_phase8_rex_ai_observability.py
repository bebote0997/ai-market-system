"""V2 Phase 8 / P3 AI request observability (Owner decision 2026-10-09): the passive ``ai.runtime`` observer and the
G14 V-P1 paths EMITTED_OBSERVED / LEGITIMATE_OMISSION / fail (insufficient or contradictory evidence).

A skipped request is OBSERVED (the real ``AIRequest`` object call_agent handled), never synthesized; the oracle
verifies the omission independently (fingerprint recomputed from the observed material, skip rule re-evaluated).
Temporary databases only; REX OFF by default.
"""
import copy
from dataclasses import asdict
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai.contracts import AIRequest
from ai.provider import DeterministicAIProvider
from ai.runtime import _REQUEST_OBSERVER, AuditLog, call_agent, observe_requests
from replay.rex_oracle import check_run, evidence_fingerprint
from runtime.rex import REX_RUN, REX_WRITE, RexRecorder
from test_demo_runner import T
from test_phase8_rex_inertness import snapshot
from test_phase8_rex_writer import catch_up_scenario, plan_scenario, rex_rows


def request(evidence):
    return AIRequest("1.0", "run-x", T, "EURUSD", "macro_ai", "macro", {}, tuple(evidence), ("ASSESS",), {}, "p1")


def audit_core(entries):
    return [{k: v for k, v in e.items() if k != "call"} for e in entries]


class HookTests(unittest.TestCase):
    def test_off_by_default_and_reset_after_use(self):
        self.assertIsNone(_REQUEST_OBSERVER.get())
        with observe_requests(lambda record: None):
            self.assertIsNotNone(_REQUEST_OBSERVER.get())
        self.assertIsNone(_REQUEST_OBSERVER.get())

    def test_skipped_request_is_observed_never_sent(self):
        seen = []

        class Counting(DeterministicAIProvider):
            calls = 0

            def generate(self, req):
                Counting.calls += 1
                return super().generate(req)
        audit = AuditLog()
        with observe_requests(seen.append):
            response = call_agent(Counting(), request(()), audit)
        self.assertEqual(Counting.calls, 0)  # no provider call, observed or not
        self.assertEqual(response.status, "NO_DATA")
        [record] = seen
        self.assertEqual((record["kind"], record["provider_called"], record["reason"]),
                         ("SKIPPED", False, "skipped_no_data"))
        self.assertEqual((record["run_id"], record["symbol"], record["as_of"], record["agent_name"]),
                         ("run-x", "EURUSD", T, "macro_ai"))
        self.assertIn("observed, never reconstructed", record["provenance"])
        self.assertEqual(evidence_fingerprint(record["fingerprint_material"]), audit.entries[0]["evidence_fingerprint"])

    def test_emitted_request_is_observed(self):
        seen = []
        evidence = ({"evidence_id": "e1", "x": 1.5},)
        audit = AuditLog()
        with observe_requests(seen.append):
            call_agent(DeterministicAIProvider(), request(evidence), audit)
        [record] = seen
        self.assertEqual((record["kind"], record["provider_called"], record["usable_evidence"]),
                         ("EMITTED", True, True))
        self.assertEqual(evidence_fingerprint(record["fingerprint_material"]), audit.entries[0]["evidence_fingerprint"])

    def test_raising_observer_never_changes_the_agent(self):
        def boom(record):
            raise RuntimeError("observer down")
        for evidence in ((), ({"evidence_id": "e1"},)):
            plain, observed = AuditLog(), AuditLog()
            a = call_agent(DeterministicAIProvider(), request(evidence), plain)
            with observe_requests(boom):
                b = call_agent(DeterministicAIProvider(), request(evidence), observed)
            self.assertEqual(asdict(a), asdict(b))
            self.assertEqual(audit_core(plain.entries), audit_core(observed.entries))


class OracleOmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="v2-p3-aiobs-")
        d = Path(cls.tmp.name)
        cls.db = d / "cu.db"
        catch_up_scenario(cls.db, d / "ev.db", symbols=("XAUUSD", "EURUSD"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def eur_run(self):
        writes = {}
        for _, run_id, _, record, _ in rex_rows(self.db, REX_WRITE):
            writes.setdefault(run_id, []).append(record)
        record = next(r for _, _, _, r, _ in rex_rows(self.db, REX_RUN) if r["symbol"] == "EURUSD")
        return copy.deepcopy(record), copy.deepcopy(writes.get(record["run_id"], []))

    @staticmethod
    def macro(record):
        return next(o for o in record["ai_observations"] if o["agent_name"] == "macro_ai")

    @staticmethod
    def audit(record):
        st5 = next(s for s in record["stages"] if s["stage"] == "ST5")
        return next(e for e in st5["audit"] if e["agent"] == "macro_ai")

    def fails(self, edit, code):
        record, writes = self.eur_run()
        edit(record)
        verdict = check_run(record, writes)
        self.assertEqual(verdict["result"], "FAIL")
        self.assertIn(code, [f["code"] for f in verdict["failures"]], verdict["failures"])

    def test_macro_without_news_is_a_legitimate_omission_without_an_invented_request(self):
        record, writes = self.eur_run()
        self.assertNotIn("macro_ai", [q["agent_name"] for q in record["ai_requests"]])  # nothing reached a provider
        obs = self.macro(record)
        self.assertEqual((obs["kind"], obs["provider_called"]), ("SKIPPED", False))
        verdict = check_run(record, writes)
        self.assertEqual(verdict["result"], "PASS", verdict["failures"])
        self.assertIn("V-P1 ai_macro: LEGITIMATE_OMISSION", verdict["checked"])
        self.assertIn("V-P1 ai_structure: EMITTED_OBSERVED", verdict["checked"])

    def test_wrong_identity_fails(self):
        self.fails(lambda r: self.macro(r).update(symbol="XAUUSD"), "VP1_IDENTITY_MISMATCH")
        self.fails(lambda r: self.macro(r).update(run_id="other-run"), "VP1_IDENTITY_MISMATCH")

    def test_tampered_omission_reason_fails(self):
        self.fails(lambda r: self.macro(r).update(reason="provider_down"), "VP1_OMISSION_REASON_MISMATCH")
        self.fails(lambda r: self.audit(r).update(validation="ok"), "VP1_OMISSION_REASON_MISMATCH")

    def test_omission_without_sufficient_evidence_fails(self):
        self.fails(lambda r: r.update(ai_observations=[o for o in r["ai_observations"]
                                                       if o["agent_name"] != "macro_ai"]), "VP1_REQUEST_NOT_OBSERVED")
        self.fails(lambda r: self.audit(r).pop("called"), "VP1_EVIDENCE_INSUFFICIENT")
        self.fails(lambda r: self.macro(r).update(provider_called=True), "VP1_OBSERVATION_CONTRADICTION")

    def test_unjustified_omission_fails_even_when_rewritten_consistently(self):
        def edit(record):
            obs, entry = self.macro(record), self.audit(record)
            obs["fingerprint_material"]["evidence"] = [{"evidence_id": "news-1"}]  # usable evidence existed
            obs["usable_evidence"] = True
            entry["evidence_ids"] = ["news-1"]
            entry["evidence_fingerprint"] = evidence_fingerprint(obs["fingerprint_material"])
        self.fails(edit, "VP1_OMISSION_UNJUSTIFIED")

    def test_fingerprint_binding_fails_on_altered_material(self):
        self.fails(lambda r: self.macro(r)["fingerprint_material"].update(role="structure"), "VP1_IDENTITY_MISMATCH")
        self.fails(lambda r: self.macro(r)["fingerprint_material"].update(evidence=["x"]), "VP1_FINGERPRINT_MISMATCH")


class RuntimeTwinTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="v2-p3-aiobs-twin-")
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def counted(self, run):
        calls = []
        real = DeterministicAIProvider.generate

        def generate(provider, req):
            calls.append((req.run_id, req.agent_name))
            return real(provider, req)
        with patch.object(DeterministicAIProvider, "generate", generate):
            results = run()
        return results, calls

    def test_no_additional_provider_calls_and_identical_outcomes(self):
        on = self.counted(lambda: plan_scenario(self.dir / "on.db"))
        off = self.counted(lambda: plan_scenario(self.dir / "off.db", rex_on=False))
        self.assertEqual(on, off)
        cu_on = self.counted(lambda: catch_up_scenario(self.dir / "c_on.db", self.dir / "e_on.db",
                                                       symbols=("XAUUSD", "EURUSD")))
        cu_off = self.counted(lambda: catch_up_scenario(self.dir / "c_off.db", self.dir / "e_off.db", rex_on=False,
                                                        symbols=("XAUUSD", "EURUSD")))
        self.assertEqual(cu_on, cu_off)
        self.assertEqual(snapshot(self.dir / "c_on.db"), snapshot(self.dir / "c_off.db"))
        self.assertEqual(snapshot(self.dir / "on.db"), snapshot(self.dir / "off.db"))

    def test_raising_observation_keeps_economics_and_decisions(self):
        with patch.object(RexRecorder, "ai_observation", side_effect=RuntimeError("observer down")):
            catch_up_scenario(self.dir / "c_on.db", self.dir / "e_on.db", symbols=("XAUUSD", "EURUSD"))
        catch_up_scenario(self.dir / "c_off.db", self.dir / "e_off.db", rex_on=False, symbols=("XAUUSD", "EURUSD"))
        self.assertEqual(snapshot(self.dir / "c_on.db"), snapshot(self.dir / "c_off.db"))


if __name__ == "__main__":
    unittest.main()
