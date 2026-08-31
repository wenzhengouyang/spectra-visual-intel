#!/usr/bin/env python3
"""Build per-run and rolling acceptance metrics for unattended SPECTRA trials."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNS_DIR = ROOT / "spectra_agent" / "runs"


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def fact_review_counts(review: dict[str, Any]) -> dict[str, int]:
    result = {"kept": 0, "modified": 0, "dropped": 0, "pending": 0}
    for record in review.get("records", []):
        for fact in record.get("suggested_evidence", []):
            decision = fact.get("human_fact_decision", "pending")
            key = {"keep": "kept", "modify": "modified", "drop": "dropped"}.get(decision, "pending")
            result[key] += 1
    return result


def metrics_for_run(run_dir: Path) -> dict[str, Any] | None:
    state = read_json(run_dir / "run.json", {})
    collection = read_json(run_dir / "collection.json", {})
    if state.get("status") != "completed" or not collection:
        return None
    candidates = read_json(run_dir / "candidates.json", {})
    verified = read_json(run_dir / "verified-events.json", {})
    checkpoint = read_json(run_dir / "p1-long-editorial-checkpoint.json", {"jobs": {}})
    review = read_json(run_dir / "p1-review.json", {"records": []})
    issue = read_json(run_dir / "editorial-issue.json", {})
    adjustments = read_json(run_dir / "manual-editorial-adjustments.json", {"records": []})

    collection_summary = collection.get("summary", {})
    jobs = list((checkpoint.get("jobs") or {}).values())
    completed_jobs = sum(job.get("status") == "completed" for job in jobs)
    sparse_demotions = sum(job.get("status") == "demoted" for job in jobs)
    failed_demotions = sum(job.get("status") == "demoted_after_failed_long_story" for job in jobs)
    manual_queue = sum(job.get("status") == "manual_review" for job in jobs)
    eligible_jobs = len(jobs) - sparse_demotions
    fact_counts = fact_review_counts(review)
    manual_editorial_edits = len(adjustments.get("records", []))

    return {
        "run_id": state.get("run_id", run_dir.name),
        "window_start": collection.get("window_start"),
        "window_end": collection.get("window_end"),
        "completed_at": state.get("completed_at"),
        "source_health": {
            "configured": collection_summary.get("configured_sources", 0),
            "successful": collection_summary.get("successful_sources", 0),
            "failed": collection_summary.get("failed_sources", 0),
            "success_rate": ratio(
                collection_summary.get("successful_sources", 0),
                collection_summary.get("configured_sources", 0),
            ),
            "records": collection_summary.get("source_records", 0),
        },
        "selection": {
            "p1_candidates": candidates.get("summary", {}).get("selected_for_verification", 0),
            "verified_events": verified.get("summary", {}).get("included_events", 0),
        },
        "p1_editorial": {
            "jobs": len(jobs),
            "eligible_long_jobs": eligible_jobs,
            "auto_passed": completed_jobs,
            "auto_pass_rate": ratio(completed_jobs, eligible_jobs),
            "demoted_for_sparse_facts": sparse_demotions,
            "demoted_after_failed_writer": failed_demotions,
            "writer_demotion_rate": ratio(failed_demotions, eligible_jobs),
            "manual_queue": manual_queue,
        },
        "human_intervention": {
            "fact_decisions": fact_counts,
            "fact_edit_rate": ratio(fact_counts["modified"] + fact_counts["dropped"], sum(fact_counts.values())),
            "editorial_adjustments": manual_editorial_edits,
        },
        "publication": {
            "publish_status": state.get("publish_status", "unknown"),
            "stories": len(issue.get("editorial_stories", [])),
            "p2_briefs": len(issue.get("news_briefs", [])),
            "p2_localization_blocked": (issue.get("localization") or {}).get("blocked_briefs", 0),
        },
    }


def distinct_completed_runs(runs_dir: Path, limit: int) -> list[dict[str, Any]]:
    by_window: dict[tuple[str | None, str | None], dict[str, Any]] = {}
    for run_dir in runs_dir.iterdir() if runs_dir.exists() else []:
        if not run_dir.is_dir():
            continue
        # Only runs that explicitly entered the acceptance cohort count. This
        # prevents older MVP runs or repeated replays from inflating stability.
        metrics = read_json(run_dir / "acceptance-metrics.json")
        if not metrics:
            continue
        window = (metrics["window_start"], metrics["window_end"])
        previous = by_window.get(window)
        if previous is None or str(metrics.get("completed_at") or "") > str(previous.get("completed_at") or ""):
            by_window[window] = metrics
    return sorted(by_window.values(), key=lambda item: str(item.get("window_end") or ""), reverse=True)[:limit]


def rolling_summary(runs: list[dict[str, Any]], target_rounds: int) -> dict[str, Any]:
    def mean(path: tuple[str, str]) -> float | None:
        values = [item[path[0]][path[1]] for item in runs if item[path[0]].get(path[1]) is not None]
        return round(sum(values) / len(values), 4) if values else None

    return {
        "schema_version": "0.1",
        "record_type": "spectra_acceptance_summary",
        "distinct_windows": len(runs),
        "target_rounds": target_rounds,
        "status": "ready_for_decision" if len(runs) >= target_rounds else "collecting",
        "remaining_rounds": max(0, target_rounds - len(runs)),
        "averages": {
            "source_success_rate": mean(("source_health", "success_rate")),
            "p1_auto_pass_rate": mean(("p1_editorial", "auto_pass_rate")),
            "p1_writer_demotion_rate": mean(("p1_editorial", "writer_demotion_rate")),
            "human_fact_edit_rate": mean(("human_intervention", "fact_edit_rate")),
        },
        "totals": {
            "manual_editorial_adjustments": sum(item["human_intervention"]["editorial_adjustments"] for item in runs),
            "p1_auto_passed": sum(item["p1_editorial"]["auto_passed"] for item in runs),
            "p1_writer_demotions": sum(item["p1_editorial"]["demoted_after_failed_writer"] for item in runs),
        },
        "runs": runs,
        "decision_note": "达到目标轮次后再人工决定是否启用自动发布；本报告不会自行改变发布状态。",
    }


def write_outputs(summary: dict[str, Any], output: Path, markdown: Path | None = None) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not markdown:
        return
    lines = [
        "# SPECTRA 连续运行验收", "",
        f"- 状态：{summary['status']}",
        f"- 不同采集窗口：{summary['distinct_windows']} / {summary['target_rounds']}",
        f"- 尚需运行：{summary['remaining_rounds']} 轮", "",
        "| 运行 | 来源成功率 | P1自动通过率 | Writer降级率 | 事实人工修改率 | 编辑人工调整 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for item in summary["runs"]:
        percent = lambda value: "—" if value is None else f"{value * 100:.1f}%"
        lines.append(
            f"| {item['run_id']} | {percent(item['source_health']['success_rate'])} | "
            f"{percent(item['p1_editorial']['auto_pass_rate'])} | "
            f"{percent(item['p1_editorial']['writer_demotion_rate'])} | "
            f"{percent(item['human_intervention']['fact_edit_rate'])} | "
            f"{item['human_intervention']['editorial_adjustments']} |"
        )
    lines += ["", summary["decision_note"], ""]
    markdown.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR))
    parser.add_argument("--include-run", help="add one completed run to the acceptance cohort before summarizing")
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--output", default=str(DEFAULT_RUNS_DIR / "acceptance-summary.json"))
    parser.add_argument("--markdown", default=str(DEFAULT_RUNS_DIR / "acceptance-summary.md"))
    args = parser.parse_args()
    runs_dir = Path(args.runs_dir)
    if args.include_run:
        run_dir = runs_dir / args.include_run
        metrics = metrics_for_run(run_dir)
        if not metrics:
            raise SystemExit(f"run is not a completed acceptance candidate: {run_dir}")
        (run_dir / "acceptance-metrics.json").write_text(
            json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    runs = distinct_completed_runs(runs_dir, args.rounds)
    summary = rolling_summary(runs, args.rounds)
    write_outputs(summary, Path(args.output), Path(args.markdown) if args.markdown else None)
    print(json.dumps({key: summary[key] for key in ("status", "distinct_windows", "target_rounds", "remaining_rounds")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
