#!/usr/bin/env python3
"""OCR tall screenshots in overlapping strips and emit deduplicated text JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image
from rapidocr_onnxruntime import RapidOCR


def line_y(item: list) -> float:
    return min(point[1] for point in item[0])


def normalize(text: str) -> str:
    return "".join(text.split()).lower()


def is_near_duplicate(text: str, recent: list[str]) -> bool:
    key = normalize(text)
    if not key:
        return True
    return any(key == normalize(old) for old in recent[-12:])


def ocr_image(engine: RapidOCR, path: Path, strip_height: int, overlap: int) -> dict:
    image = Image.open(path).convert("RGB")
    width, height = image.size
    lines: list[dict] = []
    recent: list[str] = []
    y0 = 0
    strip_index = 0

    while y0 < height:
        y1 = min(height, y0 + strip_height)
        crop = np.asarray(image.crop((0, y0, width, y1)))
        result, _ = engine(crop)
        for item in sorted(result or [], key=line_y):
            text = str(item[1]).strip()
            score = float(item[2])
            if score < 0.45 or is_near_duplicate(text, recent):
                continue
            lines.append({"text": text, "confidence": round(score, 4), "y": round(y0 + line_y(item), 1)})
            recent.append(text)
        if y1 == height:
            break
        y0 = y1 - overlap
        strip_index += 1

    text = "\n".join(item["text"] for item in lines)
    confidence = sum(item["confidence"] for item in lines) / len(lines) if lines else 0.0
    return {
        "source_image": str(path),
        "width": width,
        "height": height,
        "line_count": len(lines),
        "character_count": len(text),
        "mean_confidence": round(confidence, 4),
        "text": text,
        "lines": lines,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("images", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--strip-height", type=int, default=3200)
    parser.add_argument("--overlap", type=int, default=180)
    args = parser.parse_args()

    engine = RapidOCR()
    records = [ocr_image(engine, path, args.strip_height, args.overlap) for path in args.images]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    for record in records:
        print(
            f"{Path(record['source_image']).name}\t"
            f"chars={record['character_count']}\t"
            f"lines={record['line_count']}\t"
            f"confidence={record['mean_confidence']}"
        )


if __name__ == "__main__":
    main()
