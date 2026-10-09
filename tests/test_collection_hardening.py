import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from spectra_agent.brief_quality import isolate_headline, evidence_search_plan

spec = importlib.util.spec_from_file_location("hardened_collect", Path(__file__).parents[1] / "collector/collect.py")
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)


class HardeningTests(unittest.TestCase):
    def tearDown(self):
        collector.REQUEST_POLICY.clear()

    def test_roundup_keeps_only_model_segment(self):
        title = "折叠屏 iPhone 初期产量受限；环比增长379%，腾讯 HY4 登顶大模型调用榜；特斯拉降价 | 极客早知道"
        self.assertEqual(isolate_headline(title, "extended.foundation_multimodal"), "环比增长379%，腾讯 HY4 登顶大模型调用榜")
        self.assertIsNone(isolate_headline("GPT发布；Claude发布", "extended.foundation_multimodal"))

    def test_evidence_queries_are_bounded_and_never_approve(self):
        plan = evidence_search_plan({"canonical_title": "模型发布", "missing_evidence": ["官方日期", "指标口径", "价格"]})
        self.assertEqual(len(plan["queries"]), 2)
        self.assertFalse(plan["auto_approve"])

    def test_failure_does_not_stop_next_source_and_checkpoint_reuses_success(self):
        now = datetime(2026, 9, 10, tzinfo=timezone.utc)
        ctx = collector.Context(now, now, now, None)
        with tempfile.TemporaryDirectory() as folder:
            config = {"checkpoint_path": str(Path(folder) / "checkpoint.json"), "sources": [
                {"registry_id": name, "adapter": "rss"} for name in ("bad", "good")]}
            good = [{"access_status": "success", "raw_excerpt": "excerpt", "canonical_url": "https://example.com", "source_id": "s", "published_at": None, "source_name": "good"}]
            with patch.object(collector, "collect_feed", side_effect=[TimeoutError("timeout"), (good, {"status": "success"})]):
                result = collector.run(config, ctx)
            self.assertEqual(result["summary"]["failed_sources"], 1)
            self.assertEqual(result["source_checks"][1]["summary_only_count"], 1)
            with patch.object(collector, "collect_feed", return_value=([], {"status": "success"})) as fetch:
                resumed = collector.run(config, ctx)
            self.assertEqual(fetch.call_count, 1)
            self.assertTrue(resumed["source_checks"][1]["resumed_from_checkpoint"])

    def test_404_is_not_retried(self):
        with patch.object(collector, "urlopen", side_effect=HTTPError("https://example.com", 404, "missing", {}, None)) as fetch:
            with self.assertRaises(HTTPError):
                collector.fetch_bytes("https://example.com")
            self.assertEqual(fetch.call_count, 1)

    def test_request_budget_stops_network(self):
        collector.REQUEST_POLICY.update(used=1, limit=1)
        with patch.object(collector, "urlopen") as fetch:
            with self.assertRaisesRegex(RuntimeError, "budget exhausted"):
                collector.fetch_bytes("https://example.com")
            fetch.assert_not_called()

    def test_external_extractor_rejects_private_and_file_urls(self):
        for url in ("file:///etc/passwd", "http://127.0.0.1", "http://169.254.169.254", "https://u:p@example.com", "http://[::1]"):
            with self.assertRaises(ValueError):
                collector.validate_external_url(url)

    def test_real_http_failures_are_bounded(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        import time
        import socket

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/slow":
                    time.sleep(0.15)
                self.send_response(429 if self.path == "/limited" else 404)
                self.send_header("Retry-After", "0")
                self.end_headers()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_port}"
        original_resolve = collector.safe_http.resolve_public
        local_only = patch.object(collector.safe_http, "resolve_public", side_effect=lambda url, _: original_resolve(url, [base]))
        local_only.start()
        collector.safe_http.configure_budget(interval=0)
        try:
            for path, code in (("/missing", 404), ("/limited", 429)):
                with self.assertRaises(HTTPError) as raised:
                    collector.fetch_bytes(base + path, attempts=2, max_retry_delay=0)
                self.assertEqual(raised.exception.code, code)
            with self.assertRaises(TimeoutError):
                collector.fetch_bytes(base + "/slow", attempts=1, timeout=0.02)
        finally:
            local_only.stop()
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
