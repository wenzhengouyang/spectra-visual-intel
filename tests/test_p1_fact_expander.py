import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from verification.p1_fact_expander import atomic_review, expand_bundle, has_attribution
from spectra_agent.run import WorkflowError, materialize_fact_decisions


class FakeClient:
    def __init__(self, facts):
        self.facts = facts
        self.calls = 0
        self.settings = type("Settings", (), {"model": "qwen3:8b"})()

    def generate_json(self, **kwargs):
        self.calls += 1
        return {"facts": self.facts}, {"model": "qwen3:8b"}


class FailingClient:
    settings = type("Settings", (), {"model": "qwen3:8b"})()

    def generate_json(self, **kwargs):
        raise AssertionError("unchanged source should reuse checkpoint")


def source_bundle():
    sentences = [f"Official evidence section {letter} describes a distinct product capability." for letter in "ABCDEFGH"]
    return {
        "source_records": [{
            "source_id": "src_1", "canonical_url": "https://example.com/source",
            "raw_text": " ".join(sentences),
        }]
    }, sentences


def evidence_bundle():
    return {"records": [{
        "candidate_id": "cand_1", "title": "Product release",
        "claim_reviews": [{
            "claim": "公司文章称，产品已经发布。", "kind": "reported_release",
            "source_id": "src_1", "source_url": "https://example.com/source",
            "locator": "existing", "evidence_text": "Official evidence section A describes a distinct product capability.",
        }],
    }]}


class P1FactExpanderTest(unittest.TestCase):
    def test_data_character_is_not_mistaken_for_source_attribution(self):
        self.assertFalse(has_attribution("系统连接企业数据并继承原有权限。"))
        collection, sentences = source_bundle()
        reviewed = atomic_review({
            "text": "系统连接企业数据并继承原有权限。",
            "kind": "reported_capability",
            "evidence_quote": sentences[0],
        }, collection["source_records"][0])
        self.assertTrue(reviewed["claim"].startswith("据来源报道，"))

    def test_expands_to_eight_source_backed_pending_human_facts(self):
        collection, sentences = source_bundle()
        details = ["合同审查", "权限连接", "代理执行", "治理控制", "监管扫描", "数据隔离", "合作生态", "预览发布"]
        facts = [
            {"text": f"公司文章称，{detail}已经列入对应的产品工作范围。", "kind": "reported_capability", "evidence_quote": sentence}
            for detail, sentence in zip(details, sentences)
        ]
        with tempfile.TemporaryDirectory() as temp:
            output = expand_bundle(
                evidence_bundle(), collection, FakeClient(facts), Path(temp) / "checkpoint.json"
            )
        reviews = output["records"][0]["claim_reviews"]
        self.assertGreaterEqual(len(reviews), 8)
        self.assertTrue(all(item["human_fact_decision"] == "pending" for item in reviews))
        self.assertEqual(output["fact_expansion_summary"]["completed"], 1)

    def test_reuses_checkpoint_only_when_source_text_is_unchanged(self):
        collection, _ = source_bundle()
        source_text = collection["source_records"][0]["raw_text"]
        cached_review = evidence_bundle()["records"][0]["claim_reviews"]
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint.json"
            checkpoint.write_text(json.dumps({
                "schema_version": "0.2",
                "record_type": "p1_fact_expansion_checkpoint",
                "prompt_version": "p1_fact_expander.v0.2",
                "model": "qwen3:8b", "source_window": {"start": None, "end": None},
                "records": {"cand_1": {
                "status": "completed", "claim_reviews": cached_review,
                "fact_expansion": {"status": "completed", "supported_facts": 8},
                "input_fingerprint": hashlib.sha256(json.dumps({
                    "candidate_id": "cand_1",
                    "title": "Product release",
                    "existing_claims": ["公司文章称，产品已经发布。"],
                    "source_id": "src_1",
                    "source_text": source_text,
                }, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest(),
            }}}))
            output = expand_bundle(evidence_bundle(), collection, FailingClient(), checkpoint)
        self.assertEqual(output["records"][0]["fact_expansion"]["status"], "completed")

    def test_changed_existing_claim_invalidates_fact_checkpoint(self):
        collection, sentences = source_bundle()
        details = ["合同审查", "权限连接", "代理执行", "治理控制", "监管扫描", "数据隔离", "合作生态", "预览发布"]
        facts = [
            {
                "text": f"公司文章称，{detail}已经列入对应的产品工作范围。",
                "kind": "reported_capability",
                "evidence_quote": sentence,
            }
            for detail, sentence in zip(details, sentences)
        ]
        with tempfile.TemporaryDirectory() as temp:
            checkpoint = Path(temp) / "checkpoint.json"
            first = FakeClient(facts)
            expand_bundle(evidence_bundle(), collection, first, checkpoint)
            changed_evidence = evidence_bundle()
            changed_evidence["records"][0]["claim_reviews"][0]["claim"] = "公司文章称，产品处于预览阶段。"
            second = FakeClient(facts)
            expand_bundle(changed_evidence, collection, second, checkpoint)
        self.assertGreater(first.calls, 0)
        self.assertGreater(second.calls, 0)

    def test_fact_decisions_materialize_only_explicitly_approved_claims(self):
        review = {"records": [{
            "candidate_id": "cand_1", "decision": "include", "claims": [],
            "approve_all_suggested_facts": False,
            "suggested_evidence": [
                {"claim": "公司称事实一。", "kind": "reported_fact", "locator": "L1", "evidence_text": "E1", "human_fact_decision": "keep"},
                {"claim": "旧事实二。", "kind": "reported_fact", "locator": "L2", "evidence_text": "E2", "human_fact_decision": "modify", "human_fact_text": "公司称修订事实二。", "human_fact_kind": "reported_fact"},
                {"claim": "公司称事实三。", "kind": "reported_fact", "locator": "L3", "evidence_text": "E3", "human_fact_decision": "drop"},
            ],
        }]}
        result = materialize_fact_decisions(review)
        self.assertEqual([item["text"] for item in result["records"][0]["claims"]], ["公司称事实一。", "公司称修订事实二。"])

    def test_pending_fact_blocks_resume(self):
        review = {"records": [{
            "candidate_id": "cand_1", "decision": "include", "claims": [],
            "suggested_evidence": [{"human_fact_decision": "pending"}],
        }]}
        with self.assertRaisesRegex(WorkflowError, "fact-level review incomplete"):
            materialize_fact_decisions(review)


if __name__ == "__main__":
    unittest.main()
