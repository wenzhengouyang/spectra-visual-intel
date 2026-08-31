#!/usr/bin/env python3
"""Generate P1 long stories serially, then clean and audit them in code."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectra_agent.llm_client import create_llm_client  # noqa: E402
from editorial.diagnostic_long_writer import audit_article, generate_long_story  # noqa: E402
from editorial.finalize_diagnostic_long_stories import clean  # noqa: E402


def write_checkpoint(path: Path | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_bundle(verified: dict[str, Any], selection: dict[str, Any], client,
                 checkpoint_path: Path | None = None, max_attempts: int = 2) -> tuple[dict, dict]:
    plans = {item["event_id"]: item for item in selection["selections"]}
    event_map = {item["event_id"]: item for item in verified["intelligence_events"]}
    event_ids = [
        event_id for event_id in verified.get("editorial_selection", {}).get("top_event_ids", [])
        if event_id in event_map
    ][:5]
    drafts, blocked, demoted, records, calls = [], [], [], [], []
    checkpoint = {"schema_version": "0.1", "record_type": "p1_long_job_checkpoint", "jobs": {}}
    if checkpoint_path and checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    jobs = checkpoint.setdefault("jobs", {})
    for event_id in event_ids:
        plan = plans[event_id]
        reader = plan["reader_packet"]
        facts = reader.get("fact_units") or []
        profile = reader.get("writing_profile") or {}
        if profile.get("mode") != "long_form" or len(facts) < int(profile.get("min_fact_units", 8)):
            demoted.append({
                "event_id": event_id,
                "target_article_type": "quick_read",
                "reason": "fewer_than_8_human_verified_fact_units",
                "fact_units": len(facts),
            })
            jobs[event_id] = {"status": "demoted", "fact_units": len(facts)}
            write_checkpoint(checkpoint_path, checkpoint)
            continue
        cached = jobs.get(event_id) or {}
        if cached.get("status") == "completed":
            drafts.append(cached["draft"])
            records.append(cached["audit_record"])
            if cached.get("llm_call"):
                calls.append(cached["llm_call"])
            continue
        if cached.get("status") in {"manual_review", "demoted_after_failed_long_story"} and int(cached.get("attempts", 0)) >= max_attempts:
            demoted.append({
                "event_id": event_id,
                "target_article_type": "quick_read",
                "reason": "long_story_failed_after_max_attempts",
                "attempts": int(cached.get("attempts", 0)),
                "audit_errors": ((cached.get("audit_record") or {}).get("audit") or {}).get("errors", []),
            })
            if cached.get("audit_record"):
                records.append(cached["audit_record"])
            jobs[event_id] = {**cached, "status": "demoted_after_failed_long_story"}
            write_checkpoint(checkpoint_path, checkpoint)
            continue
        attempt = int(cached.get("attempts", 0))
        last_error = None
        completed_draft = None
        audit_record = None
        while attempt < max_attempts:
            attempt += 1
            jobs[event_id] = {"status": "running", "attempts": attempt}
            write_checkpoint(checkpoint_path, checkpoint)
            try:
                raw, metadata = generate_long_story(reader, event_id, client)
                call_record = {"event_id": event_id, "attempt": attempt, **metadata}
                calls.append(call_record)
                cleaned, actions = clean(raw)
                audit = audit_article(
                    cleaned, facts, reader.get("allowed_judgment", ""),
                    reader.get("writing_profile", {}),
                )
                audit_record = {
                    "event_id": event_id, "status": audit["status"],
                    "attempt": attempt, "cleaning_actions": actions,
                    "draft": cleaned, "audit": audit,
                }
                if audit["status"] != "passed":
                    last_error = "programmatic_long_story_audit_failed: " + " | ".join(audit["errors"])
                    jobs[event_id] = {
                        "status": "retryable_failure", "attempts": attempt,
                        "error": last_error, "audit_record": audit_record,
                    }
                    write_checkpoint(checkpoint_path, checkpoint)
                    continue
                mappings = audit["paragraph_claim_mapping"]
                all_claim_ids = [fact["claim_id"] for fact in facts]
                completed_draft = {
                    "event_id": event_id,
                    "headline": cleaned["headline"],
                    "dek": cleaned["dek"],
                    "one_line_takeaway": cleaned["judgment"],
                    "factual_paragraphs": [
                        {"text": text, "claim_ids": mappings[index]["claim_ids"]}
                        for index, text in enumerate(cleaned["paragraphs"])
                    ],
                    "judgment": cleaned["judgment"],
                    "watch_next": [],
                    "claim_support": {
                        "headline": all_claim_ids, "dek": all_claim_ids,
                        "one_line_takeaway": all_claim_ids, "judgment": all_claim_ids,
                    },
                }
                jobs[event_id] = {
                    "status": "completed", "attempts": attempt,
                    "draft": completed_draft, "audit_record": audit_record,
                    "llm_call": call_record,
                }
                write_checkpoint(checkpoint_path, checkpoint)
                break
            except Exception as exc:
                last_error = str(exc)
                jobs[event_id] = {
                    "status": "retryable_failure", "attempts": attempt, "error": last_error,
                }
                write_checkpoint(checkpoint_path, checkpoint)
        if completed_draft is None:
            demoted.append({
                "event_id": event_id,
                "target_article_type": "quick_read",
                "reason": "long_story_failed_after_max_attempts",
                "attempts": attempt,
                "audit_errors": ((audit_record or {}).get("audit") or {}).get("errors", []),
            })
            records.append(audit_record or {
                "event_id": event_id, "status": "generation_failed", "error": last_error,
            })
            jobs[event_id] = {
                **jobs.get(event_id, {}), "status": "demoted_after_failed_long_story", "attempts": attempt,
                "error": last_error,
            }
            write_checkpoint(checkpoint_path, checkpoint)
            continue
        drafts.append(completed_draft)
        records.append(audit_record)
    bundle = {
        "schema_version": "0.2",
        "record_type": "deep_story_draft_bundle",
        "prompt_version": "p1_long_writer.v0.1",
        "generation_mode": "serial_background_local_14b",
        "drafts": drafts,
        "blocked": blocked,
        "demoted": demoted,
        "llm_calls": calls,
    }
    audit_bundle = {
        "schema_version": "0.1",
        "record_type": "p1_long_editorial_audit",
        "internal_only": True,
        "publication_status": "not_approved",
        "records": records,
    }
    return bundle, audit_bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified", required=True)
    parser.add_argument("--fact-selection", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--audit-output", required=True)
    parser.add_argument("--model", default="qwen3:14b")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--num-predict", type=int, default=1200)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--max-attempts", type=int, default=2)
    args = parser.parse_args()
    os.environ["SPECTRA_MODEL"] = args.model
    os.environ["OLLAMA_NUM_CTX"] = str(args.num_ctx)
    os.environ["OLLAMA_NUM_PREDICT"] = str(args.num_predict)
    bundle, audit = build_bundle(
        json.loads(Path(args.verified).read_text(encoding="utf-8")),
        json.loads(Path(args.fact_selection).read_text(encoding="utf-8")),
        create_llm_client(), Path(args.checkpoint), args.max_attempts,
    )
    Path(args.output).write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.audit_output).write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "deep_stories": len(bundle["drafts"]), "blocked": len(bundle["blocked"]),
        "demoted": len(bundle["demoted"]), "audit": args.audit_output,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
