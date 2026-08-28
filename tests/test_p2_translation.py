import unittest

from processor.translate_p2 import apply_translation, fields_needing_translation, validate_translation


class P2TranslationTests(unittest.TestCase):
    def test_only_non_chinese_fields_are_targeted(self):
        brief = {"headline": "OpenAI releases Model 5", "dek": "OpenAI发布Model 5。"}
        self.assertEqual(fields_needing_translation(brief), ["headline"])

    def test_translation_preserves_source_and_review_status(self):
        brief = {
            "headline": "Model 5 improves scores by 20%",
            "dek": "已有中文摘要。",
            "verification_status": "secondary_source_pending",
        }
        apply_translation(brief, {"headline_zh": "Model 5将得分提高20%", "dek_zh": brief["dek"]})
        self.assertEqual(brief["original_headline"], "Model 5 improves scores by 20%")
        self.assertEqual(brief["headline"], "Model 5将得分提高20%")
        self.assertEqual(brief["verification_status"], "secondary_source_pending")

    def test_missing_number_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_translation("Model 5 improves 20%", "模型表现有所提升", "headline")

    def test_equivalent_bilingual_currency_is_preserved(self):
        validate_translation(
            "Robotics startup reaches $3B valuation, sources say",
            "据消息人士称，这家机器人初创公司估值达到30亿美元",
            "headline",
        )

    def test_lost_attribution_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_translation("Company says Model 5 wins", "Model 5已经获胜", "headline")

    def test_month_may_is_not_uncertainty(self):
        validate_translation("Announced at I/O in May", "于5月在I/O大会上公布", "headline")


if __name__ == "__main__":
    unittest.main()
