#!/usr/bin/env python3
"""Install reviewed replacement cover candidates without losing rejection history."""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from spectra_agent.image_review import embed_issue, pending_covers, write_json
from spectra_agent.publication_quality import expected_cover_motif


def parse_replacements(values: list[str]) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for value in values:
        story_id, separator, source = value.partition("=")
        if not separator or not story_id or not source:
            raise ValueError("replacement must be STORY_ID=IMAGE_PATH")
        result[story_id] = Path(source).expanduser().resolve()
    return result


def replace(run_dir: Path, replacements: dict[str, Path]) -> dict:
    issue_path = run_dir / "editorial-issue.json"
    html_path = run_dir / "rolling-digest.html"
    issue = json.loads(issue_path.read_text(encoding="utf-8"))
    stories = {item.get("story_id"): item for item in issue.get("editorial_stories") or []}
    unknown = set(replacements) - set(stories)
    if unknown:
        raise ValueError(f"unknown story IDs: {sorted(unknown)}")
    target_dir = run_dir / "assets/editorial"
    target_dir.mkdir(parents=True, exist_ok=True)
    installed = []
    for story_id, source in replacements.items():
        if not source.is_file() or source.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            raise ValueError(f"replacement is not a supported image: {source}")
        story = stories[story_id]
        cover = story.get("cover_image") or {}
        if cover.get("review_status") == "approved":
            raise ValueError(f"approved cover cannot be replaced without a new rejection: {story_id}")
        filename = f"{story_id}-topic-v2{source.suffix.lower()}"
        target = target_dir / filename
        shutil.copy2(source, target)
        previous = dict(cover)
        cover.update({
            "url": f"assets/editorial/{filename}", "kind": "generated", "label": "主题生成图",
            "credit": "SPECTRA", "semantic_motif": expected_cover_motif(str(story.get("headline") or ""), str(story.get("category") or "")),
            "semantic_match": "passed", "review_status": "pending", "previous_rejected_cover": previous,
        })
        for key in ("reviewed_by", "reviewed_at", "rejection_reason", "asset_fingerprint"):
            cover.pop(key, None)
        story["cover_image"] = cover
        installed.append({"story_id": story_id, "url": cover["url"]})
    write_json(issue_path, issue)
    if html_path.exists():
        embed_issue(html_path, issue)
    review_path = run_dir / "image-review.json"
    prior = json.loads(review_path.read_text(encoding="utf-8")) if review_path.exists() else {}
    updated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    report = {
        "schema_version": "1.0", "status": "replacement_pending_review", "replaced_at": updated_at,
        "replacements": installed, "prior_rejection": prior, "approved_story_ids": [],
        "rejected_story_ids": [], "approved_covers": [], "pending": pending_covers(issue),
    }
    write_json(review_path, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Install replacement candidates for rejected covers")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--replacement", action="append", default=[])
    args = parser.parse_args()
    report = replace(Path(args.run_dir).expanduser().resolve(), parse_replacements(args.replacement))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
