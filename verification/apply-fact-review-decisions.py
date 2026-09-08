#!/usr/bin/env python3
"""Apply a compact, auditable human fact-review decision file to p1-review.json."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectra_agent.compat import rolling_thesis
from spectra_agent.execution import atomic_json


def read(path: str) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def apply_decisions(review: dict[str, Any], decisions: dict[str, Any]) -> dict[str, Any]:
    decision_map = decisions.get("records") or {}
    record_ids = {item["candidate_id"] for item in review.get("records", [])}
    if set(decision_map) != record_ids:
        raise ValueError(
            f"decision mismatch: missing={sorted(record_ids - set(decision_map))}, "
            f"unknown={sorted(set(decision_map) - record_ids)}"
        )
    for record in review["records"]:
        candidate_id = record["candidate_id"]
        selected = decision_map[candidate_id]
        status = selected["verification_status"]
        decision = selected["decision"]
        if decision == "include" and status != "verified_primary":
            raise ValueError(f"{candidate_id}: include requires verified_primary")
        if decision == "watch" and status not in {"verified_primary", "verified_secondary"}:
            raise ValueError(f"{candidate_id}: watch requires a reviewed source status")
        fact_decisions = selected.get("fact_decisions") or []
        suggestions = record.get("suggested_evidence") or []
        if len(fact_decisions) != len(suggestions):
            raise ValueError(
                f"{candidate_id}: expected {len(suggestions)} fact decisions, got {len(fact_decisions)}"
            )
        for index, (fact, fact_decision) in enumerate(zip(suggestions, fact_decisions), 1):
            action = fact_decision if isinstance(fact_decision, str) else fact_decision.get("decision")
            if action not in {"keep", "modify", "drop"}:
                raise ValueError(f"{candidate_id}: invalid fact decision {index}: {action}")
            fact["human_fact_decision"] = action
            if action == "modify":
                if not isinstance(fact_decision, dict) or not fact_decision.get("text") or not fact_decision.get("kind"):
                    raise ValueError(f"{candidate_id}: modified fact {index} requires text and kind")
                fact["human_fact_text"] = fact_decision["text"]
                fact["human_fact_kind"] = fact_decision["kind"]
        record["approve_all_suggested_facts"] = False
        record["verification_status"] = status
        record["decision"] = decision
        record["decision_reason"] = selected["decision_reason"]
        record["limitation"] = selected["limitation"]
        record["reviewed_by"] = decisions["verified_by"]
        record["review_method"] = "human_primary_source" if status == "verified_primary" else (
            "human_secondary_source" if status == "verified_secondary" else "human_selection_only"
        )
        record["claims"] = []
        record["event"] = None
    review["review_status"] = "approved"
    review["verified_by"] = decisions["verified_by"]
    review["verified_at"] = decisions.get("verified_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for record in review["records"]:
        record["reviewed_at"] = review["verified_at"]
    thesis = rolling_thesis(decisions)
    if thesis:
        review["editorial_selection"] = {"rolling_thesis": thesis}
    return review


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", required=True)
    parser.add_argument("--decisions", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    result = apply_decisions(read(args.review), read(args.decisions))
    output = Path(args.output)
    atomic_json(output, result)
    print(json.dumps({
        "output": str(output),
        "include": sum(item["decision"] == "include" for item in result["records"]),
        "watch": sum(item["decision"] == "watch" for item in result["records"]),
        "verified_by": result["verified_by"],
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
