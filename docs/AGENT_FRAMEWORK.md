# SPECTRA Agent 当前框架

SPECTRA 是一个面向 AI 产品策略从业者的视觉行业情报 Agent。主流程采用“自动采集与处理 + 机器核验辅助 + 人工发布闸门”，不会让模型自行把未经核验的信息发布为正式文章。

## 主流程

```text
定时触发 / 手动运行
  ↓
collect
  实时采集论文、国际资讯、博客与访谈、微信公众号、企业财报和公开报告
  输出：collection.json（统一 source_record）
  ↓
validate_collection
  检查链接、时间、正文完整度、来源状态与字段结构
  ↓
discussion_radar（可选）
  汇集 AI KOL 与机构讨论信号，只作选题线索，不直接作为正式事实
  ↓
structure（规则 + 可选 LLM）
  标签过滤、负向词过滤、近似去重、同事件聚合、分类、P1/P2 初筛
  输出：candidates.json
  ↓
validate_structure
  校验分类、分数、事实措辞和候选结构
  ↓
verification_harness
  ├─ 正文完整度硬门槛
  ├─ 事实措辞保真
  ├─ 中英 claim/evidence 归一化
  ├─ 金额、百分比、单位和数量级归一化
  ├─ 跨来源一致性
  └─ 按 claim 提取与评分证据
  输出：verification-candidates.json + evidence-review.json
  ↓
人工审核闸门（强制暂停）
  人工决定保留 / 观察 / 剔除，并确认 claims、限制与来源
  未批准内容不得进入正式事件
  ↓
build-final-events
  输出：verified-events.json（正式事件与证据白名单）
  ↓
fact_selection（确定性节点）
  从正式事件中选择可写 claims、证据、允许判断与禁止外推项
  输出：fact-selection.json（锁定的写作事实白名单）
  ↓
deep_story writer（新增，可选 LLM，当前优先）
  仅使用 fact-selection + 完整 verified_text 生成原创中文文章
  不增加新事实、不弱化归因、不整篇复制来源
  输出：deep-story-drafts.json；正文不完整的事件进入 blocked，不硬凑深读
  ↓
editorial renderer
  合并 P1 深读、P2 短讯、趋势雷达和一周时间轴
  输出：editorial-issue.json + weekly-report.html
  ↓
validate_issue
  校验事件覆盖、来源链接、claim 引用、时间窗与页面数据
  ↓
人工确认后发布
  GitHub Pages；钉钉推送仍属于后续出口，不在当前自动发布范围内
```

## 三条边界

1. **事实边界**：LLM 不能创造 claim。正式正文中的事实只能来自人工确认后的证据白名单。
2. **自动化边界**：Agent 自动运行到 P1 审核队列后暂停；人工批准后才能继续生成正式事件与文章。
3. **发布边界**：本地生成不等于线上发布。GitHub Pages 和未来钉钉推送必须经过单独确认。

## LLM 的职责

- `qwen3:8b`：候选分类、标签、初筛、P2 辅助处理。
- `qwen3:14b`：只对已核验且正文完整的正式事件生成 `deep_story`，与8B串行运行。
- Verification Harness 与人工审核共同约束 LLM；模型判断不能替代来源证据。

## 降级策略

`editorial_writer` 默认不是单点故障。若本机 Ollama 不可用、输出不符合 Schema、引用未知 claim，或文章与来源文本过度相似，流程会记录失败并回退到确定性模板，不会用不合格的模型稿覆盖正式数据。
