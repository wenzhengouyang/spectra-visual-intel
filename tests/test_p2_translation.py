import unittest

from processor.p2_localizer import (
    apply_translation,
    fields_needing_translation,
    fact_risk_differences,
    quarantine_failed_briefs,
    restore_reviewed_localizations,
    requires_human_localization_review,
    validate_localized_brief,
    validate_translation,
)


class P2TranslationTests(unittest.TestCase):
    def test_huggingface_brand_is_allowed_in_chinese_headline(self):
        from processor.language_quality import has_readable_chinese

        self.assertTrue(has_readable_chinese(
            "huggingface 提供了 CyberGym 安全基准测试工具",
            "headline",
        ))

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

    def test_html_nonbreaking_space_entity_is_not_a_number(self):
        validate_translation(
            "Amazon says it will&#160;stop using NDAs with local governments.",
            "亚马逊表示将停止在与地方政府谈判时使用保密协议。",
            "dek",
        )

    def test_equivalent_bilingual_currency_is_preserved(self):
        validate_translation(
            "Robotics startup reaches $3B valuation, sources say",
            "据消息人士称，这家机器人初创公司估值达到30亿美元",
            "headline",
        )

    def test_lost_attribution_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_translation("Company says Model 5 wins", "Model 5已经获胜", "headline")

    def test_lost_uncertainty_is_rejected_even_when_company_is_present(self):
        with self.assertRaises(ValueError):
            validate_translation("Company could release a model", "公司发布了模型", "headline")

    def test_uncertainty_is_preserved(self):
        validate_translation("Company could release a model", "公司可能发布模型", "headline")

    def test_month_may_is_not_uncertainty(self):
        validate_translation("Announced at I/O in May", "于5月在I/O大会上公布", "headline")

    def test_english_ordinal_date_matches_chinese_date(self):
        validate_translation(
            "SF October 14th: A Birds of a Feather Session on Agentic Engineering",
            "10月14日旧金山：关于代理工程的交流会",
            "headline",
        )

    def test_bare_english_year_may_gain_chinese_year_unit(self):
        validate_translation("2026 Interim Report", "2026年中期报告", "headline")

    def test_english_number_word_matches_arabic_chinese_count(self):
        validate_translation(
            "Twenty-five leading mathematicians signed an open letter",
            "25位顶尖数学家签署了一封公开信",
            "headline",
        )

    def test_english_number_word_matches_chinese_cardinal_count(self):
        validate_translation(
            "Twenty-five leading mathematicians signed an open letter",
            "二十五位顶尖数学家签署了一封公开信",
            "headline",
        )

    def test_english_number_matches_chinese_ordinal(self):
        validate_translation("Muse is now the No. 2 app", "Muse现已成为第二大应用", "headline")

    def test_implicit_english_quantity_can_use_natural_chinese_classifier(self):
        validate_translation(
            "Glass Imaging was founded by a pair of former Apple engineers.",
            "Glass Imaging由两位前苹果工程师创立。",
            "dek",
        )
        validate_translation(
            "The hotline is a discreet place for agents.",
            "这条热线为智能体提供一个隐秘渠道。",
            "dek",
        )
        validate_translation(
            "Join the Builders Stage at TechCrunch Disrupt 2026.",
            "不要错过TechCrunch Disrupt 2026 Builders Stage上的一场互动环节。",
            "dek",
        )

    def test_one_of_ranking_is_not_treated_as_literal_count(self):
        validate_translation(
            "Data centers could become one of the largest consumers.",
            "数据中心可能成为全球最大的消费者之一。",
            "dek",
        )

    def test_anaphoric_one_is_not_treated_as_a_count(self):
        validate_translation(
            "This can serve as a shared training primitive, one that models can reuse.",
            "这可以作为可被模型复用的共享训练基础。",
            "dek",
        )

    def test_dimensionality_and_demonstrative_are_not_numeric_facts(self):
        validate_translation(
            "UAVs extend embodied intelligence into continuous three-dimensional space.",
            "无人机将具身智能扩展到连续的三维空间中。",
            "dek",
        )
        validate_translation(
            "This one here is my favorite.",
            "其中我最喜欢这一份。",
            "dek",
        )

    def test_spelled_out_months_and_reports_match_chinese_units(self):
        validate_translation(
            "OpenAI provided six reports observed in the last six months.",
            "OpenAI提供了过去六个月中观察到的六份报告。",
            "dek",
        )

    def test_quantization_and_deployment_use_compute_cover_motif(self):
        from spectra_agent.publication_quality import expected_cover_motif

        self.assertEqual(expected_cover_motif("量化可能导致模型部署性能下降", ""), "compute")

    def test_existing_localized_fact_error_is_repaired_again(self):
        brief = {
            "headline": "公司发布了Model 5",
            "original_headline": "Company could release Model 5",
            "dek": "公司介绍了产品进展。",
        }
        self.assertIn("headline", fields_needing_translation(brief))

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

    def test_resume_does_not_restore_copy_after_input_changes(self):
        issue = {"news_briefs": [{"brief_id": "same", "headline": "New report", "dek": "New facts"}]}
        cached = {"news_briefs": [{"brief_id": "same", "headline": "旧译文", "dek": "旧事实。",
            "original_headline": "Old report", "original_dek": "Old facts",
            "localization_status": "machine_localized_validated", "localized_fields": ["headline", "dek"]}]}
        self.assertEqual(restore_reviewed_localizations(issue, cached, None)["restored"], 0)
        self.assertEqual(issue["news_briefs"][0]["headline"], "New report")

    def test_only_factual_translation_risks_require_human_review(self):
        self.assertTrue(requires_human_localization_review(["headline translation lost, changed or added numbers/units"] ))
        self.assertTrue(requires_human_localization_review(["dek translation lost attribution or uncertainty wording"] ))
        self.assertFalse(requires_human_localization_review(["headline_not_readable_chinese"] ))

    def test_chinese_classifier_kind_preserves_two_approaches(self):
        validate_translation(
            "Planning relies on one of two costly approaches.",
            "进行规划通常依赖于两种成本较高的方法之一。",
            "dek",
        )

    def test_one_step_matches_chinese_step_classifier(self):
        validate_translation(
            "DIDO: One-Step Denoising for World Action Models",
            "DIDO：一步去噪以构建世界动作模型",
            "headline",
        )

    def test_review_difference_only_contains_fact_risks(self):
        record = {
            "original_headline": "Company could release Model 5",
            "candidate_headline_zh": "公司发布Model 5",
            "errors": [
                "headline translation lost attribution or uncertainty wording",
                "headline_not_readable_chinese",
            ],
        }
        risks = fact_risk_differences(record)
        self.assertEqual(len(risks), 1)
        self.assertEqual(risks[0]["risk_type"], "attribution_or_uncertainty")


if __name__ == "__main__":
    unittest.main()
