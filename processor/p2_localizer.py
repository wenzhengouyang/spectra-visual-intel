#!/usr/bin/env python3
"""Localize and validate P2 briefs for the Chinese SPECTRA interface.

The source wording is retained for traceability. Translation is deliberately
limited to fields that do not already contain Chinese, and P2 verification
status is never upgraded by this step.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectra_agent.llm_client import create_llm_client, load_local_env
from verification.verification_harness import numbers_match, normalized_numbers as semantic_numbers
from processor.language_quality import has_readable_chinese


CHINESE_RE = re.compile(r"[\u3400-\u9fff]")
SENSATIONAL_HEADLINE_RE = re.compile(
    r"[！!]|重磅|炸裂|惊天|刚刚[，,]|史上最|真香|颠覆|吊打|碾压|杀疯|震住|封神|官宣"
)
NUMBER_RE = re.compile(r"\d+(?:[.,]\d+)*(?:%|％)?")
ENGLISH_MONTHS = {
    "january": "1月", "february": "2月", "march": "3月", "april": "4月",
    "may": "5月", "june": "6月", "july": "7月", "august": "8月",
    "september": "9月", "october": "10月", "november": "11月", "december": "12月",
}
SOURCE_ATTRIBUTION_RE = re.compile(
    r"(?i:\b(?:says?|said|claims?|claimed|reports?|reported|according to|plans? to|might|could)\b)|"
    r"\bmay\b|称|声称|表示|据报道|据称|计划|拟|预计|可能|或将|作者报告",
)
ZH_ATTRIBUTION_RE = re.compile(r"称|声称|表示|据|报告|计划|拟|预计|可能|或将|作者|公司")


def has_chinese(value: str | None) -> bool:
    return bool(value and CHINESE_RE.search(value))


def fields_needing_translation(brief: dict[str, Any]) -> list[str]:
    fields = [field for field in ("headline", "dek") if not has_readable_chinese(brief.get(field), field)]
    if SENSATIONAL_HEADLINE_RE.search(str(brief.get("headline") or "")) and "headline" not in fields:
        fields.append("headline")
    return fields


def raw_number_tokens(value: str) -> set[str]:
    return {item.replace(",", "").replace("％", "%") for item in NUMBER_RE.findall(value or "")}


def number_basis(value: str) -> str:
    text = value or ""
    for month, replacement in ENGLISH_MONTHS.items():
        text = re.sub(rf"(?i)\b{month}\b", replacement, text)
    # A four-digit calendar year is often bare in English but naturally gains
    # “年” in Chinese. Normalize both sides before semantic unit comparison.
    text = re.sub(r"\b((?:19|20)\d{2})\b(?!年)", r"\1年", text)
    return text


def validate_translation(source: str, translated: str, field: str) -> None:
    if not has_readable_chinese(translated, field):
        raise ValueError(f"{field} is not a readable Chinese sentence")
    source_numbers = number_basis(source)
    translated_numbers = number_basis(translated)
    if not numbers_match(source_numbers, translated_numbers) or not numbers_match(translated_numbers, source_numbers):
        missing = raw_number_tokens(source) - raw_number_tokens(translated)
        raise ValueError(
            f"{field} translation lost, changed or added numbers/units: {sorted(missing)}; "
            f"required={semantic_numbers(source)}"
        )
    if SOURCE_ATTRIBUTION_RE.search(source) and not ZH_ATTRIBUTION_RE.search(translated):
        raise ValueError(f"{field} translation lost attribution or uncertainty wording")


def apply_translation(brief: dict[str, Any], translated: dict[str, Any]) -> None:
    requested = fields_needing_translation(brief)
    for field in requested:
        target = translated[f"{field}_zh"].strip()
        try:
            validate_translation(str(brief.get(field) or ""), target, field)
        except ValueError as exc:
            raise ValueError(f"{brief.get('brief_id')} {exc}") from exc
        brief[f"original_{field}"] = brief.get(field)
        brief[field] = target
    if requested:
        brief["localization_status"] = "machine_localized_validated"
        brief["localized_fields"] = requested


def validate_localized_brief(brief: dict[str, Any]) -> list[str]:
    """Return reader-facing localization errors without changing P2 fact status."""
    errors: list[str] = []
    for field in ("headline", "dek"):
        value = str(brief.get(field) or "")
        if not has_readable_chinese(value, field):
            errors.append(f"{field}_not_readable_chinese")
        original = brief.get(f"original_{field}")
        if original:
            try:
                validate_translation(str(original), value, field)
            except ValueError as exc:
                errors.append(str(exc))
    if SENSATIONAL_HEADLINE_RE.search(str(brief.get("headline") or "")):
        errors.append("headline_uses_sensational_media_wording")
    return errors


def quarantine_failed_briefs(issue: dict[str, Any], failed: list[dict[str, Any]]) -> dict[str, Any]:
    """Remove failed localizations from publication and return a review artifact."""
    failed_by_id = {item["brief_id"]: item for item in failed}
    failed_ids = set(failed_by_id)
    if failed_ids:
        issue["news_briefs"] = [
            brief for brief in issue.get("news_briefs", []) if brief.get("brief_id") not in failed_ids
        ]
        for day in (issue.get("presentation") or {}).get("timeline_days", []):
            day["brief_ids"] = [brief_id for brief_id in day.get("brief_ids", []) if brief_id not in failed_ids]
        issue_meta = issue.get("issue") or {}
        issue_meta["news_brief_ids"] = [
            brief_id for brief_id in issue_meta.get("news_brief_ids", []) if brief_id not in failed_ids
        ]
        issue_meta["brief_count"] = len(issue.get("news_briefs", []))
        issue_meta["total_intelligence_count"] = len(issue.get("editorial_stories", [])) + issue_meta["brief_count"]
    return {
        "schema_version": "0.1",
        "record_type": "p2_localization_review_queue",
        "status": "waiting_for_review" if failed else "not_required",
        "count": len(failed),
        "records": list(failed_by_id.values()),
    }


def restore_reviewed_localizations(issue: dict[str, Any], cached_issue: dict[str, Any] | None,
                                   review_queue: dict[str, Any] | None) -> dict[str, int]:
    """Reuse validated copy and apply explicit review decisions after a resumed run."""
    cached = {
        brief.get("brief_id"): brief
        for brief in (cached_issue or {}).get("news_briefs", [])
        if brief.get("localization_status") == "machine_localized_validated"
        and not validate_localized_brief(brief)
    }
    restored = 0
    for brief in issue.get("news_briefs", []):
        prior = cached.get(brief.get("brief_id"))
        if not prior:
            continue
        for field in ("headline", "dek", "original_headline", "original_dek",
                      "localization_status", "localized_fields"):
            if field in prior:
                brief[field] = copy.deepcopy(prior[field])
        restored += 1

    supplied = 0
    excluded_ids: set[str] = set()
    records = (review_queue or {}).get("records", [])
    by_id = {brief.get("brief_id"): brief for brief in issue.get("news_briefs", [])}
    for record in records:
        if record.get("review_status") != "approved":
            continue
        brief_id = record.get("brief_id")
        if record.get("decision") == "exclude":
            excluded_ids.add(brief_id)
            continue
        if record.get("decision") != "supply_chinese_copy" or brief_id not in by_id:
            continue
        apply_translation(by_id[brief_id], {
            "headline_zh": str(record.get("headline_zh") or ""),
            "dek_zh": str(record.get("dek_zh") or ""),
        })
        supplied += 1
    if excluded_ids:
        quarantine_failed_briefs(issue, [{"brief_id": brief_id} for brief_id in excluded_ids])
    return {"restored": restored, "supplied": supplied, "excluded": len(excluded_ids)}


def schema_for(ids: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "translations": {
                "type": "array",
                "minItems": len(ids),
                "maxItems": len(ids),
                "items": {
                    "type": "object",
                    "properties": {
                        "brief_id": {"type": "string", "enum": ids},
                        "headline_zh": {"type": "string"},
                        "dek_zh": {"type": "string"},
                    },
                    "required": ["brief_id", "headline_zh", "dek_zh"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["translations"],
        "additionalProperties": False,
    }


INSTRUCTIONS = """你是中文AI产业情报编辑，只负责忠实本地化，不负责补充事实或判断。
规则：
1. 将指定字段转为自然、准确、简洁的中文；模型名、公司名、论文名和必要缩写可保留英文。
2. 标题改写为中性的新闻标题，删除“震惊、刚刚、官宣、颠覆、吊打”等媒体口号，但不得改变事实或数字；摘要只转述输入已有内容，不添加背景、因果或预测。
3. 所有数字、百分比、版本号必须完整保留。
4. 若某字段无需翻译，原样返回。
5. 严格按JSON Schema输出，不输出解释。
"""


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def embed_issue(static_path: Path, issue: dict[str, Any]) -> None:
    html = static_path.read_text(encoding="utf-8")
    payload = json.dumps(issue, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    embedded = f'<!-- ISSUE_DATA_START --><script id="issue-data" type="application/json">{payload}</script><!-- ISSUE_DATA_END -->'
    html, count = re.subn(
        r"<!-- ISSUE_DATA_START -->.*?<!-- ISSUE_DATA_END -->",
        lambda _: embedded,
        html,
        count=1,
        flags=re.S,
    )
    if count != 1:
        raise RuntimeError("static prototype is missing issue data markers")
    static_path.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--review-queue")
    parser.add_argument("--static")
    parser.add_argument("--batch-size", type=int, default=6)
    parser.add_argument("--model")
    parser.add_argument("--num-ctx", type=int)
    args = parser.parse_args()

    load_local_env()
    if args.model:
        os.environ["SPECTRA_MODEL"] = args.model
    if args.num_ctx:
        os.environ["OLLAMA_NUM_CTX"] = str(args.num_ctx)
    input_path = Path(args.input)
    output_path = Path(args.output)
    checkpoint_path = Path(args.checkpoint)
    review_queue_path = Path(args.review_queue) if args.review_queue else output_path.with_name("p2-localization-review.json")
    issue = json.loads(input_path.read_text(encoding="utf-8"))
    targets = [brief for brief in issue.get("news_briefs", []) if fields_needing_translation(brief)]
    client = create_llm_client() if targets else None
    batches: list[dict[str, Any]] = []
    failed_briefs: list[dict[str, Any]] = []

    for start in range(0, len(targets), args.batch_size):
        assert client is not None
        batch = targets[start : start + args.batch_size]
        ids = [brief["brief_id"] for brief in batch]
        input_items = [
            {
                "brief_id": brief["brief_id"],
                "translate_fields": fields_needing_translation(brief),
                "headline": brief.get("headline", ""),
                "dek": brief.get("dek", ""),
            }
            for brief in batch
        ]
        last_error: Exception | None = None
        for attempt in range(1, 4):
            retry_note = "" if attempt == 1 else (
                "\n上次输出未通过校验。translate_fields的结果必须以中文句子表达并包含中文汉字，"
                "不能照抄英文；例如‘Nvidia partners with X’应写为‘英伟达与X达成合作’。同时完整保留数字。"
            )
            result, metadata = client.generate_json(
                instructions=INSTRUCTIONS + retry_note,
                input_text=json.dumps({"briefs": input_items}, ensure_ascii=False),
                schema_name="spectra_p2_localization",
                schema=schema_for(ids),
            )
            rows = result.get("translations", [])
            by_id = {row["brief_id"]: row for row in rows}
            try:
                if set(by_id) != set(ids):
                    raise ValueError("translation response IDs do not match requested brief IDs")
                checked = copy.deepcopy(batch)
                for brief in checked:
                    apply_translation(brief, by_id[brief["brief_id"]])
                for original, translated_brief in zip(batch, checked):
                    original.clear()
                    original.update(translated_brief)
                last_error = None
                break
            except (KeyError, ValueError) as exc:
                last_error = exc
        if last_error:
            # A single stubborn field should not force the model to rewrite an
            # otherwise valid batch.  Retry each brief independently and keep
            # the same hard validation rules for Chinese, numbers and source
            # attribution.  This is a repair pass, not a permissive fallback.
            repaired: list[dict[str, Any]] = []
            for brief in batch:
                brief_id = brief["brief_id"]
                single_item = {
                    "brief_id": brief_id,
                    "translate_fields": fields_needing_translation(brief),
                    "headline": brief.get("headline", ""),
                    "dek": brief.get("dek", ""),
                }
                field_error: Exception | None = None
                for repair_attempt in range(1, 4):
                    error_hint = field_error or last_error
                    required_numbers = {
                        field: semantic_numbers(str(brief.get(field) or ""))
                        for field in fields_needing_translation(brief)
                    }
                    repair_note = (
                        "\n这是字段级修订，只处理这一条。上一轮失败原因是："
                        f"{error_hint}。translate_fields中的每个结果必须包含自然中文，"
                        "不能只保留英文标题；专有名词可保留英文，但动作、状态和说明必须译成中文。"
                        f"对应译文的数字、数量级和单位必须与此清单语义完全一致：{json.dumps(required_numbers, ensure_ascii=False)}。"
                        "例如100 million必须译为1亿或保留为100 million，不能只写100；不得添加原文没有的数字。"
                    )
                    repair_result, repair_metadata = client.generate_json(
                        instructions=INSTRUCTIONS + repair_note,
                        input_text=json.dumps({"briefs": [single_item]}, ensure_ascii=False),
                        schema_name="spectra_p2_localization_repair",
                        schema=schema_for([brief_id]),
                    )
                    repair_rows = repair_result.get("translations", [])
                    try:
                        if len(repair_rows) != 1 or repair_rows[0].get("brief_id") != brief_id:
                            raise ValueError("field repair response ID does not match requested brief ID")
                        checked_brief = copy.deepcopy(brief)
                        apply_translation(checked_brief, repair_rows[0])
                        brief.clear()
                        brief.update(checked_brief)
                        repaired.append({
                            "brief_id": brief_id,
                            "attempt": repair_attempt,
                            **repair_metadata,
                        })
                        field_error = None
                        break
                    except (KeyError, ValueError) as exc:
                        field_error = exc
                if field_error:
                    failed_briefs.append({
                        "queue_id": f"p2_localization_{brief_id}",
                        "brief_id": brief_id,
                        "candidate_id": brief.get("candidate_id"),
                        "original_headline": brief.get("headline"),
                        "original_dek": brief.get("dek"),
                        "errors": [str(field_error)],
                        "review_status": "pending",
                        "allowed_decisions": ["supply_chinese_copy", "retry", "exclude"],
                    })
            metadata = {**metadata, "repair_mode": "field_level", "repairs": repaired}
        batches.append({"index": len(batches) + 1, "brief_ids": ids, **metadata})
        checkpoint = {
            "status": "running",
            "translated": start + len(batch),
            "total": len(targets),
            "batches": batches,
        }
        write_json(output_path, issue)
        write_json(checkpoint_path, checkpoint)

    failed_ids = {item["brief_id"] for item in failed_briefs}
    for brief in issue.get("news_briefs", []):
        if brief["brief_id"] in failed_ids:
            continue
        errors = validate_localized_brief(brief)
        if errors:
            failed_briefs.append({
                "queue_id": f"p2_localization_{brief['brief_id']}",
                "brief_id": brief["brief_id"],
                "candidate_id": brief.get("candidate_id"),
                "original_headline": brief.get("original_headline") or brief.get("headline"),
                "original_dek": brief.get("original_dek") or brief.get("dek"),
                "errors": errors,
                "review_status": "pending",
                "allowed_decisions": ["supply_chinese_copy", "retry", "exclude"],
            })
    review_queue = quarantine_failed_briefs(issue, failed_briefs)
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    localized_total = sum(
        brief.get("localization_status") == "machine_localized_validated"
        for brief in issue.get("news_briefs", [])
    )
    issue["localization"] = {
        "status": "completed_with_review" if failed_briefs else "completed",
        "language": "zh-CN",
        "translated_briefs": localized_total,
        "blocked_briefs": len(failed_briefs),
        "completed_at": now,
        "note": "机器翻译仅改善中文阅读，不改变P2待核验状态；英文原文保留在original_*字段。",
    }
    write_json(output_path, issue)
    write_json(review_queue_path, review_queue)
    write_json(checkpoint_path, {
        "status": "completed_with_review" if failed_briefs else "completed",
        "processed": len(targets),
        "published": max(0, len(targets) - len(failed_briefs)),
        "blocked": len(failed_briefs),
        "total": len(targets),
        "batches": batches,
    })
    if args.static:
        embed_issue(Path(args.static), issue)
    print(json.dumps(issue["localization"], ensure_ascii=False))


if __name__ == "__main__":
    main()
