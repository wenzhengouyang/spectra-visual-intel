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
FILES += ['index.html', 'app/account.js', 'app/account.css', 'spectra_agent/account_server.py']
FILES += ['editorial/build-editorial-issue.py', 'processor/p2_localizer.py']
FILES += ['spectra_agent/publication_quality.py']
FILES += ['scripts/validate-editorial-issue.py']
FILES += ['spectra_agent/image_generation.py']
FILES += ['collector/collect.py', 'processor/structure.py', 'spectra_agent/brief_quality.py',
          'spectra_agent/progress-dashboard.html']
FILES += ['spectra_agent/safe_http.py', 'spectra_agent/sandbox.py', 'collector/offline_extract.py',
          'spectra_agent/evidence_search.py', 'spectra_agent/review_samples.py', 'spectra_agent/review_cli.py',
          'verification/apply-fact-review-decisions.py']
FILES += ['spectra_agent/image_review.py']
FILES += ['assets/dingtalk/spectra-header-v2.svg', 'assets/dingtalk/spectra-header-v2.png']
FILES += ['spectra_agent/progress_server.py', 'spectra_agent/publish_run.py',
          'editorial/diagnostic_long_writer.py', 'editorial/core_event_pipeline.py',
          'spectra_agent/config.v0.1.json']
FILES += ['spectra_agent/config.v0.1.json', 'assets/dingtalk/spectra-compact-header.png',
          'assets/dingtalk/spectra-compact-header.svg']

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--files', nargs='+', choices=FILES)
    args = parser.parse_args()
    if not (RUNTIME / 'spectra_agent/config.v0.1.json').is_file():
        raise SystemExit('Existing runtime configuration required')
    backup = BASE / 'data/deployment-backups' / datetime.now().strftime('ui-%Y%m%d-%H%M%S')
    checks = {}
    for name in args.files or FILES:
        source, target = ROOT / name, RUNTIME / name
        if target.exists():
            saved = backup / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        if name == 'spectra_agent/config.v0.1.json':
            config = json.loads(target.read_text())
            version = json.loads(source.read_text())['core_event_writer']['prompt_version']
            config.setdefault('core_event_writer', {})['prompt_version'] = version
            config.setdefault('publication_quality', {})['allow_brief_only'] = json.loads(source.read_text()).get('publication_quality', {}).get('allow_brief_only', True)
            config['joint_delivery'] = json.loads(source.read_text()).get('joint_delivery', {})
            config['image_generation'] = json.loads(source.read_text()).get('image_generation', {})
            config.setdefault('dingtalk_push', {})['header_image_url'] = json.loads(source.read_text())['dingtalk_push']['header_image_url']
            target.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
            checks[name] = {'writer_prompt_version': version}
            continue
        shutil.copy2(source, target)
        checksum = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        if checksum(source) != checksum(target):
            raise RuntimeError('Checksum mismatch: ' + name)
        checks[name] = checksum(target)
    backup.mkdir(parents=True, exist_ok=True)
    result = {'runtime':str(RUNTIME), 'backup':str(backup), 'verified_files':checks}
    (backup / 'manifest.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
