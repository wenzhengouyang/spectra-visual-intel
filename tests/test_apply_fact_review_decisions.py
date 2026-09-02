import importlib.util
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "apply_fact_review_decisions",
    ROOT / "verification" / "apply-fact-review-decisions.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ApplyFactReviewDecisionsTest(unittest.TestCase):
    def test_applies_fact_modification_and_secondary_watch(self):
        review = {"records": [{
            "candidate_id": "cand_one",
            "suggested_evidence": [{"claim": "旧事实", "human_fact_decision": "pending"}],
        }]}
        decisions = {
            "verified_by": "reviewer",
            "records": {"cand_one": {
                "verification_status": "verified_secondary",
                "decision": "watch",
                "decision_reason": "只有二手来源。",
                "limitation": "尚未回溯原文。",
                "fact_decisions": [{"decision": "modify", "text": "据媒体报道，新事实。", "kind": "数据"}],
            }},
        }
        result = MODULE.apply_decisions(review, decisions)
        record = result["records"][0]
        self.assertEqual(record["verification_status"], "verified_secondary")
        self.assertEqual(record["decision"], "watch")
        self.assertEqual(record["suggested_evidence"][0]["human_fact_decision"], "modify")
        self.assertEqual(record["suggested_evidence"][0]["human_fact_text"], "据媒体报道，新事实。")

    def test_include_rejects_secondary_source(self):
        review = {"records": [{"candidate_id": "cand_one", "suggested_evidence": []}]}
        decisions = {"verified_by": "reviewer", "records": {"cand_one": {
            "verification_status": "verified_secondary", "decision": "include",
            "decision_reason": "测试", "limitation": "测试", "fact_decisions": [],
        }}}
        with self.assertRaisesRegex(ValueError, "include requires verified_primary"):
            MODULE.apply_decisions(review, decisions)


if __name__ == "__main__":
    unittest.main()
