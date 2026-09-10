# 原图优先配图策略（2026-09-10 修订，优先于下方历史说明）

核心事件与行业信号统一覆盖：article_type=core_event 或 editorial_tier=industry_signal。优先复用当前任务已完成且文件指纹不变的合格配图；新任务按新闻原文配图 → 对应公司官方素材 → AI 兜底顺序处理。普通短讯不强制配图。

prepare 默认 provider=source_search。Agent 先访问 source_links 中新闻原文，再查对应官方发布页面，核对主体、事件、清晰度、授权或使用依据，不采用无关 logo 或搜索缩略图。网络失败应重试或报告，不能当作无素材直接生图。

原图下载并实际查看后，用 image_generation --run-dir <目录> --story-id <ID> --image <本地路径> --origin news_original（或 official）--source-url <HTTPS来源页> --credit <署名> --usage-basis <使用依据> 安装。图片须保存本地并随发布打包，不依赖远程防盗链。

只有两级均无可用图片，才登记 --source-search <JSON路径>。JSON 的 news_original 和 official 分别包含 checked_urls 数组、result: unavailable、reason 具体原因。记录与提示词绑定；缺少记录时 AI 图片安装会被拒绝。登记后 provider=codex_imagegen，才可调用图像模型。旧任务已生成图片保留，不回溯重生成。

所有来源都需实际图文审核并绑定指纹，通过后才恢复流程。不得用自动 semantic_match 字段代替实际看图。AI 概念配图须明确标注；原图保留来源、署名及使用依据。单稿同提示词最多三次生图，跨轮记录，不重复消耗。来源优先检查由 Codex 定时 Agent 执行，非后台独立爬虫，需本机与 Codex 在线。

以下历史说明中的“真实模型出图”仅适用于上述 AI 兜底分支，不代表默认策略。

核心事件与行业信号（与网页头图卡片一致）在中文化之后建立 image-generation.json 队列。任务停在 image_generation，真实模型出图、验证并安装后才进入 image_preview。两类稿件共用 requires_cover 判定，不依赖核心事件数量；短讯版也可能包含需要配图的行业信号。

后端通过 Codex 定时任务读取队列并调用 Imagegen。用户于 2026-09-10 明确授权持续资源使用及 Agent 图文审核。每 10 分钟检查一次，只在有待办时出图；依赖本机与 Codex 在线。

安装命令：项目 .venv-llm/bin/python -m spectra_agent.image_generation --run-dir <目录> --story-id <ID> --image <真实生成图片路径>。依赖 Pillow。提示词、时间、期次、SHA-256 随作业保存。短讯版不强制逐条配图。

Agent 必须实际查看图片，依据文章核对主体、行为和关系，排除事实冲突与误导；不能仅凭提示词或文件存在批准。图文相符即可，不要求固定审美。通过后以 Agent 身份调用 image_review 对具体图片批准并绑定指纹，随后 resume。失败按具体原因重试，同一稿件与提示词最多 3 次，次数跨轮保存在 image-agent-review.json；耗尽后停止并通知用户。该权限仅覆盖图片审核，不改变事实和语义等其他审核要求。

2026-09-10 内置 Imagegen 实际生成示例：assets/editorial/story-images-editing-imagegen-20260910.png。提示：16:9 概念配图，同一只陶土鸟在三个编辑画面中保持主体一致，分别变化背景与照明，深蓝摄影棚、克制青色强调，无文字、标志或伪造产品界面。该图尚未作为审核通过的封面发布。

钉钉头图改为 1200×675，品牌文字在中央安全区；正文减少空段、去除横线和引用框，摘要上限缩至 56 字。客户端实际字体和间距由钉钉控制。旧消息无法通过代码修改撤回或重排。
