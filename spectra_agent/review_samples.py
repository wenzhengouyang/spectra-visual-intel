"""Local, idempotent review examples. No training/export happens here."""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3


def record_sample(run_dir: Path, *, kind, item_id, before, after, reviewer,
                  actor_type="unspecified", reason=None):
    if actor_type not in {"human", "agent", "unspecified"}:
        raise ValueError("invalid review actor type")
    sample = {"schema_version": "1.0", "run_id": run_dir.name, "kind": kind, "item_id": item_id,
              "input": before, "decision": after, "reviewer": reviewer,
              "actor_type": actor_type, "reason": reason,
              "training_eligible": False, "requires_dataset_review": True}
    # Timestamp-free identity deduplicates repeated identical submissions.
    encoded = json.dumps(sample, ensure_ascii=False, sort_keys=True)
    sample_id = hashlib.sha256(encoded.encode()).hexdigest()
    run_dir.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(run_dir / "review-samples.sqlite") as db:
        db.execute("CREATE TABLE IF NOT EXISTS samples (id TEXT PRIMARY KEY, created_at TEXT, payload TEXT)")
        db.execute("INSERT OR IGNORE INTO samples VALUES(?,?,?)",
                   (sample_id, datetime.now(timezone.utc).isoformat(), encoded))
    return sample_id


def record_fact_review(run_dir, before, after, actor_type="unspecified", kind="fact"):
    originals = {r["candidate_id"]: r for r in before.get("records", [])}
    for record in after.get("records", []):
        original = originals.get(record["candidate_id"], {})
        record_sample(run_dir, kind=kind, item_id=record["candidate_id"],
            before={"baseline_available": bool(original), **{k: original.get(k) for k in ("title", "url", "suggested_evidence", "supplemental_evidence")}},
            after={k: record.get(k) for k in ("decision", "verification_status", "suggested_evidence", "key_facts", "limitation")},
            reviewer=after.get("verified_by"), actor_type=actor_type, reason=record.get("decision_reason"))
