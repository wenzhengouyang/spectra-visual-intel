#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("file", nargs="?", default="processor/runs/first-structured-run-v0.1.json")
args = parser.parse_args()
path = Path(args.file)
data = json.loads(path.read_text(encoding="utf-8"))
selected = data["selected_candidates"]
errors = []
if not 12 <= len(selected) <= 30:
    errors.append(f"人工核验短名单应为12—30，实际{len(selected)}")
if len({item["candidate_id"] for item in selected}) != len(selected):
    errors.append("candidate_id不唯一")
if not all(item["status"] == "needs_verification" for item in selected):
    errors.append("候选不得提前标记为已核验")
if not all(item.get("why_candidate") and item.get("verification_questions") for item in selected):
    errors.append("候选缺少入选原因或核验问题")
allowed_types = {
    "type.technology_breakthrough", "type.product_release",
    "type.industry_market", "type.company_strategy",
}
editorial_types = Counter(item.get("intelligence_type") for item in selected)
unknown_types = set(editorial_types) - allowed_types
if unknown_types:
    errors.append(f"存在未知一级情报分类：{sorted(unknown_types)}")
if editorial_types["type.technology_breakthrough"] > 12:
    errors.append("技术突破短名单超过上限12，来源或选刊配比失衡")
available_types = Counter(
    item.get("deterministic_intelligence_type") or item.get("intelligence_type")
    for item in selected + data.get("overflow_candidates", [])
)
for intelligence_type in allowed_types - {"type.technology_breakthrough"}:
    if available_types[intelligence_type] and not editorial_types[intelligence_type]:
        errors.append(f"候选池存在{intelligence_type}，但人工核验短名单未保留")
github = [item for item in selected if item["canonical_title"] == "Wan-Animate-2 repository opened and documented"]
collapsed = data["summary"].get("same_event_records_collapsed", 0)
if github or collapsed:
    if len(github) != 1:
        errors.append(f"Wan-Animate-2提交应聚合为一个事件，实际候选数{len(github)}")
    elif github[0]["aggregation"].get("method") != "fixed_repository_weekly_cluster":
        errors.append("Wan-Animate-2候选未使用仓库周聚合规则")
    elif github[0]["aggregation"].get("source_count", 0) - 1 != collapsed:
        errors.append(
            f"Wan-Animate-2聚合统计不一致：来源{github[0]['aggregation'].get('source_count', 0)}条，"
            f"折叠{collapsed}条"
        )
present_fixture = [item for item in data["fixture_recall"] if item["present_in_input"]]
if not all(item["selected"] for item in present_fixture):
    errors.append("已在输入中出现的样刊回归信号未全部保留")
routes = Counter(item["primary_route"] for item in selected)
available_routes = Counter(
    item["primary_route"]
    for item in selected + data.get("overflow_candidates", [])
    if item.get("primary_route")
)
for route, minimum in {"visual_value.evaluation": 2, "frontier.video_generation": 3, "frontier.world_model": 3,
                       "frontier.embodied_ai": 3, "frontier.image_asset": 1, "visual_value.spatial_camera": 1}.items():
    attainable_minimum = min(minimum, available_routes[route])
    if routes[route] < attainable_minimum:
        errors.append(f"{route}少于本轮可达配额{attainable_minimum}")
if errors:
    print(json.dumps({"result": "fail", "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps({"result": "pass", "selected": len(selected), "routes": routes,
                  "fixture_present": len(present_fixture), "fixture_retained": sum(item["selected"] for item in present_fixture)}, ensure_ascii=False, indent=2))
