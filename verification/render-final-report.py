#!/usr/bin/env python3
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
data = json.loads((ROOT / "verification" / "runs" / "p1-verified-events-v0.2.json").read_text(encoding="utf-8"))
records = {item["candidate_id"]: item for item in data["verification_records"]}
events = data["intelligence_events"]
event_by_id = {item["event_id"]: item for item in events}
record_by_event = {item["event"]["event_id"]: item for item in records.values() if item.get("event")}
top = set(data["editorial_selection"]["top_event_ids"])

lines = [
    "# P1原文核验与正式周报事件 v0.1", "",
    "> 核验窗口：2026-08-06—2026-08-12；核验对象：结构化候选中的16条P1。", "",
    "## 1. 本轮结论", "",
    "16条P1均完成原始来源核验：15条核对arXiv原始条目，1条核对Wan官方GitHub README与提交记录。"
    "最终保留9个正式事件，另7条进入观察池。入选只表示事件事实和作者报告已经核对，不代表实验获得独立复现。", "",
    f"本周总判断：{data['editorial_selection']['weekly_thesis']}", "",
    "```text", "16条P1", "→ 题名、作者、时间核对", "→ 方法、数字、实验结论核对", "→ 明确作者报告与独立事实边界",
    "→ 同主题编辑去重", "→ 9个正式事件（其中5个首页重点）＋7个观察项", "```", "",
    "## 2. 首页重点事件（5条）", ""
]

for index, event_id in enumerate(data["editorial_selection"]["top_event_ids"], 1):
    event = event_by_id[event_id]
    record = record_by_event[event_id]
    lines.extend([
        f"### {index}. {event['canonical_title']}", "",
        f"原文：[{record['title']}]({record['url']})", "",
        f"发生了什么：{event['fact_summary']}", "",
        f"为什么重要：{record['decision_reason']}", "",
        f"证据边界：{event['limitations']}", ""
    ])

lines.extend(["## 3. 其余正式事件（4条）", ""])
for event in events:
    if event["event_id"] in top:
        continue
    record = record_by_event[event["event_id"]]
    lines.extend([
        f"### {event['canonical_title']}", "",
        f"原文：[{record['title']}]({record['url']})", "",
        f"发生了什么：{event['fact_summary']}", "",
        f"为什么重要：{record['decision_reason']}", "",
        f"证据边界：{event['limitations']}", ""
    ])

lines.extend(["## 4. 转入观察池（7条）", "", "| 候选 | 已核验事实 | 本期未入选原因 |", "|---|---|---|"])
for record in data["verification_records"]:
    if record["decision"] != "watch":
        continue
    fact = "；".join(item["text"] for item in record["claims"][:2]).replace("|", "\\|")
    reason = (record["decision_reason"] + " 证据边界：" + record["limitation"]).replace("|", "\\|")
    lines.append(f"| [{record['title']}]({record['url']}) | {fact} | {reason} |")

lines.extend([
    "", "## 5. 正式事件编排", "",
    "时间轴按发布日期排列：", ""
])
for event_id in data["editorial_selection"]["timeline_event_ids"]:
    event = event_by_id[event_id]
    badge = "首页重点" if event_id in top else "分类扩展"
    lines.append(f"- {event['event_at'][:10]}｜{badge}｜{event['canonical_title']}")

lines.extend([
    "", "## 6. 后续使用规则", "",
    "- 网页时间轴、重点摘要和趋势雷达只读取这9个 `status: approved` 的事件；",
    "- 首页优先展示5个 `top_event_ids`，其余4个进入分类栏目；",
    "- 7个观察项不删除，后续出现代码、独立评测或更完整数字时可重新升级；",
    "- 对论文效果统一使用“作者报告”，除非后续存在独立来源复现；",
    "- 这一步完成的是事实层事件，不在本轮直接生成长篇The Batch式文章。下一步才进入编辑文章层。", ""
])

(ROOT / "P1原文核验与正式周报事件_v0.1.md").write_text("\n".join(lines), encoding="utf-8")
