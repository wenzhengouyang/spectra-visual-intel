import copy
import json
import tempfile
import unittest
from pathlib import Path

from spectra_agent.run import review_template, select_review_candidates
from verification.build_final_events import build_bundle, validate_review


ROOT = Path(__file__).resolve().parents[1]


class SpectraAgentGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collection = json.loads((ROOT / "collector/runs/first-live-run-v0.2.json").read_text())
        cls.candidates = json.loads((ROOT / "processor/runs/first-structured-run-v0.1.json").read_text())
        cls.approved = json.loads((ROOT / "verification/p1-review.v0.1.json").read_text())
        cls.config = json.loads((ROOT / "spectra_agent/config.v0.1.json").read_text())

    def test_template_contains_only_p1_and_is_pending(self):
        template = review_template(self.collection, self.candidates, "test_run", self.config)
        expected = select_review_candidates(self.candidates, self.config)
        self.assertEqual(len(template["records"]), len(expected))
        self.assertTrue(all(item["decision"] == "pending" for item in template["records"]))
        self.assertEqual(template["review_status"], "pending")

    def test_template_exposes_llm_analysis_without_crossing_gate(self):
        candidates = copy.deepcopy(self.candidates)
        target_id = select_review_candidates(candidates, self.config)[0]["candidate_id"]
        target = next(item for item in candidates["selected_candidates"] if item["candidate_id"] == target_id)
        target["llm_analysis"] = {
            "recommended_disposition": "p1", "disposition_reason": "值得核验但尚未核验。",
            "verification_questions": ["原文指标在哪里？"], "what": "测试", "why": "测试",
        }
        template = review_template(self.collection, candidates, "test_llm", self.config)
        record = next(item for item in template["records"] if item["candidate_id"] == target["candidate_id"])
        self.assertEqual(record["agent_recommendation"], "p1")
        self.assertIn("原文指标在哪里？", record["verification_questions"])
        self.assertEqual(record["decision"], "pending")

    def test_pending_review_cannot_cross_gate(self):
        template = review_template(self.collection, self.candidates, "test_run", self.config)
        errors = validate_review(template, self.candidates)
        self.assertTrue(errors)
        self.assertTrue(any("review_status" in error for error in errors))
        self.assertTrue(any("primary source" in error for error in errors))

    def test_approved_fixture_builds_only_included_events(self):
        result = build_bundle(copy.deepcopy(self.approved), self.collection, self.candidates)
        self.assertEqual(result["summary"]["p1_reviewed"], 16)
        self.assertEqual(result["summary"]["included_events"], 9)
        self.assertEqual(len(result["intelligence_events"]), 9)
        self.assertTrue(all(item["status"] == "approved" for item in result["intelligence_events"]))

    def test_missing_claim_blocks_resume(self):
        review = copy.deepcopy(self.approved)
        review["records"][0]["claims"] = []
        errors = validate_review(review, self.candidates)
        self.assertTrue(any("evidence claim" in error for error in errors))

    def test_duplicate_event_id_blocks_resume(self):
        review = copy.deepcopy(self.approved)
        included = [item for item in review["records"] if item["decision"] == "include"]
        included[1]["event"]["event_id"] = included[0]["event"]["event_id"]
        errors = validate_review(review, self.candidates)
        self.assertTrue(any("duplicate event_id" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
