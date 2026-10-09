import json
import subprocess
import sys
from pathlib import Path


def test_evaluate_only_without_snapshot_reports_missing(tmp_path: Path) -> None:
    run_dir = tmp_path / "daily-20261008"
    run_dir.mkdir()
    (run_dir / "collection.json").write_text(
        json.dumps({
            "source_checks": [{"registry_id": "failed-source", "status": "failed"}],
            "summary": {"successful_sources": 0},
        }),
        encoding="utf-8",
    )
    output = run_dir / "recovery.json"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/recover-failed-sources.py",
            "--run-dir",
            str(run_dir),
            "--output",
            str(output),
            "--evaluate-only",
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        capture_output=True,
        text=True,
    )

    assert '"status": "snapshot_missing"' in result.stdout
    assert not output.exists()
