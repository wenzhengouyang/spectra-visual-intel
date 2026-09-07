import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spectra_agent.compat import canonical_artifact, compatible_value, config_section, migrate_run_artifacts, rolling_thesis
from spectra_agent.paths import logs_path, runs_path


class PathsAndCompatibilityTest(unittest.TestCase):
    def test_data_paths_are_outside_code_root(self):
        with tempfile.TemporaryDirectory() as temp:
            config = {"data_dir": temp, "runs_dir": "runs"}
            self.assertEqual(runs_path(config), Path(temp).resolve() / "runs")
            self.assertEqual(logs_path(config), Path(temp).resolve() / "logs")

    def test_environment_can_relocate_data_without_code_changes(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {"SPECTRA_DATA_ROOT": temp}):
            self.assertEqual(runs_path({"runs_dir": "runs"}), Path(temp).resolve() / "runs")

    def test_legacy_names_are_read_only_at_the_compatibility_boundary(self):
        self.assertEqual(config_section({"deep_story_writer": {"enabled": True}}, "core_event_writer"), {"enabled": True})
        self.assertEqual(compatible_value({"deep_story_min_characters": 500}, "core_event_min_characters"), 500)
        self.assertEqual(rolling_thesis({"weekly_thesis": "旧数据"}), "旧数据")

    def test_legacy_artifact_is_promoted_to_canonical_name(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            (run_dir / "weekly-report.html").write_text("legacy", encoding="utf-8")
            target = canonical_artifact(run_dir, "rolling-digest.html")
            self.assertEqual(target.name, "rolling-digest.html")
            self.assertEqual(target.read_text(encoding="utf-8"), "legacy")

    def test_run_migration_rewrites_derived_payload_without_touching_legacy_source(self):
        with tempfile.TemporaryDirectory() as temp:
            run_dir = Path(temp)
            legacy = run_dir / "weekly-report.html"
            legacy.write_text('{"weekly_thesis":"x","article_type":"deep_story"}', encoding="utf-8")
            result = migrate_run_artifacts(run_dir)
            migrated = (run_dir / "rolling-digest.html").read_text(encoding="utf-8")
            self.assertTrue(legacy.exists())
            self.assertIn('"rolling_thesis"', migrated)
            self.assertIn('"core_event"', migrated)
            self.assertEqual(result, {"promoted": 1, "removed": 0, "rewritten": 0})


if __name__ == "__main__":
    unittest.main()
