#!/usr/bin/env python3
import argparse
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


REQUIRED = {
    "schema_version", "record_type", "source_id", "registry_id", "source_type", "source_name",
    "publisher", "author", "source_url", "canonical_url", "raw_title", "raw_text", "raw_excerpt",
    "published_at", "collected_at", "language", "content_hash", "access_status", "http_status",
    "failure_reason", "discovered_by", "discovery_context", "rights_scope", "processing_status"
}


def valid_url(value):
    parts = urlsplit(value)
    return parts.scheme in {"http", "https"} and bool(parts.netloc)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    args = parser.parse_args()
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    errors = []
    ids = set()
    urls = set()
    for index, record in enumerate(payload.get("source_records", [])):
        label = record.get("source_id", f"record[{index}]")
        missing = REQUIRED - set(record)
        if missing:
            errors.append(f"{label}: missing {sorted(missing)}")
        if record.get("schema_version") != "0.2" or record.get("record_type") != "source_record":
            errors.append(f"{label}: invalid contract version/type")
        if not str(record.get("source_id", "")).startswith("src_"):
            errors.append(f"{label}: invalid source_id")
        if label in ids:
            errors.append(f"{label}: duplicate source_id")
        ids.add(label)
        for field in ("source_url", "canonical_url"):
            if not valid_url(record.get(field, "")):
                errors.append(f"{label}: invalid {field}")
        canonical = record.get("canonical_url")
        if canonical in urls:
            errors.append(f"{label}: duplicate canonical_url")
        urls.add(canonical)
        for field in ("collected_at",):
            try:
                datetime.fromisoformat(record[field].replace("Z", "+00:00"))
            except (KeyError, ValueError):
                errors.append(f"{label}: invalid {field}")
        if record.get("published_at"):
            try:
                datetime.fromisoformat(record["published_at"].replace("Z", "+00:00"))
            except ValueError:
                errors.append(f"{label}: invalid published_at")
        if record.get("access_status") in {"failed", "blocked"} and not record.get("failure_reason"):
            errors.append(f"{label}: failed/blocked record lacks failure_reason")
        if record.get("access_status") == "success" and record.get("failure_reason"):
            errors.append(f"{label}: successful record has failure_reason")
    summary_count = payload.get("summary", {}).get("source_records")
    if summary_count != len(payload.get("source_records", [])):
        errors.append("summary source_records does not match output length")
    if errors:
        print(json.dumps({"result": "fail", "errors": errors}, ensure_ascii=False, indent=2))
        raise SystemExit(1)
    print(json.dumps({"result": "pass", "source_records": len(ids), "unique_urls": len(urls)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
