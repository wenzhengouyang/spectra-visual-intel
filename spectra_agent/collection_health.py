"""Shared collection accounting and idempotent coverage ingestion."""
from __future__ import annotations

import json
from pathlib import Path

from collector.merge_incremental_runs import merge
from spectra_agent.execution import atomic_json, digest


def read(path):
    path = Path(path)
    return json.loads(path.read_text()) if path.exists() else {}


def metrics(run_dir):
    root = Path(run_dir)
    collection = read(root / 'collection.json')
    delta = read(root / 'collection.incremental.json')
    coverage = read(root / 'coverage-collection.json')
    summary = collection.get('summary', {})
    records = collection.get('source_records', [])
    history = {}
    prior_checks = []
    for previous in sorted(root.parent.glob('daily-*')):
        if previous.name >= root.name:
            continue
        previous_bundle = read(previous / 'collection.json')
        prior_checks.append({c['registry_id']: c for c in previous_bundle.get('source_checks', [])})
        for record in previous_bundle.get('source_records', []):
            history[record.get('canonical_url')] = record
    first_seen = sum(r.get('canonical_url') not in history for r in records)
    content_changes = sum(r.get('canonical_url') in history and r.get('content_hash') != history[r['canonical_url']].get('content_hash') for r in records)
    candidates = read(root / 'candidates.json').get('summary', {})
    checks = {}
    for lane, bundle in [('mainline', collection), ('coverage', coverage)]:
        for check in bundle.get('source_checks', []):
            checks[(lane, check['registry_id'])] = dict(check, lane=lane)
    for check in checks.values():
        error = str(check.get('error', '')).lower()
        if '404' in error:
            check['health_action'] = '来源地址失效，需核对官方入口；不自动重复请求'
        elif '429' in error:
            check['health_action'] = '限流，等待冷却后补采'
        elif 'dns' in error:
            check['health_action'] = '域名解析失败，检查网络后补采'
        elif 'timeout' in error or 'timed out' in error:
            check['health_action'] = '超时，定向补采'
        elif check.get('status') == 'success' and check.get('record_count') == 0:
            check['health_action'] = '请求完成但本窗口无记录；不能据此断言来源没有更新'
            reasons = check.get('rejected_by_reason', {})
            labels = {'keyword_miss': '主题关键词未命中', 'title_include_miss': '标题未命中白名单', 'title_excluded': '标题命中排除项', 'missing_date': '缺少日期', 'outside_window': '不在时间窗口'}
            if reasons:
                check['health_action'] = '过滤明细：' + '；'.join(f'{labels.get(k, k)} {v} 条' for k, v in reasons.items())
            if check.get('in_window', 0) and not check.get('accepted', 0):
                check['health_warning'] = 'all_in_window_filtered'
            # Alert only if this source previously yielded records. Quiet new or
            # low-frequency sources do not become failures solely from zero counts.
            recent = [p[check['registry_id']] for p in prior_checks[-7:] if check['registry_id'] in p]
            if recent and recent[-1].get('record_count') == 0 and any(c.get('record_count', 0) > 0 for c in recent):
                check['health_action'] += '；连续空结果，且近期曾有产出：需检查解析器或窗口过滤'
                check['health_warning'] = 'unexpected_empty_streak'
        elif check.get('summary_only_count', 0) and not check.get('full_text_count', 0):
            check['health_action'] = '仅取得摘要，正文获取情况需关注'
        else:
            check['health_action'] = check.get('failure_reason') or '—'
    return {
        'rolling_records': len(records),
        'historical_reused': summary.get('baseline_records_reused', 0),
        'mainline_fetched': len((delta or collection).get('source_records', [])),
        'changed_records': summary.get('changed_records'),
        'first_seen_urls': first_seen if history else None,
        'updated_known_urls': content_changes if history else None,
        'coverage_fetched': len(coverage.get('source_records', [])),
        'coverage_receipt': read(root / 'coverage-ingestion.json'),
        'candidate_funnel': candidates,
        'source_checks': list(checks.values()),
        'note': '抓取量、内容变化量均不等于全新新闻事件数；两条线可能重叠。',
    }


def ingest_coverage(run_dir):
    """Called only under the run lease. Receipt follows durable merged output."""
    root = Path(run_dir)
    coverage = read(root / 'coverage-collection.json')
    if read(root / 'coverage-line.json').get('status') != 'completed' or not coverage:
        return False
    fingerprint = digest(coverage)
    receipt_path = root / 'coverage-ingestion.json'
    receipt = read(receipt_path)
    pending = receipt.get('fingerprint') == fingerprint and receipt.get('status') == 'pending_structure'
    if receipt.get('fingerprint') == fingerprint and not pending:
        return False
    baseline = read(root / 'collection.json')
    if not baseline:
        return False
    from collector.merge_incremental_runs import parse_time
    end = max((baseline['window_end'], coverage.get('window_end', baseline['window_end'])), key=parse_time)
    merged = merge(baseline, coverage, None, baseline['window_start'], end)
    old = {r['canonical_url']: r for r in baseline.get('source_records', [])}
    new_urls, changed_urls = [], []
    for record in merged['source_records']:
        prior = old.get(record['canonical_url'])
        if prior is None:
            new_urls.append(record['canonical_url'])
        elif (prior.get('content_hash'), prior.get('access_status')) != (record.get('content_hash'), record.get('access_status')):
            changed_urls.append(record['canonical_url'])
    changed = bool(new_urls or changed_urls)
    receipt = receipt if pending else {'fingerprint': fingerprint, 'new_urls': new_urls, 'changed_urls': changed_urls,
               'status': 'pending_structure' if changed else 'consumed',
               'new_record_count': len(new_urls), 'changed_record_count': len(changed_urls)}
    # Write intent before output, so a crash cannot forget to rebuild candidates.
    atomic_json(receipt_path, receipt)
    if changed:
        if not (root / 'collection.before-coverage.json').exists():
            atomic_json(root / 'collection.before-coverage.json', baseline)
        merged['run_id'] = baseline.get('run_id')
        for key in ('display_window_start', 'display_window_end', 'display_window_mode'):
            if key in baseline:
                merged[key] = baseline[key]
        merged['display_window_end'] = end
        merged['mainline_source_checks'] = baseline.get('mainline_source_checks', baseline.get('source_checks', []))
        merged['coverage_source_checks'] = coverage.get('source_checks', [])
        atomic_json(root / 'collection.json', merged)
    return changed or pending


def mark_coverage_consumed(run_dir):
    path = Path(run_dir) / 'coverage-ingestion.json'
    receipt = read(path)
    if receipt.get('status') == 'pending_structure':
        receipt['status'] = 'consumed'
        atomic_json(path, receipt)
