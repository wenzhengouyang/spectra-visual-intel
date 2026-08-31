#!/usr/bin/env python3
"""Minimal local-14B long-form writer with post-generation audit."""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectra_agent.llm_client import create_llm_client  # noqa: E402
from editorial.editorial_writer import (  # noqa: E402
    ATTRIBUTION_MARKERS, meaningful_tokens, normalized_numbers,
)

LONG_FORM_ATTRIBUTION_MARKERS = ATTRIBUTION_MARKERS + ("论文", "研究团队")


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "headline": {"type": "string"},
        "dek": {"type": "string"},
        "paragraphs": {
            "type": "array", "minItems": 4, "maxItems": 6,
            "items": {"type": "string"},
        },
        "judgment": {"type": "string"},
    },
    "required": ["headline", "dek", "paragraphs", "judgment"],
}


INSTRUCTIONS = """你是SPECTRA中文情报编辑。请把输入的锁定事实写成一篇原创中文长篇深读。
只能使用fact_units中的事实；evidence_context只帮助理解对应事实，不能引入新事实。
正文写4—6个事实自然段，合计650—900个中文字符，不设段落小标题。每段围绕一组相近事实展开。每条事实最多出现一次，不得重复标题、摘要或前文，不得把allowed_judgment写入正文段落。
所有“公司称、论文作者报告、据其所知、将会”等归因与限定必须保留，不得把来源主张写成独立证实的结果。
不得新增数字、实体、效果、因果或行业趋势。judgment只能改写allowed_judgment，控制在一到两句。
不要输出事实编号、审计语言、What/Why/How。输出严格符合JSON Schema。"""


def audit_article(result: dict, facts: list[dict], allowed_judgment: str = "") -> dict:
    all_fact_text = " ".join(f["text"] for f in facts)
    allowed_numbers = normalized_numbers(all_fact_text)
    allowed_tokens = meaningful_tokens(all_fact_text)
    errors = []
    mappings = []
    for index, paragraph in enumerate(result.get("paragraphs") or []):
        extra = normalized_numbers(paragraph) - allowed_numbers
        if extra:
            errors.append(f"paragraph[{index}] unsupported numbers: {sorted(extra)}")
        tokens = meaningful_tokens(paragraph)
        if len(tokens & allowed_tokens) / max(1, len(tokens)) < 0.18:
            errors.append(f"paragraph[{index}] low fact overlap")
        ranked = []
        for fact in facts:
            fact_tokens = meaningful_tokens(fact["text"])
            score = len(tokens & fact_tokens) / max(1, len(tokens))
            if score >= 0.08:
                ranked.append((score, fact["claim_id"]))
        ranked.sort(reverse=True)
        mapped = [claim_id for _, claim_id in ranked[:4]]
        mappings.append({"paragraph_index": index, "claim_ids": mapped})
        if not mapped:
            errors.append(f"paragraph[{index}] no claim mapping")
        mapped_facts = [fact for fact in facts if fact["claim_id"] in mapped]
        if any(fact.get("attribution_required") for fact in mapped_facts) and not any(
            marker in paragraph for marker in LONG_FORM_ATTRIBUTION_MARKERS
        ):
            errors.append(f"paragraph[{index}] source attribution missing")
        for earlier_index, earlier in enumerate((result.get("paragraphs") or [])[:index]):
            ratio = difflib.SequenceMatcher(None, paragraph, earlier).ratio()
            if ratio >= 0.62:
                errors.append(f"paragraph[{index}] repeats paragraph[{earlier_index}]: {ratio:.2f}")
    article = "\n".join(result.get("paragraphs") or [])
    judgment = str(result.get("judgment") or "").strip()
    for index, paragraph in enumerate(result.get("paragraphs") or []):
        if judgment and judgment in paragraph:
            errors.append(f"paragraph[{index}] repeats editorial judgment")
    if not 650 <= len(article) <= 1100:
        errors.append(f"article length outside 650-1100: {len(article)}")
    for field in ("headline", "dek"):
        value = str(result.get(field) or "")
        extra = normalized_numbers(value) - allowed_numbers
        if extra:
            errors.append(f"{field} unsupported numbers: {sorted(extra)}")
        tokens = meaningful_tokens(value)
        if not tokens or len(tokens & allowed_tokens) / max(1, len(tokens)) < 0.18:
            errors.append(f"{field} low fact overlap")
    judgment_value = str(result.get("judgment") or "")
    if allowed_judgment:
        judgment_tokens = meaningful_tokens(judgment_value)
        allowed_judgment_tokens = meaningful_tokens(allowed_judgment)
        if (
            not judgment_tokens
            or len(judgment_tokens & allowed_judgment_tokens) / max(1, len(judgment_tokens)) < 0.18
        ):
            errors.append("judgment exceeds allowed_judgment")
        extra = normalized_numbers(judgment_value) - normalized_numbers(allowed_judgment)
        if extra:
            errors.append(f"judgment unsupported numbers: {sorted(extra)}")
    return {
        "status": "passed" if not errors else "needs_review",
        "article_characters": len(article),
        "paragraph_count": len(result.get("paragraphs") or []),
        "paragraph_claim_mapping": mappings,
        "errors": errors,
    }


def generate_long_story(reader: dict, event_id: str, client=None) -> tuple[dict, dict]:
    payload = {
        "event_id": event_id,
        "fact_units": [
            {"fact_id": f["claim_id"], "text": f["text"], "attribution_required": f["attribution_required"]}
            for f in reader["fact_units"]
        ],
        "evidence_context": [
            {"fact_id": f["claim_id"], "text": f["evidence_context"]}
            for f in reader["fact_units"] if f.get("evidence_context")
        ],
        "allowed_judgment": reader["allowed_judgment"],
    }
    return (client or create_llm_client()).generate_json(
        instructions=INSTRUCTIONS,
        input_text=json.dumps(payload, ensure_ascii=False),
        schema_name="spectra_minimal_long_story",
        schema=SCHEMA,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fact-selection", required=True)
    parser.add_argument("--event-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--model", default="qwen3:14b")
    parser.add_argument("--num-ctx", type=int, default=8192)
    args = parser.parse_args()
    os.environ["SPECTRA_MODEL"] = args.model
    os.environ["OLLAMA_NUM_CTX"] = str(args.num_ctx)
    plan_bundle = json.loads(Path(args.fact_selection).read_text(encoding="utf-8"))
    plan = next(item for item in plan_bundle["selections"] if item["event_id"] == args.event_id)
    reader = plan["reader_packet"]
    result, metadata = generate_long_story(reader, args.event_id)
    audit = audit_article(result, reader["fact_units"], reader.get("allowed_judgment", ""))
    output = {
        "schema_version": "0.1",
        "record_type": "diagnostic_long_story",
        "internal_only": True,
        "publication_status": "not_approved",
        "event_id": args.event_id,
        "draft": result,
        "audit": audit,
        "llm_call": metadata,
    }
    Path(args.output).write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"event_id": args.event_id, **audit}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
