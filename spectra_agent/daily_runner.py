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
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from spectra_agent.run import DEFAULT_CONFIG, ROOT, read_json, resolve_config, runs_dir
except ImportError:
    from run import DEFAULT_CONFIG, ROOT, read_json, resolve_config, runs_dir

try:
    from spectra_agent.paths import logs_path
except ImportError:
    from paths import logs_path

try:
    from spectra_agent.local_notification import show_review_popup
except ImportError:
    from local_notification import show_review_popup


ACTIVE_STATUSES = {"initialized", "running", "waiting_for_editorial"}
HUMAN_STATUSES = {"waiting_for_review", "waiting_for_editorial_review"}


def daily_run_id(timezone_name: str, now: datetime | None = None) -> str:
    local_now = now or datetime.now(ZoneInfo(timezone_name))
    return f"daily-{local_now.astimezone(ZoneInfo(timezone_name)):%Y%m%d}"


def release_due(config: dict, now: datetime | None = None) -> bool:
    local_now = (now or datetime.now(ZoneInfo(config.get("timezone", "Asia/Shanghai"))))
    hour, minute = map(int, str((config.get("joint_delivery") or {}).get("local_time", "12:30")).split(":"))
    return (local_now.hour, local_now.minute) >= (hour, minute)


def recovery_reservation_required(action: str) -> bool:
    return action != "run"


def choose_action(run_dir: Path) -> str:
    state_path = run_dir / "run.json"
    if not state_path.exists():
        return "run"
    state = read_json(state_path)
    status = state.get("status")
    if status == "completed":
        return "deliver_retry" if state.get("publish_status") != "published" else "noop_completed"
    if status in HUMAN_STATUSES:
        if human_gate_is_complete(run_dir, state):
            return "resume_retry"
        return "noop_human_gate"
    if status == "failed" or status in ACTIVE_STATUSES:
        # A failed LLM structure pass can leave a valid collection and a
        # durable checkpoint but no final candidates.json.  `resume --retry`
        # rebuilds that artifact from the checkpoint; starting a new run with
        # the same ID only loops on "run already exists".
        return "resume_retry" if (run_dir / "collection.json").is_file() else "run"
    return "resume_retry"


def human_gate_is_complete(run_dir: Path, state: dict) -> bool:
    stage = state.get("current_stage")
    if state.get("status") == "waiting_for_review":
        path = run_dir / "p1-review.json"
        if not path.exists():
            return False
        review = read_json(path)
        return bool(
            review.get("review_status") == "approved"
            and review.get("verified_at") and review.get("verified_by")
        )
    if stage == "localization_review":
        path = run_dir / "p2-localization-review.json"
        return bool(path.exists() and read_json(path).get("status") == "approved")
    if stage == "image_preview":
        path = run_dir / "image-review.json"
        if not path.exists():
            return False
        review = read_json(path)
        return bool(review.get("approved_covers")) and not review.get("pending") and not review.get("rejected_story_ids")
    return False


def run_command(command: list[str], log_path: Path) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{datetime.now().isoformat()}] {' '.join(command)}\n")
        handle.flush()
        result = subprocess.run(command, cwd=ROOT, stdout=handle, stderr=subprocess.STDOUT)
    return result.returncode


def review_notification(run_dir: Path, state: dict, config: dict) -> dict:
    """Use the dashboard as the primary review surface; popup is a fallback."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:8010/api/status", timeout=1) as response:
            if response.status == 200:
                return {"status": "dashboard_available", "url": "http://127.0.0.1:8010/"}
    except (OSError, TimeoutError):
        pass
    return show_review_popup(run_dir, state, config.get("local_review_notification") or {})


def main() -> int:
    parser = argparse.ArgumentParser(description="Run or recover today's local SPECTRA job")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--run-id")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--probe", action="store_true", help="validate the installed runtime without collecting")
    parser.add_argument("--watch", action="store_true", help="bounded daytime recovery巡检")
    args = parser.parse_args()

    _, config = resolve_config(args.config)
    if args.probe:
        required = [
            ROOT / "spectra_agent/run.py",
            ROOT / "spectra_agent/daily_delivery.py",
            ROOT / "spectra_agent/coverage_runner.py",
            ROOT / config.get("llm_python", ".venv-llm/bin/python"),
            ROOT / config.get("collector_python", ".venv-collector/bin/python"),
            ROOT / "visual-intelligence-prototype.html",
            ROOT / "processor/localization_rule_samples.v0.1.json",
        ]
        missing = [str(path) for path in required if not path.exists()]
        configured_sources = {
            item.get("registry_id")
            for item in read_json(ROOT / config["collection_config"]).get("sources", [])
        }
        mainline = set(((config.get("collection_lanes") or {}).get("mainline") or {}).get("source_ids") or [])
        unknown_mainline = sorted(mainline - configured_sources)
        errors = [*(f"missing:{path}" for path in missing), *(f"unknown_mainline:{item}" for item in unknown_mainline)]
        print(json.dumps({
            "status": "ready" if not errors else "invalid", "root": str(ROOT),
            "missing": missing, "unknown_mainline_sources": unknown_mainline,
            "checks": ["runtime_files", "collection_lane_config", "delivery_coordinator"],
        }, ensure_ascii=False))
        return 0 if not errors else 3
    run_id = args.run_id or daily_run_id(config.get("timezone", "Asia/Shanghai"))
    run_dir = runs_dir(config) / run_id
    if args.watch:
        hour = datetime.now(ZoneInfo(config.get('timezone', 'Asia/Shanghai'))).hour
        if not 9 <= hour < 20:
            return 0
    log_dir = logs_path(config)
    lock_path = log_dir / "daily-runner.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"status": "already_running", "run_id": run_id}, ensure_ascii=False))
            return 0

        action = choose_action(run_dir)
        if (run_dir / 'collection.json').exists():
            from spectra_agent.model_health import launch
            launch(ROOT, run_dir, str(ROOT / config.get('llm_python', '.venv-llm/bin/python')))
        if args.watch and action == "deliver_retry" and not release_due(config):
            print(json.dumps({"status": "waiting_for_release_time", "run_id": run_id,
                              "release_time": (config.get("joint_delivery") or {}).get("local_time", "12:30")}, ensure_ascii=False))
            return 0
        from spectra_agent.recovery import inspect, occupied, reserve
        if run_dir.exists() and occupied(run_dir):
            print(json.dumps({'status': 'already_running', 'run_id': run_id}))
            return 0
        # The existing five-minute trigger catches up the 11:00 delta once the
        # morning worker releases its lease. Never creates a second pipeline.
        from spectra_agent.collection_health import read
        now = datetime.now(ZoneInfo(config.get('timezone', 'Asia/Shanghai')))
        midday = read(run_dir / 'midday-collection.json')
        coverage_state = read(run_dir / 'coverage-line.json')
        if (args.watch and now.hour >= 11 and (run_dir / 'collection.json').exists()
                and coverage_state.get('status') != 'running'
                and midday.get('status') != 'completed' and midday.get('attempts', 0) < 2):
            action = 'midday_collect'
        settings = config.get('dingtalk_push') or {}
        if (action == 'noop_completed' and settings.get('enabled')
                and run_id not in settings.get('paused_run_ids', [])
                and read(run_dir / 'dingtalk-push.json').get('status') not in {'sent', 'suppressed'}
                and now.weekday() < 5):
            action = 'deliver_retry'
        decision = inspect(run_dir, action, worker_active=False)
        action = decision['action']
        if action in {'alert', 'cooldown'}:
            from spectra_agent.execution import atomic_json
            atomic_json(run_dir / 'recovery-alert.json', decision)
            print(json.dumps(decision, ensure_ascii=False))
            return 0
        if action.startswith("noop_"):
            state = read_json(run_dir / "run.json")
            notification = (
                review_notification(run_dir, state, config)
                if action == "noop_human_gate" else None
            )
            print(json.dumps({
                "status": state.get("status"),
                "run_id": run_id,
                "action": action,
                "next": "run review_cli.py" if action == "noop_human_gate" else None,
                "notification": notification,
            }, ensure_ascii=False, indent=2))
            return 0

        # A first run must create its own directory. Reserving recovery state
        # here would create the directory early and make run.py reject it as an
        # already-existing run. Recovery actions already have a run directory.
        if recovery_reservation_required(action):
            reserve(run_dir, decision)
        python = str(ROOT / config.get("llm_python", ".venv-llm/bin/python"))
        if action == 'midday_collect':
            collection = read_json(run_dir / 'collection.json')
            command = [python, str(ROOT / 'spectra_agent/coverage_runner.py'),
                       '--config', args.config, '--run-id', run_id, '--midday',
                       '--start', collection['window_end'], '--end', now.isoformat()]
        elif action == "retry_sources":
            collection = read_json(run_dir / 'collection.json')
            command = [python, str(ROOT / 'spectra_agent/coverage_runner.py'),
                       '--config', args.config, '--run-id', run_id, '--retry-failed',
                       '--start', collection['window_start'], '--end', collection['window_end']]
        elif action == "deliver_retry":
            command = [python, str(ROOT / "spectra_agent/daily_delivery.py"),
                       "--config", args.config, "--run-id", run_id, "--wait-for-web"]
        else:
            command = [python, str(ROOT / "spectra_agent/run.py"), "--config", args.config]
        if action == "run":
            command += ["run", "--run-id", run_id, "--days", str(args.days)]
            if not args.no_llm:
                command.append("--llm")
        elif action == "resume_retry":
            command += ["resume", "--run-id", run_id, "--retry"]

        return_code = run_command(command, log_dir / f"{run_id}.log")
        state = read_json(run_dir / "run.json") if (run_dir / "run.json").exists() else {}
        status = state.get("status")
        # run.py returns 2 when it intentionally stops at the human gate.
        successful_pause = status in HUMAN_STATUSES or status == "waiting_for_editorial"
        notification = (
            review_notification(run_dir, state, config)
            if status in HUMAN_STATUSES else None
        )
        result_code = 0 if successful_pause else return_code
        print(json.dumps({
            "status": status or "failed_to_initialize",
            "run_id": run_id,
            "action": action,
            "command_exit": return_code,
            "log": str(log_dir / f"{run_id}.log"),
            "notification": notification,
        }, ensure_ascii=False, indent=2))
        return result_code


if __name__ == "__main__":
    raise SystemExit(main())
