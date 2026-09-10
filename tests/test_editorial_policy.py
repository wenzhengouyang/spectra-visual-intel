import unittest

from spectra_agent.editorial_policy import classify_story


def story(text: str, *, takeaway: str = "建议建立针对性回归测试。") -> dict:
    return {
        "headline": text,
        "dek": text,
        "one_line_takeaway": takeaway,
        "article_body": {"full_text": {"text": text * 30}},
        "under_the_hood": {"text": text},
    }


class EditorialPolicyTest(unittest.TestCase):
    def test_visual_paper_can_be_core_without_writer_draft(self):
        event = {"primary_route": "frontier.world_model", "secondary_routes": ["visual_value.evaluation"], "intelligence_type": "type.technology_breakthrough"}
        result = classify_story(story("相机条件世界模型评测动作一致性与视觉质量。"), event, source_type="paper_report", selected=True, writer_draft=False)
        self.assertEqual(result["editorial_tier"], "core_event")
        self.assertEqual(result["content_format"], "compact_analysis")
        self.assertEqual(result["editorial_channel"], "capability_metrics")

    def test_financial_story_without_reviewed_visual_fact_is_signal(self):
        event = {"primary_route": "frontier.video_generation", "secondary_routes": [], "intelligence_type": "type.industry_market"}
        result = classify_story(story("快手中期收入增长，净利润同比下降。"), event, source_type="financial_report", selected=True, writer_draft=False)
        self.assertEqual(result["visual_relevance"], "adjacent")
        self.assertEqual(result["editorial_tier"], "industry_signal")
        self.assertEqual(result["editorial_channel"], "product_business")
        self.assertEqual(result["content_format"], "signal_analysis")

    def test_financial_story_needs_six_fact_points_before_core_promotion(self):
        item = story("可灵视频生成业务完成资产重组并披露产品数据。")
        item["article_body"]["fact_points"] = [f"事实{i}" for i in range(5)]
        event = {"primary_route": "frontier.video_generation", "secondary_routes": [], "intelligence_type": "type.industry_market"}
        result = classify_story(item, event, source_type="financial_report", selected=True, writer_draft=False)
        self.assertEqual(result["editorial_tier"], "industry_signal")

        item["article_body"]["fact_points"].append("事实5")
        result = classify_story(item, event, source_type="financial_report", selected=True, writer_draft=False)
        self.assertEqual(result["editorial_tier"], "core_event")

    def test_secondary_image_report_is_not_promoted_to_core(self):
        event = {"primary_route": "extended.foundation_multimodal", "secondary_routes": ["frontier.image_asset"], "intelligence_type": "type.product_release"}
        result = classify_story(story("新图像生成模型改进参考图编辑与提示词遵循。"), event, source_type="professional_view", selected=True, writer_draft=False)
        self.assertEqual(result["visual_relevance"], "direct")
        self.assertEqual(result["editorial_tier"], "industry_signal")
        self.assertEqual(result["editorial_priority"], "priority.p1")

    def test_non_visual_company_story_is_formal_brief(self):
        event = {"primary_route": "extended.talent_organization", "secondary_routes": [], "intelligence_type": "type.company_strategy"}
        result = classify_story(story("部分商家篡改机器狗电池标签。"), event, source_type="wechat_official_account", selected=True, writer_draft=False)
        self.assertEqual(result["editorial_tier"], "brief")
        self.assertEqual(result["editorial_priority"], "priority.p3")
        self.assertEqual(result["content_format"], "source_brief")

    def test_no_urgency_means_no_forced_p0(self):
        event = {"primary_route": "frontier.video_generation", "secondary_routes": [], "intelligence_type": "type.technology_breakthrough"}
        result = classify_story(story("长程视频生成支持相机轨迹和世界一致性。"), event, source_type="paper_report", selected=True, writer_draft=True)
        self.assertEqual(result["editorial_priority"], "priority.p1")


if __name__ == "__main__":
    unittest.main()
