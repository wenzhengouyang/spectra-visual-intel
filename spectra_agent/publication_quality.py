"""Single fail-closed reader quality gate shared by resume, eval and publish."""

from __future__ import annotations

import hashlib
import re
import difflib
from pathlib import Path
from typing import Any

from processor.language_quality import reader_language_errors
from spectra_agent.compat import compatible_value


INTERNAL_PLACEHOLDERS = (
    "来源标题提及上述动态",
    "具体口径与背景尚待原文核验",
    "尚待原文核验",
    "来源已收录，核心事实仍待核验",
    "在进展页核对所展示证据后确认",
)


def _materially_repeats(left: str, right: str, threshold: float = 0.82) -> bool:
    normalize = lambda value: re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", str(value or "")).lower()
    first, second = normalize(left), normalize(right)
    if not first or not second:
        return False
    shorter, longer = sorted((first, second), key=len)
    if shorter in longer or difflib.SequenceMatcher(None, first, second).ratio() >= threshold:
        return True
    if len(shorter) >= 10:
        short_pairs = {shorter[index:index + 2] for index in range(len(shorter) - 1)}
        long_pairs = {longer[index:index + 2] for index in range(len(longer) - 1)}
        return bool(short_pairs and len(short_pairs & long_pairs) / len(short_pairs) >= 0.78)
    return False


def reader_copy_values(item: dict[str, Any]) -> list[str]:
    values: list[str] = []

    def collect(value: Any) -> None:
        if isinstance(value, str):
            values.append(value)
        elif isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    for field in (
        "headline", "dek", "one_line_takeaway", "what_happened", "why_it_matters",
        "under_the_hood", "limitations", "article_body", "watch_next",
    ):
        collect(item.get(field))
    return values


def story_text(story: dict[str, Any]) -> str:
    article = story.get("article_body") or {}
    value = article.get("full_text") or {}
    if isinstance(value, dict):
        return str(value.get("text") or "")
    return str(value or "")


def _sentences(value: str) -> list[str]:
    return [item.strip() for item in re.findall(r"[^。！？]+[。！？]?", str(value or "")) if item.strip()]


def _has_repeated_pair(values: list[str], threshold: float = 0.88) -> bool:
    for index, value in enumerate(values):
        if any(_materially_repeats(value, prior, threshold) for prior in values[:index]):
            return True
    return False


def expected_cover_motif(headline: str, category: str) -> str:
    text = f"{headline} {category}".lower()
    groups = (
        ("healthcare", ("医疗", "患者", "乳腺癌", "health")),
        ("swarm", ("群体", "多智能体", "文明", "swarm")),
        ("document", ("法律", "legal", "合规")),
        ("layers", ("治理", "数据层", "安全", "governance")),
        ("workflow", ("运营", "组织", "客户回报", "roi")),
        ("autonomy", ("自动驾驶", "驾驶", "车载", "闭环", "autonomous driving", "driving simulation")),
        ("compute", ("芯片", "算力", "集群", "gpu", "量化", "quantization", "部署", "deployment")),
    )
    for motif, terms in groups:
        if any(term in text for term in terms):
            return motif
    return "signal"


def _cover_fingerprint(cover: dict[str, Any], root: Path) -> str:
    url = str(cover.get("url") or "")
    if url.startswith("assets/"):
        path = root / url
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def requires_cover(story: dict[str, Any]) -> bool:
    """Match the frontend's core-event / industry-signal image card group."""
    return story.get("article_type", "core_event") == "core_event" or story.get("editorial_tier") == "industry_signal"


def brief_only_allowed(issue: dict[str, Any], config: dict[str, Any]) -> bool:
    items = issue.get("editorial_stories") or []
    return bool((config.get("publication_quality") or {}).get("allow_brief_only", True)
                and not any(item.get("article_type") == "core_event" for item in items)
                and (items or issue.get("news_briefs")))


def publication_quality_errors(
    issue: dict[str, Any], config: dict[str, Any], *, asset_root: Path
) -> list[str]:
    quality = config.get("publication_quality") or {}
    minimum_core_events = int(compatible_value(quality, "minimum_core_events", default=1))
    minimum_characters = int(compatible_value(quality, "core_event_min_characters", default=0))
    minimum_signal_characters = int(quality.get("industry_signal_min_characters", 0))
    minimum_signal_facts = int(quality.get("industry_signal_min_fact_points", 0))
    require_image_review = bool(quality.get("require_generated_image_review", True))
    allow_pending_covers = bool(quality.get("allow_pending_cover_publish", False))
    stories = issue.get("editorial_stories") or []
    briefs = issue.get("news_briefs") or []
    errors: list[str] = []
    core_event_count = sum(story.get("article_type") == "core_event" for story in stories)
    if not stories and not briefs:
        errors.append("no_publishable_content")
    if core_event_count < minimum_core_events and not brief_only_allowed(issue, config):
        errors.append(f"core_event_count_below_{minimum_core_events}: {core_event_count}")

    fingerprints: dict[str, str] = {}
    for item in [*stories, *briefs]:
        item_id = str(item.get("story_id") or item.get("brief_id") or "unknown")
        if any(marker in value for value in reader_copy_values(item) for marker in INTERNAL_PLACEHOLDERS):
            errors.append(f"{item_id}.internal_editorial_placeholder")
        for field in ("headline", "dek"):
            language_errors = reader_language_errors(item.get(field), field)
            if language_errors:
                errors.append(f"{item_id}.{field}: {','.join(language_errors)}")
        if _materially_repeats(item.get("headline", ""), item.get("dek", "")):
            errors.append(f"{item_id}.headline_dek_repeated")
        takeaway = str(item.get("one_line_takeaway") or "")
        if takeaway and (_materially_repeats(takeaway, item.get("dek", ""))
                         or _materially_repeats(takeaway, item.get("headline", ""))):
            errors.append(f"{item_id}.takeaway_repeated")
        if item.get("article_body"):
            article = item.get("article_body") or {}
            lead = (article.get("lead") or {}).get("text", "")
            if _materially_repeats(item.get("headline", ""), lead):
                errors.append(f"{item_id}.headline_lead_repeated")
            details = (article.get("key_details") or {}).get("text", "")
            if _has_repeated_pair([*_sentences(lead), *_sentences(details)]):
                errors.append(f"{item_id}.article_sentences_repeated")
            fact_points = [str(value) for value in article.get("fact_points") or [] if str(value).strip()]
            if _has_repeated_pair(fact_points, 0.86):
                errors.append(f"{item_id}.fact_points_repeated")
            summary_paragraphs = [str(value) for value in article.get("summary_paragraphs") or [] if str(value).strip()]
            if _has_repeated_pair(summary_paragraphs):
                errors.append(f"{item_id}.summary_paragraphs_repeated")
            if re.search(r"\b[A-Z]{1,4},(?=[\u3400-\u9fff])", story_text(item)):
                errors.append(f"{item_id}.body_suspicious_ascii_fragment")
        if item.get("story_id"):
            body = story_text(item)
            language_errors = reader_language_errors(body, "dek" if item.get("editorial_tier") == "brief" else "body")
            if language_errors:
                errors.append(f"{item_id}.body: {','.join(language_errors)}")
            if item.get("article_type") == "core_event":
                count = len(re.sub(r"\s+", "", body))
                if minimum_characters > 0 and count < minimum_characters:
                    errors.append(f"{item_id}.core_event_too_short: {count}<{minimum_characters}")
            elif item.get("editorial_tier") == "industry_signal":
                count = len(re.sub(r"\s+", "", body))
                fact_count = len((item.get("article_body") or {}).get("fact_points") or [])
                if minimum_signal_characters > 0 and count < minimum_signal_characters:
                    errors.append(f"{item_id}.industry_signal_too_short: {count}<{minimum_signal_characters}")
                if minimum_signal_facts > 0 and fact_count < minimum_signal_facts:
                    errors.append(f"{item_id}.industry_signal_too_thin: {fact_count}<{minimum_signal_facts}")

            # Every image-bearing card needs a valid, reviewed cover.
            if not requires_cover(item):
                continue
            cover = item.get("cover_image") or {}
            url = str(cover.get("url") or "")
            if not url:
                errors.append(f"{item_id}.cover_missing")
                continue
            kind = str(cover.get("kind") or "")
            if cover.get("provisional") or kind == "editorial_diagram":
                if allow_pending_covers:
                    # The article may ship with a clearly marked pending visual;
                    # copy and factual gates above still apply.
                    continue
                errors.append(f"{item_id}.cover_is_provisional")
                continue
            if kind in {"source", "official"} and not str(cover.get("source_url") or "").startswith("https://"):
                errors.append(f"{item_id}.official_cover_missing_source")
            if kind in {"editorial_diagram", "generated", "source", "official"}:
                expected = expected_cover_motif(str(item.get("headline") or ""), str(item.get("category") or ""))
                if cover.get("semantic_motif") != expected or cover.get("semantic_match") != "passed":
                    errors.append(f"{item_id}.generated_cover_topic_mismatch")
                if require_image_review and cover.get("review_status") != "approved" and not allow_pending_covers:
                    errors.append(f"{item_id}.generated_cover_needs_human_preview")
                elif (require_image_review and not allow_pending_covers
                      and cover.get('asset_fingerprint') != _cover_fingerprint(cover, asset_root)):
                    errors.append(f"{item_id}.generated_cover_review_is_stale")
            fingerprint = _cover_fingerprint(cover, asset_root)
            duplicate = fingerprints.get(fingerprint)
            if duplicate:
                errors.append(f"{item_id}.cover_duplicates_{duplicate}")
            else:
                fingerprints[fingerprint] = item_id
    return errors
