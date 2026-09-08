"""Process-wide run leases, durable JSON, and content-addressed stage receipts."""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import tempfile
import time
from pathlib import Path


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


class RunLease:
    """Kernel lock is released even after SIGKILL; metadata is not the lock."""
    def __init__(self, run_dir):
        self.run_dir = Path(run_dir)
        self.handle = None

    def acquire(self, wait=False, record=True):
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.handle = (self.run_dir / 'execution.lock').open('a+')
        try:
            fcntl.flock(self.handle, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
        except BlockingIOError:
            self.handle.close()
            self.handle = None
            return False
        if record:
            self.heartbeat('acquired')
        return True

    def heartbeat(self, stage, **details):
        atomic_json(self.run_dir / 'execution-progress.json', {
            'pid': os.getpid(), 'stage': stage, 'heartbeat_at': time.time(), **details,
        })

    def release(self):
        if self.handle:
            fcntl.flock(self.handle, fcntl.LOCK_UN)
            self.handle.close()
            self.handle = None


def publication_version(run_dir):
    """Bind evaluation to source, human decisions, facts and reader content."""
    payload = {}
    for name in ('collection.json', 'p1-review.json', 'verified-events.json',
                 'core-event-drafts.json', 'editorial-issue.json'):
        path = Path(run_dir) / name
        if not path.exists():
            payload[name] = None
            continue
        value = json.loads(path.read_text())
        if name == 'editorial-issue.json':
            value.pop('workflow', None)
        payload[name] = value
    return digest(payload)
