#!/usr/bin/env python3
"""Filter, tag, deduplicate, aggregate, and shortlist source_record candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectra_agent.llm_client import LLMConfigurationError, LLMProviderError, StructuredLLM, create_llm_client


ROUTES = [
    "visual_value.evaluation",
    "frontier.video_generation",
    "frontier.world_model",
    "frontier.embodied_ai",
    "frontier.image_asset",
    "visual_value.spatial_camera",
    "frontier.multimodal_agent",
]

INTELLIGENCE_TYPES = [
    "type.technology_breakthrough",
    "type.product_release",
    "type.industry_market",
    "type.company_strategy",
]


LLM_ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "analyses": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "candidate_id": {"type": "string"},
                    "canonical_title": {"type": "string"},
                    "intelligence_type": {"type": "string", "enum": INTELLIGENCE_TYPES},
                    "intelligence_type_reason": {"type": "string"},
                    "primary_route": {"type": "string", "enum": ROUTES},
                    "secondary_routes": {"type": "array", "items": {"type": "string", "enum": ROUTES}},
                    "track": {"type": "string", "enum": ["track.fixed", "track.emerging"]},
                    "same_event_group": {"type": "string"},
                    "what": {"type": "string"},
                    "why": {"type": "string"},
                    "importance_score": {"type": "integer", "minimum": 0, "maximum": 100, "description": "0-100分。50为一般候选，70以上为本周重要，85以上仅用于行业级重大变化。"},
                    "novelty_score": {"type": "integer", "minimum": 0, "maximum": 100, "description": "0-100分。衡量相对已知方法的新增程度，不得使用1-10量表。"},
                    "strategy_relevance_score": {"type": "integer", "minimum": 0, "maximum": 100, "description": "0-100分。衡量对视频/图像模型产品策略的直接相关性。"},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                    "proposed_claims": {"type": "array", "items": {"type": "string"}},
                    "missing_evidence": {"type": "array", "items": {"type": "string"}},
                    "verification_questions": {"type": "array", "items": {"type": "string"}},
                    "recommended_disposition": {"type": "string", "enum": ["p1", "p2", "watch", "reject"]},
                    "disposition_reason": {"type": "string"},
                    "source_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "candidate_id", "canonical_title", "intelligence_type", "intelligence_type_reason",
                    "primary_route", "secondary_routes", "track",
                    "same_event_group", "what", "why", "importance_score", "novelty_score",
                    "strategy_relevance_score", "confidence", "proposed_claims", "missing_evidence",
                    "verification_questions", "recommended_disposition", "source_ids",
                    "disposition_reason",
                ],
            },
        }
    },
    "required": ["analyses"],
}


LLM_INSTRUCTIONS = """你是SPECTRA视觉行业情报Agent的结构化分析节点，面向AI产品策略从业者。
只分析输入中给出的候选与来源摘录，不补充外部事实，不把推测写成事实。
重点覆盖视频生成、图像与资产、世界模型、具身智能应用、评测标准、空间与运镜控制。
每条候选必须先判断一级情报性质intelligence_type，只能选择以下一种：
- type.technology_breakthrough：论文、模型方法、数据集、训练推理、能力突破、评测与技术指标；
- type.product_release：模型或产品发布、功能更新、API与定价、开源权重；
- type.industry_market：行业规模、财报研报、融资、用户与收入、商业化、短剧/营销/游戏等应用生态。
- type.company_strategy：大厂或重点公司的战略调整、合作收购、组织变化、资本投入与生态布局。
判断依据是“这条信息主要回答什么问题”，不是发布平台。公众号文章可以属于任意一类，论文通常但不必然属于技术突破。
公司身份也不是分类依据：同一家大厂的财报收入、市场采用和商业化数据归入industry_market；模型/产品上线与功能更新归入product_release；
投资、组织、合作和战略调整归入company_strategy。例如腾讯财报不因主体是腾讯就自动归入“大厂动态”，必须按本条信息的主问题分类。
intelligence_type_reason必须用一句中文说明为何归入该一级类别，并指出来源中支持判断的信号。
primary_route和secondary_routes继续表示二级领域，不得用它们替代一级情报性质。
canonical_title可以保留原文，除此之外所有自然语言字段必须使用简洁中文。
What必须简述来源明确写出的事实；Why必须说明这些已知事实对视觉模型能力、产品、内容或商业应用的意义，不得自行增加来源未提到的行业、场景或效果。
Why必须落到以下至少一种策略意义：能力边界、评测体系、产品功能、模型优化、内容创意或商业应用。仍需核验时使用“若原文成立/需要确认”，不要把推断冒充结论。
source_ids只能使用候选中提供的ID。证据不足时写入missing_evidence并降低confidence。
当前输入通常只是来源摘要而非人工核验后的全文：默认confidence不得高于medium；除非摘要已经提供可定位证据，否则missing_evidence至少列出需要回原文确认的指标、对比或限制。
proposed_claims必须是来源摘录直接支持、可在原文核验的事实，不得把你的Why判断写成事实主张。
不得输出字段名、占位符、拼写错误片段或元指令作为claim。模型名、方法名和数字必须逐字匹配来源摘录。
missing_evidence必须写成核验缺口，例如“需定位原文表格确认具体提升值”“需确认对比基线和限制”，不得写成泛泛的未来研究问题。
importance_score、novelty_score、strategy_relevance_score严格使用0-100量表：50为一般候选，70以上为较强信号，85以上只用于行业级重大变化；禁止把1-10分直接填入。
same_event_group用于建议跨候选事件聚合；没有可合并项时使用candidate_id本身。
recommended_disposition必须与分数和证据一致：p1=高相关且应优先原文核验；p2=相关但优先级稍低；watch=证据或影响暂弱；reject=明显不相关或无有效事实。重要度或策略相关性达到70且来源为一手论文摘要时，除非明确说明矛盾原因，不得判reject。
disposition_reason用一句中文解释该决策，必须同时提及策略相关性与当前证据边界。
输出必须完整覆盖每个输入candidate_id，且每个candidate_id只能出现一次。"""


def normalize(text: str) -> str:
    text = text.lower().replace("$", "")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def tokens(text: str) -> set[str]:
    stop = {"a", "an", "the", "for", "from", "of", "to", "with", "via", "and", "in", "on", "towards"}
    return {token for token in normalize(text).split() if token not in stop and len(token) > 1}


def similarity(a: str, b: str) -> float:
    ta, tb = tokens(a), tokens(b)
    jaccard = len(ta & tb) / len(ta | tb) if ta | tb else 0
    sequence = SequenceMatcher(None, normalize(a), normalize(b)).ratio()
    return max(jaccard, sequence)


def matches(text: str, terms: list[str]) -> list[str]:
    lowered = text.lower()
    return [term for term in terms if term.lower() in lowered]


def classify_intelligence_type(records: list[dict[str, Any]], text: str,
                               config: dict[str, Any]) -> tuple[str, list[str]]:
    """Return a deterministic first-pass editorial type and auditable signals.

    Content markers dominate, while source type is only a prior. This keeps a
    WeChat article about a paper in technology and a company-authored market
    report in industry/market instead of equating platform with category.
    """
    lowered = text.lower()
    scores = {kind: 0 for kind in INTELLIGENCE_TYPES}
    signals: dict[str, list[str]] = {kind: [] for kind in INTELLIGENCE_TYPES}
    for kind, terms in config["intelligence_type_rules"].items():
        hits = sorted({term for term in terms if term.lower() in lowered})
        scores[kind] += len(hits) * 2
        signals[kind].extend(f"content:{term}" for term in hits)
    for record in records:
        source_type = record.get("source_type")
        for kind, boost in config["intelligence_type_source_boosts"].get(source_type, {}).items():
            scores[kind] += boost
            signals[kind].append(f"source_type:{source_type}")
    precedence = {kind: index for index, kind in enumerate(config["intelligence_type_precedence"])}
    ranked = sorted(INTELLIGENCE_TYPES, key=lambda kind: (-scores[kind], precedence.get(kind, 99)))
    selected = ranked[0]
    return selected, signals[selected]


def route_scores(text: str, config: dict[str, Any]) -> tuple[dict[str, int], dict[str, list[str]]]:
    scores, hits = {}, {}
    lowered = text.lower()
    for route, rules in config["route_rules"].items():
        route_hits = [term for term in rules if term in lowered]
        if route_hits:
            scores[route] = sum(rules[term] for term in route_hits)
            hits[route] = route_hits
    return scores, hits


def atomic_tags(text: str, config: dict[str, Any], primary_route: str) -> dict[str, list[str]]:
    lowered = text.lower()
    tech_by_route = {
        "frontier.video_generation": "tech.video_generation",
        "frontier.image_asset": "tech.image_generation",
        "frontier.world_model": "tech.world_model",
        "frontier.embodied_ai": "tech.embodied_ai",
        "frontier.multimodal_agent": "tech.agent",
        "visual_value.evaluation": "tech.multimodal",
        "visual_value.spatial_camera": "tech.video_generation"
    }
    tech = {tech_by_route[primary_route]}
    for phrase, tag in [("video", "tech.video_generation"), ("image generation", "tech.image_generation"),
                        ("world model", "tech.world_model"), ("world-model", "tech.world_model"),
                        ("robot", "tech.embodied_ai"), ("embodied", "tech.embodied_ai")]:
        if phrase in lowered:
            tech.add(tag)
    caps = [tag for tag, terms in config["capability_rules"].items() if any(term in lowered for term in terms)]
    tasks = [tag for tag, terms in config["task_rules"].items() if any(term in lowered for term in terms)]
    scenes = []
    if "robot" in lowered or "embodied" in lowered or "vision-language-action" in lowered:
        scenes.append("scene.robotics")
    if "game" in lowered or "interactive world" in lowered or "virtual" in lowered:
        scenes.append("scene.virtual_environment")
    if not scenes:
        scenes.append("scene.general_visual_ai")
    return {"tech": sorted(tech), "task": sorted(tasks), "capability": sorted(caps), "scene": scenes,
            "event": ["event.repo_update" if "github" in lowered else "event.research"]}


def score_record(record: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    title = record["raw_title"]
    excerpt = record.get("raw_excerpt") or ""
    context = record.get("discovery_context") or ""
    evidence_text = " ".join([title, excerpt])
    # Financial-report headlines are often generic (for example "Quarterly
    # Results"). Their configured company focus can help route the filing to a
    # visual-domain reviewer, but it must never become evidence for editorial
    # type, tags, claims, What, or Why. Other source types receive no routing
    # boost from discovery metadata.
    routing_context = context if record.get("source_type") == "financial_report" else ""
    title_lower = title.lower()
    hard = matches(title, config["hard_exclude_title_terms"])
    title_scores, title_hits = route_scores(title, config)
    excerpt_scores, excerpt_hits = route_scores(excerpt, config)
    context_scores, context_hits = route_scores(routing_context, config)
    scores = {}
    hits = {}
    for route in set(title_scores) | set(excerpt_scores) | set(context_scores):
        title_signal = max((config["route_rules"][route][term] for term in title_hits.get(route, [])), default=0)
        title_breadth = min(3, max(0, len(title_hits.get(route, [])) - 1))
        excerpt_signal = min(2, max((config["route_rules"][route][term] for term in excerpt_hits.get(route, [])), default=0) // 4)
        context_signal = min(1, max((config["route_rules"][route][term] for term in context_hits.get(route, [])), default=0) // 4)
        scores[route] = title_signal + title_breadth + excerpt_signal + context_signal
        evidence_hits = title_hits.get(route, []) + excerpt_hits.get(route, [])
        context_only_hits = [f"routing_context:{term}" for term in context_hits.get(route, [])]
        hits[route] = sorted(set(evidence_hits + context_only_hits))
    soft_hits = {term: value for term, value in config["soft_negative_terms"].items() if term in evidence_text.lower()}
    boosts = {term: value for term, value in config["priority_boosts"].items() if term in evidence_text.lower()}
    if record["source_name"].startswith("Wan-"):
        scores["frontier.video_generation"] = max(scores.get("frontier.video_generation", 0), 8)
        hits.setdefault("frontier.video_generation", []).append("fixed_watchlist:Wan")
        boosts["track.fixed"] = 4
    precedence = {route: index for index, route in enumerate(config["primary_route_precedence"])}
    explicit_routes = [route for route in config["primary_route_precedence"]
                       if any(marker in title_lower for marker in config["primary_route_markers"].get(route, []))]
    title_ranked = explicit_routes + [route for route in sorted(title_scores, key=lambda route: (-max(config["route_rules"][route][term] for term in title_hits[route]), precedence.get(route, 99))) if route not in explicit_routes]
    ranked = title_ranked + [route for route in sorted(scores, key=lambda route: (-scores[route], precedence.get(route, 99))) if route not in title_ranked]
    primary = ranked[0] if ranked else None
    secondary = ranked[1:4]
    base = scores.get(primary, 0) if primary else 0
    cross_route_bonus = min(3, max(0, len(ranked) - 1))
    total = base + cross_route_bonus + max(boosts.values(), default=0) + sum(soft_hits.values())
    intelligence_type, _ = classify_intelligence_type([record], evidence_text, config)
    total += int(config.get("intelligence_type_priority_boosts", {}).get(intelligence_type, 0))
    if record["source_type"] in {
        "paper_report", "official_announcement", "code_dataset", "financial_report",
        "company_news", "public_report",
    }:
        total += 2
    if title_lower in {"update readme md", "fix author list in readme md", "update links", "fix arxiv link in readme md", "fix arxiv badge link in readme", "fix arxiv badge link in readme md", "revise citation for wan animate 2 in readme", "add hai xu as a contributor in readme md"}:
        total -= 8
    return {"record": record, "hard_exclude": hard, "soft_negative_hits": soft_hits,
            "route_scores": scores, "route_hits": hits, "primary_route": primary,
            "secondary_routes": secondary, "boosts": boosts, "score": total}


def near_duplicate_groups(scored: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    for item in sorted(scored, key=lambda x: (-x["score"], x["record"]["raw_title"])):
        placed = False
        for group in groups:
            a, b = item["record"]["raw_title"], group[0]["record"]["raw_title"]
            same_publisher = item["record"].get("publisher") == group[0]["record"].get("publisher")
            if same_publisher and similarity(a, b) >= 0.9:
                group.append(item)
                placed = True
                break
        if not placed:
            groups.append([item])
    return groups


def aggregate(scored: list[dict[str, Any]]) -> list[dict[str, Any]]:
    github = [item for item in scored if item["record"]["source_name"] == "Wan-Animate-2 GitHub Commits"]
    others = [item for item in scored if item not in github]
    groups = near_duplicate_groups(others)
    if github:
        groups.append(sorted(github, key=lambda x: x["record"]["published_at"] or ""))
    candidates = []
    for group in groups:
        best = max(group, key=lambda x: x["score"])
        records = [item["record"] for item in group]
        is_github_cluster = records[0]["source_name"] == "Wan-Animate-2 GitHub Commits"
        title = "Wan-Animate-2 repository opened and documented" if is_github_cluster else best["record"]["raw_title"]
        cid = "cand_" + hashlib.sha256((title + (records[0]["published_at"] or "")).encode()).hexdigest()[:16]
        # Only source-authored title/excerpt content may determine editorial
        # type and tags. Discovery metadata is deliberately excluded.
        combined_text = " ".join([
            r["raw_title"] + " " + (r.get("raw_excerpt") or "") for r in records
        ])
        tags = atomic_tags(combined_text, CONFIG, best["primary_route"])
        intelligence_type, type_signals = classify_intelligence_type(records, combined_text, CONFIG)
        candidates.append({
            "candidate_id": cid,
            "status": "needs_verification",
            "canonical_title": title,
            "intelligence_type": intelligence_type,
            "intelligence_type_signals": type_signals,
            "primary_route": best["primary_route"],
            "secondary_routes": best["secondary_routes"],
            "score": best["score"] + (3 if is_github_cluster else 0),
            "track": "track.fixed" if is_github_cluster else "track.emerging",
            "tags": tags,
            "primary_source_id": best["record"]["source_id"],
            "source_ids": [r["source_id"] for r in records],
            "source_urls": [r["canonical_url"] for r in records],
            "published_at": min((r["published_at"] for r in records if r["published_at"]), default=None),
            "matched_signals": sorted({term for item in group for terms in item["route_hits"].values() for term in terms}),
            "negative_signals": sorted({term for item in group for term in item["soft_negative_hits"]}),
            "aggregation": {
                "source_count": len(records),
                "method": "fixed_repository_weekly_cluster" if is_github_cluster else ("near_title_dedupe" if len(records) > 1 else "single_source"),
                "note": "多个提交仅构成一个仓库动态候选，不按多条新闻计数。" if is_github_cluster else None
            },
            "verification_questions": verification_questions(
                best["primary_route"],
                is_github_cluster,
                source_types={record.get("source_type") for record in records},
            )
        })
    return candidates


def verification_questions(route: str, repo: bool,
                           source_types: set[str | None] | None = None) -> list[str]:
    if repo:
        return ["仓库是否在本周正式首次开源？", "哪些提交属于能力或代码变化，而非文档维护？", "是否存在官方发布说明或模型权重？"]
    source_types = source_types or set()
    if "financial_report" in source_types:
        return [
            "核心经营数据、同比口径与统计周期能否定位到财报原文页码或表格？",
            "AI、视频或商业化指标具体衡量收入、支出、使用量还是内容供给？",
            "哪些内容是公司披露的事实，哪些只是对行业影响的推断？",
        ]
    if "public_report" in source_types:
        return [
            "报告的样本、方法、统计周期和适用范围是什么？",
            "行业规模、增长率或采用率的单位与原始表格能否定位？",
            "哪些是报告结论，哪些是基于报告数据做出的趋势预测？",
        ]
    if "company_news" in source_types or "official_announcement" in source_types:
        return [
            "该能力已经正式上线、有限开放，还是仍处于预告或合作规划阶段？",
            "官方原文明确披露了哪些产品范围、合作对象和可用条件？",
            "是否有用户、收入、采用率或效果数据支持其商业影响？",
        ]
    common = ["论文原文中的核心新增事实是什么？", "关键指标、数据规模与限制是否能定位到原文？"]
    if route == "visual_value.evaluation":
        common.append("评测任务和指标能否对应真实视频/图像产品任务？")
    elif route == "frontier.world_model":
        common.append("是否真正建模空间、时间、物理或交互，而非泛化使用“世界模型”概念？")
    elif route == "frontier.embodied_ai":
        common.append("结果来自真实机器人、仿真还是离线数据？")
    elif route == "frontier.video_generation":
        common.append("对时长、一致性、镜头或控制的提升是否有可比实验？")
    return common


def select(candidates: list[dict[str, Any]], config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    selected = []
    route_counts = Counter()
    type_counts = Counter()
    eligible = sorted((c for c in candidates if c["primary_route"] and c["score"] >= config["minimum_score"]),
                      key=lambda c: (-c["score"], c["canonical_title"]))

    def add(candidate: dict[str, Any], reason: str) -> None:
        candidate["selection_reason"] = reason
        selected.append(candidate)
        route_counts[candidate["primary_route"]] += 1
        type_counts[candidate["intelligence_type"]] += 1

    def within_caps(candidate: dict[str, Any], *, enforce_type: bool = True,
                    enforce_route: bool = True) -> bool:
        if enforce_type and type_counts[candidate["intelligence_type"]] >= config.get(
            "intelligence_type_caps", {}
        ).get(candidate["intelligence_type"], config["target_count"]):
            return False
        if enforce_route and route_counts[candidate["primary_route"]] >= config["route_caps"].get(
            candidate["primary_route"], config["target_count"]
        ):
            return False
        return True

    verified = set(config["expected_fixture_titles"])
    pinned = [candidate for candidate in eligible if candidate["canonical_title"] in verified]
    for candidate in pinned:
        add(candidate, "previously_verified_regression_sample")

    # First establish the editorial mix requested by the product: technology,
    # product, market/application, and company strategy are peer dimensions.
    for intelligence_type, minimum in config.get("intelligence_type_minimums", {}).items():
        for candidate in (
            c for c in eligible
            if c["intelligence_type"] == intelligence_type and c not in selected
        ):
            if type_counts[intelligence_type] >= minimum or len(selected) >= config["target_count"]:
                break
            if within_caps(candidate, enforce_type=True, enforce_route=False):
                add(candidate, "intelligence_type_minimum_quota")

    # Then preserve coverage of video, world models, embodied AI, evaluation,
    # image assets, and spatial/camera control inside the editorial mix.
    for route, minimum in config.get("route_minimums", {}).items():
        for candidate in (c for c in eligible if c["primary_route"] == route and c not in selected):
            if route_counts[route] >= minimum or len(selected) >= config["target_count"]:
                break
            # Route minimums are coverage guarantees. They may exceed an
            # intelligence-type cap by the smallest necessary amount; without
            # this exception a paper-heavy week can silently remove the only
            # available image/spatial candidate.
            if within_caps(candidate, enforce_type=False, enforce_route=True):
                add(candidate, "route_minimum_quota")

    for candidate in eligible:
        if candidate in selected:
            continue
        if len(selected) < config["target_count"] and within_caps(candidate):
            add(candidate, "score_and_balanced_quota")

    # A short window may not contain enough non-technical items. Meet only the
    # minimum review-pool size while preserving type caps; never use surplus
    # papers merely to hit the nominal target of 25.
    minimum_count = int(config.get("minimum_candidate_count", 20))
    for candidate in eligible:
        if len(selected) >= minimum_count:
            break
        if candidate not in selected and within_caps(candidate, enforce_type=False):
            add(candidate, "minimum_count_fill")

    # A route quota may temporarily exceed an intelligence-type cap when it
    # admits the only available candidate for an under-covered visual domain.
    # Rebalance by removing the lowest-scoring surplus candidate of that same
    # type, provided its own route remains above the configured minimum. This
    # preserves both editorial-type caps and reachable route coverage.
    for intelligence_type, cap in config.get("intelligence_type_caps", {}).items():
        while type_counts[intelligence_type] > cap:
            removable = sorted(
                (
                    candidate for candidate in selected
                    if candidate["intelligence_type"] == intelligence_type
                    and candidate["selection_reason"] != "previously_verified_regression_sample"
                    and route_counts[candidate["primary_route"]] > config.get(
                        "route_minimums", {}
                    ).get(candidate["primary_route"], 0)
                ),
                key=lambda candidate: (candidate["score"], candidate["canonical_title"]),
            )
            if not removable:
                break
            candidate = removable[0]
            selected.remove(candidate)
            route_counts[candidate["primary_route"]] -= 1
            type_counts[intelligence_type] -= 1
            candidate["selection_reason"] = "rebalanced_to_overflow"

    overflow = [candidate for candidate in eligible if candidate not in selected]
    for candidate in selected:
        candidate["verification_priority"] = "priority.p1" if candidate["selection_reason"] == "previously_verified_regression_sample" or candidate["track"] == "track.fixed" or candidate["score"] >= 16 else "priority.p2"
        candidate["why_candidate"] = candidate_rationale(candidate)
    return selected, overflow


def candidate_rationale(candidate: dict[str, Any]) -> str:
    if candidate["selection_reason"] == "previously_verified_regression_sample":
        return "该信号已在真实样刊中完成二轮核验，本轮用于检验自动链路能否稳定召回。"
    if candidate["track"] == "track.fixed":
        return "固定关注仓库在本周出现集中更新，需要确认是否构成正式开源、模型发布或能力变化。"
    route_text = {
        "visual_value.evaluation": "可能补充视频/图像模型的评测维度、指标或诊断方法",
        "frontier.video_generation": "可能改变视频生成在时长、一致性、控制或生产效率上的能力边界",
        "frontier.world_model": "可能提供空间、时间、物理或交互世界建模的新方法",
        "frontier.embodied_ai": "可能影响视觉模型到机器人感知、规划与操作的落地链路",
        "frontier.image_asset": "可能影响图像生成、参考保持或可编辑资产生产",
        "visual_value.spatial_camera": "可能改善空间一致性、4D表达、运镜理解或镜头控制",
        "frontier.multimodal_agent": "可能形成多模态Agent或自动化视觉工作流的新组织方式"
    }
    signals = "、".join(candidate["matched_signals"][:4])
    suffix = f"；命中信号：{signals}" if signals else ""
    return route_text.get(candidate["primary_route"], "与视觉情报主线相关，值得进一步核验") + suffix + "。"


def _llm_input(selected: list[dict[str, Any]], payload: dict[str, Any]) -> str:
    source_map = {item["source_id"]: item for item in payload["source_records"]}
    candidates = []
    for item in selected:
        sources = []
        for source_id in item["source_ids"]:
            source = source_map.get(source_id)
            if not source:
                continue
            excerpt_limit = 6000 if source.get("source_type") == "financial_report" else 1800
            sources.append({
                "source_id": source_id,
                "publisher": source.get("publisher"),
                "source_type": source.get("source_type"),
                "title": source.get("raw_title"),
                "excerpt": (source.get("raw_excerpt") or source.get("raw_text") or "")[:excerpt_limit],
                "published_at": source.get("published_at"),
                "url": source.get("canonical_url"),
            })
        candidates.append({
            "candidate_id": item["candidate_id"],
            "deterministic_title": item["canonical_title"],
            "deterministic_intelligence_type": item["intelligence_type"],
            "deterministic_intelligence_type_signals": item["intelligence_type_signals"],
            "deterministic_primary_route": item["primary_route"],
            "deterministic_secondary_routes": item["secondary_routes"],
            "deterministic_track": item["track"],
            "deterministic_score": item["score"],
            "matched_signals": [
                signal for signal in item["matched_signals"]
                if not signal.startswith("routing_context:")
            ],
            "source_ids": item["source_ids"],
            "sources": sources,
        })
    return json.dumps({"candidates": candidates}, ensure_ascii=False)


def validate_llm_analyses(payload: dict[str, Any], selected: list[dict[str, Any]]) -> list[dict[str, Any]]:
    analyses = payload.get("analyses")
    if not isinstance(analyses, list):
        raise ValueError("LLM structure output must contain an analyses array")
    expected = {item["candidate_id"] for item in selected}
    received = [item.get("candidate_id") for item in analyses if isinstance(item, dict)]
    if len(received) != len(set(received)):
        raise ValueError("LLM structure output contains duplicate candidate_id values")
    if set(received) != expected:
        missing = sorted(expected - set(received))
        unexpected = sorted(set(received) - expected)
        raise ValueError(f"LLM structure output candidate mismatch: missing={missing}, unexpected={unexpected}")
    source_ids = {item["candidate_id"]: set(item["source_ids"]) for item in selected}
    for analysis in analyses:
        cid = analysis["candidate_id"]
        if analysis.get("intelligence_type") not in INTELLIGENCE_TYPES:
            raise ValueError(f"LLM analysis {cid} has an invalid intelligence_type")
        if not str(analysis.get("intelligence_type_reason", "")).strip():
            raise ValueError(f"LLM analysis {cid} is missing intelligence_type_reason")
        cited_source_ids = analysis.get("source_ids", [])
        if not set(cited_source_ids).issubset(source_ids[cid]):
            # Local models occasionally copy a source id from an adjacent
            # candidate. Evidence membership is deterministic, so constrain
            # the model output to the candidate's actual sources instead of
            # failing an otherwise resumable run. This does not validate any
            # claim or cross the human-review boundary.
            allowed_in_order = [sid for sid in cited_source_ids if sid in source_ids[cid]]
            analysis["source_ids"] = allowed_in_order or list(source_ids[cid])
        if (
            max(analysis.get("importance_score", 0), analysis.get("strategy_relevance_score", 0)) >= 70
            and analysis.get("recommended_disposition") == "reject"
        ):
            raise ValueError(f"LLM analysis {cid} is inconsistent: high score cannot be reject")
        if analysis.get("confidence") == "high":
            raise ValueError(f"LLM analysis {cid} is overconfident before primary-source review")
        noise = {"verification_questions", "verifiication_questions", "todo", "n/a"}
        if any(str(claim).strip().lower() in noise for claim in analysis.get("proposed_claims", [])):
            raise ValueError(f"LLM analysis {cid} contains placeholder noise in proposed_claims")
    return analyses


def enrich_with_llm(
    result: dict[str, Any],
    source_payload: dict[str, Any],
    *,
    client: StructuredLLM,
    max_candidates: int,
    prompt_version: str,
    batch_size: int | None = None,
    checkpoint_path: Path | None = None,
) -> dict[str, Any]:
    selected = result["selected_candidates"]
    if len(selected) > max_candidates:
        raise ValueError(
            f"LLM candidate limit exceeded: {len(selected)} > {max_candidates}; "
            "tighten deterministic prefiltering first"
        )
    configured_batch_size = batch_size or int(os.environ.get("SPECTRA_LLM_BATCH_SIZE", str(len(selected))))
    if configured_batch_size < 1:
        raise ValueError("SPECTRA_LLM_BATCH_SIZE must be at least 1")
    analyses: list[dict[str, Any]] = []
    batches: list[dict[str, Any]] = []
    completed: dict[str, dict[str, Any]] = {}
    completed_fingerprints: dict[str, str] = {}
    current_fingerprints = {
        item["candidate_id"]: hashlib.sha256(
            _llm_input([item], source_payload).encode("utf-8")
        ).hexdigest()
        for item in selected
    }
    source_map = {item["source_id"]: item for item in source_payload["source_records"]}
    if checkpoint_path and checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        completed = {item["candidate_id"]: item for item in checkpoint.get("analyses", [])}
        completed_fingerprints = checkpoint.get("input_fingerprints", {})
        batches = checkpoint.get("batches", [])
    for start in range(0, len(selected), configured_batch_size):
        chunk = selected[start:start + configured_batch_size]
        reusable = []
        for item in chunk:
            candidate_id = item["candidate_id"]
            if candidate_id not in completed:
                continue
            saved_fingerprint = completed_fingerprints.get(candidate_id)
            legacy_has_extracted_text = any(
                (source_map.get(source_id) or {}).get("processing_status") == "text_extracted"
                for source_id in item["source_ids"]
            )
            if saved_fingerprint == current_fingerprints[candidate_id] or (
                saved_fingerprint is None and not legacy_has_extracted_text
            ):
                reusable.append(completed[candidate_id])
        if len(reusable) == len(chunk):
            analyses.extend(reusable)
            print(json.dumps({"event": "llm_batch_reused", "completed": len(analyses), "total": len(selected)}, ensure_ascii=False), flush=True)
            continue
        llm_payload, metadata = client.generate_json(
            instructions=LLM_INSTRUCTIONS,
            input_text=_llm_input(chunk, source_payload),
            schema_name="spectra_candidate_analyses",
            schema=LLM_ANALYSIS_SCHEMA,
        )
        chunk_analyses = validate_llm_analyses(llm_payload, chunk)
        analyses.extend(chunk_analyses)
        batches.append({"index": len(batches) + 1, "candidate_count": len(chunk), **metadata})
        if checkpoint_path:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(json.dumps({
                "prompt_version": prompt_version,
                "candidate_ids": [item["candidate_id"] for item in selected],
                "input_fingerprints": current_fingerprints,
                "analyses": analyses,
                "batches": batches,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({
            "event": "llm_batch_completed", "completed": len(analyses), "total": len(selected),
            "model": metadata.get("model"), "tokens": (metadata.get("usage") or {}).get("total_tokens"),
        }, ensure_ascii=False), flush=True)
    metadata = batches[-1] if batches else {}
    analysis_map = {item["candidate_id"]: item for item in analyses}
    for candidate in selected:
        analysis = analysis_map[candidate["candidate_id"]]
        candidate["deterministic_intelligence_type"] = candidate["intelligence_type"]
        candidate["intelligence_type"] = analysis["intelligence_type"]
        candidate["intelligence_type_reason"] = analysis["intelligence_type_reason"]
        candidate["llm_analysis"] = analysis
    result["llm"] = {
        "status": "completed",
        "prompt_version": prompt_version,
        "candidate_count": len(selected),
        "batch_size": configured_batch_size,
        "batch_count": len(batches),
        "batches": batches,
        "provider": metadata.get("provider"),
        "api": metadata.get("api"),
        "model": metadata.get("model"),
        "usage": {
            "input_tokens": sum((item.get("usage") or {}).get("input_tokens") or 0 for item in batches),
            "output_tokens": sum((item.get("usage") or {}).get("output_tokens") or 0 for item in batches),
            "total_tokens": sum((item.get("usage") or {}).get("total_tokens") or 0 for item in batches),
        },
    }
    result["summary"]["llm_analyzed_candidates"] = len(selected)
    return result


def process(payload: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    global CONFIG
    CONFIG = config
    successful = [r for r in payload["source_records"] if r["access_status"] == "success"]
    scored = [score_record(r, config) for r in successful]
    hard_excluded = [item for item in scored if item["hard_exclude"]]
    soft_demoted = [item for item in scored if not item["hard_exclude"] and item["soft_negative_hits"]]
    routed = [item for item in scored if not item["hard_exclude"] and item["primary_route"]]
    unrouted = [item for item in scored if not item["hard_exclude"] and not item["primary_route"]]
    candidates = aggregate(routed)
    selected, overflow = select(candidates, config)
    input_titles = {r["raw_title"] for r in successful}
    selected_titles = {c["canonical_title"] for c in selected}
    expected = config["expected_fixture_titles"]
    recall = [{"title": title, "present_in_input": title in input_titles,
               "selected": title in selected_titles} for title in expected]
    return {
        "version": "0.2",
        "record_type": "structured_candidate_run",
        "input_run_id": payload["run_id"],
        "window_start": payload["window_start"],
        "window_end": payload["window_end"],
        "rules": {"minimum_score": config["minimum_score"], "target_count": config["target_count"],
                  "near_duplicate_threshold": 0.9, "same_event_rule": "fixed repository + same week; otherwise conservative"},
        "summary": {
            "input_records": len(payload["source_records"]),
            "successful_input_records": len(successful),
            "hard_excluded": len(hard_excluded),
            "soft_demoted": len(soft_demoted),
            "unrouted": len(unrouted),
            "routed_before_aggregation": len(routed),
            "candidate_events_after_aggregation": len(candidates),
            "near_duplicate_records_collapsed": sum(max(0, c["aggregation"]["source_count"] - 1) for c in candidates if c["aggregation"]["method"] == "near_title_dedupe"),
            "same_event_records_collapsed": sum(max(0, c["aggregation"]["source_count"] - 1) for c in candidates if c["aggregation"]["method"] == "fixed_repository_weekly_cluster"),
            "selected_for_verification": len(selected),
            "selected_by_intelligence_type": dict(Counter(c["intelligence_type"] for c in selected)),
            "selected_by_route": dict(Counter(c["primary_route"] for c in selected)),
            "fixture_recall_in_input": sum(1 for item in recall if item["present_in_input"]),
            "fixture_recall_selected": sum(1 for item in recall if item["selected"])
        },
        "fixture_recall": recall,
        "selected_candidates": selected,
        "overflow_candidates": overflow,
        "exclusions": {
            "hard": [{"source_id": i["record"]["source_id"], "title": i["record"]["raw_title"], "terms": i["hard_exclude"]} for i in hard_excluded],
            "unrouted": [{"source_id": i["record"]["source_id"], "title": i["record"]["raw_title"]} for i in unrouted]
        }
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="collector/runs/first-live-run-v0.2.json")
    parser.add_argument("--config", default="processor/config.v0.1.json")
    parser.add_argument("--output", default="processor/runs/first-structured-run-v0.1.json")
    parser.add_argument("--llm", action="store_true", help="Enrich shortlisted candidates with a real OpenAI Responses API call")
    parser.add_argument("--llm-max-candidates", type=int, default=None)
    parser.add_argument("--llm-limit", type=int, default=None, help="Analyze only the first N shortlisted candidates for a smoke test")
    parser.add_argument("--llm-checkpoint", help="Persist and reuse completed LLM batches")
    args = parser.parse_args()
    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))
    result = process(payload, config)
    if args.llm:
        if args.llm_limit is not None:
            if args.llm_limit < 1:
                parser.error("--llm-limit must be at least 1")
            result["selected_candidates"] = result["selected_candidates"][:args.llm_limit]
            result["summary"]["selected_for_verification"] = len(result["selected_candidates"])
            result["summary"]["selected_by_intelligence_type"] = dict(Counter(
                item["intelligence_type"] for item in result["selected_candidates"]
            ))
            result["summary"]["selected_by_route"] = dict(Counter(
                item["primary_route"] for item in result["selected_candidates"]
            ))
            result["summary"]["llm_smoke_limit"] = args.llm_limit
        llm_config = config.get("llm", {})
        max_candidates = args.llm_max_candidates or int(llm_config.get("max_candidates", 25))
        try:
            result = enrich_with_llm(
                result,
                payload,
                client=create_llm_client(),
                max_candidates=max_candidates,
                prompt_version=llm_config.get("prompt_version", "structure.v0.2"),
                batch_size=int(os.environ.get("SPECTRA_LLM_BATCH_SIZE", "1")),
                checkpoint_path=Path(args.llm_checkpoint) if args.llm_checkpoint else None,
            )
        except (LLMConfigurationError, LLMProviderError) as exc:
            parser.error(str(exc))
    else:
        result["llm"] = {"status": "not_requested"}
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    return 0


CONFIG: dict[str, Any] = {}

if __name__ == "__main__":
    raise SystemExit(main())
