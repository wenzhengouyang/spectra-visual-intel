#!/usr/bin/env python3
"""Apply an editor-written core event only after the normal content audit passes."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from editorial.core_event_pipeline import (  # noqa: E402
    WRITER_PROMPT_VERSION, build_bundle, completed_draft_from_audit,
)
from editorial.diagnostic_long_writer import audit_article  # noqa: E402
from editorial.fact_selection import build_fact_selection, validate_fact_selection  # noqa: E402


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


class NoCallClient:
    """Checkpoint assembly client; a reviewed draft must prevent model calls."""

    def __init__(self, model: str):
        self.settings = type("Settings", (), {"model": model})()

    def generate_json(self, **_: Any) -> tuple[dict, dict]:
        raise RuntimeError("reviewed draft unexpectedly triggered a model call")


def apply_reviewed_core_event(
    *, verified: dict[str, Any], review: dict[str, Any], collection: dict[str, Any],
    checkpoint: dict[str, Any], event_id: str, article: dict[str, Any], author: str,
) -> tuple[dict, dict, dict, dict, dict]:
    """Return updated review, fact selection, checkpoint, drafts and audit bundles."""
    updated_review = deepcopy(review)
    record = next(
        (item for item in updated_review.get("records", [])
         if (item.get("event") or {}).get("event_id") == event_id),
        None,
    )
    if not record or record.get("decision") != "include":
        raise ValueError(f"{event_id}: reviewed core event requires an included P1 record")
    if any(
        (fact.get("human_fact_decision") or fact.get("decision") or "keep") != "keep"
        for fact in record.get("suggested_evidence") or []
    ):
        raise ValueError(f"{event_id}: reviewed core event contains a non-kept fact")

    judgment = str(article.get("judgment") or "").strip()
    if not judgment:
        raise ValueError(f"{event_id}: reader judgment is required")
    applied_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record["reader_judgment"] = judgment
    record["reader_judgment_provenance"] = {
        "generated_by": author,
        "based_on_verified_by": updated_review.get("verified_by"),
        "applied_at": applied_at,
    }

    selection = build_fact_selection(verified, updated_review, collection)
    validate_fact_selection(selection, verified)
    plan = next(item for item in selection["selections"] if item["event_id"] == event_id)
    reader = plan["reader_packet"]
    facts = reader.get("fact_units") or []
    audit = audit_article(article, facts, reader.get("allowed_judgment", ""), reader.get("writing_profile", {}))
    if audit.get("status") != "passed":
        raise ValueError("reviewed core event failed audit: " + " | ".join(audit.get("errors") or []))

    input_fingerprint = hashlib.sha256(
        json.dumps(reader, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    completed = completed_draft_from_audit(event_id, article, facts, audit)
    updated_checkpoint = deepcopy(checkpoint)
    previous = (updated_checkpoint.get("jobs") or {}).get(event_id) or {}
    audit_record = {
        "event_id": event_id,
        "status": "passed",
        "attempt": int(previous.get("attempts", 0)) + 1,
        "phase": "reviewed_editorial_override",
        "cleaning_actions": [],
        "initial_draft": article,
        "revised_draft": article,
        "revision_history": list(previous.get("revision_history") or []),
        "input_fingerprint": input_fingerprint,
        "draft": article,
        "audit": audit,
        "provenance": {
            "generated_by": author,
            "based_on_verified_by": updated_review.get("verified_by"),
            "applied_at": applied_at,
        },
    }
    updated_checkpoint.setdefault("jobs", {})[event_id] = {
        "status": "completed",
        "attempts": audit_record["attempt"],
        "phase": "reviewed_editorial_override",
        "draft": completed,
        "audit_record": audit_record,
        "initial_draft": article,
        "revised_draft": article,
        "revision_history": audit_record["revision_history"],
        "input_fingerprint": input_fingerprint,
    }
    return updated_review, selection, updated_checkpoint


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--draft", required=True)
    parser.add_argument("--author", default="Codex")
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    paths = {
        "verified": run_dir / "verified-events.json",
        "review": run_dir / "p1-review.json",
        "collection": run_dir / "collection.json",
        "checkpoint": run_dir / "core-event-checkpoint.json",
        "selection": run_dir / "fact-selection.json",
        "drafts": run_dir / "core-event-drafts.json",
        "audit": run_dir / "core-event-audit.json",
    }
    verified = json.loads(paths["verified"].read_text(encoding="utf-8"))
    review = json.loads(paths["review"].read_text(encoding="utf-8"))
    collection = json.loads(paths["collection"].read_text(encoding="utf-8"))
    checkpoint = json.loads(paths["checkpoint"].read_text(encoding="utf-8"))
    article = json.loads(Path(args.draft).read_text(encoding="utf-8"))
    updated_review, selection, updated_checkpoint = apply_reviewed_core_event(
        verified=verified, review=review, collection=collection, checkpoint=checkpoint,
        event_id=args.event_id, article=article, author=args.author,
    )
    write_json(paths["review"], updated_review)
    write_json(paths["selection"], selection)
    write_json(paths["checkpoint"], updated_checkpoint)

    model = str(updated_checkpoint.get("model") or "qwen3:14b")
    bundle, audit_bundle = build_bundle(
        verified, selection, NoCallClient(model), paths["checkpoint"],
        max_attempts=3,
    )
    write_json(paths["drafts"], bundle)
    write_json(paths["audit"], audit_bundle)
    print(json.dumps({
        "event_id": args.event_id,
        "audit_status": next(
            item["status"] for item in audit_bundle["records"] if item["event_id"] == args.event_id
        ),
        "core_events": len(bundle["drafts"]),
        "prompt_version": WRITER_PROMPT_VERSION,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
