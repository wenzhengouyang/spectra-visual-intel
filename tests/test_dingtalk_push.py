import json
import io
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from spectra_agent.dingtalk_push import (
    _story_identity, _top_stories, deliver, main, notification_payload,
    readiness, signed_webhook, wait_for_public_page,
)


class DingTalkPushTest(unittest.TestCase):
    def test_top_stories_prioritize_items_not_sent_in_previous_digest(self):
        old = {
            "story_id": "old", "headline": "昨天头条", "published_at": "2026-09-14T09:00:00Z",
            "source_links": [{"url": "https://example.com/old"}],
        }
        fresh = [{
            "brief_id": f"new-{index}", "headline": f"今日新增{index}",
            "dek": f"今日新增{index}的已核实摘要。",
            "published_at": f"2026-09-15T0{index}:00:00Z",
            "source_links": [{"url": f"https://example.com/new-{index}"}],
        } for index in range(1, 4)]
        issue = {"editorial_stories": [old], "news_briefs": fresh}
        selected = _top_stories(issue, {}, excluded_identities={_story_identity(old)})
        self.assertEqual([story["headline"] for story in selected], ["今日新增3", "今日新增2", "今日新增1"])

    def test_top_stories_never_backfill_a_previous_delivery(self):
        old = {
            "story_id": "old", "headline": "昨天头条", "published_at": "2026-09-14T09:00:00Z",
            "source_links": [{"url": "https://example.com/old"}],
        }
        selected = _top_stories(
            {"editorial_stories": [old], "news_briefs": []}, {},
            excluded_identities={_story_identity(old)},
        )
        self.assertEqual(selected, [])

    def test_top_stories_reject_internal_verification_placeholders(self):
        weak = {
            "brief_id": "weak", "headline": "一条候选动态",
            "dek": "来源标题提及上述动态，具体口径与背景尚待原文核验。",
            "published_at": "2026-09-15T09:00:00Z",
        }
        strong = {
            "brief_id": "strong", "headline": "已核实动态",
            "dek": "原文明确展示了设计师使用 Agent 更新网站和操作 Figma。",
            "published_at": "2026-09-15T08:00:00Z",
        }
        selected = _top_stories({"editorial_stories": [], "news_briefs": [weak, strong]}, {})
        self.assertEqual([story["brief_id"] for story in selected], ["strong"])

    def test_signed_webhook_uses_official_https_host(self):
        url = signed_webhook(
            "https://oapi.dingtalk.com/robot/send?access_token=test",
            "secret",
            now_ms=123,
        )
        self.assertIn("timestamp=123", url)
        self.assertIn("sign=", url)
        with self.assertRaises(ValueError):
            signed_webhook("http://example.com/hook", None)

    def test_readiness_requires_completed_published_run(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            (run_dir / "run.json").write_text(json.dumps({
                "status": "completed", "publish_status": "not_published",
            }))
            (run_dir / "editorial-issue.json").write_text(json.dumps({"issue": {}}))
            self.assertEqual(readiness(run_dir, True)[:2], (False, "page_not_published"))
            self.assertTrue(readiness(run_dir, False)[0])

    def test_wait_for_public_page_retries_until_current_issue_is_visible(self):
        with patch("spectra_agent.dingtalk_push.public_page_ready", side_effect=[False, False, True]) as check:
            self.assertTrue(wait_for_public_page("https://example.com", "daily-20260914", 10, 1,
                                                sleeper=lambda _: None))
        self.assertEqual(check.call_count, 3)

    def test_payload_links_to_public_page(self):
        payload = notification_payload({
            "issue": {
                "report_date": "2026-09-08",
                "story_count": 2,
                "brief_count": 8,
                "today_new_count": 1,
                "rolling_thesis": "视频生成进入可控运镜竞争。",
                "lead_story_id": "lead",
                "top_story_ids": ["lead", "second", "third"],
                "trend_one_line": "产品与评测需要同步关注空间轨迹。",
            },
            "editorial_stories": [{
                "story_id": "lead",
                "category": "视频生成",
                "headline": "镜头控制成为新焦点",
                "one_line_takeaway": "研究引入 $\\mathcal{L}_{motion}$ 控制项。",
                "why_it_matters": {"text": "长镜头的空间漂移有了可测量的约束。"},
                "watch_next": ["关注真实制作流程中的稳定性。"],
                "editorial_score": 96,
            }, {
                "story_id": "second", "headline": "第二焦点", "one_line_takeaway": "准确率提升12%。", "editorial_score": 93,
            }, {
                "story_id": "third", "headline": "第三焦点", "one_line_takeaway": "进入产品验证。", "editorial_score": 90,
            }],
        }, "https://example.com/", "https://example.com/header.png")
        self.assertEqual(payload["msgtype"], "markdown")
        self.assertEqual(payload["markdown"]["title"], "SPECTRA · 09月08日")
        self.assertNotIn("30 秒结论", payload["markdown"]["text"])
        self.assertIn("今日 Top 3 焦点", payload["markdown"]["text"])
        self.assertIn("![SPECTRA 今日视觉情报](https://example.com/header.png)", payload["markdown"]["text"])
        self.assertIn("**🔹 01｜镜头控制成为新焦点**", payload["markdown"]["text"])
        self.assertNotIn("权重", payload["markdown"]["text"])
        self.assertIn("**12%**", payload["markdown"]["text"])
        self.assertIn("L_motion", payload["markdown"]["text"])
        self.assertNotIn("mathcal", payload["markdown"]["text"])
        self.assertNotIn("重点事件 ·", payload["markdown"]["text"])
        self.assertIn("https://example.com/", payload["markdown"]["text"])

    def test_payload_rejects_ellipsis_and_repeated_summary(self):
        payload = notification_payload({
            "issue": {"report_date": "2026-09-08", "top_story_ids": ["cut", "repeat", "good"]},
            "editorial_stories": [
                {"story_id": "cut", "headline": "被截断的标题…", "dek": "这条不应发送。"},
                {"story_id": "repeat", "headline": "模型发布新的空间推理框架", "dek": "模型发布新的空间推理框架。"},
                {"story_id": "good", "headline": "模型发布新的空间推理框架", "dek": "作者同时公开了用于检验布局关系的评测集。"},
            ],
        }, "https://example.com/")
        text = payload["markdown"]["text"]
        self.assertNotIn("被截断", text)
        self.assertNotIn("模型发布新的空间推理框架。", text)
        self.assertIn("作者同时公开了用于检验布局关系的评测集。", text)

    def test_payload_summary_is_a_complete_sentence(self):
        payload = notification_payload({
            "issue": {"report_date": "2026-09-08", "top_story_ids": ["good"]},
            "editorial_stories": [{
                "story_id": "good", "headline": "空间推理模型更新",
                "dek": "作者同时公开了用于检验布局关系的评测集",
            }],
        }, "https://example.com/")
        self.assertIn("作者同时公开了用于检验布局关系的评测集。", payload["markdown"]["text"])

    def test_suppressed_marker_prevents_retroactive_send(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "daily-20260908"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({
                "status": "completed", "publish_status": "published",
            }))
            (run_dir / "editorial-issue.json").write_text(json.dumps({"issue": {}}))
            (run_dir / "dingtalk-push.json").write_text(json.dumps({
                "status": "suppressed", "reason": "baseline_deployment_without_retroactive_send",
            }))
            config = {
                "timezone": "Asia/Shanghai",
                "dingtalk_push": {"enabled": True, "require_published": True},
            }
            with patch("spectra_agent.dingtalk_push.resolve_config", return_value=(Path("config.json"), config)), \
                    patch("spectra_agent.dingtalk_push.runs_dir", return_value=Path(temporary)), \
                    patch("spectra_agent.dingtalk_push.push") as send, \
                    patch("sys.argv", ["dingtalk_push.py", "--run-id", "daily-20260908"]):
                self.assertEqual(main(), 0)
                send.assert_not_called()

    def test_disabled_joint_delivery_reports_disabled_not_weekend(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "daily-20260914"
            run_dir.mkdir()
            (run_dir / "run.json").write_text(json.dumps({
                "status": "completed", "publish_status": "published",
            }))
            (run_dir / "editorial-issue.json").write_text(json.dumps({"issue": {}}))
            config = {
                "timezone": "Asia/Shanghai",
                "joint_delivery": {"enabled": True, "start_date": "2026-09-11"},
                "dingtalk_push": {"enabled": False, "require_published": True},
            }
            output = io.StringIO()
            with patch("spectra_agent.dingtalk_push.resolve_config", return_value=(Path("config.json"), config)), \
                    patch("spectra_agent.dingtalk_push.runs_dir", return_value=Path(temporary)), \
                    patch("spectra_agent.dingtalk_push.weekend_delivery_blocked", return_value=False), \
                    patch("sys.argv", ["dingtalk_push.py", "--run-id", "daily-20260914"]), \
                    redirect_stdout(output):
                self.assertEqual(deliver(), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "disabled")

    def test_paused_run_blocks_even_explicit_resend(self):
        with tempfile.TemporaryDirectory() as temporary:
            config = {
                "timezone": "Asia/Shanghai",
                "dingtalk_push": {"enabled": True, "paused_run_ids": ["daily-20260915"]},
            }
            output = io.StringIO()
            with patch("spectra_agent.dingtalk_push.resolve_config", return_value=(Path("config.json"), config)), \
                    patch("spectra_agent.dingtalk_push.runs_dir", return_value=Path(temporary)), \
                    patch("spectra_agent.dingtalk_push.push") as send, \
                    patch("sys.argv", ["dingtalk_push.py", "--run-id", "daily-20260915", "--resend"]), \
                    redirect_stdout(output):
                self.assertEqual(deliver(), 0)
            self.assertEqual(json.loads(output.getvalue())["status"], "paused")
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
