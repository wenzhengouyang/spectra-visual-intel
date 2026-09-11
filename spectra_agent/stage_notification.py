"""Deduplicated stage dialog. OS success is not a human approval."""
import fcntl
import json
from pathlib import Path
import subprocess
import sys

run_dir = Path(sys.argv[1]).resolve()
with (run_dir / 'stage-notification.lock').open('a') as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    state = json.loads((run_dir / 'run.json').read_text())
    stage = state.get('current_stage', 'unknown')
    path = run_dir / 'stage-notification.json'
    records = json.loads(path.read_text()) if path.exists() else {}
    key = state.get('status', '') + ':' + stage
    prior = records.get(key, {})
    if prior.get('status') == 'delivered' or prior.get('attempts', 0) >= 3:
        raise SystemExit(0)
    record = {'attempts': prior.get('attempts', 0) + 1, 'status': 'attempting'}
    records[key] = record
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
    mode_path = run_dir / 'early-publication-mode.json'
    mode = json.loads(mode_path.read_text()) if mode_path.exists() else {}
    coverage_note = '\n采集覆盖不足，请决定是否有限发布；默认不放行。' if mode.get('recommended_mode') == 'limited_requires_approval' else ''
    message = f"SPECTRA {run_dir.name}：{stage}\n{state.get('paused_reason') or state.get('error') or '需要处理'}{coverage_note}\n请在工作台查看，提示不会代替审核批准。"
    script = 'on run argv\ndisplay dialog (item 1 of argv) with title "SPECTRA 阶段提醒" buttons {"稍后", "打开工作台"} default button "打开工作台" giving up after 30\nif button returned of result is "打开工作台" then open location "http://127.0.0.1:8010/"\nend run'
    try:
        result = subprocess.run(['/usr/bin/osascript', '-e', script, message], timeout=40, capture_output=True)
        record['status'] = 'delivered' if result.returncode == 0 else 'failed'
        record['error'] = result.stderr.decode(errors='replace')[:500] if result.returncode else None
    except (OSError, subprocess.TimeoutExpired) as exc:
        record.update(status='failed', error=str(exc))
    path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
