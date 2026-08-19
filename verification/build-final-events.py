#!/usr/bin/env python3
"""Convert a human-approved P1 review into verified events.

This is the hard human gate in the SPECTRA workflow. It never invents missing
claims or decisions: incomplete reviews fail before any issue is generated.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")[:36]


def validate_review(review: dict[str, Any], candidates: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if review.get("review_status") != "approved":
        errors.append("top-level review_status must be approved")
    if not review.get("verified_at") or not review.get("verified_by"):
        errors.append("top-level verified_at and verified_by are required")
    required = {
        item["candidate_id"]
        for item in candidates["selected_candidates"]
        if item.get("verification_priority") == "priority.p1"
    }
    eligible = {item["candidate_id"] for item in candidates["selected_candidates"]}
    records = review.get("records", [])
    actual = {item.get("candidate_id") for item in records}
    if len(actual) != len(records):
        errors.append("review contains duplicate candidate_id values")
    unknown = actual - eligible
    missing_required = required - actual
    if unknown:
        errors.append(f"review contains {len(unknown)} candidates outside the structured shortlist")
    if missing_required:
        errors.append(f"review is missing {len(missing_required)} required priority.p1 candidates")
    event_ids: list[str] = []
    for item in records:
        label = item.get("candidate_id", "unknown")
        if item.get("verification_status") != "verified_primary":
            errors.append(f"{label}: primary source is not verified")
        if item.get("decision") not in {"include", "watch", "exclude"}:
            errors.append(f"{label}: decision must be include, watch or exclude")
        if not item.get("decision_reason"):
            errors.append(f"{label}: decision_reason is required")
        if not item.get("limitation"):
            errors.append(f"{label}: limitation is required")
        claims = item.get("claims", [])
        if not claims:
            errors.append(f"{label}: at least one evidence claim is required")
        for index, claim in enumerate(claims, 1):
            if not all(claim.get(field) for field in ("text", "kind", "locator")):
                errors.append(f"{label}: claim {index} requires text, kind and locator")
        if item.get("decision") == "include":
            event = item.get("event") or {}
            required = {"event_id", "canonical_title", "event_type", "primary_route", "priority", "confidence", "entity_name", "tags"}
            missing = sorted(required - set(event))
            if missing:
                errors.append(f"{label}: included event missing {missing}")
            elif event["event_id"] in event_ids:
                errors.append(f"{label}: duplicate event_id {event['event_id']}")
            else:
                event_ids.append(event["event_id"])
    included = sum(item.get("decision") == "include" for item in records)
    if not 5 <= included <= 10:
        errors.append(f"included events must be 5-10, got {included}")
    return errors


def build_bundle(review: dict[str, Any], collector: dict[str, Any], candidate_run: dict[str, Any]) -> dict[str, Any]:
    errors = validate_review(review, candidate_run)
    if errors:
        raise ValueError("; ".join(errors))

    candidate_map = {item["candidate_id"]: item for item in candidate_run["selected_candidates"]}
    source_map = {item["source_id"]: item for item in collector["source_records"]}
    source_map.update({item["source_id"]: item for item in review.get("additional_source_records", [])})
    claims: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    records = json.loads(json.dumps(review["records"], ensure_ascii=False))

    for record in records:
        if record["source_id"] not in source_map:
            raise ValueError(f"{record['candidate_id']}: source_id is not present in collection or additions")
        claim_ids = []
        for index, claim in enumerate(record["claims"], 1):
            claim_id = f"clm_{slug(record['candidate_id'].replace('cand_', ''))}_{index:02d}"
            claim_ids.append(claim_id)
            claims.append({
                "schema_version": "0.2", "record_type": "evidence_claim", "claim_id": claim_id,
                "claim_text": claim["text"], "claim_kind": claim["kind"], "source_id": record["source_id"],
                "source_locator": claim["locator"], "quote_excerpt": claim.get("quote_excerpt"), "is_direct": True,
                "verification_status": "verified", "verified_by": review.get("verified_by", "human"),
                "verified_at": review["verified_at"],
            })
        record["claim_ids"] = claim_ids
        if record["decision"] != "include":
            continue
        meta = record["event"]
        candidate = candidate_map[record["candidate_id"]]
        source_ids = list(candidate["source_ids"])
        if record["source_id"] not in source_ids:
            source_ids.insert(0, record["source_id"])
        events.append({
            "schema_version": "0.2", "record_type": "intelligence_event",
            "event_id": meta["event_id"], "event_type": meta["event_type"],
            "canonical_title": meta["canonical_title"], "event_at": record["published_at"],
            "event_time_basis": "published_at",
            "primary_entity": {"entity_type": "research_or_product", "name": meta["entity_name"]},
            "entities": [{"entity_type": "research_or_product", "name": meta["entity_name"]}],
            "source_ids": source_ids, "primary_source_id": record["source_id"], "claim_ids": claim_ids,
            "fact_summary": "；".join(claim["text"].rstrip("。； ") for claim in record["claims"]) + "。",
            "limitations": record["limitation"], "primary_route": meta["primary_route"],
            "secondary_routes": meta.get("secondary_routes", []), "tags": meta["tags"],
            "track": meta.get("track", candidate.get("track", "track.emerging")),
            "evidence_level": "evidence.a", "confidence": meta["confidence"], "priority": meta["priority"],
            "independent_source_count": len(source_ids),
            "dedupe_key": f"{slug(meta['entity_name'])}|{meta['event_type']}|{record['published_at'][:10]}",
            "status": "approved", "needs_review_reasons": [],
        })

    ranked = sorted(events, key=lambda item: ({"priority.p0": 0, "priority.p1": 1, "priority.p2": 2}.get(item["priority"], 9), item["event_at"]))
    top_ids = [item["event_id"] for item in ranked[: min(5, len(ranked))]]
    thesis = review.get("editorial_selection", {}).get("weekly_thesis") or "本周视觉智能信号正在从单点能力更新，转向可验证、可控制、可进入工作流的系统变化。"
    return {
        "schema_version": "0.2", "record_type": "verified_event_bundle",
        "verified_at": review["verified_at"],
        "summary": {
            "p1_reviewed": len(records),
            "verified_primary": sum(item["verification_status"] == "verified_primary" for item in records),
            "included_events": len(events),
            "watchlist_events": sum(item["decision"] == "watch" for item in records),
            "excluded_events": sum(item["decision"] == "exclude" for item in records),
        },
        "editorial_selection": {
            "weekly_thesis": thesis,
            "top_event_ids": top_ids,
            "timeline_event_ids": [event["event_id"] for event in sorted(events, key=lambda item: item["event_at"])],
            "watch_candidate_ids": [item["candidate_id"] for item in records if item["decision"] == "watch"],
            "excluded_candidate_ids": [item["candidate_id"] for item in records if item["decision"] == "exclude"],
        },
        "additional_source_records": review.get("additional_source_records", []),
        "evidence_claims": claims,
        "intelligence_events": events,
        "verification_records": records,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--review", default="verification/p1-review.v0.1.json")
    parser.add_argument("--collection", default="collector/runs/first-live-run-v0.2.json")
    parser.add_argument("--candidates", default="processor/runs/first-structured-run-v0.1.json")
    parser.add_argument("--output", default="verification/runs/p1-verified-events-v0.2.json")
    args = parser.parse_args()
    review = json.loads(Path(args.review).read_text(encoding="utf-8"))
    collection = json.loads(Path(args.collection).read_text(encoding="utf-8"))
    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    bundle = build_bundle(review, collection, candidates)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(bundle["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
