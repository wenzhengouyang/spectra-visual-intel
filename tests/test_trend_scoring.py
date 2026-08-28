import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("trend_scoring", ROOT / "editorial" / "trend_scoring.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def event(event_id, evidence="evidence.a", confidence="confidence.high", sources=1):
    return {
        "event_id": event_id,
        "evidence_level": evidence,
        "confidence": confidence,
        "independent_source_count": sources,
    }


def review(kind="type.technology_breakthrough", importance=80, relevance=85, novelty=75):
    return {"agent_analysis": {
        "intelligence_type": kind,
        "intelligence_type_reason": "测试分类理由。",
        "importance_score": importance,
        "strategy_relevance_score": relevance,
        "novelty_score": novelty,
    }}


class TrendScoringTests(unittest.TestCase):
    def test_score_has_auditable_components(self):
        events = [event("a"), event("b", sources=2)]
        result = MODULE.score_trend("frontier.video_generation", events, {"a": review(), "b": review()})
        self.assertEqual(result["model_version"], "trend-score.v0.1")
        self.assertEqual(len(result["breakdown"]), 6)
        self.assertEqual(sum(item["weight"] for item in result["breakdown"]), 100)
        self.assertGreaterEqual(result["score"], 0)
        self.assertLessEqual(result["score"], 100)

    def test_more_verified_corroborated_signals_raise_score(self):
        weak = MODULE.score_trend("frontier.world_model", [event("a", "evidence.c", "confidence.low")], {"a": review(importance=60, relevance=60, novelty=60)})
        strong_events = [event("a", sources=3), event("b", sources=2), event("c", sources=2)]
        strong_reviews = {key: review(importance=90, relevance=90, novelty=85) for key in ("a", "b", "c")}
        strong = MODULE.score_trend("frontier.world_model", strong_events, strong_reviews)
        self.assertGreater(strong["score"], weak["score"])
        self.assertGreater(strong["impact"], weak["impact"])

    def test_product_release_is_more_mature_than_equal_research_signal(self):
        item = [event("a")]
        research = MODULE.score_trend("frontier.embodied_ai", item, {"a": review("type.technology_breakthrough")})
        product = MODULE.score_trend("frontier.embodied_ai", item, {"a": review("type.product_release")})
        self.assertGreater(product["maturity"], research["maturity"])


if __name__ == "__main__":
    unittest.main()
