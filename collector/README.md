# SPECTRA source collector v0.2

多种入口统一输出《情报数据结构与处理规则 v0.2》中的 `source_record`：

- `rss`：使用 `feedparser` 发现 RSS/Atom/JSON Feed 条目；
- `batch_index`：从 The Batch 官方列表页发现近一期，再把期刊正文拆成可独立追溯的单篇新闻（旧 RSS 地址已失效）；
- `arxiv`：使用 `arxiv.py` 搜索近一周视觉、世界模型和具身论文；
- `github_atom`：读取 GitHub Releases Atom；
- `news_extractor_feed`：先从公开 Feed 发现本周链接，再调用已安装的 `news-extractor` Skill 提取正文并转为统一 JSON。
- `news_extractor_inbox`：接收搜索、分享或其他连接器发现的公众号/腾讯/搜狐等文章链接，再走同一提取和标准化通道。
- `werss_api`：从本机已授权的 WeRSS 读取固定公众号白名单，将文章元数据、摘要和原文链接直接转成统一记录。
- `official_ir_index`：读取企业官方 IR 索引，并支持同一来源的多级官方备用入口。
- `hkex_title_search`：按发行人解析港交所公告表格，保留公告时间、分类和 PDF 原文链接。
- 财报 PDF 正文增强：对启用 `extract_pdf_text` 的官方 PDF 保存带页码的内部分析正文，并优先生成关注词附近的可核验摘录。
- `sec_submissions`：读取 SEC 官方结构化提交数据；当前公司网络返回 403，配置保留给外部运行环境。
- `sitemap`：从权威机构 XML sitemap 发现近期更新的报告与研究页，并明确标记日期语义为 `last_modified`。

所有入口只写 Bronze 层。采集失败也会产生 `access_status: failed` 的记录，以便与“完成检查但没有更新”区分。

## 运行

```bash
python3 -m venv .venv-collector
.venv-collector/bin/pip install -r requirements-collector.txt
.venv-collector/bin/python collector/collect.py
```

WeRSS 的管理密码只从进程环境或被 Git 忽略的 `.env.local` 读取，不写入来源配置或产出文件。每周主流程 `python3 spectra_agent/run.py run` 会自动加载 `.env.local` 并采集 `reg_werss_wechat_watchlist`。首次公众号采集也可单独执行：

```bash
WERSS_PASSWORD='你的本机 WeRSS 管理密码' \
  .venv-collector/bin/python collector/collect.py \
  --source reg_werss_wechat_watchlist \
  --output collector/runs/werss-wechat-live-v0.1.json
```

采集前会同时检查两层状态：WeRSS 管理端登录与微信公众平台扫码授权。后者过期时，
来源健康状态会明确返回 `reauth_required: true`，不会再把“11 个账号已订阅但 0 篇文章”
误报为成功；授权有效但文章库为空时返回 `sync_pending: true`。重新扫码后可用低频单账号探针恢复同步：

```bash
integrations/we-mp-rss/.venv/bin/python collector/werss_sync_one.py 机器之心 --max-page 1
```

该命令可从仓库根目录直接运行；确认单账号成功后再启动批量更新，避免延长微信频控窗口。
正式 Agent 运行会在读取 WeRSS 前按 P0/P1 优先级，通过已授权的微信读书通道半周轮换刷新
6 个公众号；周一与周四选择互补的两组，使一周内覆盖全部 11 个账号，并避免持续撞击已频控的
微信公众号文章列表接口。备用通道只负责补充来源记录，不会绕过 P1 人工审核闸门。

只检查单个来源时，可以重复使用 `--source`：

```bash
.venv-collector/bin/python collector/collect.py \
  --source reg_news_extractor_lenny_feed \
  --output collector/runs/news-extractor-check.json
```

`news-extractor` Skill 已通过独立参数数组配置在来源清单中。它的安装和依赖均位于个人 Codex Skill 目录，不复制进本仓库；适配器兼容它的 `news_url/meta_info/texts/contents` 输出。

## 社会化内容发现队列

微信公众号没有公开稳定的文章 Feed。当前固定名单通过本机 WeRSS 授权后自动发现；
临时分享或白名单外的文章链接仍可写入
`collector/news_extractor_inbox.v0.1.json`，采集器会自动识别平台、提取并标准化：

```json
{
  "records": [
    {
      "url": "https://mp.weixin.qq.com/s/ARTICLE_ID",
      "discovered_at": "2026-08-14T10:00:00+08:00",
      "discovered_by": "search",
      "discovery_context": "视频生成 模型实测",
      "source_name": "公众号名称",
      "publisher": "公众号名称",
      "enabled": true
    }
  ]
}
```

空队列属于“已检查、暂无新链接”，不会被误报为采集失败。当前可自动发现并提取的
Feed 来源包括 Lenny's Newsletter 与 BBC Technology；公众号链接发现仍需要后续搜索
连接器，Skill 本身只负责文章解析。WeRSS 只保存摘要级 `source_record`；需要核验正文时，
后续再把入围文章原文链接交给 `news-extractor`。

中文公众号固定名单维护在 `collector/wechat_watchlist.v0.1.json`。企业财报、监管披露
和公开研究报告维护在 `collector/financial_public_report_watchlist.v0.1.json`，它们不经过
新闻正文提取器，后续分别由官方 IR、HKEX/SEC 和 PDF 报告适配器采集，再统一输出
`source_record`。

当前本机已验收的财经/公开报告入口包括阿里、腾讯、快手官方入口与港交所降级页、
NVIDIA 官方新闻 RSS、WIPO RSS 和 Stanford HAI sitemap。快手的 IR 域在当前网络会超时，
港交所入口可保证来源检查不中断，但其标题检索页仍需专用解析器才能稳定提取公告正文。

## 输出

- `collector/runs/latest.json`：完整运行结果、来源健康状态及统一的 `source_records`；
- 每条记录都保留 `registry_id`、来源地址、原始标题/摘要、发布日期、采集状态和失败原因；
- `canonical_url` 去掉常见追踪参数，`source_id` 根据规范化 URL 稳定生成。

## 当前边界

- RSS 和 GitHub Atom 只保存元数据与必要摘要，不复制整篇受版权保护正文；
- arXiv 只保存论文元数据和摘要；
- `news-extractor` 必须作为隔离工具调用，避免 GPL-3.0 代码直接并入主体代码；
- 自动去重目前是 URL 级，事件级转载聚合属于下一处理阶段。
