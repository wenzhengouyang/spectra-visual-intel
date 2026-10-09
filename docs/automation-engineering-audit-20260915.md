# SPECTRA 9月14—15日自动化与 GitHub 同步审计

审计日期：2026-09-15。范围：工程源代码、持久运行副本、两天运行日志、发布缓存、GitHub main 最新提交。只做审计与无副作用复现；未修改业务代码、未发布、未发送钉钉。
GitHub 实时查询 main：c1609ac1570514531f4b4cd422ada238c06bfc81；本机发布缓存 HEAD 完全相同且工作区干净，因此用该提交对象逐文件比较。开发工作区 HEAD：1b2d599。不能根据开发工作区 git status 单独判定哪些已经上网。

## 2026-09-15 后续修复状态

已完成并部署：缺少采集产物时重新进入采集；网络预检降为诊断；覆盖线在主线前独立派发并在下一次日报消费；网页发布与钉钉拆开；未发布的已完成运行自动补发网页；P1 和 P2 审核完成后由调度恢复；P2 不确定性、中文基数和已有错误译文修复；审核队列输出结构化事实差异；全部读者正文的内部占位硬拦截；低频财报和年度报告移出早间主线。完整回归 353 项通过，持久运行环境探针通过。

仍需真实运行证明：2026-09-16 08:00 的 launchd 触发、真实网络下两条采集线、实际触发的两轮修复、人工审核后的恢复、GitHub Pages 可见和钉钉发送。配图来源搜索及 AI 生成仍依赖外部执行者，P1 事实审核与配图审批仍是人工质量闸门。GitHub 工程代码尚未提交；本报告的原始差异清单仍可作为待同步范围参考。

## 一、真实故障时间线（北京时间）

- 9/14 08:06：daily runner 已启动并到达 P1 人工审核。因此“这两天均没有启动”不准确；两天未实现按预期自动交付成立。
- 9/14 10:51：正文中文比例校验失败，story_8c7259fa7289。
- 9/14 10:56：source_success_rate 阻断最终评测。
- 9/14 11:18 完成生成；11:44 发布；13:16 钉钉发送（当前保留标记）。这些时间不能证明无人干预。
- 9/15 08:37：network_preflight 抛错，采集尚未完成。
- 9/15 10:10、10:12：恢复任务执行 resume，均因 collection.json、candidates.json 缺失失败。
- 9/15 10:13：改用 run 后才继续采集。
- 9/15 10:36：生成器读取空 event 触发 NoneType 异常；当前代码已增加空值保护。
- 9/15 10:45：停在 localization_review，6 条待审。
- 9/15 14:14：当前最终修订版完成生成、发布和钉钉发送。早先发送内容后续又修订，单个最终标记不能替代完整发送历史。

证据：持久数据目录 data/logs/daily-20260914.log、daily-20260915.log；对应 runs/daily-*/run.log.jsonl、run.json、dingtalk-push.json。

## 二、仍存在的自动化阻碍

|级别|缺口及证据|影响与修复方向|
|---|---|---|
|P0|daily_runner.choose_action 对失败一律选择 resume_retry；run._resume_run 要求 collection/candidates 已存在|已真实复现的死循环；按产物和失败阶段选择重新采集、继续结构化或恢复编辑，保留已有有效检查点|
|P0|网页发布由 run 完成回调启动 dingtalk_push，再由它调用 publish_run；paused_run_ids、sent/suppressed 提前返回发生在发布之前|暂停通知或修订已发送期次会跳过网页发布；网页发布与钉钉投递需独立状态与恢复入口|
|P0|daily_runner completed 直接 noop；人工审核状态也直接 noop，不检查审核是否实际已完成；完成回调为脱离主进程的 Popen|发布失败/回调丢失后缺少持续恢复；审核通过后不一定自动推进。应检查审核结果、发布状态、投递状态分别恢复|
|P0|P2 不确定性检查只要译文命中“公司”等任一词就放行|无副作用复现：Company could release a model → 公司发布了模型，错误地 PASS。需分别验证主体归因与可能/计划/传闻等模态|
|P1|P2 的 targets 仅来自 fields_needing_translation；已有自然中文但数字/归因错误的条目在最后校验才进入队列|并非所有校验失败都会经过两轮自动修复；应以完整字段校验构建修复任务|
|P1|P2 generate_json 在校验 try 之外；只按错误字符串两种标记决定人工审核；报错只保留首个失败字段|模型/解析异常可能直接终止阶段；界面不是结构化事实差异审核。需要完整错误分类和字段差异|
|P1|规则样本将“25位”标 rejected，“二十五位”标 accepted；number_basis 只处理中文序数，不处理该中文基数|复现推荐 accepted 文案仍 FAIL，规则与执行器自相矛盾；样本应记录误报性质和来源证据，全部逐条执行验收|
|P1|内部占位文案拦截仅扫描标题、摘要和 one_line_takeaway；共享 publication_quality 未包含此规则|正文、事实段、解释段等读者字段仍有缺口；应共享规则并覆盖全部对外可见字段，同时避免误杀合理证据边界|
|P1|coverage_runner 只产出 coverage-collection.json；全工程未找到下游消费该文件的入口|补齐线可运行却只留档，新增高价值材料没有进入之后的结构化/审核/日报。应明确异步补充或次日接入和去重契约|
|P1|主线配置含财报/Stanford 年度报告等低频源；coverage 在主线采集成功返回之后才派发|仍不完全符合“稳定日报源优先、低频源异步”；主线失败时覆盖线也不会启动。需逐来源调整并独立派发|
|P1|配图 prepare 只建 source_search/codex_imagegen 队列，记录搜索/图片依赖外部执行；两天 image-agent-window 仍留 dispatch_status=pending|已有队列不是可持续执行器；需要派发确认、完成回写、到期恢复与明确人工图审边界|
|P1|晨跑验收 heartbeat 只安排明日 08:00 一次；不是持续到发布完成的监控|08:00 只能观察启动，不能证明10:30投递。需覆盖启动、生成、审核恢复、网页可见与最终投递，未发生修复场景时标“未触发”|
|P2|daily probe 仅检查四条路径存在；部署原地删除再复制代码目录、复制现有虚拟环境，依赖本机解释器|ready 不是依赖、网络、鉴权、服务可用性的证明；正在运行的任务可能撞上半部署状态。需要版本目录切换及运行态健康检查|
|P2|LaunchAgent 属于登录用户域，仅08:00/10:10两个触发点|不能承诺关机或未登录时准时运行。当前任务刚重载 runs=0，无法据此证明此前触发原因；需要持久启动证据和错过窗口补跑|

P1 的事实核验和配图审核是现有质量边界，不能为了“全自动”跳过。自动化应自动到达待审、明确提示、审核通过后继续，而不是把等待审核算成成功交付。

## 三、上次完成声明需要收窄

341 项测试通过是真实结果，但没有覆盖上面复现的“不确定性误放行”“样本自相矛盾”和“缺产物恢复入口”。
已完成的改动包括两轮字段修复代码、部分占位拦截、空 event 保护、标题/数字部分规范化、投递去重及持久副本同步；它们尚未形成经过真实晨跑证明的端到端闭环。
本次抽查上轮工作区变更文件：68 个与 runtime 内容相同，1 个 index.html 不同，60 个未复制（主要为测试、文档、预览及安装输入，不应全部视为部署缺陷）。

## 四、GitHub 同步核对

逐文件以 Git blob 内容核对当前本地文件与远端 c1609ac，共 92 个差异：53 个远端缺失、39 个内容不同。包含测试、文档、预览和历史数据，不能把92个全部当最终工程上传。
当前 app/ 文件未发现差异，说明页面资源发布与后端工程版本同步是两条路径。
发布脚本 copy_package 只发布页面和静态资产，不会自动提交采集/审核/调度代码。

优先同步的正式工程组（先修复本报告缺口再提交）：
- 采集：collector/collect.py、source_registry、watchlist、spectra_agent/coverage_runner.py、run.py、config。
- P2/内容：processor/p2_localizer.py、localization_rule_samples、build-editorial-issue、validate-editorial-issue、localization-review-terminal、update-reviewed-brief-copy。
- 调度与发布：daily/dingtalk/progress plist、install_local_runtime、morning_guards、dingtalk_push、stage_notification、local_notification。
- 模型目录：build-model-universe.cjs、model-universe.py、sync-model-universe-runtime.py 等在远端缺失；之前“已同步”不能覆盖当前远端。
- 对应回归测试与运维说明须随工程提交。
- previews、备份、演示页及历史 issue 数据先人工判定正式用途；密钥、.env.local、账户数据库和运行日志不属于普通源码同步内容。

### 完整差异清单

|文件|对比结果|
|---|---|
|collector/collect.py|与GitHub不同|
|collector/model-universe-runs/.gitignore|GitHub缺失|
|collector/model_product_watchlist.v0.1.json|GitHub缺失|
|collector/observer_watchlist.v0.1.json|与GitHub不同|
|collector/source_registry.v0.2.json|与GitHub不同|
|docs/AGENT_FRAMEWORK.md|与GitHub不同|
|docs/ENGINEERING_BASELINE_20260908.md|GitHub缺失|
|docs/KNOWN_ISSUES_20260909.md|GitHub缺失|
|docs/acceptance-2026-09-08.md|GitHub缺失|
|docs/design/dingtalk-preview-20260910.md|GitHub缺失|
|docs/design/figma-overview-grating-20260910.md|GitHub缺失|
|docs/design/selected-b-20260909/README.md|GitHub缺失|
|docs/design/selected-b-20260909/articles-reference.png|GitHub缺失|
|docs/design/selected-b-20260909/overview-reference.png|GitHub缺失|
|docs/dingtalk-top3-selection-review-20260908.md|GitHub缺失|
|docs/engineering-baseline-2026-09-09.md|GitHub缺失|
|docs/joint-delivery.md|与GitHub不同|
|docs/model-universe.md|GitHub缺失|
|docs/morning-delivery.md|与GitHub不同|
|docs/reliability-live-acceptance-2026-09-09.md|GitHub缺失|
|docs/runtime-delivery-baseline-2026-09-08.md|GitHub缺失|
|editorial/build-editorial-issue.py|与GitHub不同|
|editorial/runs/issue-01-editorial-stories-v0.2.json|与GitHub不同|
|index-magazine-demo.html|GitHub缺失|
|index-magazine.html|GitHub缺失|
|index.html|与GitHub不同|
|index.html.backup-20260909-174916|GitHub缺失|
|previews/overview-models-20260911/README-v2.md|GitHub缺失|
|previews/overview-models-20260911/README.md|GitHub缺失|
|previews/overview-models-20260911/data.js|GitHub缺失|
|previews/overview-models-20260911/directory-additions.json|GitHub缺失|
|previews/overview-models-20260911/directory-review.md|GitHub缺失|
|previews/overview-models-20260911/index.html|GitHub缺失|
|previews/overview-models-20260911/market-coverage-20260913.md|GitHub缺失|
|previews/overview-models-20260911/orbit-depth.css|GitHub缺失|
|previews/overview-models-20260911/orbit-v3.css|GitHub缺失|
|previews/overview-models-20260911/orbit-v3.js|GitHub缺失|
|previews/overview-models-20260911/overview-preview-v2.html|GitHub缺失|
|previews/overview-models-20260911/overview-preview-v3.html|GitHub缺失|
|previews/overview-models-20260911/overview-preview.html|GitHub缺失|
|previews/overview-models-20260911/preview.css|GitHub缺失|
|previews/overview-models-20260911/preview.js|GitHub缺失|
|previews/overview-models-20260911/refinement-v2.css|GitHub缺失|
|previews/overview-models-20260911/sync-orbit.cjs|GitHub缺失|
|previews/overview-models-20260911/three-fields-audit-20260913.md|GitHub缺失|
|previews/overview-models-20260911/tokens.css|GitHub缺失|
|previews/overview-models-20260911/version-supplement.json|GitHub缺失|
|processor/localization_rule_samples.v0.1.json|GitHub缺失|
|processor/p2_localizer.py|与GitHub不同|
|requirements-llm.txt|与GitHub不同|
|schemas/intelligence-record.v0.2.schema.json|与GitHub不同|
|scripts/build-model-universe.cjs|GitHub缺失|
|scripts/deploy-approved-ui.py|与GitHub不同|
|scripts/image-review-terminal.py|GitHub缺失|
|scripts/install-morning-schedule.py|与GitHub不同|
|scripts/localization-review-terminal.py|GitHub缺失|
|scripts/model-universe.py|GitHub缺失|
|scripts/sync-model-universe-runtime.py|GitHub缺失|
|scripts/update-reviewed-brief-copy.py|GitHub缺失|
|scripts/validate-editorial-issue.py|与GitHub不同|
|scripts/validate-model-directory.cjs|GitHub缺失|
|spectra-magazine.html|GitHub缺失|
|spectra_agent/README.md|与GitHub不同|
|spectra_agent/config.v0.1.json|与GitHub不同|
|spectra_agent/coverage_runner.py|GitHub缺失|
|spectra_agent/dingtalk_push.py|与GitHub不同|
|spectra_agent/editorial_policy.py|与GitHub不同|
|spectra_agent/install_local_runtime.py|与GitHub不同|
|spectra_agent/launchd/com.spectra.visual-intel.daily.plist|GitHub缺失|
|spectra_agent/launchd/com.spectra.visual-intel.dingtalk.plist|GitHub缺失|
|spectra_agent/launchd/com.spectra.visual-intel.progress.plist|GitHub缺失|
|spectra_agent/local_notification.py|与GitHub不同|
|spectra_agent/morning_guards.py|与GitHub不同|
|spectra_agent/progress-dashboard.html|与GitHub不同|
|spectra_agent/progress_server.py|与GitHub不同|
|spectra_agent/run.py|与GitHub不同|
|spectra_agent/stage_notification.py|与GitHub不同|
|tests/test_collector.py|与GitHub不同|
|tests/test_daily_runtime.py|与GitHub不同|
|tests/test_dingtalk_push.py|与GitHub不同|
|tests/test_editorial_placeholder_gate.py|GitHub缺失|
|tests/test_editorial_policy.py|与GitHub不同|
|tests/test_incremental_collection.py|与GitHub不同|
|tests/test_local_notification.py|与GitHub不同|
|tests/test_model_universe.py|GitHub缺失|
|tests/test_morning_guards.py|与GitHub不同|
|tests/test_p2_translation.py|与GitHub不同|
|tests/test_progress_server.py|与GitHub不同|
|verification/apply-scheduled-review-20260817.py|与GitHub不同|
|verification/editorial-selection-20260813.json|与GitHub不同|
|verification/render-final-report.py|与GitHub不同|
|visual-intelligence-prototype.html|与GitHub不同|

## 五、建议执行顺序与验收

1. 先修复缺产物恢复、网页/钉钉耦合、审核后/发布失败恢复三个链路问题。
2. 修复不确定性校验、样本矛盾、完整事实差异修复入口、全部读者字段占位拦截。
3. 接通覆盖线的后续消费和配图执行器，调整低频来源归属。
4. 对上述真实故障做有限离线回放；记录恢复次数、保留产物、发布/投递状态转换。真实钉钉仍遵守今日暂停。
5. 将确定的最终代码与测试提交至 GitHub，记录提交号；部署相同版本并核对校验和。
6. 下一次真实08:00晨跑一直验收到发布完成；启动、修复、网页和投递分别给证据，不能用一次性08:00检查替代全链路结果。
