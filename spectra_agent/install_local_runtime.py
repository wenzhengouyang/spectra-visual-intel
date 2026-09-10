#!/usr/bin/env python3
"""Deploy SPECTRA outside macOS protected Documents and install launchd."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

try:
    from spectra_agent.install_werss_service import install as install_werss_service
except ImportError:
    from install_werss_service import install as install_werss_service


SOURCE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RUNTIME = Path.home() / "Library/Application Support/SPECTRA/runtime"
DEFAULT_DATA_ROOT = Path.home() / "Library/Application Support/SPECTRA/data"
DAILY_LABEL = "com.spectra.visual-intel.daily"
DINGTALK_LABEL = "com.spectra.visual-intel.dingtalk"
PROGRESS_LABEL = "com.spectra.visual-intel.progress"


def ignored(directory: str, names: list[str]) -> set[str]:
    ignored_names = {
        name for name in names
        if name in {"__pycache__", ".publish-remote", "logs", "runs"}
        or name.endswith((".pyc", ".tmp"))
    }
    if Path(directory).name == "bin" and Path(directory).parent.name in {".venv-llm", ".venv-collector"}:
        ignored_names.update({"python", "python3", "python3.9"} & set(names))
    return ignored_names


def deploy(runtime: Path) -> None:
    runtime.mkdir(parents=True, exist_ok=True)
    for directory in (
        ".venv-llm", ".venv-collector", "app", "assets", "collector", "editorial",
        "processor", "scripts", "spectra_agent", "verification",
    ):
        source = SOURCE_ROOT / directory
        if source.exists():
            target = runtime / directory
            # Runtime code is disposable. Rebuild code directories so renamed
            # or deleted files cannot survive a sync; retain venvs incrementally.
            if not directory.startswith(".venv-") and target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, dirs_exist_ok=True, ignore=ignored)
    # copytree dereferences venv launchers by default.  Restore the original
    # absolute/relative links so the copied environment uses the system Python
    # with its adjacent runtime libraries instead of an orphaned Mach-O file.
    for environment in (".venv-llm", ".venv-collector"):
        source_bin = SOURCE_ROOT / environment / "bin"
        target_bin = runtime / environment / "bin"
        for launcher in ("python", "python3", "python3.9"):
            source = source_bin / launcher
            target = target_bin / launcher
            if not source.is_symlink():
                continue
            if target.exists() or target.is_symlink():
                target.unlink()
            target.symlink_to(os.readlink(source))
    for filename in ("tokens.css", "visual-intelligence-prototype.html"):
        shutil.copy2(SOURCE_ROOT / filename, runtime / filename)
    env_file = SOURCE_ROOT / ".env.local"
    if env_file.exists():
        target = runtime / ".env.local"
        shutil.copy2(env_file, target)
        target.chmod(0o600)
    (DEFAULT_DATA_ROOT / "logs").mkdir(parents=True, exist_ok=True)


def install_plist(
    runtime: Path,
    *,
    label: str = DAILY_LABEL,
    template_name: str = "com.spectra.visual-intel.daily.plist",
    script_name: str = "daily_runner.py",
    stdout_name: str = "launchd.out.log",
    stderr_name: str = "launchd.err.log",
) -> Path:
    template = SOURCE_ROOT / "spectra_agent/launchd" / template_name
    with template.open("rb") as handle:
        payload = plistlib.load(handle)
    payload["ProgramArguments"][0] = str(runtime / ".venv-llm/bin/python")
    payload["ProgramArguments"][1] = str(runtime / "spectra_agent" / script_name)
    payload["WorkingDirectory"] = str(runtime)
    payload["StandardOutPath"] = str(DEFAULT_DATA_ROOT / "logs" / stdout_name)
    payload["StandardErrorPath"] = str(DEFAULT_DATA_ROOT / "logs" / stderr_name)
    launch_agents = Path.home() / "Library/LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    target = launch_agents / f"{label}.plist"
    with target.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", domain, str(target)], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    subprocess.run(["launchctl", "enable", f"{domain}/{label}"], check=True)
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Deploy and install the daily SPECTRA runtime")
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME))
    parser.add_argument("--copy-only", action="store_true")
    args = parser.parse_args()
    runtime = Path(args.runtime).expanduser().resolve()
    deploy(runtime)
    plists = {}
    if not args.copy_only:
        plists["daily"] = str(install_plist(runtime))
        plists["dingtalk"] = str(install_plist(
            runtime,
            label=DINGTALK_LABEL,
            template_name="com.spectra.visual-intel.dingtalk.plist",
            script_name="dingtalk_push.py",
            stdout_name="dingtalk.out.log",
            stderr_name="dingtalk.err.log",
        ))
        plists["werss"] = str(install_werss_service())
        plists["progress"] = str(install_plist(
            runtime,
            label=PROGRESS_LABEL,
            template_name="com.spectra.visual-intel.progress.plist",
            script_name="progress_server.py",
            stdout_name="progress.out.log",
            stderr_name="progress.err.log",
        ))
    probe = subprocess.run([
        str(runtime / ".venv-llm/bin/python"),
        str(runtime / "spectra_agent/daily_runner.py"),
        "--config", "spectra_agent/config.v0.1.json", "--probe",
    ], cwd=runtime, check=False, text=True, capture_output=True)
    result = {
        "status": "installed" if probe.returncode == 0 else "invalid",
        "runtime": str(runtime),
        "plists": plists,
        "schedules": {
            "daily": "08:00 Asia/Shanghai",
            "dingtalk": "every 30 minutes; sent marker prevents duplicates",
            "werss": "run at login and keep alive",
            "progress": "http://127.0.0.1:8010; run at login and keep alive",
        },
        "probe": probe.stdout.strip() or probe.stderr.strip(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if probe.returncode == 0 else probe.returncode


if __name__ == "__main__":
    raise SystemExit(main())
