import importlib.util
import json
import sys
import unittest
from unittest import mock
from pathlib import Path


ROOT = Path(__file__).parents[1]
SPEC = importlib.util.spec_from_file_location("spectra_structure", ROOT / "processor" / "structure.py")
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
CONFIG = json.loads((ROOT / "processor" / "config.v0.1.json").read_text())
MODULE.CONFIG = CONFIG


def record(title, name="arXiv", source_type="paper_report", sid="src_test"):
    return {"source_id": sid, "raw_title": title, "raw_excerpt": "", "source_name": name,
            "source_type": source_type, "publisher": name, "published_at": "2026-08-10T00:00:00Z",
            "canonical_url": f"https://example.com/{sid}"}


class ProcessorTest(unittest.TestCase):
    def test_vertical_embodied_application_is_demoted_not_deleted(self):
        result = MODULE.score_record(record("Surgical World Model for Robot Learning"), CONFIG)
        self.assertEqual(result["hard_exclude"], [])
        self.assertIn("surgical", result["soft_negative_hits"])

    def test_irrelevant_domain_is_hard_excluded(self):
        result = MODULE.score_record(record("Economic World Model for Financial Agents"), CONFIG)
        self.assertIn("economic world model", result["hard_exclude"])

    def test_video_evaluation_is_routed_to_evaluation(self):
        result = MODULE.score_record(record("A Benchmark for Physical Fidelity in Video Generation"), CONFIG)
        self.assertEqual(result["primary_route"], "visual_value.evaluation")
        self.assertGreaterEqual(result["score"], CONFIG["minimum_score"])

    def test_abstract_does_not_override_title_primary_route(self):
        item = record("ComBodied Agents: a New Paradigm of Human-Centric Agentic AI")
        item["raw_excerpt"] = "We present evaluation benchmarks and metrics."
        result = MODULE.score_record(item, CONFIG)
        self.assertEqual(result["primary_route"], "frontier.embodied_ai")

    def test_github_commits_are_one_event(self):
        items = [MODULE.score_record(record("ready for open source", "Wan-Animate-2 GitHub Commits", "code_dataset", "src_a"), CONFIG),
                 MODULE.score_record(record("Update README.md", "Wan-Animate-2 GitHub Commits", "code_dataset", "src_b"), CONFIG)]
        candidates = MODULE.aggregate(items)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["aggregation"]["source_count"], 2)

    def test_near_duplicate_titles_collapse(self):
        a = MODULE.score_record(record("VideoArgus: Unified Evaluation for Video Generation", sid="src_a"), CONFIG)
        b = MODULE.score_record(record("VideoArgus Unified Evaluation for Video Generation", sid="src_b"), CONFIG)
        candidates = MODULE.aggregate([a, b])
        self.assertEqual(len(candidates), 1)

    def test_financial_report_is_industry_market(self):
        item = record("Quarterly earnings show video generation revenue growth", source_type="financial_report")
        item["raw_excerpt"] = "The company reports revenue, customer adoption and commercial growth."
        scored = MODULE.score_record(item, CONFIG)
        candidate = MODULE.aggregate([scored])[0]
        self.assertEqual(candidate["intelligence_type"], "type.industry_market")

    def test_official_product_release_is_product_release(self):
        item = record("Company launches a new video generation API", name="Official", source_type="official_announcement")
        item["raw_excerpt"] = "The product release includes pricing and a new controllable video feature."
        scored = MODULE.score_record(item, CONFIG)
        candidate = MODULE.aggregate([scored])[0]
        self.assertEqual(candidate["intelligence_type"], "type.product_release")

    def test_company_strategy_is_separate_from_product_release(self):
        item = record("Video generation company announces acquisition and ecosystem strategy", source_type="official_announcement")
        item["raw_excerpt"] = "The video generation company announced an acquisition, investment plan and organization strategy."
        scored = MODULE.score_record(item, CONFIG)
        candidate = MODULE.aggregate([scored])[0]
        self.assertEqual(candidate["intelligence_type"], "type.company_strategy")

    def test_paper_benchmark_is_technology_breakthrough(self):
        item = record("A Benchmark for Physical Fidelity in Video Generation")
        scored = MODULE.score_record(item, CONFIG)
        candidate = MODULE.aggregate([scored])[0]
        self.assertEqual(candidate["intelligence_type"], "type.technology_breakthrough")

    def test_llm_enrichment_is_schema_checked_and_attached(self):
        source = record("A Benchmark for Physical Fidelity in Video Generation")
        source.update({"access_status": "success", "raw_text": None})
        source_payload = {"source_records": [source]}
        candidate = {
            "candidate_id": "cand_test", "canonical_title": source["raw_title"],
            "intelligence_type": "type.technology_breakthrough",
            "intelligence_type_signals": ["source_type:paper_report"],
            "primary_route": "visual_value.evaluation", "secondary_routes": [],
            "track": "track.emerging", "score": 14, "matched_signals": ["benchmark"],
            "source_ids": [source["source_id"]],
        }
        result = {"selected_candidates": [candidate], "summary": {}}

        class FakeClient:
            def generate_json(self, **kwargs):
                self.request = kwargs
                return {"analyses": [{
                    "candidate_id": "cand_test", "canonical_title": source["raw_title"],
                    "intelligence_type": "type.technology_breakthrough",
                    "intelligence_type_reason": "来源是直接介绍新评测方法的论文摘要。",
                    "primary_route": "visual_value.evaluation", "secondary_routes": [],
                    "track": "track.emerging", "same_event_group": "cand_test",
                    "what": "发布视频生成物理真实性评测。", "why": "使机制错误可以被重复测量。",
                    "importance_score": 80, "novelty_score": 70, "strategy_relevance_score": 85,
                    "confidence": "medium", "proposed_claims": ["发布了新评测"],
                    "missing_evidence": ["缺少跨模型复现"], "verification_questions": ["指标如何定义？"],
                    "recommended_disposition": "p1", "source_ids": [source["source_id"]],
                    "disposition_reason": "与评测策略直接相关，但仍需原文核验指标。",
                }]}, {"provider": "mock", "model": "mock-model", "response_id": "resp_test", "usage": {}}

        enriched = MODULE.enrich_with_llm(
            result, source_payload, client=FakeClient(), max_candidates=1, prompt_version="test.v1"
        )
        self.assertEqual(enriched["llm"]["status"], "completed")
        self.assertEqual(enriched["selected_candidates"][0]["llm_analysis"]["recommended_disposition"], "p1")
        self.assertEqual(enriched["selected_candidates"][0]["intelligence_type"], "type.technology_breakthrough")

    def test_llm_rejects_unknown_source_reference(self):
        selected = [{"candidate_id": "cand_test", "source_ids": ["src_allowed"]}]
        payload = {"analyses": [{
            "candidate_id": "cand_test", "source_ids": ["src_hallucinated"],
            "intelligence_type": "type.technology_breakthrough",
            "intelligence_type_reason": "论文介绍技术方法。",
        }]}
        analyses = MODULE.validate_llm_analyses(payload, selected)
        self.assertEqual(analyses[0]["source_ids"], ["src_allowed"])

    def test_missing_api_key_is_explicit(self):
        from spectra_agent.llm_client import LLMConfigurationError, LLMSettings
        with mock.patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(LLMConfigurationError, "OPENAI_API_KEY is missing"):
                LLMSettings.from_environment(load_env_file=False)

    def test_openai_adapter_uses_responses_json_schema(self):
        from spectra_agent.llm_client import LLMSettings, OpenAIResponsesClient

        class Usage:
            def model_dump(self):
                return {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}

        class Response:
            id = "resp_mock"
            model = "gpt-mock"
            output_text = '{"analyses": []}'
            usage = Usage()

        class Responses:
            def create(self, **kwargs):
                self.kwargs = kwargs
                return Response()

        adapter = OpenAIResponsesClient.__new__(OpenAIResponsesClient)
        adapter.settings = LLMSettings("dummy", "gpt-mock", 10, 0)
        responses = Responses()
        adapter._client = type("FakeOpenAI", (), {"responses": responses})()
        payload, metadata = adapter.generate_json(
            instructions="test", input_text="{}", schema_name="test_schema",
            schema={"type": "object", "properties": {}, "additionalProperties": False},
        )
        self.assertEqual(payload, {"analyses": []})
        self.assertEqual(responses.kwargs["text"]["format"]["type"], "json_schema")
        self.assertTrue(responses.kwargs["text"]["format"]["strict"])
        self.assertEqual(metadata["response_id"], "resp_mock")

    def test_openai_adapter_reports_exhausted_quota_cleanly(self):
        from spectra_agent.llm_client import LLMProviderError, LLMSettings, OpenAIResponsesClient

        class QuotaError(Exception):
            status_code = 429
            body = {"error": {"code": "credit_balance_exhausted"}}

        class Responses:
            def create(self, **kwargs):
                raise QuotaError()

        adapter = OpenAIResponsesClient.__new__(OpenAIResponsesClient)
        adapter.settings = LLMSettings("dummy", "gpt-mock", 10, 0)
        adapter._client = type("FakeOpenAI", (), {"responses": Responses()})()
        with self.assertRaisesRegex(LLMProviderError, "no available quota") as caught:
            adapter.generate_json(
                instructions="test", input_text="{}", schema_name="test_schema",
                schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.code, "credit_balance_exhausted")

    def test_ollama_adapter_uses_local_chat_and_json_schema(self):
        from spectra_agent.llm_client import OllamaChatClient, OllamaSettings

        raw = json.dumps({
            "model": "qwen3:8b", "message": {"content": '{"analyses": []}'},
            "prompt_eval_count": 12, "eval_count": 4, "total_duration": 100,
        }).encode()

        class Response:
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self): return raw

        adapter = OllamaChatClient(OllamaSettings("qwen3:8b", "http://127.0.0.1:11434", 10, 8192, False))
        with mock.patch("spectra_agent.llm_client.urlopen", return_value=Response()) as call:
            payload, metadata = adapter.generate_json(
                instructions="test", input_text="{}", schema_name="test_schema",
                schema={"type": "object", "properties": {}, "additionalProperties": False},
            )
        sent = json.loads(call.call_args.args[0].data.decode())
        self.assertFalse(sent["stream"])
        self.assertEqual(sent["format"]["type"], "object")
        self.assertEqual(sent["options"]["num_ctx"], 8192)
        self.assertFalse(sent["think"])
        self.assertEqual(metadata["provider"], "ollama")
        self.assertEqual(metadata["usage"]["total_tokens"], 16)

    def test_client_factory_selects_ollama_without_api_key(self):
        from spectra_agent.llm_client import OllamaChatClient, create_llm_client
        with mock.patch.dict("os.environ", {
            "SPECTRA_LLM_PROVIDER": "ollama", "SPECTRA_MODEL": "qwen3:8b",
            "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        }, clear=True):
            self.assertIsInstance(create_llm_client(), OllamaChatClient)


if __name__ == "__main__":
    unittest.main()
