import copy
import json
import tempfile
import unittest
import argparse
import subprocess
from unittest import mock
from pathlib import Path
from spectra_agent.execution import RunLease, atomic_json, publication_version
from spectra_agent.semantic_review import review, bounded_review
from spectra_agent.run import (materialize_fact_decisions, select_review_candidates, WorkflowError,
    reserve_editorial_worker, command, launch_editorial_worker, worker_lock_path, resume_run, show_status)


class Model:
    def __init__(self, status):
        self.status = status
        self.calls = 0
    def generate_json(self, **kwargs):
        self.calls += 1
        return {'status': self.status, 'issues': [] if self.status == 'pass' else [
            {'location': 'headline', 'evidence': '原文为10，稿件为20', 'requirement': '改回10'}]}, {'model': 'test-double'}


class ExecutionTests(unittest.TestCase):
    def test_lease_prevents_duplicate_and_releases(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = RunLease(d), RunLease(d)
            self.assertTrue(a.acquire())
            self.assertFalse(b.acquire())
            a.release()
            self.assertTrue(b.acquire())
            b.release()

    def test_atomic_json_and_content_change(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'p1-review.json'
            atomic_json(p, {'fact': 10})
            version = publication_version(d)
            atomic_json(p, {'fact': 20})
            self.assertNotEqual(version, publication_version(d))
            self.assertEqual(json.loads(p.read_text()), {'fact': 20})

    def test_stale_reserved_worker_reclaimed(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            atomic_json(p / 'p1-editorial-worker.lock.json', {'pid': None, 'reserved_at': '2000-01-01T00:00:00Z'})
            token, _ = reserve_editorial_worker(p)
            self.assertTrue(token)
            self.assertEqual(reserve_editorial_worker(p), (None, None))

    def test_worker_start_failure_clears_reservation(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            atomic_json(worker_lock_path(p), {'pid': None, 'reserved_at': '2026-09-08T00:00:00Z'})
            with (p / 'worker.log').open('w') as handle, mock.patch('spectra_agent.run.subprocess.Popen', side_effect=OSError('boom')):
                with self.assertRaises(WorkflowError):
                    launch_editorial_worker(p, ['worker'], {}, handle)
            self.assertFalse(worker_lock_path(p).exists())

    def test_timeout_bytes_remain_json_serializable(self):
        with tempfile.TemporaryDirectory() as d, mock.patch(
            'spectra_agent.run.subprocess.run', side_effect=subprocess.TimeoutExpired(['x'], 1, output=b'out', stderr=b'err')
        ):
            with self.assertRaisesRegex(WorkflowError, 'timed out after 1'):
                command(Path(d), 'test_timeout', ['x'], timeout=1)
            entries = [json.loads(line) for line in (Path(d) / 'run.log.jsonl').read_text().splitlines()]
            self.assertEqual(entries[-1]['stderr'], 'err')

    def test_repeat_resume_reports_active_executor(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / 'runs' / 'r1'
            run_dir.mkdir(parents=True)
            atomic_json(run_dir / 'run.json', {'status': 'running', 'current_stage': 'writer'})
            lease = RunLease(run_dir)
            self.assertTrue(lease.acquire())
            try:
                args = argparse.Namespace(run_id='r1')
                self.assertEqual(resume_run(args, {'data_dir': d, 'runs_dir': 'runs'}), 0)
            finally:
                lease.release()

    def test_dead_running_state_is_reconciled(self):
        with tempfile.TemporaryDirectory() as d:
            run_dir = Path(d) / 'runs' / 'r1'
            run_dir.mkdir(parents=True)
            atomic_json(run_dir / 'run.json', {'run_id': 'r1', 'status': 'running', 'current_stage': 'writer',
                        'created_at': '2000-01-01T00:00:00Z', 'updated_at': '2000-01-01T00:00:00Z'})
            show_status(argparse.Namespace(run_id='r1'), {'data_dir': d, 'runs_dir': 'runs'})
            self.assertEqual(json.loads((run_dir / 'run.json').read_text())['status'], 'interrupted')

    def test_changed_decision_replaces_existing_claims(self):
        data = {'records': [{'candidate_id': 'c', 'decision': 'include', 'claims': [{'text': 'old'}],
            'suggested_evidence': [{'human_fact_decision': 'modify', 'human_fact_text': 'new', 'human_fact_kind': 'reported_fact'}]}]}
        self.assertEqual(materialize_fact_decisions(data)['records'][0]['claims'][0]['text'], 'new')

    def test_minimum_before_score_fill(self):
        items = [{'candidate_id': str(i), 'canonical_title': str(i), 'score': 100-i,
                  'intelligence_type': 'tech' if i < 3 else 'market', 'verification_priority': 'priority.p1'} for i in range(4)]
        config = {'review_queue': {'max_count': 2, 'intelligence_type_minimums': {'market': 1}}}
        result = select_review_candidates({'selected_candidates': items}, config)
        self.assertIn('market', [r['intelligence_type'] for r in result])
        config['review_queue']['intelligence_type_minimums']['market'] = 3
        with self.assertRaises(WorkflowError):
            select_review_candidates({'selected_candidates': items}, config)


class SemanticTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'sources': [{'text': 'Revenue 10', 'url': 'https://example.com'}],
                        'facts': ['Revenue 10'], 'final_article': 'Revenue 10'}
    def test_correct_pass_and_version_cache(self):
        model = Model('pass')
        outcome = review(self.payload, model)
        self.assertEqual(outcome['status'], 'pass')
        self.assertTrue(review(self.payload, model, outcome)['reused'])
        changed = {**self.payload, 'final_article': 'Revenue 20'}
        review(changed, model, outcome)
        self.assertEqual(model.calls, 2)
    def test_wrong_rework(self):
        self.assertEqual(review(self.payload, Model('rework'))['status'], 'rework')
    def test_insufficient_manual(self):
        self.assertEqual(review({**self.payload, 'sources': []}, Model('pass'))['status'], 'manual')
    def test_rework_limit_stops(self):
        model = Model('rework')
        outcome = bounded_review(self.payload, model, lambda p, r: copy.deepcopy(p), 1)
        self.assertEqual(outcome['status'], 'stopped')
        self.assertEqual(model.calls, 2)
