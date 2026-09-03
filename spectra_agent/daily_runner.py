#!/usr/bin/env python3
"""Run one daily SPECTRA collection without depending on Codex.

Expected human-review pauses are successful scheduler outcomes. Failed and
interrupted runs are resumed from persisted artifacts instead of recollected.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from spectra_agent.run import DEFAULT_CONFIG, ROOT, read_json, resolve_config
except ImportError:
    from run import DEFAULT_CONFIG, ROOT, read_json, resolve_config


ACTIVE_STATUSES = {"initialized", "running", "waiting_for_editorial"}
HUMAN_STATUSES = {"waiting_for_review", "waiting_for_editorial_review"}


def daily_run_id(timezone_name: str, now: datetime | None = None) -> str:
    local_now = now or datetime.now(ZoneInfo(timezone_name))
    return f"daily-{local_now.astimezone(ZoneInfo(timezone_name)):%Y%m%d}"


def choose_action(run_dir: Path) -> str:
    state_path = run_dir / "run.json"
    if not state_path.exists():
        return "run"
    state = read_json(state_path)
    status = state.get("status")
    if status == "completed":
        return "noop_completed"
    if status in HUMAN_STATUSES:
        return "noop_human_gate"
    if status == "failed" or status in ACTIVE_STATUSES:
        return "resume_retry"
    return "resume_retry"


def run_command(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{datetime.now().isoformat()}] {' '.join(command)}\n")
        handle.flush()
        result = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or recover today's local SPECTRA job")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--run-id")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args()

    _, config = resolve_config(args.config)
    run_id = args.run_id or daily_run_id(config.get("timezone", "Asia/Shanghai"))
    run_dir = ROOT / config["runs_dir"] / run_id
    runtime = config.get("local_runtime") or {}
    log_dir = ROOT / runtime.get("logs_dir", "spectra_agent/logs")
    lock_path = log_dir / "daily-runner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running", "run_id": run_id}, ensure_ascii=False))
            return 0

        action = choose_action(run_dir)
        if action.startswith("noop_"):
            state = read_json(run_dir / "run.json")
            print(json.dumps({
                "status": state.get("status"),
                "run_id": run_id,
                "action": action,
                "next": "run review_cli.py" if action == "noop_human_gate" else None,
            }, ensure_ascii=False, indent=2))
            return 0

        python = str(ROOT / config.get("llm_python", ".venv-llm/bin/python"))
        command = [python, str(ROOT / "spectra_agent/run.py"), "--config", args.config]
        if action == "run":
            command += ["run", "--run-id", run_id, "--days", str(args.days)]
            if not args.no_llm:
                command.append("--llm")
        else:
            command += ["resume", "--run-id", run_id, "--retry"]

        return_code = run_command(command, log_dir / f"{run_id}.log")
        state = read_json(run_dir / "run.json") if (run_dir / "run.json").exists() else {}
        status = state.get("status")
        # run.py returns 2 when it intentionally stops at the human gate.
        successful_pause = status in HUMAN_STATUSES or status == "waiting_for_editorial"
        result_code = 0 if successful_pause else return_code
        print(json.dumps({
            "status": status or "failed_to_initialize",
            "run_id": run_id,
            "action": action,
            "command_exit": return_code,
            "log": str(log_dir / f"{run_id}.log"),
        }, ensure_ascii=False, indent=2))
        return result_code


if __name__ == "__main__":
    raise SystemExit(main())
