#!/usr/bin/env python3
"""Local progress and bounded human-review page for the latest SPECTRA run."""

from __future__ import annotations

import argparse
import hashlib
import html
import hmac
import json
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from urllib.parse import parse_qs, urlsplit

try:
    from spectra_agent.run import DEFAULT_CONFIG, ROOT, resolve_config, runs_dir
except ImportError:
    from run import DEFAULT_CONFIG, ROOT, resolve_config, runs_dir


TERMINAL_WRITER_STATES = {"completed", "demoted", "demoted_after_failed_long_story", "manual_review"}
RUN_ARTIFACT_FILES = {
    "candidates.evidence-search.json": "application/json; charset=utf-8",
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
PUBLICATION_JOBS: dict[str, dict[str, Any]] = {}
PUBLICATION_LOCK = threading.Lock()


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


def no_update_streak(run_dir: Path) -> int:
    streak = 0
    for candidate in sorted(run_dir.parent.glob("daily-*"), reverse=True):
        state = read_json_safe(candidate / "run.json")
        if state.get("publication_mode") == "no_update":
            streak += 1
        elif candidate.resolve() == run_dir.resolve() or state.get("publish_status") == "published":
            break
    return streak


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
        if state.get("publication_mode") == "no_update":
            return "【今日无更新】"
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
    if status == "waiting_for_editorial_review" and stage == "image_generation":
        return {"title": "配图任务等待执行", "detail": "配图队列已经建立；若长时间没有进展，说明图像执行器没有接单，系统应告警而不是一直显示生成中。"}
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
    if status == "completed" and state.get("publication_mode") == "no_update":
        return {"title": "【今日无更新】", "detail": "本次没有发现相对上一已发布版本的真实新增内容；线上页面保持上一版，失败来源将在下一轮自动重试。"}
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
    if status == "completed" and state.get("publication_mode") != "no_update":
        return {"label": "打开今日报告", "url": "/run-artifact/rolling-digest.html"}
    if status == "completed" and state.get("publication_mode") == "no_update":
        return {"label": "查看采集详情", "url": "/run-artifact/command-progress.json"}
    return {"label": "查看阶段详情", "url": "/run-artifact/command-progress.json"}


def review_panel(run_dir: Path, state: dict[str, Any]) -> dict[str, Any] | None:
    status, stage = state.get("status"), state.get("current_stage")
    if status == "waiting_for_review":
        review = read_json_safe(run_dir / "p1-review.json")
        items = []
        for record in review.get("records") or []:
            evidence = record.get("suggested_evidence") or []
            items.append({
                "id": record.get("candidate_id"), "title": record.get("title"), "url": record.get("url"),
                "source_status": record.get("verification_status"),
                "facts": [{"claim": fact.get("claim"), "evidence": fact.get("evidence_text"),
                           "risk_flags": fact.get("risk_flags") or []} for fact in evidence],
            })
        return {"type": "fact", "title": "事实审核", "items": items,
                "button": "按已展示证据一键通过并继续", "can_approve": bool(items)}
    if status == "waiting_for_editorial_review" and stage == "localization_review":
        queue = read_json_safe(run_dir / "p2-localization-review.json")
        items = [{"id": item.get("brief_id"), "title": item.get("original_headline"),
                  "facts": item.get("fact_risks") or [],
                  "candidate_headline": item.get("candidate_headline_zh"),
                  "candidate_dek": item.get("candidate_dek_zh")}
                 for item in queue.get("records") or [] if item.get("review_status") != "approved"]
        can_approve = bool(items) and all(item.get("candidate_headline") and item.get("candidate_dek") for item in items)
        return {"type": "localization", "title": "中文稿事实差异", "items": items,
                "button": "一键采用修复稿并继续", "can_approve": can_approve,
                "disabled_reason": None if can_approve else "仍有条目没有可采用的修复稿"}
    if status == "waiting_for_editorial_review" and stage == "image_preview":
        issue = read_json_safe(run_dir / "editorial-issue.json")
        items = []
        for story in issue.get("editorial_stories") or []:
            cover = story.get("cover_image") or {}
            if cover.get("review_status") == "approved":
                continue
            if cover.get("provisional") or cover.get("kind") == "editorial_diagram":
                continue
            if cover.get("kind") not in {"editorial_diagram", "generated", "source", "official"}:
                continue
            items.append({"id": story.get("story_id"), "title": story.get("headline"),
                          "image_url": "/run-asset/" + str(cover.get("url") or "")})
        return {"type": "image", "title": "配图审核", "items": items,
                "button": "一键通过全部配图并继续", "can_approve": bool(items)}
    return None


def _resume(run_dir: Path) -> None:
    log = (run_dir / "dashboard-review-resume.log").open("a", encoding="utf-8")
    subprocess.Popen([
        sys.executable, str(ROOT / "spectra_agent/run.py"), "--config", "spectra_agent/config.v0.1.json",
        "resume", "--run-id", run_dir.name, "--retry",
    ], cwd=ROOT, stdout=log, stderr=log, start_new_session=True)


def approve_current_review(config: dict[str, Any], run_id: str) -> dict[str, Any]:
    run_dir = runs_dir(config) / run_id
    latest = latest_run_path(config)
    if not run_dir.is_dir() or latest is None or run_dir.resolve() != latest.resolve():
        raise ValueError("只能审核当前运行")
    state = read_json_safe(run_dir / "run.json")
    panel = review_panel(run_dir, state)
    if not panel or not panel.get("can_approve"):
        raise ValueError(str((panel or {}).get("disabled_reason") or "当前没有可一键通过的审核"))
    review_type = panel["type"]
    if review_type == "fact":
        review = read_json_safe(run_dir / "p1-review.json")
        decisions = {"verified_by": "progress-dashboard", "actor_type": "human", "records": {}}
        for record in review.get("records") or []:
            evidence = record.get("suggested_evidence") or []
            fact_decisions = ["keep" if fact.get("support_status") == "supported" and not fact.get("risk_flags") else "drop"
                              for fact in evidence]
            kept = [fact for fact, decision in zip(evidence, fact_decisions) if decision == "keep"]
            primary = bool(kept and record.get("url") and all(fact.get("source_url") == record.get("url") for fact in kept))
            reviewed = bool(kept and all(fact.get("source_url") for fact in kept))
            status = "verified_primary" if primary else "verified_secondary" if reviewed else "rejected"
            decision = "include" if primary else "watch" if reviewed else "exclude"
            decisions["records"][record["candidate_id"]] = {
                "decision": decision, "verification_status": status, "fact_decisions": fact_decisions,
                "decision_reason": "在进展页核对所展示证据后确认。",
                "limitation": record.get("limitation") or "仅保留页面所示证据直接支持的事实。",
            }
        with tempfile.TemporaryDirectory(prefix="spectra-dashboard-review-") as temporary:
            decision_path = Path(temporary) / "decisions.json"
            decision_path.write_text(json.dumps(decisions, ensure_ascii=False), encoding="utf-8")
            subprocess.run([sys.executable, str(ROOT / "verification/apply-fact-review-decisions.py"),
                            "--review", str(run_dir / "p1-review.json"), "--decisions", str(decision_path),
                            "--output", str(run_dir / "p1-review.json")], cwd=ROOT, check=True)
    elif review_type == "localization":
        # Preflight the complete batch before writing any decision.  A failed
        # item must not leave a one-click approval half committed.
        from processor.p2_localizer import validate_translation
        queue = read_json_safe(run_dir / "p2-localization-review.json")
        records = {str(item.get("brief_id")): item for item in queue.get("records", [])}
        for item in panel["items"]:
            record = records.get(str(item["id"]), {})
            validate_translation(str(record.get("original_headline") or ""),
                                 str(item["candidate_headline"]), "headline")
            validate_translation(str(record.get("original_dek") or ""),
                                 str(item["candidate_dek"]), "dek")
        for item in panel["items"]:
            result = subprocess.run([sys.executable, str(ROOT / "processor/apply_localization_review.py"),
                                     "--run-dir", str(run_dir), "--brief-id", str(item["id"]),
                                     "--headline", str(item["candidate_headline"]), "--dek", str(item["candidate_dek"]),
                                     "--reviewer", "progress-dashboard"], cwd=ROOT, text=True, capture_output=True)
            if result.returncode:
                detail = (result.stderr or result.stdout or "修复稿保存失败").strip().splitlines()[-1]
                raise ValueError(f"短讯 {item['id']} 未通过：{detail}")
    elif review_type == "image":
        subprocess.run([sys.executable, "-m", "spectra_agent.image_review", "--run-dir", str(run_dir),
                        "--approve", "all", "--reviewer", "progress-dashboard", "--actor-type", "human"],
                       cwd=ROOT, check=True)
    _resume(run_dir)
    return {"status": "approved", "run_id": run_id, "review_type": review_type, "resume_started": True}


def publication_panel(run_dir: Path, state: dict[str, Any]) -> dict[str, Any]:
    run_id = str(state.get("run_id") or run_dir.name)
    if state.get("publish_status") == "published":
        return {"state": "published", "title": "已发布", "detail": "网页已更新，钉钉推送由独立任务处理。",
                "can_publish": False, "button": "已发布"}
    with PUBLICATION_LOCK:
        job = dict(PUBLICATION_JOBS.get(run_id) or {})
    if job.get("state") == "running":
        return {"state": "publishing", "title": "正在发布", "detail": "正在执行发布检查并更新网页，请勿重复提交。",
                "can_publish": False, "button": "正在发布…"}
    ready = state.get("status") == "completed" and state.get("publication_mode") != "no_update"
    if job.get("state") == "failed":
        return {"state": "failed", "title": "发布失败", "detail": str(job.get("error") or "请检查发布日志后重试。"),
                "can_publish": ready, "button": "重新发布"}
    if ready:
        return {"state": "ready", "title": "内容审核已完成", "detail": "发布后将更新正式网页；钉钉推送仍按独立任务执行。",
                "can_publish": True, "button": "发布到网页"}
    return {"state": "unavailable", "title": "暂不可发布", "detail": "完成审核、生成与发布检查后才会开放发布。",
            "can_publish": False, "button": "暂不可发布"}


def start_publication(config: dict[str, Any], run_id: str) -> dict[str, Any]:
    run_dir = runs_dir(config) / run_id
    latest = latest_run_path(config)
    if not run_dir.is_dir() or latest is None or run_dir.resolve() != latest.resolve():
        raise ValueError("只能发布当前运行")
    state = read_json_safe(run_dir / "run.json")
    panel = publication_panel(run_dir, state)
    if panel["state"] == "publishing":
        return {"status": "publishing", "run_id": run_id}
    if not panel.get("can_publish"):
        raise ValueError(str(panel.get("detail") or "当前任务不可发布"))
    with PUBLICATION_LOCK:
        current = PUBLICATION_JOBS.get(run_id) or {}
        if current.get("state") == "running":
            return {"status": "publishing", "run_id": run_id}
        PUBLICATION_JOBS[run_id] = {"state": "running"}
    try:
        process = subprocess.Popen([
            sys.executable, str(ROOT / "spectra_agent/publish_run.py"),
            "--config", "spectra_agent/config.v0.1.json", "--run-id", run_id, "--push", "--confirm",
        ], cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    except OSError as exc:
        with PUBLICATION_LOCK:
            PUBLICATION_JOBS[run_id] = {"state": "failed", "error": str(exc)}
        raise ValueError(f"发布任务未能启动：{exc}") from exc

    def monitor() -> None:
        output, _ = process.communicate()
        try:
            (run_dir / "dashboard-publish.log").write_text(output or "", encoding="utf-8")
        except OSError:
            pass
        error = ""
        if process.returncode:
            lines = [line.strip() for line in (output or "").splitlines() if line.strip()]
            error = lines[-1] if lines else f"发布命令退出码 {process.returncode}"
            try:
                parsed = json.loads(error)
                error = str(parsed.get("error") or error)
            except (ValueError, TypeError):
                pass
        with PUBLICATION_LOCK:
            PUBLICATION_JOBS[run_id] = {"state": "failed" if process.returncode else "completed", "error": error}

    threading.Thread(target=monitor, name=f"spectra-publish-{run_id}", daemon=True).start()
    return {"status": "publishing", "run_id": run_id}


def build_status(config: dict[str, Any]) -> dict[str, Any]:
    from spectra_agent.collection_health import metrics
    from spectra_agent.model_health import status as model_status
    from spectra_agent.recovery import inspect
    from spectra_agent.daily_runner import choose_action
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
    collection_done = (run_dir / "collection.json").exists()
    collection = read_json_safe(run_dir / "collection.json")
    coverage_line = read_json_safe(run_dir / "coverage-line.json")
    collection_health = collection.get("source_checks", [])
    if not collection_health:
        partial = read_json_safe(run_dir / "collection.checkpoint.json")
        collection_health = [entry.get("health", {}) for entry in partial.get("sources", {}).values()]
    writing_started = bool(jobs) or stage in {"p1_editorial_background", "core_event", "writer"}
    writing_done = bool(jobs) and writer_done == len(jobs)
    quality_started = stage in {"p2_localization", "p2_localizer", "localization", "image_preview", "semantic_review", "evaluation"} or (run_dir / "eval-report.json").exists()
    evaluation = read_json_safe(run_dir / "eval-report.json")
    quality_done = evaluation.get("status") == "pass" and not rejected_covers
    published = state.get("publish_status") == "published"
    update_streak = no_update_streak(run_dir) if state.get("publication_mode") == "no_update" else 0
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
    timestamps = [state.get("updated_at"), state.get("published_at")]
    progress = read_json_safe(run_dir / "command-progress.json")
    if progress.get("heartbeat_at"):
        timestamps.append(progress["heartbeat_at"])
    updated = max((value for value in timestamps if value), default=None)
    return {
        "available": True,
        "run_id": state.get("run_id") or run_dir.name,
        "label": "图片需要返工" if rejected_covers else (("短讯版 · " if state.get("publication_mode") == "brief_only" else "") + _status_label(state)),
        "status": status,
        "reliability": state.get("reliability_status") or "unknown",
        "updated_at": updated,
        "published": published,
        "no_update_streak": update_streak,
        "action": ({"title": f"{len(rejected_covers)} 张图片已驳回", "detail": str(image_review.get("rejection_reason") or "需要重新设计主题映射。")}
                   if rejected_covers else ({"title": f"【连续 {update_streak} 次无更新】采集异常告警",
                                             "detail": "失败来源会在下一次自动采集时重试；覆盖补齐线继续异步查找新增信号。"}
                                            if update_streak >= 2 else _action(state))),
        "target": _target_for(state),
        "overall_percent": min(round(overall), 100),
        "stages": stages,
        "collection_health": collection_health,
        "collection_accounting": metrics(run_dir),
        "model_collection": model_status(run_dir),
        "recovery": inspect(run_dir, choose_action(run_dir)),
        "collection_lanes": {
            "mainline": {
                "status": "completed" if collection_done else "running",
                "source_count": state.get("mainline_source_count") or len(collection_health),
                "blocking_daily": True,
            },
            "coverage": {
                "status": coverage_line.get("status") or "not_started",
                "source_count": coverage_line.get("source_count") or 0,
                "summary": coverage_line.get("summary"),
                "blocking_daily": False,
                "error": coverage_line.get("error"),
            },
        },
        "evidence_search": read_json_safe(run_dir / "candidates.evidence-search.json"),
        "writer": {"processed": writer_done, "total": len(jobs), "passed": writer_passed, "demoted": writer_demoted, "manual": writer_manual},
        "current_progress": (
            {"completed": localized, "total": localization_total, "label": "中文化已处理"}
            if stage in {"p2_localization", "p2_localizer", "localization"} and localization_total
            else {"completed": writer_done, "total": len(jobs), "label": "核心事件已处理"}
        ),
        "review": review_panel(run_dir, state),
        "publication": publication_panel(run_dir, state),
    }


class ProgressHandler(BaseHTTPRequestHandler):
    config: dict[str, Any] = {}
    dashboard_path: Path = ROOT / "spectra_agent/progress-dashboard.html"
    access_token: str = ""

    def _authorized(self) -> bool:
        if not self.access_token:
            return True
        query_token = parse_qs(urlsplit(self.path).query).get("token", [""])[0]
        cookie_token = ""
        for part in self.headers.get("Cookie", "").split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == "spectra_access":
                cookie_token = value
                break
        return hmac.compare_digest(query_token or cookie_token, self.access_token)

    def _authorize_or_reject(self) -> bool:
        if self._authorized():
            return True
        body = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>SPECTRA 访问受限</title><style>body{margin:0;background:#0d141d;color:#edf2f7;"
                "font:16px/1.7 -apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif}main{max-width:32rem;"
                "margin:18vh auto;padding:1.5rem}h1{font-size:1.5rem}p{color:#9eabb9}</style>"
                "<main><h1>这个审核入口需要专属访问链接</h1><p>请使用安装时生成的手机链接打开。"
                "如果链接已经失效，请在电脑上重新生成。</p></main></html>").encode("utf-8")
        self._send(401, body, "text/html; charset=utf-8")
        return False

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlsplit(self.path)
        if not self._authorize_or_reject():
            return
        if self.access_token and parse_qs(parsed.query).get("token"):
            self.send_response(303)
            self.send_header("Location", parsed.path or "/")
            self.send_header("Set-Cookie", "spectra_access=" + self.access_token + "; Path=/; HttpOnly; SameSite=Strict")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        path = parsed.path
        if path.rstrip("/") == "/api/status":
            body = json.dumps(build_status(self.config), ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
            return
        if path.rstrip("/") == "/image-review":
            run_dir = latest_run_path(self.config)
            issue = read_json_safe(run_dir / "editorial-issue.json") if run_dir else {}
            stories = []
            for story in issue.get("editorial_stories") or []:
                cover = story.get("cover_image") or {}
                if cover.get("provisional") or cover.get("kind") == "editorial_diagram":
                    continue
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
        if path.startswith("/run-asset/"):
            relative = path.split("/run-asset/", 1)[1]
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
        if path.startswith("/run-artifact/"):
            name = path.split("/run-artifact/", 1)[1]
            run_dir = latest_run_path(self.config)
            artifact = safe_run_artifact(run_dir, name) if run_dir else None
            if artifact:
                target, mime = artifact
                self._send(200, target.read_bytes(), mime)
            else:
                self._send(404, b"Artifact not available", "text/plain; charset=utf-8")
            return
        if path in {"/", "/index.html"}:
            try:
                body = self.dashboard_path.read_bytes()
            except OSError:
                self._send(500, b"Progress dashboard is unavailable", "text/plain; charset=utf-8")
                return
            self._send(200, body, "text/html; charset=utf-8")
            return
        self._send(404, b"Not found", "text/plain; charset=utf-8")

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorize_or_reject():
            return
        path = urlsplit(self.path).path.rstrip("/")
        if path not in {"/api/review/approve", "/api/publish"}:
            self._send(404, b"Not found", "text/plain; charset=utf-8")
            return
        origin = self.headers.get("Origin")
        host = self.headers.get("Host")
        if origin and host and origin not in {f"http://{host}", f"https://{host}"}:
            self._send(403, b"Origin rejected", "text/plain; charset=utf-8")
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 4096)
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            run_id = str(payload.get("run_id") or "")
            result = (start_publication(self.config, run_id) if path == "/api/publish"
                      else approve_current_review(self.config, run_id))
            status = 202 if path == "/api/publish" and result.get("status") == "publishing" else 200
            self._send(status, json.dumps(result, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        except (ValueError, OSError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
            self._send(409, json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

    def log_message(self, format: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser(description="Serve the read-only SPECTRA progress page")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--access-token-file")
    args = parser.parse_args()
    _, config = resolve_config(args.config)
    ProgressHandler.config = config
    ProgressHandler.dashboard_path = ROOT / "spectra_agent/progress-dashboard.html"
    if args.access_token_file:
        token_path = Path(args.access_token_file).expanduser()
        try:
            ProgressHandler.access_token = token_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            parser.error(f"cannot read access token file: {exc}")
        if len(ProgressHandler.access_token) < 24:
            parser.error("access token must contain at least 24 characters")
    elif args.host not in {"127.0.0.1", "::1", "localhost"}:
        parser.error("--access-token-file is required when listening beyond localhost")
    server = ThreadingHTTPServer((args.host, args.port), ProgressHandler)
    print(json.dumps({"status": "ready", "url": f"http://{args.host}:{args.port}"}, ensure_ascii=False), flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
