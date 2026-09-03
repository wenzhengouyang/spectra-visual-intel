#!/usr/bin/env python3
"""Validate the Issue 01 editorial bundle and its presentation projections."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from processor.p2_localizer import validate_localized_brief


EDITORIAL_PATH = ROOT / "editorial/runs/issue-01-editorial-stories-v0.2.json"
VERIFIED_PATH = ROOT / "verification/runs/p1-verified-events-v0.2.json"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--editorial", default=str(EDITORIAL_PATH))
    parser.add_argument("--verified", default=str(VERIFIED_PATH))
    args = parser.parse_args()
    editorial = json.loads(Path(args.editorial).read_text())
    verified = json.loads(Path(args.verified).read_text())
    stories = editorial["editorial_stories"]
    news_briefs = editorial.get("news_briefs", [])
    issue = editorial["issue"]
    story_ids = {story["story_id"] for story in stories}
    news_brief_ids = {brief["brief_id"] for brief in news_briefs}
    event_ids = {event["event_id"] for event in verified["intelligence_events"]}
    claim_ids = {claim["claim_id"] for claim in verified["evidence_claims"]}

    attainable_minimum = min(5, len(event_ids))
    require(
        attainable_minimum <= len(stories) <= 10,
        f"expected {attainable_minimum}-10 editorial stories for this verified event set",
    )
    counts = Counter(story["article_type"] for story in stories)
    require(counts["deep_dive"] == len(issue["top_story_ids"]), "deep dives must match the readiness-qualified top stories")
    require(counts["deep_dive"] <= min(5, len(stories)), "at most five stories may be deep dives")
    require(counts["brief"] == len(stories) - counts["deep_dive"], "non-deep stories must be quick reads")
    require(len(story_ids) == len(stories), "story IDs must be unique")
    require({story["primary_event_id"] for story in stories} == event_ids, "stories must cover all formal events exactly once")

    for story in stories:
        require(story["editorial_status"] == "fact_checked", f"{story['story_id']} must be fact_checked")
        cover = story.get("cover_image") or {}
        cover_url = cover.get("url") or ""
        require(cover_url.startswith("https://") or cover_url.startswith("assets/"), f"{story['story_id']} needs a safe cover image")
        require(cover.get("kind") in {"official", "editorial", "editorial_fallback", "editorial_diagram"}, f"{story['story_id']} has invalid cover kind")
        require(bool(cover.get("label")), f"{story['story_id']} needs a cover label")
        if cover_url.startswith("assets/"):
            require((ROOT / cover_url).exists(), f"{story['story_id']} local cover asset is missing")
        require(story["what_happened"]["statement_type"] == "fact", f"{story['story_id']} WHAT must be fact")
        require(story["why_it_matters"]["statement_type"] == "judgment", f"{story['story_id']} WHY must be judgment")
        require(story["source_links"] and all(link["url"].startswith("https://") for link in story["source_links"]), f"{story['story_id']} needs source links")
        for field in ("what_happened", "why_it_matters", "under_the_hood", "limitations"):
            section = story[field]
            require(section and section["claim_ids"], f"{story['story_id']} {field} needs claims")
            require(set(section["claim_ids"]) <= claim_ids, f"{story['story_id']} {field} has unknown claims")
        require(all(number["claim_id"] in claim_ids for number in story["key_numbers"]), f"{story['story_id']} has unknown numeric claims")
        article = story.get("article_body") or {}
        require(article.get("reading_mode") == "complete_in_page", f"{story['story_id']} must support complete in-page reading")
        for field in ("lead", "full_text", "judgment", "evidence_boundary"):
            article_section = article.get(field)
            require(article_section and article_section.get("text"), f"{story['story_id']} article_body.{field} is required")
            require(set(article_section.get("claim_ids", [])) <= claim_ids, f"{story['story_id']} article_body.{field} has unknown claims")

    require(len(issue["top_story_ids"]) <= min(5, len(stories)), "top story selection must contain at most 5 stories")
    require(set(issue["top_story_ids"]) <= story_ids, "top story selection contains unknown stories")
    require(
        set(issue.get("brief_story_ids", []))
        == {story["story_id"] for story in stories if story.get("article_type") == "brief"},
        "editorial brief story selection has wrong IDs",
    )
    require(set(issue["news_brief_ids"]) == news_brief_ids, "P2 news brief selection has wrong IDs")
    require(issue["brief_count"] == len(news_briefs), "P2 brief count is wrong")
    require(issue["total_intelligence_count"] == len(stories) + len(news_briefs), "total intelligence count is wrong")

    for brief in news_briefs:
        require(brief["priority"] == "priority.p2", f"{brief['brief_id']} must be P2")
        require(brief.get("domain_scope") in {"scope.visual_core", "scope.ai_extended"}, f"{brief['brief_id']} has invalid domain scope")
        require(brief["verification_status"] != "fact_checked", f"{brief['brief_id']} must retain its pending verification boundary")
        require(brief["source_links"] and all(link["url"].startswith("https://") for link in brief["source_links"]), f"{brief['brief_id']} needs source links")
        require(
            bool(re.search(r"[\u4e00-\u9fff]", brief["headline"])),
            f"{brief['brief_id']} headline must be localized into Chinese",
        )
        require(
            bool(re.search(r"[\u4e00-\u9fff]", brief["dek"])),
            f"{brief['brief_id']} summary must be localized into Chinese",
        )
        require(
            not re.search(r"[\u4e00-\u9fff][a-z]{3,}\b", brief["headline"]),
            f"{brief['brief_id']} headline contains a partial Chinese-English translation fragment",
        )
        localization_errors = validate_localized_brief(brief)
        require(not localization_errors, f"{brief['brief_id']} failed localization checks: {localization_errors}")

    timeline_ids = [story_id for day in editorial["presentation"]["timeline_days"] for story_id in day["story_ids"]]
    timeline_brief_ids = [brief_id for day in editorial["presentation"]["timeline_days"] for brief_id in day.get("brief_ids", [])]
    expected_timeline_days = (
        date.fromisoformat(issue["period_end"]) - date.fromisoformat(issue["period_start"])
    ).days + 1
    require(1 <= expected_timeline_days <= 8, "issue period must contain between 1 and 8 calendar dates")
    require(
        len(editorial["presentation"]["timeline_days"]) == expected_timeline_days,
        "timeline length must match the issue period",
    )
    require(len(timeline_ids) == len(stories) and set(timeline_ids) == story_ids, "timeline must contain every story exactly once")
    require(len(timeline_brief_ids) == len(news_briefs) and set(timeline_brief_ids) == news_brief_ids, "timeline must contain every P2 brief exactly once")
    require(1 <= len(editorial["presentation"]["trend_radar"]) <= 4, "trend radar must contain 1-4 trends")
    require(all(set(trend["event_ids"]) <= event_ids for trend in editorial["presentation"]["trend_radar"]), "radar contains unknown events")

    print("result: pass")
    print(f"stories: {len(stories)}")
    print(f"deep_dive: {counts['deep_dive']}")
    print(f"brief: {counts['brief']}")
    print(f"p2_news_briefs: {len(news_briefs)}")
    print(f"timeline_events: {len(timeline_ids)}")
    print(f"radar_trends: {len(editorial['presentation']['trend_radar'])}")


if __name__ == "__main__":
    main()
