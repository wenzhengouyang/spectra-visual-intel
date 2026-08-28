"""Deterministic, explainable trend scoring for SPECTRA editorial issues."""

from __future__ import annotations

import json
from pathlib import Path
from statistics import mean
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES_PATH = ROOT / "editorial" / "trend_rules.v0.1.json"

TYPE_LABELS = {
    "type.industry_market": "行业与市场",
    "type.product_release": "产品发布",
    "type.company_strategy": "公司动作",
    "type.technology_breakthrough": "技术突破",
}

COMPONENT_LABELS = {
    "signal_volume": "信号数量",
    "evidence_quality": "证据质量",
    "source_corroboration": "多源印证",
    "importance": "事件重要度",
    "strategy_relevance": "策略相关性",
    "novelty": "新增程度",
}


def load_rules(path: Path = DEFAULT_RULES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text())


def _average(values: list[float], fallback: float) -> float:
    return mean(values) if values else fallback


def _analysis(review: dict[str, Any]) -> dict[str, Any]:
    return review.get("agent_analysis") or {}


def score_trend(
    route: str,
    events: list[dict[str, Any]],
    reviews_by_event: dict[str, dict[str, Any]],
    rules: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a 0-100 heat score, radar coordinates, and auditable breakdown.

    Only verified events are accepted by the caller. LLM-assigned importance,
    novelty and relevance remain visible as judgment inputs rather than being
    presented as source facts.
    """
    rules = rules or load_rules()
    count = len(events)
    volume_raw = min(count / 3, 1) * 100

    evidence_values = []
    corroboration_values = []
    importance_values = []
    relevance_values = []
    novelty_values = []
    productization_values = []
    classification_types = []
    classification_reasons = []

    for event in events:
        review = reviews_by_event.get(event["event_id"], {})
        analysis = _analysis(review)
        evidence_base = rules["evidence_values"].get(event.get("evidence_level"), 50)
        confidence = rules["confidence_multipliers"].get(event.get("confidence"), 0.65)
        evidence_values.append(evidence_base * confidence)
        corroboration_values.append(min(max(event.get("independent_source_count", 1), 0), 3) / 3 * 100)
        importance_values.append(float(analysis.get("importance_score", 60)))
        relevance_values.append(float(analysis.get("strategy_relevance_score", 60)))
        novelty_values.append(float(analysis.get("novelty_score", 60)))
        intelligence_type = analysis.get("intelligence_type", "type.technology_breakthrough")
        classification_types.append(intelligence_type)
        productization_values.append(rules["productization_values"].get(intelligence_type, 35))
        reason = analysis.get("intelligence_type_reason")
        if reason:
            classification_reasons.append(reason)

    raw = {
        "signal_volume": volume_raw,
        "evidence_quality": _average(evidence_values, 50),
        "source_corroboration": _average(corroboration_values, 0),
        "importance": _average(importance_values, 60),
        "strategy_relevance": _average(relevance_values, 60),
        "novelty": _average(novelty_values, 60),
    }
    breakdown = []
    for key, weight in rules["heat_weights"].items():
        points = raw[key] * weight / 100
        breakdown.append({
            "key": key,
            "label": COMPONENT_LABELS[key],
            "raw_score": round(raw[key]),
            "weight": weight,
            "points": round(points, 1),
        })
    heat_score = round(sum(item["points"] for item in breakdown))

    maturity = round(
        raw["evidence_quality"] * rules["maturity_weights"]["evidence_quality"] / 100
        + raw["source_corroboration"] * rules["maturity_weights"]["source_corroboration"] / 100
        + _average(productization_values, 35) * rules["maturity_weights"]["productization"] / 100
    )
    impact = round(
        raw["importance"] * rules["impact_weights"]["importance"] / 100
        + raw["strategy_relevance"] * rules["impact_weights"]["strategy_relevance"] / 100
        + raw["signal_volume"] * rules["impact_weights"]["signal_volume"] / 100
    )

    thresholds = rules["status_thresholds"]
    if heat_score >= thresholds["重点升温"] and count >= 2:
        status = "重点升温"
    elif heat_score >= thresholds["升温"]:
        status = "升温"
    elif heat_score >= thresholds["待验证"]:
        status = "待验证"
    else:
        status = "早期观察"

    type_counts = {kind: classification_types.count(kind) for kind in sorted(set(classification_types))}
    return {
        "model_version": rules["version"],
        "score": heat_score,
        "status": status,
        "maturity": max(0, min(100, maturity)),
        "impact": max(0, min(100, impact)),
        "breakdown": breakdown,
        "coordinate_explanation": {
            "maturity": "证据质量45%＋多源印证20%＋产品化程度35%",
            "impact": "事件重要度35%＋策略相关性45%＋信号数量20%",
        },
        "classification_summary": {
            "primary_route": route,
            "intelligence_type_counts": type_counts,
            "intelligence_type_labels": [TYPE_LABELS.get(kind, kind) for kind in type_counts],
            "reasons": classification_reasons[:3],
            "rule": "一级类型回答信息主要性质；二级方向回答它属于哪个视觉领域。公司身份和发布平台不作为一级分类依据。",
        },
    }
