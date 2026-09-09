import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spectra_agent.progress_server import build_status, latest_run_path


class ProgressServerTest(unittest.TestCase):
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
            self.assertEqual(result["stages"][2]["state"], "current")


if __name__ == "__main__":
    unittest.main()
