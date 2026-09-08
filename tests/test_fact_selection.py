import json
import unittest

from editorial.fact_selection import build_fact_selection, split_judgment_layers, validate_fact_selection


class FactSelectionTest(unittest.TestCase):
    def test_human_review_confirmation_never_becomes_reader_judgment(self):
        reader, audit = split_judgment_layers("人工确认事实与证据可用。")
        self.assertEqual(reader, "")
        self.assertEqual(audit, ["人工确认事实与证据可用"])

    def test_interactive_review_reason_never_becomes_reader_judgment(self):
        reader, audit = split_judgment_layers("人工核验原始来源与事实证据后保留。")
        self.assertEqual(reader, "")
        self.assertEqual(audit, ["人工核验原始来源与事实证据后保留"])

    def test_explicit_reader_judgment_is_kept_separate_from_review_reason(self):
        review = json.loads(json.dumps(self.review, ensure_ascii=False))
        review["records"][0]["decision_reason"] = "人工核验原始来源与事实证据后保留。"
        review["records"][0]["reader_judgment"] = "该产品把动作测试与视觉质量放进同一评估流程。"
        bundle = build_fact_selection(self.verified, review, self.collection)
        reader = bundle["selections"][0]["reader_packet"]
        self.assertEqual(reader["allowed_judgment"], "该产品把动作测试与视觉质量放进同一评估流程。")

    @classmethod
    def setUpClass(cls):
        event_id = "evt_20260820_unitree_evolution"
        claim_id = "clm_dd89be07c43d07e4_05"
        claim_text = "据公司披露，系统完成了动作测试；该表述属于预测而非已验证结果。"
        cls.verified = {
            "verified_at": "2026-08-20T00:00:00Z",
            "evidence_claims": [{
                "claim_id": claim_id, "claim_text": claim_text,
                "claim_kind": "reported_fact", "source_id": "src_unitree",
                "source_locator": "正文第1段", "quote_excerpt": "系统完成了动作测试",
            }],
            "intelligence_events": [{
                "event_id": event_id, "canonical_title": "机器人动作测试",
                "event_at": "2026-08-20T00:00:00Z", "primary_route": "具身智能",
                "primary_source_id": "src_unitree", "claim_ids": [claim_id],
                "priority": "priority.p1", "confidence": "confidence.medium",
                "independent_source_count": 1,
            }],
        }
        cls.review = {"records": [{
            "decision": "include", "event": {"event_id": event_id},
            "decision_reason": "该事件值得继续观察。保留归因和预测边界。",
            "limitation": "单一公司来源，尚需独立验证。",
            "suggested_evidence": [{"claim": claim_text, "evidence_text": "系统完成了动作测试"}],
        }]}
        cls.collection = {
            "window_start": "2026-08-14T00:00:00Z", "window_end": "2026-08-20T23:59:59Z",
            "source_records": [{
                "source_id": "src_unitree", "source_name": "公司公告",
                "canonical_url": "https://example.com/unitree", "verified_text": "系统完成了动作测试。",
            }],
        }

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
