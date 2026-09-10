import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from spectra_agent.dingtalk_push import main, notification_payload, readiness, signed_webhook


class DingTalkPushTest(unittest.TestCase):
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
        self.assertIn("30 秒结论", payload["markdown"]["text"])
        self.assertIn("今日焦点", payload["markdown"]["text"])
        self.assertIn("![SPECTRA 今日视觉情报](https://example.com/header.png)", payload["markdown"]["text"])
        self.assertIn("**30 秒结论**", payload["markdown"]["text"])
        self.assertIn("**01 · 镜头控制成为新焦点**", payload["markdown"]["text"])
        self.assertNotIn("权重", payload["markdown"]["text"])
        self.assertIn("**12%**", payload["markdown"]["text"])
        self.assertIn("L_motion", payload["markdown"]["text"])
        self.assertNotIn("mathcal", payload["markdown"]["text"])
        self.assertNotIn("重点事件 ·", payload["markdown"]["text"])
        self.assertIn("https://example.com/", payload["markdown"]["text"])

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


if __name__ == "__main__":
    unittest.main()
