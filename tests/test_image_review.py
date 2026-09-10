import unittest

from spectra_agent.image_review import pending_covers


class ImageReviewTest(unittest.TestCase):
    def test_rejected_cover_remains_pending_for_rework(self):
        issue = {"editorial_stories": [{
            "story_id": "story-1",
            "cover_image": {"kind": "generated", "review_status": "rejected", "url": "assets/cover.jpg"},
        }]}
        pending = pending_covers(issue)
        self.assertEqual([item["story_id"] for item in pending], ["story-1"])

    def test_non_core_cover_is_not_sent_to_human_review(self):
        issue = {"editorial_stories": [{
            "story_id": "story-signal",
            "article_type": "brief",
            "headline": "行业信号",
            "cover_image": {"kind": "generated", "review_status": "pending", "url": "assets/cover.jpg"},
        }]}
        self.assertEqual(pending_covers(issue), [])


if __name__ == "__main__":
    unittest.main()
