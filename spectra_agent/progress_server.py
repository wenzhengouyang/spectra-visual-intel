#!/usr/bin/env python3
"""Read-only local progress page for the latest SPECTRA run."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

try:
    from spectra_agent.run import DEFAULT_CONFIG, ROOT, resolve_config, runs_dir
except ImportError:
    from run import DEFAULT_CONFIG, ROOT, resolve_config, runs_dir


TERMINAL_WRITER_STATES = {"completed", "demoted", "demoted_after_failed_long_story", "manual_review"}


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


def build_status(config: dict[str, Any]) -> dict[str, Any]:
    run_dir = latest_run_path(config)
    if run_dir is None:
        return {"available": False, "label": "还没有运行记录", "stages": []}
    state = read_json_safe(run_dir / "run.json")
    review = read_json_safe(run_dir / "p1-review.json")
    checkpoint = read_json_safe(run_dir / "core-event-checkpoint.json")
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
    quality_done = status == "completed" or (run_dir / "eval-report.json").exists()
    published = state.get("publish_status") == "published"

    def step(name: str, detail: str, done: bool, current: bool) -> dict[str, Any]:
        return {"name": name, "detail": detail, "state": "done" if done else "current" if current else "pending"}

    stages = [
        step("采集", "汇总近 7 天来源", collection_done, not collection_done),
        step("筛选与核验", "形成候选与证据", review_approved or status == "waiting_for_review", collection_done and not review_approved and status != "waiting_for_review"),
        step("人工事实审核", f"{len(review.get('records') or [])} 条候选", review_approved, status == "waiting_for_review"),
        step("核心事件写作", f"已处理 {writer_done}/{len(jobs)}" if jobs else "等待开始", writing_done, writing_started and not writing_done),
        step("质量与图片", f"中文化 {localized}/{localization_total}" if localization_total else "中文化、评测与封面", quality_done, quality_started and not quality_done),
        step("发布与推送", "网页与钉钉状态独立", published, status == "completed" and not published),
    ]
    updated = state.get("updated_at")
    progress = read_json_safe(run_dir / "command-progress.json")
    if progress.get("heartbeat_at"):
        updated = progress["heartbeat_at"]
    return {
        "available": True,
        "run_id": state.get("run_id") or run_dir.name,
        "label": _status_label(state),
        "status": status,
        "reliability": state.get("reliability_status") or "unknown",
        "updated_at": updated,
        "published": published,
        "action": _action(state),
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
