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
        for source in (record.get("supplemental_evidence") or {}).get("evidence", []):
            print(f"    补搜来源（尚未核验）: {source.get('url')}")


def prompt_choice(prompt: str, choices: set[str], input_fn=input) -> str:
    while True:
        value = input_fn(prompt).strip().lower()
        if value in choices:
            return value
        print(f"请输入：{' / '.join(sorted(choices))}")


def interactive_decisions(review: dict, reviewer: str, input_fn=input) -> dict:
    """Review every candidate and its suggested facts in a real terminal."""
    records: dict[str, dict] = {}
    for index, record in enumerate(review.get("records", []), 1):
        print("\n" + "=" * 78)
        print(f"候选 {index}/{len(review.get('records', []))}: {record.get('title')}")
        print(f"原文：{record.get('url')}")
        for source in (record.get("supplemental_evidence") or {}).get("evidence", []):
            print(f"补搜来源（尚未核验）：{source.get('url')}")
        print(f"机器建议：{record.get('agent_recommendation') or '未提供'}")
        risks = "、".join(record.get("risk_flags") or []) or "无"
        print(f"风险：{risks}；置信度：{record.get('harness_confidence') or '未知'}")
        suggestions = record.get("suggested_evidence") or []
        for fact_index, fact in enumerate(suggestions, 1):
            print(f"\n  事实 {fact_index}: {fact.get('claim')}")
            print(f"  原文证据：{fact.get('evidence_text')}")
            fact_risks = "、".join(fact.get("risk_flags") or []) or "无"
            print(f"  支持={fact.get('support_status')} 数字匹配={fact.get('numeric_match')} 风险={fact_risks}")

        print("\n选择：i=include且全部事实keep；w=watch且全部事实keep；e=exclude且全部事实drop；r=逐条审核")
        mode = prompt_choice("你的选择 [i/w/e/r]: ", {"i", "w", "e", "r"}, input_fn)
        decision = {"i": "include", "w": "watch", "e": "exclude"}.get(mode)
        if mode == "r":
            decision = {"i": "include", "w": "watch", "e": "exclude"}[
                prompt_choice("候选结论 [i/w/e]: ", {"i", "w", "e"}, input_fn)
            ]
            fact_decisions = []
            for fact_index, fact in enumerate(suggestions, 1):
                action = prompt_choice(f"事实 {fact_index} [k=keep/m=modify/d=drop]: ", {"k", "m", "d"}, input_fn)
                if action == "m":
                    text = input_fn("  修改后的事实文本: ").strip()
                    kind = input_fn(f"  事实类型 [{fact.get('kind') or '事件'}]: ").strip() or fact.get("kind") or "事件"
                    if not text:
                        raise ValueError(f"事实 {fact_index} 的修改文本不能为空")
                    fact_decisions.append({"decision": "modify", "text": text, "kind": kind})
                else:
                    fact_decisions.append("keep" if action == "k" else "drop")
        else:
            fact_decisions = ["drop" if decision == "exclude" else "keep" for _ in suggestions]
        verification_choice = prompt_choice(
            "原文核验 [p=一手原文通过/s=二手来源通过/u=尚未通过]: ", {"p", "s", "u"}, input_fn
        )
        status = {"p": "verified_primary", "s": "verified_secondary", "u": "not_verified"}[verification_choice]
        if decision == 'include' and status != 'verified_primary':
            raise ValueError('include 只表示采用内容；进入正式事件还必须明确选择一手原文通过')
        records[record["candidate_id"]] = {
            "verification_status": status,
            "decision": decision,
            "decision_reason": {
                "include": "人工核验原始来源与事实证据后保留。",
                "watch": "人工核验后列入观察，暂不生成正式事件。",
                "exclude": "人工复核后不纳入本次正式内容。",
            }[decision],
            "limitation": record.get("limitation") or "结论仅覆盖已核验来源明确支持的事实范围。",
            "fact_decisions": fact_decisions,
        }
    print("\n审核汇总：")
    for index, record in enumerate(review.get("records", []), 1):
        selected = records[record["candidate_id"]]
        print(f"  {index}. {selected['decision']} — {record.get('title')}")
    if prompt_choice("确认写入以上决定？[y/n]: ", {"y", "n"}, input_fn) != "y":
        raise KeyboardInterrupt("审核已取消，未写入任何修改")
    return {"verified_by": reviewer, "records": records}


def decisions_for(review: dict, include: set[int], watch: set[int], exclude: set[int], reviewer: str,
                  verified_primary: set[int] | None = None, verified_secondary: set[int] | None = None) -> dict:
    size = len(review.get("records", []))
    covered = include | watch | exclude
    if include & watch or include & exclude or watch & exclude:
        raise ValueError("include/watch/exclude selections overlap")
    if covered != set(range(1, size + 1)):
        missing = sorted(set(range(1, size + 1)) - covered)
        raise ValueError(f"every P1 item needs a decision; missing queue indexes: {missing}")
    records = {}
    verified_primary = verified_primary or set()
    verified_secondary = verified_secondary or set()
    if verified_primary & verified_secondary:
        raise ValueError('primary/secondary verification selections overlap')
    for index, record in enumerate(review["records"], 1):
        decision = "include" if index in include else "watch" if index in watch else "exclude"
        status = ('verified_primary' if index in verified_primary else
                  'verified_secondary' if index in verified_secondary else 'not_verified')
        if decision == 'include' and status != 'verified_primary':
            raise ValueError(f'queue index {index}: include requires explicit --verified-primary')
        if decision == 'watch' and status not in {'verified_primary', 'verified_secondary'}:
            raise ValueError(f'queue index {index}: watch requires explicit source verification')
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
    parser.add_argument("--verified-primary", help="indexes whose primary source was actually checked")
    parser.add_argument("--verified-secondary", help="indexes checked only against secondary sources")
    parser.add_argument("--reviewer")
    parser.add_argument("--actor-type", choices=["human", "agent", "unspecified"], default="unspecified")
    parser.add_argument("--interactive", action="store_true", help="review candidates and facts interactively")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    _, config = resolve_config(args.config)
    run_dir = runs_dir(config) / args.run_id
    review_path = run_dir / "p1-review.json"
    if not review_path.exists():
        raise SystemExit(f"review queue not found: {review_path}")
    review = read_json(review_path)
    if args.interactive:
        reviewer = args.reviewer or input("审核人 [欧阳文铮]: ").strip() or "欧阳文铮"
        try:
            decisions = interactive_decisions(review, reviewer)
        except (KeyboardInterrupt, EOFError) as exc:
            print(f"\n{exc or '审核已取消'}")
            return 130
    else:
        decisions = None
        print_queue(review)
        if args.list or not any((args.include, args.watch, args.exclude)):
            return 0
    if not args.interactive and not args.reviewer:
        raise SystemExit("--reviewer is required when applying decisions")

    if decisions is None:
        size = len(review.get("records", []))
        decisions = decisions_for(
            review,
            parse_selection(args.include, size),
            parse_selection(args.watch, size),
            parse_selection(args.exclude, size),
            args.reviewer,
            parse_selection(args.verified_primary, size),
            parse_selection(args.verified_secondary, size),
        )
    with tempfile.TemporaryDirectory(prefix="spectra-review-") as temp:
        decisions["actor_type"] = args.actor_type
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
