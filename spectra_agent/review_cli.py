#!/usr/bin/env python3
"""Apply a compact P1 fact decision and optionally resume the local workflow."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from spectra_agent.run import DEFAULT_CONFIG, ROOT, read_json, resolve_config, runs_dir
except ImportError:
    from run import DEFAULT_CONFIG, ROOT, read_json, resolve_config, runs_dir


def parse_selection(value: str | None, size: int) -> set[int]:
    if not value:
        return set()
    if value.strip().lower() == "all":
        return set(range(1, size + 1))
    selected: set[int] = set()
    for token in value.replace("，", ",").split(","):
        token = token.strip()
        if not token:
            continue
        index = int(token)
        if not 1 <= index <= size:
            raise ValueError(f"queue index outside 1..{size}: {index}")
        selected.add(index)
    return selected


def print_queue(review: dict) -> None:
    for index, record in enumerate(review.get("records", []), 1):
        risks = ", ".join(record.get("risk_flags") or []) or "none"
        print(f"{index:02d}. {record.get('title')}")
        print(f"    {record.get('url')}")
        print(f"    confidence={record.get('harness_confidence')} risks={risks} facts={len(record.get('suggested_evidence') or [])}")


def decisions_for(review: dict, include: set[int], watch: set[int], exclude: set[int], reviewer: str) -> dict:
    size = len(review.get("records", []))
    covered = include | watch | exclude
    if include & watch or include & exclude or watch & exclude:
        raise ValueError("include/watch/exclude selections overlap")
    if covered != set(range(1, size + 1)):
        missing = sorted(set(range(1, size + 1)) - covered)
        raise ValueError(f"every P1 item needs a decision; missing queue indexes: {missing}")
    records = {}
    for index, record in enumerate(review["records"], 1):
        decision = "include" if index in include else "watch" if index in watch else "exclude"
        status = "verified_primary" if decision in {"include", "watch"} else "verified_secondary"
        records[record["candidate_id"]] = {
            "verification_status": status,
            "decision": decision,
            "decision_reason": {
                "include": "人工核验原始来源与事实证据后保留。",
                "watch": "人工核验后列入观察，暂不生成正式事件。",
                "exclude": "人工复核后不纳入本次正式内容。",
            }[decision],
            "limitation": record.get("limitation") or "结论仅覆盖已核验来源明确支持的事实范围。",
            "fact_decisions": ["keep" if decision != "exclude" else "drop" for _ in record.get("suggested_evidence") or []],
        }
    return {"verified_by": reviewer, "records": records}


def main() -> int:
    parser = argparse.ArgumentParser(description="Review a SPECTRA P1 queue by its displayed indexes")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--include", help="comma-separated queue indexes, or all")
    parser.add_argument("--watch", help="comma-separated queue indexes")
    parser.add_argument("--exclude", help="comma-separated queue indexes")
    parser.add_argument("--reviewer")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    _, config = resolve_config(args.config)
    run_dir = runs_dir(config) / args.run_id
    review_path = run_dir / "p1-review.json"
    if not review_path.exists():
        raise SystemExit(f"review queue not found: {review_path}")
    review = read_json(review_path)
    print_queue(review)
    if args.list or not any((args.include, args.watch, args.exclude)):
        return 0
    if not args.reviewer:
        raise SystemExit("--reviewer is required when applying decisions")

    size = len(review.get("records", []))
    decisions = decisions_for(
        review,
        parse_selection(args.include, size),
        parse_selection(args.watch, size),
        parse_selection(args.exclude, size),
        args.reviewer,
    )
    with tempfile.TemporaryDirectory(prefix="spectra-review-") as temp:
        decision_path = Path(temp) / "decisions.json"
        decision_path.write_text(json.dumps(decisions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        apply_command = [
            sys.executable,
            str(ROOT / "verification/apply-fact-review-decisions.py"),
            "--review", str(review_path),
            "--decisions", str(decision_path),
            "--output", str(review_path),
        ]
        subprocess.run(apply_command, cwd=ROOT, check=True)

    if not args.resume:
        print(f"已写入审核结果。继续运行：{sys.executable} spectra_agent/run.py resume --run-id {args.run_id}")
        return 0
    resume_command = [
        sys.executable,
        str(ROOT / "spectra_agent/run.py"),
        "--config", args.config,
        "resume", "--run-id", args.run_id,
    ]
    return subprocess.run(resume_command, cwd=ROOT).returncode


if __name__ == "__main__":
    raise SystemExit(main())
