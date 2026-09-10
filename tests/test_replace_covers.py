import json
import tempfile
import unittest
from pathlib import Path

from spectra_agent.replace_covers import replace


class ReplaceCoversTest(unittest.TestCase):
    def test_rejected_cover_is_replaced_as_pending_and_history_is_kept(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new.png"
            source.write_bytes(b"new image")
            issue = {"editorial_stories": [{"story_id": "story-1", "headline": "新图像模型", "category": "基础模型与多模态", "cover_image": {"url": "assets/old.svg", "kind": "generated", "review_status": "rejected", "rejection_reason": "mismatch"}}]}
            (root / "editorial-issue.json").write_text(json.dumps(issue))
            (root / "image-review.json").write_text(json.dumps({"rejected_story_ids": ["story-1"]}))
            report = replace(root, {"story-1": source})
            updated = json.loads((root / "editorial-issue.json").read_text())
            cover = updated["editorial_stories"][0]["cover_image"]
            self.assertEqual(cover["review_status"], "pending")
            self.assertEqual(cover["previous_rejected_cover"]["rejection_reason"], "mismatch")
            self.assertEqual(report["status"], "replacement_pending_review")
            self.assertTrue((root / cover["url"]).is_file())

    def test_pending_cover_can_be_replaced_during_recovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "new.png"
            source.write_bytes(b"new image")
            issue = {"editorial_stories": [{"story_id": "story-1", "headline": "新图像模型", "category": "基础模型与多模态", "cover_image": {"url": "assets/old.svg", "kind": "generated", "review_status": "pending"}}]}
            (root / "editorial-issue.json").write_text(json.dumps(issue))
            report = replace(root, {"story-1": source})
            self.assertEqual(report["status"], "replacement_pending_review")


if __name__ == "__main__":
    unittest.main()
