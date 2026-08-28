import json
import unittest
from pathlib import Path

from editorial.fact_selection import build_fact_selection, validate_fact_selection


ROOT = Path(__file__).resolve().parents[1]


class FactSelectionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        run = ROOT / "spectra_agent" / "runs" / "harness-live-20260826"
        cls.verified = json.loads((run / "verified-events.json").read_text(encoding="utf-8"))
        cls.review = json.loads((run / "p1-review.json").read_text(encoding="utf-8"))
        cls.collection = json.loads((run / "collection.json").read_text(encoding="utf-8"))

    def test_builds_locked_whitelist_for_every_verified_event(self):
        bundle = build_fact_selection(self.verified, self.review, self.collection)
        validate_fact_selection(bundle, self.verified)
        self.assertTrue(bundle["locked"])
        self.assertEqual(len(bundle["selections"]), len(self.verified["intelligence_events"]))
        for item in bundle["selections"]:
            self.assertTrue(item["reader_packet"]["fact_units"])
            self.assertTrue(item["audit_packet"]["prohibited_extrapolations"])
            self.assertIn(item["audit_packet"]["publication_risk"]["level"], {"low", "medium", "high"})
            for fact in item["reader_packet"]["fact_units"]:
                self.assertTrue(fact["atomic_units"])
                self.assertIn("numeric_mentions", fact)

    def test_rejects_unknown_claim_in_selection(self):
        bundle = build_fact_selection(self.verified, self.review, self.collection)
        bundle["selections"][0]["reader_packet"]["fact_units"][0]["claim_id"] = "clm_unknown"
        with self.assertRaisesRegex(ValueError, "unknown claims"):
            validate_fact_selection(bundle, self.verified)

    def test_embedded_audit_commentary_is_not_exposed_to_reader_packet(self):
        bundle = build_fact_selection(self.verified, self.review, self.collection)
        unitree = next(
            item for item in bundle["selections"]
            if item["event_id"] == "evt_20260820_unitree_evolution"
        )
        reader_json = json.dumps(unitree["reader_packet"], ensure_ascii=False)
        self.assertNotIn("该表述属于预测而非已验证结果", reader_json)
        self.assertNotIn("保留归因和预测边界", reader_json)
        self.assertTrue(unitree["audit_packet"]["embedded_claim_audit_notes"])
        self.assertEqual(
            unitree["audit_packet"]["embedded_claim_audit_notes"][0]["claim_id"],
            "clm_dd89be07c43d07e4_05",
        )


if __name__ == "__main__":
    unittest.main()
