#!/usr/bin/env python3
"""SPECTRA one-command, human-gated rolling intelligence agent.

Commands:
  run     collect + structure + create P1 review queue, then pause
  status  show the current run state and next action
  resume  validate the human review, build events and editorial issue

The workflow never crosses the review gate automatically.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import traceback
import time
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

try:
    from llm_client import load_local_env
except ImportError:  # `python -m unittest` imports this file as spectra_agent.run
    from spectra_agent.llm_client import load_local_env

try:
    from paths import runs_path
except ImportError:
    from spectra_agent.paths import runs_path

try:
    from compat import canonical_artifact, compatible_artifact, config_section
except ImportError:
    from spectra_agent.compat import canonical_artifact, compatible_artifact, config_section

try:
    from acceptance_metrics import distinct_completed_runs, metrics_for_run, rolling_summary, write_outputs
    from review_policy import apply_review_policy
    from run_evaluator import evaluate_run, write_report as write_eval_report
except ImportError:
    from spectra_agent.acceptance_metrics import distinct_completed_runs, metrics_for_run, rolling_summary, write_outputs
    from spectra_agent.review_policy import apply_review_policy
    from spectra_agent.run_evaluator import evaluate_run, write_report as write_eval_report

try:
    from publication_quality import publication_quality_errors
except ImportError:
    from spectra_agent.publication_quality import publication_quality_errors


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from spectra_agent.execution import RunLease, atomic_json, digest, publication_version
from spectra_agent.reliability import mark_recovered, record_failure
DEFAULT_CONFIG = ROOT / "spectra_agent/config.v0.1.json"
TERMINAL = {"completed", "failed"}
STATIC_ASSETS = (
    Path("tokens.css"),
    Path("app/globals.css"),
    Path("app/hallmark-editorial.css"),
    Path("app/accepted-ui.css"),
    Path("app/visual-library.js"),
    Path("app/accepted-ux.js"),
    Path("app/share.js"),
    Path("app/account.js"),
    Path("app/account.css"),
)


class WorkflowError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_json(path, payload)


def prepare_static_draft(run_dir: Path, static_page: Path) -> Path:
    """Copy the report shell and every relative dependency it references."""
    static_draft = run_dir / "rolling-digest.html"
    shutil.copyfile(static_page, static_draft)
    for relative_path in STATIC_ASSETS:
        source = ROOT / relative_path
        if not source.exists():
            raise WorkflowError(f"static asset is missing: {relative_path}")
        target = run_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    source_assets = ROOT / "assets"
    if source_assets.exists():
        shutil.copytree(source_assets, run_dir / "assets", dirs_exist_ok=True)
    return static_draft


def validate_static_package(run_dir: Path, static_draft: Path, issue: dict[str, Any]) -> None:
    """Fail closed when a generated report is not a self-contained local package."""
    if not static_draft.is_file() or static_draft.stat().st_size == 0:
        raise WorkflowError("generated rolling digest is missing")
    html = static_draft.read_text(encoding="utf-8")
    for marker in ("<!-- ISSUE_DATA_START -->", "<!-- ISSUE_DATA_END -->"):
        if html.count(marker) != 1:
            raise WorkflowError(f"rolling digest needs exactly one {marker}")
    if html.count('id="issue-data"') != 1:
        raise WorkflowError("rolling digest needs exactly one embedded issue payload")
    payload_matches = re.findall(
        r'<script\b[^>]*\bid="issue-data"[^>]*>(.*?)</script>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if len(payload_matches) != 1:
        raise WorkflowError("rolling digest issue payload is missing or duplicated")
    try:
        embedded_issue = json.loads(payload_matches[0])
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"rolling digest issue payload is invalid JSON: {exc}") from exc
    if embedded_issue != issue:
        raise WorkflowError("rolling digest issue payload does not match editorial-issue.json")

    run_root = run_dir.resolve()

    def require_packaged_file(relative_url: str, asset_kind: str) -> None:
        clean_url = relative_url.split("?", 1)[0].split("#", 1)[0]
        relative_path = Path(clean_url)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise WorkflowError(f"unsafe local {asset_kind} path: {relative_url}")
        target = (run_dir / relative_path).resolve()
        try:
            target.relative_to(run_root)
        except ValueError as exc:
            raise WorkflowError(f"unsafe local {asset_kind} path: {relative_url}") from exc
        if not target.is_file() or target.stat().st_size == 0:
            raise WorkflowError(f"packaged {asset_kind} asset is missing: {relative_url}")

    stylesheet_tags = re.findall(r"<link\b[^>]*>", html, flags=re.IGNORECASE)
    stylesheet_urls = []
    for tag in stylesheet_tags:
        if not re.search(r'\brel=["\']stylesheet["\']', tag, flags=re.IGNORECASE):
            continue
        href = re.search(r'\bhref=["\']([^"\']+)["\']', tag, flags=re.IGNORECASE)
        if href and not re.match(r"^(?:https?:)?//", href.group(1)):
            stylesheet_urls.append(href.group(1))
    if not stylesheet_urls:
        raise WorkflowError("rolling digest has no packaged stylesheet references")
    for stylesheet_url in stylesheet_urls:
        require_packaged_file(stylesheet_url, "stylesheet")

    for story in issue.get("editorial_stories", []):
        cover_url = str((story.get("cover_image") or {}).get("url") or "")
        if not cover_url.startswith("assets/"):
            continue
        require_packaged_file(cover_url, "cover")


def resolve_config(path: str) -> tuple[Path, dict[str, Any]]:
    config_path = (ROOT / path).resolve() if not Path(path).is_absolute() else Path(path)
    return config_path, read_json(config_path)


def runs_dir(config: dict[str, Any]) -> Path:
    return runs_path(config, ROOT)


def latest_pointer(config: dict[str, Any]) -> Path:
    return runs_dir(config) / "latest.json"


def locate_run(config: dict[str, Any], run_id: str | None) -> Path:
    if run_id:
        path = runs_dir(config) / run_id
    else:
        pointer = latest_pointer(config)
        if not pointer.exists():
            raise WorkflowError("no run exists; start with `run`")
        path = runs_dir(config) / read_json(pointer)["run_id"]
    if not path.exists():
        raise WorkflowError(f"run not found: {path.name}")
    return path


def update_state(run_dir: Path, **changes: Any) -> dict[str, Any]:
    """Atomically update run state with file locking to prevent race conditions."""
    import fcntl
    state_path = run_dir / "run.json"

    # Acquire exclusive lock on state file
    with open(run_dir / 'state.lock', "a+", encoding="utf-8") as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            state = read_json(state_path)
            previous = {"status": state.get("status"), "stage": state.get("current_stage")}
            state.update(changes)
            state["updated_at"] = utc_now()
            current = {"status": state.get("status"), "stage": state.get("current_stage")}
            if current != previous:
                state.setdefault("history", []).append({
                    "at": state["updated_at"],
                    "from": previous,
                    "to": current,
                })
            # Write back to file with lock held
            write_json(state_path, state)
            sync_issue_workflow(run_dir, state)
            return state
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def sync_issue_workflow(run_dir: Path, state: dict[str, Any]) -> None:
    """Project the canonical run state into the generated JSON and HTML."""
    issue_path = run_dir / "editorial-issue.json"
    if not issue_path.exists():
        return
    try:
        issue = read_json(issue_path)
        review_status = (
            read_json(run_dir / "p1-review.json").get("review_status")
            if (run_dir / "p1-review.json").exists()
            else None
        )
    except (OSError, ValueError, json.JSONDecodeError):
        return
    issue["workflow"] = {
        "status": state.get("status"),
        "stage": state.get("current_stage"),
        "review_status": review_status,
        "publish_status": state.get("publish_status", "not_published"),
        "paused_reason": state.get("paused_reason"),
        "updated_at": state.get("updated_at"),
    }
    write_json(issue_path, issue)
    html_path = canonical_artifact(run_dir, "rolling-digest.html")
    if not html_path.exists():
        return
    html = html_path.read_text(encoding="utf-8")
    payload = json.dumps(issue, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    replacement = (
        '<!-- ISSUE_DATA_START --><script id="issue-data" type="application/json">'
        f"{payload}</script><!-- ISSUE_DATA_END -->"
    )
    html, count = re.subn(
        r"<!-- ISSUE_DATA_START -->.*?<!-- ISSUE_DATA_END -->",
        lambda _: replacement,
        html,
        count=1,
        flags=re.S,
    )
    if count == 1:
        html_path.write_text(html, encoding="utf-8")


def log(run_dir: Path, stage: str, message: str, **details: Any) -> None:
    entry = {"at": utc_now(), "stage": stage, "message": message, **details}
    path = run_dir / "run.log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


WORKER_TOKEN_ENV = "SPECTRA_EDITORIAL_WORKER_TOKEN"


def worker_lock_path(run_dir: Path) -> Path:
    return run_dir / "p1-editorial-worker.lock.json"


def process_is_alive(pid: int | None) -> bool:
    if not pid or pid < 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def reserve_editorial_worker(run_dir: Path) -> tuple[str | None, int | None]:
    """Atomically reserve one background Writer slot for a run.

    Uses O_EXCL flag for atomic file creation. The lock file persists
    until the worker completes, preventing duplicate reservations.
    """
    path = worker_lock_path(run_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Check if existing reservation is active
    if path.exists():
        try:
            lock = read_json(path)
            pid = lock.get("pid")
            # If pid is None, lock is reserved but worker hasn't claimed it yet
            if pid is None:
                reserved = parse_timestamp(lock.get('reserved_at', '1970-01-01T00:00:00Z'))
                if (datetime.now(timezone.utc) - reserved).total_seconds() < 120:
                    return None, None
                path.unlink(missing_ok=True)
            # If pid exists and process is alive, lock is active
            if process_is_alive(pid):
                return None, int(pid)
            # Pid exists but process is dead - stale lock, remove it
            path.unlink(missing_ok=True)
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # Corrupted or unreadable - try to remove
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    # Try to atomically create the lock file
    token = secrets.token_hex(16)
    payload = json.dumps({"token": token, "pid": None, "reserved_at": utc_now()}, ensure_ascii=False)
    try:
        # O_EXCL ensures atomic creation - fails if file exists
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        return token, None
    except FileExistsError:
        # Another process created the lock between our check and creation
        # This race window is very small (microseconds)
        try:
            lock = read_json(path)
            return None, int(lock.get("pid")) if process_is_alive(lock.get("pid")) else None
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return None, None


def claim_editorial_worker(run_dir: Path) -> bool:
    """Validate a launcher token or acquire a stale/empty lease for manual recovery."""
    path = worker_lock_path(run_dir)
    supplied = os.environ.get(WORKER_TOKEN_ENV)
    if path.exists():
        try:
            lock = read_json(path)
        except (OSError, ValueError, json.JSONDecodeError):
            lock = {}
        if supplied and supplied == lock.get("token"):
            write_json(path, {**lock, "pid": os.getpid(), "claimed_at": utc_now()})
            return True
        if process_is_alive(lock.get("pid")):
            return False
        path.unlink(missing_ok=True)
    token, active_pid = reserve_editorial_worker(run_dir)
    if not token or active_pid:
        return False
    os.environ[WORKER_TOKEN_ENV] = token
    write_json(path, {"token": token, "pid": os.getpid(), "claimed_at": utc_now()})
    return True


def release_editorial_worker(run_dir: Path) -> None:
    path = worker_lock_path(run_dir)
    if not path.exists():
        return
    try:
        lock = read_json(path)
        if lock.get("pid") == os.getpid() and lock.get("token") == os.environ.get(WORKER_TOKEN_ENV):
            path.unlink(missing_ok=True)
            state_path = run_dir / "run.json"
            if state_path.exists():
                state = read_json(state_path)
                if state.get("editorial_worker_pid") == os.getpid():
                    update_state(run_dir, editorial_worker_pid=None)
    except (OSError, ValueError, json.JSONDecodeError):
        return


def launch_editorial_worker(run_dir: Path, worker_command: list[str], worker_env: dict[str, str], worker_log):
    try:
        return subprocess.Popen(worker_command, cwd=ROOT, stdout=worker_log, stderr=worker_log,
                                start_new_session=True, env=worker_env)
    except Exception as exc:
        worker_lock_path(run_dir).unlink(missing_ok=True)
        log(run_dir, 'p1_editorial_queued', 'worker_start_failed', error=str(exc))
        raise WorkflowError(f'background worker failed to start: {exc}') from exc
def parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def automatic_resume_args(args: argparse.Namespace, run_id: str) -> argparse.Namespace:
    """Preserve root parser options when the review policy resumes internally."""
    return argparse.Namespace(
        run_id=run_id,
        review=None,
        retry=False,
        editorial_worker=False,
        editorial_only=False,
        config=getattr(args, "config", str(DEFAULT_CONFIG.relative_to(ROOT))),
    )


def pre_review_artifacts_recoverable(run_dir: Path, review_path: Path) -> bool:
    """Use persisted artifacts, not a fragile stage-name allowlist, for recovery."""
    if not (run_dir / "collection.json").exists() or not (run_dir / "candidates.json").exists():
        return False
    if not review_path.exists():
        return True
    try:
        review = read_json(review_path)
    except (OSError, ValueError, json.JSONDecodeError):
        return True
    return review.get("review_status") != "approved" and not review.get("verification_harness")


def resume_is_allowed(status: str, retry: bool) -> bool:
    return retry or status in {"waiting_for_review", "waiting_for_editorial_review"}


def rebuild_structure_from_collection(
    run_dir: Path,
    config: dict[str, Any],
    llm_requested: bool,
) -> None:
    """Rebuild structure from a persisted collection without collecting again."""
    collection_path = run_dir / "collection.json"
    candidates_path = run_dir / "candidates.json"
    update_state(run_dir, status="running", current_stage="validate_collection", error=None)
    command(run_dir, "validate_collection", [
        sys.executable,
        "scripts/validate-source-run.py",
        str(collection_path),
    ])
    structure_command = [
        sys.executable,
        "processor/structure.py",
        "--input",
        str(collection_path),
        "--config",
        config["processor_config"],
        "--output",
        str(candidates_path),
    ]
    stage = "structure"
    if llm_requested:
        stage = "llm_structure"
        structure_command[0] = llm_python(config)
        structure_command += [
            "--llm",
            "--llm-checkpoint",
            str(run_dir / "llm-structure-checkpoint.json"),
        ]
    update_state(run_dir, current_stage=stage)
    command(run_dir, stage, structure_command)
    command(run_dir, "validate_structure", [
        sys.executable,
        "scripts/validate-structured-run.py",
        str(candidates_path),
    ])
    if llm_requested:
        candidates = read_json(candidates_path)
        update_state(run_dir, llm=candidates.get("llm"))
    log(run_dir, stage, "structure_rebuilt_from_persisted_collection")


def prepare_retry_artifacts(
    run_dir: Path,
    config: dict[str, Any],
    llm_requested: bool,
) -> bool:
    """Validate persisted inputs and rebuild only a missing or invalid structure artifact."""
    collection_path = run_dir / "collection.json"
    candidates_path = run_dir / "candidates.json"
    try:
        if not collection_path.exists():
            raise WorkflowError(
                "retry cannot continue without collection.json; start a new run to collect again"
            )
        if not candidates_path.exists():
            rebuild_structure_from_collection(run_dir, config, llm_requested)
            return True
        command(run_dir, "validate_collection", [
            sys.executable,
            "scripts/validate-source-run.py",
            str(collection_path),
        ])
        try:
            command(run_dir, "validate_structure", [
                sys.executable,
                "scripts/validate-structured-run.py",
                str(candidates_path),
            ])
            return False
        except WorkflowError:
            rebuild_structure_from_collection(run_dir, config, llm_requested)
            return True
    except Exception as exc:
        state = read_json(run_dir / "run.json")
        current_stage = state.get("current_stage")
        failed_stage = (
            state.get("failed_stage")
            if current_stage == "failed"
            else current_stage
        ) or "retry_prepare"
        incident = record_failure(
            run_dir, failed_stage, exc,
            max_attempts=int((config.get("reliability") or {}).get("max_automatic_attempts", 2)),
        )
        update_state(
            run_dir,
            status="failed",
            current_stage="failed",
            failed_stage=failed_stage,
            error=str(exc),
            last_failure=incident,
            next_action=incident["action"],
        )
        log(run_dir, "failed", "retry_preparation_failed", error=str(exc))
        raise


def find_incremental_baseline(config: dict[str, Any], end: datetime, exclude: Path) -> Path | None:
    """Return the latest valid collection from this ISO week for a daily delta.

    The configured baseline weekday always starts from a fresh rolling window.
    Every other configured incremental weekday may reuse the most recent valid
    collection in the same local ISO week.
    """
    incremental = config.get("incremental_collection") or {}
    if not incremental.get("enabled", False):
        return None
    local_tz = ZoneInfo(config.get("timezone", "Asia/Shanghai"))
    local_end = end.astimezone(local_tz)
    baseline_weekday = int(incremental.get("baseline_weekday", 0))
    configured_days = incremental.get("incremental_weekdays")
    if configured_days is None:
        configured_days = [int(incremental.get("incremental_weekday", 3))]
    allowed_days = {int(day) for day in configured_days}
    if local_end.weekday() == baseline_weekday or local_end.weekday() not in allowed_days:
        return None
    candidates: list[tuple[datetime, Path]] = []
    for directory in runs_dir(config).iterdir() if runs_dir(config).exists() else []:
        collection_path = directory / "collection.json"
        if directory == exclude or not collection_path.exists():
            continue
        try:
            payload = read_json(collection_path)
            baseline_end = parse_timestamp(payload["window_end"])
            local_baseline = baseline_end.astimezone(local_tz)
            records = payload.get("source_records")
            checks = payload.get("source_checks")
            structurally_valid = (
                isinstance(records, list)
                and isinstance(checks, list) and bool(checks)
                and all(
                    record.get("source_id") and record.get("canonical_url") and record.get("content_hash")
                    for record in records
                )
                and all(check.get("registry_id") and check.get("status") for check in checks)
            )
            if (
                local_baseline.isocalendar()[:2] == local_end.isocalendar()[:2]
                and baseline_end < end
                and structurally_valid
            ):
                candidates.append((baseline_end, collection_path))
        except (OSError, KeyError, ValueError, TypeError, json.JSONDecodeError):
            continue
    return max(candidates, default=(None, None), key=lambda item: item[0])[1]


def annotate_display_window(collection_path: Path, config: dict[str, Any]) -> None:
    """Add the rolling collection window used by the daily page."""
    collection = read_json(collection_path)
    window_end = parse_timestamp(collection["window_end"])
    window_start = parse_timestamp(collection["window_start"])
    collection["display_window_start"] = window_start.isoformat().replace("+00:00", "Z")
    collection["display_window_end"] = window_end.isoformat().replace("+00:00", "Z")
    collection["display_window_mode"] = "rolling_7x24_hours"
    write_json(collection_path, collection)


def incremental_retry_source_ids(
    baseline: dict[str, Any],
    delta: dict[str, Any],
) -> list[str]:
    """Return only failed or newly introduced sources that need a full-window retry."""
    return incremental_retry_plan(baseline, delta)[0]


def active_rate_limit_cooldowns(
    collection: dict[str, Any],
    now: datetime,
    cooldown_hours: int = 24,
) -> list[str]:
    """Return sources whose most recent 429 is still inside the cooldown."""
    try:
        observed_at = parse_timestamp(collection["collected_at"])
    except (KeyError, TypeError, ValueError):
        return []
    if now - observed_at >= timedelta(hours=max(0, cooldown_hours)):
        return []
    cooled = []
    for check in collection.get("source_checks", []):
        error_text = " ".join(str(check.get(key) or "") for key in ("error", "warning", "failure_reason"))
        if check.get("http_status") == 429 or re.search(r"(?:HTTP(?: Error)?\s*)?429\b|too many requests|rate.?limit", error_text, re.I):
            cooled.append(check["registry_id"])
    return sorted(cooled)


def incremental_retry_plan(
    baseline: dict[str, Any],
    delta: dict[str, Any],
) -> tuple[list[str], dict[str, str]]:
    """Plan full-window recovery without hammering unavailable sources.

    Incremental collection has already made the current request. A second,
    seven-day request cannot repair an HTTP 429 or an unavailable local WeRSS
    service, so those sources enter a visible cooldown instead.
    """
    baseline_checks = {
        item["registry_id"]: item
        for item in baseline.get("source_checks", [])
    }
    delta_checks = {
        item["registry_id"]: item
        for item in delta.get("source_checks", [])
    }
    failed_now = {
        registry_id
        for registry_id, check in delta_checks.items()
        if check.get("status") == "failed"
    }
    failed_before = {
        registry_id
        for registry_id, check in baseline_checks.items()
        if check.get("status") == "failed"
    }
    newly_configured = set(delta_checks) - set(baseline_checks)
    retry_ids = failed_now | failed_before | newly_configured
    cooled: dict[str, str] = {}
    for registry_id in sorted(retry_ids):
        check = delta_checks.get(registry_id) or baseline_checks.get(registry_id) or {}
        error_text = " ".join(str(check.get(key) or "") for key in ("error", "warning", "failure_reason"))
        if check.get("http_status") == 429 or re.search(r"(?:HTTP(?: Error)?\s*)?429\b|too many requests|rate.?limit", error_text, re.I):
            cooled[registry_id] = "rate_limited_429"
        elif check.get("adapter") == "werss_api" and check.get("status") in {"failed", "degraded"}:
            cooled[registry_id] = "werss_unavailable"
    return sorted(retry_ids - set(cooled)), cooled


def command(run_dir: Path, stage: str, args: list[str], timeout: int | None = None) -> None:
    """Execute a subprocess with timeout and proper error handling.

    Args:
        run_dir: Run directory for logging
        stage: Stage name for logging
        args: Command arguments
        timeout: Timeout in seconds (default: 1800 = 30 minutes)
    """
    if timeout is None:
        # Default timeout: 30 minutes for most commands, 2 hours for LLM/collection
        timeout = 7200 if stage in {"llm_structure", "collect", "p1_editorial_background"} else 1800

    started = time.monotonic()
    stop = threading.Event()
    def pulse():
        while not stop.is_set():
            write_json(run_dir / 'command-progress.json', {
                'stage': stage, 'pid': os.getpid(), 'heartbeat_at': utc_now(),
                'elapsed_seconds': round(time.monotonic() - started, 2),
                'note': 'heartbeat is liveness, not proof of model progress',
            })
            stop.wait(5)
    worker = threading.Thread(target=pulse, daemon=True)
    worker.start()
    log(run_dir, stage, "command_started", command=args, timeout=timeout)
    try:
        result = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
        log(
            run_dir, stage, "command_finished",
            returncode=result.returncode,
            duration_seconds=round(time.monotonic() - started, 3),
            stdout=result.stdout[-4000:] if result.stdout else "",
            stderr=result.stderr[-4000:] if result.stderr else "",
        )
        if result.returncode:
            error_output = (result.stderr or result.stdout or "").strip()
            # Keep more context but still limit to prevent log explosion
            error_excerpt = error_output[-2000:] if error_output else "no output"
            raise WorkflowError(f"{stage} failed with code {result.returncode}: {error_excerpt}")
    except subprocess.TimeoutExpired as exc:
        log(
            run_dir, stage, "command_timeout",
            timeout=timeout,
            stdout=(exc.stdout.decode('utf-8', errors='replace') if isinstance(exc.stdout, bytes) else exc.stdout or '')[-2000:],
            stderr=(exc.stderr.decode('utf-8', errors='replace') if isinstance(exc.stderr, bytes) else exc.stderr or '')[-2000:],
        )
        raise WorkflowError(
            f"{stage} timed out after {timeout} seconds. "
            f"Check {run_dir / 'run.log.jsonl'} for details."
        ) from exc
    finally:
        stop.set()
        worker.join(timeout=6)


def url_reachable(url: str, timeout: float = 2.0) -> bool:
    try:
        with urlopen(Request(url, headers={"User-Agent": "SPECTRA-Agent/0.2"}), timeout=timeout):
            return True
    except Exception:
        return False


def check_werss_service(config: dict[str, Any], run_dir: Path) -> dict[str, Any]:
    """Check the long-running local WeRSS dependency without restarting it.

    The WeChat backend session is process-bound in this WeRSS version. An
    automatic restart would therefore turn a service outage into a forced QR
    login. Failure remains non-fatal so other sources can still be collected.
    """
    service = config.get("werss_service") or {}
    if not service.get("enabled", False):
        return {"status": "disabled"}
    health_url = service.get("health_url", "http://127.0.0.1:8001/api/docs")
    if url_reachable(health_url):
        result = {"status": "running", "health_url": health_url}
        log(run_dir, "collect", "werss_service_ready", **result)
        return result
    result = {
        "status": "failed",
        "error": "WeRSS is not running; start it and restore WeChat authorization before the next Agent run",
    }
    log(run_dir, "collect", "werss_service_failed", **result)
    return result


def collector_python(config: dict[str, Any]) -> str:
    configured = config.get("collector_python")
    if configured:
        candidate = ROOT / configured
        if not candidate.exists():
            raise WorkflowError(f"configured collector Python not found: {candidate}")
        return str(candidate)
    return sys.executable


def llm_python(config: dict[str, Any]) -> str:
    candidate = ROOT / config.get("llm_python", ".venv-llm/bin/python")
    if not candidate.exists():
        raise WorkflowError(f"configured LLM Python not found: {candidate}")
    return str(candidate)


def select_review_candidates(candidates: dict[str, Any], config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build a bounded P1 queue without allowing technical items to crowd out
    every product or market signal.

    Deterministic P1 items remain the backbone. LLM recommendations are only
    used to fill configured type minimums; they never cross the human gate.
    """
    queue_config = config.get("review_queue") or {}
    maximum = int(queue_config.get("max_count", 10))
    all_candidates = [
        item for item in candidates["selected_candidates"]
        if item.get("front_display_eligible", True)
        and ((item.get("hard_gates") or {}).get("content_completeness") or {}).get("status", "pass") == "pass"
        and ((item.get("hard_gates") or {}).get("fact_wording_fidelity") or {}).get("status", "pass") == "pass"
    ]
    backbone = sorted(
        (item for item in all_candidates if item.get("verification_priority") == "priority.p1"),
        key=lambda item: (-item["score"], item["canonical_title"]),
    )
    minimums = queue_config.get("intelligence_type_minimums", {})
    if maximum < 1 or any(int(n) < 0 for n in minimums.values()) or sum(int(n) for n in minimums.values()) > maximum:
        raise WorkflowError("review queue minimum quotas exceed capacity or are invalid")
    selected = []
    selected_ids = set()
    warnings = []

    for intelligence_type, minimum in queue_config.get("intelligence_type_minimums", {}).items():
        present = sum(1 for item in selected if item.get("intelligence_type") == intelligence_type)
        pool = sorted(
            (
                item for item in all_candidates
                if item["candidate_id"] not in selected_ids
                and item.get("intelligence_type") == intelligence_type
                and (item.get("verification_priority") == "priority.p1" or (item.get("llm_analysis") or {}).get("recommended_disposition") in {"p1", "p2"})
            ),
            key=lambda item: (
                0 if (item.get("llm_analysis") or {}).get("recommended_disposition") == "p1" else 1,
                -(item.get("llm_analysis") or {}).get("strategy_relevance_score", 0),
                -item["score"],
                item["canonical_title"],
            ),
        )
        while present < int(minimum) and pool and len(selected) < maximum:
            item = pool.pop(0)
            selected.append(item)
            selected_ids.add(item["candidate_id"])
            present += 1
        if present < int(minimum):
            warnings.append(f"{intelligence_type}: requested {minimum}, available {present}")
    for item in backbone:
        if len(selected) >= maximum:
            break
        if item["candidate_id"] not in selected_ids:
            selected.append(item)
            selected_ids.add(item["candidate_id"])
    candidates["review_queue_warnings"] = warnings
    return selected


def review_template(collection: dict[str, Any], candidates: dict[str, Any], run_id: str,
                    config: dict[str, Any]) -> dict[str, Any]:
    source_map = {item["source_id"]: item for item in collection["source_records"]}
    records = []
    for item in select_review_candidates(candidates, config):
        source = source_map[item["primary_source_id"]]
        llm_analysis = item.get("llm_analysis")
        verification_questions = list(item["verification_questions"])
        for question in (llm_analysis or {}).get("verification_questions", []):
            if question not in verification_questions:
                verification_questions.append(question)
        records.append({
            "candidate_id": item["candidate_id"],
            "source_id": item["primary_source_id"],
            "title": item["canonical_title"],
            "url": source["canonical_url"],
            "published_at": item["published_at"],
            "verification_status": "pending",
            "claims": [],
            "limitation": "",
            "decision": "pending",
            "decision_reason": "",
            "event": None,
            "verification_questions": verification_questions,
            "agent_analysis": llm_analysis,
            "agent_recommendation": (llm_analysis or {}).get("recommended_disposition"),
            "agent_recommendation_reason": (llm_analysis or {}).get("disposition_reason"),
        })
    return {
        "version": "0.2", "record_type": "p1_human_review", "run_id": run_id,
        "review_status": "pending", "verified_at": None, "verified_by": None,
        "editorial_selection": {"rolling_thesis": ""},
        "records": records, "additional_source_records": [],
    }


def gated_review_template(collection: dict[str, Any], candidates: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Create a separate queue for records blocked before P1 editorial review."""
    source_map = {item["source_id"]: item for item in collection["source_records"]}
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in (candidates.get("exclusions") or {}).get("content_incomplete", []):
        source_id = item["source_id"]
        source = source_map.get(source_id, {})
        records.append({
            "queue_id": f"gate_content_{source_id}",
            "gate_type": "content_completeness",
            "source_id": source_id,
            "candidate_id": None,
            "title": item.get("title") or source.get("raw_title"),
            "url": source.get("canonical_url"),
            "gate": item.get("gate"),
            "review_status": "pending",
            "decision": None,
            "allowed_decisions": ["retry", "supply_verified_text", "exclude"],
        })
        seen.add(source_id)
    fidelity_candidates: dict[str, dict[str, Any]] = {
        item["candidate_id"]: item for item in candidates.get("gated_candidates", [])
    }
    for item in candidates.get("selected_candidates", []):
        fidelity = ((item.get("hard_gates") or {}).get("fact_wording_fidelity") or {})
        if fidelity.get("status") != "pass":
            fidelity_candidates.setdefault(item["candidate_id"], item)
    for candidate in fidelity_candidates.values():
        source_id = candidate.get("primary_source_id")
        if not source_id or source_id in seen:
            continue
        source = source_map.get(source_id, {})
        fidelity = (candidate.get("hard_gates") or {}).get("fact_wording_fidelity") or {}
        records.append({
            "queue_id": f"gate_fidelity_{candidate['candidate_id']}",
            "gate_type": "fact_wording_fidelity",
            "source_id": source_id,
            "candidate_id": candidate["candidate_id"],
            "title": candidate.get("canonical_title") or source.get("raw_title"),
            "url": source.get("canonical_url"),
            "gate": fidelity,
            "agent_analysis": candidate.get("llm_analysis"),
            "review_status": "pending",
            "decision": None,
            "allowed_decisions": ["verify_and_rewrite_with_attribution", "watch", "exclude"],
        })
    return {
        "version": "0.1",
        "record_type": "pre_p1_gate_review",
        "run_id": run_id,
        "status": "waiting_for_review" if records else "not_required",
        "count": len(records),
        "records": records,
    }


def attach_harness_evidence(review: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    """Attach machine-located evidence without converting it into human claims."""
    evidence_map = {item["candidate_id"]: item for item in evidence.get("records", [])}
    for record in review.get("records", []):
        packet = evidence_map.get(record["candidate_id"])
        if not packet:
            continue
        record["evidence_review_id"] = packet["evidence_review_id"]
        record["suggested_evidence"] = packet.get("claim_reviews", [])
        record["approve_all_suggested_facts"] = False
        record["fact_review_instructions"] = "逐条设置 human_fact_decision=keep/modify/drop，或明确将 approve_all_suggested_facts 设为 true。"
        record["risk_flags"] = packet.get("risk_flags", [])
        record["harness_confidence"] = packet.get("confidence")
        record["agent_recommendation"] = packet.get("agent_recommendation")
        record["verification_status"] = "pending"
        record["claims"] = []
        record["decision"] = "pending"
    review["verification_harness"] = {
        "status": evidence.get("status"),
        "summary": evidence.get("summary", {}),
        "evidence_file": "evidence-review.json",
        "note": "suggested_evidence仅为机器定位结果，不等于人工核验后的claims。",
    }
    return review


def materialize_fact_decisions(review: dict[str, Any]) -> dict[str, Any]:
    """Convert explicit fact-level human decisions into formal review claims."""
    for record in review.get("records", []):
        if record.get("decision") not in {"include", "watch"}:
            continue
        suggestions = record.get("suggested_evidence") or []
        if not suggestions:
            continue  # Legacy explicitly authored claims have no suggested queue.
        approve_all = record.get("approve_all_suggested_facts") is True
        claims = []
        pending = []
        for index, item in enumerate(suggestions, 1):
            decision = "keep" if approve_all else item.get("human_fact_decision", "pending")
            if approve_all and (
                item.get("support_status") != "supported"
                or item.get("numeric_match") is False
                or item.get("risk_flags")
            ):
                raise WorkflowError(
                    f"{record['candidate_id']}: approve_all_suggested_facts cannot include risky fact {index}; review it individually"
                )
            if decision == "pending":
                pending.append(index)
                continue
            if decision == "drop":
                continue
            if decision not in {"keep", "modify"}:
                raise WorkflowError(f"{record['candidate_id']}: invalid human_fact_decision at fact {index}")
            text = item.get("claim") if decision == "keep" else item.get("human_fact_text")
            kind = (item.get("kind") or "reported_fact") if decision == "keep" else item.get("human_fact_kind")
            if not text or not kind:
                raise WorkflowError(f"{record['candidate_id']}: modified fact {index} requires text and kind")
            claims.append({
                "text": text,
                "kind": kind,
                "locator": item.get("locator"),
                "quote_excerpt": item.get("evidence_text"),
            })
        if pending and not approve_all:
            raise WorkflowError(
                f"{record['candidate_id']}: fact-level review incomplete; pending facts: {pending}"
            )
        record["claims"] = claims
        record['facts_version'] = digest(claims)
    return review


def materialize_review_metadata(review: dict[str, Any], candidates: dict[str, Any],
                                collection: dict[str, Any]) -> dict[str, Any]:
    """Fill non-factual event routing metadata after facts are approved."""
    candidate_map = {item["candidate_id"]: item for item in candidates.get("selected_candidates", [])}
    source_map = {item["source_id"]: item for item in collection.get("source_records", [])}
    event_types = {
        "type.product_release": "event.product_release",
        "type.industry_market": "event.market",
        "type.technology_breakthrough": "event.research",
        "type.company_strategy": "event.company_strategy",
    }
    confidences = {
        "high": "confidence.high",
        "medium": "confidence.medium",
        "low": "confidence.low",
    }
    for record in review.get("records", []):
        if record.get("decision") != "include":
            continue
        candidate = candidate_map.get(record.get("candidate_id"))
        source = source_map.get(record.get("source_id"), {})
        if not candidate:
            raise WorkflowError(f"{record.get('candidate_id')}: structured candidate is missing")
        existing_event = record.get("event") or {}
        if record.get('verification_status') != 'verified_primary':
            raise WorkflowError(f"{record['candidate_id']}: include requires explicit primary-source verification")
        record.setdefault('reviewed_by', review.get('verified_by'))
        record.setdefault('reviewed_at', review.get('verified_at'))
        record.setdefault('review_method', 'automated_policy' if review.get('verified_by') == 'verification_policy'
                          else 'legacy_top_level_review')
        risks = record.get("risk_flags") or []
        if not record.get("limitation"):
            record["limitation"] = (
                "仅确认人工保留的原文事实；风险标记不作为独立结论。"
                if risks else "仅确认人工保留的原文事实，不外推未被证据支持的效果、因果或趋势。"
            )
        entity_name = existing_event.get("entity_name") or source.get("publisher") or candidate.get("canonical_title") or record.get("title")
        record["event"] = {
            "event_id": "evt_" + record["candidate_id"].removeprefix("cand_"),
            "canonical_title": existing_event.get("canonical_title") or candidate.get("canonical_title") or record.get("title"),
            "event_type": event_types.get(candidate.get("intelligence_type"), "event.research"),
            "primary_route": candidate.get("primary_route"),
            "secondary_routes": candidate.get("secondary_routes") or [],
            "priority": "priority.p1",
            "confidence": confidences.get(record.get("harness_confidence"), "confidence.medium"),
            "entity_name": entity_name,
            "tags": candidate.get("tags") or {},
            "track": candidate.get("track", "track.emerging"),
        }
    return review


def write_review_instructions(run_dir: Path, count: int, gated_count: int = 0) -> None:
    text = f"""# P1 人工核验闸门

本次共有 **{count} 条 P1 候选**。主流程已暂停，不会在核验完成前生成或发布滚动情报。

另有 **{gated_count} 条**因正文完整度或事实措辞保真未通过，已写入 `gated-review.json`。这些记录不会进入 `p1-review.json` 或P1深读；只有补齐正文或人工确认并按原文限定重写后，才能在后续运行中重新参与筛选。

请编辑 `p1-review.json`，每条候选必须完成：

1. 先查看 `evidence-review.json` 及每条记录的 `suggested_evidence`；这些是机器定位建议，不等于核验结论；
2. 阅读并确认原始来源，将 `verification_status` 改为 `verified_primary`；
3. 对每条 `suggested_evidence` 设置 `human_fact_decision=keep/modify/drop`；修改时填写 `human_fact_text` 与 `human_fact_kind`。如果逐条确认后全部保留，可明确将记录级 `approve_all_suggested_facts` 设为 `true`；系统随后自动生成 `claims`；
4. 填写 `limitation`；
5. 将 `decision` 设为 `include`、`watch` 或 `exclude`，并填写 `decision_reason`；
6. `include` 的记录会依据结构化候选自动补齐 `event` 路由元数据，不需要人工重复填写；
7. 全部完成后填写顶层 `verified_at`、`verified_by`，将 `review_status` 改为 `approved`。

完成后运行：

```bash
python3 spectra_agent/run.py resume --run-id {run_dir.name}
```
"""
    (run_dir / "REVIEW.md").write_text(text, encoding="utf-8")


def create_run(args: argparse.Namespace, config: dict[str, Any]) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    args.run_id = args.run_id or f"spectra_{stamp}"
    target = runs_dir(config) / args.run_id
    target.parent.mkdir(parents=True, exist_ok=True)
    # A separate creation lease avoids making the run directory before preflight.
    lease = RunLease(target.parent / '.creation-locks' / args.run_id)
    if not lease.acquire():
        raise WorkflowError(f"run creation already active: {args.run_id}")
    try:
        return _create_run(args, config)
    finally:
        lease.release()


def _create_run(args: argparse.Namespace, config: dict[str, Any]) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.run_id or f"spectra_{stamp}"
    run_dir = runs_dir(config) / run_id
    required = [ROOT / config['collection_config'], ROOT / config['processor_config'], ROOT / config['static_page']]
    if args.from_collection:
        required.append((ROOT / args.from_collection).resolve())
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise WorkflowError('preflight missing required input/config: ' + ', '.join(missing))
    for key in ('collector_python', 'llm_python'):
        executable = Path(config.get(key, ''))
        executable = executable if executable.is_absolute() else ROOT / executable
        if (key == 'collector_python' or args.llm) and not executable.is_file():
            raise WorkflowError(f'preflight missing {key}: {executable}')
    if run_dir.exists():
        raise WorkflowError(f"run already exists: {run_id}")
    run_dir.mkdir(parents=True)
    if args.llm_checkpoint_source:
        source_checkpoint = ROOT / args.llm_checkpoint_source
        if not source_checkpoint.exists():
            raise WorkflowError(f"LLM checkpoint source not found: {source_checkpoint}")
        shutil.copyfile(source_checkpoint, run_dir / "llm-structure-checkpoint.json")
    state = {
        "schema_version": "0.1", "record_type": "spectra_agent_run", "run_id": run_id,
        "status": "initialized", "created_at": utc_now(), "updated_at": utc_now(),
        "current_stage": "initialize", "paused_reason": None, "error": None,
        "publish_status": "not_published",
        "llm_requested": bool(args.llm),
        "llm": None,
        "artifacts": {
            "collection": "collection.json", "candidates": "candidates.json", "review": "p1-review.json",
            "gated_review": "gated-review.json",
            "verification_candidates": "verification-candidates.json",
            "evidence_review": "evidence-review.json",
            "p1_fact_expansion_checkpoint": "p1-fact-expansion-checkpoint.json",
            "core_event_checkpoint": "core-event-checkpoint.json",
            "core_event_audit": "core-event-audit.json",
            "discussion_radar": "discussion-radar.json",
            "verified": "verified-events.json", "issue": "editorial-issue.json",
            "web_draft": "rolling-digest.html", "report": "run-report.md",
        },
        "history": [],
    }
    write_json(run_dir / "run.json", state)
    write_json(latest_pointer(config), {"run_id": run_id})
    try:
        update_state(run_dir, status="running", current_stage="collect")
        collection_path = run_dir / "collection.json"
        target_end = parse_timestamp(args.end) if args.end else datetime.now(timezone.utc)
        baseline_path = find_incremental_baseline(config, target_end, run_dir)
        if args.from_collection:
            shutil.copyfile(ROOT / args.from_collection, collection_path)
            log(run_dir, "collect", "reused_collection", source=args.from_collection)
        elif baseline_path:
            baseline = read_json(baseline_path)
            baseline_end = parse_timestamp(baseline["window_end"])
            overlap_hours = max(
                0,
                int((config.get("incremental_collection") or {}).get("overlap_hours", 6)),
            )
            delta_start = baseline_end - timedelta(hours=overlap_hours)
            desired_start = target_end - timedelta(days=int(args.days or config.get("schedule", {}).get("window_days", 7)))
            delta_path = run_dir / "collection.incremental.json"
            check_werss_service(config, run_dir)
            cooldown_hours = int((config.get("incremental_collection") or {}).get("rate_limit_cooldown_hours", 24))
            active_cooldowns = active_rate_limit_cooldowns(baseline, target_end, cooldown_hours)
            delta_cmd = [
                collector_python(config), "collector/collect.py", "--config", config["collection_config"],
                "--output", str(delta_path), "--start", delta_start.isoformat(), "--end", target_end.isoformat(),
            ]
            for registry_id in active_cooldowns:
                delta_cmd += ["--exclude-source", registry_id]
            if args.newscrawler_command:
                delta_cmd += ["--newscrawler-command", args.newscrawler_command]
            command(run_dir, "collect_incremental", delta_cmd)
            delta = read_json(delta_path)
            retry_ids, cooled_sources = incremental_retry_plan(baseline, delta)
            if cooled_sources:
                log(run_dir, "collect", "full_window_retry_cooled", sources=cooled_sources)
            retry_path = run_dir / "collection.full-window-retry.json"
            if retry_ids:
                retry_cmd = [
                    collector_python(config), "collector/collect.py", "--config", config["collection_config"],
                    "--output", str(retry_path), "--start", desired_start.isoformat(), "--end", target_end.isoformat(),
                ]
                for registry_id in retry_ids:
                    retry_cmd += ["--source", registry_id]
                if args.newscrawler_command:
                    retry_cmd += ["--newscrawler-command", args.newscrawler_command]
                command(run_dir, "collect_full_window_retry", retry_cmd)
            merge_cmd = [
                sys.executable, "collector/merge_incremental_runs.py",
                "--baseline", str(baseline_path), "--delta", str(delta_path),
                "--window-start", desired_start.isoformat(), "--window-end", target_end.isoformat(),
                "--output", str(collection_path),
            ]
            if retry_path.exists():
                merge_cmd += ["--retry", str(retry_path)]
            command(run_dir, "merge_incremental", merge_cmd)
            checkpoint_available = (run_dir / "llm-structure-checkpoint.json").exists()
            if (
                args.llm
                and (config.get("incremental_collection") or {}).get("reuse_llm_checkpoint", True)
                and not checkpoint_available
            ):
                previous_checkpoint = baseline_path.parent / "llm-structure-checkpoint.json"
                if previous_checkpoint.exists():
                    shutil.copyfile(previous_checkpoint, run_dir / "llm-structure-checkpoint.json")
                    checkpoint_available = True
                previous_fact_checkpoint = baseline_path.parent / "p1-fact-expansion-checkpoint.json"
                if previous_fact_checkpoint.exists():
                    shutil.copyfile(previous_fact_checkpoint, run_dir / "p1-fact-expansion-checkpoint.json")
            merged_collection = read_json(collection_path)
            incremental_summary = merged_collection.get("summary") or {}
            update_state(
                run_dir,
                collection_mode="daily_incremental",
                incremental_baseline_run=baseline_path.parent.name,
                incremental_overlap_hours=overlap_hours,
                incremental_changed_records=incremental_summary.get("changed_records", 0),
                incremental_unchanged_records=incremental_summary.get("unchanged_records", 0),
                full_window_retry_cooled_sources=cooled_sources,
                active_rate_limit_cooldowns=active_cooldowns,
                incremental_processing_mode=(
                    "deterministic_without_llm"
                    if not args.llm
                    else (
                        "fingerprint_checkpoint_reuse"
                        if checkpoint_available
                        else "full_structure_fallback_missing_checkpoint"
                    )
                ),
            )
            log(
                run_dir,
                "collect",
                "daily_incremental_merged",
                baseline_run=baseline_path.parent.name,
                overlap_hours=overlap_hours,
                retry_sources=retry_ids,
                changed_records=incremental_summary.get("changed_records", 0),
                unchanged_records=incremental_summary.get("unchanged_records", 0),
                checkpoint_available=checkpoint_available,
            )
        else:
            check_werss_service(config, run_dir)
            cmd = [collector_python(config), "collector/collect.py", "--config", config["collection_config"], "--output", str(collection_path)]
            cmd += ["--end", target_end.isoformat()]
            if args.days:
                cmd += ["--days", str(args.days)]
            if args.newscrawler_command:
                cmd += ["--newscrawler-command", args.newscrawler_command]
            command(run_dir, "collect", cmd)
            update_state(
                run_dir,
                collection_mode="full_baseline",
                incremental_fallback_reason="scheduled_rolling_baseline_or_valid_recent_baseline_not_found",
            )
        annotate_display_window(collection_path, config)
        command(run_dir, "validate_collection", [sys.executable, "scripts/validate-source-run.py", str(collection_path)])

        radar_config = config.get("discussion_radar") or {"enabled": True, "required": False}
        if radar_config.get("enabled", True):
            update_state(run_dir, current_stage="discussion_radar")
            try:
                command(run_dir, "discussion_radar", [
                    sys.executable,
                    "processor/discussion_radar.py",
                    "--input", str(collection_path),
                    "--watchlist", config.get("observer_watchlist", "collector/observer_watchlist.v0.1.json"),
                    "--output", str(run_dir / "discussion-radar.json"),
                ])
            except Exception as exc:
                if radar_config.get("required", False):
                    raise
                incident = record_failure(
                    run_dir, "discussion_radar", exc,
                    max_attempts=int((config.get("reliability") or {}).get("max_automatic_attempts", 2)),
                    scope="optional_stage", blocking=False,
                )
                update_state(
                    run_dir, reliability_status="degraded",
                    last_warning=incident,
                )
                log(run_dir, "discussion_radar", "optional_stage_failed", error=str(exc))
        else:
            log(run_dir, "discussion_radar", "optional_stage_skipped")

        update_state(run_dir, current_stage="structure")
        candidates_path = run_dir / "candidates.json"
        structure_cmd = [sys.executable, "processor/structure.py", "--input", str(collection_path), "--config", config["processor_config"], "--output", str(candidates_path)]
        if args.llm:
            update_state(run_dir, current_stage="llm_structure")
            structure_cmd[0] = llm_python(config)
            structure_cmd += ["--llm", "--llm-checkpoint", str(run_dir / "llm-structure-checkpoint.json")]
        command(run_dir, "llm_structure" if args.llm else "structure", structure_cmd)
        command(run_dir, "validate_structure", [sys.executable, "scripts/validate-structured-run.py", str(candidates_path)])
        candidates = read_json(candidates_path)
        if args.llm:
            update_state(run_dir, llm=candidates.get("llm"))
            log(run_dir, "llm_structure", "llm_structure_completed", **(candidates.get("llm") or {}))
        collection = read_json(collection_path)
        template = review_template(collection, candidates, run_id, config)
        gated_template = gated_review_template(collection, candidates, run_id)
        write_json(run_dir / "p1-review.json", template)
        write_json(run_dir / "gated-review.json", gated_template)

        update_state(run_dir, current_stage="verification_harness")
        command(run_dir, "verification_harness", [
            sys.executable,
            "verification/verification_harness.py",
            "--collection", str(collection_path),
            "--candidates", str(candidates_path),
            "--review", str(run_dir / "p1-review.json"),
            "--verification-output", str(run_dir / "verification-candidates.json"),
            "--evidence-output", str(run_dir / "evidence-review.json"),
        ])
        evidence_path = run_dir / "evidence-review.json"
        fact_expander_config = config.get("p1_fact_expander") or {}
        if state.get("llm_requested") and fact_expander_config.get("enabled", True):
            update_state(run_dir, current_stage="p1_fact_expansion")
            fact_expansion_command = [
                llm_python(config), "verification/p1_fact_expander.py",
                "--evidence", str(evidence_path),
                "--collection", str(run_dir / "collection.json"),
                "--output", str(evidence_path),
                "--checkpoint", str(run_dir / "p1-fact-expansion-checkpoint.json"),
            ]
            if fact_expander_config.get("model"):
                fact_expansion_command += ["--model", str(fact_expander_config["model"])]
            if fact_expander_config.get("num_ctx"):
                fact_expansion_command += ["--num-ctx", str(fact_expander_config["num_ctx"])]
            try:
                command(run_dir, "p1_fact_expansion", fact_expansion_command)
            except WorkflowError:
                if fact_expander_config.get("required", False):
                    raise
                log(run_dir, "p1_fact_expansion", "fact_expansion_failed_using_harness_claims")
        evidence = read_json(evidence_path)
        template = attach_harness_evidence(template, evidence)
        template = apply_review_policy(
            template, candidates, evidence, config.get("review_policy"), collection
        )
        write_json(run_dir / "p1-review.json", template)
        confidence_summary = evidence.get("summary", {})
        log(run_dir, "verification_harness", "evidence_packet_ready", **confidence_summary)

        policy = template.get("review_policy") or {}
        if (
            template["records"]
            and not policy.get("manual_review_required", True)
            and not policy.get("run_gate_preserved", True)
        ):
            template["review_status"] = "approved"
            template["verified_at"] = utc_now()
            template["verified_by"] = "verification_policy"
            write_json(run_dir / "p1-review.json", template)
            update_state(
                run_dir, status="waiting_for_review", current_stage="auto_fact_lock",
                paused_reason=None,
            )
            log(run_dir, "auto_fact_lock", "all_p1_facts_auto_locked", **policy.get("counts", {}))
            return _resume_run(automatic_resume_args(args, run_id), config)

        write_review_instructions(run_dir, len(template["records"]), gated_template["count"])
        update_state(run_dir, status="waiting_for_review", current_stage="human_review", paused_reason="P1 primary-source verification required")
        log(run_dir, "human_review", "workflow_paused", p1_candidates=len(template["records"]), gated_candidates=gated_template["count"])
        print(json.dumps({"run_id": run_id, "status": "waiting_for_review", "p1_candidates": len(template["records"]), "gated_candidates": gated_template["count"], "review_file": str(run_dir / "p1-review.json"), "gated_review_file": str(run_dir / "gated-review.json"), "next": f"python3 spectra_agent/run.py resume --run-id {run_id}"}, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        failed_stage = read_json(run_dir / "run.json").get("current_stage")
        incident = record_failure(
            run_dir, failed_stage or "unknown", exc,
            max_attempts=int((config.get("reliability") or {}).get("max_automatic_attempts", 2)),
        )
        update_state(
            run_dir, status="failed", current_stage="failed", failed_stage=failed_stage,
            error=str(exc), last_failure=incident, next_action=incident["action"],
        )
        log(run_dir, "failed", "workflow_failed", error=str(exc), traceback=traceback.format_exc())
        raise


def resume_run(args: argparse.Namespace, config: dict[str, Any]) -> int:
    run_dir = locate_run(config, args.run_id)
    creation = RunLease(run_dir.parent / '.creation-locks' / run_dir.name)
    if not creation.acquire(wait=bool(os.environ.get(WORKER_TOKEN_ENV))):
        print(json.dumps({'run_id': run_dir.name, 'status': 'already_running', 'stage': 'creating'}))
        return 0
    creation.release()
    lease = RunLease(run_dir)
    if not lease.acquire(wait=bool(os.environ.get(WORKER_TOKEN_ENV))):
        print(json.dumps({'run_id': run_dir.name, 'status': 'already_running',
                          'progress': read_json(run_dir / 'run.json') .get('current_stage'),
                          'next': f'python3 spectra_agent/run.py status --run-id {run_dir.name}'}, ensure_ascii=False))
        return 0
    try:
        return _resume_run(args, config)
    finally:
        lease.release()


def _resume_run(args: argparse.Namespace, config: dict[str, Any]) -> int:
    run_dir = locate_run(config, args.run_id)
    state = read_json(run_dir / "run.json")
    missing = [name for name in ('collection.json', 'candidates.json') if not (run_dir / name).is_file()]
    if missing:
        raise WorkflowError('resume preflight missing required artifacts: ' + ', '.join(missing))
    resumed_localization_issue = None
    resumed_localization_review = None
    existing_issue = run_dir / "editorial-issue.json"
    existing_review = run_dir / "p2-localization-review.json"
    if existing_issue.exists():
        resumed_localization_issue = read_json(existing_issue)
    if existing_review.exists():
        resumed_localization_review = read_json(existing_review)
    evaluation_path = run_dir / 'eval-report.json'
    current_version = publication_version(run_dir)
    evaluation_current = evaluation_path.exists() and read_json(evaluation_path).get('content_version') == current_version
    if state["status"] == "completed" and not args.retry and evaluation_current:
        print(json.dumps({"run_id": run_dir.name, "status": "completed", "message": "nothing to resume"}, ensure_ascii=False, indent=2))
        return 0
    resume_from_editorial_review = state["status"] == "waiting_for_editorial_review" or (run_dir / 'core-event-checkpoint.json').exists()
    if not resume_is_allowed(state["status"], args.retry) and not (args.retry and state['status'] in {'running', 'completed', 'waiting_for_editorial', 'interrupted'}):
        raise WorkflowError(f"run is {state['status']}; resume requires a review state (or --retry after fixing a failed run)")
    # A resumed run is no longer complete. Clear the old terminal timestamp up
    # front so pauses or interruptions cannot report contradictory state.
    update_state(
        run_dir,
        status="running",
        completed_at=None,
        error=None,
    )
    review_path = run_dir / "p1-review.json"
    if args.review:
        shutil.copyfile(ROOT / args.review, review_path)
        log(run_dir, "human_review", "review_imported", source=args.review)
    collection_path = run_dir / "collection.json"
    candidates_path = run_dir / "candidates.json"
    structure_rebuilt = (
        prepare_retry_artifacts(
            run_dir,
            config,
            bool(state.get("llm_requested")),
        )
        if args.retry
        else False
    )
    recoverable_pre_review = pre_review_artifacts_recoverable(run_dir, review_path)
    if args.retry and (structure_rebuilt or recoverable_pre_review):
        # Recover a run that failed after structure output was persisted but
        # before the review packet was fully materialized.  An interrupted
        # fact-expansion stage may already have a preliminary p1-review.json;
        # rebuild it from persisted evidence and checkpoints without repeating
        # collection or model structuring.
        update_state(run_dir, status="running", current_stage="validate_structure", error=None)
        command(run_dir, "validate_structure", [
            sys.executable, "scripts/validate-structured-run.py", str(candidates_path)
        ])
        collection = read_json(collection_path)
        candidates = read_json(candidates_path)
        template = review_template(collection, candidates, run_dir.name, config)
        gated_template = gated_review_template(collection, candidates, run_dir.name)
        write_json(review_path, template)
        write_json(run_dir / "gated-review.json", gated_template)

        update_state(run_dir, current_stage="verification_harness")
        command(run_dir, "verification_harness", [
            sys.executable,
            "verification/verification_harness.py",
            "--collection", str(collection_path),
            "--candidates", str(candidates_path),
            "--review", str(review_path),
            "--verification-output", str(run_dir / "verification-candidates.json"),
            "--evidence-output", str(run_dir / "evidence-review.json"),
        ])
        evidence_path = run_dir / "evidence-review.json"
        fact_expander_config = config.get("p1_fact_expander") or {}
        if state.get("llm_requested") and fact_expander_config.get("enabled", True):
            update_state(run_dir, current_stage="p1_fact_expansion")
            fact_expansion_command = [
                llm_python(config), "verification/p1_fact_expander.py",
                "--evidence", str(evidence_path),
                "--collection", str(collection_path),
                "--output", str(evidence_path),
                "--checkpoint", str(run_dir / "p1-fact-expansion-checkpoint.json"),
            ]
            if fact_expander_config.get("model"):
                fact_expansion_command += ["--model", str(fact_expander_config["model"])]
            if fact_expander_config.get("num_ctx"):
                fact_expansion_command += ["--num-ctx", str(fact_expander_config["num_ctx"])]
            try:
                command(run_dir, "p1_fact_expansion", fact_expansion_command)
            except WorkflowError:
                if fact_expander_config.get("required", False):
                    raise
                log(run_dir, "p1_fact_expansion", "fact_expansion_failed_using_harness_claims")
        evidence = read_json(evidence_path)
        template = attach_harness_evidence(template, evidence)
        template = apply_review_policy(
            template, candidates, evidence, config.get("review_policy"), collection
        )
        write_json(review_path, template)
        policy = template.get("review_policy") or {}
        if (
            template["records"]
            and not policy.get("manual_review_required", True)
            and not policy.get("run_gate_preserved", True)
        ):
            template["review_status"] = "approved"
            template["verified_at"] = utc_now()
            template["verified_by"] = "verification_policy"
            write_json(review_path, template)
            update_state(
                run_dir, status="waiting_for_review", current_stage="auto_fact_lock",
                paused_reason=None, error=None,
            )
            log(run_dir, "auto_fact_lock", "all_p1_facts_auto_locked", **policy.get("counts", {}))
            return _resume_run(automatic_resume_args(args, run_dir.name), config)
        write_review_instructions(run_dir, len(template["records"]), gated_template["count"])
        update_state(
            run_dir,
            status="waiting_for_review",
            current_stage="human_review",
            paused_reason="P1 primary-source verification required",
            error=None,
        )
        log(
            run_dir, "human_review", "workflow_recovered_and_paused",
            p1_candidates=len(template["records"]), gated_candidates=gated_template["count"],
        )
        print(json.dumps({
            "run_id": run_dir.name,
            "status": "waiting_for_review",
            "p1_candidates": len(template["records"]),
            "gated_candidates": gated_template["count"],
            "review_file": str(review_path),
            "gated_review_file": str(run_dir / "gated-review.json"),
            "publish_status": "not_published",
        }, ensure_ascii=False, indent=2))
        return 2
    review = materialize_fact_decisions(read_json(review_path))
    review = materialize_review_metadata(
        review,
        read_json(run_dir / "candidates.json"),
        read_json(run_dir / "collection.json"),
    )
    write_json(review_path, review)
    if review.get("review_status") != "approved" or not review.get("verified_at") or not review.get("verified_by"):
        raise WorkflowError("review is not approved: set review_status, verified_at and verified_by")
    try:
        update_state(run_dir, status="running", current_stage="verify", paused_reason=None, error=None)
        verified_path = run_dir / "verified-events.json"
        command(run_dir, "verify", [sys.executable, "verification/build-final-events.py", "--review", str(review_path), "--collection", str(run_dir / "collection.json"), "--candidates", str(run_dir / "candidates.json"), "--output", str(verified_path)])
        command(run_dir, "validate_verified", [sys.executable, "scripts/validate-verified-events.py", "--verified", str(verified_path), "--collection", str(run_dir / "collection.json")])
        verified = read_json(verified_path)
        count = verified["summary"]["included_events"]
        approved_count = sum(
            item.get("decision") == "include" for item in review.get("records", [])
        )
        attainable_minimum = min(config["minimum_formal_events"], approved_count)
        if not attainable_minimum <= count <= config["maximum_formal_events"]:
            raise WorkflowError(f"formal event count outside configured boundary: {count}")

        fact_selection_path = run_dir / "fact-selection.json"
        update_state(run_dir, current_stage="fact_selection")
        command(run_dir, "fact_selection", [
            sys.executable, "editorial/fact_selection.py",
            "--verified", str(verified_path),
            "--review", str(review_path),
            "--collection", str(run_dir / "collection.json"),
            "--output", str(fact_selection_path),
        ])

        core_event_config = config_section(config, "core_event_writer")
        editorial_drafts_path = canonical_artifact(run_dir, "core-event-drafts.json")
        editorial_audit_path = canonical_artifact(run_dir, "core-event-audit.json")
        editorial_checkpoint_path = canonical_artifact(run_dir, "core-event-checkpoint.json")
        background_mode = core_event_config.get("generation_mode") == "serial_background"
        if state.get("llm_requested") and background_mode and not args.editorial_worker and not resume_from_editorial_review:
            worker_token, active_pid = reserve_editorial_worker(run_dir)
            if not worker_token:
                # Distinguish between active worker and reserved-but-unclaimed lock
                if active_pid is not None:
                    # Case 1: Active worker is running
                    paused_reason = "existing qwen3:14b P1 editorial worker is still running"
                    message = "existing editorial worker retained"
                else:
                    # Case 2: Lock is reserved but worker hasn't claimed it yet (race window)
                    paused_reason = "editorial worker slot is reserved, waiting for worker to start"
                    message = "editorial worker slot already reserved"

                update_state(
                    run_dir, status="waiting_for_editorial", current_stage="p1_editorial_queued",
                    paused_reason=paused_reason,
                    editorial_worker_pid=active_pid,
                )
                log(run_dir, "p1_editorial_queued", "duplicate_worker_start_prevented", pid=active_pid)
                print(json.dumps({
                    "run_id": run_dir.name, "status": "waiting_for_editorial",
                    "worker_pid": active_pid, "message": message,
                    "publish_status": "not_published",
                }, ensure_ascii=False, indent=2))
                return 0
            update_state(
                run_dir,
                status="waiting_for_editorial",
                current_stage="p1_editorial_queued",
                paused_reason="qwen3:14b P1 editorial worker is running",
                error=None,
            )
            worker_log = (run_dir / "p1-editorial-worker.log").open("a", encoding="utf-8")
            worker_command = [
                sys.executable, str(ROOT / "spectra_agent/run.py"),
                "--config", getattr(args, "config", str(DEFAULT_CONFIG.relative_to(ROOT))),
                "resume", "--run-id", run_dir.name, "--retry", "--editorial-worker",
            ]
            worker_env = os.environ.copy()
            worker_env[WORKER_TOKEN_ENV] = worker_token
            try:
                process = launch_editorial_worker(run_dir, worker_command, worker_env, worker_log)
            finally:
                worker_log.close()
            write_json(worker_lock_path(run_dir), {
                "token": worker_token, "pid": process.pid,
                "reserved_at": utc_now(), "command": "p1_editorial_worker",
            })
            update_state(run_dir, editorial_worker_pid=process.pid)
            log(run_dir, "p1_editorial_queued", "background_worker_started", pid=process.pid)
            print(json.dumps({
                "run_id": run_dir.name,
                "status": "waiting_for_editorial",
                "worker_pid": process.pid,
                "checkpoint": str(editorial_checkpoint_path),
                "publish_status": "not_published",
            }, ensure_ascii=False, indent=2))
            return 0
        if state.get("llm_requested") and core_event_config.get("enabled", True):
            update_state(run_dir, current_stage="p1_editorial_background")
            # A retry must never reuse a draft bundle left by an earlier writer
            # attempt that subsequently failed validation.
            if editorial_drafts_path.exists():
                editorial_drafts_path.unlink()
            try:
                core_event_command = [
                    llm_python(config), "editorial/core_event_pipeline.py",
                    "--verified", str(verified_path),
                    "--fact-selection", str(fact_selection_path),
                    "--output", str(editorial_drafts_path),
                    "--audit-output", str(editorial_audit_path),
                    "--checkpoint", str(editorial_checkpoint_path),
                ]
                if core_event_config.get("model"):
                    core_event_command += ["--model", str(core_event_config["model"])]
                if core_event_config.get("num_ctx"):
                    core_event_command += ["--num-ctx", str(core_event_config["num_ctx"])]
                if core_event_config.get("num_predict"):
                    core_event_command += ["--num-predict", str(core_event_config["num_predict"])]
                if core_event_config.get("max_attempts"):
                    core_event_command += ["--max-attempts", str(core_event_config["max_attempts"])]
                command(run_dir, "p1_editorial_background", core_event_command)
            except WorkflowError:
                if core_event_config.get("required", False):
                    raise
                log(run_dir, "p1_editorial_background", "writer_failed_using_quick_read_fallback")

        if args.editorial_only:
            if not state.get('llm_requested') or not core_event_config.get('enabled', True) or not editorial_checkpoint_path.exists():
                raise WorkflowError('--editorial-only requires enabled Writer and a current checkpoint')
            checkpoint = read_json(editorial_checkpoint_path)
            jobs = list((checkpoint.get("jobs") or {}).values())
            update_state(
                run_dir,
                status="waiting_for_editorial_review",
                current_stage="p1_editorial_review",
                paused_reason="P1 Writer acceptance review requested; publication stages not run",
                error=None,
                publish_status="not_published",
            )
            print(json.dumps({
                "run_id": run_dir.name,
                "status": "waiting_for_editorial_review",
                "writer_jobs": len(jobs),
                "auto_passed": sum(item.get("status") == "completed" for item in jobs),
                "demoted": sum(str(item.get("status", "")).startswith("demoted") for item in jobs),
                "audit": str(editorial_audit_path),
                "checkpoint": str(editorial_checkpoint_path),
                "publish_status": "not_published",
            }, ensure_ascii=False, indent=2))
            return 0

        update_state(run_dir, current_stage="generate")
        issue_path = run_dir / "editorial-issue.json"
        static_draft = prepare_static_draft(run_dir, ROOT / config["static_page"])
        # Run data may live outside the source checkout. The builder accepts
        # absolute paths, so never force data artifacts through relative_to(ROOT).
        generate_command = [
            sys.executable, "editorial/build-editorial-issue.py",
            "--verified", str(verified_path),
            "--review", str(review_path),
            "--candidates", str(run_dir / "candidates.json"),
            "--collection", str(run_dir / "collection.json"),
            "--output", str(issue_path),
            "--static", str(static_draft),
        ]
        if editorial_drafts_path.exists():
            generate_command += ["--drafts", str(editorial_drafts_path)]
        command(run_dir, "generate", generate_command)
        issue = read_json(issue_path)
        if resumed_localization_issue is not None:
            from processor.p2_localizer import restore_reviewed_localizations
            restored = restore_reviewed_localizations(
                issue, resumed_localization_issue, resumed_localization_review,
            )
            write_json(issue_path, issue)
            log(run_dir, "generate", "validated_localization_progress_restored", **restored)
        minimum_core_events = int(
            (config.get("publication_quality") or {}).get("minimum_core_events", 1)
        )
        core_event_count = sum(
            story.get("article_type") == "core_event"
            for story in issue.get("editorial_stories", [])
        )
        from spectra_agent.publication_quality import brief_only_allowed
        brief_only = brief_only_allowed(issue, config)
        update_state(run_dir, publication_mode="brief_only" if brief_only else "standard")
        if core_event_count < minimum_core_events and not brief_only:
            update_state(
                run_dir,
                status="waiting_for_editorial_review",
                current_stage="content_quality_review",
                paused_reason=(
                    f"core_event quality gate: {core_event_count}/{minimum_core_events}"
                ),
                error=None,
                publish_status="not_published",
            )
            print(json.dumps({
                "run_id": run_dir.name,
                "status": "waiting_for_editorial_review",
                "current_stage": "content_quality_review",
                "core_event_count": core_event_count,
                "minimum_core_events": minimum_core_events,
                "publish_status": "not_published",
            }, ensure_ascii=False, indent=2))
            return 2
        localizer_config = config.get("p2_localizer") or config.get("p2_translation") or {}
        localization_review = run_dir / "p2-localization-review.json"
        if state.get("llm_requested") and localizer_config.get("enabled", True):
            localization_checkpoint = run_dir / "p2-localization-checkpoint.json"
            update_state(run_dir, current_stage="p2_localizer")
            localize_command = [
                llm_python(config), "processor/p2_localizer.py",
                "--input", str(issue_path),
                "--output", str(issue_path),
                "--checkpoint", str(localization_checkpoint),
                "--review-queue", str(localization_review),
                "--static", str(static_draft),
                "--batch-size", str(localizer_config.get("batch_size", 6)),
            ]
            if localizer_config.get("model"):
                localize_command += ["--model", str(localizer_config["model"])]
            if localizer_config.get("num_ctx"):
                localize_command += ["--num-ctx", str(localizer_config["num_ctx"])]
            command(run_dir, "p2_localizer", localize_command)
        issue = read_json(issue_path)
        blocked_localizations = int((issue.get("localization") or {}).get("blocked_briefs", 0))
        if blocked_localizations:
            update_state(
                run_dir,
                status="waiting_for_editorial_review",
                current_stage="localization_review",
                paused_reason=f"{blocked_localizations} localized briefs require review",
                error=None,
                publish_status="not_published",
            )
            print(json.dumps({
                "run_id": run_dir.name,
                "status": "waiting_for_editorial_review",
                "current_stage": "localization_review",
                "blocked_briefs": blocked_localizations,
                "review_file": str(localization_review),
                "publish_status": "not_published",
            }, ensure_ascii=False, indent=2))
            return 2
        if (config.get('image_generation') or {}).get('enabled', False):
            from spectra_agent.image_generation import prepare as prepare_image_generation
            image_jobs = prepare_image_generation(run_dir)
            if image_jobs['status'] == 'pending':
                update_state(run_dir, status='waiting_for_editorial_review', current_stage='image_generation',
                             paused_reason='waiting for actual Imagegen output', publish_status='not_published')
                return 2
            issue = read_json(issue_path)
        from spectra_agent.publication_quality import requires_cover
        pending_image_ids = [
            story.get("story_id")
            for story in issue.get("editorial_stories", [])
            if requires_cover(story)
            and (story.get("cover_image") or {}).get("kind") in {"editorial_diagram", "generated", "source", "official"}
            and (story.get("cover_image") or {}).get("review_status") != "approved"
        ]
        if pending_image_ids:
            update_state(
                run_dir,
                status="waiting_for_editorial_review",
                current_stage="image_preview",
                paused_reason="generated story covers require human preview",
                pending_image_story_ids=pending_image_ids,
                error=None,
            )
            print(json.dumps({
                "run_id": run_dir.name,
                "status": "waiting_for_editorial_review",
                "pending_image_story_ids": pending_image_ids,
                "next": (
                    f"python3 spectra_agent/image_review.py --run-dir {run_dir} "
                    "--approve all --reviewer <name>, then resume"
                ),
                "publish_status": "not_published",
            }, ensure_ascii=False, indent=2))
            return 2
        update_state(run_dir, pending_image_story_ids=[])
        issue = read_json(issue_path)
        command(run_dir, "validate_issue", [sys.executable, "scripts/validate-editorial-issue.py", "--editorial", str(issue_path), "--verified", str(verified_path), "--config", str(resolve_config(args.config)[0])])
        validate_static_package(run_dir, static_draft, issue)
        reader_quality_errors = publication_quality_errors(issue, config, asset_root=run_dir)
        if reader_quality_errors:
            raise WorkflowError("reader-facing publication quality failed: " + "; ".join(reader_quality_errors))
        evaluation_config = config.get("run_evaluation") or {}
        if (config.get('semantic_review') or {}).get('enabled', False):
            update_state(run_dir, current_stage='semantic_review')
            from spectra_agent.semantic_review import review_run
            from spectra_agent.llm_client import create_llm_client
            semantic_config = config['semantic_review']
            prior_model = os.environ.get('SPECTRA_MODEL')
            os.environ['SPECTRA_MODEL'] = semantic_config.get('model', 'qwen3:8b')
            try:
                semantic = review_run(run_dir, create_llm_client(semantic_config.get('provider', 'ollama')),
                                      int(semantic_config.get('max_source_chars', 24000)))
            finally:
                if prior_model is None:
                    os.environ.pop('SPECTRA_MODEL', None)
                else:
                    os.environ['SPECTRA_MODEL'] = prior_model
            log(run_dir, 'semantic_review', 'shadow_review_completed', elapsed_seconds=semantic['elapsed_seconds'])
            semantic_non_pass = [item for item in semantic['records'] if item['status'] != 'pass']
            if semantic_non_pass and semantic_config.get('block_on_non_pass', True):
                update_state(run_dir, status='waiting_for_editorial_review', current_stage='semantic_review',
                             paused_reason=f"semantic review requires action for {len(semantic_non_pass)} stories",
                             error=None, publish_status='not_published')
                print(json.dumps({'run_id': run_dir.name, 'status': 'waiting_for_editorial_review',
                    'current_stage': 'semantic_review', 'results': {status: sum(r['status'] == status for r in semantic['records'])
                    for status in ('pass', 'rework', 'manual')}, 'report': str(run_dir / 'semantic-review.json'),
                    'next': 'revise rework items; supplement evidence or review manual items; then resume --retry',
                    'publish_status': 'not_published'}, ensure_ascii=False, indent=2))
                return 2
        evaluation = None
        if evaluation_config.get("enabled", False):
            update_state(run_dir, current_stage="run_evaluation")
            evaluation = evaluate_run(run_dir, config, runs_dir(config))
            evaluation['content_version'] = publication_version(run_dir)
            write_eval_report(
                evaluation,
                run_dir / "eval-report.json",
                run_dir / "eval-report.md",
            )
            log(
                run_dir,
                "run_evaluation",
                "run_evaluation_completed",
                evaluation_status=evaluation["status"],
                allow_expand=evaluation["rolling_advice"]["allow_expand"],
                failed_checks=evaluation["failed_checks"],
            )
            if evaluation["status"] == "fail" and evaluation_config.get("block_completion_on_failure", False):
                raise WorkflowError(
                    "run evaluation failed and block_completion_on_failure is enabled: "
                    + ", ".join(evaluation["failed_checks"])
                )
        reliability_status = mark_recovered(run_dir, "complete")
        update_state(
            run_dir,
            status="completed",
            current_stage="complete",
            paused_reason=None,
            error=None,
            publish_status="not_published",
            completed_at=utc_now(),
            evaluation_status=evaluation["status"] if evaluation else "disabled",
            evaluation_report="eval-report.json" if evaluation else None,
            automation_expansion_advice=(evaluation or {}).get("rolling_advice", {}).get("allow_expand"),
            reliability_status=reliability_status,
            last_failure=None,
            next_action=None,
        )
        write_run_report(run_dir, read_json(run_dir / 'collection.json'), read_json(run_dir / 'candidates.json'), verified, issue)
        try:
            acceptance = metrics_for_run(run_dir)
            if acceptance:
                write_json(run_dir / "acceptance-metrics.json", acceptance)
                acceptance_runs_dir = runs_dir(config)
                acceptance_summary = rolling_summary(distinct_completed_runs(acceptance_runs_dir, 3), 3)
                write_outputs(acceptance_summary, acceptance_runs_dir / "acceptance-summary.json",
                              acceptance_runs_dir / "acceptance-summary.md")
            update_state(run_dir, metrics_status="completed")
        except Exception as metrics_error:
            incident = record_failure(
                run_dir, "metrics", metrics_error,
                max_attempts=int((config.get("reliability") or {}).get("max_automatic_attempts", 2)),
                scope="optional_stage", blocking=False,
            )
            update_state(
                run_dir, metrics_status="failed", metrics_error=str(metrics_error),
                reliability_status="degraded", last_warning=incident,
            )
            log(run_dir, "metrics", "auxiliary_metrics_failed", error=str(metrics_error))
        log(run_dir, "complete", "workflow_completed", events=count, stories=len(issue["editorial_stories"]))
        print(json.dumps({
            "run_id": run_dir.name,
            "status": "completed",
            "formal_events": count,
            "stories": len(issue["editorial_stories"]),
            "p2_briefs": len(issue.get("news_briefs", [])),
            "p2_localization_blocked": (issue.get("localization") or {}).get("blocked_briefs", 0),
            "p2_localization_review": str(localization_review) if localization_review.exists() else None,
            "p1_editorial_audit": str(editorial_audit_path) if editorial_audit_path.exists() else None,
            "evaluation_status": evaluation["status"] if evaluation else "disabled",
            "evaluation_report": str(run_dir / "eval-report.json") if evaluation else None,
            "allow_expand": (evaluation or {}).get("rolling_advice", {}).get("allow_expand"),
            "issue": str(issue_path),
            "static_draft": str(static_draft),
            "publish_status": "not_published",
        }, ensure_ascii=False, indent=2))
        delivery = config.get("joint_delivery") or {}
        if delivery.get("enabled") and run_dir.name >= "daily-" + str(delivery.get("start_date", "9999-12-31")).replace("-", ""):
            try:
                with (run_dir / "joint-delivery.log").open("a") as delivery_log:
                    subprocess.Popen([sys.executable, str(ROOT / "spectra_agent/dingtalk_push.py"),
                        "--config", str(resolve_config(args.config)[0]), "--run-id", run_dir.name],
                        cwd=ROOT, stdout=delivery_log, stderr=delivery_log, start_new_session=True)
            except OSError as exc:
                log(run_dir, "delivery", "delivery_launch_failed", error=str(exc))
        return 0
    except Exception as exc:
        failed_stage = read_json(run_dir / "run.json").get("current_stage")
        incident = record_failure(
            run_dir, failed_stage or "unknown", exc,
            max_attempts=int((config.get("reliability") or {}).get("max_automatic_attempts", 2)),
        )
        update_state(
            run_dir, status="failed", current_stage="failed", failed_stage=failed_stage,
            error=str(exc), last_failure=incident, next_action=incident["action"],
        )
        log(run_dir, "failed", "resume_failed", error=str(exc), traceback=traceback.format_exc())
        raise


def write_run_report(run_dir: Path, collection: dict[str, Any], candidates: dict[str, Any], verified: dict[str, Any], issue: dict[str, Any]) -> None:
    failed = [item for item in collection["source_checks"] if item["status"] == "failed"]
    lines = [
        f"# SPECTRA Agent 运行报告 — {run_dir.name}", "",
        f"- 状态：完成", f"- 原始记录：{collection['summary']['source_records']}",
        f"- 候选事件：{candidates['summary']['selected_for_verification']}",
        f"- P1人工核验：{verified['summary']['p1_reviewed']}",
        f"- 正式事件：{verified['summary']['included_events']}",
        f"- 观察事件：{verified['summary']['watchlist_events']}",
        f"- 情报文章：{len(issue['editorial_stories'])}",
        f"- P2短讯：{len(issue.get('news_briefs', []))}",
        f"- P2中文化拦截：{(issue.get('localization') or {}).get('blocked_briefs', 0)}",
        f"- 失败来源：{len(failed)}", "",
        "## 失败来源", "",
    ]
    lines.extend(f"- {item['registry_id']}：{item.get('error', 'unknown error')}" for item in failed)
    if not failed:
        lines.append("- 无")
    review = read_json(run_dir / 'p1-review.json')
    methods = sorted({record.get('review_method', 'unknown') for record in review.get('records', [])})
    lines += ["", "## 审核边界", "",
              f"- P1审核主体：{review.get('verified_by') or '未记录'}",
              f"- P1审核方式：{', '.join(methods)}",
              "- 采用内容、原文核验、自动评测与人工发布确认是四个独立状态。", ""]
    (run_dir / "run-report.md").write_text("\n".join(lines), encoding="utf-8")


def show_status(args: argparse.Namespace, config: dict[str, Any]) -> int:
    run_dir = locate_run(config, args.run_id)
    state = read_json(run_dir / "run.json")
    lease = RunLease(run_dir)
    run_executor_active = not lease.acquire(record=False)
    if not run_executor_active:
        lease.release()
    creation_lease = RunLease(run_dir.parent / '.creation-locks' / run_dir.name)
    creation_executor_active = not creation_lease.acquire(record=False)
    if not creation_executor_active:
        creation_lease.release()
    executor_active = run_executor_active or creation_executor_active
    if state.get('status') in {'running', 'waiting_for_editorial'} and not executor_active:
        age = (datetime.now(timezone.utc) - parse_timestamp(state.get('updated_at') or state.get('created_at'))).total_seconds()
        if age > 30:
            interrupted_stage = state.get('current_stage') or 'unknown'
            incident = record_failure(
                run_dir, interrupted_stage,
                'executor stopped without a live run lease or recent progress',
                max_attempts=2,
            )
            update_state(run_dir, status='interrupted', current_stage='interrupted',
                         error=('recorded executor holds no run lease; a PID alone is not progress'),
                         failed_stage=interrupted_stage, last_failure=incident,
                         next_action=incident['action'], reliability_status='blocked')
            state = read_json(run_dir / 'run.json')
    result = {key: state.get(key) for key in ("run_id", "status", "current_stage", "publish_status", "reliability_status", "paused_reason", "error", "last_failure", "last_warning", "next_action", "created_at", "updated_at", "completed_at") if state.get(key) is not None}
    result['executor_active'] = executor_active
    result["run_dir"] = str(run_dir)
    if (run_dir / "eval-report.json").exists():
        evaluation = read_json(run_dir / "eval-report.json")
        result["evaluation"] = {
            "status": evaluation.get("status"),
            "allow_expand": (evaluation.get("rolling_advice") or {}).get("allow_expand"),
            "report": str(run_dir / "eval-report.json"),
        }
    if state["status"] == "waiting_for_review":
        review = read_json(run_dir / "p1-review.json")
        result["p1_candidates"] = len(review["records"])
        result["next"] = f"complete {run_dir / 'p1-review.json'}, then run resume"
    elif state["status"] == "waiting_for_editorial":
        result["worker_pid"] = state.get("editorial_worker_pid")
        result["checkpoint"] = str(compatible_artifact(run_dir, "core-event-checkpoint.json"))
        result["worker_log"] = str(run_dir / "p1-editorial-worker.log")
        result["recovery"] = f"if the worker stops, run: python3 spectra_agent/run.py resume --run-id {run_dir.name} --retry"
    elif state['status'] == 'waiting_for_editorial_review' and state.get('current_stage') == 'image_preview':
        result['next'] = f"preview covers, then run image_review.py for {run_dir} and resume"
    elif state['status'] == 'waiting_for_editorial_review' and state.get('current_stage') == 'semantic_review':
        result['next'] = f"review {run_dir / 'semantic-review.json'}, revise or supplement evidence, then resume --retry"
    elif state['status'] == 'waiting_for_editorial_review':
        result['next'] = f"review core-event-audit.json and Writer output in {run_dir}, then resume --retry"
    elif state['status'] == 'failed':
        result['next'] = (state.get('last_failure') or {}).get('next') or f"fix the reported error, then run resume --run-id {run_dir.name} --retry"
    elif state['status'] == 'interrupted':
        failure = state.get('last_failure') or {}
        if failure.get('exhausted'):
            result['next'] = failure.get('next') or 'automatic recovery is exhausted; inspect the failure before retrying'
        else:
            result['next'] = f"run resume --run-id {run_dir.name} --retry; completed checkpoints will be reused"
    elif state['status'] == 'completed':
        result['next'] = 'publication remains a separate explicit action' if state.get('publish_status') != 'published' else 'published'
    progress_path = run_dir / 'command-progress.json'
    if progress_path.exists():
        result['command_progress'] = read_json(progress_path)
    stage_progress = {}
    checkpoint = compatible_artifact(run_dir, 'core-event-checkpoint.json')
    if checkpoint.exists():
        jobs = list((read_json(checkpoint).get('jobs') or {}).values())
        stage_progress['writer'] = {
            'completed': sum(job.get('status') == 'completed' for job in jobs),
            'total': len(jobs),
        }
    localization = run_dir / 'p2-localization-checkpoint.json'
    if localization.exists():
        value = read_json(localization)
        stage_progress['localization'] = {'completed': value.get('processed', 0), 'total': value.get('total', 0)}
    if stage_progress:
        result['stage_progress'] = stage_progress
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="SPECTRA human-gated intelligence agent")
    root.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    sub = root.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--run-id")
    run.add_argument("--from-collection", help="reuse a collected source_record bundle; still runs all downstream stages")
    run.add_argument("--end")
    run.add_argument("--days", type=int)
    run.add_argument("--newscrawler-command")
    run.add_argument("--llm", action="store_true", help="run LLM enrichment before creating the review queue")
    run.add_argument("--llm-checkpoint-source", help="import an existing LLM checkpoint for resume or acceptance testing")
    status = sub.add_parser("status")
    status.add_argument("--run-id")
    resume = sub.add_parser("resume")
    resume.add_argument("--run-id")
    resume.add_argument("--review", help="import an approved review JSON before resuming")
    resume.add_argument("--retry", action="store_true", help="retry a failed resume after correcting its input")
    resume.add_argument("--editorial-worker", action="store_true", help=argparse.SUPPRESS)
    resume.add_argument("--editorial-only", action="store_true", help="stop after P1 Writer and its audit; do not build or publish an issue")
    return root


def main() -> int:
    args = parser().parse_args()
    load_local_env()
    _, config = resolve_config(args.config)
    worker_run_dir = None
    try:
        if args.command == "run":
            return create_run(args, config)
        if args.command == "resume":
            if args.editorial_worker:
                worker_run_dir = locate_run(config, args.run_id)
                if not claim_editorial_worker(worker_run_dir):
                    print(json.dumps({
                        "run_id": worker_run_dir.name,
                        "status": "waiting_for_editorial",
                        "message": "another editorial worker already owns this run",
                    }, ensure_ascii=False, indent=2))
                    return 0
            return resume_run(args, config)
        return show_status(args, config)
    except WorkflowError as exc:
        print(json.dumps({"result": "blocked", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 3
    finally:
        if worker_run_dir is not None:
            release_editorial_worker(worker_run_dir)


if __name__ == "__main__":
    raise SystemExit(main())
