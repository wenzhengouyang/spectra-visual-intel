# SPECTRA 工程整理基线（2026-09-08）

本文件记录清理和命名迁移完成后的工程边界，供后续修改、运行与审核时对照。

## 目录边界

| 内容 | 固定位置 | 约束 |
| --- | --- | --- |
| 源码、配置、测试、静态页面 | `/Users/wenzheng/Documents/ChatGPT/agent` | Git 管理，不写运行日志与批次产物 |
| 本地执行环境 | `~/Library/Application Support/SPECTRA/runtime` | 由安装脚本从源码同步，不作为数据仓库 |
| Run 产物 | `~/Library/Application Support/SPECTRA/data/runs` | 每次运行独立目录，不进入 Git |
| 日志 | `~/Library/Application Support/SPECTRA/data/logs` | 调度器与运行日志集中存放 |
| 发布缓存 | `~/Library/Application Support/SPECTRA/data/publish` | 只存发布过程的临时检出与缓存 |

可通过 `SPECTRA_DATA_ROOT` 覆盖数据根目录；代码不得重新把产物写回源码树。

## 统一命名

- 内容类型统一为 `core_event`，对应产物为 `core-event-drafts.json`、`core-event-checkpoint.json`、`core-event-audit.json`。
- 报告统一为 rolling digest，页面产物为 `rolling-digest.html`，结构字段为 `rolling_thesis`。
- 旧的 `deep_story`、`p1-long-*`、`p1_long_*`、`weekly-*` 只允许在 `spectra_agent/compat.py` 和对应兼容性测试中出现；业务模块不得直接读取旧名。
- `compatible_artifact` 的兼容读取不修改历史 Run；只有规范化流程或显式迁移才会生成规范文件名或重写可再生派生产物。

## 发布与审核边界

标准链路为：采集 → 结构化 → 事实核验 → 人工事实审核 → `core_event` 写作与证据审计 → 本地化 → 图片预览 → 发布物质量校验 → 只读 Run 评测 → 本地草稿。

`core_event 0/1`、图片未人工确认、语言或证据质量未通过时，运行必须停在审核态并保持 `not_published`。测试通过、页面生成、调度器运行和线上发布是四个独立状态，不得互相替代。

## 基线验证

在源码根目录执行：

```bash
PYTHONPYCACHEPREFIX=/tmp/spectra-pycache .venv-collector/bin/python -m unittest discover -s tests -v
PYTHONPYCACHEPREFIX=/tmp/spectra-pycache .venv-collector/bin/python -m compileall -q collector editorial processor spectra_agent verification scripts tests
plutil -lint spectra_agent/launchd/com.spectra.visual-intel.daily.plist spectra_agent/launchd/com.spectra.visual-intel.dingtalk.plist
git diff --check
```

结构化与发布物校验必须显式传入同一个 Run 的文件，避免把不同历史样刊误配为一组：

```bash
.venv-collector/bin/python scripts/validate-structured-run.py "$RUN_DIR/candidates.json"
.venv-collector/bin/python scripts/validate-editorial-issue.py \
  --editorial "$RUN_DIR/editorial-issue.json" \
  --verified "$RUN_DIR/verified-events.json" \
  --config spectra_agent/config.v0.1.json
```

本基线建立时，Python 回归测试为 209 项全部通过。`daily-20260907` 的事实人工审核已完成，但因 `core_event 0/1` 仍停在内容质量审核且未发布；这是质量闸门的正常阻断状态，不属于工程失败。
