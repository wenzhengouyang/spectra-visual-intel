#!/usr/bin/env python3
"""Replace reviewed brief copy in a completed run while preserving an audit backup."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--brief-id")
    target.add_argument("--story-id")
    parser.add_argument("--headline", required=True)
    parser.add_argument("--dek", required=True)
    args = parser.parse_args()

    issue_path = args.run_dir.resolve() / "editorial-issue.json"
    issue = json.loads(issue_path.read_text(encoding="utf-8"))
    item_id = args.brief_id or args.story_id
    collection = "news_briefs" if args.brief_id else "editorial_stories"
    id_field = "brief_id" if args.brief_id else "story_id"
    brief = next((item for item in issue.get(collection) or []
                  if item.get(id_field) == item_id), None)
    if brief is None:
        raise ValueError(f"{id_field.removesuffix('_id')} not found: {item_id}")

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = issue_path.with_name(f"editorial-issue.before-copy-fix-{timestamp}.json")
    shutil.copy2(issue_path, backup)
    before = {"headline": brief.get("headline"), "dek": brief.get("dek")}
    brief.update(headline=args.headline, dek=args.dek)
    issue_path.write_text(json.dumps(issue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    overrides_path = issue_path.with_name("editorial-overrides.json")
    overrides = json.loads(overrides_path.read_text(encoding="utf-8")) if overrides_path.exists() else {}
    overrides.setdefault(collection, {})[item_id] = {
        "headline": args.headline,
        "dek": args.dek,
        "reason": "source_verified_manual_correction",
    }
    overrides_path.write_text(json.dumps(overrides, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    audit = {
        id_field: item_id,
        "updated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "reason": "replace_internal_verification_placeholder_with_source_verified_copy",
        "before": before,
        "after": {"headline": args.headline, "dek": args.dek},
        "backup": backup.name,
    }
    issue_path.with_name(f"copy-fix-{item_id}.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
