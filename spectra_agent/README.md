# SPECTRA Agent v0.1

这不是网页启动器，而是周报生产的主控状态机。它串联采集、结构化、人工核验、正式事件和情报文章生成，并把人工审核作为不可绕过的闸门。

## 一键开始

```bash
python3 spectra_agent/run.py run
```

流程会自动完成采集与结构化，然后以退出码 `2` 正常暂停在 `waiting_for_review`。这不是失败。采集阶段使用项目内的 `.venv-collector`，避免系统 Python 缺少 RSS、arXiv 等依赖。

查看状态：

```bash
python3 spectra_agent/run.py status
```

根据运行目录中的 `REVIEW.md` 完成 `p1-review.json`，批准后恢复：

```bash
python3 spectra_agent/run.py resume
```

## 用已有采集结果回放

```bash
python3 spectra_agent/run.py run \
  --run-id rehearsal \
  --from-collection collector/runs/first-live-run-v0.2.json
```

## 状态

- `running`：Agent正在执行可自动化步骤；
- `waiting_for_review`：已强制暂停，等待P1原文核验；
- `completed`：审核通过后已生成正式事件、周报数据和网页草稿；
- `failed`：保留全部中间产物与日志，修复输入后可 `resume --retry`。

每次运行都独立保存在 `spectra_agent/runs/<run-id>/`，包含状态、状态变更历史、JSONL日志、采集结果、候选、审核文件、正式事件、周报和运行报告。

采集完成后还会生成 `discussion-radar.json`：它汇总核心人物与机构在近 7 天对同一主题的
独立提及，用于发现尚未成为正式新闻的前置信号。该文件只进入 P2 观察层，不会自动改变
P1 队列，也不会绕过人工审核。

`resume` 只生成运行目录内的 `weekly-report.html` 草稿，不会自动覆盖线上页面。发布是独立步骤，防止一次错误运行污染当前线上周报。

## 每周更新时间

- 自动触发：每周一、周四 09:30（Asia/Shanghai），每次生成滚动近 7 天情报；
- 采集窗口：此前 7 天；
- 自动完成：采集、来源校验、结构化、去重聚合、P1 队列；
- 自动暂停：`waiting_for_review`；
- 不自动执行：批准 P1、生成正式周报、覆盖或发布 GitHub Pages。

Codex 自动化名称为 `SPECTRA 每周情报采集`。时间可以在 Codex 自动化界面修改；程序内的默认计划同时保存在 `config.v0.1.json`，便于团队查看口径。

## LLM结构化（v0.1）

默认运行继续使用确定性过滤、标签、去重和聚合，避免未配置密钥时破坏每周任务。需要真实OpenAI模型分析时：

```bash
python3 -m venv .venv-llm
.venv-llm/bin/pip install -r requirements-llm.txt
cp .env.example .env.local
# 在本机编辑.env.local，填入 WERSS_PASSWORD（以及可选的 OPENAI_API_KEY）；不要提交或发送该文件。
.venv-llm/bin/python processor/structure.py \
  --input collector/runs/first-live-run-v0.2.json \
  --output processor/runs/llm-structured-test.json \
  --llm
```

LLM只分析规则层压缩后的20—30个候选。每个候选会增加`llm_analysis`，包含分类、What、Why、重要度、新颖度、策略相关性、证据缺口和核验问题。运行元数据记录模型、Response ID和token用量，但不记录API Key。

当前`run.py`尚未默认启用`--llm`；需要先完成一次小规模真实调用验收，再把它接入每周自动流程。

### 本地Ollama（推荐的首轮验收路径）

`.env.local`：

```dotenv
SPECTRA_LLM_PROVIDER=ollama
SPECTRA_MODEL=qwen3:8b
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

正式 `run` 会在采集前检查本机 WeRSS。当前 WeRSS 的微信后台会话依赖长期运行的
本机进程，程序不会擅自重启并使授权失效；服务不可用或需要重新扫码时，微信来源记为
失败但不阻断其他来源。WeRSS 每次 Agent 运行只轮换刷新 1 个白名单公众号，并在微信
频控或空结果时停止。

启动与准备：

```bash
ollama serve
ollama pull qwen3:8b
.venv-llm/bin/python processor/structure.py \
  --input collector/runs/first-live-run-v0.2.json \
  --output processor/runs/ollama-structured-test.json \
  --llm --llm-limit 2
```

Ollama模式通过本机`/api/chat`和原生JSON Schema输出，不需要云端API Key，也不会把候选内容发送到外部模型服务。

### 接入主流程

```bash
python3 spectra_agent/run.py run --llm
```

主流程使用`.venv-llm`执行结构化模型，并在运行目录写入`llm-structure-checkpoint.json`。每完成一批会输出`llm_batch_completed`，重试时可复用已完成分析。LLM建议只辅助核验，不能自动批准候选；流程仍强制暂停在`waiting_for_review`。

结构化阶段执行两道前置硬门槛：`content_completeness` 检查正文是否满足来源类型要求，`fact_wording_fidelity` 检查来源归因、不确定措辞以及单一案例是否被错误升级为行业趋势。只有两道门槛均为 `pass` 的候选才能进入 `p1-review.json`。失败或仍为 `pending_llm` 的记录统一写入同一运行目录的 `gated-review.json`，等待补正文、按原文限定重写、观察或剔除；它们不会进入P1深读或后续正式事件生成。

通过结构化硬门槛后，`verification/verification_harness.py` 会在人工审核前运行：从完整正文定位每条候选主张的对应证据，检查数字、归因和单一案例趋势化风险，并在有多来源时给出一致性提示。它输出 `verification-candidates.json` 和 `evidence-review.json`，同时把机器建议写入 `p1-review.json.suggested_evidence`。所有结果状态仍为 `provisional_unverified` 或 `waiting_for_human_review`；Harness不会自动填入正式 `claims`、不会批准事件，也不会触发发布。

当前主流程为：

```text
collect → validate_collection → discussion_radar → structure
→ validate_structure → verification_harness → waiting_for_review
→ human review → verified-events → editorial
```
