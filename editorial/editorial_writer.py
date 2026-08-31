#!/usr/bin/env python3
"""Write reader-facing Chinese deep stories from verified evidence only.

This node runs after the verification harness and human review. It cannot add
events or claims; it only turns approved evidence into original editorial copy.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectra_agent.llm_client import create_llm_client  # noqa: E402


EDITORIAL_WRITER_INSTRUCTIONS = """你是 SPECTRA 的中文情报编辑，读者是 AI 产品策略、模型与内容行业从业者。
输入中的 reader_packet 已经完成事实确认，你的任务是把它组织成自然、专业、清晰的深读文章。

写作目标：把锁定的 fact_selection 整理成原创、专业、清晰的中文深读文章。信息收集是第一目标，判断是第二目标。

内容要求：
1. 正文事实只能来自 reader_packet.fact_units；evidence_context 只用来理解该条事实的上下文，不能提供新的可写事实。
2. 每个事实段在 JSON 的 claim_ids 字段列出支持它的编号，但正文 text 中绝对不得显示 claim ID。数字、名称、时间和单位保持 fact_units 中的原样。
3. claim_support 分别列出支持标题、摘要、一句话结论和判断的 claim_ids；不得用无关 claim 充数。
4. fact_unit 带有来源限定时，在中文中自然保留相同归因和语气。
5. judgment 只围绕 reader_packet.allowed_judgment 写一段策略意义，与事实段明确分开。

版权与改写边界：
1. 必须用自己的中文句式重新组织，不得整篇复制、逐段翻译或连续复现来源表达。
2. 除产品名、技术术语和必要短语外，避免连续照搬原文。不要输出大段引文。
3. 不得为了显得完整而把 evidence_context 中未写入 fact_units.text 的细节当作事实。

呈现方式：
1. headline 准确、自然，优先使用正式中文名称；首次出现时可在括号中保留英文名。
2. dek 用一到两句交代最重要的新事实，不重复标题。
3. deep_story 必须写成博客式文章摘要，而不是证据清单：通常为3—5个自然段，每段包含2—4个相互关联的完整句子。若 reader_packet.writing_profile.mode 为 long_form，事实正文写到其 min_characters—max_characters 指定范围；否则通常为260—600个中文字符。事实包不足时宁可更短，绝不通过补背景、效果、目的或行业影响凑字数。
4. 第一段只用已核验事实交代事件主体、发生了什么以及它要解决的具体问题；中间段按产品构成、运行机制、数据结果或适用范围组织；最后一段补充发布状态、限制或后续安排。不得把每个 fact_unit 单独写成一段。
5. 每段围绕一个信息重点，用句号形成正常阅读节奏。避免用分号串联大量事实，不使用项目符号、编号、问答体或 What、Why、How 等小标题。
6. 同一来源在一个段落中通常只归因一次。不得让连续句子或连续段落都以“据某某报道”“某某称”“文章指出”开头；后续句子应在不丢失归因边界的前提下自然承接。
7. factual_paragraphs 只写事实和有来源归因的内容；judgment 只写一段最精炼的产品或策略意义。
8. 摘要、标题和正文不得相互机械重复。语言客观、具体、克制，不使用宣传式语言。
9. 不得在 fact_unit 后追加“旨在、确保、有助于、推动、提升效率、提供基础”等解释性尾句，除非这些表述已在同一 fact_unit.text 中明确出现。

每次输入只包含一个正式事件。draft 字段必须返回一篇与该 event_id 对应的 deep_story；不得返回其他事件。
输出必须严格符合 JSON Schema。"""


REVISION_INSTRUCTIONS = """

这是一次且仅一次的定向修订。输入中的 revision.validation_error 是刚性校验失败原因。
1. 保留 failed_draft 中未被错误指向的字段和段落，只修改与该错误直接相关的位置。
2. 新增数字错误：删除该数字或改回 fact_units.text 明确提供的数字，不得使用 failed_draft 中的错误细节。
3. 归因丢失错误：在对应事实段自然恢复“文章称”“据团队介绍”“文章援引”“王兴兴认为”等与 fact_unit 一致的归因。
4. 只返回 revision.patches，绝对不得返回或重写完整 draft。
5. target 必须是校验错误指向的字段；修改段落时填 factual_paragraph 和对应 paragraph_index，其他字段不能出现。
6. 若错误为 sentence lacks claim support 或 unsupported inference，删除无支持句子，只保留对应 fact_unit 明确提供的内容；不得换一种说法继续保留该推断。"""


WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "draft": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "event_id": {"type": "string"},
                "headline": {"type": "string"},
                "dek": {"type": "string"},
                "one_line_takeaway": {"type": "string"},
                "factual_paragraphs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string"},
                            "claim_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["text", "claim_ids"],
                    },
                },
                "judgment": {"type": "string"},
                "watch_next": {"type": "array", "items": {"type": "string"}},
                "claim_support": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "headline": {"type": "array", "items": {"type": "string"}},
                        "dek": {"type": "array", "items": {"type": "string"}},
                        "one_line_takeaway": {"type": "array", "items": {"type": "string"}},
                        "judgment": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["headline", "dek", "one_line_takeaway", "judgment"],
                },
            },
            "required": [
                "event_id", "headline", "dek", "one_line_takeaway",
                "factual_paragraphs", "judgment", "watch_next", "claim_support",
            ],
        }
    },
    "required": ["draft"],
}


# Long-form generation keeps only fields that require editorial judgment.
# Optional display fields are added deterministically after generation, which
# substantially reduces constrained-decoding cost on local 14B models.
LONG_FORM_WRITER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "draft": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "event_id": {"type": "string"},
                "headline": {"type": "string"},
                "dek": {"type": "string"},
                "one_line_takeaway": {"type": "string"},
                "factual_paragraphs": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "text": {"type": "string"},
                            "claim_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["text", "claim_ids"],
                    },
                },
                "judgment": {"type": "string"},
                "headline_claim_ids": {"type": "array", "items": {"type": "string"}},
                "dek_claim_ids": {"type": "array", "items": {"type": "string"}},
                "takeaway_claim_ids": {"type": "array", "items": {"type": "string"}},
                "judgment_claim_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "event_id", "headline", "dek", "one_line_takeaway",
                "factual_paragraphs", "judgment", "headline_claim_ids",
                "dek_claim_ids", "takeaway_claim_ids", "judgment_claim_ids",
            ],
        }
    },
    "required": ["draft"],
}


def normalize_long_form_draft(draft: dict[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(draft)
    normalized["watch_next"] = []
    normalized["claim_support"] = {
        "headline": normalized.pop("headline_claim_ids"),
        "dek": normalized.pop("dek_claim_ids"),
        "one_line_takeaway": normalized.pop("takeaway_claim_ids"),
        "judgment": normalized.pop("judgment_claim_ids"),
    }
    return normalized


REVISION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "revision": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "event_id": {"type": "string"},
                "patches": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "target": {
                                "type": "string",
                                "enum": ["headline", "dek", "one_line_takeaway", "judgment", "factual_paragraph"],
                            },
                            "paragraph_index": {"type": "integer"},
                            "text": {"type": "string"},
                            "claim_ids": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["target", "paragraph_index", "text", "claim_ids"],
                    },
                },
            },
            "required": ["event_id", "patches"],
        },
    },
    "required": ["revision"],
}


def source_text(source: dict[str, Any]) -> tuple[str, str]:
    for field in ("verified_text", "raw_text", "raw_excerpt"):
        value = source.get(field)
        if isinstance(value, str) and value.strip():
            return value.strip(), field
    return "", "missing"


def input_for_event(event: dict[str, Any], fact_plan: dict[str, Any],
                    collection: dict[str, Any], article_type: str) -> str:
    reader = fact_plan["reader_packet"]
    fact_units = []
    evidence_context = []
    for fact in reader.get("fact_units", []):
        fact_units.append({
            key: fact[key] for key in (
                "claim_id", "role", "text", "kind", "atomic_units",
                "numeric_mentions", "attribution_required",
            ) if key in fact
        })
        if fact.get("evidence_context"):
            evidence_context.append({
                "claim_id": fact["claim_id"],
                "text": fact["evidence_context"],
            })
    # This is the complete model-visible fact envelope. Event date, canonical
    # title, category, source metadata, audit packet and full text stay outside.
    payload = {
        "event_id": event["event_id"],
        "fact_units": fact_units,
        "evidence_context": evidence_context,
        "allowed_judgment": reader.get("allowed_judgment", ""),
        "writing_profile": reader.get("writing_profile", {}),
    }
    return json.dumps(payload, ensure_ascii=False)


def deep_story_readiness(fact_plan: dict[str, Any]) -> dict[str, Any]:
    """Decide whether the locked fact envelope can sustain a real deep story."""
    reader = fact_plan.get("reader_packet") or {}
    facts = reader.get("fact_units") or []
    claim_count = len({fact.get("claim_id") for fact in facts if fact.get("claim_id")})
    atomic_count = sum(len(fact.get("atomic_units") or [fact.get("text")]) for fact in facts)
    fact_characters = sum(len(str(fact.get("text") or "")) for fact in facts)
    evidence_count = sum(bool(str(fact.get("evidence_context") or "").strip()) for fact in facts)
    profile = reader.get("writing_profile") or {}
    long_form = profile.get("mode") == "long_form"
    minimum_claims = int(profile.get("min_fact_units", 8) if long_form else 3)
    minimum_characters = int(profile.get("min_fact_characters", 300) if long_form else 150)
    reasons = []
    if claim_count < minimum_claims:
        reasons.append(f"fewer_than_{minimum_claims}_distinct_verified_claims")
    if atomic_count < minimum_claims:
        reasons.append(f"fewer_than_{minimum_claims}_atomic_fact_units")
    if fact_characters < minimum_characters:
        reasons.append("verified_fact_envelope_too_short")
    if evidence_count < 2:
        reasons.append("insufficient_evidence_context")
    if not str(reader.get("allowed_judgment") or "").strip():
        reasons.append("allowed_judgment_missing")
    return {
        "status": "ready" if not reasons else "quick_read",
        "ready": not reasons,
        "reasons": reasons,
        "metrics": {
            "distinct_claims": claim_count,
            "atomic_fact_units": atomic_count,
            "fact_characters": fact_characters,
            "evidence_contexts": evidence_count,
        },
    }


def revision_input(initial_input: str, failed_draft: dict[str, Any], error: str) -> str:
    return json.dumps({
        "locked_writer_input": json.loads(initial_input),
        "revision": {
            "validation_error": error,
            "failed_draft": failed_draft,
            "scope": "modify_only_the_failed_location",
        },
    }, ensure_ascii=False)


def allowed_patch_scope(error: str, draft: dict[str, Any]) -> set[tuple[str, int]]:
    allowed = {
        ("factual_paragraph", int(match.group(1)))
        for match in re.finditer(r"factual_paragraph\[(\d+)\]", error)
    }
    for field in ("headline", "dek", "one_line_takeaway", "judgment"):
        if re.search(rf"(?:^|: ){re.escape(field)}:", error) or error.startswith(f"{field}:"):
            allowed.add((field, -1))
    if any(marker in error for marker in ("deep story is too short", "overuses semicolons", "too similar to source")):
        allowed.update(
            ("factual_paragraph", index)
            for index, _ in enumerate(draft.get("factual_paragraphs") or [])
        )
    return allowed


def apply_revision_patches(draft: dict[str, Any], revision: dict[str, Any], error: str,
                           event_id: str) -> dict[str, Any]:
    if revision.get("event_id") != event_id:
        raise ValueError(f"revision returned wrong event_id for {event_id}")
    allowed = allowed_patch_scope(error, draft)
    if not allowed:
        raise ValueError(f"{event_id}: validation error is not safely patchable")
    revised = copy.deepcopy(draft)
    seen: set[tuple[str, int]] = set()
    for patch in revision.get("patches") or []:
        target = patch.get("target")
        index = patch.get("paragraph_index", -1)
        key = (target, index)
        if key not in allowed:
            raise ValueError(f"{event_id}: revision attempted out-of-scope patch {target}[{index}]")
        if key in seen:
            raise ValueError(f"{event_id}: duplicate revision patch {target}[{index}]")
        seen.add(key)
        if target == "factual_paragraph":
            paragraphs = revised.get("factual_paragraphs") or []
            if not 0 <= index < len(paragraphs):
                raise ValueError(f"{event_id}: revision paragraph index out of range")
            paragraphs[index] = {"text": patch["text"], "claim_ids": patch["claim_ids"]}
        else:
            if index != -1:
                raise ValueError(f"{event_id}: non-paragraph patch must use paragraph_index -1")
            revised[target] = patch["text"]
            revised.setdefault("claim_support", {})[target] = patch["claim_ids"]
    return revised


ATTRIBUTION_MARKERS = ("据", "称", "援引", "转述", "文章", "报道", "介绍", "表示", "认为", "判断")
INFERENCE_MARKERS = (
    "推动", "促进", "重塑", "意味着", "表明", "体现", "凸显", "引领", "加速",
    "提供了", "硬件基础", "技术路径", "工程化方向", "规模化", "行业趋势",
    "广泛认为", "高效且实用", "实际应用价值",
)


def sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[!?！？。]+", text or "") if part.strip()]


def normalized_numbers(text: str) -> set[str]:
    return {item.replace(",", "") for item in re.findall(r"\d[\d,.]*(?:%|％|亿|万|千|百万|千万)?", text or "")}


def meaningful_tokens(text: str) -> set[str]:
    compact = re.sub(r"[^A-Za-z0-9一-鿿]+", "", text or "").lower()
    chinese = "".join(re.findall(r"[一-鿿]", compact))
    tokens = {chinese[i:i + 2] for i in range(max(0, len(chinese) - 1))}
    tokens.update(token.lower() for token in re.findall(r"[A-Za-z][A-Za-z0-9+.×-]*", text or ""))
    return tokens


def validate_supported_text(field: str, text: str, claim_ids: list[str], facts: dict[str, dict[str, Any]],
                            allowed_judgment: str = "") -> None:
    if not claim_ids or not set(claim_ids) <= set(facts):
        raise ValueError(f"{field}: missing or unknown claim support")
    support_text = " ".join(facts[claim_id]["text"] for claim_id in claim_ids)
    if field == "judgment":
        support_text += " " + allowed_judgment
    support_tokens = meaningful_tokens(support_text)
    support_numbers = normalized_numbers(support_text)
    for sentence in sentences(text):
        extra_numbers = normalized_numbers(sentence) - support_numbers
        if extra_numbers:
            raise ValueError(f"{field}: unsupported numeric claim {sorted(extra_numbers)}")
        sentence_tokens = meaningful_tokens(sentence)
        overlap = len(sentence_tokens & support_tokens) / max(1, len(sentence_tokens))
        if overlap < 0.18:
            raise ValueError(f"{field}: sentence lacks claim support: {sentence}")
        for marker in INFERENCE_MARKERS:
            if marker in sentence and marker not in support_text:
                raise ValueError(f"{field}: unsupported inference '{marker}'")


def validate_draft(draft: dict[str, Any], event: dict[str, Any], original: str,
                   fact_plan: Optional[dict[str, Any]] = None) -> None:
    errors: list[str] = []
    if draft.get("event_id") != event["event_id"]:
        raise ValueError(f"writer returned wrong event_id for {event['event_id']}")
    reader = (fact_plan or {}).get("reader_packet") or {}
    facts = {fact["claim_id"]: fact for fact in reader.get("fact_units", [])}
    # Diagnostic fact expansion may add machine-evidence-checked claims without
    # mutating the locked formal event. The model and validator are constrained
    # to the fact plan actually supplied for this draft.
    allowed = set(facts) if facts else set(event["claim_ids"])
    paragraphs = draft.get("factual_paragraphs") or []
    if not paragraphs:
        raise ValueError(f"{event['event_id']}: no factual paragraphs")
    profile = reader.get("writing_profile") or {}
    max_paragraphs = 8 if profile.get("mode") == "long_form" else 6
    if not 3 <= len(paragraphs) <= max_paragraphs:
        errors.append(f"{event['event_id']}: deep story requires 3-{max_paragraphs} factual paragraphs")
    for paragraph_index, paragraph in enumerate(paragraphs):
        used = set(paragraph.get("claim_ids") or [])
        if not used or not used <= allowed:
            errors.append(f"{event['event_id']}: factual_paragraph[{paragraph_index}] has missing or unknown claim IDs")
            continue
        if facts:
            try:
                validate_supported_text(
                    f"factual_paragraph[{paragraph_index}]", paragraph.get("text", ""), list(used), facts
                )
            except ValueError as exc:
                errors.append(str(exc))
            if any(facts[claim_id].get("attribution_required") for claim_id in used):
                if not any(marker in paragraph.get("text", "") for marker in ATTRIBUTION_MARKERS):
                    errors.append(
                        f"{event['event_id']}: factual_paragraph[{paragraph_index}] "
                        "attribution-required claim lost source attribution"
                    )
    article = "\n".join(item["text"] for item in paragraphs)
    minimum_article_length = int(profile.get("min_characters", 150))
    if len(article) < minimum_article_length:
        errors.append(f"{event['event_id']}: deep story is too short")
    if article.count("；") > 3:
        errors.append(f"{event['event_id']}: deep story overuses semicolons")
    if len(article) >= 240 and len(original) >= 240:
        ratio = difflib.SequenceMatcher(None, re.sub(r"\s+", "", article), re.sub(r"\s+", "", original)).ratio()
        if ratio >= 0.72:
            errors.append(f"{event['event_id']}: draft is too similar to source text ({ratio:.2f})")
    reader_text = "\n".join(
        [draft.get("headline", ""), draft.get("dek", ""), draft.get("one_line_takeaway", ""), article,
         draft.get("judgment", ""), *(draft.get("watch_next") or [])]
    )
    banned = (
        "该表述属于预测而非已验证结果", "证据边界", "Verification Harness",
        "保留归因和预测边界", "待核验", "已核验", "What", "Why", "How",
    )
    if any(term in reader_text for term in banned) or re.search(r"clm_[a-zA-Z0-9_]+", reader_text):
        errors.append(f"{event['event_id']}: reader-facing draft contains audit language")
    if facts:
        support = draft.get("claim_support") or {}
        for field in ("headline", "dek", "one_line_takeaway", "judgment"):
            try:
                validate_supported_text(
                    field, draft.get(field, ""), support.get(field) or [], facts,
                    reader.get("allowed_judgment", ""),
                )
            except ValueError as exc:
                errors.append(str(exc))
    if errors:
        raise ValueError(" | ".join(dict.fromkeys(errors)))


def write_drafts(verified: dict[str, Any], fact_selection: dict[str, Any],
                 collection: dict[str, Any], client=None,
                 event_ids: Optional[list[str]] = None) -> dict[str, Any]:
    client = client or create_llm_client()
    plans = {item["event_id"]: item for item in fact_selection["selections"]}
    event_map = {item["event_id"]: item for item in verified["intelligence_events"]}
    source_map = {item["source_id"]: item for item in collection["source_records"]}
    deep_story_ids = [
        event_id for event_id in verified.get("editorial_selection", {}).get("top_event_ids", [])
        if event_id in event_map
    ][:5]
    if event_ids is not None:
        selected = set(event_ids)
        deep_story_ids = [event_id for event_id in deep_story_ids if event_id in selected]
    ordered = sorted(
        (event_map[event_id] for event_id in deep_story_ids),
        key=lambda item: ({"priority.p0": 0, "priority.p1": 1, "priority.p2": 2}.get(item["priority"], 9), item["event_at"]),
    )
    drafts = []
    calls = []
    blocked = []
    attempt_log = []
    diagnostics = []
    readiness_records = []
    demoted = []
    for rank, event in enumerate(ordered):
        article_type = "deep_story"
        if event["event_id"] not in plans:
            raise ValueError(f"{event['event_id']}: missing locked fact selection")
        readiness = deep_story_readiness(plans[event["event_id"]])
        readiness_records.append({"event_id": event["event_id"], **readiness})
        if not readiness["ready"]:
            demoted.append({
                "event_id": event["event_id"],
                "target_article_type": "quick_read",
                "reason": "deep_story_readiness_failed",
                "details": readiness,
            })
            continue
        input_text = input_for_event(event, plans[event["event_id"]], collection, article_type)
        long_form_mode = (
            plans[event["event_id"]].get("reader_packet", {}).get("writing_profile", {}).get("mode")
            == "long_form"
        )
        payload, metadata = client.generate_json(
            instructions=EDITORIAL_WRITER_INSTRUCTIONS,
            input_text=input_text,
            schema_name="spectra_editorial_draft",
            schema=LONG_FORM_WRITER_SCHEMA if long_form_mode else WRITER_SCHEMA,
        )
        calls.append({**metadata, "event_id": event["event_id"], "attempt": 1})
        returned = payload.get("draft")
        if not isinstance(returned, dict):
            blocked.append({
                "event_id": event["event_id"],
                "reason": "writer did not return the required draft object",
            })
            continue
        if long_form_mode:
            returned = normalize_long_form_draft(returned)
        original, _ = source_text(source_map[event["primary_source_id"]])
        try:
            validate_draft(returned, event, original, plans[event["event_id"]])
        except ValueError as first_exc:
            first_reason = str(first_exc)
            diagnostic = {
                "event_id": event["event_id"],
                "initial_draft": returned,
                "initial_validation_error": first_reason,
            }
            if not allowed_patch_scope(first_reason, returned):
                diagnostic.update({"outcome": "manual_review", "revision": None, "revised_draft": None})
                diagnostics.append(diagnostic)
                blocked.append({
                    "event_id": event["event_id"], "reason": first_reason,
                    "initial_reason": first_reason, "attempts": 1, "queue": "manual_editorial_review",
                })
                attempt_log.append({"event_id": event["event_id"], "attempts": 1, "outcome": "manual_review"})
                continue
            retry_payload, retry_metadata = client.generate_json(
                instructions=EDITORIAL_WRITER_INSTRUCTIONS + REVISION_INSTRUCTIONS,
                input_text=revision_input(input_text, returned, first_reason),
                schema_name="spectra_editorial_patch_revision",
                schema=REVISION_SCHEMA,
            )
            calls.append({**retry_metadata, "event_id": event["event_id"], "attempt": 2})
            revision = retry_payload.get("revision")
            if not isinstance(revision, dict):
                diagnostic.update({"outcome": "manual_review", "revision": retry_payload, "revised_draft": None})
                diagnostics.append(diagnostic)
                blocked.append({
                    "event_id": event["event_id"], "reason": "revision did not return the required patch object",
                    "initial_reason": first_reason, "attempts": 2, "queue": "manual_editorial_review",
                })
                attempt_log.append({"event_id": event["event_id"], "attempts": 2, "outcome": "manual_review"})
                continue
            revised = None
            try:
                revised = apply_revision_patches(returned, revision, first_reason, event["event_id"])
                validate_draft(revised, event, original, plans[event["event_id"]])
            except ValueError as second_exc:
                diagnostic.update({
                    "outcome": "manual_review", "revision": revision,
                    "revised_draft": revised, "final_validation_error": str(second_exc),
                })
                diagnostics.append(diagnostic)
                blocked.append({
                    "event_id": event["event_id"], "reason": str(second_exc),
                    "initial_reason": first_reason, "attempts": 2, "queue": "manual_editorial_review",
                })
                attempt_log.append({"event_id": event["event_id"], "attempts": 2, "outcome": "manual_review"})
                continue
            drafts.append(revised)
            diagnostic.update({
                "outcome": "passed_after_revision", "revision": revision,
                "revised_draft": revised, "final_validation_error": None,
            })
            diagnostics.append(diagnostic)
            attempt_log.append({"event_id": event["event_id"], "attempts": 2, "outcome": "passed_after_revision"})
            continue
        drafts.append(returned)
        diagnostics.append({
            "event_id": event["event_id"], "initial_draft": returned,
            "initial_validation_error": None, "revision": None,
            "revised_draft": None, "final_validation_error": None,
            "outcome": "passed_first_attempt",
        })
        attempt_log.append({"event_id": event["event_id"], "attempts": 1, "outcome": "passed_first_attempt"})
    return {
        "schema_version": "0.1",
        "record_type": "deep_story_draft_bundle",
        "prompt_version": "deep_story_writer.v0.2",
        "drafts": drafts,
        "blocked": blocked,
        "demoted": demoted,
        "readiness": readiness_records,
        "llm_calls": calls,
        "attempt_log": attempt_log,
        "diagnostics": diagnostics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified", required=True)
    parser.add_argument("--fact-selection", required=True)
    parser.add_argument("--collection", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--diagnostics-output", help="Internal-only draft and patch diagnostics JSON")
    parser.add_argument("--model", help="Optional model override for deep-story writing only")
    parser.add_argument("--num-ctx", type=int, help="Optional Ollama context override for deep-story writing only")
    parser.add_argument("--event-id", action="append", dest="event_ids", help="Limit a diagnostic run to selected event IDs")
    args = parser.parse_args()
    if args.model:
        os.environ["SPECTRA_MODEL"] = args.model
    if args.num_ctx:
        os.environ["OLLAMA_NUM_CTX"] = str(args.num_ctx)
    bundle = write_drafts(
        json.loads(Path(args.verified).read_text(encoding="utf-8")),
        json.loads(Path(args.fact_selection).read_text(encoding="utf-8")),
        json.loads(Path(args.collection).read_text(encoding="utf-8")),
        event_ids=args.event_ids,
    )
    diagnostics = bundle.pop("diagnostics", [])
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(bundle, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    diagnostics_output = (
        Path(args.diagnostics_output) if args.diagnostics_output
        else output.with_name(f"{output.stem}.diagnostics.json")
    )
    diagnostics_output.write_text(json.dumps({
        "schema_version": "0.1",
        "record_type": "internal_editorial_revision_diagnostics",
        "internal_only": True,
        "records": diagnostics,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "deep_stories": len(bundle["drafts"]), "blocked": len(bundle["blocked"]),
        "demoted": len(bundle["demoted"]),
        "prompt_version": bundle["prompt_version"], "diagnostics": str(diagnostics_output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
