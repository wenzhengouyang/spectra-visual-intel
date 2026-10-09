#!/usr/bin/env python3
"""Interactive second-round cover review with automatic workflow resume."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    run_dir = Path(args.run_dir).resolve()
    state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
    if state.get("current_stage") != "image_preview":
        print("流程已不在配图预览阶段，请返回工作台确认。")
        return 0

    check = subprocess.run([
        sys.executable, str(ROOT / "spectra_agent/image_review.py"),
        "--run-dir", str(run_dir),
    ], cwd=ROOT, capture_output=True, text=True)
    payload = json.loads(check.stdout)
    pending = payload.get("pending") or []
    if not pending:
        print("没有待审核配图，正在恢复流程。")
    else:
        digest = run_dir / "rolling-digest.html"
        if digest.exists():
            subprocess.run(["/usr/bin/open", str(digest)], check=False)
        print(f"\nSPECTRA 第二轮配图审核 — {run_dir.name}")
        print("已打开今日预览，共有 %d 张配图待人工确认：" % len(pending))
        for index, item in enumerate(pending, 1):
            print(f"{index}. {item.get('headline') or item.get('story_id')}")
            print(f"   {item.get('url') or ''}")
        while True:
            action = input("\n输入 y 全部通过；输入 r 全部退回；输入 q 暂停：").strip().lower()
            if action in {"q", "quit"}:
                print("已保持暂停，未作批准。")
                return 0
            if action == "r":
                reason = input("请输入退回原因：").strip()
                if not reason:
                    print("退回原因不能为空。")
                    continue
                result = subprocess.run([
                    sys.executable, str(ROOT / "spectra_agent/image_review.py"),
                    "--run-dir", str(run_dir), "--reject", "all", "--reason", reason,
                    "--reviewer", "human-terminal", "--actor-type", "human",
                ], cwd=ROOT)
                print("配图已退回，流程保持暂停，等待重新生成。")
                return result.returncode
            if action == "y":
                result = subprocess.run([
                    sys.executable, str(ROOT / "spectra_agent/image_review.py"),
                    "--run-dir", str(run_dir), "--approve", "all",
                    "--reviewer", "human-terminal", "--actor-type", "human",
                ], cwd=ROOT)
                if result.returncode:
                    print("配图审核保存失败，请查看上方错误。")
                    return result.returncode
                break
            print("请输入 y、r 或 q。")

    print("配图审核已完成，正在自动恢复语义复核、发布与钉钉推送流程。")
    return subprocess.run([
        sys.executable, str(ROOT / "spectra_agent/run.py"), "--config",
        "spectra_agent/config.v0.1.json", "resume", "--run-id", run_dir.name, "--retry",
    ], cwd=ROOT).returncode


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (KeyboardInterrupt, EOFError):
        print("\n已退出，流程保持暂停。")
        raise SystemExit(0)
