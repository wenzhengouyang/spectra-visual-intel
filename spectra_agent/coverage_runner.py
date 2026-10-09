#!/usr/bin/env python3
"""Run the non-blocking SPECTRA coverage lane for one daily run."""
from __future__ import annotations

import argparse
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    from spectra_agent.run import ROOT, read_json, resolve_config, runs_dir, write_json
except ImportError:
    from run import ROOT, read_json, resolve_config, runs_dir, write_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def enabled_registry_ids(registry: dict) -> list[str]:
    return [item["registry_id"] for item in registry.get("sources", []) if item.get("enabled", True)]


def coverage_source_ids(config: dict, registry: dict, run_dir: Path) -> list[str]:
    lanes = config.get("collection_lanes") or {}
    mainline = set((lanes.get("mainline") or {}).get("source_ids") or [])
    coverage = lanes.get("coverage") or {}
    selected = set(coverage.get("source_ids") or [])
    if coverage.get("derive_from_non_mainline_enabled_sources", True):
        selected.update(set(enabled_registry_ids(registry)) - mainline)
    if coverage.get("include_failed_mainline_sources", True):
        for name in ("collection.incremental.json", "collection.mainline.json", "collection.json"):
            path = run_dir / name
            if not path.exists():
                continue
            payload = read_json(path)
            selected.update(
                item["registry_id"] for item in payload.get("source_checks", [])
                if item.get("status") == "failed" and item.get("registry_id") in mainline
            )
            break
    return sorted(selected)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run SPECTRA's asynchronous coverage lane")
    parser.add_argument("--config", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--midday", action="store_true")
    args = parser.parse_args()
    _, config = resolve_config(args.config)
    run_dir = runs_dir(config) / args.run_id
    status_path = run_dir / "coverage-line.json"
    registry = read_json(ROOT / config["collection_config"])
    source_ids = coverage_source_ids(config, registry, run_dir)
    if args.midday:
        from spectra_agent.collection_health import read
        prior_midday = read(run_dir / 'midday-collection.json')
        if prior_midday.get('status') == 'completed' or prior_midday.get('attempts', 0) >= 2:
            return 0
        source_ids = list(config['collection_lanes']['mainline']['source_ids'])
        write_json(run_dir / 'midday-collection.json', {
            'status': 'running', 'attempts': prior_midday.get('attempts', 0) + 1,
            'window_start': args.start, 'window_end': args.end,
        })
    if args.retry_failed:
        from spectra_agent.recovery import source_retry_ids
        from spectra_agent.collection_health import read
        prior_attempt = read(run_dir / 'source-recovery.json')
        if prior_attempt.get('attempts', 0) >= 2 or time.time() < prior_attempt.get('next_retry_at', 0):
            return 0
        source_ids = source_retry_ids(run_dir)
        if not source_ids:
            return 0
        write_json(run_dir / 'source-recovery.json', {
            'attempts': prior_attempt.get('attempts', 0) + 1,
            'next_retry_at': time.time() + 3600, 'source_ids': source_ids,
        })
    coverage = (config.get("collection_lanes") or {}).get("coverage") or {}
    output = run_dir / "coverage-collection.json"
    if args.retry_failed:
        output = run_dir / ('coverage-retry-%s.json' % (prior_attempt.get('attempts', 0) + 1))
    if args.midday:
        output = run_dir / 'collection.midday.json'
    if not source_ids:
        write_json(status_path, {
            "status": "completed", "completed_at": utc_now(), "source_count": 0,
            "source_ids": [], "output": None, "summary": None, "blocking_daily": False,
        })
        return 0
    command = [
        str(ROOT / config.get("collector_python", ".venv-collector/bin/python")),
        "collector/collect.py", "--config", config["collection_config"],
        "--output", str(output), "--start", args.start, "--end", args.end,
    ]
    for source_id in source_ids:
        command += ["--source", source_id]
    write_json(status_path, {
        "status": "running", "started_at": utc_now(), "source_count": len(source_ids),
        "source_ids": source_ids, "output": output.name, "blocking_daily": False,
    })
    try:
        result = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True,
            timeout=int(coverage.get("timeout_seconds", 1800)),
            env={**os.environ, "SPECTRA_ACQUISITION_BUDGET_DB": str(run_dir / "coverage-acquisition-budget.sqlite")},
        )
        if result.returncode:
            raise RuntimeError((result.stderr or result.stdout or "coverage collector failed")[-2000:])
        if args.retry_failed or args.midday:
            from collector.merge_incremental_runs import merge
            prior_path = run_dir / 'coverage-collection.json'
            prior = read_json(prior_path) if prior_path.exists() else read_json(run_dir / 'collection.json')
            combined = merge(prior, read_json(output), None, prior['window_start'], args.end)
            output = prior_path
            write_json(output, combined)
        if args.midday:
            midday = read(run_dir / 'midday-collection.json')
            write_json(run_dir / 'midday-collection.json', {**midday, 'status': 'completed'})
        summary = read_json(output).get("summary") if output.exists() else None
        write_json(status_path, {
            "status": "completed", "completed_at": utc_now(), "source_count": len(source_ids),
            "source_ids": source_ids, "output": output.name, "summary": summary, "blocking_daily": False,
        })
        return 0
    except Exception as exc:
        write_json(status_path, {
            "status": "failed", "failed_at": utc_now(), "source_count": len(source_ids),
            "source_ids": source_ids, "output": output.name, "error": str(exc), "blocking_daily": False,
        })
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
