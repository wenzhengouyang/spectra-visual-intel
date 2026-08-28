#!/usr/bin/env python3
"""Build provisional verification candidates and an evidence review packet.

This stage is deliberately pre-human: it may locate evidence and flag risk, but
it never marks a claim or event as verified and never crosses the P1 gate.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any


NUMBER_RE = re.compile(
    r"(?:[$¥￥]\s*)?\d+(?:\.\d+)?\s*(?:trillion|billion|million|thousand|[KMBT](?![A-Za-z])|万亿|亿|万|千|百)?"
    r"[-\s]*(?:美元|人民币|元|%|％|hours?|minutes?|days?|years?|件|人|小时|分钟|个|条|次|台|家|国|地区|支|所|块|步|年|月|日)?",
    re.IGNORECASE,
)
ATTRIBUTION_MARKERS = (
    "称", "表示", "据", "报道", "作者", "文章", "计划", "预计", "可能", "或将",
    "尚不清楚", "尚未确认", "未确认", "不确定", "according to", "reported", "may", "could",
)
UNCERTAIN_EVIDENCE_MARKERS = (
    "据报道", "据称", "声称", "可能", "预计", "或将", "尚不清楚", "尚未确认",
    "未确认", "不确定", "仿佛", "好像", "神秘", "作者认为", "作者表示", "文章认为",
    "according to", "reportedly", "may", "might", "could",
)
TREND_PATTERNS = (
    r"行业(?:已经|正在|全面)?进入.{0,12}时代",
    r"已成为行业趋势",
    r"行业正在(?:普遍|全面)",
    r"(?:普遍|全面)替代(?:传统|现有)",
)
TREND_QUALIFIERS = ("该案例", "该产品", "该平台", "文章", "作者", "可能", "若", "有待", "单一案例")

# A deliberately small, auditable bilingual concept map. It is not a general
# translator. It only bridges recurring concepts in SPECTRA claims so that an
# English primary source is not rejected merely because the claim is Chinese.
BILINGUAL_CONCEPTS: dict[str, tuple[str, ...]] = {
    "release": ("推出", "发布", "上线", "introduce", "launch", "release"),
    "legal": ("法律", "法务", "律所", "legal", "law firm"),
    "skill": ("技能", "skill"),
    "connector": ("连接器", "连接系统", "connector", "connection"),
    "agent": ("代理", "智能体", "agent"),
    "ecosystem": ("生态系统", "生态", "ecosystem"),
    "governance": ("治理", "受治理", "governed", "governance"),
    "deployment": ("部署", "deployment"),
    "latent": ("潜在", "潜空间", "latent"),
    "trajectory": ("轨迹", "trajectory", "path"),
    "planning": ("规划", "planning"),
    "conditional_generation": ("条件生成", "条件潜在轨迹生成", "conditional", "generation"),
    "benchmark": ("基准", "基准测试", "benchmark"),
    "success_rate": ("成功率", "success rate", "success-rate"),
    "planning_time": ("规划时间", "planning time"),
    "open_source": ("开源", "代码已开放", "code is available", "github"),
    "memory": ("记忆", "memory"),
    "control": ("控制", "control"),
    "attention_window": ("注意力窗口", "attention window"),
    "head_routing": ("头路由", "head routing"),
    "physical_scale": ("物理动作尺度", "物理尺度", "physical action scale", "metric-scale"),
    "distillation": ("蒸馏", "distillation"),
    "real_time": ("实时", "real-time", "real time"),
    "cache": ("缓存", "cache"),
    "starting_view": ("起始视角", "starting view"),
    "generation_quality": ("生成质量", "generation quality", "video quality"),
    "control_fidelity": ("控制精度", "控制保真", "control fidelity"),
    "paper_retrieval": ("获取论文", "最新论文", "retrieve papers", "papers"),
    "code_generation": ("生成代码", "训练代码", "code generation", "generate code"),
    "simulation_training": ("仿真训练", "仿真环境", "simulation training", "simulation"),
    "physical_robot": ("真机", "实体机器人", "physical robot"),
    "evaluation_loop": ("评价", "评估", "闭环", "循环", "evaluation", "feedback loop"),
    "fundraising": ("融资", "投资者", "raise", "investor"),
    "valuation": ("估值", "valuation"),
}

MULTIPLIERS = {
    "百": 1e2, "千": 1e3, "万": 1e4, "亿": 1e8, "万亿": 1e12,
    "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12,
    "k": 1e3, "m": 1e6, "b": 1e9, "t": 1e12,
}


def normalize(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", value.lower())


def shingles(value: str, size: int = 2) -> set[str]:
    text = normalize(value)
    if len(text) < size:
        return {text} if text else set()
    return {text[index:index + size] for index in range(len(text) - size + 1)}


def concept_tokens(value: str) -> set[str]:
    lowered = value.lower()
    return {
        concept
        for concept, aliases in BILINGUAL_CONCEPTS.items()
        if any(alias.lower() in lowered for alias in aliases)
    }


def normalized_numbers(value: str) -> list[dict[str, Any]]:
    numbers: list[dict[str, Any]] = []
    for match in NUMBER_RE.finditer(value):
        raw = match.group(0).strip()
        if not raw:
            continue
        number_match = re.search(r"\d+(?:\.\d+)?", raw)
        if not number_match:
            continue
        amount = float(number_match.group(0))
        lowered = raw.lower().replace(" ", "")
        multiplier = 1.0
        for marker in sorted((item for item in MULTIPLIERS if len(item) > 1 or item in "百千万亿"), key=len, reverse=True):
            if marker in lowered:
                multiplier = MULTIPLIERS[marker]
                break
        short_scale = re.search(r"\d+(?:\.\d+)?\s*([kmbt])(?:\s*(?:美元|人民币|元))?$", lowered)
        if short_scale:
            multiplier = MULTIPLIERS[short_scale.group(1)]
        if "%" in lowered or "％" in lowered:
            kind = "percent"
        elif "$" in raw or "美元" in raw:
            kind = "usd"
        elif any(marker in raw for marker in ("¥", "￥", "人民币", "元")):
            kind = "cny"
        elif any(marker in lowered for marker in ("年", "月", "日", "year", "day")):
            kind = "date"
        elif any(marker in lowered for marker in ("小时", "分钟", "hour", "minute")):
            kind = "duration"
        else:
            kind = "count"
        numbers.append({"raw": raw, "value": amount * multiplier, "kind": kind})
    return numbers


def numbers_match(claim: str, evidence: str) -> bool:
    expected = normalized_numbers(claim)
    if not expected:
        return True
    observed = normalized_numbers(evidence)
    for wanted in expected:
        compatible = [item for item in observed if item["kind"] == wanted["kind"]]
        # A bare count may correspond to a currency/percentage when the unit is
        # stated once around a list. Keep this fallback strict on value.
        if wanted["kind"] == "count":
            compatible += [item for item in observed if item["kind"] not in {"date", "duration"}]
        if not any(math.isclose(wanted["value"], item["value"], rel_tol=1e-9, abs_tol=1e-9) for item in compatible):
            return False
    return True


def split_evidence_units(text: str) -> list[tuple[int, str]]:
    base_units: list[tuple[int, str]] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        parts = [part.strip() for part in re.split(r"(?<=[。！？!?；;])", line) if part.strip()]
        expanded: list[str] = []
        for part in parts:
            if len(part) > 520:
                clauses = [item.strip() for item in re.split(r"(?<=[,，:：])", part) if item.strip()]
                expanded.extend(clauses or [part])
            else:
                expanded.append(part)
        parts = expanded
        base_units.extend((line_number, part) for part in parts)
    units = list(base_units)
    # OCR and PDF extraction often hard-wrap a single sentence across lines.
    # Sliding windows preserve the original order while allowing a claim to
    # match text that was split at an arbitrary visual line boundary.
    for index in range(len(base_units)):
        for width in (2, 3, 4, 5):
            chunk = base_units[index:index + width]
            if len(chunk) == width:
                units.append((chunk[0][0], "".join(item[1] for item in chunk)))
    return units


def evidence_similarity(claim: str, evidence: str) -> float:
    claim_shingles, evidence_shingles = shingles(claim), shingles(evidence)
    overlap = len(claim_shingles & evidence_shingles) / len(claim_shingles) if claim_shingles else 0.0
    sequence = SequenceMatcher(None, normalize(claim), normalize(evidence)).ratio()
    claim_concepts, evidence_concepts = concept_tokens(claim), concept_tokens(evidence)
    concept_coverage = len(claim_concepts & evidence_concepts) / len(claim_concepts) if claim_concepts else 0.0
    number_bonus = 0.12 if normalized_numbers(claim) and numbers_match(claim, evidence) else 0.0
    # Concept coverage is language-independent; lexical measures remain useful
    # for names and Chinese-to-Chinese evidence. A small length penalty avoids
    # selecting an entire article when a focused passage is available.
    base = max(overlap, sequence, concept_coverage * 0.72)
    length_penalty = min(0.10, max(0, len(evidence) - 900) / 9000)
    return min(1.0, max(0.0, base + number_bonus - length_penalty))


def locate_evidence(claim: str, sources: list[dict[str, Any]]) -> dict[str, Any]:
    ranked: list[dict[str, Any]] = []
    for source in sources:
        text = source.get("raw_text") or source.get("raw_excerpt") or ""
        for line_number, unit in split_evidence_units(text):
            score = evidence_similarity(claim, unit)
            ranked.append({
                "source_id": source["source_id"],
                "source_url": source.get("canonical_url"),
                "locator": f"正文第{line_number}行",
                "evidence_text": unit,
                "score": score,
            })
    if not ranked:
        return {
            "source_id": None, "source_url": None, "locator": None,
            "evidence_text": None, "score": 0.0,
        }
    ranked.sort(key=lambda item: (item["score"], -len(item["evidence_text"])), reverse=True)
    best = ranked[0]
    alternatives = []
    seen: set[str] = {normalize(best["evidence_text"])}
    for item in ranked[1:]:
        key = normalize(item["evidence_text"])
        if not key or key in seen:
            continue
        seen.add(key)
        alternatives.append({**item, "score": round(item["score"], 4)})
        if len(alternatives) == 2:
            break
    best["score"] = round(best["score"], 4)
    best["evidence_alternatives"] = alternatives
    return best


def claim_review(claim: str, sources: list[dict[str, Any]], requires_attribution: bool) -> dict[str, Any]:
    evidence = locate_evidence(claim, sources)
    numeric_match = numbers_match(claim, evidence.get("evidence_text") or "")
    evidence_text = evidence.get("evidence_text") or ""
    attribution_required_here = requires_attribution and any(
        marker in evidence_text for marker in UNCERTAIN_EVIDENCE_MARKERS
    )
    attribution_preserved = not attribution_required_here or any(marker in claim for marker in ATTRIBUTION_MARKERS)
    trend_generalization = any(re.search(pattern, claim) for pattern in TREND_PATTERNS) and not any(
        marker in claim for marker in TREND_QUALIFIERS
    )
    score = float(evidence["score"])
    support_status = "supported" if score >= 0.55 and numeric_match else ("partial" if score >= 0.30 else "unsupported")
    risk_flags: list[str] = []
    if not evidence.get("evidence_text"):
        risk_flags.append("evidence_not_found")
    if not numeric_match:
        risk_flags.append("numeric_mismatch")
    if not attribution_preserved:
        risk_flags.append("attribution_missing")
    if trend_generalization:
        risk_flags.append("single_case_generalized_to_trend")
    if support_status != "supported":
        risk_flags.append(f"claim_{support_status}")
    return {
        "claim": claim,
        **evidence,
        "support_status": support_status,
        "numeric_match": numeric_match,
        "attribution_preserved": attribution_preserved,
        "attribution_required": attribution_required_here,
        "risk_flags": sorted(set(risk_flags)),
        "verification_status": "pending_human_review",
    }


def cross_source_check(sources: list[dict[str, Any]]) -> dict[str, Any]:
    if len(sources) < 2:
        return {"status": "not_applicable", "independent_source_count": len(sources), "shared_numbers": []}
    number_sets = [
        {(item["kind"], item["value"]) for item in normalized_numbers(source.get("raw_text") or source.get("raw_excerpt") or "")}
        for source in sources
    ]
    nonempty = [numbers for numbers in number_sets if numbers]
    shared_values = sorted(set.intersection(*nonempty)) if len(nonempty) >= 2 else []
    shared = [f"{kind}:{value:g}" for kind, value in shared_values]
    return {
        "status": "consistent_shared_facts" if shared else "needs_human_comparison",
        "independent_source_count": len(sources),
        "shared_numbers": shared,
        "note": "无共享数字不等于来源冲突，只表示自动规则无法形成跨来源一致性结论。" if not shared else None,
    }


def build_artifacts(collection: dict[str, Any], candidates: dict[str, Any], review: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    source_map = {item["source_id"]: item for item in collection["source_records"]}
    candidate_map = {item["candidate_id"]: item for item in candidates["selected_candidates"]}
    provisional: list[dict[str, Any]] = []
    evidence_records: list[dict[str, Any]] = []

    for review_record in review.get("records", []):
        candidate = candidate_map[review_record["candidate_id"]]
        sources = [source_map[source_id] for source_id in candidate["source_ids"] if source_id in source_map]
        analysis = candidate.get("llm_analysis") or {}
        claims = [str(item).strip() for item in analysis.get("proposed_claims", []) if str(item).strip()]
        if not claims and analysis.get("what"):
            claims = [analysis["what"]]
        gates = candidate.get("hard_gates") or {}
        completeness = (gates.get("content_completeness") or {}).get("status", "unknown")
        fidelity = (gates.get("fact_wording_fidelity") or {}).get("status", "unknown")
        requires_attribution = bool((gates.get("fact_wording_fidelity") or {}).get("requires_attribution"))
        claim_reviews = [claim_review(claim, sources, requires_attribution) for claim in claims]
        all_risks = sorted({risk for item in claim_reviews for risk in item["risk_flags"]})
        supported = sum(item["support_status"] == "supported" and not item["risk_flags"] for item in claim_reviews)
        if completeness != "pass":
            all_risks.append("content_completeness_failed")
        if fidelity != "pass":
            all_risks.append("fact_wording_fidelity_failed")
        if claim_reviews and supported == len(claim_reviews) and not all_risks:
            confidence, recommendation = "high", "keep"
        elif any(item["support_status"] == "unsupported" for item in claim_reviews) or completeness == "fail":
            confidence, recommendation = "low", "reject"
        else:
            confidence, recommendation = "medium", "watch"
        evidence_id = f"evidence_{candidate['candidate_id'].removeprefix('cand_')}"
        evidence_records.append({
            "evidence_review_id": evidence_id,
            "candidate_id": candidate["candidate_id"],
            "title": candidate["canonical_title"],
            "hard_rules": {
                "content_completeness": completeness,
                "fact_wording_fidelity": fidelity,
                "source_present": bool(sources),
            },
            "cross_source_consistency": cross_source_check(sources),
            "claim_reviews": claim_reviews,
            "confidence": confidence,
            "agent_recommendation": recommendation,
            "risk_flags": sorted(set(all_risks)),
            "human_decision": None,
            "status": "waiting_for_human_review",
        })
        provisional.append({
            "candidate_id": candidate["candidate_id"],
            "status": "provisional_unverified",
            "canonical_title": analysis.get("canonical_title") or candidate["canonical_title"],
            "intelligence_type": candidate.get("intelligence_type"),
            "primary_route": candidate.get("primary_route"),
            "source_ids": candidate.get("source_ids", []),
            "what": analysis.get("what"),
            "why": analysis.get("why"),
            "evidence_review_id": evidence_id,
            "confidence": confidence,
            "agent_recommendation": recommendation,
            "human_decision": None,
        })

    summary = {
        "candidate_count": len(provisional),
        "high_confidence": sum(item["confidence"] == "high" for item in evidence_records),
        "medium_confidence": sum(item["confidence"] == "medium" for item in evidence_records),
        "low_confidence": sum(item["confidence"] == "low" for item in evidence_records),
        "requires_human_review": len(evidence_records),
    }
    return (
        {
            "schema_version": "0.1", "record_type": "verification_candidate_bundle",
            "run_id": review.get("run_id"), "status": "waiting_for_human_review",
            "summary": summary, "verification_candidates": provisional,
        },
        {
            "schema_version": "0.1", "record_type": "evidence_review_bundle",
            "run_id": review.get("run_id"), "status": "waiting_for_human_review",
            "summary": summary, "records": evidence_records,
        },
    )


def validate_artifacts(provisional: dict[str, Any], evidence: dict[str, Any]) -> None:
    candidates = provisional.get("verification_candidates", [])
    records = evidence.get("records", [])
    if len(candidates) != len(records):
        raise ValueError("verification candidates and evidence records have different counts")
    if any(item.get("status") != "provisional_unverified" or item.get("human_decision") is not None for item in candidates):
        raise ValueError("verification harness must not approve provisional events")
    if any(item.get("status") != "waiting_for_human_review" or item.get("human_decision") is not None for item in records):
        raise ValueError("evidence review must remain behind the human gate")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", required=True)
    parser.add_argument("--candidates", required=True)
    parser.add_argument("--review", required=True)
    parser.add_argument("--verification-output", required=True)
    parser.add_argument("--evidence-output", required=True)
    args = parser.parse_args()
    collection = json.loads(Path(args.collection).read_text(encoding="utf-8"))
    candidates = json.loads(Path(args.candidates).read_text(encoding="utf-8"))
    review = json.loads(Path(args.review).read_text(encoding="utf-8"))
    provisional, evidence = build_artifacts(collection, candidates, review)
    validate_artifacts(provisional, evidence)
    for path, payload in ((Path(args.verification_output), provisional), (Path(args.evidence_output), evidence)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(evidence["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
