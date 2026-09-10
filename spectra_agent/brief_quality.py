"""Conservative roundup isolation. Never synthesize a new factual claim."""
from __future__ import annotations
import re

ROUTE_TERMS = {
    "extended.foundation_multimodal": ("大模型", "多模态", "模型调用", "llm", "gpt", "claude", "gemini", "hy4"),
    "extended.ai_agent_tools": ("agent", "智能体", "代理"),
    "frontier.embodied_ai": ("机器人", "机器狗", "具身"),
}


def isolate_headline(title: str, route: str) -> str | None:
    parts = [p.strip() for p in re.split(r"[；;]|\s+[|｜]\s*", title) if p.strip()]
    if len(parts) <= 1:
        return title
    terms = ROUTE_TERMS.get(route, ())
    matches = [p for p in parts if any(t in p.lower() for t in terms)]
    # Ambiguous roundups are held back, not routed by a guess or first segment.
    return matches[0] if len(matches) == 1 else None


def evidence_search_plan(candidate: dict) -> dict:
    analysis = candidate.get("llm_analysis") or {}
    missing = analysis.get("missing_evidence") or candidate.get("missing_evidence") or []
    if isinstance(missing, str):
        missing = [missing]
    title = isolate_headline(candidate.get("canonical_title", ""), candidate.get("primary_route", ""))
    return {"candidate_id": candidate.get("candidate_id"),
            "status": "pending_search" if title and missing else "needs_title_review" if not title else "not_needed",
            "queries": [f"{title[:120]} {str(gap)[:100]}" for gap in missing[:2]] if title else [],
            "max_results_per_query": 3, "missing_evidence": missing[:2],
            "auto_approve": False}
