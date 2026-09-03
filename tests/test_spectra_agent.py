import copy
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from spectra_agent.run import (
    WORKER_TOKEN_ENV,
    WorkflowError,
    automatic_resume_args,
    claim_editorial_worker,
    gated_review_template,
    pre_review_artifacts_recoverable,
    prepare_retry_artifacts,
    prepare_static_draft,
    rebuild_structure_from_collection,
    release_editorial_worker,
    reserve_editorial_worker,
    resume_is_allowed,
    review_template,
    select_review_candidates,
    validate_static_package,
    worker_lock_path,
)
from verification.build_final_events import build_bundle, validate_review


ROOT = Path(__file__).resolve().parents[1]


class SpectraAgentGateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.collection = json.loads((ROOT / "collector/runs/first-live-run-v0.2.json").read_text())
        cls.candidates = json.loads((ROOT / "processor/runs/first-structured-run-v0.1.json").read_text())
        cls.approved = json.loads((ROOT / "verification/p1-review.v0.1.json").read_text())
        cls.config = json.loads((ROOT / "spectra_agent/config.v0.1.json").read_text())

    @staticmethod
    def replace_embedded_issue(draft: Path, issue: dict) -> None:
        html = draft.read_text(encoding="utf-8")
        payload = json.dumps(issue, ensure_ascii=False, separators=(",", ":"))
        updated, replacements = re.subn(
            r'(<script\b[^>]*\bid="issue-data"[^>]*>).*?(</script>)',
            lambda match: f"{match.group(1)}{payload}{match.group(2)}",
            html,
            count=1,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if replacements != 1:
            raise AssertionError("test fixture needs exactly one issue-data script")
        draft.write_text(updated, encoding="utf-8")

    def test_template_contains_only_p1_and_is_pending(self):
        template = review_template(self.collection, self.candidates, "test_run", self.config)
        expected = select_review_candidates(self.candidates, self.config)
        self.assertEqual(len(template["records"]), len(expected))
        self.assertTrue(all(item["decision"] == "pending" for item in template["records"]))
        self.assertEqual(template["review_status"], "pending")

    def test_auto_fact_lock_resume_preserves_config(self):
        args = automatic_resume_args(type("Args", (), {"config": "custom.json"})(), "run_auto")
        self.assertEqual(args.config, "custom.json")
        self.assertEqual(args.run_id, "run_auto")
        self.assertFalse(args.editorial_worker)

    def test_retry_rebuilds_review_from_collection_and_candidates_artifacts(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            (run_dir / "collection.json").write_text("{}")
            (run_dir / "candidates.json").write_text("{}")
            review = run_dir / "p1-review.json"
            self.assertTrue(pre_review_artifacts_recoverable(run_dir, review))
            review.write_text(json.dumps({"review_status": "pending"}))
            self.assertTrue(pre_review_artifacts_recoverable(run_dir, review))
            review.write_text(json.dumps({"review_status": "pending", "verification_harness": {"status": "ready"}}))
            self.assertFalse(pre_review_artifacts_recoverable(run_dir, review))

    def test_retry_rebuilds_missing_structure_without_collecting_again(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            (run_dir / "run.json").write_text(json.dumps({
                "status": "failed",
                "current_stage": "structure",
                "history": [],
            }))
            (run_dir / "collection.json").write_text("{}")
            stages = []

            def fake_command(_run_dir, stage, _arguments):
                stages.append(stage)

            with mock.patch("spectra_agent.run.command", side_effect=fake_command):
                rebuild_structure_from_collection(
                    run_dir,
                    {"processor_config": "processor/config.v0.1.json"},
                    False,
                )

            self.assertEqual(stages, [
                "validate_collection",
                "structure",
                "validate_structure",
            ])
            self.assertNotIn("collect", stages)

    def test_retry_without_collection_remains_failed_with_clear_error(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            (run_dir / "run.json").write_text(json.dumps({
                "status": "failed",
                "current_stage": "failed",
                "failed_stage": "collect",
                "history": [],
            }))
            with self.assertRaisesRegex(WorkflowError, "without collection.json"):
                prepare_retry_artifacts(run_dir, {}, False)
            state = json.loads((run_dir / "run.json").read_text())
            self.assertEqual(state["status"], "failed")
            self.assertEqual(state["failed_stage"], "collect")

    def test_editorial_worker_lock_rejects_duplicate_and_recovers_stale_lock(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            (run_dir / "run.json").write_text(json.dumps({
                "editorial_worker_pid": __import__("os").getpid(),
            }))
            token, active_pid = reserve_editorial_worker(run_dir)
            self.assertIsNotNone(token)
            self.assertIsNone(active_pid)
            with mock.patch.dict("os.environ", {WORKER_TOKEN_ENV: token}, clear=False):
                self.assertTrue(claim_editorial_worker(run_dir))
                duplicate_token, duplicate_pid = reserve_editorial_worker(run_dir)
                self.assertIsNone(duplicate_token)
                self.assertEqual(duplicate_pid, __import__("os").getpid())
                release_editorial_worker(run_dir)
                self.assertIsNone(json.loads((run_dir / "run.json").read_text())["editorial_worker_pid"])
            worker_lock_path(run_dir).write_text(json.dumps({"token": "stale", "pid": 99999999}))
            recovered_token, recovered_pid = reserve_editorial_worker(run_dir)
            self.assertIsNotNone(recovered_token)
            self.assertIsNone(recovered_pid)

    def test_resume_state_policy(self):
        self.assertTrue(resume_is_allowed("waiting_for_review", False))
        self.assertTrue(resume_is_allowed("waiting_for_editorial_review", False))
        self.assertFalse(resume_is_allowed("waiting_for_editorial", False))
        self.assertTrue(resume_is_allowed("failed", True))

    def test_static_draft_copies_all_relative_dependencies(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            draft = prepare_static_draft(run_dir, ROOT / "visual-intelligence-prototype.html")
            html = draft.read_text(encoding="utf-8")
            self.assertIn('href="tokens.css?v=4"', html)
            self.assertIn('href="app/globals.css?v=19"', html)
            self.assertIn('href="app/hallmark-editorial.css?v=13"', html)
            self.assertIn('id="verifiedBriefIndex"', html)
            self.assertTrue((run_dir / "tokens.css").exists())
            self.assertTrue((run_dir / "app/globals.css").exists())
            self.assertTrue((run_dir / "app/hallmark-editorial.css").exists())

    def test_static_package_requires_local_story_cover(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            draft = prepare_static_draft(run_dir, ROOT / "visual-intelligence-prototype.html")
            issue = {"editorial_stories": [{
                "story_id": "story_1",
                "cover_image": {"url": "assets/editorial/missing.jpg"},
            }]}
            self.replace_embedded_issue(draft, issue)
            with self.assertRaisesRegex(WorkflowError, "packaged cover asset is missing"):
                validate_static_package(run_dir, draft, issue)
            cover = run_dir / "assets/editorial/missing.jpg"
            cover.parent.mkdir(parents=True, exist_ok=True)
            cover.write_bytes(b"image")
            validate_static_package(run_dir, draft, issue)

    def test_static_package_rejects_mismatched_or_duplicate_issue_payload(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            draft = prepare_static_draft(run_dir, ROOT / "visual-intelligence-prototype.html")
            issue = {"editorial_stories": []}
            with self.assertRaisesRegex(WorkflowError, "does not match"):
                validate_static_package(run_dir, draft, issue)
            self.replace_embedded_issue(draft, issue)
            html = draft.read_text(encoding="utf-8")
            draft.write_text(html.replace(
                "<!-- ISSUE_DATA_END -->",
                "<!-- ISSUE_DATA_END --><!-- ISSUE_DATA_END -->",
            ), encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "exactly one"):
                validate_static_package(run_dir, draft, issue)

    def test_static_package_rejects_missing_css_and_unsafe_cover_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            run_dir = Path(temporary_directory)
            draft = prepare_static_draft(run_dir, ROOT / "visual-intelligence-prototype.html")
            issue = {"editorial_stories": []}
            self.replace_embedded_issue(draft, issue)
            (run_dir / "tokens.css").unlink()
            with self.assertRaisesRegex(WorkflowError, "stylesheet asset is missing"):
                validate_static_package(run_dir, draft, issue)

            (run_dir / "tokens.css").write_text("body {}")
            issue["editorial_stories"] = [{
                "story_id": "story_unsafe",
                "cover_image": {"url": "assets/../outside.jpg"},
            }]
            self.replace_embedded_issue(draft, issue)
            with self.assertRaisesRegex(WorkflowError, "unsafe local cover path"):
                validate_static_package(run_dir, draft, issue)

    def test_template_exposes_llm_analysis_without_crossing_gate(self):
        candidates = copy.deepcopy(self.candidates)
        target_id = select_review_candidates(candidates, self.config)[0]["candidate_id"]
        target = next(item for item in candidates["selected_candidates"] if item["candidate_id"] == target_id)
        target["llm_analysis"] = {
            "recommended_disposition": "p1", "disposition_reason": "值得核验但尚未核验。",
            "verification_questions": ["原文指标在哪里？"], "what": "测试", "why": "测试",
        }
        template = review_template(self.collection, candidates, "test_llm", self.config)
        record = next(item for item in template["records"] if item["candidate_id"] == target["candidate_id"])
        self.assertEqual(record["agent_recommendation"], "p1")
        self.assertIn("原文指标在哪里？", record["verification_questions"])
        self.assertEqual(record["decision"], "pending")

    def test_failed_hard_gates_are_separate_from_p1_review(self):
        collection = {
            "source_records": [{
                "source_id": "src_incomplete", "raw_title": "正文缺失",
                "canonical_url": "https://example.com/incomplete",
            }, {
                "source_id": "src_fidelity", "raw_title": "推测被写成事实",
                "canonical_url": "https://example.com/fidelity",
            }]
        }
        candidates = {
            "selected_candidates": [],
            "exclusions": {"content_incomplete": [{
                "source_id": "src_incomplete", "title": "正文缺失",
                "gate": {"status": "fail", "reason": "full_text_required_but_missing"},
            }]},
            "gated_candidates": [{
                "candidate_id": "cand_fidelity", "primary_source_id": "src_fidelity",
                "canonical_title": "推测被写成事实",
                "hard_gates": {"fact_wording_fidelity": {
                    "status": "fail", "reason": "source_attribution_or_uncertainty_was_upgraded_to_fact",
                }},
                "llm_analysis": {"what": "未经限定的事实"},
            }],
        }
        p1 = review_template(collection, candidates, "test_gates", self.config)
        gated = gated_review_template(collection, candidates, "test_gates")
        self.assertEqual(p1["records"], [])
        self.assertEqual(gated["count"], 2)
        self.assertEqual({item["gate_type"] for item in gated["records"]}, {
            "content_completeness", "fact_wording_fidelity",
        })

    def test_pending_fidelity_gate_cannot_enter_p1(self):
        candidate = {
            "candidate_id": "cand_pending", "primary_source_id": "src_pending",
            "canonical_title": "待模型归因检查", "published_at": "2026-08-26T00:00:00Z",
            "verification_priority": "priority.p1", "front_display_eligible": True,
            "hard_gates": {
                "content_completeness": {"status": "pass"},
                "fact_wording_fidelity": {"status": "pending_llm"},
            },
            "score": 20, "intelligence_type": "type.company_strategy",
        }
        candidates = {"selected_candidates": [candidate], "exclusions": {}}
        collection = {"source_records": [{
            "source_id": "src_pending", "raw_title": "待模型归因检查",
            "canonical_url": "https://example.com/pending",
        }]}
        self.assertEqual(select_review_candidates(candidates, self.config), [])
        gated = gated_review_template(collection, candidates, "test_pending")
        self.assertEqual(gated["count"], 1)
        self.assertEqual(gated["records"][0]["gate"]["status"], "pending_llm")

    def test_pending_review_cannot_cross_gate(self):
        template = review_template(self.collection, self.candidates, "test_run", self.config)
        errors = validate_review(template, self.candidates)
        self.assertTrue(errors)
        self.assertTrue(any("review_status" in error for error in errors))
        self.assertTrue(any("primary source" in error for error in errors))

    def test_approved_fixture_builds_only_included_events(self):
        result = build_bundle(copy.deepcopy(self.approved), self.collection, self.candidates)
        self.assertEqual(result["summary"]["p1_reviewed"], 16)
        self.assertEqual(result["summary"]["included_events"], 9)
        self.assertEqual(len(result["intelligence_events"]), 9)
        self.assertTrue(all(item["status"] == "approved" for item in result["intelligence_events"]))

    def test_missing_claim_blocks_resume(self):
        review = copy.deepcopy(self.approved)
        review["records"][0]["claims"] = []
        errors = validate_review(review, self.candidates)
        self.assertTrue(any("evidence claim" in error for error in errors))

    def test_secondary_source_can_be_watch_but_not_formal_event(self):
        review = copy.deepcopy(self.approved)
        review["records"][0]["decision"] = "watch"
        review["records"][0]["verification_status"] = "verified_secondary"
        review["records"][0]["event"] = None
        errors = validate_review(review, self.candidates)
        self.assertFalse(any("source" in error for error in errors))

    def test_review_does_not_force_unverified_items_to_meet_a_minimum(self):
        review = copy.deepcopy(self.approved)
        for index, record in enumerate(review["records"]):
            if index >= 3:
                record["decision"] = "watch"
                record["verification_status"] = "verified_secondary"
                record["event"] = None
        errors = validate_review(review, self.candidates)
        self.assertFalse(any("included events" in error for error in errors))

    def test_duplicate_event_id_blocks_resume(self):
        review = copy.deepcopy(self.approved)
        included = [item for item in review["records"] if item["decision"] == "include"]
        included[1]["event"]["event_id"] = included[0]["event"]["event_id"]
        errors = validate_review(review, self.candidates)
        self.assertTrue(any("duplicate event_id" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
