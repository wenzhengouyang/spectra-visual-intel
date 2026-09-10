"""Install bounded local morning triggers; never starts collection or sends a message."""
import argparse
import plistlib
import shutil
import subprocess
import os
from datetime import datetime
from pathlib import Path

PLAN = {
    'com.spectra.visual-intel.daily': [{'Hour': 8, 'Minute': 0}, {'Hour': 10, 'Minute': 10}],
    'com.spectra.visual-intel.dingtalk': [{'Hour': 10, 'Minute': m} for m in (20, 25, 28, 30)],
}

def revised(config, intervals):
    result = dict(config)
    result.pop('StartInterval', None)
    result.pop('RunAtLoad', None)
    result['StartCalendarInterval'] = intervals
    return result

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    directory = Path.home() / 'Library/LaunchAgents'
    backup = Path.home() / 'Library/Application Support/SPECTRA/data/deployment-backups' / datetime.now().strftime('schedule-%Y%m%d-%H%M%S')
    for label, intervals in PLAN.items():
        file = directory / (label + '.plist')
        original = plistlib.loads(file.read_bytes())
        updated = revised(original, intervals)
        print(label, intervals)
        if not args.apply:
            continue
        backup.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, backup / file.name)
        file.write_bytes(plistlib.dumps(updated))
        domain = f'gui/{os.getuid()}'
        subprocess.run(['launchctl', 'bootout', domain + '/' + label], check=False, capture_output=True)
        subprocess.run(['launchctl', 'bootstrap', domain, str(file)], check=True)
        subprocess.run(['launchctl', 'print', domain + '/' + label], check=True, stdout=subprocess.DEVNULL)
    if args.apply:
        print('Installed and verified. Backup:', backup)
