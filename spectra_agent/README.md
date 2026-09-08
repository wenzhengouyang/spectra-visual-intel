# SPECTRA Agent v0.1

这不是网页启动器，而是滚动情报生产的主控状态机。它串联采集、结构化、人工核验、核心事件和情报文章生成，并把人工审核作为不可绕过的闸门。

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
- `completed`：审核通过后已生成正式事件、滚动摘要数据和网页草稿；
- `failed`：保留全部中间产物与日志，修复输入后可 `resume --retry`。

每次运行的数据独立保存在 `~/Library/Application Support/SPECTRA/data/runs/<run-id>/`，源码、运行时副本和数据互不混放。目录内包含状态、状态变更历史、JSONL日志、采集结果、候选、审核文件、正式事件、滚动摘要和运行报告。

采集完成后还会生成 `discussion-radar.json`：它汇总核心人物与机构在近 7 天对同一主题的
独立提及，用于发现尚未成为正式新闻的前置信号。该文件只进入 P2 观察层，不会自动改变
P1 队列，也不会绕过人工审核。

`resume` 只生成运行目录内的 `rolling-digest.html` 草稿，不会自动覆盖线上页面。发布是独立步骤，防止一次错误运行污染当前线上内容。

## 每日更新时间

- 自动触发：每天 08:00（Asia/Shanghai），每次生成滚动近 7 天情报；
- 采集窗口：此前 7 天；
- 自动完成：采集、来源校验、结构化、去重聚合、P1 队列；
- 自动暂停：`waiting_for_review`；
- 不自动执行：批准 P1、生成正式滚动摘要、覆盖或发布 GitHub Pages。

本机任务名称为 `SPECTRA 每日滚动情报采集`。程序内的默认计划同时保存在 `config.v0.1.json`，便于团队查看口径。

## LLM结构化（v0.1）

默认运行继续使用确定性过滤、标签、去重和聚合，避免未配置密钥时破坏日更任务。需要真实OpenAI模型分析时：

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

当前`run.py`尚未默认启用`--llm`；需要先完成一次小规模真实调用验收，再把它接入日更自动流程。

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

通过结构化硬门槛后，`verification/verification_harness.py` 会在人工审核前运行：从完整正文定位每条候选主张的对应证据，检查数字、归因和单一案例趋势化风险，并在有多来源时给出一致性提示。随后 `p1_fact_expander.py` 使用 `qwen3:8b` 为每个 P1 提议 8—12 条原子事实；只有原文引文精确命中、数字一致且保留来源归因的事实才会写入 `p1-review.json.suggested_evidence`。

`review_policy.py` 是候选级唯一判定入口。它把正文完整、措辞保真、证据支持、风险、数字一致和主来源有效作为六项硬条件，并在配置中限定自动锁定可接受的置信度、情报类型、来源、单主源和模型扩写深度。命中必人工类型、任意风险、低置信、数字或效果结论、多来源拼接或扩写过深时，记录仍进入人工队列。自动锁定只修改仍为 `pending` 的达标子集，不覆盖已有人工决定；默认 `preserve_run_human_gate=true`，所以即使某些记录已自动锁定，整次 Run 仍暂停在人工事实审核闸门，也不会触发发布。

P1 人工事实审核通过后，`fact_selection` 只从 `verified_events` 生成 Writer 可见事实包。至少包含 8 条人工核验事实的事件进入 `core_event_pipeline.py`：系统启动独立后台 worker，本机 `qwen3:14b` 按事件串行生成长篇正文，随后由程序执行重复句清理、段落—claim 映射、数字新增检查、来源归因检查、事实重合度检查和判断边界检查。每个事件在 `core-event-checkpoint.json` 中记录 queued/running/retryable_failure/completed/manual_review 状态；中断后复用已完成稿，单篇最多尝试两次。未达到 8 条事实的事件自动降为快速解读；最终失败稿进入 `manual_editorial_review`。审计写入 `core-event-audit.json`，后台结束后仍保持 `publish_status=not_published`。

## 本机日更运行

日更不依赖 Codex。定期重建近 7 日完整基线；其余运行从最近一次有效 collection 接续，只处理新增或发生变化的记录，并向前重叠 6 小时防止延迟源漏采。页面始终展示截至当天的 7 个自然日，并区分“今日新增”和“近 7 日累计”。

```bash
.venv-llm/bin/python spectra_agent/daily_runner.py
```

同一天重复启动不会重复采集：已完成或正在等待人工时直接退出；失败或异常中断时使用既有产物执行 `resume --retry`。为避开 macOS 对 Documents 目录的后台读取限制，正式 `launchd` 不直接运行工程目录，而是运行由下列命令同步到 `~/Library/Application Support/SPECTRA/runtime` 的独立副本：

```bash
.venv-llm/bin/python spectra_agent/install_local_runtime.py
```

安装器可重复执行，用于把后续工程修改同步到运行副本；它不复制数据目录，重新加载每天 08:00 的服务，并执行不采集的运行环境探针。正式日志位于 `~/Library/Application Support/SPECTRA/data/logs/`。

查看 P1 队列：

```bash
.venv-llm/bin/python spectra_agent/review_cli.py --run-id daily-YYYYMMDD --list
```

进入可直接操作的终端逐条审核，并在确认后自动续跑：

```bash
.venv-llm/bin/python spectra_agent/review_cli.py --run-id daily-YYYYMMDD --interactive --resume
```

确认所有事实，并在同一命令中续跑 Writer 与页面生成：

```bash
.venv-llm/bin/python spectra_agent/review_cli.py \
  --run-id daily-YYYYMMDD \
  --include 1,2,3 \
  --watch 4 \
  --exclude 5 \
  --reviewer 欧阳文铮 \
  --resume
```

只有完成状态、人工事实审核和发布前校验全部满足后，下面的显式命令才会推送 GitHub Pages。日更任务本身不会跨过人工闸门或自动发布。

```bash
.venv-llm/bin/python spectra_agent/publish_run.py --run-id daily-YYYYMMDD
.venv-llm/bin/python spectra_agent/publish_run.py --run-id daily-YYYYMMDD --push --confirm
```

`launchd` 配置模板位于 `spectra_agent/launchd/com.spectra.visual-intel.daily.plist`，每天 08:00 启动本地 runner。

钉钉推送目前在配置中关闭，不安装、不启动。预留模块使用独立的 `spectra_agent/launchd/com.spectra.visual-intel.dingtalk.plist`，未来启用时可在每天 10:00 检查当日 Run。只有 Run 已完成且 GitHub Pages 已发布时才会推送；仍在审核、Writer 生成或发布校验中时仅记录 `not_ready`，不会推送旧页面。机器人凭证只从被 Git 忽略的 `.env.local` 读取：

```bash
DINGTALK_WEBHOOK_URL=https://oapi.dingtalk.com/robot/send?access_token=...
DINGTALK_SECRET=SEC...
```

未配置 Webhook 时推送器返回 `not_configured`；可先用不访问网络的命令预览消息：

```bash
.venv-llm/bin/python spectra_agent/dingtalk_push.py --run-id daily-YYYYMMDD --dry-run
```

当前主流程为：

```text
collect → validate_collection → discussion_radar → structure
→ validate_structure → verification_harness → p1_fact_expansion → waiting_for_review
→ fact-level human review → verified-events → p1 editorial queue/worker → p2_localizer
→ generated-image preview（仅无官方图时）→ validate_issue → run_evaluation（只读、失败阻断）→ local draft
```

## Run 级评测

`run_evaluator.py` 可独立回放任意历史 Run。它检查 ID 与数量一致性、队列互斥、来源成功率、增量基线与重复处理、自动锁定及人工修改、Writer 降级、固定种子的事实抽样和发布物基础完整性，并写入机器可读的 `eval-report.json` 与简短的 `eval-report.md`：

```bash
.venv-llm/bin/python spectra_agent/run_evaluator.py \
  --run-dir "$HOME/Library/Application Support/SPECTRA/data/runs/<run-id>" \
  --config spectra_agent/config.v0.1.json
```

评测默认只读流水线产物、只写上述报告。主流程在发布物校验之后、标记 `completed` 之前按 `run_evaluation.enabled` 调用；当前 `block_completion_on_failure=true`，Writer 降级率、深读数量、P1/P2 中文完整度或图片质量任一不达标都会阻止 `completed` 和发布。深读不设最低字数，要求在事实保真、证据支持和不重复的前提下尽量覆盖有效信息。只有事实审核、Writer、页面校验全部结束且发布前校验通过的不同真实窗口才标记为 `cohort_eligible` 并计入滚动判断；在 `waiting_for_review` 阶段做的手动回放不会冒充完整轮次。滚动建议中的 `allow_expand` 只供人判断，评测不会修改 `p1-review.json`、claims、policy 或发布权限。模型、采集策略或窗口配置变化会改变策略指纹，使既有“稳定”结论失效并重新进入观察。

`p2_localizer` 使用 `qwen3:8b` 将仍为英文的 P2 标题与摘要忠实转为中文。它保留原文字段，双向检查数字、单位和数量级，并检查来源归因与不确定措辞是否丢失。通过校验的短讯不会改变原有 P2 待核验状态；失败内容从页面数据中隔离并进入 `p2-localization-review.json`，不得进入网页。

P1 与 P2 共用 `processor/language_quality.py`：允许模型名、公司名和必要缩写保留英文，但按字段检查中文占比、英文连接词和完整英文句式。采集器会把 RSS/公众号公开元数据中的官方封面写入 `source_record.official_image_url`；正式稿先用官方图，只有缺图时才生成主题示意图。生成图必须通过主题匹配、重复检查，并由人执行预览确认后才能继续：

```bash
.venv-llm/bin/python spectra_agent/image_review.py --run-dir "$HOME/Library/Application Support/SPECTRA/data/runs/<run-id>"
.venv-llm/bin/python spectra_agent/image_review.py --run-dir "$HOME/Library/Application Support/SPECTRA/data/runs/<run-id>" --approve all --reviewer 欧阳文铮
```

## 连续运行验收

每个完成的新流程运行会写入 `acceptance-metrics.json`，并更新
`~/Library/Application Support/SPECTRA/data/runs/acceptance-summary.json` 与 `.md`。统计只纳入显式加入验收队列且采集窗口不同的运行，
避免用同一批数据反复回放冒充稳定性。指标包括来源成功率、P1 Writer 自动通过率、Writer 降级率、
事实级人工修改率和编辑人工调整篇数。

手动将一个既有完成运行作为验收基线：

```bash
.venv-llm/bin/python spectra_agent/acceptance_metrics.py \
  --include-run <run-id> --rounds 3
```

累计三个不同采集窗口后，报告状态才会从 `collecting` 变为 `ready_for_decision`。这只表示可以人工评估
是否扩大低风险子集的自动锁定范围，不会自行修改过审或发布开关。P1 Writer 不再使用事件 ID 对应的人工正文覆盖；模型稿未通过
程序审计时必须降为快速解读，并计入 Writer 降级率。
