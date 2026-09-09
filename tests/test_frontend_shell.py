import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "visual-intelligence-prototype.html").read_text(encoding="utf-8")
        cls.editorial_css = (ROOT / "app" / "hallmark-editorial.css").read_text(encoding="utf-8")

    def test_reader_uses_structured_editorial_analysis(self):
        for label in ("最新进展", "关键证据与架构", "为什么重要", "策略启示", "证据边界与来源"):
            self.assertIn(label, self.html)
        self.assertNotIn("核心问题与事实", self.html)
        self.assertNotIn("<b>情报正文</b>", self.html)

    def test_structured_analysis_is_isolated_from_legacy_field_layout(self):
        self.assertIn(".structured-analysis .article-section", self.editorial_css)
        self.assertIn("grid-template-columns: minmax(8.5rem, 0.34fr) minmax(0, 1fr);", self.editorial_css)

    def test_ideas_are_a_separate_utility_destination(self):
        self.assertIn('data-view-target="ideas">灵感库', self.html)
        self.assertIn('data-platform-view="ideas"', self.html)

    def test_interest_view_has_dedicated_copy(self):
        self.assertIn("关注方向与匹配内容", self.html)
        self.assertIn('id="channelDescription"', self.html)

    def test_core_event_contract_is_used_end_to_end(self):
        self.assertIn('article_type === \'core_event\'', self.html)
        self.assertIn('bundle.issue.core_event_count', self.html)
        self.assertIn('id="coreEventIndex"', self.html)

    def test_editorial_channels_replace_generic_intelligence_navigation(self):
        for label in ("本期精选", "能力与指标", "模型与前沿", "产品与商业", "内容与文化", "团队与人才"):
            self.assertIn(label, self.html)
        self.assertNotIn('>情报文章 ', self.html)

    def test_core_is_image_led_but_signals_and_briefs_are_not(self):
        self.assertIn("if (story.article_type !== 'core_event') return '';", self.html)
        self.assertIn('id="industrySignalIndex"', self.html)

    def test_priority_zero_has_an_explicit_empty_state(self):
        self.assertIn("本期无 P0", self.html)


if __name__ == "__main__":
    unittest.main()
