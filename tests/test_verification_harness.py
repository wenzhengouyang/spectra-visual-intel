import unittest

from spectra_agent.run import attach_harness_evidence
from verification.verification_harness import (
    build_artifacts,
    claim_review,
    numbers_match,
    validate_artifacts,
)


class VerificationHarnessTest(unittest.TestCase):
    def test_locates_evidence_without_approving_claim(self):
        sources = [{
            "source_id": "src_1", "canonical_url": "https://example.com/1",
            "raw_text": "公司公告称，模型包含20个场景，并计划下月开放测试。\n其他说明。",
        }]
        result = claim_review("公司公告称模型包含20个场景。", sources, requires_attribution=True)
        self.assertIn("20个场景", result["evidence_text"])
        self.assertTrue(result["numeric_match"])
        self.assertTrue(result["attribution_preserved"])
        self.assertEqual(result["verification_status"], "pending_human_review")

    def test_numeric_mismatch_and_missing_attribution_are_flagged(self):
        sources = [{
            "source_id": "src_1", "canonical_url": "https://example.com/1",
            "raw_text": "作者表示，样本包含20个场景。",
        }]
        result = claim_review("样本包含30个场景。", sources, requires_attribution=True)
        self.assertIn("numeric_mismatch", result["risk_flags"])
        self.assertIn("attribution_missing", result["risk_flags"])

    def test_normalizes_bilingual_currency_units(self):
        self.assertTrue(numbers_match("融资估值为60亿美元", "raise at a $6 billion pre-money valuation"))
        self.assertTrue(numbers_match("涨幅629.44%，市值4449亿元", "涨幅 629.44%，总市值达到 4449 亿元"))
        self.assertFalse(numbers_match("融资估值为70亿美元", "raise at a $6 billion pre-money valuation"))
        self.assertTrue(numbers_match("Python 3 Begins", "Python 3迁移开始"))
        self.assertTrue(numbers_match("A 48-minute video walkthrough", "一段48分钟的视频讲解"))

    def test_cross_language_claim_locates_relevant_evidence(self):
        sources = [{
            "source_id": "src_en", "canonical_url": "https://example.com/en",
            "raw_text": (
                "Background information unrelated to the product. "
                "LeFlow recasts planning as conditional latent trajectory generation and achieves "
                "success-rate gains with an order-of-magnitude reduction in planning time."
            ),
        }]
        result = claim_review(
            "LeFlow将规划转化为条件潜在轨迹生成，并减少一个数量级的规划时间。",
            sources,
            requires_attribution=False,
        )
        self.assertIn("conditional latent trajectory generation", result["evidence_text"])
        self.assertNotEqual(result["support_status"], "unsupported")

    def test_long_text_reranks_claim_specific_passage(self):
        filler = "This paragraph discusses unrelated company history, customers, and offices. " * 20
        sources = [{
            "source_id": "src_long", "canonical_url": "https://example.com/long",
            "raw_text": filler + "; Distribution-matching distillation compresses sampling to four steps and supports real-time interaction.",
        }]
        result = claim_review("分布匹配蒸馏把采样压缩至四步并支持实时交互。", sources, False)
        self.assertIn("four steps", result["evidence_text"])
        self.assertIn("real-time", result["evidence_text"])

    def test_generic_test_word_does_not_create_false_bilingual_support(self):
        sources = [{
            "source_id": "src_weak", "canonical_url": "https://example.com/weak",
            "raw_text": "未来数据来自编程、仿真、测试和评价，这些环节都会产生数据。",
        }]
        result = claim_review(
            "系统自动获取论文、生成代码、完成仿真训练与真机测试闭环。",
            sources,
            requires_attribution=False,
        )
        self.assertNotEqual(result["support_status"], "supported")

    def test_build_artifacts_stays_behind_human_gate(self):
        collection = {"source_records": [{
            "source_id": "src_1", "canonical_url": "https://example.com/1",
            "raw_text": "文章称该产品提供3D白模预演功能。",
        }]}
        candidates = {"selected_candidates": [{
            "candidate_id": "cand_1", "canonical_title": "3D白模预演",
            "intelligence_type": "type.product_release", "primary_route": "frontier.video_generation",
            "source_ids": ["src_1"],
            "hard_gates": {
                "content_completeness": {"status": "pass"},
                "fact_wording_fidelity": {"status": "pass", "requires_attribution": True},
            },
            "llm_analysis": {
                "canonical_title": "文章展示3D白模预演功能",
                "what": "文章称该产品提供3D白模预演功能。",
                "why": "可能改善视频创作流程。",
                "proposed_claims": ["文章称该产品提供3D白模预演功能。"],
            },
        }]}
        review = {"run_id": "test", "records": [{
            "candidate_id": "cand_1", "source_id": "src_1",
            "verification_status": "pending", "claims": [], "decision": "pending",
        }]}
        provisional, evidence = build_artifacts(collection, candidates, review)
        validate_artifacts(provisional, evidence)
        self.assertEqual(provisional["verification_candidates"][0]["status"], "provisional_unverified")
        self.assertIsNone(provisional["verification_candidates"][0]["human_decision"])
        attached = attach_harness_evidence(review, evidence)
        self.assertEqual(attached["records"][0]["claims"], [])
        self.assertEqual(attached["records"][0]["decision"], "pending")
        self.assertTrue(attached["records"][0]["suggested_evidence"])


if __name__ == "__main__":
    unittest.main()
