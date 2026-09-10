#!/usr/bin/env python3
"""Merge a rolling-window baseline, a delta pull, and optional full-window retries."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def merge(
    baseline: dict[str, Any],
    delta: dict[str, Any],
    retry: dict[str, Any] | None,
    window_start: str,
    window_end: str,
) -> dict[str, Any]:
    start, end = parse_time(window_start), parse_time(window_end)
    bundles = [("baseline", baseline), ("delta", delta)]
    if retry:
        bundles.append(("full_window_retry", retry))
    by_url: dict[str, dict[str, Any]] = {}
    by_hash: dict[str, str] = {}
    rank = {"failed": 0, "blocked": 1, "partial": 2, "success": 3}
    baseline_by_url = {
        item.get("canonical_url"): item
        for item in baseline.get("source_records", [])
        if item.get("canonical_url")
    }
    for origin, bundle in bundles:
        for source in bundle.get("source_records", []):
            published = source.get("published_at")
            if not published or not start <= parse_time(published) <= end:
                continue
            record = dict(source)
            record["collection_origin"] = origin
            url = record["canonical_url"]
            digest = record.get("content_hash")
            existing = by_url.get(url)
            existing_rank = rank.get((existing or {}).get("access_status"), 0)
            record_rank = rank.get(record.get("access_status"), 0)
            same_content = bool(
                existing
                and digest
                and digest == existing.get("content_hash")
            )
            if (
                existing is None
                or record_rank > existing_rank
                or (record_rank == existing_rank and not same_content)
            ):
                by_url[url] = record
            if digest and digest in by_hash and by_hash[digest] != url:
                prior_url = by_hash[digest]
                prior = by_url.get(prior_url)
                prior_rank = rank.get((prior or {}).get("access_status"), 0)
                if prior and record_rank > prior_rank:
                    by_url.pop(prior_url, None)
                    by_hash[digest] = url
                else:
                    by_url.pop(url, None)
            elif digest:
                by_hash[digest] = url

    records = sorted(
        by_url.values(),
        key=lambda item: (item.get("published_at") or "", item.get("source_name") or ""),
        reverse=True,
    )
    changed_ids = {
        item["source_id"]
        for item in records
        if item.get("collection_origin") != "baseline"
        and (
            item.get("canonical_url") not in baseline_by_url
            or item.get("content_hash")
            != baseline_by_url[item["canonical_url"]].get("content_hash")
            or item.get("access_status")
            != baseline_by_url[item["canonical_url"]].get("access_status")
        )
    }
    baseline_checks = {item["registry_id"]: item for item in baseline.get("source_checks", [])}
    delta_checks = {item["registry_id"]: item for item in delta.get("source_checks", [])}
    retry_checks = {item["registry_id"]: item for item in (retry or {}).get("source_checks", [])}
    checks = []
    for registry_id in sorted(set(baseline_checks) | set(delta_checks) | set(retry_checks)):
        current = dict(delta_checks.get(registry_id) or baseline_checks.get(registry_id) or {})
        if registry_id in retry_checks:
            retried = retry_checks[registry_id]
            current["full_window_retry"] = retried
            if retried.get("status") == "success":
                current = {**current, **retried, "effective_status": "recovered_by_full_window_retry"}
        checks.append(current)
    return {
        "schema_version": baseline.get("schema_version", "0.2"),
        "record_type": "incremental_collection_run",
        "run_id": delta.get("run_id"),
        "window_start": window_start,
        "window_end": window_end,
        "collected_at": delta.get("collected_at"),
        "source_checks": checks,
        "incremental": {
            "baseline_run_id": baseline.get("run_id"),
            "delta_start": delta.get("window_start"),
            "delta_end": delta.get("window_end"),
            "changed_source_ids": sorted(changed_ids),
            "full_window_retry_sources": sorted(retry_checks),
            "processing_scope": "changed_records_only",
        },
        "summary": {
            "configured_sources": len(checks),
            "successful_sources": sum(item.get("status") == "success" for item in checks),
            "degraded_sources": sum(item.get("status") == "degraded" for item in checks),
            "failed_sources": sum(item.get("status") == "failed" for item in checks),
            "source_records": len(records),
            "successful_records": sum(item.get("access_status") == "success" for item in records),
            "failed_records": sum(item.get("access_status") == "failed" for item in records),
            "baseline_records_reused": sum(item.get("collection_origin") == "baseline" for item in records),
            "incremental_records": sum(item.get("collection_origin") == "delta" for item in records),
            "retry_records": sum(item.get("collection_origin") == "full_window_retry" for item in records),
            "changed_records": len(changed_ids),
            "unchanged_records": len(records) - len(changed_ids),
        },
        "source_records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--delta", required=True)
    parser.add_argument("--retry")
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    load = lambda value: json.loads(Path(value).read_text(encoding="utf-8"))
    result = merge(load(args.baseline), load(args.delta), load(args.retry) if args.retry else None,
                   args.window_start, args.window_end)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
