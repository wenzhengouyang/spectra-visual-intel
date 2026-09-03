import unittest

from spectra_agent.review_policy import apply_review_policy, assess_review_record


def fixtures(claim="产品发布了新版本。", sources=2, consistency="consistent"):
    review = {"run_id": "run_1", "records": [{"candidate_id": "c1", "suggested_evidence": [{
        "claim": claim, "support_status": "supported", "numeric_match": True, "risk_flags": [],
    }], "decision": "pending", "verification_status": "pending"}]}
    candidates = {"selected_candidates": [{
        "candidate_id": "c1", "source_ids": [f"s{i}" for i in range(sources)],
        "source_types": ["official_announcement"],
    }]}
    evidence = {"records": [{
        "candidate_id": "c1", "risk_flags": [],
        "cross_source_consistency": {"status": consistency, "independent_source_count": sources},
        "hard_rules": {"content_completeness": "pass", "fact_wording_fidelity": "pass", "source_present": True},
    }]}
    config = {"enabled": True, "minimum_consistent_sources": 2, "sample_rate": 0,
              "official_source_types": ["official_announcement"]}
    return review, candidates, evidence, config


class ReviewPolicyTest(unittest.TestCase):
    def test_single_source_is_mandatory(self):
        args = fixtures(sources=1, consistency="not_applicable")
        result = apply_review_policy(*args)
        self.assertEqual(result["records"][0]["review_policy"]["tier"], "mandatory_review")
        self.assertIn("single_source", result["records"][0]["review_policy"]["mandatory_reasons"])

    def test_numeric_or_effect_claim_is_mandatory(self):
        args = fixtures(claim="官方称效率提升了 20%。")
        result = apply_review_policy(*args)
        reasons = result["records"][0]["review_policy"]["mandatory_reasons"]
        self.assertIn("numeric_claim", reasons)
        self.assertIn("effect_or_causal_claim", reasons)

    def test_safe_multi_source_official_fact_is_auto_locked(self):
        result = apply_review_policy(*fixtures())
        record = result["records"][0]
        self.assertEqual(record["review_policy"]["tier"], "auto_locked")
        self.assertEqual(record["decision"], "include")
        self.assertEqual(record["suggested_evidence"][0]["human_fact_decision"], "keep")

    def test_sampled_auto_eligible_fact_stays_for_review(self):
        args = list(fixtures())
        args[3]["sample_rate"] = 1
        result = apply_review_policy(*args)
        self.assertEqual(result["records"][0]["review_policy"]["tier"], "sample_review")
        self.assertEqual(result["records"][0]["decision"], "pending")

    def test_human_decision_is_never_overwritten(self):
        review, candidates, evidence, config = fixtures()
        review["records"][0]["decision"] = "watch"
        result = apply_review_policy(review, candidates, evidence, config)
        self.assertEqual(result["records"][0]["decision"], "watch")

    def test_configured_hard_and_soft_conditions_are_auditable(self):
        review, candidates, evidence, config = fixtures()
        config["auto_lock"] = {
            "enabled": True,
            "minimum_confidence": "high",
            "allowed_intelligence_types": ["type.product_release"],
            "allowed_source_types": ["official_announcement"],
        }
        candidates["selected_candidates"][0]["intelligence_type"] = "type.product_release"
        evidence["records"][0]["confidence"] = "medium"
        result = assess_review_record(
            review["records"][0], candidates["selected_candidates"][0], evidence["records"][0], config
        )
        self.assertTrue(result["hard_pass"])
        self.assertFalse(result["soft_pass"])
        self.assertIn("low_confidence", result["mandatory_reasons"])

    def test_run_human_gate_is_preserved_independently_of_auto_lock(self):
        review, candidates, evidence, config = fixtures()
        config["preserve_run_human_gate"] = True
        result = apply_review_policy(review, candidates, evidence, config)
        self.assertEqual(result["records"][0]["review_policy"]["tier"], "auto_locked")
        self.assertTrue(result["review_policy"]["run_gate_preserved"])


if __name__ == "__main__":
    unittest.main()
