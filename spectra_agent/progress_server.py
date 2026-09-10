#!/usr/bin/env python3
"""Read-only local progress page for the latest SPECTRA run."""

from __future__ import annotations

import argparse
import html
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote

try:
    from spectra_agent.run import DEFAULT_CONFIG, ROOT, resolve_config, runs_dir
except ImportError:
    from run import DEFAULT_CONFIG, ROOT, resolve_config, runs_dir


TERMINAL_WRITER_STATES = {"completed", "demoted", "demoted_after_failed_long_story", "manual_review"}
RUN_ARTIFACT_FILES = {
    "REVIEW.md": "text/plain; charset=utf-8",
    "rolling-digest.html": "text/html; charset=utf-8",
    "semantic-review.json": "application/json; charset=utf-8",
    "command-progress.json": "application/json; charset=utf-8",
    "tokens.css": "text/css; charset=utf-8",
    "app/globals.css": "text/css; charset=utf-8",
    "app/hallmark-editorial.css": "text/css; charset=utf-8",
    "app/accepted-ui.css": "text/css; charset=utf-8",
    "app/visual-library.js": "text/javascript; charset=utf-8",
    "app/accepted-ux.js": "text/javascript; charset=utf-8",
    "app/share.js": "text/javascript; charset=utf-8",
    "app/account.js": "text/javascript; charset=utf-8",
    "app/account.css": "text/css; charset=utf-8",
}
IMAGE_MIMES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}


def safe_run_artifact(run_dir: Path, name: str) -> tuple[Path, str] | None:
    """Resolve only the report, its styles, and report-local image assets."""
    decoded = unquote(name).lstrip("/")
    relative = Path(decoded)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    mime = RUN_ARTIFACT_FILES.get(relative.as_posix())
    if mime is None and relative.parts[:1] == ("assets",):
        mime = IMAGE_MIMES.get(relative.suffix.lower())
    if mime is None:
        return None
    root = run_dir.resolve()
    target = (root / relative).resolve()
    try:
        safe = target.is_file() and target.is_relative_to(root)
    except (OSError, ValueError):
        safe = False
    return (target, mime) if safe else None


def read_json_safe(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def latest_run_path(config: dict[str, Any]) -> Path | None:
    root = runs_dir(config)
    pointer = read_json_safe(root / "latest.json")
    if pointer.get("run_id"):
        candidate = root / str(pointer["run_id"])
        if candidate.is_dir():
            return candidate
    candidates = [path for path in root.glob("daily-*") if path.is_dir()]
    return max(candidates, key=lambda path: path.name, default=None)


def _status_label(state: dict[str, Any]) -> str:
    status = state.get("status")
    stage = state.get("current_stage")
    if status == 'waiting_for_editorial_review' and stage == 'image_generation':
        return '等待图像模型生成'
    if status == "waiting_for_review":
        return "等待事实审核"
    if status == "waiting_for_editorial_review":
        return "等待内容审核"
    if status in {"failed", "interrupted"}:
        return "需要处理"
    if status == "completed":
        return "已发布" if state.get("publish_status") == "published" else "生成完成"
    if stage in {"collect", "collection", "incremental_collection"}:
        return "正在采集"
    if stage in {"p1_editorial_background", "core_event", "writer"}:
        return "正在写核心事件"
    if stage in {"p2_localization", "p2_localizer", "localization"}:
        return "正在中文化"
    if stage in {"image_preview", "semantic_review", "evaluation"}:
        return "正在质量检查"
    return "正在处理"


def _action(state: dict[str, Any]) -> dict[str, str] | None:
    status = state.get("status")
    stage = state.get("current_stage")
    if status == "waiting_for_editorial_review" and stage == "content_quality_review":
        return {"title": "恢复已执行，核心事件数量不足", "detail": "报告日期筛选已修复。当前窗口内尚无达到发布标准的核心事件，发布要求至少 1 篇；需补充合格稿件后继续。"}
    if status == "waiting_for_review":
        return {"title": "需要你审核事实", "detail": "打开终端审核清单；确认后任务会从检查点继续。"}
    if status == "waiting_for_editorial_review" and stage == "image_preview":
        return {"title": "需要你审核图片", "detail": "确认封面是否可用；批准后再进入发布。"}
    if status == "waiting_for_editorial_review":
        return {"title": "需要你审核内容", "detail": "检查被转人工的稿件或语义问题，再恢复任务。"}
    if status in {"failed", "interrupted"}:
        return {"title": "运行需要恢复", "detail": str(state.get("next_action") or state.get("error") or "从已有检查点恢复，不需重新采集。")}
    if status == "completed" and state.get("publish_status") != "published":
        return {"title": "等待发布确认", "detail": "内容已生成，但尚未发布到网页。"}
    return None


def _target_for(state: dict[str, Any]) -> dict[str, str]:
    status = state.get("status")
    stage = state.get("current_stage")
    if status == "waiting_for_review":
        return {"label": "打开事实审核", "url": "/run-artifact/REVIEW.md"}
    if status == "waiting_for_editorial_review" and stage == "image_preview":
        return {"label": "打开图片审核", "url": "/image-review"}
    if status == "waiting_for_editorial_review" and stage == "semantic_review":
        return {"label": "打开语义审核", "url": "/run-artifact/semantic-review.json"}
    if status == "completed":
        return {"label": "打开今日报告", "url": "/run-artifact/rolling-digest.html"}
    return {"label": "查看阶段详情", "url": "/run-artifact/command-progress.json"}


def build_status(config: dict[str, Any]) -> dict[str, Any]:
    run_dir = latest_run_path(config)
    if run_dir is None:
        return {"available": False, "label": "还没有运行记录", "stages": []}
    state = read_json_safe(run_dir / "run.json")
    review = read_json_safe(run_dir / "p1-review.json")
    checkpoint = read_json_safe(run_dir / "core-event-checkpoint.json")
    image_review = read_json_safe(run_dir / "image-review.json")
    rejected_covers = image_review.get("rejected_story_ids") or []
    jobs = list((checkpoint.get("jobs") or {}).values())
    writer_done = sum(job.get("status") in TERMINAL_WRITER_STATES for job in jobs)
    writer_passed = sum(job.get("status") == "completed" for job in jobs)
    writer_demoted = sum(str(job.get("status") or "").startswith("demoted") for job in jobs)
    writer_manual = sum(job.get("status") == "manual_review" for job in jobs)
    localization = read_json_safe(run_dir / "p2-localization-checkpoint.json")
    localized = int(localization.get("processed") or 0)
    localization_total = int(localization.get("total") or 0)

    review_approved = review.get("review_status") == "approved"
    status = state.get("status")
    stage = state.get("current_stage")
    collection_done = (run_dir / "collection.json").exists() or bool(state.get("artifacts", {}).get("collection"))
    writing_started = bool(jobs) or stage in {"p1_editorial_background", "core_event", "writer"}
    writing_done = bool(jobs) and writer_done == len(jobs)
    quality_started = stage in {"p2_localization", "p2_localizer", "localization", "image_preview", "semantic_review", "evaluation"} or (run_dir / "eval-report.json").exists()
    evaluation = read_json_safe(run_dir / "eval-report.json")
    quality_done = evaluation.get("status") == "pass" and not rejected_covers
    published = state.get("publish_status") == "published"
    overall = 0.0
    overall += 15 if collection_done else 0
    overall += 15 if review_approved or status == "waiting_for_review" else 0
    overall += 15 if review_approved else 0
    overall += 25 * (writer_done / len(jobs)) if jobs else 0
    if localization_total:
        overall += 10 * min(localized / localization_total, 1)
    elif quality_done:
        overall += 10
    if quality_done:
        overall += 10
    overall += 10 if published else 0

    def step(name: str, detail: str, done: bool, current: bool) -> dict[str, Any]:
        return {"name": name, "detail": detail, "state": "done" if done else "current" if current else "pending"}

    stages = [
        step("采集", "汇总近 7 天来源", collection_done, not collection_done),
        step("筛选与核验", "形成候选与证据", review_approved or status == "waiting_for_review", collection_done and not review_approved and status != "waiting_for_review"),
        step("人工事实审核", f"{len(review.get('records') or [])} 条候选", review_approved, status == "waiting_for_review"),
        step("核心事件写作", f"已处理 {writer_done}/{len(jobs)}" if jobs else "等待开始", writing_done, writing_started and not writing_done),
        step("质量与图片", f"{len(rejected_covers)} 张封面待返工" if rejected_covers else f"中文化 {localized}/{localization_total}" if localization_total else "中文化、评测与封面", quality_done, quality_started and not quality_done),
        step("发布与推送", "网页与钉钉状态独立", published, status == "completed" and not published),
    ]
    updated = state.get("updated_at")
    progress = read_json_safe(run_dir / "command-progress.json")
    if progress.get("heartbeat_at"):
        updated = progress["heartbeat_at"]
    return {
        "available": True,
        "run_id": state.get("run_id") or run_dir.name,
        "label": "图片需要返工" if rejected_covers else (("短讯版 · " if state.get("publication_mode") == "brief_only" else "") + _status_label(state)),
        "status": status,
        "reliability": state.get("reliability_status") or "unknown",
        "updated_at": updated,
        "published": published,
        "action": ({"title": f"{len(rejected_covers)} 张图片已驳回", "detail": str(image_review.get("rejection_reason") or "需要重新设计主题映射。")}
                   if rejected_covers else _action(state)),
        "target": _target_for(state),
        "overall_percent": min(round(overall), 100),
        "stages": stages,
        "writer": {"processed": writer_done, "total": len(jobs), "passed": writer_passed, "demoted": writer_demoted, "manual": writer_manual},
        "current_progress": (
            {"completed": localized, "total": localization_total, "label": "中文化已处理"}
            if stage in {"p2_localization", "p2_localizer", "localization"} and localization_total
            else {"completed": writer_done, "total": len(jobs), "label": "核心事件已处理"}
        ),
    }


class ProgressHandler(BaseHTTPRequestHandler):
    config: dict[str, Any] = {}
    dashboard_path: Path = ROOT / "spectra_agent/progress-dashboard.html"

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/api/status":
            body = json.dumps(build_status(self.config), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if self.path.rstrip("/") == "/image-review":
            run_dir = latest_run_path(self.config)
            issue = read_json_safe(run_dir / "editorial-issue.json") if run_dir else {}
            stories = []
            for story in issue.get("editorial_stories") or []:
                cover = story.get("cover_image") or {}
                if (not (story.get("article_type", "core_event") == "core_event" or story.get("editorial_tier") == "industry_signal")
                        or cover.get("kind") not in {"editorial_diagram", "generated", "source", "official"}
                        or cover.get("review_status") == "approved"):
                    continue
                url = str(cover.get("url") or "")
                stories.append(
                    '<article><img src="/run-asset/' + html.escape(url, quote=True) + '" alt="">'
                    '<h2>' + html.escape(str(story.get("headline") or "未命名")) + '</h2>'
                    '<p>' + html.escape(str(story.get("story_id") or "")) + '</p></article>'
                )
            review_items = "".join(stories) if stories else (
                '<div class="empty"><strong>本轮图片已全部审核</strong>'
                '<p>没有待处理的核心事件封面。</p></div>'
            )
            body = ("""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>今天的图片审核 · SPECTRA</title><style>
:root{--paper:#0d141d;--raised:#17212c;--ink:#edf2f7;--muted:#8e9bab;--rule:#344150;--accent:#61b1d1}
*{box-sizing:border-box}html,body{margin:0;overflow-x:clip;background:var(--paper);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}
main{width:min(72rem,100%);margin:auto;padding:clamp(1.25rem,5vw,4rem)}header{display:flex;justify-content:space-between;align-items:end;gap:1rem;padding-bottom:1.5rem;border-bottom:1px solid var(--rule)}
h1{margin:0;font-size:clamp(1.8rem,5vw,3.5rem);overflow-wrap:anywhere;min-width:0}header p,article p{color:var(--muted)}.grid{display:grid;gap:2rem;padding-top:2rem}
article{border-bottom:1px solid var(--rule);padding-bottom:2rem}img{display:block;width:100%;aspect-ratio:16/9;object-fit:cover;background:var(--raised)}h2{font-size:1.05rem;line-height:1.45;margin:1rem 0 .35rem}article p{font: .72rem/1.4 ui-monospace,monospace;margin:0}.empty{grid-column:1/-1;padding:4rem 0;color:var(--muted)}.empty strong{display:block;color:var(--ink);font-size:1.25rem;margin-bottom:.5rem}.empty p{margin:0}
@media(min-width:48rem){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}a{color:var(--accent);white-space:nowrap}
</style><main><header><div><h1>今天的图片审核</h1><p>逐张确认语义是否匹配；本页只读，不会自动批准。</p></div><a href="/">返回进展</a></header><section class="grid">""" + review_items + "</section></main></html>").encode("utf-8")
            self._send(200, body, "text/html; charset=utf-8")
            return
        if self.path.startswith("/run-asset/"):
            relative = self.path.split("/run-asset/", 1)[1].split("?", 1)[0]
            run_dir = latest_run_path(self.config)
            target = (run_dir / relative).resolve() if run_dir and relative.startswith("assets/") else None
            try:
                safe = bool(target and target.is_file() and target.is_relative_to((run_dir / "assets").resolve()))
            except (OSError, ValueError):
                safe = False
            if safe:
                mime = "image/svg+xml" if target.suffix.lower() == ".svg" else "image/jpeg"
                self._send(200, target.read_bytes(), mime)
            else:
                self._send(404, b"Asset not available", "text/plain; charset=utf-8")
            return
        if self.path.startswith("/run-artifact/"):
            name = self.path.split("/run-artifact/", 1)[1].split("?", 1)[0]
            run_dir = latest_run_path(self.config)
            artifact = safe_run_artifact(run_dir, name) if run_dir else None
            if artifact:
                target, mime = artifact
                self._send(200, target.read_bytes(), mime)
            else:
                self._send(404, b"Artifact not available", "text/plain; charset=utf-8")
            return
        if self.path in {"/", "/index.html"}:
            try:
                body = self.dashboard_path.read_bytes()
            except OSError:
                self._send(500, b"Progress dashboard is unavailable", "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return
        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the read-only SPECTRA progress page")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    _, config = resolve_config(args.config)
    ProgressHandler.config = config
    ProgressHandler.dashboard_path = ROOT / "spectra_agent/progress-dashboard.html"
    server = ThreadingHTTPServer((args.host, args.port), ProgressHandler)
    print(json.dumps({"status": "ready", "url": f"http://{args.host}:{args.port}"}, ensure_ascii=False), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
