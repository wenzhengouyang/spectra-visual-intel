#!/usr/bin/env python3
"""Backfill reviewed source records from user-provided full-page screenshot OCR."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path


TRIM_MARKERS = {
    "src_3079c9c8ea0adcc82d83": "一键三连",
    "src_ebe10d5964d709946301": "THE END",
    "src_9ba9b011c33006409b37": "一键三连",
    "src_dc1693018fed3fac0ca0": "本文为极客公园原创文章",
    "src_ae91d31e776b727771d9": "更多阅读",
}


def normalized(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value.lower())


def title_score(title: str, text: str) -> float:
    lines = text.splitlines()[:8]
    expected = normalized(title)
    candidates = [normalized("".join(lines[:count])) for count in range(1, min(5, len(lines)) + 1)]
    return max(SequenceMatcher(None, expected, candidate).ratio() for candidate in candidates)


def clean_ocr_text(text: str, marker: str | None) -> str:
    if marker and marker in text:
        text = text.split(marker, 1)[0]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", required=True, type=Path)
    parser.add_argument("--review-queue", required=True, type=Path)
    parser.add_argument("--ocr-json", required=True, type=Path)
    args = parser.parse_args()

    source_run = json.loads(args.source_run.read_text(encoding="utf-8"))
    review_queue = json.loads(args.review_queue.read_text(encoding="utf-8"))
    ocr_records = json.loads(args.ocr_json.read_text(encoding="utf-8"))
    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    queue_items = review_queue["items"]
    if len(queue_items) != len(ocr_records):
        raise SystemExit("OCR image count does not match review queue count")

    source_by_id = {item["source_id"]: item for item in source_run["source_records"]}
    results = []
    for queue_item, ocr in zip(queue_items, ocr_records):
        source_id = queue_item["source_id"]
        text = clean_ocr_text(ocr["text"], TRIM_MARKERS.get(source_id))
        score = title_score(queue_item["title"], text)
        body_complete = len(text) >= 180
        wording_fidelity = ocr["mean_confidence"] >= 0.92 and score >= 0.72
        passed = body_complete and wording_fidelity
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

        queue_item.update(
            {
                "verified_text": text,
                "review_status": "verified" if passed else "verification_failed",
                "processing_status": "text_extracted_verified" if passed else "full_text_needs_review",
                "verification_method": "user_provided_full_page_screenshot_ocr",
                "verified_at": now,
                "verified_text_sha256": digest,
                "ocr_character_count": len(text),
                "ocr_mean_confidence": ocr["mean_confidence"],
                "title_match_score": round(score, 4),
                "hard_gates": {
                    "body_completeness": {
                        "status": "pass" if body_complete else "fail",
                        "character_count": len(text),
                        "minimum_character_count": 180,
                    },
                    "fact_wording_fidelity": {
                        "status": "pass" if wording_fidelity else "fail",
                        "basis": "OCR confidence plus title-to-screenshot identity match; no semantic rewriting",
                        "ocr_mean_confidence": ocr["mean_confidence"],
                        "title_match_score": round(score, 4),
                    },
                },
            }
        )

        record = source_by_id[source_id]
        if passed:
            record.update(
                {
                    "raw_text": text,
                    "raw_excerpt": text[:500],
                    "content_hash": digest,
                    "rights_scope": "full_text_internal_analysis",
                    "processing_status": "text_extracted_verified",
                    "verification_method": "user_provided_full_page_screenshot_ocr",
                    "verified_at": now,
                    "ocr_mean_confidence": ocr["mean_confidence"],
                    "title_match_score": round(score, 4),
                    "hard_gates": queue_item["hard_gates"],
                }
            )
            context = record.get("discovery_context") or ""
            marker = "verified_text:user_provided_screenshot_ocr"
            record["discovery_context"] = f"{context};{marker}" if context else marker

        results.append(
            {
                "source_id": source_id,
                "title": queue_item["title"],
                "character_count": len(text),
                "ocr_mean_confidence": ocr["mean_confidence"],
                "title_match_score": round(score, 4),
                "status": "pass" if passed else "fail",
            }
        )

    review_queue["status"] = "completed" if all(x["status"] == "pass" for x in results) else "needs_attention"
    review_queue["completed_at"] = now
    review_queue["validation_summary"] = {
        "total": len(results),
        "passed": sum(x["status"] == "pass" for x in results),
        "failed": sum(x["status"] == "fail" for x in results),
        "results": results,
    }
    source_run.setdefault("summary", {})["verified_full_text_records"] = sum(
        item.get("processing_status") == "text_extracted_verified" for item in source_run["source_records"]
    )

    args.review_queue.write_text(json.dumps(review_queue, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.source_run.write_text(json.dumps(source_run, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(review_queue["validation_summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
