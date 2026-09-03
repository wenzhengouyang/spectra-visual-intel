#!/usr/bin/env python3
"""Minimal local-14B long-form writer with post-generation audit."""

from __future__ import annotations

import argparse
import copy
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


class LongRevisionError(ValueError):
    def __init__(self, message: str, revision: dict, metadata: dict):
        super().__init__(message)
        self.revision = revision
        self.metadata = metadata


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


INSTRUCTIONS = """你是SPECTRA的中文情报编辑，读者是AI产品策略、模型与内容行业从业者。请把输入的锁定事实写成原创、专业、清晰的博客式情报文章。
只能使用fact_units中的事实；evidence_context只帮助理解对应事实，不能引入新事实。
正文写3—5个自然段，不设段落小标题：第一段交代事件主体、发生了什么及具体问题；中间段按产品构成、运行机制、数据结果或适用范围组织；最后一段补充发布状态、限制或后续安排。不得把每条fact_unit单独写成一段。
正文长度遵守writing_profile的min_characters与max_characters。每段包含2—4个相互关联的完整句子，用句号形成正常节奏，避免用分号串联事实，不使用项目符号、编号、问答体或What/Why/How。
同一来源在一个段落中通常只归因一次。所有“公司称、论文作者报告、据其所知、将会”等归因与限定必须保留，但不得让连续句子或连续段落都以同一种归因开头。
每条事实最多出现一次，标题、摘要与正文不得机械重复。不得新增数字、实体、效果、因果或行业趋势。
judgment只能改写allowed_judgment，控制在一到两句；allowed_judgment为空时judgment必须返回空字符串。不要输出事实编号或“人工确认、已核验、证据边界”等审计语言。
输出严格符合JSON Schema。"""


REVISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "revision": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "event_id": {"type": "string"},
                "patches": {
                    "type": "array", "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": {
                            "target": {
                                "type": "string",
                                "enum": ["headline", "dek", "paragraph", "judgment"],
                            },
                            "paragraph_index": {"type": "integer", "minimum": -1},
                            "text": {"type": "string"},
                        },
                        "required": ["target", "paragraph_index", "text"],
                    },
                },
            },
            "required": ["event_id", "patches"],
        },
    },
    "required": ["revision"],
}


REVISION_INSTRUCTIONS = """你正在修订一篇已经完成初稿、但未通过程序审计的中文情报文章。
只能使用locked_writer_input中的fact_units和evidence_context。audit_errors指出了失败位置。
只返回失败字段或失败段落的补丁，绝对不得重写完整文章，也不得修改allowed_targets之外的位置。
逐项满足target_requirements：require_attribution为true时，修订段落必须明确保留“据公司介绍、论文称、研究团队报告”等与事实一致的归因。修订段落保持正常的2—4句阅读节奏。
若某句缺少claim支持或包含未经支持的效果、因果、趋势、价值判断，删除该句或严格改写为fact_units明确提供的事实。
所有paragraph补丁合计必须达到minimum_total_characters_for_paragraph_patches，保证修订后正文不少于final_article_min_characters。只能用尚未充分表达的fact_units增加信息，不得重复原句凑字数。
不得新增数字、主体、效果、因果、行业趋势或后续计划。输出严格符合JSON Schema。"""


LENGTH_REPAIR_INSTRUCTIONS = """你正在对一篇事实审计已经通过、但正文篇幅不足的中文情报文章做最后一次证据重排。
只能使用locked_writer_input中的fact_units；evidence_context只用于理解对应fact_unit，不得成为新增事实来源。
必须返回allowed_targets列出的全部正文段落补丁，不得修改标题、摘要或判断。按照paragraph_evidence_plan组织正文：每段只能使用assigned_facts中的事实，把相关事实写成2—4个连贯完整句子；同一事实只在一个段落中展开，避免逐条罗列和机械复述。
修订后正文总长度必须落在target_article_characters与final_article_max_characters之间，每段不得短于target_requirements指定的minimum_characters。篇幅补足必须来自事实的背景、构成、机制、数据、适用范围、发布状态或限制等已有信息，不得用评价、效果、因果或趋势判断凑字数。
每个句子都必须能直接对应assigned_facts中的至少一条事实。除非assigned_facts原文明确包含，否则禁止使用“从而、以确保、帮助、使得、提升、改善、推动、促进、意味着、表明”等目的、效果或推断连接语；宁可减少修饰，也不能补写事实没有陈述的用途或结果。
attribution_required为true的事实必须保留“据／称／援引／报告”等来源归因，但同一段通常只需归因一次。不得新增数字、主体、效果、因果、行业趋势或后续计划，不得出现“人工确认、核验、审计、证据边界”等内部语言。
输出严格符合JSON Schema。"""


UNSUPPORTED_INFERENCE_MARKERS = (
    "确保", "有助于", "帮助", "提升", "改善", "推动", "促进", "重塑", "引领", "加速",
    "意味着", "表明", "体现", "凸显", "反映", "标志着", "证明", "显示出", "显著",
    "领先地位", "重要进展", "可复制", "奠定基础", "满足更多", "广泛应用", "规模化",
    "行业趋势", "增长趋势", "实际应用价值", "高效且实用", "后续安排可能", "预计将",
)


def sentence_parts(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"(?<=[。！？!?])", text or "") if part.strip()]


def ranked_claims(text: str, facts: list[dict], minimum_score: float = 0.08) -> list[tuple[float, str]]:
    tokens = meaningful_tokens(text)
    ranked = []
    for fact in facts:
        fact_tokens = meaningful_tokens(fact["text"])
        score = len(tokens & fact_tokens) / max(1, len(tokens))
        if score >= minimum_score:
            ranked.append((score, fact["claim_id"]))
    return sorted(ranked, reverse=True)


def revision_targets(errors: list[str], draft: dict) -> set[tuple[str, int]]:
    targets: set[tuple[str, int]] = set()
    for error in errors:
        for match in re.finditer(r"paragraph\[(\d+)\]", error):
            targets.add(("paragraph", int(match.group(1))))
        for field in ("headline", "dek", "judgment"):
            if error.startswith(field + " ") or error.startswith(field + ":"):
                targets.add((field, -1))
    if any(error.startswith("article length outside") for error in errors):
        paragraphs = draft.get("paragraphs") or []
        if paragraphs:
            length_errors = [error for error in errors if error.startswith("article length outside")]
            if len(length_errors) == len(errors):
                # Once every factual boundary has passed, recomposing all paragraphs is
                # safer than repeatedly inflating the two shortest paragraphs. It lets
                # the writer place every verified fact exactly once in a coherent order.
                targets.update(("paragraph", index) for index in range(len(paragraphs)))
            else:
                shortest = sorted(range(len(paragraphs)), key=lambda index: len(paragraphs[index]))[:2]
                targets.update(("paragraph", index) for index in shortest)
    return targets


def is_length_only_failure(errors: list[str]) -> bool:
    return bool(errors) and all(error.startswith("article length outside") for error in errors)


def paragraph_evidence_plan(draft: dict, facts: list[dict]) -> list[dict]:
    """Assign each verified fact to one best-fit paragraph exactly once."""
    paragraphs = draft.get("paragraphs") or []
    if not paragraphs:
        return []
    assignments: list[list[str]] = [[] for _ in paragraphs]
    paragraph_tokens = [meaningful_tokens(paragraph) for paragraph in paragraphs]
    for fact in facts:
        fact_tokens = meaningful_tokens(str(fact.get("text") or ""))
        scored = []
        for index, tokens in enumerate(paragraph_tokens):
            overlap = len(fact_tokens & tokens) / max(1, len(fact_tokens))
            scored.append((overlap, -len(assignments[index]), -index, index))
        best_index = max(scored)[-1]
        assignments[best_index].append(fact["claim_id"])

    # A sparse draft can map everything onto one paragraph. Move the least
    # represented facts so every rewritten paragraph has an explicit boundary.
    empty_indexes = [index for index, values in enumerate(assignments) if not values]
    for empty_index in empty_indexes:
        donor = max(range(len(assignments)), key=lambda index: len(assignments[index]))
        if len(assignments[donor]) <= 1:
            break
        assignments[empty_index].append(assignments[donor].pop())
    fact_map = {fact["claim_id"]: fact for fact in facts}
    return [
        {
            "paragraph_index": index,
            "assigned_facts": [
                {
                    "fact_id": fact_id,
                    "text": fact_map[fact_id]["text"],
                    "attribution_required": bool(fact_map[fact_id].get("attribution_required")),
                    "evidence_context": fact_map[fact_id].get("evidence_context", ""),
                }
                for fact_id in fact_ids
            ],
        }
        for index, fact_ids in enumerate(assignments)
    ]


def apply_long_revision(draft: dict, revision: dict, allowed: set[tuple[str, int]],
                        event_id: str, requirements: dict[tuple[str, int], dict] | None = None,
                        paragraph_patch_minimum: int = 0) -> dict:
    if revision.get("event_id") != event_id:
        raise ValueError(f"revision returned wrong event_id for {event_id}")
    revised = copy.deepcopy(draft)
    seen: set[tuple[str, int]] = set()
    for patch in revision.get("patches") or []:
        target = patch.get("target")
        index = int(patch.get("paragraph_index", -1))
        key = (target, index)
        if key not in allowed:
            raise ValueError(f"{event_id}: revision attempted out-of-scope patch {target}[{index}]")
        if key in seen:
            raise ValueError(f"{event_id}: duplicate revision patch {target}[{index}]")
        seen.add(key)
        if target == "paragraph":
            paragraphs = revised.get("paragraphs") or []
            if not 0 <= index < len(paragraphs):
                raise ValueError(f"{event_id}: revision paragraph index out of range")
            paragraphs[index] = patch["text"]
        else:
            if index != -1:
                raise ValueError(f"{event_id}: non-paragraph patch must use paragraph_index -1")
            revised[target] = patch["text"]
    if not seen:
        raise ValueError(f"{event_id}: revision returned no applicable patches")
    requirements = requirements or {}
    for target, index in allowed:
        requirement = requirements.get((target, index)) or {}
        if (target, index) not in seen:
            raise ValueError(f"{event_id}: revision omitted required patch {target}[{index}]")
        value = revised["paragraphs"][index] if target == "paragraph" else str(revised.get(target) or "")
        minimum = int(requirement.get("minimum_characters", 0))
        if len(value) < minimum:
            raise ValueError(
                f"{event_id}: revision patch {target}[{index}] shorter than required {minimum} characters"
            )
        if requirement.get("require_attribution") and not any(
            marker in value for marker in LONG_FORM_ATTRIBUTION_MARKERS
        ):
            raise ValueError(f"{event_id}: revision patch paragraph[{index}] still lacks attribution")
    patched_paragraph_characters = sum(
        len(revised["paragraphs"][index]) for target, index in allowed if target == "paragraph"
    )
    if patched_paragraph_characters < paragraph_patch_minimum:
        raise ValueError(
            f"{event_id}: paragraph patches total {patched_paragraph_characters} characters; "
            f"requires {paragraph_patch_minimum}"
        )
    return revised


def backfill_verified_facts(revised: dict, allowed: set[tuple[str, int]], facts: list[dict],
                            minimum_article: int, maximum_article: int) -> list[str]:
    """Fill a small post-revision length gap with untouched verified fact units only."""
    paragraph_indexes = [index for target, index in allowed if target == "paragraph"]
    if not paragraph_indexes:
        return []
    inserted: list[str] = []
    paragraphs = revised.get("paragraphs") or []
    article = "\n".join(paragraphs)
    ranked = []
    article_tokens = meaningful_tokens(article)
    for fact in facts:
        fact_text = str(fact.get("text") or "").strip()
        fact_tokens = meaningful_tokens(fact_text)
        represented = len(fact_tokens & article_tokens) / max(1, len(fact_tokens))
        if fact_text and represented < 0.55:
            ranked.append((represented, fact["claim_id"], fact_text))
    for _, claim_id, fact_text in sorted(ranked):
        current_length = len("\n".join(paragraphs))
        if current_length >= minimum_article:
            break
        if current_length + len(fact_text) > maximum_article:
            continue
        target_index = min(paragraph_indexes, key=lambda index: len(paragraphs[index]))
        separator = "" if paragraphs[target_index].endswith(("。", "！", "？")) else "。"
        paragraphs[target_index] = paragraphs[target_index] + separator + fact_text
        inserted.append(claim_id)
        article_tokens.update(meaningful_tokens(fact_text))
    return inserted


def audit_article(result: dict, facts: list[dict], allowed_judgment: str = "",
                  writing_profile: dict | None = None) -> dict:
    all_fact_text = " ".join(f["text"] for f in facts)
    allowed_numbers = normalized_numbers(all_fact_text)
    allowed_tokens = meaningful_tokens(all_fact_text)
    errors = []
    mappings = []
    sentence_mappings = []
    for index, paragraph in enumerate(result.get("paragraphs") or []):
        extra = normalized_numbers(paragraph) - allowed_numbers
        if extra:
            errors.append(f"paragraph[{index}] unsupported numbers: {sorted(extra)}")
        tokens = meaningful_tokens(paragraph)
        if len(tokens & allowed_tokens) / max(1, len(tokens)) < 0.18:
            errors.append(f"paragraph[{index}] low fact overlap")
        ranked = ranked_claims(paragraph, facts)
        mapped = [claim_id for _, claim_id in ranked[:4]]
        mappings.append({"paragraph_index": index, "claim_ids": mapped})
        if not mapped:
            errors.append(f"paragraph[{index}] no claim mapping")
        mapped_facts = [fact for fact in facts if fact["claim_id"] in mapped]
        if any(fact.get("attribution_required") for fact in mapped_facts) and not any(
            marker in paragraph for marker in LONG_FORM_ATTRIBUTION_MARKERS
        ):
            errors.append(f"paragraph[{index}] source attribution missing")
        for sentence_index, sentence in enumerate(sentence_parts(paragraph)):
            sentence_ranked = ranked_claims(sentence, facts)
            sentence_claim_ids = [claim_id for _, claim_id in sentence_ranked[:3]]
            sentence_mappings.append({
                "paragraph_index": index,
                "sentence_index": sentence_index,
                "text": sentence,
                "claim_ids": sentence_claim_ids,
            })
            if not sentence_claim_ids:
                errors.append(
                    f"paragraph[{index}] sentence[{sentence_index}] lacks claim support: {sentence}"
                )
                continue
            sentence_support = " ".join(
                fact["text"] for fact in facts if fact["claim_id"] in sentence_claim_ids
            )
            sentence_extra = normalized_numbers(sentence) - normalized_numbers(sentence_support)
            if sentence_extra:
                errors.append(
                    f"paragraph[{index}] sentence[{sentence_index}] unsupported numbers: "
                    f"{sorted(sentence_extra)}"
                )
            for marker in UNSUPPORTED_INFERENCE_MARKERS:
                if marker in sentence and marker not in sentence_support:
                    errors.append(
                        f"paragraph[{index}] sentence[{sentence_index}] unsupported inference "
                        f"'{marker}': {sentence}"
                    )
        for earlier_index, earlier in enumerate((result.get("paragraphs") or [])[:index]):
            ratio = difflib.SequenceMatcher(None, paragraph, earlier).ratio()
            if ratio >= 0.62:
                errors.append(f"paragraph[{index}] repeats paragraph[{earlier_index}]: {ratio:.2f}")
    article = "\n".join(result.get("paragraphs") or [])
    judgment = str(result.get("judgment") or "").strip()
    for index, paragraph in enumerate(result.get("paragraphs") or []):
        if judgment and judgment in paragraph:
            errors.append(f"paragraph[{index}] repeats editorial judgment")
    profile = writing_profile or {}
    minimum = int(profile.get("min_characters", 450))
    maximum = int(profile.get("max_characters", 1000))
    if not minimum <= len(article) <= maximum:
        errors.append(f"article length outside {minimum}-{maximum}: {len(article)}")
    for field in ("headline", "dek"):
        value = str(result.get(field) or "")
        extra = normalized_numbers(value) - allowed_numbers
        if extra:
            errors.append(f"{field} unsupported numbers: {sorted(extra)}")
        tokens = meaningful_tokens(value)
        if not tokens or len(tokens & allowed_tokens) / max(1, len(tokens)) < 0.18:
            errors.append(f"{field} low fact overlap")
        for sentence_index, sentence in enumerate(sentence_parts(value)):
            sentence_ranked = ranked_claims(sentence, facts)
            sentence_claim_ids = [claim_id for _, claim_id in sentence_ranked[:3]]
            if not sentence_claim_ids:
                errors.append(f"{field} sentence[{sentence_index}] lacks claim support: {sentence}")
                continue
            sentence_support = " ".join(
                fact["text"] for fact in facts if fact["claim_id"] in sentence_claim_ids
            )
            for marker in UNSUPPORTED_INFERENCE_MARKERS:
                if marker in sentence and marker not in sentence_support:
                    errors.append(
                        f"{field} sentence[{sentence_index}] unsupported inference '{marker}': {sentence}"
                    )
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
    elif judgment_value:
        errors.append("judgment not allowed when allowed_judgment is empty")
    return {
        "status": "passed" if not errors else "needs_review",
        "article_characters": len(article),
        "paragraph_count": len(result.get("paragraphs") or []),
        "paragraph_claim_mapping": mappings,
        "sentence_claim_mapping": sentence_mappings,
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
        "writing_profile": reader.get("writing_profile", {}),
    }
    return (client or create_llm_client()).generate_json(
        instructions=INSTRUCTIONS,
        input_text=json.dumps(payload, ensure_ascii=False),
        schema_name="spectra_minimal_long_story",
        schema=SCHEMA,
    )


def revise_long_story(reader: dict, event_id: str, draft: dict, audit: dict,
                      client=None) -> tuple[dict, dict, dict]:
    audit_errors = audit.get("errors") or []
    length_repair = is_length_only_failure(audit_errors)
    allowed = revision_targets(audit_errors, draft)
    if not allowed:
        raise ValueError(f"{event_id}: audit failure has no safely patchable location")
    profile = reader.get("writing_profile", {})
    minimum_article = int(profile.get("min_characters", 450))
    paragraphs = draft.get("paragraphs") or []
    paragraph_targets = {index for target, index in allowed if target == "paragraph"}
    unchanged_characters = sum(
        len(text) for index, text in enumerate(paragraphs) if index not in paragraph_targets
    )
    paragraph_patch_minimum = max(0, minimum_article - unchanged_characters)
    paragraph_count = max(1, len(paragraph_targets))
    length_repair_paragraph_minimum = 80 if length_repair else 40
    attribution_targets = {
        int(match.group(1))
        for error in audit.get("errors") or []
        for match in re.finditer(r"paragraph\[(\d+)\] source attribution missing", error)
    }
    fact_map = {fact["claim_id"]: fact for fact in reader["fact_units"]}
    for mapping in audit.get("paragraph_claim_mapping") or []:
        index = int(mapping.get("paragraph_index", -1))
        if index in paragraph_targets and any(
            fact_map.get(claim_id, {}).get("attribution_required")
            for claim_id in mapping.get("claim_ids") or []
        ):
            attribution_targets.add(index)
    unchanged_claim_ids = {
        claim_id
        for mapping in audit.get("paragraph_claim_mapping") or []
        if int(mapping.get("paragraph_index", -1)) not in paragraph_targets
        for claim_id in mapping.get("claim_ids") or []
    }
    preferred_expansion_fact_ids = [
        fact["claim_id"] for fact in reader["fact_units"]
        if fact["claim_id"] not in unchanged_claim_ids
    ]
    requirements = {
        (target, index): {
            "require_attribution": target == "paragraph" and index in attribution_targets,
            "minimum_characters": length_repair_paragraph_minimum if target == "paragraph" else 1,
        }
        for target, index in allowed
    }
    evidence_plan = paragraph_evidence_plan(draft, reader["fact_units"]) if length_repair else []
    fact_by_id = {fact["claim_id"]: fact for fact in reader["fact_units"]}
    for item in evidence_plan:
        if any(
            fact.get("attribution_required") for fact in item["assigned_facts"]
        ):
            requirements[("paragraph", item["paragraph_index"])]["require_attribution"] = True
    payload = {
        "locked_writer_input": {
            "event_id": event_id,
            "fact_units": [
                {
                    "fact_id": fact["claim_id"], "text": fact["text"],
                    "attribution_required": fact["attribution_required"],
                }
                for fact in reader["fact_units"]
            ],
            "evidence_context": [
                {"fact_id": fact["claim_id"], "text": fact["evidence_context"]}
                for fact in reader["fact_units"] if fact.get("evidence_context")
            ],
            "allowed_judgment": reader.get("allowed_judgment", ""),
            "writing_profile": profile,
        },
        "failed_draft": draft,
        "revision_mode": "evidence_recomposition_for_length" if length_repair else "targeted_fact_repair",
        "audit_errors": audit_errors,
        "allowed_targets": [
            {"target": target, "paragraph_index": index}
            for target, index in sorted(allowed)
        ],
        "target_requirements": [
            {"target": target, "paragraph_index": index, **requirements[(target, index)]}
            for target, index in sorted(allowed)
        ],
        "final_article_min_characters": minimum_article,
        "final_article_max_characters": int(profile.get("max_characters", 1000)),
        "target_article_characters": min(
            int(profile.get("max_characters", 1000)), max(minimum_article + 80, 680)
        ),
        "minimum_total_characters_for_paragraph_patches": paragraph_patch_minimum,
        "preferred_expansion_fact_ids": preferred_expansion_fact_ids,
        "paragraph_evidence_plan": evidence_plan,
    }
    response, metadata = (client or create_llm_client()).generate_json(
        instructions=LENGTH_REPAIR_INSTRUCTIONS if length_repair else REVISION_INSTRUCTIONS,
        input_text=json.dumps(payload, ensure_ascii=False),
        schema_name="spectra_minimal_long_story_patch_revision",
        schema=REVISION_SCHEMA,
    )
    revision = response.get("revision") or {}
    revision["revision_mode"] = payload["revision_mode"]
    if evidence_plan:
        revision["paragraph_evidence_plan"] = evidence_plan
    try:
        revised = apply_long_revision(
            draft, revision, allowed, event_id, requirements, 0
        )
    except ValueError as exc:
        raise LongRevisionError(str(exc), revision, metadata) from exc
    inserted = backfill_verified_facts(
        revised, allowed, reader["fact_units"], minimum_article,
        int(profile.get("max_characters", 1000)),
    )
    if inserted:
        revision["programmatic_backfill_fact_ids"] = inserted
    # Do not reject a locally valid patch merely because the edited paragraphs
    # miss their estimated share of the article target by a few characters.
    # The caller immediately audits the complete article (including untouched
    # paragraphs) against the real min/max range and every claim boundary.
    return revised, metadata, revision


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
    audit = audit_article(
        result, reader["fact_units"], reader.get("allowed_judgment", ""),
        reader.get("writing_profile", {}),
    )
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
