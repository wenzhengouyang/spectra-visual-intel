"""Build a local acceptance package without changing reviewed run artifacts."""
import importlib.util
import json
import tempfile
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spectra_agent.publish_run import copy_package

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    issue = json.loads((args.run_dir / 'editorial-issue.json').read_text())
    spec = importlib.util.spec_from_file_location('refresh_ui', ROOT / 'scripts/refresh-ui.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with tempfile.TemporaryDirectory() as temporary:
        package = Path(temporary)
        module.render(issue, package / 'rolling-digest.html', args.run_dir)
        (package / 'editorial-issue.json').write_text(json.dumps(issue, ensure_ascii=False))
        args.output.mkdir(parents=True, exist_ok=True)
        paths = copy_package(package, args.output)
    print(json.dumps({'status': 'prepared', 'output': str(args.output), 'paths': paths}))
