import json
import tempfile
import unittest
from pathlib import Path

from spectra_agent.dingtalk_push import notification_payload, readiness, signed_webhook


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
            "issue": {"title": "今日情报", "story_count": 2, "brief_count": 8},
        }, "https://example.com/")
        self.assertEqual(payload["msgtype"], "markdown")
        self.assertIn("正式事件：2 条", payload["markdown"]["text"])
        self.assertIn("https://example.com/", payload["markdown"]["text"])


if __name__ == "__main__":
    unittest.main()
