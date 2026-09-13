#!/usr/bin/env python3
"""Independent official-source snapshots and evidence-reviewed catalog updates."""
import argparse
from datetime import datetime, timezone, date
import hashlib
from html.parser import HTMLParser
import json
import re
from pathlib import Path
import subprocess
import sys
from urllib.parse import urldefrag

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from spectra_agent.safe_http import request_bytes
CATALOG = ROOT / 'assets/model-universe/catalog.json'
STATE = ROOT / 'collector/model-universe-runs'

class Text(HTMLParser):
    def __init__(self):
        super().__init__(); self.hidden = 0; self.parts = []
    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'): self.hidden += 1
    def handle_endtag(self, tag):
        if tag in ('script', 'style'): self.hidden = max(0, self.hidden - 1)
    def handle_data(self, text):
        if not self.hidden: self.parts.extend(text.split())

def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    temp.replace(path)

def collect(limit):
    catalog = json.loads(CATALOG.read_text())
    urls = {}
    for model in catalog['models']:
        for url in [model['source'], model.get('extraSource')]:
            if url: urls.setdefault(urldefrag(url)[0], []).append(model['id'])
    previous = json.loads((STATE/'latest.json').read_text()) if (STATE/'latest.json').exists() else {'sources':{}}
    sources = previous['sources'].copy()
    checked = datetime.now(timezone.utc).isoformat()
    # safe_http owns a thread-bound request budget: fetch sequentially.
    outcomes = []
    for url in list(urls)[:limit or None]:
        record = {'url':url, 'model_ids':urls[url], 'checked_at':checked}
        try:
            body, final = request_bytes(url, timeout=12, max_bytes=2_000_000)
            parser = Text(); parser.feed(body.decode('utf-8', errors='replace'))
            text = ' '.join(parser.parts)
            if len(text) < 100: raise ValueError('Insufficient source text; browser extraction needed')
            digest = hashlib.sha256(text.encode()).hexdigest()
            old = sources.get(url, {})
            record.update(status='changed' if old.get('sha256') and old['sha256'] != digest else 'unchanged' if old.get('sha256') else 'baseline', sha256=digest, final_url=final)
            write(STATE/'snapshots'/f'{digest}.json', {'url':url,'final_url':final,'text':text,'checked_at':checked})
            sources[url] = record
        except Exception as exc:
            record.update(status='error', error=str(exc))
        outcomes.append(record)
    result = {'checked_at':checked,'sources':sources,'results':outcomes,'rule':'Page changes require evidence review; never imply a model release.'}
    write(STATE/'latest.json',result)
    write(STATE/'history'/(checked.replace(':','-')+'.json'), result)
    print(json.dumps({'sources':len(outcomes),'changed':sum(r['status']=='changed' for r in outcomes),'errors':sum(r['status']=='error' for r in outcomes)},ensure_ascii=False))
    return 1 if outcomes and all(r['status']=='error' for r in outcomes) else 0

def apply_review(review_path):
    review = json.loads(Path(review_path).read_text())
    catalog = json.loads(CATALOG.read_text())
    models = {m['id']:m for m in catalog['models']}
    for change in review['changes']:
        model = models[change['id']]
        if not change.get('reviewer') or not change.get('evidence_quote'): raise ValueError('Reviewer and evidence quote required')
        if not re.fullmatch(r'[0-9a-f]{64}', change['snapshot_sha256']): raise ValueError('Invalid snapshot hash')
        evidence = json.loads((STATE/'snapshots'/(change['snapshot_sha256']+'.json')).read_text())
        if change['evidence_quote'] not in evidence['text']: raise ValueError('Evidence quote not in collected source')
        allowed = {urldefrag(u)[0] for u in [model['source'],model.get('extraSource')] if u}
        if evidence['url'] not in allowed: raise ValueError('Source does not belong to model')
        fields = change['fields']
        if set(fields) - {'currentVersion','currentVersionStatus','releaseDate','releaseDateStatus','access','versions','update','change'}: raise ValueError('Unsupported fields')
        if 'access' in fields and (not isinstance(fields['access'],list) or not fields['access']): raise ValueError('Access must be a nonempty list')
        for key in ('releaseDate','update'):
            if fields.get(key) and date.fromisoformat(fields[key]) > date.today(): raise ValueError('Future dates cannot be published')
        if 'update' in fields and fields['update'] != fields.get('releaseDate',model.get('releaseDate')): raise ValueError('Update marker requires matching release date')
        model.update(fields)
        model['verifiedAt'] = date.today().isoformat()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    write(STATE/'reviews'/f'{stamp}.json',review)
    write(STATE/'backups'/f'{stamp}.json',json.loads(CATALOG.read_text()))
    write(CATALOG,catalog)
    subprocess.run(['node',str(ROOT/'scripts/build-model-universe.cjs')],check=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command',required=True)
    collect_parser = sub.add_parser('collect'); collect_parser.add_argument('--limit',type=int,default=0)
    review_parser = sub.add_parser('apply'); review_parser.add_argument('review')
    args = parser.parse_args()
    if args.command == 'collect': sys.exit(collect(args.limit))
    apply_review(args.review)
