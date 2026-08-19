#!/usr/bin/env python3
"""Build an editorial issue from a human-approved verified event bundle.

Known Issue 01 stories retain their edited copy. New events use a conservative
fallback that only promotes verified facts and keeps judgment visibly separate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIED_PATH = ROOT / "verification/runs/p1-verified-events-v0.2.json"
REVIEW_PATH = ROOT / "verification/p1-review.v0.1.json"
OUTPUT_PATH = ROOT / "editorial/runs/issue-01-editorial-stories-v0.2.json"
STATIC_PATH = ROOT / "visual-intelligence-prototype.html"


STORY_COPY = {
    "evt_20260810_scivbench_auto": {
        "story_id": "story_202633_scivbench",
        "article_type": "deep_dive",
        "headline": "视频评测的下一关：科学机制是否正确？",
        "dek": "Sci-VBench用1,253项专家样例测试16个视频模型。结果指向一个正在扩大的缺口：画面可以足够逼真，但科学过程和因果链仍可能是错的。",
        "one_line_takeaway": "视频生成的竞争正从画质和提示词对齐，进入机制正确性与可验证结构的评测阶段。",
        "category": "评测与标准",
        "what": "Sci-VBench发布了一套面向科学知识与推理的视频生成评测：1,253个专家标注样例覆盖四大学科、60个主题，并用Rubric协议评估16个闭源和开源模型。作者报告，各模型的感知质量已经较为接近，但Prompt Grounding以及科学和因果正确性仍有明显差异。",
        "why": "高画质Demo会掩盖对象状态、过程顺序和因果关系上的结构性错误。把这些错误拆成可重复测试的任务，意味着模型团队可以建立机制层的Bad Case回归集，产品侧也能更准确地界定模型适用边界。",
        "take": "对视频模型产品策略而言，后续能力卖点不应只强调分辨率、时长和指令遵循，还要回答物理、因果和空间过程能否被稳定验证。科学评测可以先作为高难度切片，而不是直接替代通用审美评测。",
        "how": "该基准以专家设计的科学任务为基本单元，通过细分Rubric检查生成结果中的知识、推理、时空连续和因果正确性，避免单一综合分数把机制失败平均掉。",
        "watch": ["主流视频模型是否开始公开机制正确性的分项结果？", "科学与因果Bad Case能否进入团队统一回归测试？"],
        "numbers": [("专家样例", "1,253", 0), ("科学主题", "60", 0), ("评估模型", "16", 1)],
        "tags": ["评测与标准", "视频生成", "机制正确性"],
        "reading": 4,
        "score": 97,
    },
    "evt_20260807_wananimate2": {
        "story_id": "story_202633_wananimate2",
        "article_type": "deep_dive",
        "headline": "Wan-Animate-2把角色动画推向端到端驱动",
        "dek": "Wan-Animate-2开放推理脚本、基础权重和蒸馏权重，并将驱动视频直接接入角色动画管线。对创作者而言，动作迁移、身份保持与视角控制开始被收束到同一套工作流。",
        "one_line_takeaway": "角色动画的竞争开始从单点动作迁移，转向身份、动作、视角和推理效率的一体化工作流。",
        "category": "视频生成",
        "what": "Wan官方仓库记录，Wan-Animate-2于8月7日开放推理脚本、基础模型权重和蒸馏模型权重。官方将其定义为直接消费驱动视频的端到端角色动画框架，并加入文本驱动的视角控制；公开模型为14B，蒸馏示例采用10步且不使用CFG。",
        "why": "角色动画产品过去往往需要分开处理动作提取、身份一致性、背景合成和镜头调整。端到端输入驱动视频并提供视角控制，有机会减少中间资产和手工修补，直接影响短剧、数字人和营销素材的制作效率。",
        "take": "这次发布最值得验证的不是单条Demo质量，而是同一角色跨动作、跨视角和复杂遮挡时的稳定性，以及蒸馏版本能否在可接受成本下进入真实创作管线。",
        "how": "框架直接读取驱动视频来控制角色动作，同时保留参考角色身份，并通过文本条件改变观察视角。官方示例表明蒸馏版本缩短了采样步数，但公开信息还不足以证明实时能力。",
        "watch": ["蒸馏版本在常用硬件上的速度、显存和质量损失如何？", "视角变化下的身份、手部、遮挡和背景一致性是否稳定？"],
        "numbers": [("模型规模", "14B", 2), ("蒸馏步数", "10", 2), ("CFG", "关闭", 2)],
        "tags": ["视频生成", "角色动画", "开源模型"],
        "reading": 4,
        "score": 94,
    },
    "evt_20260809_logishot": {
        "story_id": "story_202633_logishot",
        "article_type": "deep_dive",
        "headline": "跨镜头一致性开始从外观进入世界状态",
        "dek": "LogiShot用上下文视频编码和视觉记忆处理跨镜头逻辑一致性，并构建11万样本的数据集。它瞄准的不是单镜头更好看，而是人物、物体和事件在下一镜里仍然说得通。",
        "one_line_takeaway": "多镜头视频的核心问题正从角色长得像，转向跨镜头世界状态和叙事逻辑能否延续。",
        "category": "视频生成",
        "what": "LogiShot联合编码上下文视频与其他生成条件，并在生成过程中保持上下文视频的视觉记忆。论文构建了11万样本的数据集和专用评测，作者报告其在多镜头逻辑一致性上持续优于现有基线。",
        "why": "短剧和影视工作流真正需要的不是若干独立好看的镜头，而是服装、道具、空间关系、动作结果和人物状态能够跨镜头延续。视觉记忆把这类需求从提示词技巧推进到模型结构和训练数据问题。",
        "take": "这条路线与视频产品最直接的结合点，是把上一镜的关键状态显式抽取成可检查的记忆，再分别评测外观连续、空间连续和事件连续，而不是只给一个笼统的一致性分数。",
        "how": "模型把上下文视频与当前条件共同编码，并在生成阶段保存可调用的视觉记忆，使后续镜头能够参考前序镜头中的人物、物体和情节状态。",
        "watch": ["论文承诺的模型与数据何时真正开放？", "11万样本对复杂人物关系和长叙事的覆盖范围有多大？"],
        "numbers": [("训练样本", "110K", 1)],
        "tags": ["视频生成", "跨镜头一致性", "短剧"],
        "reading": 4,
        "score": 92,
    },
    "evt_20260806_gauge": {
        "story_id": "story_202633_gauge",
        "article_type": "deep_dive",
        "headline": "世界模型“看起来对”，不等于物理量真的对",
        "dek": "GAUGE把物理引擎和视频世界模型放进同一套真实测量框架。作者发现，一段轨迹即使视觉上合理，仍可能在加速度、动量传递或振荡时序上出现系统性错误。",
        "one_line_takeaway": "物理一致性正在从视觉印象题，变成可以用真实测量和参数恢复诊断的工程问题。",
        "category": "世界模型",
        "what": "GAUGE建立22个受控任务族，覆盖刚体、绳索、织物和体积可变形物体。论文评测三个物理引擎的14个任务族，以及六个图生视频模型的五个刚体任务；作者报告，部分视频轨迹看似遵循方程，却恢复出错误的加速度、动量传递和振荡时序。",
        "why": "如果评测只看成片或依赖主观评分，物理错误很容易被画质掩盖。测量驱动的评测能把问题定位到具体参数和时间阶段，为世界模型训练、奖励设计及高风险仿真应用提供更可靠的诊断依据。",
        "take": "团队可以优先选择与产品Bad Case最接近的少量任务建立内部测量切片，例如抛物、碰撞、液体和布料，而不必一开始复刻完整基准。关键是把“像不像”改成“哪一个物理量错了”。",
        "how": "GAUGE用真实世界测量作为参照，把视频中的运动轨迹恢复为可比较的物理参数，并对引擎模拟和生成模型使用统一诊断逻辑。",
        "watch": ["测量框架能否扩展到流体、材质和复杂接触？", "视频模型的物理错误是否能被奖励模型或后训练直接修正？"],
        "numbers": [("任务族", "22", 0), ("物理引擎", "3", 1), ("视频模型", "6", 1)],
        "tags": ["世界模型", "物理评测", "Bad Case"],
        "reading": 4,
        "score": 90,
    },
    "evt_20260808_phys": {
        "story_id": "story_202633_phys",
        "article_type": "deep_dive",
        "headline": "1.3B流式模型开始承接14B教师的物理先验",
        "dek": "PhyS把12万条真实物理交互视频、14B教师模型和1.3B因果DiT连接成一条蒸馏链路。目标不是只生成更真实的片段，而是在轻量流式推理中保留物理状态。",
        "one_line_takeaway": "物理视频数据、时序奖励和教师蒸馏正在形成轻量流式世界模型的完整优化路径。",
        "category": "世界模型",
        "what": "PhyS构建12万条真实物理交互视频，覆盖刚体、软体、流体和相变，并附带物体属性与因果状态转移描述。方法先向14B双向DiT教师注入物理先验，再蒸馏到1.3B因果DiT；作者报告其PhysicsIQ相对教师提升18.2%，相对三类流式基线分别提升23.7%、14.8%和31.4%。",
        "why": "流式世界模型必须同时面对长时误差累积、推理成本和物理状态漂移。PhyS把真实数据、物理奖励和小模型蒸馏放在同一流程中，提供了一条比单纯扩大模型更接近部署约束的路线。",
        "take": "这项工作对视频模型团队的价值，在于展示了物理能力可以被单独组织成数据和奖励，再向轻量模型迁移。下一步应验证PhysicsIQ提升是否对应开放域视频中可感知的稳定性收益。",
        "how": "训练流程先让双向教师吸收带因果描述的真实物理视频，再通过时序奖励和蒸馏把知识迁移到少步生成的因果DiT，从而支持逐步延展的视频状态预测。",
        "watch": ["长时间滚动生成时，物理优势能保持多久？", "PhysicsIQ提升能否转化为真实产品Bad Case的下降？"],
        "numbers": [("物理视频", "120K", 0), ("教师模型", "14B", 1), ("学生模型", "1.3B", 1)],
        "tags": ["世界模型", "物理先验", "模型蒸馏"],
        "reading": 4,
        "score": 88,
    },
    "evt_20260811_beyondpixels_auto": {
        "story_id": "story_202633_beyondpixels",
        "article_type": "brief",
        "headline": "视频潜变量正在变成4D资产接口",
        "dek": "Latent-to-4D尝试绕过“先生成视频、再重建3D”的串行流程，直接把共享VAE的视频模型潜变量映射为显式4D表示。",
        "one_line_takeaway": "视频模型的内部表示，可能成为连接内容生成与4D资产生产的新接口。",
        "category": "空间与4D",
        "what": "论文提出Latent-to-4D，把视频模型最终去噪潜变量直接映射到显式4D表示。作者报告，同一检查点可在共享VAE的多个视频扩散模型之间复用，并以约1,000条重建片段完成训练。",
        "why": "如果潜变量能够跨模型复用，视频生成和4D资产生产之间就可能少一次像素级重建，降低工作流长度，并为虚拟场景、运镜和空间编辑提供统一接口。",
        "take": None,
        "how": "方法依赖共享VAE，将最终潜变量送入4D解码器，而不是从生成视频重新估计空间结构。",
        "watch": ["跨不同VAE家族能否迁移？", "投影指标提升是否对应真实4D几何质量？"],
        "numbers": [("训练片段", "≈1K", 2), ("DINO-F1提升", "+2.88—5.81", 2)],
        "tags": ["视频生成", "4D资产", "空间表征"],
        "reading": 2,
        "score": 84,
    },
    "evt_20260806_emoworld": {
        "story_id": "story_202633_emoworld",
        "article_type": "brief",
        "headline": "情绪控制开始被拆成氛围、线索与时间曲线",
        "dek": "EmoWorld在冻结的视频DiT上分离三类情绪控制，并报告在Wan2.2上的多项代理指标提升，为表演、短剧和MV提供更细粒度的控制思路。",
        "one_line_takeaway": "“生成某种情绪”正在从抽象提示词，变成可拆分、可调节的视觉控制维度。",
        "category": "视频生成",
        "what": "EmoWorld在冻结Video DiT的前提下，分别控制整体氛围、情绪语义线索和情绪随时间的变化。论文覆盖27类情绪及文生视频、图生视频设置；作者报告三类模块均带来对应代理指标提升。",
        "why": "情绪视频的失败往往不是完全没有情绪，而是氛围、人物线索和变化节奏混在一起。拆分控制维度有利于建立更明确的创作参数与专项评测。",
        "take": None,
        "how": "方法不更新基础生成器参数，而是在冻结的Video DiT上增加解耦的情绪控制模块。",
        "watch": ["代理指标与真实观众偏好是否一致？", "27类情绪在复杂叙事中的可区分度如何？"],
        "numbers": [("情绪类别", "27", 2), ("氛围对齐", "+19%", 1), ("情绪波动", "-48%", 1)],
        "tags": ["视频生成", "情绪控制", "内容创意"],
        "reading": 2,
        "score": 79,
    },
    "evt_20260810_sekai2": {
        "story_id": "story_202633_sekai2",
        "article_type": "brief",
        "headline": "长视频世界数据开始同时记录轨迹与语义",
        "dek": "Sekai2发布2,826小时长视频数据，为片段配套相机轨迹和分层时序标注，并补充具有回环与重访的全景序列。",
        "one_line_takeaway": "世界模型数据正从大规模视频堆积，转向相机运动、时序语义和空间重访的联合记录。",
        "category": "世界模型",
        "what": "Sekai2包含128,892个片段、总计2,826小时，来自10,428个源视频并覆盖113个国家或地区。每个片段带相机轨迹和分层时序标注，另含982段具有回环和重访的全景序列。",
        "why": "长视频与世界模型不仅需要更多画面，还需要知道相机如何移动、事件何时发生、空间是否被再次访问。这样的联合标注更接近训练世界状态和相机控制所需的数据结构。",
        "take": None,
        "how": "数据把长视频片段、相机轨迹、分层时序语义和全景探索序列组织到同一资源中。",
        "watch": ["数据开放范围与许可条件如何？", "加入轨迹标注后，对相机控制和长时一致性的实际增益多大？"],
        "numbers": [("视频片段", "128,892", 0), ("总时长", "2,826h", 0), ("国家/地区", "113", 0)],
        "tags": ["世界模型", "长视频", "相机轨迹"],
        "reading": 2,
        "score": 77,
    },
    "evt_20260806_robustwam": {
        "story_id": "story_202633_robustwam",
        "article_type": "brief",
        "headline": "视频生成先验正在进入机器人行动模型",
        "dek": "Robust-WAM保留视频生成模型的VAE路径，并增加语义前视目标来对齐动作表示。作者报告其在分布外仿真和真实机器人中改善多个基线。",
        "one_line_takeaway": "视频生成预训练的价值，正在从预测画面延伸到机器人的状态理解与动作选择。",
        "category": "具身智能",
        "what": "Robust-WAM保留视频生成模型的VAE生成路径，并在动作流上增加轻量语义前视对齐。作者报告，该方法在分布外仿真和真实机器人设置中提升多个WAM基线的成功率，同时不牺牲分布内表现。",
        "why": "它展示了视频生成先验如何进入机器人控制：生成路径保留对世界状态的建模，语义前视则约束动作表示。对具身产品而言，这是一条把视觉基础模型资产迁移到行动模型的具体路线。",
        "take": None,
        "how": "模型联合使用生成式VAE路径和动作语义前视目标，使视觉世界状态与后续动作在表征空间中对齐。",
        "watch": ["真实机器人任务规模和具体成功率是多少？", "提升来自生成先验还是语义前视目标？"],
        "numbers": [],
        "tags": ["具身智能", "世界模型", "机器人控制"],
        "reading": 2,
        "score": 75,
    },
}


TOP_EVENT_IDS = [
    "evt_20260810_scivbench_auto",
    "evt_20260807_wananimate2",
    "evt_20260809_logishot",
    "evt_20260806_gauge",
    "evt_20260808_phys",
]


TIMELINE = [
    {"day": "THU", "date": "08.06", "tone": "purple", "summary": "物理评测、情绪控制与机器人行动模型在同一天推进。", "event_ids": ["evt_20260806_gauge", "evt_20260806_emoworld", "evt_20260806_robustwam"]},
    {"day": "FRI", "date": "08.07", "tone": "blue", "summary": "角色动画的端到端驱动与蒸馏权重正式开放。", "event_ids": ["evt_20260807_wananimate2"]},
    {"day": "SAT", "date": "08.08", "tone": "cyan", "summary": "真实物理视频开始被系统性蒸馏进轻量流式世界模型。", "event_ids": ["evt_20260808_phys"]},
    {"day": "SUN", "date": "08.09", "tone": "orange", "summary": "跨镜头生成开始显式维护前序镜头的视觉记忆。", "event_ids": ["evt_20260809_logishot"]},
    {"day": "MON", "date": "08.10", "tone": "pink", "summary": "科学机制评测与带轨迹的世界数据共同补齐可验证结构。", "event_ids": ["evt_20260810_scivbench_auto", "evt_20260810_sekai2"]},
    {"day": "TUE", "date": "08.11", "tone": "green", "summary": "视频模型潜变量被直接复用为4D世界生成接口。", "event_ids": ["evt_20260811_beyondpixels_auto"]},
    {"day": "WED", "date": "08.12", "tone": "muted", "summary": "完成检查，暂无达到阈值的事件。", "event_ids": []},
]


TRENDS = [
    {
        "trend_id": "trend_measurable_correctness",
        "label": "机制正确性",
        "headline": "可测量的机制正确性",
        "summary": "Sci-VBench、GAUGE与PhyS把科学因果、真实物理量和流式物理状态连接成评测—数据—优化链路。",
        "status": "升温",
        "score": 95,
        "delta": "+18",
        "maturity": 58,
        "impact": 88,
        "tone": "purple",
        "event_ids": ["evt_20260810_scivbench_auto", "evt_20260806_gauge", "evt_20260808_phys"],
    },
    {
        "trend_id": "trend_controllable_video_workflow",
        "label": "可控视频",
        "headline": "可控视频工作流",
        "summary": "Wan-Animate-2、LogiShot与EmoWorld分别推进动作与视角、跨镜头记忆和情绪时序控制。",
        "status": "升温",
        "score": 91,
        "delta": "+16",
        "maturity": 67,
        "impact": 80,
        "tone": "blue",
        "event_ids": ["evt_20260807_wananimate2", "evt_20260809_logishot", "evt_20260806_emoworld"],
    },
    {
        "trend_id": "trend_video_to_world_action",
        "label": "空间与行动",
        "headline": "视频先验进入空间与行动接口",
        "summary": "Latent-to-4D与Robust-WAM显示，视频生成表征正被直接复用于4D资产和机器人行动模型。",
        "status": "形成",
        "score": 85,
        "delta": "+12",
        "maturity": 39,
        "impact": 74,
        "tone": "cyan",
        "event_ids": ["evt_20260811_beyondpixels_auto", "evt_20260806_robustwam"],
    },
    {
        "trend_id": "trend_trajectory_world_data",
        "label": "世界数据",
        "headline": "带轨迹的世界数据资产",
        "summary": "Sekai2把相机轨迹、时序语义和空间重访写进同一份长视频数据结构。",
        "status": "待验证",
        "score": 76,
        "delta": "+08",
        "maturity": 31,
        "impact": 62,
        "tone": "orange",
        "event_ids": ["evt_20260810_sekai2"],
    },
]


def section(text: str, claim_ids: list[str], statement_type: str) -> dict:
    return {"text": text, "claim_ids": claim_ids, "statement_type": statement_type}


ROUTE_CATEGORY = {
    "frontier.video_generation": "视频生成",
    "frontier.image_asset": "图像与资产",
    "frontier.world_model": "世界模型",
    "frontier.embodied_ai": "具身智能",
    "frontier.multimodal_agent": "多模态 Agent",
    "visual_value.evaluation": "评测与标准",
    "visual_value.spatial_camera": "空间与4D",
}

# Editorial headlines may be shorter than the canonical event title, but they
# must not introduce a claim that is absent from the verified evidence set.
VERIFIED_HEADLINE_OVERRIDES = {
    "evt_20260812_tencent_q2_ai": "腾讯财报披露AI相关预付款用途与收入增长",
}


EXPANDED_STORY_COPY = {
    "evt_20260812_tencent_q2_ai": {
        "what": (
            "腾讯在2026年第二季度财报中披露：收入为2,048亿元，同比增长11%；毛利润为1,184亿元，同比增长13%；"
            "非国际财务报告准则净利润为706亿元，同比增长9%。同期资本开支为528亿元，同比增长176%。\n\n"
            "现金流层面，公司当季录得138亿元负自由现金流。腾讯解释，经营现金流中包含大额AI相关预付款，"
            "用途覆盖混元模型增强、WorkBuddy与CodeBuddy推理、微信AI项目、产品与服务中的AI能力建设，以及云服务外部需求。"
            "若剔除计算资源采购预付款，公司披露的自由现金流为376亿元。\n\n"
            "业务侧，营销服务收入为436亿元，同比增长22%。腾讯将增长归因于AI广告推荐模型、自动化投放方案AIM+和微信生态闭环营销能力等因素。"
        ),
        "why": (
            "这组数据把腾讯的AI投入从抽象战略转成了可以观察的经营变量：资本开支、计算资源预付款和自由现金流同时发生变化，"
            "说明AI投入已经进入模型、应用和基础设施的联合建设阶段，而不只是单一模型研发。\n\n"
            "对视觉与视频产品策略而言，更值得关注的是投入如何进入商业链路。财报已经给出广告推荐、自动化投放、微信生态营销和云服务需求等落点，"
            "但没有单独披露视频生成业务收入，也不能据此把视频号观看增长直接归因于AI。后续判断应继续追踪AI投入对应的收入、客户采用和内容生产效率。"
        ),
        "take": (
            "对模型团队来说，这份财报的价值不是证明某个视频模型已经商业成功，而是提供了一套观察大厂AI战略的经营框架："
            "投入看资本开支与预付款，应用看产品调用和用户采用，变现看广告、云与企业服务。三条证据需要同时成立，才能把技术投入写成商业化结论。"
        ),
        "how": (
            "腾讯将AI进展分为智能、应用和基础设施三个层次：模型侧推进混元系列，应用侧覆盖办公、编程和微信AI项目，"
            "基础设施侧扩大计算资源采购。财报中的现金流与资本开支数据，为这套三层投入提供了财务侧证据。"
        ),
        "watch": [
            "后续季度是否开始单独披露AI产品或视觉生成相关收入、客户数与调用规模？",
            "资本开支和AI预付款上升后，广告、云服务与企业应用的增量回报能否持续体现？",
        ],
        "numbers": [("季度收入", "2,048亿元", 0), ("资本开支", "528亿元", 0), ("营销服务收入", "436亿元", 0)],
        "visual_data": {
            "type": "bar",
            "title": "2026年第二季度经营规模",
            "unit": "亿元",
            "note": "同一季度的规模对照，仅用于帮助阅读量级，不代表增长率比较。",
            "series": [
                {"label": "季度收入", "value": 2048},
                {"label": "资本开支", "value": 528},
                {"label": "营销服务收入", "value": 436},
            ],
        },
        "reading": 6,
    },
    "evt_20260813_lerobot_data_loop": {
        "what": (
            "Hugging Face与Amazon发布的官方方案把机器人数据记录、数据存储、策略训练和部署组织进同一条工作流。"
            "其中，LeRobot承担机器人数据与策略训练相关环节，Hugging Face Storage Buckets用于衔接数据资产，Strands Agents负责工作流编排。\n\n"
            "这次材料的重点不是发布一个新的机器人基础模型，而是把原本分散的工具组件组合成可重复的数据闭环。"
            "官方材料尚未提供大规模生产环境中的成本、成功率或训练周期对比。"
        ),
        "why": (
            "具身智能的迭代速度往往受制于数据采集、版本管理、训练和回部署之间的断点。把这些环节连接起来，"
            "意味着团队可以更快地把新采集的失败样本重新送入训练，并形成从真实操作到策略更新的持续循环。\n\n"
            "产品价值目前主要体现在开发工具链，而不是机器人能力已经发生跃迁。是否具备规模化价值，仍要观察数据吞吐、权限管理、训练成本、"
            "不同硬件适配以及回部署后的成功率变化。"
        ),
        "take": "这条信号适合归入产品发布与公司动作，因为它主要回答“开发者现在能使用什么工作流”，而不是提出一种新的机器人学习方法。",
        "how": "工作流以机器人交互数据为起点，经由统一存储进入LeRobot训练环节，再将策略部署回设备；Agent编排用于降低跨工具调用和任务衔接的人工成本。",
        "watch": ["是否出现公开的端到端运行成本、数据规模和训练周期？", "该闭环能否适配多种机器人硬件和企业权限体系？"],
        "reading": 5,
    },
    "evt_20260817_gaussiandwmpp": {
        "what": (
            "GaussianDWM++提出以3D Gaussian为核心的驾驶世界表示，把场景理解、语言推理、可控4D编辑和多模态生成放进同一框架。"
            "方法将Qwen与SigLIP视觉语言特征蒸馏进3D Gaussian primitives，形成开放词汇的Gaussian语义场。\n\n"
            "随后，geometry-aware Gaussian adapter通过分层选择和文本条件交叉注意力，将密集Gaussian压缩为world tokens，"
            "并使用KL对齐目标连接Gaussian token与图像基础模型token。作者展示的控制能力包括天气条件生成和动态车辆编辑，并表示将公开代码与数据。"
        ),
        "why": (
            "多数驾驶生成方法擅长条件视频生成，却未必具备显式3D结构、语言定位和可编辑的世界状态。GaussianDWM++尝试把这些能力放在统一空间表示中，"
            "使“理解场景”和“修改场景”不再是完全分离的模型链路。\n\n"
            "如果其表示能够稳定复用，可能服务于驾驶仿真、规划推理和可控场景生产。但当前SOTA结论来自作者实验，代码与数据仍待发布，"
            "因此还不能把论文结果直接等同于开放环境中的真实驾驶可靠性。"
        ),
        "take": "对视频与世界模型团队，最值得借鉴的是把语言特征、3D结构和生成控制对齐到同一token接口，而不是只追加一个后处理编辑模块。",
        "how": "核心链路由Gaussian tokenizer、geometry-aware adapter、Gaussian—image token对齐和指令控制生成组成，目标是让空间表示同时支持理解、推理和编辑。",
        "watch": ["代码和数据公开后，语言定位与4D编辑能否被独立复现？", "在长时序、遮挡和复杂交通参与者下，Gaussian表示是否保持一致？"],
        "reading": 6,
    },
    "evt_20260817_mllm_video_correction": {
        "what": (
            "论文提出一个无需重新训练的视频生成中途纠偏框架，把多模态大模型反馈直接接入扩散采样循环。"
            "它针对缺失对象、属性错误和动作不匹配等语义偏差，不再只在采样前优化提示词或在生成后进行二次修复。\n\n"
            "框架包含两个模块：Semantic Assessment Supervisor从生成中间状态制作预览帧，进行语义评估和偏差诊断；"
            "Semantic Modification Assistant则通过可控的latent trajectory intervention修正语义漂移。作者报告，该方法在不修改基础模型参数的情况下改善语义对齐、视觉质量和时间一致性。"
        ),
        "why": (
            "视频模型在生成早期发生的对象遗漏或动作偏差，会在后续帧中被持续放大。中途检测与干预提供了一条不同于重新训练基础模型的优化路径，"
            "尤其适合验证指令遵循和语义一致性问题。\n\n"
            "代价是推理链路引入额外的预览、MLLM判断和latent干预，可能增加延迟与成本。摘要没有给出可直接用于产品决策的成本数据，"
            "也需要继续检查MLLM误判是否会破坏原本正确的画面。"
        ),
        "take": "可以先把该方案视为高价值Bad Case修复器，而不是默认开启的全量生成步骤：优先用于复杂多主体、动作关系和属性约束较强的提示词。",
        "how": "生成过程中周期性提取中间预览，由MLLM诊断与文本条件的偏差，再通过latent轨迹干预把修正信号送回扩散过程。",
        "watch": ["每次MLLM反馈增加多少时延与推理成本？", "不同基础视频模型和复杂提示词上的收益是否稳定？"],
        "reading": 6,
    },
    "evt_20260817_calibench": {
        "what": (
            "CaliBench不只判断单条视频“像不像真实物理”，而是检查多次生成能否复现一个物理事件应有的结果分布。"
            "它把结果映射到可解释的离散空间，例如Galton板落点、骰子点数、纸牌花色、彩票结果和轮盘颜色，再直接与已知参考分布比较。\n\n"
            "基准把表现拆成两个维度：scorability衡量生成结果能否被明确判定，calibration衡量生成分布与参考分布的总变差距离。"
            "作者测试9个场景、6个图生视频模型，每个组合生成32次。论文报告多数场景—模型组合显著失准，模型经常把概率集中在少数结果上；没有一个模型在全部9个场景中占优。"
        ),
        "why": (
            "世界模型不仅要生成一个视觉上合理的未来，还需要在存在随机性的事件中给出合理的多种可能结果。"
            "如果模型反复生成同一种骰子点数或把轮盘结果集中到少数颜色，它可能学会了视觉模板，却没有校准真实世界的不确定性。\n\n"
            "这为视频世界模型增加了一个重要评测维度：从单样本质量转向结果分布。不过每个组合只有32次采样，"
            "卡方检验主要能发现较大的偏差；该基准也不能替代对连续动力学、长时因果和复杂交互的评测。"
        ),
        "take": "团队可以借鉴其“结果空间＋重复采样”思路，为碰撞、抛物、液体和角色动作建立内部校准测试，而不只比较一条最佳样片。",
        "how": "基准使用已知闭式参考分布、总变差距离和卡方检验来评估模型；同时单独记录不可明确判分的生成，避免低质量样本被错误计入概率分布。",
        "watch": ["增加采样次数后，各模型的失准结论是否稳定？", "校准训练或奖励设计能否改善分布，同时保持单条视频质量？"],
        "numbers": [("测试场景", "9", 0), ("图生视频模型", "6", 0), ("每组生成", "32", 0)],
        "reading": 6,
    },
}


def source_label(url: str) -> str:
    if "github.com" in url:
        return "官方 GitHub"
    if "arxiv.org" in url:
        return "arXiv 原文"
    if "hkexnews.hk" in url:
        return "港交所公告"
    if "tencent.com" in url:
        return "腾讯官方财报"
    if "huggingface.co" in url:
        return "Hugging Face 官方文章"
    return "查看官方原文"


def generic_copy(event: dict, review_item: dict, rank: int) -> dict:
    title = VERIFIED_HEADLINE_OVERRIDES.get(event["event_id"], event["canonical_title"])
    claims = review_item["claims"]
    fact = event["fact_summary"]
    reason = review_item["decision_reason"]
    entity = event["primary_entity"]["name"]
    digest = hashlib.sha256(event["event_id"].encode()).hexdigest()[:12]
    copy = {
        "story_id": f"story_{digest}",
        "article_type": "deep_dive" if rank < 5 else "brief",
        "headline": title,
        "dek": claims[0]["text"] if claims else fact,
        "one_line_takeaway": reason,
        "category": ROUTE_CATEGORY.get(event["primary_route"], "视觉智能"),
        "what": fact,
        "why": reason,
        "take": f"这条信号值得围绕{entity}的真实能力边界、成本与工作流适配继续验证。" if rank < 5 else None,
        "how": claims[-1]["text"] if claims else fact,
        "watch": ["是否出现独立复现或真实产品数据？", "能力变化能否进入稳定工作流？"],
        "numbers": [],
        "visual_data": None,
        "tags": [ROUTE_CATEGORY.get(event["primary_route"], "视觉智能"), entity],
        "reading": 4 if rank < 5 else 2,
        "score": max(70, 96 - rank * 3),
    }
    copy.update(EXPANDED_STORY_COPY.get(event["event_id"], {}))
    return copy


def build_timeline(events: list[dict], story_by_event: dict[str, dict]) -> list[dict]:
    event_dates = {
        item["event_id"]: datetime.fromisoformat(item["event_at"].replace("Z", "+00:00")).date()
        for item in events
    }
    dates = list(event_dates.values())
    start = min(dates)
    end = max(max(dates), start + timedelta(days=6))
    start = end - timedelta(days=6)
    tones = ["purple", "blue", "cyan", "orange", "pink", "green", "muted"]
    timeline = []
    for offset in range(7):
        day = start + timedelta(days=offset)
        # A rolling seven-day window can touch eight calendar dates when a
        # source only provides day-level timestamps at the opening boundary.
        # Keep every approved event by folding such boundary records into the
        # nearest endpoint instead of silently dropping them from the timeline.
        day_events = [
            item for item in events
            if min(end, max(start, event_dates[item["event_id"]])) == day
        ]
        represented_dates = sorted({event_dates[item["event_id"]] for item in day_events})
        date_label = day.strftime("%m.%d")
        if represented_dates and (represented_dates[0] < start or represented_dates[-1] > end):
            date_label = f"{represented_dates[0].strftime('%m.%d')}–{represented_dates[-1].strftime('%m.%d')}"
        categories = list(dict.fromkeys(ROUTE_CATEGORY.get(item["primary_route"], "视觉智能") for item in day_events))
        summary = "、".join(categories) + "出现值得关注的新信号。" if categories else "完成检查，暂无达到阈值的事件。"
        timeline.append({
            "day": day.strftime("%a").upper(), "date": date_label, "tone": tones[offset],
            "summary": summary, "event_ids": [item["event_id"] for item in day_events],
            "story_ids": [story_by_event[item["event_id"]]["story_id"] for item in day_events],
        })
    return timeline


def build_trends(events: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for event in events:
        grouped.setdefault(event["primary_route"], []).append(event)
    tones = ["purple", "blue", "cyan", "orange"]
    trends = []
    for index, (route, items) in enumerate(sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))[:4]):
        category = ROUTE_CATEGORY.get(route, "视觉智能")
        names = "、".join(item["primary_entity"]["name"] for item in items[:3])
        count = len(items)
        trends.append({
            "trend_id": "trend_" + hashlib.sha256(route.encode()).hexdigest()[:12], "label": category,
            "headline": f"{category}形成本周集中信号", "summary": f"{names}共同构成{category}方向的本周证据，需要继续观察独立复现与产品化表现。",
            "status": "升温" if count >= 2 else "待验证", "score": min(95, 74 + count * 7),
            "delta": f"+{6 + count * 4:02d}", "maturity": min(80, 30 + count * 12), "impact": min(90, 55 + count * 9),
            "tone": tones[index], "event_ids": [item["event_id"] for item in items],
        })
    return trends


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified", default=str(VERIFIED_PATH.relative_to(ROOT)))
    parser.add_argument("--review", default=str(REVIEW_PATH.relative_to(ROOT)))
    parser.add_argument("--output", default=str(OUTPUT_PATH.relative_to(ROOT)))
    parser.add_argument("--static", default=str(STATIC_PATH.relative_to(ROOT)))
    args = parser.parse_args()
    verified_path = ROOT / args.verified
    review_path = ROOT / args.review
    output_path = ROOT / args.output
    static_path = ROOT / args.static if args.static else None
    verified = json.loads(verified_path.read_text())
    review = json.loads(review_path.read_text())
    events = {item["event_id"]: item for item in verified["intelligence_events"]}
    claims = {item["claim_id"]: item for item in verified["evidence_claims"]}
    reviews = {
        item["event"]["event_id"]: item
        for item in review["records"]
        if item.get("decision") == "include"
    }

    ordered_events = sorted(events.values(), key=lambda item: ({"priority.p0": 0, "priority.p1": 1, "priority.p2": 2}.get(item["priority"], 9), item["event_at"]))
    stories = []
    for rank, event in enumerate(ordered_events):
        event_id = event["event_id"]
        review_item = reviews[event_id]
        copy = STORY_COPY.get(event_id) or generic_copy(event, review_item, rank)
        claim_ids = event["claim_ids"]
        source = {
            "source_id": event["primary_source_id"],
            "label": source_label(review_item["url"]),
            "url": review_item["url"],
            "role": "primary",
        }
        key_numbers = [
            {"label": label, "value": value, "claim_id": claim_ids[claim_index]}
            for label, value, claim_index in copy["numbers"]
        ]
        story = {
            "schema_version": "0.2",
            "record_type": "editorial_story",
            "story_id": copy["story_id"],
            "article_type": copy["article_type"],
            "headline": copy["headline"],
            "dek": copy["dek"],
            "one_line_takeaway": copy["one_line_takeaway"],
            "category": copy["category"],
            "intelligence_type": (review_item.get("agent_analysis") or {}).get(
                "intelligence_type", "type.technology_breakthrough"
            ),
            "related_event_ids": [event_id],
            "primary_event_id": event_id,
            "what_happened": section(copy["what"], claim_ids, "fact"),
            "why_it_matters": section(copy["why"], claim_ids, "judgment"),
            "our_take": section(copy["take"], claim_ids, "judgment") if copy["take"] else None,
            "under_the_hood": section(copy["how"], claim_ids, "fact"),
            "limitations": section(event["limitations"], claim_ids, "judgment"),
            "watch_next": copy["watch"],
            "key_numbers": key_numbers,
            "visual_data": copy.get("visual_data"),
            "source_links": [source],
            "primary_tags": copy["tags"],
            "confidence": event["confidence"],
            "priority": event["priority"],
            "reading_time_minutes": copy["reading"],
            "editorial_status": "fact_checked",
            "drafted_by": "llm",
            "reviewer": None,
            "reviewed_at": None,
            "revision_note": "一手来源已核验；编辑判断仍需发布者确认。",
            "editorial_score": copy["score"],
            "published_at": event["event_at"],
        }
        stories.append(story)

    story_by_event = {story["primary_event_id"]: story for story in stories}
    top_event_ids = [item for item in verified["editorial_selection"]["top_event_ids"] if item in story_by_event][:5]
    top_story_ids = [story_by_event[event_id]["story_id"] for event_id in top_event_ids]
    # The verified bundle, rather than legacy hand-written copy, owns the issue
    # hierarchy. This also guarantees that an issue with exactly five formal
    # events presents all five as deep dives.
    for story in stories:
        is_deep_dive = story["primary_event_id"] in top_event_ids
        story["article_type"] = "deep_dive" if is_deep_dive else "brief"
        story["reading_time_minutes"] = max(story["reading_time_minutes"], 5) if is_deep_dive else 2
    exact_issue_one = set(events) == set(STORY_COPY)
    timeline = [{**day, "story_ids": [story_by_event[event_id]["story_id"] for event_id in day["event_ids"]]} for day in TIMELINE] if exact_issue_one else build_timeline(list(events.values()), story_by_event)
    trends = TRENDS if exact_issue_one else build_trends(list(events.values()))
    trend_labels = "、".join(trend["label"] for trend in trends)
    industry_count = sum(
        story["intelligence_type"] == "type.industry_market" for story in stories
    )
    current_signal_set = {
        "evt_20260812_tencent_q2_ai",
        "evt_20260817_calibench",
        "evt_20260817_gaussiandwmpp",
        "evt_20260818_hydra0",
        "evt_20260819_kuaishou_q2_ai",
    }
    if current_signal_set.issubset(events):
        weekly_thesis = (
            "8月12—19日，腾讯披露AI相关预付款用途，快手披露AIGC短视频营销素材支出同比增长超过70%；"
            "技术侧，CaliBench、GaussianDWM++与Hydra-0分别指向物理校准、可控4D驾驶场景和机器人控制。"
        )
    else:
        weekly_thesis = (
            f"本周{len(stories)}条已核验事件主要覆盖{trend_labels}；"
            f"其中{industry_count}条企业披露补充了AIGC商业化与AI相关投入信号。"
        )
    trend_one_line = (
        f"事实：本周正式事件集中在{trend_labels}，并包含{industry_count}条行业市场信号；"
        "判断：下一阶段竞争将进一步转向可验证的时空稳定性、动作响应与真实任务价值。"
    )
    period_start = datetime.fromisoformat(min(item["event_at"] for item in events.values()).replace("Z", "+00:00")).date()
    period_end = datetime.fromisoformat(max(item["event_at"] for item in events.values()).replace("Z", "+00:00")).date()
    issue_year, issue_week, _ = period_end.isocalendar()

    output = {
        "schema_version": "0.2",
        "record_type": "weekly_issue_editorial_bundle",
        "issue": {
            "issue_id": f"issue_{issue_year}_w{issue_week:02d}",
            "title": f"本周视觉行业情报 · {issue_year}年第{issue_week:02d}周",
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "updated_at": verified["verified_at"],
            "weekly_thesis": weekly_thesis,
            "trend_one_line": trend_one_line,
            "thesis_dek": f"本周{len(stories)}个正式事件经过原文核验，形成{len(trends)}条值得持续观察的趋势判断。",
            "lead_story_id": top_story_ids[0],
            "top_story_ids": top_story_ids,
            "brief_story_ids": [story["story_id"] for story in stories if story["article_type"] == "brief"],
            "story_count": len(stories),
            "deep_dive_count": sum(story["article_type"] == "deep_dive" for story in stories),
            "brief_count": sum(story["article_type"] == "brief" for story in stories),
            "reviewed_count": verified["summary"]["p1_reviewed"],
            "watchlist_count": verified["summary"]["watchlist_events"],
            "excluded_count": verified["summary"].get("excluded_events", 0),
        },
        "presentation": {
            "timeline_days": timeline,
            "trend_radar": trends,
        },
        "editorial_stories": stories,
        "evidence_claims": [claims[claim_id] for story in stories for claim_id in events[story["primary_event_id"]]["claim_ids"]],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    if static_path and static_path.exists():
        html = static_path.read_text()
        css_path = os.path.relpath(ROOT / "app" / "globals.css", static_path.parent)
        html = re.sub(r'href="[^"]*app/globals\.css(?:\?v=\d+)?"', f'href="{css_path}?v=14"', html, count=1)
        payload = json.dumps(output, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        embedded = f'<!-- ISSUE_DATA_START --><script id="issue-data" type="application/json">{payload}</script><!-- ISSUE_DATA_END -->'
        html, replacements = re.subn(r"<!-- ISSUE_DATA_START -->.*?<!-- ISSUE_DATA_END -->", lambda _: embedded, html, count=1, flags=re.S)
        if replacements != 1:
            raise RuntimeError("static prototype is missing issue data markers")
        static_path.write_text(html)
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
