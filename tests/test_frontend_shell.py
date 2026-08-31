import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class FrontendShellTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT / "index.html").read_text(encoding="utf-8")
        cls.editorial_css = (ROOT / "app" / "hallmark-editorial.css").read_text(encoding="utf-8")

    def test_reader_uses_blog_summary_structure(self):
        self.assertIn("事件梳理", self.html)
        self.assertIn("summary_paragraphs", self.html)
        self.assertIn("summary-copy", self.html)
        self.assertNotIn("核心问题与事实", self.html)
        self.assertNotIn("fact-answer-list", self.html)
        self.assertNotIn("<b>情报正文</b>", self.html)

    def test_blog_summary_copy_is_isolated_from_legacy_field_layout(self):
        self.assertIn(".article-prose.blog-summary > .summary-copy", self.editorial_css)
        self.assertIn("display: block;", self.editorial_css)

    def test_ideas_are_signal_derived_and_have_no_fake_library_link(self):
        self.assertIn('id="ideaGrid"', self.html)
        self.assertIn("function renderIdeas()", self.html)
        self.assertNotIn("进入灵感库 ↗", self.html)

    def test_interest_view_has_dedicated_copy(self):
        self.assertIn("关注方向与匹配情报", self.html)
        self.assertIn('id="channelDescription"', self.html)


if __name__ == "__main__":
    unittest.main()
