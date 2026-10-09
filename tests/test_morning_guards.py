import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from spectra_agent.morning_guards import automatic_limited_release, network_preflight, coverage_decision, image_window, reserve_image_attempt

class MorningGuardsTest(unittest.TestCase):
    def test_network_preflight_is_diagnostic_and_does_not_block_collectors(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fail(url):
                raise OSError('DNS unavailable')
            self.assertEqual(network_preflight(Path(tmp), fail)['status'], 'degraded')
            self.assertEqual(network_preflight(Path(tmp), lambda url: True)['status'], 'passed')

    def test_low_coverage_never_authorizes_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = coverage_decision(Path(tmp), {'source_checks': [{'status':'failed'}]})
            self.assertEqual(result['recommended_mode'], 'limited_requires_approval')
            self.assertFalse(result['publication_authorized'])

    def test_bounded_automatic_limited_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            collection = {'source_checks': [{'status': 'success'}] * 32 + [{'status': 'failed'}] * 4}
            result = automatic_limited_release(Path(tmp), collection, {
                'enabled': True,
                'minimum_success_rate': .8,
                'minimum_successful_sources': 20,
                'maximum_failed_sources': 6,
            })
            self.assertTrue(result['authorized'])
            self.assertEqual(result['allowed_failed_checks'], ['source_success_rate'])

    def test_automatic_release_rejects_broad_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            collection = {'source_checks': [{'status': 'success'}] * 20 + [{'status': 'failed'}] * 10}
            result = automatic_limited_release(Path(tmp), collection, {
                'enabled': True,
                'minimum_success_rate': .6,
                'minimum_successful_sources': 20,
                'maximum_failed_sources': 6,
            })
            self.assertFalse(result['authorized'])

    def test_window_does_not_reset_on_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = datetime.now(timezone.utc)
            first = image_window(Path(tmp), now)
            self.assertEqual(image_window(Path(tmp), now + timedelta(hours=1)), first)
            self.assertEqual(len(first['due_at']), 3)
            self.assertTrue(reserve_image_attempt(Path(tmp), now))
            self.assertFalse(reserve_image_attempt(Path(tmp), now))
            self.assertTrue(reserve_image_attempt(Path(tmp), now + timedelta(minutes=10)))
            self.assertTrue(reserve_image_attempt(Path(tmp), now + timedelta(minutes=20)))
            self.assertFalse(reserve_image_attempt(Path(tmp), now + timedelta(minutes=21)))
