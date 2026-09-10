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
        self.assertIn('data-view-target="ideas">视觉灵感库', self.html)
        self.assertIn('data-platform-view="ideas"', self.html)

    def test_interest_view_has_dedicated_copy(self):
        self.assertIn("关注方向与匹配内容", self.html)
        self.assertIn('id="channelDescription"', self.html)

    def test_core_event_contract_is_used_end_to_end(self):
        self.assertIn('article_type === \'core_event\'', self.html)
        self.assertIn('bundle.issue.core_event_count', self.html)
        self.assertIn('id="coreEventIndex"', self.html)
        self.assertIn("industry_signal:'行业信号'", self.html)
        self.assertNotIn('id="industrySignalIndex"', self.html)

    def test_primary_navigation_is_limited_to_four_product_destinations(self):
        expected = {
            'overview': '情报总览',
            'selection': '情报文章',
            'interests': '我的关注',
            'ideas': '视觉灵感库',
        }
        for target, label in expected.items():
            self.assertIn(f'data-view-target="{target}">{label}', self.html)
        self.assertEqual(self.html.count('class="nav-item'), 4)
        self.assertNotIn('class="utility-nav"', self.html)

    def test_overview_remains_the_default_and_keeps_dashboard_sections(self):
        self.assertIn('data-view-target="overview">情报总览', self.html)
        self.assertIn('data-platform-view="overview"', self.html)
        self.assertIn("showView('overview', false);", self.html)

    def test_visual_topics_remain_available_as_article_filters(self):
        for label in ("视频生成", "世界模型", "具身智能", "评测与标准", "空间与4D"):
            self.assertIn(label, self.html)
        self.assertNotIn('data-view-target="model_frontier"', self.html)

    def test_article_view_exposes_only_high_value_direct_buttons(self):
        for label in ("全部", "行业与市场", "产品与公司", "技术突破", "视觉AI核心", "外围AI观察"):
            self.assertIn(label, self.html)
        self.assertIn('id="channelFieldBar"', self.html)
        self.assertIn('data-channel-field=', self.html)
        self.assertIn('aria-pressed="${channelFieldFilter === value}"', self.html)

    def test_active_navigation_exposes_current_page_semantics(self):
        self.assertIn("item.setAttribute('aria-current', 'page')", self.html)
        self.assertIn("item.removeAttribute('aria-current')", self.html)

    def test_legacy_taxonomy_is_collapsed_into_one_filter_entry(self):
        self.assertIn('>筛选 <b id="advancedFilterCount"', self.html)
        self.assertNotIn('class="taxonomy-note"', self.html)
        self.assertLess(self.html.index('id="advancedFilterPanel"'), self.html.index('id="primaryFilters"'))

    def test_core_is_image_led_and_internal_signals_share_the_same_section(self):
        self.assertIn("if (story.editorial_tier === 'brief') return '';", self.html)
        self.assertIn("story.article_type === 'core_event' || story.editorial_tier === 'industry_signal'", self.html)
        self.assertNotIn('<h3>行业信号</h3>', self.html)
        self.assertIn("if (story.editorial_tier === 'brief') return;", self.html)
        self.assertIn("story.editorial_tier === 'brief' ? '来源短读'", self.html)
        self.assertIn("const deepRead = sourceBrief ? ''", self.html)
        self.assertIn("const reading = sourceBrief ? '来源短读'", self.html)
        validator = (ROOT / 'scripts/validate-editorial-issue.py').read_text()
        self.assertIn('"source_brief" if story.get("editorial_tier") == "brief"', validator)

    def test_priority_zero_has_an_explicit_empty_state(self):
        self.assertIn("本期无 P0", self.html)


if __name__ == "__main__":
    unittest.main()
