"""Back up and refresh an existing report UI without changing approved content."""
import argparse
import hashlib
import importlib.util
import json
import shutil
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    args = parser.parse_args()
    run = args.run_dir.resolve()
    issue = json.loads((run / 'editorial-issue.json').read_text())
    protected = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in run.glob('*.json')}
    backup = run.parent.parent / 'deployment-backups' / datetime.now().strftime('preview-%Y%m%d-%H%M%S')
    backup.mkdir(parents=True)
    for name in ['rolling-digest.html', 'tokens.css', 'app']:
        source = run / name
        if source.is_dir():
            shutil.copytree(source, backup / name)
        elif source.exists():
            shutil.copy2(source, backup / name)
    spec = importlib.util.spec_from_file_location('refresh_ui', ROOT / 'scripts/refresh-ui.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.render(issue, run / 'rolling-digest.html', run)
    assert all(hashlib.sha256(p.read_bytes()).hexdigest() == checksum for p, checksum in protected.items())
    print(json.dumps({'status': 'refreshed', 'backup': str(backup), 'unchanged_json_artifacts': len(protected)}))
