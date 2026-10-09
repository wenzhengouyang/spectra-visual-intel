"""Non-blocking official catalog checks, using the existing recovery trigger."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from spectra_agent.collection_health import read
from spectra_agent.execution import atomic_json


def status(run_dir):
    root = Path(run_dir)
    state = read(root / 'model-collection.json')
    official = read(root.parent.parent / 'model-universe/latest.json')
    return {**state, 'last_checked_at': official.get('checked_at'),
            'last_verified_at': official.get('last_verified_at'),
            'pending_verification': len(official.get('pending_verification', {})),
            'source_count': len(official.get('results', [])),
            'errors': sum(r.get('status') == 'error' for r in official.get('results', [])),
            'news': read(root / 'model-news.json'),
            'note': '官网变更待证据核验，不自动改版本与发布日期。'}


def launch(root, run_dir, python):
    run_dir = Path(run_dir)
    state = read(run_dir / 'model-collection.json')
    if (state.get('status') == 'completed' or state.get('attempts', 0) >= 2
            or time.time() < state.get('next_retry_at', 0)):
        return
    subprocess.Popen([python, '-m', 'spectra_agent.model_health', str(run_dir)],
                     cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def work(run_dir):
    root = Path(__file__).resolve().parents[1]
    state_dir = run_dir.parent.parent / 'model-universe'
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'execution.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        prior = read(run_dir / 'model-collection.json')
        if prior.get('status') == 'completed' or prior.get('attempts', 0) >= 2 or time.time() < prior.get('next_retry_at', 0):
            return
        state = {'status': 'running', 'attempts': prior.get('attempts', 0) + 1,
                 'next_retry_at': time.time() + 1800, 'blocking_daily': False}
        atomic_json(run_dir / 'model-collection.json', state)
        try:
            result = subprocess.run([sys.executable, str(root / 'scripts/model-universe.py'), 'collect'],
                                    cwd=root, env={**os.environ, 'SPECTRA_MODEL_STATE': str(state_dir)},
                                    capture_output=True, text=True, timeout=1200)
            failures = [r for r in read(state_dir / 'latest.json').get('results', []) if r.get('status') == 'error']
            state['status'] = 'completed' if result.returncode == 0 and not failures else 'retry_pending'
            state['error'] = result.stderr[-1000:] if result.returncode else f'{len(failures)} 个官网需重试'
        except Exception as exc:
            state.update(status='retry_pending', error=str(exc))
        if state['status'] != 'completed' and state['attempts'] >= 2:
            state['status'] = 'needs_attention'
        atomic_json(run_dir / 'model-collection.json', state)


if __name__ == '__main__':
    work(Path(sys.argv[1]))
