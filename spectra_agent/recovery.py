"""Single bounded recovery decision point, driven by daily_runner."""
from datetime import datetime, timezone
from pathlib import Path
import time

from spectra_agent.collection_health import read
from spectra_agent.execution import atomic_json, digest, RunLease


def coverage_pending(root):
    payload = read(root / 'coverage-collection.json')
    return bool(payload and read(root / 'coverage-line.json').get('status') == 'completed'
                and (read(root / 'coverage-ingestion.json').get('fingerprint') != digest(payload)
                     or read(root / 'coverage-ingestion.json').get('status') == 'pending_structure'))


def source_retry_ids(root):
    checks = {}
    for name in ('collection.json', 'coverage-collection.json'):
        for item in read(Path(root) / name).get('source_checks', []):
            checks[item['registry_id']] = item
    return sorted(key for key, item in checks.items() if item.get('status') == 'failed'
                  and any(word in str(item.get('error', '')).lower()
                          for word in ('timeout', 'timed out', 'dns', '429', 'connection')))


def inspect(root, action, now=None, worker_active=None):
    """No side effects: the same decision is used by the UI and scheduler."""
    root = Path(root)
    now = time.time() if now is None else now
    state = read(root / 'run.json')
    recovery = read(root / 'recovery-state.json')
    stage = state.get('current_stage')
    if worker_active is None and state.get('status') in {'running', 'waiting_for_editorial'}:
        return {'action': 'observe', 'reason': '阶段执行中，巡检会检查执行锁后决定是否需要恢复。'}
    coverage_state = read(root / 'coverage-line.json')
    if action == 'noop_completed' and coverage_state.get('status') == 'running':
        started = datetime.fromisoformat(coverage_state['started_at'].replace('Z', '+00:00')).timestamp()
        if now - started > 2100:
            return {'action': 'alert', 'reason': '覆盖补齐超过执行时限，需检查工作进程。'}
        return {'action': 'noop_coverage_running', 'reason': '覆盖补齐仍在运行'}
    if stage == 'image_generation':
        return {'action': 'alert', 'reason': '配图队列等待执行器接单；当前本地调度尚未接通图像生成执行器。'}
    # Background coverage can finish after the mainline has reached the fact
    # review gate.  In that state the new records must still rebuild the
    # candidate/review packet; otherwise the dashboard remains stuck on the
    # pre-coverage empty queue until the next day.
    if action in {'noop_completed', 'noop_human_gate'} and coverage_pending(root):
        action = 'resume_retry'
    if action == 'noop_completed' and source_retry_ids(root):
        attempts = read(root / 'source-recovery.json')
        if attempts.get('attempts', 0) >= 2:
            return {'action': 'alert', 'reason': '失败来源已补采两轮，仍有异常；请查看来源表。'}
        if now < attempts.get('next_retry_at', 0):
            return {'action': 'cooldown', 'reason': '来源冷却后定向补采', 'next_retry_at': attempts['next_retry_at']}
        return {'action': 'retry_sources', 'reason': '定向补采超时、DNS 或限流来源'}
    if action.startswith('noop'):
        return {'action': action, 'reason': '等待审核' if action == 'noop_human_gate' else '本轮已完成'}
    key = digest([stage, state.get('error'), read(root / 'coverage-collection.json'),
                  read(root / 'p1-review.json'), read(root / 'p2-localization-review.json'),
                  read(root / 'image-review.json')])
    attempt = recovery.get('attempts', 0) if recovery.get('key') == key else 0
    if attempt >= 2:
        return {'action': 'alert', 'reason': '该故障自动恢复两次仍未完成，需要处理。', 'key': key}
    due = recovery.get('next_retry_at', 0) if recovery.get('key') == key else 0
    if now < due:
        return {'action': 'cooldown', 'reason': '等待重试时间', 'next_retry_at': due, 'key': key}
    return {'action': action, 'key': key, 'attempts': attempt, 'reason': '从现有检查点继续'}


def reserve(root, decision):
    atomic_json(Path(root) / 'recovery-state.json', {
        **decision, 'attempts': decision.get('attempts', 0) + 1,
        'next_retry_at': time.time() + 600,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    })


def occupied(root):
    lease = RunLease(root)
    if not lease.acquire(record=False):
        return True
    lease.release()
    return False
