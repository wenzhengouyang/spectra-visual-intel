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


def validate_run(run_dir: Path, config: dict, config_path: Path = DEFAULT_CONFIG) -> dict:
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
        "--config", str(config_path),
    ], ROOT)
    if (config.get("publishing") or {}).get("require_publication_checks", True):
        if not (run_dir / "eval-report.json").exists():
            raise WorkflowError("run evaluation report is missing")
        evaluation = read_json(run_dir / "eval-report.json")
        from spectra_agent.execution import publication_version
        if evaluation.get("content_version") != publication_version(run_dir):
            raise WorkflowError("evaluation is missing a current content version; resume --retry before publishing")
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
    valid_checkout = False
    if (checkout / ".git").exists():
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=checkout, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            check=False,
        )
        valid_checkout = probe.returncode == 0 and probe.stdout.strip() == "true"
    if not valid_checkout:
        if checkout.exists():
            # Preserve a broken cache for diagnosis instead of recursively
            # deleting a path that originated in configuration.
            suffix = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            quarantine = checkout.with_name(f"{checkout.name}.invalid-{suffix}")
            checkout.replace(quarantine)
        checkout.parent.mkdir(parents=True, exist_ok=True)
        command([
            "git", "clone", "--depth", "1", "--branch", branch, "--single-branch",
            remote_url, str(checkout),
        ], ROOT)
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
    # Keep each published issue self-contained so rolling updates do not break shares.
    run_id = read_json(run_dir / "editorial-issue.json").get("run_id") or run_dir.name
    if not isinstance(run_id, str) or not run_id or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in run_id):
        raise WorkflowError("invalid archive run id")
    archive = checkout / "archive" / run_id
    if not archive.exists():
        staging = checkout / "archive" / (run_id + ".pending")
        staging.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(checkout / "index.html", staging / "index.html")
        for relative in STATIC_ASSETS:
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(checkout / relative, target)
        if source_assets.exists():
            shutil.copytree(source_assets, staging / "assets", dirs_exist_ok=True)
        staging.rename(archive)
    paths.append(str(archive.relative_to(checkout)))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish a validated SPECTRA run to GitHub Pages")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG.relative_to(ROOT)))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--push", action="store_true", help="commit and push after validation")
    parser.add_argument("--confirm", action="store_true", help="required explicit publication confirmation")
    args = parser.parse_args()

    config_path, config = resolve_config(args.config)
    run_dir = runs_dir(config) / args.run_id
    try:
        validate_run(run_dir, config, config_path)
        if not args.push:
            print(json.dumps({"run_id": args.run_id, "status": "validated", "published": False}, ensure_ascii=False))
            return 0
        if not args.confirm:
            raise WorkflowError("--push requires --confirm")

        checkout = prepare_checkout(config)
        changed_paths = copy_package(run_dir, checkout)
        # The publication checkout may ignore generated raster assets globally.
        # Reviewed run assets are an explicit part of the validated package and
        # must still be staged, otherwise the HTML can publish broken cover URLs.
        command(["git", "add", "-f", "--", *changed_paths], checkout)
        staged = command(["git", "diff", "--cached", "--name-only"], checkout, capture=True)
        if staged:
            command(["git", "commit", "-m", f"publish: {args.run_id}"], checkout)
        # A previous push may have failed after committing locally. Even with
        # an empty staging area, retry the push before marking this run published.
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
