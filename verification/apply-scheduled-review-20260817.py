#!/usr/bin/env python3
"""Apply the 2026-08-17 editorial decision to the scheduled acceptance review."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUN = ROOT / "spectra_agent/runs/scheduled-acceptance-complete-20260817"
CURRENT = RUN / "p1-review.json"
PRIOR = ROOT / "spectra_agent/runs/llm-agent-acceptance-v02/p1-review.json"

INCLUDE = {1, 2, 3, 6, 8}
WATCH = {4, 5, 7, 9}
EXCLUDE = {10}

REASONS = {
    1: "视频潜变量直接连接4D生成，兼具视频模型优化、空间表达和资产生产价值。",
    2: "补足视频模型从视觉逼真度走向知识、因果与任务正确性的评测缺口。",
    3: "几何一致性是视频模型优化的核心质量约束，指标方向与团队Bad Case分析直接相关。",
    4: "流式视频生成训练方向重要，但本期正式版面优先保留证据覆盖更完整的优化与应用信号。",
    5: "直接体现视频生成服务具身智能应用，保留观察；仍需正文指标与更多独立场景验证。",
    6: "从世界探索数据走向可交互世界建模，连接视频生成、空间建模与长期交互。",
    7: "自动驾驶4D一致世界模型值得持续跟踪，但场景专用性较强，本期列入观察池。",
    8: "少步因果生成直指交互式世界模型的时延和动作响应问题，兼具模型优化与应用价值。",
    9: "潜空间动力学外推具有研究价值，但当前与本期世界模型主题重叠，先观察后续验证。",
    10: "用户明确剔除：本期不进入正式事件或观察池，保留核验记录仅用于审计。",
}

NEW_EVIDENCE = {
    5: {
        "claims": [
            {"text": "论文提出面向人机物体交接的Hand2Bot RGB-D视频数据集，并包含身体姿态与面部表情等上下文。", "kind": "method", "locator": "arXiv abstract"},
            {"text": "论文提出PassGen，以稳定视频扩散、意图感知时序面部编码器和深度噪声编辑生成交接序列。", "kind": "method", "locator": "arXiv abstract"},
            {"text": "作者报告在物理机器人部署中实现较高意图识别准确率、较低误触发率，并支持零样本迁移和更早意图预判。", "kind": "result", "locator": "arXiv abstract"},
        ],
        "limitation": "摘要未给出具体数值、数据规模和跨设备泛化结果；结论仍需正文表格和独立复现支持。",
    },
    8: {
        "claims": [
            {"text": "ForgeWM通过领域适配、教师强制因果训练、因果一致性蒸馏和在策略分布匹配，将双向动作条件视频生成器转化为少步世界模型。", "kind": "method", "locator": "arXiv abstract"},
            {"text": "其学生模型分别以1、2、4步稳态去噪预算运行，并支持低延迟交互与回放时优化两条部署路径。", "kind": "result", "locator": "arXiv abstract"},
            {"text": "作者报告ForgeWM在配对Minecraft轨迹上领先所评系统的多项成像与动作控制指标，且同一训练配方可迁移至手柄控制的FPS玩法。", "kind": "result", "locator": "arXiv abstract"},
        ],
        "limitation": "公开摘要的结果集中于Minecraft与FPS游戏；尚不能外推到开放世界、真实机器人或更长时交互。",
        "event": {
            "event_id": "evt_20260814_forgewm_fewstep",
            "canonical_title": "ForgeWM将动作条件视频模型压缩为1至4步交互式世界模型",
            "event_type": "event.research",
            "primary_route": "frontier.world_model",
            "secondary_routes": ["frontier.video_generation", "visual_value.evaluation"],
            "priority": "priority.p1",
            "confidence": "confidence.high",
            "entity_name": "ForgeWM",
            "tags": {
                "tech": ["tech.world_model", "tech.video_generation"],
                "task": ["task.action_conditioned_generation"],
                "capability": ["cap.low_latency", "cap.action_control"],
                "scene": ["scene.game", "scene.virtual_environment"],
                "event": ["event.research"],
            },
        },
    },
    10: {
        "claims": [
            {"text": "DreamX-Phi 1.0根据观察帧、语言指令和末端执行器动作序列预测机器人操作的未来观察。", "kind": "method", "locator": "arXiv abstract"},
            {"text": "模型使用PRoPE式几何编码、深度分支、SAM3掩码和冻结的V-JEPA教师约束机械臂身份、场景几何与物体一致性。", "kind": "method", "locator": "arXiv abstract"},
            {"text": "作者报告其在WorldArena 2.0挑战中获得Track 1第一、Track 2第二。", "kind": "result", "locator": "arXiv abstract"},
        ],
        "limitation": "排名来自作者在论文提交时的陈述，摘要未披露完整对比、绝对指标和真实机器人部署结果。",
    },
}


def main() -> None:
    current = json.loads(CURRENT.read_text(encoding="utf-8"))
    prior = json.loads(PRIOR.read_text(encoding="utf-8"))
    prior_map = {item["candidate_id"]: item for item in prior["records"]}

    for index, record in enumerate(current["records"], 1):
        old = prior_map.get(record["candidate_id"])
        if old:
            record["verification_status"] = old["verification_status"]
            record["claims"] = old["claims"]
            record["limitation"] = old["limitation"]
            record["event"] = old.get("event")
        if index in NEW_EVIDENCE:
            record.update(NEW_EVIDENCE[index])

        record["decision"] = "include" if index in INCLUDE else "watch" if index in WATCH else "exclude"
        record["decision_reason"] = REASONS[index]
        record["verification_status"] = "verified_primary"
        if record["decision"] != "include":
            record["event"] = None

    # Item 03 was previously a watch item, so it needs event metadata now.
    item3 = current["records"][2]
    item3["event"] = {
        "event_id": "evt_20260811_aigc_geometry_metric",
        "canonical_title": "新指标开始单独衡量AIGC视频的跨帧几何一致性",
        "event_type": "event.research",
        "primary_route": "visual_value.evaluation",
        "secondary_routes": ["frontier.video_generation"],
        "priority": "priority.p1",
        "confidence": "confidence.high",
        "entity_name": "Geometrical Consistency Metric",
        "tags": {
            "tech": ["tech.video_generation"],
            "task": ["task.video_evaluation"],
            "capability": ["cap.geometric_consistency", "cap.temporal_consistency"],
            "scene": ["scene.model_evaluation"],
            "event": ["event.research"],
        },
    }

    current["review_status"] = "approved"
    current["verified_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    current["verified_by"] = "user editorial decision + Codex primary-source verification"
    current["editorial_selection"]["weekly_thesis"] = (
        "本周视频模型优化正从单纯画质提升转向几何一致性、知识正确性与少步交互效率；"
        "具身应用将继续推动视频模型接受动作控制、空间约束和真实任务指标的检验。"
    )
    CURRENT.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
