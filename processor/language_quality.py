"""Shared reader-facing Chinese checks for P1 stories and P2 briefs."""

from __future__ import annotations

import re
from typing import Any


CJK_RE = re.compile(r"[\u3400-\u9fff]")
LATIN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9.+/_-]*\b")
ENGLISH_CONNECTOR_RE = re.compile(
    r"(?i)\b(?:the|and|or|but|is|are|was|were|has|have|had|with|without|for|from|"
    r"into|onto|of|to|in|on|at|by|as|why|how|what|when|where|will|would|could|should)\b"
)
ENGLISH_SENTENCE_RE = re.compile(
    r"(?:^|[。！？!?；;]\s*)(?:[A-Z][A-Za-z0-9.+/_'’\-]*\s+){3,}"
    r"[A-Za-z][^。！？!?]{5,}(?:[.!?]|$)"
)
KNOWN_TERMS = {
    "ai", "api", "agent", "agents", "benchmark", "chatgpt", "claude", "codex",
    "deepmind", "deepseek", "gemini", "github", "google", "gpu", "huawei",
    "hugging", "face", "llm", "mcp", "meta", "minimax", "nvidia", "ollama",
    "openai", "qwen", "spatialcrafter", "transformer", "wan", "youtube",
}
FIELD_LIMITS: dict[str, dict[str, float | int]] = {
    "headline": {"min_cjk": 2, "min_ratio": 0.45, "max_connectors": 1},
    "dek": {"min_cjk": 6, "min_ratio": 0.45, "max_connectors": 2},
    "body": {"min_cjk": 20, "min_ratio": 0.60, "max_connectors": 4},
}


def _counted_latin_words(text: str) -> list[str]:
    counted: list[str] = []
    for token in LATIN_WORD_RE.findall(text):
        lowered = token.lower().strip("./_-")
        if lowered in KNOWN_TERMS or any(char.isdigit() for char in token):
            continue
        if token[:1].isupper() and token[1:].islower():
            continue
        counted.append(token)
    return counted


def chinese_completeness(value: str | None) -> dict[str, Any]:
    text = str(value or "").strip()
    cjk_count = len(CJK_RE.findall(text))
    latin_words = _counted_latin_words(text)
    denominator = cjk_count + sum(len(word) for word in latin_words)
    return {
        "cjk_count": cjk_count,
        "latin_word_count": len(latin_words),
        "chinese_ratio": round(cjk_count / denominator, 4) if denominator else 0.0,
        "english_connectors": len(ENGLISH_CONNECTOR_RE.findall(text)),
        "english_sentence_detected": bool(ENGLISH_SENTENCE_RE.search(text)),
    }


def reader_language_errors(value: str | None, field: str) -> list[str]:
    text = str(value or "").strip()
    limits = FIELD_LIMITS.get(field, FIELD_LIMITS["body"])
    metrics = chinese_completeness(text)
    errors: list[str] = []
    if metrics["cjk_count"] < limits["min_cjk"]:
        errors.append(f"{field}_insufficient_chinese")
    if metrics["chinese_ratio"] < limits["min_ratio"]:
        errors.append(f"{field}_chinese_ratio_below_{limits['min_ratio']}")
    if metrics["english_connectors"] > limits["max_connectors"]:
        errors.append(f"{field}_english_sentence_structure")
    if metrics["english_sentence_detected"]:
        errors.append(f"{field}_contains_english_sentence")
    if field == "headline" and text.endswith(("…", "...")):
        errors.append("headline_truncated")
    return errors


def has_readable_chinese(value: str | None, field: str) -> bool:
    return not reader_language_errors(value, field)
