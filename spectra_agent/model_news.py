"""Project daily editorial candidates into related news; never edit catalog facts."""
import json
import re
from pathlib import Path
from spectra_agent.execution import atomic_json


def project(run_dir):
    root = Path(run_dir)
    target = root / 'assets/model-universe'
    catalog_path = target / 'catalog.json'
    issue_path = root / 'editorial-issue.json'
    if not catalog_path.exists() or not issue_path.exists():
        return
    catalog = json.loads(catalog_path.read_text())
    issue = json.loads(issue_path.read_text())
    news = []
    for model in catalog['models']:
        model['newsIds'] = []
    for brief in issue.get('news_briefs', []):
        if brief.get('localization_status') not in {'machine_localized_validated', 'human_reviewed', 'approved'}:
            continue
        links = brief.get('source_links') or []
        if not links or not links[0].get('url', '').startswith(('https://', 'http://')):
            continue
        text = ' '.join(str(brief.get(k) or '') for k in ('headline', 'dek', 'original_headline', 'original_dek'))
        matched = [m for m in catalog['models'] if len(m.get('name', '')) >= 3 and re.search(r'(?<![a-zA-Z0-9])' + re.escape(m['name']) + r'(?![a-zA-Z0-9])', text, re.I)]
        if not matched:
            continue
        nid = brief['brief_id']
        news.append({'id': nid, 'date': (brief.get('published_at') or '')[:10],
                     'title': brief['headline'], 'summary': brief.get('dek', ''),
                     'source': links[0]['url'], 'label': '日报相关资讯',
                     'provenance': brief.get('accuracy_note') or '日报候选池转写；不作为模型目录事实依据。',
                     'candidate_id': brief.get('candidate_id'), 'run_id': issue.get('run_id')})
        for model in matched:
            model['newsIds'].append(nid)
    catalog['news'] = news
    atomic_json(catalog_path, catalog)
    html_path = target / 'index.html'
    html = html_path.read_text()
    payload = json.dumps(catalog, ensure_ascii=False).replace('<', '\\u003c')
    html, count = re.subn(r'window.PREVIEW_DATA = [\s\S]*?;\s*</script>', lambda _: 'window.PREVIEW_DATA = ' + payload + ';\n</script>', html, count=1)
    if count != 1:
        raise ValueError('Model catalog embed missing')
    html_path.write_text(html)
    atomic_json(root / 'model-news.json', {'run_id': issue.get('run_id'), 'related_news': len(news), 'source': 'daily_editorial_candidates', 'catalog_facts_modified': False})
