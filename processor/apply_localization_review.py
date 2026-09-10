#!/usr/bin/env python3
"""Validate and record supplied Chinese copy for one P2 localization review item."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from processor.p2_localizer import validate_translation  # noqa: E402


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--brief-id", required=True)
    parser.add_argument("--headline", required=True)
    parser.add_argument("--dek", required=True)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    path = Path(args.run_dir).resolve() / "p2-localization-review.json"
    queue = json.loads(path.read_text(encoding="utf-8"))
    record = next((item for item in queue.get("records", []) if item.get("brief_id") == args.brief_id), None)
    if not record:
        raise SystemExit(f"review item not found: {args.brief_id}")
    validate_translation(str(record.get("original_headline") or ""), args.headline, "headline")
    validate_translation(str(record.get("original_dek") or ""), args.dek, "dek")
    record.update({
        "review_status": "approved",
        "decision": "supply_chinese_copy",
        "headline_zh": args.headline,
        "dek_zh": args.dek,
        "reviewed_by": args.reviewer,
        "reviewed_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    })
    queue["status"] = "approved" if all(
        item.get("review_status") == "approved" for item in queue.get("records", [])
    ) else "waiting_for_review"
    write_json(path, queue)
    print(json.dumps({"brief_id": args.brief_id, "status": "approved"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
