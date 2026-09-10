import json
import tempfile
import unittest
from pathlib import Path

from spectra_agent.acceptance_metrics import distinct_completed_runs, metrics_for_run, rolling_summary


def write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


class AcceptanceMetricsTest(unittest.TestCase):
    def make_run(self, root: Path, name: str, start: str, end: str, writer_status: str) -> Path:
        run = root / name
        run.mkdir()
        write(run / "run.json", {
            "run_id": name, "status": "completed", "completed_at": end,
            "publish_status": "not_published",
        })
        write(run / "collection.json", {
            "window_start": start, "window_end": end,
            "summary": {"configured_sources": 10, "successful_sources": 9, "failed_sources": 1, "source_records": 20},
        })
        write(run / "candidates.json", {"summary": {"selected_for_verification": 4}})
        write(run / "verified-events.json", {"summary": {"included_events": 3}})
        write(run / "p1-review.json", {"records": [{"review_policy": {"tier": "sample_review"}, "suggested_evidence": [
            {"human_fact_decision": "keep"}, {"human_fact_decision": "modify"},
        ]}]})
        write(run / "core-event-checkpoint.json", {"jobs": {
            "evt_1": {"status": writer_status}, "evt_sparse": {"status": "demoted"},
        }})
        write(run / "editorial-issue.json", {"editorial_stories": [{}, {}, {}], "news_briefs": [{}]})
        write(run / "acceptance-metrics.json", metrics_for_run(run))
        return run

    def test_per_run_metrics_distinguish_sparse_and_writer_demotions(self):
        with tempfile.TemporaryDirectory() as temp:
            run = self.make_run(Path(temp), "run_1", "2026-08-01", "2026-08-07", "completed")
            metrics = metrics_for_run(run)
        self.assertEqual(metrics["source_health"]["success_rate"], 0.9)
        self.assertEqual(metrics["p1_editorial"]["eligible_long_jobs"], 1)
        self.assertEqual(metrics["p1_editorial"]["auto_pass_rate"], 1.0)
        self.assertEqual(metrics["p1_editorial"]["writer_demotion_rate"], 0.0)
        self.assertEqual(metrics["human_intervention"]["fact_edit_rate"], 0.5)
        self.assertEqual(metrics["tiered_review"]["sample_fact_edit_rate"], 0.5)

    def test_rolling_summary_requires_distinct_collection_windows(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.make_run(root, "run_old", "2026-08-01", "2026-08-07", "completed")
            self.make_run(root, "run_repeat", "2026-08-01", "2026-08-07", "demoted_after_failed_long_story")
            self.make_run(root, "run_new", "2026-08-05", "2026-08-11", "completed")
            runs = distinct_completed_runs(root, 3)
            summary = rolling_summary(runs, 3)
        self.assertEqual(len(runs), 2)
        self.assertEqual(summary["status"], "collecting")
        self.assertEqual(summary["remaining_rounds"], 1)


if __name__ == "__main__":
    unittest.main()
