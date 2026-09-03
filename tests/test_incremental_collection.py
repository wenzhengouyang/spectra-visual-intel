import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from collector.merge_incremental_runs import merge
from spectra_agent.run import find_weekly_baseline, incremental_retry_source_ids


def record(source_id, url, digest, published, title="item"):
    return {
        "source_id": source_id, "canonical_url": url, "content_hash": digest,
        "published_at": published, "source_name": "source", "raw_title": title,
        "access_status": "success",
    }


class IncrementalCollectionTest(unittest.TestCase):
    def test_merge_keeps_baseline_and_prefers_delta_update(self):
        baseline = {
            "schema_version": "0.2", "run_id": "monday",
            "source_checks": [{"registry_id": "feed", "status": "success"}],
            "source_records": [
                record("old", "https://example.com/old", "h1", "2026-08-31T01:00:00Z"),
                record("changed-old", "https://example.com/change", "h2", "2026-09-01T01:00:00Z"),
            ],
        }
        delta = {
            "run_id": "thursday", "collected_at": "2026-09-03T02:00:00Z",
            "window_start": "2026-08-31T02:00:00Z", "window_end": "2026-09-03T02:00:00Z",
            "source_checks": [{"registry_id": "feed", "status": "success"}],
            "source_records": [
                record("changed-new", "https://example.com/change", "h3", "2026-09-02T01:00:00Z"),
                record("new", "https://example.com/new", "h4", "2026-09-03T01:00:00Z"),
            ],
        }
        result = merge(baseline, delta, None, "2026-08-27T02:00:00Z", "2026-09-03T02:00:00Z")
        self.assertEqual({item["source_id"] for item in result["source_records"]}, {"old", "changed-new", "new"})
        self.assertEqual(result["summary"]["baseline_records_reused"], 1)
        self.assertEqual(result["summary"]["incremental_records"], 2)
        self.assertEqual(result["summary"]["changed_records"], 2)
        self.assertEqual(result["incremental"]["processing_scope"], "changed_records_only")

    def test_unchanged_delta_record_keeps_baseline_and_is_not_reprocessed(self):
        baseline = {
            "run_id": "monday",
            "source_checks": [{"registry_id": "feed", "status": "success"}],
            "source_records": [
                record("old", "https://example.com/same", "same-hash", "2026-09-01T01:00:00Z"),
            ],
        }
        delta = {
            "run_id": "thursday",
            "collected_at": "2026-09-03T02:00:00Z",
            "window_start": "2026-08-31T02:00:00Z",
            "window_end": "2026-09-03T02:00:00Z",
            "source_checks": [{"registry_id": "feed", "status": "success"}],
            "source_records": [
                record("duplicate", "https://example.com/same", "same-hash", "2026-09-01T01:00:00Z"),
            ],
        }
        result = merge(
            baseline,
            delta,
            None,
            "2026-08-27T02:00:00Z",
            "2026-09-03T02:00:00Z",
        )
        self.assertEqual(result["source_records"][0]["source_id"], "old")
        self.assertEqual(result["summary"]["changed_records"], 0)
        self.assertEqual(result["summary"]["unchanged_records"], 1)
        self.assertEqual(result["incremental"]["changed_source_ids"], [])

    def test_same_content_at_new_url_does_not_replace_baseline(self):
        baseline = {
            "run_id": "monday",
            "source_checks": [{"registry_id": "feed", "status": "success"}],
            "source_records": [
                record("old", "https://example.com/original", "same-hash", "2026-09-01T01:00:00Z"),
            ],
        }
        delta = {
            "run_id": "thursday",
            "collected_at": "2026-09-03T02:00:00Z",
            "source_checks": [{"registry_id": "feed", "status": "success"}],
            "source_records": [
                record("copy", "https://mirror.example.com/copy", "same-hash", "2026-09-02T01:00:00Z"),
            ],
        }
        result = merge(
            baseline,
            delta,
            None,
            "2026-08-27T02:00:00Z",
            "2026-09-03T02:00:00Z",
        )
        self.assertEqual([item["source_id"] for item in result["source_records"]], ["old"])
        self.assertEqual(result["summary"]["changed_records"], 0)

    def test_full_window_retry_wins_and_is_visible(self):
        baseline = {"run_id": "monday", "source_checks": [{"registry_id": "feed", "status": "failed"}], "source_records": []}
        delta = {"run_id": "thursday", "collected_at": "2026-09-03T02:00:00Z", "window_start": "2026-08-31T02:00:00Z", "window_end": "2026-09-03T02:00:00Z", "source_checks": [{"registry_id": "feed", "status": "failed"}], "source_records": []}
        retry = {"source_checks": [{"registry_id": "feed", "status": "success"}], "source_records": [record("recovered", "https://example.com/a", "h1", "2026-09-01T01:00:00Z")]}
        result = merge(baseline, delta, retry, "2026-08-27T02:00:00Z", "2026-09-03T02:00:00Z")
        self.assertEqual(result["source_checks"][0]["effective_status"], "recovered_by_full_window_retry")
        self.assertEqual(result["summary"]["retry_records"], 1)

    def test_full_window_retry_is_limited_to_failed_and_new_sources(self):
        baseline = {
            "source_checks": [
                {"registry_id": "healthy", "status": "success"},
                {"registry_id": "failed_before", "status": "failed"},
            ],
        }
        delta = {
            "source_checks": [
                {"registry_id": "healthy", "status": "success"},
                {"registry_id": "failed_before", "status": "success"},
                {"registry_id": "failed_now", "status": "failed"},
                {"registry_id": "new_source", "status": "success"},
            ],
        }
        self.assertEqual(incremental_retry_source_ids(baseline, delta), [
            "failed_before",
            "failed_now",
            "new_source",
        ])

    def test_thursday_selects_same_week_monday_baseline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monday = root / "monday"
            monday.mkdir()
            (monday / "collection.json").write_text(json.dumps({
                "window_end": "2026-08-31T02:00:00Z",
                "source_records": [record("old", "https://example.com/old", "h1", "2026-08-31T01:00:00Z")],
                "source_checks": [{"registry_id": "feed", "status": "success"}],
            }))
            config = {"runs_dir": str(root), "timezone": "Asia/Shanghai", "incremental_collection": {
                "enabled": True, "baseline_weekday": 0, "incremental_weekday": 3,
            }}
            selected = find_weekly_baseline(config, datetime(2026, 9, 3, 2, tzinfo=timezone.utc), root / "current")
        self.assertEqual(selected, monday / "collection.json")

    def test_corrupt_monday_baseline_falls_back_to_full_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monday = root / "monday"
            monday.mkdir()
            (monday / "collection.json").write_text('{"window_end":"2026-08-31T02:00:00Z","source_records":[]}')
            config = {"runs_dir": str(root), "timezone": "Asia/Shanghai", "incremental_collection": {
                "enabled": True, "baseline_weekday": 0, "incremental_weekday": 3,
            }}
            selected = find_weekly_baseline(config, datetime(2026, 9, 3, 2, tzinfo=timezone.utc), root / "current")
        self.assertIsNone(selected)

    def test_valid_empty_monday_baseline_can_be_reused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            monday = root / "monday"
            monday.mkdir()
            (monday / "collection.json").write_text(json.dumps({
                "window_end": "2026-08-31T02:00:00Z",
                "source_records": [],
                "source_checks": [{"registry_id": "feed", "status": "success"}],
            }))
            config = {
                "runs_dir": str(root),
                "timezone": "Asia/Shanghai",
                "incremental_collection": {
                    "enabled": True,
                    "baseline_weekday": 0,
                    "incremental_weekday": 3,
                },
            }
            selected = find_weekly_baseline(
                config,
                datetime(2026, 9, 3, 2, tzinfo=timezone.utc),
                root / "current",
            )
        self.assertEqual(selected, monday / "collection.json")

    def test_non_thursday_uses_full_collection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = {"runs_dir": str(root), "timezone": "Asia/Shanghai", "incremental_collection": {
                "enabled": True, "baseline_weekday": 0, "incremental_weekday": 3,
            }}
            selected = find_weekly_baseline(config, datetime(2026, 8, 31, 2, tzinfo=timezone.utc), root / "current")
        self.assertIsNone(selected)


if __name__ == "__main__":
    unittest.main()
