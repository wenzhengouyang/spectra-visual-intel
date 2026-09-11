"""Bounded preflight, early coverage decision, and durable image readiness window."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
import json
import fcntl
from pathlib import Path
from spectra_agent.safe_http import resolve_public


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def network_preflight(run_dir, resolver=resolve_public):
    results = []
    for url in ('https://arxiv.org', 'https://www.tencent.com', 'https://github.com'):
        try:
            resolver(url)
            results.append({'url': url, 'status': 'passed'})
        except (ValueError, OSError) as exc:
            results.append({'url': url, 'status': 'failed', 'error': str(exc)})
    report = {'status': 'passed' if any(r['status'] == 'passed' for r in results) else 'blocked',
              'checked_at': datetime.now(timezone.utc).isoformat(), 'checks': results,
              'scope': 'dns_and_public_address_only'}
    save(Path(run_dir) / 'network-preflight.json', report)
    if report['status'] == 'blocked':
        raise RuntimeError('Network preflight blocked: check network / execution permissions before collection')
    return report


def coverage_decision(run_dir, collection, threshold=.9):
    checks = collection.get('source_checks', [])
    success = sum(item.get('status') == 'success' for item in checks)
    ratio = success / len(checks) if checks else 0
    decision = {'source_success_rate': ratio, 'threshold': threshold,
                'recommended_mode': 'normal' if ratio >= threshold else 'limited_requires_approval',
                'publication_authorized': False,
                'reason': '采集覆盖不足时提前请求有限发布决定；不改变事实审核或最终评测。'}
    save(Path(run_dir) / 'early-publication-mode.json', decision)
    return decision


def image_window(run_dir, now=None):
    now = now or datetime.now(timezone.utc)
    path = Path(run_dir) / 'image-agent-window.json'
    if path.exists():
        return json.loads(path.read_text())
    value = {'ready_at': now.isoformat(), 'expires_at': (now + timedelta(minutes=30)).isoformat(),
             'attempts': 0, 'max_attempts': 3,
             'due_at': [(now + timedelta(minutes=m)).isoformat() for m in (0,10,20)],
             'status': 'ready', 'dispatch_status': 'pending'}
    save(path, value)
    return value


def reserve_image_attempt(run_dir, now=None):
    now = now or datetime.now(timezone.utc)
    root = Path(run_dir)
    with (root / 'image-agent-window.lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        value = image_window(root, now)
        attempts = value['attempts']
        if attempts >= 3 or now >= datetime.fromisoformat(value['expires_at']):
            return False
        if now < datetime.fromisoformat(value['due_at'][attempts]):
            return False
        value['attempts'] += 1
        value['last_started_at'] = now.isoformat()
        save(root / 'image-agent-window.json', value)
        return True
