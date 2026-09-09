#!/usr/bin/env python3
"""Read-only, replayable quality evaluation for one SPECTRA run.

The evaluator reads pipeline artifacts and writes only eval reports.  It never
changes review decisions, claims, verified events, or policy configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from acceptance_metrics import metrics_for_run
except ImportError:
    from spectra_agent.acceptance_metrics import metrics_for_run

try:
    from compat import compatible_artifact
except ImportError:
    from spectra_agent.compat import compatible_artifact


DEFAULT_CONFIG = ROOT / "spectra_agent/config.v0.1.json"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _fingerprint(payload: Any) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


def _ids(items: list[dict[str, Any]], key: str) -> list[str]:
    return [str(item.get(key)) for item in items if item.get(key)]


def _check(name: str, passed: bool, detail: str, severity: str = "error") -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "severity": severity, "detail": detail}


def _artifact_checks(run_dir: Path, artifacts: dict[str, Any]) -> list[dict[str, Any]]:
    collection = artifacts["collection"] or {}
    candidates = artifacts["candidates"] or {}
    review = artifacts["review"] or {}
    gated = artifacts["gated"] or {}
    source_records = collection.get("source_records") or []
    selected = candidates.get("selected_candidates") or []
    review_records = review.get("records") or []
    gated_records = gated.get("records") or []
    source_ids = _ids(source_records, "source_id")
    candidate_ids = _ids(selected, "candidate_id")
    review_ids = _ids(review_records, "candidate_id")
    gated_ids = _ids(gated_records, "candidate_id")
    expected_sources = (collection.get("summary") or {}).get("source_records")
    expected_candidates = (candidates.get("summary") or {}).get("selected_for_verification")
    return [
        _check("collection_present", bool(collection), "collection.json 可读取" if collection else "collection.json 缺失或损坏"),
        _check("source_ids_unique", len(source_ids) == len(set(source_ids)), f"{len(source_ids)} 条 source_record"),
        _check("source_count_consistent", expected_sources in {None, len(source_records)}, f"summary={expected_sources}, actual={len(source_records)}"),
        _check("candidate_ids_unique", len(candidate_ids) == len(set(candidate_ids)), f"{len(candidate_ids)} 条候选"),
        _check("candidate_count_consistent", expected_candidates in {None, len(selected)}, f"summary={expected_candidates}, actual={len(selected)}"),
        _check("review_ids_unique", len(review_ids) == len(set(review_ids)), f"{len(review_ids)} 条 P1 review"),
        _check("review_candidates_exist", set(review_ids) <= set(candidate_ids), "review candidate_id 必须来自 candidates"),
        _check("queues_mutually_exclusive", not (set(review_ids) & set(gated_ids)), "P1 与 gated 队列不得重叠"),
    ]


def _collection_metrics(collection: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    summary = collection.get("summary") or {}
    configured = int(summary.get("configured_sources") or 0)
    successful = int(summary.get("successful_sources") or 0)
    unchanged = int(summary.get("unchanged_records") or 0)
    total = int(summary.get("source_records") or len(collection.get("source_records") or []))
    incremental = collection.get("incremental") or {}
    changed_only = incremental.get("processing_scope") == "changed_records_only"
    duplicate_processing_count = 0 if changed_only else unchanged
    return {
        "configured_sources": configured,
        "successful_sources": successful,
        "failed_sources": int(summary.get("failed_sources") or 0),
        "source_success_rate": _ratio(successful, configured),
        "source_records": total,
        "baseline_run_id": incremental.get("baseline_run_id") or state.get("incremental_baseline_run"),
        "processing_scope": incremental.get("processing_scope") or state.get("incremental_processing_mode") or "full_window",
        "changed_records": int(summary.get("changed_records") or total),
        "baseline_records_reused": int(summary.get("baseline_records_reused") or 0),
        "duplicate_processing_count": duplicate_processing_count,
        "duplicate_processing_rate": _ratio(duplicate_processing_count, total),
        "full_window_retry_sources": incremental.get("full_window_retry_sources") or [],
    }


def _review_metrics(review: dict[str, Any], checkpoint: dict[str, Any]) -> dict[str, Any]:
    records = review.get("records") or []
    tiers = {"mandatory_review": 0, "sample_review": 0, "auto_locked": 0}
    auto_facts = auto_edited = human_modified = fact_total = 0
    for record in records:
        tier = (record.get("review_policy") or {}).get("tier")
        if tier in tiers:
            tiers[tier] += 1
        for fact in record.get("suggested_evidence") or []:
            fact_total += 1
            decision = fact.get("human_fact_decision")
            if decision in {"modify", "drop"}:
                human_modified += 1
            if tier == "auto_locked":
                auto_facts += 1
                if decision in {"modify", "drop"} or fact.get("reviewed_by") not in {"auto_fact_lock", "verification_policy"}:
                    auto_edited += 1
    jobs = list((checkpoint.get("jobs") or {}).values())
    # Sparse fact packages never entered Writer, so they do not belong in the
    # Writer success/demotion denominator.
    eligible = [job for job in jobs if job.get("status") != "demoted"]
    demoted = [job for job in eligible if str(job.get("status") or "").startswith("demoted")]
    return {
        **tiers,
        "p1_records": len(records),
        "auto_lock_rate": _ratio(tiers["auto_locked"], len(records)),
        "human_fact_modification_rate": _ratio(human_modified, fact_total),
        "auto_lock_post_edit_rate": _ratio(auto_edited, auto_facts),
        "writer_jobs": len(jobs),
        "writer_demotions": len(demoted),
        "writer_demotion_rate": _ratio(len(demoted), len(eligible)),
    }


def _fact_passes(fact: dict[str, Any]) -> tuple[bool, list[str]]:
    failures = []
    if fact.get("support_status") != "supported":
        failures.append("unsupported")
    if fact.get("numeric_match") is False:
        failures.append("numeric_mismatch")
    if fact.get("attribution_required") and fact.get("attribution_preserved") is not True:
        failures.append("attribution_missing")
    if fact.get("risk_flags"):
        failures.append("risk_flag")
    return not failures, failures


def _sample_facts(review: dict[str, Any], rate: float, seed: int) -> dict[str, Any]:
    facts = [
        {"candidate_id": record.get("candidate_id"), "index": index, "fact": fact}
        for record in review.get("records") or []
        for index, fact in enumerate(record.get("suggested_evidence") or [], 1)
    ]
    sample_count = min(len(facts), max(1, round(len(facts) * rate))) if facts and rate > 0 else 0
    chosen = random.Random(seed).sample(facts, sample_count) if sample_count else []
    results = []
    failures = 0
    for item in chosen:
        passed, reasons = _fact_passes(item["fact"])
        failures += int(not passed)
        results.append({
            "candidate_id": item["candidate_id"],
            "fact_index": item["index"],
            "passed": passed,
            "failure_reasons": reasons,
            "source_id": item["fact"].get("source_id"),
            "locator": item["fact"].get("locator"),
        })
    return {
        "mode": "deterministic_rules",
        "seed": seed,
        "population": len(facts),
        "sample_size": len(chosen),
        "failures": failures,
        "failure_rate": _ratio(failures, len(chosen)),
        "results": results,
        "note": "抽样结果仅用于质量评测，不写回 claims 或人工 decision。",
    }


def _publication_checks(run_dir: Path, issue: dict[str, Any], config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    issue_path = run_dir / "editorial-issue.json"
    html_path = compatible_artifact(run_dir, "rolling-digest.html")
    if not issue_path.exists() and not html_path.exists():
        return [_check("publication_not_yet_generated", True, "当前阶段尚无发布物", "info")]
    if not issue or not html_path.exists():
        return [_check("publication_pair_present", False, "editorial-issue.json 与 rolling-digest.html 必须成对存在")]
    try:
        # Reuse the exact fail-closed validator used by the production path.
        try:
            from run import validate_static_package
        except ImportError:
            from spectra_agent.run import validate_static_package
        validate_static_package(run_dir, html_path, issue)
        try:
            from publication_quality import publication_quality_errors
        except ImportError:
            from spectra_agent.publication_quality import publication_quality_errors
        quality_errors = publication_quality_errors(
            issue, config or read_json(DEFAULT_CONFIG, {}), asset_root=run_dir
        )
        return [
            _check("publication_package_valid", True, "复用主流程发布前校验并通过"),
            _check("reader_content_quality", not quality_errors, "; ".join(quality_errors) or "中文、深读与配图门槛通过"),
        ]
    except Exception as exc:  # report evidence; caller decides whether to block
        return [_check("publication_package_valid", False, str(exc))]


def _strategy_fingerprint(config: dict[str, Any], collection: dict[str, Any]) -> str:
    payload = {
        "review_policy": config.get("review_policy"),
        "collection_config": config.get("collection_config"),
        "processor_config": config.get("processor_config"),
        "p1_fact_expander": config.get("p1_fact_expander"),
        "core_event_writer": config.get("core_event_writer"),
        "p2_localizer": config.get("p2_localizer"),
        "window_days": (config.get("schedule") or {}).get("window_days"),
    }
    return _fingerprint(payload)


def _distinct_prior_reports(runs_dir: Path, current: dict[str, Any], limit: int) -> list[dict[str, Any]]:
    reports = [current] if current.get("cohort_eligible") else []
    for path in runs_dir.glob("*/eval-report.json") if runs_dir.exists() else []:
        report = read_json(path)
        if report and report.get("cohort_eligible") and report.get("run_id") != current.get("run_id"):
            reports.append(report)
    by_window: dict[tuple[Any, Any], dict[str, Any]] = {}
    for report in reports:
        window = (report.get("window_start"), report.get("window_end"))
        previous = by_window.get(window)
        if previous is None or str(report.get("evaluated_at") or "") > str(previous.get("evaluated_at") or ""):
            by_window[window] = report
    return sorted(by_window.values(), key=lambda item: str(item.get("window_end") or ""), reverse=True)[:limit]


def _mean(reports: list[dict[str, Any]], section: str, field: str) -> float | None:
    values = [r.get(section, {}).get(field) for r in reports]
    numeric = [float(value) for value in values if value is not None]
    return round(sum(numeric) / len(numeric), 4) if numeric else None


def _rolling_advice(current: dict[str, Any], runs_dir: Path, config: dict[str, Any]) -> dict[str, Any]:
    evaluation = config.get("run_evaluation") or {}
    window = int(evaluation.get("rolling_window", 3))
    reports = _distinct_prior_reports(runs_dir, current, window)
    thresholds = evaluation.get("thresholds") or {}
    minimum = int((evaluation.get("expansion_advice") or {}).get("minimum_distinct_windows", window))
    averages = {
        "source_success_rate": _mean(reports, "collection_quality", "source_success_rate"),
        "duplicate_processing_rate": _mean(reports, "collection_quality", "duplicate_processing_rate"),
        "sample_fact_failure_rate": _mean(reports, "fact_alignment_sample", "failure_rate"),
        "auto_lock_post_edit_rate": _mean(reports, "review_behavior", "auto_lock_post_edit_rate"),
        "writer_demotion_rate": _mean(reports, "review_behavior", "writer_demotion_rate"),
    }
    checks = {
        "enough_distinct_windows": len(reports) >= minimum,
        "source_success_rate": averages["source_success_rate"] is not None and averages["source_success_rate"] >= float(thresholds.get("source_success_rate_min", 0.9)),
        "duplicate_processing_rate": averages["duplicate_processing_rate"] is not None and averages["duplicate_processing_rate"] <= float(thresholds.get("duplicate_processing_rate_max", 0.15)),
        "sample_fact_failure_rate": averages["sample_fact_failure_rate"] is not None and averages["sample_fact_failure_rate"] <= float(thresholds.get("sample_fact_failure_rate_max", 0.05)),
        "auto_lock_post_edit_rate": averages["auto_lock_post_edit_rate"] is not None and averages["auto_lock_post_edit_rate"] <= float(thresholds.get("auto_lock_post_edit_rate_max", 0.05)),
        "writer_demotion_rate": averages["writer_demotion_rate"] is not None and averages["writer_demotion_rate"] <= float(thresholds.get("writer_demotion_rate_max", 0.2)),
    }
    stable_strategy = len({report.get("strategy_fingerprint") for report in reports}) == 1
    if (evaluation.get("expansion_advice") or {}).get("require_unchanged_strategy", True):
        checks["strategy_unchanged"] = stable_strategy
    allow_expand = all(checks.values())
    failed = [name for name, passed in checks.items() if not passed]
    return {
        "window_size": window,
        "distinct_windows": len(reports),
        "averages": averages,
        "checks": checks,
        "allow_expand": allow_expand,
        "reason": "所有滚动门槛通过，可由人工决定是否扩大允许范围。" if allow_expand else "暂不建议扩大自动锁定：" + "、".join(failed),
        "policy_changed": False,
        "note": "本建议不会修改 review policy 或发布权限。",
    }


def evaluate_run(
    run_dir: Path,
    config: dict[str, Any],
    runs_dir: Path | None = None,
    publication_validator: Callable[[Path, dict[str, Any]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Evaluate one run without mutating any pipeline artifact."""
    run_dir = run_dir.resolve()
    evaluation = config.get("run_evaluation") or {}
    artifacts = {
        "state": read_json(run_dir / "run.json", {}),
        "collection": read_json(run_dir / "collection.json", {}),
        "candidates": read_json(run_dir / "candidates.json", {}),
        "review": read_json(run_dir / "p1-review.json", {}),
        "gated": read_json(run_dir / "gated-review.json", {}),
        "verified": read_json(run_dir / "verified-events.json", {}),
        "issue": read_json(run_dir / "editorial-issue.json", {}),
        "checkpoint": read_json(compatible_artifact(run_dir, "core-event-checkpoint.json"), {}),
    }
    state = artifacts["state"]
    collection = artifacts["collection"]
    structural = _artifact_checks(run_dir, artifacts)
    publication = (
        publication_validator(run_dir, artifacts["issue"])
        if publication_validator
        else _publication_checks(run_dir, artifacts["issue"], config)
    )
    collection_quality = _collection_metrics(collection, state)
    review_behavior = _review_metrics(artifacts["review"], artifacts["checkpoint"])
    sample = _sample_facts(
        artifacts["review"],
        float(evaluation.get("sample_rate", 0.25)),
        int(evaluation.get("sample_seed", 20260903)),
    )
    thresholds = evaluation.get("thresholds") or {}
    threshold_checks = [
        _check("source_success_rate", (collection_quality["source_success_rate"] or 0) >= float(thresholds.get("source_success_rate_min", 0.9)), str(collection_quality["source_success_rate"])),
        _check("duplicate_processing_rate", (collection_quality["duplicate_processing_rate"] or 0) <= float(thresholds.get("duplicate_processing_rate_max", 0.15)), str(collection_quality["duplicate_processing_rate"])),
        _check("sample_fact_failure_rate", sample["failure_rate"] is not None and sample["failure_rate"] <= float(thresholds.get("sample_fact_failure_rate_max", 0.05)), str(sample["failure_rate"]), severity="warning"),
        _check(
            "writer_demotion_rate",
            (review_behavior["writer_demotion_rate"] or 0) <= float(thresholds.get("writer_demotion_rate_max", 0.2)),
            str(review_behavior["writer_demotion_rate"]),
            severity="warning",
        ),
    ]
    failures = [item for item in structural + publication + threshold_checks if item["severity"] == "error" and not item["passed"]]
    publication_ready = bool(
        artifacts["verified"]
        and artifacts["issue"]
        and compatible_artifact(run_dir, "rolling-digest.html").exists()
    )
    review_complete = (artifacts["review"] or {}).get("review_status") == "approved"
    cohort_eligible = publication_ready and review_complete and all(
        item["passed"] for item in publication + threshold_checks if item["severity"] == "error"
    )
    report = {
        "schema_version": "1.0",
        "record_type": "spectra_run_evaluation",
        "run_id": state.get("run_id") or run_dir.name,
        "evaluated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window_start": collection.get("window_start"),
        "window_end": collection.get("window_end"),
        "run_status_at_evaluation": state.get("status"),
        "cohort_eligible": cohort_eligible,
        "cohort_reason": (
            "完整链路与发布前校验已完成，可计入滚动窗口。"
            if cohort_eligible
            else "尚未完成事实审核、Writer、页面校验中的全部环节，不计入三轮滚动判断。"
        ),
        "strategy_fingerprint": _strategy_fingerprint(config, collection),
        "status": "fail" if failures else "pass",
        "blocking_recommended": bool(failures),
        "structural_checks": structural,
        "collection_quality": collection_quality,
        "review_behavior": review_behavior,
        "fact_alignment_sample": sample,
        "publication_checks": publication,
        "threshold_checks": threshold_checks,
        "acceptance_metrics": metrics_for_run(run_dir, require_completed=False),
        "failed_checks": [item["name"] for item in failures],
        "warning_checks": [item["name"] for item in threshold_checks if item["severity"] == "warning" and not item["passed"]],
        "immutability": {
            "review_decisions_modified": False,
            "claims_modified": False,
            "policy_modified": False,
        },
    }
    report["rolling_advice"] = _rolling_advice(report, runs_dir or run_dir.parent, config)
    return report


def write_report(report: dict[str, Any], output: Path, markdown: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    q = report["collection_quality"]
    r = report["review_behavior"]
    advice = report["rolling_advice"]
    pct = lambda value: "—" if value is None else f"{value * 100:.1f}%"
    lines = [
        f"# SPECTRA Run 评测 — {report['run_id']}", "",
        f"- 结论：{report['status']}",
        f"- 来源成功率：{pct(q['source_success_rate'])}",
        f"- 重复处理率：{pct(q['duplicate_processing_rate'])}",
        f"- 自动锁定：{r['auto_locked']} / {r['p1_records']}",
        f"- 事实抽检失败率：{pct(report['fact_alignment_sample']['failure_rate'])}",
        f"- Writer 降级率：{pct(r['writer_demotion_rate'])}", "",
        "## 自动锁定扩围建议", "",
        f"- allow_expand：{str(advice['allow_expand']).lower()}",
        f"- 原因：{advice['reason']}", "",
        "评测只提供证据和建议，不修改人工决定、claims、policy 或发布权限。", "",
    ]
    markdown.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Replayable, read-only SPECTRA run evaluator")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--runs-dir")
    parser.add_argument("--output")
    parser.add_argument("--markdown")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    config = read_json(Path(args.config).resolve(), {})
    output = Path(args.output).resolve() if args.output else run_dir / "eval-report.json"
    markdown = Path(args.markdown).resolve() if args.markdown else run_dir / "eval-report.md"
    report = evaluate_run(run_dir, config, Path(args.runs_dir).resolve() if args.runs_dir else run_dir.parent)
    write_report(report, output, markdown)
    print(json.dumps({
        "run_id": report["run_id"],
        "status": report["status"],
        "allow_expand": report["rolling_advice"]["allow_expand"],
        "report": str(output),
    }, ensure_ascii=False))
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())
