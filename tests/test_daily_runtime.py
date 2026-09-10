import json
import plistlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from spectra_agent.daily_runner import choose_action, daily_run_id
from spectra_agent.review_cli import decisions_for, interactive_decisions, parse_selection


class DailyRuntimeTest(unittest.TestCase):
    def test_installer_preserves_bounded_morning_schedule(self):
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("morning_schedule", root / "scripts/install-morning-schedule.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.PLAN["com.spectra.visual-intel.daily"], [{"Hour": 8, "Minute": 0}, {"Hour": 10, "Minute": 10}])
        self.assertEqual(module.PLAN["com.spectra.visual-intel.dingtalk"], [{"Hour": 10, "Minute": m} for m in (20, 25, 28, 30)])
        revised = module.revised({"StartInterval": 1800, "RunAtLoad": True, "WorkingDirectory": "unchanged"}, module.PLAN["com.spectra.visual-intel.daily"])
        self.assertNotIn("StartInterval", revised)
        self.assertNotIn("RunAtLoad", revised)
        self.assertEqual(revised["WorkingDirectory"], "unchanged")

    def test_dingtalk_push_is_enabled_and_uses_environment_secrets(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "spectra_agent/config.v0.1.json").read_text())
        settings = config["dingtalk_push"]
        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["webhook_env"], "DINGTALK_WEBHOOK_URL")
        self.assertEqual(settings["secret_env"], "DINGTALK_SECRET")

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
        result = decisions_for(review, {1}, {2}, set(), "reviewer", {1}, {2})
        self.assertEqual(result["records"]["a"]["decision"], "include")
        self.assertEqual(result["records"]["b"]["decision"], "watch")
        self.assertEqual(result["records"]["a"]["fact_decisions"], ["keep"])
        with self.assertRaises(ValueError):
            decisions_for(review, {1}, set(), set(), "reviewer", {1})

    def test_selection_accepts_all_and_chinese_commas(self):
        self.assertEqual(parse_selection("all", 3), {1, 2, 3})
        self.assertEqual(parse_selection("1，3", 3), {1, 3})

    def test_interactive_review_can_include_and_keep_all_visible_facts(self):
        review = {"records": [{
            "candidate_id": "a", "title": "候选A", "url": "https://example.com/a",
            "suggested_evidence": [{"claim": "事实A", "evidence_text": "source A"}],
        }]}
        answers = iter(["i", "p", "y"])
        result = interactive_decisions(review, "reviewer", input_fn=lambda _prompt: next(answers))
        self.assertEqual(result["records"]["a"]["decision"], "include")
        self.assertEqual(result["records"]["a"]["fact_decisions"], ["keep"])


if __name__ == "__main__":
    unittest.main()
