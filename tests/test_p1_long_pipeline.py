import unittest
import tempfile
from pathlib import Path

from editorial.p1_long_pipeline import build_bundle
from editorial.diagnostic_long_writer import audit_article, backfill_verified_facts


class FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def generate_json(self, **kwargs):
        self.calls += 1
        return self.result, {"model": "qwen3:14b", "usage": {"total_tokens": 100}}


class SequenceClient:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = 0

    def generate_json(self, **kwargs):
        result = self.results[self.calls]
        self.calls += 1
        return result, {"model": "qwen3:14b", "usage": {"total_tokens": 100}}


def verified(event_id="evt_1"):
    return {
        "editorial_selection": {"top_event_ids": [event_id]},
        "intelligence_events": [{"event_id": event_id}],
    }


def fact(index):
    subjects = ["法律技能", "系统连接", "专业代理", "治理控制", "访问权限", "合作律所", "数据政策", "预览发布"]
    details = [
        "覆盖合同审查、监管扫描与法律研究等任务，并执行机构自己的工作手册",
        "接入文档管理、案件资料库和研究服务，并继承原有系统权限",
        "处理政策研究、监管筛查和合同起草，并接受统一的平台治理",
        "执行安全策略、维护私有数据隔离，并为输出保留可追溯引用",
        "受到角色权限、文档级权限和电子取证系统的数据控制约束",
        "由多家国际律所参与开发，用于贴近复杂法律工作的实际流程",
        "让客户数据、知识产权和模型输出保持组织私有且不用于模型训练",
        "当前以预览形式提供，并与面向其他专业行业的方案共同推出",
    ]
    subject = subjects[index]
    return {
        "claim_id": f"clm_{index}",
        "text": f"公司文章称，{subject}{details[index]}，相关内容属于本次正式发布信息。",
        "evidence_context": f"官方来源提供了{subject}的对应证据。",
        "attribution_required": True,
    }


def selection(count=8):
    return {"selections": [{
        "event_id": "evt_1",
        "reader_packet": {
            "fact_units": [fact(index) for index in range(count)],
            "allowed_judgment": "该产品体现法律工作流与企业治理的组合落地。",
            "writing_profile": {
                "mode": "long_form", "min_fact_units": 8,
                "min_characters": 600, "max_characters": 1000,
            } if count >= 8 else {},
        },
    }]}


def valid_result():
    paragraphs = []
    closings = [
        "法律技能和系统连接共同界定了任务范围、资料来源和既有权限边界。",
        "专业代理和治理控制分别对应任务执行方式以及平台统一管理要求。",
        "访问权限与合作律所信息说明了数据控制约束和产品开发参与主体。",
        "数据政策与预览发布信息共同说明组织私有边界和当前提供状态。",
    ]
    closing_details = [
        "公司文章把可复用任务说明与可信企业数据同时列为产品的基础组成。",
        "公司文章同时强调代理执行的具体法律任务和底层安全治理要求。",
        "公司文章将权限继承与律所参与开发分别作为系统接入和行业适配信息。",
        "公司文章分别说明数据不会用于模型训练以及产品尚处预览阶段。",
    ]
    for pair_index, (left, right) in enumerate(((0, 1), (2, 3), (4, 5), (6, 7))):
        paragraphs.append(
            f"公司文章称，{fact(left)['text'].removeprefix('公司文章称，')}"
            f"据公司文章介绍，{fact(right)['text'].removeprefix('公司文章称，')}"
            + closings[pair_index] + closing_details[pair_index]
        )
    return {
        "headline": "公司文章介绍法律技能与企业治理产品",
        "dek": "公司文章称，该产品覆盖法律技能、系统连接、专业代理与治理控制。",
        "paragraphs": paragraphs,
        "judgment": "该产品体现法律工作流与企业治理的组合落地。",
    }


class P1LongPipelineTest(unittest.TestCase):
    def test_demotes_event_with_fewer_than_eight_human_verified_facts(self):
        client = FakeClient(valid_result())
        bundle, audit = build_bundle(verified(), selection(3), client)
        self.assertEqual(client.calls, 0)
        self.assertEqual(len(bundle["demoted"]), 1)
        self.assertEqual(bundle["demoted"][0]["target_article_type"], "quick_read")
        self.assertEqual(audit["records"], [])

    def test_serial_writer_emits_only_programmatically_audited_draft(self):
        client = FakeClient(valid_result())
        bundle, audit = build_bundle(verified(), selection(8), client)
        self.assertEqual(client.calls, 1)
        self.assertEqual(len(bundle["drafts"]), 1)
        self.assertFalse(bundle["blocked"])
        self.assertEqual(audit["records"][0]["status"], "passed")
        self.assertGreaterEqual(len(bundle["drafts"][0]["factual_paragraphs"]), 4)

    def test_failed_audit_demotes_to_quick_read(self):
        result = valid_result()
        result["paragraphs"] = ["公司文章称，无法支持的短句。"] * 4
        client = FakeClient(result)
        bundle, audit = build_bundle(verified(), selection(8), client)
        self.assertFalse(bundle["drafts"])
        self.assertFalse(bundle["blocked"])
        self.assertEqual(bundle["demoted"][0]["target_article_type"], "quick_read")
        self.assertEqual(bundle["demoted"][0]["reason"], "long_story_failed_after_max_attempts")
        self.assertEqual(audit["records"][0]["status"], "needs_review")

    def test_completed_checkpoint_is_reused_without_second_model_call(self):
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "jobs.json"
            first = FakeClient(valid_result())
            first_bundle, _ = build_bundle(verified(), selection(8), first, checkpoint, 2)
            second = FakeClient(valid_result())
            second_bundle, _ = build_bundle(verified(), selection(8), second, checkpoint, 2)
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 0)
        self.assertEqual(first_bundle["drafts"], second_bundle["drafts"])

    def test_second_attempt_patches_only_failed_paragraph(self):
        initial = valid_result()
        original_paragraphs = list(initial["paragraphs"])
        initial["paragraphs"][0] = (
            initial["paragraphs"][0]
            .replace("公司文章称，", "")
            .replace("据公司文章介绍，", "")
            .replace("公司文章", "产品说明")
        )
        revision = {
            "revision": {
                "event_id": "evt_1",
                "patches": [{
                    "target": "paragraph", "paragraph_index": 0,
                    "text": original_paragraphs[0],
                }],
            },
        }
        client = SequenceClient(initial, revision)
        bundle, audit = build_bundle(verified(), selection(8), client)
        self.assertEqual(client.calls, 2)
        self.assertEqual(len(bundle["drafts"]), 1)
        record = audit["records"][0]
        self.assertEqual(record["status"], "passed")
        self.assertEqual(record["initial_draft"]["paragraphs"][1:], record["draft"]["paragraphs"][1:])
        self.assertEqual(record["revision_history"][0]["patches"][0]["paragraph_index"], 0)

    def test_sentence_audit_rejects_unsupported_effect_claim(self):
        result = valid_result()
        result["paragraphs"][0] += "这将显著提升法律团队效率。"
        audit = audit_article(
            result, selection(8)["selections"][0]["reader_packet"]["fact_units"],
            selection(8)["selections"][0]["reader_packet"]["allowed_judgment"],
            selection(8)["selections"][0]["reader_packet"]["writing_profile"],
        )
        self.assertEqual(audit["status"], "needs_review")
        self.assertTrue(any("unsupported inference '显著'" in error for error in audit["errors"]))
        self.assertTrue(audit["sentence_claim_mapping"])

    def test_length_backfill_uses_verified_fact_units_only(self):
        draft = {
            "paragraphs": ["公司文章称，产品进入预览阶段。", "系统继承既有访问权限。"]
        }
        inserted = backfill_verified_facts(
            draft, {("paragraph", 0)}, [fact(index) for index in range(8)], 120, 500
        )
        self.assertTrue(inserted)
        self.assertGreaterEqual(len("\n".join(draft["paragraphs"])), 120)
        self.assertTrue(any(fact(index)["text"] in draft["paragraphs"][0] for index in range(8)))


if __name__ == "__main__":
    unittest.main()
