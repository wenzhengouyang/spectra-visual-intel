#!/usr/bin/env python3
"""Deploy SPECTRA outside macOS protected Documents and install launchd."""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import plistlib
import secrets
import shutil
import socket
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
DELIVERY_LABEL = "com.spectra.visual-intel.delivery"
PROGRESS_TOKEN_FILE = DEFAULT_DATA_ROOT / "progress-access-token"


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
    if runtime.resolve() == SOURCE_ROOT.resolve():
        raise ValueError("Deploy from the source checkout, not from the runtime directory")
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
    argument_replacements: dict[str, str] | None = None,
    extra_arguments: list[str] | None = None,
) -> Path:
    template = SOURCE_ROOT / "spectra_agent/launchd" / template_name
    with template.open("rb") as handle:
        payload = plistlib.load(handle)
    payload["ProgramArguments"][0] = str(runtime / ".venv-llm/bin/python")
    payload["ProgramArguments"][1] = str(runtime / "spectra_agent" / script_name)
    for old, new in (argument_replacements or {}).items():
        payload["ProgramArguments"][payload["ProgramArguments"].index(old)] = new
    payload["ProgramArguments"].extend(extra_arguments or [])
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


def prepare_progress_access(runtime: Path) -> tuple[Path, str]:
    DEFAULT_DATA_ROOT.mkdir(parents=True, exist_ok=True)
    if not PROGRESS_TOKEN_FILE.exists():
        PROGRESS_TOKEN_FILE.write_text(secrets.token_urlsafe(32), encoding="utf-8")
        PROGRESS_TOKEN_FILE.chmod(0o600)
    token = PROGRESS_TOKEN_FILE.read_text(encoding="utf-8").strip()
    if len(token) < 24:
        raise ValueError("progress access token is invalid")
    plist_path = install_plist(
        runtime,
        label=PROGRESS_LABEL,
        template_name="com.spectra.visual-intel.progress.plist",
        script_name="progress_server.py",
        stdout_name="progress.out.log",
        stderr_name="progress.err.log",
        argument_replacements={"127.0.0.1": "0.0.0.0"},
        extra_arguments=["--access-token-file", str(PROGRESS_TOKEN_FILE)],
    )
    return plist_path, token


def local_ip() -> str | None:
    for interface in ("en0", "en1"):
        result = subprocess.run(["ipconfig", "getifaddr", interface], check=False,
                                text=True, capture_output=True)
        candidate = result.stdout.strip()
        try:
            if candidate and not ipaddress.ip_address(candidate).is_loopback:
                return candidate
        except ValueError:
            continue
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            candidate = str(probe.getsockname()[0])
            return candidate if not ipaddress.ip_address(candidate).is_loopback else None
    except OSError:
        return None


def retire_dingtalk_timer():
    """Preserve the former calendar job outside LaunchAgents for rollback."""
    target = Path.home() / 'Library/LaunchAgents' / (DINGTALK_LABEL + '.plist')
    if target.exists():
        subprocess.run(['launchctl', 'bootout', f'gui/{os.getuid()}', str(target)],
                       check=False, capture_output=True)
        backup = DEFAULT_DATA_ROOT / 'deployment-backups' / 'after-publish-schedule'
        backup.mkdir(parents=True, exist_ok=True)
        if not (backup / target.name).exists():
            shutil.move(str(target), str(backup / target.name))
        else:
            target.rename(backup / (str(__import__('time').time_ns()) + '-' + target.name))


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
        plists["recovery"] = str(install_plist(runtime,
            label="com.spectra.visual-intel.recovery",
            template_name="com.spectra.visual-intel.recovery.plist",
            script_name="daily_runner.py", stdout_name="recovery.out.log", stderr_name="recovery.err.log"))
        plists["delivery"] = str(install_plist(runtime,
            label=DELIVERY_LABEL,
            template_name="com.spectra.visual-intel.delivery.plist",
            script_name="daily_runner.py", stdout_name="delivery.out.log", stderr_name="delivery.err.log"))
        retire_dingtalk_timer()
        plists["werss"] = str(install_werss_service())
        progress_plist, progress_token = prepare_progress_access(runtime)
        plists["progress"] = str(progress_plist)
    else:
        progress_token = ""
    probe = subprocess.run([
        str(runtime / ".venv-llm/bin/python"),
        str(runtime / "spectra_agent/daily_runner.py"),
        "--config", "spectra_agent/config.v0.1.json", "--probe",
    ], cwd=runtime, check=False, text=True, capture_output=True)
    mobile_ip = local_ip()
    result = {
        "status": "installed" if probe.returncode == 0 else "invalid",
        "runtime": str(runtime),
        "plists": plists,
        "schedules": {
            "daily": "09:00 collection; 11:00 incremental catch-up via recovery巡检 (Asia/Shanghai)",
            "delivery": "12:30 web publication, then DingTalk if enabled; sent marker prevents duplicates",
            "werss": "run at login and keep alive",
            "progress": "local and protected mobile access; run at login and keep alive",
        },
        "mobile_url": (f"http://{mobile_ip}:8010/?token={progress_token}"
                       if progress_token and mobile_ip else None),
        "probe": probe.stdout.strip() or probe.stderr.strip(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if probe.returncode == 0 else probe.returncode


if __name__ == "__main__":
    raise SystemExit(main())
