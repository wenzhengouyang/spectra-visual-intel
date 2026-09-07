import unittest

from processor.language_quality import chinese_completeness, has_readable_chinese, reader_language_errors


class LanguageQualityTest(unittest.TestCase):
    def test_product_names_can_remain_english(self):
        title = "Google发布Gemini Enterprise法律行业版"
        self.assertTrue(has_readable_chinese(title, "headline"))

    def test_english_sentence_with_chinese_decoration_fails(self):
        title = "新模型 OpenAI launches a powerful model for enterprise users"
        self.assertFalse(has_readable_chinese(title, "headline"))
        self.assertTrue(reader_language_errors(title, "headline"))

    def test_chinese_body_with_model_names_passes(self):
        body = "据Google介绍，Gemini Enterprise面向法律团队提供文档连接与权限控制。产品仍处于预览阶段，实际效果需要后续案例和产品数据验证。"
        self.assertTrue(has_readable_chinese(body, "body"))
        self.assertGreater(chinese_completeness(body)["chinese_ratio"], 0.6)

    def test_full_english_body_fails(self):
        body = "The model is available for enterprise users and will improve document review workflows."
        self.assertFalse(has_readable_chinese(body, "body"))

    def test_truncated_headline_fails(self):
        self.assertIn("headline_truncated", reader_language_errors("企业模型发布新版本…", "headline"))


if __name__ == "__main__":
    unittest.main()
