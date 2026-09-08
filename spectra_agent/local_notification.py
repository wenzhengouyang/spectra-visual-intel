#!/usr/bin/env python3
"""Show a deduplicated macOS dialog when a SPECTRA Run needs a person."""

from __future__ import annotations

import json
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
  set dialogTitle to item 1 of argv
  set dialogMessage to item 2 of argv
  set reviewTarget to item 3 of argv
  set timeoutSeconds to (item 4 of argv) as integer
  set answer to display dialog dialogMessage with title dialogTitle buttons {"稍后处理", "打开审核"} default button "打开审核" with icon caution giving up after timeoutSeconds
  if gave up of answer is false and button returned of answer is "打开审核" then
    do shell script "/usr/bin/open " & quoted form of reviewTarget
  end if
end run
'''.strip()


def review_target(run_dir: Path, state: dict[str, Any]) -> Path:
    if state.get("status") == "waiting_for_review":
        review_guide = run_dir / "REVIEW.md"
        return review_guide if review_guide.exists() else run_dir / "p1-review.json"
    digest = compatible_artifact(run_dir, "rolling-digest.html")
    return digest if digest.exists() else run_dir


def notification_key(state: dict[str, Any]) -> str:
    return f"{state.get('status', 'unknown')}:{state.get('current_stage', 'unknown')}"


def popup_message(run_dir: Path, state: dict[str, Any]) -> str:
    if state.get("status") == "waiting_for_review":
        try:
            review = json.loads((run_dir / "p1-review.json").read_text(encoding="utf-8"))
            count = len(review.get("records") or [])
        except (OSError, ValueError, json.JSONDecodeError):
            count = 0
        detail = f"有 {count} 条 P1 候选等待一手来源与事实审核。" if count else "P1 候选正在等待人工审核。"
    else:
        detail = "稿件质量、图片或本地化结果正在等待人工确认。"
    reason = str(state.get("paused_reason") or "人工审核闸门已触发")
    return f"{run_dir.name}\n\n{detail}\n\n原因：{reason}\n\n点击“打开审核”查看对应文件。"


def show_review_popup(
    run_dir: Path,
    state: dict[str, Any],
    settings: dict[str, Any],
    *,
    launcher: Callable[..., Any] = subprocess.Popen,
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
    timeout = max(60, int(settings.get("dialog_timeout_seconds", 86400)))
    command = [
        "/usr/bin/osascript", "-e", APPLESCRIPT,
        "SPECTRA 需要人工审核", popup_message(run_dir, state), str(target), str(timeout),
    ]
    try:
        launcher(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
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
