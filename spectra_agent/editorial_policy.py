"""Deterministic editorial presentation policy for the rolling digest.

Collection taxonomy stays broad.  This module is the single boundary that
projects verified material into reader-facing channels, tiers and priorities.
"""

from __future__ import annotations

import re
from typing import Any


CHANNELS = (
    "weekly_selection",
    "capability_metrics",
    "model_frontier",
    "product_business",
    "content_culture",
    "team_talent",
)

DIRECT_VISUAL_ROUTES = {
    "visual_value.evaluation",
    "visual_value.spatial_camera",
    "frontier.video_generation",
    "frontier.image_asset",
    "frontier.world_model",
}

PRIMARY_EVIDENCE_TYPES = {
    "paper_report",
    "official_announcement",
    "company_news",
    "financial_report",
    "public_report",
    "code_dataset",
    "product_test",
}

VISUAL_TERMS = re.compile(
    r"视频|图像|影像|视觉|生成|相机|镜头|3d|4d|世界模型|video|image|visual|camera|kling|可灵",
    re.I,
)
CAPABILITY_TERMS = re.compile(
    r"评测|基准|指标|一致性|质量|动作|运动|物理|伪影|提示词|相机|镜头|"
    r"evaluation|benchmark|metric|consisten|quality|motion|artifact|prompt|camera|reward",
    re.I,
)
CONTENT_CULTURE_TERMS = re.compile(
    r"短剧|影视|电影|广告创意|创作者|文化|娱乐|营销内容|creator|film|culture|entertainment",
    re.I,
)
TALENT_TERMS = re.compile(r"人才|招聘|薪酬|期权|团队|组织|hiring|talent|recruit|organization", re.I)
URGENT_TERMS = re.compile(r"今日上线|立即可用|正式开放|重大故障|禁用|召回|监管生效|available now", re.I)


def _text(*values: Any) -> str:
    return " ".join(str(value or "") for value in values)


def source_evidence_strength(source_type: str | None) -> str:
    if source_type in PRIMARY_EVIDENCE_TYPES:
        return "primary_reported"
    if source_type in {"professional_view", "media", "industry_media", "wechat_official_account"}:
        return "secondary_reported"
    return "source_checked"


def visual_relevance(event: dict[str, Any], copy: dict[str, Any] | None = None) -> str:
    copy = copy or {}
    routes = {event.get("primary_route"), *(event.get("secondary_routes") or [])}
    text = _text(
        copy.get("headline"), copy.get("dek"), copy.get("what"), copy.get("how"),
        event.get("canonical_title"), event.get("fact_summary"),
    )
    has_visual_substance = bool(VISUAL_TERMS.search(text))
    if routes & DIRECT_VISUAL_ROUTES and has_visual_substance:
        # A financial filing routed from discovery metadata is only adjacent
        # until the reviewed facts themselves contain a visual-product signal.
        if event.get("intelligence_type") == "type.industry_market" and not re.search(
            r"可灵|kling|视频生成|图像生成|visual generation|video generation", text, re.I
        ):
            return "adjacent"
        return "direct"
    if routes & DIRECT_VISUAL_ROUTES:
        return "adjacent"
    if event.get("primary_route") == "frontier.embodied_ai" or has_visual_substance:
        return "adjacent"
    if event.get("primary_route") in {"extended.talent_organization", "extended.compute_data"}:
        return "weak"
    return "none"


def editorial_channel(event: dict[str, Any], copy: dict[str, Any] | None = None) -> str:
    copy = copy or {}
    text = _text(copy.get("headline"), copy.get("dek"), copy.get("what"), event.get("canonical_title"))
    title_text = _text(copy.get("headline"), event.get("canonical_title"))
    route = str(event.get("primary_route") or "")
    secondary = set(event.get("secondary_routes") or [])
    intelligence_type = event.get("intelligence_type")
    if route == "visual_value.evaluation" or (
        "visual_value.evaluation" in secondary and re.search(r"reward|评测|基准|指标|evaluation|benchmark|metric", title_text, re.I)
    ) or re.search(r"reward|评测|基准|指标", title_text, re.I):
        return "capability_metrics"
    if TALENT_TERMS.search(text) and route == "extended.talent_organization":
        return "team_talent"
    if CONTENT_CULTURE_TERMS.search(text):
        return "content_culture"
    if intelligence_type in {"type.industry_market", "type.company_strategy"}:
        return "product_business"
    if route in DIRECT_VISUAL_ROUTES or secondary & DIRECT_VISUAL_ROUTES:
        return "model_frontier"
    if intelligence_type == "type.product_release":
        return "product_business"
    return "model_frontier"


def capability_dimensions(event: dict[str, Any], copy: dict[str, Any] | None = None) -> list[str]:
    copy = copy or {}
    text = _text(copy.get("headline"), copy.get("dek"), copy.get("what"), copy.get("how"), event.get("fact_summary"))
    rules = (
        ("motion_smoothness", r"运动质量|动作|运动|motion"),
        ("temporal_consistency", r"时间|长程|时序|跨镜头|temporal|long-range"),
        ("prompt_adherence", r"指令|提示词|prompt|instruction"),
        ("artifact_risk", r"伪影|崩坏|artifact"),
        ("camera_control", r"相机|镜头|camera"),
        ("character_consistency", r"角色|身份一致|character|identity"),
        ("physical_plausibility", r"物理|因果|physical|causal"),
        ("generation_efficiency", r"速度|成本|效率|推理|speed|cost|latency|inference"),
        ("visual_quality", r"视觉质量|画质|外观质量|高保真|visual quality|fidelity"),
    )
    return [name for name, pattern in rules if re.search(pattern, text, re.I)]


def article_character_count(story: dict[str, Any]) -> int:
    body = ((story.get("article_body") or {}).get("full_text") or {}).get("text", "")
    return len(re.sub(r"\s+", "", str(body)))


def classify_story(
    story: dict[str, Any],
    event: dict[str, Any],
    *,
    source_type: str | None,
    selected: bool,
    writer_draft: bool,
) -> dict[str, Any]:
    copy = {
        "headline": story.get("headline"),
        "dek": story.get("dek"),
        "what": ((story.get("article_body") or {}).get("full_text") or {}).get("text"),
        "how": ((story.get("under_the_hood") or {}).get("text")),
    }
    relevance = visual_relevance(event, copy)
    evidence = source_evidence_strength(source_type)
    dimensions = capability_dimensions(event, copy) if relevance == "direct" else []
    impact = "high" if relevance == "direct" and (
        event.get("primary_route") in DIRECT_VISUAL_ROUTES or dimensions
    ) else "medium" if relevance in {"direct", "adjacent"} else "low"
    complete = article_character_count(story) >= 260
    fact_point_count = len((story.get("article_body") or {}).get("fact_points") or [])
    # A broad financial filing can mention a visual-AI business without
    # providing enough reviewed operating detail for a core event. Keep it as
    # an industry signal until the fact envelope contains real depth; prose
    # length alone must not promote it.
    core_depth_ready = source_type != "financial_report" or fact_point_count >= 6
    actionable = bool(
        str(story.get("one_line_takeaway") or "").strip()
        or str((((story.get("article_body") or {}).get("judgment") or {}).get("text")) or "").strip()
        or story.get("watch_next")
    )
    core_ready = selected and relevance == "direct" and impact == "high" and complete and actionable and evidence == "primary_reported" and core_depth_ready
    tier = "core_event" if core_ready else "industry_signal" if relevance in {"direct", "adjacent"} else "brief"
    text = _text(story.get("headline"), story.get("dek"), copy.get("what"))
    priority = "p0" if core_ready and URGENT_TERMS.search(text) else "p1" if core_ready or (relevance == "direct" and impact == "high") else "p2" if tier == "industry_signal" else "p3"
    return {
        "visual_relevance": relevance,
        "strategic_impact": impact,
        "evidence_strength": evidence,
        "actionable": actionable,
        "content_complete": complete,
        "editorial_tier": tier,
        "article_type": "core_event" if tier == "core_event" else "brief",
        "content_format": (
            "full_analysis" if tier == "core_event" and writer_draft
            else "compact_analysis" if tier == "core_event"
            else "signal_analysis" if tier == "industry_signal"
            else "source_brief"
        ),
        "editorial_priority": f"priority.{priority}",
        "editorial_channel": editorial_channel(event, copy),
        "capability_dimensions": dimensions,
    }


def should_attempt_core_writer(
    event: dict[str, Any], fact_plan: dict[str, Any], source_type: str | None
) -> bool:
    if not event.get("primary_route") and not event.get("secondary_routes"):
        return source_type not in {"professional_view", "media", "industry_media", "wechat_official_account"}
    reader = fact_plan.get("reader_packet") or {}
    facts = reader.get("fact_units") or []
    copy = {"what": " ".join(str(item.get("text") or "") for item in facts)}
    return (
        visual_relevance(event, copy) == "direct"
        and source_evidence_strength(source_type) == "primary_reported"
    )


def classify_brief(candidate: dict[str, Any], source_type: str | None, headline: str, dek: str) -> dict[str, Any]:
    event = {
        "primary_route": candidate.get("primary_route"),
        "secondary_routes": candidate.get("secondary_routes") or [],
        "intelligence_type": candidate.get("intelligence_type"),
        "canonical_title": candidate.get("canonical_title"),
        "fact_summary": dek,
    }
    copy = {"headline": headline, "dek": dek, "what": dek}
    relevance = visual_relevance(event, copy)
    return {
        "visual_relevance": relevance,
        "strategic_impact": "medium" if relevance in {"direct", "adjacent"} else "low",
        "evidence_strength": source_evidence_strength(source_type),
        "editorial_tier": "brief",
        "content_format": "compact_analysis",
        "editorial_priority": "priority.p2",
        "editorial_channel": editorial_channel(event, copy),
        "capability_dimensions": capability_dimensions(event, copy),
    }
