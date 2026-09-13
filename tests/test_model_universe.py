import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('model_universe', Path(__file__).resolve().parents[1]/'scripts/model-universe.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ModelUniverseTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = self.root/'catalog.json'
        self.original = {'models':[{'id':'test','source':'https://example.com/model'}]}
        self.catalog.write_text(json.dumps(self.original))
        self.patches = [patch.object(module,'CATALOG',self.catalog),patch.object(module,'STATE',self.root/'runs')]
        for p in self.patches: p.start(); self.addCleanup(p.stop)

    def test_baseline_change_and_failure_preserve_profile(self):
        for content, expected in [('a '*100,'baseline'),('a '*100,'unchanged'),('b '*100,'changed')]:
            with patch.object(module,'request_bytes',return_value=(content.encode(),'https://example.com/model')):
                self.assertEqual(module.collect(0),0)
            state = json.loads((module.STATE/'latest.json').read_text())
            self.assertEqual(state['results'][0]['status'],expected)
        digest = state['sources']['https://example.com/model']['sha256']
        with patch.object(module,'request_bytes',side_effect=TimeoutError('timeout')):
            self.assertEqual(module.collect(0),1)
        state = json.loads((module.STATE/'latest.json').read_text())
        self.assertEqual(state['sources']['https://example.com/model']['sha256'],digest)
        self.assertEqual(json.loads(self.catalog.read_text()),self.original)

    def test_review_requires_evidence_and_rebuilds(self):
        with patch.object(module,'request_bytes',return_value=(b'Release v2 '*100,'https://example.com/model')): module.collect(0)
        state = json.loads((module.STATE/'latest.json').read_text())
        change = {'id':'test','reviewer':'tester','evidence_quote':'Release v2','snapshot_sha256':state['results'][0]['sha256'],'fields':{'currentVersion':'v2'}}
        review = self.root/'review.json'; review.write_text(json.dumps({'changes':[change]}))
        with patch.object(module.subprocess,'run') as build: module.apply_review(review); build.assert_called_once()
        self.assertEqual(json.loads(self.catalog.read_text())['models'][0]['currentVersion'],'v2')
        change['evidence_quote']='not in source'; review.write_text(json.dumps({'changes':[change]}))
        with self.assertRaises(ValueError): module.apply_review(review)

if __name__ == '__main__': unittest.main()
