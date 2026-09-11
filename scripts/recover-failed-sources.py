"""Bounded, separate recovery snapshot; never edits a published run or sends messages."""
import argparse
import json
import subprocess
import sys
import socket
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument('--run-dir', required=True, type=Path)
p.add_argument('--output', required=True, type=Path)
p.add_argument('--evaluate-only', action='store_true')
a = p.parse_args()
prior = json.loads((a.run_dir / 'collection.json').read_text())
# Fail once at the environment boundary instead of emitting dozens of source failures.
probes = {}
for host in ('arxiv.org', 'www.tencent.com', 'github.com'):
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        probes[host] = 'resolved'
    except OSError as exc:
        probes[host] = f'{type(exc).__name__}: errno={exc.errno}'
if all(value != 'resolved' for value in probes.values()):
    raise SystemExit('Network preflight failed; check execution permissions/network: ' + json.dumps(probes))
selected = [s['registry_id'] for s in prior['source_checks'] if s['status'] in ('failed', 'degraded')]
cmd = [sys.executable, 'collector/collect.py', '--output', str(a.output),
       '--start', prior.get('display_window_start') or prior['window_start'],
       '--end', prior.get('display_window_end') or prior['window_end']]
for source in selected:
    cmd.extend(['--source', source])
print(json.dumps({'recovery_sources': selected, 'published_run_unchanged': True}), flush=True)
code = 0 if a.evaluate_only else subprocess.call(cmd)
if code == 0:
    result = json.loads(a.output.read_text())
    recovery_checks = {s['registry_id']: s for s in result['source_checks']}
    checks = [recovery_checks.get(s['registry_id'], s) for s in prior['source_checks']]
    success = sum(s['status'] == 'success' for s in checks)
    report = {'scope': 'source_recovery_only', 'published_run_unchanged': True,
              'original_successful_sources': prior['summary']['successful_sources'],
              'recovered_snapshot_successful_sources': success, 'configured_sources': len(checks),
              'source_success_rate': success / len(checks),
              'threshold': 0.9, 'source_health_passed': success / len(checks) >= 0.9,
              'remaining': [s for s in checks if s['status'] != 'success'],
              'new_content_reviewed': False}
    a.output.with_name('source-recovery-evaluation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
raise SystemExit(code)
