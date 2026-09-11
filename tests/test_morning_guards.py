import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone, timedelta
from spectra_agent.morning_guards import network_preflight, coverage_decision, image_window, reserve_image_attempt

class MorningGuardsTest(unittest.TestCase):
    def test_network_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fail(url):
                raise OSError('DNS unavailable')
            with self.assertRaises(RuntimeError):
                network_preflight(Path(tmp), fail)
            self.assertEqual(network_preflight(Path(tmp), lambda url: True)['status'], 'passed')

    def test_low_coverage_never_authorizes_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = coverage_decision(Path(tmp), {'source_checks': [{'status':'failed'}]})
            self.assertEqual(result['recommended_mode'], 'limited_requires_approval')
            self.assertFalse(result['publication_authorized'])

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
