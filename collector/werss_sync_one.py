#!/usr/bin/env python3
"""Low-frequency one-account WeRSS sync probe.

Use this after WeChat frequency control has cooled down. It deliberately avoids
the bulk queue so one failed probe cannot keep extending the throttle window.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WERSS_ROOT = ROOT / "integrations" / "we-mp-rss"
# WeRSS resolves config.yaml and its relative SQLite path from the current
# working directory during module import. Make this helper safe to invoke from
# the SPECTRA repository root as documented.
os.chdir(WERSS_ROOT)
sys.path.insert(0, str(WERSS_ROOT))

from core.db import DB  # noqa: E402
from core.models.article import Article  # noqa: E402
from core.models.feed import Feed  # noqa: E402
from core.wx import WxGather  # noqa: E402
from jobs.article import UpdateArticle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("account", nargs="?", default="机器之心")
    parser.add_argument("--max-page", type=int, default=1)
    parser.add_argument(
        "--mode",
        choices=("web", "free_publish", "playwright", "api", "app", "auto"),
        default="free_publish",
        help="WeRSS collector mode; free_publish avoids the legacy web endpoint's long retry loop",
    )
    parser.add_argument("--interval", type=int, default=3)
    args = parser.parse_args()

    session = DB.get_session()
    try:
        feed = session.query(Feed).filter(Feed.mp_name == args.account).first()
        if not feed:
            parser.error(f"unknown WeRSS subscription: {args.account}")
        before = session.query(Article).filter(Article.mp_id == feed.id).count()
        collector = WxGather().Model(args.mode)
        collector.get_Articles(
            feed.faker_id,
            Mps_id=feed.id,
            Mps_title=feed.mp_name,
            MaxPage=args.max_page,
            CallBack=UpdateArticle,
            interval=args.interval,
        )
        session.expire_all()
        after = session.query(Article).filter(Article.mp_id == feed.id).count()
        result = {
            "account": feed.mp_name,
            "feed_id": feed.id,
            "mode": args.mode,
            "collected": collector.all_count(),
            "stored_before": before,
            "stored_after": after,
            "status": "success" if after else "no_articles",
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0 if after else 2
    except Exception as exc:
        print(json.dumps({"account": args.account, "status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
