#!/usr/bin/env python3
"""Validate and publish one completed run to the configured GitHub Pages branch."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    from spectra_agent.compat import canonical_artifact
    from spectra_agent.paths import publish_cache_path
    from spectra_agent.run import (
        DEFAULT_CONFIG,
        ROOT,
        STATIC_ASSETS,
        WorkflowError,
        read_json,
        resolve_config,
        runs_dir,
        validate_static_package,
        write_json,
    )
except ImportError:
    from compat import canonical_artifact
    from paths import publish_cache_path
    from run import (
        DEFAULT_CONFIG,
        ROOT,
        STATIC_ASSETS,
        WorkflowError,
        read_json,
        resolve_config,
        runs_dir,
        validate_static_package,
        write_json,
    )


def command(arguments: list[str], cwd: Path, capture: bool = False) -> str:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.STDOUT if capture else None,
    )
    return result.stdout.strip() if capture else ""


def validate_run(run_dir: Path, config: dict) -> dict:
    state = read_json(run_dir / "run.json")
    if state.get("status") != "completed":
        raise WorkflowError(f"run is not completed: {state.get('status')}")
    review = read_json(run_dir / "p1-review.json")
    if review.get("review_status") != "approved":
        raise WorkflowError("P1 review is not approved")
    issue = read_json(run_dir / "editorial-issue.json")
    report = canonical_artifact(run_dir, "rolling-digest.html")
    validate_static_package(run_dir, report, issue)
    command([
        sys.executable,
        "scripts/validate-editorial-issue.py",
        "--editorial", str(run_dir / "editorial-issue.json"),
        "--verified", str(run_dir / "verified-events.json"),
        "--config", str(DEFAULT_CONFIG),
    ], ROOT)
    if (config.get("publishing") or {}).get("require_publication_checks", True):
        if not (run_dir / "eval-report.json").exists():
            raise WorkflowError("run evaluation report is missing")
        evaluation = read_json(run_dir / "eval-report.json")
        failures = [
            item["name"] for item in evaluation.get("publication_checks", [])
            if item.get("severity") == "error" and not item.get("passed")
        ]
        if failures:
            raise WorkflowError("publication checks failed: " + ", ".join(failures))
    return issue


def prepare_checkout(config: dict) -> Path:
    publishing = config.get("publishing") or {}
    remote = publishing.get("remote", "origin")
    branch = publishing.get("branch", "main")
    remote_url = publishing.get("remote_url")
    if not remote_url:
        remote_url = command(["git", "remote", "get-url", remote], ROOT, capture=True)
    checkout = publish_cache_path(config)
    if not (checkout / ".git").exists():
        checkout.parent.mkdir(parents=True, exist_ok=True)
        command(["git", "clone", "--branch", branch, "--single-branch", remote_url, str(checkout)], ROOT)
    if command(["git", "status", "--porcelain"], checkout, capture=True):
        raise WorkflowError(f"publish checkout has uncommitted changes: {checkout}")
    command(["git", "pull", "--ff-only", "origin", branch], checkout)
    return checkout


def copy_package(run_dir: Path, checkout: Path) -> list[str]:
    shutil.copyfile(canonical_artifact(run_dir, "rolling-digest.html"), checkout / "index.html")
    paths = ["index.html"]
    for relative in STATIC_ASSETS:
        source = run_dir / relative
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        paths.append(str(relative))
    source_assets = run_dir / "assets"
    if source_assets.exists():
        shutil.copytree(source_assets, checkout / "assets", dirs_exist_ok=True)
        paths.append("assets")
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a validated SPECTRA run to GitHub Pages")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--push", action="store_true", help="commit and push after validation")
    parser.add_argument("--confirm", action="store_true", help="required explicit publication confirmation")
    args = parser.parse_args()

    _, config = resolve_config(args.config)
    run_dir = runs_dir(config) / args.run_id
    try:
        validate_run(run_dir, config)
        if not args.push:
            print(json.dumps({"run_id": args.run_id, "status": "validated", "published": False}, ensure_ascii=False))
            return 0
        if not args.confirm:
            raise WorkflowError("--push requires --confirm")

        checkout = prepare_checkout(config)
        changed_paths = copy_package(run_dir, checkout)
        command(["git", "add", "--", *changed_paths], checkout)
        staged = command(["git", "diff", "--cached", "--name-only"], checkout, capture=True)
        if not staged:
            print(json.dumps({"run_id": args.run_id, "status": "unchanged", "published": True}, ensure_ascii=False))
            return 0
        command(["git", "commit", "-m", f"publish: {args.run_id}"], checkout)
        branch = (config.get("publishing") or {}).get("branch", "main")
        command(["git", "push", "origin", f"HEAD:{branch}"], checkout)
        state = read_json(run_dir / "run.json")
        state["publish_status"] = "published"
        state["published_at"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        state["published_branch"] = branch
        write_json(run_dir / "run.json", state)
        print(json.dumps({"run_id": args.run_id, "status": "published", "branch": branch}, ensure_ascii=False))
        return 0
    except (WorkflowError, subprocess.CalledProcessError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"run_id": args.run_id, "status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
