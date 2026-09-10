#!/usr/bin/env python3
"""Build the immutable editorial fact whitelist for verified SPECTRA events."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


def has_number(text: str) -> bool:
    return bool(re.search(r"\d|[%％$¥￥]|亿|万|千|百万|千万", text or ""))


def text_basis(source: dict[str, Any]) -> str:
    for field in ("verified_text", "raw_text"):
        if isinstance(source.get(field), str) and source[field].strip():
            return field
    return "insufficient"


def numeric_mentions(text: str) -> list[str]:
    """Expose verified numeric atoms without interpreting or converting them."""
    pattern = r"(?:[$¥￥])?\d[\d,.]*(?:\.\d+)?(?:%|％|倍|点|个|项|小时|分钟|秒|亿|万|千|百万|千万|GB|MB)?"
    return list(dict.fromkeys(re.findall(pattern, text or "", flags=re.IGNORECASE)))


def atomic_statements(text: str) -> list[str]:
    """Split an approved claim only at hard sentence boundaries.

    Soft comma splitting can detach a subject from its predicate and silently
    change meaning, so compound clauses remain together unless the human claim
    itself contains a sentence or semicolon boundary.
    """
    return [part.strip(" ，,。；;\n") for part in re.split(r"[。；;\n]+", text or "") if part.strip(" ，,。；;\n")]


AUDIT_ONLY_PATTERNS = (
    "该表述属于预测而非已验证结果",
    "该表述尚未验证",
    "不应上升为行业趋势",
    "仍需交叉验证",
    "保留归因和预测边界",
    "人工确认事实与证据可用",
    "人工确认来源正文与事实证据可用",
    "人工确认来源与事实证据可用",
    "人工核验原始来源与事实证据后保留",
    "人工核验后列入观察",
    "人工复核后不纳入本次正式内容",
)


def split_claim_layers(text: str) -> tuple[list[str], list[str]]:
    """Separate publishable factual atoms from internal audit commentary."""
    reader_units: list[str] = []
    audit_units: list[str] = []
    for unit in atomic_statements(text):
        target = audit_units if any(pattern in unit for pattern in AUDIT_ONLY_PATTERNS) else reader_units
        target.append(unit)
    return reader_units, audit_units


def split_judgment_layers(text: str) -> tuple[str, list[str]]:
    """Keep the useful editorial judgment while moving process notes to audit."""
    clauses = [part.strip(" ，,。；;") for part in re.split(r"[。；;]|但(?=所有|该|相关)", text or "") if part.strip(" ，,。；;")]
    reader: list[str] = []
    audit: list[str] = []
    for clause in clauses:
        if any(pattern in clause for pattern in AUDIT_ONLY_PATTERNS) or re.search(r"待核验|已核验|证据边界|审计", clause):
            audit.append(clause)
        else:
            reader.append(clause)
    return "。".join(reader) + ("。" if reader else ""), audit


def compact_evidence(value: str | None, limit: int = 1200) -> str | None:
    text = re.sub(r"\s+", " ", value or "").strip()
    if not text:
        return None
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def editorial_facet(kind: str) -> str:
    normalized = kind.removeprefix("reported_").removeprefix("attributed_")
    if normalized in {"metric", "result", "financial_metric", "market_data", "company_data"}:
        return "results_and_metrics"
    if normalized in {"method", "product_capability", "mechanism", "capability", "strategy"}:
        return "mechanism_and_capability"
    if normalized in {"release", "product_positioning", "announcement", "forecast"}:
        return "event_and_scope"
    return "supporting_context"


def build_fact_selection(verified: dict[str, Any], review: dict[str, Any],
                         collection: dict[str, Any]) -> dict[str, Any]:
    claims = {item["claim_id"]: item for item in verified["evidence_claims"]}
    review_by_event = {
        item.get("event", {}).get("event_id"): item
        for item in review.get("records", [])
        if item.get("decision") == "include"
    }
    sources = {item["source_id"]: item for item in collection["source_records"]}
    selections = []

    for event in verified["intelligence_events"]:
        event_id = event["event_id"]
        if event_id not in review_by_event:
            raise ValueError(f"{event_id}: no included human review record")
        item = review_by_event[event_id]
        source = sources.get(event["primary_source_id"])
        if not source:
            raise ValueError(f"{event_id}: primary source is missing from collection")
        evidence_by_claim = {
            evidence.get("claim"): evidence for evidence in item.get("suggested_evidence", [])
        }
        selected_facts = []
        embedded_audit_notes = []
        for position, claim_id in enumerate(event["claim_ids"]):
            if claim_id not in claims:
                raise ValueError(f"{event_id}: unknown verified claim {claim_id}")
            claim = claims[claim_id]
            evidence = evidence_by_claim.get(claim["claim_text"], {})
            statement = claim["claim_text"]
            reader_units, audit_units = split_claim_layers(statement)
            if not reader_units:
                raise ValueError(f"{event_id}: claim {claim_id} contains audit commentary but no publishable fact")
            reader_statement = "。".join(reader_units) + "。"
            embedded_audit_notes.extend(
                {"claim_id": claim_id, "text": unit} for unit in audit_units
            )
            selected_facts.append({
                "claim_id": claim_id,
                "role": "core_fact" if position == 0 else "supporting_fact",
                "text": reader_statement,
                "kind": claim["claim_kind"],
                "editorial_facet": editorial_facet(claim["claim_kind"]),
                "atomic_units": reader_units,
                "numeric_mentions": numeric_mentions(reader_statement),
                "source_id": claim["source_id"],
                "source_locator": claim["source_locator"],
                "evidence_context": compact_evidence(evidence.get("evidence_text") or claim.get("quote_excerpt")),
                "attribution_required": bool(evidence.get("attribution_required")) or bool(
                    re.search(r"称|据报道|援引|转述|作者认为|按照.+判断|预计|可能", reader_statement)
                ),
                "contains_number": has_number(reader_statement),
            })

        prohibited = [
            "不得加入 selected_facts 之外的新事实、数字、实体、效果或因果关系。",
            "不得弱化“称、计划、预计、可能、据报道、作者认为”等来源限定。",
            "不得把事实选择阶段提供的编辑判断改写成已经验证的事实。",
        ]
        if event.get("independent_source_count", 1) < 2:
            prohibited.append("该事件缺少两个以上独立来源，不得上升为行业趋势或普遍结论。")
        if any(fact["contains_number"] for fact in selected_facts):
            prohibited.append("不得改写、换算或补充 selected_facts 未明确提供的数字与单位。")

        risk_reasons = []
        if event.get("independent_source_count", 1) < 2:
            risk_reasons.append("single_source")
        if any(fact["attribution_required"] for fact in selected_facts):
            risk_reasons.append("attribution_sensitive")
        if any(fact["contains_number"] for fact in selected_facts):
            risk_reasons.append("numeric_claim")
        basis = text_basis(source)
        if basis == "insufficient":
            risk_reasons.append("full_text_missing")

        reader_judgment, judgment_audit_notes = split_judgment_layers(item["decision_reason"])
        # Candidate disposition and reader-facing editorial judgment are separate
        # layers. A reviewed judgment may be supplied explicitly after the facts
        # are locked; generic review-process wording must never leak into copy.
        explicit_reader_judgment = str(item.get("reader_judgment") or "").strip()
        if explicit_reader_judgment:
            reader_judgment = explicit_reader_judgment
        embedded_audit_notes.extend(
            {"claim_id": None, "text": note, "origin": "decision_reason"}
            for note in judgment_audit_notes
        )
        reader_packet = {
            "event_id": event_id,
            "canonical_title": event["canonical_title"],
            "event_at": event["event_at"],
            "primary_route": event["primary_route"],
            "source": {
                "source_id": event["primary_source_id"],
                "name": source.get("source_name") or source.get("publisher"),
                "url": source.get("canonical_url") or source.get("source_url"),
            },
            "fact_units": selected_facts,
            "editorial_facets": {
                facet: [fact["claim_id"] for fact in selected_facts if fact["editorial_facet"] == facet]
                for facet in ("event_and_scope", "mechanism_and_capability", "results_and_metrics", "supporting_context")
            },
            "allowed_judgment": reader_judgment,
        }
        if len(selected_facts) >= 8:
            reader_packet["writing_profile"] = {
                "mode": "long_form",
                "min_fact_units": 8,
                "min_fact_characters": 300,
                "min_characters": 0,
                "preferred_characters": 600,
                "max_characters": 1000,
            }
        audit_packet = {
            "limitations": item["limitation"],
            "embedded_claim_audit_notes": embedded_audit_notes,
            "prohibited_extrapolations": prohibited,
            "publication_risk": {
                "level": "high" if "full_text_missing" in risk_reasons else ("medium" if risk_reasons else "low"),
                "reasons": risk_reasons,
            },
        }
        selections.append({
            "event_id": event_id,
            "priority": event["priority"],
            "confidence": event["confidence"],
            "source_context": {**reader_packet["source"], "text_basis": basis},
            "reader_packet": reader_packet,
            "audit_packet": audit_packet,
        })

    return {
        "schema_version": "0.1",
        "record_type": "editorial_fact_selection_bundle",
        "source_of_truth": "verified_events",
        "locked": True,
        "verified_at": verified["verified_at"],
        "source_window": {
            "start": collection.get("window_start"),
            "end": collection.get("window_end"),
        },
        "selections": selections,
    }


def validate_fact_selection(bundle: dict[str, Any], verified: dict[str, Any]) -> None:
    if bundle.get("source_of_truth") != "verified_events" or bundle.get("locked") is not True:
        raise ValueError("fact selection must be locked to verified_events")
    expected = {event["event_id"]: set(event["claim_ids"]) for event in verified["intelligence_events"]}
    actual: dict[str, set[str]] = {}
    for item in bundle.get("selections", []):
        event_id = item.get("event_id")
        if event_id in actual:
            raise ValueError(f"duplicate fact selection: {event_id}")
        reader = item.get("reader_packet") or {}
        audit = item.get("audit_packet") or {}
        actual[event_id] = {fact["claim_id"] for fact in reader.get("fact_units", [])}
        if "allowed_judgment" not in reader or not audit.get("limitations"):
            raise ValueError(f"{event_id}: judgment field and limitations are required")
    if set(actual) != set(expected):
        raise ValueError("fact selection must cover every verified event exactly once")
    for event_id, claim_ids in actual.items():
        if not claim_ids or not claim_ids <= expected[event_id]:
            raise ValueError(f"{event_id}: fact selection contains missing or unknown claims")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    verified = json.loads(Path(args.verified).read_text(encoding="utf-8"))
    bundle = build_fact_selection(
        verified,
        json.loads(Path(args.review).read_text(encoding="utf-8")),
        json.loads(Path(args.collection).read_text(encoding="utf-8")),
    )
    validate_fact_selection(bundle, verified)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"fact_selections": len(bundle["selections"]), "locked": True}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
