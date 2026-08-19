#!/usr/bin/env python3
"""Overlay a live collection with same-window records from a prior successful run.

Only sources that failed in the live run are eligible for fallback. Current
successful records always win, and fallback records must still fall inside the
current run's time window. Source health remains failed/visible; the merge does
not pretend an external outage did not happen.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def merge(current: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
    window_start = parse_time(current["window_start"])
    window_end = parse_time(current["window_end"])
    assert window_start and window_end
    failed_registry_ids = {
        item["registry_id"] for item in current.get("source_checks", [])
        if item.get("status") == "failed"
    }
    fallback_records = []
    fallback_counts: dict[str, int] = {}
    for record in fallback.get("source_records", []):
        registry_id = record.get("registry_id")
        published = parse_time(record.get("published_at"))
        if (
            registry_id not in failed_registry_ids
            or record.get("access_status") != "success"
            or not published
            or not window_start <= published.astimezone(timezone.utc) <= window_end
        ):
            continue
        copied = dict(record)
        copied["discovery_context"] = (
            f"{copied.get('discovery_context') or ''}; "
            f"fallback_collection:{fallback.get('run_id', 'unknown')}"
        ).strip("; ")
        fallback_records.append(copied)
        fallback_counts[registry_id] = fallback_counts.get(registry_id, 0) + 1

    current_records = [
        record for record in current.get("source_records", [])
        if not (
            record.get("registry_id") in fallback_counts
            and record.get("access_status") != "success"
        )
    ]
    by_url = {record["canonical_url"]: record for record in fallback_records}
    for record in current_records:
        by_url[record["canonical_url"]] = record
    records = sorted(
        by_url.values(),
        key=lambda record: (record.get("published_at") or "", record.get("source_name") or ""),
        reverse=True,
    )
    checks = []
    for check in current.get("source_checks", []):
        updated = dict(check)
        count = fallback_counts.get(check.get("registry_id"), 0)
        if count:
            updated["effective_status"] = "fallback"
            updated["fallback_records"] = count
            updated["fallback_run_id"] = fallback.get("run_id")
        checks.append(updated)
    result = dict(current)
    result["record_type"] = "collection_run_with_fallback"
    result["source_checks"] = checks
    result["source_records"] = records
    result["fallback"] = {
        "run_id": fallback.get("run_id"),
        "failed_registry_ids": sorted(failed_registry_ids),
        "restored_registry_ids": sorted(fallback_counts),
        "restored_records": len(fallback_records),
    }
    result["summary"] = {
        **current.get("summary", {}),
        "source_records": len(records),
        "successful_records": sum(record.get("access_status") == "success" for record in records),
        "failed_records": sum(record.get("access_status") == "failed" for record in records),
        "fallback_records": len(fallback_records),
        "fallback_sources": len(fallback_counts),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--current", required=True)
    parser.add_argument("--fallback", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    current = json.loads(Path(args.current).read_text(encoding="utf-8"))
    fallback = json.loads(Path(args.fallback).read_text(encoding="utf-8"))
    result = merge(current, fallback)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
