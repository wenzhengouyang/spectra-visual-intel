#!/usr/bin/env python3
"""SPECTRA one-command, human-gated weekly intelligence agent.

Commands:
  run     collect + structure + create P1 review queue, then pause
  status  show the current run state and next action
  resume  validate the human review, build events and editorial issue

The workflow never crosses the review gate automatically.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

try:
    from llm_client import load_local_env
except ImportError:  # `python -m unittest` imports this file as spectra_agent.run
    from spectra_agent.llm_client import load_local_env


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "spectra_agent/config.v0.1.json"
TERMINAL = {"completed", "failed"}


class WorkflowError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def resolve_config(path: str) -> tuple[Path, dict[str, Any]]:
    config_path = (ROOT / path).resolve() if not Path(path).is_absolute() else Path(path)
    return config_path, read_json(config_path)


def runs_dir(config: dict[str, Any]) -> Path:
    return ROOT / config["runs_dir"]


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
    state_path = run_dir / "run.json"
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
    write_json(state_path, state)
    return state


def log(run_dir: Path, stage: str, message: str, **details: Any) -> None:
    entry = {"at": utc_now(), "stage": stage, "message": message, **details}
    path = run_dir / "run.log.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def command(run_dir: Path, stage: str, args: list[str]) -> None:
    log(run_dir, stage, "command_started", command=args)
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    log(run_dir, stage, "command_finished", returncode=result.returncode, stdout=result.stdout[-4000:], stderr=result.stderr[-4000:])
    if result.returncode:
        raise WorkflowError(f"{stage} failed: {(result.stderr or result.stdout).strip()[-600:]}")


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
    all_candidates = candidates["selected_candidates"]
    selected = sorted(
        (item for item in all_candidates if item.get("verification_priority") == "priority.p1"),
        key=lambda item: (-item["score"], item["canonical_title"]),
    )[:maximum]
    selected_ids = {item["candidate_id"] for item in selected}

    for intelligence_type, minimum in queue_config.get("intelligence_type_minimums", {}).items():
        present = sum(1 for item in selected if item.get("intelligence_type") == intelligence_type)
        pool = sorted(
            (
                item for item in all_candidates
                if item["candidate_id"] not in selected_ids
                and item.get("intelligence_type") == intelligence_type
                and (item.get("llm_analysis") or {}).get("recommended_disposition") in {"p1", "p2"}
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
        "editorial_selection": {"weekly_thesis": ""},
        "records": records, "additional_source_records": [],
    }


def write_review_instructions(run_dir: Path, count: int) -> None:
    text = f"""# P1 人工核验闸门

本次共有 **{count} 条 P1 候选**。主流程已暂停，不会在核验完成前生成或发布周报。

请编辑 `p1-review.json`，每条候选必须完成：

1. 阅读并确认原始来源，将 `verification_status` 改为 `verified_primary`；
2. 填写至少一条 `claims`，每条含 `text`、`kind`、`locator`；
3. 填写 `limitation`；
4. 将 `decision` 设为 `include`、`watch` 或 `exclude`，并填写 `decision_reason`；
5. `include` 的记录必须填写完整 `event`；最终正式事件必须为 5—10 条；
6. 全部完成后填写顶层 `verified_at`、`verified_by`，将 `review_status` 改为 `approved`。

完成后运行：

```bash
python3 spectra_agent/run.py resume --run-id {run_dir.name}
```
"""
    (run_dir / "REVIEW.md").write_text(text, encoding="utf-8")


def create_run(args: argparse.Namespace, config: dict[str, Any]) -> int:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_id = args.run_id or f"spectra_{stamp}"
    run_dir = runs_dir(config) / run_id
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
        "publish_status": "not_ready",
        "llm_requested": bool(args.llm),
        "llm": None,
        "artifacts": {
            "collection": "collection.json", "candidates": "candidates.json", "review": "p1-review.json",
            "verified": "verified-events.json", "issue": "editorial-issue.json",
            "web_draft": "weekly-report.html", "report": "run-report.md",
        },
        "history": [],
    }
    write_json(run_dir / "run.json", state)
    write_json(latest_pointer(config), {"run_id": run_id})
    try:
        update_state(run_dir, status="running", current_stage="collect")
        collection_path = run_dir / "collection.json"
        if args.from_collection:
            shutil.copyfile(ROOT / args.from_collection, collection_path)
            log(run_dir, "collect", "reused_collection", source=args.from_collection)
        else:
            check_werss_service(config, run_dir)
            cmd = [collector_python(config), "collector/collect.py", "--config", config["collection_config"], "--output", str(collection_path)]
            if args.end:
                cmd += ["--end", args.end]
            if args.days:
                cmd += ["--days", str(args.days)]
            if args.newscrawler_command:
                cmd += ["--newscrawler-command", args.newscrawler_command]
            command(run_dir, "collect", cmd)
        command(run_dir, "validate_collection", [sys.executable, "scripts/validate-source-run.py", str(collection_path)])

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
        write_json(run_dir / "p1-review.json", template)
        write_review_instructions(run_dir, len(template["records"]))
        update_state(run_dir, status="waiting_for_review", current_stage="human_review", paused_reason="P1 primary-source verification required")
        log(run_dir, "human_review", "workflow_paused", p1_candidates=len(template["records"]))
        print(json.dumps({"run_id": run_id, "status": "waiting_for_review", "p1_candidates": len(template["records"]), "review_file": str(run_dir / "p1-review.json"), "next": f"python3 spectra_agent/run.py resume --run-id {run_id}"}, ensure_ascii=False, indent=2))
        return 2
    except Exception as exc:
        update_state(run_dir, status="failed", current_stage="failed", error=str(exc))
        log(run_dir, "failed", "workflow_failed", error=str(exc), traceback=traceback.format_exc())
        raise


def resume_run(args: argparse.Namespace, config: dict[str, Any]) -> int:
    run_dir = locate_run(config, args.run_id)
    state = read_json(run_dir / "run.json")
    if state["status"] == "completed":
        print(json.dumps({"run_id": run_dir.name, "status": "completed", "message": "nothing to resume"}, ensure_ascii=False, indent=2))
        return 0
    if state["status"] != "waiting_for_review" and not args.retry:
        raise WorkflowError(f"run is {state['status']}; resume requires waiting_for_review (or --retry after fixing a failed run)")
    review_path = run_dir / "p1-review.json"
    if args.review:
        shutil.copyfile(ROOT / args.review, review_path)
        log(run_dir, "human_review", "review_imported", source=args.review)
    review = read_json(review_path)
    if review.get("review_status") != "approved" or not review.get("verified_at") or not review.get("verified_by"):
        raise WorkflowError("review is not approved: set review_status, verified_at and verified_by")
    try:
        update_state(run_dir, status="running", current_stage="verify", paused_reason=None, error=None)
        verified_path = run_dir / "verified-events.json"
        command(run_dir, "verify", [sys.executable, "verification/build-final-events.py", "--review", str(review_path), "--collection", str(run_dir / "collection.json"), "--candidates", str(run_dir / "candidates.json"), "--output", str(verified_path)])
        command(run_dir, "validate_verified", [sys.executable, "scripts/validate-verified-events.py", "--verified", str(verified_path), "--collection", str(run_dir / "collection.json")])
        verified = read_json(verified_path)
        count = verified["summary"]["included_events"]
        if not config["minimum_formal_events"] <= count <= config["maximum_formal_events"]:
            raise WorkflowError(f"formal event count outside configured boundary: {count}")

        update_state(run_dir, current_stage="generate")
        issue_path = run_dir / "editorial-issue.json"
        static_draft = run_dir / "weekly-report.html"
        shutil.copyfile(ROOT / config["static_page"], static_draft)
        draft_style = run_dir / "app" / "globals.css"
        draft_style.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "app" / "globals.css", draft_style)
        command(run_dir, "generate", [sys.executable, "editorial/build-editorial-issue.py", "--verified", str(verified_path.relative_to(ROOT)), "--review", str(review_path.relative_to(ROOT)), "--output", str(issue_path.relative_to(ROOT)), "--static", str(static_draft.relative_to(ROOT))])
        command(run_dir, "validate_issue", [sys.executable, "scripts/validate-editorial-issue.py", "--editorial", str(issue_path), "--verified", str(verified_path)])
        issue = read_json(issue_path)
        write_run_report(run_dir, read_json(run_dir / "collection.json"), read_json(run_dir / "candidates.json"), verified, issue)
        update_state(
            run_dir,
            status="completed",
            current_stage="complete",
            paused_reason=None,
            error=None,
            publish_status="not_published",
            completed_at=utc_now(),
        )
        log(run_dir, "complete", "workflow_completed", events=count, stories=len(issue["editorial_stories"]))
        print(json.dumps({"run_id": run_dir.name, "status": "completed", "formal_events": count, "stories": len(issue["editorial_stories"]), "issue": str(issue_path), "static_draft": str(static_draft), "publish_status": "not_published"}, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        update_state(run_dir, status="failed", current_stage="failed", error=str(exc))
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
        f"- 情报文章：{len(issue['editorial_stories'])}", f"- 失败来源：{len(failed)}", "",
        "## 失败来源", "",
    ]
    lines.extend(f"- {item['registry_id']}：{item.get('error', 'unknown error')}" for item in failed)
    if not failed:
        lines.append("- 无")
    lines += ["", "## 人工边界", "", "本期仅在P1原文核验文件批准后继续生成；Agent未自动跨越审核闸门。", ""]
    (run_dir / "run-report.md").write_text("\n".join(lines), encoding="utf-8")


def show_status(args: argparse.Namespace, config: dict[str, Any]) -> int:
    run_dir = locate_run(config, args.run_id)
    state = read_json(run_dir / "run.json")
    result = {key: state.get(key) for key in ("run_id", "status", "current_stage", "publish_status", "paused_reason", "error", "created_at", "updated_at", "completed_at") if state.get(key) is not None}
    result["run_dir"] = str(run_dir)
    if state["status"] == "waiting_for_review":
        review = read_json(run_dir / "p1-review.json")
        result["p1_candidates"] = len(review["records"])
        result["next"] = f"complete {run_dir / 'p1-review.json'}, then run resume"
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
    return root


def main() -> int:
    args = parser().parse_args()
    load_local_env()
    _, config = resolve_config(args.config)
    try:
        if args.command == "run":
            return create_run(args, config)
        if args.command == "resume":
            return resume_run(args, config)
        return show_status(args, config)
    except WorkflowError as exc:
        print(json.dumps({"result": "blocked", "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
