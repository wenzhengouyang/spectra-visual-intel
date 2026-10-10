#!/usr/bin/env python3
"""Localize and validate P2 briefs for the Chinese SPECTRA interface.

The source wording is retained for traceability. Translation is deliberately
limited to fields that do not already contain Chinese, and P2 verification
status is never upgraded by this step.
"""

from __future__ import annotations

import argparse
import copy
import html
import json
import math
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
ENGLISH_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
    "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
CHINESE_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
                  "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
SOURCE_REPORT_RE = re.compile(
    r"(?i:\b(?:says?|said|claims?|claimed|reports?|reported|according to)\b)|称|声称|表示|据报道|据称|作者报告"
)
ZH_REPORT_RE = re.compile(r"称|声称|表示|据|报告|作者")
SOURCE_UNCERTAINTY_RE = re.compile(
    r"(?i:\b(?:plans? to|might|could|reportedly)\b)|\bmay\b|计划|拟|预计|可能|或将|尚未确认|不确定"
)
ZH_UNCERTAINTY_RE = re.compile(r"计划|拟|预计|可能|或将|据报道|据称|尚未确认|不确定")
AUTO_REPAIR_ROUNDS = 2
FACT_RISK_ERROR_MARKERS = ("numbers/units", "attribution or uncertainty")
RULE_SAMPLES_PATH = ROOT / "processor/localization_rule_samples.v0.1.json"


def repair_rule_examples() -> str:
    try:
        samples = json.loads(RULE_SAMPLES_PATH.read_text(encoding="utf-8")).get("samples", [])
    except (OSError, ValueError):
        return ""
    examples = [
        {"source": item.get("source"), "accepted": item.get("accepted")}
        for item in samples if item.get("source") and item.get("accepted")
    ]
    return "\n参考已审核规则样本：" + json.dumps(examples, ensure_ascii=False)


def chinese_ordinal_value(raw: str) -> int | None:
    if raw == "十":
        return 10
    if "十" in raw:
        left, right = raw.split("十", 1)
        tens = CHINESE_DIGITS.get(left, 1) if left else 1
        ones = CHINESE_DIGITS.get(right, 0) if right else 0
        return tens * 10 + ones
    digits = [CHINESE_DIGITS.get(char) for char in raw]
    if any(value is None for value in digits):
        return None
    return int("".join(str(value) for value in digits))


def has_chinese(value: str | None) -> bool:
    return bool(value and CHINESE_RE.search(value))


def fields_needing_translation(brief: dict[str, Any]) -> list[str]:
    fields = [field for field in ("headline", "dek") if not has_readable_chinese(brief.get(field), field)]
    if SENSATIONAL_HEADLINE_RE.search(str(brief.get("headline") or "")) and "headline" not in fields:
        fields.append("headline")
    for field in ("headline", "dek"):
        original = brief.get(f"original_{field}")
        if not original or field in fields:
            continue
        try:
            validate_translation(str(original), str(brief.get(field) or ""), field)
        except ValueError:
            fields.append(field)
    return fields


def raw_number_tokens(value: str) -> set[str]:
    value = html.unescape(value or "")
    return {item.replace(",", "").replace("％", "%") for item in NUMBER_RE.findall(value)}


def number_basis(value: str) -> str:
    # Source extracts can contain HTML non-breaking-space entities.  Decode
    # them before numeric validation so ``&#160;`` is never mistaken for fact 160.
    text = html.unescape(value or "")
    # "one of the largest" is a ranking idiom, not a factual count that must
    # appear as the digit 1 in Chinese.
    text = re.sub(r"(?i)\bone\s+of\b", "ranking-of", text)
    # In "a shared training primitive, one that ...", "one" is an anaphoric
    # pronoun rather than a measurable quantity.
    text = re.sub(r"(?i)\bone\s+(?=that\b|which\b)", "anaphoric-item ", text)
    # Hyphenated dimensionality and demonstratives are descriptive language,
    # not reportable quantities. Converting them to bare digits makes the
    # shared numeric validator invent a missing fact in otherwise faithful
    # Chinese copy.
    text = re.sub(r"(?i)\b(?:one|two|three|four|five|six|seven|eight|nine|ten)-dimensional\b", "dimensional", text)
    text = re.sub(r"(?i)\bthis\s+one(?:\s+here)?\b", "this item", text)
    word_pattern = "|".join(sorted(ENGLISH_NUMBER_WORDS, key=len, reverse=True))

    def replace_english_number(match: re.Match[str]) -> str:
        parts = re.split(r"[-\s]+", match.group(0).lower())
        return str(sum(ENGLISH_NUMBER_WORDS[part] for part in parts))

    text = re.sub(rf"(?i)\b(?:{word_pattern})(?:[- ](?:{word_pattern}))?\b",
                  replace_english_number, text)

    def replace_chinese_ordinal(match: re.Match[str]) -> str:
        parsed = chinese_ordinal_value(match.group(1))
        return str(parsed) if parsed is not None else match.group(0)

    text = re.sub(r"第([零一二两三四五六七八九十]{1,3})", replace_chinese_ordinal, text)
    text = re.sub(
        r"([零一二两三四五六七八九十]{1,3})(?=位|人|个|家|项|条|次|台|所|款|名|种|类|套|步|份)",
        lambda match: str(chinese_ordinal_value(match.group(1)))
        if chinese_ordinal_value(match.group(1)) is not None else match.group(0),
        text,
    )
    for month, replacement in ENGLISH_MONTHS.items():
        if month.lower() == "may":
            text = re.sub(r"(?i)\bmay\b(?=\s+\d)|(?<=\d)\s+\bmay\b|(?<=in )\bmay\b|(?<=of )\bmay\b", replacement, text)
            continue
        text = re.sub(rf"(?i)\b{month}\b", replacement, text)
    # English dates commonly use an ordinal day (for example, "October
    # 14th").  Once the month has been converted to Chinese, normalize that
    # day too so it compares to the natural Chinese form "10月14日".
    text = re.sub(r"(?i)(?<=月)\s*(\d{1,2})(?:st|nd|rd|th)\b", r"\1日", text)
    # A four-digit calendar year is often bare in English but naturally gains
    # “年” in Chinese. Normalize both sides before semantic unit comparison.
    text = re.sub(r"\b((?:19|20)\d{2})\b(?!年)", r"\1年", text)
    return text


def validate_translation(source: str, translated: str, field: str) -> None:
    if not has_readable_chinese(translated, field):
        raise ValueError(f"{field} is not a readable Chinese sentence")
    source_numbers = number_basis(source)
    translated_numbers = number_basis(translated)
    forward_matches = numbers_match(source_numbers, translated_numbers)
    reverse_matches = numbers_match(translated_numbers, source_numbers)
    # Chinese commonly makes an implicit English article/pair explicit as
    # “一个/两位”.  Those grammatical 1/2 counts are safe when neither side
    # contains an explicit Arabic number.  Material added quantities (20%,
    # years, money, etc.) remain blocked.
    source_explicit = raw_number_tokens(source)
    translated_explicit = raw_number_tokens(translated)
    source_semantic = semantic_numbers(source_numbers)
    translated_semantic = semantic_numbers(translated_numbers)
    unmatched_translated = list(translated_semantic)
    for wanted in source_semantic:
        match = next((item for item in unmatched_translated
                      if item.get("kind") == wanted.get("kind")
                      and math.isclose(float(item.get("value") or 0), float(wanted.get("value") or 0),
                                       rel_tol=1e-9, abs_tol=1e-9)), None)
        if match is not None:
            unmatched_translated.remove(match)
    implicit_classifier_only = (
        translated_explicit.issubset(source_explicit)
        and unmatched_translated
        and all(item.get("kind") == "count" and item.get("value") in {1.0, 2.0}
                for item in unmatched_translated)
    )
    if not forward_matches or (not reverse_matches and not implicit_classifier_only):
        missing = raw_number_tokens(source) - raw_number_tokens(translated)
        raise ValueError(
            f"{field} translation lost, changed or added numbers/units: {sorted(missing)}; "
            f"required={semantic_numbers(source)}"
        )
    if SOURCE_REPORT_RE.search(source) and not ZH_REPORT_RE.search(translated):
        raise ValueError(f"{field} translation lost attribution or uncertainty wording")
    if SOURCE_UNCERTAINTY_RE.search(source) and not ZH_UNCERTAINTY_RE.search(translated):
        raise ValueError(f"{field} translation lost attribution or uncertainty wording")


def apply_translation(brief: dict[str, Any], translated: dict[str, Any]) -> None:
    requested = fields_needing_translation(brief)
    for field in requested:
        target = translated[f"{field}_zh"].strip()
        source = str(brief.get(f"original_{field}") or brief.get(field) or "")
        try:
            validate_translation(source, target, field)
        except ValueError as exc:
            raise ValueError(f"{brief.get('brief_id')} {exc}") from exc
        brief.setdefault(f"original_{field}", brief.get(field))
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


def requires_human_localization_review(errors: list[str]) -> bool:
    """Only factual fidelity risks belong in the human localization queue."""
    return any(marker in error for error in errors for marker in FACT_RISK_ERROR_MARKERS)


def fact_risk_differences(record: dict[str, Any]) -> list[dict[str, str]]:
    """Build the small, factual comparison shown to a human reviewer."""
    risks: list[dict[str, str]] = []
    for error in record.get("errors") or []:
        if "numbers/units" in error:
            risk_type = "number_or_unit"
        elif "attribution or uncertainty" in error:
            risk_type = "attribution_or_uncertainty"
        else:
            continue
        field = "headline" if "headline" in error else "dek" if "dek" in error else "unknown"
        risks.append({
            "risk_type": risk_type,
            "field": field,
            "source": str(record.get(f"original_{field}") or ""),
            "candidate": str(record.get(f"candidate_{field}_zh") or record.get(f"{field}_zh") or ""),
            "reason": error,
        })
    return risks


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
        if ((prior.get('original_headline') or prior.get('headline')) != brief.get('headline')
                or (prior.get('original_dek') or prior.get('dek')) != brief.get('dek')):
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
        # Generate once. Validation failure then enters exactly two bounded,
        # field-level repair rounds instead of immediately asking a person.
        for attempt in range(1, 2):
            retry_note = "" if attempt == 1 else (
                "\n上次输出未通过校验。translate_fields的结果必须以中文句子表达并包含中文汉字，"
                "不能照抄英文；例如‘Nvidia partners with X’应写为‘英伟达与X达成合作’。同时完整保留数字。"
            )
            try:
                result, metadata = client.generate_json(
                    instructions=INSTRUCTIONS + retry_note,
                    input_text=json.dumps({"briefs": input_items}, ensure_ascii=False),
                    schema_name="spectra_p2_localization",
                    schema=schema_for(ids),
                )
            except Exception as exc:
                last_error = exc
                metadata = {"generation_error": str(exc)}
                continue
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
                last_candidate: dict[str, Any] = {}
                for repair_attempt in range(1, AUTO_REPAIR_ROUNDS + 1):
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
                    ) + repair_rule_examples()
                    try:
                        repair_result, repair_metadata = client.generate_json(
                            instructions=INSTRUCTIONS + repair_note,
                            input_text=json.dumps({"briefs": [single_item]}, ensure_ascii=False),
                            schema_name="spectra_p2_localization_repair",
                            schema=schema_for([brief_id]),
                        )
                    except Exception as exc:
                        field_error = exc
                        continue
                    repair_rows = repair_result.get("translations", [])
                    if repair_rows:
                        last_candidate = repair_rows[0]
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
                        "candidate_headline_zh": last_candidate.get("headline_zh"),
                        "candidate_dek_zh": last_candidate.get("dek_zh"),
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
    human_failures = [item for item in failed_briefs if requires_human_localization_review(item.get("errors") or [])]
    for item in human_failures:
        item["fact_risks"] = fact_risk_differences(item)
    automatic_exclusions = [item for item in failed_briefs if item not in human_failures]
    review_queue = quarantine_failed_briefs(issue, failed_briefs)
    review_queue.update({
        "status": "waiting_for_review" if human_failures else "not_required",
        "count": len(human_failures),
        "records": human_failures,
        "automatic_exclusion_count": len(automatic_exclusions),
    })
    write_json(output_path.with_name("p2-localization-auto-exclusions.json"), {
        "schema_version": "0.1",
        "record_type": "p2_localization_auto_exclusions",
        "count": len(automatic_exclusions),
        "records": automatic_exclusions,
    })
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    localized_total = sum(
        brief.get("localization_status") == "machine_localized_validated"
        for brief in issue.get("news_briefs", [])
    )
    issue["localization"] = {
        "status": "completed_with_review" if human_failures else "completed",
        "language": "zh-CN",
        "translated_briefs": localized_total,
        "blocked_briefs": len(human_failures),
        "auto_excluded_briefs": len(automatic_exclusions),
        "completed_at": now,
        "note": "机器翻译仅改善中文阅读，不改变P2待核验状态；英文原文保留在original_*字段。",
    }
    write_json(output_path, issue)
    write_json(review_queue_path, review_queue)
    write_json(checkpoint_path, {
        "status": "completed_with_review" if human_failures else "completed",
        "processed": len(targets),
        "published": max(0, len(targets) - len(failed_briefs)),
        "blocked": len(human_failures),
        "auto_excluded": len(automatic_exclusions),
        "total": len(targets),
        "batches": batches,
    })
    if args.static:
        embed_issue(Path(args.static), issue)
    print(json.dumps(issue["localization"], ensure_ascii=False))


if __name__ == "__main__":
    main()
