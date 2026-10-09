#!/usr/bin/env python3
"""Build a sanitized, read-only SPECTRA progress snapshot for public sharing."""

from __future__ import annotations

import argparse
import html
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from spectra_agent.progress_server import build_status
from spectra_agent.run import DEFAULT_CONFIG, resolve_config


def render(status: dict) -> str:
    esc = lambda value: html.escape(str(value if value is not None else "—"))
    label = html.escape(str(status.get("label") or "暂无运行"))
    run_id = html.escape(str(status.get("run_id") or "—"))
    updated = html.escape(str(status.get("updated_at") or "暂无更新时间"))
    percent = max(0, min(int(status.get("overall_percent") or 0), 100))
    stages = "".join(
        "<li class='{}'><i>{}</i><div><strong>{}</strong><span>{}</span></div></li>".format(
            html.escape(str(stage.get("state") or "pending")),
            "✓" if stage.get("state") == "done" else index + 1,
            html.escape(str(stage.get("name") or "")),
            html.escape(str(stage.get("detail") or "")),
        )
        for index, stage in enumerate(status.get("stages") or [])
    )
    lanes = status.get("collection_lanes") or {}
    accounting = status.get("collection_accounting") or {}
    model = status.get("model_collection") or {}
    health = accounting.get("source_checks") or status.get("collection_health") or []
    source_rows = "".join(
        "<tr><td><strong>{}</strong><span>{}</span></td><td><b class='health {}'>{}</b></td>"
        "<td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            esc(source.get("source_name") or source.get("registry_id") or "未知来源"),
            "覆盖补齐" if source.get("lane") == "coverage" else "日报主线",
            esc(source.get("status") or "unknown"), esc(source.get("status") or "未知"),
            esc(source.get("record_count", source.get("accepted"))),
            esc(source.get("full_text_count", "未记录")),
            esc(source.get("summary_only_count", "未记录")),
            esc(source.get("health_action") or source.get("failure_reason") or source.get("error") or source.get("note") or "正常"),
        )
        for source in health
    ) or "<tr><td colspan='6'>尚无来源结果</td></tr>"
    generated = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{label} · SPECTRA 运行进度</title>
<style>
:root{{--paper:#080d14;--raised:#101924;--ink:#edf3fa;--muted:#8e9bad;--rule:#2b3948;--accent:#63b5d4;--green:#79c99b}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:16px/1.55 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}
main{{width:min(52rem,100%);margin:auto;padding:clamp(1.25rem,5vw,3.5rem)}}header{{padding-bottom:1.5rem;border-bottom:1px solid var(--rule)}}
.brand{{color:var(--accent);font:700 .75rem/1 ui-monospace,monospace;letter-spacing:.14em}}h1{{margin:.7rem 0 0;font-size:clamp(2rem,8vw,4rem);letter-spacing:0}}
.summary{{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:1rem;align-items:end;padding:2rem 0;border-bottom:1px solid var(--rule)}}
.summary h2{{margin:0;font-size:clamp(1.4rem,5vw,2.25rem)}}.summary p,.meta,li span{{color:var(--muted)}}.percent{{font-size:clamp(2.5rem,10vw,4.5rem);line-height:1;text-align:right}}
.bar{{grid-column:1/-1;height:.4rem;background:var(--rule)}}.bar i{{display:block;width:{percent}%;height:100%;background:var(--accent)}}
ol{{list-style:none;margin:0;padding:1rem 0}}li{{display:grid;grid-template-columns:2rem 1fr;gap:.8rem;padding:1rem 0;border-bottom:1px solid var(--rule)}}
li i{{display:grid;place-items:center;width:1.7rem;height:1.7rem;border:1px solid var(--rule);border-radius:50%;font-style:normal;font-size:.72rem}}li.done i{{border-color:var(--green);color:var(--green)}}li.current i{{border-color:var(--accent);color:var(--accent)}}
	li strong,li span{{display:block}}li span{{margin-top:.2rem;font-size:.87rem}}footer{{padding-top:1rem;color:var(--muted);font-size:.78rem}}
	.health-section{{padding:2rem 0;border-top:1px solid var(--rule)}}.health-section h2{{margin:0 0 1rem;font-size:1.35rem}}.health-grid{{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:1px;background:var(--rule);border:1px solid var(--rule)}}
	.health-card{{padding:1rem;background:var(--raised)}}.health-card strong,.health-card span{{display:block}}.health-card span{{margin-top:.35rem;color:var(--muted);font-size:.82rem}}.table-wrap{{overflow-x:auto;margin-top:1rem}}
	table{{width:100%;min-width:48rem;border-collapse:collapse;font-size:.82rem}}th,td{{padding:.75rem;text-align:left;border-bottom:1px solid var(--rule);vertical-align:top}}th{{color:var(--muted);font-weight:500}}td span{{display:block;color:var(--muted);font-size:.72rem}}.health{{font-weight:600;color:var(--accent)}}.health.success,.health.completed{{color:var(--green)}}.health.failed,.health.error{{color:#ef8b80}}
	@media(max-width:42rem){{.summary{{grid-template-columns:1fr auto}}main{{padding:1rem}}.health-grid{{grid-template-columns:1fr}}}}
	</style></head><body><main><header><div class="brand">SPECTRA / PUBLIC STATUS</div><h1>运行进度</h1></header>
	<section class="summary"><div><h2>{label}</h2><p>运行编号 {run_id}<br>最后更新 {updated}</p></div><div class="percent">{percent}%</div><div class="bar"><i></i></div></section>
	<ol>{stages}</ol>
	<section class="health-section"><h2>采集健康</h2><div class="health-grid">
	<div class="health-card"><strong>日报主线 · {esc((lanes.get('mainline') or {}).get('status') or '未开始')}</strong><span>{esc((lanes.get('mainline') or {}).get('source_count') or 0)} 个固定来源，直接进入日报</span></div>
	<div class="health-card"><strong>覆盖补齐线 · {esc((lanes.get('coverage') or {}).get('status') or '未开始')}</strong><span>{esc((lanes.get('coverage') or {}).get('source_count') or 0)} 个来源，不阻塞日报</span></div>
	<div class="health-card"><strong>模型官网巡检 · {esc(model.get('status') or '未开始')}</strong><span>{esc(model.get('source_count') or 0)} 个来源 · 错误 {esc(model.get('errors') or 0)} · 待核验 {esc(model.get('pending_verification') or 0)}</span></div>
	</div><p class="meta">滚动记录 {esc(accounting.get('rolling_records'))} · 历史复用 {esc(accounting.get('historical_reused'))} · 主线抓取 {esc(accounting.get('mainline_fetched'))} · 补齐抓取 {esc(accounting.get('coverage_fetched'))}</p>
	<div class="table-wrap"><table><thead><tr><th>来源</th><th>状态</th><th>记录</th><th>有正文</th><th>仅摘要</th><th>原因与处理</th></tr></thead><tbody>{source_rows}</tbody></table></div></section>
	<footer>只读公开快照 · 生成于 {html.escape(generated)} · 审核与发布操作仅在受保护的内部工作台完成。</footer>
	</main></body></html>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    _, config = resolve_config(args.config)
    output = Path(args.output).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(build_status(config)), encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
