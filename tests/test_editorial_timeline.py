import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "editorial" / "build-editorial-issue.py"
SPEC = importlib.util.spec_from_file_location("build_editorial_issue", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class EditorialTimelineTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
