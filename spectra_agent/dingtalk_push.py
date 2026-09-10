#!/usr/bin/env python3
"""Push the published daily SPECTRA issue to DingTalk at the scheduled time.

The command is deliberately fail-closed: an unpublished, incomplete, or
unconfigured run is never presented as a successful notification.
"""

from __future__ import annotations

import argparse
import fcntl
import subprocess
import sys
import base64
import hashlib
import hmac
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from spectra_agent.compat import rolling_thesis
    from spectra_agent.daily_runner import daily_run_id
    from spectra_agent.llm_client import load_local_env
    from spectra_agent.run import DEFAULT_CONFIG, ROOT, read_json, resolve_config, runs_dir, write_json
except ImportError:
    from compat import rolling_thesis
    from daily_runner import daily_run_id
    from llm_client import load_local_env
    from run import DEFAULT_CONFIG, ROOT, read_json, resolve_config, runs_dir, write_json


def signed_webhook(webhook: str, secret: str | None, now_ms: int | None = None) -> str:
    parsed = urllib.parse.urlsplit(webhook)
    if parsed.scheme != "https" or parsed.hostname not in {"oapi.dingtalk.com", "api.dingtalk.com"}:
        raise ValueError("DingTalk webhook must use HTTPS on an official dingtalk.com host")
    if not secret:
        return webhook
    timestamp = now_ms if now_ms is not None else int(time.time() * 1000)
    signature = base64.b64encode(
        hmac.new(secret.encode("utf-8"), f"{timestamp}\n{secret}".encode("utf-8"), hashlib.sha256).digest()
    ).decode("ascii")
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    query.extend((("timestamp", str(timestamp)), ("sign", signature)))
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(query), parsed.fragment))


def _field_text(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("text")
    return str(value or "").strip()


def _compact(value: object, limit: int = 140) -> str:
    text = " ".join(_field_text(value).split())
    for source in ("$\\mathcal{L}_{motion}$", "\\mathcal{L}_{motion}", "$L_{motion}$", "L_{motion}"):
        text = text.replace(source, "L_motion")
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip("，。；、 ") + "…"


def _top_stories(issue: dict, meta: dict, limit: int = 3) -> list[dict]:
    stories = issue.get("editorial_stories") or []
    by_id = {str(story.get("story_id")): story for story in stories}
    ranked = [by_id[story_id] for story_id in meta.get("top_story_ids") or [] if story_id in by_id]
    if len(ranked) < limit:
        remaining = [story for story in stories if story not in ranked]
        ranked.extend(sorted(remaining, key=lambda story: float(story.get("editorial_score") or 0), reverse=True))
    return ranked[:limit]


def _bold_data(text: str) -> str:
    pattern = r"(\d+(?:\.\d+)?(?:%|倍|个|项|条|万|亿|GB|TB|fps|帧|秒|分钟|小时|天|年|D))"
    return re.sub(pattern, r"**\1**", text)


def notification_payload(issue: dict, public_url: str, header_image_url: str = "") -> dict:
    meta = issue.get("issue") or {}
    report_date = str(meta.get("report_date") or meta.get("period_end") or "")
    date_label = f"{report_date[5:7]}月{report_date[8:10]}日" if len(report_date) >= 10 else "今日"
    title = f"SPECTRA · {date_label}"
    thesis = _compact(rolling_thesis(meta, "今日情报已完成更新。"), 100)
    items = []
    for index, story in enumerate(_top_stories(issue, meta), start=1):
        headline = _compact(story.get("headline") or "今日重点情报", 72)
        headline = re.sub(r"([\\\[\]*_`])", r"\\\1", headline)
        source_url = next((str(source.get("url") or "") for source in story.get("source_links") or []
                           if urllib.parse.urlsplit(str(source.get("url") or "")).scheme == "https"), "")
        headline_link = f"[{headline}]({source_url.replace('(', '%28').replace(')', '%29')})" if source_url else headline
        summary = _bold_data(_compact(story.get("one_line_takeaway") or story.get("dek"), 56))
        items.append(
            f"**🔹 {index:02d}｜{headline_link}**\n\n"
            f"{summary or '打开工作台查看详情'}"
        )
    top_three = "\n\n".join(items) or "今日暂无达到发布标准的焦点事件"
    blocks = []
    if header_image_url:
        blocks.append(f"![SPECTRA 今日视觉情报]({header_image_url})")
    blocks.extend((
        f"{date_label} · 近7日视觉情报",
        f"**⚡ 30 秒结论**\n\n{thesis}",
        "**🔥 今日 Top 3 焦点**",
        top_three,
        f"[打开 SPECTRA 网页工作台 →]({public_url})",
    ))
    text = "\n\n".join(blocks)
    return {"msgtype": "markdown", "markdown": {"title": title, "text": text}}


def push(webhook: str, secret: str | None, payload: dict, timeout: int = 15) -> dict:
    request = urllib.request.Request(
        signed_webhook(webhook, secret),
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8"))
    if int(result.get("errcode", -1)) != 0:
        raise RuntimeError(f"DingTalk rejected notification: code={result.get('errcode')} message={result.get('errmsg')}")
    return result


def readiness(run_dir: Path, require_published: bool) -> tuple[bool, str, dict]:
    state_path = run_dir / "run.json"
    issue_path = run_dir / "editorial-issue.json"
    if not state_path.exists() or not issue_path.exists():
        return False, "run_or_issue_missing", {}
    state = read_json(state_path)
    if state.get("status") != "completed":
        return False, f"run_status_{state.get('status') or 'unknown'}", {}
    if require_published and state.get("publish_status") != "published":
        return False, "page_not_published", {}
    return True, "ready", read_json(issue_path)


def main() -> int:
    # Serialize scheduled retries and the immediate completion callback.
    options = argparse.ArgumentParser(add_help=False)
    options.add_argument("--config", default=str(DEFAULT_CONFIG))
    selected, _ = options.parse_known_args()
    _, config = resolve_config(selected.config)
    lock_path = runs_dir(config) / ".daily-delivery.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return deliver()


def deliver() -> int:
    parser = argparse.ArgumentParser(description="Send today's validated SPECTRA page to DingTalk")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resend", action="store_true", help="explicitly authorized resend of a sent issue")
    args = parser.parse_args()

    load_local_env(ROOT / ".env.local")
    _, config = resolve_config(args.config)
    settings = config.get("dingtalk_push") or {}
    timezone_name = config.get("timezone", "Asia/Shanghai")
    run_id = args.run_id or daily_run_id(timezone_name)
    run_dir = runs_dir(config) / run_id
    state_path = run_dir / "run.json"
    state = read_json(state_path) if state_path.exists() else {}
    release = config.get("joint_delivery") or {}
    joint = (release.get("enabled", False)
             and run_id >= "daily-" + str(release.get("start_date", "9999-12-31")).replace("-", ""))
    if joint and settings.get("enabled") and not args.dry_run and state.get("status") == "completed":
        if state.get("publish_status") != "published":
            result = subprocess.run([sys.executable, str(ROOT / "spectra_agent/publish_run.py"),
                "--config", args.config, "--run-id", run_id, "--push", "--confirm"], cwd=ROOT)
            if result.returncode:
                return result.returncode
        # Pages deployment is asynchronous: never send a link to yesterday's issue.
        try:
            with urllib.request.urlopen(str(settings.get("public_url") or ""), timeout=20) as response:
                page = response.read().decode("utf-8")
            if not re.search(r'"run_id"\s*:\s*"' + re.escape(run_id) + r'"', page):
                print(json.dumps({"status": "waiting_for_web", "run_id": run_id}))
                return 0
        except (urllib.error.URLError, TimeoutError):
            print(json.dumps({"status": "waiting_for_web", "run_id": run_id}))
            return 0
    ready, reason, issue = readiness(run_dir, bool(settings.get("require_published", True)))
    if not settings.get("enabled", False):
        print(json.dumps({"status": "disabled", "run_id": run_id}, ensure_ascii=False))
        return 0
    if not ready:
        print(json.dumps({"status": "not_ready", "run_id": run_id, "reason": reason}, ensure_ascii=False))
        return 0

    marker = run_dir / "dingtalk-push.json"
    if marker.exists():
        marker_status = read_json(marker).get("status")
        if marker_status == "suppressed" or (marker_status == "sent" and not args.resend):
            print(json.dumps({
                "status": "already_sent" if marker_status == "sent" else "suppressed",
                "run_id": run_id,
            }, ensure_ascii=False))
            return 0

    webhook = os.environ.get(str(settings.get("webhook_env") or "DINGTALK_WEBHOOK_URL"), "").strip()
    secret = os.environ.get(str(settings.get("secret_env") or "DINGTALK_SECRET"), "").strip() or None
    public_url = str(settings.get("public_url") or "").strip()
    header_image_url = str(settings.get("header_image_url") or "").strip()
    payload = notification_payload(issue, public_url, header_image_url)
    if args.dry_run:
        print(json.dumps({"status": "dry_run", "run_id": run_id, "payload": payload}, ensure_ascii=False, indent=2))
        return 0
    if not webhook:
        print(json.dumps({"status": "not_configured", "run_id": run_id, "missing": settings.get("webhook_env")}, ensure_ascii=False))
        return 0

    try:
        result = push(webhook, secret, payload)
    except (ValueError, RuntimeError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "failed", "run_id": run_id, "error": str(exc)}, ensure_ascii=False))
        return 3
    record = {
        "status": "sent",
        "run_id": run_id,
        "sent_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "response_code": result.get("errcode"),
    }
    if marker.exists():
        previous = read_json(marker)
        record['history'] = [*previous.get('history', []), {key: value for key, value in previous.items() if key != 'history'}]
    write_json(marker, record)
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
