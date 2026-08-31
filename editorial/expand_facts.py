#!/usr/bin/env python3
"""Validate source-backed diagnostic fact expansion and build Writer input.

This tool never mutates verified-events.json. Expanded facts are explicitly
machine-evidence-checked and remain internal until a human approves them.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import html
import json
import re
from pathlib import Path
from typing import Any


ATTRIBUTION_MARKERS = ("据", "称", "介绍", "表示", "报告", "作者", "论文", "宣布")


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def numeric_atoms(value: str) -> list[str]:
    return re.findall(r"\d[\d,.]*(?:K|M|B|%|％|亿|万|千)?", value or "", flags=re.I)


def locate_quote(source_text: str, quote: str) -> tuple[int, int]:
    normalized_source = normalize(source_text)
    normalized_quote = normalize(quote)
    start = normalized_source.find(normalized_quote)
    if start < 0:
        raise ValueError(f"evidence quote not found: {normalized_quote[:90]}")
    return start, start + len(normalized_quote)


def claim_id(event_id: str, position: int, text: str) -> str:
    digest = hashlib.sha256(f"{event_id}|{position}|{text}".encode()).hexdigest()[:12]
    return f"clm_exp_{digest}_{position:02d}"


def build(candidates: dict[str, Any], collection: dict[str, Any],
          selection: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    sources = {item["source_id"]: item for item in collection["source_records"]}
    plans = {item["event_id"]: item for item in selection["selections"]}
    table_events = []
    expanded = copy.deepcopy(selection)
    expanded["schema_version"] = "0.1-diagnostic-expansion"
    expanded["record_type"] = "diagnostic_editorial_fact_selection_bundle"
    expanded["source_of_truth"] = "verified_events_plus_machine_evidence_checked_expansion"
    expanded["locked"] = False
    expanded["internal_only"] = True
    expanded["publication_status"] = "not_approved"
    expanded_plans = {item["event_id"]: item for item in expanded["selections"]}

    for event in candidates["events"]:
        event_id = event["event_id"]
        source_id = event["source_id"]
        if event_id not in plans or source_id not in sources:
            raise ValueError(f"unknown event/source pair: {event_id}/{source_id}")
        source = sources[source_id]
        source_text = source.get("verified_text") or source.get("raw_text") or source.get("raw_excerpt") or ""
        if not source_text:
            raise ValueError(f"{source_id}: source text missing")
        fact_units = []
        evidence_rows = []
        for position, fact in enumerate(event["facts"], 1):
            text = fact["text"].strip()
            quote = fact["evidence_quote"].strip()
            start, end = locate_quote(source_text, quote)
            source_numbers = {item.upper() for item in numeric_atoms(quote)}
            fact_numbers = {item.upper() for item in numeric_atoms(text)}
            if not fact_numbers <= source_numbers:
                raise ValueError(
                    f"{event_id} fact {position}: unsupported numbers {sorted(fact_numbers - source_numbers)}"
                )
            attribution_required = True
            if attribution_required and not any(marker in text for marker in ATTRIBUTION_MARKERS):
                raise ValueError(f"{event_id} fact {position}: source attribution missing")
            cid = claim_id(event_id, position, text)
            locator = f"normalized_char:{start}-{end}"
            row = {
                "fact_id": cid,
                "fact_text": text,
                "kind": fact["kind"],
                "source_id": source_id,
                "source_locator": locator,
                "evidence_quote": normalize(quote),
                "quote_exact_match": True,
                "numeric_check": "passed",
                "attribution_required": attribution_required,
                "attribution_check": "passed",
                "verification_status": "machine_evidence_checked_pending_human",
            }
            evidence_rows.append(row)
            fact_units.append({
                "claim_id": cid,
                "role": "core_fact" if position == 1 else "supporting_fact",
                "text": text,
                "kind": fact["kind"],
                "atomic_units": [text.rstrip("。")],
                "numeric_mentions": numeric_atoms(text),
                "source_id": source_id,
                "source_locator": locator,
                "evidence_context": normalize(quote),
                "attribution_required": attribution_required,
                "contains_number": bool(numeric_atoms(text)),
                "verification_status": "machine_evidence_checked_pending_human",
            })
        if not 8 <= len(fact_units) <= 12:
            raise ValueError(f"{event_id}: expected 8-12 expanded facts, got {len(fact_units)}")
        target_reader = expanded_plans[event_id]["reader_packet"]
        target_reader["fact_units"] = fact_units
        target_reader["writing_profile"] = {
            "mode": "long_form",
            "min_fact_units": 8,
            "min_fact_characters": 300,
            "min_characters": 600,
            "max_characters": 1200,
        }
        expanded_plans[event_id]["audit_packet"]["diagnostic_expansion"] = {
            "status": "pending_human_review",
            "formal_verified_events_mutated": False,
        }
        table_events.append({
            "event_id": event_id,
            "source_id": source_id,
            "source_url": source.get("canonical_url") or source.get("source_url"),
            "fact_count": len(evidence_rows),
            "facts": evidence_rows,
        })

    evidence_table = {
        "schema_version": "0.1",
        "record_type": "diagnostic_fact_evidence_table",
        "internal_only": True,
        "publication_status": "not_approved",
        "events": table_events,
    }
    return evidence_table, expanded


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--fact-selection", required=True)
    parser.add_argument("--evidence-output", required=True)
    parser.add_argument("--selection-output", required=True)
    args = parser.parse_args()
    evidence, expanded = build(
        json.loads(Path(args.candidates).read_text(encoding="utf-8")),
        json.loads(Path(args.collection).read_text(encoding="utf-8")),
        json.loads(Path(args.fact_selection).read_text(encoding="utf-8")),
    )
    Path(args.evidence_output).write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    Path(args.selection_output).write_text(json.dumps(expanded, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"events": len(evidence["events"]), "facts": sum(x["fact_count"] for x in evidence["events"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
