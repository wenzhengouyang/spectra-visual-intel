import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spectra_agent.progress_server import build_status, latest_run_path, no_update_streak, publication_panel, review_panel, safe_run_artifact


class ProgressServerTest(unittest.TestCase):
    def test_completed_run_exposes_publish_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "daily-20260917"
            run_dir.mkdir()
            panel = publication_panel(run_dir, {"run_id": run_dir.name, "status": "completed", "publish_status": "not_published"})
            self.assertEqual(panel["state"], "ready")
            self.assertTrue(panel["can_publish"])
            self.assertEqual(panel["button"], "发布到网页")

    def test_published_run_cannot_publish_again(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "daily-20260917"
            run_dir.mkdir()
            panel = publication_panel(run_dir, {"run_id": run_dir.name, "status": "completed", "publish_status": "published"})
            self.assertEqual(panel["state"], "published")
            self.assertFalse(panel["can_publish"])

    def test_remote_listener_requires_access_token(self):
        with patch("sys.argv", ["progress_server.py", "--host", "0.0.0.0"]):
            from spectra_agent.progress_server import main
            with self.assertRaises(SystemExit):
                main()

    def test_image_generation_wait_is_not_presented_as_human_review(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            (run_dir / "run.json").write_text(json.dumps({
                "status": "waiting_for_editorial_review", "current_stage": "image_generation",
                "publish_status": "not_published",
            }))
            with patch("spectra_agent.progress_server.latest_run_path", return_value=run_dir):
                result = build_status({})
            self.assertEqual(result["action"]["title"], "配图任务等待执行")
            self.assertIn("图像执行器没有接单", result["action"]["detail"])

    def test_no_update_is_explicit_and_keeps_previous_page(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            (run_dir / "run.json").write_text(json.dumps({
                "run_id": "daily-20260917", "status": "completed",
                "publish_status": "not_published", "publication_mode": "no_update",
                "no_update": True,
            }))
            with patch("spectra_agent.progress_server.latest_run_path", return_value=run_dir):
                result = build_status({})
            self.assertEqual(result["label"], "【今日无更新】")
            self.assertEqual(result["action"]["title"], "【今日无更新】")
            self.assertEqual(result["target"]["label"], "查看采集详情")

    def test_consecutive_no_update_becomes_collection_alert(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for day in ("16", "17"):
                run = root / f"daily-202609{day}"
                run.mkdir()
                (run / "run.json").write_text(json.dumps({
                    "run_id": run.name, "status": "completed", "publish_status": "not_published",
                    "publication_mode": "no_update",
                }))
            current = root / "daily-20260917"
            self.assertEqual(no_update_streak(current), 2)
            with patch("spectra_agent.progress_server.latest_run_path", return_value=current):
                result = build_status({})
            self.assertIn("采集异常告警", result["action"]["title"])

    def test_failed_evaluation_and_rejected_covers_are_not_done(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "run.json").write_text(json.dumps({"status": "completed"}))
            for evaluation, rejected, expected in [("fail", [], False), ("pass", ["a"], False), ("pass", [], True)]:
                (root / "eval-report.json").write_text(json.dumps({"status": evaluation}))
                (root / "image-review.json").write_text(json.dumps({"rejected_story_ids": rejected}))
                with patch("spectra_agent.progress_server.latest_run_path", return_value=root):
                    result = build_status({})
                self.assertEqual(result["stages"][4]["state"] == "done", expected)

    def test_report_styles_and_images_are_served_without_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / "app").mkdir()
            (run_dir / "assets" / "editorial").mkdir(parents=True)
            (run_dir / "app" / "hallmark-editorial.css").write_text("body{}")
            (run_dir / "app" / "share.js").write_text("void 0")
            (run_dir / "app" / "accepted-ui.css").write_text("body{}")
            (run_dir / "assets" / "editorial" / "cover.jpg").write_bytes(b"jpg")

            css = safe_run_artifact(run_dir, "app/hallmark-editorial.css")
            image = safe_run_artifact(run_dir, "assets/editorial/cover.jpg")

            self.assertEqual(css[1], "text/css; charset=utf-8")
            self.assertEqual(image[1], "image/jpeg")
            self.assertEqual(safe_run_artifact(run_dir, "app/share.js")[1], "text/javascript; charset=utf-8")
            self.assertIsNotNone(safe_run_artifact(run_dir, "app/accepted-ui.css"))
            self.assertIsNone(safe_run_artifact(run_dir, "../run.json"))
            self.assertIsNone(safe_run_artifact(run_dir, "run.json"))
    def test_latest_run_prefers_pointer(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "daily-20260908").mkdir()
            chosen = root / "daily-20260909"
            chosen.mkdir()
            (root / "latest.json").write_text(json.dumps({"run_id": chosen.name}))
            with patch("spectra_agent.progress_server.runs_dir", return_value=root):
                self.assertEqual(latest_run_path({}), chosen)

    def test_build_status_is_read_only_and_counts_terminal_writer_jobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "daily-20260909"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({
                "run_id": run_dir.name, "status": "running", "current_stage": "p1_editorial_background",
                "publish_status": "not_published", "reliability_status": "healthy", "updated_at": "2026-09-09T02:00:00Z",
            }))
            (run_dir / "p1-review.json").write_text(json.dumps({"review_status": "approved", "records": [{}, {}]}))
            (run_dir / "collection.json").write_text("{}")
            (run_dir / "coverage-line.json").write_text(json.dumps({"status": "running", "source_count": 18, "blocking_daily": False}))
            (run_dir / "core-event-checkpoint.json").write_text(json.dumps({"jobs": {
                "a": {"status": "completed"}, "b": {"status": "demoted_after_failed_long_story"}, "c": {"status": "running"},
            }}))
            with patch("spectra_agent.progress_server.latest_run_path", return_value=run_dir):
                result = build_status({})
            self.assertEqual(result["label"], "正在写核心事件")
            self.assertEqual(result["writer"], {"processed": 2, "total": 3, "passed": 1, "demoted": 1, "manual": 0})
            self.assertEqual(result["current_progress"]["label"], "核心事件已处理")
            self.assertEqual(result["overall_percent"], 62)
            self.assertEqual(result["stages"][2]["state"], "done")
            self.assertEqual(result["stages"][3]["state"], "current")
            self.assertEqual(result["collection_lanes"]["coverage"]["status"], "running")
            self.assertFalse(result["collection_lanes"]["coverage"]["blocking_daily"])

    def test_waiting_review_exposes_one_clear_action(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "daily-20260909"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"run_id": run_dir.name, "status": "waiting_for_review", "current_stage": "p1_review"}))
            (run_dir / "p1-review.json").write_text(json.dumps({"review_status": "pending", "records": [{}, {}, {}]}))
            with patch("spectra_agent.progress_server.latest_run_path", return_value=run_dir):
                result = build_status({})
            self.assertEqual(result["label"], "等待事实审核")
            self.assertEqual(result["action"]["title"], "需要你审核事实")
            self.assertEqual(result["target"]["url"], "/run-artifact/REVIEW.md")
            self.assertEqual(result["stages"][2]["state"], "current")
            self.assertEqual(result["review"]["type"], "fact")
            self.assertTrue(result["review"]["can_approve"])

    def test_localization_panel_only_exposes_structured_fact_risks(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            state = {"status": "waiting_for_editorial_review", "current_stage": "localization_review"}
            (run_dir / "p2-localization-review.json").write_text(json.dumps({"records": [{
                "brief_id": "brief_1", "original_headline": "Company could launch Model 5",
                "candidate_headline_zh": "公司可能发布Model 5", "candidate_dek_zh": "据报道，公司正在测试。",
                "fact_risks": [{"risk_type": "attribution_or_uncertainty", "field": "headline",
                                "source": "Company could launch Model 5", "candidate": "公司发布Model 5"}],
            }]}))
            panel = review_panel(run_dir, state)
            self.assertEqual(panel["type"], "localization")
            self.assertEqual(len(panel["items"][0]["facts"]), 1)
            self.assertTrue(panel["can_approve"])

    def test_image_panel_offers_one_action_for_all_pending_images(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            state = {"status": "waiting_for_editorial_review", "current_stage": "image_preview"}
            (run_dir / "editorial-issue.json").write_text(json.dumps({"editorial_stories": [{
                "story_id": "story_1", "headline": "图像模型更新", "article_type": "core_event",
                "cover_image": {"kind": "generated", "url": "assets/cover.jpg", "review_status": "pending"},
            }]}))
            panel = review_panel(run_dir, state)
            self.assertEqual(panel["type"], "image")
            self.assertEqual(panel["items"][0]["image_url"], "/run-asset/assets/cover.jpg")
            self.assertTrue(panel["can_approve"])

    def test_rejected_images_are_shown_as_rework(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "daily-20260909"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({"run_id": run_dir.name, "status": "waiting_for_editorial_review", "current_stage": "image_preview"}))
            (run_dir / "image-review.json").write_text(json.dumps({"rejected_story_ids": ["a", "b"], "rejection_reason": "主题不匹配"}))
            with patch("spectra_agent.progress_server.latest_run_path", return_value=run_dir):
                result = build_status({})
            self.assertEqual(result["label"], "图片需要返工")
            self.assertEqual(result["action"]["title"], "2 张图片已驳回")


if __name__ == "__main__":
    unittest.main()
