#!/usr/bin/env python3
"""Back up and synchronize the approved UI to the existing scheduled runtime."""
import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = Path.home() / 'Library/Application Support/SPECTRA'
RUNTIME = BASE / 'runtime'
FILES = ['visual-intelligence-prototype.html', 'app/accepted-ui.css',
         'app/accepted-ux.js', 'app/share.js', 'app/visual-library.js', 'spectra_agent/run.py',
         'spectra_agent/dingtalk_push.py', 'scripts/refresh-ui.py']

if __name__ == '__main__':
    if not (RUNTIME / 'spectra_agent/config.v0.1.json').is_file():
        raise SystemExit('Existing runtime configuration required')
    backup = BASE / 'data/deployment-backups' / datetime.now().strftime('ui-%Y%m%d-%H%M%S')
    checks = {}
    for name in FILES:
        source, target = ROOT / name, RUNTIME / name
        if target.exists():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        checksum = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        if checksum(source) != checksum(target):
            raise RuntimeError('Checksum mismatch: ' + name)
        checks[name] = checksum(target)
    backup.mkdir(parents=True, exist_ok=True)
    result = {'runtime':str(RUNTIME), 'backup':str(backup), 'verified_files':checks}
    (backup / 'manifest.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
