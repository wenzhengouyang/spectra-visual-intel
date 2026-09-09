# SPECTRA 总览光栅设计

- Figma: https://www.figma.com/design/IHON7EPd6BYQqELsOiotBv?node-id=3-129
- 总览画板：3:129，1440 × 1040。
- 按钮状态样式：11:35，默认、悬停、按下、禁用。
- 光栅层：4:2，可编辑矢量，位于右上方背景，不覆盖实色阅读面板。
- 延续 B 版深蓝黑风格；总览保留一句话判断、趋势雷达、近 7 日无图时间轴。
- 中文字体：Figma 可用 Noto Sans SC；总览文字节点已检查。
- 页面示例内容沿用已选原型，不代表当前情报发布数据。

## 状态

2026-09-10：已接入共享生成模板与本地首页。光栅使用 CSS 绘制，按钮具备悬停、按下和焦点状态；雷达联动关联日期和事件，阅读返回保持原视图与位置，文章与视觉灵感库可以往返。

文章页保留三列有图核心事件、无图近期短讯。灵感库展示正式资讯的方法、能力边界与来源。预览首页数据沿用 9 月 9 日正式内容，并非 9 月 10 日新增资讯。

钉钉改为品牌头图、日期、引用式结论、编号 Top 3 和单一工作台入口；仅更新模板，未重复发送。预览见 dingtalk-preview-20260910.md。

验证：280 项 Python 测试通过；scripts/check-ui.cjs 验证桌面/390px 手机、三列卡片、无图时间轴、雷达点击、阅读返回、灵感库，未发现 JS 异常。从正式 runtime 生成 /tmp/spectra-release-check/rolling-digest.html 并通过静态包验证。

已同步到 /Users/wenzheng/Library/Application Support/SPECTRA/runtime，7 个文件的 SHA-256 一致。备份位于 data/deployment-backups/ui-20260910-013035。runtime probe 返回 ready。已检查 launchd：每日 08:00 加载正式 runtime；钉钉任务仍启用。

边界：08:00 自动采集和生成仍受事实、内容与图片审核约束，publish_run 是独立的发布动作。此次 UI 部署不代表下一期内容已审核或线上已发布。
