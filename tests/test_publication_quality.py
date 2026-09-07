import json
import tempfile
import unittest
from pathlib import Path

from spectra_agent.publication_quality import publication_quality_errors


def sample_issue() -> dict:
    body = "据官方介绍，产品已经开放测试，并提供了模型接口和权限控制。" * 24
    return {
        "editorial_stories": [{
            "story_id": "story_1", "article_type": "core_event",
            "headline": "新模型开放企业测试", "dek": "官方介绍了企业测试范围与当前产品边界。",
            "category": "产品与公司",
            "article_body": {"full_text": {"text": body}},
            "cover_image": {
                "url": "assets/cover.svg", "kind": "editorial_diagram",
                "semantic_motif": "signal", "semantic_match": "passed", "review_status": "approved",
            },
        }],
        "news_briefs": [],
    }


class PublicationQualityTest(unittest.TestCase):
    def setUp(self):
        self.config = {"publication_quality": {"minimum_core_events": 1, "core_event_min_characters": 0, "require_generated_image_review": True}}

    def test_valid_issue_passes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "assets").mkdir()
            (root / "assets/cover.svg").write_text("unique")
            self.assertEqual(publication_quality_errors(sample_issue(), self.config, asset_root=root), [])

    def test_unreviewed_generated_cover_blocks(self):
        issue = sample_issue()
        issue["editorial_stories"][0]["cover_image"]["review_status"] = "pending"
        with tempfile.TemporaryDirectory() as temp:
            errors = publication_quality_errors(issue, self.config, asset_root=Path(temp))
        self.assertTrue(any("needs_human_preview" in error for error in errors))

    def test_english_core_event_blocks_without_length_minimum(self):
        issue = sample_issue()
        story = issue["editorial_stories"][0]
        story["article_body"]["full_text"]["text"] = "The model is released for enterprise users."
        with tempfile.TemporaryDirectory() as temp:
            errors = publication_quality_errors(issue, self.config, asset_root=Path(temp))
        self.assertFalse(any("core_event_too_short" in error for error in errors))
        self.assertTrue(any("body_" in error for error in errors))

    def test_short_supported_chinese_story_is_not_blocked_by_length(self):
        issue = sample_issue()
        issue["editorial_stories"][0]["article_body"]["full_text"]["text"] = (
            "据官方介绍，产品已经开放测试，并提供模型接口和权限控制。"
        )
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "assets").mkdir()
            (root / "assets/cover.svg").write_text("unique")
            errors = publication_quality_errors(issue, self.config, asset_root=root)
        self.assertFalse(any("core_event_too_short" in error for error in errors))

    def test_legacy_quality_config_names_remain_readable(self):
        legacy_config = {"publication_quality": {
            "minimum_deep_stories": 1,
            "core_event_min_characters": 1,
            "require_generated_image_review": True,
        }}
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "assets").mkdir()
            (root / "assets/cover.svg").write_text("unique")
            self.assertEqual(
                publication_quality_errors(sample_issue(), legacy_config, asset_root=root),
                [],
            )


if __name__ == "__main__":
    unittest.main()
