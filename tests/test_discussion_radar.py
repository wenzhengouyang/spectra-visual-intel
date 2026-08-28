import json
import unittest
from pathlib import Path

from processor.discussion_radar import build_radar


ROOT = Path(__file__).resolve().parents[1]


class DiscussionRadarTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.watchlist = json.loads((ROOT / "collector/observer_watchlist.v0.1.json").read_text(encoding="utf-8"))

    def record(self, registry_id, source_id, title):
        return {
            "registry_id": registry_id,
            "source_id": source_id,
            "access_status": "success",
            "raw_title": title,
            "raw_excerpt": "",
            "raw_text": None,
            "canonical_url": f"https://example.com/{source_id}",
            "published_at": "2026-08-20T00:00:00Z",
        }

    def test_watchlist_size_and_no_personal_account(self):
        self.assertGreaterEqual(len(self.watchlist["observers"]), 30)
        self.assertLessEqual(len(self.watchlist["observers"]), 50)
        self.assertFalse(self.watchlist["policy"]["requires_personal_account"])

    def test_three_independent_observers_make_rising_signal(self):
        collection = {
            "window_start": "2026-08-13T00:00:00Z",
            "window_end": "2026-08-20T00:00:00Z",
            "source_records": [
                self.record("reg_rss_openai_news", "a", "New video generation model"),
                self.record("reg_rss_deepmind_news", "b", "Video generation evaluation"),
                self.record("reg_rss_simon_willison_blog", "c", "Testing a video model"),
            ],
        }
        radar = build_radar(collection, self.watchlist)
        signal = next(item for item in radar["signals"] if item["theme_id"] == "video_generation")
        self.assertEqual(signal["signal_level"], "rising")
        self.assertEqual(signal["independent_observers"], 3)
        self.assertEqual(signal["editorial_status"], "p2_signal_only")

    def test_single_observer_is_not_published_as_trend(self):
        collection = {
            "window_start": "2026-08-13T00:00:00Z",
            "window_end": "2026-08-20T00:00:00Z",
            "source_records": [self.record("reg_rss_simon_willison_blog", "a", "A new AI agent tool")],
        }
        radar = build_radar(collection, self.watchlist)
        self.assertFalse(any(item["theme_id"] == "agents_tools" for item in radar["signals"]))
        self.assertTrue(any(item["theme_id"] == "agents_tools" for item in radar["isolated_mentions"]))


if __name__ == "__main__":
    unittest.main()
