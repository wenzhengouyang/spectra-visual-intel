"""Canonical filesystem boundaries for SPECTRA code, data, and runtime."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


DEFAULT_DATA_ROOT = Path.home() / "Library/Application Support/SPECTRA/data"


def data_root(config: dict[str, Any]) -> Path:
    configured = os.environ.get("SPECTRA_DATA_ROOT") or config.get("data_dir")
    return Path(configured).expanduser().resolve() if configured else DEFAULT_DATA_ROOT


def runs_path(config: dict[str, Any], code_root: Path | None = None) -> Path:
    """Return the data-only runs directory, with legacy test config support."""
    if config.get("data_dir") or os.environ.get("SPECTRA_DATA_ROOT"):
        return data_root(config) / str(config.get("runs_dir") or "runs")
    legacy = Path(str(config.get("runs_dir") or "spectra_agent/runs")).expanduser()
    return legacy if legacy.is_absolute() else (code_root or Path.cwd()) / legacy


def logs_path(config: dict[str, Any]) -> Path:
    return data_root(config) / "logs"


def publish_cache_path(config: dict[str, Any]) -> Path:
    return data_root(config) / "publish"
