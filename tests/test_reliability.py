import json
import tempfile
import unittest
from pathlib import Path

from spectra_agent.reliability import classify_failure, mark_recovered, record_failure


class ReliabilityTest(unittest.TestCase):
    def test_429_uses_cooldown_instead_of_retry_loop(self):
        with tempfile.TemporaryDirectory() as temporary:
            incident = record_failure(Path(temporary), "collect", "HTTP 429 Too Many Requests")
            self.assertEqual(incident["action"], "cooldown_source")
            self.assertFalse(incident["retryable"])

    def test_dependency_timeout_has_bounded_checkpoint_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = record_failure(root, "llm_structure", "request timed out", max_attempts=2)
            second = record_failure(root, "llm_structure", "request timed out", max_attempts=2)
            self.assertEqual(first["action"], "retry_from_checkpoint")
            self.assertEqual(second["action"], "stop_or_degrade")
            self.assertTrue(second["exhausted"])

    def test_invalid_json_requires_human_action_without_blind_retry(self):
        category, code, retryable = classify_failure("verify", "JSON decode failed: Expecting value")
        self.assertEqual((category, code, retryable), ("data_integrity", "invalid_json", False))

    def test_optional_failure_is_degraded_not_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            incident = record_failure(Path(temporary), "metrics", "service unavailable", blocking=False)
            self.assertEqual(incident["action"], "continue_with_degraded_optional_stage")
            self.assertFalse(incident["blocking"])

    def test_recovery_preserves_incident_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record_failure(root, "writer", "model timeout")
            status = mark_recovered(root, "writer")
            self.assertEqual(status, "healthy")
            state = json.loads((root / "reliability.json").read_text())
            self.assertEqual(state["status"], "healthy")
            self.assertEqual(len(state["incidents"]), 1)
            self.assertIn("resolved_at", state["incidents"][0])
            next_incident = record_failure(root, "writer", "model timeout")
            self.assertEqual(next_incident["attempt"], 1)

    def test_optional_warning_remains_degraded_after_workflow_completion(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record_failure(root, "metrics", "metrics unavailable", blocking=False)
            self.assertEqual(mark_recovered(root, "complete"), "degraded")

    def test_corrupted_reliability_file_does_not_block_new_record(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "reliability.json").write_text("{")
            record_failure(root, "generate", "boom")
            state = json.loads((root / "reliability.json").read_text())
            self.assertTrue(state["recovered_from_invalid_reliability_file"])


if __name__ == "__main__":
    unittest.main()
