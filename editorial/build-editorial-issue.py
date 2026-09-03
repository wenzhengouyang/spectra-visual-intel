#!/usr/bin/env python3
"""Build an editorial issue from a human-approved verified event bundle.

Known Issue 01 stories retain their edited copy. New events use a conservative
fallback that only promotes verified facts and keeps judgment visibly separate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import textwrap
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
VERIFIED_PATH = ROOT / "verification/runs/p1-verified-events-v0.2.json"
REVIEW_PATH = ROOT / "verification/p1-review.v0.1.json"
OUTPUT_PATH = ROOT / "editorial/runs/issue-01-editorial-stories-v0.2.json"
STATIC_PATH = ROOT / "visual-intelligence-prototype.html"
COVER_MANIFEST_PATH = ROOT / "editorial/cover-manifest.json"
REPORT_TIMEZONE = ZoneInfo("Asia/Shanghai")


def load_cover_manifest(path: Path = COVER_MANIFEST_PATH) -> dict:
    if not path.exists():
        return {"covers": [], "fallbacks": {}}
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest.get("covers"), list):
        raise ValueError("cover manifest must contain a covers list")
    return manifest


def semantic_cover_motif(headline: str, category: str) -> str:
    text = f"{headline} {category}".lower()
    if any(term in text for term in ("法律", "legal", "合规")):
        return "document"
    if any(term in text for term in ("治理", "数据层", "安全", "governance")):
        return "layers"
    if any(term in text for term in ("运营", "组织", "客户回报", "roi")):
        return "workflow"
    if any(term in text for term in ("芯片", "算力", "集群", "gpu")):
        return "compute"
    return "signal"


def write_semantic_cover(headline: str, category: str, credit: str, target_dir: Path) -> str:
    """Create a deterministic, topic-specific editorial diagram for sources without art."""
    target_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(f"{headline}|{category}|{credit}".encode()).hexdigest()[:12]
    filename = f"story-auto-{digest}.svg"
    target = target_dir / filename
    accent_by_motif = {
        "document": "#9aaee8", "layers": "#72c1bd", "workflow": "#c5a3e6",
        "compute": "#e0a36f", "signal": "#94a3b8",
    }
    motif = semantic_cover_motif(headline, category)
    accent = accent_by_motif[motif]
    lines = textwrap.wrap(headline, width=18, break_long_words=False)[:3]
    title = "".join(
        f'<text x="112" y="{430 + index * 82}" class="title">{xml_escape(line)}</text>'
        for index, line in enumerate(lines)
    )
    motif_markup = {
        "document": '<rect x="980" y="150" width="330" height="470" rx="20" class="shape"/><path d="M1040 260h210M1040 330h210M1040 400h150" class="line"/><circle cx="1145" cy="530" r="64" class="node"/>',
        "layers": '<path d="M970 250l190-100 190 100-190 100zM970 390l190-100 190 100-190 100zM970 530l190-100 190 100-190 100z" class="shape"/><circle cx="1160" cy="390" r="34" class="node"/>',
        "workflow": '<circle cx="1160" cy="390" r="175" class="shape"/><circle cx="1160" cy="215" r="38" class="node"/><circle cx="1315" cy="470" r="38" class="node"/><circle cx="1005" cy="470" r="38" class="node"/><path d="M1198 225c85 25 135 82 125 178M1288 504c-63 67-141 78-220 22M1028 426c-9-85 31-151 100-190" class="line"/>',
        "compute": '<rect x="990" y="220" width="210" height="210" rx="18" class="shape"/><path d="M1040 270h110v110h-110zM1200 325h120M1200 365h120M1200 405h120M1095 430v105M1055 430v105M1135 430v105" class="line"/><g class="nodes"><circle cx="1260" cy="325" r="12"/><circle cx="1320" cy="325" r="12"/><circle cx="1260" cy="365" r="12"/><circle cx="1320" cy="365" r="12"/><circle cx="1260" cy="405" r="12"/><circle cx="1320" cy="405" r="12"/></g>',
        "signal": '<path d="M990 560l90-210 95 90 165-250" class="line strong"/><circle cx="1080" cy="350" r="28" class="node"/><circle cx="1175" cy="440" r="28" class="node"/><circle cx="1340" cy="190" r="28" class="node"/>',
    }[motif]
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" viewBox="0 0 1600 900" role="img" aria-labelledby="title desc">
<title id="title">{xml_escape(headline)}</title><desc id="desc">SPECTRA根据文章主题生成的编辑示意图</desc>
<style>.bg{{fill:#0c1118}}.grid{{stroke:#26303c;stroke-width:1}}.shape{{fill:none;stroke:{accent};stroke-width:5}}.line{{fill:none;stroke:{accent};stroke-width:5;stroke-linecap:round;stroke-linejoin:round}}.strong{{stroke-width:9}}.node{{fill:#0c1118;stroke:{accent};stroke-width:7}}.nodes{{fill:{accent}}}.kicker{{fill:{accent};font:700 25px system-ui,sans-serif;letter-spacing:4px}}.title{{fill:#eef2f7;font:700 57px system-ui,sans-serif}}.credit{{fill:#7f8b9a;font:500 22px system-ui,sans-serif}}</style>
<rect width="1600" height="900" class="bg"/><path d="M0 150h1600M0 300h1600M0 450h1600M0 600h1600M0 750h1600M200 0v900M400 0v900M600 0v900M800 0v900M1000 0v900M1200 0v900M1400 0v900" class="grid"/>
<text x="112" y="118" class="kicker">{xml_escape(category.upper())} / VERIFIED INTELLIGENCE</text>{title}
<text x="112" y="790" class="credit">来源：{xml_escape(credit)} · SPECTRA 编辑示意</text>{motif_markup}
</svg>'''
    target.write_text(svg, encoding="utf-8")
    return f"assets/editorial/{filename}"


def resolve_cover_image(
    manifest: dict,
    event_id: str,
    intelligence_type: str,
    *,
    source_url: str = "",
    headline: str = "",
    category: str = "",
    credit: str = "",
    generated_dirs: list[Path] | None = None,
) -> dict:
    for item in manifest.get("covers", []):
        if item.get("event_id") == event_id:
            return {key: value for key, value in item.items() if key != "event_id"}
    if source_url:
        for item in manifest.get("covers", []):
            if item.get("source_url") == source_url:
                return {key: value for key, value in item.items() if key != "event_id"}
    if headline and generated_dirs:
        relative_url = ""
        for target_dir in generated_dirs:
            relative_url = write_semantic_cover(headline, category or "视觉智能", credit or "已核验来源", target_dir)
        return {
            "url": relative_url,
            "kind": "editorial_diagram",
            "label": "主题示意图",
            "credit": "SPECTRA",
            "source_url": source_url or None,
        }
    fallback_key = {
        "type.industry_market": "行业与市场",
        "type.company_strategy": "产品与公司",
        "type.product_release": "产品与公司",
    }.get(intelligence_type, "技术突破")
    fallback_url = manifest.get("fallbacks", {}).get(fallback_key) or manifest.get("fallbacks", {}).get("default")
    return {
        "url": fallback_url,
        "kind": "editorial_fallback",
        "label": "分类配图",
        "credit": "SPECTRA",
        "source_url": None,
    }


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


def reader_facing_text(value: str) -> str:
    """Remove audit-note phrasing while preserving the underlying attribution."""
    text = str(value or "").strip()
    audit_phrases = (
        "；该表述属于预测而非已验证结果",
        "，该表述属于预测而非已验证结果",
        "该表述属于预测而非已验证结果。",
    )
    for phrase in audit_phrases:
        text = text.replace(phrase, "")
    text = re.sub(r"；对“[^”]+”的更强表述不纳入正式结论", "", text)
    text = re.sub(r"([。！？]){2,}", r"\1", text)
    return text.strip()


def build_article_body(copy: dict, event: dict, claim_ids: list[str]) -> dict:
    """Project audit fields into a complete, reader-facing article."""
    judgment_parts = [copy.get("why"), copy.get("take")]
    judgment = "\n\n".join(part.strip() for part in judgment_parts if part and part.strip())
    watch_items = [str(item).strip() for item in copy.get("watch", []) if str(item).strip()]
    fact_parts = [reader_facing_text(copy["what"])]
    how = reader_facing_text(copy.get("how") or "")
    if how and how not in fact_parts[0]:
        fact_parts.append(how)
    return {
        "lead": section(reader_facing_text(copy["what"]), claim_ids, "fact"),
        "key_details": section(how, claim_ids, "fact") if how else None,
        "full_text": section("\n\n".join(fact_parts), claim_ids, "fact"),
        "fact_points": [
            reader_facing_text(item)
            for item in copy.get("fact_points", [])
            if reader_facing_text(item)
        ],
        "summary_paragraphs": [
            reader_facing_text(item)
            for item in copy.get("summary_paragraphs", [])
            if reader_facing_text(item)
        ],
        "judgment": section(judgment, claim_ids, "judgment"),
        "evidence_boundary": section(event["limitations"], claim_ids, "judgment"),
        "watch_next": watch_items,
        "reading_mode": "complete_in_page",
    }


ROUTE_CATEGORY = {
    "frontier.video_generation": "视频生成",
    "frontier.image_asset": "图像与资产",
    "frontier.world_model": "世界模型",
    "frontier.embodied_ai": "具身智能",
    "frontier.multimodal_agent": "多模态 Agent",
    "extended.foundation_multimodal": "基础模型与多模态",
    "extended.ai_agent_tools": "AI Agent与工具",
    "extended.compute_data": "算力与数据",
    "extended.open_source_ecosystem": "开源生态",
    "industry.marketing": "行业与市场",
    "industry.short_drama": "行业与市场",
    "visual_value.evaluation": "评测与标准",
    "visual_value.spatial_camera": "空间与4D",
}

CORE_VISUAL_ROUTES = {
    "frontier.video_generation",
    "frontier.image_asset",
    "frontier.world_model",
    "frontier.embodied_ai",
    "visual_value.evaluation",
    "visual_value.spatial_camera",
    "industry.marketing",
    "industry.short_drama",
}


def domain_scope(route: str) -> str:
    return "scope.visual_core" if route in CORE_VISUAL_ROUTES else "scope.ai_extended"

# Editorial headlines may be shorter than the canonical event title, but they
# must not introduce a claim that is absent from the verified evidence set.
VERIFIED_HEADLINE_OVERRIDES = {
    "evt_20260812_tencent_q2_ai": "腾讯财报披露AI相关预付款用途与收入增长",
    "evt_20260825_gemini_legal": "Google Cloud发布面向法律行业的Gemini Enterprise",
}


# P2 copy is intentionally conservative. These records have passed collection,
# routing and deduplication, but have not crossed the human fact-check gate.
# The summary may restate the source abstract; it must not present an LLM
# judgment or an unlocated metric as a verified fact.
P2_BRIEF_COPY = {
    "cand_04f007eab88ff413": {
        "headline": "Anthropic与OpenAI将亮相TechCrunch Disrupt 2026人工智能舞台",
        "summary": "TechCrunch宣布两家公司将参与Disrupt 2026的人工智能专题讨论；议程内容与具体发言仍以大会最终安排为准。",
    },
    "cand_5fe747cc48a2afaa": {
        "headline": "研究者质疑Claude Code Opus 5自动模式的提示注入防护",
        "summary": "Simon Willison转述安全研究者Johann Rehberger对Claude Code自动模式防护机制的测试与质疑；具体攻击条件和防护效果仍待阅读全文核验。",
    },
    "cand_1db654ce391bd80f": {
        "headline": "Thinking Machines联合创始人Barret Zoph转投Google",
        "summary": "TechCrunch报道称，曾短暂加入OpenAI的Barret Zoph现已加入Google；具体岗位与团队安排仍待公司信息确认。",
    },
    "cand_c2506e05ccef24ef": {
        "headline": "CLAP探索跨具身视频世界模型的零样本物理模拟",
        "summary": "论文摘要称，CLAP通过互联网规模视频数据训练跨具身视频世界模型，并探索在真实任务中的零样本部署；具体实验范围与效果仍待正文核验。",
    },
    "cand_b244787caf1969ad": {
        "headline": "OpenAI拟在印度ChatGPT免费版与Go版展示广告",
        "summary": "TechCrunch报道称，OpenAI计划在印度的ChatGPT免费版与Go版中引入广告；上线时间、展示范围与用户影响仍待官方说明。",
    },
    "cand_b492fc1b3f4cd856": {
        "headline": "TetherMem用查询感知记忆路由改善长视频生成",
        "summary": "论文提出一种无需额外训练的查询感知时空记忆路由方法，尝试分别处理主体与场景信息；长视频生成效果仍待正文核验。",
    },
    "cand_5380824b7af533db": {
        "headline": "Ring Forcing探索自回归视频扩散的长期记忆",
        "summary": "论文将长视频生成中的长期记忆拆分为物体再现能力与超长上下文容量，并提出相应方法；实验结果仍待正文核验。",
    },
    "cand_f8e9882e9ff07aab": {
        "headline": "RECAP-Forcing按内容新颖性组织长视频记忆",
        "summary": "论文提出按内容新颖性组织记忆的长视频生成方法，以应对有限注意力窗口带来的记忆问题；具体效果仍待正文核验。",
    },
    "cand_4729839a2bd28b22": {
        "headline": "HUG-VIS构建面向人物理解与生成的多模态基准",
        "summary": "论文介绍一个面向人物理解与生成的多模态视频基准，覆盖情感识别、视频生成、语音克隆与视频抠图等任务；数据规模与评测结果仍待正文核验。",
    },
    "cand_5ed95eb1e72bed39": {
        "headline": "OpenAI发布Hugging Face安全事件正式报告",
        "summary": "TechCrunch称该报告梳理了多起相互关联的网络安全事件；事件细节与责任边界仍需回到OpenAI正式报告核验。",
    },
    "cand_9329b73d7920cf4c": {
        "headline": "4DGS-WAM以物体为中心连接历史状态与未来动作",
        "summary": "论文提出基于4D高斯泼溅的世界动作模型，以显式空间结构表示物体并减少重复背景处理；具体效果仍待正文核验。",
    },
    "cand_e7fb56cf759860cb": {
        "headline": "Radar让播客内容可搜索并可供AI Agent调用",
        "summary": "TechCrunch介绍Particle的播客情报平台，可转录和分析播客，并通过API与MCP提供检索能力；覆盖范围与实际可用性仍待产品页面核验。",
    },
    "cand_271ac81bd7481553": {
        "headline": "Code World Model让代码Agent承担世界状态记忆",
        "summary": "论文提出结合语言模型推理与视频生成的框架，由代码Agent记录世界状态并维持规则一致性；方法与实验结果仍待正文核验。",
    },
    "cand_710a1e206b3e96bb": {
        "headline": "Anima Anandkumar：语言已有基础模型，物理仍没有",
        "summary": "访谈围绕物理世界建模展开，并介绍其团队在天气预测模型方面的工作；相关能力、成本与精度表述仍需回到完整访谈核验。",
    },
    "cand_63734d3379a6c1e2": {
        "headline": "比尔·盖茨提出机器人税与“人类保留岗位”",
        "summary": "TechCrunch转述比尔·盖茨对AI就业影响的看法，包括机器人税和保留部分人类岗位等主张；本条属于个人观点，而非已实施政策。",
    },
    "cand_8fea707e260cdb8a": {
        "headline": "AI Agent时代，客户体验系统面临新的编排难题",
        "summary": "VentureBeat赞助内容讨论企业在旧有客户服务系统上接入AI Agent、语音AI与自动化时的编排挑战；具体实践成效仍待独立核验。",
    },
    "cand_caf00800a927a493": {
        "headline": "Agent化游戏开发被用作世界模型的可验证轨迹数据引擎",
        "summary": "论文主张，扩展世界模型不仅需要更多视频与算力，还需要能够提供可验证奖励信号的递归数据引擎；方法与实验仍待正文核验。",
    },
    "cand_19eb21e06aae80c9": {
        "headline": "EVE Online启动Python 3迁移",
        "summary": "官方工程文章介绍EVE Online从Python 2迁移至Python 3的计划，包括自动转换与人工审查；迁移进度与影响范围仍以原文为准。",
    },
    "cand_6b53e8bbd36eb719": {
        "headline": "Claude如何为AI生成文本加入水印",
        "summary": "该视频介绍令牌采样、文本水印检测与移除方法；具体技术结论和适用范围仍待观看完整演示核验。",
    },
    "cand_11f5792d28f7eb89": {
        "headline": "Google Cloud CISO：AI时代仍需坚持安全基本原则",
        "summary": "Google Cloud安全负责人在官方文章中讨论AI时代的安全基础，并主张在传统安全措施上结合AI能力；具体实践建议仍以原文为准。",
    },
    "cand_8054a194921a8a82": {
        "headline": "烤过头了？为何机器人披萨制作系统仍频频失败",
        "summary": "BBC报道关注机器人披萨制作系统持续遭遇的落地问题；当前采集记录只有来源标题，具体失败环节、案例范围与原因仍待阅读全文核验。",
    },
    "cand_44472fd7bd4eb2c0": {
        "headline": "AI算力开始被讨论为可定价与对冲的成本资产",
        "summary": "TechCrunch介绍一家尝试为AI算力建立价格与风险管理工具的创业公司；具体产品机制、客户采用和市场规模仍待核验。",
    },
    "cand_a42476accc680e1d": {
        "headline": "Cursor推出代码托管平台，开始挑战GitHub工作流",
        "summary": "TechCrunch报道称Cursor正在推出面向开发者的代码托管平台。本条属于媒体报道，功能范围、迁移条件与正式可用性仍需回到产品页面确认。",
    },
    "cand_ea49e482210e94fb": {
        "headline": "Google为Search与Gemini增加学习工具",
        "summary": "媒体报道显示，Google继续把学习功能整合进Search与Gemini，希望强化学生使用场景；具体功能覆盖和开放范围仍待官方页面核验。",
    },
    "cand_bc9dea07f47970d2": {
        "headline": "OpenAI推出面向青少年的ChatGPT安全版本",
        "summary": "TechCrunch报道称该版本加入适龄安全措施、家长控制和学习工具；年龄识别、地区范围和实际保护机制仍需核验官方说明。",
    },
    "cand_af7cb51e34dcb994": {
        "headline": "TerraPower把核能方案指向AI数据中心需求",
        "summary": "TechCrunch讨论TerraPower核电项目争取数据中心订单的潜在优势；供电能力、建设周期和已签客户仍待进一步确认。",
    },
    "cand_2343530bb6c88adc": {
        "headline": "个人创业者用Codex与ChatGPT搭建时尚品牌",
        "summary": "Lenny's Newsletter案例标题显示，一名没有工程团队的个人创业者使用Codex与ChatGPT推进品牌上线；具体工作流和效果数据仍待阅读全文核验。",
    },
    "cand_9e4daa9d592a4bcf": {
        "headline": "从业者实测Grok Bot、Grok 4.6与Cursor Origin",
        "summary": "Lenny's Newsletter发布一篇工具体验文章，比较Grok与Cursor相关产品；本条属于个人观点，能力结论和测试条件需要结合原文理解。",
    },
    "cand_ff7acc40619d7c44": {
        "headline": "Box用Gemini Embeddings 2扩展企业多模态Agent",
        "summary": "Google Cloud官方文章介绍Box如何把企业内容检索从文本RAG扩展到多模态内容与Agent场景；实施范围和客户效果仍需继续核验。",
    },
    "cand_84f379a9c91c952d": {
        "headline": "OpenAI在Hugging Face安全事件后增加模型防护措施",
        "summary": "TechCrunch报道称新增措施包含更细的开发期模型监控，以及后训练阶段的对齐与安全要求；事件经过与措施边界仍待官方来源确认。",
    },
    "cand_863666dde927dac5": {
        "headline": "MDD尝试拆分幅值与方向以加速视频生成",
        "summary": "论文摘要提出在流匹配视频模型的去噪过程中组合轻量模型与缓存信息，以降低推理成本并校正轨迹偏差；具体加速和质量结果仍待正文核验。",
    },
    "cand_16af3af343f18b23": {
        "headline": "动作条件JEPA被用于机器人执行前风险预测",
        "summary": "论文提出先预测候选动作的任务进展与物理风险，再由针对不同机器人形态的安全屏障过滤动作；真实部署效果和安全保证仍待核验。",
    },
    "cand_90e235f15a2ca8ce": {
        "headline": "GigaBrain-WBC-0.5探索人形机器人行为世界模型",
        "summary": "论文提出让因果Transformer联合预测下一动作、状态和潜在行为指令，用于处理地形与物体交互中的全身控制；实验范围仍待正文确认。",
    },
    "cand_d67218552fe3fc7a": {
        "headline": "PROBE评测机器人通过操作揭示被遮挡信息",
        "summary": "论文把需要移动物体后才能回答的问题定义为操作驱动视觉问答，并构建模拟器与评测任务；任务规模和基线表现仍待二轮核验。",
    },
    "cand_91338786878ccac3": {
        "headline": "RoomWright按使用任务生成可交互3D代码场景",
        "summary": "论文提出围绕任务所需对象、可供性和状态规则生成3D代码场景，以服务具身交互；场景质量和策略训练收益仍待验证。",
    },
    "cand_b3a00295709cf065": {
        "headline": "推理时注意力引导被用于VLA自动驾驶模型",
        "summary": "论文尝试在不重新训练的情况下，把注意力引向安全关键交通参与者并观察轨迹变化；测试规模和安全外推边界仍需结合正文判断。",
    },
    "cand_e49d3112192a2a1e": {
        "headline": "QWM尝试用世界模型增强离策略Q-learning",
        "summary": "论文摘要提出QWM，将世界模型用于提升强化学习的样本效率，同时避免直接依赖想象轨迹训练策略与价值函数；具体实验结果仍待正文核验。",
    },
    "cand_695d7d880441abdd": {
        "headline": "第一视角视频VLM综述梳理具身智能能力边界",
        "summary": "这篇综述聚焦第一视角视频中的手—物交互、时间推理与多模态表示，并讨论其在可穿戴设备、人机交互和具身系统中的挑战。",
    },
    "cand_88fcd6266cfbbc44": {
        "headline": "研究讨论连续控制世界模型的抽样验证风险",
        "summary": "论文分析代码世界模型在有限抽样中遗漏低概率关键事件的风险，并给出漏检概率与采样预算之间的理论关系；实验边界仍待阅读全文确认。",
    },
    "cand_7bfa877782fd0637": {
        "headline": "DiSCO探索黑盒文生图安全提示优化",
        "summary": "论文提出无需访问模型内部的提示优化方法，通过安全与不安全图像分布的对比信号降低有害内容生成；效果数据尚未完成二轮核验。",
    },
    "cand_dec42b16eb491fc5": {
        "headline": "对比逆动力学被用于稳定JEPA世界模型训练",
        "summary": "论文尝试从环境转移数据本身构造反崩溃信号，减少对高斯分布约束等机制的依赖；方法收益与适用范围仍需结合正文评估。",
    },
    "cand_0857cebc3df02af3": {
        "headline": "DA-WAM让未来表征直接参与自动驾驶决策",
        "summary": "论文提出把预测表示学习、动作条件未来建模和轨迹评分放入同一框架，使预测结果服务于轨迹选择；基准结果仍待原文表格核验。",
    },
    "cand_8c556f21e113f776": {
        "headline": "媒体关注MiniMax H3权重开放范围与许可限制",
        "summary": "DeepLearning.AI文章称MiniMax H3权重可免费下载，但许可包含特殊限制。本条属于媒体转述，具体条款仍需回溯模型官方页面。",
    },
    "cand_1582a2b21e0e0bd7": {
        "headline": "HarnessEval-W尝试让世界模型评测可解释",
        "summary": "论文提出代理化评测流程，把视觉世界的物理、因果与状态判断拆成可检查的子问题，再汇总评估结论；评测覆盖与一致性仍待正文核验。",
    },
    "cand_3f36d0912c6917c3": {
        "headline": "CL4D对齐动态点云与自然语言表示",
        "summary": "论文提出直接处理动态点云的4D视觉编码器，并用对比学习对齐时空几何与文本描述；性能提升与对比设置尚未完成二轮核验。",
    },
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
    "evt_20260818_hydra0": {
        "what": (
            "Hydra-0提出以动作流（action flow）作为通用控制接口：机器人动作不再只被表示为关节指令，"
            "而被转换成图像中的像素运动，使世界模型能够在不同机器人形态、任务、环境与视频生成骨干之间学习动作后果。\n\n"
            "作者报告，最佳配置相对动作条件基线将机器人运动误差降低90.4%、物体运动误差降低60.2%；"
            "在RoboLab基准上，重放成功率与参考成功率的Pearson相关系数为r=0.96。模型还支持零样本组合与数据高效适配。"
        ),
        "why": (
            "这条路线尝试解决具身数据难以跨硬件复用的问题：如果不同机器人的动作都能投影到同一视觉运动接口，"
            "视频数据、机器人示范和世界模型预测就有机会进入同一训练与评估框架。\n\n"
            "但r=0.96只表示两组成功率高度相关，并不代表任务成功率达到96%。论文结果仍是作者在特定基准上的报告，"
            "真实机器人中的控制稳定性、延迟和跨形态泛化仍需独立复现。"
        ),
        "take": "值得优先观察动作流是否能成为视频世界模型与机器人控制之间的中间表示，并建立跨硬件可复用的动作—结果数据资产。",
        "how": "模型把机器人动作编码为像素运动，并学习动作流条件下的未来视觉结果；逆向模式还能从目标物体流预测兼容的机器人运动，再映射为可执行动作。",
        "watch": ["跨机器人形态的零样本组合能否在真实任务中复现？", "动作流接口的时延、精度与安全约束是否适合闭环控制？"],
        "numbers": [("机器人运动误差", "-90.4%", 1), ("物体运动误差", "-60.2%", 1), ("RoboLab相关性", "r=0.96", 2)],
        "reading": 6,
    },
    "evt_20260819_kuaishou_q2_ai": {
        "what": (
            "快手在2026年第二季度业绩公告中披露，AIGC短视频营销素材支出同比增长超过70%。"
            "截至6月，平台短剧供给（同时包含真人短剧与AI生成短剧）较1月增长超过五倍；第二季度由短剧驱动的在线营销服务总支出同比增长超过100%。\n\n"
            "产品与付费互动方面，快手披露当季用户发送的AI礼物超过600万次。Kling AI 3.0系列上线原生4K输出，"
            "同时发布Kling 3.0 Turbo、Kling MCP与Kling CLI，用于提高生成效率并支持Agent批量编排内容。"
        ),
        "why": (
            "这份披露的价值在于把视频生成的商业化从Demo和案例推进到公司经营口径：营销素材支出、短剧营销支出与AI礼物使用量"
            "开始成为可持续跟踪的量化信号。它说明AIGC正同时进入广告生产、内容供给和直播付费互动。\n\n"
            "需要避免过度归因：五倍的短剧供给同时包含真人与AI内容，不能全部视作AIGC增长；营销服务支出也不是快手收入同比增幅。"
        ),
        "take": "后续应连续跟踪AIGC素材支出、Kling调用或付费规模，以及AI礼物等原生消费场景，判断增长是一次性活动还是稳定业务结构。",
        "how": "快手一端用Kling生成能力服务营销素材、短剧与直播礼物，另一端通过MCP和CLI把模型接入批量内容生产与Agent工作流。",
        "watch": ["后续季度是否披露Kling收入、付费客户或调用量？", "短剧供给增长中AI生成内容的独立占比是多少？"],
        "numbers": [("AIGC营销素材支出", ">70% YoY", 0), ("短剧营销支出", ">100% YoY", 1), ("AI礼物", ">600万次", 2)],
        "visual_data": {
            "type": "bar", "title": "快手披露的AIGC与短剧增长信号", "unit": "% / 万次",
            "note": "三项指标口径不同，仅展示披露量级，不做横向业务规模比较。",
            "series": [
                {"label": "AIGC素材支出同比", "value": 70},
                {"label": "短剧营销支出同比", "value": 100},
                {"label": "AI礼物（万次）", "value": 600},
            ],
        },
        "reading": 6,
    },
    "evt_20260819_sparsepr": {
        "what": (
            "SparsePR提出一种无需再训练的块稀疏注意力方案，组合Response-Coupled Partitioning与Probe-Fitted Residual Reconstruction。"
            "前者让共享路由的查询与键值分组更匹配，后者用少量精确查询行拟合稀疏输出遗漏的残差。\n\n"
            "论文作者报告，该方法在四个异构视频生成和世界模型上持续降低注意力重建误差；"
            "在22.0%—26.0%的实际执行对密度下保持生成质量，并取得1.48—2.61倍端到端加速。"
        ),
        "why": (
            "视频Transformer的长序列注意力是推理成本的重要来源。SparsePR的信号在于，它不要求重新训练基础模型，"
            "更接近可插拔的推理优化，理论上能缩短从研究方法到现有模型部署验证的路径。\n\n"
            "不过端到端加速高度依赖模型结构、硬件和算子实现；摘要没有展开四个模型的逐项结果，"
            "因此不能直接把最高2.61倍视为所有视频模型都能获得的固定收益。"
        ),
        "take": "可以把SparsePR纳入视频模型推理优化候选，在典型长视频与高分辨率任务上同时测延迟、显存、吞吐和质量回退。",
        "how": "方法先根据采样查询的键响应建立共享稀疏路由，再用少量精确行校准当前调用的仿射残差，从而补回硬稀疏丢失的信息。",
        "watch": ["在主流视频模型与常用GPU上的真实加速是多少？", "长时序、复杂运动和高分辨率下的质量回退是否可控？"],
        "numbers": [("执行对密度", "22%—26%", 2), ("端到端加速", "1.48—2.61×", 2), ("评估模型", "4个", 1)],
        "reading": 6,
    },
    "evt_20260818_semcomp": {
        "what": (
            "SemComp-Bench把视频生成定义为“语义任务完成”：模型既要实现指令要求的最终结果，也要保留参考图像中与任务相关的高层语义。"
            "评估不强制生成完整的中间步骤，也不把传统外观一致性作为唯一判断。\n\n"
            "配套SemComp-Data覆盖六个领域，每个样本包含参考图像、详细指令、简短指令和结果导向视频。"
            "基准使用VLM回答结构化二元问题，并分别报告Outcome Achievement与Generation Reliability。"
        ),
        "why": (
            "当前视频评测常能回答画面是否好看、是否像参考图，却较难回答任务到底有没有完成。"
            "对广告、教程、操作演示和交互内容而言，结果正确往往比复现每个中间动作更重要。\n\n"
            "这一框架为结果导向视频提供了更直接的Bad Case结构，但VLM评审本身可能产生偏差，"
            "六个领域的覆盖度和各模型分数仍需结合正文核验。"
        ),
        "take": "团队可借鉴OA与GR的拆分：先判断结果是否达成，再判断多次生成是否稳定达成，避免平均画质分掩盖任务失败。",
        "how": "基准把任务目标拆成结构化二元问题，由VLM逐项判断结果达成与语义保留，再汇总为OA Score和GR Score。",
        "watch": ["VLM评审与人工判断的一致性如何？", "哪些任务最容易出现画面合理但结果未完成的失败？"],
        "numbers": [("覆盖领域", "6", 1), ("核心评分", "OA / GR", 2)],
        "reading": 6,
    },
    "evt_20260817_tau0_vla": {
        "what": (
            "τ₀-VLA是一种分层机器人基础模型，把高层子任务生成改写为可扩展测试时计算问题。"
            "高层策略先利用执行记忆生成子任务，遇到困难或关键选择时搜索替代方案，再由低层策略跨多种机器人形态执行。\n\n"
            "论文披露模型使用40,115小时异构真实世界数据进行多模态联合训练。作者报告，"
            "增加测试时计算在域内与分布偏移设置中提升下一子任务预测准确率，并带来更高的长时机器人操作闭环成功率。"
        ),
        "why": (
            "长时机器人任务的瓶颈不仅是单个动作是否可靠，还包括先做什么、失败后如何调整以及何时需要更多推理。"
            "τ₀-VLA允许模型把更多计算分配给困难决策，为具身系统提供了一条区别于单次前向预测的规划路线。\n\n"
            "摘要尚未披露具体成功率、额外时延和计算成本。测试时搜索能否在真实机器人实时约束下带来净收益，仍是产品化关键。"
        ),
        "take": "可以重点评估“按难度分配推理预算”的机制，而不是只比较统一计算量下的平均成功率。",
        "how": "高层世界模型利用执行记忆提出并比较子任务，低层VLA负责动作执行；当决策不确定时增加测试时搜索，再提交最终子任务。",
        "watch": ["额外测试时计算带来的时延和成功率增益如何权衡？", "跨机器人形态与分布偏移下的闭环提升能否独立复现？"],
        "numbers": [("训练数据", "40,115小时", 1)],
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
    if "cloud.google.com" in url:
        return "Google Cloud 官方文章"
    if "techcrunch.com" in url:
        return "TechCrunch 报道"
    if "mp.weixin.qq.com" in url:
        return "公众号原文"
    if "simonwillison.net" in url:
        return "作者原文"
    return "查看来源"


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
        "fact_points": list(dict.fromkeys(
            reader_facing_text(item.get("text", ""))
            for item in claims
            if reader_facing_text(item.get("text", ""))
        )),
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


def publication_deep_event_ids(
    selected_event_ids: list[str],
    writer_drafts: dict[str, dict],
    draft_bundle: dict | None,
) -> list[str]:
    """Only model drafts that crossed readiness and validation may ship as deep stories."""
    selected = selected_event_ids[:5]
    if draft_bundle is None:
        # Preserve deterministic legacy fixtures that predate the Writer.
        return selected
    return [event_id for event_id in selected if event_id in writer_drafts]


def brief_source_status(source: dict) -> tuple[str, str]:
    source_type = source.get("source_type")
    if source_type == "paper_report":
        return "论文摘要", "abstract_checked"
    if source_type in {"professional_view", "media", "industry_media"}:
        return "媒体转述", "secondary_source_pending"
    if source_type in {"official_announcement", "company_news", "financial_report", "code_dataset"}:
        return "官方来源", "source_checked"
    return "来源摘要", "source_checked"


def compact_brief_summary(value: str, limit: int = 360) -> str:
    """Keep a P2 brief readable and prevent full articles entering translation."""
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    prefix = text[:limit]
    endings = [prefix.rfind(mark) for mark in ("。", "！", "？", ". ", "! ", "? ")]
    cut = max(endings)
    if cut >= int(limit * 0.55):
        return prefix[:cut + 1].strip()
    return prefix.rstrip(" ,，;；:：") + "…"


def build_news_briefs(
    candidate_run: dict,
    collection: dict,
    reviewed_ids: set[str],
    window_end: str | None = None,
    window_start: str | None = None,
) -> list[dict]:
    source_map = {item["source_id"]: item for item in collection.get("source_records", [])}
    briefs = []
    end_date = report_date(window_end) if window_end else None
    start_value = window_start or candidate_run.get("window_start")
    start_date = report_date(start_value) if start_value else (end_date - timedelta(days=6) if end_date else None)
    feed_candidates = candidate_run.get("feed_candidates") or candidate_run.get("selected_candidates", [])
    for candidate in feed_candidates:
        if not candidate.get("front_display_eligible", True):
            continue
        gates = candidate.get("hard_gates") or {}
        if (gates.get("content_completeness") or {}).get("status") == "fail":
            continue
        if (gates.get("fact_wording_fidelity") or {}).get("status") == "fail":
            continue
        if candidate["candidate_id"] in reviewed_ids:
            continue
        # The collector uses a rolling 7x24-hour window, which can touch eight
        # local calendar dates. The publication timeline intentionally shows
        # exactly seven local dates ending on collection day, so keep boundary
        # spillover records in the intelligence pool but outside this issue.
        published_at = candidate.get("published_at")
        if start_date and published_at:
            candidate_date = report_date(published_at)
            if not start_date <= candidate_date <= end_date:
                continue
        source = source_map.get(candidate["primary_source_id"])
        if not source:
            continue
        copy = P2_BRIEF_COPY.get(candidate["candidate_id"], {})
        llm_analysis = candidate.get("llm_analysis") or {}
        summary = compact_brief_summary(
            copy.get("summary") or llm_analysis.get("what") or source.get("raw_excerpt") or "来源已收录，核心事实仍待核验。"
        )
        headline = copy.get("headline") or candidate["canonical_title"]
        source_badge, verification_status = brief_source_status(source)
        tags = [ROUTE_CATEGORY.get(candidate["primary_route"], "视觉智能")]
        for values in (candidate.get("tags") or {}).values():
            for value in values:
                if value not in tags:
                    tags.append(value)
        digest = hashlib.sha256(candidate["candidate_id"].encode()).hexdigest()[:12]
        briefs.append({
            "schema_version": "0.2",
            "record_type": "news_brief",
            "brief_id": f"brief_{digest}",
            "candidate_id": candidate["candidate_id"],
            "headline": headline,
            "dek": summary,
            "category": ROUTE_CATEGORY.get(candidate["primary_route"], "视觉智能"),
            "domain_scope": candidate.get("domain_scope") or domain_scope(candidate["primary_route"]),
            "intelligence_type": candidate.get("intelligence_type", "type.technology_breakthrough"),
            "primary_tags": tags,
            "priority": "priority.p2",
            "score": candidate.get("score"),
            "published_at": candidate.get("published_at"),
            "reading_time_minutes": 1,
            "source_badge": source_badge,
            "verification_status": verification_status,
            "accuracy_note": "根据来源标题与摘要整理，尚未完成P1级原文核验。",
            "source_links": [{
                "source_id": source["source_id"],
                "label": source_badge,
                "url": source["canonical_url"],
                "role": "primary" if source_badge in {"论文摘要", "官方来源"} else "secondary",
            }],
            "source_count": len(candidate.get("source_ids", [])),
        })
    return sorted(briefs, key=lambda item: (item.get("published_at") or "", item["score"] or 0), reverse=True)


def add_briefs_to_timeline(
    timeline: list[dict],
    briefs: list[dict],
    story_by_event: dict[str, dict],
) -> list[dict]:
    brief_by_date: dict[str, list[dict]] = {}
    for brief in briefs:
        if not brief.get("published_at"):
            continue
        brief_by_date.setdefault(report_date(brief["published_at"]).strftime("%m.%d"), []).append(brief)
    for day in timeline:
        day_briefs = brief_by_date.get(day["date"], [])
        day["brief_ids"] = [item["brief_id"] for item in day_briefs]
        categories = [story_by_event[event_id]["category"] for event_id in day["event_ids"]]
        categories.extend(item["category"] for item in day_briefs)
        categories = list(dict.fromkeys(categories))
        day["summary"] = (
            "、".join(categories) + "出现值得关注的新信号。"
            if categories else "完成检查，暂无达到阈值的事件。"
        )
    return timeline


def report_date(value: str):
    """Convert an ISO timestamp to the report's Asia/Shanghai calendar date."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=REPORT_TIMEZONE)
    return parsed.astimezone(REPORT_TIMEZONE).date()


def build_timeline(
    events: list[dict],
    story_by_event: dict[str, dict],
    window_end: str,
    window_start: str | None = None,
) -> list[dict]:
    event_dates = {
        item["event_id"]: report_date(item["event_at"])
        for item in events
    }
    # A weekly issue always ends on the collection date. The old implementation
    # extended forward from the latest event to fill seven slots, which could
    # display future dates. Seven calendar days inclusive means end - 6 days.
    # A rolling 7x24-hour window can touch eight local calendar dates. Preserve
    # both boundary dates so events are never silently omitted from the UI.
    start = report_date(window_start) if window_start else report_date(window_end) - timedelta(days=6)
    end = report_date(window_end)
    tones = ["purple", "blue", "cyan", "orange", "pink", "green", "muted"]
    timeline = []
    for offset in range((end - start).days + 1):
        day = start + timedelta(days=offset)
        day_events = [
            item for item in events
            if event_dates[item["event_id"]] == day
        ]
        date_label = day.strftime("%m.%d")
        categories = list(dict.fromkeys(ROUTE_CATEGORY.get(item["primary_route"], "视觉智能") for item in day_events))
        summary = "、".join(categories) + "出现值得关注的新信号。" if categories else "完成检查，暂无达到阈值的事件。"
        timeline.append({
            "day": day.strftime("%a").upper(), "date": date_label, "tone": tones[offset % len(tones)],
            "summary": summary, "event_ids": [item["event_id"] for item in day_events],
            "story_ids": [story_by_event[item["event_id"]]["story_id"] for item in day_events],
        })
    return timeline


def build_trends(events: list[dict]) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for event in events:
        route = event["primary_route"]
        # Evaluation is part of the video-model competition signal in the
        # overview. Grouping it with video generation keeps every formal event
        # represented without overloading the radar with near-duplicate axes.
        trend_route = "frontier.video_generation" if route == "visual_value.evaluation" else route
        grouped.setdefault(trend_route, []).append(event)
    tones = ["purple", "blue", "cyan", "orange"]
    radar_positions = {
        "industry.marketing": (80, 90),
        "frontier.video_generation": (38, 86),
        "frontier.world_model": (64, 73),
        "frontier.embodied_ai": (42, 53),
    }
    trends = []
    for index, (route, items) in enumerate(sorted(grouped.items(), key=lambda pair: (-len(pair[1]), pair[0]))[:4]):
        category = "视频生成与评测" if route == "frontier.video_generation" and any(item["primary_route"] == "visual_value.evaluation" for item in items) else ROUTE_CATEGORY.get(route, "视觉智能")
        names = "、".join(item["primary_entity"]["name"] for item in items[:3])
        count = len(items)
        maturity, impact = radar_positions.get(route, (40 + index * 8, 68 + index * 5))
        trends.append({
            "trend_id": "trend_" + hashlib.sha256(route.encode()).hexdigest()[:12], "label": category,
            "headline": f"{category}形成本周集中信号", "summary": f"{names}共同构成{category}方向的本周证据，需要继续观察独立复现与产品化表现。",
            "status": "升温" if count >= 2 else "待验证", "score": min(95, 74 + count * 7),
            "delta": f"+{6 + count * 4:02d}", "maturity": maturity, "impact": impact,
            "tone": tones[index], "event_ids": [item["event_id"] for item in items],
        })
    return trends


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verified", default=str(VERIFIED_PATH.relative_to(ROOT)))
    parser.add_argument("--review", default=str(REVIEW_PATH.relative_to(ROOT)))
    parser.add_argument("--candidates")
    parser.add_argument("--collection")
    parser.add_argument("--drafts")
    parser.add_argument("--output", default=str(OUTPUT_PATH.relative_to(ROOT)))
    parser.add_argument("--static", default=str(STATIC_PATH.relative_to(ROOT)))
    args = parser.parse_args()
    verified_path = ROOT / args.verified
    review_path = ROOT / args.review
    output_path = ROOT / args.output
    static_path = ROOT / args.static if args.static else None
    verified = json.loads(verified_path.read_text())
    review = json.loads(review_path.read_text())
    candidate_run = json.loads((ROOT / args.candidates).read_text()) if args.candidates else None
    collection = json.loads((ROOT / args.collection).read_text()) if args.collection else None
    draft_bundle = json.loads((ROOT / args.drafts).read_text()) if args.drafts else None
    writer_drafts = {item["event_id"]: item for item in (draft_bundle or {}).get("drafts", [])}
    cover_manifest = load_cover_manifest()
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
        writer_draft = writer_drafts.get(event_id)
        if writer_draft:
            paragraphs = writer_draft["factual_paragraphs"]
            copy.update({
                "headline": writer_draft["headline"],
                "dek": writer_draft["dek"],
                "one_line_takeaway": writer_draft["one_line_takeaway"],
                "what": paragraphs[0]["text"],
                "fact_points": [item["text"] for item in paragraphs],
                "how": "\n\n".join(item["text"] for item in paragraphs[1:]),
                "why": writer_draft["judgment"],
                "take": None,
                "watch": writer_draft["watch_next"],
            })
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
        intelligence_type = (review_item.get("agent_analysis") or {}).get(
            "intelligence_type", "type.technology_breakthrough"
        )
        story = {
            "schema_version": "0.2",
            "record_type": "editorial_story",
            "story_id": copy["story_id"],
            "article_type": copy["article_type"],
            "headline": copy["headline"],
            "dek": copy["dek"],
            "one_line_takeaway": copy["one_line_takeaway"],
            "category": copy["category"],
            "domain_scope": event.get("domain_scope") or domain_scope(event["primary_route"]),
            "intelligence_type": intelligence_type,
            "related_event_ids": [event_id],
            "primary_event_id": event_id,
            "what_happened": section(copy["what"], claim_ids, "fact"),
            "why_it_matters": section(copy["why"], claim_ids, "judgment"),
            "our_take": section(copy["take"], claim_ids, "judgment") if copy["take"] else None,
            "under_the_hood": section(copy["how"], claim_ids, "fact"),
            "limitations": section(event["limitations"], claim_ids, "judgment"),
            "watch_next": copy["watch"],
            "article_body": build_article_body(copy, event, claim_ids),
            "key_numbers": key_numbers,
            "visual_data": copy.get("visual_data"),
            "cover_image": resolve_cover_image(
                cover_manifest,
                event_id,
                intelligence_type,
                source_url=source["url"],
                headline=copy["headline"],
                category=copy["category"],
                credit=source["label"],
                generated_dirs=[
                    ROOT / "assets/editorial",
                    static_path.parent / "assets/editorial" if static_path else ROOT / "assets/editorial",
                ],
            ),
            "source_links": [source],
            "primary_tags": copy["tags"],
            "confidence": event["confidence"],
            "priority": event["priority"],
            "reading_time_minutes": copy["reading"],
            "editorial_status": "fact_checked",
            "drafted_by": "deep_story_writer_llm" if writer_draft else "deterministic_template",
            "reviewer": None,
            "reviewed_at": None,
            "revision_note": "一手来源已核验；编辑判断仍需发布者确认。",
            "editorial_score": copy["score"],
            "published_at": event["event_at"],
        }
        stories.append(story)

    story_by_event = {story["primary_event_id"]: story for story in stories}
    reviewed_candidate_ids = {item["candidate_id"] for item in review.get("records", [])}
    news_briefs = (
        build_news_briefs(
            candidate_run,
            collection,
            reviewed_candidate_ids,
            verified.get("display_window_end") or verified.get("window_end"),
            verified.get("display_window_start"),
        )
        if candidate_run and collection else []
    )
    selected_top_event_ids = [
        item for item in verified["editorial_selection"]["top_event_ids"] if item in story_by_event
    ][:5]
    top_event_ids = publication_deep_event_ids(selected_top_event_ids, writer_drafts, draft_bundle)
    top_story_ids = [story_by_event[event_id]["story_id"] for event_id in top_event_ids]
    # Readiness and Writer validation own the deep/quick split. Sparse events
    # remain useful, but must not be padded into artificial deep stories.
    for story in stories:
        is_deep_dive = story["primary_event_id"] in top_event_ids
        story["article_type"] = "deep_dive" if is_deep_dive else "brief"
        story["reading_time_minutes"] = max(story["reading_time_minutes"], 5) if is_deep_dive else 2
    exact_issue_one = set(events) == set(STORY_COPY)
    fallback_window_end = max(item["event_at"] for item in events.values())
    window_end_value = verified.get("display_window_end") or verified.get("window_end") or fallback_window_end
    display_window_start = verified.get("display_window_start") or verified.get("window_start")
    timeline = [{**day, "story_ids": [story_by_event[event_id]["story_id"] for event_id in day["event_ids"]]} for day in TIMELINE] if exact_issue_one else build_timeline(
        list(events.values()), story_by_event, window_end_value, display_window_start
    )
    timeline = add_briefs_to_timeline(timeline, news_briefs, story_by_event)
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
    reviewed_thesis = verified.get("editorial_selection", {}).get("weekly_thesis")
    if reviewed_thesis:
        weekly_thesis = reviewed_thesis
    elif current_signal_set.issubset(events):
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
    if exact_issue_one:
        period_start = report_date(min(item["event_at"] for item in events.values()))
        period_end = report_date(max(item["event_at"] for item in events.values()))
    else:
        period_end = report_date(window_end_value)
        period_start = report_date(display_window_start) if display_window_start else period_end - timedelta(days=6)
    issue_year, issue_week, _ = period_end.isocalendar()
    today_new_count = sum(
        report_date(item["published_at"]) == period_end
        for item in [*stories, *news_briefs]
        if item.get("published_at")
    )
    rolling_window_count = len(stories) + len(news_briefs)

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
            "lead_story_id": top_story_ids[0] if top_story_ids else stories[0]["story_id"],
            "top_story_ids": top_story_ids,
            "brief_story_ids": [
                story["story_id"]
                for story in stories
                if story.get("article_type") == "brief"
            ],
            "news_brief_ids": [item["brief_id"] for item in news_briefs],
            "story_count": len(stories),
            "total_intelligence_count": len(stories) + len(news_briefs),
            "today_new_count": today_new_count,
            "rolling_window_count": rolling_window_count,
            "deep_dive_count": sum(story["article_type"] == "deep_dive" for story in stories),
            "brief_count": len(news_briefs),
            "reviewed_count": verified["summary"]["p1_reviewed"],
            "watchlist_count": verified["summary"]["watchlist_events"],
            "excluded_count": verified["summary"].get("excluded_events", 0),
        },
        "presentation": {
            "timeline_days": timeline,
            "trend_radar": trends,
        },
        "editorial_stories": stories,
        "news_briefs": news_briefs,
        "evidence_claims": [claims[claim_id] for story in stories for claim_id in events[story["primary_event_id"]]["claim_ids"]],
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    if static_path and static_path.exists():
        html = static_path.read_text()
        # Every generated report is self-contained beside its copied assets.
        # Keeping root-relative-to-report paths here prevents a run-directory
        # draft from leaking `../../../` paths into the GitHub Pages homepage.
        html = re.sub(r'href="[^"]*tokens\.css(?:\?v=\d+)?"', 'href="tokens.css?v=4"', html, count=1)
        html = re.sub(r'href="[^"]*app/globals\.css(?:\?v=\d+)?"', 'href="app/globals.css?v=19"', html, count=1)
        html = re.sub(r'href="[^"]*app/hallmark-editorial\.css(?:\?v=\d+)?"', 'href="app/hallmark-editorial.css?v=13"', html, count=1)
        payload = json.dumps(output, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
        embedded = f'<!-- ISSUE_DATA_START --><script id="issue-data" type="application/json">{payload}</script><!-- ISSUE_DATA_END -->'
        html, replacements = re.subn(r"<!-- ISSUE_DATA_START -->.*?<!-- ISSUE_DATA_END -->", lambda _: embedded, html, count=1, flags=re.S)
        if replacements != 1:
            raise RuntimeError("static prototype is missing issue data markers")
        static_path.write_text(html)
    print(f"wrote {output_path}")


if __name__ == "__main__":
    main()
