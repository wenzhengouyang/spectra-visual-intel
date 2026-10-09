#!/usr/bin/env python3
"""Show a deduplicated macOS dialog when a SPECTRA Run needs a person."""

from __future__ import annotations

import json
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

try:
    from spectra_agent.compat import compatible_artifact
except ImportError:
    from compat import compatible_artifact


APPLESCRIPT = r'''
on run argv
  set terminalCommand to item 1 of argv
  tell application "Terminal"
    activate
    do script terminalCommand
  end tell
end run
'''.strip()


def review_target(run_dir: Path, state: dict[str, Any]) -> Path:
    if state.get("status") == "waiting_for_review":
        review_guide = run_dir / "REVIEW.md"
        return review_guide if review_guide.exists() else run_dir / "p1-review.json"
    digest = compatible_artifact(run_dir, "rolling-digest.html")
    return digest if digest.exists() else run_dir


def notification_key(state: dict[str, Any]) -> str:
    # Bump the key when the popup workflow changes so an old, non-actionable
    # notification cannot suppress the repaired review window.
    return f"terminal_review_v2:{state.get('status', 'unknown')}:{state.get('current_stage', 'unknown')}"


def terminal_review_command(run_dir: Path, state: dict[str, Any]) -> str:
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv-llm/bin/python"
    status = state.get("status")
    stage = state.get("current_stage")
    if status == "waiting_for_review":
        command = [
            str(python), str(root / "spectra_agent/review_cli.py"),
            "--config", "spectra_agent/config.v0.1.json",
            "--run-id", run_dir.name, "--interactive", "--resume",
        ]
    elif status == "waiting_for_editorial_review" and stage == "localization_review":
        command = [
            str(python), str(root / "scripts/localization-review-terminal.py"),
            "--run-dir", str(run_dir),
        ]
    elif status == "waiting_for_editorial_review" and stage == "image_preview":
        command = [
            str(python), str(root / "scripts/image-review-terminal.py"),
            "--run-dir", str(run_dir),
        ]
    else:
        command = [
            str(python), str(root / "spectra_agent/run.py"),
            "--config", "spectra_agent/config.v0.1.json",
            "status", "--run-id", run_dir.name,
        ]
    return f"cd {shlex.quote(str(root))}; {shlex.join(command)}; printf '\\n审核命令已结束，按回车关闭窗口。'; read"


def show_review_popup(
    run_dir: Path,
    state: dict[str, Any],
    settings: dict[str, Any],
    *,
    launcher: Callable[..., Any] = subprocess.run,
) -> dict[str, Any]:
    if not settings.get("enabled", True):
        return {"status": "disabled"}
    key = notification_key(state)
    marker = run_dir / "local-review-notification.json"
    try:
        record = json.loads(marker.read_text(encoding="utf-8")) if marker.exists() else {"shown_keys": []}
    except (OSError, ValueError, json.JSONDecodeError):
        record = {"shown_keys": []}
    shown_keys = list(record.get("shown_keys") or [])
    if key in shown_keys:
        return {"status": "already_shown", "key": key}

    target = review_target(run_dir, state)
    terminal_command = terminal_review_command(run_dir, state)
    command = [
        "/usr/bin/osascript", "-e", APPLESCRIPT,
        terminal_command,
    ]
    try:
        result = launcher(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=20,
        )
        if getattr(result, "returncode", 0) != 0:
            error = getattr(result, "stderr", b"")
            if isinstance(error, bytes):
                error = error.decode(errors="replace")
            return {"status": "failed", "key": key, "error": str(error)[:500]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"status": "failed", "key": key, "error": str(exc)}

    shown_keys.append(key)
    record.update({
        "status": "shown",
        "shown_keys": shown_keys,
        "last_key": key,
        "target": str(target),
        "shown_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    marker.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "shown", "key": key, "target": str(target)}
