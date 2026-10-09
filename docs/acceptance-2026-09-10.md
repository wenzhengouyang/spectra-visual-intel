# 2026-09-10 验收候选版

验收入口：[本机工作台](http://127.0.0.1:4174/index.html) · [正式运行进度](http://127.0.0.1:8010/) · [正式报告预览](http://127.0.0.1:8010/run-artifact/rolling-digest.html)

范围：有人审核的内部视觉情报工作台。功能验收、真实内容通过率、连续运行稳定性分别记录；进度页的 90% 仅表示该次运行等待发布，不代表项目完成度。

| 验收项目 | 本轮结果 | 明早操作 |
|---|---|---|
| 桌面/手机浏览 | 通过原有浏览器回归，无 JS 异常、横向溢出 | 切换总览、文章、关注、灵感库 |
| 趋势与阅读返回 | 通过 | 点击趋势、打开事件、返回原位置 |
| 核心事件分层 | 通过新增浏览器回归 | 核心事件筛选只保留核心事件，行业信号标签单独显示 |
| 数量口径 | 已统一 | 核心事件数量不再加上行业信号 |
| 反馈实际生效 | 通过新增浏览器回归 | 标为有用后在我的关注可见，标为不相关后隐藏；完整列表仍保留 |
| 反馈持久化 | 通过 | 刷新后反馈仍在；仅当前浏览器生效，不声称训练后端模型 |
| 跨期分享 | 单元测试及浏览器通过 | 公开链接指向 archive/运行编号/index.html；新期发布不覆盖旧期 |
| 质量失败提示 | 已复现旧 bug 并回归通过 | 失败评测或驳回封面不能将质量阶段标成完成 |
| 报告预览资源 | 正式服务 HTTP 200 | 打开正式报告，确认样式、分享、灵感库正常 |
| 后端回归 | 286 项测试通过，git diff --check 通过 | 如需复查运行下述命令 |
| 发布重试 | 空暂存区仍要求推送成功后才标记发布，成功/失败回归均通过 | 避免上次推送失败被误报已发布 |
| 提示升级与检查点 | 升级提示后复用已审计通过的稿件，事实/窗口/模型变化仍使缓存失效 | 不因本次升级重复消耗已完成稿件 |
| 本机运行副本 | 已备份同步并校验；探针 ready | 查看 08:00 后任务状态，人工审核暂停为预期流程 |
| 事实与审核保护 | 31 份 JSON 哈希保持不变 | 未批准、未重写事实或封面审核 |
| 写作提示冲突 | 已修复并同步 | 去掉从上下文补新闻凑字数的要求，保留原有审计门槛 |
| 写作真实回归 | 未通过：强化审计拦截无证据推断；追加两次定点修订后仍因归因缺失降级，core_events=0 / demoted=1 | 不计为合格产出，不替换正式稿 |
| 审计升级兼容 | 已完成缓存按当前规则重审，旧 pass 不能绕过新门禁 | 更严格规则发现问题后仅重修该稿 |
| 连续 7–14 天交付 | 待观察 | 记录来源成功率、写作通过率、人工耗时、发布与推送结果 |
| 公网发布/钉钉发送 | 本轮未执行 | 本轮交付为本机候选与正式 runtime 更新 |

## 回归命令

```sh
.venv-collector/bin/python -m unittest discover -s tests
SPECTRA_PLAYWRIGHT=/Users/wenzheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright node scripts/check-ui.cjs
SPECTRA_PLAYWRIGHT=/Users/wenzheng/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright node scripts/check-acceptance.cjs
```

## 回退与边界

- 本机代码备份：`~/Library/Application Support/SPECTRA/data/deployment-backups/ui-20260910-015728/`。
- 正式报告界面备份：`~/Library/Application Support/SPECTRA/data/deployment-backups/preview-20260910-015938/`。
- 配置版本号备份：`~/Library/Application Support/SPECTRA/data/deployment-backups/ui-20260910-015945/`；只更新提示版本，保留其他配置。
- 原有 magazine 实验文件及其他任务正在修改的钉钉文件保留，本轮没有替其提交或部署。
- 最终增量代码备份：`~/Library/Application Support/SPECTRA/data/deployment-backups/ui-20260910-020757/`；最终预览备份：`preview-20260910-020802/`。
- 本轮尚未证明真实写作通过率提高，不能将单晚回归替代连续运行验收。
- 实测还暴露单篇生成与修订耗时数分钟的性能限制。整体超过 90% 尚未证实；本版本可验收已完成的功能，但内容产出稳定性仍未达标。
- 实测机器可读摘要：[writer-acceptance-20260910.json](./writer-acceptance-20260910.json)。
