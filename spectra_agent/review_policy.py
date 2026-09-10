#!/usr/bin/env python3
"""Single, deterministic entry point for P1 review and auto-lock eligibility.

This module decides whether a candidate may be auto-locked. It never changes
an existing human decision, never promotes a risky fact, and never controls
publication. Run-level quality evaluation lives in ``run_evaluator.py``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any


NUMBER_RE = re.compile(r"(?:\d[\d,.]*\s*(?:%|％|万|亿|千|百|元|美元|小时|分钟|倍|个|项|条|人|m|km)?)", re.I)
EFFECT_RE = re.compile(r"(?:导致|带来|使得|推动|提升|提高|降低|减少|增长|改善|加速|确保|证明|效果|效率|准确率|成功率|行业趋势)")
CONFIDENCE = {"low": 0.25, "medium": 0.6, "high": 0.9}


def _sampled(run_id: str, candidate_id: str, rate: float, seed: str = "review-policy") -> bool:
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    value = int(hashlib.sha256(f"{seed}:{run_id}:{candidate_id}".encode()).hexdigest()[:8], 16)
    return value / 0xFFFFFFFF < rate


def _confidence(value: Any) -> float:
    if isinstance(value, (int, float)):
        return max(0.0, min(1.0, float(value)))
    return CONFIDENCE.get(str(value or "").lower(), 0.0)


def _check(name: str, passed: bool, reason: str) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "reason": "" if passed else reason}


def _source_map(collection: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    return {
        item.get("source_id"): item
        for item in (collection or {}).get("source_records", [])
        if item.get("source_id")
    }


def assess_review_record(
    record: dict[str, Any],
    candidate: dict[str, Any],
    packet: dict[str, Any],
    config: dict[str, Any] | None,
    collection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return auditable hard/soft/manual checks without mutating inputs."""
    config = config or {}
    facts = record.get("suggested_evidence") or []
    consistency = packet.get("cross_source_consistency") or {}
    hard_rules = packet.get("hard_rules") or {}
    source_ids = set(candidate.get("source_ids") or [])
    source_types = set(candidate.get("source_types") or [])
    source_count = int(consistency.get("independent_source_count") or len(source_ids))
    primary_source_id = candidate.get("primary_source_id") or record.get("source_id") or next(iter(source_ids), None)
    primary_source = _source_map(collection).get(primary_source_id, {})
    primary_valid = bool(primary_source_id and hard_rules.get("source_present") is True)
    if collection is not None:
        primary_valid = primary_valid and bool(primary_source) and primary_source.get("access_status") == "success"
        primary_valid = primary_valid and bool(primary_source.get("canonical_url"))

    supported = bool(facts) and all(fact.get("support_status") == "supported" for fact in facts)
    numeric_consistent = bool(facts) and all(
        fact.get("numeric_match") is True
        if NUMBER_RE.search(str(fact.get("claim") or ""))
        else fact.get("numeric_match") is not False
        for fact in facts
    )
    no_risk = not packet.get("risk_flags") and all(not fact.get("risk_flags") for fact in facts)
    hard_checks = [
        _check("content_complete", hard_rules.get("content_completeness") == "pass", "content_incomplete"),
        _check("wording_faithful", hard_rules.get("fact_wording_fidelity") == "pass", "wording_fidelity_failed"),
        _check("evidence_supported", supported, "unsupported_or_missing_evidence"),
        _check("risk_clear", no_risk, "risk_flag_present"),
        _check("numbers_consistent", numeric_consistent, "numeric_mismatch_or_unchecked"),
        _check("primary_source_valid", primary_valid, "primary_source_invalid"),
    ]

    auto = config.get("auto_lock") or {}
    allowed_types = set(auto.get("allowed_intelligence_types") or config.get("allowed_intelligence_types") or [])
    allowed_sources = set(auto.get("allowed_source_types") or config.get("official_source_types") or [])
    allowed_registry_ids = set(auto.get("allowed_registry_ids") or [])
    intelligence_type = candidate.get("intelligence_type") or (record.get("agent_analysis") or {}).get("intelligence_type")
    confidence = _confidence(packet.get("confidence") or record.get("harness_confidence") or (candidate.get("llm_analysis") or {}).get("confidence"))
    minimum_confidence = _confidence(auto.get("minimum_confidence", "high")) if "auto_lock" in config else 0.0
    allow_single = bool(auto.get("allow_single_primary_source", False))
    max_expansion_depth = int(auto.get("maximum_model_expansion_depth", 1))
    expansion_depth = int(record.get("model_expansion_depth") or packet.get("model_expansion_depth") or 0)
    facts_source_count = len({fact.get("source_id") for fact in facts if fact.get("source_id")})
    source_allowed = bool(source_types & allowed_sources) if allowed_sources else True
    if allowed_registry_ids:
        source_allowed = source_allowed and primary_source.get("registry_id") in allowed_registry_ids
    soft_checks = [
        _check("confidence_allowed", confidence >= minimum_confidence, "confidence_below_auto_threshold"),
        _check("intelligence_type_allowed", not allowed_types or intelligence_type in allowed_types, "intelligence_type_not_auto_allowed"),
        _check("source_allowed", source_allowed, "source_not_auto_allowed"),
        _check("source_count_allowed", allow_single or source_count >= int(config.get("minimum_consistent_sources", 2)), "single_primary_source"),
        _check("expansion_depth_allowed", expansion_depth <= max_expansion_depth, "model_expansion_too_deep"),
    ]

    mandatory = config.get("mandatory_review") or {}
    mandatory_types = set(mandatory.get("intelligence_types") or [])
    mandatory_reasons: list[str] = []
    if intelligence_type in mandatory_types:
        mandatory_reasons.append("mandatory_intelligence_type")
    if mandatory.get("numeric_or_effect_claims", True):
        if any(NUMBER_RE.search(str(fact.get("claim") or "")) for fact in facts):
            mandatory_reasons.append("numeric_claim")
        if any(EFFECT_RE.search(str(fact.get("claim") or "")) for fact in facts):
            mandatory_reasons.append("effect_or_causal_claim")
    if mandatory.get("multi_source_fact_mix", True) and facts_source_count > 1:
        mandatory_reasons.append("multi_source_fact_mix")
    if not no_risk:
        mandatory_reasons.append("risk_flag_present")
    if confidence < minimum_confidence:
        mandatory_reasons.append("low_confidence")
    if not allow_single and source_count < int(config.get("minimum_consistent_sources", 2)):
        mandatory_reasons.append("single_source")
    if expansion_depth > max_expansion_depth:
        mandatory_reasons.append("model_expansion_too_deep")
    mandatory_reasons.extend(check["reason"] for check in hard_checks if not check["passed"])
    mandatory_reasons.extend(check["reason"] for check in soft_checks if not check["passed"])
    mandatory_reasons = list(dict.fromkeys(reason for reason in mandatory_reasons if reason))
    hard_pass = all(item["passed"] for item in hard_checks)
    soft_pass = all(item["passed"] for item in soft_checks)
    return {
        "standard_version": "1.0",
        "hard_conditions": hard_checks,
        "soft_conditions": soft_checks,
        "hard_pass": hard_pass,
        "soft_pass": soft_pass,
        "source_count": source_count,
        "fact_source_count": facts_source_count,
        "confidence": confidence,
        "minimum_auto_confidence": minimum_confidence,
        "mandatory_reasons": mandatory_reasons,
        "auto_lock_eligible": bool(auto.get("enabled", config.get("enabled", False))) and hard_pass and soft_pass and not mandatory_reasons,
    }


def _auto_lock(record: dict[str, Any]) -> None:
    """Lock only an untouched pending record; human decisions always win."""
    if record.get("decision") not in {None, "pending"}:
        return
    record["decision"] = "include"
    record["verification_status"] = "verified_primary"
    record["decision_reason"] = "统一过审标准自动锁定；保留完整证据轨迹供人工抽检。"
    record["decision_source"] = "auto_fact_lock"
    for fact in record.get("suggested_evidence") or []:
        if fact.get("human_fact_decision") in {None, "pending"}:
            fact["human_fact_decision"] = "keep"
            fact["reviewed_by"] = "auto_fact_lock"


def apply_review_policy(
    review: dict[str, Any],
    candidates: dict[str, Any],
    evidence: dict[str, Any],
    config: dict[str, Any] | None,
    collection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify records and auto-lock only the configured, hard-passing subset."""
    config = config or {}
    if not config.get("enabled", False):
        return review
    candidate_map = {item["candidate_id"]: item for item in candidates.get("selected_candidates", [])}
    evidence_map = {item["candidate_id"]: item for item in evidence.get("records", [])}
    counts = {"mandatory_review": 0, "sample_review": 0, "auto_locked": 0}
    run_id = str(review.get("run_id") or "")
    sample_rate = float((config.get("auto_lock") or {}).get("sample_rate", config.get("sample_rate", 0.2)))
    seed = str((config.get("auto_lock") or {}).get("sample_seed", "review-policy"))

    for record in review.get("records", []):
        candidate_id = record["candidate_id"]
        assessment = assess_review_record(
            record,
            candidate_map.get(candidate_id, {}),
            evidence_map.get(candidate_id, {}),
            config,
            collection,
        )
        if not assessment["auto_lock_eligible"]:
            tier = "mandatory_review"
        elif _sampled(run_id, candidate_id, sample_rate, seed):
            tier = "sample_review"
        else:
            tier = "auto_locked"
            _auto_lock(record)
        assessment["tier"] = tier
        assessment["sample_rate"] = sample_rate
        record["review_policy"] = assessment
        counts[tier] += 1

    manual = counts["mandatory_review"] + counts["sample_review"]
    review["review_policy"] = {
        "version": "1.0",
        "mode": "tiered",
        "counts": counts,
        "manual_review_required": manual > 0,
        "manual_review_count": manual,
        "run_gate_preserved": bool(config.get("preserve_run_human_gate", True)),
        "auto_publish_enabled": False,
        "note": "自动锁定仅覆盖达标子集；Run 人工闸门与发布权限保持独立。",
    }
    return review
