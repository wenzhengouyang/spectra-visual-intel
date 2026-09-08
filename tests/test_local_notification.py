import json
import tempfile
import unittest
from pathlib import Path

from spectra_agent.local_notification import notification_key, review_target, show_review_popup, terminal_review_command


class LocalNotificationTest(unittest.TestCase):
    def test_fact_review_opens_review_guide_and_is_deduplicated(self):
        calls = []

        def launch(command, **kwargs):
            calls.append((command, kwargs))

        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp) / "daily-20260908"
            run_dir.mkdir()
            (run_dir / "REVIEW.md").write_text("review", encoding="utf-8")
            (run_dir / "p1-review.json").write_text(json.dumps({"records": [{}, {}]}), encoding="utf-8")
            state = {"status": "waiting_for_review", "current_stage": "human_review", "paused_reason": "verify"}
            first = show_review_popup(run_dir, state, {"enabled": True}, launcher=launch)
            second = show_review_popup(run_dir, state, {"enabled": True}, launcher=launch)

        self.assertEqual(first["status"], "shown")
        self.assertEqual(second["status"], "already_shown")
        self.assertEqual(len(calls), 1)
        self.assertIn("review_cli.py", calls[0][0][3])
        self.assertIn("--interactive", calls[0][0][3])
        self.assertIn("--resume", calls[0][0][3])

    def test_editorial_gate_uses_digest_and_has_distinct_key(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            digest = run_dir / "rolling-digest.html"
            digest.write_text("ok", encoding="utf-8")
            state = {"status": "waiting_for_editorial_review", "current_stage": "image_review"}
            self.assertEqual(review_target(run_dir, state), digest)
            self.assertEqual(notification_key(state), "terminal_review_v1:waiting_for_editorial_review:image_review")
            self.assertIn("run.py", terminal_review_command(run_dir, state))


if __name__ == "__main__":
    unittest.main()
