#!/usr/bin/env python3
"""Expand P1 review suggestions to 8-12 source-backed atomic facts.

The output remains provisional. Every fact carries a human decision field and
cannot become a verified claim until the reviewer explicitly keeps or edits it.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectra_agent.llm_client import create_llm_client  # noqa: E402
from editorial.expand_facts import locate_quote, normalize, numeric_atoms  # noqa: E402

CHECKPOINT_SCHEMA_VERSION = "0.2"
PROMPT_VERSION = "p1_fact_expander.v0.2"


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "facts": {
            "type": "array", "minItems": 8, "maxItems": 12,
            "items": {
                "type": "object", "additionalProperties": False,
                "properties": {
                    "text": {"type": "string"},
                    "kind": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                },
                "required": ["text", "kind", "evidence_quote"],
            },
        }
    },
    "required": ["facts"],
}


INSTRUCTIONS = """你是P1事实扩展器，不负责写文章或作判断。
从完整来源正文中提取8—12条互不重复的原子事实，覆盖事件、组成、机制、数据、适用范围、限制与发布状态。
text使用准确中文；来源是公司、论文或媒体时必须保留“公司称／论文作者报告／据报道”等归因。
evidence_quote必须逐字复制来源正文中能够完整支持该事实的最短连续片段，不能翻译、改写或拼接不连续句子。
不得新增数字、因果、效果、行业趋势或来源没有表达的结论。每条只表达一个主要事实。
输出严格符合JSON Schema。"""

ATTRIBUTION_PATTERNS = (
    r"(?:公司|官方|团队|研究团队|论文|论文作者|作者|文章|Google Cloud).{0,6}(?:称|介绍|表示|报告|提出|宣布)",
    r"据(?:公司|官方|团队|论文|作者|文章|报道|来源|Google Cloud)",
)


def has_attribution(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in ATTRIBUTION_PATTERNS)


def attribution_prefix(source: dict[str, Any]) -> str:
    if source.get("source_type") == "paper_report":
        return "论文作者报告称，"
    publisher = source.get("publisher") or source.get("source_name")
    if source.get("source_type") in {"company_news", "official_announcement", "financial_report", "code_dataset"}:
        return f"{publisher}称，" if publisher else "官方来源称，"
    return f"据{publisher}报道，" if publisher else "据来源报道，"


def atomic_review(fact: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
    text = str(fact.get("text") or "").strip()
    quote = str(fact.get("evidence_quote") or "").strip()
    source_text = source.get("verified_text") or source.get("raw_text") or source.get("raw_excerpt") or ""
    start, end = locate_quote(source_text, quote)
    fact_numbers = {item.upper() for item in numeric_atoms(text)}
    source_numbers = {item.upper() for item in numeric_atoms(quote)}
    if not fact_numbers <= source_numbers:
        raise ValueError(f"unsupported numbers: {sorted(fact_numbers - source_numbers)}")
    if not has_attribution(text):
        text = attribution_prefix(source) + text.lstrip("，,")
    return {
        "claim": text,
        "kind": str(fact.get("kind") or "reported_fact"),
        "source_id": source["source_id"],
        "source_url": source.get("canonical_url") or source.get("source_url"),
        "locator": f"normalized_char:{start}-{end}",
        "evidence_text": normalize(quote),
        "score": 1.0,
        "evidence_alternatives": [],
        "support_status": "supported",
        "numeric_match": True,
        "attribution_preserved": True,
        "attribution_required": True,
        "risk_flags": [],
        "verification_status": "pending_human_review",
        "human_fact_decision": "pending",
        "human_fact_text": "",
        "human_fact_kind": "",
    }


def merge_reviews(existing: list[dict[str, Any]], expanded: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged = []
    seen = set()
    seen_evidence = set()
    safe_existing = [item for item in existing if item.get("support_status") == "supported" and not item.get("risk_flags")]
    risky_existing = [item for item in existing if item not in safe_existing]
    for item in [*expanded, *safe_existing, *risky_existing]:
        key = re.sub(r"\W+", "", str(item.get("claim") or "")).lower()
        evidence_key = normalize(str(item.get("evidence_text") or ""))
        if (
            not key or key in seen
            or (evidence_key and evidence_key in seen_evidence)
            or any(difflib.SequenceMatcher(None, key, old).ratio() >= 0.90 for old in seen)
        ):
            continue
        seen.add(key)
        if evidence_key:
            seen_evidence.add(evidence_key)
        row = dict(item)
        row.setdefault("human_fact_decision", "pending")
        row.setdefault("human_fact_text", "")
        row.setdefault("human_fact_kind", "")
        merged.append(row)
        if len(merged) == 12:
            break
    return merged


def safe_fact_count(items: list[dict[str, Any]]) -> int:
    return sum(
        item.get("support_status") == "supported"
        and item.get("numeric_match") is not False
        and not item.get("risk_flags")
        for item in items
    )


def expand_bundle(evidence: dict[str, Any], collection: dict[str, Any], client,
                  checkpoint_path: Path | None = None, max_attempts: int = 2) -> dict[str, Any]:
    sources = {item["source_id"]: item for item in collection["source_records"]}
    checkpoint = {
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "record_type": "p1_fact_expansion_checkpoint",
        "prompt_version": PROMPT_VERSION,
        "model": getattr(getattr(client, "settings", None), "model", None),
        "source_window": {
            "start": collection.get("window_start"),
            "end": collection.get("window_end"),
        },
        "records": {},
    }
    if checkpoint_path and checkpoint_path.exists():
        expected_model = getattr(getattr(client, "settings", None), "model", None)
        try:
            loaded = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError):
            loaded = {}
        saved_window = loaded.get("source_window") or {}
        current_start, current_end = collection.get("window_start"), collection.get("window_end")
        window_compatible = not (current_start and current_end) or bool(
            saved_window.get("start") and saved_window.get("end")
            and saved_window["end"] <= current_end
            and saved_window["end"] >= current_start
        )
        if (
            loaded.get("schema_version") == CHECKPOINT_SCHEMA_VERSION
            and loaded.get("record_type") == "p1_fact_expansion_checkpoint"
            and loaded.get("prompt_version") == PROMPT_VERSION
            and expected_model
            and loaded.get("model") == expected_model
            and window_compatible
        ):
            checkpoint = loaded
    checkpoint.update({
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "record_type": "p1_fact_expansion_checkpoint",
        "prompt_version": PROMPT_VERSION,
        "model": getattr(getattr(client, "settings", None), "model", None),
        "source_window": {"start": collection.get("window_start"), "end": collection.get("window_end")},
    })
    completed = checkpoint.setdefault("records", {})
    output = json.loads(json.dumps(evidence, ensure_ascii=False))
    for record in output.get("records", []):
        candidate_id = record["candidate_id"]
        existing = record.get("claim_reviews") or []
        source_id = existing[0].get("source_id") if existing else None
        source = sources.get(source_id)
        if not source:
            record["fact_expansion"] = {"status": "failed", "reason": "source_missing"}
            continue
        source_text = source.get("verified_text") or source.get("raw_text") or source.get("raw_excerpt") or ""
        input_fingerprint = hashlib.sha256(json.dumps({
            "candidate_id": candidate_id,
            "title": record.get("title"),
            "existing_claims": [item.get("claim") for item in existing],
            "source_id": source_id,
            "source_text": source_text,
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        cached = completed.get(candidate_id)
        if (
            cached
            and cached.get("status") in {"completed", "insufficient_source_facts"}
            and cached.get("input_fingerprint") == input_fingerprint
        ):
            record["claim_reviews"] = cached["claim_reviews"]
            record["fact_expansion"] = cached["fact_expansion"]
            continue
        try:
            accepted, rejected, calls = [], [], []
            merged = merge_reviews(existing, accepted)
            for attempt in range(1, max_attempts + 1):
                if safe_fact_count(merged) >= 8:
                    break
                payload, metadata = client.generate_json(
                    instructions=INSTRUCTIONS + (
                        "\n这是补充轮次。不要重复existing_claims，只提取正文中尚未覆盖的事实。"
                        if attempt > 1 else ""
                    ),
                    input_text=json.dumps({
                        "title": record.get("title"),
                        "existing_claims": [item.get("claim") for item in merged],
                        "source_text": source_text,
                    }, ensure_ascii=False),
                    schema_name="spectra_p1_fact_expansion",
                    schema=SCHEMA,
                )
                calls.append({"attempt": attempt, "model": metadata.get("model")})
                for index, fact in enumerate(payload.get("facts") or []):
                    try:
                        accepted.append(atomic_review(fact, source))
                    except ValueError as exc:
                        rejected.append({"attempt": attempt, "index": index, "reason": str(exc)})
                merged = merge_reviews(existing, accepted)
            supported_count = safe_fact_count(merged)
            status = "completed" if supported_count >= 8 else "insufficient_source_facts"
            record["claim_reviews"] = merged
            record["fact_expansion"] = {
                "status": status, "supported_facts": supported_count,
                "suggested_facts": len(merged), "rejected_facts": rejected,
                "calls": calls,
            }
            completed[candidate_id] = {
                "status": status, "claim_reviews": merged,
                "fact_expansion": record["fact_expansion"],
                "input_fingerprint": input_fingerprint,
            }
        except Exception as exc:
            record["claim_reviews"] = merge_reviews(existing, [])
            record["fact_expansion"] = {"status": "failed", "reason": str(exc)}
            completed[candidate_id] = {
                "status": "failed",
                "reason": str(exc),
                "input_fingerprint": input_fingerprint,
            }
        if checkpoint_path:
            checkpoint_path.write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = [len(record.get("claim_reviews") or []) for record in output.get("records", [])]
    output["fact_expansion_summary"] = {
        "completed": sum((record.get("fact_expansion") or {}).get("status") == "completed" for record in output.get("records", [])),
        "failed": sum((record.get("fact_expansion") or {}).get("status") == "failed" for record in output.get("records", [])),
        "insufficient_source_facts": sum((record.get("fact_expansion") or {}).get("status") == "insufficient_source_facts" for record in output.get("records", [])),
        "minimum_facts": min(counts) if counts else 0,
        "maximum_facts": max(counts) if counts else 0,
        "human_review_required": True,
    }
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--model", default="qwen3:8b")
    parser.add_argument("--num-ctx", type=int, default=8192)
    parser.add_argument("--max-attempts", type=int, default=2)
    parser.add_argument("--candidate-id", action="append", dest="candidate_ids")
    args = parser.parse_args()
    os.environ["SPECTRA_MODEL"] = args.model
    os.environ["OLLAMA_NUM_CTX"] = str(args.num_ctx)
    evidence = json.loads(Path(args.evidence).read_text(encoding="utf-8"))
    if args.candidate_ids:
        selected = set(args.candidate_ids)
        evidence["records"] = [item for item in evidence.get("records", []) if item.get("candidate_id") in selected]
    output = expand_bundle(
        evidence,
        json.loads(Path(args.collection).read_text(encoding="utf-8")),
        create_llm_client(), Path(args.checkpoint), args.max_attempts,
    )
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(output["fact_expansion_summary"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
