import json
from pathlib import Path
import tempfile
import unittest
from spectra_agent.model_news import project
from spectra_agent.model_health import status


class ModelNewsTest(unittest.TestCase):
    def test_daily_projection_preserves_facts_and_excludes_unreviewed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / 'assets/model-universe'
            target.mkdir(parents=True)
            model = {'id': 'gemini', 'name': 'Gemini', 'currentVersion': 'old', 'releaseDate': '2026-01-01'}
            (target / 'catalog.json').write_text(json.dumps({'models': [model], 'news': []}))
            (target / 'index.html').write_text('<script>window.PREVIEW_DATA = {};\n</script>')
            brief = {'brief_id': 'b1', 'headline': 'Gemini 有新动态', 'dek': '版本待核验', 'source_links': [{'url': 'https://example.com'}], 'localization_status': 'machine_localized_validated'}
            (root / 'editorial-issue.json').write_text(json.dumps({'news_briefs': [brief, {**brief, 'brief_id': 'bad', 'localization_status': 'blocked'}]}))
            project(root)
            result = json.loads((target / 'catalog.json').read_text())
            self.assertEqual(result['models'][0]['currentVersion'], 'old')
            self.assertEqual(result['models'][0]['releaseDate'], '2026-01-01')
            self.assertEqual(result['models'][0]['newsIds'], ['b1'])
            self.assertEqual(len(result['news']), 1)
            project(root)
            self.assertEqual(json.loads((target / 'catalog.json').read_text()), result)

    def test_missing_state_is_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(status(Path(tmp))['last_checked_at'])
