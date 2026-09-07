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

from processor.language_quality import has_readable_chinese, reader_language_errors
from processor.p2_localizer import validate_localized_brief
from spectra_agent.publication_quality import publication_quality_errors


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--editorial", required=True)
    parser.add_argument("--verified", required=True)
    parser.add_argument("--config", default=str(ROOT / "spectra_agent/config.v0.1.json"))
    args = parser.parse_args()
    editorial = json.loads(Path(args.editorial).read_text())
    verified = json.loads(Path(args.verified).read_text())
    config = json.loads(Path(args.config).read_text())
    asset_root = Path(args.editorial).resolve().parent
    if not (asset_root / "assets").exists():
        asset_root = ROOT
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
    require(counts["core_event"] == len(issue["top_story_ids"]), "core events must match the readiness-qualified top stories")
    require(counts["core_event"] <= min(5, len(stories)), "at most five stories may be core events")
    require(counts["brief"] == len(stories) - counts["core_event"], "non-core stories must be quick reads")
    require(len(story_ids) == len(stories), "story IDs must be unique")
    require({story["primary_event_id"] for story in stories} == event_ids, "stories must cover all formal events exactly once")

    for story in stories:
        require(story["editorial_status"] == "fact_checked", f"{story['story_id']} must be fact_checked")
        require(
            has_readable_chinese(story.get("headline"), "headline"),
            f"{story['story_id']} headline must be readable Chinese",
        )
        require(
            has_readable_chinese(story.get("dek"), "dek"),
            f"{story['story_id']} dek must be readable Chinese",
        )
        cover = story.get("cover_image") or {}
        cover_url = cover.get("url") or ""
        require(cover_url.startswith("https://") or cover_url.startswith("assets/"), f"{story['story_id']} needs a safe cover image")
        require(
            cover.get("kind") in {"official", "editorial", "editorial_fallback", "editorial_diagram", "generated"},
            f"{story['story_id']} has invalid cover kind",
        )
        require(bool(cover.get("label")), f"{story['story_id']} needs a cover label")
        if cover_url.startswith("assets/"):
            require((asset_root / cover_url).exists(), f"{story['story_id']} local cover asset is missing")
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
        for field in ("lead", "full_text", "evidence_boundary"):
            article_section = article.get(field)
            require(article_section and article_section.get("text"), f"{story['story_id']} article_body.{field} is required")
            require(set(article_section.get("claim_ids", [])) <= claim_ids, f"{story['story_id']} article_body.{field} has unknown claims")
        full_text = str((article.get("full_text") or {}).get("text") or "")
        require(
            not reader_language_errors(full_text, "body"),
            f"{story['story_id']} body must be complete Chinese prose: {reader_language_errors(full_text, 'body')}",
        )
        judgment = article.get("judgment") or {}
        # A core event may omit judgment when the locked fact package does not
        # provide an allowed_judgment. Requiring filler here would encourage
        # unsupported analysis in otherwise valid factual prose.
        require(set(judgment.get("claim_ids", [])) <= claim_ids, f"{story['story_id']} article_body.judgment has unknown claims")

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
        require(has_readable_chinese(brief["headline"], "headline"), f"{brief['brief_id']} headline must be readable Chinese")
        require(has_readable_chinese(brief["dek"], "dek"), f"{brief['brief_id']} summary must be readable Chinese")
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
    if editorial.get("report_type") == "rolling_7_day_digest":
        require(expected_timeline_days == 7, "rolling digest must contain exactly 7 calendar dates")
        require(issue.get("window_days") == 7, "rolling digest window_days must be 7")
        require(issue.get("report_date") == issue.get("period_end"), "report_date must equal period_end")
    else:
        require(1 <= expected_timeline_days <= 7, "issue period must contain between 1 and 7 calendar dates")
    require(
        len(editorial["presentation"]["timeline_days"]) == expected_timeline_days,
        "timeline length must match the issue period",
    )
    require(len(timeline_ids) == len(stories) and set(timeline_ids) == story_ids, "timeline must contain every story exactly once")
    require(len(timeline_brief_ids) == len(news_briefs) and set(timeline_brief_ids) == news_brief_ids, "timeline must contain every P2 brief exactly once")
    require(1 <= len(editorial["presentation"]["trend_radar"]) <= 4, "trend radar must contain 1-4 trends")
    require(all(set(trend["event_ids"]) <= event_ids for trend in editorial["presentation"]["trend_radar"]), "radar contains unknown events")

    quality_errors = publication_quality_errors(editorial, config, asset_root=asset_root)
    require(not quality_errors, "reader-facing publication quality failed: " + "; ".join(quality_errors))

    print("result: pass")
    print(f"stories: {len(stories)}")
    print(f"core_event: {counts['core_event']}")
    print(f"brief: {counts['brief']}")
    print(f"p2_news_briefs: {len(news_briefs)}")
    print(f"timeline_events: {len(timeline_ids)}")
    print(f"radar_trends: {len(editorial['presentation']['trend_radar'])}")


if __name__ == "__main__":
    main()
