import unittest

from processor.p2_localizer import (
    apply_translation,
    fields_needing_translation,
    quarantine_failed_briefs,
    restore_reviewed_localizations,
    validate_localized_brief,
    validate_translation,
)


class P2TranslationTests(unittest.TestCase):
    def test_only_non_chinese_fields_are_targeted(self):
        brief = {"headline": "OpenAI releases Model 5", "dek": "OpenAI发布Model 5。"}
        self.assertEqual(fields_needing_translation(brief), ["headline", "dek"])

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

    def test_added_number_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_translation("Model improves scores", "模型得分提高20%", "headline")

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

    def test_bare_english_year_may_gain_chinese_year_unit(self):
        validate_translation("2026 Interim Report", "2026年中期报告", "headline")

    def test_english_sentence_with_chinese_decoration_is_rejected(self):
        errors = validate_localized_brief({
            "headline": "Anthropic and OpenAI are joining 人工智能",
            "dek": "这是一条完成中文转述的短讯摘要。",
        })
        self.assertIn("headline_not_readable_chinese", errors)

    def test_failed_brief_is_removed_and_enters_review_queue(self):
        issue = {
            "issue": {
                "news_brief_ids": ["brief_ok", "brief_bad"],
                "brief_count": 2,
                "total_intelligence_count": 3,
            },
            "editorial_stories": [{"story_id": "story_1"}],
            "news_briefs": [{"brief_id": "brief_ok"}, {"brief_id": "brief_bad"}],
            "presentation": {"timeline_days": [{"brief_ids": ["brief_ok", "brief_bad"]}]},
        }
        queue = quarantine_failed_briefs(issue, [{
            "brief_id": "brief_bad", "queue_id": "p2_localization_brief_bad",
        }])
        self.assertEqual([item["brief_id"] for item in issue["news_briefs"]], ["brief_ok"])
        self.assertEqual(issue["issue"]["brief_count"], 1)
        self.assertEqual(issue["issue"]["total_intelligence_count"], 2)
        self.assertEqual(issue["presentation"]["timeline_days"][0]["brief_ids"], ["brief_ok"])
        self.assertEqual(queue["status"], "waiting_for_review")

    def test_resume_restores_valid_copy_and_applies_supplied_review_copy(self):
        issue = {"news_briefs": [
            {"brief_id": "cached", "headline": "Cached report", "dek": "Cached body"},
            {"brief_id": "fixed", "headline": "2026 Interim Report", "dek": "Revenue grew 70% in 2026"},
        ]}
        cached = {"news_briefs": [{
            "brief_id": "cached", "headline": "已缓存的报告", "dek": "已缓存的正文。",
            "original_headline": "Cached report", "original_dek": "Cached body",
            "localization_status": "machine_localized_validated",
            "localized_fields": ["headline", "dek"],
        }]}
        review = {"records": [{
            "brief_id": "fixed", "review_status": "approved",
            "decision": "supply_chinese_copy", "headline_zh": "2026年中期报告",
            "dek_zh": "报告称，2026年收入增长70%。",
        }]}
        result = restore_reviewed_localizations(issue, cached, review)
        self.assertEqual(result, {"restored": 1, "supplied": 1, "excluded": 0})
        self.assertEqual(issue["news_briefs"][0]["headline"], "已缓存的报告")
        self.assertEqual(issue["news_briefs"][1]["headline"], "2026年中期报告")


if __name__ == "__main__":
    unittest.main()
