"""Test-only probe; supplied paths point to disposable fixtures, never user data."""
import json
import os
import socket
import subprocess
import sys

target = json.loads(sys.stdin.read())
results = {}
for name, action in {
    "read": lambda: open(target["path"]).read(),
    "write": lambda: open(target["path"] + ".new", "w"),
    "network": lambda: socket.create_connection(("127.0.0.1", target["port"]), timeout=1),
    "process": lambda: subprocess.run(["/bin/echo", "child"], check=True),
}.items():
    try:
        action()
        results[name] = "allowed"
    except PermissionError:
        results[name] = "denied"
results["secret_in_environment"] = "OPENAI_API_KEY" in os.environ
print(json.dumps(results))
