import json
import unittest

from editorial.editorial_writer import (
    apply_revision_patches, deep_story_readiness, input_for_event, source_text,
    validate_draft, write_drafts,
)


class EditorialWriterTest(unittest.TestCase):
    @staticmethod
    def supported_plan(attribution_required=False):
        return {"reader_packet": {
            "allowed_judgment": "该事件对视频模型产品化具有参考价值。",
            "fact_units": [
                {
                    "claim_id": "clm_1",
                    "text": "公司文章称新模型支持十秒视频生成，并说明该能力面向既有产品工作流；文章同时明确了该功能属于本次产品发布范围。",
                    "atomic_units": ["新模型支持十秒视频生成"],
                    "evidence_context": "来源提供了对应能力说明。",
                    "attribution_required": attribution_required,
                },
                {
                    "claim_id": "clm_2",
                    "text": "公司文章介绍了该模型的输入方式、生成环节与输出范围，并说明这些环节均属于已经发布的产品能力和当前使用范围。",
                    "atomic_units": ["文章介绍模型工作方式"],
                    "evidence_context": "来源解释了模型工作方式。",
                    "attribution_required": False,
                },
                {
                    "claim_id": "clm_3",
                    "text": "公司文章将该模型定位为现有视频产品线的一项新增能力，并给出了适用对象、产品使用范围和对应工作环节。",
                    "atomic_units": ["模型属于新增产品能力"],
                    "evidence_context": "来源说明了产品定位。",
                    "attribution_required": False,
                },
            ],
        }}

    @staticmethod
    def supported_draft(paragraph_text):
        return {
            "event_id": "evt_1",
            "headline": "公司发布新视频模型",
            "dek": "新模型支持十秒视频生成。",
            "one_line_takeaway": "十秒视频生成是本次发布的核心能力。",
            "factual_paragraphs": [
                {"text": paragraph_text, "claim_ids": ["clm_1"]},
                {"text": paragraph_text, "claim_ids": ["clm_1"]},
                {"text": paragraph_text, "claim_ids": ["clm_1"]},
            ],
            "judgment": "该事件对视频模型产品化具有参考价值。",
            "watch_next": [],
            "claim_support": {
                "headline": ["clm_1"], "dek": ["clm_1"],
                "one_line_takeaway": ["clm_1"], "judgment": ["clm_1"],
            },
        }

    def test_prefers_verified_text(self):
        text, basis = source_text({"verified_text": "人工核验正文", "raw_text": "抓取正文"})
        self.assertEqual(text, "人工核验正文")
        self.assertEqual(basis, "verified_text")

    def test_model_input_physically_excludes_audit_packet(self):
        event = {"event_id": "evt_1", "primary_source_id": "src_1"}
        plan = {
            "reader_packet": {
                "event_id": "evt_1",
                "event_at": "2026-08-28T00:00:00Z",
                "canonical_title": "旧标题",
                "primary_route": "frontier.video",
                "fact_units": [{
                    "claim_id": "clm_1", "text": "正式事实",
                    "evidence_context": "短证据片段", "source_locator": "内部定位",
                }],
                "allowed_judgment": "允许判断",
            },
            "audit_packet": {
                "limitations": "内部限制说明",
                "prohibited_extrapolations": ["内部禁写规则"],
            },
        }
        collection = {"source_records": [{
            "source_id": "src_1", "raw_text": "完整来源正文",
            "source_name": "官方来源", "canonical_url": "https://example.com",
        }]}

        payload = json.loads(input_for_event(event, plan, collection, "deep_story"))

        self.assertEqual(
            set(payload),
            {"event_id", "fact_units", "evidence_context", "allowed_judgment", "writing_profile"},
        )
        self.assertNotIn("audit_packet", payload)
        self.assertEqual(payload["evidence_context"], [{"claim_id": "clm_1", "text": "短证据片段"}])
        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("内部限制说明", serialized)
        self.assertNotIn("内部禁写规则", serialized)
        self.assertNotIn("完整来源正文", serialized)
        self.assertNotIn("source_text", serialized)
        self.assertNotIn("verified_text", serialized)
        self.assertNotIn("raw_text", serialized)
        self.assertNotIn("2026-08-28", serialized)
        self.assertNotIn("旧标题", serialized)
        self.assertNotIn("frontier.video", serialized)
        self.assertNotIn("内部定位", serialized)

    def test_model_input_accepts_raw_excerpt_because_only_locked_facts_are_visible(self):
        event = {"event_id": "evt_1", "primary_source_id": "src_1"}
        plan = {"reader_packet": {
            "fact_units": [{
                "claim_id": "clm_1", "text": "正式事实。",
                "atomic_units": ["正式事实"], "evidence_context": "证据片段",
            }],
            "allowed_judgment": "允许判断。",
        }}
        collection = {"source_records": [{"source_id": "src_1", "raw_excerpt": "来源摘要"}]}

        payload = json.loads(input_for_event(event, plan, collection, "deep_story"))

        self.assertEqual(payload["fact_units"][0]["text"], "正式事实。")
        self.assertNotIn("来源摘要", json.dumps(payload, ensure_ascii=False))

    def test_deep_story_readiness_accepts_three_substantive_verified_facts(self):
        facts = [
            {"claim_id": f"clm_{index}", "text": "这是一条经过确认且足够具体的正式事实内容。" * 3,
             "atomic_units": ["正式事实"], "evidence_context": "对应证据上下文"}
            for index in range(3)
        ]
        result = deep_story_readiness({"reader_packet": {
            "fact_units": facts, "allowed_judgment": "允许给出克制判断。",
        }})
        self.assertTrue(result["ready"])
        self.assertEqual(result["status"], "ready")

    def test_deep_story_readiness_does_not_require_editorial_judgment(self):
        facts = [
            {"claim_id": f"clm_{index}", "text": "这是一条经过确认且足够具体的正式事实内容。" * 3,
             "atomic_units": ["正式事实"], "evidence_context": "对应证据上下文"}
            for index in range(3)
        ]
        result = deep_story_readiness({"reader_packet": {
            "fact_units": facts, "allowed_judgment": "",
        }})
        self.assertTrue(result["ready"])

    def test_deep_story_readiness_demotes_sparse_event(self):
        result = deep_story_readiness({"reader_packet": {
            "fact_units": [{
                "claim_id": "clm_1", "text": "只有一条事实。",
                "atomic_units": ["只有一条事实"], "evidence_context": "一条证据",
            }],
            "allowed_judgment": "允许判断。",
        }})
        self.assertFalse(result["ready"])
        self.assertEqual(result["status"], "quick_read")
        self.assertIn("fewer_than_3_distinct_verified_claims", result["reasons"])

    def test_rejects_unknown_claim(self):
        event = {"event_id": "evt_1", "claim_ids": ["clm_1"]}
        draft = {
            "event_id": "evt_1",
            "factual_paragraphs": [
                {"text": "第一段事实内容用于解释事件背景和已经确认的变化。" * 3, "claim_ids": ["clm_unknown"]},
                {"text": "第二段事实内容用于补充产品构成和来源明确披露的信息。" * 3, "claim_ids": ["clm_1"]},
                {"text": "第三段事实内容用于说明适用范围以及来源给出的具体条件。" * 3, "claim_ids": ["clm_1"]},
            ],
            "judgment": "值得关注。",
        }
        with self.assertRaisesRegex(ValueError, "unknown claim"):
            validate_draft(draft, event, "来源正文")

    def test_rejects_audit_language_in_reader_copy(self):
        event = {"event_id": "evt_1", "claim_ids": ["clm_1"]}
        draft = {
            "event_id": "evt_1",
            "factual_paragraphs": [
                {"text": "该表述属于预测而非已验证结果。" * 5, "claim_ids": ["clm_1"]},
                {"text": "第二段事实内容用于补充产品构成和来源明确披露的信息。" * 3, "claim_ids": ["clm_1"]},
                {"text": "第三段事实内容用于说明适用范围以及来源给出的具体条件。" * 3, "claim_ids": ["clm_1"]},
            ],
            "judgment": "值得关注。",
        }
        with self.assertRaisesRegex(ValueError, "audit language"):
            validate_draft(draft, event, "来源正文")

    def test_rejects_lost_attribution(self):
        event = {"event_id": "evt_1", "claim_ids": ["clm_1"]}
        draft = self.supported_draft("新模型支持十秒视频生成，并适用于产品工作流。" * 3)
        with self.assertRaisesRegex(ValueError, "lost source attribution"):
            validate_draft(draft, event, "来源正文", self.supported_plan(attribution_required=True))

    def test_rejects_unsupported_inference_in_visible_field(self):
        event = {"event_id": "evt_1", "claim_ids": ["clm_1"]}
        paragraph = "据公司文章称，新模型支持十秒视频生成。" * 4
        draft = self.supported_draft(paragraph)
        draft["one_line_takeaway"] = "新模型将重塑视频生成行业。"
        with self.assertRaisesRegex(ValueError, "unsupported inference"):
            validate_draft(draft, event, "来源正文", self.supported_plan(attribution_required=True))

    def test_retries_once_then_accepts_targeted_attribution_revision(self):
        class FakeClient:
            def __init__(self, first, second):
                self.outputs = [first, second]
                self.calls = []

            def generate_json(self, **kwargs):
                self.calls.append(kwargs)
                return self.outputs.pop(0), {"model": "fake"}

        missing = self.supported_draft("新模型支持十秒视频生成。" * 4)
        attributed_text = "据公司文章称，新模型支持十秒视频生成。" * 4
        missing["factual_paragraphs"][1]["text"] = attributed_text
        missing["factual_paragraphs"][2]["text"] = attributed_text
        repaired_patch = {
            "event_id": "evt_1",
            "patches": [{
                "target": "factual_paragraph", "paragraph_index": 0,
                "text": attributed_text,
                "claim_ids": ["clm_1"],
            }],
        }
        client = FakeClient({"draft": missing}, {"revision": repaired_patch})
        verified = {
            "intelligence_events": [{
                "event_id": "evt_1", "primary_source_id": "src_1", "claim_ids": ["clm_1"],
                "priority": "priority.p0", "event_at": "2026-08-28T00:00:00Z",
            }],
            "editorial_selection": {"top_event_ids": ["evt_1"]},
        }
        plan = self.supported_plan(attribution_required=True)
        plan.update({"event_id": "evt_1"})
        result = write_drafts(
            verified, {"selections": [plan]},
            {"source_records": [{"source_id": "src_1", "raw_text": "来源正文" * 100}]},
            client=client,
        )
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(result["drafts"]), 1)
        self.assertEqual(result["attempt_log"][0]["outcome"], "passed_after_revision")
        self.assertEqual(
            result["drafts"][0]["factual_paragraphs"][1],
            missing["factual_paragraphs"][1],
        )
        retry = json.loads(client.calls[1]["input_text"])
        self.assertIn("lost source attribution", retry["revision"]["validation_error"])
        self.assertNotIn("raw_text", json.dumps(retry["locked_writer_input"], ensure_ascii=False))
        self.assertEqual(result["diagnostics"][0]["initial_draft"], missing)
        self.assertEqual(result["diagnostics"][0]["revision"], repaired_patch)
        self.assertEqual(result["diagnostics"][0]["outcome"], "passed_after_revision")

    def test_rejects_revision_patch_outside_failed_paragraph(self):
        draft = self.supported_draft("据文章称，新模型支持十秒视频生成。" * 4)
        revision = {
            "event_id": "evt_1",
            "patches": [{
                "target": "factual_paragraph", "paragraph_index": 1,
                "text": "试图修改未失败的段落。", "claim_ids": ["clm_1"],
            }],
        }
        error = "evt_1: factual_paragraph[0] attribution-required claim lost source attribution"
        with self.assertRaisesRegex(ValueError, "out-of-scope patch"):
            apply_revision_patches(draft, revision, error, "evt_1")


if __name__ == "__main__":
    unittest.main()
