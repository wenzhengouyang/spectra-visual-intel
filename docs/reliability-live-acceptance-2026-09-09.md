# SPECTRA 可靠性实测闭环｜2026-09-09

## 结论

本轮在不发布网页、不发送钉钉的前提下，完成了冷启动探针、429 退避、WeRSS 断连降级、常驻服务重启、模型超时、重复恢复、中途退出与检查点恢复实测。实测发现并修复 3 个状态/版本问题；最终 275 项自动化测试通过，修复已 copy-only 同步到本机 runtime。

## 实测结果

| 路径 | 结果 | 证据 |
|---|---|---|
| 安装态冷启动探针 | 通过 | runtime `daily_runner.py --probe` 返回 `ready`，必需文件无缺失 |
| HTTP 429 | 通过 | 本机临时 HTTP 服务首请求返回 429 与 `Retry-After: 1`；客户端等待约 1.01 秒后第二次请求成功 |
| WeRSS 断连 | 通过 | 隔离健康地址返回失败；重试计划为 `full_window_retry_sources=[]`，来源进入 `werss_unavailable` 冷却 |
| WeRSS 常驻恢复 | 通过 | 强制重启后 PID `66541 → 87907`，运行次数 `1 → 2`，端口 8001 与 `/api/docs` 返回 200 |
| 进度服务常驻恢复 | 通过 | 强制重启后 PID `66546 → 87908`，运行次数 `1 → 2`，端口 8010 返回 200 |
| WeRSS 授权链 | 通过 | 重启后单来源 1 日读取：11/11 公众号匹配，54 篇可见，9 条记录成功，`reauth_required=false` |
| 模型超时 | 通过 | 真实子进程在 0.3 秒超时；日志保留 `command_timeout`；第一次建议检查点重试，第二次到上限后 `stop_or_degrade` |
| 重复恢复 | 通过 | 临时运行持有内核租约时，第二个 `resume --retry` 返回 `already_running`，未启动重复执行者 |
| 中途退出识别 | 通过 | 临时执行者被 SIGKILL 后状态变为 `interrupted`，可靠性同步为 `blocked`，并给出检查点恢复动作 |
| 检查点恢复 | 通过 | 当日运行隔离副本约 1.4 秒恢复为 `completed`；5 条正式事件、5 篇文章、26 条短讯、0 条中文化阻塞 |
| 审核/检查点保留 | 通过 | `collection`、`candidates`、P1 审核、图片审核、Writer 检查点在恢复前后 SHA-256 一致 |
| 重复完成恢复 | 通过 | 内容版本一致时再次执行 `resume` 返回 `nothing to resume` |
| 完整测试 | 通过 | `.venv-collector` 下 275/275 通过；`git diff --check` 通过 |

## 实测发现并修复

1. 任务中断后 `status=interrupted`，但 `reliability_status` 仍可能保留 `healthy`。现统一写为 `blocked`，恢复成功后再由可靠性状态机回到 `healthy`。
2. 达到自动恢复上限后，状态页仍提示继续 `resume --retry`。现按 `exhausted` 分流：未耗尽才提示自动恢复，耗尽后显示转人工/降级动作。
3. 恢复只刷新 `localization.completed_at` 时，旧算法会误判内容版本变化并触发不必要评测。现将该运行时间戳排除出内容版本，正文、事实、审核或文章变化仍会使评测失效。

## 非阻塞降级

WeRSS 重启后的来源读取成功，但 6 篇需要正文补全的文章中只有 2 篇补全成功，4 篇进入 `full_text_needs_review`。这是当前正文可得性降级，已经显式进入审核队列，没有被误当作完整正文或阻断其他来源。

## 部署边界

- 两处可靠性代码已使用 `install_local_runtime.py --copy-only` 同步，runtime 与源码哈希一致。
- 当日正式 `daily-20260909` 已用新版本从检查点校准，结果为 `completed / healthy / evaluation pass / not_published`；5 条正式事件、5 篇文章、26 条短讯均保留。
- 本轮没有发布网页、没有发送钉钉、没有覆盖当日审核产物。
- UI 与可靠性修改将在同一工程基线中提交、打标签并归档。
