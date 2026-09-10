#!/usr/bin/env python3
"""Install the local WeRSS process as a per-user macOS LaunchAgent."""

from __future__ import annotations

import os
import plistlib
import subprocess
from pathlib import Path


LABEL = "com.spectra.visual-intel.werss"
ROOT = Path(__file__).resolve().parents[1]
WERSS_ROOT = ROOT / "integrations/we-mp-rss"
DATA_ROOT = Path.home() / "Library/Application Support/SPECTRA/data"


def launch_agent_payload() -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            str(WERSS_ROOT / ".venv/bin/python"),
            "-m",
            "uvicorn",
            "web:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001",
        ],
        "WorkingDirectory": str(WERSS_ROOT),
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "ProcessType": "Background",
        "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
        "StandardOutPath": str(DATA_ROOT / "logs/werss.out.log"),
        "StandardErrorPath": str(DATA_ROOT / "logs/werss.err.log"),
    }


def install() -> Path:
    (DATA_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    launch_agents = Path.home() / "Library/LaunchAgents"
    launch_agents.mkdir(parents=True, exist_ok=True)
    target = launch_agents / f"{LABEL}.plist"
    with target.open("wb") as handle:
        plistlib.dump(launch_agent_payload(), handle, sort_keys=False)

    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["launchctl", "bootout", domain, str(target)],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(["launchctl", "bootstrap", domain, str(target)], check=True)
    subprocess.run(["launchctl", "enable", f"{domain}/{LABEL}"], check=True)
    subprocess.run(["launchctl", "kickstart", f"{domain}/{LABEL}"], check=True)
    return target


if __name__ == "__main__":
    print(install())
