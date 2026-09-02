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

try:
    from acceptance_metrics import distinct_completed_runs, metrics_for_run, rolling_summary, write_outputs
except ImportError:
    from spectra_agent.acceptance_metrics import distinct_completed_runs, metrics_for_run, rolling_summary, write_outputs


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "spectra_agent/config.v0.1.json"
TERMINAL = {"completed", "failed"}
STATIC_ASSETS = (
    Path("tokens.css"),
    Path("app/globals.css"),
    Path("app/hallmark-editorial.css"),
)


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


def prepare_static_draft(run_dir: Path, static_page: Path) -> Path:
    """Copy the report shell and every relative dependency it references."""
    static_draft = run_dir / "weekly-report.html"
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
    all_candidates = [
        item for item in candidates["selected_candidates"]
        if item.get("front_display_eligible", True)
        and ((item.get("hard_gates") or {}).get("content_completeness") or {}).get("status", "pass") == "pass"
        and ((item.get("hard_gates") or {}).get("fact_wording_fidelity") or {}).get("status", "pass") == "pass"
    ]
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
        if record.get("decision") not in {"include", "watch"} or record.get("claims"):
            continue
        suggestions = record.get("suggested_evidence") or []
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
        record["verification_status"] = "verified_primary"
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

本次共有 **{count} 条 P1 候选**。主流程已暂停，不会在核验完成前生成或发布周报。

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
            "gated_review": "gated-review.json",
            "verification_candidates": "verification-candidates.json",
            "evidence_review": "evidence-review.json",
            "p1_fact_expansion_checkpoint": "p1-fact-expansion-checkpoint.json",
            "p1_long_editorial_checkpoint": "p1-long-editorial-checkpoint.json",
            "p1_long_editorial_audit": "p1-long-editorial-audit.json",
            "discussion_radar": "discussion-radar.json",
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
        write_json(run_dir / "p1-review.json", template)
        confidence_summary = evidence.get("summary", {})
        log(run_dir, "verification_harness", "evidence_packet_ready", **confidence_summary)

        write_review_instructions(run_dir, len(template["records"]), gated_template["count"])
        update_state(run_dir, status="waiting_for_review", current_stage="human_review", paused_reason="P1 primary-source verification required")
        log(run_dir, "human_review", "workflow_paused", p1_candidates=len(template["records"]), gated_candidates=gated_template["count"])
        print(json.dumps({"run_id": run_id, "status": "waiting_for_review", "p1_candidates": len(template["records"]), "gated_candidates": gated_template["count"], "review_file": str(run_dir / "p1-review.json"), "gated_review_file": str(run_dir / "gated-review.json"), "next": f"python3 spectra_agent/run.py resume --run-id {run_id}"}, ensure_ascii=False, indent=2))
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
    recoverable_pre_review_stage = state.get("current_stage") in {
        "validate_structure",
        "verification_harness",
        "p1_fact_expansion",
    }
    if args.retry and (not review_path.exists() or recoverable_pre_review_stage):
        # Recover a run that failed after structure output was persisted but
        # before the review packet was fully materialized.  An interrupted
        # fact-expansion stage may already have a preliminary p1-review.json;
        # rebuild it from persisted evidence and checkpoints without repeating
        # collection or model structuring.
        collection_path = run_dir / "collection.json"
        candidates_path = run_dir / "candidates.json"
        if not collection_path.exists() or not candidates_path.exists():
            raise WorkflowError("cannot recover pre-review run: collection.json or candidates.json is missing")
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
        write_json(review_path, template)
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

        deep_story_config = config.get("deep_story_writer") or config.get("editorial_writer") or {}
        editorial_drafts_path = run_dir / "deep-story-drafts.json"
        editorial_audit_path = run_dir / "p1-long-editorial-audit.json"
        background_mode = deep_story_config.get("generation_mode") == "serial_background"
        if state.get("llm_requested") and background_mode and not args.editorial_worker:
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
                "--config", args.config,
                "resume", "--run-id", run_dir.name, "--retry", "--editorial-worker",
            ]
            process = subprocess.Popen(
                worker_command, cwd=ROOT, stdout=worker_log, stderr=worker_log,
                start_new_session=True,
            )
            worker_log.close()
            update_state(run_dir, editorial_worker_pid=process.pid)
            log(run_dir, "p1_editorial_queued", "background_worker_started", pid=process.pid)
            print(json.dumps({
                "run_id": run_dir.name,
                "status": "waiting_for_editorial",
                "worker_pid": process.pid,
                "checkpoint": str(run_dir / "p1-long-editorial-checkpoint.json"),
                "publish_status": "not_published",
            }, ensure_ascii=False, indent=2))
            return 0
        if state.get("llm_requested") and deep_story_config.get("enabled", True):
            update_state(run_dir, current_stage="p1_editorial_background")
            # A retry must never reuse a draft bundle left by an earlier writer
            # attempt that subsequently failed validation.
            if editorial_drafts_path.exists():
                editorial_drafts_path.unlink()
            try:
                deep_story_command = [
                    llm_python(config), "editorial/p1_long_pipeline.py",
                    "--verified", str(verified_path),
                    "--fact-selection", str(fact_selection_path),
                    "--output", str(editorial_drafts_path),
                    "--audit-output", str(editorial_audit_path),
                    "--checkpoint", str(run_dir / "p1-long-editorial-checkpoint.json"),
                ]
                if deep_story_config.get("model"):
                    deep_story_command += ["--model", str(deep_story_config["model"])]
                if deep_story_config.get("num_ctx"):
                    deep_story_command += ["--num-ctx", str(deep_story_config["num_ctx"])]
                if deep_story_config.get("num_predict"):
                    deep_story_command += ["--num-predict", str(deep_story_config["num_predict"])]
                if deep_story_config.get("max_attempts"):
                    deep_story_command += ["--max-attempts", str(deep_story_config["max_attempts"])]
                command(run_dir, "p1_editorial_background", deep_story_command)
            except WorkflowError:
                if deep_story_config.get("required", False):
                    raise
                log(run_dir, "p1_editorial_background", "writer_failed_using_quick_read_fallback")

        if args.editorial_only:
            checkpoint = read_json(run_dir / "p1-long-editorial-checkpoint.json")
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
                "checkpoint": str(run_dir / "p1-long-editorial-checkpoint.json"),
                "publish_status": "not_published",
            }, ensure_ascii=False, indent=2))
            return 0

        update_state(run_dir, current_stage="generate")
        issue_path = run_dir / "editorial-issue.json"
        static_draft = prepare_static_draft(run_dir, ROOT / config["static_page"])
        generate_command = [sys.executable, "editorial/build-editorial-issue.py", "--verified", str(verified_path.relative_to(ROOT)), "--review", str(review_path.relative_to(ROOT)), "--candidates", str((run_dir / "candidates.json").relative_to(ROOT)), "--collection", str((run_dir / "collection.json").relative_to(ROOT)), "--output", str(issue_path.relative_to(ROOT)), "--static", str(static_draft.relative_to(ROOT))]
        if editorial_drafts_path.exists():
            generate_command += ["--drafts", str(editorial_drafts_path.relative_to(ROOT))]
        command(run_dir, "generate", generate_command)
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
        acceptance = metrics_for_run(run_dir)
        if acceptance:
            write_json(run_dir / "acceptance-metrics.json", acceptance)
            runs_dir = ROOT / "spectra_agent" / "runs"
            acceptance_summary = rolling_summary(distinct_completed_runs(runs_dir, 3), 3)
            write_outputs(
                acceptance_summary,
                runs_dir / "acceptance-summary.json",
                runs_dir / "acceptance-summary.md",
            )
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
            "issue": str(issue_path),
            "static_draft": str(static_draft),
            "publish_status": "not_published",
        }, ensure_ascii=False, indent=2))
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
        f"- 情报文章：{len(issue['editorial_stories'])}",
        f"- P2短讯：{len(issue.get('news_briefs', []))}",
        f"- P2中文化拦截：{(issue.get('localization') or {}).get('blocked_briefs', 0)}",
        f"- 失败来源：{len(failed)}", "",
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
    elif state["status"] == "waiting_for_editorial":
        result["worker_pid"] = state.get("editorial_worker_pid")
        result["checkpoint"] = str(run_dir / "p1-long-editorial-checkpoint.json")
        result["worker_log"] = str(run_dir / "p1-editorial-worker.log")
        result["recovery"] = f"if the worker stops, run: python3 spectra_agent/run.py resume --run-id {run_dir.name} --retry"
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
