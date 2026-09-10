#!/usr/bin/env python3
"""Generate P1 long stories serially, then clean and audit them in code."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectra_agent.llm_client import LLMProviderError, create_llm_client  # noqa: E402
from editorial.diagnostic_long_writer import (  # noqa: E402
    audit_article, generate_long_story, revise_long_story,
)
from editorial.finalize_diagnostic_long_stories import clean  # noqa: E402

CHECKPOINT_SCHEMA_VERSION = "0.2"
WRITER_PROMPT_VERSION = "core_event_writer.v1.2.3"


def completed_draft_from_audit(event_id: str, cleaned: dict[str, Any], facts: list[dict],
                               audit: dict[str, Any]) -> dict[str, Any]:
    """Convert an audited article into the canonical core-event draft shape."""
    if audit.get("status") != "passed":
        raise ValueError(f"{event_id}: cannot complete a draft that failed editorial audit")
    mappings = audit["paragraph_claim_mapping"]
    all_claim_ids = [fact["claim_id"] for fact in facts]
    return {
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


def write_checkpoint(path: Path | None, payload: dict[str, Any]) -> None:
    if not path:
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def build_bundle(verified: dict[str, Any], selection: dict[str, Any], client,
                 checkpoint_path: Path | None = None, max_attempts: int = 3,
                 event_ids: list[str] | None = None) -> tuple[dict, dict]:
    plans = {item["event_id"]: item for item in selection["selections"]}
    event_map = {item["event_id"]: item for item in verified["intelligence_events"]}
    requested_event_ids = set(event_ids) if event_ids is not None else None
    event_ids = [
        event_id for event_id in verified.get("editorial_selection", {}).get("top_event_ids", [])
        if event_id in event_map
    ][:5]
    if requested_event_ids is not None:
        event_ids = [event_id for event_id in event_ids if event_id in requested_event_ids]
    drafts, blocked, demoted, records, calls = [], [], [], [], []
    expected_model = getattr(getattr(client, "settings", None), "model", None)
    current_window = selection.get("source_window") or {}
    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "record_type": "core_event_job_checkpoint",
        "prompt_version": WRITER_PROMPT_VERSION,
        "model": expected_model,
        "source_window": current_window,
        "jobs": {},
    }
    if checkpoint_path and checkpoint_path.exists():
        try:
            loaded = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            loaded = {}
        saved_window = loaded.get("source_window") or {}
        window_compatible = bool(
            current_window.get("start")
            and current_window.get("end")
            and saved_window == current_window
        )
        schema_compatible = loaded.get("schema_version") == CHECKPOINT_SCHEMA_VERSION
        prompt_compatible = loaded.get("prompt_version") == WRITER_PROMPT_VERSION
        model_compatible = not expected_model or loaded.get("model") == expected_model

        if schema_compatible and prompt_compatible and model_compatible and window_compatible:
            # Full compatibility: use entire checkpoint
            checkpoint = loaded
        elif schema_compatible and window_compatible and model_compatible and loaded.get("jobs"):
            # A prompt update should retry failures, not discard already audited
            # drafts. Input fingerprints are still checked below before reuse.
            recovered_jobs = {
                event_id: job for event_id, job in loaded.get("jobs", {}).items()
                if job.get("status") == "completed" and job.get("draft")
                and ((job.get("audit_record") or {}).get("audit") or {}).get("status") == "passed"
            }
            if recovered_jobs:
                checkpoint["jobs"] = recovered_jobs
                checkpoint["_recovered_from"] = {
                    "prompt_version": loaded.get("prompt_version"),
                    "model": loaded.get("model"),
                    "recovered_jobs": len(recovered_jobs),
                }
    jobs = checkpoint.setdefault("jobs", {})
    for event_id in event_ids:
        plan = plans[event_id]
        reader = plan["reader_packet"]
        input_fingerprint = hashlib.sha256(
            json.dumps(reader, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        facts = reader.get("fact_units") or []
        profile = reader.get("writing_profile") or {}
        if profile.get("mode") != "long_form" or len(facts) < int(profile.get("min_fact_units", 8)):
            demoted.append({
                "event_id": event_id,
                "target_article_type": "quick_read",
                "reason": "fewer_than_8_human_verified_fact_units",
                "fact_units": len(facts),
            })
            jobs[event_id] = {
                "status": "demoted",
                "fact_units": len(facts),
                "input_fingerprint": input_fingerprint,
            }
            write_checkpoint(checkpoint_path, checkpoint)
            continue
        cached = jobs.get(event_id) or {}
        if cached.get("input_fingerprint") != input_fingerprint:
            cached = {}
        if cached.get("status") == "completed":
            saved_article = (cached.get("audit_record") or {}).get("draft")
            fresh_audit = audit_article(saved_article, facts, reader.get("allowed_judgment", ""), profile) if saved_article else None
            if not fresh_audit:
                cached = {}
            elif fresh_audit.get("status") != "passed":
                cached = {**cached, "status": "retryable_failure", "attempts": 0,
                          "audit_record": {**cached["audit_record"], "audit": fresh_audit, "status": "failed"},
                          "revised_draft": saved_article}
        if cached.get("status") == "completed" and cached.get("input_fingerprint") == input_fingerprint:
            drafts.append(cached["draft"])
            records.append(cached["audit_record"])
            if cached.get("llm_call"):
                calls.append(cached["llm_call"])
            continue
        if (
            cached.get("input_fingerprint") == input_fingerprint
            and cached.get("status") in {"manual_review", "demoted_after_failed_long_story"}
            and int(cached.get("attempts", 0)) >= max_attempts
        ):
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
        audit_record = cached.get("audit_record")
        initial_draft = cached.get("initial_draft")
        latest_draft = cached.get("revised_draft") or initial_draft
        latest_audit = (audit_record or {}).get("audit")
        revision_history = list(cached.get("revision_history") or [])
        while attempt < max_attempts:
            attempt += 1
            phase = "initial_generation" if latest_draft is None else "targeted_revision"
            jobs[event_id] = {
                **jobs.get(event_id, {}), "status": "running", "attempts": attempt, "phase": phase,
                "input_fingerprint": input_fingerprint,
            }
            write_checkpoint(checkpoint_path, checkpoint)
            try:
                revision = None
                if latest_draft is None:
                    raw, metadata = generate_long_story(reader, event_id, client)
                    cleaned, actions = clean(raw)
                    initial_draft = cleaned
                else:
                    raw, metadata, revision = revise_long_story(
                        reader, event_id, latest_draft, latest_audit or {}, client
                    )
                    # The patch is already constrained to audited locations. Running the
                    # whole-article cleaner here can reinsert an unfixed dek into a repaired
                    # paragraph and silently undo the targeted edit.
                    cleaned, actions = raw, []
                    revision_history.append({
                        "attempt": attempt, "audit_errors": (latest_audit or {}).get("errors", []),
                        "revision_mode": revision.get("revision_mode", "targeted_fact_repair"),
                        "patches": revision.get("patches") or [],
                        "paragraph_evidence_plan": revision.get("paragraph_evidence_plan") or [],
                        "programmatic_backfill_fact_ids": (
                            revision.get("programmatic_backfill_fact_ids") or []
                        ),
                    })
                call_record = {"event_id": event_id, "attempt": attempt, "phase": phase, **metadata}
                calls.append(call_record)
                audit = audit_article(
                    cleaned, facts, reader.get("allowed_judgment", ""),
                    reader.get("writing_profile", {}),
                )
                latest_draft, latest_audit = cleaned, audit
                audit_record = {
                    "event_id": event_id, "status": audit["status"],
                    "attempt": attempt, "cleaning_actions": actions,
                    "initial_draft": initial_draft,
                    "revised_draft": cleaned if revision is not None else None,
                    "revision_history": revision_history,
                    "input_fingerprint": input_fingerprint,
                    "draft": cleaned, "audit": audit,
                }
                if audit["status"] != "passed":
                    last_error = "programmatic_long_story_audit_failed: " + " | ".join(audit["errors"])
                    jobs[event_id] = {
                        "status": "retryable_failure", "attempts": attempt,
                        "error": last_error, "audit_record": audit_record,
                        "initial_draft": initial_draft,
                        "revised_draft": cleaned if revision is not None else None,
                        "revision_history": revision_history,
                        "input_fingerprint": input_fingerprint,
                    }
                    write_checkpoint(checkpoint_path, checkpoint)
                    continue
                completed_draft = completed_draft_from_audit(event_id, cleaned, facts, audit)
                jobs[event_id] = {
                    "status": "completed", "attempts": attempt,
                    "draft": completed_draft, "audit_record": audit_record,
                    "llm_call": call_record, "initial_draft": initial_draft,
                    "revised_draft": cleaned if revision is not None else None,
                    "revision_history": revision_history,
                    "input_fingerprint": input_fingerprint,
                }
                write_checkpoint(checkpoint_path, checkpoint)
                break
            except LLMProviderError as exc:
                # Provider outages are infrastructure failures, not evidence that a
                # story is editorially unfit. Preserve the job for resume and stop
                # the pipeline instead of consuming all attempts and publishing a
                # low-quality deterministic fallback.
                if exc.code in {"ollama_unreachable", "ollama_timeout", "ollama_http_error"}:
                    jobs[event_id] = {
                        **jobs.get(event_id, {}),
                        "status": "waiting_for_provider",
                        "attempts": max(0, attempt - 1),
                        "error": str(exc),
                        "audit_record": audit_record,
                        "initial_draft": initial_draft,
                        "revised_draft": latest_draft if latest_draft != initial_draft else None,
                        "revision_history": revision_history,
                        "input_fingerprint": input_fingerprint,
                    }
                    write_checkpoint(checkpoint_path, checkpoint)
                    raise
                last_error = str(exc)
                jobs[event_id] = {
                    "status": "retryable_failure", "attempts": attempt, "error": last_error,
                    "audit_record": audit_record, "initial_draft": initial_draft,
                    "revised_draft": latest_draft if latest_draft != initial_draft else None,
                    "revision_history": revision_history,
                    "input_fingerprint": input_fingerprint,
                }
                write_checkpoint(checkpoint_path, checkpoint)
            except Exception as exc:
                last_error = str(exc)
                failed_revision = getattr(exc, "revision", None)
                if failed_revision:
                    revision_history.append({
                        "attempt": attempt,
                        "audit_errors": (latest_audit or {}).get("errors", []),
                        "patches": failed_revision.get("patches") or [],
                        "patch_validation_error": last_error,
                    })
                jobs[event_id] = {
                    "status": "retryable_failure", "attempts": attempt, "error": last_error,
                    "audit_record": audit_record, "initial_draft": initial_draft,
                    "revised_draft": latest_draft if latest_draft != initial_draft else None,
                    "revision_history": revision_history,
                    "input_fingerprint": input_fingerprint,
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
                "input_fingerprint": input_fingerprint,
            }
            write_checkpoint(checkpoint_path, checkpoint)
            continue
        drafts.append(completed_draft)
        records.append(audit_record)
    bundle = {
        "schema_version": "0.2",
        "record_type": "core_event_draft_bundle",
        "prompt_version": WRITER_PROMPT_VERSION,
        "generation_mode": "serial_background_local_14b",
        "drafts": drafts,
        "blocked": blocked,
        "demoted": demoted,
        "llm_calls": calls,
    }
    audit_bundle = {
        "schema_version": "0.1",
        "record_type": "core_event_editorial_audit",
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
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--event-id", action="append", dest="event_ids")
    args = parser.parse_args()
    os.environ["SPECTRA_MODEL"] = args.model
    os.environ["OLLAMA_NUM_CTX"] = str(args.num_ctx)
    os.environ["OLLAMA_NUM_PREDICT"] = str(args.num_predict)
    bundle, audit = build_bundle(
        json.loads(Path(args.verified).read_text(encoding="utf-8")),
        json.loads(Path(args.fact_selection).read_text(encoding="utf-8")),
        create_llm_client(), Path(args.checkpoint), args.max_attempts, args.event_ids,
    )
    Path(args.output).write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.audit_output).write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "core_events": len(bundle["drafts"]), "blocked": len(bundle["blocked"]),
        "demoted": len(bundle["demoted"]), "audit": args.audit_output,
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
