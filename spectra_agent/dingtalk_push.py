#!/usr/bin/env python3
"""Push the published daily SPECTRA issue to DingTalk at the scheduled time.

The command is deliberately fail-closed: an unpublished, incomplete, or
unconfigured run is never presented as a successful notification.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
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


def notification_payload(issue: dict, public_url: str) -> dict:
    meta = issue.get("issue") or {}
    story_count = int(meta.get("story_count") or len(issue.get("editorial_stories") or []))
    brief_count = int(meta.get("brief_count") or len(issue.get("news_briefs") or []))
    title = str(meta.get("title") or "SPECTRA 每日情报")
    period = f"{meta.get('period_start', '—')} 至 {meta.get('period_end', '—')}"
    thesis = str(rolling_thesis(meta, "今日情报已完成更新。"))
    text = (
        f"### {title}\n\n"
        f"{thesis}\n\n"
        f"- 时间窗口：{period}\n"
        f"- 正式事件：{story_count} 条\n"
        f"- 短讯：{brief_count} 条\n\n"
        f"[打开 SPECTRA 情报页]({public_url})"
    )
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
    parser = argparse.ArgumentParser(description="Send today's validated SPECTRA page to DingTalk")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_local_env(ROOT / ".env.local")
    _, config = resolve_config(args.config)
    settings = config.get("dingtalk_push") or {}
    timezone_name = config.get("timezone", "Asia/Shanghai")
    run_id = args.run_id or daily_run_id(timezone_name)
    run_dir = runs_dir(config) / run_id
    ready, reason, issue = readiness(run_dir, bool(settings.get("require_published", True)))
    if not settings.get("enabled", False):
        print(json.dumps({"status": "disabled", "run_id": run_id}, ensure_ascii=False))
        return 0
    if not ready:
        print(json.dumps({"status": "not_ready", "run_id": run_id, "reason": reason}, ensure_ascii=False))
        return 0

    marker = run_dir / "dingtalk-push.json"
    if marker.exists() and read_json(marker).get("status") == "sent":
        print(json.dumps({"status": "already_sent", "run_id": run_id}, ensure_ascii=False))
        return 0

    webhook = os.environ.get(str(settings.get("webhook_env") or "DINGTALK_WEBHOOK_URL"), "").strip()
    secret = os.environ.get(str(settings.get("secret_env") or "DINGTALK_SECRET"), "").strip() or None
    payload = notification_payload(issue, str(settings.get("public_url") or "").strip())
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
    write_json(marker, record)
    print(json.dumps(record, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
