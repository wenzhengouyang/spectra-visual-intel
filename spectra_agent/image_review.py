#!/usr/bin/env python3
"""List or approve generated story covers after a human preview."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from spectra_agent.publication_quality import expected_cover_motif
from spectra_agent.compat import canonical_artifact


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def embed_issue(path: Path, issue: dict) -> None:
    html = path.read_text(encoding="utf-8")
    payload = json.dumps(issue, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    replacement = f'<!-- ISSUE_DATA_START --><script id="issue-data" type="application/json">{payload}</script><!-- ISSUE_DATA_END -->'
    html, count = re.subn(
        r"<!-- ISSUE_DATA_START -->.*?<!-- ISSUE_DATA_END -->",
        lambda _: replacement,
        html,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError("rolling digest is missing the issue payload markers")
    path.write_text(html, encoding="utf-8")


def pending_covers(issue: dict) -> list[dict]:
    pending = []
    for story in issue.get("editorial_stories") or []:
        cover = story.get("cover_image") or {}
        if cover.get("kind") not in {"editorial_diagram", "generated"}:
            continue
        if cover.get("review_status") == "approved":
            continue
        pending.append({
            "story_id": story.get("story_id"),
            "headline": story.get("headline"),
            "url": cover.get("url"),
            "semantic_motif": cover.get("semantic_motif"),
        })
    return pending


def asset_fingerprint(run_dir: Path, url: str) -> str:
    path = run_dir / url
    if not path.is_file():
        raise ValueError(f'cover asset is missing: {path}')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Human preview gate for generated SPECTRA covers")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--approve", help="comma-separated story IDs, or all")
    parser.add_argument("--reviewer")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    issue_path = run_dir / "editorial-issue.json"
    html_path = canonical_artifact(run_dir, "rolling-digest.html")
    issue = json.loads(issue_path.read_text(encoding="utf-8"))
    pending = pending_covers(issue)
    if not args.approve:
        print(json.dumps({"status": "waiting_for_image_review" if pending else "approved", "pending": pending}, ensure_ascii=False, indent=2))
        return 2 if pending else 0
    if not args.reviewer:
        raise ValueError("--reviewer is required when approving generated covers")
    selected = {item["story_id"] for item in pending} if args.approve == "all" else {
        value.strip() for value in args.approve.split(",") if value.strip()
    }
    known = {item["story_id"] for item in pending}
    if not selected <= known:
        raise ValueError(f"unknown or already reviewed story IDs: {sorted(selected - known)}")
    reviewed_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    for story in issue.get("editorial_stories") or []:
        if story.get("story_id") not in selected:
            continue
        cover = story["cover_image"]
        expected = expected_cover_motif(str(story.get("headline") or ""), str(story.get("category") or ""))
        if cover.get("semantic_motif") != expected or cover.get("semantic_match") != "passed":
            raise ValueError(f"{story['story_id']} failed semantic cover matching")
        cover.update({"review_status": "approved", "reviewed_by": args.reviewer, "reviewed_at": reviewed_at,
                      "asset_fingerprint": asset_fingerprint(run_dir, str(cover.get('url') or ''))})
    write_json(issue_path, issue)
    if html_path.exists():
        embed_issue(html_path, issue)
    report = {
        "schema_version": "1.0",
        "reviewed_at": reviewed_at,
        "reviewed_by": args.reviewer,
        "approved_story_ids": sorted(selected),
        "approved_covers": [{"story_id": story["story_id"],
            "asset_fingerprint": asset_fingerprint(run_dir, story["cover_image"]["url"])}
            for story in issue.get("editorial_stories", []) if story.get("story_id") in selected],
        "pending": pending_covers(issue),
    }
    write_json(run_dir / "image-review.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not report["pending"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
