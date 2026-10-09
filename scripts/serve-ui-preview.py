#!/usr/bin/env python3
"""Serve the static SPECTRA preview without exposing checkout-only files."""

from __future__ import annotations

import argparse
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_FILES = {"/", "/index.html", "/tokens.css", "/favicon.ico"}
PUBLIC_DIRECTORIES = ("/app/", "/assets/")


class PreviewHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        request_path = unquote(urlparse(path).path)
        if request_path in PUBLIC_FILES:
            request_path = "/index.html" if request_path == "/" else request_path
        elif not request_path.startswith(PUBLIC_DIRECTORIES):
            return str(ROOT / ".preview-not-found")
        candidate = (ROOT / request_path.lstrip("/")).resolve()
        try:
            candidate.relative_to(ROOT)
        except ValueError:
            return str(ROOT / ".preview-not-found")
        return str(candidate)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=4174)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), PreviewHandler)
    print(f"Preview available at http://{args.host}:{args.port}/index.html", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
