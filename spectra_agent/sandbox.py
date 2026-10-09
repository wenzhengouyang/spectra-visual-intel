"""Fail-closed macOS sandbox for trusted, offline extraction programs."""
from __future__ import annotations

import json
import os
import resource
from pathlib import Path
import signal
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def _limits():
    resource.setrlimit(resource.RLIMIT_FSIZE, (10 * 1024 * 1024, 10 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))


def run_sandboxed(args, *, input_text="", timeout=45):
    executable = Path(args[0]).resolve()
    python = Path(sys.executable).resolve()
    script = Path(args[1]).resolve() if len(args) > 1 else None
    if executable != python or not script or script.suffix != ".py" or not script.is_relative_to(ROOT):
        raise ValueError("sandbox accepts only project-owned Python extractor scripts")
    if not Path("/usr/bin/sandbox-exec").is_file():
        raise RuntimeError("OS sandbox unavailable; refusing unsandboxed extraction")
    with tempfile.TemporaryDirectory(prefix="spectra-offline-") as temporary:
        root = Path(temporary).resolve()
        reads = ["/System/Library", "/usr/lib", str(Path(sys.base_prefix).resolve()),
                 str(Path(sys.prefix).resolve() / "lib")]
        profile = "\n".join([
            "(version 1)", "(deny default)", "(allow sysctl-read)",
            '(allow mach-lookup (global-name "com.apple.system.notification_center"))',
            f"(allow process-exec (subpath {json.dumps(str(Path(sys.base_prefix).resolve()))}))",
            "(allow file-read-metadata)",
            *[f"(allow file-read* (subpath {json.dumps(p)}))" for p in reads],
            *[f"(allow file-read* (literal {json.dumps(p)}))" for p in [str(script), str(python), "/", "/dev/null", "/dev/urandom", "/dev/random"]],
            f"(allow file-read* file-write* (subpath {json.dumps(str(root))}))",
            # Network and subprocess creation remain denied by default.
        ])
        environment = {"PATH": "/usr/bin:/bin", "HOME": str(root), "TMPDIR": str(root),
                       "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "LANG": "en_US.UTF-8"}
        with (root / "stdout").open("w+") as stdout, (root / "stderr").open("w+") as stderr:
            process = subprocess.Popen(["/usr/bin/sandbox-exec", "-p", profile, str(python), "-I", str(script), *args[2:]],
                                       stdin=subprocess.PIPE, stdout=stdout, stderr=stderr, text=True,
                                       cwd=root, env=environment, start_new_session=True, preexec_fn=_limits)
            try:
                process.communicate(input_text, timeout=min(timeout, 90))
            except BaseException:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
                raise
            if stdout.tell() > 10 * 1024 * 1024:
                raise ValueError("extractor output exceeds limit")
            stdout.seek(0)
            if process.returncode:
                # Do not surface raw extractor diagnostics or environment data.
                raise RuntimeError(f"sandboxed extractor failed (exit {process.returncode})")
            return subprocess.CompletedProcess(args, process.returncode, stdout.read(10 * 1024 * 1024), "")
