"""Durable source-first cover queue shared by core events and industry signals."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from spectra_agent.image_review import write_json, embed_issue
from spectra_agent.publication_quality import expected_cover_motif, requires_cover


def prepare(run_dir: Path) -> dict:
    issue = json.loads((run_dir / 'editorial-issue.json').read_text())
    path = run_dir / 'image-generation.json'
    prior = json.loads(path.read_text()) if path.exists() else {}
    old = {job['story_id']: job for job in prior.get('jobs', [])}
    review_path = run_dir / 'image-review.json'
    review = json.loads(review_path.read_text()) if review_path.exists() else {}
    approved = {item['story_id']: item for item in review.get('approved_covers', [])}
    jobs = []
    for story in issue.get('editorial_stories', []):
        if not requires_cover(story):
            continue
        prompt = ('Editorial conceptual cover, landscape 16:9, centered safe area, no text or logos. '
                  'Create a concrete visual metaphor specific to these facts, not a generic robot or chart. '
                  'Do not present invented imagery as documentary evidence. Headline: '
                  + story['headline'] + '\nSummary: ' + str(story.get('dek', '')))
        key = hashlib.sha256(prompt.encode()).hexdigest()
        job = old.get(story['story_id'], {})
        if job.get('prompt_sha256') != key:
            job = {'story_id': story['story_id'], 'prompt': prompt, 'prompt_sha256': key,
                   'status': 'pending', 'provider': 'codex_imagegen'}
        job['source_links'] = story.get('source_links', [])
        job['selection_order'] = ['news_original', 'official', 'ai_fallback']
        target = run_dir / job.get('url', 'missing')
        if job.get('status') == 'generated' and (not target.is_file() or
                hashlib.sha256(target.read_bytes()).hexdigest() != job.get('sha256')):
            job['status'] = 'pending'
        if job.get('status') != 'generated':
            job['provider'] = 'codex_imagegen' if job.get('source_search', {}).get('fallback_allowed') else 'source_search'
        if job.get('status') == 'generated':
            cover = story.get('cover_image') or {}
            restored = make_cover(story, job)
            if cover.get('review_status') == 'approved' and cover.get('asset_fingerprint') == job['sha256']:
                restored.update({key: cover[key] for key in ('review_status', 'asset_fingerprint', 'reviewed_at', 'reviewed_by') if key in cover})
            else:
                decision = approved.get(story['story_id'], {})
                if (decision.get('url') == job['url']
                        and decision.get('asset_fingerprint') == job['sha256']
                        and decision.get('semantic_motif') == restored.get('semantic_motif')
                        and decision.get('semantic_match') == 'passed'):
                    restored.update(review_status='approved', asset_fingerprint=job['sha256'],
                                    reviewed_at=review.get('reviewed_at'), reviewed_by=review.get('reviewed_by'))
            story['cover_image'] = restored
        jobs.append(job)
    result = {'run_id': run_dir.name, 'status': 'pending' if any(j['status'] != 'generated' for j in jobs) else 'completed', 'jobs': jobs}
    write_json(path, result)
    write_json(run_dir / 'editorial-issue.json', issue)
    if (run_dir / 'rolling-digest.html').exists():
        embed_issue(run_dir / 'rolling-digest.html', issue)
    return result


def make_cover(story, job):
    if job.get('origin') in {'news_original', 'official'}:
        return {'url': job['url'], 'kind': 'source', 'label': '新闻原图' if job['origin'] == 'news_original' else '官方素材',
                'credit': job['credit'], 'source_url': job['source_url'], 'usage_basis': job['usage_basis'],
                'semantic_motif': expected_cover_motif(story['headline'], story.get('category', '')), 'semantic_match': 'passed',
                'review_status': 'pending'}
    return {'url': job['url'], 'kind': 'generated', 'label': 'AI 生成概念配图',
            'credit': 'SPECTRA / Imagegen', 'generation_method': 'codex_imagegen',
            'generated_this_run': True, 'generation_run_id': job['run_id'],
            'generated_at': job['generated_at'], 'generation_sha256': job['sha256'],
            'semantic_motif': expected_cover_motif(story['headline'], story.get('category', '')),
            'semantic_match': 'passed', 'review_status': 'pending'}


def record_search(run_dir: Path, story_id: str, evidence: dict):
    """Require an auditable search of both source tiers before spending generation resources."""
    for tier in ('news_original', 'official'):
        check = evidence.get(tier, {})
        urls = check.get('checked_urls')
        if (check.get('result') != 'unavailable' or not str(check.get('reason', '')).strip()
                or not isinstance(urls, list) or not urls
                or any(not isinstance(url, str) or not url.startswith('https://') for url in urls)):
            raise ValueError('Both source tiers need checked_urls, unavailable result and reason')
    queue = json.loads((run_dir / 'image-generation.json').read_text())
    job = next(j for j in queue['jobs'] if j['story_id'] == story_id)
    job['source_search'] = {**evidence, 'fallback_allowed': True, 'checked_at': datetime.now(timezone.utc).isoformat()}
    job['provider'] = 'codex_imagegen'
    write_json(run_dir / 'image-generation.json', queue)


def install(run_dir: Path, story_id: str, source: Path, origin='generated', source_url='', credit='', usage_basis=''):
    from PIL import Image
    with Image.open(source) as image:
        image.verify()
    queue = json.loads((run_dir / 'image-generation.json').read_text())
    job = next(j for j in queue['jobs'] if j['story_id'] == story_id)
    if origin == 'generated' and not job.get('source_search', {}).get('fallback_allowed'):
        raise ValueError('Search news originals and official assets before AI fallback')
    if origin != 'generated' and not (source_url.startswith('https://') and credit.strip() and usage_basis.strip()):
        raise ValueError('Source assets require HTTPS source URL, credit and usage basis')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    relative = f'assets/editorial/{story_id}-{origin}-{digest[:12]}{source.suffix.lower()}'
    target = run_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    job.update(status='generated', url=relative, sha256=digest, run_id=run_dir.name,
               origin=origin, source_url=source_url, credit=credit, usage_basis=usage_basis,
               generated_at=datetime.now(timezone.utc).isoformat())
    issue = json.loads((run_dir / 'editorial-issue.json').read_text())
    story = next(s for s in issue['editorial_stories'] if s['story_id'] == story_id)
    story['cover_image'] = make_cover(story, job)
    queue['status'] = 'completed' if all(j['status'] == 'generated' for j in queue['jobs']) else 'pending'
    write_json(run_dir / 'image-generation.json', queue)
    write_json(run_dir / 'editorial-issue.json', issue)
    embed_issue(run_dir / 'rolling-digest.html', issue)


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--run-dir', type=Path, required=True)
    p.add_argument('--story-id')
    p.add_argument('--image', type=Path)
    p.add_argument('--origin', choices=['generated', 'news_original', 'official'], default='generated')
    p.add_argument('--source-url', default='')
    p.add_argument('--credit', default='')
    p.add_argument('--usage-basis', default='')
    p.add_argument('--source-search', type=Path, help='JSON evidence of unsuccessful news and official image searches')
    a = p.parse_args()
    if a.source_search:
        record_search(a.run_dir, a.story_id, json.loads(a.source_search.read_text()))
    elif a.image:
        install(a.run_dir, a.story_id, a.image, a.origin, a.source_url, a.credit, a.usage_basis)
    else:
        print(json.dumps(prepare(a.run_dir), ensure_ascii=False))
