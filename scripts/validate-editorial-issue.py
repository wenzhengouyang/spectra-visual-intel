#!/usr/bin/env python3
"""Validate the Issue 01 editorial bundle and its presentation projections."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
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
    story_ids = {story["story_id"] for story in stories}
    event_ids = {event["event_id"] for event in verified["intelligence_events"]}
    claim_ids = {claim["claim_id"] for claim in verified["evidence_claims"]}

    require(5 <= len(stories) <= 10, "expected 5-10 editorial stories")
    counts = Counter(story["article_type"] for story in stories)
    require(counts["deep_dive"] == min(5, len(stories)), "top five stories must be deep dives")
    require(counts["brief"] == max(0, len(stories) - 5), "remaining stories must be briefs")
    require(len(story_ids) == len(stories), "story IDs must be unique")
    require({story["primary_event_id"] for story in stories} == event_ids, "stories must cover all formal events exactly once")

    for story in stories:
        require(story["editorial_status"] == "fact_checked", f"{story['story_id']} must be fact_checked")
        require(story["what_happened"]["statement_type"] == "fact", f"{story['story_id']} WHAT must be fact")
        require(story["why_it_matters"]["statement_type"] == "judgment", f"{story['story_id']} WHY must be judgment")
        require(story["source_links"] and all(link["url"].startswith("https://") for link in story["source_links"]), f"{story['story_id']} needs source links")
        for field in ("what_happened", "why_it_matters", "under_the_hood", "limitations"):
            section = story[field]
            require(section and section["claim_ids"], f"{story['story_id']} {field} needs claims")
            require(set(section["claim_ids"]) <= claim_ids, f"{story['story_id']} {field} has unknown claims")
        require(all(number["claim_id"] in claim_ids for number in story["key_numbers"]), f"{story['story_id']} has unknown numeric claims")

    issue = editorial["issue"]
    require(len(issue["top_story_ids"]) == min(5, len(stories)), "top story selection must contain up to 5 stories")
    require(set(issue["top_story_ids"]) <= story_ids, "top story selection contains unknown stories")
    require(len(issue["brief_story_ids"]) == max(0, len(stories) - 5), "brief selection has wrong size")

    timeline_ids = [story_id for day in editorial["presentation"]["timeline_days"] for story_id in day["story_ids"]]
    require(len(editorial["presentation"]["timeline_days"]) == 7, "timeline must contain 7 days")
    require(len(timeline_ids) == len(stories) and set(timeline_ids) == story_ids, "timeline must contain every story exactly once")
    require(1 <= len(editorial["presentation"]["trend_radar"]) <= 4, "trend radar must contain 1-4 trends")
    require(all(set(trend["event_ids"]) <= event_ids for trend in editorial["presentation"]["trend_radar"]), "radar contains unknown events")

    print("result: pass")
    print(f"stories: {len(stories)}")
    print(f"deep_dive: {counts['deep_dive']}")
    print(f"brief: {counts['brief']}")
    print(f"timeline_events: {len(timeline_ids)}")
    print(f"radar_trends: {len(editorial['presentation']['trend_radar'])}")


if __name__ == "__main__":
    main()
