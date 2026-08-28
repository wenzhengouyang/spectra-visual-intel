import copy
import json
import tempfile
import unittest
from pathlib import Path

from spectra_agent.run import gated_review_template, prepare_static_draft, review_template, select_review_candidates
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

    def test_static_draft_copies_all_relative_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            draft = prepare_static_draft(run_dir, ROOT / "visual-intelligence-prototype.html")
            html = draft.read_text(encoding="utf-8")
            self.assertIn('href="tokens.css?v=4"', html)
            self.assertIn('href="app/globals.css?v=19"', html)
            self.assertIn('href="app/hallmark-editorial.css?v=13"', html)
            self.assertIn('id="verifiedBriefIndex"', html)
            self.assertTrue((run_dir / "tokens.css").exists())
            self.assertTrue((run_dir / "app/globals.css").exists())
            self.assertTrue((run_dir / "app/hallmark-editorial.css").exists())

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

    def test_failed_hard_gates_are_separate_from_p1_review(self):
        collection = {
            "source_records": [{
                "source_id": "src_incomplete", "raw_title": "正文缺失",
                "canonical_url": "https://example.com/incomplete",
            }, {
                "source_id": "src_fidelity", "raw_title": "推测被写成事实",
                "canonical_url": "https://example.com/fidelity",
            }]
        }
        candidates = {
            "selected_candidates": [],
            "exclusions": {"content_incomplete": [{
                "source_id": "src_incomplete", "title": "正文缺失",
                "gate": {"status": "fail", "reason": "full_text_required_but_missing"},
            }]},
            "gated_candidates": [{
                "candidate_id": "cand_fidelity", "primary_source_id": "src_fidelity",
                "canonical_title": "推测被写成事实",
                "hard_gates": {"fact_wording_fidelity": {
                    "status": "fail", "reason": "source_attribution_or_uncertainty_was_upgraded_to_fact",
                }},
                "llm_analysis": {"what": "未经限定的事实"},
            }],
        }
        p1 = review_template(collection, candidates, "test_gates", self.config)
        gated = gated_review_template(collection, candidates, "test_gates")
        self.assertEqual(p1["records"], [])
        self.assertEqual(gated["count"], 2)
        self.assertEqual({item["gate_type"] for item in gated["records"]}, {
            "content_completeness", "fact_wording_fidelity",
        })

    def test_pending_fidelity_gate_cannot_enter_p1(self):
        candidate = {
            "candidate_id": "cand_pending", "primary_source_id": "src_pending",
            "canonical_title": "待模型归因检查", "published_at": "2026-08-26T00:00:00Z",
            "verification_priority": "priority.p1", "front_display_eligible": True,
            "hard_gates": {
                "content_completeness": {"status": "pass"},
                "fact_wording_fidelity": {"status": "pending_llm"},
            },
            "score": 20, "intelligence_type": "type.company_strategy",
        }
        candidates = {"selected_candidates": [candidate], "exclusions": {}}
        collection = {"source_records": [{
            "source_id": "src_pending", "raw_title": "待模型归因检查",
            "canonical_url": "https://example.com/pending",
        }]}
        self.assertEqual(select_review_candidates(candidates, self.config), [])
        gated = gated_review_template(collection, candidates, "test_pending")
        self.assertEqual(gated["count"], 1)
        self.assertEqual(gated["records"][0]["gate"]["status"], "pending_llm")

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
