"""Sync only the model universe, with backups, into the existing local runtime."""
from datetime import datetime
from pathlib import Path
import shutil
ROOT = Path(__file__).resolve().parents[1]
BASE = Path.home()/'Library/Application Support/SPECTRA'
RUNTIME = BASE/'runtime'
if not (RUNTIME/'index.html').exists(): raise SystemExit('Existing runtime not found')
backup = BASE/'backups'/('model-universe-'+datetime.now().strftime('%Y%m%dT%H%M%S'))
files = [str(p.relative_to(ROOT)) for p in (ROOT/'assets/model-universe').iterdir() if p.is_file()]
files += ['scripts/model-universe.py','scripts/build-model-universe.cjs','spectra_agent/safe_http.py']
files += ['previews/overview-models-20260911/'+name for name in ['data.js','overview-preview-v2.html','overview-preview-v3.html','orbit-v3.js','orbit-v3.css','orbit-depth.css','sync-orbit.cjs']]
for name in files:
    target = RUNTIME/name
    if target.exists():
        saved = backup/name; saved.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(target,saved)
    target.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(ROOT/name,target)
for name in ['index.html','visual-intelligence-prototype.html']:
    target = RUNTIME/name
    if not target.exists(): continue
    text = target.read_text()
    if 'assets/model-universe/mount.js' not in text:
        saved = backup/name; saved.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(target,saved)
        target.write_text(text.replace('</head>','<script src="assets/model-universe/mount.js" defer></script>\n</head>'))
print('Model universe synchronized; backup:',backup)
