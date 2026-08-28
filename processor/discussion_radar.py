#!/usr/bin/env python3
"""Build a discovery-only discussion radar from collected public sources.

The radar detects independent observers discussing the same theme. It never
promotes a discussion signal to a verified fact or crosses the P1 review gate.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

THEMES = {
    "video_generation": {"label": "视频生成", "terms": ["video generation", "text-to-video", "image-to-video", "video model", "sora", "veo", "kling", "wan", "视频生成", "文生视频", "图生视频", "可灵"]},
    "world_models": {"label": "世界模型", "terms": ["world model", "world-model", "interactive world", "genie", "世界模型"]},
    "embodied_ai": {"label": "具身智能", "terms": ["embodied", "robot", "robotics", "humanoid", "vision-language-action", "vla", "具身智能", "机器人", "人形机器人"]},
    "image_generation": {"label": "图像与视觉资产", "terms": ["image generation", "text-to-image", "image model", "3d asset", "firefly", "图像生成", "文生图", "3d资产"]},
    "evaluation": {"label": "评测与标准", "terms": ["benchmark", "evaluation", "eval", "leaderboard", "metric", "评测", "基准"]},
    "foundation_multimodal": {"label": "基础模型与多模态", "terms": ["foundation model", "multimodal", "large language model", "llm", "基础模型", "多模态", "大模型"]},
    "agents_tools": {"label": "AI Agent与工具", "terms": ["ai agent", "agentic", "coding agent", "developer tool", "智能体", "ai工具", "ai助手"]},
    "compute_data": {"label": "算力与数据", "terms": ["gpu", "ai chip", "data center", "inference infrastructure", "training data", "算力", "芯片", "数据中心"]},
    "open_source": {"label": "开源生态", "terms": ["open source", "open-source", "model weights", "github", "hugging face", "开源", "模型权重"]},
    "commercialization": {"label": "商业化与行业应用", "terms": ["revenue", "pricing", "customer", "adoption", "commercial", "advertising", "marketing", "营收", "定价", "客户", "商业化", "广告", "营销"]}
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def observer_registry_map(watchlist: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for observer in watchlist["observers"]:
        for registry_id in observer.get("existing_registry_ids", []):
            result[registry_id] = observer
        for index, channel in enumerate(observer.get("channels", []), 1):
            generated_id = channel.get("registry_id") or f"observer_{observer['observer_id']}_{index}"
            result[generated_id] = observer
    return result


def record_text(record: dict[str, Any]) -> str:
    return " ".join(str(record.get(key) or "") for key in ("raw_title", "raw_excerpt", "raw_text")).lower()


def matched_themes(record: dict[str, Any]) -> list[str]:
    text = record_text(record)
    return [theme_id for theme_id, theme in THEMES.items() if any(term.lower() in text for term in theme["terms"])]


def signal_level(observer_count: int, institution_count: int, rules: dict[str, Any]) -> str:
    if observer_count >= int(rules["strong_minimum_observers"]) and institution_count >= 2:
        return "strong_rising"
    if observer_count >= int(rules["rising_minimum_observers"]) and (
        not rules.get("institution_required_for_rising") or institution_count >= 1
    ):
        return "rising"
    if observer_count >= int(rules["minimum_independent_observers"]):
        return "emerging"
    return "isolated"


def build_radar(collection: dict[str, Any], watchlist: dict[str, Any]) -> dict[str, Any]:
    mapping = observer_registry_map(watchlist)
    buckets: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(list)
    observed_records = 0
    for record in collection.get("source_records", []):
        if record.get("access_status") != "success":
            continue
        observer = mapping.get(record.get("registry_id"))
        if not observer:
            continue
        observed_records += 1
        for theme_id in matched_themes(record):
            buckets[theme_id].append((record, observer))

    rules = watchlist["radar_rules"]
    signals = []
    isolated = []
    for theme_id, pairs in buckets.items():
        observers = {observer["observer_id"]: observer for _, observer in pairs}
        institutions = {key for key, observer in observers.items() if observer["observer_type"] == "institution"}
        people = {key for key, observer in observers.items() if observer["observer_type"] == "person"}
        level = signal_level(len(observers), len(institutions), rules)
        sources = []
        seen = set()
        for record, observer in sorted(pairs, key=lambda pair: pair[0].get("published_at") or "", reverse=True):
            key = record.get("source_id")
            if key in seen:
                continue
            seen.add(key)
            sources.append({
                "observer_id": observer["observer_id"],
                "observer_name": observer["name"],
                "observer_type": observer["observer_type"],
                "title": record.get("raw_title"),
                "url": record.get("canonical_url"),
                "published_at": record.get("published_at"),
                "evidence_tier": observer["evidence_tier"],
            })
        item = {
            "theme_id": theme_id,
            "theme": THEMES[theme_id]["label"],
            "signal_level": level,
            "independent_observers": len(observers),
            "institution_count": len(institutions),
            "person_count": len(people),
            "record_count": len(sources),
            "observer_names": sorted(observer["name"] for observer in observers.values()),
            "summary": f"近7天有{len(observers)}个独立观察对象讨论{THEMES[theme_id]['label']}，其中机构{len(institutions)}个、个人{len(people)}个。",
            "editorial_status": "p2_signal_only",
            "verification_required": True,
            "sources": sources[:12],
        }
        (isolated if level == "isolated" else signals).append(item)

    rank = {"strong_rising": 3, "rising": 2, "emerging": 1}
    signals.sort(key=lambda item: (-rank[item["signal_level"]], -item["independent_observers"], item["theme_id"]))
    isolated.sort(key=lambda item: (-item["record_count"], item["theme_id"]))
    observers = watchlist["observers"]
    active = sum(any(channel.get("collection_status") == "active_existing" for channel in item.get("channels", [])) for item in observers)
    trial = sum(any(channel.get("collection_status") == "trial" for channel in item.get("channels", [])) for item in observers)
    return {
        "version": "discussion-radar.v0.1",
        "record_type": "discussion_radar",
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "window_start": collection.get("window_start"),
        "window_end": collection.get("window_end"),
        "policy": {
            "purpose": "前置信号发现，不替代事实核验",
            "single_observer_is_trend": False,
            "default_disposition": "p2_signal_only",
            "p1_requires_primary_source_review": True,
        },
        "coverage": {
            "configured_observers": len(observers),
            "institution_observers": sum(item["observer_type"] == "institution" for item in observers),
            "person_observers": sum(item["observer_type"] == "person" for item in observers),
            "active_existing_observers": active,
            "trial_observers": trial,
            "matched_source_records": observed_records,
        },
        "signals": signals,
        "isolated_mentions": isolated,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="collection.json")
    parser.add_argument("--watchlist", default="collector/observer_watchlist.v0.1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    collection = load_json(Path(args.input))
    watchlist_path = Path(args.watchlist)
    if not watchlist_path.is_absolute():
        watchlist_path = ROOT / watchlist_path
    result = build_radar(collection, load_json(watchlist_path))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["coverage"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
