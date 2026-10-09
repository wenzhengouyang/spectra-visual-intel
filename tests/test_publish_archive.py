import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import subprocess

from spectra_agent.publish_run import NoFreshContent, copy_package, main, validate_evaluation_release, validate_freshness
from spectra_agent.run import STATIC_ASSETS


class ArchiveTest(unittest.TestCase):
    def test_freshness_accepts_one_new_story_and_rejects_rewrapped_old_issue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = root / "daily-20260916"
            current = root / "daily-20260917"
            old.mkdir(); current.mkdir()
            (old / "run.json").write_text(json.dumps({"publish_status": "published"}))
            old_issue = {"news_briefs": [{"headline": "旧闻", "source_links": [{"url": "https://example.com/old"}]}]}
            (old / "editorial-issue.json").write_text(json.dumps(old_issue))
            with self.assertRaises(NoFreshContent):
                validate_freshness(current, old_issue)
            new_issue = {"news_briefs": [*old_issue["news_briefs"],
                                          {"headline": "新增", "source_links": [{"url": "https://example.com/new"}]}]}
            self.assertEqual(validate_freshness(current, new_issue), 1)

    def test_failed_evaluation_only_allows_disclosed_coverage_exception(self):
        evaluation = {"status": "fail", "blocking_recommended": True,
                      "failed_checks": ["source_success_rate"]}
        with self.assertRaises(Exception):
            validate_evaluation_release(evaluation, {"issue": {}})
        issue = {"issue": {"release_mode": "limited_source_coverage",
                           "release_authorization": "bounded_automatic_policy",
                           "coverage_disclosure": "部分来源暂不可用，内容已完成事实校验。"}}
        validate_evaluation_release(evaluation, issue)
        with self.assertRaises(Exception):
            validate_evaluation_release({**evaluation, "failed_checks": ["source_success_rate", "reader_content_quality"]}, issue)

    def test_empty_staging_still_requires_successful_push(self):
        for fails in [False, True]:
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                run = root / 'r1'
                run.mkdir()
                (run / 'run.json').write_text(json.dumps({'publish_status': 'not_published'}))
                calls = []
                def command(arguments, *args, **kwargs):
                    calls.append(arguments)
                    if arguments[:2] == ['git', 'push'] and fails:
                        raise subprocess.CalledProcessError(1, arguments)
                    return ''
                with patch('sys.argv', ['publish', '--run-id', 'r1', '--push', '--confirm']), \
                     patch('spectra_agent.publish_run.resolve_config', return_value=(root / 'config.json', {})), \
                     patch('spectra_agent.publish_run.runs_dir', return_value=root), \
                     patch('spectra_agent.publish_run.validate_run'), \
                     patch('spectra_agent.publish_run.prepare_checkout', return_value=root), \
                     patch('spectra_agent.publish_run.copy_package', return_value=['index.html']), \
                     patch('spectra_agent.publish_run.command', side_effect=command):
                    result = main()
                self.assertTrue(any(call[:2] == ['git', 'push'] for call in calls))
                self.assertEqual(result, 3 if fails else 0)
                state = json.loads((run / 'run.json').read_text())
                self.assertEqual(state['publish_status'], 'not_published' if fails else 'published')

    def test_new_issue_preserves_old_share_and_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run, site = root / 'run', root / 'site'
            run.mkdir()
            site.mkdir()
            for relative in STATIC_ASSETS:
                target = run / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('asset')
            for run_id, body in [('daily-one', 'first'), ('daily-two', 'second')]:
                (run / 'editorial-issue.json').write_text(json.dumps({'run_id': run_id}))
                (run / 'rolling-digest.html').write_text(body)
                copy_package(run, site)
            self.assertEqual((site / 'index.html').read_text(), 'second')
            self.assertEqual((site / 'archive/daily-one/index.html').read_text(), 'first')
            self.assertTrue((site / 'archive/daily-one/app/share.js').is_file())
            (run / 'rolling-digest.html').write_text('updated second')
            copy_package(run, site)
            self.assertEqual((site / 'archive/daily-two/index.html').read_text(), 'second')
