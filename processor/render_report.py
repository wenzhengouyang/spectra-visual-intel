#!/usr/bin/env python3
import argparse
import json
from pathlib import Path


ROUTE_NAMES = {
    "visual_value.evaluation": "评测与标准",
    "frontier.video_generation": "视频生成",
    "frontier.world_model": "世界模型",
    "frontier.embodied_ai": "具身智能",
    "frontier.image_asset": "图像与资产",
    "visual_value.spatial_camera": "空间与镜头",
    "frontier.multimodal_agent": "多模态Agent"
}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="processor/runs/first-structured-run-v0.1.json")
    parser.add_argument("--output", default="结构化处理与核验候选_v0.1.md")
    args = parser.parse_args()
    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    summary = data["summary"]
    selected = data["selected_candidates"]
    lines = [
        "# 结构化处理与核验候选 v0.1", "",
        "> 输入：第一次自动采集的 143 条 `source_record`；本文件是核验工作池，不是正式周报。", "",
        "## 1. 处理结果", "",
        f"143 条原始记录中有 {summary['successful_input_records']} 条采集成功；硬性排除 {summary['hard_excluded']} 条，"
        f"另有 {summary['soft_demoted']} 条触发负向词并被降权。标题近似去重折叠 {summary['near_duplicate_records_collapsed']} 条；"
        f"GitHub 同事件聚合折叠 {summary['same_event_records_collapsed']} 条，将 13 个 Wan-Animate-2 提交聚合为 1 个仓库事件。"
        f"最终选择 {summary['selected_for_verification']} 条进入人工/原文核验。", "",
        "```text", "source_record", "→ 硬性排除与负向降权", "→ 主路由＋原子标签", "→ URL／标题近似去重",
        "→ 固定仓库同周事件聚合", "→ 方向配额＋回归样本保护", "→ 25条待核验候选", "```", "",
        "## 2. 候选总表", "",
        "| # | 优先级 | 主方向 | 分数 | 候选事件 | 聚合来源 |", "|---:|---|---|---:|---|---:|"
    ]
    for index, item in enumerate(selected, 1):
        title = item["canonical_title"].replace("|", "\\|")
        lines.append(f"| {index} | `{item['verification_priority']}` | {ROUTE_NAMES.get(item['primary_route'], item['primary_route'])} | {item['score']} | [{title}]({item['source_urls'][0]}) | {item['aggregation']['source_count']} |")
    lines.extend(["", "## 3. 分方向核验清单", ""])
    for route in ROUTE_NAMES:
        items = [item for item in selected if item["primary_route"] == route]
        if not items:
            continue
        lines.extend([f"### {ROUTE_NAMES[route]}（{len(items)}条）", ""])
        for item in items:
            lines.extend([
                f"- [{item['canonical_title']}]({item['source_urls'][0]})｜`{item['verification_priority']}`｜评分 {item['score']}",
                f"  - 入选原因：{item['why_candidate']}",
                f"  - 标签：{'、'.join(item['tags']['tech'] + item['tags']['capability'] + item['tags']['task']) or '待原文补充'}",
                f"  - 核验：{'；'.join(item['verification_questions'])}",
                f"  - 聚合：{item['aggregation']['method']}，{item['aggregation']['source_count']} 个原始来源。",
                ""
            ])
    present = [item for item in data["fixture_recall"] if item["present_in_input"]]
    missing = [item for item in data["fixture_recall"] if not item["present_in_input"]]
    lines.extend([
        "## 4. 回归召回检查", "",
        f"现有 8 条真实样刊重点中，本批原始采集只召回 {len(present)} 条；这 {len(present)} 条均进入本次 25 条候选。"
        f"其余 {len(missing)} 条不是被结构化处理删除，而是采集发现阶段没有召回。", "",
        "已召回：", ""
    ])
    lines.extend([f"- {item['title']}" for item in present])
    lines.extend(["", "未被原始采集召回：", ""])
    lines.extend([f"- {item['title']}" for item in missing])
    lines.extend([
        "", "因此本轮结构化压缩可以进入使用，但采集查询仍需在下一轮补充：arXiv ID/题名回查、Hugging Face Papers、官方博客和文章正文入口。", "",
        "## 5. 使用边界", "",
        "- `priority.p1` 表示优先核验，不表示事实已经成立；",
        "- 论文摘要只能支持候选筛选，关键数字、结论和限制必须回到论文正文；",
        "- 13 个 GitHub Commit 只形成一个仓库动态，不得拆成 13 条新闻；",
        "- 自动驾驶和手术机器人等具身垂直应用只降权、不硬删，以符合 PRD 对具身应用核心场景的要求；",
        "- 25 条全部核验成本仍较高，下一步应先处理 P1，再决定最终进入周报的 5—10 个事件。", ""
    ])
    Path(args.output).write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
