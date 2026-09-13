# 模型宇宙

正式入口由 `assets/model-universe/mount.js` 接入总览，隔离样式的子页面为 `assets/model-universe/index.html`。目录唯一更新源为 `assets/model-universe/catalog.json`，包含 99 个模型、产品和分支条目；并非声称覆盖全市场全部基础模型。

## 独立更新

运行 `python3 scripts/model-universe.py collect` 采集目录中所有官方主来源及补充来源；首次建立基线，以后比较正文摘要。失败记录保留且不覆盖成功基线。状态、正文证据、历史、审核记录都保存于 `collector/model-universe-runs/`，不进入 Git。

网页变化不会直接改变版本或发布日期。核实后创建审核 JSON，运行 `python3 scripts/model-universe.py apply review.json`。每条 changes 项包含 id、reviewer、snapshot_sha256、evidence_quote、fields；fields 支持 currentVersion、currentVersionStatus、releaseDate、releaseDateStatus、access、versions、update、change。证据必须来自该模型官方来源的已采集正文，发布日期不能在未来。保留更改前目录和审核记录，随后自动重建页面。

运行 `node scripts/build-model-universe.cjs` 可单独重建。本模块独立于日报采集和发布；目前提供主动运行命令，未配置定时执行。页面未披露的字段仍须标注待核实，网页反爬或 JS 渲染失败在采集报告中列出。新增未收录公司和模型仍需扩充目录，页面差异检测不等同于全网发现。

视觉源在 `previews/overview-models-20260911/`，六方向使用对称椭圆布局及统一尺度，背景静止。
