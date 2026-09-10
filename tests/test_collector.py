import importlib.util
import json
import os
import sys
import tempfile
import unittest
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


SPEC = importlib.util.spec_from_file_location("spectra_collect", Path(__file__).parents[1] / "collector" / "collect.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class CollectorContractTest(unittest.TestCase):
    def setUp(self):
        now = datetime(2026, 8, 12, tzinfo=timezone.utc)
        self.ctx = MODULE.Context(now, datetime(2026, 8, 5, tzinfo=timezone.utc), now, None)
        self.source = {
            "registry_id": "reg_test",
            "adapter": "newscrawler",
            "source_name": "Test",
            "source_type": "media",
            "publisher": "Publisher",
            "url": "https://example.com/article?utm_source=test",
            "language": "en"
        }

    def test_fetch_bytes_honors_retry_after_on_429(self):
        limited = HTTPError(
            "https://example.com/feed", 429, "Too Many Requests",
            {"Retry-After": "7"}, BytesIO(),
        )
        response = unittest.mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"ok"
        with patch.object(MODULE, "urlopen", side_effect=[limited, response]), \
             patch.object(MODULE.time, "sleep") as sleep:
            self.assertEqual(MODULE.fetch_bytes("https://example.com/feed", attempts=2), b"ok")
        sleep.assert_called_once_with(7.0)

    def test_fetch_bytes_bounds_large_retry_after(self):
        limited = HTTPError(
            "https://example.com/feed", 429, "Too Many Requests",
            {"Retry-After": "3600"}, BytesIO(),
        )
        with patch.object(MODULE, "urlopen", side_effect=limited), \
             patch.object(MODULE.time, "sleep") as sleep:
            with self.assertRaises(HTTPError):
                MODULE.fetch_bytes("https://example.com/feed", attempts=2, max_retry_delay=30)
        sleep.assert_called_once_with(30)

    def test_newscrawler_normalizes_to_source_record(self):
        payload = {"title": "A visual AI release", "url": self.source["url"], "author": "A", "content": "Body", "published_at": "2026-08-10T10:00:00Z"}
        record = MODULE.normalize_newscrawler_payload(payload, self.source, self.ctx)
        self.assertEqual(record["record_type"], "source_record")
        self.assertEqual(record["canonical_url"], "https://example.com/article")
        self.assertEqual(record["author"], ["A"])
        self.assertEqual(record["raw_text"], "Body")
        self.assertEqual(record["access_status"], "success")
        self.assertEqual(set(record), {
            "schema_version", "record_type", "source_id", "registry_id", "source_type", "source_name",
            "publisher", "author", "source_url", "canonical_url", "raw_title", "raw_text", "raw_excerpt", "official_image_url",
            "published_at", "collected_at", "language", "content_hash", "access_status", "http_status",
            "failure_reason", "discovered_by", "discovery_context", "rights_scope", "processing_status"
        })

    def test_news_extractor_payload_normalizes_to_source_record(self):
        payload = {
            "title": "AI product workflow",
            "news_url": self.source["url"],
            "meta_info": {"author_name": "Lenny", "publish_time": "2026-08-10 10:00:00"},
            "texts": ["Paragraph one", "Paragraph two"],
        }
        record = MODULE.normalize_newscrawler_payload(payload, self.source, self.ctx)
        self.assertEqual(record["author"], ["Lenny"])
        self.assertEqual(record["raw_text"], "Paragraph one\nParagraph two")
        self.assertEqual(record["published_at"], "2026-08-10T10:00:00Z")

    def test_source_ids_are_stable_after_tracking_removal(self):
        self.assertEqual(MODULE.stable_id("https://example.com/a?utm_source=x"), MODULE.stable_id("https://example.com/a"))

    def test_podcast_feed_records_audio_without_downloading_it(self):
        source = {
            "registry_id": "reg_podcast_test",
            "adapter": "rss",
            "source_name": "Test Podcast",
            "source_type": "professional_view",
            "publisher": "Publisher",
            "url": "https://example.com/feed.xml",
            "content_format": "podcast",
            "rights_scope": "excerpt",
            "audio_ingestion": "metadata_only",
            "language": "en",
        }
        feed = b'''<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title><item>
          <title>AI agent interview</title><link>https://example.com/episode</link>
          <pubDate>Mon, 10 Aug 2026 10:00:00 GMT</pubDate>
          <description>Public show notes about an AI agent.</description>
          <enclosure url="https://cdn.example.com/episode.mp3" type="audio/mpeg" length="100"/>
        </item></channel></rss>'''
        with patch.object(MODULE, "fetch_bytes", return_value=feed):
            records, health = MODULE.collect_feed(source, self.ctx, [])
        self.assertEqual(len(records), 1)
        self.assertIsNone(records[0]["raw_text"])
        self.assertIn("audio_enclosure:present_not_downloaded", records[0]["discovery_context"])
        self.assertEqual(health["audio_enclosures"], 1)
        self.assertEqual(health["feed_audio_entries"], 1)
        self.assertEqual(health["audio_ingestion"], "metadata_only")
        self.assertEqual(health["transcription"], "disabled_unless_official_public_transcript")

    def test_podcast_feed_limits_public_show_notes_to_configured_excerpt(self):
        source = {
            "registry_id": "reg_podcast_test",
            "adapter": "rss",
            "source_name": "Test Podcast",
            "source_type": "professional_view",
            "publisher": "Publisher",
            "url": "https://example.com/feed.xml",
            "content_format": "podcast",
            "rights_scope": "excerpt",
            "max_excerpt_chars": 80,
            "language": "en",
        }
        feed = ('''<?xml version="1.0"?><rss version="2.0"><channel><title>Test</title><item>
          <title>AI interview</title><link>https://example.com/episode</link>
          <pubDate>Mon, 10 Aug 2026 10:00:00 GMT</pubDate><description>''' +
          ("Public show notes and transcript material. " * 20) +
          '''</description></item></channel></rss>''').encode()
        with patch.object(MODULE, "fetch_bytes", return_value=feed):
            records, health = MODULE.collect_feed(source, self.ctx, [])
        self.assertLessEqual(len(records[0]["raw_excerpt"]), 82)
        self.assertTrue(records[0]["raw_excerpt"].endswith("…"))
        self.assertEqual(health["truncated_excerpts"], 1)

    def test_feed_official_image_enters_source_record(self):
        source = {
            "registry_id": "reg_feed_image", "adapter": "rss", "source_name": "Test",
            "source_type": "official_announcement", "publisher": "Publisher",
            "url": "https://example.com/feed.xml", "rights_scope": "excerpt", "language": "en",
        }
        feed = b'''<?xml version="1.0"?><rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>Test</title><item>
          <title>AI model release</title><link>https://example.com/release</link>
          <pubDate>Mon, 10 Aug 2026 10:00:00 GMT</pubDate>
          <description>Official release notes.</description>
          <media:thumbnail url="https://cdn.example.com/release.jpg" />
        </item></channel></rss>'''
        with patch.object(MODULE, "fetch_bytes", return_value=feed):
            records, _ = MODULE.collect_feed(source, self.ctx, [])
        self.assertEqual(records[0]["official_image_url"], "https://cdn.example.com/release.jpg")

    def test_registry_includes_compliant_blog_and_podcast_sources(self):
        path = Path(__file__).parents[1] / "collector" / "source_registry.v0.2.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        expected = {
            "reg_rss_sv101_podcast",
            "reg_rss_simon_willison_blog",
            "reg_rss_crossing_podcast",
            "reg_rss_latent_space_podcast",
        }
        selected = {source["registry_id"]: source for source in config["sources"] if source["registry_id"] in expected}
        self.assertEqual(set(selected), expected)
        self.assertTrue(all(source["rights_scope"] == "excerpt" for source in selected.values()))
        podcasts = [source for source in selected.values() if source["content_format"] == "podcast"]
        self.assertTrue(all(source["audio_ingestion"] == "metadata_only" for source in podcasts))
        self.assertTrue(all(source["transcript_ingestion"] == "official_public_only" for source in podcasts))

    def test_registry_is_valid_json(self):
        path = Path(__file__).parents[1] / "collector" / "source_registry.v0.2.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(config["schema_version"], "0.2")
        self.assertEqual(
            {s["adapter"] for s in config["sources"]},
            {"rss", "batch_index", "arxiv", "github_atom", "news_extractor_feed", "news_extractor_inbox", "werss_api", "official_ir_index", "hkex_title_search", "sec_submissions", "sitemap"},
        )
        self.assertEqual(config["watchlists"]["wechat_accounts"], "collector/wechat_watchlist.v0.1.json")
        self.assertEqual(config["watchlists"]["core_observers"], "collector/observer_watchlist.v0.1.json")

    def test_observer_watchlist_expands_only_public_automated_feeds(self):
        config = {
            "watchlists": {"core_observers": "collector/observer_watchlist.v0.1.json"},
            "sources": [],
        }
        expanded = MODULE.expand_observer_sources(config)
        self.assertGreaterEqual(len(expanded["sources"]), 5)
        self.assertTrue(all(source["adapter"] == "rss" for source in expanded["sources"]))
        self.assertTrue(all(source["url"].startswith("https://") for source in expanded["sources"]))
        self.assertTrue(all(source["rights_scope"] == "excerpt" for source in expanded["sources"]))

    def test_werss_article_normalizes_to_source_record(self):
        source = {
            "registry_id": "reg_werss",
            "adapter": "werss_api",
            "source_name": "WeRSS 微信公众号订阅",
            "source_type": "wechat_official_account",
            "publisher": None,
            "url": "http://127.0.0.1:8001",
            "language": "zh-CN",
        }
        record = MODULE.normalize_werss_article({
            "mp_id": "MP_WXS_1",
            "mp_name": "机器之心",
            "title": "视频生成模型的新进展",
            "description": "<p>模型在时空一致性上获得提升。</p>",
            "url": "https://mp.weixin.qq.com/s/example?utm_source=test",
            "publish_time": 1786500000,
        }, source, self.ctx)
        self.assertEqual(record["record_type"], "source_record")
        self.assertEqual(record["source_name"], "机器之心")
        self.assertEqual(record["publisher"], "机器之心")
        self.assertEqual(record["raw_excerpt"], "模型在时空一致性上获得提升。")
        self.assertEqual(record["canonical_url"], "https://mp.weixin.qq.com/s/example")
        self.assertEqual(record["rights_scope"], "excerpt")

    def test_werss_full_text_enrichment_merges_skill_output_into_source_record(self):
        source = {
            "registry_id": "reg_werss", "adapter": "werss_api",
            "source_name": "WeRSS", "source_type": "wechat_official_account",
            "publisher": None, "url": "http://127.0.0.1:8001", "language": "zh-CN",
            "watchlist": str(Path(__file__).parents[1] / "collector" / "wechat_watchlist.v0.1.json"),
            "full_text_command": ["uv", "run"], "full_text_extract_limit": 3,
            "full_text_min_chars": 50,
        }
        record = MODULE.normalize_werss_article({
            "mp_id": "MP_WXS_1", "mp_name": "机器之心", "title": "视频生成模型的新进展",
            "description": "一段导语", "url": "https://mp.weixin.qq.com/s/example",
            "publish_time": 1786500000,
        }, source, self.ctx)
        extracted = MODULE.make_record(
            source={**source, "adapter": "news_extractor", "source_name": "机器之心", "publisher": "机器之心"},
            url=record["canonical_url"], title=record["raw_title"], ctx=self.ctx,
            published_at=datetime.fromtimestamp(1786500000, tz=timezone.utc),
            raw_text="这是完整正文。" * 20, rights_scope="full_text",
        )
        with patch.object(MODULE, "collect_news_extractor", return_value=([extracted], {"status": "success"})):
            health = MODULE.enrich_werss_full_text([record], source, self.ctx)
        self.assertEqual(health["extracted"], 1)
        self.assertEqual(record["processing_status"], "text_extracted")
        self.assertGreaterEqual(len(record["raw_text"]), 50)
        self.assertEqual(record["rights_scope"], "full_text_internal_analysis")

    def test_werss_full_text_failure_enters_dedicated_review_queue(self):
        source = {
            "registry_id": "reg_werss", "adapter": "werss_api",
            "source_name": "WeRSS", "source_type": "wechat_official_account",
            "publisher": None, "url": "http://127.0.0.1:8001", "language": "zh-CN",
            "watchlist": str(Path(__file__).parents[1] / "collector" / "wechat_watchlist.v0.1.json"),
            "full_text_command": ["uv", "run"], "full_text_extract_limit": 3,
            "full_text_min_chars": 50, "full_text_retry_attempts": 2,
        }
        record = MODULE.normalize_werss_article({
            "mp_id": "MP_WXS_1", "mp_name": "机器之心", "title": "待补抓文章",
            "description": "一段导语", "url": "https://mp.weixin.qq.com/s/missing",
            "publish_time": 1786500000,
        }, source, self.ctx)
        failed = MODULE.make_record(
            source={**source, "adapter": "news_extractor", "source_name": "机器之心"},
            url=record["canonical_url"], title=record["raw_title"], ctx=self.ctx,
            access_status="failed", failure_reason="blocked", rights_scope="metadata_only",
        )
        with patch.object(MODULE, "collect_news_extractor", return_value=([failed], {"status": "failed", "error": "blocked"})) as mocked:
            health = MODULE.enrich_werss_full_text([record], source, self.ctx)
        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(record["processing_status"], "full_text_needs_review")
        self.assertEqual(health["failed"], 1)
        self.assertEqual(health["review_queue"][0]["review_status"], "pending")
        self.assertEqual(health["review_queue"][0]["allowed_decisions"], ["retry", "supply_verified_text", "exclude"])

        artifact = MODULE.build_full_text_review_artifact({
            "run_id": "run_test", "collected_at": "2026-08-12T00:00:00Z",
            "source_checks": [{"full_text_extraction": health}],
        })
        self.assertEqual(artifact["status"], "waiting_for_review")
        self.assertEqual(artifact["count"], 1)

    def test_werss_detail_text_is_preferred_when_cached_locally(self):
        source = {
            "registry_id": "reg_werss", "adapter": "werss_api",
            "source_name": "WeRSS", "source_type": "wechat_official_account",
            "publisher": None, "url": "http://127.0.0.1:8001", "language": "zh-CN",
        }
        record = MODULE.normalize_werss_article({
            "mp_id": "MP_WXS_1", "mp_name": "机器之心", "title": "视频生成模型的新进展",
            "description": "一段导语", "url": "https://mp.weixin.qq.com/s/example",
            "publish_time": 1786500000,
        }, source, self.ctx)
        attached = MODULE.attach_werss_detail_text(
            record, {"content_html": "<p>这是正文内容。</p>" * 30}, minimum_chars=50
        )
        self.assertTrue(attached)
        self.assertEqual(record["processing_status"], "text_extracted")
        self.assertIn("full_text:werss_detail", record["discovery_context"])

    def test_local_env_loads_export_prefixed_werss_password(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env.local"
            env_file.write_text("export WERSS_PASSWORD=secret-test\n", encoding="utf-8")
            saved = os.environ.pop("WERSS_PASSWORD", None)
            try:
                MODULE.load_local_env(env_file)
                self.assertEqual(os.environ["WERSS_PASSWORD"], "secret-test")
            finally:
                os.environ.pop("WERSS_PASSWORD", None)
                if saved is not None:
                    os.environ["WERSS_PASSWORD"] = saved

    def test_werss_reports_missing_password_without_crossing_into_api(self):
        source = {
            "registry_id": "reg_werss_wechat_watchlist",
            "adapter": "werss_api",
            "url": "http://127.0.0.1:8001",
            "watchlist": str(Path(__file__).parents[1] / "collector/wechat_watchlist.v0.1.json"),
            "password_env": "WERSS_PASSWORD",
        }
        saved = os.environ.pop("WERSS_PASSWORD", None)
        try:
            records, health = MODULE.collect_werss(source, self.ctx)
        finally:
            if saved is not None:
                os.environ["WERSS_PASSWORD"] = saved
        self.assertEqual(records, [])
        self.assertEqual(health["status"], "failed")
        self.assertEqual(health["error"], "WERSS_PASSWORD is not set")

    def test_werss_reports_expired_wechat_authorization(self):
        source = {
            "registry_id": "reg_werss_wechat_watchlist",
            "adapter": "werss_api",
            "url": "http://127.0.0.1:8001",
            "watchlist": str(Path(__file__).parents[1] / "collector/wechat_watchlist.v0.1.json"),
            "password_env": "WERSS_PASSWORD",
        }
        saved = os.environ.get("WERSS_PASSWORD")
        os.environ["WERSS_PASSWORD"] = "test-only"
        try:
            with patch.object(
                MODULE,
                "werss_request_json",
                side_effect=[{"access_token": "token"}, {"login_status": False, "qr_code": False}],
            ):
                records, health = MODULE.collect_werss(source, self.ctx)
        finally:
            if saved is None:
                os.environ.pop("WERSS_PASSWORD", None)
            else:
                os.environ["WERSS_PASSWORD"] = saved
        self.assertEqual(records, [])
        self.assertEqual(health["status"], "failed")
        self.assertTrue(health["reauth_required"])
        self.assertIn("authorization is expired", health["error"])

    def test_werss_empty_article_store_is_not_reported_as_healthy(self):
        source = {
            "registry_id": "reg_werss_wechat_watchlist",
            "adapter": "werss_api",
            "url": "http://127.0.0.1:8001",
            "watchlist": str(Path(__file__).parents[1] / "collector/wechat_watchlist.v0.1.json"),
            "password_env": "WERSS_PASSWORD",
            "page_size": 100,
            "max_scan": 100,
        }
        saved = os.environ.get("WERSS_PASSWORD")
        os.environ["WERSS_PASSWORD"] = "test-only"
        feeds = {
            "list": [{"id": f"feed-{i}", "mp_name": name} for i, name in enumerate([
                "机器之心", "AI好好用", "新智元", "量子位", "晚点LatePost", "极客公园",
                "Founder Park", "雷峰网", "AI科技评论", "海外独角兽", "数字生命卡兹克",
            ])]
        }
        try:
            with patch.object(
                MODULE,
                "werss_request_json",
                side_effect=[
                    {"access_token": "token"},
                    {"login_status": True, "qr_code": False},
                    feeds,
                    {"list": [], "total": 0},
                ],
            ):
                records, health = MODULE.collect_werss(source, self.ctx)
        finally:
            if saved is None:
                os.environ.pop("WERSS_PASSWORD", None)
            else:
                os.environ["WERSS_PASSWORD"] = saved
        self.assertEqual(records, [])
        self.assertEqual(health["status"], "failed")
        self.assertTrue(health["sync_pending"])
        self.assertFalse(health["reauth_required"])

    def test_werss_refreshes_only_one_rotating_account_before_read(self):
        source = {
            "registry_id": "reg_werss_wechat_watchlist",
            "adapter": "werss_api",
            "url": "http://127.0.0.1:8001",
            "watchlist": str(Path(__file__).parents[1] / "collector/wechat_watchlist.v0.1.json"),
            "password_env": "WERSS_PASSWORD",
            "refresh_before_read": True,
            "refresh_limit": 1,
            "page_size": 100,
            "max_scan": 100,
        }
        saved = os.environ.get("WERSS_PASSWORD")
        os.environ["WERSS_PASSWORD"] = "test-only"
        calls = []

        def fake_request(base_url, path, **kwargs):
            calls.append(path)
            if path.endswith("/auth/login"):
                return {"access_token": "token"}
            if path.endswith("/auth/qr/status"):
                return {"login_status": True, "qr_code": False}
            if path.startswith("/api/v1/wx/mps?"):
                return {"list": [{"id": "feed-1", "mp_name": "机器之心"}]}
            if "/mps/update/" in path:
                return {"total": 0, "list": []}
            if path.startswith("/api/v1/wx/articles?"):
                return {"list": [], "total": 0}
            raise AssertionError(path)

        try:
            with patch.object(MODULE, "werss_request_json", side_effect=fake_request):
                records, health = MODULE.collect_werss(source, self.ctx)
        finally:
            if saved is None:
                os.environ.pop("WERSS_PASSWORD", None)
            else:
                os.environ["WERSS_PASSWORD"] = saved
        self.assertEqual(records, [])
        self.assertEqual(health["refresh_attempts"], 1)
        self.assertEqual(health["refresh_results"][0]["status"], "no_articles")
        self.assertEqual(sum("/mps/update/" in path for path in calls), 1)

    def test_werss_falls_back_to_weread_on_wechat_frequency_control(self):
        source = {
            "registry_id": "reg_werss_wechat_watchlist",
            "adapter": "werss_api",
            "url": "http://127.0.0.1:8001",
            "watchlist": str(Path(__file__).parents[1] / "collector/wechat_watchlist.v0.1.json"),
            "password_env": "WERSS_PASSWORD",
            "refresh_before_read": True,
            "refresh_limit": 1,
            "weread_fallback": True,
            "page_size": 100,
            "max_scan": 100,
        }
        saved = os.environ.get("WERSS_PASSWORD")
        os.environ["WERSS_PASSWORD"] = "test-only"
        calls = []

        def fake_request(base_url, path, **kwargs):
            calls.append((path, kwargs))
            if path.endswith("/auth/login"):
                return {"access_token": "token"}
            if path.endswith("/auth/qr/status"):
                return {"login_status": True}
            if path.startswith("/api/v1/wx/mps?"):
                return {"list": [{"id": "MP_WXS_1", "mp_name": "机器之心"}]}
            if "/mps/update/" in path:
                raise RuntimeError("WeChat frequency control on endpoints: appmsg")
            if path.endswith("/weread/collect"):
                self.assertEqual(kwargs["json_body"]["mp_id"], "MP_WXS_1")
                return {"collected": 1}
            if path.startswith("/api/v1/wx/articles?"):
                return {"list": [], "total": 0}
            raise AssertionError(path)

        try:
            with patch.object(MODULE, "werss_request_json", side_effect=fake_request):
                records, health = MODULE.collect_werss(source, self.ctx)
        finally:
            if saved is None:
                os.environ.pop("WERSS_PASSWORD", None)
            else:
                os.environ["WERSS_PASSWORD"] = saved
        self.assertEqual(records, [])
        self.assertEqual(health["refresh_results"][0]["status"], "fallback_success")
        self.assertEqual(health["refresh_results"][0]["fallback"], "weread")
        self.assertEqual(sum(path.endswith("/weread/collect") for path, _ in calls), 1)

    def test_werss_can_use_weread_as_primary_refresh_channel(self):
        source = {
            "registry_id": "reg_werss_wechat_watchlist",
            "adapter": "werss_api",
            "url": "http://127.0.0.1:8001",
            "watchlist": str(Path(__file__).parents[1] / "collector/wechat_watchlist.v0.1.json"),
            "password_env": "WERSS_PASSWORD",
            "refresh_before_read": True,
            "refresh_limit": 1,
            "refresh_channel": "weread",
            "page_size": 100,
            "max_scan": 100,
        }
        saved = os.environ.get("WERSS_PASSWORD")
        os.environ["WERSS_PASSWORD"] = "test-only"
        calls = []

        def fake_request(base_url, path, **kwargs):
            calls.append((path, kwargs))
            if path.endswith("/auth/login"):
                return {"access_token": "token"}
            if path.endswith("/auth/qr/status"):
                return {"login_status": True}
            if path.startswith("/api/v1/wx/mps?"):
                return {"list": [{"id": "MP_WXS_1", "mp_name": "机器之心"}]}
            if path.endswith("/weread/collect"):
                return {"collected": 1}
            if path.startswith("/api/v1/wx/articles?"):
                return {"list": [], "total": 0}
            raise AssertionError(path)

        try:
            with patch.object(MODULE, "werss_request_json", side_effect=fake_request):
                _, health = MODULE.collect_werss(source, self.ctx)
        finally:
            if saved is None:
                os.environ.pop("WERSS_PASSWORD", None)
            else:
                os.environ["WERSS_PASSWORD"] = saved
        self.assertEqual(health["refresh_results"][0]["channel"], "weread")
        self.assertEqual(health["refresh_results"][0]["accepted"], 1)
        self.assertFalse(any(path.endswith("/auth/qr/status") for path, _ in calls))
        self.assertFalse(any("/mps/update/" in path for path, _ in calls))

    def test_werss_reports_expired_weread_as_degraded_cached_read(self):
        source = {
            "registry_id": "reg_werss_wechat_watchlist",
            "adapter": "werss_api",
            "source_name": "WeRSS 微信公众号订阅",
            "source_type": "wechat_official_account",
            "publisher": None,
            "url": "http://127.0.0.1:8001",
            "watchlist": str(Path(__file__).parents[1] / "collector/wechat_watchlist.v0.1.json"),
            "password_env": "WERSS_PASSWORD",
            "refresh_before_read": True,
            "refresh_channel": "weread",
            "verify_refresh_auth": True,
            "page_size": 100,
            "max_scan": 100,
            "language": "zh-CN",
        }
        saved = os.environ.get("WERSS_PASSWORD")
        os.environ["WERSS_PASSWORD"] = "test-only"

        def fake_request(base_url, path, **kwargs):
            if path.endswith("/auth/login"):
                return {"access_token": "token"}
            if path.startswith("/api/v1/wx/mps?"):
                return {"list": [{"id": "MP_WXS_1", "mp_name": "机器之心"}]}
            if path.endswith("/weread/mp/test"):
                raise RuntimeError("WeRSS API error: Cookie expired")
            if path.startswith("/api/v1/wx/articles?"):
                return {"list": [{
                    "mp_id": "MP_WXS_1", "mp_name": "机器之心",
                    "title": "缓存中的视频生成文章", "description": "缓存摘要",
                    "url": "https://mp.weixin.qq.com/s/cached",
                    "publish_time": int(datetime(2026, 8, 10, tzinfo=timezone.utc).timestamp()),
                }], "total": 1}
            raise AssertionError(path)

        try:
            with patch.object(MODULE, "werss_request_json", side_effect=fake_request):
                records, health = MODULE.collect_werss(source, self.ctx)
        finally:
            if saved is None:
                os.environ.pop("WERSS_PASSWORD", None)
            else:
                os.environ["WERSS_PASSWORD"] = saved
        self.assertEqual(len(records), 1)
        self.assertEqual(health["status"], "degraded")
        self.assertTrue(health["stale_cache_only"])
        self.assertEqual(health["refresh_attempts"], 0)
        self.assertIn("cached", health["warning"])

    def test_wechat_and_financial_watchlists_preserve_required_scope(self):
        root = Path(__file__).parents[1] / "collector"
        wechat = json.loads((root / "wechat_watchlist.v0.1.json").read_text(encoding="utf-8"))
        finance = json.loads((root / "financial_public_report_watchlist.v0.1.json").read_text(encoding="utf-8"))
        account_names = {item["account_name"] for item in wechat["accounts"]}
        company_names = {item["name"] for item in finance["companies"]}
        self.assertEqual(len(account_names), 11)
        self.assertTrue({"机器之心", "新智元", "量子位", "晚点LatePost", "AI科技评论"} <= account_names)
        self.assertIn("数字生命卡兹克", account_names)
        self.assertTrue({"Alibaba Group", "Tencent", "Kuaishou", "Alphabet", "Adobe"} <= company_names)

    def test_official_ir_index_keeps_focus_context_separate_from_evidence(self):
        source = {
            "registry_id": "reg_ir_test",
            "adapter": "official_ir_index",
            "source_name": "Example Investor Relations",
            "source_type": "financial_report",
            "publisher": "Example",
            "url": "https://example.com/investors",
            "layout": "dated_links",
            "title_include_terms": ["results"],
            "focus_terms": ["video generation", "robotics"],
            "language": "en",
        }
        page = b'''<article><a href="/q2-results.pdf">Example Announces Second Quarter Results</a>
        <span>August 10, 2026</span></article>'''
        with patch.object(MODULE, "fetch_bytes", return_value=page):
            records, health = MODULE.collect_official_ir_index(source, self.ctx)
        self.assertEqual(health["status"], "success")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["source_type"], "financial_report")
        self.assertEqual(records[0]["canonical_url"], "https://example.com/q2-results.pdf")
        self.assertNotIn("video generation", records[0]["raw_excerpt"])
        self.assertIn("watchlist_focus:video generation,robotics", records[0]["discovery_context"])

    def test_official_ir_date_prefers_nearest_human_date_over_url_date(self):
        page = (
            '<a href="https://example.com/uploads/2026/06/report.pdf">Tencent Announces Results</a>'
            '<span>August 12, 2026</span>'
        )
        start = page.index("<a")
        end = page.index("</a>") + 4
        self.assertEqual(MODULE._nearby_date(page, start, end).date().isoformat(), "2026-08-12")

    def test_alibaba_ssr_ir_index_reads_embedded_official_records(self):
        source = {
            "registry_id": "reg_ir_alibaba",
            "adapter": "official_ir_index",
            "source_name": "Alibaba Group Investor Relations",
            "source_type": "financial_report",
            "publisher": "Alibaba Group",
            "url": "https://home.alibabagroup.com/en-US/ir-news-filings",
            "layout": "alibaba_ssr",
            "title_include_terms": ["results"],
            "focus_terms": ["Wan"],
            "language": "en",
        }
        timestamp = int(datetime(2026, 8, 10, tzinfo=timezone.utc).timestamp() * 1000)
        page = ('{"documentId":"123","documentPublishTime":' + str(timestamp)
                + ',"documentTitle":"Alibaba Group Announces June Quarter 2026 Results"}').encode()
        with patch.object(MODULE, "fetch_bytes", return_value=page):
            records, health = MODULE.collect_official_ir_index(source, self.ctx)
        self.assertEqual(health["status"], "success")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["canonical_url"], "https://home.alibabagroup.com/en-US/document-123")

    def test_feed_url_rewrite_supports_bbc_extractor_pattern(self):
        source = {
            "link_replacements": [{
                "from": "https://www.bbc.co.uk/news/articles/",
                "to": "https://www.bbc.com/news/articles/",
            }]
        }
        self.assertEqual(
            MODULE.rewrite_discovered_url(
                "https://www.bbc.co.uk/news/articles/cp30pz8d09jo?at_medium=RSS",
                source,
            ),
            "https://www.bbc.com/news/articles/cp30pz8d09jo?at_medium=RSS",
        )

    def test_empty_social_inbox_is_a_successful_health_check(self):
        with tempfile.TemporaryDirectory() as directory:
            inbox = Path(directory) / "inbox.json"
            inbox.write_text('{"records": []}', encoding="utf-8")
            source = {
                "registry_id": "reg_inbox",
                "adapter": "news_extractor_inbox",
                "source_name": "Social inbox",
                "source_type": "social_post",
                "publisher": None,
                "url": "https://mp.weixin.qq.com/",
                "input_file": str(inbox),
                "language": "zh-CN",
            }
            records, health = MODULE.collect_news_extractor_inbox(source, self.ctx)
        self.assertEqual(records, [])
        self.assertEqual(health["status"], "success")
        self.assertEqual(health["attempted"], 0)

    def test_batch_index_emits_individual_stories_at_window_boundary(self):
        source = {
            "registry_id": "reg_batch",
            "adapter": "batch_index",
            "source_name": "The Batch",
            "source_type": "professional_view",
            "publisher": "DeepLearning.AI",
            "url": "https://www.deeplearning.ai/the-batch/tag/the-batch",
            "language": "en",
        }
        ctx = MODULE.Context(
            datetime(2026, 8, 14, 8, tzinfo=timezone.utc),
            datetime(2026, 8, 7, 8, tzinfo=timezone.utc),
            datetime(2026, 8, 14, 8, tzinfo=timezone.utc),
            None,
        )
        listing = (
            r'\"title\":\"Issue title\" '
            r'\"excerpt\":\"Issue excerpt\" '
            r'\"href\":\"/the-batch/issue-365\" '
            r'\"date\":\"August 7, 2026\"'
        )
        issue = """
          <meta property="article:published_time" content="2026-08-07T07:00:00-07:00">
          <meta name="author" content="The Batch">
          <h1 id="news">News</h1>
          <h1 id="a-visual-world-model-story">A Visual World Model Story</h1>
          <p>A new model simulates video and physical environments.</p>
          <p><strong>What's new:</strong> It adds controllable motion.</p>
        """

        def fake_fetch(url):
            if url == source["url"]:
                return listing.encode()
            if url.endswith("issue-365"):
                return issue.encode()
            raise AssertionError(url)

        with patch.object(MODULE, "fetch_bytes", side_effect=fake_fetch):
            records, health = MODULE.collect_batch_index(source, ctx, [])
        self.assertEqual(health["issues_in_window"], 1)
        self.assertEqual(health["story_sections"], 1)
        self.assertEqual(health["accepted"], 1)
        self.assertEqual(records[0]["raw_title"], "A Visual World Model Story")
        self.assertEqual(records[0]["canonical_url"], "https://www.deeplearning.ai/the-batch/issue-365#a-visual-world-model-story")
        self.assertEqual(records[0]["published_at"], "2026-08-07T14:00:00Z")

    def test_batch_business_item_list_emits_article_records(self):
        source = {
            "registry_id": "reg_batch_business",
            "adapter": "batch_index",
            "source_name": "The Batch Business",
            "source_type": "professional_view",
            "publisher": "DeepLearning.AI",
            "url": "https://www.deeplearning.ai/the-batch/tag/business",
            "language": "en",
            "max_scan": 10,
        }
        listing = """
          <script type="application/ld+json">
          {"@type":"CollectionPage","mainEntity":{"@type":"ItemList","itemListElement":[
            {"@type":"ListItem","position":1,"url":"https://www.deeplearning.ai/the-batch/video-model-business","name":"Video Model Business"}
          ]}}
          </script>
        """
        article = """
          <meta property="article:published_time" content="2026-08-10T08:00:00Z">
          <meta property="article:author" content="DeepLearning.AI">
          <meta property="og:title" content="Video Model Business">
          <meta property="og:description" content="A company released a new video generation product.">
        """

        def fake_fetch(url):
            return listing.encode() if url == source["url"] else article.encode()

        with patch.object(MODULE, "fetch_bytes", side_effect=fake_fetch):
            records, health = MODULE.collect_batch_index(source, self.ctx, ["video"])
        self.assertEqual(health["listing_mode"], "business_item_list")
        self.assertEqual(health["accepted"], 1)
        self.assertEqual(records[0]["raw_title"], "Video Model Business")
        self.assertEqual(records[0]["published_at"], "2026-08-10T08:00:00Z")


if __name__ == "__main__":
    unittest.main()
