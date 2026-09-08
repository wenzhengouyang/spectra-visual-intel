import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "editorial" / "build-editorial-issue.py"
SPEC = importlib.util.spec_from_file_location("build_editorial_issue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class EditorialTimelineTest(unittest.TestCase):
    def test_builder_can_run_directly_outside_repository_cwd(self):
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), "--help"],
            cwd="/tmp",
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_future_issues_do_not_use_event_specific_manual_story_overrides(self):
        self.assertFalse(hasattr(MODULE, "BLOG_SUMMARY_OVERRIDES"))

    def test_only_readiness_qualified_writer_drafts_ship_as_core_events(self):
        selected = ["evt_ready", "evt_sparse", "evt_failed"]
        writer_drafts = {"evt_ready": {"event_id": "evt_ready"}}
        actual = MODULE.publication_core_event_ids(selected, writer_drafts, {"drafts": list(writer_drafts.values())})
        self.assertEqual(actual, ["evt_ready"])

    def test_english_event_title_falls_back_to_a_chinese_verified_claim(self):
        event = {"event_id": "evt_new", "canonical_title": "English only title"}
        review = {"agent_analysis": {"canonical_title": "Still English"}}
        claims = [
            {"text": "据来源报道，English claim only."},
            {"text": "该系统提示新增了不复现歌词和书籍段落的规则。"},
        ]
        headline = MODULE.neutral_fallback_headline(event, review, claims)
        self.assertIn("系统提示", headline)

    def test_cover_manifest_resolves_specific_asset_and_safe_fallback(self):
        manifest = {
            "covers": [{"event_id": "evt_1", "url": "assets/editorial/one.jpg", "kind": "editorial"}],
            "fallbacks": {"技术突破": "assets/editorial/technology.jpg", "default": "assets/editorial/default.jpg"},
        }

        specific = MODULE.resolve_cover_image(manifest, "evt_1", "type.technology_breakthrough")
        fallback = MODULE.resolve_cover_image(manifest, "evt_2", "type.technology_breakthrough")

        self.assertEqual(specific["url"], "assets/editorial/one.jpg")
        self.assertEqual(fallback["url"], "assets/editorial/technology.jpg")
        self.assertEqual(fallback["kind"], "editorial_fallback")

    def test_cover_manifest_reuses_official_asset_by_source_url_across_event_ids(self):
        manifest = {
            "covers": [{
                "event_id": "evt_old",
                "url": "https://cdn.example.com/legal.jpg",
                "kind": "official",
                "source_url": "https://example.com/legal",
            }],
            "fallbacks": {"产品与公司": "assets/editorial/product.jpg"},
        }

        cover = MODULE.resolve_cover_image(
            manifest,
            "evt_new",
            "type.product_release",
            source_url="https://example.com/legal",
        )

        self.assertEqual(cover["url"], "https://cdn.example.com/legal.jpg")
        self.assertEqual(cover["kind"], "official")

    def test_source_record_official_image_precedes_generated_cover(self):
        cover = MODULE.resolve_cover_image(
            {"covers": [], "fallbacks": {}},
            "evt_new",
            "type.product_release",
            source_url="https://example.com/article",
            official_image_url="https://cdn.example.com/official.jpg",
            headline="企业模型发布新版本",
            category="产品与公司",
            generated_dirs=[Path("/tmp/should-not-be-used")],
        )

        self.assertEqual(cover["url"], "https://cdn.example.com/official.jpg")
        self.assertEqual(cover["kind"], "official")
        self.assertEqual(cover["review_status"], "approved")

    def test_missing_official_cover_generates_topic_specific_svg(self):
        with tempfile.TemporaryDirectory() as directory:
            cover = MODULE.resolve_cover_image(
                {"covers": [], "fallbacks": {}},
                "evt_compute",
                "type.industry_market",
                source_url="https://example.com/compute",
                headline="从芯片到万卡集群",
                category="算力与数据",
                credit="已核验来源",
                generated_dirs=[Path(directory)],
            )
            svg = Path(directory) / Path(cover["url"]).name

            self.assertEqual(cover["kind"], "editorial_diagram")
            self.assertEqual(cover["review_status"], "pending")
            self.assertEqual(cover["semantic_match"], "passed")
            self.assertTrue(svg.exists())
            self.assertIn("从芯片到万卡集群", svg.read_text(encoding="utf-8"))

    def test_reader_facing_text_removes_audit_language_but_keeps_attribution(self):
        text = "按照王兴兴的判断，若闭环跑通，迭代速度可能提升；该表述属于预测而非已验证结果。"

        cleaned = MODULE.reader_facing_text(text)

        self.assertIn("按照王兴兴的判断", cleaned)
        self.assertIn("可能提升", cleaned)
        self.assertNotIn("预测而非已验证结果", cleaned)

    def test_p2_summary_does_not_copy_full_article(self):
        summary = MODULE.compact_brief_summary("Long source sentence. " * 80)
        self.assertLessEqual(len(summary), 361)
        self.assertTrue(summary.endswith((".", "…")))

    def test_p2_known_english_headline_uses_complete_chinese_copy(self):
        candidates = {
            "selected_candidates": [{
                "candidate_id": "cand_8054a194921a8a82",
                "canonical_title": "Overcooked? Why robotic pizza makers are failing",
                "primary_source_id": "src_pizza",
                "source_ids": ["src_pizza"],
                "published_at": "2026-08-28T00:00:00Z",
                "primary_route": "frontier.embodied_ai",
                "intelligence_type": "type.industry_market",
                "tags": {},
                "score": 13,
            }]
        }
        collection = {"source_records": [{
            "source_id": "src_pizza",
            "source_type": "media",
            "canonical_url": "https://example.com/pizza",
        }]}

        brief = MODULE.build_news_briefs(candidates, collection, set())[0]

        self.assertEqual(brief["headline"], "烤过头了？为何机器人披萨制作系统仍频频失败")
        self.assertNotIn("cooked", brief["headline"])

    def test_timeline_ends_on_collection_day_and_uses_shanghai_dates(self):
        events = [
            {
                "event_id": "evt_1",
                "event_at": "2026-08-17T17:59:11Z",
                "primary_route": "frontier.embodied_ai",
            }
        ]
        stories = {"evt_1": {"story_id": "story_1"}}

        timeline = MODULE.build_timeline(
            events,
            stories,
            "2026-08-20T02:04:20Z",
        )

        self.assertEqual([day["date"] for day in timeline], [
            "08.14", "08.15", "08.16", "08.17", "08.18", "08.19", "08.20"
        ])
        self.assertEqual(timeline[4]["event_ids"], ["evt_1"])
        self.assertNotIn("08.23", [day["date"] for day in timeline])

    def test_timeline_limits_rolling_window_to_seven_local_calendar_dates(self):
        events = [{
            "event_id": "evt_1", "event_at": "2026-08-25T12:00:00Z",
            "primary_route": "extended.foundation_multimodal",
        }]
        timeline = MODULE.build_timeline(
            events, {"evt_1": {"story_id": "story_1"}},
            "2026-09-01T00:00:00Z", "2026-08-25T00:00:00Z",
        )
        self.assertEqual([day["date"] for day in timeline], [
            "08.26", "08.27", "08.28", "08.29", "08.30", "08.31", "09.01"
        ])
        self.assertNotIn("story_1", [story_id for day in timeline for story_id in day["story_ids"]])

    def test_explicit_rolling_start_does_not_expand_briefs_to_eight_dates(self):
        candidates = {
            "selected_candidates": [
                {
                    "candidate_id": "cand_boundary",
                    "canonical_title": "Partial eighth date",
                    "primary_source_id": "src_1",
                    "source_ids": ["src_1"],
                    "published_at": "2026-08-25T12:00:00Z",
                    "primary_route": "extended.ai_agent_tools",
                    "intelligence_type": "type.product_release",
                    "tags": {},
                    "score": 10,
                }
            ]
        }
        collection = {"source_records": [{
            "source_id": "src_1", "source_type": "professional_view",
            "canonical_url": "https://example.com", "raw_excerpt": "Excerpt",
        }]}
        briefs = MODULE.build_news_briefs(
            candidates, collection, set(),
            "2026-09-01T00:00:00Z", "2026-08-25T00:00:00Z",
        )
        self.assertEqual(briefs, [])

    def test_p2_briefs_exclude_human_reviewed_candidates_and_keep_accuracy_boundary(self):
        candidates = {
            "selected_candidates": [
                {
                    "candidate_id": "cand_pending",
                    "canonical_title": "A pending paper",
                    "primary_source_id": "src_1",
                    "source_ids": ["src_1"],
                    "published_at": "2026-08-19T08:00:00Z",
                    "verification_priority": "priority.p2",
                    "primary_route": "frontier.world_model",
                    "intelligence_type": "type.technology_breakthrough",
                    "tags": {},
                    "score": 12,
                    "llm_analysis": {"what": "来源摘要中的待核验说明。"},
                },
                {
                    "candidate_id": "cand_reviewed",
                    "canonical_title": "Already reviewed",
                    "primary_source_id": "src_1",
                    "source_ids": ["src_1"],
                    "published_at": "2026-08-19T08:00:00Z",
                    "verification_priority": "priority.p2",
                    "primary_route": "frontier.world_model",
                    "intelligence_type": "type.technology_breakthrough",
                    "tags": {},
                    "score": 12,
                },
            ]
        }
        collection = {
            "source_records": [{
                "source_id": "src_1",
                "source_type": "paper_report",
                "canonical_url": "https://arxiv.org/abs/example",
                "raw_excerpt": "Abstract",
            }]
        }

        briefs = MODULE.build_news_briefs(candidates, collection, {"cand_reviewed"})

        self.assertEqual(len(briefs), 1)
        self.assertEqual(briefs[0]["candidate_id"], "cand_pending")
        self.assertEqual(briefs[0]["priority"], "priority.p2")
        self.assertEqual(briefs[0]["verification_status"], "abstract_checked")
        self.assertIn("尚未完成P1级原文核验", briefs[0]["accuracy_note"])

    def test_p2_briefs_exclude_rolling_window_spillover_before_seven_day_timeline(self):
        candidates = {
            "selected_candidates": [
                {
                    "candidate_id": "cand_spillover",
                    "canonical_title": "Outside local calendar window",
                    "primary_source_id": "src_1",
                    "source_ids": ["src_1"],
                    "published_at": "2026-08-17T15:03:16Z",
                    "primary_route": "extended.ai_agent_tools",
                    "intelligence_type": "type.product_release",
                    "tags": {},
                    "score": 10,
                },
                {
                    "candidate_id": "cand_inside",
                    "canonical_title": "Inside local calendar window",
                    "primary_source_id": "src_1",
                    "source_ids": ["src_1"],
                    "published_at": "2026-08-18T00:03:16Z",
                    "primary_route": "extended.ai_agent_tools",
                    "intelligence_type": "type.product_release",
                    "tags": {},
                    "score": 10,
                },
            ]
        }
        collection = {
            "source_records": [{
                "source_id": "src_1",
                "source_type": "professional_view",
                "canonical_url": "https://example.com/source",
                "raw_excerpt": "Excerpt",
            }]
        }

        briefs = MODULE.build_news_briefs(
            candidates,
            collection,
            set(),
            "2026-08-24T01:57:01Z",
        )

        self.assertEqual([brief["candidate_id"] for brief in briefs], ["cand_inside"])

    def test_p2_briefs_can_use_current_week_display_window(self):
        candidates = {
            "window_start": "2026-08-27T02:00:00Z",
            "selected_candidates": [
                {
                    "candidate_id": "cand_prior_week",
                    "canonical_title": "Prior week context",
                    "primary_source_id": "src_1",
                    "source_ids": ["src_1"],
                    "published_at": "2026-08-30T08:00:00Z",
                    "primary_route": "frontier.world_model",
                    "intelligence_type": "type.technology_breakthrough",
                    "tags": {},
                    "score": 12,
                },
                {
                    "candidate_id": "cand_this_week",
                    "canonical_title": "This week",
                    "primary_source_id": "src_1",
                    "source_ids": ["src_1"],
                    "published_at": "2026-09-01T08:00:00Z",
                    "primary_route": "frontier.world_model",
                    "intelligence_type": "type.technology_breakthrough",
                    "tags": {},
                    "score": 12,
                },
            ],
        }
        collection = {"source_records": [{
            "source_id": "src_1",
            "source_type": "paper_report",
            "canonical_url": "https://example.com",
            "raw_excerpt": "Abstract",
        }]}

        briefs = MODULE.build_news_briefs(
            candidates,
            collection,
            set(),
            "2026-09-03T02:00:00Z",
            "2026-08-31T16:00:00Z",
        )

        self.assertEqual([item["candidate_id"] for item in briefs], ["cand_this_week"])


if __name__ == "__main__":
    unittest.main()
