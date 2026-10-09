import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spectra_agent.collection_health import ingest_coverage, mark_coverage_consumed, read
from spectra_agent.execution import atomic_json, RunLease
from spectra_agent.recovery import inspect, occupied, source_retry_ids, reserve


class CollectionRecoveryTests(unittest.TestCase):
    def test_midday_records_extend_morning_window(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            baseline = self.bundle(['a'])
            baseline['window_end'] = '2026-09-16T01:00:00Z'
            delta = self.bundle(['b'])
            delta['window_end'] = '2026-09-16T03:00:00Z'
            delta['source_records'][0]['published_at'] = '2026-09-16T02:30:00Z'
            atomic_json(root / 'collection.json', baseline)
            atomic_json(root / 'coverage-collection.json', delta)
            atomic_json(root / 'coverage-line.json', {'status': 'completed'})
            self.assertTrue(ingest_coverage(root))
            merged = read(root / 'collection.json')
            self.assertEqual(len(merged['source_records']), 2)
            self.assertEqual(merged['window_end'], delta['window_end'])

    def test_source_recovery_excludes_permanent_errors_and_stops_after_two_rounds(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            atomic_json(root / 'collection.json', {'source_checks': [
                {'registry_id': 'a', 'status': 'failed', 'error': 'HTTP 404'},
                {'registry_id': 'b', 'status': 'failed', 'error': 'HTTP 429'},
                {'registry_id': 'c', 'status': 'failed', 'error': 'DNS failure'},
            ]})
            self.assertEqual(source_retry_ids(root), ['b', 'c'])
            self.assertEqual(inspect(root, 'noop_completed')['action'], 'retry_sources')
            atomic_json(root / 'source-recovery.json', {'attempts': 2})
            self.assertEqual(inspect(root, 'noop_completed')['action'], 'alert')

    def test_completed_coverage_rebuilds_an_empty_human_review_packet(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            atomic_json(root / 'run.json', {'status': 'waiting_for_review', 'current_stage': 'human_review'})
            atomic_json(root / 'coverage-line.json', {'status': 'completed'})
            atomic_json(root / 'coverage-collection.json', self.bundle(['coverage']))
            self.assertEqual(inspect(root, 'noop_human_gate')['action'], 'resume_retry')

    def test_recovery_cooldown_and_budget(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            decision = inspect(root, 'resume_retry')
            reserve(root, decision)
            self.assertEqual(inspect(root, 'resume_retry')['action'], 'cooldown')
            state = read(root / 'recovery-state.json')
            state['attempts'] = 2
            atomic_json(root / 'recovery-state.json', state)
            self.assertEqual(inspect(root, 'resume_retry')['action'], 'alert')

    def bundle(self, urls):
        return {'run_id': 'daily-test', 'window_start': '2026-09-10T00:00:00Z',
                'window_end': '2026-09-17T00:00:00Z', 'source_checks': [],
                'source_records': [{'canonical_url': u, 'source_id': u, 'content_hash': u,
                    'published_at': '2026-09-16T00:00:00Z', 'access_status': 'success'} for u in urls]}

    def test_coverage_is_idempotent_and_crash_recoverable(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            atomic_json(root / 'collection.json', self.bundle(['a']))
            atomic_json(root / 'coverage-collection.json', self.bundle(['a', 'b']))
            atomic_json(root / 'coverage-line.json', {'status': 'completed'})
            self.assertTrue(ingest_coverage(root))
            self.assertTrue(ingest_coverage(root))  # interrupted before structure
            self.assertEqual(len(read(root / 'collection.json')['source_records']), 2)
            mark_coverage_consumed(root)
            self.assertFalse(ingest_coverage(root))

    def test_intent_before_merge_crash_replays_merge(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            atomic_json(root / 'collection.json', self.bundle(['a']))
            atomic_json(root / 'coverage-collection.json', self.bundle(['b']))
            atomic_json(root / 'coverage-line.json', {'status': 'completed'})
            original = atomic_json
            def fail(path, value):
                if Path(path).name == 'collection.json':
                    raise OSError('simulated crash')
                original(path, value)
            with patch('spectra_agent.collection_health.atomic_json', side_effect=fail):
                with self.assertRaises(OSError):
                    ingest_coverage(root)
            self.assertTrue(ingest_coverage(root))
            self.assertEqual(len(read(root / 'collection.json')['source_records']), 2)

    def test_no_executor_is_actionable_and_does_not_retry_forever(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            atomic_json(root / 'run.json', {'current_stage': 'image_generation'})
            self.assertEqual(inspect(root, 'noop_human_gate')['action'], 'alert')

    def test_live_lease_is_not_removed_or_restarted(self):
        with tempfile.TemporaryDirectory() as d:
            lease = RunLease(d)
            self.assertTrue(lease.acquire())
            try:
                self.assertTrue(occupied(d))
            finally:
                lease.release()
            self.assertFalse(occupied(d))
