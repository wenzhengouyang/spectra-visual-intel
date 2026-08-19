#!/usr/bin/env python3
"""Merge verified evidence with a human editorial selection.

The script keeps model analysis from the active Agent run, imports only the
primary-source evidence fields from a reviewed evidence file, and applies the
editor's include/watch decisions.  It never creates evidence claims.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path


def read(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", required=True)
    parser.add_argument("--evidence-review", required=True)
    parser.add_argument("--selection", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    review = read(args.review)
    evidence = read(args.evidence_review)
    selection = read(args.selection)
    evidence_by_id = {item["candidate_id"]: item for item in evidence["records"]}
    include_ids = set(selection["include_candidate_ids"])
    watch_ids = set(selection["watch_candidate_ids"])
    record_ids = {item["candidate_id"] for item in review["records"]}

    if include_ids & watch_ids:
        raise ValueError("a candidate cannot be both include and watch")
    if include_ids | watch_ids != record_ids:
        missing = sorted(record_ids - include_ids - watch_ids)
        unknown = sorted((include_ids | watch_ids) - record_ids)
        raise ValueError(f"selection mismatch: missing={missing}, unknown={unknown}")
    if not 5 <= len(include_ids) <= 10:
        raise ValueError(f"formal events must be 5-10, got {len(include_ids)}")

    event_overrides = selection.get("event_overrides", {})
    for record in review["records"]:
        candidate_id = record["candidate_id"]
        source = evidence_by_id.get(candidate_id)
        if not source:
            raise ValueError(f"no verified evidence for {candidate_id}")
        for key in ("verification_status", "claims", "limitation"):
            record[key] = source[key]
        record["decision"] = "include" if candidate_id in include_ids else "watch"
        record["decision_reason"] = selection["decision_reasons"][candidate_id]
        if record["decision"] == "include":
            record["event"] = event_overrides.get(candidate_id) or source.get("event")
            if not record["event"]:
                raise ValueError(f"included candidate {candidate_id} has no event metadata")
        else:
            record["event"] = None

    review["review_status"] = "approved"
    review["verified_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    review["verified_by"] = selection["verified_by"]
    review["editorial_selection"] = {"weekly_thesis": selection["weekly_thesis"]}
    output = Path(args.output)
    output.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"result": "pass", "include": len(include_ids), "watch": len(watch_ids), "output": str(output)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
