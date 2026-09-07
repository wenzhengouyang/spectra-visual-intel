import json
import plistlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from spectra_agent.daily_runner import choose_action, daily_run_id
from spectra_agent.review_cli import decisions_for, parse_selection


class DailyRuntimeTest(unittest.TestCase):
    def test_launchd_schedules_collection_at_eight_and_dingtalk_at_ten(self):
        root = Path(__file__).resolve().parents[1]
        with (root / "spectra_agent/launchd/com.spectra.visual-intel.daily.plist").open("rb") as handle:
            collection = plistlib.load(handle)
        with (root / "spectra_agent/launchd/com.spectra.visual-intel.dingtalk.plist").open("rb") as handle:
            dingtalk = plistlib.load(handle)
        self.assertEqual(collection["StartCalendarInterval"], {"Hour": 8, "Minute": 0})
        self.assertNotIn("/Documents/", collection["WorkingDirectory"])
        self.assertIn("Application Support/SPECTRA/runtime", collection["WorkingDirectory"])
        self.assertEqual(dingtalk["StartCalendarInterval"], {"Hour": 10, "Minute": 0})

    def test_daily_run_id_uses_configured_local_date(self):
        now = datetime(2026, 9, 3, 23, 30, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(daily_run_id("Asia/Shanghai", now), "daily-20260904")

    def test_completed_and_human_gate_do_not_repeat_work(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "run.json").write_text(json.dumps({"status": "completed"}))
            self.assertEqual(choose_action(run_dir), "noop_completed")
            (run_dir / "run.json").write_text(json.dumps({"status": "waiting_for_review"}))
            self.assertEqual(choose_action(run_dir), "noop_human_gate")

    def test_failed_run_resumes_from_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "run.json").write_text(json.dumps({"status": "failed"}))
            self.assertEqual(choose_action(run_dir), "resume_retry")

    def test_compact_review_requires_complete_non_overlapping_selection(self):
        review = {"records": [
            {"candidate_id": "a", "suggested_evidence": [{"claim": "a"}]},
            {"candidate_id": "b", "suggested_evidence": [{"claim": "b"}]},
        ]}
        result = decisions_for(review, {1}, {2}, set(), "reviewer")
        self.assertEqual(result["records"]["a"]["decision"], "include")
        self.assertEqual(result["records"]["b"]["decision"], "watch")
        self.assertEqual(result["records"]["a"]["fact_decisions"], ["keep"])
        with self.assertRaises(ValueError):
            decisions_for(review, {1}, set(), set(), "reviewer")

    def test_selection_accepts_all_and_chinese_commas(self):
        self.assertEqual(parse_selection("all", 3), {1, 2, 3})
        self.assertEqual(parse_selection("1，3", 3), {1, 3})


if __name__ == "__main__":
    unittest.main()
