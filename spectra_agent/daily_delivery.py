#!/usr/bin/env python3
"""Recover webpage publication independently, then attempt the optional alert."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from spectra_agent.run import DEFAULT_CONFIG, ROOT, read_json, resolve_config, runs_dir, write_json
except ImportError:
    from run import DEFAULT_CONFIG, ROOT, read_json, resolve_config, runs_dir, write_json


def run_step(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a completed SPECTRA issue and deliver its alert")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--wait-for-web", action="store_true")
    args = parser.parse_args()
    _, config = resolve_config(args.config)
    run_dir = runs_dir(config) / args.run_id
    state_path = run_dir / "run.json"
    if not state_path.exists() or read_json(state_path).get("status") != "completed":
        print(json.dumps({"status": "not_ready", "run_id": args.run_id}, ensure_ascii=False))
        return 0

    record = {
        "run_id": args.run_id,
        "attempted_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if read_json(state_path).get("publish_status") != "published":
        published = run_step([
            sys.executable, str(ROOT / "spectra_agent/publish_run.py"), "--config", args.config,
            "--run-id", args.run_id, "--push", "--confirm",
        ])
        record["web_exit"] = published.returncode
        record["web_output"] = (published.stdout or published.stderr)[-2000:]
        if published.returncode:
            record["status"] = "web_failed"
            write_json(run_dir / "delivery-status.json", record)
            print(json.dumps(record, ensure_ascii=False))
            return published.returncode
        refreshed = read_json(state_path)
        if refreshed.get("publication_mode") == "no_update":
            record["web_status"] = "no_update"
            record["status"] = "completed_no_update"
            record["reason"] = refreshed.get("no_update_reason")
            write_json(run_dir / "delivery-status.json", record)
            print(json.dumps(record, ensure_ascii=False))
            return 0
    record["web_status"] = "published"

    command = [
        sys.executable, str(ROOT / "spectra_agent/dingtalk_push.py"), "--config", args.config,
        "--run-id", args.run_id,
    ]
    if args.wait_for_web:
        command.append("--wait-for-web")
    alert = run_step(command)
    record["dingtalk_exit"] = alert.returncode
    record["dingtalk_output"] = (alert.stdout or alert.stderr)[-2000:]
    record["status"] = "completed" if alert.returncode == 0 else "dingtalk_failed"
    write_json(run_dir / "delivery-status.json", record)
    print(json.dumps(record, ensure_ascii=False))
    return alert.returncode


if __name__ == "__main__":
    raise SystemExit(main())
