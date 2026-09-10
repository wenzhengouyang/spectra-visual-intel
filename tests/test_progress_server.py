import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spectra_agent.progress_server import build_status, latest_run_path, safe_run_artifact


class ProgressServerTest(unittest.TestCase):
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
