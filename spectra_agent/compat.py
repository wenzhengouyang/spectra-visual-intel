"""Single compatibility boundary for pre-core-event and pre-digest artifacts.

New code must use the canonical names. Legacy names are read only here so old
runs remain resumable during the migration window.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any


LEGACY_CONFIG_KEYS = {"core_event_writer": ("deep_story_writer", "editorial_writer")}
LEGACY_VALUE_KEYS = {
    "core_event_min_characters": ("deep_story_min_characters",),
    "minimum_core_events": ("minimum_deep_stories",),
    "rolling_thesis": ("weekly_thesis",),
}
LEGACY_ARTIFACT_NAMES = {
    "core-event-drafts.json": ("deep-story-drafts.json",),
    "core-event-checkpoint.json": ("p1-long-editorial-checkpoint.json",),
    "core-event-audit.json": ("p1-long-editorial-audit.json",),
    "rolling-digest.html": ("weekly-report.html",),
}
LEGACY_TEXT_REPLACEMENTS = {
    "deep_story": "core_event",
    "deep-story": "core-event",
    "deep_dive": "core_event",
    "weekly_thesis": "rolling_thesis",
    "weekly_issue_editorial_bundle": "rolling_digest_editorial_bundle",
    "weeklyThesis": "rollingThesis",
    "weekly-timeline": "rolling-timeline",
    "deepDive": "coreEvent",
    "deep-dive": "core-event",
    "p1_long_writer": "core_event_writer",
    "p1_long_job_checkpoint": "core_event_job_checkpoint",
    "p1_long_editorial_audit": "core_event_editorial_audit",
}
MIGRATABLE_DERIVED_ARTIFACTS = {
    "core-event-drafts.json",
    "core-event-checkpoint.json",
    "core-event-audit.json",
    "editorial-issue.json",
    "rolling-digest.html",
    "run.json",
}


def config_section(config: dict[str, Any], canonical_key: str) -> dict[str, Any]:
    if canonical_key in config:
        return config[canonical_key] or {}
    for legacy_key in LEGACY_CONFIG_KEYS.get(canonical_key, ()):
        if legacy_key in config:
            return config[legacy_key] or {}
    return {}


def legacy_value(values: dict[str, Any], canonical_key: str, *legacy_keys: str, default: Any = None) -> Any:
    if canonical_key in values:
        return values[canonical_key]
    for legacy_key in legacy_keys:
        if legacy_key in values:
            return values[legacy_key]
    return default


def compatible_value(values: dict[str, Any], canonical_key: str, default: Any = None) -> Any:
    """Read canonical data while containing every legacy spelling here."""
    return legacy_value(values, canonical_key, *LEGACY_VALUE_KEYS.get(canonical_key, ()), default=default)


def rolling_thesis(values: dict[str, Any], default: Any = None) -> Any:
    return compatible_value(values, "rolling_thesis", default=default)


def normalize_derived_artifact(path: Path) -> bool:
    if not path.is_file() or path.name not in MIGRATABLE_DERIVED_ARTIFACTS:
        return False
    try:
        original = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return False
    migrated = original
    for legacy_text, canonical_text in LEGACY_TEXT_REPLACEMENTS.items():
        migrated = migrated.replace(legacy_text, canonical_text)
    if migrated == original:
        return False
    path.write_text(migrated, encoding="utf-8")
    return True


def canonical_artifact(run_dir: Path, name: str) -> Path:
    target = run_dir / name
    if target.exists():
        normalize_derived_artifact(target)
        return target
    for legacy_name in LEGACY_ARTIFACT_NAMES.get(name, ()):
        legacy = run_dir / legacy_name
        if legacy.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(legacy, target)
            normalize_derived_artifact(target)
            return target
    return target


def compatible_artifact(run_dir: Path, name: str) -> Path:
    """Resolve a canonical or legacy artifact without mutating the Run."""
    target = run_dir / name
    if target.exists():
        return target
    for legacy_name in LEGACY_ARTIFACT_NAMES.get(name, ()):
        legacy = run_dir / legacy_name
        if legacy.exists():
            return legacy
    return target


def migrate_run_artifacts(run_dir: Path, backup_root: Path | None = None) -> dict[str, int]:
    """Migrate only regenerable outputs; source evidence and logs stay immutable."""
    promoted = 0
    removed = 0
    rewritten = 0
    for canonical_name, legacy_names in LEGACY_ARTIFACT_NAMES.items():
        target = canonical_artifact(run_dir, canonical_name)
        if target.exists() and any((run_dir / legacy).exists() for legacy in legacy_names):
            promoted += 1
    for name in MIGRATABLE_DERIVED_ARTIFACTS:
        path = run_dir / name
        if not path.is_file():
            continue
        original = path.read_bytes()
        if any(legacy.encode("utf-8") in original for legacy in LEGACY_TEXT_REPLACEMENTS):
            if backup_root is not None:
                backup = backup_root / run_dir.name / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
            rewritten += int(normalize_derived_artifact(path))
    return {"promoted": promoted, "removed": removed, "rewritten": rewritten}
