"""Small shared vocabulary for failures and bounded recovery decisions."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spectra_agent.execution import atomic_json


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def classify_failure(stage: str, error: BaseException | str) -> tuple[str, str, bool]:
    text = str(error)
    lowered = f"{stage} {text}".lower()
    if re.search(r"(?:http(?: error)?\s*)?429\b|too many requests|rate.?limit", lowered):
        return "external_source", "rate_limited", False
    if re.search(r"unauthori[sz]ed|forbidden|cookie expired|authorization.*expired|scan.*qr|扫码", lowered):
        return "authorization", "authorization_required", False
    if re.search(r"timed? out|timeout", lowered):
        return "timeout", "stage_timeout", True
    if re.search(r"connection refused|cannot reach|unreachable|network_error|dns|name resolution", lowered):
        return "dependency", "dependency_unavailable", True
    if re.search(r"json.*(?:invalid|decode|corrupt)|expecting value", lowered):
        return "data_integrity", "invalid_json", False
    if re.search(r"missing|required artifact|cannot continue without|not found|no such file|unsupported|configuration|config", lowered):
        return "configuration", "invalid_or_missing_input", False
    if re.search(r"quality|audit|evaluation failed|validation failed|outside configured boundary", lowered):
        return "quality_gate", "quality_gate_failed", False
    if "publish" in lowered:
        return "publishing", "publication_failed", True
    if "model" in lowered or "llm" in lowered or "ollama" in lowered or "writer" in lowered:
        return "model", "model_failed", True
    return "execution", "stage_failed", True


def failure_record(
    stage: str,
    error: BaseException | str,
    *,
    attempt: int,
    max_attempts: int,
    scope: str = "run",
    blocking: bool = True,
) -> dict[str, Any]:
    category, code, retryable = classify_failure(stage, error)
    exhausted = retryable and attempt >= max_attempts
    if not blocking:
        action = "continue_with_degraded_optional_stage"
        next_step = "主流程继续；在运行报告中保留该异常"
    elif category == "external_source" and code == "rate_limited":
        action = "cooldown_source"
        next_step = "等待来源冷却结束，不执行整窗重复补采"
    elif category in {"authorization", "configuration", "data_integrity", "quality_gate"}:
        action = "human_action_required"
        next_step = "按错误信息修复输入或完成人工审核，再从有效检查点恢复"
    elif exhausted:
        action = "stop_or_degrade"
        next_step = "已达到自动恢复上限；降级局部内容或转人工处理"
    else:
        action = "retry_from_checkpoint"
        next_step = "复用有效产物，只重跑失败阶段"
    return {
        "schema_version": "1.0",
        "occurred_at": utc_now(),
        "stage": stage,
        "scope": scope,
        "category": category,
        "code": code,
        "message": str(error),
        "blocking": blocking,
        "retryable": retryable,
        "attempt": attempt,
        "max_attempts": max_attempts,
        "exhausted": exhausted,
        "action": action,
        "next": next_step,
    }


def record_failure(
    run_dir: Path,
    stage: str,
    error: BaseException | str,
    *,
    max_attempts: int = 2,
    scope: str = "run",
    blocking: bool = True,
) -> dict[str, Any]:
    """Persist one normalized incident and its bounded attempt count."""
    run_dir = Path(run_dir)
    path = run_dir / "reliability.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError, json.JSONDecodeError):
        state = {"recovered_from_invalid_reliability_file": True}
    incidents = list(state.get("incidents") or [])
    key = f"{scope}:{stage}"
    attempt = 1 + sum(item.get("key") == key and not item.get("resolved_at") for item in incidents)
    record = failure_record(
        stage, error, attempt=attempt, max_attempts=max(1, max_attempts),
        scope=scope, blocking=blocking,
    )
    incidents.append({"key": key, **record})
    payload = {
        "schema_version": "1.0",
        "status": "blocked" if blocking else "degraded",
        "last_incident": record,
        "incidents": incidents,
        "updated_at": record["occurred_at"],
    }
    if state.get("recovered_from_invalid_reliability_file"):
        payload["recovered_from_invalid_reliability_file"] = True
    atomic_json(path, payload)
    return record


def mark_recovered(run_dir: Path, stage: str = "workflow") -> str:
    path = Path(run_dir) / "reliability.json"
    state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"schema_version": "1.0", "incidents": []}
    recovered_at = utc_now()
    for incident in state.get("incidents") or []:
        if incident.get("blocking") and not incident.get("resolved_at"):
            incident["resolved_at"] = recovered_at
    unresolved_warnings = [
        incident for incident in state.get("incidents") or []
        if not incident.get("blocking") and not incident.get("resolved_at")
    ]
    status = "degraded" if unresolved_warnings else "healthy"
    state.update({"status": status, "recovered_at": recovered_at, "recovered_stage": stage, "updated_at": recovered_at})
    atomic_json(path, state)
    return status
