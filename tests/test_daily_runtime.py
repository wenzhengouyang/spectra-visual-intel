import json
import plistlib
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from spectra_agent.daily_runner import choose_action, daily_run_id, human_gate_is_complete, recovery_reservation_required, review_notification, release_due
from spectra_agent.review_cli import decisions_for, interactive_decisions, parse_selection


class DailyRuntimeTest(unittest.TestCase):
    def test_first_run_does_not_create_recovery_state_before_run_directory(self):
        self.assertFalse(recovery_reservation_required("run"))
        self.assertTrue(recovery_reservation_required("resume_retry"))

    def test_installer_preserves_bounded_morning_schedule(self):
        import importlib.util
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location("morning_schedule", root / "scripts/install-morning-schedule.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.PLAN["com.spectra.visual-intel.daily"], [{"Hour": 9, "Minute": 0}])
        self.assertEqual(module.PLAN["com.spectra.visual-intel.delivery"], [{"Hour": 12, "Minute": 30}])
        self.assertNotIn("com.spectra.visual-intel.dingtalk", module.PLAN)
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

    def test_daily_mainline_and_nonblocking_coverage_are_configured(self):
        root = Path(__file__).resolve().parents[1]
        config = json.loads((root / "spectra_agent/config.v0.1.json").read_text())
        lanes = config["collection_lanes"]
        self.assertGreaterEqual(len(lanes["mainline"]["source_ids"]), 8)
        self.assertEqual(len(lanes["mainline"]["source_ids"]), len(set(lanes["mainline"]["source_ids"])))
        self.assertTrue(lanes["coverage"]["enabled"])
        self.assertTrue(lanes["coverage"]["derive_from_non_mainline_enabled_sources"])

    def test_daily_run_id_uses_configured_local_date(self):
        now = datetime(2026, 9, 3, 23, 30, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(daily_run_id("Asia/Shanghai", now), "daily-20260904")

    def test_release_time_gate(self):
        config = {"timezone": "Asia/Shanghai", "joint_delivery": {"local_time": "12:30"}}
        before = datetime(2026, 9, 16, 12, 29, tzinfo=ZoneInfo("Asia/Shanghai"))
        due = datetime(2026, 9, 16, 12, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.assertFalse(release_due(config, before))
        self.assertTrue(release_due(config, due))

    def test_completed_and_human_gate_do_not_repeat_work(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "run.json").write_text(json.dumps({"status": "completed", "publish_status": "published"}))
            self.assertEqual(choose_action(run_dir), "noop_completed")
            (run_dir / "run.json").write_text(json.dumps({"status": "waiting_for_review"}))
            self.assertEqual(choose_action(run_dir), "noop_human_gate")

    def test_failed_run_resumes_from_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "collection.json").write_text("{}")
            (run_dir / "candidates.json").write_text("{}")
            (run_dir / "run.json").write_text(json.dumps({"status": "failed"}))
            self.assertEqual(choose_action(run_dir), "resume_retry")

    def test_failed_run_with_collection_resumes_llm_checkpoint(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "collection.json").write_text("{}")
            (run_dir / "run.json").write_text(json.dumps({"status": "failed", "failed_stage": "collect"}))
            self.assertEqual(choose_action(run_dir), "resume_retry")

    def test_failed_pre_collection_run_restarts_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "run.json").write_text(json.dumps({"status": "failed", "failed_stage": "collect"}))
            self.assertEqual(choose_action(run_dir), "run")

    def test_completed_unpublished_run_recovers_delivery(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "run.json").write_text(json.dumps({"status": "completed", "publish_status": "not_published"}))
            self.assertEqual(choose_action(run_dir), "deliver_retry")

    def test_approved_fact_review_is_resumed_by_scheduler(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            state = {"status": "waiting_for_review", "current_stage": "human_review"}
            (run_dir / "run.json").write_text(json.dumps(state))
            (run_dir / "p1-review.json").write_text(json.dumps({
                "review_status": "approved", "verified_at": "2026-09-15T00:00:00Z", "verified_by": "human",
            }))
            self.assertTrue(human_gate_is_complete(run_dir, state))
            self.assertEqual(choose_action(run_dir), "resume_retry")

    def test_popup_is_suppressed_when_dashboard_is_available(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
        with patch("spectra_agent.daily_runner.urllib.request.urlopen", return_value=Response()), \
                patch("spectra_agent.daily_runner.show_review_popup") as popup:
            result = review_notification(Path("/tmp/run"), {}, {})
        self.assertEqual(result["status"], "dashboard_available")
        popup.assert_not_called()

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
