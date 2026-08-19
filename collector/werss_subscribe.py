#!/usr/bin/env python3
"""Provision the fixed WeChat watchlist in a local WeRSS instance.

The script deliberately separates discovery from selection: only an exact
nickname match is accepted, and verified accounts are preferred. This avoids
silently subscribing to similarly named accounts returned by WeChat search.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


def request_json(
    url: str,
    *,
    method: str = "GET",
    token: str | None = None,
    form: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    body = None
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if form is not None:
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        body = urlencode(form).encode("utf-8")
    elif payload is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, TimeoutError) as exc:
        raise RuntimeError(f"WeRSS request failed: {url}: {exc}") from exc
    if result.get("code") != 0:
        detail = result.get("detail") or result
        raise RuntimeError(f"WeRSS API error: {detail}")
    return result.get("data") or {}


def select_exact_match(name: str, candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    exact = [item for item in candidates if str(item.get("nickname", "")).strip() == name]
    if not exact:
        return None
    return sorted(
        exact,
        key=lambda item: (
            int(item.get("verify_status") or 0),
            bool(item.get("alias")),
            int(item.get("service_type") or 0),
        ),
        reverse=True,
    )[0]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=os.getenv("WERSS_BASE_URL", "http://127.0.0.1:8001"))
    parser.add_argument("--username", default=os.getenv("WERSS_USERNAME", "admin"))
    parser.add_argument("--watchlist", default="collector/wechat_watchlist.v0.1.json")
    parser.add_argument("--dry-run", action="store_true", help="Search and verify exact matches without subscribing")
    parser.add_argument("--delay", type=float, default=1.0, help="Pause between WeChat searches")
    args = parser.parse_args()
    env_path = Path(__file__).resolve().parents[1] / ".env.local"
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value

    password = os.getenv("WERSS_PASSWORD")
    if not password:
        parser.error("set WERSS_PASSWORD in the process environment")

    watchlist = json.loads(Path(args.watchlist).read_text(encoding="utf-8"))
    names = [item["account_name"] for item in watchlist["accounts"] if item.get("enabled", True)]
    login = request_json(
        f"{args.base_url.rstrip('/')}/api/v1/wx/auth/login",
        method="POST",
        form={"username": args.username, "password": password},
    )
    token = login["access_token"]

    results: list[dict[str, Any]] = []
    for name in names:
        try:
            search = request_json(
                f"{args.base_url.rstrip('/')}/api/v1/wx/mps/search/{quote(name)}",
                token=token,
            )
            match = select_exact_match(name, search.get("list") or [])
            if not match:
                results.append({"account_name": name, "status": "no_exact_match"})
                time.sleep(args.delay)
                continue
            record = {
                "account_name": name,
                "status": "matched" if args.dry_run else "subscribed",
                "alias": match.get("alias"),
                "fakeid": match.get("fakeid"),
                "verify_status": match.get("verify_status"),
                "signature": match.get("signature"),
            }
            if not args.dry_run:
                added = request_json(
                    f"{args.base_url.rstrip('/')}/api/v1/wx/mps",
                    method="POST",
                    token=token,
                    payload={
                        "mp_name": match["nickname"],
                        "mp_cover": match.get("round_head_img"),
                        "mp_id": match["fakeid"],
                        "avatar": match.get("round_head_img"),
                        "mp_intro": match.get("signature") or "",
                    },
                )
                record["feed_id"] = added.get("id")
            results.append(record)
        except Exception as exc:
            results.append({"account_name": name, "status": "failed", "error": str(exc)})
        time.sleep(args.delay)

    summary = {
        "requested": len(names),
        "matched": sum(item["status"] in {"matched", "subscribed"} for item in results),
        "subscribed": sum(item["status"] == "subscribed" for item in results),
        "failed": sum(item["status"] == "failed" for item in results),
        "no_exact_match": sum(item["status"] == "no_exact_match" for item in results),
        "results": results,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
