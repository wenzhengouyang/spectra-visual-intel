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


if __name__ == "__main__":
    unittest.main()
