#!/usr/bin/env python3
import argparse
import json
from collections import Counter
from pathlib import Path


parser = argparse.ArgumentParser()
parser.add_argument("file", nargs="?", default="processor/runs/first-structured-run-v0.1.json")
parser.add_argument("--config", default="processor/config.v0.1.json")
args = parser.parse_args()
path = Path(args.file)
data = json.loads(path.read_text(encoding="utf-8"))
config = json.loads(Path(args.config).read_text(encoding="utf-8"))
selected = data["selected_candidates"]
feed = data.get("feed_candidates", selected)
errors = []
if not 12 <= len(selected) <= 30:
    errors.append(f"人工核验短名单应为12—30，实际{len(selected)}")
if len({item["candidate_id"] for item in selected}) != len(selected):
    errors.append("candidate_id不唯一")
if len({item["candidate_id"] for item in feed}) != len(feed):
    errors.append("信息流candidate_id不唯一")
if not set(item["candidate_id"] for item in selected).issubset(item["candidate_id"] for item in feed):
    errors.append("人工核验短名单必须包含在信息流候选中")
for item in selected + feed:
    if not item.get("front_display_eligible", True):
        errors.append(f"前台候选未通过硬门槛：{item.get('candidate_id')}")
    gates = item.get("hard_gates") or {}
    if (gates.get("content_completeness") or {}).get("status") == "fail":
        errors.append(f"正文完整度硬门槛失败的候选仍被选中：{item.get('candidate_id')}")
    if (gates.get("fact_wording_fidelity") or {}).get("status") == "fail":
        errors.append(f"事实措辞保真硬门槛失败的候选仍被选中：{item.get('candidate_id')}")
feed_limit = int(config.get("feed_count", 40))
if len(feed) > feed_limit:
    errors.append(f"信息流候选超过上限{feed_limit}，实际{len(feed)}")
allowed_scopes = {"scope.visual_core", "scope.ai_extended"}
if any(item.get("domain_scope") not in allowed_scopes for item in feed):
    errors.append("信息流候选存在未知核心/外围范围")
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
if github:
    if len(github) != 1:
        errors.append(f"Wan-Animate-2提交应聚合为一个事件，实际候选数{len(github)}")
    elif github[0]["aggregation"].get("method") != "fixed_repository_weekly_cluster":
        errors.append("Wan-Animate-2候选未使用仓库周聚合规则")
all_candidates = selected + data.get("overflow_candidates", [])
expected_collapsed = sum(
    max(0, item.get("aggregation", {}).get("source_count", 0) - 1)
    for item in all_candidates
    if item.get("aggregation", {}).get("method") in {"fixed_repository_weekly_cluster", "same_event_rule"}
)
# The summary covers every aggregated candidate, including candidates removed
# by hard gates or score thresholds; selected + overflow is only the publishable
# subset. It may therefore account for fewer collapsed source records, but it
# must never exceed the summary count.
if collapsed < expected_collapsed:
    errors.append(f"同事件聚合统计少计：记录{collapsed}条，公开候选至少需要{expected_collapsed}条")
present_fixture = [item for item in data["fixture_recall"] if item["present_in_input"]]
if not all(item["selected"] for item in present_fixture):
    errors.append("已在输入中出现的样刊回归信号未全部保留")
routes = Counter(item["primary_route"] for item in selected)
llm_completed = (data.get("llm") or {}).get("status") == "completed"
if llm_completed:
    # After the LLM fidelity gate, an overflow candidate without llm_analysis
    # is not an immediately attainable replacement. Counting it here can make
    # a safely gated run fail merely because an unreviewed overflow item shares
    # the missing route. P2 feed coverage remains available separately.
    attainable = selected + [
        item for item in data.get("overflow_candidates", [])
        if item.get("llm_analysis")
        and item.get("front_display_eligible", True)
        and ((item.get("hard_gates") or {}).get("fact_wording_fidelity") or {}).get("status") == "pass"
    ]
else:
    attainable = selected + data.get("overflow_candidates", [])
available_routes = Counter(
    item["primary_route"] for item in attainable if item.get("primary_route")
)
for route, minimum in {"visual_value.evaluation": 2, "frontier.video_generation": 3, "frontier.world_model": 3,
                       "frontier.embodied_ai": 3, "frontier.image_asset": 1, "visual_value.spatial_camera": 1}.items():
    attainable_minimum = min(minimum, available_routes[route])
    if routes[route] < attainable_minimum:
        errors.append(f"{route}少于本轮可达配额{attainable_minimum}")
if errors:
    print(json.dumps({"result": "fail", "errors": errors}, ensure_ascii=False, indent=2))
    raise SystemExit(1)
print(json.dumps({"result": "pass", "selected": len(selected), "feed": len(feed), "routes": routes,
                  "fixture_present": len(present_fixture), "fixture_retained": sum(item["selected"] for item in present_fixture)}, ensure_ascii=False, indent=2))
