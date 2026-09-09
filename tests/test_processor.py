import importlib.util
import json
import sys
import tempfile
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
    def test_wechat_without_full_text_fails_content_completeness_gate(self):
        item = record("实测飞猪 AI：真正的突围才刚刚开始？", name="AI科技评论", source_type="wechat_official_account")
        item["raw_excerpt"] = ""
        gate = MODULE.content_completeness_gate(item, CONFIG)
        self.assertEqual(gate["status"], "fail")

    def test_structured_paper_abstract_passes_content_completeness_gate(self):
        item = record("A new video generation benchmark")
        item["raw_excerpt"] = "We present a sufficiently detailed benchmark abstract with tasks, metrics, comparisons, limitations, and evaluation results."
        gate = MODULE.content_completeness_gate(item, CONFIG)
        self.assertEqual(gate["status"], "pass")

    def test_attribution_detection_requires_preserved_qualifier(self):
        self.assertTrue(MODULE.requires_attribution("Company says its model wins"))
        self.assertFalse(MODULE.preserves_attribution("该模型已经超过所有对手"))
        self.assertTrue(MODULE.preserves_attribution("公司称该模型超过对手"))

    def test_company_name_alone_is_not_attribution(self):
        self.assertFalse(MODULE.preserves_attribution("该公司模型已经超过对手"))

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

    def test_cross_publisher_same_event_rule_collapses_wechat_rewrites(self):
        titles = [
            "宇树上市，机器人公司进入新阶段",
            "宇树上市开盘后，王兴兴谈下一步",
            "Unitree IPO draws attention to humanoid robots",
        ]
        scored = [
            MODULE.score_record(record(title, name=f"公众号{index}", source_type="wechat_official_account", sid=f"src_unitree_{index}"), CONFIG)
            for index, title in enumerate(titles)
        ]
        candidates = MODULE.aggregate(scored)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["aggregation"]["method"], "same_event_rule")
        self.assertEqual(candidates[0]["aggregation"]["source_count"], 3)

    def test_financial_report_is_industry_market(self):
        item = record("Quarterly earnings show video generation revenue growth", source_type="financial_report")
        item["raw_excerpt"] = "The company reports revenue, customer adoption and commercial growth."
        scored = MODULE.score_record(item, CONFIG)
        candidate = MODULE.aggregate([scored])[0]
        self.assertEqual(candidate["intelligence_type"], "type.industry_market")

    def test_generic_financial_title_includes_issuer(self):
        item = record("2026 Interim Report", name="Kuaishou Technology HKEX Filings",
                      source_type="financial_report")
        item["publisher"] = "Kuaishou Technology"
        item["raw_excerpt"] = "Kling AI video generation revenue and company results."
        candidate = MODULE.aggregate([MODULE.score_record(item, CONFIG)])[0]
        self.assertEqual(candidate["canonical_title"], "快手科技 — 2026年中期报告")
        self.assertGreaterEqual(MODULE.score_record(item, CONFIG)["score"], 12)

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

    def test_extended_ai_routes_are_separate_from_visual_core(self):
        cases = {
            "Gemini launches a new multimodal model": "extended.foundation_multimodal",
            "Cursor launches a coding agent developer tool": "extended.ai_agent_tools",
            "New AI chip targets data center inference infrastructure": "extended.compute_data",
            "Hugging Face releases open-source model weights": "extended.open_source_ecosystem",
        }
        for index, (title, expected_route) in enumerate(cases.items()):
            with self.subTest(title=title):
                scored = MODULE.score_record(record(title, sid=f"src_extended_{index}"), CONFIG)
                candidate = MODULE.aggregate([scored])[0]
                self.assertEqual(candidate["primary_route"], expected_route)
                self.assertEqual(candidate["domain_scope"], "scope.ai_extended")

    def test_visual_route_remains_in_core_scope(self):
        scored = MODULE.score_record(record("A new video generation model", sid="src_visual_core"), CONFIG)
        candidate = MODULE.aggregate([scored])[0]
        self.assertEqual(candidate["domain_scope"], "scope.visual_core")

    def test_chinese_wechat_topics_are_routed_before_llm(self):
        cases = {
            "写2000字提示词，不如先一键生成3D白模！AI视频创作进入预演时代": "frontier.video_generation",
            "冷战核废墟变8吉瓦AI超级工厂！英伟达追加投资": "extended.compute_data",
            "实测飞猪 AI：真正的突围才刚刚开始？": "extended.ai_agent_tools",
        }
        for index, (title, expected_route) in enumerate(cases.items()):
            with self.subTest(title=title):
                item = record(title, name="公众号", source_type="wechat_official_account", sid=f"src_wechat_{index}")
                scored = MODULE.score_record(item, CONFIG)
                self.assertEqual(scored["primary_route"], expected_route)
                self.assertGreaterEqual(scored["score"], CONFIG["minimum_score"])

    def test_embodied_title_overrides_spurious_talent_route_from_mixed_article(self):
        candidate = {
            "canonical_title": "宇树机器狗被曝篡改电池标签以规避航空规定",
            "source_ids": ["src_unitree"],
        }
        source_map = {
            "src_unitree": {
                "raw_title": "宇树机器狗电池贴假标签；月之暗面启动IPO",
            }
        }
        analysis = {
            "primary_route": "extended.talent_organization",
            "secondary_routes": ["frontier.embodied_ai", "extended.compute_data"],
            "intelligence_type_reason": "混合文章还包含公司动态。",
        }
        corrected = MODULE.reconcile_llm_route(candidate, analysis, source_map)
        self.assertEqual(corrected["primary_route"], "frontier.embodied_ai")
        self.assertNotIn("extended.talent_organization", corrected["secondary_routes"])
        self.assertIn("标题主事件", corrected["intelligence_type_reason"])

    def test_embodied_company_hiring_remains_talent_route(self):
        candidate = {
            "canonical_title": "宇树机器人团队发布招聘计划",
            "source_ids": ["src_unitree_hiring"],
        }
        source_map = {
            "src_unitree_hiring": {"raw_title": "Unitree robotics hiring and talent plan"}
        }
        analysis = {
            "primary_route": "extended.talent_organization",
            "secondary_routes": [],
            "intelligence_type_reason": "来源讨论招聘与人才计划。",
        }
        unchanged = MODULE.reconcile_llm_route(candidate, analysis, source_map)
        self.assertEqual(unchanged["primary_route"], "extended.talent_organization")

    def test_wechat_source_cannot_become_p1_before_original_verification(self):
        candidate = {
            "score": 30,
            "track": "track.emerging",
            "source_types": ["wechat_official_account"],
            "selection_reason": "score_and_balanced_quota",
        }
        self.assertEqual(MODULE.candidate_priority(candidate, CONFIG), "priority.p2")

    def test_single_case_cannot_be_generalized_to_industry_trend(self):
        candidate = {"aggregation": {"source_count": 1}}
        analysis = {
            "canonical_title": "AI视频行业进入预演时代",
            "what": "AI视频行业进入预演时代。",
            "proposed_claims": ["3D白模正在普遍替代传统提示词。"],
        }
        self.assertTrue(MODULE.single_case_generalization_failed(candidate, analysis))
        analysis["what"] = "文章展示该平台的3D白模预演功能。"
        analysis["canonical_title"] = "该产品展示3D白模预演功能"
        analysis["proposed_claims"] = ["该产品提供3D白模预演功能。"]
        self.assertFalse(MODULE.single_case_generalization_failed(candidate, analysis))

    def test_uncertain_source_requires_attribution(self):
        self.assertTrue(MODULE.requires_attribution("神秘模型仿佛实现了自我进化"))
        self.assertTrue(MODULE.preserves_attribution("文章称模型可能支持跨本体协作"))

    def test_feed_preserves_relevant_wechat_minimum(self):
        config = dict(CONFIG)
        config["feed_count"] = 3
        config["feed_source_type_minimums"] = {"wechat_official_account": 2}
        candidates = [
            {"candidate_id": "paper", "canonical_title": "paper", "primary_route": "frontier.video_generation", "score": 20, "track": "track.emerging", "source_types": ["paper_report"], "selection_reason": "score_and_balanced_quota", "matched_signals": []},
            {"candidate_id": "wx1", "canonical_title": "wx1", "primary_route": "extended.ai_agent_tools", "score": 10, "track": "track.emerging", "source_types": ["wechat_official_account"], "matched_signals": []},
            {"candidate_id": "wx2", "canonical_title": "wx2", "primary_route": "frontier.embodied_ai", "score": 9, "track": "track.emerging", "source_types": ["wechat_official_account"], "matched_signals": []},
            {"candidate_id": "media", "canonical_title": "media", "primary_route": "extended.ai_agent_tools", "score": 15, "track": "track.emerging", "source_types": ["industry_media"], "matched_signals": []},
        ]
        feed = MODULE.build_feed_candidates(candidates, config)
        self.assertEqual(len(feed), 3)
        self.assertEqual(sum("wechat_official_account" in item["source_types"] for item in feed), 2)

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

    def test_llm_checkpoint_reuse_does_not_resend_completed_candidate(self):
        sources = [record("First", sid="src_first"), record("Second", sid="src_second")]
        source_payload = {"source_records": sources}

        def candidate(name):
            return {
                "candidate_id": f"cand_{name}", "canonical_title": name,
                "intelligence_type": "type.technology_breakthrough",
                "intelligence_type_signals": ["source_type:paper_report"],
                "primary_route": "visual_value.evaluation", "secondary_routes": [],
                "track": "track.emerging", "score": 14, "matched_signals": ["benchmark"],
                "source_ids": [f"src_{name}"],
            }

        selected = [candidate("first"), candidate("second")]

        def analysis(item):
            return {
                "candidate_id": item["candidate_id"], "canonical_title": item["canonical_title"],
                "intelligence_type": "type.technology_breakthrough",
                "intelligence_type_reason": "论文介绍了可核验的技术方法。",
                "primary_route": "visual_value.evaluation", "secondary_routes": [],
                "track": "track.emerging", "same_event_group": item["candidate_id"],
                "what": "发布了新的评测方法。", "why": "可用于后续原文核验。",
                "importance_score": 60, "novelty_score": 60, "strategy_relevance_score": 60,
                "confidence": "medium", "proposed_claims": ["发布了评测方法"],
                "missing_evidence": ["需核验原文"], "verification_questions": ["方法如何定义？"],
                "recommended_disposition": "p1", "source_ids": item["source_ids"],
                "disposition_reason": "与评测方向相关，仍需人工核验。",
            }

        class FakeClient:
            calls = []
            settings = type("Settings", (), {"model": "mock-model"})()

            def generate_json(self, **kwargs):
                ids = [item["candidate_id"] for item in json.loads(kwargs["input_text"])["candidates"]]
                self.calls.append(ids)
                by_id = {item["candidate_id"]: item for item in selected}
                return {"analyses": [analysis(by_id[cid]) for cid in ids]}, {
                    "provider": "mock", "model": "mock-model", "usage": {}
                }

        with tempfile.TemporaryDirectory() as temp_dir:
            checkpoint_path = Path(temp_dir) / "checkpoint.json"
            checkpoint_path.write_text(json.dumps({
                "schema_version": "0.2",
                "record_type": "llm_structure_checkpoint",
                "prompt_version": "test.v1",
                "model": "mock-model",
                "source_window": {"start": None, "end": None},
                "analyses": [analysis(selected[0])],
                "input_fingerprints": {
                    selected[0]["candidate_id"]: MODULE.hashlib.sha256(
                        MODULE._llm_input([selected[0]], source_payload).encode("utf-8")
                    ).hexdigest(),
                },
                "batches": [],
            }))
            client = FakeClient()
            result = {"selected_candidates": selected, "summary": {}}
            enriched = MODULE.enrich_with_llm(
                result, source_payload, client=client, max_candidates=2,
                prompt_version="test.v1", batch_size=3, checkpoint_path=checkpoint_path,
            )

        self.assertEqual(client.calls, [["cand_second"]])
        self.assertEqual(enriched["llm"]["reused_candidate_count"], 1)
        self.assertEqual(enriched["llm"]["new_batch_count"], 1)

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
