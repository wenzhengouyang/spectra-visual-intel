import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from spectra_agent.run_evaluator import evaluate_run, write_report


def write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class RunEvaluatorTest(unittest.TestCase):
    def make_run(self, root: Path, name: str = "run_1") -> tuple[Path, dict]:
        run = root / name
        run.mkdir()
        write(run / "run.json", {"run_id": name, "status": "waiting_for_review"})
        write(run / "collection.json", {
            "window_start": "2026-09-01T00:00:00Z",
            "window_end": "2026-09-07T00:00:00Z",
            "incremental": {"baseline_run_id": "monday", "processing_scope": "changed_records_only"},
            "summary": {
                "configured_sources": 10, "successful_sources": 10, "failed_sources": 0,
                "source_records": 2, "changed_records": 1, "unchanged_records": 1,
                "baseline_records_reused": 1,
            },
            "source_records": [
                {"source_id": "s1", "canonical_url": "https://a", "content_hash": "1"},
                {"source_id": "s2", "canonical_url": "https://b", "content_hash": "2"},
            ],
        })
        write(run / "candidates.json", {
            "summary": {"selected_for_verification": 1},
            "selected_candidates": [{"candidate_id": "c1", "source_ids": ["s1"]}],
        })
        review = {"records": [{
            "candidate_id": "c1", "review_policy": {"tier": "sample_review"},
            "suggested_evidence": [{
                "source_id": "s1", "locator": "char:1-10", "support_status": "supported",
                "numeric_match": True, "attribution_required": True,
                "attribution_preserved": True, "risk_flags": [], "human_fact_decision": "pending",
            }],
        }]}
        write(run / "p1-review.json", review)
        write(run / "gated-review.json", {"records": []})
        config = {
            "review_policy": {"enabled": True},
            "run_evaluation": {
                "sample_rate": 1, "sample_seed": 7, "rolling_window": 3,
                "thresholds": {
                    "source_success_rate_min": 0.9, "duplicate_processing_rate_max": 0.1,
                    "sample_fact_failure_rate_max": 0.05,
                },
                "expansion_advice": {"minimum_distinct_windows": 3, "require_unchanged_strategy": True},
            },
        }
        return run, config

    def test_evaluation_is_read_only_and_replayable(self):
        with tempfile.TemporaryDirectory() as temp:
            run, config = self.make_run(Path(temp))
            before = (run / "p1-review.json").read_bytes()
            report_1 = evaluate_run(run, config, run.parent)
            report_2 = evaluate_run(run, config, run.parent)
            after = (run / "p1-review.json").read_bytes()
        self.assertEqual(before, after)
        self.assertEqual(report_1["fact_alignment_sample"]["results"], report_2["fact_alignment_sample"]["results"])
        self.assertFalse(report_1["rolling_advice"]["allow_expand"])
        self.assertTrue(all(value is False for value in report_1["immutability"].values()))

    def test_malformed_queue_relationship_fails(self):
        with tempfile.TemporaryDirectory() as temp:
            run, config = self.make_run(Path(temp))
            write(run / "gated-review.json", {"records": [{"candidate_id": "c1"}]})
            report = evaluate_run(run, config, run.parent)
        self.assertEqual(report["status"], "fail")
        self.assertIn("queues_mutually_exclusive", report["failed_checks"])

    def test_reports_only_write_dedicated_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            run, config = self.make_run(Path(temp))
            review_before = deepcopy(json.loads((run / "p1-review.json").read_text()))
            report = evaluate_run(run, config, run.parent)
            write_report(report, run / "eval-report.json", run / "eval-report.md")
            review_after = json.loads((run / "p1-review.json").read_text())
            self.assertEqual(review_before, review_after)
            self.assertTrue((run / "eval-report.json").exists())
            self.assertTrue((run / "eval-report.md").exists())


if __name__ == "__main__":
    unittest.main()
